"""Control tools: restart, timeout settings, scheduling, review, chat history, model switching."""

from __future__ import annotations

import json
import logging
import os  # noqa: F401
import queue  # noqa: F401
import shutil
import threading  # noqa: F401
import time
import uuid
from hashlib import sha256  # noqa: F401
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ouroboros.config import (
    apply_settings_to_env,  # noqa: F401
    get_max_subagent_depth,
    load_settings,
    save_settings,  # noqa: F401
)
from ouroboros.depth_evidence import parse_task_depth
from ouroboros.headless import prepare_task_drive, task_state_dir
from ouroboros.contracts.task_contract import (
    build_task_contract,
    effective_acceptance_claims,
    normalize_allowed_resources,
)
from ouroboros.tools.control_delegation import (
    _ensure_project_scope,
    admitted_depth_cap,
    child_budget_for_schedule,
    normalize_required_capabilities,
    profile_from_task_constraint,
    record_depth_limit_refusal,
    resolve_cooperative_write_root,
    schedule_delegation_refusal,
)
from ouroboros.tools.registry import active_repo_dir_for, system_repo_dir_for
from ouroboros.outcomes import normalize_outcome_axes
from ouroboros.task_results import (
    STATUS_COMPLETED,
    STATUS_REJECTED_DUPLICATE,
    STATUS_REQUESTED,
    validate_task_id,
    write_task_result,
)
from ouroboros.task_status import load_effective_task_result, wait_for_effective_tasks
from ouroboros.subagents import (
    LEGACY_SUBAGENT_FIELDS,
    build_subagent_envelope,
)
from ouroboros.subagent_runtime import (
    SubagentSelectionError,
    effective_runtime_subagent_settings,
    select_subagent_snapshot,
)
from ouroboros.tool_capabilities import ACTING_SUBAGENT_MODE, LOCAL_READONLY_SUBAGENT_MODE
from ouroboros.tool_policy import swarm_router_turn  # noqa: F401
from ouroboros.tools.registry import ToolContext, ToolEntry
from ouroboros.utils import append_jsonl, atomic_write_json, truncate_review_artifact, utc_now_iso, run_cmd  # noqa: F401

log = logging.getLogger(__name__)

VALID_SUBTASK_MEMORY_MODES = frozenset({"forked", "empty"})

# Guards parent-side shared ctx state mutated during (possibly parallel)
# schedule_subagent emission within one tool-call round. Process-local: a parent
# ctx is never shared across processes, so a threading.Lock is sufficient.


def _record_scheduled_subagent(ctx: ToolContext, record: Dict[str, Any]) -> None:
    """Append a scheduled-subagent record to ctx under the emit lock.

    The read-copy-append-setattr of ``_last_scheduled_subagents`` is a lost-update
    race when a burst of schedule_subagent calls is emitted in parallel; the lock
    serializes it. (list.append is atomic under the GIL, but the surrounding RMW
    is not.)
    """
    with _SCHEDULE_EMIT_LOCK:
        scheduled_records = list(getattr(ctx, "_last_scheduled_subagents", []) or [])
        scheduled_records.append(record)
        setattr(ctx, "_last_scheduled_subagents", scheduled_records)


def _emit_swarm_fanout(
    ctx: ToolContext,
    *,
    parent_task_id: str,
    root_task_id: str,
    depth: int,
    task_group_id: str,
    task_ids: List[str],
    role: str,
    requested_model_lane: str,
    objective: str,
    emitted_live: bool,
) -> None:
    """Emit one durable swarm_fanout telemetry event per spawn wave (WS8).

    The name avoids task_/llm_/tool_ prefixes and the event sets no
    delegation_role/subagent_task_id, so the Logs UI never renders a phantom
    child card or folds it into a grouped-task lane (web/modules/log_events.js).
    inter_wave_latency_sec reuses ``_last_wave_ts`` under the emit lock (no new
    persistent state).
    """
    now = time.time()
    with _SCHEDULE_EMIT_LOCK:
        prev = float(getattr(ctx, "_last_wave_ts", 0.0) or 0.0)
        inter_wave = round(now - prev, 3) if prev > 0 else None
        setattr(ctx, "_last_wave_ts", now)
    evt = {
        "ts": utc_now_iso(),
        "type": "swarm_fanout",
        "task_id": parent_task_id,
        "parent_task_id": parent_task_id,
        "root_task_id": root_task_id,
        "depth": depth,
        "task_group_id": task_group_id,
        "requested_count": len(task_ids),
        "task_ids": task_ids,
        "role": role,
        # The REQUEST. What the children actually ran on is a per-child DISPATCH
        # fact and lives on each child's own record — a wave event written before
        # any child started cannot know it, and `effective_model_lanes` used to
        # claim it anyway.
        "requested_model_lane": requested_model_lane,
        "slot_count": len(task_ids),
        "objective_preview": objective[:200],
        "emitted_live": bool(emitted_live),
        "inter_wave_latency_sec": inter_wave,
    }
    try:
        append_jsonl(ctx.drive_logs() / "events.jsonl", evt)
    except Exception:
        log.debug("Failed to emit swarm_fanout telemetry", exc_info=True)


