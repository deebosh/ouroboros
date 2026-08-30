"""Tests for the work_uncommitted observability surface (ibl-local-27745117e0e1).

A task can finalize cleanly — no provider failure, no tool error — yet leave
tracked files modified/staged without a commit. ``detect_work_uncommitted`` is
the SSOT probe; ``list_work_uncommitted_tasks`` and the
``/api/review-continuations`` route surface it to the owner.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from ouroboros.outcomes import (
    REASON_WORK_UNCOMMITTED,
    _failure_block_for_work_uncommitted,
    detect_work_uncommitted,
)


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _git("config", "user.email", "t@t", cwd=tmp_path)
    _git("config", "user.name", "t", cwd=tmp_path)
    (tmp_path / "a.txt").write_text("one\n")
    _git("add", "a.txt", cwd=tmp_path)
    _git("commit", "-qm", "init", cwd=tmp_path)
    return tmp_path


def test_clean_tree_returns_empty(repo):
    assert detect_work_uncommitted(repo) == []


def test_modified_tracked_file_is_reported(repo):
    (repo / "a.txt").write_text("two\n")
    lines = detect_work_uncommitted(repo)
    assert any(line.endswith("a.txt") and line.strip().startswith("M") for line in lines)


def test_staged_tracked_file_is_reported(repo):
    (repo / "a.txt").write_text("three\n")
    _git("add", "a.txt", cwd=repo)
    lines = detect_work_uncommitted(repo)
    assert any("a.txt" in line for line in lines)


def test_untracked_file_is_not_reported(repo):
    (repo / "new.txt").write_text("brand new\n")
    assert detect_work_uncommitted(repo) == []


def test_non_repo_path_returns_empty(tmp_path):
    assert detect_work_uncommitted(tmp_path / "does-not-exist") == []
    assert detect_work_uncommitted("") == []


def test_failure_block_shape(repo):
    (repo / "a.txt").write_text("changed\n")
    files = detect_work_uncommitted(repo)
    block = _failure_block_for_work_uncommitted(files)
    assert block["kind"] == "work_uncommitted"
    assert block["reason_code"] == REASON_WORK_UNCOMMITTED
    assert block["files"] == files


def test_max_files_bound(repo):
    for i in range(10):
        (repo / f"f{i}.txt").write_text(f"v{i}\n")
        _git("add", f"f{i}.txt", cwd=repo)
    _git("commit", "-qm", "more", cwd=repo)
    for i in range(10):
        (repo / f"f{i}.txt").write_text(f"changed{i}\n")
    lines = detect_work_uncommitted(repo, max_files=3)
    assert len(lines) == 3


# ─────────────────────────────────────────────────────────────────────────────
# Activation (ibl-local-27745117e0e1): scope detect_work_uncommitted through the
# per-task mutation-attribution machinery so concurrent tasks' dirty state
# cannot bleed into this task's verdict. Raw probe still fires in the isolated
# worktree regime where the worktree itself isolates the task.
# ─────────────────────────────────────────────────────────────────────────────


def test_porcelain_relpath_extracts_modified_path(repo):
    from ouroboros.outcomes import _porcelain_relpath
    # Modified tracked file: "XY path" format starting at column 3.
    assert _porcelain_relpath(" M ouroboros/utils.py") == "ouroboros/utils.py"
    # Staged addition.
    assert _porcelain_relpath("A  ouroboros/utils.py") == "ouroboros/utils.py"
    # Staged modification.
    assert _porcelain_relpath("M  ouroboros/utils.py") == "ouroboros/utils.py"
    # Rename picks the post-target half (plain porcelain, no -z).
    assert (
        _porcelain_relpath("R  oldname -> newname") == "newname"
    )
    # Malformed input yields "" — the caller treats that as a no-match.
    assert _porcelain_relpath("") == ""
    assert _porcelain_relpath("XY") == ""


def test_filter_work_uncommitted_to_attributed_passes_through_when_none(repo):
    from ouroboros.outcomes import filter_work_uncommitted_to_attributed
    files = [" M ouroboros/utils.py", " M ouroboros/agent.py"]
    # None preserves the raw observation — the isolated-worktree regime.
    assert filter_work_uncommitted_to_attributed(files, None) == files


def test_filter_work_uncommitted_to_attributed_narrows_to_attributed_set(repo):
    from ouroboros.outcomes import filter_work_uncommitted_to_attributed
    files = [
        " M ouroboros/utils.py",       # task A owns this
        " M ouroboros/concurrent.py",  # task B left this dirty
        " M ouroboros/agent.py",       # task A owns this
    ]
    attributed = ["ouroboros/utils.py", "ouroboros/agent.py"]
    narrowed = filter_work_uncommitted_to_attributed(files, attributed)
    paths = [line.split()[-1] for line in narrowed]
    assert paths == ["ouroboros/utils.py", "ouroboros/agent.py"]


def test_filter_work_uncommitted_to_attributed_empty_iterable_yields_empty(repo):
    from ouroboros.outcomes import filter_work_uncommitted_to_attributed
    files = [" M ouroboros/utils.py", " M ouroboros/agent.py"]
    # Empty attributed set: every observed dirty file is concurrent dirt;
    # the activation must NOT degrade this task.
    assert filter_work_uncommitted_to_attributed(files, []) == []


def test_filter_work_uncommitted_to_attributed_rename_target(repo):
    from ouroboros.outcomes import filter_work_uncommitted_to_attributed
    files = ["R  ouroboros/old.py -> ouroboros/new.py"]
    # Attribution keyed on the post-target (the path the task actually owns).
    assert filter_work_uncommitted_to_attributed(
        files, ["ouroboros/new.py"],
    ) == files
    assert filter_work_uncommitted_to_attributed(
        files, ["ouroboros/old.py"],
    ) == []


def test_derive_loop_outcome_attribute_filter_prevents_concurrent_dirt_blame(repo):
    """Task A finishes while task B left the shared tree dirty — A is NOT flagged.

    Regression: the naive ``git status`` probe would attribute B's dirty file
    to whichever task finalized first. The activation scopes the probe to
    task A's own mutation-attributed set; concurrent dirt does not bleed.
    """
    from ouroboros.outcomes import derive_loop_outcome

    # Set up the baseline scenario:
    # - task A is the task under test
    # - task B left an unrelated tracked file dirty in the shared tree
    (repo / "task_a_owned.py").write_text("base\n")
    (repo / "task_b_concurrent.py").write_text("base\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "shared baseline", cwd=repo)
    # B left this dirty — concurrent dirt, NOT owned by task A.
    (repo / "task_b_concurrent.py").write_text("B's change\n")
    # A's own dirty file (would be flagged if not for attribution filtering).
    (repo / "task_a_owned.py").write_text("A's change\n")

    # The attributed_paths filter contains ONLY task_a_owned.py. The raw probe
    # would surface BOTH files; the filter narrows to task_a_owned.py alone
    # (and that file IS A's own, so it IS reported). Both flags are present
    # in this scenario — the regression focus is the *absence of B's file* in
    # the failure block, not the absence of degradation for A.
    outcome = derive_loop_outcome(
        "final", {"usage": {"tokens": 1}}, {},
        repo_dir=repo,
        attributed_paths=["task_a_owned.py"],
    )

    # outcome is degraded (A did leave its own tracked file dirty), but the
    # failure block must cite ONLY A's file — not B's.
    failure = outcome.get("failure") or {}
    reported = list(failure.get("files") or [])
    reported_paths = [
        line.split()[-1].split(" -> ")[-1] for line in reported
    ]
    assert "task_a_owned.py" in reported_paths, (
        "task A's own dirty file MUST be reported as work-uncommitted"
    )
    assert "task_b_concurrent.py" not in reported_paths, (
        "task B's concurrent dirty file MUST NOT be attributed to task A"
    )
    assert outcome.get("reason_code") == REASON_WORK_UNCOMMITTED


def test_derive_loop_outcome_shared_tree_attribute_filter_skips_pure_concurrent_dirt(repo):
    """Task A finishes with NO mutations of its own; concurrent dirt is ignored.

    The shared-tree regime must skip the degradation entirely when the
    attributed set is empty (A owns nothing; all dirt is concurrent).
    """
    from ouroboros.outcomes import derive_loop_outcome

    (repo / "shared_clean.txt").write_text("base\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    # Some other process / task left this dirty — not A.
    (repo / "shared_clean.txt").write_text("concurrent\n")

    outcome = derive_loop_outcome(
        "final", {"usage": {"tokens": 1}}, {},
        repo_dir=repo,
        attributed_paths=[],  # A owns nothing
    )

    # No failure block, no reason_code — A is not blamed for concurrent dirt.
    assert not outcome.get("failure"), (
        "empty attributed set must NOT produce a work-uncommitted failure"
    )
    assert outcome.get("reason_code") != REASON_WORK_UNCOMMITTED


def test_resolve_work_uncommitted_scope_isolated_worktree_returns_task_root(tmp_path):
    """When ``task.repo_dir`` differs from ``env.repo_dir`` the raw probe is safe."""
    from ouroboros.agent_task_pipeline import _resolve_work_uncommitted_scope

    shared_repo = tmp_path / "shared"
    isolated_worktree = tmp_path / "task_a_worktree"
    for root in (shared_repo, isolated_worktree):
        root.mkdir()
        _git("init", "-q", cwd=root)
        _git("config", "user.email", "t@t", cwd=root)
        _git("config", "user.name", "t", cwd=root)

    class _Env:
        pass

    env = _Env()
    env.repo_dir = shared_repo
    env.drive_root = tmp_path / "drive"

    task = {"id": "task-a", "root_task_id": "task-a", "repo_dir": str(isolated_worktree)}
    repo_dir, attributed_paths = _resolve_work_uncommitted_scope(env, task, {})
    assert pathlib.Path(repo_dir).resolve(strict=False) == pathlib.Path(
        isolated_worktree,
    ).resolve(strict=False)
    assert attributed_paths is None, (
        "isolated worktree regime must NOT carry an attributed filter — the "
        "worktree itself isolates the task"
    )


def test_resolve_work_uncommitted_scope_shared_tree_narrows_via_attribution(tmp_path):
    """Shared-tree regime narrows the probe to this task's clean-at-baseline
    candidate set (``attributed_git_candidates`` — the same notion the commit
    gate uses). A file that was ALREADY dirty when task A started is
    pre-existing WIP and is excluded from A's set, so a concurrent task whose
    change merely persists that pre-existing dirt cannot enter A's verdict.
    """
    from ouroboros.agent_task_pipeline import _resolve_work_uncommitted_scope
    from ouroboros.mutation_attribution import capture_mutation_baseline
    from ouroboros.task_results import STATUS_RUNNING, write_task_result

    shared_repo = tmp_path / "shared"
    shared_repo.mkdir()
    _git("init", "-q", cwd=shared_repo)
    _git("config", "user.email", "t@t", cwd=shared_repo)
    _git("config", "user.name", "t", cwd=shared_repo)
    (shared_repo / "clean.txt").write_text("base\n")
    (shared_repo / "preexisting.txt").write_text("base\n")
    _git("add", ".", cwd=shared_repo)
    _git("commit", "-qm", "base", cwd=shared_repo)

    drive_root = tmp_path / "drive"
    write_task_result(drive_root, "task-a", STATUS_RUNNING)

    # preexisting.txt is ALREADY dirty when task A starts — owner/other WIP.
    (shared_repo / "preexisting.txt").write_text("preexisting WIP\n")
    capture_mutation_baseline(
        drive_root,
        "task-a",
        [{"surface_type": "system_repo", "host_root": str(shared_repo)}],
    )

    # Task A touches a clean-at-baseline file and adds a new one. The
    # pre-existing dirty file is left exactly as it was at baseline (a change
    # to it after baseline would raise a `preexisting_dirty_changed` blocker,
    # which is its own tested path below).
    (shared_repo / "clean.txt").write_text("A's change\n")
    (shared_repo / "new_by_a.txt").write_text("new by A\n")

    class _Env:
        pass

    env = _Env()
    env.repo_dir = shared_repo
    env.drive_root = drive_root

    task = {"id": "task-a", "root_task_id": "task-a"}
    repo_dir, attributed_paths = _resolve_work_uncommitted_scope(env, task, {})
    assert pathlib.Path(repo_dir).resolve(strict=False) == pathlib.Path(
        shared_repo,
    ).resolve(strict=False)
    # A's candidate set: clean-at-baseline files A changed. The pre-existing
    # dirty file is excluded evidence — it never enters A's work-uncommitted
    # verdict even though it kept changing after A's baseline.
    assert sorted(attributed_paths) == ["clean.txt", "new_by_a.txt"]


def test_resolve_work_uncommitted_scope_returns_none_when_attribution_blocked(tmp_path):
    """Missing baseline must NOT silently widen the probe back to a false-positive gate."""
    from ouroboros.agent_task_pipeline import _resolve_work_uncommitted_scope

    shared_repo = tmp_path / "shared"
    shared_repo.mkdir()
    _git("init", "-q", cwd=shared_repo)
    _git("config", "user.email", "t@t", cwd=shared_repo)
    _git("config", "user.name", "t", cwd=shared_repo)
    (shared_repo / "any.txt").write_text("x\n")
    _git("add", ".", cwd=shared_repo)
    _git("commit", "-qm", "init", cwd=shared_repo)
    (shared_repo / "any.txt").write_text("dirt\n")

    class _Env:
        pass

    env = _Env()
    env.repo_dir = shared_repo
    env.drive_root = tmp_path / "drive"  # no task result / baseline at all

    task = {"id": "task-a", "root_task_id": "task-a"}
    repo_dir, attributed_paths = _resolve_work_uncommitted_scope(env, task, {})
    # Skipping the probe entirely — no attribution gap may produce a
    # false-positive verdict for the host-bound task.
    assert repo_dir is None
    assert attributed_paths is None


def test_resolve_work_uncommitted_scope_returns_none_when_preexisting_dirt_changed(tmp_path):
    """A pre-existing dirty file that keeps changing after baseline raises the
    ``preexisting_dirty_changed`` attribution blocker — the system's own signal
    that it can no longer cleanly separate this task's writes from other WIP.
    The probe must SKIP entirely rather than degrade the task on ambiguous
    evidence.
    """
    from ouroboros.agent_task_pipeline import _resolve_work_uncommitted_scope
    from ouroboros.mutation_attribution import capture_mutation_baseline
    from ouroboros.task_results import STATUS_RUNNING, write_task_result

    shared_repo = tmp_path / "shared"
    shared_repo.mkdir()
    _git("init", "-q", cwd=shared_repo)
    _git("config", "user.email", "t@t", cwd=shared_repo)
    _git("config", "user.name", "t", cwd=shared_repo)
    (shared_repo / "wip.txt").write_text("base\n")
    _git("add", ".", cwd=shared_repo)
    _git("commit", "-qm", "base", cwd=shared_repo)

    drive_root = tmp_path / "drive"
    write_task_result(drive_root, "task-a", STATUS_RUNNING)

    # wip.txt is dirty at baseline...
    (shared_repo / "wip.txt").write_text("owner WIP\n")
    capture_mutation_baseline(
        drive_root,
        "task-a",
        [{"surface_type": "system_repo", "host_root": str(shared_repo)}],
    )
    # ...and it changes again during A's window (owner or concurrent task).
    (shared_repo / "wip.txt").write_text("owner WIP, more\n")

    class _Env:
        pass

    env = _Env()
    env.repo_dir = shared_repo
    env.drive_root = drive_root

    task = {"id": "task-a", "root_task_id": "task-a"}
    repo_dir, attributed_paths = _resolve_work_uncommitted_scope(env, task, {})
    # Ambiguous attribution → skip, never a false-positive degradation.
    assert repo_dir is None
    assert attributed_paths is None
