"""Dispatch worker EVENT_Q messages to supervisor handlers."""

from __future__ import annotations

import logging
import os  # noqa: F401
import pathlib
import subprocess  # noqa: F401
import threading  # noqa: F401
import time
from collections import deque  # noqa: F401
from typing import Any, Dict, Optional

from ouroboros.utils import append_jsonl, atomic_write_json, truncate_for_log, utc_now_iso  # noqa: F401
from ouroboros.config import get_max_subagent_depth  # noqa: F401 -- facade name tests monkeypatch
from ouroboros.tool_capabilities import ACTING_SUBAGENT_MODE, LOCAL_READONLY_SUBAGENT_MODE  # noqa: F401
from ouroboros.contracts.task_constraint import VALID_WRITE_SURFACES  # noqa: F401
from ouroboros.task_results import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_REJECTED_DUPLICATE,
    STATUS_SCHEDULED,  # noqa: F401 -- facade name tests read
    load_task_result,
    write_task_result,
)
from ouroboros.cost_projection import carry_cost_meta, live_root_cost_projection, with_cost_aliases  # noqa: F401
from ouroboros.outcomes import infra_failed_axes, normalize_outcome_axes
from ouroboros.post_task_checkpoint import post_task_synthesis_is_open
from ouroboros.subagent_messages import subagent_message_meta  # noqa: F401
from ouroboros.task_finalization import send_provider_death_notice
from supervisor.cognitive_operations import EVENT_HANDLERS as _CEH, _handle_cognitive_operation  # noqa: F401
from supervisor.task_dispatch import (  # noqa: F401 -- facade name tests import
    build_scheduled_task_payload as _build_scheduled_task_payload,
)
from supervisor.log_addressing import (  # re-export: one events surface
    address_ctx_event as _address_ctx,  # noqa: F401
    address_task_event as _address_task_event,  # noqa: F401  (tests pin it here)
    bound_project_chat_id as _bound_project_chat_id,
    make_server_log_sink,  # noqa: F401  (server.py installs it)
)

log = logging.getLogger(__name__)


# A progress frame's ``task_id`` is a ROUTING address — it says which live card the
# line lands on, NOT who wrote the line. The supervisor narrates a task's terminal
# path (grace requested, grace withdrawn) onto that task's own card, so those frames
# carry the task's id while the task itself did nothing. Host-authored frames set
# this key; ``_handle_send_message`` refuses to count them as the task's work.
# Without it the supervisor's own voice answers its own question — the grace toast
# stamped last_progress_at, the next 0.5s tick read the task as resumed, and the
# episode it had just opened was withdrawn before the worker could ever drain it.


def _routing_attachments(value: Any) -> Optional[list]:
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else None


def _parent_delegation_budget(ctx: Any, parent_task_id: Any, drive_root: Any) -> Dict[str, Any]:
    """Read the parent's canonical budget for supervisor-side admission."""
    parent = str(parent_task_id or "").strip()
    if not parent:
        return {}
    running = getattr(ctx, "RUNNING", {})
    if isinstance(running, dict):
        for meta in running.values():
            task = meta.get("task") if isinstance(meta, dict) else None
            if not isinstance(task, dict) or str(task.get("id") or "").strip() != parent:
                continue
            contract = task.get("task_contract")
            if isinstance(contract, dict) and isinstance(contract.get("delegation_budget"), dict):
                return contract["delegation_budget"]
    roots = [drive_root]
    canonical = getattr(ctx, "DRIVE_ROOT", "")
    if canonical and str(canonical) != str(drive_root):
        roots.append(canonical)
    for root in roots:
        try:
            row = load_task_result(root, parent)
        except Exception:
            row = None
        if isinstance(row, dict):
            contract = row.get("task_contract")
            if isinstance(contract, dict) and isinstance(contract.get("delegation_budget"), dict):
                return contract["delegation_budget"]
    return {}


# Durable terminal registry dedupes successful sends across restarts.


# In-flight latch for off-loop coop checkpoints: one commit run per root at a
# time. A re-trigger after completion is safe (the helper no-ops on a clean
# tree), so this is concurrency control, not a permanent phase marker. A
# trigger arriving WHILE a run is in flight cannot simply be dropped: the
# in-flight worker may have already sampled liveness and seen the (then-live)
# last child, so it will skip the commit — and the dropped trigger was the
# last one there is. Such triggers are remembered per root and replayed once
# after the latch clears; the replayed run revalidates liveness itself.


def _authoritative_terminal_cost(
    task_id: str, task: Dict[str, Any], result: Dict[str, Any], evt: Dict[str, Any], drive_root: pathlib.Path,
) -> Dict[str, Any]:
    """Project one terminal task/root from the physical-attempt authority."""
    from ouroboros.cost_projection import honest_accounted_amount
    from supervisor.state import reconstruct_task_cost

    authority_root = pathlib.Path(task.get("budget_drive_root") or drive_root)
    projection = reconstruct_task_cost(task_id, fields=True, drive_root=authority_root)
    from ouroboros.task_results import resolve_task_lineage

    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    root_id = str(result.get("root_task_id") or task.get("root_task_id") or evt.get("root_task_id") or "")
    parent_id = str(result.get("parent_task_id") or task.get("parent_task_id") or evt.get("parent_task_id") or "")
    lineage = resolve_task_lineage(
        task_id,
        metadata=metadata,
        root_task_id=root_id,
        parent_task_id=parent_id,
        delegation_role=(
            result.get("delegation_role")
            or task.get("delegation_role")
            or evt.get("delegation_role")
        ),
        original_task_id=(
            result.get("original_task_id")
            or task.get("original_task_id")
            or evt.get("original_task_id")
        ),
        timeout_retry_from=(
            result.get("timeout_retry_from")
            or task.get("timeout_retry_from")
            or evt.get("timeout_retry_from")
        ),
    )
    is_root = bool(lineage["is_root_task"])
    if is_root and projection.get("cost_accounting_status") == "available":
        try:
            from ouroboros.usage_accounting import usage_breakdown

            subtree = usage_breakdown(
                authority_root,
                root_task_id=str(lineage["root_task_id"] or task_id),
            )
            subtree_final = bool(subtree.get("cost_final"))
            subtree_amount = honest_accounted_amount(subtree)
            projection.update({
                "cost_usd_with_children": (
                    round(subtree_amount, 6) if subtree_amount is not None else None
                ),
                "cost_with_children_partial": not subtree_final,
                "cost_final": bool(projection.get("cost_final") and subtree_final),
                # THIRD site of the same class: `non_final_rows` is `cost_final`'s
                # DISCLOSED CAUSE and rides with it by contract (task_results.py), but
                # the root branch narrowed `cost_final` against the SUBTREE and then
                # left the row count describing this task alone — so a root turned
                # non-final purely by a child's open row reported a cause of 0, a flag
                # no reader could reconstruct.
                "non_final_rows": int(subtree.get("non_final_rows") or 0),
            })
        except Exception:
            log.error("Root subtree cost authority unavailable for %s", task_id, exc_info=True)
            projection.update({
                "cost_accounting_status": "unavailable", "cost_final": False,
                "cost_accounting_error": "ledger_unavailable",
                "cost_usd": None, "cost_usd_with_children": None,
                "cost_with_children_partial": True,
            })
    elif not is_root:
        rollup = result.get("cost_usd_with_children", evt.get("cost_usd_with_children"))
        projection["cost_usd_with_children"] = rollup
        projection["cost_with_children_partial"] = bool(
            result.get("cost_with_children_partial", evt.get("cost_with_children_partial", True))
        )
    checkpoint = result.get("root_phase_checkpoint")
    post_status = str(checkpoint.get("post_task_synthesis") or "") if isinstance(checkpoint, dict) else ""
    if is_root and post_task_synthesis_is_open(post_status):
        projection["cost_final"] = False
        projection["cost_with_children_partial"] = True
    # SSOT cost naming (C2): re-converge the additive/deprecated alias pairs at
    # this outer seam — the branches above legitimately mutate the deprecated
    # names, and the honest names must leave carrying the same values. This is
    # deliberately the LAST statement: any cost mutation added after it would
    # persist a diverged pair.
    return with_cost_aliases(projection)


