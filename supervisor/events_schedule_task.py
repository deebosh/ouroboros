"""The schedule_task admission gates, the duplicate gate, and its refusals.

One owner for the facts the dispatch parent's schedule handler needs: the
chat-target gate, the semantic duplicate gate, the composed queue payload,
and every refusal path including worktree cleanup for a rejected subagent.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional
from ouroboros.tool_capabilities import ACTING_SUBAGENT_MODE
from ouroboros.task_results import (
    STATUS_FAILED,
    write_task_result,
)
from supervisor.events_subagent_admission import (
    _send_subagent_rejection,
)

log = logging.getLogger(__name__)


def _events():
    """The parent module, read at call time.

    The parent owns the rebindable module state and the members tests
    monkeypatch there; reading them through the module at each call keeps
    one binding, where a from-import would freeze the value this leaf saw
    at import time (the owner-approved D18/D33 mechanical exception).
    """
    from supervisor import events

    return events


_PARENT_CONTEXT_MARKER = "[BEGIN_PARENT_CONTEXT"


_PARENT_CONTEXT_END = "[END_PARENT_CONTEXT]"


VALID_SUBAGENT_MEMORY_MODES = frozenset({"forked", "empty"})


def _extract_task_description_and_context(task: Dict[str, Any]) -> tuple[str, str]:
    description = str(task.get("description") or "").strip()
    context = str(task.get("context") or "").strip()
    if description or context:
        return description, context

    text = str(task.get("text") or task.get("description") or "").strip()
    if not text:
        return "", ""
    if _PARENT_CONTEXT_MARKER not in text or _PARENT_CONTEXT_END not in text:
        return text, ""

    before_marker, after_marker = text.split(_PARENT_CONTEXT_MARKER, 1)
    description = before_marker.split("\n\n---\n", 1)[0].strip()
    if "]\n" in after_marker:
        after_marker = after_marker.split("]\n", 1)[1]
    context = after_marker.rsplit(_PARENT_CONTEXT_END, 1)[0].strip()
    return description, context


def _format_task_for_dedup(
    task_id: str,
    description: str,
    context: str,
    *,
    expected_output: str = "",
    constraints: str = "",
    role: str = "",
) -> str:
    sections = [
        f"Task ID: {task_id}\n"
        f"Description:\n{description or '(empty)'}\n\n"
        f"Context:\n{context or '(none)'}"
    ]
    if expected_output:
        sections.append(f"Expected output:\n{expected_output}")
    if constraints:
        sections.append(f"Constraints:\n{constraints}")
    if role:
        sections.append(f"Role:\n{role}")
    return "\n\n".join(sections)


def _find_duplicate_task(
    desc: str,
    task_context: str,
    pending: list,
    running: dict,
    *,
    expected_output: str = "",
    constraints: str = "",
    role: str = "",
    dedupe_identity: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Use a scoped light-model attempt to reject only true duplicate active tasks.

    Provider/parse failures remain fail-soft, but monetary-accounting rails propagate
    so an unavailable budget can never be mistaken for a semantic non-duplicate.
    """
    identity = dedupe_identity if isinstance(dedupe_identity, dict) else {}

    def _task_identifier(existing_task: Dict[str, Any]) -> str:
        return str(existing_task.get("id") or existing_task.get("task_id") or "").strip()

    def _is_subagent_ancestor_task(existing_task: Dict[str, Any]) -> bool:
        delegation_role = str(identity.get("delegation_role") or "")
        if delegation_role != "subagent":
            return False
        existing_id = _task_identifier(existing_task)
        parent = str(identity.get("parent_task_id") or "").strip()
        root = str(identity.get("root_task_id") or "").strip()
        if existing_id and existing_id in {parent, root}:
            return True
        existing_role = str(existing_task.get("delegation_role") or "")
        existing_root = str(existing_task.get("root_task_id") or "").strip()
        return bool(existing_role == "root" and root and existing_root == root)

    def _is_distinct_parallel_subagent(existing_task: Dict[str, Any]) -> bool:
        # Lineage/role are scheduler identity facts for parallel swarm slots;
        # semantic duplicate judgment still belongs to the LLM for remaining cases.
        delegation_role = str(identity.get("delegation_role") or "")
        if str(delegation_role or "") != "subagent":
            return False
        if str(existing_task.get("delegation_role") or "") != "subagent":
            return False
        root = str(identity.get("root_task_id") or "")
        if not root or str(existing_task.get("root_task_id") or "") != root:
            return False
        parent = str(identity.get("parent_task_id") or "")
        existing_parent = str(existing_task.get("parent_task_id") or "")
        if parent != existing_parent:
            return True
        new_role = str(role or "").strip()
        existing_role = str(existing_task.get("role") or "").strip()
        return bool(new_role and existing_role and new_role != existing_role)

    existing = []
    for task in pending:
        description, context = _extract_task_description_and_context(task)
        if (
            description.strip()
            and not _is_subagent_ancestor_task(task)
            and not _is_distinct_parallel_subagent(task)
        ):
            existing.append({
                "id": str(task.get("id", "?")),
                "description": description,
                "context": context,
                "expected_output": str(task.get("expected_output") or ""),
                "constraints": str(task.get("constraints") or ""),
                "role": str(task.get("role") or ""),
                "delegation_role": str(task.get("delegation_role") or ""),
                "parent_task_id": str(task.get("parent_task_id") or ""),
                "root_task_id": str(task.get("root_task_id") or ""),
            })
    for task_id, meta in running.items():
        task_data = meta.get("task") if isinstance(meta, dict) else None
        if not isinstance(task_data, dict):
            continue
        description, context = _extract_task_description_and_context(task_data)
        if (
            description.strip()
            and not _is_subagent_ancestor_task({"id": task_id, **task_data})
            and not _is_distinct_parallel_subagent(task_data)
        ):
            existing.append({
                "id": str(task_id),
                "description": description,
                "context": context,
                "expected_output": str(task_data.get("expected_output") or ""),
                "constraints": str(task_data.get("constraints") or ""),
                "role": str(task_data.get("role") or ""),
                "delegation_role": str(task_data.get("delegation_role") or ""),
                "parent_task_id": str(task_data.get("parent_task_id") or ""),
                "root_task_id": str(task_data.get("root_task_id") or ""),
            })

    if not existing:
        return None

    existing_lines = "\n\n".join(
        _format_task_for_dedup(
            e["id"],
            e["description"],
            e["context"],
            expected_output=e.get("expected_output", ""),
            constraints=e.get("constraints", ""),
            role=e.get("role", ""),
        )
        for e in existing
    )
    prompt = (
        "Determine whether the NEW task is a true duplicate of any EXISTING active task.\n"
        "Only return a task ID if the requested work is materially the same.\n"
        "Tasks that share a broad goal but differ in target model, creative focus, "
        "scope, parent context, or intended output are NOT duplicates.\n\n"
        "NEW TASK\n"
        f"{_format_task_for_dedup('NEW', desc, task_context, expected_output=expected_output, constraints=constraints, role=role)}\n\n"
        f"EXISTING ACTIVE TASKS\n{existing_lines}\n\n"
        "Reply ONLY with the task ID if duplicate, or NONE if not."
    )

    from dataclasses import replace

    from ouroboros.usage_accounting import (
        BudgetExceeded,
        UsageAccountingError,
        UsageScope,
        current_usage_scope,
        usage_scope,
    )

    base_scope = current_usage_scope()
    prospective_task_id = str(identity.get("task_id") or (base_scope.task_id if base_scope else ""))
    prospective_root_id = str(
        identity.get("root_task_id")
        or (base_scope.root_task_id if base_scope else "")
        or prospective_task_id
    )
    prospective_parent_id = str(
        identity.get("parent_task_id")
        or (base_scope.parent_task_id if base_scope else "")
    )
    prospective_budget_root: Any = identity.get("budget_drive_root") or (
        base_scope.drive_root if base_scope else None
    )
    if base_scope is not None:
        duplicate_scope = replace(
            base_scope,
            drive_root=prospective_budget_root,
            task_id=prospective_task_id,
            root_task_id=prospective_root_id,
            parent_task_id=prospective_parent_id,
            category="planning",
            source="task_duplicate_check",
        )
    else:
        try:
            global_limit = float(os.environ.get("TOTAL_BUDGET", "0") or 0)
        except (TypeError, ValueError):
            global_limit = 0.0
        try:
            root_limit = float(os.environ.get("OUROBOROS_PER_TASK_COST_USD", "0") or 0)
        except (TypeError, ValueError):
            root_limit = 0.0
        duplicate_scope = UsageScope(
            drive_root=prospective_budget_root,
            task_id=prospective_task_id,
            root_task_id=prospective_root_id,
            parent_task_id=prospective_parent_id,
            category="planning",
            source="task_duplicate_check",
            global_limit_usd=global_limit if global_limit > 0 else None,
            root_limit_usd=root_limit if root_limit > 0 else None,
        )

    try:
        from ouroboros.config import get_light_model
        from ouroboros.llm import LLMClient
        light_model = get_light_model()
        client = LLMClient()
        with usage_scope(duplicate_scope):
            resp_msg, _usage = client.chat(
                messages=[{"role": "user", "content": prompt}],
                model=light_model,
                reasoning_effort="low",
                max_tokens=50,
            )
        answer = (resp_msg.get("content") or "NONE").strip()
        if answer.upper() == "NONE" or not answer:
            return None
        answer_lower = answer.lower()
        for e in existing:
            if e["id"].lower() in answer_lower:
                return e["id"]
        return None
    except (BudgetExceeded, UsageAccountingError):
        raise
    except Exception as exc:
        log.warning("LLM dedup unavailable, accepting task: %s", exc)
        return None