def maybe_emit_delegated_run_fanout(ctx: ToolContext, *, run_id: str, route_id: str,
                                    objective: str, durable: bool) -> None:
    """swarm_fanout for a delegated harness run, only under host-attested Swarm intent.

    A task admitted through the Swarm button carries the typed metadata fact
    ``force_plan_source == "swarm"`` — the gate reads exactly that admission fact,
    never keywords or prompt text (P5). Only such a hosting task folds its
    delegate_start into swarm telemetry; an ordinary delegated run on a task that
    never asked for a swarm emits nothing. The event reuses the exact existing
    ``swarm_fanout`` wave shape (``requested_count=1``, ``role="delegated_run"``,
    requested lane = the selected session route) and means STARTED/REQUESTED, not
    completed. An uncustodied start (``durable=False``) is not attested and emits
    nothing. Telemetry must never break a start that already succeeded, so
    failures stay logged, never raised.
    """
    metadata = getattr(ctx, "task_metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    if not durable or metadata.get("force_plan_source") != "swarm":
        return
    task_id = str(getattr(ctx, "task_id", "") or "")
    try:
        depth = int(getattr(ctx, "task_depth", 0) or 0) + 1
    except (TypeError, ValueError):
        depth = 1
    try:
        _emit_swarm_fanout(
            ctx,
            parent_task_id=task_id,
            root_task_id=str(metadata.get("root_task_id") or task_id),
            depth=depth,
            task_group_id="",
            task_ids=[str(run_id or "")],
            role="delegated_run",
            requested_model_lane=str(route_id or ""),
            objective=str(objective or ""),
            emitted_live=True,
        )
    except Exception:
        log.debug("Failed to emit delegated-run swarm_fanout telemetry", exc_info=True)


def _subagent_slot_note(ctx: ToolContext, root_task_id: str) -> str:
    """Compact slot-occupancy transparency for the schedule_subagent result (v6.54.3, 1.6).

    Read-only queue-snapshot facts — the LLM decides what to do with them (P5);
    nothing here gates admission (the supervisor stays authoritative). Counts are
    from the last persisted snapshot, i.e. BEFORE this wave lands."""
    try:
        status_root = Path(str(getattr(ctx, "budget_drive_root", "") or ctx.drive_root))
        snap = json.loads((status_root / "state" / "queue_snapshot.json").read_text(encoding="utf-8"))
    except Exception:
        return ""

    def _is_tree_subagent(row: Any) -> bool:
        if not isinstance(row, dict):
            return False
        task = row.get("task") if isinstance(row.get("task"), dict) else row
        return (
            str(task.get("delegation_role") or "") == "subagent"
            and str(task.get("root_task_id") or "") == str(root_task_id or "")
        )

    active = sum(1 for r in (snap.get("running") or []) if _is_tree_subagent(r))
    queued = sum(1 for r in (snap.get("pending") or []) if _is_tree_subagent(r))
    try:
        from ouroboros.config import get_max_active_subagents_per_root
        cap = int(get_max_active_subagents_per_root())
    except Exception:
        return ""
    tail = "; children beyond the active cap WAIT for a free slot" if active >= cap else ""
    return f" [tree slots before this wave: {active}/{cap} active, {queued} queued{tail}]"


def _capability_mismatch_message(selected_profile: str, missing_caps: Any) -> str:
    """v6.57.0 (1.6): name the CORRECT spawn so the parent fixes a capability mismatch
    in one move instead of burning a round guessing (the prober-without-shell incidents).
    shell/write/edit/service/vcs need an ACTING child (a write_surface); a read-only child
    has no shell and no writable roots."""
    if set(missing_caps) & {"shell", "write", "edit", "service", "vcs"}:
        hint = (
            "These need an ACTING child: pass write_surface (self_worktree for a throwaway "
            "checkout to run shell/build in; external_workspace for the shared project tree; "
            "genesis for a from-scratch project). A read-only child has no shell/writable roots."
        )
    else:
        hint = (
            "Adjust the child's profile/lane so the declared capabilities are available, "
            "or drop capabilities the child does not actually need."
        )
    return (
        "⚠️ SUBAGENT_CAPABILITY_MISMATCH: selected child profile "
        f"{selected_profile!r} cannot satisfy required_capabilities={missing_caps}. " + hint
    )


def _finalize_schedule_emission(ctx: ToolContext, emission: Dict[str, Any]) -> str:
    """Record the scheduled wave, emit swarm_fanout telemetry, and build the
    tool-result string. Extracted from _schedule_task to keep that function
    within the per-function size budget (P7). The emission facts ride ONE spec
    dict — the same idiom as ``_validated_schedule_fields`` — keeping the
    signature inside the <8-parameter contract. Keys: ``task_ids``,
    ``requested_model_lane``, ``objective``, ``role``, ``depth``,
    ``parent_task_id``, ``root_task_id``, ``emitted_modes``, plus optional
    ``write_surface`` and ``coop_shared_tree``.

    It reports the REQUEST and nothing else. Until v6.87.28 it also printed an
    `effective_lane=` and a `CAPABILITY_DELTA` line, both produced by resolving the
    child inside the scheduling call — an answer about live availability, given
    before the child was queued, let alone started. The reduction now reaches the
    parent where it can act on it: in `[SUBTASK_OUTCOME]`, when it reads the answer
    and decides how far to trust it. ``coop_shared_tree`` is the ONE exception by
    design: the host-minted shared coop tree is a SCHEDULE-TIME fact (the parent's
    effective write_root input, not a dispatch-time resolution), and withholding it
    forced every wave to rediscover its own tree by trial and error (the submarine
    waves' 'user_files path blocked' loop)."""
    task_ids = list(emission.get("task_ids") or [])
    requested_model_lane = str(emission.get("requested_model_lane") or "")
    objective = str(emission.get("objective") or "")
    role = str(emission.get("role") or "")
    depth = int(emission.get("depth") or 0)
    parent_task_id = str(emission.get("parent_task_id") or "")
    root_task_id = str(emission.get("root_task_id") or "")
    emitted_modes = list(emission.get("emitted_modes") or [])
    write_surface = str(emission.get("write_surface") or "")
    coop_shared_tree = str(emission.get("coop_shared_tree") or "")
    configured = emission.get("configured_subagent") if isinstance(
        emission.get("configured_subagent"), dict
    ) else {}
    selected_id = str(configured.get("selected_subagent_id") or "")
    selected_route = configured.get("route") if isinstance(configured.get("route"), dict) else {}
    route_kind = str(selected_route.get("kind") or "")
    legacy_selection = bool(emission.get("legacy_selection"))
    worker_note = " (live queue emission requested)" if any(m == "live" for m in emitted_modes) else ""
    try:
        _record_scheduled_subagent(ctx, {
            "task_ids": task_ids,
            "requested_model_lane": requested_model_lane,
            "objective": objective,
            "role": role,
        })
    except Exception:
        pass
    try:
        _emit_swarm_fanout(
            ctx,
            parent_task_id=parent_task_id,
            root_task_id=root_task_id,
            depth=depth,
            task_group_id="",
            task_ids=task_ids,
            role=role,
            requested_model_lane=requested_model_lane,
            objective=objective,
            emitted_live=any(m == "live" for m in emitted_modes),
        )
    except Exception:
        pass
    slot_note = _subagent_slot_note(ctx, root_task_id)
    # v6.57.0 (1.6): preview the child's EFFECTIVE tool profile (shell/writable) so
    # the parent knows up front whether the child can run shell / write — the wasted
    # rounds where a prober child hit workspace_blocked came from neither side
    # knowing. AUTHORITY only: it carries no lane, because the lane is not resolved
    # yet and a preview that guesses one is the claim this release removed.
    profile_note = ""
    try:
        from ouroboros.tool_access import predicted_subagent_profile, summarize_subagent_profile

        profile_note = "\n" + summarize_subagent_profile(
            predicted_subagent_profile(write_surface=write_surface))
    except Exception:
        pass
    coop_note = ""
    if str(coop_shared_tree or "").strip():
        try:
            import pathlib as _pl

            _tree = _pl.Path(coop_shared_tree)
            coop_note = (
                f"\nshared coop tree: {_tree} — children write there "
                "(write_surface=external_workspace); you read it via "
                f"root=subagent_projects, path={_tree.name!r}/…"
            )
        except Exception:
            coop_note = f"\nshared coop tree: {coop_shared_tree}"
    commitment = (
        "ordinary recursive API actor"
        if route_kind == "api_model"
        else "recursive nanny; exact route/work-order authority is frozen before its first model call"
    )
    legacy_note = (
        "\nDEPRECATED_LEGACY_SELECTOR: deterministically mapped to this exact configured row; "
        "future calls must pass subagent_id."
        if legacy_selection else ""
    )
    return (
        f"Subagent request queued {task_ids[0]}: {objective} "
        f"(subagent_id={selected_id}, route={route_kind}, {commitment})"
        f"{worker_note}{slot_note}{profile_note}{coop_note}{legacy_note}"
    )


def disclosable_capability_delta(data: Dict[str, Any]) -> Dict[str, Any]:
    """The child's delta when it has something to SAY, else ``{}`` — ONE predicate.

    THE terminal parent-facing disclosure, and since v6.87.28 the only parent-facing
    one: the reduction is not known until the child is dispatched, so no scheduling
    result can carry it. It is a predicate rather than an inline test because the
    parent absorbs a child through TWO surfaces — `get_task_result`/`wait_task` read
    one child in full, `wait_tasks` projects a batch compactly — and the batch one
    is the surface a fan-out parent actually uses. It had the test in neither place
    and the disclosure in one, so a parent that scheduled five children and absorbed
    them in a burst was told nothing about any of them.

    A delta that took nothing away and ignored nothing is noise in every payload.
    """
    delta = data.get("capability_delta") if isinstance(data.get("capability_delta"), dict) else {}
    return delta if (delta.get("reduced") or delta.get("legacy_note")) else {}


def _subtask_outcome_summary(data: Dict[str, Any], receipts: list | None = None) -> str:
    ledger = data.get("verification_ledger") if isinstance(data.get("verification_ledger"), dict) else {}
    summary: Dict[str, Any] = {
        "outcome_axes": normalize_outcome_axes(data),
    }
    if isinstance(data.get("task_contract"), dict):
        summary["task_contract"] = data.get("task_contract")
    _delta = disclosable_capability_delta(data)
    if _delta:
        summary["capability_delta"] = _delta
    if isinstance(data.get("artifact_bundle"), dict):
        summary["artifact_bundle"] = data.get("artifact_bundle")
    if ledger:
        summary["verification_ledger"] = {
            "schema_version": ledger.get("schema_version"),
            "summary": ledger.get("summary") if isinstance(ledger.get("summary"), dict) else {},
            "entry_count": len(ledger.get("entries") or []) if isinstance(ledger.get("entries"), list) else 0,
        }
    if receipts:
        # W2: bounded per-receipt rows for the FULL single-child handoff ONLY
        # (get_task_result/wait_task — already uncapped surfaces): which checks
        # passed, not just counts, so a parent can absorb a child on receipt-level
        # green/red instead of prose. The wait_tasks BATCH projection deliberately
        # stays counts-compact (v6.17.0 birth shape + v6.71.2 measured compaction,
        # 694K->25K). Rows render through the SSOT identity projection + disclosed
        # bound (hard cap, exact omitted count).
        #
        # The bound is OUTSTANDING-FIRST, then newest: a plain newest-10 window let
        # a child that failed a check early and then produced ten greens hand the
        # parent an affirmatively all-green list, with the red only implied by a
        # count. The still-unreconciled SET is this repo's SSOT for exactly that
        # problem ("a newer red would let a latest-pointer erase an older still-red
        # one"), so every outstanding red / masked pass is carried first — tagged so
        # the parent sees WHY it is here — and the rest of the cap is filled with the
        # newest remaining receipts. The cap and its exact omitted count are unchanged.
        from ouroboros._outcome_receipts import (
            disclosed_list_projection,
            receipt_identity_projection,
            unreconciled_failed,
            unreconciled_masked,
        )

        rows = [r for r in receipts if isinstance(r, dict)]
        outstanding_kind: Dict[int, str] = {}
        for _receipt in unreconciled_failed(rows):
            outstanding_kind[id(_receipt)] = "unreconciled_failed"
        for _receipt in unreconciled_masked(rows):
            outstanding_kind.setdefault(id(_receipt), "unreconciled_masked_pass")
        ordered = [r for r in reversed(rows) if id(r) in outstanding_kind]
        ordered += [r for r in reversed(rows) if id(r) not in outstanding_kind]

        def _receipt_row(receipt: Any) -> Any:
            if not isinstance(receipt, dict):
                return truncate_review_artifact(str(receipt), limit=200)
            row = {"status": str(receipt.get("status") or "")}
            outstanding = outstanding_kind.get(id(receipt), "")
            if outstanding:
                row["outstanding"] = outstanding
            if "matched" in receipt:
                row["matched"] = receipt.get("matched")
            row.update(receipt_identity_projection(receipt, check_cap=200))
            return row

        summary.update(disclosed_list_projection(
            ordered, key="verification_receipts", limit=10, item=_receipt_row,
        ))
    return json.dumps(summary, ensure_ascii=False, indent=2, default=str)


def _build_acting_constraint(
    *,
    write_surface: str,
    write_root: str,
    protected_paths_grant: bool,
    external_tool_grants: Any,
    parent_workspace_root: str,
):
    """Validate a mutative-subagent request; return its constraint dict, or an
    error string for the LLM (which can then fall back to a read-only subagent).

    The toggle/surface checks here give the caller immediate feedback. The
    supervisor is the authoritative gate and provisions the self_worktree
    (filling write_root/base_sha) before the child runs.
    """
    from ouroboros.config import get_allow_mutative_subagents
    from ouroboros.contracts.task_constraint import VALID_WRITE_SURFACES

    if write_surface not in VALID_WRITE_SURFACES:
        allowed = ", ".join(sorted(VALID_WRITE_SURFACES))
        return (
            "⚠️ TOOL_ARG_ERROR (schedule_subagent): write_surface must be one of "
            f"{allowed} (or omit it for a read-only subagent)."
        )
    if not get_allow_mutative_subagents(write_surface):
        return (
            "⚠️ MUTATIVE_SUBAGENTS_DISABLED: acting children with "
            f"write_surface={write_surface!r} are disabled here. "
            "OUROBOROS_ALLOW_MUTATIVE_SUBAGENTS is the master gate: an explicit owner "
            "true/false applies to every surface; when it is empty the runtime mode "
            "decides — advanced/pro allow every surface, light allows the external "
            "build surfaces (external_workspace, genesis — they write outside the "
            "Ouroboros runtime) and keeps self_worktree (a checkout of the live body) "
            "off. Schedule a read-only subagent (omit write_surface), use an external "
            "surface, or have the owner enable the toggle."
        )
    grants: List[str] = []
    if isinstance(external_tool_grants, (list, tuple)):
        grants = [str(g).strip() for g in external_tool_grants if str(g).strip()]
    resolved_write_root = str(write_root or "").strip()
    if write_surface == "external_workspace" and not resolved_write_root:
        resolved_write_root = str(parent_workspace_root or "").strip()
    if write_surface == "external_workspace" and not resolved_write_root:
        return (
            "⚠️ TOOL_ARG_ERROR (schedule_subagent): write_surface=external_workspace "
            "requires write_root (the external project directory) or a parent workspace."
        )
    return {
        "mode": ACTING_SUBAGENT_MODE,
        "surface": write_surface,
        "write_root": resolved_write_root,
        "protected_paths_grant": protected_paths_grant,
        "external_tool_grants": grants,
        "parent_only_commit": True,
        "return_kind": "workspace_patch",
        "allow_enable": False,
        "allow_review": False,
    }


def _select_subagent_constraint(write_surface, write_root, protected_paths_grant, external_tool_grants, parent_workspace_root, caller_readonly=False):
    """Read-only default (no surface), a validated acting constraint, or an error string."""
    if not write_surface or str(write_surface).strip().lower() == "read_only":
        # `read_only` is the explicit, provider-safe alias for the omit-surface
        # read-only path (the handler also normalizes it; this guard keeps the selector
        # correct for any direct caller and matches the schema enum) — never acting.
        return {"mode": LOCAL_READONLY_SUBAGENT_MODE, "allow_enable": False, "allow_review": False}
    if caller_readonly:
        # A read-only subagent may delegate read-only children only — never spawn an acting one.
        return (
            "⚠️ MUTATIVE_SUBAGENTS_DISABLED: a read-only subagent cannot spawn a mutative (acting) "
            "child. Only the root agent, workspace tasks, or acting subagents may pass write_surface; "
            "schedule a read-only child instead."
        )
    return _build_acting_constraint(
        write_surface=write_surface,
        write_root=write_root,
        protected_paths_grant=protected_paths_grant,
        external_tool_grants=external_tool_grants,
        parent_workspace_root=parent_workspace_root,
    )


def _populate_subagent_event_extras(
    evt: Dict[str, Any], *, current_chat_id: Any, child_drive: Any, workspace_root: str,
    workspace_mode: str, executor_ref: Any, context: str, parent_task_id: str,
) -> None:
    """Add the optional fields of a schedule_subagent event in place (extracted from
    _schedule_task to keep it under the method gate; pure field assignment)."""
    if current_chat_id:
        evt["chat_id"] = current_chat_id
    if child_drive is not None:
        evt["drive_root"] = str(child_drive)
        evt["child_drive_root"] = str(child_drive)
    if workspace_root:
        evt["workspace_root"] = workspace_root
    if workspace_mode:
        evt["workspace_mode"] = workspace_mode
    if executor_ref:
        evt["executor_ref"] = executor_ref
        evt["metadata"] = {**(evt.get("metadata") if isinstance(evt.get("metadata"), dict) else {}), "executor_ref": executor_ref}
    if context:
        evt["context"] = context
    if parent_task_id:
        evt["parent_task_id"] = parent_task_id


def _prepare_child_drive(tid, status_drive_root, memory_mode, parent_project_id):
    """Prepare the forked/empty child drive. On failure clean up the drive + the
    task-state dir and return ``(None, error_string)``; otherwise ``(drive, "")``.
    (Extracted from _schedule_task to keep it under the method gate.)"""
    if memory_mode not in {"forked", "empty"}:
        return None, ""
    try:
        return prepare_task_drive(status_drive_root, tid, memory_mode, project_id=parent_project_id), ""
    except Exception as exc:
        shutil.rmtree(task_state_dir(status_drive_root, tid), ignore_errors=True)
        log.warning("Failed to prepare child drive for subtask %s", tid, exc_info=True)
        return None, f"⚠️ SUBTASK_DRIVE_ERROR: failed to prepare {memory_mode} child drive: {exc}"


def _earliest_deadline_at(requested: str, inherited: str) -> str:
    """The tighter of two ISO deadlines (either may be empty/unparseable)."""
    from ouroboros.deadline_utils import parse_deadline_ts

    stamps = {text: parse_deadline_ts(text) for text in (requested, inherited) if text}
    usable = {text: ts for text, ts in stamps.items() if ts is not None}
    if not usable:
        return requested or inherited
    return min(usable, key=lambda text: usable[text])


def _build_child_subagent_contract(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Build a delegated child's task contract from a single spec mapping (extracted
    from _schedule_task to keep it under the method size gate; one dict param to stay
    within the parameter-count discipline; pure construction)."""
    parent_contract = spec.get("parent_contract")
    objective = spec.get("objective", "")
    expected_output = spec.get("expected_output", "")
    constraints = spec.get("constraints", "")
    delegation_budget = spec.get("child_delegation_budget")
    narrowed_deadline_at = _earliest_deadline_at(
        str(spec.get("deadline_at") or ""),
        str(parent_contract.get("deadline_at") or "") if isinstance(parent_contract, dict) else "",
    )
    # The child's claims come from the parent's EXPLICIT acceptance_claims param —
    # its ingress — through the one effective-claims seam; there is no plan wave at
    # dispatch. Re-stated below even when EMPTY: omitted means the child has none,
    # never "inherit the parent's" (the deadline_at spread lesson).
    child_claims, _claims_source = effective_acceptance_claims(
        {"acceptance_claims": spec.get("acceptance_claims")}
    )
    return build_task_contract({
        "id": spec.get("tid"),
        "type": "task",
        "description": objective,
        "objective": objective,
        "expected_output": expected_output,
        "constraints": constraints,
        "workspace_root": spec.get("workspace_root", ""),
        "workspace_mode": spec.get("workspace_mode", ""),
        "project_id": spec.get("parent_project_id", ""),
        "allowed_resources": spec.get("allowed_resources"),
        # A caller may bind the child to an EARLIER deadline than the parent's when the
        # parent can only consume the child's handoff inside a narrower window (planning
        # scouts). Never LATER: the earliest of the two wins, so a requested deadline can
        # only tighten the inherited one.
        "deadline_at": narrowed_deadline_at,
        "parent_task_id": spec.get("parent_task_id", ""),
        "root_task_id": spec.get("root_task_id"),
        "session_id": spec.get("session_id", ""),
        "delegation_role": "subagent",
        "metadata": {
            "task_contract": {
                **parent_contract,
                "source": "parent_delegation",
                "objective": objective,
                "expected_output": expected_output,
                "constraints": constraints,
                # The spread above hands the child EVERY parent field, and this merged
                # mapping outranks the task-level keys in build_task_contract. Any field we
                # deliberately narrow must therefore be re-stated after it, or the parent's
                # value silently wins back — which is exactly what used to happen to a
                # requested child deadline whenever the parent carried one of its own.
                "deadline_at": narrowed_deadline_at,
                "delegation_budget": delegation_budget,
                "attachment_manifest": spec.get("attachment_manifest") or [],
                # Same lesson for the criteria carriers, re-stated even when EMPTY:
                # without these, the parent's claims/criteria leak into every child and
                # child verify receipts would "support" claims the child never owned.
                "acceptance_claims": child_claims,
                "success_criteria": [],
            } if isinstance(parent_contract, dict) else {
                "delegation_budget": delegation_budget,
                "acceptance_claims": child_claims,
                "attachment_manifest": spec.get("attachment_manifest") or [],
            },
        },
    })


def _resolve_executor_ref(ctx: Any) -> dict:
    """The child's workspace executor reference (docker/host), or {} when unavailable."""
    accessor = getattr(ctx, "workspace_executor_ref", None)
    if callable(accessor):
        try:
            candidate = accessor()
            if isinstance(candidate, dict) and candidate:
                return dict(candidate)
        except Exception:
            return {}
    return {}


def _inherited_workspace_from_active_repo(
    ctx: ToolContext, workspace_root: str, workspace_mode: str
) -> tuple[str, str]:
    """Inherit an external active workspace for readonly children when metadata is absent."""
    if workspace_root:
        return workspace_root, workspace_mode
    try:
        active = active_repo_dir_for(ctx).resolve(strict=False)
        system = system_repo_dir_for(ctx).resolve(strict=False)
        if active != system:
            return str(active), workspace_mode or "external"
    except Exception:
        pass
    return workspace_root, workspace_mode


def _materialize_child_attachment_manifest(
    parent_contract: Dict[str, Any], target_root: Path, task_id: str,
    *, owner_drive: Optional[Path] = None, owner_task_id: str = "",
) -> tuple[list[dict], str]:
    """Copy inherited task inputs into the child's own artifact store."""

    initial = parent_contract.get("attachment_manifest") if parent_contract else []
    inherited = list(initial) if isinstance(initial, list) else []
    if owner_drive is not None and owner_task_id:
        from ouroboros.owner_mailbox import owner_attachment_manifest

        inherited.extend(owner_attachment_manifest(owner_drive, owner_task_id))
    if not inherited:
        return [], ""
    from ouroboros.artifacts import materialize_inherited_attachment_manifest

    return materialize_inherited_attachment_manifest(inherited, target_root, task_id)


def schedule_subagent_properties() -> Dict[str, Any]:
    """SSOT for the schedule_subagent parameter surface: ONE object, TWO derived consumers.

    The PUBLIC schema is the model contract (`ToolEntry("schedule_subagent", …)` in `get_tools`,
    with `additionalProperties: False`). Ordinary arguments are derived from this one mapping;
    the only non-public exception is the bounded D23 legacy selector set carried through the
    registry for deterministic migration. This avoids the former pair of hand-maintained public
    parameter lists drifting apart (BIBLE P7).

    Returns a FRESH mapping per call, exactly as the inline literal did, so a caller that mutates
    a returned schema cannot corrupt every later `get_tools()`."""
    from ouroboros.tool_access import SUBAGENT_CAPABILITIES

    return {
        "subagent_id": {
            "type": "string",
            "description": (
                "Exact actor id from the Available subagents catalog. The selected row is "
                "snapshotted into the child, so later Settings edits do not retarget it."
            ),
        },
        "objective": {"type": "string", "description": "Focused child objective. Be specific about scope. State the OUTCOME you need, not a step-by-step script: on a delegated (harness) dispatch the child forwards the work to its own delegated run, and a script-shaped objective reads as orders to execute natively."},
        "expected_output": {"type": "string", "description": "Concrete handoff expected from the child."},
        "role": {"type": "string", "description": "Optional freeform role label for lineage/UI, e.g. architecture-reviewer."},
        "context": {"type": "string", "description": "Optional parent reference material. It is injected as context, not instructions; for a harness-dispatched child it becomes the WORK ORDER for its delegated run's prompt, so put the recipe/details here rather than in the objective."},
        "constraints": {"type": "string", "description": "Optional constraints/non-goals for the child."},
        "memory_mode": {
            "type": "string",
            "enum": sorted(VALID_SUBTASK_MEMORY_MODES),
            "description": "Child memory mode. Default forked copies stable memory only; empty starts blank. shared is disabled for live local subagents.",
        },
        "write_surface": {
            "type": "string",
            # No empty-string member: Google Gemini's function-calling validator
            # rejects empty enum values (400 INVALID_ARGUMENT). Read-only is the
            # default by OMITTING this param; `read_only` is an explicit, provider-safe
            # (non-empty) alias for the SAME read-only path, so an audit/read-only child
            # can NAME its intent instead of reaching for an acting surface like
            # self_worktree (the trap behind the read-only-audit cancel-storm). It is NOT
            # an acting VALID_WRITE_SURFACES member — it normalizes to the omit path.
            "enum": ["read_only", "self_worktree", "external_workspace", "genesis"],
            "description": "read_only (or omit) = read-only child auditing THIS repo. Otherwise the isolated write surface for a MUTATIVE child (see tool description). Acting surfaces require mutative subagents enabled (default ON in advanced/pro).",
        },
        "write_root": {"type": "string", "description": "For write_surface=external_workspace: the external project directory — a REAL external Git working tree, never runtime data. An installed non-Git skill payload is NOT an external workspace: delegate it directly with delegate_start(subagent_id=..., prompt=..., root='skill_payload', bucket=..., skill_name=...). OMIT write_root to build COOPERATIVELY from scratch — the host mints ONE shared git tree the whole subagent tree writes into together (deeper descendants inherit it), and you integrate the result as the sole committer. Ignored for self_worktree and genesis (both auto-provisioned)."},
        "protected_paths_grant": {"type": "boolean", "default": False, "description": "Allow the child to modify protected paths in its self_worktree. Honored only in pro runtime mode; you still re-check at integration."},
        "external_tool_grants": {"type": "array", "items": {"type": "string"}, "description": "Optional extension/MCP tool names to grant this mutative child. Denied by default."},
        "delegation_intent": {"type": "string", "description": "Optional: tell THIS child whether/how to delegate further (e.g. 'build the whole game; spawn your own children per subsystem and let them spawn too'). Propagated structurally into the child's delegation budget and surfaced in its prompt, so a 'use maximum subagents / grandchildren' intent is not lost. Defaults to inheriting the parent's intent."},
        "may_mutate": {"type": "boolean", "default": False, "description": "Optional: grant this child the intent to spawn MUTATIVE (acting) descendants of its own. Still bounded by the usual mutative-subagent gating and depth/active caps."},
        "may_fan_out": {"type": "boolean", "default": True, "description": "Optional: whether this child may spawn MULTIPLE children (a wave). Bounded by the per-root active cap."},
        "max_children": {"type": "integer", "default": 0, "description": "Optional soft cap on this child's own direct children (0 = inherit / configured cap)."},
        "required_capabilities": {
            "type": "array",
            "items": {"type": "string", "enum": list(SUBAGENT_CAPABILITIES)},
            "description": "Closed-enum capabilities this child must have (e.g. shell/vcs/write/service). The scheduler reconciles this with the selected profile before spawning; do not encode these needs in prose.",
        },
        # Per-call effort is retired. The selected Available-subagent row owns its
        # effort; a second request knob could contradict that immutable row or the
        # compound session route it pins.
        "deadline_at": {
            "type": "string",
            "description": "Optional ISO-8601 UTC instant after which this child's work is worthless to you (e.g. a scout whose handoff you can only consume inside a narrow window). NARROWING ONLY: the earlier of this and the parent's deadline wins, so it can tighten your own deadline but never extend it. Omit it to simply inherit the parent's.",
        },
        "acceptance_claims": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional concrete, checkable claims of what 'done' means for THIS child "
                "(plain strings, e.g. 'the collision module rejects overlapping hulls'). "
                "They become the child contract's acceptance_claims (ids claim_1..N in "
                "list order) — the child links verify_and_record receipts to them via "
                "criterion_id, and you see per-claim support at absorption. The child "
                "NEVER inherits your own claims: omitted means the child has none. Omit "
                "the field unless you can state real checks; empty/blank values are "
                "treated as absent."
            ),
        },
    }