def _task_done_review_projection(
    result: Dict[str, Any], event: Dict[str, Any],
) -> Dict[str, Any]:
    """Select the compact persisted reviewer view for one terminal event."""
    value = result.get("review_projection")
    if not isinstance(value, dict):
        value = event.get("review_projection")
    return value if isinstance(value, dict) and value.get("panels") else {}


def _close_campaign_after_owner_stop(exclude_task_id: str = "") -> None:
    """GR3-3 owner-stop backstop: close the campaign once its live task settled.

    An INCOMPLETE ``/evolve off`` / ``toggle_evolution(False)`` deliberately
    leaves the campaign OPEN over the still-live evolution task (closing it
    would declare a clean terminal that did not happen); the durable
    ``evolution_owner_stopped`` state flag blocks new cycles meanwhile. Every
    evolution terminal routes through ``_handle_evolution_task_done``, so this
    runs at exactly the moment the deferred close becomes honest — and no-ops
    whenever the owner never stopped or the campaign is already terminal.
    Never raises.

    GR4-6: the close is gated on NO OTHER evolution task being live — the
    multi-live incomplete-stop shape settles ONE task at a time, and closing
    on the first terminal would declare a clean stop over the others.
    ``exclude_task_id`` names the task whose terminal is being processed (its
    RUNNING row is popped only later, by ``_finish_task_done_dispatch``).
    """
    try:
        from supervisor.evolution_lifecycle import (
            _read_evolution_campaign,
            complete_evolution_campaign,
        )
        from supervisor.state import load_state

        if not bool(load_state().get("evolution_owner_stopped")):
            return
        if _read_evolution_campaign().get("status") not in {"active", "paused"}:
            return
        from supervisor.queue import PENDING, RUNNING, _queue_lock

        with _queue_lock:
            live = [
                str(task.get("id") or "")
                for task in PENDING
                if isinstance(task, dict) and str(task.get("type") or "") == "evolution"
            ] + [
                str(tid)
                for tid, meta in RUNNING.items()
                if isinstance(meta, dict)
                and isinstance(meta.get("task"), dict)
                and str(meta["task"].get("type") or "") == "evolution"
            ]
        live = [tid for tid in live if tid and tid != str(exclude_task_id or "")]
        if live:
            log.info(
                "owner-stop campaign close deferred: evolution task(s) still live: %s",
                live,
            )
            return
        complete_evolution_campaign(
            "owner stop completed after the live evolution task settled",
            status="stopped",
        )
    except Exception:
        log.debug("owner-stop campaign close backstop failed", exc_info=True)


def _handle_evolution_task_done(
    ctx: Any,
    *,
    evt: Dict[str, Any],
    task_id: Any,
    task: Dict[str, Any],
    task_done_event: Dict[str, Any],
    outcome_axes: Dict[str, Any],
    cost: Any,
    rounds: Any,
) -> None:
    """Project one evolution terminal through the existing campaign authority."""

    try:
        from supervisor.evolution_lifecycle import (
            _read_evolution_campaign,
            update_evolution_campaign_after_task,
        )

        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        if not metadata and isinstance(evt.get("metadata"), dict):
            metadata = evt.get("metadata") or {}
        transaction = (
            metadata.get("evolution_transaction")
            if isinstance(metadata.get("evolution_transaction"), dict)
            else {}
        )
        lifecycle_result = update_evolution_campaign_after_task(
            str(task_id or ""),
            cost_usd=cost,
            cost_accounting_status=str(
                task_done_event.get("cost_accounting_status") or "available"
            ),
            outcome_axes=outcome_axes,
            rounds=rounds,
            transaction=transaction,
        )
        if not isinstance(lifecycle_result, dict):
            log.warning("Evolution terminal rejected: invalid lifecycle result for %s", task_id)
            return
        if not lifecycle_result.get("accepted") or not lifecycle_result.get("persisted"):
            log.warning(
                "Evolution terminal rejected for %s: %s",
                task_id, lifecycle_result.get("reason") or "not_persisted",
            )
            return
        if lifecycle_result.get("replay"):
            return
        recorded_transaction = lifecycle_result.get("transaction")
        recorded_transaction = recorded_transaction if isinstance(recorded_transaction, dict) else {}
        try:
            from ouroboros.evolution_checkpoints import append_evolution_checkpoint

            append_evolution_checkpoint(
                ctx.DRIVE_ROOT,
                ctx.REPO_DIR,
                task_id=str(task_id or ""),
                campaign=_read_evolution_campaign(),
                outcome_axes=outcome_axes,
                cost_usd=cost,
                cost_accounting_status=str(
                    task_done_event.get("cost_accounting_status") or "available"
                ),
                rounds=rounds,
                transaction=recorded_transaction or transaction,
            )
        except Exception:
            log.debug("Failed to append evolution checkpoint", exc_info=True)
    except Exception:
        log.debug("Failed to update evolution campaign state", exc_info=True)
        return
    finally:
        # GR3-3: runs on EVERY evolution terminal — including rejected/replay
        # early returns above — so an owner stop that had to leave the campaign
        # open (still-live task) gets its deferred terminal close here.
        # GR4-6: the settling task is excluded from the liveness gate — its
        # RUNNING row is popped only later by _finish_task_done_dispatch.
        _close_campaign_after_owner_stop(exclude_task_id=str(task_id or ""))

    axes = normalize_outcome_axes({
        "status": task_done_event.get("status"),
        "outcome_axes": outcome_axes,
    })
    execution_status = str((axes.get("execution") or {}).get("status") or "").lower()
    objective_status = str((axes.get("objective") or {}).get("status") or "").lower()
    artifact_status = str((axes.get("artifacts") or {}).get("status") or "").lower()
    lifecycle_status = str(
        (axes.get("lifecycle") or {}).get("status")
        or task_done_event.get("status")
        or ""
    ).lower()
    failed_by_axes = (
        lifecycle_status in {"failed", "cancelled", "interrupted"}
        or execution_status in {"failed", "infra_failed", "degraded"}
        or objective_status in {"fail", "degraded"}
        or artifact_status in {"failed", "missing"}
    )
    if not failed_by_axes and (rounds or 0) >= 1:
        from supervisor.state import update_state

        update_state(lambda live: live.update(evolution_consecutive_failures=0))
    else:
        from supervisor.state import update_state

        failures_box: Dict[str, int] = {}

        def _bump_failures(live: Dict[str, Any]) -> None:
            failures_box["n"] = int(live.get("evolution_consecutive_failures") or 0) + 1
            live["evolution_consecutive_failures"] = failures_box["n"]

        update_state(_bump_failures)
        ctx.append_jsonl(
            ctx.DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": utc_now_iso(),
                "type": "evolution_task_failure_tracked",
                "task_id": task_id,
                "consecutive_failures": failures_box.get("n", 0),
                "cost_usd": cost,
                "rounds": rounds,
            },
        )
    try:
        from supervisor.state import update_state

        def _consume_autostop(live: Dict[str, Any]) -> None:
            if live.get("post_task_autostop"):
                live["evolution_mode_enabled"] = False
                live["post_task_autostop"] = False

        update_state(_consume_autostop)
    except Exception:
        log.debug("Post-task evolution autostop failed", exc_info=True)


