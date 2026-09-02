"""Tests for the work_uncommitted observability surface (ibl-local-27745117e0e1).

A task can finalize cleanly — no provider failure, no tool error — yet leave
tracked files modified/staged without a commit. ``detect_work_uncommitted`` is
the SSOT probe; ``downgrade_outcome_for_uncommitted_work`` and
``_store_task_result`` act on it.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from ouroboros.work_uncommitted import (
    REASON_WORK_UNCOMMITTED,
    _failure_block_for_work_uncommitted,
    detect_work_uncommitted,
)

# The repo fixtures spawn real `git` subprocesses (CONTRIBUTING §4).
pytestmark = pytest.mark.serial


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
    from ouroboros.work_uncommitted import _porcelain_relpath
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
    from ouroboros.work_uncommitted import filter_work_uncommitted_to_attributed
    files = [" M ouroboros/utils.py", " M ouroboros/agent.py"]
    # None preserves the raw observation — the isolated-worktree regime.
    assert filter_work_uncommitted_to_attributed(files, None) == files


def test_filter_work_uncommitted_to_attributed_narrows_to_attributed_set(repo):
    from ouroboros.work_uncommitted import filter_work_uncommitted_to_attributed
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
    from ouroboros.work_uncommitted import filter_work_uncommitted_to_attributed
    files = [" M ouroboros/utils.py", " M ouroboros/agent.py"]
    # Empty attributed set: every observed dirty file is concurrent dirt;
    # the activation must NOT degrade this task.
    assert filter_work_uncommitted_to_attributed(files, []) == []


def test_filter_work_uncommitted_to_attributed_rename_target(repo):
    from ouroboros.work_uncommitted import filter_work_uncommitted_to_attributed
    files = ["R  ouroboros/old.py -> ouroboros/new.py"]
    # Attribution keyed on the post-target (the path the task actually owns).
    assert filter_work_uncommitted_to_attributed(
        files, ["ouroboros/new.py"],
    ) == files
    assert filter_work_uncommitted_to_attributed(
        files, ["ouroboros/old.py"],
    ) == []


def _clean_ok_outcome():
    """A minimal loop-outcome dict shaped like ``derive_loop_outcome``'s return,
    with the execution axis at ``EXECUTION_OK`` so the downgrade post-processor
    is eligible to act on it."""
    from ouroboros.outcomes import EXECUTION_OK, REASON_FINAL_MESSAGE

    return {
        "reason_code": REASON_FINAL_MESSAGE,
        "finish_reason": REASON_FINAL_MESSAGE,
        "failure": None,
        "outcome_axes": {
            "execution": {"status": EXECUTION_OK, "reason_code": REASON_FINAL_MESSAGE, "failure": None},
        },
    }


def test_downgrade_attribute_filter_prevents_concurrent_dirt_blame(repo, monkeypatch):
    """Task A finishes while task B left the shared tree dirty — the failure
    block cites ONLY A's own mutation-attributed file, never B's concurrent dirt.
    """
    import ouroboros.work_uncommitted as wu

    (repo / "task_a_owned.py").write_text("base\n")
    (repo / "task_b_concurrent.py").write_text("base\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "shared baseline", cwd=repo)
    (repo / "task_b_concurrent.py").write_text("B's change\n")   # concurrent dirt
    (repo / "task_a_owned.py").write_text("A's change\n")        # A's own

    # Shared-tree regime: the resolver returns the shared root + A's attributed set.
    monkeypatch.setattr(wu, "resolve_work_uncommitted_scope",
                        lambda *_a, **_k: (repo, ["task_a_owned.py"]))

    outcome = wu.downgrade_outcome_for_uncommitted_work(
        _clean_ok_outcome(), object(), {"id": "task-a"}, {},
    )

    failure = outcome.get("failure") or {}
    reported_paths = [
        line.split()[-1].split(" -> ")[-1] for line in (failure.get("files") or [])
    ]
    assert "task_a_owned.py" in reported_paths
    assert "task_b_concurrent.py" not in reported_paths
    assert outcome.get("reason_code") == REASON_WORK_UNCOMMITTED
    assert outcome["outcome_axes"]["execution"]["status"] == "degraded"


def test_downgrade_shared_tree_skips_pure_concurrent_dirt(repo, monkeypatch):
    """Task A owns nothing (empty attributed set); the downgrade is skipped
    entirely — A is not blamed for concurrent dirt."""
    import ouroboros.work_uncommitted as wu

    (repo / "shared_clean.txt").write_text("base\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    (repo / "shared_clean.txt").write_text("concurrent\n")

    monkeypatch.setattr(wu, "resolve_work_uncommitted_scope",
                        lambda *_a, **_k: (repo, []))  # A owns nothing

    outcome = wu.downgrade_outcome_for_uncommitted_work(
        _clean_ok_outcome(), object(), {"id": "task-a"}, {},
    )

    assert not outcome.get("failure")
    assert outcome.get("reason_code") != REASON_WORK_UNCOMMITTED


def test_downgrade_is_noop_when_scope_resolver_skips(repo, monkeypatch):
    """The resolver returns ``(None, None)`` on any attribution ambiguity — the
    downgrade must be a pure no-op (a gap never widens the probe into a gate)."""
    import ouroboros.work_uncommitted as wu

    (repo / "f.py").write_text("dirty, but attribution is blocked\n")
    monkeypatch.setattr(wu, "resolve_work_uncommitted_scope", lambda *_a, **_k: (None, None))

    outcome = wu.downgrade_outcome_for_uncommitted_work(
        _clean_ok_outcome(), object(), {"id": "task-a"}, {},
    )
    assert not outcome.get("failure")
    assert outcome.get("reason_code") != REASON_WORK_UNCOMMITTED


def test_downgrade_is_noop_for_a_non_ok_run(repo, monkeypatch):
    """A run that is already degraded/failed is never touched by the
    work-uncommitted post-processor, even with attributed dirt present."""
    import ouroboros.work_uncommitted as wu

    (repo / "f.py").write_text("dirty\n")
    monkeypatch.setattr(wu, "resolve_work_uncommitted_scope", lambda *_a, **_k: (repo, ["f.py"]))

    outcome = _clean_ok_outcome()
    outcome["outcome_axes"]["execution"]["status"] = "failed"
    outcome["reason_code"] = "tool_failure"

    result = wu.downgrade_outcome_for_uncommitted_work(outcome, object(), {"id": "t"}, {})
    assert result["reason_code"] == "tool_failure"
    assert result["outcome_axes"]["execution"]["status"] == "failed"


def test_resolve_work_uncommitted_scope_isolated_worktree_returns_task_root(tmp_path):
    """When ``task.repo_dir`` differs from ``env.repo_dir`` the raw probe is safe."""
    from ouroboros.work_uncommitted import resolve_work_uncommitted_scope as _resolve_work_uncommitted_scope

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
    from ouroboros.work_uncommitted import resolve_work_uncommitted_scope as _resolve_work_uncommitted_scope
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
    from ouroboros.work_uncommitted import resolve_work_uncommitted_scope as _resolve_work_uncommitted_scope

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
    from ouroboros.work_uncommitted import resolve_work_uncommitted_scope as _resolve_work_uncommitted_scope
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
