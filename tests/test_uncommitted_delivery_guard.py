"""ibl-d29bc3cc9d67: a FRESH prose turn must not latch a terminal delivery
candidate while the task's own tracked edits are still uncommitted — the loop
re-loops with a reminder so commit_reviewed stays reachable, bounded by a
per-task nudge budget."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

import ouroboros.work_uncommitted as wu

# _repo_with_committed_file spawns real `git` subprocesses (CONTRIBUTING §4).
pytestmark = pytest.mark.serial


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), capture_output=True, check=True)


def _repo_with_committed_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "mod.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _fake_tools(repo, *, is_subagent=False):
    ctx = SimpleNamespace(
        repo_dir=str(repo),
        _uncommitted_delivery_nudges=0,
    )
    return SimpleNamespace(
        _ctx=ctx,
        _ctx_is_delegated_subagent=lambda: is_subagent,
    )


def _trace_edit(path):
    return {"tool_calls": [{"tool": "edit_text", "status": "ok", "args": {"path": path}}]}


def test_attributed_probe_flags_own_uncommitted_edit(tmp_path):
    repo = _repo_with_committed_file(tmp_path)
    (repo / "mod.py").write_text("x = 2\n")  # uncommitted edit
    hits = wu._uncommitted_attributed_paths(_fake_tools(repo), _trace_edit("mod.py"))
    assert hits == ["mod.py"]


def test_attributed_probe_ignores_unattributed_dirty_file(tmp_path):
    repo = _repo_with_committed_file(tmp_path)
    (repo / "mod.py").write_text("x = 2\n")  # dirty, but NOT in this trace
    hits = wu._uncommitted_attributed_paths(
        _fake_tools(repo), _trace_edit("other_file.py")
    )
    assert hits == []


def test_attributed_probe_clean_tree_returns_empty(tmp_path):
    repo = _repo_with_committed_file(tmp_path)
    hits = wu._uncommitted_attributed_paths(_fake_tools(repo), _trace_edit("mod.py"))
    assert hits == []


def test_attributed_probe_skips_subagents(tmp_path):
    repo = _repo_with_committed_file(tmp_path)
    (repo / "mod.py").write_text("x = 2\n")
    hits = wu._uncommitted_attributed_paths(
        _fake_tools(repo, is_subagent=True), _trace_edit("mod.py")
    )
    assert hits == []


def test_withhold_reloops_then_relents_after_budget(tmp_path):
    repo = _repo_with_committed_file(tmp_path)
    (repo / "mod.py").write_text("x = 2\n")
    tools = _fake_tools(repo)
    trace = {"reasoning_notes": [], **_trace_edit("mod.py")}
    progress: list[str] = []

    # First _MAX_UNCOMMITTED_DELIVERY_NUDGES calls withhold (re-loop).
    for i in range(wu.MAX_UNCOMMITTED_DELIVERY_NUDGES):
        msgs: list = []
        assert wu.withhold_prose_for_uncommitted_work(
            tools, msgs, trace, "done, changes on disk", progress.append
        ) is True
        assert any("UNCOMMITTED_WORK" in str(m.get("content", "")) for m in msgs)
        assert tools._ctx._uncommitted_delivery_nudges == i + 1

    # Budget spent → let finalization proceed unchanged.
    msgs = []
    assert wu.withhold_prose_for_uncommitted_work(
        tools, msgs, trace, "done", progress.append
    ) is False
    assert msgs == []


def test_withhold_noop_when_tree_clean(tmp_path):
    repo = _repo_with_committed_file(tmp_path)
    tools = _fake_tools(repo)
    trace = {"reasoning_notes": [], **_trace_edit("mod.py")}
    assert wu.withhold_prose_for_uncommitted_work(
        tools, [], trace, "analysis complete", lambda _s: None
    ) is False
    assert tools._ctx._uncommitted_delivery_nudges == 0


def test_reminder_names_the_files_and_both_exits(tmp_path):
    text = wu._uncommitted_delivery_reminder(["ouroboros/a.py", "tests/b.py"])
    assert "ouroboros/a.py" in text and "tests/b.py" in text
    assert "commit_reviewed" in text
    assert "vcs_restore" in text


def test_attributed_probe_covers_edit_batch_and_apply_patch(tmp_path):
    """The multi-file coding tools carry paths under different arg keys —
    edit_batch: args['edits'][*]['path']; apply_patch: `*** Update File:`
    headers in args['patch']; multi-file write_file: args['files'][*]['path']."""
    repo = _repo_with_committed_file(tmp_path)
    (repo / "mod.py").write_text("x = 2\n")
    (repo / "two.py").write_text("y = 1\n")
    (repo / "three.py").write_text("z = 1\n")
    _git(repo, "add", "two.py", "three.py")
    _git(repo, "commit", "-qm", "more")
    (repo / "two.py").write_text("y = 2\n")
    (repo / "three.py").write_text("z = 2\n")

    trace = {"tool_calls": [
        {"tool": "edit_batch", "status": "ok",
         "args": {"edits": [{"path": "mod.py"}, {"path": "two.py"}]}},
        {"tool": "apply_patch", "status": "ok",
         "args": {"patch": "*** Begin Patch\n*** Update File: three.py\n*** End Patch\n"}},
    ]}
    hits = sorted(wu._uncommitted_attributed_paths(_fake_tools(repo), trace))
    assert hits == ["mod.py", "three.py", "two.py"]