# Single-shot registry for the provider-death owner notification. The old gate
# (`and task`, a live RUNNING row) also swallowed every reaper-delivered terminal:
# the reaper loop pops RUNNING before its task_done dispatches (regression tests:
# test_supervisor_reaper_notification.py). Process-local: after a restart the
# worst case is one repeated notification, never a lost one.
_PROVIDER_DEATH_NOTIFIED: set[str] = set()


def _maybe_notify_provider_death(
    ctx: Any,
    task_id: Any,
    task: Dict[str, Any],
    final_task_result: Dict[str, Any],
    task_done_event: Dict[str, Any],
) -> None:
    """Provider-death honesty (P1): tell the owner a root task terminalized by a
    provider outage was NOT completed — the historical shape was 95 minutes of
    silence behind a result claiming "completed". Runs AFTER the task-done
    bookkeeping (cleanup never depends on chat delivery) and registers the id in
    the single-shot registry only after a SUCCESSFUL send, so a raising send is
    retried by a later dispatch instead of being lost. Never raises."""
    if not (
        task_id
        and str(task_id) not in _PROVIDER_DEATH_NOTIFIED
        and str(
            task.get("delegation_role") or final_task_result.get("delegation_role") or ""
        ) != "subagent"
        and str(task_done_event.get("reason_code") or "") == "provider_unavailable"
        and str(task_done_event.get("status") or "") == STATUS_FAILED
    ):
        return
    notify_chat = int(task_done_event.get("chat_id") or 0)
    if not notify_chat:
        return
    try:
        # Promise only what works: the resume endpoint serves budget-paused
        # PENDING tasks (task_lifecycle.resume_budget_paused_task), never a
        # failed terminal — "resume" here was a false owner promise.
        if not send_provider_death_notice(
            ctx, notify_chat, task_id, final_task_result,
        ):
            return
    except Exception:
        log.warning(
            "Provider-death owner notification failed for %s", task_id, exc_info=True,
        )
        return
    _PROVIDER_DEATH_NOTIFIED.add(str(task_id))


