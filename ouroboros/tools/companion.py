"""Tool surface for skill companion lifecycle diagnostics.

Adds a single ``restart_companion`` tool that recovers a skill companion
process which exceeded its auto-restart cap (5 in 300s by default) or
crashed in a way ``_monitor_runtime`` could not recover from. Closes the
``ibl-65bd10ebca02`` class: a dead companion used to mean a permanent
outage because no diagnostic path could reconstruct the spawn parameters
once the supervisor popped the runtime entry.

The persistence layer is the existing
``data/state/extension_companions.json`` file: ``CompanionSupervisor.start()``
already writes it after each spawn, ``stop()`` rewrites it after each pop,
and ``restart()`` reads it. One write path, no silent failure mode where a
companion is alive but un-recoverable.

Every invocation writes exactly one ``companion_restart`` audit row to
``data/logs/events.jsonl`` with the typed skill/companion identity, the
pre/post pids, the reason, and the task id that requested the restart.
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Any, List

from ouroboros.extension_companion import get_global_supervisor
from ouroboros.tools.registry import ToolContext, ToolEntry
from ouroboros.utils import append_jsonl, utc_now_iso

log = logging.getLogger(__name__)

_MAX_NAME_LEN = 200


def _validate_name(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return f"⚠️ TOOL_ARG_ERROR (restart_companion): {field} must be a non-empty string."
    if len(text) > _MAX_NAME_LEN:
        return f"⚠️ TOOL_ARG_ERROR (restart_companion): {field} must be ≤ {_MAX_NAME_LEN} chars."
    return ""


def _restart_companion(
    ctx: ToolContext,
    skill_name: str,
    companion_name: str,
    reason: str = "manual restart",
) -> str:
    skill_error = _validate_name(skill_name, "skill_name")
    if skill_error:
        return skill_error
    companion_error = _validate_name(companion_name, "companion_name")
    if companion_error:
        return companion_error
    reason_text = str(reason or "manual restart").strip() or "manual restart"
    if len(reason_text) > 500:
        return "⚠️ TOOL_ARG_ERROR (restart_companion): reason must be ≤ 500 chars."

    supervisor = get_global_supervisor()
    if supervisor is None:
        # Audit the failure too — silent refusal would hide diagnostic gaps.
        _audit(ctx, skill_name, companion_name, reason_text, None, None, None, False,
               "supervisor_uninitialized")
        return "⚠️ COMPANION_RESTART_ERROR: companion supervisor is not initialized."

    result = supervisor.restart(skill_name, companion_name, reason=reason_text)
    _audit(
        ctx,
        skill_name,
        companion_name,
        reason_text,
        result.pid_before,
        result.pid_after,
        result.returncode_before,
        result.success,
        result.error,
    )
    return json.dumps(
        {
            "success": result.success,
            "skill_name": skill_name,
            "companion_name": companion_name,
            "pid_before": result.pid_before,
            "pid_after": result.pid_after,
            "returncode_before": result.returncode_before,
            "error": result.error,
            "reason": reason_text,
        },
        ensure_ascii=False,
        indent=2,
    )


def _audit(
    ctx: ToolContext,
    skill_name: str,
    companion_name: str,
    reason: str,
    pid_before: Any,
    pid_after: Any,
    returncode_before: Any,
    success: bool,
    error: str,
) -> None:
    """Emit exactly one ``companion_restart`` row per invocation.

    The audit row carries the typed identity (skill_name, companion_name),
    the pre/post pids, the boolean verdict, and the free-form error string.
    A failed call (success=False, including ``not_registered`` and
    ``supervisor_uninitialized``) still produces a row so a missing companion
    leaves a discoverable trace in events.jsonl.
    """
    try:
        drive_root = pathlib.Path(getattr(ctx, "drive_root", "") or ".")
        append_jsonl(drive_root / "logs" / "events.jsonl", {
            "ts": utc_now_iso(),
            "type": "companion_restart",
            "skill_name": skill_name,
            "companion_name": companion_name,
            "reason": reason,
            "pid_before": pid_before,
            "pid_after": pid_after,
            "returncode_before": returncode_before,
            "success": bool(success),
            "error": error,
            "requested_by": "agent",
            "task_id": getattr(ctx, "task_id", "") or "",
        })
    except Exception:
        log.debug("companion_restart audit failed", exc_info=True)


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            "restart_companion",
            {
                "name": "restart_companion",
                "description": (
                    "Stop (if alive) and re-spawn a registered skill companion using its "
                    "persisted descriptor. Resets the auto-restart history for that companion "
                    "so a fresh 5-restart-in-300s window starts. Use to recover companions "
                    "that have exceeded their auto-restart cap or crashed without auto-recovery. "
                    "Returns success=False with error='not_registered' when the companion was "
                    "never started by the supervisor."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {"type": "string"},
                        "companion_name": {"type": "string"},
                        "reason": {
                            "type": "string",
                            "default": "manual restart",
                            "description": "Free-form reason recorded in the audit log (≤500 chars).",
                        },
                    },
                    "required": ["skill_name", "companion_name"],
                },
            },
            _restart_companion,
            is_code_tool=True,
            timeout_sec=30,
            # Restart touches data/state/extension_companions.json only — not the
            # repo worktree. mutates_worktree=False avoids spurious advisory
            # invalidations on every restart.
            mutates_worktree=False,
        ),
    ]