def _cleanup_rejected_worktree(tid: str, result_fields: Dict[str, Any]) -> None:
    """Tear down a write surface provisioned for an acting subagent that is then
    rejected by a later gate, so rejected schedules never leak a worktree or an
    empty genesis project."""
    tc = result_fields.get("task_constraint") if isinstance(result_fields, dict) else None
    if not (isinstance(tc, dict) and tc.get("mode") == ACTING_SUBAGENT_MODE):
        return
    surface = str(tc.get("surface") or "")
    write_root = str(tc.get("write_root") or "").strip()
    if not write_root:
        return
    try:
        from ouroboros import subagent_worktrees

        if surface == "self_worktree":
            subagent_worktrees.remove_worktree(task_id=str(tid))
        elif surface == "genesis":
            subagent_worktrees.remove_genesis_project(write_root)
    except Exception:
        log.debug("Failed to clean up rejected acting write surface for %s", tid, exc_info=True)


def _reject_schedule_task(
    ctx: Any,
    *,
    tid: str,
    chat_id: int,
    delegation_role: str,
    parent_id: Any,
    root_task_id: str,
    role: str,
    result_fields: Dict[str, Any],
    detail: str,
    status: str = STATUS_FAILED,
    fallback_message: str = "",
    reason_code: Optional[str] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
    persist_result: bool = True,
) -> None:
    """Clean up and notify a rejection, preserving an existing result when asked."""
    _cleanup_rejected_worktree(tid, result_fields)
    log.warning("Rejecting scheduled task %s: %s", tid, detail)
    write_fields = {**result_fields, **(extra_fields or {})}
    if reason_code:
        write_fields["reason_code"] = reason_code
    if persist_result:
        try:
            write_task_result(
                ctx.DRIVE_ROOT,
                tid,
                status,
                **write_fields,
                result=detail,
                cost_usd=0.0,
            )
        except Exception:
            log.warning("Failed to persist schedule rejection for %s", tid, exc_info=True)
    # A torn-down notification bus must not escape into the supervisor loop.
    try:
        if chat_id:
            if delegation_role == "subagent":
                _send_subagent_rejection(
                    ctx,
                    chat_id,
                    tid=tid,
                    parent_id=parent_id,
                    root_task_id=root_task_id,
                    role=role,
                    status=status,
                    detail=detail,
                )
            elif fallback_message:
                ctx.send_with_budget(chat_id, fallback_message)
    except Exception:
        log.warning("Failed to notify schedule rejection for %s", tid, exc_info=True)