def _finish_task_done_dispatch(
    evt: Dict[str, Any],
    ctx: Any,
    *,
    task_id: Any,
    worker_id: Any,
    task: Dict[str, Any],
    final_task_result: Dict[str, Any],
    task_done_event: Dict[str, Any],
) -> None:
    """Notify lineage, release queue state, and preserve terminal compatibility."""

    from ouroboros.project_dialogue import (
        append_terminal_task_projection,
        enqueue_project_completion_summary,
    )

    append_terminal_task_projection(
        ctx.DRIVE_ROOT, str(task_id or ""), task, final_task_result, task_done_event,
    )

    enqueue_project_completion_summary(
        ctx.DRIVE_ROOT, evt, str(task_id or ""), task, final_task_result, task_done_event,
    )

    if task_id and str(task.get("delegation_role") or "") == "subagent":
        effective_result = (
            final_task_result
            or load_task_result(ctx.DRIVE_ROOT, str(task_id or ""))
            or {}
        )
        from supervisor.message_bus import notification_chat_route
        from supervisor.subagent_task_truth import enrich_task_done_event

        _envelope = enrich_task_done_event(task_done_event, effective_result)
        # Membership, not truthiness (C4): chat 0 real, negative A2A.
        chat_id = notification_chat_route(
            _bound_project_chat_id(
                ctx, task_id, task.get("parent_task_id"), task.get("root_task_id")
            ) or None,
            task.get("chat_id"),
        )
        if chat_id is not None:
            status = str(
                effective_result.get("status")
                or evt.get("status")
                or STATUS_COMPLETED
            )
            status_display = {
                STATUS_COMPLETED: ("✅", "completed", "completed"),
                STATUS_FAILED: ("❌", "failed", "failed"),
                STATUS_REJECTED_DUPLICATE: ("⚠️", "rejected", "rejected"),
                STATUS_CANCELLED: ("⏹️", STATUS_CANCELLED, STATUS_CANCELLED),
                STATUS_INTERRUPTED: ("⏹️", STATUS_INTERRUPTED, STATUS_INTERRUPTED),
            }.get(status, ("ℹ️", status or "done", status or "finished"))
            icon, subagent_event, verb = status_display
            result_text = str(effective_result.get("result") or "")
            trace_text = str(effective_result.get("trace_summary") or "")
            constraint = effective_result.get("task_constraint")
            constraint = constraint if isinstance(constraint, dict) else {}
            # The `cost_usd: None` seed keeps the frame's long-standing shape
            # (both alias spellings always present, null when unknown) even for a
            # terminal event that carried no cost field at all.
            _cost_meta = carry_cost_meta({"cost_usd": None, **task_done_event})
            progress_meta = {
                "subagent_event": subagent_event,
                "subagent_task_id": str(task_id or ""),
                "root_task_id": str(task.get("root_task_id") or ""),
                "parent_task_id": str(task.get("parent_task_id") or ""),
                "delegation_role": "subagent",
                "subagent_role": str(task.get("role") or ""),
                "write_surface": str(constraint.get("surface") or ""),
                "status": status,
                # C2/C12: both alias spellings plus EVERY openness/integrity
                # marker accounting recorded. The VALUES come from the cost SSOT
                # (`_cost_meta` above) so a marker added there arrives here too;
                # the KEYS stay literal because a ChatOutbound frame's key set
                # must be statically checkable (tests/test_contracts.py) — and
                # `tests/test_cost_projection.py` fails if this literal ever
                # stops covering the SSOT. The hand-picked list this replaces
                # dropped `reserved_usd`, `unresolved_upper_bound_usd` and the
                # ledger integrity marker, leaving an unexplained "not final".
                "cost_usd": _cost_meta.get("cost_usd"),
                "accounted_upper_bound_usd": _cost_meta.get("accounted_upper_bound_usd"),
                "cost_with_children_partial": _cost_meta.get("cost_with_children_partial"),
                "unknown_unmetered": _cost_meta.get("unknown_unmetered"),
                "non_final_rows": _cost_meta.get("non_final_rows"),
                "reserved_usd": _cost_meta.get("reserved_usd"),
                "unresolved_upper_bound_usd": _cost_meta.get("unresolved_upper_bound_usd"),
                "ledger_integrity_degraded": _cost_meta.get("ledger_integrity_degraded"),
                "cost_accounting_error": _cost_meta.get("cost_accounting_error"),
                "cost_accounting_status": str(
                    task_done_event.get("cost_accounting_status") or "unavailable"
                ),
                "cost_final": bool(task_done_event.get("cost_final", False)),
                "result": truncate_for_log(result_text, 4000),
                "result_truncated": len(result_text) > 4000,
                "trace_summary": truncate_for_log(trace_text, 4000),
                "trace_summary_truncated": len(trace_text) > 4000,
                "error": truncate_for_log(str(effective_result.get("error") or ""), 1000),
                "artifact_status": str(effective_result.get("artifact_status") or ""),
                # The terminal frame carries the route so the finished card's chip can be
                # rebuilt on replay, and the completion-seam EVIDENCE (below) so the chip
                # upgrades from the neutral "dispatched" decision to what actually ran.
                "executor_route": str(effective_result.get("executor_route") or ""),
            }
            if isinstance(_envelope.get("execution_evidence"), dict):
                progress_meta["execution_evidence"] = _envelope["execution_evidence"]
            if _envelope.get("actual_substrate"):
                progress_meta["actual_substrate"] = str(_envelope["actual_substrate"])
            if isinstance(task_done_event.get("outcome_axes"), dict):
                progress_meta["outcome_axes"] = task_done_event["outcome_axes"]
            if task_done_event.get("reason_code"):
                progress_meta["reason_code"] = str(task_done_event["reason_code"])
            if "review_projection" in task_done_event:
                progress_meta["review_projection"] = task_done_event["review_projection"]
            ctx.send_with_budget(
                chat_id,
                f"{icon} Subagent {task_id} {verb} ({task.get('role') or 'researcher'}).",
                is_progress=True,
                task_id=str(task_id or ""),
                progress_meta=progress_meta,
            )

    from supervisor.queue import _queue_lock, clear_acceptance_fence_for_root

    with _queue_lock:
        if task_id:
            ctx.RUNNING.pop(str(task_id), None)
            # A child's settled result is the parent's cue to START integrating,
            # so settlement counts as the PARENT's own progress. Without this
            # stamp a coordinator blocked in wait_tasks was idle-killed exactly
            # when its last child delivered (the completed child instantly left
            # RUNNING, so _subtree_progressing went dark and only the grace
            # window remained). Own progress also lets the existing spare
            # machinery (resolve_grace_episode_for_spared_task) withdraw an
            # outstanding finalization-grace episode on the next enforce tick.
            # A one-shot event per child terminal — unlike subtree narration,
            # it cannot re-arm/flicker episodes. `task` is {} for reaper-delivered
            # terminals (RUNNING popped before dispatch), so fall back to the
            # durable result for the parent id — same shape the notification
            # gate handles.
            parent_meta = ctx.RUNNING.get(str(
                task.get("parent_task_id")
                or final_task_result.get("parent_task_id") or ""
            ))
            if isinstance(parent_meta, dict):
                parent_meta["last_progress_at"] = time.time()
        if worker_id in ctx.WORKERS and ctx.WORKERS[worker_id].busy_task_id == task_id:
            # A `reaping` slot is OWNED — by the reaper or by an in-flight
            # cancellation custody. Its owner confirms process death and then
            # respawns or releases; freeing the slot from here would hand a
            # mid-kill process back to assignment.
            if not getattr(ctx.WORKERS[worker_id], "reaping", False):
                ctx.WORKERS[worker_id].busy_task_id = None
    if task_id:
        try:
            clear_acceptance_fence_for_root(str(task_id))
        except Exception:
            log.warning(
                "Failed to clear terminal task acceptance fence for %s",
                task_id,
                exc_info=True,
            )
    ctx.persist_queue_snapshot(reason="task_done")
    try:
        ctx.bridge.push_log(task_done_event)
    except Exception:
        log.warning(
            "Failed to forward task_done to live logs (card may not finalize)",
            exc_info=True,
        )

    if bool(evt.get("_ephemeral")):
        # An ephemeral direct-chat decision turn shows its failure inline —
        # no duplicate provider-outage owner ping.
        return
    _maybe_notify_provider_death(ctx, task_id, task, final_task_result, task_done_event)
    try:
        results_dir = pathlib.Path(ctx.DRIVE_ROOT) / "task_results"
        results_dir.mkdir(parents=True, exist_ok=True)
        result_file = results_dir / f"{task_id}.json"
        if not result_file.exists():
            write_task_result(
                ctx.DRIVE_ROOT,
                str(task_id or ""),
                STATUS_FAILED,
                reason_code="missing_task_result",
                outcome_axes=infra_failed_axes(
                    "missing_task_result", review_trigger="supervisor_fallback"
                ),
                result="",
                **({
                    key: task_done_event[key]
                    for key in ("total_rounds", "prompt_tokens", "completion_tokens")
                    if key in task_done_event
                }),
                # C12: the accounting fields come from the cost SSOT, so a marker
                # added there (reserved/unresolved/ledger integrity) reaches this
                # fallback result too instead of being dropped by a stale list.
                **carry_cost_meta(task_done_event),
                ts=evt.get("ts", ""),
            )
    except Exception as exc:
        log.warning("Failed to store task result in events: %s", exc)
    if task_id:
        try:
            from supervisor.terminal_delivery import cleanup_settled_owner_mailbox

            cleanup_settled_owner_mailbox(ctx.DRIVE_ROOT, str(task_id), task)
        except Exception:
            log.warning("Failed to cleanup terminal owner mailbox for %s", task_id, exc_info=True)