def schedule_subagent_param_names() -> frozenset:
    """The handler's closed keyword set, DERIVED from the public schema above.

    Anything the schema does not expose is refused with the strict v6 message instead of being
    silently accepted — and because the set is derived, "what the schema exposes" is the only
    definition of it there is."""
    return frozenset(schedule_subagent_properties())


# Runtime-INTERNAL scheduling options, deliberately absent from the public schema and
# structurally unreachable from a model tool call: they ride in the POSITIONAL-ONLY `internal`
# mapping, which no keyword argument produced from tool-call JSON can ever bind to. Keeping
# them out of the signature is also what holds the handler inside the <8-parameter contract.
#
# The set is EMPTY as of v6.87.7: its only member, `deadline_at`, became a public parameter
# once the caller judged to be the right one turned out to be the parent LLM itself — it is
# the parent that knows when a child's handoff stops being useful. The seam stays because it
# is closed and cheap, and an unknown key here still fails loudly rather than being ignored.
_INTERNAL_SCHEDULE_OPTIONS: frozenset = frozenset()


def _validated_schedule_fields(params: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
    """Normalize and validate the public schedule_subagent fields.

    Returns ``(fields, "")`` or ``({}, refusal)``. Extracted from ``_schedule_task`` so
    the handler stays inside the method-size gate — argument validation is a coherent
    phase with one job, not a slice taken to shed lines.
    """
    deadline_at = str(params.get("deadline_at") or "").strip()
    memory_mode = str(params.get("memory_mode") or "forked").strip().lower()
    if deadline_at:
        # `deadline_at` became MODEL-AUTHORED in v6.87.7; it used to be computed by
        # plan_review, where neither check could fail. Both failures below are SILENT
        # without them (BIBLE P1): an unparseable stamp rides into the child contract
        # verbatim and simply never fires, so the parent believes it bound a child that is
        # running deadline-blind; and a past stamp makes the child emit its canned
        # "produce your best answer NOW" on round one, having done no work at all.
        from ouroboros.deadline_utils import parse_deadline_ts, utc_now

        parsed = parse_deadline_ts(deadline_at)
        if parsed is None:
            return {}, (
                "⚠️ TOOL_ARG_ERROR (schedule_subagent): deadline_at must be an ISO-8601 UTC "
                f"instant such as 2026-08-02T18:30:00Z (got: {deadline_at!r})."
            )
        if parsed <= utc_now():
            return {}, (
                "⚠️ TOOL_ARG_ERROR (schedule_subagent): deadline_at is already in the past "
                f"({deadline_at}); a child bound to it would finalize before doing any work."
            )
    objective = str(params.get("objective") or "").strip()
    if not objective:
        return {}, "⚠️ TOOL_ARG_ERROR (schedule_subagent): objective is required."
    expected_output = str(params.get("expected_output") or "").strip()
    if not expected_output:
        return {}, "⚠️ TOOL_ARG_ERROR (schedule_subagent): expected_output is required."
    raw_claims = params.get("acceptance_claims")
    if raw_claims is not None and (
        not isinstance(raw_claims, list)
        or any(not isinstance(item, str) for item in raw_claims)
    ):
        return {}, (
            "⚠️ TOOL_ARG_ERROR (schedule_subagent): acceptance_claims must be an array "
            "of plain strings (one checkable claim per entry)."
        )
    # Vacuous claims normalize to ABSENT, never an error (the v6.65.1/.2 lesson:
    # min-constraints shape placeholder junk instead of preventing it).
    acceptance_claims = [
        item.strip() for item in (raw_claims or []) if isinstance(item, str) and item.strip()
    ]
    if memory_mode not in VALID_SUBTASK_MEMORY_MODES:
        allowed = ", ".join(sorted(VALID_SUBTASK_MEMORY_MODES))
        return {}, (
            f"⚠️ TOOL_ARG_ERROR (schedule_subagent): memory_mode must be one of: {allowed}. "
            "memory_mode=shared is disabled for live local subagents until a sanitized shared-context mode exists."
        )
    return {
        "deadline_at": deadline_at, "objective": objective, "expected_output": expected_output,
        "role": str(params.get("role") or "researcher").strip() or "researcher",
        "context": str(params.get("context") or "").strip(),
        "constraints": str(params.get("constraints") or "").strip(),
        "memory_mode": memory_mode, "may_mutate": params.get("may_mutate", False),
        "acceptance_claims": acceptance_claims,
    }, ""


# A parameter this tool used to publish, mapped to the durable field it wrote.
# Separate from "unsupported" because a caller passing one is not guessing: it read
# a schema that was real, and "unsupported argument" hides that the capability still
# exists and is now derived. The REASON is not restated here — it is
# `LEGACY_SUBAGENT_FIELDS`, the same sentence the dispatch resolution puts on the
# record when it ignores a stored value, so the live refusal and the durable
# disclosure cannot come to disagree about why the field went away.
RETIRED_SCHEDULE_PARAMS: Dict[str, str] = {"effort": "reasoning_effort"}

# D23: accepted only by the real registry invocation path and intentionally absent
# from ``schedule_subagent_properties``.  The handler attribute is consumed by the
# generic registry seam; no tool-name special case or public schema alias exists.
HIDDEN_LEGACY_SCHEDULE_PARAMS: frozenset[str] = frozenset({"model_lane", "executor"})


def _context_task_depth(ctx: ToolContext) -> tuple[int, str]:
    try:
        return parse_task_depth(getattr(ctx, "task_depth", 0), default=0), ""
    except (TypeError, ValueError) as exc:
        return 0, str(exc)


def _schedule_task(ctx: ToolContext, internal: Dict[str, Any] | None = None, /, **params: Any) -> str:
    allowed_params = schedule_subagent_param_names() | HIDDEN_LEGACY_SCHEDULE_PARAMS
    retired = sorted(str(key) for key in params if key in RETIRED_SCHEDULE_PARAMS)
    if retired:
        return "⚠️ TOOL_ARG_ERROR (schedule_subagent): " + " ".join(
            f"{name} was withdrawn: {LEGACY_SUBAGENT_FIELDS[RETIRED_SCHEDULE_PARAMS[name]]}. "
            "Drop it — the owner's configured effort applies, exactly as it did when "
            f"{name} was omitted." for name in retired)
    unsupported = sorted(str(key) for key in params if key not in allowed_params)
    if unsupported:
        bad = ", ".join(unsupported)
        return (
            "⚠️ TOOL_ARG_ERROR (schedule_subagent): unsupported argument(s): "
            f"{bad}. Use the strict schema: subagent_id, objective, expected_output, "
            "optional role/context/constraints/memory_mode and (for "
            "mutative children) write_surface/write_root/protected_paths_grant/"
            "external_tool_grants."
        )
    internal = dict(internal or {})
    if set(internal) - _INTERNAL_SCHEDULE_OPTIONS:
        raise TypeError(f"_schedule_task: unknown internal scheduling option(s): "
                        f"{sorted(set(internal) - _INTERNAL_SCHEDULE_OPTIONS)}")
    fields, arg_error = _validated_schedule_fields(params)
    if arg_error:
        return arg_error
    deadline_at = fields["deadline_at"]
    objective = fields["objective"]
    expected_output = fields["expected_output"]
    role = fields["role"]
    context = fields["context"]
    constraints = fields["constraints"]
    memory_mode = fields["memory_mode"]
    may_mutate = fields["may_mutate"]
    try:
        configured_subagent, legacy_selection = select_subagent_snapshot(
            effective_runtime_subagent_settings(load_settings()),
            subagent_id=str(params.get("subagent_id") or ""),
            legacy_model_lane=params.get("model_lane"),
            legacy_executor=params.get("executor"),
            legacy_model_lane_supplied="model_lane" in params,
            legacy_executor_supplied="executor" in params,
        )
    except SubagentSelectionError as exc:
        return f"⚠️ {exc.code}: {exc.detail}"
    route = configured_subagent.get("route") if isinstance(configured_subagent.get("route"), dict) else {}
    requested_model_lane = "auto"  # bounded historical projection only
    requested_executor = "harness" if route.get("kind") == "agent_session" else "native"

    current_depth, depth_error = _context_task_depth(ctx)
    if depth_error:
        return (
            "⚠️ TOOL_ERROR (schedule_subagent): invalid_task_depth: "
            f"{depth_error}"
        )
    new_depth = current_depth + 1
    metadata = getattr(ctx, "task_metadata", {}) if isinstance(getattr(ctx, "task_metadata", {}), dict) else {}
    parent_contract = metadata.get("task_contract") if isinstance(metadata.get("task_contract"), dict) else {}
    if not parent_contract and isinstance(getattr(ctx, "task_contract", None), dict):
        parent_contract = getattr(ctx, "task_contract")
    max_depth = admitted_depth_cap(parent_contract, get_max_subagent_depth())
    if new_depth > max_depth:
        return record_depth_limit_refusal(
            ctx, fields, params, configured_subagent,
            current_depth=current_depth, new_depth=new_depth, max_depth=max_depth,
        )

    if getattr(ctx, 'is_direct_chat', False):
        from ouroboros.utils import append_jsonl
        try:
            append_jsonl(ctx.drive_logs() / "events.jsonl", {
                "ts": utc_now_iso(),
                "type": "schedule_task_from_direct_chat",
                "description": objective[:200],
                "warning": "schedule_subagent called from direct chat context — potential duplicate work",
            })
        except Exception:
            pass
    # EMPTINESS decides, not type. `ToolContext.task_contract` defaults to `{}`, so testing
    # only `isinstance(..., dict)` let that empty default win over a contract that really is
    # in `task_metadata` — and the parent's `deadline_at` lives in the contract, so the miss
    # silently un-narrowed every child deadline. Same precedence the registry already uses.
    current_task_id = str(getattr(ctx, "task_id", "") or "")
    parent_task_id = str(current_task_id or metadata.get("parent_task_id") or "").strip()
    root_task_id_seed = str(metadata.get("root_task_id") or current_task_id or "").strip()
    session_id = str(metadata.get("session_id") or "")
    try:
        current_chat_id = int(getattr(ctx, "current_chat_id", None) or 0)
    except (TypeError, ValueError):
        current_chat_id = 0
    budget_drive_root = str(metadata.get("budget_drive_root") or getattr(ctx, "budget_drive_root", "") or ctx.drive_root)
    status_drive_root = Path(budget_drive_root)
    if refusal := schedule_delegation_refusal(parent_contract, status_drive_root, parent_task_id):
        return refusal
    workspace_root = str(getattr(ctx, "workspace_root", "") or metadata.get("workspace_root") or "").strip()
    workspace_mode = str(getattr(ctx, "workspace_mode", "") or metadata.get("workspace_mode") or "").strip()
    workspace_root, workspace_mode = _inherited_workspace_from_active_repo(ctx, workspace_root, workspace_mode)
    parent_project_id = str(getattr(ctx, "project_id", "") or "").strip()
    requested_surface = str(params.get("write_surface") or "").strip().lower()
    # `read_only` is a first-class, provider-safe alias for "omit write_surface" (NOT a
    # VALID_WRITE_SURFACES acting surface) — normalize it to the read-only path so
    # constraint selection, mutating detection, and the event all treat it as read-only (P5).
    if requested_surface == "read_only":
        requested_surface = ""
    if requested_surface:
        from ouroboros.presence_authority import presence_ceiling_allows_delegated_surface

        if not presence_ceiling_allows_delegated_surface(ctx, requested_surface):
            return (
                "⚠️ PRESENCE_DELEGATION_BLOCKED: mutative delegation requires an exact "
                "selected write root for this surface in the inherited capability ceiling."
            )
    # FR2: a flat parent requesting external_workspace with no write_root builds
    # cooperatively in ONE host-minted shared tree (helper extracted to keep this
    # method under the size gate).
    effective_write_root, caller_profile, coop_err = resolve_cooperative_write_root(
        ctx, requested_surface, params.get("write_root", ""), workspace_root, metadata)
    if coop_err:
        return coop_err
    task_constraint = _select_subagent_constraint(
        requested_surface, effective_write_root, params.get("protected_paths_grant", False),
        params.get("external_tool_grants"), workspace_root,
        caller_readonly=(caller_profile == "local_readonly_subagent"))
    if isinstance(task_constraint, str):
        return task_constraint
    from ouroboros.tool_access import subagent_profile_satisfies

    required_caps, cap_error = normalize_required_capabilities(params.get("required_capabilities"))
    if cap_error:
        return f"⚠️ TOOL_ARG_ERROR (schedule_subagent): {cap_error}"
    selected_profile = profile_from_task_constraint(task_constraint)
    ok, missing_caps = subagent_profile_satisfies(selected_profile, required_caps)
    if not ok:
        return _capability_mismatch_message(selected_profile, missing_caps)
    allowed_resources = normalize_allowed_resources(
        (parent_contract.get("allowed_resources") if isinstance(parent_contract, dict) else {})
        or metadata.get("allowed_resources")
        or {}
    )
    executor_ref = _resolve_executor_ref(ctx)
    # SCHEDULING STATES INTENT AND NOTHING ELSE. The lane, the model, the effort, the
    # route, the profile and the effective executor are all resolved ONCE, at
    # dispatch, by `subagents.resolve_subagent_dispatch` — see it for why. What is
    # recorded here is what the parent ASKED for, plus the parent's own lane, which
    # is the fact an omitted lane inherits and which only the parent knows.
    tid = uuid.uuid4().hex[:8]
    task_ids: List[str] = [tid]
    root_task_id = root_task_id_seed or tid
    parent_model_lane = str(metadata.get("effective_model_lane") or "")
    parent_cognitive_route = {
        "model": str(getattr(ctx, "active_model", "") or metadata.get("model") or ""),
        "effort": str(getattr(ctx, "active_effort", "") or metadata.get("reasoning_effort") or ""),
        "use_local_model": bool(
            getattr(ctx, "active_use_local", metadata.get("use_local_model", False))
        ),
    }
    child_drive, _drive_err = _prepare_child_drive(
        tid, status_drive_root, memory_mode, parent_project_id)
    if _drive_err:
        return _drive_err
    child_attachment_manifest, attachment_error = _materialize_child_attachment_manifest(
        parent_contract, child_drive or status_drive_root, tid,
        owner_drive=Path(ctx.drive_root), owner_task_id=parent_task_id,
    )
    if attachment_error:
        shutil.rmtree(task_state_dir(status_drive_root, tid), ignore_errors=True)
        return f"⚠️ SUBTASK_ATTACHMENT_ERROR: {attachment_error}"

    # C3.1: propagate and narrow the parent's typed delegation intent.
    child_delegation_budget = child_budget_for_schedule(
        parent_contract,
        current_depth=current_depth, new_depth=new_depth, max_depth=max_depth,
        may_mutate=may_mutate, may_fan_out=params.get("may_fan_out", True),
        max_children=params.get("max_children", 0),
        intent_note=params.get("delegation_intent", ""),
    )

    child_contract = _build_child_subagent_contract({
        "tid": tid, "objective": objective, "expected_output": expected_output, "constraints": constraints,
        "workspace_root": workspace_root, "workspace_mode": workspace_mode, "parent_project_id": parent_project_id,
        "allowed_resources": allowed_resources, "parent_contract": parent_contract,
        "parent_task_id": parent_task_id, "root_task_id": root_task_id, "session_id": session_id,
        "child_delegation_budget": child_delegation_budget, "deadline_at": str(deadline_at or ""),
        "acceptance_claims": fields["acceptance_claims"],
        "attachment_manifest": child_attachment_manifest,
    })
    # The requested-status envelope carries the REQUEST. Its derived half stays
    # empty until dispatch fills it, so a queued child's public description never
    # names a lane, a model or an effort that no resolution has produced.
    envelope = build_subagent_envelope(
        task_id=tid,
        parent_task_id=parent_task_id,
        root_task_id=root_task_id,
        depth=new_depth,
        role=role,
        requested_lane=requested_model_lane,
        executor=requested_executor,
        status=STATUS_REQUESTED,
    )
    intent_fields = {
        "model_lane": requested_model_lane,
        "requested_model_lane": requested_model_lane,
        "parent_model_lane": parent_model_lane,
        "requested_executor": requested_executor,
        "configured_subagent": configured_subagent,
        "parent_cognitive_route": parent_cognitive_route,
    }
    evt = {
        "type": "schedule_subagent",
        "description": objective,
        "objective": objective,
        "expected_output": expected_output,
        "constraints": constraints,
        "role": role,
        "task_id": tid,
        "depth": new_depth,
        "ts": utc_now_iso(),
        "root_task_id": root_task_id,
        "session_id": session_id,
        "actor_id": f"subagent:{role}",
        "delegation_role": "subagent",
        "memory_mode": memory_mode,
        "project_id": parent_project_id,
        "budget_drive_root": budget_drive_root,
        "task_constraint": task_constraint,
        "write_surface": requested_surface,
        "task_contract": child_contract,
        "allowed_resources": allowed_resources,
        "required_capabilities": required_caps,
        **intent_fields,
        "subagent_envelope": envelope,
    }
    _populate_subagent_event_extras(
        evt, current_chat_id=current_chat_id, child_drive=child_drive,
        workspace_root=workspace_root, workspace_mode=workspace_mode,
        executor_ref=executor_ref, context=context, parent_task_id=parent_task_id,
    )
    try:
        write_task_result(
            status_drive_root,
            tid,
            STATUS_REQUESTED,
            parent_task_id=parent_task_id or None,
            root_task_id=root_task_id,
            session_id=session_id,
            actor_id=f"subagent:{role}",
            delegation_role="subagent",
            project_id=parent_project_id,
            role=role,
            description=objective,
            objective=objective,
            expected_output=expected_output,
            constraints=constraints,
            context=context,
            workspace_root=workspace_root,
            workspace_mode=workspace_mode,
            executor_ref=executor_ref,
            allowed_resources=allowed_resources,
            task_contract=child_contract,
            required_capabilities=required_caps,
            chat_id=current_chat_id or None,
            memory_mode=memory_mode,
            drive_root=str(child_drive) if child_drive is not None else "",
            child_drive_root=str(child_drive) if child_drive is not None else "",
            budget_drive_root=budget_drive_root,
            task_constraint=task_constraint,
            **intent_fields,
            subagent_envelope=envelope,
            result="Subagent request queued. Awaiting supervisor acceptance.",
        )
    except Exception:
        log.warning("Failed to persist requested task status for %s", tid, exc_info=True)
        try:
            (status_drive_root / "task_results" / f"{tid}.json").unlink(missing_ok=True)
        except Exception:
            pass
        if child_drive is not None:
            shutil.rmtree(child_drive, ignore_errors=True)
        return f"⚠️ SUBTASK_STATUS_ERROR: failed to persist requested status for {tid}; subagent was not scheduled."

    emitted_modes: List[str] = [_emit_control_event(ctx, evt)]
    return _finalize_schedule_emission(ctx, {
        "task_ids": task_ids,
        "requested_model_lane": requested_model_lane,
        "objective": objective,
        "role": role,
        "depth": new_depth,
        "parent_task_id": parent_task_id,
        "root_task_id": root_task_id_seed or current_task_id,
        "emitted_modes": emitted_modes,
        "write_surface": requested_surface,
        "configured_subagent": configured_subagent,
        "legacy_selection": legacy_selection,
        # Host-minted shared coop tree only (a caller-supplied write_root is the
        # parent's own knowledge already).
        "coop_shared_tree": (
            effective_write_root
            if effective_write_root and effective_write_root != str(params.get("write_root", "") or "")
            else ""
        ),
    })


setattr(_schedule_task, "_hidden_legacy_params", HIDDEN_LEGACY_SCHEDULE_PARAMS)


def _get_task_result(
    ctx: ToolContext, task_id: str, include_authority: bool = False,
    include_work_order_source: bool = False, source_start_char: Any = None,
    source_end_char: Any = None,
) -> str:
    """Read a task result, or one bounded canonical work-order source range."""
    metadata = getattr(ctx, "task_metadata", {}) if isinstance(getattr(ctx, "task_metadata", {}), dict) else {}
    status_drive_root = Path(str(metadata.get("budget_drive_root") or getattr(ctx, "budget_drive_root", "") or ctx.drive_root))
    data = load_effective_task_result(status_drive_root, task_id)
    if not data:
        return f"Task {task_id}: unknown or not yet registered"
    if bool(include_authority) or bool(include_work_order_source):
        from ouroboros.agent_startup_checks import task_result_authority_projection

        authority = task_result_authority_projection(data, drive_root=status_drive_root)
        payload: Dict[str, Any] = {
            "status": "available", "authority": authority,
            "source": {"tool": "get_task_result", "task_id": str(task_id)},
        }
        if bool(include_work_order_source):
            from ouroboros.subagent_work_order import (
                _source_task_from_context,
                work_order_source_projection,
            )

            projection, reason = work_order_source_projection(
                _source_task_from_context(ctx, str(task_id)),
                source_start_char,
                source_end_char,
            )
            source = {
                "kind": "task_result",
                "task_id": str(task_id),
                "tool": "get_task_result",
                "arguments": {
                    "task_id": str(task_id),
                    "include_authority": True,
                    "include_work_order_source": True,
                },
                "projection": "canonical_work_order",
            }
            payload["source"] = source
            payload["work_order_source"] = projection or {
                "schema": 1, "kind": "canonical_work_order", "status": "unavailable",
            }
            if reason:
                payload["work_order_source"]["reason"] = reason
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    status = data.get("status", "unknown")
    result = data.get("result", "")
    trace = data.get("trace_summary", "")
    try:
        from ouroboros.outcomes import read_verification_receipts_from_roots
        from ouroboros.task_status import _child_drive_candidates

        # During the pre-copy-back window ordinary verification lives on the
        # isolated child drive while a zero-run lifecycle receipt is already on
        # the canonical root. Merge both replicas; a non-empty canonical file is
        # not evidence that the local one contains nothing new.
        receipts = read_verification_receipts_from_roots(
            [*_child_drive_candidates(data), status_drive_root], task_id,
        )
    except Exception:
        receipts = []
    outcome_summary = _subtask_outcome_summary(data, receipts=receipts)
    from ouroboros.tools.join_ledger import _child_result_sha256

    child_result_sha256 = _child_result_sha256(data)
    # SSOT cost projection (C2): unknown never renders as $0.00 (and a null in
    # the stored result no longer crashes the f-string with a TypeError).
    from ouroboros.cost_projection import cost_display

    if status == STATUS_COMPLETED:
        output = (
            f"Task {task_id} [{status}]: cost={cost_display(data)}\n"
            f"child_result_sha256={child_result_sha256}\n\n"
            f"[SUBTASK_OUTCOME]\n{outcome_summary}\n[/SUBTASK_OUTCOME]\n\n"
            f"[BEGIN_SUBTASK_OUTPUT]\n{result}\n[END_SUBTASK_OUTPUT]"
        )
    elif status == STATUS_REJECTED_DUPLICATE:
        duplicate_of = str(data.get("duplicate_of") or "?")
        output = (
            f"Task {task_id} [{status}]: duplicate_of={duplicate_of}\n"
            f"child_result_sha256={child_result_sha256}\n\n"
            f"[SUBTASK_OUTCOME]\n{outcome_summary}\n[/SUBTASK_OUTCOME]\n\n"
            f"{result or f'Task was rejected as a duplicate of {duplicate_of}.'}"
        )
    else:
        output = (
            f"Task {task_id} [{status}]\n"
            f"child_result_sha256={child_result_sha256}\n\n"
            f"[SUBTASK_OUTCOME]\n{outcome_summary}\n[/SUBTASK_OUTCOME]\n\n"
            f"{result or 'No details available.'}"
        )
    if trace:
        output += f"\n\n[SUBTASK_TRACE]\n{trace}\n[/SUBTASK_TRACE]"
    return output


def _wait_attention_poll(
    ctx: ToolContext, after_ts: str, task_ids: List[str],
) -> Callable[..., Any]:
    """on_poll hook: break a sliced wait early when a child appends an attention beacon
    (blocker/question/interface_contract/review_requested/delegation_constraint).

    The cursor is context-local and per child: a beacon written before this
    particular tool call is still delivered, while a later wait in the same
    actor context does not replay it.  Equal-timestamp rows use their stable
    content identity, so the five-row response bound cannot strand the rest.
    """
    # tree_note/tree_read live in ouroboros/tools/task_tree.py (extracted for module size).
    from ouroboros.tools.task_tree import tree_root_id

    rid = tree_root_id(ctx)

    cursor_store = getattr(ctx, "_wait_attention_cursors", None)
    if not isinstance(cursor_store, dict):
        cursor_store = {}
        try:
            setattr(ctx, "_wait_attention_cursors", cursor_store)
        except Exception:
            # An exotic immutable context still gets correct delivery within
            # this hook instance; ordinary ToolContext objects retain it across
            # subsequent wait_task/wait_tasks calls.
            pass

    child_cursors: Dict[str, Dict[str, Any]] = {}
    for task_id in task_ids:
        key = f"{rid}:{task_id}"
        cursor = cursor_store.get(key)
        if not isinstance(cursor, dict):
            cursor = {"after_ts": str(after_ts or ""), "seen_ids": set()}
            cursor_store[key] = cursor
        if not isinstance(cursor.get("seen_ids"), set):
            cursor["seen_ids"] = {
                str(item) for item in (cursor.get("seen_ids") or []) if str(item)
            }
        child_cursors[str(task_id)] = cursor

    def _hook(_results: Dict[str, Any], _terminal: Dict[str, bool]) -> Any:
        if not rid:
            return None
        try:
            from ouroboros.task_tree_ledger import (
                tree_ledger_attention_after,
                tree_ledger_row_id,
            )

            attention = tree_ledger_attention_after(rid, "", task_ids=set(task_ids))
        except Exception:
            return None
        pending: List[tuple[Dict[str, Any], str]] = []
        for row in attention:
            task_id = str(row.get("task_id") or "")
            cursor = child_cursors.get(task_id)
            if cursor is None:
                continue
            ts = str(row.get("ts") or "")
            cursor_ts = str(cursor.get("after_ts") or "")
            row_id = tree_ledger_row_id(row)
            if ts < cursor_ts:
                continue
            if ts == cursor_ts and row_id in cursor["seen_ids"]:
                continue
            pending.append((row, row_id))
        if not pending:
            return None

        delivered = pending[:5]
        for row, row_id in delivered:
            cursor = child_cursors[str(row.get("task_id") or "")]
            ts = str(row.get("ts") or "")
            cursor_ts = str(cursor.get("after_ts") or "")
            if ts > cursor_ts:
                cursor["after_ts"] = ts
                cursor["seen_ids"] = set()
            cursor["seen_ids"].add(row_id)
        return {
            "reason": "child_attention_beacon",
            "beacons": [row for row, _row_id in delivered],
            "beacons_remaining": len(pending) - len(delivered),
        }

    return _hook


def cache_horizon_note(ctx: Any, elapsed_sec: Any) -> str:
    """One factual line when a blocking wait outlived the APPLIED prompt-cache TTL.

    Reads the RECORDED fact of this task's latest send — ``_last_prompt_cache_ttl``
    in the loop's accumulated usage (published on the tool ctx), converted by
    ``llm.cache_ttl_seconds`` — never a route-level prediction (a second predictor
    can disagree with the payload after route-filter/promotion/cap). Empty string
    when the horizon is unknown or not yet elapsed. UNKNOWN covers three cases,
    all silent: no cached send recorded, a route that carries no markers at all,
    and a send whose markers were BARE (reported ``"default"``) — a bare marker
    names no tier, so its horizon is the provider's business and inventing one
    would mislead the agent into re-planning its waits around a number nobody
    established. Only the explicitly stamped ``5m``/``1h`` tiers speak here.
    Deliberately NO token-count predictions: the submarine forensics showed the
    fact ("the wait outlived the cache") is what changes the agent's next decision
    (batch waits, longer single windows), while "~X tokens will re-write" is a
    counterfactual — the next send may reroute, compact, or still hit a live cache.

    REACHABILITY, honestly (each wait tool clamps its own window, so "all three
    wait tools carry the line" is a capability, not a per-configuration promise):
    at the shipped default TTL ``1h`` (3600s horizon) only ``wait_tasks`` (7200s
    clamp) can genuinely emit it; ``wait_task`` clamps at exactly 3600s and can
    only cross by a poll overshoot of a couple of seconds, and ``delegate_wait``
    clamps its WINDOW at ``config.DELEGATE_WAIT_WINDOW_MAX_SEC`` (1800s; the
    2100s ToolEntry ceiling above it is the kill timeout, not the window — F5)
    and cannot cross at all.
    At ``5m`` all three emit it. Pinned by
    tests/test_cache_optimization.py::test_cache_horizon_reachability_matches_the_wait_clamps —
    the call sites stay on all three because the tier is an owner setting, not a
    constant, and a wait tool that silently could not disclose would be worse.
    """
    try:
        elapsed = float(elapsed_sec)
    except (TypeError, ValueError):
        return ""
    usage = getattr(ctx, "_accumulated_usage", None)
    if not isinstance(usage, dict):
        return ""
    applied_ttl = str(usage.get("_last_prompt_cache_ttl") or "").strip()
    from ouroboros.llm import cache_ttl_seconds

    horizon = cache_ttl_seconds(applied_ttl)
    if horizon is None or elapsed <= horizon:
        return ""
    return (
        f"⚠️ configured prompt-cache horizon ({applied_ttl}, {horizon}s) elapsed during "
        f"this wait ({elapsed:.0f}s); the next model send may be cold."
    )


def _wait_for_task(ctx: ToolContext, task_id: str, timeout_sec: int = 180) -> str:
    """Wait for a subtask to reach a terminal status."""
    try:
        tid = validate_task_id(task_id)
    except ValueError as exc:
        return f"⚠️ TOOL_ARG_ERROR (wait_task): {exc}"
    try:
        timeout = max(0, min(int(timeout_sec), 3600))
    except (TypeError, ValueError):
        timeout = 180
    metadata = getattr(ctx, "task_metadata", {}) if isinstance(getattr(ctx, "task_metadata", {}), dict) else {}
    status_drive_root = Path(str(metadata.get("budget_drive_root") or getattr(ctx, "budget_drive_root", "") or ctx.drive_root))
    waited = wait_for_effective_tasks(
        status_drive_root, [tid], timeout_sec=timeout,
        on_poll=_wait_attention_poll(ctx, "", [tid]), poll_interval_sec=2.0,
    )
    early = waited.get("early_return")
    if early:
        header = "Task wait interrupted by a child attention beacon"
        extra = f"\n\n[CHILD_BEACONS]\n{json.dumps(early, ensure_ascii=False, indent=2)}\n[/CHILD_BEACONS]"
    else:
        header = "Task wait completed" if waited.get("all_terminal") else "Task wait timed out"
        extra = ""
    # B2 advisory (never a gate): if ANY other child of THIS parent is still in flight
    # while we block on this one, point at wait_tasks(any_terminal) so the agent absorbs
    # whichever finishes first instead of blocking serially on one id at a time.
    other_live = _count_live_sibling_children(ctx, status_drive_root, exclude_task_id=tid)
    if other_live >= 1:
        extra += (
            f"\n\n[ADVISORY] {other_live} other child(ren) still running/scheduled — consider "
            "wait_tasks(any_terminal) to absorb whichever finishes first instead of waiting one at a time."
        )
    horizon_note = cache_horizon_note(ctx, waited.get("elapsed_sec"))
    if horizon_note:
        extra += f"\n\n{horizon_note}"
    return f"{header} after {waited.get('elapsed_sec', 0):.1f}s.{extra}\n\n{_get_task_result(ctx, tid)}"


def _count_live_sibling_children(ctx: ToolContext, status_drive_root: Path, *, exclude_task_id: str) -> int:
    """Count this parent's children still running/scheduled/requested (excluding the one
    just waited on). Advisory only — a failure returns 0 so it never breaks wait_task."""
    parent_id = str(getattr(ctx, "task_id", "") or "").strip()
    if not parent_id:
        return 0
    try:
        from ouroboros.task_results import (
            STATUS_REQUESTED,
            STATUS_RUNNING,
            STATUS_SCHEDULED,
            list_task_results,
        )

        live = 0
        for item in list_task_results(status_drive_root, statuses=[STATUS_RUNNING, STATUS_SCHEDULED, STATUS_REQUESTED]):
            if str(item.get("task_id") or item.get("id") or "") == exclude_task_id:
                continue
            if str(item.get("parent_task_id") or "") == parent_id:
                live += 1
        return live
    except Exception:
        return 0


# Registration-race grace for a wait set in which NOTHING was minted (v6.91):
# "not YET registered" is a real state for a child scheduled moments ago, so a
# phantom-only wait still polls — but only for this long, instead of blocking
# the parent for the whole requested window on ids that exist nowhere.
_UNMINTED_WAIT_GRACE_SEC = 30.0


def _unminted_wait_ids(ctx: ToolContext, status_drive_root: Path, task_ids: List[str]) -> List[str]:
    """Ids with no trace on ANY surface this tree mints ids through: no task
    result, no queue-snapshot row, and no tree-ledger row naming them (v6.91).

    wave2's root blocked 900s slices on three hallucinated ids that wait_tasks
    silently polled as 'unknown' — while the real lead was missing from the wait
    set. The typed marker (plus the actual children roster) lets the parent
    repair its wait set instead of starving on phantoms. Fail-soft per probe: an
    unreadable surface treats the id as KNOWN — a real child must never be
    branded unknown on an I/O error."""
    from ouroboros.task_status import _load_queue_snapshot, _queue_task_status

    try:
        snapshot = _load_queue_snapshot(status_drive_root)
    except Exception:
        snapshot = {"_snapshot_invalid": True}
    ledger_ids: set = set()
    try:
        from ouroboros.task_tree_ledger import tree_ledger_rows
        from ouroboros.tools.task_tree import tree_root_id

        for row in tree_ledger_rows(tree_root_id(ctx)):
            for key in ("task_id", "child_task_id", "parent_task_id"):
                value = str(row.get(key) or "").strip()
                if value:
                    ledger_ids.add(value)
    except Exception:
        pass
    unknown: List[str] = []
    for tid in task_ids:
        try:
            if load_effective_task_result(status_drive_root, tid):
                continue
            queue_status, _ = _queue_task_status(snapshot, tid)
            if queue_status:  # running/scheduled row, or "unknown" on a missing snapshot (fail-soft)
                continue
            if tid in ledger_ids:
                continue
        except Exception:
            continue  # unreadable surface: treat as known
        unknown.append(tid)
    return unknown


def _children_roster_projection(
    ctx: ToolContext, status_drive_root: Path, *, limit: int = 30,
) -> Dict[str, Any]:
    """This parent's DIRECT children in the v6.71.2 compact field set (task_id/
    status/cost_usd/sha/outcome_axes) — never result envelopes; missing
    accounting projects null, never a confirmed-looking $0. The bound is
    DISCLOSED through the shared ``disclosed_list_projection`` (BIBLE P1): the
    payload carries ``children_roster`` plus ``children_roster_omitted``, the
    exact count of real children the cap hid — a silent ``[:limit]`` here could
    hide the very replacement id this repair surface exists to show. Fail-soft:
    an empty roster with omitted=0."""
    from ouroboros._outcome_receipts import disclosed_list_projection
    from ouroboros.task_status import find_child_tasks
    from ouroboros.tools.join_ledger import _child_result_sha256

    empty = {"children_roster": [], "children_roster_omitted": 0}
    my_id = str(getattr(ctx, "task_id", "") or "").strip()
    if not my_id:
        return empty
    try:
        rows = find_child_tasks(
            status_drive_root, parent_task_id=my_id, root_task_id="",
            exclude_task_id=my_id, scope="direct",
        )
    except Exception:
        return empty
    from ouroboros.cost_projection import cost_projection

    roster: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        _cost = cost_projection(row)
        roster.append({
            "task_id": str(row.get("task_id") or row.get("id") or ""),
            "status": row.get("status"),
            "cost_usd": _cost["cost_usd"],
            "accounted_upper_bound_usd": _cost["accounted_upper_bound_usd"],
            "child_result_sha256": _child_result_sha256(row),
            "outcome_axes": normalize_outcome_axes(row),
        })
    return disclosed_list_projection(
        roster, key="children_roster", limit=max(1, int(limit)), item=lambda entry: entry,
    )


def _wait_for_tasks(
    ctx: ToolContext,
    task_ids: List[str],
    timeout_sec: int = 600,
    mode: str = "all_terminal",
) -> str:
    """Wait for multiple subtasks and return a compact structural projection per child.

    A wait set whose ids were ALL unminted at entry ends after the registration
    grace instead of the full requested window (disclosed as
    ``wait_short_circuited``); any id that turns real during the grace makes it
    an ordinary wait again, with the remaining window intact."""
    if not isinstance(task_ids, list) or not task_ids:
        return "⚠️ TOOL_ARG_ERROR (wait_tasks): task_ids must be a non-empty list."
    from ouroboros.config import MAX_ACTIVE_SUBAGENTS_HARD_CAP
    from ouroboros.cost_projection import cost_projection

    if len(task_ids) > MAX_ACTIVE_SUBAGENTS_HARD_CAP:
        return (
            "⚠️ TOOL_ARG_ERROR (wait_tasks): task_ids is capped at "
            f"{MAX_ACTIVE_SUBAGENTS_HARD_CAP}."
        )
    normalized_ids: List[str] = []
    for item in task_ids:
        try:
            tid = validate_task_id(item)
        except ValueError as exc:
            return f"⚠️ TOOL_ARG_ERROR (wait_tasks): {exc}"
        if tid not in normalized_ids:
            normalized_ids.append(tid)
    try:
        timeout = max(0, min(int(timeout_sec), 7200))
    except (TypeError, ValueError):
        timeout = 600
    normalized_mode = str(mode or "all_terminal").strip().lower()
    if normalized_mode not in {"all_terminal", "any_terminal"}:
        return "⚠️ TOOL_ARG_ERROR (wait_tasks): mode must be all_terminal or any_terminal."
    metadata = getattr(ctx, "task_metadata", {}) if isinstance(getattr(ctx, "task_metadata", {}), dict) else {}
    status_drive_root = Path(str(metadata.get("budget_drive_root") or getattr(ctx, "budget_drive_root", "") or ctx.drive_root))
    # Typed unknown-id detection (v6.91): flagged ids KEEP polling — "not YET
    # registered" is a real state for a just-scheduled child — but a phantom id
    # is disclosed instead of silently starving the wait (wave2: three
    # hallucinated ids blocked 900s slices while the real lead went unwaited).
    entry_unknown_ids = _unminted_wait_ids(ctx, status_drive_root, normalized_ids)
    # One beacon cursor for the whole wait, so a two-phase window cannot skip an
    # attention beacon emitted during its first phase.
    _wait_since = ""
    # A wait set in which EVERY id is unminted cannot be satisfied by waiting —
    # nothing was ever scheduled to terminate. Spend only the registration-race
    # grace on it (wave1's root blocked its whole window on three hallucinated
    # ids), then re-probe; the moment any id turns real this becomes an ordinary
    # wait and gets the rest of the requested window.
    _phantom_only = bool(entry_unknown_ids) and len(entry_unknown_ids) == len(normalized_ids)
    first_window = min(float(timeout), _UNMINTED_WAIT_GRACE_SEC) if _phantom_only else float(timeout)
    waited = wait_for_effective_tasks(
        status_drive_root, normalized_ids, timeout_sec=first_window, mode=normalized_mode,
        on_poll=_wait_attention_poll(ctx, _wait_since, normalized_ids), poll_interval_sec=2.0,
    )
    if _phantom_only and first_window < float(timeout) and waited.get("early_return") is None:
        entry_unknown_ids = _unminted_wait_ids(ctx, status_drive_root, normalized_ids)
        if len(entry_unknown_ids) < len(normalized_ids):
            elapsed = float(waited.get("elapsed_sec") or 0.0)
            resumed = wait_for_effective_tasks(
                status_drive_root, normalized_ids,
                timeout_sec=max(0.0, float(timeout) - elapsed), mode=normalized_mode,
                on_poll=_wait_attention_poll(ctx, _wait_since, normalized_ids), poll_interval_sec=2.0,
            )
            resumed["elapsed_sec"] = float(resumed.get("elapsed_sec") or 0.0) + elapsed
            resumed["timeout_sec"] = float(timeout)
            waited = resumed
        else:
            # Disclosed, not silent: the wait ended early and says why.
            waited["wait_short_circuited"] = {
                "reason": "all_task_ids_unminted",
                "requested_timeout_sec": float(timeout),
                "waited_sec": round(float(waited.get("elapsed_sec") or 0.0), 1),
                "note": (
                    "Every requested task_id was unminted at entry and still unminted after "
                    f"the {int(_UNMINTED_WAIT_GRACE_SEC)}s registration grace, so the wait "
                    "returned instead of blocking for the full timeout. Fix the wait set from "
                    "children_roster / your schedule_subagent results, then wait again."
                ),
            }
    tasks = waited.get("tasks")
    if isinstance(tasks, dict):
        from ouroboros.tools.join_ledger import _child_result_sha256

        # Re-probe the entry-time unknowns once: an id minted mid-wait (queue
        # row or result appeared) is a real child, not a phantom.
        unknown_ids = [tid for tid in entry_unknown_ids if not tasks.get(tid)]
        if unknown_ids:
            unknown_ids = _unminted_wait_ids(ctx, status_drive_root, unknown_ids)

        # Compact STRUCTURAL projection (v6.71.2): the full public_task_result
        # envelope duplicated forensics (trace_refs, loop_outcome internals,
        # verification_ledger) into the parent context on every batch absorb.
        # The parent decision needs the semantic handoff only; the full envelope
        # stays on disk in task_results/<id>.json, addressable by
        # child_result_sha256 (the join-ledger SSOT hash), and is fetched with
        # get_task_result — a DISCLOSED omission (BIBLE P1), not silent
        # truncation. Single-task wait_task/get_task_result stay full.
        public_tasks: Dict[str, Any] = {}
        for tid, data in tasks.items():
            if str(tid) in unknown_ids:
                public_tasks[str(tid)] = {
                    "task_id": str(tid),
                    "status": None,
                    "unknown_task_id": True,
                    "note": (
                        "UNKNOWN_TASK_ID: not yet registered or never scheduled — no task "
                        "result, no queue row, and no tree-ledger row names this id in this "
                        "tree. Check it against your schedule_subagent results / the "
                        "children_roster below; an all_terminal wait cannot complete while "
                        "it stays unscheduled."
                    ),
                }
                continue
            if not isinstance(data, dict):
                public_tasks[str(tid)] = data
                continue
            # SSOT cost projection (C2): honest null (never a confirmed-looking $0),
            # the additive honest name beside the deprecated alias, and finality
            # only when the child's own record claims it.
            _cost = cost_projection(data)
            projected: Dict[str, Any] = {
                "task_id": str(data.get("task_id") or data.get("id") or tid),
                "status": data.get("status"),
                "cost_usd": _cost["cost_usd"],
                "accounted_upper_bound_usd": _cost["accounted_upper_bound_usd"],
                "cost_final": _cost["cost_final"],
                "child_result_sha256": _child_result_sha256(data),
                "outcome_axes": normalize_outcome_axes(data),
                "result": data.get("result"),
                "trace_summary": data.get("trace_summary"),
            }
            if data.get("duplicate_of"):
                projected["duplicate_of"] = str(data.get("duplicate_of"))
            # A capability reduction is a SEMANTIC handoff fact, not forensics: it is
            # what decides how far to trust this answer, and this is the surface a
            # fan-out parent absorbs its children through. Same predicate as the
            # single-child read, so the batch and the singleton cannot disagree.
            _delta = disclosable_capability_delta(data)
            if _delta:
                projected["capability_delta"] = _delta
            # Delegation honesty (Q1A, 2026-08-10 amendments): whether a
            # harness-dispatched child ACTUALLY delegated is a handoff fact the
            # fan-out parent absorbs here — the e9108a09 incident hid nine
            # native-only "harness" children behind this very projection.
            # Compact counts only; the full evidence stays in the envelope.
            _envelope = data.get("subagent_envelope") if isinstance(data.get("subagent_envelope"), dict) else {}
            _evidence = _envelope.get("execution_evidence") if isinstance(_envelope.get("execution_evidence"), dict) else {}
            if _evidence or str(data.get("effective_executor") or "") == "harness":
                _ee: Dict[str, Any] = {
                    "dispatch_executor": str(data.get("effective_executor") or ""),
                }
                if _evidence.get("evidence_read_failed"):
                    # Unreadable custody log (v6.94.0 landing-gate scope fix):
                    # the counts are UNKNOWN — emitting them as 0 beside the
                    # marker fabricated a "no runs" receipt for a log that was
                    # never read. The compact projection carries ONLY the typed
                    # marker; counts AND the substrate claim are omitted, the
                    # same omission rule subagents.envelope_from_task applies.
                    _ee["evidence_read_failed"] = True
                else:
                    if _evidence:
                        # Counts only when the envelope actually attested them:
                        # a result with no evidence recorded (pre-6.94) gets NO
                        # zero counts — absence means "no evidence yet", not
                        # "no runs".
                        _ee["delegated_runs_started"] = int(_evidence.get("delegated_runs_started") or 0)
                        _ee["delegated_runs_settled"] = int(_evidence.get("delegated_runs_settled") or 0)
                        _ee["delegated_runs_succeeded"] = int(_evidence.get("delegated_runs_succeeded") or 0)
                        _ee["delegated_runs_failed"] = int(_evidence.get("delegated_runs_failed") or 0)
                        _ee["delegated_runs_source_unresolved"] = int(
                            _evidence.get("delegated_runs_source_unresolved") or 0
                        )
                    # The substrate claim rides only when the envelope made one.
                    _substrate = str(data.get("actual_substrate") or _envelope.get("actual_substrate") or "")
                    if _substrate:
                        _ee["actual_substrate"] = _substrate
                        # C3: counters are delegated-run facts; the native
                        # (metered) contribution beside them is unknown.
                        _ee["native_contribution"] = "unknown"
                projected["execution_evidence"] = _ee
            public_tasks[str(tid)] = projected
        waited["tasks"] = public_tasks
        waited["tasks_note"] = (
            "Compact per-child projection. The full result envelope (trace_refs, "
            "loop_outcome, verification_ledger) remains on disk in task_results/"
            "<task_id>.json, addressable by child_result_sha256; get_task_result "
            "returns the full result text plus trace/outcome summaries."
        )
        if unknown_ids:
            waited["unknown_task_ids"] = unknown_ids
            # The repair surface: the ACTUAL direct children, compact v6.71.2
            # field set only (never envelopes), so the parent can fix its wait
            # set instead of re-polling phantoms. Carries children_roster plus
            # the disclosed children_roster_omitted count (never a silent cap).
            waited.update(_children_roster_projection(ctx, status_drive_root))
    horizon_note = cache_horizon_note(ctx, waited.get("elapsed_sec"))
    if horizon_note:
        waited["cache_horizon_note"] = horizon_note
    return json.dumps(waited, ensure_ascii=False, indent=2)


# promote_chat_to_task tool description (hoisted from get_tools for the
# 300-line function gate; v6.70.0 added the ground-truth-probe contract).
_PROMOTE_CHAT_DESCRIPTION = (
    "Promote real work out of this conversation into a supervised pooled task "
    "while the conversation remains available. Use it "
    "whenever a chat request needs tools/files/multi-step work rather than a "
    "conversational answer. Before framing the objective around an EXISTING artifact "
    "('check/fix/extend the X skill/file'), ground-truth its existence with one cheap probe "
    "first (skills: list_skills; files: list_files) — memory of past work is not evidence "
    "the referent still exists. Always give a short, human-readable task `title`. To "
    "CREATE A NEW NAMED PROJECT and do the work there (owner asked to 'create a "
    "project called X and …'), set `project_name` — the project is created now "
    "and this task runs inside it (my own judgment: the owner's phrasing is intent, "
    "not a keyword trigger — I name the project from what they actually want it "
    "called, and do not just answer or spawn a project-less task). `project_id` "
    "scopes to an existing project. When this new task continues one specific "
    "completed result shown by the host (the Main manifest or Project last-result "
    "preview), pass its internal id as `predecessor_task_id`; pass an empty string for fresh work. "
    "`workspace_root` points at a working folder. A project-scoped task inherits "
    "the project's working folder as its ACTIVE WORKSPACE by default (its file/"
    "shell/git tools operate there, not on the Ouroboros repo); pass "
    "workspace='none' for a folder-less task. Owner follow-ups can steer the "
    "running task. Report creation only when this tool returns "
    "OK; PROMOTE_REJECTED or PROMOTE_UNCONFIRMED means the task must not be "
    "claimed as created, and UNCONFIRMED must not be retried automatically."
)


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("set_tool_timeout", {
            "name": "set_tool_timeout",
            "description": "Update the global tool timeout in settings.json and apply it immediately without restart.",
            "parameters": {"type": "object", "properties": {
                "seconds": {"type": "integer", "description": "New timeout in seconds (>= 1)"},
            }, "required": ["seconds"]},
        }, _set_tool_timeout),
        ToolEntry("request_restart", {
            "name": "request_restart",
            "description": "Ask supervisor to restart runtime after a reviewed local commit or a non-evolution clean no-op; evolution requires its exact active commit receipt.",
            "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
        }, _request_restart),
        ToolEntry("promote_to_stable", {
            "name": "promote_to_stable",
            "description": "Promote ouroboros -> ouroboros-stable. Call when you consider the code stable.",
            "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
        }, _promote_to_stable),
        ToolEntry("promote_chat_to_task", {
            "name": "promote_chat_to_task",
            "description": _PROMOTE_CHAT_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "objective": {"type": "string", "description": "What the task must accomplish."},
                    "title": {"type": "string", "description": "A short human-readable task name (<=80 chars, e.g. 'Tic-tac-toe game'). Reused as the project name if the owner later turns the task into a project — so coin a clean, concise one.", "default": ""},
                    "project_name": {"type": "string", "description": "Set ONLY to create a brand-new NAMED project now and run this task inside it (e.g. 'airi research'). The display name; a filesystem id is derived from it.", "default": ""},
                    "expected_output": {"type": "string", "description": "What done looks like.", "default": ""},
                    "project_id": {"type": "string", "description": "Optional EXISTING project scope (filesystem-clean id).", "default": ""},
                    "workspace_root": {"type": "string", "description": "Optional absolute working-folder path (validated at admission: must be a git worktree root outside the Ouroboros repo/data). When omitted for a project-scoped task, the project's registered working_dir is used by default.", "default": ""},
                    "workspace": {"type": "string", "description": "Pass 'none' to opt OUT of the project room's default working folder (a folder-less task in a folder-ful project). Leave empty otherwise.", "default": ""},
                    "source": {"type": "string", "description": "Attach or clone the project's working folder in ONE move: a git URL (https://... or git@host:path — cloned server-side into the projects root; private repos fail typed auth_required) or an existing folder path (validated attach). The folder is registered on the project (provenance + trusted_at) and becomes this task's active workspace. Use for 'help me debug this GitHub repo / this folder' asks.", "default": ""},
                    "predecessor_task_id": {"type": "string", "description": "Required explicit selector: pass an empty string for fresh work, or the completed result id shown by the host routing manifest to continue it."},
                },
                "required": ["objective", "predecessor_task_id"],
            },
        }, _promote_chat_to_task),
        ToolEntry("ensure_project_scope", {
            "name": "ensure_project_scope",
            "description": (
                "Create (or attach to) a named Ouroboros PROJECT and scope THE CURRENT running "
                "task into it. Use this when you are ALREADY working a task and realize it should "
                "be a named project (the owner asked to 'create a project called X', or the work "
                "has grown into a real deliverable) — instead of a bare filesystem mkdir. Unlike "
                "promote_chat_to_task (which creates a NEW task in a project), this binds the task "
                "you are in: its journal_write and per-project knowledge start working, and its "
                "live progress routes to the project thread. Idempotent for the same project; it "
                "will NOT re-scope a task that already belongs to a different project."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "Display name for a NEW project (a filesystem id is derived from it). Honor the owner's stated name.", "default": ""},
                    "project_id": {"type": "string", "description": "Optional EXISTING project id (filesystem-clean) to attach to instead of creating one.", "default": ""},
                },
                "required": [],
            },
        }, _ensure_project_scope),
        ToolEntry("list_projects", {
            "name": "list_projects",
            "description": (
                "List the owner's projects (id, name, recency, running flag) — read-only. "
                "Use it in a main-chat turn to decide whether a message belongs to an existing "
                "project, then route it there with route_to_project."
            ),
            "parameters": {"type": "object", "properties": {
                "limit": {"type": "integer", "default": 50, "description": "Max projects to list."},
            }},
        }, _list_projects),
        ToolEntry("route_to_project", {
            "name": "route_to_project",
            "description": (
                "Route a main-chat message to an EXISTING project so the work continues in that "
                "project's own context (memory/journal/thread), keeping the main chat free. Use "
                "when a message clearly belongs to a known project (call list_projects first if "
                "unsure of the id). If confidence is low or several projects/tasks could match, "
                "CALL THIS TOOL with project_id='' and the owner's message: it emits the typed "
                "needs_manual_target acknowledgement with host-validated task options and New task "
                "in Project; prose alone cannot emit that typed choice. For brand-new work that is not yet a project, "
                "use promote_chat_to_task instead. When continuing one completed result from the "
                "Main host manifest, pass its internal `predecessor_task_id`; pass an empty string for fresh work. "
                "Returns a visible routing receipt."
            ),
            "parameters": {"type": "object", "properties": {
                "project_id": {"type": "string", "default": "", "description": "Target project id (filesystem-clean; see list_projects), or empty to emit typed needs_manual_target."},
                "message": {"type": "string", "description": "The owner message / work to route into the project."},
                "reason": {"type": "string", "default": "", "description": "Optional short why-this-project note (provenance)."},
                "predecessor_task_id": {"type": "string", "description": "Required explicit selector: pass an empty string for fresh work, or the completed result id listed by the Main host manifest to continue it."},
            }, "required": ["message", "predecessor_task_id"]},
        }, _route_to_project),
        ToolEntry("steer_task", {
            "name": "steer_task",
            "description": (
                "Deliver a follow-up/steering message to a host-listed RUNNING/PENDING owner root — YOU "
                "pick from current_chat.addressable_root_tasks in a Project room, or from "
                "main_routing_manifest.root_tasks in Main (including Project-bound roots). Use it when a message continues or redirects a task already "
                "in flight, instead of spawning a duplicate. The message reaches that task's mailbox and "
                "it picks it up at its next step. If no running task clearly fits, use promote_chat_to_task "
                "(new work) or answer inline — never steer a task you are unsure about."
            ),
            "parameters": {"type": "object", "properties": {
                "task_id": {"type": "string", "description": "Id of the running task to steer (from current_chat.running_tasks)."},
                "message": {"type": "string", "description": "The follow-up / steering message to deliver to that task."},
            }, "required": ["task_id", "message"]},
        }, _steer_task),
        ToolEntry("schedule_subagent", {
            "name": "schedule_subagent",
            "description": (
                "Schedule a live subagent (a child of Ouroboros). Returns task_id for later retrieval. "
                "DEFAULT is READ-ONLY: the child inspects local repo/data/history plus web/browser and "
                "returns findings (it cannot write local state, commit, enable tools, or run "
                "shell/review/runtime/skills). Set write_surface to spawn a MUTATIVE (acting) child that "
                "writes inside an ISOLATED root and returns a workspace.patch you integrate with "
                "integrate_subagent_patch — you remain the sole committer of the live body. write_surface: "
                "self_worktree (isolated git worktree of THIS repo, for parallel self-modification / best-of-N), "
                "external_workspace (an external project dir via write_root or the parent workspace), or "
                "genesis (a from-scratch new project — game/site/app/new Ouroboros — auto-provisioned as a fresh "
                "empty git repo under the durable projects root; the project directory IS the deliverable, not "
                "integrated into this repo). "
                "An installed skill payload under data/ is NOT a write_surface (runtime data is never one, by "
                "design): mutate it YOURSELF via delegate_start(subagent_id=..., prompt=..., root='skill_payload', bucket=..., skill_name=...) "
                "— a child cannot open a payload delegation — and schedule children only as read-only "
                "designers/reviewers for that work. "
                "COOPERATIVE MULTI-BUILDER vs GENESIS: when SEVERAL builder children must contribute to ONE new "
                "deliverable together, give each write_surface=external_workspace and OMIT write_root — the host "
                "mints ONE shared git tree the whole subagent tree writes into cooperatively (deeper descendants "
                "inherit it), and you integrate it as the sole committer. Use genesis instead only when EACH child "
                "should own its OWN standalone durable repo (e.g. best-of-N separate builds). "
                "Mutative children still cannot commit, run "
                "review/runtime/skills lifecycle, enable tools, or write cognitive memory. Nested delegation "
                "is allowed within configured depth/cap limits — use delegation_intent / may_mutate / "
                "may_fan_out to tell a child to recurse further, so a 'maximum subagents / grandchildren' "
                "request propagates structurally instead of collapsing into one flat layer. "
                "BURST + ABSORB: when several children are INDEPENDENT, emit them in ONE batch (parallel "
                "schedule_subagent calls in the same round) so they run concurrently, then absorb with "
                "wait_tasks(any_terminal) — handling whichever finishes first — instead of scheduling and "
                "blocking on them one at a time with serial wait_task calls. "
                "INDEPENDENT VERIFIER: to check a finished deliverable without builder bias, spawn a "
                "read-only child with memory_mode=empty whose objective carries ONLY the deliverable "
                "location + the task's acceptance criteria (NOT your own probes/assumptions) and have it "
                "verify through the task's own interface. Always retrieve "
                "the handoff with get_task_result, wait_task, or wait_tasks before relying on its results."
            ),
            "parameters": {
                "type": "object",
                # DERIVED, not restated: schedule_subagent_properties() is the single source
                # this schema and the handler's allowed-key set both read from.
                "properties": schedule_subagent_properties(),
                "required": ["subagent_id", "objective", "expected_output"],
                "additionalProperties": False,
            },
        }, _schedule_task),
        # cancel_task + peek_task + discard_child_result are registered by ouroboros/tools/join_ledger.py.
        ToolEntry("request_deep_self_review", {
            "name": "request_deep_self_review",
            "description": "Request an Atlas-backed deep self-review of the entire Ouroboros project. Uses OUROBOROS_MODEL_DEEP_SELF_REVIEW with its matching provider key, full core memory whitelist, and manifest accounting for every tracked repo path against the Constitution. Results go to chat and memory.",
            "parameters": {"type": "object", "properties": {
                "reason": {"type": "string", "description": "Why you want a review (context for the reviewer)"},
            }, "required": ["reason"]},
        }, _request_deep_self_review),
        ToolEntry("chat_history", {
            "name": "chat_history",
            "description": "Retrieve live and recent archived chat messages. Supports exact provenance/date filters, substring search, and pagination.",
            "parameters": {"type": "object", "properties": {
                "count": {"type": "integer", "default": 100, "description": "Number of messages (from latest)"},
                "offset": {"type": "integer", "default": 0, "description": "Skip N from end (pagination)"},
                "search": {"type": "string", "default": "", "description": "Text filter"},
                "provider": {"type": "string", "default": "", "description": "Exact transport provider"},
                "account_id": {"type": "string", "default": "", "description": "Exact transport account ID"},
                "conversation_id": {"type": "string", "default": "", "description": "Exact transport conversation ID"},
                "thread_id": {"type": "string", "default": "", "description": "Exact transport thread ID"},
                "actor_id": {"type": "string", "default": "", "description": "Exact platform actor ID"},
                "date_from": {"type": "string", "default": "", "description": "Inclusive ISO-8601 lower timestamp bound"},
                "date_to": {"type": "string", "default": "", "description": "Inclusive ISO-8601 upper timestamp bound"},
                "snapshot": {"type": "string", "default": "", "description": "Opaque snapshot returned by the first page; reuse it with offset to refuse shifted/mixed pages"},
            }, "required": [], "additionalProperties": False},
        }, _chat_history),
        ToolEntry("update_scratchpad", {
            "name": "update_scratchpad",
            "description": "Append a block to your working memory (scratchpad). Each call adds a "
                           "timestamped block; oldest blocks are auto-evicted when the cap (10) is reached. "
                           "Write what matters NOW — active tasks, decisions, observations. "
                           "Persists across sessions, read at every task start. "
                           "No-op on a project-scoped task (no per-project scratchpad); use knowledge_write for project facts.",
            "parameters": {"type": "object", "properties": {
                "content": {"type": "string", "description": "Content for this scratchpad block"},
            }, "required": ["content"]},
        }, _update_scratchpad),
        ToolEntry("send_user_message", {
            "name": "send_user_message",
            "description": "Send a proactive message to the user. Use when you have something "
                           "genuinely worth saying — an insight, a question, or an invitation to collaborate. "
                           "This is NOT for task responses (those go automatically).",
            "parameters": {"type": "object", "properties": {
                "text": {"type": "string", "description": "Message text"},
                "reason": {"type": "string", "description": "Why you're reaching out (logged, not sent)"},
            }, "required": ["text"]},
        }, _send_user_message),
        ToolEntry("update_identity", {
            "name": "update_identity",
            "description": "Update your identity manifest (who you are, who you want to become). "
                           "Persists across sessions. Obligation to yourself (Principle 1: Continuity). "
                           "Read your current identity first, then evolve it — add, refine, deepen. "
                           "Full rewrites are allowed but should be rare; continuity of self matters. "
                           "Use this only after substantive reflection or real experience — not on a "
                           "greeting or trivial turn. This is the only correct way to write identity; "
                           "never write memory/identity.md through write_file/edit_text. "
                           "No-op on a project-scoped task (identity is global and continuous, never per-project).",
            "parameters": {"type": "object", "properties": {
                "content": {"type": "string", "description": "Full identity content (prefer evolving over rewriting from scratch)"},
            }, "required": ["content"]},
        }, _update_identity),
        ToolEntry("toggle_evolution", {
            "name": "toggle_evolution",
            "description": "Enable or disable evolution mode. When enabled, Ouroboros runs continuous self-improvement cycles. Enabling requires runtime_mode 'advanced' or 'pro'; it is refused in 'light' mode.",
            "parameters": {"type": "object", "properties": {
                "enabled": {"type": "boolean", "description": "true to enable, false to disable"},
                "objective": {"type": "string", "default": "", "description": "Optional Evolution Campaign objective when enabling."},
            }, "required": ["enabled"]},
        }, _toggle_evolution),
        ToolEntry("toggle_consciousness", {
            "name": "toggle_consciousness",
            "description": "Control background consciousness: 'start', 'stop', or 'status'.",
            "parameters": {"type": "object", "properties": {
                "action": {"type": "string", "enum": ["start", "stop", "status"], "description": "Action to perform"},
            }, "required": ["action"]},
        }, _toggle_consciousness),
        ToolEntry("switch_model", {
            "name": "switch_model",
            "description": "Switch to a different LLM model or reasoning effort level. "
                           "Use when you need more power (complex code, deep reasoning) "
                           "or want to save budget (simple tasks). Takes effect on next round.",
            "parameters": {"type": "object", "properties": {
                "model": {"type": "string", "description": "Model name (e.g. anthropic/claude-sonnet-4). Leave empty to keep current."},
                "effort": {"type": "string", "enum": ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
                           "description": "Reasoning effort level (clamped to the model's real ceiling). Leave empty to keep current."},
            }, "required": []},
        }, _switch_model),
        ToolEntry("get_task_result", {
            "name": "get_task_result",
            "description": "Read the effective result or exact authority of a task, including one bounded canonical work-order source range when requested.",
            "parameters": {"type": "object", "required": ["task_id"], "properties": {
                "task_id": {"type": "string", "description": "Task ID returned by scheduling or exposed by the host routing manifest."},
                "include_authority": {"type": "boolean", "default": False,
                                      "description": "Return the exact selected result, task contract, origin, artifact references, and current plan-review authority."},
                "include_work_order_source": {"type": "boolean", "default": False,
                                               "description": "Return the canonical work-order source projection; provide both source_start_char and source_end_char for the exact bounded range."},
                "source_start_char": {"type": "integer", "description": "Inclusive character offset for the requested canonical work-order source range."},
                "source_end_char": {"type": "integer", "description": "Exclusive character offset for the requested canonical work-order source range."},
            }},
        }, _get_task_result),
        ToolEntry("wait_task", {
            "name": "wait_task",
            "description": "Wait for ONE subtask to reach a terminal status and return its effective result. May return EARLY (before terminal) if the child raises a tree_note blocker/question/interface_contract/review_requested/delegation_constraint beacon — the result then carries a [CHILD_BEACONS] block so you can steer, review, or override it. With SEVERAL children in flight, prefer wait_tasks(any_terminal) to absorb whichever finishes first rather than blocking serially on one id at a time.",
            "parameters": {"type": "object", "required": ["task_id"], "properties": {
                "task_id": {"type": "string", "description": "Task ID to check"},
                "timeout_sec": {"type": "integer", "default": 180, "description": "Maximum seconds to wait (default 180)."},
            }},
        }, _wait_for_task, timeout_sec=7200),
        ToolEntry("wait_tasks", {
            "name": "wait_tasks",
            "description": "Wait for MULTIPLE subtasks at once and return a compact structural projection per child (task_id, status, cost_usd, child_result_sha256, outcome_axes, result, trace_summary, capability_delta when the child has something to disclose, duplicate_of) — the right tool to ABSORB a batch of independent children you scheduled in one burst. The full per-child envelope stays on disk in task_results/<task_id>.json (child_result_sha256 pins the exact result you saw; get_task_result returns the full result text plus trace/outcome summaries). With mode=any_terminal it returns as soon as the FIRST child finishes (handle it, then call again for the rest) instead of blocking serially. The JSON also includes live_child_status (running/scheduled/terminal per child) and may early_return (before all terminal) on a child tree_note blocker/question/interface_contract/review_requested/delegation_constraint beacon so you can steer, review, or override mid-flight. An id no surface of this tree ever minted (no task result, no queue row, no tree-ledger row) is flagged unknown_task_id — 'not yet registered or never scheduled' — and unknown_task_ids + a compact children_roster of your ACTUAL direct children are attached so you can repair the wait set instead of re-polling phantoms.",
            "parameters": {"type": "object", "required": ["task_ids"], "properties": {
                "task_ids": {"type": "array", "items": {"type": "string"}, "description": "Task IDs returned by schedule_subagent."},
                "timeout_sec": {"type": "integer", "default": 600, "description": "Maximum seconds to wait (default 600)."},
                "mode": {"type": "string", "enum": ["all_terminal", "any_terminal"], "default": "all_terminal"},
            }},
        }, _wait_for_tasks, timeout_sec=7200),
    ]


