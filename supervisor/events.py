"""Dispatch worker EVENT_Q messages to supervisor handlers."""

from __future__ import annotations

import logging
import os  # noqa: F401
import pathlib  # noqa: F401
import subprocess  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
import uuid
from collections import deque  # noqa: F401
from typing import Any, Dict, Optional  # noqa: F401

from ouroboros.utils import append_jsonl, atomic_write_json, truncate_for_log, utc_now_iso  # noqa: F401
from ouroboros.config import (
    MAX_ACTIVE_SUBAGENTS_HARD_CAP,
    get_max_active_subagents_per_root,
    get_max_subagent_depth,
)
from ouroboros.tool_capabilities import ACTING_SUBAGENT_MODE, LOCAL_READONLY_SUBAGENT_MODE  # noqa: F401
from ouroboros.contracts.task_constraint import VALID_WRITE_SURFACES  # noqa: F401
from ouroboros.task_results import (
    STATUS_CANCELLED,  # noqa: F401
    STATUS_COMPLETED,  # noqa: F401
    STATUS_FAILED,  # noqa: F401
    STATUS_INTERRUPTED,  # noqa: F401
    STATUS_REJECTED_DUPLICATE,
    STATUS_SCHEDULED,
    load_task_result,  # noqa: F401
    write_task_result,
)
from ouroboros.cost_projection import carry_cost_meta, with_cost_aliases  # noqa: F401
from ouroboros.outcomes import infra_failed_axes, normalize_outcome_axes  # noqa: F401
from ouroboros.subagents import intended_lane as intended_subagent_lane
from ouroboros.contracts.task_contract import build_task_contract, normalize_allowed_resources
# The declared disposition of every event kind the runtime can produce. Data only:
# the dispatch table below stays the single execution authority, and the taxonomy
# answers the one question the table cannot — what a MISS means.
from supervisor.event_taxonomy import disposition_for

