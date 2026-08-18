"""LLM tool loop: call model, execute tools, repeat until final response."""

from __future__ import annotations

import json
import hashlib
import os
import queue
import pathlib
import time
from dataclasses import dataclass, field, replace  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from typing import Any, Callable, Dict, List, Optional, Tuple

import logging

from ouroboros.llm import LLMClient, normalize_reasoning_effort, add_usage  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros import task_pacing
from ouroboros.config import adaptive_quorum, get_context_mode, get_light_model, get_review_enforcement, get_task_review_mode, resolve_effort  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.review_cycles import REASON_REVIEW_CYCLES_EXHAUSTED  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.outcomes import ACCEPTANCE_ACCEPTED, ACCEPTANCE_BYPASS_REASON_BY_RAIL, ACCEPTANCE_BYPASS_REASONS, ACCEPTANCE_DECISION_STATUSES, ACCEPTANCE_FINALIZED_UNACCEPTED, ACCEPTANCE_REVISION_REQUESTED, REASON_ACCEPTANCE_REVIEW_SKIPPED_DEADLINE_RESERVE, REASON_DELIVERY_CONTROL_DEGRADED, REASON_OWNER_REQUESTED_FINALIZATION, RESULT_INFRA_FAILED, extract_final_answer, latest_agent_defined_verification, latest_unreconciled_failed_verification, latest_unreconciled_masked_verification, reviewable_effect_projection, should_nudge_verification, turn_has_reviewable_effects  # noqa: F401 -- moved readers import via the L-B leaves; the loop surface keeps these bindings
from ouroboros.observability import new_execution_id
from ouroboros.tool_policy import CAPABILITY_OMISSION_HEADER, format_capability_omissions, initial_tool_schemas, list_non_core_tools, swarm_router_turn
from ouroboros.tools.registry import ToolRegistry
from ouroboros.context import build_user_content  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.context_budget import ContextReclaimRequest
from ouroboros.context_compaction import compact_tool_history_llm, context_reclaim_transcript_sha256
from ouroboros.deadline_utils import parse_deadline_ts, utc_now  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.utils import estimate_tokens, truncate_review_artifact
from ouroboros.usage_accounting import (
    BudgetExceeded,
    PhysicalAttemptContext,
    PhysicalAttemptPreconditionFailed,
    last_physical_attempt_capture,
)

from ouroboros.loop_tool_execution import (
    StatefulToolExecutor,
    handle_tool_calls,
    prune_reclaim_trace_refs,
    reclaim_negative_memo,
    reclaim_trace_refs,
)
from ouroboros.loop_llm_call import call_llm_with_retry, emit_llm_usage_event  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.pricing import estimate_cost_optional  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves

# Backward-compat alias for source-inspecting/monkeypatched tests.
_call_llm_with_retry = call_llm_with_retry

log = logging.getLogger(__name__)


@dataclass
class DeliveryCandidate:
    """Loop-local complete answer retained across service/finalization rounds."""

    full_text: str
    content_sha256: str
    revision: int
    evidence_revision: int
    evidence_fingerprint: str
    acceptance_binding: Dict[str, Any]
    finalization_control: str = "candidate"
    repair_attempted: bool = False
    degraded: bool = False
    degraded_reason: str = ""


