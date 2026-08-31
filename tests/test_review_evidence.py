"""Tests for review_evidence.collect_review_evidence task-scoped stale
marker gating (ibl-00615dbd1a16).

The displayed `current_repo.stale_reason` / `.stale_ts` panel must show a
DIFFERENT task's stale marker as empty (`""`), regardless of repo_key
matching. An empty stored task_id (legacy / unattributed) still matches
every caller; a non-empty stored task_id only matches itself or an unset
task_id query.
"""

import pathlib

from ouroboros.review_evidence import collect_review_evidence
from ouroboros.review_state import (
    AdvisoryReviewState,
    AdvisoryRunRecord,
    compute_snapshot_hash,
    make_repo_key,
    save_state,
)


FIXED_STALE_TS = "2026-04-07T10:00:00+00:00"
STALE_REASON = "Worktree edit invalidated advisory freshness."


def _make_git_repo(tmp_path: pathlib.Path, filename: str = "tracked.py") -> pathlib.Path:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()
    (repo_dir / filename).write_text("print('hi')\n", encoding="utf-8")
    return repo_dir


def _seed_state_with_stale_marker(
    tmp_path: pathlib.Path,
    repo_dir: pathlib.Path,
    *,
    stale_task_id: str = "",
    stale_repo_key: str | None = None,
) -> None:
    state = AdvisoryReviewState()
    snapshot_hash = compute_snapshot_hash(repo_dir)
    state.add_run(AdvisoryRunRecord(
        snapshot_hash=snapshot_hash,
        commit_message="baseline",
        status="fresh",
        ts="2026-04-07T09:00:00+00:00",
        repo_key=make_repo_key(repo_dir),
    ))
    state.mark_repo_stale(
        repo_key=make_repo_key(repo_dir),
        reason_ts=FIXED_STALE_TS,
        reason=STALE_REASON,
        stale_repo_key=stale_repo_key if stale_repo_key is not None else make_repo_key(repo_dir),
        stale_task_id=stale_task_id,
    )
    save_state(tmp_path, state)


def test_collect_review_evidence_hides_other_tasks_stale_marker(tmp_path):
    """Two tasks on the same tree: taskA records the stale marker; taskB
    asks for evidence. The `current_repo.stale_reason` / `.stale_ts` fields
    must be empty for taskB even though the repo_key matches."""

    repo_dir = _make_git_repo(tmp_path)
    _seed_state_with_stale_marker(
        tmp_path,
        repo_dir,
        stale_task_id="taskA",
        stale_repo_key=make_repo_key(repo_dir),
    )

    evidence = collect_review_evidence(
        tmp_path,
        task_id="taskB",
        repo_dir=repo_dir,
    )

    assert evidence["task_id"] == "taskB"
    assert evidence["current_repo"]["stale_reason"] == ""
    assert evidence["current_repo"]["stale_ts"] == ""


def test_collect_review_evidence_shows_matching_tasks_stale_marker(tmp_path):
    """The same task that recorded the stale marker reads it back fully."""

    repo_dir = _make_git_repo(tmp_path)
    _seed_state_with_stale_marker(
        tmp_path,
        repo_dir,
        stale_task_id="taskA",
        stale_repo_key=make_repo_key(repo_dir),
    )

    evidence = collect_review_evidence(
        tmp_path,
        task_id="taskA",
        repo_dir=repo_dir,
    )

    assert evidence["current_repo"]["stale_reason"] == STALE_REASON
    assert evidence["current_repo"]["stale_ts"] == FIXED_STALE_TS


def test_collect_review_evidence_unattributed_stale_marker_matches_every_caller(tmp_path):
    """Empty `last_stale_task_id` (legacy / pre-fix record) matches every
    caller so no historical behavior regresses for stored state written
    before this fix."""

    repo_dir = _make_git_repo(tmp_path)
    _seed_state_with_stale_marker(
        tmp_path,
        repo_dir,
        stale_task_id="",
        stale_repo_key=make_repo_key(repo_dir),
    )

    for query_task in ("taskA", "taskB", ""):
        evidence = collect_review_evidence(
            tmp_path,
            task_id=query_task,
            repo_dir=repo_dir,
        )
        assert evidence["current_repo"]["stale_reason"] == STALE_REASON, (
            f"legacy unattributed stale marker must match caller task_id={query_task!r}; "
            f"got stale_reason={evidence['current_repo']['stale_reason']!r}"
        )
        assert evidence["current_repo"]["stale_ts"] == FIXED_STALE_TS


def test_collect_review_evidence_omits_task_id_still_hides_other_task_marker(tmp_path):
    """With no `task_id` argument the legacy gate behavior is preserved:
    an UNATTRIBUTED marker still shows (matches every caller)."""

    repo_dir = _make_git_repo(tmp_path)
    _seed_state_with_stale_marker(
        tmp_path,
        repo_dir,
        stale_task_id="taskA",
        stale_repo_key=make_repo_key(repo_dir),
    )

    evidence = collect_review_evidence(
        tmp_path,
        repo_dir=repo_dir,
    )
    # No task_id supplied → (not task_id) is True → stale_matches_task is True
    # regardless of the stored stale_task_id, so the panel shows the marker.
    # (Documented: this is the caller's "tell me everything about this repo"
    # path; the gating is only meaningful when a task_id is supplied.)
    assert evidence["current_repo"]["stale_reason"] == STALE_REASON
    assert evidence["current_repo"]["stale_ts"] == FIXED_STALE_TS