def _resolve_lifecycle_fault(
    evt: Dict[str, Any], ctx: Any, evt_status: str, *, detail: str = "",
) -> None:
    """Give a refused ``task_done`` an OWNER, or the worker slot wedges.

    Refusing the publication is right — the incident published a cancel latch as
    a terminal — but a refusal alone leaves the task in RUNNING with its worker
    still marked busy and nothing scheduled to finish it. Two cases:

    - A durable cancel intent (or a legacy ``cancel_requested`` latch) exists:
      cancellation custody and the watchdog already own this task, so the row
      stays exactly where it is and they settle it honestly.
    - Nothing owns it: the event is a genuine lifecycle bug, so the task is
      TERMINALIZED as ``failed`` with a typed reason and the slot is released.
      A wedged worker costs strictly more than an honest infra failure.

    ``detail`` overrides the default event-status wording — the durable-result
    fault (AR2-3) refuses an event whose OWN status looks settled.
    """
    task_id = str(evt.get("task_id") or "").strip()
    if not task_id:
        return
    try:
        from ouroboros.cancel_intents import cancel_pending

        if cancel_pending(ctx.DRIVE_ROOT, task_id):
            log.info(
                "task_done lifecycle fault for %s left to cancellation custody (cancel pending)",
                task_id,
            )
            return
    except Exception:
        log.debug("lifecycle-fault cancel-pending check failed for %s", task_id, exc_info=True)
    detail = detail or (
        f"Worker published a non-settled task_done ({evt_status!r}) and no cancellation "
        "owns this task; the supervisor terminalized it so the slot is not wedged."
    )
    # Capture the RUNNING row BEFORE the dispatch below pops it: it carries the
    # routing facts (chat/lineage/type) the terminal frame needs.
    task_row: Dict[str, Any] = {}
    try:
        running = getattr(ctx, "RUNNING", None)
        meta = running.get(task_id) if isinstance(running, dict) else None
        if isinstance(meta, dict) and isinstance(meta.get("task"), dict):
            task_row = dict(meta["task"])
    except Exception:
        task_row = {}
    # GR4-3: the synthetic terminal fires the SAME assisted-update hooks the
    # normal task_done path reaches — an orphaned managed-update transaction or
    # a held assisted writer gate would otherwise survive a lifecycle-fault
    # terminal until an unrelated task released them.
    try:
        event_metadata = evt.get("metadata")
        task_metadata = (
            task_row.get("metadata")
            if isinstance(task_row.get("metadata"), dict)
            else event_metadata if isinstance(event_metadata, dict) else None
        )
        from supervisor.update_merge import (
            abort_orphaned_assisted_tx,
            release_assisted_writer_gate_after_task,
        )

        abort_orphaned_assisted_tx(str(task_id), task_metadata)
        release_assisted_writer_gate_after_task(task_metadata)
    except Exception:
        log.debug("assisted-merge orphan watchdog failed (lifecycle fault)", exc_info=True)
    stored: Dict[str, Any] = {}
    try:
        from ouroboros.task_results import STATUS_FAILED, write_task_result

        write_task_result(
            ctx.DRIVE_ROOT, task_id, STATUS_FAILED,
            reason_code="task_done_lifecycle_fault",
            result=detail,
            outcome_axes=infra_failed_axes(
                "task_done_lifecycle_fault", review_trigger="supervisor_terminal",
            ),
        )
        stored = load_task_result(ctx.DRIVE_ROOT, task_id) or {}
    except Exception:
        # GR3-6: durable persistence FAILED — retain lifecycle ownership. The
        # row stays in RUNNING and the slot stays busy: releasing them over a
        # non-settled durable truth would recreate the exact wedge this seam
        # closes (task invisible, nothing scheduled to finish it). The next
        # fault/watchdog pass retries.
        log.error(
            "Failed to terminalize lifecycle-fault task %s; retaining lifecycle "
            "ownership (no slot release)", task_id, exc_info=True,
        )
        return
    # GR3-6: the synthetic terminal goes through the NORMAL dispatch seam —
    # terminal UI frame, acceptance-fence clearing, campaign/project hooks,
    # RUNNING/slot bookkeeping, snapshot — instead of the old private partial
    # copy (RUNNING pop + slot clear only), which resolved nothing owner-visible.
    status = str(stored.get("status") or "failed")
    task_type = str(evt.get("task_type") or task_row.get("type") or "")
    task_done_event: Dict[str, Any] = {
        "ts": utc_now_iso(),
        "type": "task_done",
        "task_id": task_id,
        "task_type": task_type,
        "chat_id": int(
            _bound_project_chat_id(
                ctx, task_id, task_row.get("parent_task_id"), task_row.get("root_task_id")
            )
            or evt.get("chat_id") or task_row.get("chat_id") or stored.get("chat_id") or 0
        ),
        "status": status,
        "reason_code": str(stored.get("reason_code") or "task_done_lifecycle_fault"),
        "outcome_axes": normalize_outcome_axes(stored),
    }
    try:
        task_done_event.update(_authoritative_terminal_cost(
            task_id, task_row, stored, evt, pathlib.Path(ctx.DRIVE_ROOT),
        ))
    except Exception:
        log.debug("lifecycle-fault cost projection failed for %s", task_id, exc_info=True)
    try:
        append_jsonl(ctx.DRIVE_ROOT / "logs" / "events.jsonl", task_done_event)
    except Exception:
        log.warning("Failed to log lifecycle-fault task_done to events.jsonl", exc_info=True)
    if task_type == "evolution":
        _handle_evolution_task_done(
            ctx, evt=evt, task_id=task_id, task=task_row,
            task_done_event=task_done_event,
            outcome_axes=task_done_event.get("outcome_axes") or {},
            cost=task_done_event.get("cost_usd"),
            rounds=task_done_event.get("total_rounds"),
        )
    # GR4-3: the cooperative-checkpoint hooks fire for the synthetic terminal
    # exactly as the normal path fires them — a lifecycle-fault root would
    # otherwise never checkpoint its coop tree, and a faulted last subagent
    # would never trigger the tree-quiescence checkpoint.
    try:
        if task_row and str(task_row.get("delegation_role") or "") != "subagent":
            _checkpoint_coop_roots_on_root_done(ctx, task_row, task_id)
    except Exception:
        log.debug("coop root-done checkpoint failed (lifecycle fault)", exc_info=True)
    _finish_task_done_dispatch(
        evt, ctx,
        task_id=task_id, worker_id=evt.get("worker_id"),
        task=task_row, final_task_result=stored, task_done_event=task_done_event,
    )
    try:
        if task_row and str(task_row.get("delegation_role") or "") == "subagent":
            _maybe_checkpoint_coop_on_tree_quiescence(ctx, task_row, task_id)
    except Exception:
        log.debug("coop quiescence checkpoint failed (lifecycle fault)", exc_info=True)


def _task_done_durable_fault(evt: Dict[str, Any], ctx: Any, task_id: Any) -> bool:
    """AR2-3 / GR2-3 (§8-A1): validate ``task_done`` through the DURABLE result.

    UNCONDITIONAL for every non-ephemeral task_done: the durable post-copy-back
    result must be settled (or the formalized ``interrupted`` transient),
    regardless of what the event's own status field says. The original AR2-3
    check gated on a settled event CLAIM — and the PRIMARY producer
    (``agent_task_pipeline``) emits task_done with a blank status, so ordinary
    completions bypassed validation entirely; a blank-status event over a
    running/absent row sailed through to publication. A blank status is now
    validated exactly like a settled claim: the worker asserted "done" and the
    disk must agree. Refused + forensic row; the existing fault-resolution
    path decides slot fate. Two exemptions stand: ephemeral turns (their event
    IS their terminal outcome — no durable lifecycle) and an ``interrupted``
    event status (its owner is the snapshot restore/requeue path). Never
    raises.
    """
    try:
        if bool(evt.get("_ephemeral")) or not task_id:
            return False
        evt_status = str(evt.get("status") or "").strip().lower()
        from ouroboros.task_results import STATUS_INTERRUPTED
        from ouroboros.task_status import SETTLED_STATUSES

        if evt_status == STATUS_INTERRUPTED:
            return False  # formalized transient: the restore/requeue path owns the row
        if evt_status and evt_status not in SETTLED_STATUSES:
            return False  # non-settled claims were already refused at the gate
        try:
            durable_status = str(
                (load_task_result(ctx.DRIVE_ROOT, str(task_id)) or {}).get("status") or ""
            ).strip().lower()
        except Exception:
            # An unreadable row is not proof of a fault; fail open toward the
            # ordinary dispatch (its own missing-result fallback still runs).
            log.debug("task_done durable validation read failed for %s", task_id, exc_info=True)
            return False
        if durable_status in SETTLED_STATUSES or durable_status == STATUS_INTERRUPTED:
            return False
        log.error(
            "task_done for %s claims settled %r but the durable result is %r; "
            "refused (durable lifecycle fault)",
            task_id, evt_status or "(blank)", durable_status or "absent",
        )
        try:
            ctx.append_jsonl(
                ctx.DRIVE_ROOT / "logs" / "events.jsonl",
                {
                    "ts": utc_now_iso(),
                    "type": "task_done_invalid_status",
                    "task_id": str(task_id),
                    "status": evt_status,
                    "durable_status": durable_status,
                    "worker_id": evt.get("worker_id"),
                },
            )
        except Exception:
            log.debug("task_done_invalid_status record failed", exc_info=True)
        _resolve_lifecycle_fault(
            evt, ctx, evt_status,
            detail=(
                f"Worker published task_done claiming settled {evt_status or '(blank)'!r} "
                f"while the durable result is {durable_status or 'absent'!r} (not settled) "
                "and no cancellation owns this task; the supervisor terminalized it so the "
                "slot is not wedged."
            ),
        )
        return True
    except Exception:
        log.debug("task_done durable validation failed open for %s", task_id, exc_info=True)
        return False


