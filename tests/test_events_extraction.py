"""Structural contracts for the semantic-no-op supervisor event-handler extraction.

v7next D08 lane: this is the QUIET half of the reference split. The task-done,
evolution-done and cancel handlers — the cancel/custody organ upstream re-owned
in 65b5d19f — stay on the facade (HOT-DEFERRED to the F2 phase), and this suite
pins that partial-split shape explicitly so a later lane inherits an exact
inventory instead of a guess.
"""

from __future__ import annotations

import ast
import pathlib

from supervisor import (
    events,
    events_budget,
    events_chat_delivery,
    events_coop_checkpoint,
    events_project_routing,
    events_runtime_controls,
    events_schedule_task,
    events_subagent_admission,
    events_worker_reports,
)
from supervisor.update_merge_policy import HOT_CODE_PATHS

REPO = pathlib.Path(__file__).parents[1]

_FAMILIES = (
    events_chat_delivery,
    events_subagent_admission,
    events_schedule_task,
    events_project_routing,
    events_coop_checkpoint,
    events_budget,
    events_worker_reports,
    events_runtime_controls,
)

_MOVED_OWNERS = {
    "HOST_NARRATION": events_chat_delivery,
    "_DELIVERED_MESSAGE_IDS": events_chat_delivery,
    # upstream extracted the resolver itself to supervisor/log_addressing.py;
    # the family keeps the historical private alias, same object.
    "_bound_project_chat_id": events_chat_delivery,
    "_handle_send_document": events_chat_delivery,
    "_handle_send_message": events_chat_delivery,
    "_handle_send_photo": events_chat_delivery,
    "_handle_send_video": events_chat_delivery,
    "_handle_typing_start": events_chat_delivery,
    "_register_delivered": events_chat_delivery,
    "_GIT_UNBORN_HEAD": events_subagent_admission,
    "_active_subagent_count": events_subagent_admission,
    "_compose_subagent_text": events_subagent_admission,
    "_depth_reservation_admits": events_subagent_admission,
    "_external_workspace_head": events_subagent_admission,
    "_is_active_subagent_task": events_subagent_admission,
    "_iter_tree_subagent_tasks": events_subagent_admission,
    "_record_delegation_constraint": events_subagent_admission,
    "_resolve_subagent_constraint": events_subagent_admission,
    "_send_subagent_rejection": events_subagent_admission,
    "_subagent_cap_blocks": events_subagent_admission,
    "_subagent_rejection_meta": events_subagent_admission,
    "_subagent_scheduled_meta": events_subagent_admission,
    "_task_own_id": events_subagent_admission,
    "_validate_external_workspace": events_subagent_admission,
    "VALID_SUBAGENT_MEMORY_MODES": events_schedule_task,
    "_PARENT_CONTEXT_END": events_schedule_task,
    "_PARENT_CONTEXT_MARKER": events_schedule_task,
    "_cleanup_rejected_worktree": events_schedule_task,
    "_extract_task_description_and_context": events_schedule_task,
    "_find_duplicate_task": events_schedule_task,
    "_format_task_for_dedup": events_schedule_task,
    "_reject_schedule_task": events_schedule_task,
    "_emit_routing_receipt": events_project_routing,
    "_handle_ensure_project_scope": events_project_routing,
    "_handle_project_digest": events_project_routing,
    "_handle_promote_chat_to_task": events_project_routing,
    "_handle_routing_manual_target": events_project_routing,
    "_persist_promote_rejection": events_project_routing,
    "_prepare_promote_source_off_loop": events_project_routing,
    "_publish_routing_ack": events_project_routing,
    "_rollback_promoted_pending": events_project_routing,
    "_COOP_CHECKPOINT_DROPPED": events_coop_checkpoint,
    "_COOP_CHECKPOINT_INFLIGHT": events_coop_checkpoint,
    "_COOP_CHECKPOINT_LOCK": events_coop_checkpoint,
    "_checkpoint_coop_roots_on_root_done": events_coop_checkpoint,
    "_maybe_checkpoint_coop_on_tree_quiescence": events_coop_checkpoint,
    "_spawn_coop_checkpoint": events_coop_checkpoint,
    "_handle_budget_pause": events_budget,
    "_handle_budget_root_fence": events_budget,
    "_handle_llm_usage": events_budget,
    "_handle_review_wave_budget_insufficient": events_budget,
    "_set_root_budget_pause_locked": events_budget,
    "_handle_acceptance_fence": events_worker_reports,
    "_handle_external_wait_lease": events_worker_reports,
    "_handle_log_event": events_worker_reports,
    "_handle_skill_lifecycle": events_worker_reports,
    "_handle_task_dispatch_resolved": events_worker_reports,
    "_handle_task_heartbeat": events_worker_reports,
    "_handle_task_metrics": events_worker_reports,
    "_handle_deep_self_review_request": events_runtime_controls,
    "_handle_owner_message_injected": events_runtime_controls,
    "_handle_promote_to_stable": events_runtime_controls,
    "_handle_toggle_consciousness": events_runtime_controls,
    "_handle_toggle_evolution": events_runtime_controls,
}