# v7next F1 (D08): moved spans live in their owner leaves; re-exported here
# so this facade stays the single import surface for callers and tests.
from ouroboros.tools.control_events import (  # noqa: E402, F401 -- intentional public re-exports
    _PROMOTE_CONFIRM_POLL_SEC,
    _PROMOTE_CONFIRM_TIMEOUT_SEC,
    _SCHEDULE_EMIT_LOCK,
    _emit_and_wait_for_routing,
    _emit_control_event,
    _promotion_pool_disabled_from_snapshot,
    _routing_status_root,
    _wait_for_promotion_admission,
    _wait_for_routing_annotation,
)
from ouroboros.tools.control_routing import (  # noqa: E402, F401 -- intentional public re-exports
    _MISSING_PREDECESSOR_SELECTOR,
    _attach_client_surface,
    _attach_origin_from_metadata,
    _attach_predecessor_authority_from_metadata,
    _attach_swarm_intent,
    _cached_swarm_handoff,
    _finish_swarm_handoff,
    _list_projects,
    _predecessor_selector_error,
    _promote_chat_to_task,
    _route_to_project,
    _steer_task,
)
from ouroboros.tools.control_runtime import (  # noqa: E402, F401 -- intentional public re-exports
    _chat_history,
    _evolution_restart_block_reason,
    _promote_to_stable,
    _request_deep_self_review,
    _request_restart,
    _send_user_message,
    _set_tool_timeout,
    _switch_model,
    _toggle_consciousness,
    _toggle_evolution,
    _update_identity,
    _update_scratchpad,
)
