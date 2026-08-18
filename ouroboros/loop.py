"""LLM tool loop: call model, execute tools, repeat until final response."""

from __future__ import annotations

import json
import hashlib
import os
import queue
import pathlib
import time  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from dataclasses import dataclass, field, replace  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from typing import Any, Callable, Dict, List, Optional, Tuple

import logging

from ouroboros.llm import LLMClient, normalize_reasoning_effort, add_usage  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros import task_pacing  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.config import adaptive_quorum, get_context_mode, get_light_model, get_review_enforcement, get_task_review_mode, resolve_effort  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.review_cycles import REASON_REVIEW_CYCLES_EXHAUSTED  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.outcomes import ACCEPTANCE_ACCEPTED, ACCEPTANCE_BYPASS_REASON_BY_RAIL, ACCEPTANCE_BYPASS_REASONS, ACCEPTANCE_DECISION_STATUSES, ACCEPTANCE_FINALIZED_UNACCEPTED, ACCEPTANCE_REVISION_REQUESTED, REASON_ACCEPTANCE_REVIEW_SKIPPED_DEADLINE_RESERVE, REASON_DELIVERY_CONTROL_DEGRADED, REASON_OWNER_REQUESTED_FINALIZATION, RESULT_INFRA_FAILED, extract_final_answer, latest_agent_defined_verification, latest_unreconciled_failed_verification, latest_unreconciled_masked_verification, reviewable_effect_projection, should_nudge_verification, turn_has_reviewable_effects  # noqa: F401 -- moved readers import via the L-B leaves; the loop surface keeps these bindings
from ouroboros.observability import new_execution_id  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.tool_policy import CAPABILITY_OMISSION_HEADER, format_capability_omissions, initial_tool_schemas, list_non_core_tools, swarm_router_turn
from ouroboros.tools.registry import ToolRegistry
from ouroboros.context import build_user_content  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.context_budget import ContextReclaimRequest  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.context_compaction import compact_tool_history_llm, context_reclaim_transcript_sha256  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.deadline_utils import parse_deadline_ts, utc_now  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.utils import estimate_tokens, truncate_review_artifact  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.usage_accounting import (
    BudgetExceeded,
    PhysicalAttemptContext,  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
    PhysicalAttemptPreconditionFailed,  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
    last_physical_attempt_capture,  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
)

from ouroboros.loop_tool_execution import (
    StatefulToolExecutor,
    handle_tool_calls,
    prune_reclaim_trace_refs,  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
    reclaim_negative_memo,  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
    reclaim_trace_refs,  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
)
from ouroboros.loop_llm_call import call_llm_with_retry, emit_llm_usage_event  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves
from ouroboros.pricing import estimate_cost_optional  # noqa: F401 -- the loop module keeps its historical import surface for the L-B leaves

# Backward-compat alias for source-inspecting/monkeypatched tests.
_call_llm_with_retry = call_llm_with_retry

log = logging.getLogger(__name__)


def _handle_text_response(
    content: Optional[str],
    llm_trace: Dict[str, Any],
    accumulated_usage: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """Handle LLM response without tool calls (final response)."""
    if content and content.strip():
        llm_trace["reasoning_notes"].append(content.strip())
    return (content or ""), accumulated_usage, llm_trace


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
from ouroboros.loop_nudges import (  # noqa: E402, F401 -- intentional public re-exports
    _skill_names_touched_by_trace,
    _skill_finalization_message,
    _force_plan_decision,
    _force_plan_reminder,
    _force_plan_disclosure,
    _build_recent_tool_trace,
    _maybe_inject_self_check,
    _maybe_inject_time_budget_milestone,
    _maybe_inject_cost_budget_milestone,
    _DELEGATE_ACTIVITY_TOOLS,
    _note_nanny_delegate_activity,
    _nanny_metered_since_delegate_activity,
    _nanny_reminder_due,
    _nanny_burn_phrase,
    _maybe_inject_nanny_economics_reminder,
    _inject_round_checkpoints,
    _forced_delegation_note,
    _nanny_finalization_message,
    _maybe_inject_finalization_nudges,
    _answer_protocol_active,
    _contract_expected_output,
)
from ouroboros.loop_model_call import (  # noqa: E402, F401 -- intentional public re-exports
    _adopt_fallback_route,
    _snapshot_context_fit_usage,
    _restore_context_fit_usage,
    _run_cross_model_fallback_chain,
    _rebind_context_fit_plan,
    _RoundModelCallContext,
    _context_fit_round_id,
    _main_context_profile,
    _remember_main_fit,
    _measure_round_main_fit,
    _physical_context_for_fit,
    _dispatch_round_model,
    _run_main_reclaim,
    _measure_after_reclaim,
    _reproject_actual_overflow_low,
    _failed_capture_is_comparable,
    _strict_context_shrink_predicate,
    _emit_overflow_retry_skipped,
    _call_round_model,
)
from ouroboros.loop_budget import (  # noqa: E402, F401 -- intentional public re-exports
    _check_budget_limits,
    _resolve_task_cost_ceiling,
    _TREE_ACCOUNTING_MAX_STALE_SEC,
    _loop_tree_accounting,
    _soft_land_exhausted_ceiling,
    _service_finalization_evidence,
    _LoopExitContext,
    _handle_budget_exceeded,
    _cleanup_loop_resources,
    _service_identity_projection,
    _finalize_task_services,
    _prepare_post_tool_budget_context,
)
from ouroboros.loop_delivery import (  # noqa: E402, F401 -- intentional public re-exports
    DeliveryCandidate,
    _swarm_handoff_attempt,
    _compute_subagent_handoff,
    _delivery_evidence_state,
    _unaccepted_delivery_binding,
    _delivery_acceptance_binding,
    _publish_delivery_candidate,
    _replace_delivery_candidate,
    _ensure_explicit_acceptance_binding,
    _forced_unaccepted_binding,
    _live_delivery_candidate,
    _current_delivery_candidate,
    _degrade_retained_delivery_candidate,
    _merge_finalization_trace,
    _delivery_control_prompt,
    _delivery_replace_required,
    _delivery_keep_allowed,
    _arm_delivery_control,
    _hold_delivery_for_skill_action,
    _parse_delivery_control_object,
    _resolve_delivery_control,
    _compose_delivery_suffix,
    _no_tool_final_answer,
)