def _handle_text_response(
    content: Optional[str],
    llm_trace: Dict[str, Any],
    accumulated_usage: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """Handle LLM response without tool calls (final response)."""
    if content and content.strip():
        llm_trace["reasoning_notes"].append(content.strip())
    return (content or ""), accumulated_usage, llm_trace


def _skill_names_touched_by_trace(llm_trace: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for call in llm_trace.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        tool = str(call.get("tool") or "")
        if tool not in {"write_file", "edit_text"}:
            continue
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        bucket = str(args.get("bucket") or "").strip().lower()
        skill_name = str(args.get("skill_name") or "").strip()
        if bucket in {"external", "clawhub", "ouroboroshub"} and skill_name:
            if skill_name not in names:
                names.append(skill_name)
            continue
        candidates = [str(args.get("path") or "")]
        for raw in candidates:
            norm = raw.replace("\\", "/").strip().lstrip("/")
            if norm.startswith("data/"):
                norm = norm[len("data/"):]
            parts = pathlib.PurePosixPath(norm).parts
            if len(parts) >= 3 and parts[0] == "skills" and parts[1] in {"external", "clawhub", "ouroboroshub", "native"}:
                name = parts[2]
                if name and name not in names:
                    names.append(name)
    return names


def _skill_finalization_message(drive_root: pathlib.Path, llm_trace: Dict[str, Any]) -> str:
    names = _skill_names_touched_by_trace(llm_trace)
    if not names:
        return ""
    try:
        from ouroboros.skill_loader import find_skill
        from ouroboros.skill_readiness import skill_readiness_for_execution
    except Exception:
        return ""
    blockers: List[str] = []
    for name in names:
        try:
            skill = find_skill(pathlib.Path(drive_root), name)
            if skill is None or not getattr(skill, "is_self_authored", False):
                continue
            readiness = skill_readiness_for_execution(pathlib.Path(drive_root), skill)
            ready = readiness.ready
        except Exception:
            continue
        if not ready:
            blockers.append(
                f"{skill.name}: status={skill.review.status!r}, "
                f"blockers={readiness.blockers}"
            )
    if not blockers:
        return ""
    return (
        "⚠️ SKILL_NOT_FINALIZED: You edited self-authored skill payloads but "
        "they are not ready yet. Call skill_review for each skill before "
        "declaring the task done. Current blockers: " + "; ".join(blockers)
    )


def _force_plan_decision(
    ctx: Any,
    _llm_trace: Dict[str, Any],
    *,
    hard_rail: str = "",
) -> Dict[str, Any]:
    """Project force-plan finalization from existing review + policy SSOTs.

    Body extracted to ``owner_hurry.force_plan_decision`` (the hurry latch makes
    the projection task-locally advisory for reviewed/open/unavailable states —
    §19.7.2 item 9); unlatched behavior is byte-identical.
    """
    from ouroboros.owner_hurry import force_plan_decision

    return force_plan_decision(
        ctx, _llm_trace, hard_rail=hard_rail,
        enforcement=get_review_enforcement(),
    )


def _force_plan_reminder(decision: Dict[str, Any]) -> str:
    from ouroboros.owner_hurry import plan_review_reminder

    return plan_review_reminder(decision)


def _force_plan_disclosure(
    ctx: Any,
    llm_trace: Dict[str, Any],
    *,
    forced_reason: str = "",
) -> str:
    # Normal finalization reuses the reducer projection that already decided
    # this exact candidate. The trace copy is presentation-only and cannot grant
    # permission; forced rails recompute with their explicit rail input.
    from ouroboros.owner_hurry import plan_review_disclosure

    projected = llm_trace.get("force_plan_decision")
    decision = (
        projected
        if not forced_reason and isinstance(projected, dict)
        else _force_plan_decision(ctx, llm_trace, hard_rail=forced_reason)
    )
    return plan_review_disclosure(decision, forced_reason)


def _swarm_handoff_attempt(ctx: Any) -> Dict[str, Any]:
    attempt = getattr(ctx, "_swarm_handoff_attempt", None)
    return dict(attempt) if isinstance(attempt, dict) else {}


def _check_budget_limits(
    ctx: "_RoundLimitContext",
    budget_remaining_usd: Optional[float],
    cost_ceiling: Optional["task_pacing.CostCeiling"] = None,
) -> Optional[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """Return a final-response tuple when budget limits require stopping.

    ``cost_ceiling`` is the typed in-task stop resolved ONCE at loop start
    (``task_pacing.resolve_cost_ceiling``). Only an ``active`` ceiling stops
    here; ``exhausted_soft_land`` fires at the top of the round. The deciding
    spend is the root subtree's ledger-accounted number when a root cap exists
    (the fence counts the TREE, not own calls); own cost is the DISCLOSED
    fallback and the diagnostic. Unknown spend never becomes $0. The two axes
    are INDEPENDENT (v6.91 fix): ``budget_remaining_usd`` None only means no
    finite GLOBAL budget exists (TOTAL_BUDGET unset — the GAIA-shaped run) and
    must not silence a live per-task ROOT CAP; with neither, the ceiling
    resolves ``disabled`` and the whole cost axis stays silent, as before."""
    accumulated_usage = ctx.accumulated_usage
    raw_task_cost = accumulated_usage.get("cost")
    task_cost = float(raw_task_cost) if raw_task_cost is not None else None

    if budget_remaining_usd is not None and budget_remaining_usd <= 0:
        finish_reason = "🚫 Task rejected. Total budget exhausted. Please increase TOTAL_BUDGET in settings."
        accumulated_usage["execution_status"] = "failed"
        accumulated_usage["reason_code"] = "budget_exhausted"
        if ctx.round_idx <= 1:
            trace = ctx.llm_trace if isinstance(ctx.llm_trace, dict) else {}
            router_result = _forced_swarm_router_result(ctx, trace, "budget_exhausted")
            if router_result is not None:
                return router_result
            tool_ctx = getattr(getattr(ctx, "tools", None), "_ctx", None)
            suffix = (
                _force_plan_disclosure(
                    tool_ctx, trace, forced_reason="budget_exhausted",
                )
                if tool_ctx is not None else ""
            )
            # This early rejection is a forced sink like every other: nothing
            # was produced, but a queued/headless root still OWED a panel, and
            # returning without the record left `not_eligible / run_count=0` —
            # indistinguishable from "no panel was warranted". Pure ledger
            # write: no panel, no model round, no fence.
            _record_forced_finalization(
                ctx,
                trace,
                reason_code="budget_exhausted",
                source="host_budget_rejection_before_work",
                candidate=None,
            )
            return _compose_delivery_suffix(finish_reason, suffix), accumulated_usage, trace
        return _forced_final_answer(
            ctx,
            prompt=(
                "[BUDGET LIMIT] Total budget exhausted. Produce your best final answer NOW "
                "from the verified work so far; clearly mark anything unverified or "
                "incomplete. An honest best-effort result is the expected outcome here."
            ),
            fallback_text=finish_reason,
            reason_code="budget_exhausted",
        )
    # The pre-v6.91 per-task soft "[COST NOTE]" is gone: since v6.64.0 the same
    # settings key hard-fences the whole TREE at the ledger, so an own-cost note
    # keyed to it could never fire before the fence (proven live: silent through
    # two tree deaths). The v6.56.0 latched milestones are the designed nudge.

    if cost_ceiling is None or cost_ceiling.state != task_pacing.COST_CEILING_ACTIVE:
        return None
    tree_info = _loop_tree_accounting(
        refresh=True, max_age_sec=_TREE_ACCOUNTING_MAX_STALE_SEC,
    )
    tree_cost = tree_info.get("accounted_usd") if isinstance(tree_info, dict) else None
    deciding, spend_basis = task_pacing.resolve_deciding_spend(
        tree_cost_usd=tree_cost,
        task_cost_usd=task_cost,
        root_cap_usd=cost_ceiling.root_cap_usd,
    )
    ceiling_usd = cost_ceiling.ceiling_usd
    if deciding is not None and ceiling_usd is not None and deciding > ceiling_usd:
        if spend_basis == task_pacing.SPEND_BASIS_TREE:
            spent_text = (
                f"Task tree spent ${deciding:.3f} (ledger-accounted incl. in-flight holds, "
                f"subagents included; own calls ${task_cost:.3f})"
                if task_cost is not None
                else f"Task tree spent ${deciding:.3f} (ledger-accounted incl. in-flight holds)"
            )
        elif spend_basis == task_pacing.SPEND_BASIS_OWN_TREE_UNKNOWN:
            # Stopping on a disclosed lower bound beats not stopping at all, but
            # the substitution is stated, never silent (BIBLE P1).
            spent_text = (
                f"Task spent ${deciding:.3f} on its OWN calls (the tree-accounted total "
                "is unavailable right now, so subagent spend is not included — this is a "
                "lower bound)"
            )
        else:
            spent_text = f"Task spent ${deciding:.3f}"
        cap_text = (
            f"; the hard tree cap is ${cost_ceiling.root_cap_usd:.2f}"
            if cost_ceiling.root_cap_usd is not None else ""
        )
        finish_reason = (
            f"{spent_text}, over the in-task cost ceiling ${ceiling_usd:.2f}{cap_text}. "
            "Budget exhausted."
        )
        # The basis rides the usage record too, so a later reader can tell a
        # tree-decided stop from an own-cost stand-in without parsing prose.
        accumulated_usage["cost_stop_spend_basis"] = spend_basis
        return _forced_final_answer(
            ctx,
            prompt=(
                f"[BUDGET LIMIT] {finish_reason} Produce your best final answer now from "
                "the verified work so far; clearly mark anything unverified or incomplete. "
                "An honest best-effort result is the expected outcome here, not a failure."
            ),
            fallback_text=finish_reason,
            reason_code="budget_exhausted",
        )
    # The old round-gated "[INFO] ... Wrap up if possible" nudge is replaced by
    # the latched cost milestones in task_pacing (transport: _inject_round_checkpoints).

    return None


def _resolve_task_cost_ceiling(
    ctx: Any, budget_remaining_usd: Optional[float],
) -> "task_pacing.CostCeiling":
    """The typed in-task cost stop, resolved ONCE at loop start.

    The root cap comes from the bound usage scope — the SAME
    ``OUROBOROS_PER_TASK_COST_USD``-derived value the ledger fence enforces
    (agent.py wires it as ``UsageScope.root_limit_usd``), so the graceful stop
    and the fence can never disagree about the cap."""
    root_cap = None
    try:
        from ouroboros.usage_accounting import current_usage_scope

        scope = current_usage_scope()
        root_cap = getattr(scope, "root_limit_usd", None) if scope is not None else None
    except Exception:
        log.debug("Usage scope unavailable for cost ceiling resolution", exc_info=True)
    return task_pacing.resolve_cost_ceiling(
        budget_remaining_usd,
        task_pacing.resolve_budget_profile(ctx),
        root_cap_usd=root_cap,
    )


# Bounded staleness for the two DECIDING cost surfaces (ceiling check and
# milestone note). The free stash is refreshed by every dispatch under this
# root — at most one round old, zero reads — but ONE round can block 900s in
# wait_tasks while children spend (the shape both dead waves had), and the
# pacing refresh only covers deadline-less tasks, so a round outliving this
# bound pays for exactly one real projection read. Never per-round (see the
# usage_accounting telemetry note and the e4a87344 contention class).
_TREE_ACCOUNTING_MAX_STALE_SEC = 120.0


def _loop_tree_accounting(
    *, refresh: bool, max_age_sec: float = 30.0,
) -> Optional[Dict[str, Any]]:
    """The root subtree's accounted spend for the CURRENT task's tree (nullable).

    Reads the reserve-time scope telemetry for free; ``refresh=True`` may do one
    real ledger projection read when the stash is older than ``max_age_sec``.
    Callers: loop start / 600s pacing note / 15-round checkpoint (cache-breaking
    surfaces, small max_age), plus the two DECIDING surfaces (ceiling check +
    milestone note) with the wider ``_TREE_ACCOUNTING_MAX_STALE_SEC`` bound —
    free while rounds are shorter than the bound, since every dispatch refreshes
    the stash. Never an unconditional per-round read (usage_accounting notes,
    e4a87344). Only meaningful under a root cap; returns None otherwise (unknown
    is represented, never $0)."""
    try:
        from ouroboros.usage_accounting import (
            current_usage_scope,
            last_root_accounting,
            refresh_root_accounting,
        )

        scope = current_usage_scope()
        if scope is None or not scope.root_task_id or scope.root_limit_usd is None:
            return None
        if refresh:
            return refresh_root_accounting(
                scope.drive_root, scope.root_task_id, max_age_sec=max_age_sec,
            )
        return last_root_accounting(scope.root_task_id)
    except Exception:
        log.debug("Tree accounting telemetry unavailable", exc_info=True)
        return None


def _soft_land_exhausted_ceiling(
    limit_ctx: "_RoundLimitContext",
    cost_ceiling: "task_pacing.CostCeiling",
) -> Optional[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """Typed soft landing (v6.91): a root cap at or below the planning margin
    leaves no working room — enter the existing graceful best-effort wrap-up
    BEFORE spending a work round; never run uncapped (the pre-typed shape
    resolved this to the same None as "unlimited"). The ledger fence stays the
    untouched backstop. Returns the forced-final tuple, or None when the
    ceiling is not in the ``exhausted_soft_land`` state."""
    if cost_ceiling.state != task_pacing.COST_CEILING_EXHAUSTED_SOFT_LAND:
        return None
    cap_text = (
        f"${cost_ceiling.root_cap_usd:.2f}"
        if cost_ceiling.root_cap_usd is not None else "the per-task tree cap"
    )
    margin_text = (
        f"${cost_ceiling.planning_margin_usd:.2f}"
        if cost_ceiling.planning_margin_usd is not None else "the wrap-up planning margin"
    )
    soft_land_reason = (
        f"Per-task tree cap {cap_text} leaves no working room above the "
        f"wrap-up planning margin ({margin_text}). Budget exhausted."
    )
    return _forced_final_answer(
        limit_ctx,
        prompt=(
            f"[BUDGET LIMIT] {soft_land_reason} Produce your best final answer "
            "NOW from the verified work so far; clearly mark anything unverified "
            "or incomplete. An honest best-effort result is the expected outcome "
            "here, not a failure."
        ),
        fallback_text=soft_land_reason,
        reason_code="budget_exhausted",
    )


def _build_recent_tool_trace(messages: List[Dict[str, Any]], window: int = 15) -> str:
    """Build a compact recent-tool trace for the self-check prompt."""
    all_calls: List[str] = []
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", "")
                if isinstance(args, dict):
                    args = json.dumps(args, sort_keys=True)
                args_str = str(args)
                summary = f"{name}({args_str[:80]})" if len(args_str) > 80 else f"{name}({args_str})"
                all_calls.append(summary)
    recent = all_calls[-window:] if all_calls else []
    if not recent:
        return ""
    return "Recent tool calls (oldest first):\n" + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(recent))


def _adopt_fallback_route(
    ctx: Any,
    tools: ToolRegistry,
    fallback_model: str,
    fallback_use_local: bool,
    messages: List[Dict[str, Any]],
    fallback_messages: List[Dict[str, Any]],
    context_fit_plan: Any,
    active_context_mode: str,
    tool_schemas: List[Dict[str, Any]],
    accumulated_usage: Dict[str, Any],
) -> tuple:
    """Round-4 C1.1: adopt a SUCCESSFUL cross-family fallback as the active route for
    the rest of the loop. Otherwise a later round (esp. a tool loop) replays THIS
    fallback's reasoning/thinking back to the original primary family with no
    model-switch sanitizer firing (active_model never changed) — the cross-family
    signature replay, in reverse. Adopting the sanitized transcript as canonical
    keeps the old family's provider-private blocks off the switched route (a later
    switch_model/override re-triggers the round-start sanitizer normally); the
    caller already rebound the context-fit plan to this exact route, so adoption
    makes that tested projection canonical. Returns the new
    ``(active_model, active_use_local, context_fit_plan, context_mode)``."""
    ctx.active_model = fallback_model
    messages[:] = fallback_messages
    if context_fit_plan is not None:
        tools._ctx.context_fit_plan = context_fit_plan
        tools._ctx.messages = messages
        tools._ctx.active_context_mode = active_context_mode
        # _call_round_model already recorded the accepted candidate's complete
        # same-basis fit facts. Do not replace them with a raw char estimate.
    return fallback_model, fallback_use_local, context_fit_plan, active_context_mode


def _snapshot_context_fit_usage(usage: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in usage.items() if key.startswith("_context_")}


def _restore_context_fit_usage(
    usage: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> None:
    for key in tuple(usage):
        if key.startswith("_context_"):
            usage.pop(key, None)
    usage.update(snapshot)


def _run_cross_model_fallback_chain(
    *, llm, ctx, tools, messages, active_model, active_use_local, tool_schemas,
    active_effort, max_retries, drive_logs, task_id, round_idx, event_queue,
    accumulated_usage, task_type, emit_progress, context_fit_plan,
    active_context_mode,
) -> tuple:
    """F1 (v6.39): 429-aware cross-model fallback CHAIN. Mark the failed primary on
    cooldown if its last failure was transient (a swarm stops stampeding it), then
    walk the configured chain, skipping cooled-down models, until one responds; a
    small per-candidate attempt cap keeps a multi-model chain from a retry storm,
    and every call stays deadline-aware. The bench (FALLBACKS==main) dedupes to an
    empty chain -> no cross-model fallback, by design. Returns the new ``(msg,
    active_model, active_use_local, context_fit_plan, context_mode)``; ``msg`` is
    None when the whole (cooled-down / empty) chain is exhausted, leaving the
    caller to join the provider-unavailable shelf."""
    from ouroboros import fallback_cooldown as _fcd
    from ouroboros.config import get_fallback_models
    from ouroboros.loop_llm_call import _COOLDOWN_ERROR_KINDS as _cooldown_kinds

    def _cooled(model: str, use_local: bool) -> None:
        if str(accumulated_usage.get("_last_llm_error_kind") or "") in _cooldown_kinds:
            _fcd.mark_cooldown(model, use_local)

    _cooled(active_model, active_use_local)
    primary_context_usage = _snapshot_context_fit_usage(accumulated_usage)
    fallback_use_local = os.environ.get("USE_LOCAL_FALLBACK", "").lower() in ("true", "1")
    attempt_cap = _fcd.attempts_per_model()
    msg = None
    for fallback_model in get_fallback_models(active_model):
        if _fcd.is_cooling_down(fallback_model, fallback_use_local):
            continue
        deadline = _task_deadline_epoch(tools)
        if deadline and time.time() >= deadline:
            break
        ptag = " (local)" if active_use_local else ""
        ftag = " (local)" if fallback_use_local else ""
        emit_progress(f"⚡ Fallback: {active_model}{ptag} → {fallback_model}{ftag}")
        # Cross-FAMILY fallback must not replay the primary's provider-private reasoning to
        # a different family (the GLM->Claude 400 "Invalid signature" death); the SSOT
        # sanitizer is a no-op same-family.
        fallback_messages = LLMClient.sanitize_reasoning_on_model_switch(messages, active_model, fallback_model)
        # Bind exact route evidence and choose its deterministic projection BEFORE
        # physical dispatch.  This prevents the fallback's first request from
        # inheriting the failed primary route's Max projection/fingerprint.  It
        # then uses the ordinary single confirmed-overflow Low retry path.
        candidate_plan, candidate_mode = _rebind_context_fit_plan(
            context_fit_plan,
            tools,
            fallback_messages,
            model=fallback_model,
            use_local=fallback_use_local,
            preferred_mode=str(
                getattr(context_fit_plan, "preferred_mode", "") or active_context_mode
            ),
            tool_schemas=tool_schemas,
        )
        msg, _cost, candidate_mode = _call_round_model(
            _RoundModelCallContext(
                llm=llm,
                messages=fallback_messages,
                tools=tools,
                context_fit_plan=candidate_plan,
                active_model=fallback_model,
                tool_schemas=tool_schemas,
                active_effort=active_effort,
                max_retries=max_retries,
                drive_logs=drive_logs,
                task_id=task_id,
                round_idx=round_idx,
                event_queue=event_queue,
                accumulated_usage=accumulated_usage,
                task_type=task_type,
                active_use_local=fallback_use_local,
                active_context_mode=candidate_mode,
                drive_root=pathlib.Path(drive_logs).parent,
                attempt_cap=attempt_cap,
            )
        )
        if msg is not None:
            (
                active_model,
                active_use_local,
                context_fit_plan,
                active_context_mode,
            ) = _adopt_fallback_route(
                ctx,
                tools,
                fallback_model,
                fallback_use_local,
                messages,
                fallback_messages,
                candidate_plan,
                candidate_mode,
                tool_schemas,
                accumulated_usage,
            )
            break
        # Candidate evidence was real for its dispatched attempts, but an
        # unaccepted route must not become the task's canonical plan/transcript.
        tools._ctx.context_fit_plan = context_fit_plan
        tools._ctx.messages = messages
        tools._ctx.active_context_mode = active_context_mode
        _restore_context_fit_usage(accumulated_usage, primary_context_usage)
        _cooled(fallback_model, fallback_use_local)
    return (
        msg,
        active_model,
        active_use_local,
        context_fit_plan,
        active_context_mode,
    )


def _load_direct_child_results(
    status_root: pathlib.Path,
    task_id: str,
    root_task_id: str,
) -> list[Dict[str, Any]]:
    """Read this task's direct children (plan review spawns none)."""

    from ouroboros.task_status import find_child_tasks

    return [
        row for row in find_child_tasks(
            pathlib.Path(status_root),
            parent_task_id=task_id,
            root_task_id=root_task_id,
            exclude_task_id=task_id,
            scope="direct",
        )
        if isinstance(row, dict)
    ]


def _compute_subagent_handoff(tools: Any, drive_root: Any, task_id: str, content: Any) -> str:
    """C3.4 pre-finalization child absorption: build the bounded subagent-handoff
    reminder when a finished child's status/result changed since the last refresh, or
    a nonterminal child is unacknowledged in the final text. Returns "" when there is
    nothing to inject. Scans the SAME status root get_task_result uses
    (budget_drive_root, not the forked drive_root — else nested grandchildren in
    forked child drives are missed). Never raises."""
    if drive_root is None or not task_id:
        return ""
    try:
        from ouroboros.task_status import FINAL_STATUSES, format_subagent_absorption_message

        metadata = getattr(tools._ctx, "task_metadata", {}) if isinstance(getattr(tools._ctx, "task_metadata", {}), dict) else {}
        status_drive_root = pathlib.Path(
            str(metadata.get("budget_drive_root") or getattr(tools._ctx, "budget_drive_root", "") or "")
            or drive_root
        )
        children = _load_direct_child_results(
            status_drive_root,
            task_id,
            str(metadata.get("root_task_id") or task_id),
        )
        # Exact-hash dispositions suppress the unchanged result only. If status,
        # result, trace, or artifact identity changes, the disposition becomes stale
        # and this reminder automatically re-opens without parsing prose.
        children = [
            child for child in children
            if _child_disposition_state(child) not in {
                "integrated", "irrelevant", "deferred", "discarded", "cancelled",
            }
        ]
        from ouroboros.tools.join_ledger import _child_result_sha256

        signature = "|".join(
            f"{child.get('task_id') or child.get('id')}:{_child_result_sha256(child)}"
            for child in children
        )
        previous = getattr(tools._ctx, "_subagent_handoff_signature", "")
        nonterminal_children = [
            child for child in children
            if str(child.get("status") or "").strip().lower() not in FINAL_STATUSES
        ]
        # P5: the reminder is suppressed ONLY by structured signals — a child
        # discarded/cancelled (filtered above) or absorbed (unchanged
        # signature). NEVER by parsing final PROSE for status words. Fires once
        # per CHANGE, not every round; if the agent still finalizes with
        # unhandled children, the no-tool / forced finalization paths append a
        # loud orphan note via _forced_orphan_note (P1).
        _ = nonterminal_children  # (kept for readability; trigger is change-based)
        if children and signature and signature != previous:
            tools._ctx._subagent_handoff_signature = signature
            tools._ctx._child_absorption_reminded = False
            _absorb_budget = 160_000 if str(get_context_mode()).lower() == "max" else 60_000
            return format_subagent_absorption_message(
                children, parent_task_id=task_id, budget_chars=_absorb_budget,
            )
    except Exception:
        log.debug("Failed to build subagent handoff reminder", exc_info=True)
    return ""


def _maybe_inject_self_check(
    round_idx: int,
    max_rounds: int,
    messages: List[Dict[str, Any]],
    accumulated_usage: Dict[str, Any],
    emit_progress: Callable[[str], None],
    *,
    event_queue: Optional[queue.Queue] = None,
    task_id: str = "",
    drive_logs: Optional[pathlib.Path] = None,
) -> bool:
    """Inject a normal user-turn self-check and emit one checkpoint event."""
    REMINDER_INTERVAL = 15
    if round_idx <= 1 or round_idx % REMINDER_INTERVAL != 0 or round_idx >= max_rounds:
        return False

    ctx_tokens = sum(
        estimate_tokens(_extract_plain_text_from_content(m.get("content")))
        for m in messages
    )
    raw_task_cost = accumulated_usage.get("cost")
    task_cost = float(raw_task_cost) if raw_task_cost is not None else None
    cost_text = f"${task_cost:.2f}" if task_cost is not None else "unknown"
    checkpoint_num = round_idx // REMINDER_INTERVAL

    # Tree spend under a root cap (v6.91): the checkpoint is an already
    # cache-breaking user turn, so it is one of the RARE surfaces allowed to
    # carry a live ledger number (DEVELOPMENT cache_friendliness item 22). The
    # fence counts the whole tree, so own cost alone hid two tree deaths.
    tree_line = ""
    tree_accounted: Optional[float] = None
    tree_cap: Optional[float] = None
    tree_info = _loop_tree_accounting(refresh=True, max_age_sec=30.0)
    if isinstance(tree_info, dict) and tree_info.get("accounted_usd") is not None:
        tree_accounted = float(tree_info["accounted_usd"])
        raw_cap = tree_info.get("root_limit_usd")
        tree_cap = float(raw_cap) if raw_cap is not None else None
        cap_text = f" of ${tree_cap:.2f} hard tree cap" if tree_cap is not None else ""
        tree_line = (
            f"Task tree spend: ~${tree_accounted:.2f}{cap_text} "
            "(ledger-accounted incl. in-flight holds, subagents included)\n"
        )

    tool_trace = _build_recent_tool_trace(messages)

    reminder = (
        f"[CHECKPOINT {checkpoint_num} — round {round_idx}/{max_rounds}]\n"
        f"Context: ~{ctx_tokens} tokens | Cost so far: {cost_text} | "
        f"Rounds remaining: {max_rounds - round_idx}\n"
        f"{tree_line}"
    )
    if tool_trace:
        reminder += f"\n{tool_trace}\n"
    reminder += (
        "\nThis is a periodic self-check, not a command to stop. "
        "Glance at your recent tool-call trace above and briefly consider:\n"
        "- Are you still making progress toward the task, or repeating the same actions?\n"
        "- Is the current approach still the right one, or should you narrow scope / try a different angle?\n"
        "- If you are waiting on a long build/download/training run or have independent branches of investigation, consider schedule_subagent for a focused parallel handoff.\n"
        "- If the task is effectively done, first re-check the literal original requirements one by one "
        "against the specified interface/path/format/service, then wrap up by replying with your final answer in plain text (no tool call). "
        "Otherwise continue with the most valuable next step.\n"
        "\nNo special format required — just think, then act."
    )

    # Merge into a prior user turn to avoid Anthropic consecutive-role 400s,
    # preserving multipart blocks so images/cache markers survive.
    _append_or_merge_user_message(messages, reminder)
    emit_progress(
        f"Checkpoint {checkpoint_num} at round {round_idx}: "
        f"~{ctx_tokens} tokens, {cost_text} spent"
    )

    checkpoint_payload: Dict[str, Any] = {
        "checkpoint_number": checkpoint_num,
        "round": round_idx,
        "max_rounds": max_rounds,
        "context_tokens": ctx_tokens,
        "task_cost": task_cost,
    }
    if tree_accounted is not None:
        checkpoint_payload["tree_accounted_usd"] = round(tree_accounted, 4)
        checkpoint_payload["tree_cap_usd"] = round(tree_cap, 4) if tree_cap is not None else None
    _emit_checkpoint_event(event_queue, task_id, drive_logs, checkpoint_payload)

    return True


def _maybe_inject_time_budget_milestone(
    messages: List[Dict[str, Any]],
    tools: ToolRegistry,
    *,
    event_queue: Optional[queue.Queue] = None,
    task_id: str = "",
    drive_logs: Optional[pathlib.Path] = None,
    round_idx: int = 0,
    accumulated_usage: Optional[Dict[str, Any]] = None,
) -> bool:
    """Thin transport over the task_pacing SSOT (v6.54.4): the milestone content,
    thresholds, and seen-state live in ouroboros/task_pacing.py; this wrapper only
    appends the note and emits the checkpoint event."""
    note = task_pacing.build_time_budget_note(
        tools._ctx, round_idx=round_idx, accumulated_usage=accumulated_usage,
        # A real ledger read happens ONLY when the pacing note actually fires
        # (per 600s bucket) — the note is a cache-breaking user turn already.
        tree_cost_provider=lambda: _loop_tree_accounting(refresh=True, max_age_sec=30.0),
    )
    if note is None:
        return False
    _append_or_merge_user_message(messages, note.text)
    _emit_checkpoint_event(event_queue, task_id, drive_logs, note.checkpoint)
    return True


def _maybe_inject_cost_budget_milestone(
    messages: List[Dict[str, Any]],
    tools: ToolRegistry,
    *,
    budget_remaining_usd: Optional[float],
    cost_ceiling: Optional["task_pacing.CostCeiling"],
    accumulated_usage: Optional[Dict[str, Any]],
    event_queue: Optional[queue.Queue] = None,
    task_id: str = "",
    drive_logs: Optional[pathlib.Path] = None,
) -> bool:
    """Thin transport over the task_pacing cost axis (v6.56.0): content,
    thresholds, and latch state live in ouroboros/task_pacing.py. The deciding
    spend under a root cap is the tree-accounted stash (free read; refreshed by
    every dispatch) with a bounded staleness cap — never a per-round ledger
    read, see ``_TREE_ACCOUNTING_MAX_STALE_SEC``."""
    ceiling_usd = (
        cost_ceiling.ceiling_usd
        if cost_ceiling is not None and cost_ceiling.state == task_pacing.COST_CEILING_ACTIVE
        else None
    )
    tree_info = _loop_tree_accounting(
        refresh=True, max_age_sec=_TREE_ACCOUNTING_MAX_STALE_SEC,
    )
    tree_cost = tree_info.get("accounted_usd") if isinstance(tree_info, dict) else None
    note = task_pacing.build_cost_budget_note(
        tools._ctx,
        start_remaining_usd=budget_remaining_usd,
        cost_ceiling_usd=ceiling_usd,
        task_cost=(accumulated_usage or {}).get("cost"),
        tree_cost_usd=tree_cost,
        # Whether a tree cap exists at all decides if own cost is the complete
        # picture or a disclosed lower bound (task_pacing.resolve_deciding_spend).
        root_cap_usd=(cost_ceiling.root_cap_usd if cost_ceiling is not None else None),
    )
    if note is None:
        return False
    _append_or_merge_user_message(messages, note.text)
    _emit_checkpoint_event(event_queue, task_id, drive_logs, note.checkpoint)
    return True


# The verbs whose call IS delegated-run activity for the nanny-economics baseline.
# Exact tool-call transitions, observed in the loop as they happen — never a scan
# of the custody log or events.jsonl (the baseline must be free to read per round).
_DELEGATE_ACTIVITY_TOOLS = frozenset({
    "delegate_start", "delegate_wait", "delegate_cancel", "delegate_answer",
})


def _note_nanny_delegate_activity(
    ctx: Any, round_idx: int, accumulated_usage: Dict[str, Any],
    tool_calls: List[Dict[str, Any]],
) -> None:
    """Advance the nanny's metered-progress marker, and its delegate-activity baseline
    when this round actually touched a delegated run.

    Two process-local marks on the ToolContext, written once per round: what the task
    has spent so far (round index + accumulated cost), and where that stood at the
    LAST delegate-verb call. Their difference is the whole input of the proportional
    reminder — the poltergeist children burned $87 of opus rounds co-building around
    their $0 runs, and nothing measured the burn while it happened.
    """
    if not getattr(ctx, "_nanny_route_dispatched", False):
        return
    try:
        cost = float(accumulated_usage.get("cost") or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    mark = {"round": int(round_idx), "cost": cost}
    ctx._nanny_metered_progress = mark
    verbs = set()
    for call in tool_calls or []:
        fn = call.get("function") if isinstance(call, dict) else None
        name = str((fn or {}).get("name") or "").strip() if isinstance(fn, dict) else ""
        if name in _DELEGATE_ACTIVITY_TOOLS:
            verbs.add(name)
    if not verbs:
        return
    if verbs == {"delegate_wait"}:
        # R2-5: a wait is WATCHING, not delegating — it advances only the
        # ROUND half of the baseline. Preserving the COST half keeps the
        # dollar axis cumulative across waits: re-zeroing BOTH axes at every
        # wait never heard the reminder ($0.24/round probe), while a genuinely
        # holding nanny stays under the dollar threshold anyway.
        prior = getattr(ctx, "_nanny_delegate_baseline", None)
        prior_cost = float(prior.get("cost") or 0.0) if isinstance(prior, dict) else 0.0
        ctx._nanny_delegate_baseline = {"round": mark["round"], "cost": prior_cost}
    else:
        ctx._nanny_delegate_baseline = dict(mark)
    # Delegate activity also RE-ARMS the reminder: the fire cursor is
    # cleared so a cooldown earned BEFORE this activity can never mute
    # the reminder for burn that happens AFTER it (gemini, fix F1).
    ctx._nanny_reminder_mark = None


def _nanny_metered_since_delegate_activity(ctx: Any) -> Tuple[int, float]:
    """(rounds, dollars) this task's OWN metered loop has spent since the last
    delegate-verb call — zero before the first round is marked."""
    progress = getattr(ctx, "_nanny_metered_progress", None)
    progress = progress if isinstance(progress, dict) else {}
    baseline = getattr(ctx, "_nanny_delegate_baseline", None)
    baseline = baseline if isinstance(baseline, dict) else {}
    try:
        rounds = max(0, int(progress.get("round") or 0) - int(baseline.get("round") or 0))
    except (TypeError, ValueError):
        rounds = 0
    try:
        cost = max(0.0, float(progress.get("cost") or 0.0) - float(baseline.get("cost") or 0.0))
    except (TypeError, ValueError):
        cost = 0.0
    return rounds, cost


def _nanny_reminder_due(ctx: Any, round_idx: int) -> Tuple[int, float, bool]:
    """The measured burn plus whether the proportional reminder is due THIS round.

    Due when EITHER axis (rounds or dollars, ``task_pacing.NANNY_REMINDER_*``)
    crossed its threshold since the last delegate-verb call. The re-arm is
    dual-axis too (fix F1): the next firing waits for a further threshold-width
    on EITHER axis, so a fast dollar burn is never muted by round spacing. The
    first firing has no spacing gate; delegate activity clears the fire cursor
    (``_note_nanny_delegate_activity``). Proportional and repeating, never a cap
    (owner decision 2=B). With no delegate verb AND no prior firing, the first
    reminder fires early (``NANNY_FIRST_REMINDER_ROUNDS``, owner-approved
    2026-08-15) regardless of dollars; any delegate activity or re-arm restores
    the ordinary dual-axis thresholds unchanged."""
    from ouroboros.task_pacing import (
        NANNY_FIRST_REMINDER_ROUNDS, NANNY_REMINDER_ROUNDS, NANNY_REMINDER_USD,
    )

    rounds, cost = _nanny_metered_since_delegate_activity(ctx)
    round_threshold = NANNY_REMINDER_ROUNDS
    if (
        not isinstance(getattr(ctx, "_nanny_delegate_baseline", None), dict)
        and not isinstance(getattr(ctx, "_nanny_reminder_mark", None), dict)
    ):
        # No delegate verb AND no reminder yet: first firing comes early.
        round_threshold = NANNY_FIRST_REMINDER_ROUNDS
    if rounds < round_threshold and cost < NANNY_REMINDER_USD:
        return rounds, cost, False
    mark = getattr(ctx, "_nanny_reminder_mark", None)
    if not isinstance(mark, dict):
        return rounds, cost, True  # first firing: no spacing gate
    progress = getattr(ctx, "_nanny_metered_progress", None)
    progress = progress if isinstance(progress, dict) else {}
    try:
        rounds_since_fire = int(progress.get("round") or 0) - int(mark.get("round") or 0)
    except (TypeError, ValueError):
        rounds_since_fire = 0
    try:
        cost_since_fire = float(progress.get("cost") or 0.0) - float(mark.get("cost") or 0.0)
    except (TypeError, ValueError):
        cost_since_fire = 0.0
    if rounds_since_fire >= NANNY_REMINDER_ROUNDS or cost_since_fire >= NANNY_REMINDER_USD:
        return rounds, cost, True
    return rounds, cost, False


def _nanny_burn_phrase(rounds: int, cost: float) -> str:
    return (f"{rounds} of your own metered LLM rounds (~${cost:.2f})" if cost > 0
            else f"{rounds} of your own metered LLM rounds")


def _maybe_inject_nanny_economics_reminder(
    round_idx: int,
    messages: List[Dict[str, Any]],
    tools: ToolRegistry,
    emit_progress: Callable[[str], None],
    *,
    event_queue: Optional[queue.Queue] = None,
    task_id: str = "",
    drive_logs: Optional[pathlib.Path] = None,
) -> bool:
    """The periodic half of the nanny-economics reminder (poltergeist phase B).

    A plain user-message reminder in the existing self-checkpoint style — the loop's
    checkpoints are ordinary user turns, never protocol (ARCHITECTURE: "Loop
    self-checkpoints remain plain user-message reminders"). It fires between rounds,
    while the burn is happening, because the finalization nudge alone arrives only
    after the money is spent. Proportional and unbounded in count: each further
    threshold-width of metered rounds re-arms it (owner 2=B — no round cap)."""
    ctx = tools._ctx
    if not getattr(ctx, "_nanny_route_dispatched", False):
        return False
    rounds, cost, due = _nanny_reminder_due(ctx, round_idx)
    if not due:
        return False
    # The fire cursor is the metered-progress mark AT this firing (round + cost),
    # so the dual-axis re-arm in `_nanny_reminder_due` measures both axes from
    # the same instant. Cleared on delegate activity.
    _progress_mark = getattr(ctx, "_nanny_metered_progress", None)
    ctx._nanny_reminder_mark = (dict(_progress_mark) if isinstance(_progress_mark, dict)
                                else {"round": int(round_idx), "cost": 0.0})
    # R2-7c: before the first delegate verb there IS no "last delegated-run
    # activity" — the burn is measured from the task's start, and the wording
    # says so instead of implying an activity that never happened.
    _baseline_known = isinstance(getattr(ctx, "_nanny_delegate_baseline", None), dict)
    since_phrase = ("since your last delegated-run activity" if _baseline_known
                    else "since this task started (no delegated-run activity yet)")
    # BR1-3: never an unconditional "$0" claim — the owner's wording law is
    # typed cost classes: known-zero only on a settled $0 spend, never "free"
    # unqualified (estimated/undisclosed spend is never zero).
    reminder = (
        "[NANNY ECONOMICS REMINDER]\n"
        f"You are a harness-dispatched NANNY and you have spent {_nanny_burn_phrase(rounds, cost)} "
        f"{since_phrase}. A subscription-lane delegated run has known-zero "
        "marginal cost only when its settled spend reports $0 (estimated or "
        "undisclosed spend is never zero); every round you think yourself is "
        "metered API money.\n"
        "This is a reminder, not a stop. Consider: delegate the remaining work "
        "(delegate_start / delegate_wait — follow-up work and fixes are delegated too), "
        "and keep your own rounds for judgment: acceptance, integration, honest "
        "settlement. A deliberate switch_model raise for that judgment is "
        "sanctioned — finish it and drop back. If this work genuinely must run "
        "on metered tokens, continue deliberately and say why in your result."
    )
    _append_or_merge_user_message(messages, reminder)
    # Owner decision (2026-08-15): no owner-chat progress line — the model sees
    # the reminder and the typed task_checkpoint below carries observability.
    _emit_checkpoint_event(event_queue, task_id, drive_logs, {
        "checkpoint_kind": "nanny_economics_reminder",
        "round": round_idx,
        "metered_rounds_since_delegate_activity": rounds,
        "metered_cost_since_delegate_activity_usd": round(cost, 4),
    })
    return True


def _inject_round_checkpoints(
    *,
    round_idx: int,
    max_rounds: int,
    messages: List[Dict[str, Any]],
    accumulated_usage: Dict[str, Any],
    emit_progress: Callable[[str], None],
    tools: ToolRegistry,
    event_queue: Optional[queue.Queue],
    task_id: str,
    drive_logs: Optional[pathlib.Path],
    budget_remaining_usd: Optional[float] = None,
    cost_ceiling: Optional["task_pacing.CostCeiling"] = None,
) -> bool:
    """Inject the per-round self-check and the time-budget / intrinsic-pacing
    milestone AFTER owner messages, so the checkpoint is the LLM-call tail (a
    normal user turn). Returns whether any was injected (routine compaction is
    skipped that round when so)."""
    checkpoint = _maybe_inject_self_check(
        round_idx, max_rounds, messages, accumulated_usage, emit_progress,
        event_queue=event_queue, task_id=task_id, drive_logs=drive_logs,
    )
    time_budget = _maybe_inject_time_budget_milestone(
        messages, tools, event_queue=event_queue, task_id=task_id, drive_logs=drive_logs,
        round_idx=round_idx, accumulated_usage=accumulated_usage,
    )
    cost_budget = _maybe_inject_cost_budget_milestone(
        messages, tools,
        budget_remaining_usd=budget_remaining_usd, cost_ceiling=cost_ceiling,
        accumulated_usage=accumulated_usage,
        event_queue=event_queue, task_id=task_id, drive_logs=drive_logs,
    )
    nanny_economics = _maybe_inject_nanny_economics_reminder(
        round_idx, messages, tools, emit_progress,
        event_queue=event_queue, task_id=task_id, drive_logs=drive_logs,
    )
    return bool(checkpoint or time_budget or cost_budget or nanny_economics)


def seal_task_transcript(
    messages: List[Dict[str, Any]],
    keep_active: int = 5,
    min_prefix_tokens: int = 2048,
) -> None:
    """Mark one stable old tool-result boundary for provider prompt caching."""
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            # Flatten the old sealed boundary before choosing a new one.
            msg["content"] = _extract_plain_text_from_content(content)

    tool_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "tool"
    ]
    if len(tool_indices) <= keep_active:
        return

    seal_candidate_idx = tool_indices[-(keep_active + 1)]

    prefix_text_len = sum(
        len(_extract_plain_text_from_content(m.get("content", "")))
        for m in messages[: seal_candidate_idx + 1]
    )
    prefix_tokens = prefix_text_len // 4  # rough 4-chars-per-token estimate

    if prefix_tokens < min_prefix_tokens:
        return

    candidate = messages[seal_candidate_idx]
    plain_text = str(candidate.get("content", ""))
    if not plain_text.strip():
        # Anthropic 400s on cache_control attached to an empty text block; never seal
        # an empty tool output as the cache anchor (turns the whole task unanswerable).
        plain_text = "(no tool output)"
    candidate["content"] = [
        {
            "type": "text",
            "text": plain_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _setup_dynamic_tools(tools_registry, tool_schemas, messages):
    """Attach list/enable tool handlers and mutate the active schema list."""
    enabled_extra: set = set()
    active_tool_names = {
        str(schema.get("function", {}).get("name") or "").strip()
        for schema in tool_schemas
        if str(schema.get("function", {}).get("name") or "").strip()
    }

    def _handle_list_tools(ctx=None, **kwargs):
        omissions = (
            tools_registry.capability_omissions()
            if hasattr(tools_registry, "capability_omissions")
            else []
        )
        non_core = [
            t for t in list_non_core_tools(tools_registry)
            if t["name"] not in active_tool_names
        ]
        if not non_core:
            if not omissions:
                return "All tools are already in your active set."
            lines = ["All currently discovered tools are already in your active set.", ""]
            lines.extend(format_capability_omissions(omissions))
            return "\n".join(lines)
        lines = [f"**{len(non_core)} additional tools available** (use `enable_tools` to activate):\n"]
        for t in non_core:
            lines.append(f"- **{t['name']}**: {t['description'][:120]}")
        if omissions:
            lines.extend(format_capability_omissions(
                omissions, header="\n" + CAPABILITY_OMISSION_HEADER,
            ))
        return "\n".join(lines)

    def _handle_enable_tools(ctx=None, tools: str = "", **kwargs):
        names = [n.strip() for n in tools.split(",") if n.strip()]
        enabled, hidden, not_found = [], [], []
        for name in names:
            schema = tools_registry.get_schema_by_name(name)
            if schema and name not in active_tool_names:
                tool_schemas.append(schema)
                enabled_extra.add(name)
                active_tool_names.add(name)
                enabled.append(f"{name} (registered late)")
            elif name in active_tool_names:
                enabled.append(f"{name} (already active)")
            else:
                # F3 (2026-08-10 saga): a policy-filtered tool is not "Not found" —
                # answer with the typed reason so the agent stops guessing names.
                reason = (
                    tools_registry.policy_hidden_reason(name)
                    if hasattr(tools_registry, "policy_hidden_reason") else None
                )
                if reason:
                    hidden.append(f"{name} — {reason}")
                else:
                    not_found.append(name)
        parts = []
        if enabled:
            parts.append(
                "✅ Tools are registered in the active capability envelope: "
                + ", ".join(enabled)
            )
        if hidden:
            parts.append(
                "🚫 Hidden by policy (the tool exists but this task cannot use it): "
                + "; ".join(hidden)
            )
        if not_found:
            parts.append(f"❌ Not found: {', '.join(not_found)}")
        return "\n".join(parts) if parts else "No tools specified."

    tools_registry.override_handler("list_available_tools", _handle_list_tools)
    tools_registry.override_handler("enable_tools", _handle_enable_tools)

    non_core_count = len(list_non_core_tools(tools_registry))
    if non_core_count > 0:
        _append_or_merge_user_message(
            messages,
            (
                "[SYSTEM NOTICE]\n"
                f"You have {len(tool_schemas)} core tools loaded. "
                f"There are {non_core_count} additional tools available "
                f"(use `list_available_tools` to see them, `enable_tools` to activate). "
                f"Core tools cover most tasks. Enable extras only when needed."
            ),
        )
    omissions = (
        tools_registry.capability_omissions()
        if hasattr(tools_registry, "capability_omissions")
        else []
    )
    if omissions:
        _append_or_merge_user_message(
            messages,
            "[SYSTEM NOTICE]\n" + "\n".join(format_capability_omissions(omissions)),
        )

    return tool_schemas, enabled_extra


def _direct_child_results(ctx: _RoundLimitContext) -> list[Dict[str, Any]]:
    """Read this node's direct children from the existing task-status authority."""

    try:
        status_root = ctx.status_drive_root or ctx.drive_root or pathlib.Path(ctx.drive_logs).parent
        if status_root is None or not ctx.task_id:
            return []
        return _load_direct_child_results(
            pathlib.Path(status_root),
            ctx.task_id,
            str(ctx.root_task_id or ctx.task_id),
        )
    except Exception:
        return []


def _child_disposition_state(child: Dict[str, Any]) -> str:
    """Return cancellation or the current task-tree exact-hash disposition."""

    # Explicit cancellation is lifecycle authority and wins every completion
    # race. Late scratch results are not projected or recovered. Only a
    # SETTLED ``cancelled`` counts as handled (GR2-8c): the legacy
    # ``cancel_requested`` STATUS is an unsettled latch — intent, not outcome.
    # Treating it as done suppressed the handoff reminder for a child still
    # being torn down; such a child stays visible as cancel-pending until
    # custody settles it.
    if (
        str(child.get("parent_decision") or "").strip().lower() == "cancelled"
        and str(child.get("status") or "").strip().lower() == "cancelled"
    ):
        return "cancelled"
    try:
        from ouroboros.tools.join_ledger import _current_child_result_disposition

        current = _current_child_result_disposition(child)
        if current:
            return current
    except Exception:
        pass
    return ""


def _project_child_result_dispositions(
    ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
) -> None:
    """Expose a compact exact-hash projection for acceptance/outcome reducers."""

    try:
        from ouroboros.tools.join_ledger import _child_result_sha256

        current = []
        for child in _direct_child_results(ctx):
            disposition = _child_disposition_state(child)
            if disposition not in {"integrated", "irrelevant", "deferred"}:
                continue
            current.append({
                "child_task_id": str(child.get("task_id") or child.get("id") or ""),
                "disposition": disposition,
                "child_result_sha256": _child_result_sha256(child),
            })
        llm_trace["child_result_dispositions"] = {
            "current": current,
            "deferred_count": sum(row["disposition"] == "deferred" for row in current),
        }
    except Exception:
        llm_trace["child_result_dispositions"] = {"current": [], "deferred_count": 0}


def _delivery_evidence_state(
    tools: ToolRegistry,
    ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
) -> tuple[int, str]:
    """Fingerprint only evidence that can invalidate a complete answer."""

    from ouroboros.outcomes import read_verification_receipts
    from ouroboros.tools.join_ledger import _child_result_sha256

    owner_directives = getattr(tools._ctx, "_owner_directives", [])
    owner_directives = owner_directives if isinstance(owner_directives, list) else []
    children = []
    for child in _direct_child_results(ctx):
        children.append({
            "task_id": str(child.get("task_id") or child.get("id") or ""),
            "status": str(child.get("status") or ""),
            "sha256": _child_result_sha256(child),
            "disposition": _child_disposition_state(child),
        })
    receipt_root = pathlib.Path(
        str(getattr(tools._ctx, "drive_root", "") or ctx.drive_root or ctx.status_drive_root or ctx.drive_logs.parent)
    )
    evidence = {
        "owner_directives": owner_directives,
        "tool_effects": reviewable_effect_projection(llm_trace),
        # The typed plan-review control is not a filesystem effect, but it
        # changes whether a pre-plan answer is grounded.
        "plan_review_receipts": [
            {
                "index": index,
                "outcome": call.get("plan_review_outcome"),
                "closed": call.get("plan_review_closed"),
                "result": call.get("result"),
            }
            for index, call in enumerate(llm_trace.get("tool_calls") or [])
            if isinstance(call, dict) and call.get("plan_review_outcome")
        ],
        "children": children,
        "verification_receipts": read_verification_receipts(receipt_root, ctx.task_id),
        # Task-scoped service teardown can register declared outputs or surface an
        # output-finalization failure.  Those facts are produced outside an ordinary
        # tool call, so bind their stable projection explicitly; otherwise a host
        # acceptance panel could review the pre-teardown state.
        "service_finalization": _service_finalization_evidence(llm_trace),
    }
    fingerprint = hashlib.sha256(json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")).hexdigest()
    previous = str(getattr(tools._ctx, "_delivery_evidence_fingerprint", "") or "")
    revision = int(getattr(tools._ctx, "_delivery_evidence_revision", 0) or 0)
    if fingerprint != previous:
        candidate = getattr(tools._ctx, "_delivery_candidate", None)
        if (
            isinstance(candidate, DeliveryCandidate)
            and bool(candidate.evidence_fingerprint)
            and candidate.evidence_fingerprint != fingerprint
        ):
            _supersede_delivery_acceptance_binding(
                tools,
                llm_trace,
                candidate,
                reason="delivery_evidence_changed_after_host_acceptance",
            )
        revision += 1
        tools._ctx._delivery_evidence_fingerprint = fingerprint
        tools._ctx._delivery_evidence_revision = revision
    return revision, fingerprint


def _service_finalization_evidence(llm_trace: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Return the stable, answer-relevant part of service finalization events."""

    rows: list[Dict[str, Any]] = []
    stable_fields = (
        "service_id",
        "name",
        "task_id",
        "lifecycle",
        "backend",
        "pid",
        "port",
        "artifact_outputs",
        "artifact_output_failed",
        "artifact_audit_gap",
        "log_finalization",
    )
    for event in llm_trace.get("verification_events") or []:
        if not isinstance(event, dict) or str(event.get("kind") or "") not in {
            "services_stopped",
            "services_kept",
            "service_finalization_error",
        }:
            continue
        services = []
        for service in event.get("services") or []:
            if not isinstance(service, dict):
                continue
            services.append({
                key: service.get(key)
                for key in stable_fields
                if service.get(key) not in (None, "", [], {})
            })
        rows.append({
            "kind": str(event.get("kind") or ""),
            "services": services,
            "error": str(event.get("error") or ""),
        })
    return rows


def _unaccepted_delivery_binding(
    tools: ToolRegistry,
    candidate_hash: str,
) -> Dict[str, Any]:
    fence_value = str(
        getattr(tools._ctx, "_task_acceptance_sealed_fence_token", "")
        or "unsealed"
    )
    return {
        "candidate_sha256": candidate_hash,
        "evidence_revision": int(getattr(tools._ctx, "_delivery_evidence_revision", 0) or 0),
        "acceptance_status": "unaccepted",
        "authoritative": False,
        "panel_id": "",
        "binding_hash": "",
        "fence_hash": hashlib.sha256(fence_value.encode("utf-8")).hexdigest(),
    }


def _delivery_acceptance_binding(
    tools: ToolRegistry,
    llm_trace: Dict[str, Any],
    candidate_hash: str,
) -> Dict[str, Any]:
    """Refresh a candidate from one exact, complete, active host-root verdict."""

    binding = _unaccepted_delivery_binding(tools, candidate_hash)
    review_decision = llm_trace.get("review_decision") if isinstance(llm_trace.get("review_decision"), dict) else {}
    expected_panel = str(review_decision.get("panel_id") or "")
    expected_binding = str(review_decision.get("binding_hash") or "")
    # Candidate text alone is not a review identity: the same full answer can be
    # regenerated after tool/child/verification evidence changes.  Refresh host
    # authority only from the panel the current acceptance pass explicitly names;
    # an older exact-text run must never be rediscovered by a hash-only scan.
    if not expected_panel or not expected_binding:
        return binding
    for raw_run in reversed(llm_trace.get("review_runs") or []):
        if not isinstance(raw_run, dict):
            continue
        if raw_run.get("authority") != "host_root" or raw_run.get("superseded_by_revision"):
            continue
        run_candidate = str(
            raw_run.get("candidate_hash") or raw_run.get("candidate_sha256") or ""
        )
        if run_candidate != candidate_hash:
            continue
        run_panel = str(raw_run.get("panel_id") or "")
        run_binding = str(raw_run.get("binding_hash") or "")
        if not run_panel or not run_binding:
            continue
        if run_panel != expected_panel:
            continue
        if run_binding != expected_binding:
            continue
        verdict = str(
            raw_run.get("aggregate_signal") or raw_run.get("semantic_verdict") or ""
        ).strip().lower()
        if not verdict:
            continue
        binding.update({
            "acceptance_status": verdict,
            "authoritative": True,
            "panel_id": run_panel,
            "binding_hash": run_binding,
            "fence_hash": str(raw_run.get("fence_hash") or binding["fence_hash"]),
            "review_evidence_revision": str(raw_run.get("evidence_revision") or ""),
        })
        break
    return binding


def _publish_delivery_candidate(
    tools: ToolRegistry,
    candidate: DeliveryCandidate,
    llm_trace: Dict[str, Any],
) -> None:
    """Publish hashes/control state only; the complete text remains loop-local."""

    current_fp = str(getattr(tools._ctx, "_delivery_evidence_fingerprint", "") or "")
    llm_trace["delivery_candidate"] = {
        "content_sha256": candidate.content_sha256,
        "revision": candidate.revision,
        "evidence_revision": candidate.evidence_revision,
        "evidence_fingerprint": candidate.evidence_fingerprint,
        "evidence_current": candidate.evidence_fingerprint == current_fp,
        "acceptance_binding": dict(candidate.acceptance_binding),
        "finalization_control": candidate.finalization_control,
        "degraded": candidate.degraded,
        "degraded_reason": candidate.degraded_reason,
    }


def _replace_delivery_candidate(
    tools: ToolRegistry,
    ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
    full_text: str,
    *,
    control: str,
) -> DeliveryCandidate:
    previous_candidate = getattr(tools._ctx, "_delivery_candidate", None)
    if isinstance(previous_candidate, DeliveryCandidate):
        _supersede_delivery_acceptance_binding(
            tools,
            llm_trace,
            previous_candidate,
            reason="delivery_candidate_replaced",
        )
    evidence_revision, evidence_fingerprint = _delivery_evidence_state(tools, ctx, llm_trace)
    content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
    revision = int(getattr(tools._ctx, "_delivery_candidate_revision", 0) or 0) + 1
    tools._ctx._delivery_candidate_revision = revision
    candidate = DeliveryCandidate(
        full_text=full_text,
        content_sha256=content_hash,
        revision=revision,
        evidence_revision=evidence_revision,
        evidence_fingerprint=evidence_fingerprint,
        acceptance_binding=_unaccepted_delivery_binding(tools, content_hash),
        finalization_control=control,
    )
    tools._ctx._delivery_candidate = candidate
    tools._ctx._delivery_control_required = False
    _publish_delivery_candidate(tools, candidate, llm_trace)
    return candidate


def _ensure_explicit_acceptance_binding(candidate: DeliveryCandidate) -> None:
    """Keep an exact historical binding, or state explicitly that none exists."""

    binding = dict(candidate.acceptance_binding or {})
    if binding.get("authoritative") is not True:
        binding.update({
            "acceptance_status": "unaccepted",
            "authoritative": False,
            "panel_id": "",
            "binding_hash": "",
        })
        binding.pop("review_evidence_revision", None)
    candidate.acceptance_binding = binding


def _forced_unaccepted_binding(
    tools: ToolRegistry,
    candidate: DeliveryCandidate,
    reason_code: str,
) -> Dict[str, Any]:
    """Bind a newly generated forced answer without borrowing an older verdict."""

    binding = _unaccepted_delivery_binding(tools, candidate.content_sha256)
    binding.update({
        "acceptance_status": "unaccepted",
        "authoritative": False,
        "degraded": True,
        "degraded_reason": reason_code,
        "panel_id": "",
        "binding_hash": "",
    })
    binding.pop("review_evidence_revision", None)
    return binding


def _live_delivery_candidate(ctx: _RoundLimitContext) -> Optional[DeliveryCandidate]:
    tools = getattr(ctx, "tools", None)
    if tools is not None:
        candidate = getattr(tools._ctx, "_delivery_candidate", None)
        if isinstance(candidate, DeliveryCandidate):
            return candidate
    candidate = getattr(ctx, "delivery_candidate", None)
    return candidate if isinstance(candidate, DeliveryCandidate) else None


def _current_delivery_candidate(
    ctx: Optional[_RoundLimitContext],
    llm_trace: Dict[str, Any],
) -> Optional[DeliveryCandidate]:
    """Return a retained answer only after checking live answer-invalidating evidence."""

    if ctx is None or getattr(ctx, "tools", None) is None:
        return None
    candidate = _live_delivery_candidate(ctx)
    if candidate is None:
        return None
    evidence_revision, evidence_fingerprint = _delivery_evidence_state(
        ctx.tools, ctx, llm_trace,
    )
    if (
        candidate.evidence_revision != evidence_revision
        or candidate.evidence_fingerprint != evidence_fingerprint
    ):
        return None
    return candidate


def _degrade_retained_delivery_candidate(
    ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
    candidate: DeliveryCandidate,
    *,
    control: str,
    reason_code: str,
) -> DeliveryCandidate:
    """Publish a current unchanged candidate while preserving its exact verdict binding."""

    candidate.degraded = True
    candidate.degraded_reason = reason_code
    candidate.finalization_control = control
    _ensure_explicit_acceptance_binding(candidate)
    tools = getattr(ctx, "tools", None)
    if tools is not None:
        _publish_delivery_candidate(tools, candidate, llm_trace)
    ctx.delivery_candidate = candidate
    return candidate


def _record_forced_finalization(
    ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
    *,
    reason_code: str,
    source: str,
    candidate: Optional[DeliveryCandidate],
) -> None:
    # Forced exits bypass the normal no-tool finalization gate. Project child
    # dispositions here, after services/evidence and the returned candidate
    # have been refreshed, so every forced return exposes the same terminal
    # child-result truth to the outcome reducer.
    _project_child_result_dispositions(ctx, llm_trace)
    # Common terminal recorder = the ONE seam covering both the LLM-seam forced
    # answer (`_forced_final_answer`) and the no-spend host-fallback fence path
    # (`_handle_budget_exceeded` -> `_forced_fallback_result`).
    _record_forced_acceptance_bypass(ctx, llm_trace, reason_code)
    binding = dict(candidate.acceptance_binding or {}) if candidate is not None else {}
    tools = getattr(ctx, "tools", None)
    current_fingerprint = str(
        getattr(getattr(tools, "_ctx", None), "_delivery_evidence_fingerprint", "")
        or ""
    )
    current_revision = int(
        getattr(getattr(tools, "_ctx", None), "_delivery_evidence_revision", 0)
        or 0
    )
    llm_trace["forced_finalization"] = {
        "reason_code": reason_code,
        "source": source,
        "degraded": True,
        "candidate_sha256": candidate.content_sha256 if candidate is not None else "",
        "candidate_revision": candidate.revision if candidate is not None else None,
        "evidence_revision": candidate.evidence_revision if candidate is not None else None,
        "current_evidence_revision": current_revision,
        "evidence_current": bool(
            candidate is not None
            and candidate.evidence_fingerprint == current_fingerprint
        ),
        "acceptance_status": str(binding.get("acceptance_status") or "unaccepted"),
        "acceptance_authoritative": bool(binding.get("authoritative", False)),
    }


def _merge_finalization_trace(
    llm_trace: Dict[str, Any],
    returned_trace: Any,
) -> Dict[str, Any]:
    """Merge a forced-path trace without duplicating the live trace object."""

    if not isinstance(returned_trace, dict) or returned_trace is llm_trace:
        return llm_trace
    for key, value in returned_trace.items():
        if isinstance(value, list) and isinstance(llm_trace.get(key), list):
            for item in value:
                if item not in llm_trace[key]:
                    llm_trace[key].append(item)
        elif isinstance(value, dict) and isinstance(llm_trace.get(key), dict):
            llm_trace[key].update(value)
        else:
            llm_trace[key] = value
    return llm_trace


def _delivery_control_prompt(candidate: DeliveryCandidate, *, keep_allowed: bool) -> str:
    keep_line = (
        "keep is allowed because no answer-invalidating evidence changed."
        if keep_allowed
        else "keep is NOT allowed because owner/tool/child/verification evidence changed."
    )
    return (
        "[DELIVERY_FINALIZATION_CONTROL]\n"
        f"A complete answer candidate (revision {candidate.revision}, sha256 "
        f"{candidate.content_sha256[:12]}) is retained by the loop; do not replace it with a "
        f"service notice. {keep_line}\n"
        "Return exactly one JSON object and no other text:\n"
        '{"delivery_control":"keep"}\n'
        "or\n"
        '{"delivery_control":"replace","full_answer":"<the complete user-facing answer>"}'
    )


def _delivery_replace_required(candidate: DeliveryCandidate) -> bool:
    """Return whether a typed full replacement is mandatory for this control round."""

    return candidate.finalization_control.startswith(
        ("effect_revision_required", "skill_revision_required")
    )


def _delivery_keep_allowed(
    candidate: DeliveryCandidate,
    evidence_revision: int,
    evidence_fingerprint: str,
) -> bool:
    return (
        not _delivery_replace_required(candidate)
        and candidate.evidence_revision == evidence_revision
        and candidate.evidence_fingerprint == evidence_fingerprint
    )


def _arm_delivery_control(
    tools: ToolRegistry,
    ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
    *,
    control: str = "awaiting_control",
) -> None:
    candidate = getattr(tools._ctx, "_delivery_candidate", None)
    if not isinstance(candidate, DeliveryCandidate):
        return
    evidence_revision, evidence_fingerprint = _delivery_evidence_state(tools, ctx, llm_trace)
    candidate.finalization_control = control
    candidate.repair_attempted = False
    tools._ctx._delivery_control_required = True
    _append_or_merge_user_message(
        ctx.messages,
        _delivery_control_prompt(
            candidate,
            keep_allowed=_delivery_keep_allowed(
                candidate, evidence_revision, evidence_fingerprint,
            ),
        ),
    )
    _publish_delivery_candidate(tools, candidate, llm_trace)


def _hold_delivery_for_skill_action(
    tools: ToolRegistry,
    llm_trace: Dict[str, Any],
) -> None:
    """Retain the answer while an unresolved skill lifecycle gate requires action."""

    candidate = getattr(tools._ctx, "_delivery_candidate", None)
    if not isinstance(candidate, DeliveryCandidate):
        return
    candidate.finalization_control = "skill_action_or_revision_required"
    candidate.repair_attempted = False
    tools._ctx._delivery_control_required = False
    _publish_delivery_candidate(tools, candidate, llm_trace)


def _parse_delivery_control_object(
    raw: str,
) -> tuple[Optional[Dict[str, Any]], bool]:
    """Parse a delivery-control object while rejecting duplicate JSON keys.

    The boolean preserves protocol intent for the repair path when a duplicate
    ``delivery_control`` or ``full_answer`` key made the object invalid.
    """

    duplicate_protocol_key = False

    def _unique_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        nonlocal duplicate_protocol_key
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                if key in {"delivery_control", "full_answer"}:
                    duplicate_protocol_key = True
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, duplicate_protocol_key
    if not isinstance(payload, dict):
        return None, False
    return payload, False


def _resolve_delivery_control(
    content: Any,
    tools: ToolRegistry,
    ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
) -> tuple[str, str]:
    """Return ``retry`` or a complete answer text before any existing gate runs."""

    candidate = getattr(tools._ctx, "_delivery_candidate", None)
    required = bool(getattr(tools._ctx, "_delivery_control_required", False))
    if not isinstance(candidate, DeliveryCandidate):
        return "fresh", _extract_plain_text_from_content(content)
    raw = _extract_plain_text_from_content(content).strip()
    parsed, duplicate_protocol_key = _parse_delivery_control_object(raw)
    # ANY parsed object carrying the protocol key is control intent, regardless of
    # verb/value — an unknown verb is a mangled protocol attempt, never prose (raw
    # JSON leaked to chat). Verb/shape validity is judged below (repair path).
    is_control_intent = duplicate_protocol_key or (
        isinstance(parsed, dict) and "delivery_control" in parsed
    )
    if not required:
        if _delivery_replace_required(candidate):
            # A writer/skill action cannot silently turn a short acknowledgement
            # into the new complete answer, even if a caller lost the transient
            # required latch. The candidate's typed control state is authoritative.
            required = True
            tools._ctx._delivery_control_required = True
        elif candidate.finalization_control == "skill_action_or_revision_required":
            # Preserve the historical bounded skill gate: an actual tool action
            # or a reconsidered full prose answer may proceed, but a typed keep
            # cannot acknowledge the gate. Do not inject the delivery JSON prompt
            # before the action because it would conflict with the instruction to
            # call the skill lifecycle tool.
            if not is_control_intent:
                return "fresh", _extract_plain_text_from_content(content)
            candidate.finalization_control = "skill_revision_required"
            required = True
            tools._ctx._delivery_control_required = True
        else:
            # An owner revision starts an ordinary substantive answer round. If
            # the model nevertheless follows the prior typed instruction, honor
            # that control structurally; service/effect/skill rounds are handled
            # by the replace-required branch above.
            if not (
                candidate.finalization_control == "owner_revision_required"
                and is_control_intent
            ):
                return "fresh", _extract_plain_text_from_content(content)
            tools._ctx._delivery_control_required = True
    evidence_revision, evidence_fingerprint = _delivery_evidence_state(tools, ctx, llm_trace)
    error = "control must be one exact JSON object"
    selected = str(parsed.get("delivery_control") or "") if isinstance(parsed, dict) else ""
    valid = False
    replacement = ""
    if selected == "keep" and set(parsed) == {"delivery_control"}:
        valid = _delivery_keep_allowed(
            candidate, evidence_revision, evidence_fingerprint,
        )
        error = "keep cannot bind changed evidence; send replace with the complete answer"
    elif selected == "replace" and set(parsed) == {"delivery_control", "full_answer"}:
        replacement_value = parsed.get("full_answer")
        if isinstance(replacement_value, str):
            replacement = replacement_value
        valid = isinstance(replacement_value, str) and bool(replacement.strip())
        error = "replace requires a non-empty complete full_answer"

    if valid and selected == "keep":
        tools._ctx._delivery_control_required = False
        candidate.finalization_control = "keep"
        candidate.acceptance_binding = _delivery_acceptance_binding(
            tools, llm_trace, candidate.content_sha256,
        )
        _publish_delivery_candidate(tools, candidate, llm_trace)
        return "resolved", candidate.full_text
    if valid and selected == "replace":
        updated = _replace_delivery_candidate(
            tools, ctx, llm_trace, replacement, control="replace",
        )
        return "resolved", updated.full_text

    if not candidate.repair_attempted:
        candidate.repair_attempted = True
        candidate.finalization_control = (
            f"{candidate.finalization_control}_repair_requested"
            if _delivery_replace_required(candidate)
            else "repair_requested"
        )
        if raw:
            ctx.messages.append({"role": "assistant", "content": raw})
        _append_or_merge_user_message(
            ctx.messages,
            "[DELIVERY_CONTROL_REPAIR] Invalid finalization control: " + error + ".\n"
            + _delivery_control_prompt(
                candidate,
                keep_allowed=_delivery_keep_allowed(
                    candidate, evidence_revision, evidence_fingerprint,
                ),
            ),
        )
        _publish_delivery_candidate(tools, candidate, llm_trace)
        return "retry", ""

    tools._ctx._delivery_control_required = False
    candidate.degraded = True
    candidate.degraded_reason = "invalid_delivery_control_after_repair"
    candidate.finalization_control = "degraded_preserve"
    # The control failed, not the retained text. Bind that unchanged text to
    # the evidence the failed control was meant to acknowledge so the stale
    # check cannot reopen another control round. It remains explicitly
    # unaccepted; the ordinary host acceptance gate still judges this exact
    # candidate/evidence pair before publication.
    candidate.evidence_revision = evidence_revision
    candidate.evidence_fingerprint = evidence_fingerprint
    candidate.acceptance_binding = _unaccepted_delivery_binding(
        tools, candidate.content_sha256,
    )
    llm_trace["reasoning_notes"].append(
        "Delivery finalization control remained invalid after one repair; preserved the prior complete answer."
    )
    _publish_delivery_candidate(tools, candidate, llm_trace)
    return "degraded", candidate.full_text


def _compose_delivery_suffix(full_text: str, suffix: str) -> str:
    """Compose one host-owned suffix into the exact delivered/candidate text."""

    text = str(full_text or "")
    note = str(suffix or "")
    if not note or text.endswith(note):
        return text
    return text + note


def _forced_orphan_note(ctx: _RoundLimitContext, *, include_terminal: bool = True) -> str:
    """A bounded note listing children the parent did NOT explicitly handle (discard/cancel),
    appended to a finalization so paid child work is never SILENTLY orphaned (P1; P5 — no
    prose parsing). On a FORCED finalization (deadline / provider death / finalize_now,
    ``include_terminal=True``) the parent was cut off and may not have seen completions, so
    RUNNING and COMPLETED-undecided children are both reported. On a NORMAL no-tool
    finalization (``include_terminal=False``) the agent was reminded of every change
    (including completions) before choosing to finalize, so only STILL-RUNNING undecided
    children — genuinely orphaned by finalizing mid-flight — are reported. Never raises."""
    try:
        from ouroboros.task_status import FINAL_STATUSES

        children = _direct_child_results(ctx)
        claimed = _claimed_child_dispositions(ctx)

        def _undecided(c: Dict[str, Any]) -> bool:
            if _child_disposition_state(c) in {
                "integrated", "irrelevant", "deferred", "discarded", "cancelled",
            }:
                return False  # explicitly handled
            if not include_terminal and str(c.get("status") or "").strip().lower() in FINAL_STATUSES:
                return False  # completed children were already surfaced via the reminder
            return True

        undecided = [c for c in children if _undecided(c)]
        deferred = [c for c in children if _child_disposition_state(c) == "deferred"]

        def _label(c: Dict[str, Any]) -> str:
            tid = str(c.get("task_id") or c.get("id") or "?")
            st = str(c.get("status") or "?").strip().lower()
            lifecycle = "running" if st not in FINAL_STATUSES else st
            # W2: a child whose LATEST blackboard decision row no longer binds
            # the current result was READ and decided — say that, not "unread".
            # Say only what the ledger PROVES: the row EXISTS; the binding to
            # the standing result did not. Scoped to children the projection
            # genuinely left UNDECIDED: a carried disposition (deferred /
            # integrated / irrelevant / discarded / cancelled) is not a
            # failed binding, and "re-submit to close it" would be false there.
            claim = claimed.get(tid) if not _child_disposition_state(c) else None
            if claim is not None:
                disposition, row_sha = claim
                from ouroboros.tools.join_ledger import _child_result_sha256

                if _child_result_sha256(c) != row_sha:
                    detail = (
                        f"{disposition} recorded for an EARLIER result hash; the current "
                        "result is not bound — re-inspect and re-submit the current hash"
                    )
                else:
                    detail = (
                        f"{disposition} recorded for this exact result hash but not carried "
                        "by this round's disposition projection — re-submit to close it"
                    )
                return f"{tid} [{lifecycle}; {detail}]"
            terminal = str(c.get("child_status") or "").strip().lower()
            if terminal and terminal != st:
                return f"{tid} [{lifecycle}; terminal_result={terminal}]"
            return f"{tid} [{lifecycle}]"

        notes: list[str] = []
        if undecided:
            listed = "; ".join(_label(c) for c in undecided[:10])
            more = f" (+{len(undecided) - 10} more)" if len(undecided) > 10 else ""
            lead = "finalized under a hard limit with" if include_terminal else "finalized with"
            detail = (
                "running ones may be incomplete, completed ones may be UNREAD"
                if include_terminal else
                "still-running children not absorbed or discarded"
            )
            notes.append(
                f"\n\n⚠️ NOTE: {lead} {len(undecided)} child task(s) not explicitly absorbed or "
                f"discarded — {detail}: {listed}{more}. Inspect with get_task_result(<id>) / "
                f"peek_task(<id>)."
            )
        if deferred:
            listed = "; ".join(_label(c) for c in deferred[:10])
            more = f" (+{len(deferred) - 10} more)" if len(deferred) > 10 else ""
            notes.append(
                f"\n\n⚠️ DEFERRED CHILD RESULTS: {listed}{more}. These exact results were "
                "explicitly deferred, so this answer is degraded/best-effort rather than clean solved."
            )
        return "".join(notes)
    except Exception:
        return ""


def _claimed_child_dispositions(ctx: _RoundLimitContext) -> Dict[str, tuple]:
    """task_id -> (disposition, row_sha) from THIS parent's latest blackboard
    decision rows (W2). Consulted only for children the disposition projection
    left undecided: a row that exists but no longer binds is audit evidence of a
    claimed-but-failed disposition write, and the forced orphan note must say so
    instead of calling the child unread. Pure read, never raises."""
    try:
        from ouroboros.task_tree_ledger import CHILD_RESULT_DISPOSITION_TYPE, tree_ledger_rows

        status_root = (
            getattr(ctx, "status_drive_root", None)
            or getattr(ctx, "drive_root", None)
        )
        root_id = str(getattr(ctx, "root_task_id", "") or getattr(ctx, "task_id", "") or "")
        parent_id = str(getattr(ctx, "task_id", "") or "")
        if status_root is None or not root_id or not parent_id:
            return {}
        claims: Dict[str, tuple] = {}
        for row in tree_ledger_rows(root_id, data_root=pathlib.Path(status_root)):
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            if (
                str(row.get("kind") or "") == "decision"
                and str(payload.get("type") or "") == CHILD_RESULT_DISPOSITION_TYPE
                and str(row.get("task_id") or "") == parent_id
                and str(payload.get("child_task_id") or "")
            ):
                # Later rows win: the ledger is append-only and the newest decision
                # is the one whose failure to bind is worth naming.
                claims[str(payload["child_task_id"])] = (
                    str(payload.get("disposition") or ""),
                    str(payload.get("child_result_sha256") or ""),
                )
        return claims
    except Exception:
        return {}


def _undispositioned_children(ctx: _RoundLimitContext) -> list[Dict[str, Any]]:
    try:
        return [
            child for child in _direct_child_results(ctx)
            if _child_disposition_state(child) not in {
                "integrated", "irrelevant", "deferred", "discarded", "cancelled",
            }
        ]
    except Exception:
        return []


def _maybe_enforce_child_absorption_gate(
    tools: ToolRegistry,
    limit_ctx: _RoundLimitContext,
    content: Any,
    messages: List[Dict[str, Any]],
    emit_progress: Callable[[str], None],
    llm_trace: Dict[str, Any],
) -> Optional[Tuple[str, Dict[str, Any], Dict[str, Any]] | str]:
    undecided = _undispositioned_children(limit_ctx)
    if not undecided:
        return None
    if not getattr(tools._ctx, "_child_absorption_reminded", False):
        tools._ctx._child_absorption_reminded = True
        if content and str(content).strip():
            messages.append({"role": "assistant", "content": content})
        from ouroboros.tools.join_ledger import _child_result_sha256

        listed = "; ".join(
            f"{c.get('task_id') or c.get('id') or '?'} [{c.get('status') or 'unknown'}] "
            f"sha256={_child_result_sha256(c)}"
            for c in undecided[:10]
        )
        reminder = (
            "[CHILD_ABSORPTION_REQUIRED]\n"
            "You have child result(s) without a current exact-hash disposition: "
            f"{listed}. Before a clean final answer, inspect unfinished children or record a "
            "tree_note(kind='decision') payload with type=child_result_disposition, child_task_id, "
            "disposition=integrated|irrelevant|deferred, and the shown child_result_sha256. "
            "To disposition several children in ONE call, pass a children array instead: "
            "payload={'type': 'child_result_disposition', 'children': [{'child_task_id': ..., "
            "'disposition': ..., 'child_result_sha256': ...}, ...]}. "
            "discard_child_result remains the shorthand for irrelevant. This is a bounded reminder; "
            "ignoring it will finalize best_effort, not clean."
        )
        _append_or_merge_user_message(messages, reminder)
        emit_progress("Child absorption reminder injected before final response.")
        llm_trace["reasoning_notes"].append("Child absorption reminder injected before final response.")
        return "continue"
    text, usage, forced_trace = _forced_final_answer(
        limit_ctx,
        prompt=(
            "[FINALIZE_WITH_UNABSORBED_CHILDREN]\n"
            "You still have child results without exact dispositions and already received one "
            "child-absorption reminder. Produce an honest best-effort final answer now; name the "
            "unabsorbed or unfinished children explicitly."
        ),
        fallback_text="⚠️ Finalized best-effort with undispositioned child results.",
        reason_code="children_unabsorbed",
    )
    _merge_finalization_trace(llm_trace, forced_trace)
    _run_forced_children_acceptance(
        tools, limit_ctx, undecided, text, messages, emit_progress, llm_trace,
    )
    return text, usage, llm_trace


def _run_forced_children_acceptance(
    tools: ToolRegistry,
    limit_ctx: _RoundLimitContext,
    undecided: list[Dict[str, Any]],
    text: str,
    messages: List[Dict[str, Any]],
    emit_progress: Callable[[str], None],
    llm_trace: Dict[str, Any],
) -> None:
    """Content acceptance still runs on the forced children_unabsorbed rail (owner Q2A).

    The panel uses the ORDINARY entry point (`_run_task_acceptance_review_once`)
    after the forced answer text exists but BEFORE the loop seals it; the evidence
    packet carries the undispositioned children via the ctx stash. The forced rail
    can never take another model round, so a ``True`` return terminalizes here: a
    requested improvement pass downgrades to ``finalized_unaccepted``, while a WAIT
    shape that never ran the panel keeps the typed acceptance-bypass verdict from
    `_record_forced_finalization`. Never raises — salvage outranks review."""
    if not str(text or "").strip():
        return
    tools_ctx = tools._ctx
    try:
        from ouroboros.tools.join_ledger import _child_result_sha256

        debt = [
            {
                "task_id": str(c.get("task_id") or c.get("id") or ""),
                "status": str(c.get("status") or "unknown"),
                "child_result_sha256": _child_result_sha256(c),
            }
            for c in undecided[:20]
            if isinstance(c, dict)
        ]
        if len(undecided) > 20:
            # Explicit omission marker: a >20-child debt list must not read as complete.
            debt.append({"omitted": len(undecided) - 20, "total": len(undecided)})
        tools_ctx._forced_undispositioned_children = debt
        another_round = _run_task_acceptance_review_once(
            tools=tools,
            content=str(text),
            task_id=limit_ctx.task_id,
            task_type=limit_ctx.task_type,
            llm_trace=llm_trace,
            drive_root=limit_ctx.drive_root,
            messages=messages,
            emit_progress=emit_progress,
        )
        if not another_round:
            return
        tools_ctx._task_acceptance_reviewed = True
        _end_task_acceptance_fence(tools_ctx, outcome="terminal")
        decision = llm_trace.get("acceptance_decision")
        status = str(decision.get("status") or "") if isinstance(decision, dict) else ""
        if status == ACCEPTANCE_REVISION_REQUESTED:
            # A panel DID run and asked for an improvement pass; record the honest
            # terminal state instead of leaving a dangling revision request.
            _set_acceptance_decision(llm_trace, {
                "status": ACCEPTANCE_FINALIZED_UNACCEPTED,
                "reason": "revision_unavailable_on_forced_rail",
                "source": "forced_finalization",
                "rationale": (
                    "The acceptance panel requested an improvement pass, but the "
                    "forced children_unabsorbed rail cannot take another model round."
                ),
            })
            emit_progress(
                "Task acceptance ran on the forced rail; the requested improvement "
                "pass is unavailable, finalizing unaccepted."
            )
    except Exception:
        log.debug("Forced children_unabsorbed acceptance run failed", exc_info=True)
    finally:
        tools_ctx._forced_undispositioned_children = None


def _enforce_swarm_actions(
    content: str,
    messages: List[Dict[str, Any]],
    tools: ToolRegistry,
    llm_trace: Dict[str, Any],
    emit_progress: Callable[[str], None],
) -> bool:
    """Hold normal finalization while routing or blocking plan work is open."""

    if swarm_router_turn(tools._ctx) and not _swarm_handoff_attempt(tools._ctx):
        if content.strip():
            messages.append({"role": "assistant", "content": content})
        reminder = (
            "[SWARM_ROUTING_INTENT] Admit exactly one new managed root now with "
            "promote_chat_to_task, or from Main route_to_project for a clearly matching "
            "existing Project. Do not answer inline or steer an existing task."
        )
        _append_or_merge_user_message(messages, reminder)
        llm_trace["reasoning_notes"].append(reminder)
        emit_progress("Swarm routing action required before final response.")
        return True

    decision = _force_plan_decision(tools._ctx, llm_trace)
    if decision.get("required"):
        llm_trace["force_plan_decision"] = decision
    if decision.get("allow"):
        return False
    if content.strip():
        messages.append({"role": "assistant", "content": content})
    reminder = _force_plan_reminder(decision)
    _append_or_merge_user_message(messages, reminder)
    llm_trace["reasoning_notes"].append(reminder)
    emit_progress("Plan-review action required before final response.")
    return True


def _no_tool_final_answer(
    content: Any,
    limit_ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
    tools: ToolRegistry,
    incoming_messages: queue.Queue,
    owner_msg_seen: set,
    emit_progress: Callable[[str], None],
) -> Optional[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """Run the no-tool finalization gates; ``None`` requests another model round."""
    messages = limit_ctx.messages
    control_state, controlled_content = _resolve_delivery_control(
        content, tools, limit_ctx, llm_trace,
    )
    if control_state == "retry":
        return None
    content = controlled_content
    _project_child_result_dispositions(limit_ctx, llm_trace)
    if control_state == "fresh" and str(content or "").strip():
        candidate = _replace_delivery_candidate(
            tools, limit_ctx, llm_trace, str(content), control="candidate",
        )
        content = candidate.full_text
    else:
        candidate = getattr(tools._ctx, "_delivery_candidate", None)
        if isinstance(candidate, DeliveryCandidate):
            content = candidate.full_text

    if _enforce_swarm_actions(
        str(content or ""), messages, tools, llm_trace, emit_progress,
    ):
        return None
    handoff_msg = _compute_subagent_handoff(tools, limit_ctx.drive_root, limit_ctx.task_id, content)
    if handoff_msg:
        if content and content.strip():
            messages.append({"role": "assistant", "content": content})
        _append_or_merge_user_message(messages, f"[SYSTEM REMINDER]\n{handoff_msg}")
        emit_progress("Subagent handoff status refreshed before final response.")
        llm_trace["reasoning_notes"].append("Subagent handoff status refreshed before final response.")
        _arm_delivery_control(tools, limit_ctx, llm_trace)
        return None
    absorption_result = _maybe_enforce_child_absorption_gate(
        tools, limit_ctx, content, messages, emit_progress, llm_trace,
    )
    if absorption_result == "continue":
        _arm_delivery_control(tools, limit_ctx, llm_trace)
        return None
    if absorption_result is not None:
        return absorption_result
    skill_finalization_was_injected = bool(
        getattr(tools._ctx, "_skill_finalization_injected", False)
    )
    if _maybe_inject_finalization_nudges(
        tools, limit_ctx.drive_root, limit_ctx.task_id, llm_trace, content, messages, emit_progress,
    ):
        skill_finalization_injected_now = (
            not skill_finalization_was_injected
            and bool(getattr(tools._ctx, "_skill_finalization_injected", False))
        )
        # Skill finalization is an action gate, not a service notice. Preserve
        # the candidate without adding a conflicting JSON-only instruction: the
        # next round may run the required tool or provide the historically
        # allowed reconsidered full answer, but a typed keep cannot close it.
        if skill_finalization_injected_now:
            _hold_delivery_for_skill_action(tools, llm_trace)
        else:
            _arm_delivery_control(tools, limit_ctx, llm_trace)
        return None

    # Declared service outputs and teardown failures are acceptance evidence, not
    # postscript cleanup.  Finalize them before the authoritative host panel and,
    # when that changes evidence, require one complete replacement answer bound to
    # the new revision.  The finally-path calls the same idempotent helper as a
    # safety net for forced/error exits.
    service_exit_ctx = _LoopExitContext(
        tools=tools,
        drive_root=limit_ctx.drive_root,
        task_id=limit_ctx.task_id,
        event_queue=limit_ctx.event_queue,
        drive_logs=limit_ctx.drive_logs,
        accumulated_usage=limit_ctx.accumulated_usage,
        llm_trace=llm_trace,
    )
    if _finalize_task_services(service_exit_ctx):
        evidence_revision, evidence_fingerprint = _delivery_evidence_state(
            tools, limit_ctx, llm_trace,
        )
        candidate = getattr(tools._ctx, "_delivery_candidate", None)
        if (
            isinstance(candidate, DeliveryCandidate)
            and (
                candidate.evidence_revision != evidence_revision
                or candidate.evidence_fingerprint != evidence_fingerprint
            )
        ):
            if content and str(content).strip():
                messages.append({"role": "assistant", "content": str(content)})
            llm_trace["reasoning_notes"].append(
                "Task services were finalized before acceptance; the complete answer must bind the resulting evidence."
            )
            _arm_delivery_control(tools, limit_ctx, llm_trace)
            return None

    _project_child_result_dispositions(limit_ctx, llm_trace)
    plan_suffix = _force_plan_disclosure(tools._ctx, llm_trace)
    orphan_suffix = _forced_orphan_note(limit_ctx, include_terminal=False)
    normal_suffix = plan_suffix + orphan_suffix
    composed_content = _compose_delivery_suffix(str(content or ""), normal_suffix)
    candidate = getattr(tools._ctx, "_delivery_candidate", None)
    if composed_content and (
        not isinstance(candidate, DeliveryCandidate)
        or candidate.full_text != composed_content
    ):
        candidate = _replace_delivery_candidate(
            tools,
            limit_ctx,
            llm_trace,
            composed_content,
            control="host_suffix" if normal_suffix else "candidate",
        )
    if isinstance(candidate, DeliveryCandidate):
        if orphan_suffix:
            candidate.degraded = True
            candidate.degraded_reason = "host_child_status_suffix"
            _publish_delivery_candidate(tools, candidate, llm_trace)
        elif plan_suffix:
            candidate.degraded = True
            candidate.degraded_reason = "plan_review_advisory"
            _publish_delivery_candidate(tools, candidate, llm_trace)
        content = candidate.full_text

    tools._ctx._acceptance_loop_rails = {
        "round_idx": limit_ctx.round_idx,
        "max_rounds": limit_ctx.max_rounds,
        "task_cost_usd": limit_ctx.accumulated_usage.get("cost"),
    }
    # v6.78.0 (owner Q20/Q22): mirror the host-attested native-retrieval fact into the
    # trace so `build_task_acceptance_evidence` can show the reviewer whether the answer
    # was grounded in fetched pages. Reviewer-side only — the agent never sees it (it
    # receives the improvement capsule, not the evidence packet).
    _retrieval = limit_ctx.accumulated_usage.get("retrieval")
    if isinstance(_retrieval, dict) and _retrieval:
        llm_trace["retrieval"] = dict(_retrieval)
    if _run_task_acceptance_review_once(
        tools=tools,
        content=content or "",
        task_id=limit_ctx.task_id,
        task_type=limit_ctx.task_type,
        llm_trace=llm_trace,
        drive_root=limit_ctx.drive_root,
        messages=messages,
        emit_progress=emit_progress,
    ):
        # v6.71.1: an acceptance improvement pass is an ORDINARY substantive
        # answer round — do NOT arm delivery-control here: layering "return
        # exactly one JSON object" on top of OPEN OBLIGATIONS and the self-
        # check froze the model into resubmitting the same answer. The next
        # free-form answer re-enters the acceptance panel, so blocking is not
        # weakened; other lanes still arm where JSON keep/replace is needed.
        return None
    candidate = getattr(tools._ctx, "_delivery_candidate", None)
    if isinstance(candidate, DeliveryCandidate):
        candidate.acceptance_binding = _delivery_acceptance_binding(
            tools, llm_trace, candidate.content_sha256,
        )
        _publish_delivery_candidate(tools, candidate, llm_trace)

    # Close delivery under the same lock as routing, then drain once. A follow-up
    # either forces another round or is rejected after the fence, never stranded.
    admission_lock = getattr(tools._ctx, "owner_message_admission_lock", None)
    admission_agent = getattr(tools._ctx, "owner_message_admission_agent", None)
    if admission_lock is not None and admission_agent is not None:
        before_directives = len(getattr(tools._ctx, "_owner_directives", []) or [])
        acceptance_was_terminal = bool(
            getattr(tools._ctx, "_task_acceptance_reviewed", False)
            or getattr(tools._ctx, "_task_acceptance_sealed_fence_token", None)
        )
        provisional_assistant = {"role": "assistant", "content": content} if content else None
        if provisional_assistant is not None:
            messages.append(provisional_assistant)
        with admission_lock:
            admission_agent._accepting_owner_messages = False
            post_controls = _drain_incoming_messages(
                messages, incoming_messages, limit_ctx.drive_root, limit_ctx.task_id,
                limit_ctx.event_queue, owner_msg_seen, owner_ctx=tools._ctx,
            )
        if len(getattr(tools._ctx, "_owner_directives", []) or []) > before_directives:
            with admission_lock:
                if acceptance_was_terminal:
                    _supersede_task_acceptance_for_owner_followup(
                        tools._ctx, llm_trace, admission_locked=True,
                    )
                if (
                    getattr(admission_agent, "_busy", False)
                    and str(getattr(admission_agent, "_current_task_id", "") or "") == limit_ctx.task_id
                ):
                    admission_agent._accepting_owner_messages = True
            if acceptance_was_terminal:
                emit_progress(
                    "Task acceptance review superseded: an owner follow-up arrived before finalization."
                )
            # An owner directive is a substantive revision request, not a service
            # notification. The next complete response creates a fresh candidate.
            tools._ctx._delivery_control_required = False
            if isinstance(candidate, DeliveryCandidate):
                candidate.finalization_control = "owner_revision_required"
                _delivery_evidence_state(tools, limit_ctx, llm_trace)
                _publish_delivery_candidate(tools, candidate, llm_trace)
            return None
        if provisional_assistant is not None and messages[-1] is provisional_assistant:
            messages.pop()
        if post_controls.get("finalize_now"):
            text, usage, forced_trace = _handle_forced_finalization(
                limit_ctx, str(post_controls.get("finalize_now") or "deadline"),
            )
            _merge_finalization_trace(llm_trace, forced_trace)
            return text, usage, llm_trace
    _project_child_result_dispositions(limit_ctx, llm_trace)
    evidence_revision, evidence_fingerprint = _delivery_evidence_state(
        tools, limit_ctx, llm_trace,
    )
    candidate = getattr(tools._ctx, "_delivery_candidate", None)
    if (
        isinstance(candidate, DeliveryCandidate)
        and (
            candidate.evidence_revision != evidence_revision
            or candidate.evidence_fingerprint != evidence_fingerprint
        )
    ):
        acceptance_was_terminal = bool(
            getattr(tools._ctx, "_task_acceptance_reviewed", False)
            or getattr(tools._ctx, "_task_acceptance_sealed_fence_token", None)
        )
        if acceptance_was_terminal:
            decision = (
                llm_trace.get("review_decision")
                if isinstance(llm_trace.get("review_decision"), dict)
                else {}
            )
            expected_panel = str(decision.get("panel_id") or "")
            expected_binding = str(decision.get("binding_hash") or "")
            active_run = next(
                (
                    run
                    for run in reversed(llm_trace.get("review_runs") or [])
                    if isinstance(run, dict)
                    and run.get("authority") == "host_root"
                    and not run.get("superseded_by_revision")
                    and str(run.get("panel_id") or "") == expected_panel
                    and str(run.get("binding_hash") or "") == expected_binding
                ),
                None,
            )
            _supersede_task_acceptance_for_evidence_change(
                tools._ctx,
                llm_trace,
                active_run,
                "delivery_evidence_changed_after_host_acceptance",
                messages,
                emit_progress,
            )
        if candidate.full_text:
            messages.append({"role": "assistant", "content": candidate.full_text})
        llm_trace["reasoning_notes"].append(
            "Delivery evidence changed after host acceptance; a complete replacement answer is required."
        )
        _arm_delivery_control(tools, limit_ctx, llm_trace)
        return None
    if isinstance(candidate, DeliveryCandidate):
        candidate.acceptance_binding = _delivery_acceptance_binding(
            tools, llm_trace, candidate.content_sha256,
        )
        _publish_delivery_candidate(tools, candidate, llm_trace)
        content = candidate.full_text
    return _handle_text_response(
        str(content or ""),
        llm_trace,
        limit_ctx.accumulated_usage,
    )


def _finalize_forced_services(
    ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
) -> None:
    """Finalize services and expose their stable projection before forced synthesis."""

    tools = getattr(ctx, "tools", None)
    if tools is None:
        return
    _finalize_task_services(_LoopExitContext(
        tools=tools,
        drive_root=ctx.drive_root,
        task_id=ctx.task_id,
        event_queue=ctx.event_queue,
        drive_logs=ctx.drive_logs,
        accumulated_usage=ctx.accumulated_usage,
        llm_trace=llm_trace,
    ))
    _delivery_evidence_state(tools, ctx, llm_trace)
    projection = _service_finalization_evidence(llm_trace)
    if not projection:
        return
    payload = json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if ctx.forced_service_evidence_fingerprint == fingerprint:
        return
    from ouroboros.observability import redact_projection

    ctx.forced_service_evidence_fingerprint = fingerprint
    safe_payload = truncate_review_artifact(
        str(redact_projection(payload).value),
        limit=8000,
    )
    _append_or_merge_user_message(
        ctx.messages,
        "[SERVICE_FINALIZATION_EVIDENCE]\n"
        "Task services were finalized before forced synthesis. Incorporate this "
        f"evidence and disclose any failure honestly:\n{safe_payload}",
    )


def _drain_forced_owner_directives(
    ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
) -> bool:
    """Drain typed owner input after a forced call and advance answer evidence."""

    tools = getattr(ctx, "tools", None)
    if tools is None:
        return False
    incoming = ctx.incoming_messages
    if incoming is None:
        incoming = queue.Queue()
    seen = ctx.owner_msg_seen
    if not isinstance(seen, set):
        seen = set()
        ctx.owner_msg_seen = seen
    directives = getattr(tools._ctx, "_owner_directives", None)
    before = len(directives) if isinstance(directives, list) else 0
    _drain_incoming_messages(
        ctx.messages,
        incoming,
        ctx.drive_root,
        ctx.task_id,
        ctx.event_queue,
        seen,
        owner_ctx=tools._ctx,
    )
    directives = getattr(tools._ctx, "_owner_directives", None)
    after = len(directives) if isinstance(directives, list) else 0
    if after <= before:
        return False
    candidate = _live_delivery_candidate(ctx)
    binding = (
        candidate.acceptance_binding
        if isinstance(candidate, DeliveryCandidate)
        and isinstance(candidate.acceptance_binding, dict)
        else {}
    )
    if (
        binding.get("authoritative") is True
        or bool(getattr(tools._ctx, "_task_acceptance_reviewed", False))
        or bool(getattr(tools._ctx, "_task_acceptance_sealed_fence_token", None))
    ):
        _supersede_task_acceptance_for_owner_followup(tools._ctx, llm_trace)
    _delivery_evidence_state(tools, ctx, llm_trace)
    return True


def _call_forced_model_once(ctx: _RoundLimitContext) -> str:
    final_msg, _final_cost = call_llm_with_retry(
        ctx.llm,
        ctx.messages,
        ctx.active_model,
        None,
        ctx.active_effort,
        ctx.max_retries,
        ctx.drive_logs,
        ctx.task_id,
        ctx.round_idx,
        ctx.event_queue,
        ctx.accumulated_usage,
        ctx.task_type,
        use_local=ctx.active_use_local,
        deadline_ts=ctx.deadline_ts,
    )
    return str((final_msg or {}).get("content") or "").strip()


def _publish_model_forced_candidate(
    ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
    full_text: str,
    reason_code: str,
) -> Optional[DeliveryCandidate]:
    """Replace the retained answer and invalidate any verdict for the old SHA."""

    tools = getattr(ctx, "tools", None)
    if tools is None:
        return None
    candidate = _replace_delivery_candidate(
        tools,
        ctx,
        llm_trace,
        full_text,
        control=f"forced_replace:{reason_code}",
    )
    candidate.acceptance_binding = _forced_unaccepted_binding(
        tools, candidate, reason_code,
    )
    candidate.degraded = True
    candidate.degraded_reason = reason_code
    _publish_delivery_candidate(tools, candidate, llm_trace)
    ctx.delivery_candidate = candidate
    return candidate


def _publish_stale_forced_candidate(
    ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
    stale_candidate: DeliveryCandidate,
    reason_code: str,
    suffix: str,
) -> Optional[DeliveryCandidate]:
    """Preserve useful old text without pretending it absorbed newer evidence."""

    tools = getattr(ctx, "tools", None)
    if tools is None:
        return None
    current_revision, _current_fingerprint = _delivery_evidence_state(
        tools, ctx, llm_trace,
    )
    disclosure = (
        "\n\n⚠️ STALE-EVIDENCE NOTICE — RESUME REQUIRED (host): The preserved "
        "answer above was produced before newer task evidence reached the loop. "
        "It has not been regenerated or accepted against that newer evidence and "
        "does not claim to incorporate it. Resume the task to produce and review "
        "a complete answer against the latest evidence."
    )
    full_text = _compose_delivery_suffix(
        _compose_delivery_suffix(stale_candidate.full_text, suffix),
        disclosure,
    )
    candidate = _replace_delivery_candidate(
        tools,
        ctx,
        llm_trace,
        full_text,
        control=f"forced_stale_preserve:{reason_code}",
    )
    # The host-added disclosure is current, but the substantive answer it
    # qualifies is not. Preserve the answer's original evidence provenance so
    # every projection remains conservative instead of laundering unchanged
    # text onto the newer fingerprint.
    candidate.evidence_revision = stale_candidate.evidence_revision
    candidate.evidence_fingerprint = stale_candidate.evidence_fingerprint
    candidate.acceptance_binding = _forced_unaccepted_binding(
        tools, candidate, reason_code,
    )
    candidate.acceptance_binding.update({
        "evidence_revision": stale_candidate.evidence_revision,
        "current_evidence_revision": current_revision,
        "stale_evidence": True,
    })
    candidate.degraded = True
    candidate.degraded_reason = reason_code
    _publish_delivery_candidate(tools, candidate, llm_trace)
    ctx.delivery_candidate = candidate
    return candidate


def _forced_fallback_result(
    ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
    fallback_text: str,
    reason_code: str,
    *,
    source: str = "host_fallback",
    retained_source: str = "",
    retained_control: str = "",
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """Return one exact candidate; reuse only current unchanged full text."""

    router_result = _forced_swarm_router_result(ctx, llm_trace, reason_code)
    if router_result is not None:
        return router_result
    tool_ctx = getattr(getattr(ctx, "tools", None), "_ctx", None)
    plan_suffix = (
        _force_plan_disclosure(tool_ctx, llm_trace, forced_reason=reason_code)
        if tool_ctx is not None else ""
    )
    suffix = plan_suffix + _forced_orphan_note(ctx)
    live_candidate = _live_delivery_candidate(ctx)
    fallback_is_retained_model_text = (
        isinstance(live_candidate, DeliveryCandidate)
        and fallback_text == live_candidate.full_text
    )
    candidate = _current_delivery_candidate(ctx, llm_trace)
    if candidate is not None:
        composed = _compose_delivery_suffix(candidate.full_text, suffix)
        if composed != candidate.full_text:
            candidate = _publish_model_forced_candidate(
                ctx, llm_trace, composed, reason_code,
            )
            ctx.accumulated_usage["_best_effort_extracted"] = True
            _record_forced_finalization(
                ctx,
                llm_trace,
                reason_code=reason_code,
                source=(
                    f"{retained_source}_with_host_suffix"
                    if retained_source else "retained_candidate_with_host_suffix"
                ),
                candidate=candidate,
            )
            return composed, ctx.accumulated_usage, llm_trace
        _degrade_retained_delivery_candidate(
            ctx,
            llm_trace,
            candidate,
            control=retained_control or f"forced_preserve:{reason_code}",
            reason_code=reason_code,
        )
        # The preserved candidate is a previously model-produced complete answer.
        ctx.accumulated_usage["_best_effort_extracted"] = True
        _record_forced_finalization(
            ctx,
            llm_trace,
            reason_code=reason_code,
            source=retained_source or "retained_candidate",
            candidate=candidate,
        )
        return candidate.full_text, ctx.accumulated_usage, llm_trace

    if fallback_is_retained_model_text and live_candidate is not None:
        candidate = _publish_stale_forced_candidate(
            ctx,
            llm_trace,
            live_candidate,
            reason_code,
            suffix,
        )
        if candidate is not None:
            ctx.accumulated_usage["_best_effort_extracted"] = True
            _record_forced_finalization(
                ctx,
                llm_trace,
                reason_code=reason_code,
                source=f"{source}_stale_evidence_resume_required",
                candidate=candidate,
            )
            return candidate.full_text, ctx.accumulated_usage, llm_trace

    composed = _compose_delivery_suffix(fallback_text, suffix)
    candidate = _publish_model_forced_candidate(
        ctx, llm_trace, composed, reason_code,
    )
    if fallback_is_retained_model_text:
        ctx.accumulated_usage["_best_effort_extracted"] = True
    _record_forced_finalization(
        ctx,
        llm_trace,
        reason_code=reason_code,
        source=source,
        candidate=candidate,
    )
    return composed, ctx.accumulated_usage, llm_trace


def _forced_swarm_router_result(
    ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
    reason_code: str,
) -> Optional[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """Use deterministic routing text only when a real rail ends the router."""

    tools = getattr(ctx, "tools", None)
    if tools is None or not swarm_router_turn(tools._ctx):
        return None
    attempt = _swarm_handoff_attempt(tools._ctx)
    status = str(attempt.get("status") or "not_attempted")
    task_id = str(attempt.get("task_id") or "")
    if status == "scheduled":
        text = f"✅ Swarm admitted managed task {task_id}. Work continues in that task."
    elif status == "unconfirmed":
        text = (
            f"⚠️ Swarm attempted managed task {task_id}, but admission was not confirmed. "
            "No second routing event was emitted; keep the task id for reconciliation."
        )
    elif status == "rejected":
        detail = str(attempt.get("reason") or "admission rejected")
        text = f"⚠️ Swarm could not admit a new managed task ({detail}). No retry was emitted."
    else:
        text = (
            f"⚠️ Swarm reached the task-wide rail `{reason_code}` before a managed-root "
            "admission attempt completed. No inline work was published."
        )
    full_text = _compose_delivery_suffix(text, _forced_orphan_note(ctx))
    candidate = _replace_delivery_candidate(
        tools, ctx, llm_trace, full_text, control=f"forced_swarm_router:{reason_code}",
    )
    if status != "scheduled":
        candidate.degraded = True
        candidate.degraded_reason = reason_code
    _publish_delivery_candidate(tools, candidate, llm_trace)
    if status == "scheduled":
        # The short acknowledgement hit a rail, but the requested managed work
        # was already durably admitted. Keep that successful handoff truthful.
        ctx.accumulated_usage.pop("execution_status", None)
        ctx.accumulated_usage.pop("reason_code", None)
    else:
        ctx.accumulated_usage.update(execution_status="failed", reason_code=reason_code)
    _record_forced_finalization(
        ctx,
        llm_trace,
        reason_code=reason_code,
        source="host_swarm_routing_fallback",
        candidate=candidate,
    )
    return candidate.full_text, ctx.accumulated_usage, llm_trace


def _resolve_forced_delivery_control(
    tools_ctx: Any,
    extracted: str,
) -> Tuple[str, str]:
    """PURE, no-retry delivery-control resolution for the forced rail.

    While the latch is armed, the one forced answer may legitimately be the
    protocol object ``{"delivery_control": ...}`` — shipped raw it leaked
    protocol JSON into the owner's chat and the durable result. Resolve it
    before suffix composition, never re-looping (``_resolve_delivery_control``
    can inject a repair round, which a hard forced stop must never do): valid
    ``keep`` = the retained candidate's full text, valid ``replace`` =
    ``full_answer``, malformed/duplicate/invalid = the retained candidate with
    the typed degraded reason. Armed protocol intent is ANY parsed object with
    the ``delivery_control`` key AND any JSON-looking text that fails to parse
    (the model was told to answer with the object, so that is a mangled
    control, never the answer). JSON while NOT armed passes through untouched.
    Disclosed residual: armed PROSE stands as-is. Clears the latch. Returns
    ``(resolved_text, degraded_reason)``."""
    if tools_ctx is None or not extracted:
        return extracted, ""
    candidate = getattr(tools_ctx, "_delivery_candidate", None)
    candidate = candidate if isinstance(candidate, DeliveryCandidate) else None
    armed = bool(getattr(tools_ctx, "_delivery_control_required", False)) or (
        candidate is not None and _delivery_replace_required(candidate)
    )
    if not armed:
        return extracted, ""
    tools_ctx._delivery_control_required = False
    parsed, duplicate_protocol_key = _parse_delivery_control_object(extracted)
    # Protocol intent: any parsed object with the protocol key (unknown verb =
    # broken control, never prose), or JSON-looking text that fails to parse (a
    # mangled protocol attempt under the armed latch — the candidate is the answer).
    protocol_intent = duplicate_protocol_key or (
        ("delivery_control" in parsed)
        if isinstance(parsed, dict)
        else extracted.lstrip().startswith("{")
    )
    if not protocol_intent:
        # An ordinary prose answer under an armed latch: the fresh text stands.
        return extracted, ""
    selected = str(parsed.get("delivery_control") or "") if isinstance(parsed, dict) else ""
    if selected == "replace" and set(parsed) == {"delivery_control", "full_answer"}:
        replacement = parsed.get("full_answer")
        if isinstance(replacement, str) and replacement.strip():
            return replacement, ""
    elif selected == "keep" and set(parsed) == {"delivery_control"} and candidate is not None:
        return candidate.full_text, ""
    # Malformed/duplicate/invalid control: preserve the retained candidate (or,
    # with none retained, let the caller's fallback text stand) and say so.
    return (
        candidate.full_text if candidate is not None else "",
        REASON_DELIVERY_CONTROL_DEGRADED,
    )


def _forced_delegation_note(tools_ctx: Any, llm_trace: Dict[str, Any]) -> str:
    """The nanny postcondition's forced-path half, grounded in DURABLE custody.

    A forced finalization may not re-loop, so the substrate fact rides the one
    final prompt. `delegate_custody.task_execution_evidence` on the custody root
    (canonical/budget root — the split-root rule Phase A fixed) decides, not just
    this execution's trace: succeeded → no note; started-but-unsettled → pending
    wording (no retry pressure); settled-without-success → truthful failure
    wording; zero started with readable evidence → the no-delegation wording;
    unreadable evidence → no accusation."""
    if not getattr(tools_ctx, "_nanny_route_dispatched", False):
        return ""
    try:
        from ouroboros import delegate_custody

        root = delegate_custody.custody_root(tools_ctx)
        log_path = delegate_custody.event_log_path(root)
        if log_path.exists():
            # _iter_rows swallows OSError, which would misread an unreadable log
            # as "zero runs" — probe readability so absence of rows is a fact.
            log_path.open("rb").close()
        evidence = delegate_custody.task_execution_evidence(
            root, str(getattr(tools_ctx, "task_id", "") or ""),
        )
    except Exception:
        log.debug("Forced-path custody evidence unreadable; nanny note skipped", exc_info=True)
        return ""
    started = int(evidence.get("delegated_runs_started") or 0)
    settled = int(evidence.get("delegated_runs_settled") or 0)
    if int(evidence.get("delegated_runs_succeeded") or 0):
        # The proportional silence must not extend to FORCED exits (grok / F16):
        # a wrap-up forced by an overrun still owes the parent the honest-spend
        # line. One shot, riding the single forced prompt — never a re-loop.
        rounds, cost = _nanny_metered_since_delegate_activity(tools_ctx)
        from ouroboros.task_pacing import NANNY_REMINDER_ROUNDS, NANNY_REMINDER_USD

        if rounds >= NANNY_REMINDER_ROUNDS or cost >= NANNY_REMINDER_USD:
            return (
                "\nNOTE: your delegated run(s) succeeded, but you have since spent "
                f"{_nanny_burn_phrase(rounds, cost)} with no delegated-run activity. "
                "Account for that metered spend honestly in your answer."
            )
        return ""
    if started > settled:
        return (
            "\nNOTE: this task dispatched delegated run(s) that have not settled "
            f"yet ({started - settled} of {started} pending). State their status "
            "in your answer; do not claim the delegated work finished."
        )
    if settled:
        return (
            f"\nNOTE: this task's delegated run(s) settled WITHOUT success ({settled} "
            "run(s)). State that failure and its impact honestly in your answer."
        )
    if any(str(c.get("tool") or "") == "delegate_start"
           for c in (llm_trace.get("tool_calls") or []) if isinstance(c, dict)):
        # The trace shows a dispatch the durable rows have not recorded — never
        # accuse over evidence that is behind the task's own actions.
        return ""
    return (
        "\nNOTE: this task was dispatched onto the delegated substrate "
        "(executor=harness) and made no delegate_start calls — the work ran on "
        "metered API tokens. State why in your answer."
    )


def _forced_final_answer(
    ctx: _RoundLimitContext,
    *,
    prompt: str,
    fallback_text: str,
    reason_code: str,
    single_semantic_turn: bool = False,
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """Force one tool-less final answer; stamp the typed forced-finalization
    reason code (the best_effort outcome gate reads it downstream).
    ``single_semantic_turn`` (owner-stop rail, CF-03): exactly ONE logical
    model call — the late-owner-directive semantic refresh is disabled because
    steering is fenced while the stop intent is pending."""
    live_trace = getattr(ctx, "llm_trace", None)
    llm_trace = live_trace if isinstance(live_trace, dict) else {}
    _finalize_forced_services(ctx, llm_trace)
    router_result = _forced_swarm_router_result(ctx, llm_trace, reason_code)
    if router_result is not None:
        return router_result
    tools_ctx = getattr(getattr(ctx, "tools", None), "_ctx", None)
    prompt += _forced_delegation_note(tools_ctx, llm_trace)
    _append_or_merge_user_message(ctx.messages, prompt)
    extracted = ""
    for attempt in range(1 if single_semantic_turn else 2):
        try:
            extracted = _call_forced_model_once(ctx)
        except BudgetExceeded:
            _drain_forced_owner_directives(ctx, llm_trace)
            raise
        except Exception:
            log.warning("Failed to get final response after %s", reason_code, exc_info=True)
            extracted = ""
        ctx.accumulated_usage["execution_status"] = "failed"
        ctx.accumulated_usage["reason_code"] = reason_code
        if not _drain_forced_owner_directives(ctx, llm_trace):
            break
        if attempt == 1:
            return _forced_fallback_result(
                ctx,
                llm_trace,
                (
                    "⚠️ A new owner directive arrived during the forced refresh and could "
                    "not be incorporated safely before the hard stop. Resume the task to "
                    "produce an answer bound to the latest directive."
                ),
                reason_code,
                source="late_owner_directive_requires_resume",
            )
        _finalize_forced_services(ctx, llm_trace)
        _append_or_merge_user_message(
            ctx.messages,
            "[FORCED_OWNER_REFRESH] A new typed owner directive arrived while the prior "
            "forced answer was being generated. Discard that stale draft and produce one "
            "new complete answer bound to every owner directive now present.",
        )

    extracted, control_degraded = _resolve_forced_delivery_control(
        getattr(getattr(ctx, "tools", None), "_ctx", None), extracted,
    )
    if extracted:
        # Typed fact for the best_effort outcome gate: a REAL model answer
        # was extracted (host fallback strings never set this).
        ctx.accumulated_usage["_best_effort_extracted"] = True
        tool_ctx = getattr(getattr(ctx, "tools", None), "_ctx", None)
        plan_suffix = (
            _force_plan_disclosure(tool_ctx, llm_trace, forced_reason=reason_code)
            if tool_ctx is not None else ""
        )
        full_text = _compose_delivery_suffix(
            extracted, plan_suffix + _forced_orphan_note(ctx),
        )
        candidate = _publish_model_forced_candidate(
            ctx, llm_trace, full_text, reason_code,
        )
        if control_degraded and candidate is not None:
            candidate.degraded_reason = control_degraded
            llm_trace.setdefault("reasoning_notes", []).append(
                "Forced finalization received an invalid delivery-control object; "
                "preserved the retained complete answer."
            )
            if getattr(ctx, "tools", None) is not None:
                _publish_delivery_candidate(ctx.tools, candidate, llm_trace)
        _record_forced_finalization(
            ctx,
            llm_trace,
            reason_code=reason_code,
            source="model",
            candidate=candidate,
        )
        return (
            candidate.full_text if candidate is not None else full_text,
            ctx.accumulated_usage,
            llm_trace,
        )
    return _forced_fallback_result(
        ctx,
        llm_trace,
        fallback_text,
        reason_code,
    )


def _apply_runtime_overrides(
    ctx: Any,
    active_model: str,
    active_use_local: bool,
    active_effort: str,
) -> Tuple[str, bool, str]:
    """Apply one-shot per-round model/locality/effort overrides from tool ctx."""
    if ctx.active_model_override:
        active_model = ctx.active_model_override
        ctx.active_model_override = None
    if getattr(ctx, "active_use_local_override", None) is not None:
        active_use_local = ctx.active_use_local_override
        ctx.active_use_local_override = None
    if ctx.active_effort_override:
        active_effort = normalize_reasoning_effort(ctx.active_effort_override, default=active_effort)
        ctx.active_effort_override = None
    return active_model, active_use_local, active_effort


def _apply_overrides_and_regate_mode(ctx, active_model, active_use_local, active_effort, active_context_mode):
    """Apply per-round overrides; route rebind never predicts a mode change."""
    active_model, active_use_local, active_effort = _apply_runtime_overrides(
        ctx, active_model, active_use_local, active_effort,
    )
    return active_model, active_use_local, active_effort, active_context_mode


def _rebind_context_fit_plan(
    plan: Any,
    tools: ToolRegistry,
    messages: List[Dict[str, Any]],
    *,
    model: str,
    use_local: bool,
    preferred_mode: str,
    tool_schemas: List[Dict[str, Any]],
) -> Tuple[Any, str]:
    """Recalibrate the captured immutable core for one new exact route.

    Route switches reuse the plan's already-rendered Low/Max projections; only
    exact-route evidence, calibration, and fit are rebound.  This avoids both a
    stale initial-route retry plan and a second context-builder/intent corpus.
    """
    if plan is None or not all(
        hasattr(plan, name) for name in ("max_projection", "low_projection", "core_sha256")
    ):
        raise RuntimeError(
            "CONTEXT_FIT_REBUILD_FAILED: immutable context core is unavailable for route switch"
        )
    from ouroboros.capability_evidence import is_known
    from ouroboros.context import _context_fit_route
    from ouroboros.context_fit import _failed_route_evidence, _route_calibration_ratio

    metadata = getattr(tools._ctx, "task_metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    task = {
        "model": model,
        "use_local_model": use_local,
        "task_metadata": metadata,
        "delegation_role": metadata.get("delegation_role"),
    }
    is_subagent = str(metadata.get("delegation_role") or "").lower() == "subagent"
    try:
        route, evidence = _context_fit_route(task, allow_fetch=not is_subagent)
    except Exception:
        log.debug("Route-switch capability probe failed; preserving unknown Max", exc_info=True)
        route, evidence = _failed_route_evidence(task)
    ratio = _route_calibration_ratio(
        None,  # canonical evidence root (one observation store)
        str(getattr(evidence, "route_fp", "") or ""),
        str(route.get("model") or model),
    )
    known_window = is_known(evidence, require_fresh=True)
    window_tokens = int(getattr(evidence, "window_tokens", 0) or 0)

    def project(projection: Any) -> Any:
        calibrated = int(int(projection.estimated_tokens or 0) * ratio)
        fits = (
            calibrated + int(plan.output_reserve_tokens or 0) <= window_tokens
            if known_window else None
        )
        return replace(
            projection,
            calibrated_tokens=calibrated,
            calibration_ratio=ratio,
            fits_known_window=fits,
        )

    max_projection = project(plan.max_projection)
    low_projection = project(plan.low_projection)
    preferred = preferred_mode if preferred_mode in {"low", "max"} else "max"
    initial_mode = preferred
    rebound = replace(
        plan,
        preferred_mode=preferred,
        initial_mode=initial_mode,
        model=str(route.get("model") or model),
        provider=str(route.get("provider") or ""),
        route_fp=str(getattr(evidence, "route_fp", "") or ""),
        status=str(getattr(evidence, "status", "") or ""),
        stale=bool(getattr(evidence, "stale", False)),
        window_tokens=window_tokens,
        max_projection=max_projection,
        low_projection=low_projection,
    )
    mode = initial_mode
    projected_prompt_tokens = rebound.projected_tokens_with_tools(mode, tool_schemas)
    messages[:] = rebound.reproject_transcript(messages, mode)
    tools._ctx.context_fit_plan = rebound
    tools._ctx.messages = messages
    tools._ctx.active_context_mode = mode
    try:
        _emit_checkpoint_event(
            getattr(tools._ctx, "event_queue", None),
            str(getattr(tools._ctx, "task_id", "") or ""),
            tools._ctx.drive_logs(),
            {
                "checkpoint_kind": "context_fit_route_rebound",
                "model": rebound.model,
                "route_fp": rebound.route_fp,
                "core_sha256": rebound.core_sha256,
                "preferred_mode": preferred,
                "effective_mode": mode,
                "evidence_status": rebound.status,
                "window_tokens": rebound.window_tokens,
                "projected_prompt_tokens": projected_prompt_tokens,
            },
        )
    except Exception:
        log.debug("Failed to emit route-switch context-fit checkpoint", exc_info=True)
    return rebound, mode


def _nanny_finalization_message(
    tools: ToolRegistry, drive_root: pathlib.Path, task_id: str,
    trace_attempted: bool = False,
) -> str:
    """The honest nanny reminder for a harness-dispatched child at finalization —
    or '' when no reminder is deserved.

    F4 (2026-08-10 saga): the old reminder accused children whose delegated runs
    CRASHED of "choosing" not to delegate, and fired even when the delegate verbs
    were policy-hidden. Two structural facts fix both: the task's own visible
    toolset, and durable custody evidence (delegate_custody.
    task_execution_evidence), which spans the WHOLE task — per-execution
    llm_trace resets on continuation. `trace_attempted` is the third fact: a
    delegate_start in THIS execution's trace. It must not suppress the failure
    message (triad finding on e84475f2: delegate, run dies, finish by hand,
    finalize — all inside ONE execution), only the accusation when custody has
    no rows yet (a pending/uncustodied start is an attempt, not a choice)."""
    try:
        if "delegate_start" not in set(tools.available_tools()):
            return ""  # the verbs are invisible here; "you chose not to" would be false
    except Exception:
        log.debug("nanny nudge: toolset visibility check failed", exc_info=True)
    evidence: Dict[str, Any] = {}
    try:
        from ouroboros.delegate_custody import custody_root, task_execution_evidence

        # Split-root fix (2026-08-10 amendments): custody WRITES land on the
        # CANONICAL (budget) root, but this read used the loop's drive_root —
        # a split-root subagent's child drive has no custody rows, leaving
        # the nanny blind. Resolve the SAME root the writers use; the passed
        # drive_root stays the fallback (e.g. unit-test stubs).
        try:
            evidence_root = custody_root(tools._ctx)
        except Exception:
            evidence_root = drive_root
        evidence = task_execution_evidence(evidence_root, str(task_id or ""))
    except Exception:
        log.debug("nanny nudge: custody evidence read failed", exc_info=True)
    if evidence.get("delegated_runs_succeeded"):
        # The route WAS used and worked — but "used once" is not a permanent
        # license: the poltergeist children each ran ONE successful $0 run,
        # then co-built for tens of opus rounds while this early return kept
        # the nudge silent. Silence is now proportional to the measured burn
        # since the last delegated-run activity.
        rounds, cost = _nanny_metered_since_delegate_activity(tools._ctx)
        from ouroboros.task_pacing import NANNY_REMINDER_ROUNDS, NANNY_REMINDER_USD

        if rounds < NANNY_REMINDER_ROUNDS and cost < NANNY_REMINDER_USD:
            return ""
        return (
            "⚠️ NANNY_METERED_OVERRUN: your delegated run(s) succeeded, but you have "
            f"since spent {_nanny_burn_phrase(rounds, cost)} with no delegated-run "
            "activity. A successful run is verified and integrated, not rebuilt. If "
            "the remaining work is substantive, delegate it (a new delegate_start); "
            "if you are wrapping up, keep the wrap-up short and account for the "
            "metered spend honestly in your result."
        )
    started = int(evidence.get("delegated_runs_started") or 0)
    if not started and (evidence.get("evidence_read_failed") or not evidence):
        # Zero attempts is an ACCUSATION and needs positively-established
        # evidence: an unreadable custody log (or a failed read above) proves
        # nothing (scope finding on a5e59bdf).
        return ""
    if not started and trace_attempted:
        # A start this execution's trace saw but custody has no row for: pending
        # settlement or an uncustodied start. An attempt either way — neither
        # accusation fits, and the wait/cancel path owns its own disclosure.
        return ""
    settled = int(evidence.get("delegated_runs_settled") or 0)
    failure_states = [str(s) for s in (evidence.get("delegated_run_failure_states") or [])]
    pending = max(0, started - settled)
    if pending:
        # PENDING ≠ FAILED (sol review on b49f8192): a STARTED row with no
        # settlement may still be executing — calling it failed invites a
        # duplicate run, and finalizing over it orphans the result. Takes
        # precedence over the failed message: with a run in flight, "retry"
        # is wrong even when an earlier sibling died (still a fact below).
        failed_note = (
            f" {len(failure_states)} earlier run(s) already ended: {', '.join(failure_states)}."
            if failure_states else ""
        )
        return (
            "⚠️ NANNY_DELEGATED_RUN_PENDING: you routed work onto the delegated "
            f"substrate and {pending} delegated run(s) have started but not "
            "settled — they may still be executing. Do not finalize over an "
            "in-flight delegated run (its result would be orphaned) and do not "
            "start a duplicate: wait for or check it (delegate_wait) before "
            "finalizing, or cancel it (delegate_cancel) and say so." + failed_note
        )
    if started:
        states = ", ".join(failure_states) or "settled without a recorded terminal state"
        return (
            "⚠️ NANNY_DELEGATED_RUN_FAILED: you DID route work onto the delegated "
            f"substrate ({started} run(s) started), but none succeeded — your "
            f"delegated run(s) ended: {states}. Do not finalize as if delegation "
            "was never attempted: either retry it (delegate_start / delegate_wait) "
            "or state in your final answer that the delegated run failed and why "
            "the remaining work ran on metered API tokens."
        )
    return (
        "⚠️ NANNY_DID_NOT_DELEGATE: this task was dispatched onto the delegated "
        "substrate (executor=harness), but you are finalizing with ZERO "
        "delegate_start calls — the work would end up billed to metered API "
        "tokens the parent asked to avoid. Either delegate the remaining work "
        "now (delegate_start / delegate_wait), or finalize with an explicit "
        "statement of WHY delegation was not used (route refused, work shape "
        "unsuited, deadline) so your parent sees the substrate decision."
    )


def _maybe_inject_finalization_nudges(
    tools: ToolRegistry, drive_root: Optional[pathlib.Path], task_id: str,
    llm_trace: Dict[str, Any], content: Optional[str], messages: List[Dict[str, Any]],
    emit_progress: Callable[[str], None],
) -> bool:
    """One-shot pre-finalization injections that each re-loop (return True): the skill
    finalization reminder, then the FR3 verify-before-done nudge. Extracted from
    run_llm_loop to keep it under the method size gate."""
    if drive_root is None:
        return False
    if (getattr(tools._ctx, "_nanny_route_dispatched", False)
            and not getattr(tools._ctx, "_nanny_finalization_injected", False)):
        # Nanny postcondition (owner decision, 2026-08-07): a child dispatched
        # onto the delegated substrate must not finalize as if that decision
        # never existed. One structural fact, one re-loop; the child may still
        # delegate OR finalize with a typed reason — never a hard gate (P5).
        # A delegate_start in THIS trace rides into the message decision
        # (triad, e84475f2), where custody evidence separates a failed run
        # (NANNY_DELEGATED_RUN_FAILED) from a pending attempt (no message).
        # Suppression cases live in _nanny_finalization_message.
        _trace_attempted = any(
            str(c.get("tool") or "") == "delegate_start"
            for c in (llm_trace.get("tool_calls") or [])
            if isinstance(c, dict)
        )
        tools._ctx._nanny_finalization_injected = True
        _nanny_msg = _nanny_finalization_message(
            tools, drive_root, task_id, trace_attempted=_trace_attempted,
        )
        if _nanny_msg:
            if content and content.strip():
                messages.append({"role": "assistant", "content": content})
            _append_or_merge_user_message(messages, f"[SYSTEM REMINDER]\n{_nanny_msg}")
            # Owner decision (2026-08-15): no owner-chat progress line — the
            # model sees the [SYSTEM REMINDER], the trace keeps the durable
            # text, and the typed task_checkpoint carries observability.
            _emit_checkpoint_event(
                getattr(tools._ctx, "event_queue", None), task_id,
                getattr(tools._ctx, "drive_logs", None),
                {"checkpoint_kind": "nanny_finalization_nudge",
                 "nanny_code": _nanny_msg.split(":", 1)[0].replace("⚠️", "").strip()},
            )
            llm_trace["reasoning_notes"].append(_nanny_msg)
            return True
    finalization_msg = _skill_finalization_message(drive_root, llm_trace)
    if finalization_msg and not getattr(tools._ctx, "_skill_finalization_injected", False):
        tools._ctx._skill_finalization_injected = True
        if content and content.strip():
            messages.append({"role": "assistant", "content": content})
        _append_or_merge_user_message(messages, f"[SYSTEM REMINDER]\n{finalization_msg}")
        emit_progress(finalization_msg)
        llm_trace["reasoning_notes"].append(finalization_msg)
        return True
    if not getattr(tools._ctx, "_verify_red_nudged", False):
        # Red-verification one-shot nudge: the latest host-attested verify receipt
        # is RED and unreconciled — finalizing over your own failing check is a
        # self-contradiction (Bible P3/P12), distinct from receipt_absent below
        # ("no grounding" vs "grounding says FAIL"). Ordered BEFORE the FR3 verify
        # nudge. Binary latch; advisory; forced-finalization paths bypass it.
        # Keyed on the typed receipt status, never content (Bible P5).
        _failed_receipt = latest_unreconciled_failed_verification(drive_root, task_id)
        if _failed_receipt is not None:
            tools._ctx._verify_red_nudged = True
            _check = str(_failed_receipt.get("check") or "").strip()
            _rc = _failed_receipt.get("returncode")
            _on = f" on `{_check}`" if _check else ""
            _exit = f" (exit {_rc})" if _rc is not None else ""
            if content and content.strip():
                messages.append({"role": "assistant", "content": content})
            _append_or_merge_user_message(
                messages,
                "[SYSTEM REMINDER]\nYour latest host-attested verification is RED" + _on + _exit +
                ". Before a clean final answer, reconcile it: re-check it, explain why this check is "
                "not the task's acceptance contract, or fix and re-run verification. This is advisory — "
                "if you finalize anyway, make the residual risk explicit.",
            )
            emit_progress("Red-verification nudge injected before final response.")
            llm_trace["reasoning_notes"].append("Red-verification nudge injected before final response.")
            return True
    if not getattr(tools._ctx, "_verify_masked_nudged", False):
        # Exit-masking one-shot ADVISORY nudge (v6.52.2): a PASSING verify
        # check can LAUNDER the real exit code (`| tail`/`|| true` — the
        # false-green tutanota hit). Distinct from the red nudge; ordered
        # after it. Binary latch; advisory; forced paths bypass it. Flag-
        # driven on the typed receipt sensor, never content (Bible P5).
        _masked_receipt = latest_unreconciled_masked_verification(drive_root, task_id)
        if _masked_receipt is not None:
            tools._ctx._verify_masked_nudged = True
            _mcheck = str(_masked_receipt.get("check") or "").strip()
            _mreasons = ", ".join(str(x) for x in (_masked_receipt.get("check_exit_masking_reasons") or []))
            _mon = f" on `{_mcheck}`" if _mcheck else ""
            _mwhy = f" ({_mreasons})" if _mreasons else ""
            if content and content.strip():
                messages.append({"role": "assistant", "content": content})
            _append_or_merge_user_message(
                messages,
                "[SYSTEM REMINDER]\nYour latest passing verification" + _mon + " uses a shell pipe" + _mwhy +
                " that can hide the real command's exit code, so a failing run could read as exit 0. "
                "Before a clean final answer, re-ground so the exit reflects the real result (drop the "
                "masking pipe / use the runner's own pass marker), or explain why it is reliable. This is "
                "advisory — if you finalize anyway, make the residual risk explicit.",
            )
            emit_progress("Masked-verification nudge injected before final response.")
            llm_trace["reasoning_notes"].append("Masked-verification nudge injected before final response.")
            return True
    if not getattr(tools._ctx, "_criterion_source_nudged", False):
        # Criterion-provenance one-shot ADVISORY nudge (v6.54.4): the latest passing
        # verification used an AGENT-DEFINED criterion with no stated basis — the check
        # is green, but the success criterion itself was synthesized. One reminder to
        # confirm equivalence with the task's real requirement (or state the basis via
        # criterion_basis). Ordered AFTER the masked nudge, BEFORE FR3. Flag-driven on
        # the typed receipt field, never content (P5); forced paths bypass earlier.
        _agent_defined = latest_agent_defined_verification(drive_root, task_id)
        if _agent_defined is not None:
            tools._ctx._criterion_source_nudged = True
            _acheck = str(_agent_defined.get("check") or "").strip()
            _aon = f" (`{_acheck}`)" if _acheck else ""
            if content and content.strip():
                messages.append({"role": "assistant", "content": content})
            _append_or_merge_user_message(
                messages,
                "[SYSTEM REMINDER]\nYour latest passing verification" + _aon + " uses a success "
                "criterion YOU defined, not one the task states. Before finalizing, double-check the "
                "criterion is equivalent to what the task actually asks for (format, units, scope) — "
                "re-run verify_and_record with criterion_basis stating why it suffices, or adjust the "
                "check. Advisory only — if you finalize anyway, make the assumption explicit.",
            )
            emit_progress("Criterion-provenance nudge injected before final response.")
            llm_trace["reasoning_notes"].append("Criterion-provenance nudge injected before final response.")
            return True
    if not getattr(tools._ctx, "_verify_nudged", False) and should_nudge_verification(llm_trace, drive_root, task_id):
        # FR3 one-shot verify-before-done nudge: real effects, no host-attested grounding
        # yet. Binary latch (not a tunable counter), sibling BEFORE the acceptance-review
        # gate so it reaches both required and auto. Forced finalization paths return
        # earlier and bypass it (they land best_effort).
        tools._ctx._verify_nudged = True
        if content and content.strip():
            messages.append({"role": "assistant", "content": content})
        _append_or_merge_user_message(
            messages,
            "[SYSTEM REMINDER]\nBefore finalizing: you produced a real deliverable but recorded no "
            "machine verification. Call verify_and_record — run your test/command (explicit_command/"
            "explicit_metric/visible_verifier), confirm the artifact exists (artifact_observation), or "
            "honestly declare no_visible_machine_contract — so the result is grounded, then continue.",
        )
        emit_progress("Verify-before-done nudge injected before final response.")
        llm_trace["reasoning_notes"].append("Verify-before-done nudge injected before final response.")
        return True
    # A3 one-shot no-op nudge: a declared deliverable (non-empty
    # expected_output) but the turn made NO tool calls, NO reviewable effects,
    # NO FINAL ANSWER marker — about-to-finalize-without-attempting (same
    # family as the M2 expected_output_ungrounded flag). Own latch, AFTER the
    # verify nudge; never forces acceptance review; forced paths return
    # earlier. Structural facts only (no refusal-text matching).
    if (
        not getattr(tools._ctx, "_noop_attempt_nudged", False)
        and str(_contract_expected_output(tools._ctx)).strip()
        and not (llm_trace.get("tool_calls") or [])
        and not turn_has_reviewable_effects(llm_trace)
        and not extract_final_answer(content or "")
    ):
        tools._ctx._noop_attempt_nudged = True
        if content and content.strip():
            messages.append({"role": "assistant", "content": content})
        # v6.60.0: the nudge keys on expected_output SEMANTICS; it mentions the FINAL
        # ANSWER marker only when this task's contract actually declares the protocol.
        _marker_bit = (
            "no tool calls, no reviewable effects, no FINAL ANSWER"
            if _answer_protocol_active(tools._ctx)
            else "no tool calls, no reviewable effects, no delivered answer"
        )
        _append_or_merge_user_message(
            messages,
            "[SYSTEM REMINDER]\nThis task declares an expected output, but you are about to finalize "
            f"without having attempted it — {_marker_bit}. "
            "Actually attempt the task now (do the work / produce the deliverable / derive the answer), "
            "then finalize. If it is genuinely blocked, say so with the concrete blocker and evidence.",
        )
        emit_progress("No-op attempt nudge injected before final response.")
        llm_trace["reasoning_notes"].append("No-op attempt nudge injected before final response.")
        return True
    # P2 one-shot final-answer-marker nudge: the turn produced REAL work AND
    # visible prose but no FINAL ANSWER marker — the typed extractor would drop
    # it and a forced/deadline finalization would score empty. Strengthen the
    # BEHAVIOR (ask the agent to mark its OWN answer), never mine prose into a
    # claimed answer (Bible P5). Own latch, ordered AFTER verify/red/A3
    # (grounding outranks formatting); mutually exclusive with the A3 no-op
    # nudge; forced paths return earlier. Structural facts only. The protocol
    # gate alone suffices: answer_protocol="final_answer_line" itself declares
    # a machine-extracted deliverable, so the nudge must not ALSO require a
    # declared expected_output — GAIA-shaped contracts keep expected_output
    # empty, and that extra gate once suppressed the only salvage surface
    # (a v6.56.0 run finalized a last-round refusal empty despite 24 calls).
    if (
        not getattr(tools._ctx, "_final_marker_nudged", False)
        and _answer_protocol_active(tools._ctx)  # v6.60.0: marker nudge is protocol-gated
        and content and content.strip()
        and not extract_final_answer(content or "")
        and ((llm_trace.get("tool_calls") or []) or turn_has_reviewable_effects(llm_trace))
    ):
        tools._ctx._final_marker_nudged = True
        messages.append({"role": "assistant", "content": content})
        _append_or_merge_user_message(
            messages,
            "[SYSTEM REMINDER]\nYou have done the work but have not marked a final answer. If you "
            "are done, end your response with a single line, exactly: FINAL ANSWER: <answer> — the "
            "bare deliverable only (a number / a few words / a short list), so it is captured even if "
            "the run is cut short. If you are not done, keep working.",
        )
        emit_progress("Final-answer marker nudge injected before final response.")
        llm_trace["reasoning_notes"].append("Final-answer marker nudge injected before final response.")
        return True
    return False


def _answer_protocol_active(ctx: Any) -> bool:
    """True when this task's contract declares answer_protocol="final_answer_line"
    (v6.60.0): the FINAL ANSWER marker instructions/nudges/pacing phrases are
    PROTOCOL-GATED — only adapter/exact-match tasks see them; ordinary chat and
    self-tasks never get marker prompting (the latch/extractor stay unconditional).
    Thin alias over the contracts SSOT gate."""
    from ouroboros.contracts.task_contract import answer_protocol_active

    return answer_protocol_active(ctx)


def _contract_expected_output(ctx: Any) -> str:
    """Read the declared expected_output (as carried on the task contract/metadata for the
    running ctx — the same declared field the M2 ungrounded flag keys on), for the A3 no-op nudge gate."""
    contract = getattr(ctx, "task_contract", {})
    if isinstance(contract, dict) and str(contract.get("expected_output") or "").strip():
        return str(contract.get("expected_output") or "")
    metadata = getattr(ctx, "task_metadata", {})
    if isinstance(metadata, dict):
        if str(metadata.get("expected_output") or "").strip():
            return str(metadata.get("expected_output") or "")
        meta_contract = metadata.get("task_contract")
        if isinstance(meta_contract, dict):
            return str(meta_contract.get("expected_output") or "")
    return ""


@dataclass
class _RoundModelCallContext:
    llm: LLMClient
    messages: List[Dict[str, Any]]
    tools: ToolRegistry
    context_fit_plan: Any
    active_model: str
    tool_schemas: List[Dict[str, Any]]
    active_effort: str
    max_retries: int
    drive_logs: pathlib.Path
    task_id: str
    round_idx: int
    event_queue: Optional[queue.Queue]
    accumulated_usage: Dict[str, Any]
    task_type: str
    active_use_local: bool
    active_context_mode: str
    drive_root: Optional[pathlib.Path]
    attempt_cap: Optional[int] = None


def _context_fit_round_id(ctx: _RoundModelCallContext) -> str:
    execution_id = str(ctx.accumulated_usage.setdefault("execution_id", new_execution_id()))
    return f"{execution_id}:round:{ctx.round_idx}"


def _main_context_profile(plan: Any, rendered_mode: str) -> str:
    if rendered_mode != "low":
        return "owner_max"
    # Effective Low is the sizing authority even when a bare env override keeps
    # owner intent Max for P3. A Low entered only after a real Max overflow is
    # task-local and therefore does not inherit the economy target T.
    return "owner_low" if str(getattr(plan, "preferred_mode", "")) == "low" else "task_local_low"


def _remember_main_fit(ctx: _RoundModelCallContext, disposition: Any) -> None:
    measurement = disposition.measurement
    usage = ctx.accumulated_usage
    usage["_context_route_fp"] = measurement.route_fp
    usage["_context_prompt_estimate"] = measurement.estimated_input_tokens
    usage["_context_fit_mode"] = measurement.rendered_mode
    usage["_context_profile"] = measurement.profile
    usage["_context_measurement_basis"] = measurement.measurement_basis
    usage["_context_measurement_density"] = measurement.measurement_density
    usage["_context_target_total_tokens"] = measurement.target_total_tokens
    usage["_context_capacity_total_tokens"] = measurement.capacity_total_tokens
    usage["_context_target_deficit_tokens"] = measurement.target_deficit_tokens
    usage["_context_capacity_deficit_tokens"] = measurement.capacity_deficit_tokens
    usage["_context_reclaim_goal_tokens"] = measurement.reclaim_goal_tokens
    usage["_context_target_miss"] = disposition.action == "send_target_miss"
    usage["_context_automatic_pass_used"] = disposition.automatic_pass_used
    usage["_context_predicted_capacity_miss"] = disposition.predicted_capacity_miss


def _measure_round_main_fit(
    ctx: _RoundModelCallContext,
    *,
    automatic_pass_used: bool,
) -> Any:
    plan = ctx.context_fit_plan
    if plan is None or str(ctx.active_model or "") != str(getattr(plan, "model", "") or ""):
        return None
    from ouroboros.context_fit import measure_main_fit

    rendered_mode = "low" if ctx.active_context_mode == "low" else "max"
    disposition = measure_main_fit(
        plan,
        ctx.messages,
        ctx.tool_schemas,
        profile=_main_context_profile(plan, rendered_mode),
        rendered_mode=rendered_mode,
        round_id=_context_fit_round_id(ctx),
        automatic_pass_used=automatic_pass_used,
    )
    _remember_main_fit(ctx, disposition)
    return disposition


def _physical_context_for_fit(disposition: Any) -> PhysicalAttemptContext:
    measurement = disposition.measurement
    return PhysicalAttemptContext(
        profile=measurement.profile,
        rendered_mode=measurement.rendered_mode,
        measurement_basis=measurement.measurement_basis,
        route_fp=measurement.route_fp,
        round_id=measurement.round_id,
        target_total_tokens=measurement.target_total_tokens,
        capacity_total_tokens=measurement.capacity_total_tokens,
        context_target_miss=disposition.action == "send_target_miss",
        automatic_pass_used=disposition.automatic_pass_used,
    )


def _dispatch_round_model(
    ctx: _RoundModelCallContext,
    disposition: Any,
    *,
    attempt_cap: Optional[int],
    candidate_predicate: Optional[Callable[[Any], Any]] = None,
) -> Tuple[Any, float]:
    return call_llm_with_retry(
        ctx.llm,
        ctx.messages,
        ctx.active_model,
        ctx.tool_schemas,
        ctx.active_effort,
        ctx.max_retries,
        ctx.drive_logs,
        ctx.task_id,
        ctx.round_idx,
        ctx.event_queue,
        ctx.accumulated_usage,
        ctx.task_type,
        use_local=ctx.active_use_local,
        deadline_ts=_task_deadline_epoch(ctx.tools),
        attempt_cap=attempt_cap,
        allow_server_web_search=_server_web_allowed_by_task(ctx.tools._ctx),
        physical_context=(
            _physical_context_for_fit(disposition) if disposition is not None else None
        ),
        candidate_predicate=candidate_predicate,
    )


def _run_main_reclaim(
    ctx: _RoundModelCallContext,
    disposition: Any,
    *,
    minimum_goal_tokens: int = 0,
) -> Any:
    measurement = disposition.measurement
    key = (measurement.route_fp, measurement.round_id)
    passes = _context_reclaim_passes(ctx.tools._ctx)
    if key in passes:
        return None
    request = ContextReclaimRequest(
        route_fp=measurement.route_fp,
        round_id=measurement.round_id,
        transcript_sha256=context_reclaim_transcript_sha256(ctx.messages),
        measurement_basis=measurement.measurement_basis,
        measurement_density=measurement.measurement_density,
        reclaim_goal_tokens=max(
            int(measurement.reclaim_goal_tokens),
            max(0, int(minimum_goal_tokens)),
        ),
        allow_partial_shrink=True,
    )
    rebuilt, receipt, usage = compact_tool_history_llm(
        ctx.messages,
        request=request,
        drive_root=pathlib.Path(ctx.drive_root or ctx.drive_logs.parent),
        task_id=ctx.task_id,
        negative_memo=reclaim_negative_memo(ctx.tools._ctx),
        trace_refs_by_tool_call_id=reclaim_trace_refs(ctx.tools._ctx),
    )
    passes.add(key)
    # The checkpoint is written only after non-empty selection and immediately
    # before map/fold, so it also covers a post-summary binding mismatch.
    if receipt.checkpoint_ref:
        _context_reclaim_materializations(ctx.tools._ctx).add(key)
    if usage:
        _account_compaction_usage(ctx.accumulated_usage, usage, ctx.event_queue, ctx.task_id)
    if receipt.status == "applied":
        ctx.messages[:] = rebuilt
        ctx.tools._ctx.messages = ctx.messages
        seal_task_transcript(ctx.messages)
        prune_reclaim_trace_refs(ctx.tools._ctx, ctx.messages)
    _emit_checkpoint_event(ctx.event_queue, ctx.task_id, ctx.drive_logs, {
        "type": "context_reclaim",
        "checkpoint_kind": "context_reclaim_automatic",
        "round": ctx.round_idx,
        "route_fp": measurement.route_fp,
        "round_id": measurement.round_id,
        "status": receipt.status,
        "reclaim_goal_tokens": request.reclaim_goal_tokens,
        "reclaimed_tokens": receipt.reclaimed_tokens,
        "goal_reached": receipt.goal_reached,
        "checkpoint_ref": receipt.checkpoint_ref,
    })
    return receipt


def _measure_after_reclaim(ctx: _RoundModelCallContext) -> Any:
    """Suppress a second pass while reporting whether a summarizer actually ran."""
    disposition = _measure_round_main_fit(ctx, automatic_pass_used=True)
    if disposition is None:
        return None
    key = (disposition.measurement.route_fp, disposition.measurement.round_id)
    used = key in _context_reclaim_materializations(ctx.tools._ctx)
    if disposition.automatic_pass_used != used:
        disposition = replace(disposition, automatic_pass_used=used)
        _remember_main_fit(ctx, disposition)
    return disposition


def _reproject_actual_overflow_low(ctx: _RoundModelCallContext) -> None:
    if ctx.active_context_mode == "low" or ctx.context_fit_plan is None:
        return
    ctx.messages[:] = ctx.context_fit_plan.reproject_transcript(ctx.messages, "low")
    ctx.active_context_mode = "low"
    ctx.tools._ctx.messages = ctx.messages
    ctx.tools._ctx.active_context_mode = "low"
    _emit_checkpoint_event(ctx.event_queue, ctx.task_id, ctx.drive_logs, {
        "checkpoint_kind": "context_fit_low_retry",
        "round": ctx.round_idx,
        "route_fp": str(getattr(ctx.context_fit_plan, "route_fp", "") or ""),
        "preferred_mode": str(getattr(ctx.context_fit_plan, "preferred_mode", "") or ""),
        "effective_mode": "low",
        "owner_visible": True,
    })


def _failed_capture_is_comparable(capture: Any) -> bool:
    return bool(
        capture is not None
        and capture.state in {"dispatched", "settled", "unresolved"}
        and capture.candidate_measurement_kind == "canonical_json_v1"
        and capture.candidate_raw_sha256
        and capture.candidate_context_size_bytes is not None
        and capture.physical_context is not None
    )


def _strict_context_shrink_predicate(failed: Any) -> Callable[[Any], bool]:
    def predicate(request: Any) -> bool:
        failed_context = failed.physical_context
        current_context = request.physical_context
        return bool(
            request.candidate_measurement_kind == "canonical_json_v1"
            and request.provider == failed.provider
            and request.model == failed.model
            and request.max_completion_tokens == failed.max_completion_tokens
            and current_context is not None
            and failed_context is not None
            and current_context.route_fp == failed_context.route_fp
            and current_context.round_id == failed_context.round_id
            and request.candidate_raw_sha256 != failed.candidate_raw_sha256
            and request.candidate_context_size_bytes is not None
            and int(request.candidate_context_size_bytes) < int(failed.candidate_context_size_bytes)
        )

    return predicate


def _emit_overflow_retry_skipped(ctx: _RoundModelCallContext, reason: str) -> None:
    _emit_checkpoint_event(ctx.event_queue, ctx.task_id, ctx.drive_logs, {
        "type": "context_overflow_retry_skipped",
        "round": ctx.round_idx,
        "route_fp": str(getattr(ctx.context_fit_plan, "route_fp", "") or ""),
        "reason": reason,
    })


def _call_round_model(ctx: _RoundModelCallContext) -> Tuple[Any, float, str]:
    """Measure, optionally reclaim, dispatch, and recover one Main round."""
    disposition = _measure_round_main_fit(ctx, automatic_pass_used=False)
    if disposition is not None:
        key = (disposition.measurement.route_fp, disposition.measurement.round_id)
        already_reclaimed = key in _context_reclaim_passes(ctx.tools._ctx)
        if disposition.action == "reclaim_once" and not already_reclaimed:
            _run_main_reclaim(ctx, disposition)
            already_reclaimed = True
        if already_reclaimed:
            disposition = _measure_after_reclaim(ctx)

    msg, cost = _dispatch_round_model(
        ctx,
        disposition,
        attempt_cap=ctx.attempt_cap,
    )
    if msg is not None or str(ctx.accumulated_usage.get("_last_llm_error_kind") or "") != "context_overflow":
        return msg, cost, ctx.active_context_mode

    # Snapshot immediately: a reclaim summarizer is itself physically receipted
    # and would otherwise replace the failed Main candidate in the ContextVar.
    failed_capture = last_physical_attempt_capture()
    if disposition is None:
        return msg, cost, ctx.active_context_mode
    _reproject_actual_overflow_low(ctx)
    reclaim_key = (disposition.measurement.route_fp, disposition.measurement.round_id)
    overflow_fit = (
        _measure_after_reclaim(ctx)
        if reclaim_key in _context_reclaim_passes(ctx.tools._ctx)
        else _measure_round_main_fit(ctx, automatic_pass_used=False)
    )
    if overflow_fit is None:
        return msg, cost, ctx.active_context_mode
    key = (overflow_fit.measurement.route_fp, overflow_fit.measurement.round_id)
    if key not in _context_reclaim_passes(ctx.tools._ctx):
        _run_main_reclaim(ctx, overflow_fit, minimum_goal_tokens=1)
        overflow_fit = _measure_after_reclaim(ctx)
        if overflow_fit is None:
            return msg, cost, ctx.active_context_mode

    retries = _context_overflow_retries(ctx.tools._ctx)
    if key in retries:
        _emit_overflow_retry_skipped(ctx, "route_round_retry_already_used")
        return msg, cost, ctx.active_context_mode
    if not _failed_capture_is_comparable(failed_capture):
        _emit_overflow_retry_skipped(ctx, "failed_candidate_not_comparable")
        return msg, cost, ctx.active_context_mode
    retries.add(key)
    try:
        retry_msg, retry_cost = _dispatch_round_model(
            ctx,
            overflow_fit,
            attempt_cap=1,
            candidate_predicate=_strict_context_shrink_predicate(
                failed_capture,
            ),
        )
    except PhysicalAttemptPreconditionFailed:
        _emit_overflow_retry_skipped(ctx, "context_candidate_not_strictly_smaller")
        return msg, cost, ctx.active_context_mode
    return retry_msg, retry_cost, ctx.active_context_mode


@dataclass
class _LoopExitContext:
    tools: ToolRegistry
    drive_root: Optional[pathlib.Path]
    task_id: str
    event_queue: Optional[queue.Queue]
    drive_logs: pathlib.Path
    accumulated_usage: Dict[str, Any]
    llm_trace: Dict[str, Any]


def _handle_budget_exceeded(
    exc: BudgetExceeded,
    ctx: _LoopExitContext,
    *,
    limit_ctx: Optional[_RoundLimitContext] = None,
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """Apply the physical-attempt dispatch rail without spending a wrap-up call."""
    physical_calls: Optional[int] = None
    try:
        from ouroboros.usage_accounting import usage_breakdown

        budget_root = (
            getattr(ctx.tools._ctx, "budget_drive_root", None)
            or ctx.drive_root
            or getattr(ctx.tools._ctx, "drive_root", None)
        )
        if budget_root is not None:
            attempt_evidence = usage_breakdown(
                pathlib.Path(budget_root), task_id=str(ctx.task_id),
            )
            physical_calls = int(attempt_evidence.get("physical_calls") or 0)
            if attempt_evidence.get("integrity_degraded"):
                physical_calls = None
    except Exception:
        log.exception("Could not inspect task attempts after budget rail")
    direct_chat = bool(getattr(ctx.tools._ctx, "is_direct_chat", False))
    replay_safe = physical_calls == 0 and not direct_chat
    scope = str(getattr(exc, "limit_scope", "global") or "global")
    resource_limit = {
        "status": "paused_before_dispatch" if replay_safe else "resource_limited",
        "scope": scope,
        "root_task_id": str(getattr(exc, "root_task_id", "") or ""),
        "physical_calls": physical_calls,
        "replay_safe": replay_safe,
        "auto_resume": False,
        "resume_policy": (
            "increase_or_reset_budget_then_retry"
            if direct_chat
            else ("manual_same_generation" if replay_safe else "cancel_or_new_run")
        ),
    }
    if replay_safe:
        raise exc
    ctx.accumulated_usage["execution_status"] = "failed"
    ctx.accumulated_usage["reason_code"] = "budget_exhausted"
    ctx.accumulated_usage["resource_limit"] = resource_limit
    ctx.llm_trace["resource_limit"] = resource_limit
    _emit_checkpoint_event(ctx.event_queue, ctx.task_id, ctx.drive_logs, {
        "checkpoint_kind": "budget_scope_paused",
        "owner_visible": True,
        "toast_once": f"{ctx.task_id}:budget-paused:{scope}",
        **resource_limit,
    })
    if (
        scope == "root"
        and ctx.event_queue is not None
        and not bool(getattr(ctx.tools._ctx, "is_direct_chat", False))
    ):
        try:
            ctx.event_queue.put_nowait({
                "type": "budget_root_fence",
                "task_id": ctx.task_id,
                "root_task_id": resource_limit["root_task_id"],
                "resource_limit": resource_limit,
            })
        except Exception:
            log.error("Could not publish root budget fence for %s", ctx.task_id, exc_info=True)
    # A physical budget rail is terminal for this execution.  Finalize task
    # services before testing or creating a DeliveryCandidate so no pre-teardown
    # answer can be published against stale service/output evidence.  The loop's
    # outer cleanup repeats this helper only as an idempotent safety net.
    if limit_ctx is not None:
        limit_ctx.tools = ctx.tools
        limit_ctx.llm_trace = ctx.llm_trace
        _finalize_forced_services(limit_ctx, ctx.llm_trace)
    else:
        _finalize_task_services(ctx)
    candidate_seen: Optional[DeliveryCandidate] = None
    if limit_ctx is not None:
        # The exception can arrive after a substantive answer entered a service
        # re-loop. Re-read the live evidence now; the round-start snapshot alone
        # cannot prove that candidate is still current.
        limit_ctx.tools = ctx.tools
        limit_ctx.llm_trace = ctx.llm_trace
        candidate_seen = _live_delivery_candidate(limit_ctx)
        current_candidate = _current_delivery_candidate(limit_ctx, ctx.llm_trace)
        if current_candidate is not None:
            return _forced_fallback_result(
                limit_ctx,
                ctx.llm_trace,
                current_candidate.full_text,
                "budget_exhausted",
                source="budget_host_fallback",
                retained_source="budget_preserve",
                retained_control="budget_preserve",
            )
        if candidate_seen is not None:
            candidate_seen.degraded = True
            candidate_seen.degraded_reason = "budget_exhausted"
            candidate_seen.finalization_control = "budget_stale_rejected"
            _publish_delivery_candidate(ctx.tools, candidate_seen, ctx.llm_trace)
    latched = str(ctx.llm_trace.get("best_valid_final_answer") or "").strip()
    latched_is_current = (
        latched
        and len(ctx.llm_trace.get("tool_calls") or [])
        <= int(ctx.llm_trace.get("best_valid_final_answer_tools") or 0)
    )
    if latched_is_current:
        ctx.accumulated_usage["_best_effort_extracted"] = True
        if limit_ctx is not None:
            return _forced_fallback_result(
                limit_ctx,
                ctx.llm_trace,
                latched,
                "budget_exhausted",
                source="budget_latched_fallback",
            )
        return latched, ctx.accumulated_usage, ctx.llm_trace
    if candidate_seen is not None and limit_ctx is not None:
        return _forced_fallback_result(
            limit_ctx,
            ctx.llm_trace,
            candidate_seen.full_text,
            "budget_exhausted",
            source="budget_stale_candidate_preserved",
        )
    message = (
        "🚫 Model budget exhausted before another model dispatch. Increase or reset "
        "the global/root budget, then retry or resume the request. Starting a new run "
        "before changing the budget will hit the same limit."
        if direct_chat
        else (
            "🚫 Resource limit reached before another model dispatch. The task was not "
            "auto-resumed; cancel it or start a new run unless the recorded checkpoint "
            "is explicitly replay-safe."
        )
    )
    if limit_ctx is not None:
        return _forced_fallback_result(
            limit_ctx,
            ctx.llm_trace,
            message,
            "budget_exhausted",
            source="budget_host_fallback",
        )
    return message, ctx.accumulated_usage, ctx.llm_trace


def _cleanup_loop_resources(
    stateful_executor: Any,
    ctx: _LoopExitContext,
) -> None:
    """Release executor, task services, and mailbox after every loop exit."""
    if stateful_executor:
        try:
            from ouroboros.tools.browser import cleanup_browser

            stateful_executor.submit(cleanup_browser, ctx.tools._ctx).result(timeout=5)
        except Exception:
            log.debug("Browser cleanup on executor thread failed or timed out", exc_info=True)
        try:
            stateful_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            log.warning("Failed to shutdown stateful executor", exc_info=True)
    _finalize_task_services(ctx)
    # The full DeliveryCandidate is intentionally loop-local. Only its compact
    # hash/revision projection remains in llm_trace after this cleanup. Clear it
    # after the idempotent teardown safety net so cleanup cannot erase the only
    # complete answer before service evidence is collected.
    ctx.tools._ctx._delivery_candidate = None
    ctx.tools._ctx._delivery_control_required = False
    if ctx.drive_root is None or not ctx.task_id:
        return
    try:
        from ouroboros.delegate_custody import custody_root, release_task_runs

        # A delegated run is a resource this task HOLDS, like a service or an executor:
        # a terminalized parent that leaves one running has a mutating process nothing
        # is watching. The durable reconciler still covers a worker that dies before
        # reaching here; this is the ordinary path.
        release_task_runs(custody_root(ctx.tools._ctx), ctx.task_id)
    except Exception:
        log.debug("Failed to release delegated runs for task %s", ctx.task_id, exc_info=True)
    try:
        from ouroboros.owner_mailbox import cleanup_task_mailbox

        cleanup_task_mailbox(ctx.drive_root, ctx.task_id)
    except Exception:
        log.debug("Failed to cleanup task mailbox", exc_info=True)


def _service_identity_projection(service: Dict[str, Any]) -> Dict[str, Any]:
    """Bounded identity used to deduplicate idempotent teardown observations."""

    fields = (
        "service_id",
        "name",
        "task_id",
        "lifecycle",
        "backend",
        "pid",
        "port",
        "artifact_outputs",
        "artifact_output_failed",
        "artifact_audit_gap",
        "log_finalization",
    )
    return {
        key: service.get(key)
        for key in fields
        if service.get(key) not in (None, "", [], {})
    }


def _finalize_task_services(ctx: _LoopExitContext) -> bool:
    """Finalize newly observed task services and record answer-bound evidence.

    Returns True only when a new stopped/kept/error observation was added.  The
    same helper is safe both immediately before acceptance and from ``finally``.
    """

    if ctx.drive_root is None or not ctx.task_id:
        return False
    try:
        from ouroboros.tools.services import stop_task_services

        finalized = stop_task_services(ctx.tools._ctx)
        seen = getattr(ctx.tools._ctx, "_service_finalization_signatures", None)
        if not isinstance(seen, set):
            seen = set()
            ctx.tools._ctx._service_finalization_signatures = seen
        fresh = []
        for service in finalized:
            if not isinstance(service, dict):
                continue
            signature = hashlib.sha256(json.dumps(
                _service_identity_projection(service),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")).hexdigest()
            if signature in seen:
                continue
            seen.add(signature)
            fresh.append(service)
        stopped = [service for service in fresh if service.get("lifecycle") != "kept"]
        kept = [service for service in fresh if service.get("lifecycle") == "kept"]
        if stopped:
            _emit_checkpoint_event(ctx.event_queue, ctx.task_id, ctx.drive_logs, {
                "checkpoint_kind": "services_stopped",
                "services": stopped,
            })
            ctx.llm_trace.setdefault("verification_events", []).append({
                "kind": "services_stopped",
                "services": stopped,
            })
        if kept:
            _emit_checkpoint_event(ctx.event_queue, ctx.task_id, ctx.drive_logs, {
                "checkpoint_kind": "services_kept",
                "services": kept,
            })
            ctx.llm_trace.setdefault("verification_events", []).append({
                "kind": "services_kept",
                "services": kept,
            })
        return bool(stopped or kept)
    except Exception as exc:
        log.debug("Failed to stop task services", exc_info=True)
        event = {
            "kind": "service_finalization_error",
            "services": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
        signature = hashlib.sha256(json.dumps(
            event, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        seen = getattr(ctx.tools._ctx, "_service_finalization_signatures", None)
        if not isinstance(seen, set):
            seen = set()
            ctx.tools._ctx._service_finalization_signatures = seen
        if signature in seen:
            return False
        seen.add(signature)
        ctx.llm_trace.setdefault("verification_events", []).append(event)
        return True


def _prepare_post_tool_budget_context(
    tools: ToolRegistry,
    limit_ctx: _RoundLimitContext,
    llm_trace: Dict[str, Any],
    active_model: str,
    active_use_local: bool,
    active_effort: str,
) -> None:
    """Refresh candidate evidence and the actual route before budget wrap-up."""

    candidate = getattr(tools._ctx, "_delivery_candidate", None)
    if isinstance(candidate, DeliveryCandidate):
        skill_action_pending = (
            candidate.finalization_control == "skill_action_or_revision_required"
        )
        evidence_revision, evidence_fingerprint = _delivery_evidence_state(
            tools, limit_ctx, llm_trace,
        )
        if (
            candidate.evidence_revision != evidence_revision
            or candidate.evidence_fingerprint != evidence_fingerprint
        ):
            _arm_delivery_control(
                tools,
                limit_ctx,
                llm_trace,
                control="effect_revision_required",
            )
        elif skill_action_pending:
            _arm_delivery_control(
                tools,
                limit_ctx,
                llm_trace,
                control="skill_revision_required",
            )
    # Cross-model fallback can adopt a different route during this round.
    limit_ctx.active_model = active_model
    limit_ctx.active_use_local = active_use_local
    limit_ctx.active_effort = active_effort


def _resolve_loop_max_rounds() -> int:
    from ouroboros.config import SETTINGS_DEFAULTS

    default = int(SETTINGS_DEFAULTS["OUROBOROS_MAX_ROUNDS"])
    try:
        return max(1, int(os.environ.get("OUROBOROS_MAX_ROUNDS", str(default))))
    except (ValueError, TypeError):
        log.warning("Invalid OUROBOROS_MAX_ROUNDS, defaulting to %s", default)
        return default


def run_llm_loop(
    messages: List[Dict[str, Any]],
    tools: ToolRegistry,
    llm: LLMClient,
    drive_logs: pathlib.Path,
    emit_progress: Callable[[str], None],
    incoming_messages: queue.Queue,
    task_type: str = "",
    task_id: str = "",
    budget_remaining_usd: Optional[float] = None,
    event_queue: Optional[queue.Queue] = None,
    initial_effort: str = "medium",
    drive_root: Optional[pathlib.Path] = None,
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """Run the LLM-with-tools loop and return final text, usage, and trace."""
    ctx = tools._ctx
    ctx._delivery_candidate = None
    ctx._delivery_candidate_revision = 0
    ctx._delivery_control_required = False
    ctx._delivery_evidence_revision = 0
    ctx._delivery_evidence_fingerprint = ""
    _initialize_owner_directives(ctx, messages)
    task_model_override = str(getattr(ctx, "task_model_override", "") or "").strip()
    active_model = task_model_override or llm.default_model()
    active_effort = initial_effort
    if getattr(ctx, "task_use_local_override", None) is not None:
        active_use_local = bool(ctx.task_use_local_override)
    else:
        active_use_local = os.environ.get("USE_LOCAL_MAIN", "").lower() in ("true", "1")
    # Unknown routes get one honest call; no synthetic short-window capacity.
    _preferred_context_mode = get_context_mode()
    context_fit_plan = getattr(ctx, "context_fit_plan", None)
    if (
        context_fit_plan is not None
        and str(getattr(context_fit_plan, "preferred_mode", "")) == _preferred_context_mode
    ):
        active_context_mode = str(getattr(context_fit_plan, "initial_mode", "") or _preferred_context_mode)
    else:
        active_context_mode = _preferred_context_mode
    llm_trace: Dict[str, Any] = {"reasoning_notes": [], "tool_calls": []}
    accumulated_usage: Dict[str, Any] = {}
    # Published as a live reference so blocking tools (wait_task/wait_tasks/
    # delegate_wait) can read RECORDED per-send facts — e.g. the APPLIED
    # prompt-cache TTL (`_last_prompt_cache_ttl`) behind the cache-horizon
    # disclosure — without a second, route-derived predictor.
    tools._ctx._accumulated_usage = accumulated_usage
    max_retries = 3
    cost_ceiling = _resolve_task_cost_ceiling(ctx, budget_remaining_usd)
    if cost_ceiling.root_cap_usd is not None:
        # Loop-start seed (one rare ledger read): a resumed/late-started member
        # of a spending tree must see the real tree number before its first
        # pacing surface, not a process-local empty stash.
        _loop_tree_accounting(refresh=True, max_age_sec=0.0)
    from ouroboros.tools import tool_discovery as _td
    _td.set_registry(tools)

    tool_schemas = initial_tool_schemas(tools)
    tool_schemas, _enabled_extra_tools = _setup_dynamic_tools(tools, tool_schemas, messages)
    tools._ctx.event_queue = event_queue
    tools._ctx.task_id = task_id
    tools._ctx.messages = messages
    stateful_executor = StatefulToolExecutor()
    exit_ctx = _LoopExitContext(
        tools, drive_root, task_id, event_queue, drive_logs, accumulated_usage, llm_trace,
    )
    _owner_msg_seen: set = set()
    MAX_ROUNDS = _resolve_loop_max_rounds()
    round_idx = 0
    limit_ctx: Optional[_RoundLimitContext] = None
    try:
        while True:
            round_idx += 1

            ctx = tools._ctx
            _prev_active_route = (active_model, active_use_local)
            _prev_active_model = active_model
            active_model, active_use_local, active_effort, active_context_mode = _apply_overrides_and_regate_mode(
                ctx, active_model, active_use_local, active_effort, active_context_mode,
            )
            if (active_model, active_use_local) != _prev_active_route:
                context_fit_plan, active_context_mode = _rebind_context_fit_plan(
                    context_fit_plan, tools, messages, model=active_model,
                    use_local=active_use_local, preferred_mode=_preferred_context_mode,
                    tool_schemas=tool_schemas,
                )
            if active_model != _prev_active_model:
                # A cross-FAMILY switch_model / per-task override: strip the
                # prior family's provider-private reasoning blocks from the
                # history so the new family does not 400 on a signature it
                # cannot validate (safe — loses only reasoning continuity).
                # Same family is a no-op.
                _sanitized = LLMClient.sanitize_reasoning_on_model_switch(messages, _prev_active_model, active_model)
                if _sanitized is not messages:
                    messages[:] = _sanitized
            ctx.active_context_mode = active_context_mode  # switch_model re-binds the fit plan from this round's mode
            ctx.active_model = active_model  # publish the round's REAL model (incl. switch_model / per-task override) so tools (native screenshot vision-routing) don't read the stale global OUROBOROS_MODEL env

            # One forced-wrap-up context per round: consumed by the round-limit
            # path and the supervisor finalize_now control path below.
            limit_ctx = _RoundLimitContext(
                messages, llm, active_model, active_effort, max_retries, drive_logs,
                task_id, round_idx, event_queue, accumulated_usage, task_type,
                active_use_local, MAX_ROUNDS, drive_root=drive_root,
                incoming_messages=incoming_messages, owner_msg_seen=_owner_msg_seen,
            )
            _finalize_limit_ctx(limit_ctx, tools, llm_trace)
            if round_idx > MAX_ROUNDS:
                text, accumulated_usage, forced_trace = _handle_round_limit(limit_ctx)
                _merge_finalization_trace(llm_trace, forced_trace)
                return text, accumulated_usage, llm_trace

            _controls = _drain_incoming_messages(
                messages,
                incoming_messages,
                drive_root,
                task_id,
                event_queue,
                _owner_msg_seen,
                owner_ctx=ctx,
            )
            # Early-exit per round: supervisor finalize_now, else loop-local real-
            # deadline finalize (headless runs that get no finalize_now) — finalize
            # best-effort rather than be killed mid-step with nothing.
            _early_final = _maybe_early_finalize(limit_ctx, tools, _controls)
            if _early_final is not None:
                text, accumulated_usage, forced_trace = _early_final
                _merge_finalization_trace(llm_trace, forced_trace)
                return text, accumulated_usage, llm_trace

            # Typed soft landing (v6.91): the ledger fence stays the untouched
            # backstop; an exhausted ceiling wraps up BEFORE spending a round.
            _soft_land = _soft_land_exhausted_ceiling(limit_ctx, cost_ceiling)
            if _soft_land is not None:
                text, accumulated_usage, forced_trace = _soft_land
                _merge_finalization_trace(llm_trace, forced_trace)
                return text, accumulated_usage, llm_trace

            _checkpoint_injected = _inject_round_checkpoints(
                round_idx=round_idx, max_rounds=MAX_ROUNDS, messages=messages, accumulated_usage=accumulated_usage,
                emit_progress=emit_progress, tools=tools, event_queue=event_queue, task_id=task_id,
                drive_logs=drive_logs, budget_remaining_usd=budget_remaining_usd, cost_ceiling=cost_ceiling)

            messages, _compaction_usage = _run_round_compaction(
                messages,
                _CompactionRoundContext(
                    tools=tools,
                    drive_root=drive_root,
                    drive_logs=drive_logs,
                    task_id=task_id,
                    round_idx=round_idx,
                    event_queue=event_queue,
                    emit_progress=emit_progress,
                ),
            )
            if tools._ctx.messages is not messages:
                tools._ctx.messages = messages
            limit_ctx.messages = messages  # WA2: provider-death finalize must salvage the COMPACTED transcript
            if _compaction_usage:
                _account_compaction_usage(accumulated_usage, _compaction_usage, event_queue, task_id)

            seal_task_transcript(messages)

            msg, cost, active_context_mode = _call_round_model(
                _RoundModelCallContext(
                    llm=llm,
                    messages=messages,
                    tools=tools,
                    context_fit_plan=context_fit_plan,
                    active_model=active_model,
                    tool_schemas=tool_schemas,
                    active_effort=active_effort,
                    max_retries=max_retries,
                    drive_logs=drive_logs,
                    task_id=task_id,
                    round_idx=round_idx,
                    event_queue=event_queue,
                    accumulated_usage=accumulated_usage,
                    task_type=task_type,
                    active_use_local=active_use_local,
                    active_context_mode=active_context_mode,
                    drive_root=drive_root,
                )
            )
            tools._ctx._current_llm_call_meta = dict(accumulated_usage.get("_last_llm_call_meta") or {})

            if msg is None:
                (
                    msg,
                    active_model,
                    active_use_local,
                    context_fit_plan,
                    active_context_mode,
                ) = _run_cross_model_fallback_chain(
                    llm=llm, ctx=ctx, tools=tools, messages=messages, active_model=active_model,
                    active_use_local=active_use_local, tool_schemas=tool_schemas, active_effort=active_effort,
                    max_retries=max_retries, drive_logs=drive_logs, task_id=task_id, round_idx=round_idx,
                    event_queue=event_queue, accumulated_usage=accumulated_usage, task_type=task_type,
                    emit_progress=emit_progress, context_fit_plan=context_fit_plan,
                    active_context_mode=active_context_mode)
                if msg is None:
                    # Provider-death: salvage the useful workspace state like the
                    # forced rails do, but terminalize as an infra failure — an
                    # outage interrupts the task, it never completes it.
                    text, accumulated_usage, forced_trace = _handle_provider_unavailable(limit_ctx)
                    _merge_finalization_trace(llm_trace, forced_trace)
                    return text, accumulated_usage, llm_trace

            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content")
            _latch_final_answer_marker(llm_trace, content, current_tool_calls=tool_calls)
            # F12: EVERY LLM response marks metered nanny progress (expensive
            # no-tool rounds count); the delegate BASELINE moves post-tools only.
            _note_nanny_delegate_activity(tools._ctx, round_idx, accumulated_usage, [])
            if not tool_calls:
                final_result = _no_tool_final_answer(
                    content, limit_ctx, llm_trace, tools, incoming_messages,
                    _owner_msg_seen, emit_progress,
                )
                if final_result is None:
                    continue
                return final_result

            if getattr(tools._ctx, "_skill_finalization_injected", False):
                tools._ctx._skill_finalization_injected = False
            assistant_msg = dict(msg)
            assistant_msg.setdefault("role", "assistant")
            messages.append(assistant_msg)

            _emit_round_progress(content, msg, emit_progress, llm_trace)

            handle_tool_calls(
                tool_calls, tools, drive_logs, task_id, stateful_executor,
                messages, llm_trace, emit_progress
            )

            # Nanny-economics baseline (poltergeist phase B): mark this round's
            # metered progress, and re-baseline when the round touched a
            # delegated run. Exact tool-call transitions — no log scans.
            _note_nanny_delegate_activity(
                tools._ctx, round_idx, accumulated_usage, tool_calls,
            )

            _prepare_post_tool_budget_context(
                tools, limit_ctx, llm_trace, active_model, active_use_local, active_effort,
            )
            budget_result = _check_budget_limits(
                limit_ctx,
                budget_remaining_usd,
                cost_ceiling=cost_ceiling,
            )
            if budget_result is not None:
                text, accumulated_usage, budget_trace = budget_result
                _merge_finalization_trace(llm_trace, budget_trace)
                return text, accumulated_usage, llm_trace

    except BudgetExceeded as exc:
        return _handle_budget_exceeded(exc, exit_ctx, limit_ctx=limit_ctx)
    finally:
        _cleanup_loop_resources(stateful_executor, exit_ctx)


# The v7 L-B split: the members below moved into cohesive leaves (module-
# size boundary); each leaf reads the loop's own rebindable globals back through
# a call-time handle, and this block re-exports every moved name so historical
# `ouroboros.loop` imports and monkeypatch targets keep working unchanged.
from ouroboros.loop_messages import (  # noqa: E402, F401 -- intentional public re-exports
    _emit_checkpoint_event,
    _extract_plain_text_from_content,
    _append_or_merge_user_message,
    _evict_stale_image_blocks,
    _append_or_merge_user_content,
    _owner_marked_content,
    _record_owner_directive,
    _initialize_owner_directives,
    _last_assistant_text,
    _visible_round_text,
    _emit_round_progress,
)
from ouroboros.loop_acceptance import (  # noqa: E402, F401 -- intentional public re-exports
    _task_acceptance_eligible,
    _begin_task_acceptance_fence,
    _end_task_acceptance_fence,
    _supersede_delivery_acceptance_binding,
    _supersede_task_acceptance_for_owner_followup,
    _task_acceptance_owner_generation_changed,
    _supersede_task_acceptance_for_evidence_change,
    _task_acceptance_subtree_snapshot,
    _mark_root_acceptance_checkpoint,
    _latch_final_answer_marker,
    _server_web_allowed_by_task,
    ACCEPTANCE_REASON_UNSPECIFIED,
    ACCEPTANCE_DECISION_REASONS,
    _set_acceptance_decision,
    _collect_acceptance_obligations,
    _reopen_obligation_row,
    _open_acceptance_obligations,
    _dispose_obligations_on_clean_pass,
    _format_obligations_clause,
    _record_forced_acceptance_bypass,
)
from ouroboros.loop_acceptance_review import (  # noqa: E402, F401 -- intentional public re-exports
    _ACCEPTANCE_REVIEW_CHECKLIST,
    _TaskAcceptanceContext,
    _acceptance_dialogue_quorum,
    _attach_dialogue_to_host_run,
    _mark_agent_acceptance_runs_advisory,
    _latest_agent_acceptance_evidence,
    _build_host_acceptance_evidence,
    _execute_task_acceptance_panel,
    _record_host_acceptance_run,
    _set_applied_host_acceptance_impact,
    _apply_task_acceptance_result,
    _record_acceptance_infra_failure,
    _prior_acceptance_run,
    _direct_context_fence_state,
    _run_task_acceptance_review_once,
)
from ouroboros.loop_round_limits import (  # noqa: E402, F401 -- intentional public re-exports
    _CompactionRoundContext,
    _provider_failure_hint,
    _provider_recovery_hint,
    _task_deadline_epoch,
    _mark_owner_stop_control_drained,
    _owner_stop_window_elapsed,
    _drain_incoming_messages,
    _context_reclaim_passes,
    _context_reclaim_materializations,
    _context_overflow_retries,
    _run_round_compaction,
    _RoundLimitContext,
    _account_compaction_usage,
    _handle_round_limit,
    _handle_forced_finalization,
    _handle_owner_stop_finalization,
    _handle_provider_unavailable,
    _maybe_deadline_local_finalize,
    _maybe_early_finalize,
    _finalize_limit_ctx,
)
