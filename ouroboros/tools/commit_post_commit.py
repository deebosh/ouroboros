"""Owner-authorized auto-restart hook after bypass-path commits.

Per BIBLE P3 (Immune Integrity), restart hooks gate when the supervisor picks
up new code. This hook is owner-authorized (the caller explicitly requested
``skip_advisory_review=True``), single-purpose, and bounded to the bypass
path — the only path today that lands a commit without an accompanying
``restart_required`` flag from an evolution transaction.

The hook is a NO-OP when:

* ``skip_advisory_review=False`` — the reviewed path is responsible for its
  own restart machinery (e.g. evolution tx + ``restart_required``).
* ``task_type == "evolution"`` — evolution has its own restart gate at
  ``supervisor/queue.py:1377-1385``; double-triggering would race.
* ``push_succeeded=False`` — do not restart over an unpushed commit.
* A ``pending_restart_verify.json`` already exists — someone is mid-restart;
  do not stack another verify on top.

The hook triggers when:

* ``skip_advisory_review=True`` on the current commit.
* The current task is NOT an evolution task.
* Push succeeded.
* No pending file is present.

On trigger the hook writes ``pending_restart_verify.json`` (consumed on next
boot by ``ouroboros/agent_startup_checks.py``) and returns a short
operator-visible notice describing the queued restart. The hook does NOT
modify the reviewed commit path, the supervisor loop, or any gate that
``commit_reviewed`` would otherwise run.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Optional

from ouroboros.utils import atomic_write_json, run_cmd, utc_now_iso

log = logging.getLogger(__name__)


def maybe_request_restart_after_bypass(
    *,
    repo_dir: pathlib.Path,
    drive_root: pathlib.Path,
    task_type: str,
    skip_advisory_review: bool,
    commit_sha: str,
    commit_message: str,
    push_succeeded: bool,
) -> Optional[str]:
    """Queue a post-restart verification if the current commit was a bypass.

    Returns a short operator-visible notice when a restart was queued, or
    ``None`` for every no-op path. Side effect: writes
    ``pending_restart_verify.json`` under ``<drive_root>/state/``.
    """
    if not skip_advisory_review:
        return None
    if str(task_type or "") == "evolution":
        return None
    if not push_succeeded:
        return None

    pending = pathlib.Path(drive_root) / "state" / "pending_restart_verify.json"
    if pending.exists():
        return None

    try:
        sha = str(commit_sha or "").strip()
        if not sha:
            sha = str(run_cmd(
                ["git", "rev-parse", "HEAD"], cwd=str(repo_dir)
            )).strip()
        branch = str(run_cmd(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo_dir)
        )).strip()
    except Exception as exc:  # noqa: BLE001 — best-effort gate, see plan §3.4
        log.debug("bypass-post-commit hook: git ref probe failed (%s)", exc)
        return None

    if not sha:
        return None

    pending.parent.mkdir(parents=True, exist_ok=True)
    msg = (commit_message or "").strip() or "(no message)"
    reason = (
        "auto-restart after skip_advisory_review=True bypass commit: "
        f"{msg[:120]}"
    )
    try:
        atomic_write_json(pending, {
            "ts": utc_now_iso(),
            "expected_sha": sha,
            "expected_branch": branch,
            "reason": reason,
            "source": "bypass_commit_post_hook_v1",
        })
    except Exception as exc:  # noqa: BLE001
        log.debug("bypass-post-commit hook: failed to persist verify file (%s)", exc)
        return None

    return (
        f"⚠️ BYPASS_COMMIT_RESTART_QUEUED: supervisor will pick up {sha[:8]} "
        "on next boot. Owner may force an immediate /restart to shorten the window."
    )