# Handler families owned by their own modules (module-size boundary). Each
# family is re-imported here so this module keeps ONE public surface for the
# dispatch table, its callers and its tests, and so the historical
# ``supervisor.events`` names keep resolving. The dependency is one-way: no
# owner below imports this module.
from supervisor.events_chat_delivery import (  # noqa: F401 -- supervisor/events.py facade re-exports
    HOST_NARRATION,
    _DELIVERED_MESSAGE_IDS,
    _bound_project_chat_id,
    _handle_send_document,
    _handle_send_message,
    _handle_send_photo,
    _handle_send_video,
    _handle_typing_start,
    _register_delivered,
)
from supervisor.events_subagent_admission import (  # noqa: F401 -- supervisor/events.py facade re-exports
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
from supervisor.events_schedule_task import (  # noqa: F401 -- supervisor/events.py facade re-exports
    VALID_SUBAGENT_MEMORY_MODES,
    _PARENT_CONTEXT_END,
    _PARENT_CONTEXT_MARKER,
    _build_scheduled_task_payload,
    _cleanup_rejected_worktree,
    _extract_task_description_and_context,
    _find_duplicate_task,
    _format_task_for_dedup,
    _reject_if_no_chat_target,
    _reject_schedule_task,
)
from supervisor.events_project_routing import (  # noqa: F401 -- supervisor/events.py facade re-exports
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
from supervisor.events_coop_checkpoint import (  # noqa: F401 -- supervisor/events.py facade re-exports
    _COOP_CHECKPOINT_DROPPED,
    _COOP_CHECKPOINT_INFLIGHT,
    _COOP_CHECKPOINT_LOCK,
    _checkpoint_coop_roots_on_root_done,
    _maybe_checkpoint_coop_on_tree_quiescence,
    _spawn_coop_checkpoint,
)
from supervisor.events_evolution_done import (  # noqa: F401 -- supervisor/events.py facade re-exports
    _handle_evolution_task_done,
)
# The owner-stop backstop is one honesty rule with ``stop_evolution_tasks`` and lives
# beside it; re-exported here because callers and tests reach it through this module.
from supervisor.queue_transitions import (  # noqa: F401 -- supervisor/events.py facade re-export
    _close_campaign_after_owner_stop,
)
from supervisor.events_task_done import (  # noqa: F401 -- supervisor/events.py facade re-exports
    _PROVIDER_DEATH_NOTIFIED,
    _authoritative_terminal_cost,
    _finish_task_done_dispatch,
    _handle_task_done,
    _maybe_notify_provider_death,
    _resolve_lifecycle_fault,
    _task_done_durable_fault,
    _task_done_review_projection,
)
from supervisor.events_budget import (  # noqa: F401 -- supervisor/events.py facade re-exports
    _handle_budget_pause,
    _handle_budget_root_fence,
    _handle_llm_usage,
    _handle_review_wave_budget_insufficient,
    _set_root_budget_pause_locked,
)
from supervisor.events_worker_reports import (  # noqa: F401 -- supervisor/events.py facade re-exports
    _handle_acceptance_fence,
    _handle_external_wait_lease,
    _handle_log_event,
    _handle_skill_lifecycle,
    _handle_task_dispatch_resolved,
    _handle_task_heartbeat,
    _handle_task_metrics,
)
from supervisor.events_runtime_controls import (  # noqa: F401 -- supervisor/events.py facade re-exports
    _handle_cancel_task,
    _handle_deep_self_review_request,
    _handle_owner_message_injected,
    _handle_promote_to_stable,
    _handle_toggle_consciousness,
    _handle_toggle_evolution,
)

log = logging.getLogger(__name__)


# Owner steering delivery (cancel-pending refusal + the steer_task handler)
# lives in supervisor/steering.py (module-size boundary for this pinned
# surface); imported back so the dispatch table and callers keep one name.
from supervisor.steering import (  # noqa: E402 -- intentional re-import
    _handle_steer_task,
    _refuse_steering_while_cancelling,  # noqa: F401 -- re-exported for callers/tests
)


def _handle_schedule_task(evt: Dict[str, Any], ctx: Any) -> None:
    st = ctx.load_state()
    owner_chat_id = st.get("owner_chat_id")
    try:
        event_chat_id = int(evt.get("chat_id") or 0)
    except (TypeError, ValueError):
        event_chat_id = 0
    try:
        owner_chat_int = int(owner_chat_id or 0)
    except (TypeError, ValueError):
        owner_chat_int = 0
    chat_id = event_chat_id or owner_chat_int
    tid = str(evt.get("task_id") or uuid.uuid4().hex[:8])
    desc = str(evt.get("objective") or evt.get("description") or "").strip()
    expected_output = str(evt.get("expected_output") or "").strip()
    constraints = str(evt.get("constraints") or "").strip()
    role = str(evt.get("role") or "researcher").strip() or "researcher"
    task_context = str(evt.get("context") or "").strip()
    depth = int(evt.get("depth", 0))
    parent_id = evt.get("parent_task_id")
    root_task_id = str(evt.get("root_task_id") or parent_id or tid)
    session_id = str(evt.get("session_id") or "")
    actor_id = str(evt.get("actor_id") or "ouroboros")
    delegation_role = str(evt.get("delegation_role") or "subagent")
    memory_mode = str(evt.get("memory_mode") or "").strip()
    drive_root = str(evt.get("drive_root") or "").strip()
    child_drive_root = str(evt.get("child_drive_root") or drive_root).strip()
    budget_drive_root = str(evt.get("budget_drive_root") or "").strip()
    # INTENT ONLY (see `_build_scheduled_task_payload`): the supervisor forwards what
    # the parent ASKED for. What the child gets is resolved once, at dispatch.
    requested_model_lane = str(evt.get("requested_model_lane") or evt.get("model_lane") or "auto").strip() or "auto"
    parent_model_lane = str(evt.get("parent_model_lane") or "").strip()
    requested_executor = str(evt.get("requested_executor") or "").strip().lower() or "auto"
    task_group_id = str(evt.get("task_group_id") or "").strip()
    task_group = evt.get("task_group") if isinstance(evt.get("task_group"), dict) else {}
    subagent_envelope = evt.get("subagent_envelope") if isinstance(evt.get("subagent_envelope"), dict) else {}
    task_constraint = evt.get("task_constraint") if isinstance(evt.get("task_constraint"), dict) else None
    required_capabilities = [
        str(item or "").strip().lower()
        for item in (evt.get("required_capabilities") if isinstance(evt.get("required_capabilities"), list) else [])
        if str(item or "").strip()
    ]
    workspace_root = str(evt.get("workspace_root") or "").strip()
    workspace_mode = str(evt.get("workspace_mode") or "").strip()
    project_id = str(evt.get("project_id") or "").strip()
    acting_reject_detail = ""
    if delegation_role == "subagent":
        task_constraint, workspace_root, workspace_mode, acting_reject_detail = _resolve_subagent_constraint(
            ctx, tid=tid, requested_constraint=task_constraint, workspace_root=workspace_root,
            workspace_mode=workspace_mode, base_sha=str(evt.get("base_sha") or ""), parent_task_id=str(parent_id or ""))
    allowed_resources = normalize_allowed_resources(evt.get("allowed_resources") or {})
    task_contract = evt.get("task_contract") if isinstance(evt.get("task_contract"), dict) else build_task_contract({
        "id": tid,
        "type": "task",
        "description": desc,
        "objective": desc,
        "expected_output": expected_output,
        "constraints": constraints,
        "workspace_root": workspace_root,
        "workspace_mode": workspace_mode,
        "allowed_resources": allowed_resources,
        "parent_task_id": parent_id,
        "root_task_id": root_task_id,
        "session_id": session_id,
        "delegation_role": delegation_role,
    })
    result_fields = {
        "parent_task_id": parent_id,
        "root_task_id": root_task_id,
        "session_id": session_id,
        "actor_id": actor_id,
        "delegation_role": delegation_role,
        "role": role,
        "description": desc,
        "objective": desc,
        "expected_output": expected_output,
        "constraints": constraints,
        "context": task_context,
        "workspace_root": workspace_root,
        "workspace_mode": workspace_mode, "project_id": project_id,
        "allowed_resources": allowed_resources,
        "task_contract": task_contract,
        "chat_id": chat_id or None,
        "memory_mode": memory_mode,
        "drive_root": drive_root,
        "child_drive_root": child_drive_root,
        "budget_drive_root": budget_drive_root,
        "task_constraint": task_constraint,
        "required_capabilities": required_capabilities,
        "model_lane": requested_model_lane,
        "requested_model_lane": requested_model_lane,
        "parent_model_lane": parent_model_lane,
        "requested_executor": requested_executor,
        "task_group_id": task_group_id,
        "task_group": task_group,
        "subagent_envelope": subagent_envelope,
    }
    if delegation_role == "subagent" and (not str(evt.get("objective") or "").strip() or not expected_output):
        detail = "Subagent rejected: schedule_subagent requires objective and expected_output."
        log.warning("Rejected subagent due to strict schedule_subagent schema violation: task_id=%s", tid)
        _reject_schedule_task(
            ctx, tid=tid, chat_id=chat_id, delegation_role=delegation_role,
            parent_id=parent_id, root_task_id=root_task_id, role=role,
            result_fields={**result_fields, "objective": str(evt.get("objective") or "").strip()},
            detail=detail,
        )
        return

    if delegation_role == "subagent" and acting_reject_detail:
        log.warning("Acting subagent request rejected: task_id=%s detail=%s", tid, acting_reject_detail[:160])
        _record_delegation_constraint(
            root_task_id,
            task_id=tid,
            role=role,
            directive="block_surface",
            scope={"surface": str((task_constraint or {}).get("surface") or evt.get("write_surface") or "")},
            rationale=acting_reject_detail,
            advisory=True,
        )
        _reject_schedule_task(
            ctx, tid=tid, chat_id=chat_id, delegation_role=delegation_role,
            parent_id=parent_id, root_task_id=root_task_id, role=role,
            result_fields=result_fields, detail=acting_reject_detail,
        )
        return

    if delegation_role == "subagent" and (memory_mode not in VALID_SUBAGENT_MEMORY_MODES or not child_drive_root):
        detail = (
            "Subagent rejected: internal schedule_subagent events must use memory_mode=forked or empty "
            "and include a child_drive_root."
        )
        log.warning("Rejected subagent due to invalid child-drive contract: task_id=%s memory_mode=%s child_drive_root=%s", tid, memory_mode, child_drive_root)
        _reject_schedule_task(
            ctx, tid=tid, chat_id=chat_id, delegation_role=delegation_role,
            parent_id=parent_id, root_task_id=root_task_id, role=role,
            result_fields=result_fields, detail=detail,
        )
        return

    # The lane an applicable, non-advisory require_lane constraint verified this
    # admission against (F9): stamped onto the child record so the dispatch-time
    # policy default cannot override the lane the gate just enforced.
    required_model_lane = ""
    if delegation_role == "subagent":
        try:
            from ouroboros.tool_access import subagent_profile_satisfies
            from ouroboros.tools.control_delegation import effective_delegation_budget
            from ouroboros.task_tree_ledger import open_delegation_constraints

            selected_profile = (
                "acting_subagent"
                if isinstance(task_constraint, dict)
                and task_constraint.get("mode") == ACTING_SUBAGENT_MODE
                and task_constraint.get("surface")
                else "local_readonly_subagent"
            )
            _ok, missing_caps = subagent_profile_satisfies(selected_profile, required_capabilities)
            constraints_for_tree = open_delegation_constraints(root_task_id)
            decision = effective_delegation_budget(
                task_contract.get("delegation_budget") if isinstance(task_contract, dict) else {},
                missing_capabilities=missing_caps,
                unresolved_constraints=constraints_for_tree,
                write_surface=str((task_constraint or {}).get("surface") or "") if isinstance(task_constraint, dict) else "",
                role=role,
                requested_lane=requested_model_lane,
                intended_lane=intended_subagent_lane(requested_model_lane, parent_model_lane),
                active_child_count=_active_subagent_count(root_task_id, getattr(ctx, "PENDING", []), getattr(ctx, "RUNNING", {})),
            )
            if not decision.ok:
                detail = f"Subagent rejected: {decision.reason_code}: {decision.detail}"
                _reject_schedule_task(
                    ctx, tid=tid, chat_id=chat_id, delegation_role=delegation_role,
                    parent_id=parent_id, root_task_id=root_task_id, role=role,
                    result_fields=result_fields, detail=detail,
                )
                return
            if isinstance(task_contract, dict) and decision.budget:
                task_contract = {**task_contract, "delegation_budget": decision.budget}
                result_fields["task_contract"] = task_contract
            required_model_lane = str(getattr(decision, "required_lane", "") or "")
        except Exception:
            log.debug("Delegation reconciliation failed open for %s", tid, exc_info=True)

    max_depth = get_max_subagent_depth()
    if depth > max_depth:
        detail = f"Subagent rejected: subtask depth limit ({max_depth}) exceeded."
        log.warning("Rejected task due to depth limit: depth=%d, desc=%s", depth, desc[:100])
        _reject_schedule_task(
            ctx, tid=tid, chat_id=chat_id, delegation_role=delegation_role,
            parent_id=parent_id, root_task_id=root_task_id, role=role,
            result_fields=result_fields,
            detail=detail,
            fallback_message=f"⚠️ Task rejected: subtask depth limit ({max_depth}) exceeded",
        )
        return

    if _reject_if_no_chat_target(
        ctx, desc=desc, chat_id=chat_id, delegation_role=delegation_role, tid=tid,
        role=role, parent_id=parent_id, root_task_id=root_task_id, result_fields=result_fields,
    ):
        return

    # Fail fast when the worker pool is disabled (e.g. after a crash storm put
    # the supervisor in direct-chat mode). Without this, the task is written as
    # 'scheduled' and enqueued but nothing can ever run it — a permanent "ghost"
    # the parent keeps polling. Give the parent a clear terminal signal instead
    # so it can do the work inline.
    if desc and not (getattr(ctx, "WORKERS", {}) or {}):
        _reject_schedule_task(
            ctx, tid=tid, chat_id=chat_id, delegation_role=delegation_role,
            parent_id=parent_id, root_task_id=root_task_id, role=role,
            result_fields=result_fields,
            detail=(
                "Subagent not scheduled: the worker pool is currently unavailable "
                "(workers_unavailable), likely disabled after repeated worker crashes "
                "(direct-chat mode). It was NOT left scheduled — do the work inline "
                "yourself, or retry after /restart."
            ),
            reason_code="workers_unavailable",
            fallback_message=f"⚠️ Task {tid} not scheduled: worker pool unavailable.",
        )
        return

    if desc:
        # Bible P5: duplicate judgment stays LLM-first, not hardcoded.
        from supervisor.queue import PENDING as QUEUE_PENDING, RUNNING as QUEUE_RUNNING
        pending_ref = getattr(ctx, "PENDING", QUEUE_PENDING)
        running_ref = getattr(ctx, "RUNNING", QUEUE_RUNNING)
        max_active = get_max_active_subagents_per_root()
        queued_behind_active_cap = False
        if delegation_role == "subagent" and _subagent_cap_blocks(root_task_id, parent_id, pending_ref, running_ref, max_active):
            active_count = _active_subagent_count(root_task_id, pending_ref, running_ref)
            if active_count >= MAX_ACTIVE_SUBAGENTS_HARD_CAP:
                log.warning("Rejected subagent due to hard active child cap: root=%s desc=%s", root_task_id, desc[:100])
                detail = (
                    "Subagent rejected: hard active child limit "
                    f"({MAX_ACTIVE_SUBAGENTS_HARD_CAP}) exceeded for root_task_id={root_task_id}."
                )
                _reject_schedule_task(
                    ctx, tid=tid, chat_id=chat_id, delegation_role=delegation_role,
                    parent_id=parent_id, root_task_id=root_task_id, role=role,
                    result_fields=result_fields, detail=detail,
                )
                return
            queued_behind_active_cap = True
            _record_delegation_constraint(
                root_task_id,
                task_id=tid,
                role=role,
                directive="cap_children",
                scope={"max_children": max_active},
                rationale=f"Queued behind active subagent cap {max_active}; wait for a slot before additional fan-out.",
                advisory=True,
            )
        dup_id = _find_duplicate_task(
            desc,
            task_context,
            pending_ref,
            running_ref,
            expected_output=expected_output,
            constraints=constraints,
            role=role,
            dedupe_identity={
                "delegation_role": delegation_role,
                "task_id": tid,
                "parent_task_id": str(parent_id or ""),
                "root_task_id": root_task_id,
                "budget_drive_root": budget_drive_root or str(ctx.DRIVE_ROOT),
            },
        )
        if dup_id:
            log.info("Rejected duplicate task: new='%s' duplicates='%s'", desc[:100], dup_id)
            detail = f"Task was rejected as semantically similar to already active task {dup_id}."
            _reject_schedule_task(
                ctx, tid=tid, chat_id=chat_id, delegation_role=delegation_role,
                parent_id=parent_id, root_task_id=root_task_id, role=role,
                result_fields=result_fields,
                detail=detail,
                status=STATUS_REJECTED_DUPLICATE,
                extra_fields={"duplicate_of": dup_id},
                fallback_message=f"⚠️ Task rejected: semantically similar to already active task {dup_id}",
            )
            return

        text = _compose_subagent_text(
            desc,
            role=role,
            expected_output=expected_output,
            constraints=constraints,
            context=task_context,
            task_constraint=task_constraint,
            delegation_budget=task_contract.get("delegation_budget") if isinstance(task_contract, dict) else None,
        ) if delegation_role == "subagent" else desc
        task = _build_scheduled_task_payload({
            "tid": tid,
            "chat_id": chat_id,
            "text": text,
            "desc": desc,
            "expected_output": expected_output,
            "constraints": constraints,
            "role": role,
            "task_context": task_context,
            "depth": depth,
            "root_task_id": root_task_id,
            "session_id": session_id,
            "actor_id": actor_id,
            "delegation_role": delegation_role,
            "memory_mode": memory_mode,
            "drive_root": drive_root,
            "child_drive_root": child_drive_root,
            "budget_drive_root": budget_drive_root,
            "task_constraint": task_constraint,
            "workspace_root": workspace_root,
            "workspace_mode": workspace_mode,
            "project_id": project_id,
            "allowed_resources": allowed_resources,
            "task_contract": task_contract,
            "required_capabilities": required_capabilities,
            "model_lane": requested_model_lane,
            "requested_model_lane": requested_model_lane,
            "parent_model_lane": parent_model_lane,
            "required_model_lane": required_model_lane,
            "requested_executor": requested_executor,
            "task_group_id": task_group_id,
            "task_group": task_group,
            "subagent_envelope": subagent_envelope,
            "parent_id": parent_id,
        })
        admitted = ctx.enqueue_task(task)
        if isinstance(admitted, dict) and admitted.get("_admission_blocked"):
            blocked_reason = str(admitted.get("_admission_blocked") or "admission_fence")
            if blocked_reason.startswith("project_routing_fence"):
                fence_status = str(admitted.get("_project_lifecycle") or "unavailable")
                detail = (
                    "Subagent not scheduled: the target Project has closed its routing/admission "
                    f"fence ({fence_status}) and cannot accept new work."
                )
                reason_code = blocked_reason
                extra = {
                    "project_id": str(admitted.get("_project_id") or project_id),
                    "project_lifecycle": fence_status,
                }
            elif blocked_reason == "root_cancelled":
                detail = (
                    "Subagent not scheduled: its root's subtree cancellation has "
                    "begun, so the tree accepts no new work."
                )
                reason_code = blocked_reason
                extra = {"root_task_id": str(root_task_id or "")}
            elif blocked_reason == "root_budget_fence":
                detail = (
                    "Subagent not scheduled: the root budget is paused and requires an "
                    "explicit replay-safe resume, cancellation, or a new run."
                )
                reason_code = blocked_reason
                extra = {
                    "root_task_id": str(admitted.get("_budget_root_task_id") or root_task_id),
                    "budget_fence_id": str(admitted.get("_budget_fence_id") or ""),
                }
            else:
                fence_status = str(admitted.get("_acceptance_fence_status") or "active")
                detail = (
                    "Subagent not scheduled: the root task is in its atomic task-acceptance "
                    f"phase ({fence_status}); admission is closed until an explicit revision round."
                )
                reason_code = "task_acceptance_fence"
                extra = {
                    "acceptance_fence_token": str(admitted.get("_acceptance_fence_token") or ""),
                    "acceptance_fence_status": fence_status,
                }
            _reject_schedule_task(
                ctx,
                tid=tid,
                chat_id=chat_id,
                delegation_role=delegation_role,
                parent_id=parent_id,
                root_task_id=root_task_id,
                role=role,
                result_fields=result_fields,
                detail=detail,
                reason_code=reason_code,
                extra_fields=extra,
            )
            return
        try:
            write_task_result(
                ctx.DRIVE_ROOT,
                tid,
                STATUS_SCHEDULED,
                **result_fields,
                result="Subagent accepted and scheduled." if delegation_role == "subagent" else "Task accepted and scheduled.",
            )
        except Exception:
            log.warning("Failed to persist scheduled task status for %s", tid, exc_info=True)
        progress_meta = {
            "root_task_id": root_task_id,
            "parent_task_id": parent_id,
            "delegation_role": delegation_role,
            "task_group_id": task_group_id,
            "required_capabilities": required_capabilities,
            "requested_model_lane": requested_model_lane,
            # v6.82 (P5): host-attested cancelability, ROOTS ONLY. Every task
            # admitted here is a supervisor-queue task the cancel endpoint can
            # reach, but the marker exists to gate the ROOT card's "Cancel run"
            # button — a subagent row must never carry it (its card is a child
            # card, and a lineage-less replay of a marked child row could mint a
            # root-shaped card with a live Cancel). Direct-chat turns never pass
            # through this path (or RUNNING).
            "cancelable": delegation_role != "subagent",
        }
        if delegation_role == "subagent":
            progress_meta.update(_subagent_scheduled_meta(
                tid=tid, role=role, task_constraint=task_constraint,
                task_group_id=task_group_id, requested_model_lane=requested_model_lane,
                active_subagent_count=_active_subagent_count(root_task_id, pending_ref, running_ref),
                max_active_subagents=max_active,
            ))
            if queued_behind_active_cap:
                progress_meta["queued_behind_active_cap"] = True
        else:
            progress_meta["task_event"] = "scheduled"
        workers = getattr(ctx, "WORKERS", {}) or {}
        if workers and not any(not getattr(worker, "busy_task_id", None) for worker in workers.values()):
            progress_meta["worker_saturation_warning"] = True
            suffix = " (all workers are currently busy; it will start when one is free)"
        else:
            suffix = ""
        if delegation_role == "subagent" and queued_behind_active_cap:
            suffix = (
                f" (queued behind active subagent cap {max_active}; it will start when a slot frees)"
            )
        # A subagent's scheduled notice routes to its root project thread by lineage (C4.4); else its own chat; a headless subagent (chat_id=0, no bound root) still skips.
        _notice_chat = (_bound_project_chat_id(ctx, tid, parent_id, root_task_id)
                        if delegation_role == "subagent" else 0) or chat_id
        if _notice_chat:
            ctx.send_with_budget(
                _notice_chat,
                f"🗓️ Scheduled subagent {tid} ({role}): {desc}{suffix}" if delegation_role == "subagent" else f"🗓️ Scheduled task {tid}: {desc}",
                is_progress=True, task_id=tid, progress_meta=progress_meta,
            )
        ctx.persist_queue_snapshot(reason="schedule_subagent_event")


EVENT_HANDLERS = {
    "llm_usage": _handle_llm_usage,
    "external_wait_lease": _handle_external_wait_lease,
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
        disposition = disposition_for(event_type)
        if disposition is not None:
            # Declared, just not dispatched here: the server intercepts it, the log
            # envelope already answered it, or it is a fact for the ledger. Record
            # the fact under its declared tier instead of dropping it as unknown.
            log.debug(
                "Worker event %r has no dispatch handler by design (%s)",
                event_type, disposition.tier,
            )
            ctx.append_jsonl(
                ctx.DRIVE_ROOT / "logs" / "events.jsonl",
                {
                    "ts": utc_now_iso(),
                    **{key: value for key, value in evt.items() if key != "ts"},
                    "event_disposition": disposition.tier,
                },
            )
            return
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
