"""Host-bound task outcome helpers, split from ``agent_task_pipeline`` (module ceiling).

- ``_attach_host_mutation_projection`` binds durable mutation evidence to the
  existing outcome trace seam.
- ``_resolve_work_uncommitted_scope`` resolves the work-uncommitted probe's
  working tree and attributed-path filter (isolated-worktree vs shared-tree
  regimes; ``(None, None)`` when the probe must be skipped).

Both are re-exported from ``ouroboros.agent_task_pipeline`` so existing
``pipeline.<name>`` references and ``from ouroboros.agent_task_pipeline import
<name>`` call sites keep working unchanged.
"""
from __future__ import annotations

import logging
import pathlib
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


def _attach_host_mutation_projection(
    env: Any,
    task: Dict[str, Any],
    llm_trace: Dict[str, Any],
) -> None:
    """Bind durable mutation evidence to the existing outcome trace seam."""
    from ouroboros.mutation_attribution import (
        attribution_task_id,
        load_mutation_evidence_projection,
        record_terminal_mutation_candidates,
    )

    task_id = str(task.get("id") or "").strip()
    root_task_id = str(task.get("root_task_id") or task_id).strip()
    task_ids = list(dict.fromkeys(item for item in (root_task_id, task_id) if item))
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    evidence_root = pathlib.Path(
        task.get("budget_drive_root")
        or metadata.get("budget_drive_root")
        or getattr(env, "budget_drive_root", None)
        or env.drive_root
    )
    # Only the owning root task refreshes its terminal candidate snapshot at
    # outcome derivation; a subagent never rewrites the root's evidence.
    if task_id and task_id == root_task_id:
        try:
            if attribution_task_id(evidence_root, (task_id,)) == task_id:
                record_terminal_mutation_candidates(evidence_root, task_id)
        except Exception:
            log.debug("terminal mutation snapshot failed for %s", task_id, exc_info=True)
    llm_trace.pop("mutation_attribution", None)
    for candidate in task_ids:
        projection = load_mutation_evidence_projection(evidence_root, candidate)
        if projection:
            llm_trace["mutation_attribution"] = projection
            return


def _resolve_work_uncommitted_scope(
    env: Any,
    task: Dict[str, Any],
    llm_trace: Dict[str, Any],
) -> tuple[Any, Any]:
    """Resolve the work-uncommitted probe's working tree and attributed-path filter.

    Two regimes drive the activation contract for ``detect_work_uncommitted``:

    - **Isolated worktree** — the task owns its own worktree (``task.repo_dir``
      distinct from ``env.repo_dir``). Concurrent dirt cannot reach it, so the
      raw probe is safe and the attributed-path filter is ``None``.
    - **Shared tree** — host-bound tasks share ``env.repo_dir``. A naive
      ``git status`` would attribute a concurrent task's dirty files to whichever
      task finalized first. The contract here is "only flag files this task's
      own mutation evidence attributes to it": we load the per-task attribution
      via ``attributed_git_candidates`` and pass the resulting path set as the
      filter. Concurrent dirt is ignored.

    Returns ``(None, None)`` when the probe should be skipped entirely
    (attribution blocked, no task-owned candidates, or no shared-tree root to
    scan). Skipping is the honest response when attribution cannot establish a
    clean task-owned set: an attribution gap must not silently widen the probe
    back into a false-positive gate.
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
        # Isolated worktree regime: the worktree itself isolates the task.
        return task_root, None

    # Shared-tree regime: narrow the probe to the per-task attributed set.
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
        # Attribution could not establish a clean task-owned set (baseline
        # missing, baseline stale, etc.). Skipping is the honest response.
        return None, None
    candidates = [
        str(p).strip() for p in (attribution.get("candidates") or [])
        if str(p or "").strip()
    ]
    if not candidates:
        # No task-owned candidates; concurrent dirt is irrelevant to this task.
        return None, None
    return shared_root, candidates