# The dispatcher keeps the table, its loop and the module logger — plus the
# HOT-DEFERRED cancel/custody organ (D09-class, upstream 65b5d19f re-owned it)
# and the unrowed post-cutoff upstream helpers. This inventory is the F2 lane's
# work order, pinned so a later edit cannot silently grow or shrink it.
_STAYED = ("EVENT_HANDLERS", "dispatch_event", "log")
_DEFERRED = (
    # _handle_schedule_task carries a >300-line FUNCTION_DEBT entry keyed by
    # path+qualname; relocating it needs the reference's same-qualname
    # relocation rule (delta D11) in the transition validator, which is ratchet
    # machinery outside this lane's mandate. It stays with the debt key.
    "_handle_schedule_task",
    "_authoritative_terminal_cost",
    "_close_campaign_after_owner_stop",
    "_finish_task_done_dispatch",
    "_handle_cancel_task",
    "_handle_evolution_task_done",
    "_handle_task_done",
    "_maybe_notify_provider_death",
    "_resolve_lifecycle_fault",
    "_task_done_durable_fault",
    "_task_done_review_projection",
)
_UNROWED_UPSTREAM = (
    "_handle_main_llm_call_state",
    "_parent_delegation_budget",
    "_routing_attachments",
)


def test_event_families_never_import_the_dispatcher_they_serve():
    """The dependency is one-way at module scope (the two handle leaves import
    the parent only inside the call-time handle), which is what keeps the
    import graph acyclic."""
    for module in _FAMILIES:
        tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                assert node.module != "supervisor.events", module.__name__
            if isinstance(node, ast.Import):
                assert all(a.name != "supervisor.events" for a in node.names), module.__name__


def test_events_facade_reexports_every_moved_identity():
    """``supervisor.events`` keeps the exact objects, so importers, monkeypatchers
    and ``inspect.getsource`` consumers see no identity change."""
    for name, owner in _MOVED_OWNERS.items():
        assert hasattr(events, name), name
        assert getattr(events, name) is getattr(owner, name), name
    owned = {name for module in _FAMILIES for name in vars(module)}
    assert set(_MOVED_OWNERS) <= owned


def test_dispatch_table_entries_resolve_to_their_owner_families():
    """Every carried family serves its wire vocabulary; the deferred organ's
    entries still resolve on the facade itself."""
    owners = {name: handler.__module__ for name, handler in events.EVENT_HANDLERS.items()}
    assert owners["send_message"] == "supervisor.events_chat_delivery"
    assert owners["typing_start"] == "supervisor.events_chat_delivery"
    assert owners["llm_usage"] == "supervisor.events_budget"
    assert owners["budget_root_fence"] == "supervisor.events_budget"
    assert owners["task_heartbeat"] == "supervisor.events_worker_reports"
    assert owners["acceptance_fence"] == "supervisor.events_worker_reports"
    assert owners["promote_chat_to_task"] == "supervisor.events_project_routing"
    assert owners["ensure_project_scope"] == "supervisor.events_project_routing"
    # _handle_schedule_task stays facade-owned (FUNCTION_DEBT key, see _DEFERRED)
    assert owners["schedule_subagent"] == "supervisor.events"
    assert owners["schedule_task"] == "supervisor.events"
    assert owners["toggle_evolution"] == "supervisor.events_runtime_controls"
    assert owners["promote_to_stable"] == "supervisor.events_runtime_controls"
    # deferred cancel/custody organ: still owned by the facade, by design
    assert owners["task_done"] == "supervisor.events"
    assert owners["cancel_task"] == "supervisor.events"
    assert owners["main_llm_call_state"] == "supervisor.events"


def test_every_event_family_is_a_hot_code_path_like_its_dispatcher():
    """Managed-update conflict labelling followed ``supervisor/events.py``; the
    families carry the same label so a split cannot silently downgrade it."""
    assert "supervisor/events.py" in HOT_CODE_PATHS
    for module in _FAMILIES:
        rel = pathlib.Path(module.__file__).relative_to(REPO).as_posix()
        assert rel in HOT_CODE_PATHS, rel


def test_the_dispatcher_kept_only_the_table_the_loop_and_the_deferred_organ():
    tree = ast.parse(pathlib.Path(events.__file__).read_text(encoding="utf-8"))
    defined = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.append(node.name)
        elif isinstance(node, ast.Assign):
            defined.extend(t.id for t in node.targets if isinstance(t, ast.Name))
    assert sorted(defined) == sorted(_STAYED + _DEFERRED + _UNROWED_UPSTREAM)