def _handle_task_done(evt: Dict[str, Any], ctx: Any) -> None:
    # Phase A1.7: ``task_done`` asserts a SETTLED outcome. A non-settled status
    # (the incident's shape: the cancel latch published as a terminal) is a
    # durable LIFECYCLE FAULT — recorded loudly, RUNNING/worker state NOT
    # released (the row stays visible for custody/watchdog to settle honestly),
    # never a crash. Two deliberate exemptions: ephemeral direct-chat decision
    # turns (no durable task-result lifecycle — their event IS their terminal
    # outcome), and ``interrupted`` — the FORMALIZED transient the update/restart
    # teardown publishes for this generation (A1.11): its owner is the snapshot
    # restore/requeue path, and the effective-status orphan reconcile terminal-
    # izes a retry-less leftover, so it can never wedge the way the latch did.
    # The durable half of the same law (AR2-3) runs after the child copy-back:
    # a SETTLED event claim over a NON-settled durable row is refused too.
    _evt_status = str(evt.get("status") or "").strip().lower()
    if _evt_status and not bool(evt.get("_ephemeral")):
        from ouroboros.task_results import STATUS_INTERRUPTED as _INTERRUPTED
        from ouroboros.task_status import SETTLED_STATUSES as _SETTLED

        if _evt_status not in _SETTLED and _evt_status != _INTERRUPTED:
            log.error(
                "task_done with non-settled status %r for %s refused (lifecycle fault)",
                _evt_status, evt.get("task_id"),
            )
            try:
                ctx.append_jsonl(
                    ctx.DRIVE_ROOT / "logs" / "events.jsonl",
                    {
                        "ts": utc_now_iso(),
                        "type": "task_done_invalid_status",
                        "task_id": str(evt.get("task_id") or ""),
                        "status": _evt_status,
                        "worker_id": evt.get("worker_id"),
                    },
                )
            except Exception:
                log.debug("task_done_invalid_status record failed", exc_info=True)
            _resolve_lifecycle_fault(evt, ctx, _evt_status)
            return
    task_id = evt.get("task_id")
    wid = evt.get("worker_id")
    meta = ctx.RUNNING.get(str(task_id or ""), {}) if task_id else {}
    task = meta.get("task") if isinstance(meta, dict) and isinstance(meta.get("task"), dict) else {}
    event_metadata = evt.get("metadata")
    task_metadata = (
        task.get("metadata")
        if isinstance(task.get("metadata"), dict)
        else event_metadata if isinstance(event_metadata, dict) else None
    )
    if task_id:
        try:
            from supervisor.update_merge import (
                abort_orphaned_assisted_tx,
                release_assisted_writer_gate_after_task,
            )

            abort_orphaned_assisted_tx(str(task_id), task_metadata)
            release_assisted_writer_gate_after_task(task_metadata)
        except Exception:
            log.debug("assisted-merge orphan watchdog failed", exc_info=True)
    task_type = str(evt.get("task_type") or task.get("type") or "")

    final_task_result: Dict[str, Any] = {}
    if task_id:
        try:
            from ouroboros.headless import (
                copy_child_task_result,
                finalize_task_artifacts,
                task_is_readonly_subagent,
            )

            if task:
                copy_child_task_result(ctx.DRIVE_ROOT, task)
            # AR2-3 (§8-A1): task_done is validated through the DURABLE result,
            # not the event's own status claim. The read sits AFTER the child
            # copy-back (split-drive tasks settle on the child drive first) and
            # BEFORE artifact finalization, which would default-stamp a
            # fabricated ``completed`` row for a workspace task that never
            # wrote one — exactly the shape this refusal must catch.
            if _task_done_durable_fault(evt, ctx, task_id):
                return
            if task:
                if not task_is_readonly_subagent(task):
                    finalize_task_artifacts(ctx.DRIVE_ROOT, task)
                if str(task.get("delegation_role") or "") != "subagent":
                    _checkpoint_coop_roots_on_root_done(ctx, task, str(task_id or ""))
        except Exception as exc:
            try:
                from ouroboros.headless import ARTIFACT_STATUS_FAILED
                from ouroboros.outcomes import artifact_bundle_from_result

                existing = load_task_result(ctx.DRIVE_ROOT, str(task_id)) or {}
                # GR2-3b: annotate ONLY a row that exists. The old fallback
                # defaulted a MISSING row's status to "completed" — a copy-back
                # exception then minted a fabricated completion that the
                # monotonic guard defended and the durable validation below
                # would read back as settled. A task with no durable result
                # stays absent here and is judged by the fault seam instead.
                if existing and str(existing.get("status") or ""):
                    fields = {
                        "artifact_status": ARTIFACT_STATUS_FAILED,
                        "artifact_error": f"{type(exc).__name__}: {exc}",
                        "artifact_finalized_at": utc_now_iso(),
                    }
                    provisional = {**existing, **fields}
                    fields["artifact_bundle"] = artifact_bundle_from_result(provisional)
                    write_task_result(
                        ctx.DRIVE_ROOT,
                        str(task_id),
                        str(existing.get("status") or ""),
                        **fields,
                    )
            except Exception:
                pass
            log.warning("Failed to finalize headless artifacts for task %s", task_id, exc_info=True)
            # GR2-3b: an exception on the copy-back path must not SKIP the
            # durable validation — the incident shape is precisely a task_done
            # whose durable truth never landed. (When the exception came from
            # artifact finalization AFTER a passed validation, this re-check is
            # an idempotent read that passes again.)
            if _task_done_durable_fault(evt, ctx, task_id):
                return
        try:
            final_task_result = load_task_result(ctx.DRIVE_ROOT, str(task_id)) or {}
        except Exception:
            final_task_result = {}
        if not bool(evt.get("_ephemeral")):
            try:
                # §19.7.2 item 5: a hurry the worker never drained loses the
                # terminal race honestly — not_applied_before_terminal.
                from ouroboros.owner_hurry import reconcile_terminal

                reconcile_terminal(ctx.DRIVE_ROOT, str(task_id))
            except Exception:
                log.debug("owner_hurry terminal reconcile failed for %s", task_id, exc_info=True)

    outcome_axes = normalize_outcome_axes({**evt, **(final_task_result if isinstance(final_task_result, dict) else {})})
    reason_code = final_task_result.get("reason_code") or evt.get("reason_code")
    artifact_status = final_task_result.get("artifact_status") or evt.get("artifact_status")
    terminal_cost = _authoritative_terminal_cost(
        str(task_id or ""), task,
        final_task_result if isinstance(final_task_result, dict) else {}, evt,
        pathlib.Path(ctx.DRIVE_ROOT),
    )
    eff_cost = terminal_cost.get("cost_usd")
    eff_rounds = terminal_cost.get("total_rounds")
    task_done_event = {
        "ts": evt.get("ts", utc_now_iso()),
        "type": "task_done",
        "task_id": task_id,
        "task_type": task_type,
        "chat_id": int(
            _bound_project_chat_id(
                ctx, task_id,
                (final_task_result.get("parent_task_id") if isinstance(final_task_result, dict) else "") or evt.get("parent_task_id"),
                (final_task_result.get("root_task_id") if isinstance(final_task_result, dict) else "") or evt.get("root_task_id"),
            )
            or evt.get("chat_id")
            or (final_task_result.get("chat_id") if isinstance(final_task_result, dict) else 0)
            or 0
        ),
        "status": str(final_task_result.get("status") or evt.get("status") or ""),
        "outcome_axes": outcome_axes,
        "reason_code": reason_code,
        "artifact_status": artifact_status,
        **terminal_cost,
    }
    if bool(evt.get("ephemeral_decision") or evt.get("_ephemeral")):
        task_done_event["ephemeral_decision"] = True
    if str(evt.get("typed_routing_action") or "").strip():
        task_done_event["typed_routing_action"] = str(evt.get("typed_routing_action") or "").strip()
    artifact_bundle = final_task_result.get("artifact_bundle") if isinstance(final_task_result, dict) else None
    if not isinstance(artifact_bundle, dict):
        artifact_bundle = evt.get("artifact_bundle")
    if isinstance(artifact_bundle, dict):
        task_done_event["artifact_bundle"] = artifact_bundle
    review_status = final_task_result.get("review_status") if isinstance(final_task_result, dict) else None
    if not isinstance(review_status, dict):
        review_status = evt.get("review_status")
    if isinstance(review_status, dict):
        task_done_event["review_status"] = review_status
    if review_projection := _task_done_review_projection(final_task_result, evt):
        task_done_event["review_projection"] = review_projection
    try:
        append_jsonl(ctx.DRIVE_ROOT / "logs" / "events.jsonl", task_done_event)
    except Exception:
        log.warning("Failed to log task_done to events.jsonl", exc_info=True)

    if task_type == "evolution":
        _handle_evolution_task_done(
            ctx,
            evt=evt,
            task_id=task_id,
            task=task,
            task_done_event=task_done_event,
            outcome_axes=outcome_axes,
            cost=eff_cost,
            rounds=eff_rounds,
        )

    _finish_task_done_dispatch(
        evt,
        ctx,
        task_id=task_id,
        worker_id=wid,
        task=task,
        final_task_result=final_task_result,
        task_done_event=task_done_event,
    )

    # v6.91 tree-quiescence coop checkpoint: MUST run after the dispatch
    # bookkeeping above removed this terminal child from RUNNING, or the
    # finishing child still counts live and "zero live members" is never true.
    if task_id and str(task.get("delegation_role") or "") == "subagent":
        _maybe_checkpoint_coop_on_tree_quiescence(ctx, task, str(task_id))


