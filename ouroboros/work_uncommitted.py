"""Terminal-failure detection for a host-bound task that finalized cleanly but
left the tracked files it was sent to change modified/staged with no commit.

A leaf module (no import of ``outcomes`` or ``agent_task_pipeline``): the probe,
the per-task attribution scope, the outcome post-processor, and the two
lifecycle decisions all live here so the two size-gated call-site modules gain
only imports and one-line calls.

Contract: the probe is READ-ONLY (`git status --porcelain`), bounded, and fails
OPEN — any ambiguity (attribution blocked, no task-owned candidates, git error)
skips it entirely rather than widening it into a false-positive gate.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

# A task that ran (no provider failure, no tool errors, no delivery-control
# degradation) but TERMINATED with staged/modified tracked files left
# uncommitted. Distinct from a clean no-op: the agent DID change tracked files;
# it just did not commit. Surfaced through outcome_axes.execution.reason_code.
REASON_WORK_UNCOMMITTED = "work_uncommitted"


def detect_work_uncommitted(
    repo_dir: Any,
    *,
    max_files: int = 50,
    timeout_sec: float = 5.0,
) -> List[str]:
    """Return porcelain lines for tracked-file changes left uncommitted in ``repo_dir``.

    The shape is the same as ``git status --porcelain`` (e.g. ``" M ouroboros/x.py"``,
    ``"M  ouroboros/y.py"``). Untracked-file entries (``??``) are NOT included — they
    are a different surface, not "work the agent should have committed". The returned
    list is bounded to ``max_files``. Returns an empty list when the working tree is
    clean OR when the repository is unreadable / not a git repo.

    READ-ONLY by construction — ``git status --porcelain`` never mutates state.
    ``repo_dir`` may be a string or ``pathlib.Path``. The timeout is short: the
    check is in the task-finalization hot path and a stuck git invocation here must
    never delay finalization beyond the loop's own budget.
    """
    if not repo_dir:
        return []
    try:
        repo_path = pathlib.Path(str(repo_dir))
    except (TypeError, ValueError):
        return []
    if not repo_path.exists() or not repo_path.is_dir():
        return []
    try:
        import subprocess  # local import: keep the module import-time cheap

        completed = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if completed.returncode != 0:
        return []
    lines: List[str] = []
    for raw_line in (completed.stdout or "").splitlines():
        line = raw_line.rstrip("\r")
        if not line or len(line) < 3:
            continue
        if line.startswith("?? "):
            continue  # untracked: explicitly excluded (see docstring)
        lines.append(line)
        if len(lines) >= max_files:
            break
    return lines


def _failure_block_for_work_uncommitted(files: List[str]) -> Dict[str, Any]:
    """The ``failure`` block for a ``work_uncommitted`` reason. Centralised so the
    reason-chain and any future consumer share one shape. ``files`` is the output
    of ``detect_work_uncommitted`` (bounded at the source)."""
    return {
        "kind": "work_uncommitted",
        "reason_code": REASON_WORK_UNCOMMITTED,
        "files": list(files or []),
    }


def _porcelain_relpath(line: str) -> str:
    """Extract the repo-relative path from one ``git status --porcelain`` line.

    Plain porcelain (without ``-z``) uses ``" -> "`` for renames / copies, so a
    rename row reads ``"R  oldname -> newname"``; the post-target half is the
    path the task owns. For every other row the path occupies columns 3 onward
    (``"XY path"``). Untracked rows are filtered upstream so this never sees them.
    Returns ``""`` for malformed input — the caller treats that as a no-match.
    """
    text = str(line or "").rstrip("\r")
    if len(text) < 4:
        return ""
    body = text[3:].strip()
    if not body:
        return ""
    if " -> " in body:
        return body.split(" -> ", 1)[1].strip()
    return body


def filter_work_uncommitted_to_attributed(
    files: List[str],
    attributed_paths: Optional[Iterable[str]],
) -> List[str]:
    """Narrow a ``detect_work_uncommitted`` result to the per-task attributed set.

    ``attributed_paths=None`` preserves the raw observation. When the caller passes
    an iterable, only lines whose repo-relative path appears in it survive — the
    "only flag files this task's own mutation evidence attributes to it" contract.
    Empty input + iterable yields an empty list, not a baseline-missing error.
    """
    if attributed_paths is None:
        return list(files or [])
    allowed = {
        str(item).strip()
        for item in attributed_paths
        if str(item or "").strip()
    }
    if not allowed:
        return []
    return [
        line for line in (files or [])
        if _porcelain_relpath(line) in allowed
    ]


def resolve_work_uncommitted_scope(
    env: Any,
    task: Dict[str, Any],
    llm_trace: Dict[str, Any],
) -> tuple[Any, Any]:
    """Resolve the probe's working tree and attributed-path filter.

    - **Isolated worktree** — ``task.repo_dir`` distinct from ``env.repo_dir``:
      concurrent dirt cannot reach it, so the raw probe is safe and the filter
      is ``None``.
    - **Shared tree** — host-bound tasks share ``env.repo_dir``: a naive
      ``git status`` would attribute a concurrent task's dirt to whichever task
      finalized first, so the per-task attribution (``attributed_git_candidates``)
      is loaded and its candidate set becomes the filter.

    Returns ``(None, None)`` when the probe must be skipped (attribution blocked,
    no task-owned candidates, no shared-tree root). Skipping is the honest
    response when attribution cannot establish a clean task-owned set — an
    attribution gap must never widen the probe into a false-positive gate.
    """
    from ouroboros.mutation_attribution import attributed_git_candidates

    shared_repo_dir = getattr(env, "repo_dir", None)
    if shared_repo_dir is None:
        return None, None

    task_repo_dir_raw = task.get("repo_dir")
    task_repo_dir: Optional[pathlib.Path]
    if task_repo_dir_raw:
        task_repo_dir = pathlib.Path(task_repo_dir_raw)
    else:
        task_repo_dir = None
    try:
        shared_root = pathlib.Path(shared_repo_dir).expanduser().resolve(strict=False)
        task_root = (
            task_repo_dir.expanduser().resolve(strict=False)
            if task_repo_dir is not None
            else None
        )
    except (OSError, ValueError, TypeError):
        return None, None
    if task_root is not None and task_root != shared_root:
        return task_root, None

    drive_root = (
        task.get("budget_drive_root")
        or getattr(env, "drive_root", None)
    )
    if drive_root is None:
        return None, None
    root_task_id = str(
        task.get("root_task_id") or task.get("id") or ""
    ).strip()
    if not root_task_id:
        return None, None
    try:
        attribution = attributed_git_candidates(
            pathlib.Path(drive_root), root_task_id, shared_root,
        )
    except Exception:
        log.debug(
            "work-uncommitted attribution lookup failed for %s", root_task_id,
            exc_info=True,
        )
        return None, None
    blockers = [str(b) for b in (attribution.get("blockers") or []) if str(b or "").strip()]
    if blockers:
        return None, None
    candidates = [
        str(p).strip() for p in (attribution.get("candidates") or [])
        if str(p or "").strip()
    ]
    if not candidates:
        return None, None
    return shared_root, candidates


def _is_subagent(task: Dict[str, Any]) -> bool:
    return str((task or {}).get("delegation_role") or "").lower() == "subagent"


def downgrade_outcome_for_uncommitted_work(
    loop_outcome: Dict[str, Any],
    env: Any,
    task: Dict[str, Any],
    llm_trace: Dict[str, Any],
) -> Dict[str, Any]:
    """Post-process a derived loop outcome: an otherwise-``EXECUTION_OK`` run that
    left its OWN mutation-attributed tracked files uncommitted is downgraded to
    ``EXECUTION_DEGRADED`` / ``REASON_WORK_UNCOMMITTED`` with a typed failure
    block. Mutates and returns ``loop_outcome`` in place; a no-op for any
    non-clean run, when the scope resolver skips, or when the attributed set is
    clean. The probe runs after outcome derivation (not inside it) so the size-
    and function-gated ``derive_loop_outcome`` stays byte-identical.
    """
    from ouroboros.outcomes import EXECUTION_DEGRADED, EXECUTION_OK

    axes = loop_outcome.get("outcome_axes") if isinstance(loop_outcome, dict) else None
    execution = axes.get("execution") if isinstance(axes, dict) else None
    if not isinstance(execution, dict):
        return loop_outcome
    if str(execution.get("status") or "") != EXECUTION_OK:
        return loop_outcome

    repo_dir, attributed_paths = resolve_work_uncommitted_scope(env, task, llm_trace)
    if repo_dir is None:
        return loop_outcome
    dirty = detect_work_uncommitted(repo_dir)
    if attributed_paths is not None:
        dirty = filter_work_uncommitted_to_attributed(dirty, attributed_paths)
    if not dirty:
        return loop_outcome

    failure = _failure_block_for_work_uncommitted(dirty)
    execution["status"] = EXECUTION_DEGRADED
    execution["reason_code"] = REASON_WORK_UNCOMMITTED
    execution["failure"] = failure
    loop_outcome["reason_code"] = REASON_WORK_UNCOMMITTED
    loop_outcome["finish_reason"] = REASON_WORK_UNCOMMITTED
    loop_outcome["failure"] = failure
    return loop_outcome


def work_uncommitted_task_eval_ok(
    execution_status: str, reason_code: str, task: Dict[str, Any],
) -> bool:
    """``task_eval.ok`` including the work-uncommitted adjustment: a host-bound
    (non-subagent) run that finalized ``work_uncommitted`` is NOT ok, so
    reflection does not learn a false success. A subagent's status stays
    ``completed`` so its ``ok`` must not disagree — it keeps the ordinary rule.
    """
    from ouroboros.outcomes import EXECUTION_FAILED, EXECUTION_INFRA_FAILED

    if execution_status in {EXECUTION_FAILED, EXECUTION_INFRA_FAILED}:
        return False
    if reason_code == REASON_WORK_UNCOMMITTED and not _is_subagent(task):
        return False
    return True


def work_uncommitted_terminal_status(
    existing_status: str, execution_status: str, reason_code: str,
    task: Dict[str, Any],
) -> str:
    """The lifecycle status for a stored task result, including the
    work-uncommitted promotion. A host-bound (non-subagent) task that finalized
    ``REASON_WORK_UNCOMMITTED`` has NOT delivered — the uncommitted diff IS the
    failure — so it is promoted to ``STATUS_FAILED`` alongside the existing
    failed / infra-failed cases. A subagent stays ``STATUS_COMPLETED`` (the
    typed reason is still recorded on the axes)."""
    from ouroboros.outcomes import EXECUTION_FAILED, EXECUTION_INFRA_FAILED
    from ouroboros.task_results import STATUS_COMPLETED, STATUS_FAILED

    work_uncommitted_failure = (
        reason_code == REASON_WORK_UNCOMMITTED and not _is_subagent(task)
    )
    if (
        str(existing_status or "") == STATUS_FAILED
        or execution_status in {EXECUTION_FAILED, EXECUTION_INFRA_FAILED}
        or work_uncommitted_failure
    ):
        return STATUS_FAILED
    return STATUS_COMPLETED
