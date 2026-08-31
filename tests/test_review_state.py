"""Tests for review_state.AdvisoryReviewState task-scoped stale marker (ibl-00615dbd1a16).

The displayed `current_repo.stale_reason` / `.stale_ts` panel in
`review_evidence.collect_review_evidence` must NOT show another task's
stale marker. The marker now carries a `last_stale_task_id` and is
serialized through `save_state` / `load_state`.
"""

import pathlib

from ouroboros.review_state import (
    AdvisoryReviewState,
    AdvisoryRunRecord,
    compute_snapshot_hash,
    invalidate_advisory_after_mutation,
    load_state,
    make_repo_key,
    save_state,
    update_state,
)


FIXED_STALE_TS = "2026-04-07T10:00:00+00:00"


def _make_git_repo(tmp_path: pathlib.Path, filename: str = "tracked.py") -> pathlib.Path:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()
    (repo_dir / filename).write_text("print('hi')\n", encoding="utf-8")
    return repo_dir


def _seed_fresh_run(state: AdvisoryReviewState, repo_dir: pathlib.Path) -> str:
    """Add a `fresh` advisory run so `mark_repo_stale` has something to
    invalidate; return the snapshot_hash used."""

    snapshot_hash = compute_snapshot_hash(repo_dir)
    state.add_run(AdvisoryRunRecord(
        snapshot_hash=snapshot_hash,
        commit_message="baseline",
        status="fresh",
        ts="2026-04-07T09:00:00+00:00",
        repo_key=make_repo_key(repo_dir),
    ))
    return snapshot_hash


def test_mark_repo_stale_round_trips_last_stale_task_id(tmp_path):
    repo_dir = _make_git_repo(tmp_path)
    state = AdvisoryReviewState()
    _seed_fresh_run(state, repo_dir)
    state.mark_repo_stale(
        repo_key=make_repo_key(repo_dir),
        reason_ts=FIXED_STALE_TS,
        reason="Worktree edit invalidated advisory freshness.",
        stale_repo_key=make_repo_key(repo_dir),
        stale_task_id="taskA",
    )
    assert state.last_stale_task_id == "taskA"
    save_state(tmp_path, state)

    reloaded = load_state(tmp_path)
    assert reloaded.last_stale_task_id == "taskA"


def test_mark_repo_stale_legacy_unattributed_still_matches(tmp_path):
    """An empty stored stale_task_id (legacy / pre-fix record) is the
    unattributed state — every caller matches it, preserving every
    pre-existing record's effectiveness."""

    repo_dir = _make_git_repo(tmp_path)
    state = AdvisoryReviewState()
    _seed_fresh_run(state, repo_dir)
    state.last_stale_from_edit_ts = FIXED_STALE_TS
    state.last_stale_reason = "pre-fix mutation"
    state.last_stale_repo_key = make_repo_key(repo_dir)
    # last_stale_task_id left as default ""
    save_state(tmp_path, state)

    reloaded = load_state(tmp_path)
    assert reloaded.last_stale_task_id == ""
    # matches "" in ("" , task_id) → every caller matches
    for query_task in ("taskA", "taskB", ""):
        stale_matches_task = (not query_task) or reloaded.last_stale_task_id in ("", query_task)
        assert stale_matches_task is True


def test_invalidate_advisory_after_mutation_records_mutating_task_id(tmp_path):
    """`invalidate_advisory_after_mutation(..., mutating_task_id="t1")`
    records `state.last_stale_task_id == "t1"` after the mutation flows
    through."""

    repo_dir = _make_git_repo(tmp_path, filename="mutated.py")
    repo_key = make_repo_key(repo_dir)

    state = AdvisoryReviewState()
    _seed_fresh_run(state, repo_dir)
    save_state(tmp_path, state)

    # Trigger a mutation (touch the file so a path exists to invalidate on).
    (repo_dir / "mutated.py").write_text("print('changed')\n", encoding="utf-8")

    invalidate_advisory_after_mutation(
        tmp_path,
        mutation_root=repo_dir,
        changed_paths=["mutated.py"],
        source_tool="test",
        mutating_task_id="t1",
    )

    reloaded = load_state(tmp_path)
    assert reloaded.last_stale_task_id == "t1"
    assert reloaded.last_stale_repo_key == repo_key


def test_invalidate_advisory_after_mutation_empty_task_id_remains_unattributed(tmp_path):
    """A caller that does NOT pass `mutating_task_id` (legacy call sites,
    tests, other paths) records `last_stale_task_id=""` so every existing
    match-everything behavior is preserved."""

    repo_dir = _make_git_repo(tmp_path, filename="legacy.py")
    repo_key = make_repo_key(repo_dir)

    state = AdvisoryReviewState()
    _seed_fresh_run(state, repo_dir)
    save_state(tmp_path, state)

    (repo_dir / "legacy.py").write_text("print('changed')\n", encoding="utf-8")

    invalidate_advisory_after_mutation(
        tmp_path,
        mutation_root=repo_dir,
        changed_paths=["legacy.py"],
        source_tool="test",
        # mutating_task_id omitted on purpose
    )

    reloaded = load_state(tmp_path)
    assert reloaded.last_stale_task_id == ""
    # matches "" in ("" , task_id) → still matches every caller
    stale_matches_task = (not "taskA") or reloaded.last_stale_task_id in ("", "taskA")
    assert stale_matches_task is True


def test_successful_commit_clears_last_stale_task_id(tmp_path):
    """`on_successful_commit` resets `last_stale_task_id` to "" everywhere
    it resets `last_stale_repo_key`."""

    repo_dir = _make_git_repo(tmp_path)
    repo_key = make_repo_key(repo_dir)
    state = AdvisoryReviewState()
    _seed_fresh_run(state, repo_dir)
    state.last_stale_from_edit_ts = FIXED_STALE_TS
    state.last_stale_reason = "pre-fix mutation"
    state.last_stale_repo_key = repo_key
    state.last_stale_task_id = "taskA"
    save_state(tmp_path, state)

    update_state(tmp_path, lambda s: s.on_successful_commit(repo_key=repo_key) or None)

    reloaded = load_state(tmp_path)
    assert reloaded.last_stale_repo_key == ""
    assert reloaded.last_stale_task_id == ""
    assert reloaded.last_stale_from_edit_ts == ""
    assert reloaded.last_stale_reason == ""


def test_add_run_fresh_status_clears_last_stale_task_id(tmp_path):
    """When a new fresh advisory run is appended, the stale marker (and its
    task_id) is cleared — the new run supersedes any prior stale claim."""

    repo_dir = _make_git_repo(tmp_path)
    repo_key = make_repo_key(repo_dir)
    state = AdvisoryReviewState()
    _seed_fresh_run(state, repo_dir)
    state.last_stale_from_edit_ts = FIXED_STALE_TS
    state.last_stale_reason = "pre-fix mutation"
    state.last_stale_repo_key = repo_key
    state.last_stale_task_id = "taskA"

    state.add_run(AdvisoryRunRecord(
        snapshot_hash=compute_snapshot_hash(repo_dir),
        commit_message="fresh",
        status="fresh",
        ts="2026-04-07T11:00:00+00:00",
        repo_key=repo_key,
    ))

    assert state.last_stale_from_edit_ts == ""
    assert state.last_stale_reason == ""
    assert state.last_stale_repo_key == ""
    assert state.last_stale_task_id == ""