# Owner steering delivery (cancel-pending refusal + the steer_task handler)
# lives in supervisor/steering.py (module-size boundary for this pinned
# surface); imported back so the dispatch table and callers keep one name.
from supervisor.steering import (  # noqa: E402 -- intentional re-import
    _handle_steer_task,
    _refuse_steering_while_cancelling,  # noqa: F401 -- re-exported for callers/tests
)


def _handle_cancel_task(evt: Dict[str, Any], ctx: Any) -> None:
    """Drive one agent-requested cancel through custody — TYPED outcome end to end.

    Phase A1.12: the old boolean facade collapsed ``already_settled`` into the
    same "✅ cancel" as a real teardown — a lie when the child had finished on its
    own and kept its completed result. Each typed outcome now gets its honest
    acknowledgement, and ✅ is sent only after a CONFIRMED teardown + durable
    settled write."""
    task_id = str(evt.get("task_id") or "").strip()
    requested_task_id = str(evt.get("requested_task_id") or "").strip()
    display_task_id = requested_task_id or task_id
    st = ctx.load_state()
    owner_chat_id = st.get("owner_chat_id")
    from supervisor.queue import (
        CANCEL_ALREADY_SETTLED,
        CANCEL_CANCELLED,
        CANCEL_NOT_FOUND,
        drive_cancel_intent_scope,
    )

    outcome = drive_cancel_intent_scope(task_id) if task_id else CANCEL_NOT_FOUND
    if not owner_chat_id:
        return
    if outcome == CANCEL_CANCELLED:
        ctx.send_with_budget(
            int(owner_chat_id),
            f"✅ cancel {display_task_id or '?'}: teardown confirmed, outcome settled (event)",
        )
    elif outcome == CANCEL_ALREADY_SETTLED:
        settled_status = str(
            (load_task_result(ctx.DRIVE_ROOT, task_id) or {}).get("status") or "settled"
        )
        ctx.send_with_budget(
            int(owner_chat_id),
            f"ℹ️ cancel {display_task_id or '?'}: the task had already finished "
            f"({settled_status}) — its result is preserved, nothing was torn down (event)",
        )
    elif outcome == CANCEL_NOT_FOUND:
        ctx.send_with_budget(
            int(owner_chat_id),
            f"⚠️ cancel {display_task_id or '?'}: no such live task (event)",
        )
    else:
        incident_meta = {
            "task_incident": "cancellation_fault",
            "toast_once": f"{display_task_id or 'unknown'}:cancellation_fault",
        }
        if task_id and display_task_id != task_id:
            incident_meta["cancel_physical_task_id"] = task_id
        ctx.send_with_budget(
            int(owner_chat_id),
            f"❌ cancel {display_task_id or '?'} did not settle — the task is still live; "
            "the durable cancel intent stays open and the supervisor watchdog retries (event)",
            is_progress=True,
            task_id=display_task_id,
            progress_meta=incident_meta,
        )


def _handle_main_llm_call_state(evt: Dict[str, Any], ctx: Any) -> None:
    task_id = str(evt.get("task_id") or "")
    phase = str(evt.get("phase") or "").strip().lower()
    llm_call_id = str(evt.get("llm_call_id") or "")
    execution_id = str(evt.get("execution_id") or "")
    round_id = str(evt.get("round_id") or "")
    if (
        not task_id
        or phase not in {"started", "finished", "failed"}
        or not llm_call_id
        or not execution_id
        or not round_id
    ):
        return
    try:
        task_attempt = int(evt["task_attempt"])
        call_attempt = int(evt["call_attempt"])
    except (KeyError, TypeError, ValueError):
        return
    if task_attempt < 1 or call_attempt < 1:
        return
    running = getattr(ctx, "RUNNING", None)
    meta = running.get(task_id) if isinstance(running, dict) else None
    if not isinstance(meta, dict):
        return
    expected_attempt = meta.get("attempt")
    if expected_attempt is None:
        task = meta.get("task") if isinstance(meta.get("task"), dict) else {}
        expected_attempt = task.get("_attempt")
    try:
        if int(expected_attempt) != task_attempt:
            return
    except (TypeError, ValueError):
        return
    identity = {
        "task_attempt": task_attempt,
        "llm_call_id": llm_call_id,
        "execution_id": execution_id,
        "round_id": round_id,
        "call_attempt": call_attempt,
    }
    if phase == "started":
        meta["active_llm_call"] = {**identity, "started_at": time.time()}
        return
    active = meta.get("active_llm_call")
    if not isinstance(active, dict) or any(active.get(key) != value for key, value in identity.items()):
        return
    meta.pop("active_llm_call", None)


# v7next F1 (D08): moved spans live in their owner leaves; re-exported here
# so this facade stays the single import surface for callers and tests.
from supervisor.events_budget import (  # noqa: E402, F401 -- intentional public re-exports
    _handle_budget_pause,
    _handle_budget_root_fence,
    _handle_llm_usage,
    _handle_review_wave_budget_insufficient,
    _set_root_budget_pause_locked,
)
from supervisor.events_chat_delivery import (  # noqa: E402, F401 -- intentional public re-exports
    HOST_NARRATION,
    _DELIVERED_MESSAGE_IDS,
    _handle_send_document,
    _handle_send_message,
    _handle_send_photo,
    _handle_send_video,
    _handle_typing_start,
    _register_delivered,
)
from supervisor.events_coop_checkpoint import (  # noqa: E402, F401 -- intentional public re-exports
    _COOP_CHECKPOINT_DROPPED,
    _COOP_CHECKPOINT_INFLIGHT,
    _COOP_CHECKPOINT_LOCK,
    _checkpoint_coop_roots_on_root_done,
    _maybe_checkpoint_coop_on_tree_quiescence,
    _spawn_coop_checkpoint,
)
from supervisor.events_project_routing import (  # noqa: E402, F401 -- intentional public re-exports
    _emit_routing_receipt,
    _handle_ensure_project_scope,
    _handle_project_digest,
    _handle_promote_chat_to_task,
    _handle_routing_manual_target,
    _persist_promote_rejection,
    _prepare_promote_source_off_loop,
    _publish_routing_ack,
    _rollback_promoted_pending,
)
from supervisor.events_runtime_controls import (  # noqa: E402, F401 -- intentional public re-exports
    _handle_deep_self_review_request,
    _handle_owner_message_injected,
    _handle_promote_to_stable,
    _handle_toggle_consciousness,
    _handle_toggle_evolution,
)
from supervisor.events_schedule_task import (  # noqa: E402, F401 -- intentional public re-exports
    VALID_SUBAGENT_MEMORY_MODES,
    _PARENT_CONTEXT_END,
    _PARENT_CONTEXT_MARKER,
    _cleanup_rejected_worktree,
    _extract_task_description_and_context,
    _find_duplicate_task,
    _handle_schedule_task,
    _format_task_for_dedup,
    _reject_schedule_task,
)
from supervisor.events_subagent_admission import (  # noqa: E402, F401 -- intentional public re-exports
    _GIT_UNBORN_HEAD,
    _active_subagent_count,
    _compose_subagent_text,
    _depth_reservation_admits,
    _external_workspace_head,
    _is_active_subagent_task,
    _iter_tree_subagent_tasks,
    _record_delegation_constraint,
    _resolve_subagent_constraint,
    _send_subagent_rejection,
    _subagent_cap_blocks,
    _subagent_rejection_meta,
    _subagent_scheduled_meta,
    _task_own_id,
    _validate_external_workspace,
)
from supervisor.events_worker_reports import (  # noqa: E402, F401 -- intentional public re-exports
    _handle_acceptance_fence,
    _handle_external_wait_lease,
    _handle_log_event,
    _handle_skill_lifecycle,
    _handle_task_dispatch_resolved,
    _handle_task_heartbeat,
    _handle_task_metrics,
)

EVENT_HANDLERS = {
    "llm_usage": _handle_llm_usage,
    "external_wait_lease": _handle_external_wait_lease,
    **_CEH,
    "main_llm_call_state": _handle_main_llm_call_state,
    "budget_pause": _handle_budget_pause,
    "budget_root_fence": _handle_budget_root_fence,
    "task_heartbeat": _handle_task_heartbeat,
    "task_dispatch_resolved": _handle_task_dispatch_resolved,
    "typing_start": _handle_typing_start,
    "send_message": _handle_send_message,
    "task_done": _handle_task_done,
    "task_metrics": _handle_task_metrics,
    "deep_self_review_request": _handle_deep_self_review_request,
    "promote_to_stable": _handle_promote_to_stable,
    "schedule_task": _handle_schedule_task,
    "schedule_subagent": _handle_schedule_task,
    "promote_chat_to_task": _handle_promote_chat_to_task,
    "ensure_project_scope": _handle_ensure_project_scope,
    "routing_manual_target": _handle_routing_manual_target,
    "steer_task": _handle_steer_task,
    "project_digest": _handle_project_digest,
    "cancel_task": _handle_cancel_task,
    "send_photo": _handle_send_photo,
    "send_video": _handle_send_video,
    "send_document": _handle_send_document,
    "toggle_evolution": _handle_toggle_evolution,
    "toggle_consciousness": _handle_toggle_consciousness,
    "owner_message_injected": _handle_owner_message_injected,
    "log_event": _handle_log_event,
    "review_wave_budget_insufficient": _handle_review_wave_budget_insufficient,
    "skill_exec_finished": _handle_skill_lifecycle,
    "skill_exec_failed": _handle_skill_lifecycle,
    "acceptance_fence": _handle_acceptance_fence,
}


def dispatch_event(evt: Dict[str, Any], ctx: Any) -> None:
    """Dispatch a single worker event to its handler."""
    if not isinstance(evt, dict):
        ctx.append_jsonl(
            ctx.DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": utc_now_iso(),
                "type": "invalid_worker_event",
                "error": "event is not dict",
                "event_repr": repr(evt)[:1000],
            },
        )
        return

    event_type = str(evt.get("type") or "").strip()
    if not event_type:
        ctx.append_jsonl(
            ctx.DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": utc_now_iso(),
                "type": "invalid_worker_event",
                "error": "missing event.type",
                "event_repr": repr(evt)[:1000],
            },
        )
        return

    handler = EVENT_HANDLERS.get(event_type)
    if handler is None:
        log.warning("No handler for worker event type %r — event dropped", event_type)
        ctx.append_jsonl(
            ctx.DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": utc_now_iso(),
                "type": "unknown_worker_event",
                "event_type": event_type,
                "event_repr": repr(evt)[:1000],
            },
        )
        return

    try:
        handler(evt, ctx)
    except Exception as e:
        # Surface the failure with a full traceback. Previously this only wrote a
        # repr(e) to supervisor.jsonl, so a crashing handler (e.g. an ImportError
        # in a task_done/heartbeat handler) was invisible and left the UI stuck.
        log.warning("Worker event handler %r failed: %s", event_type, e, exc_info=True)
        ctx.append_jsonl(
            ctx.DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": utc_now_iso(),
                "type": "worker_event_handler_error",
                "event_type": event_type,
                "error": repr(e),
            },
        )
