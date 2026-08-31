"""ibl-d29bc3cc9d67: a FRESH prose turn must not latch a terminal delivery
candidate while the task's own tracked edits are still uncommitted — the loop
re-loops with a reminder so commit_reviewed stays reachable, bounded by a
per-task nudge budget."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import ouroboros.loop as loop


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


def _fake_tools(repo, *, delegation_role="root"):
    ctx = SimpleNamespace(
        repo_dir=str(repo),
        delegation_role=delegation_role,
        _uncommitted_delivery_nudges=0,
    )
    return SimpleNamespace(_ctx=ctx)


def _trace_edit(path):
    return {"tool_calls": [{"tool": "edit_text", "status": "ok", "args": {"path": path}}]}


def test_attributed_probe_flags_own_uncommitted_edit(tmp_path):
    repo = _repo_with_committed_file(tmp_path)
    (repo / "mod.py").write_text("x = 2\n")  # uncommitted edit
    hits = loop._uncommitted_attributed_paths(_fake_tools(repo), _trace_edit("mod.py"))
    assert hits == ["mod.py"]


def test_attributed_probe_ignores_unattributed_dirty_file(tmp_path):
    repo = _repo_with_committed_file(tmp_path)
    (repo / "mod.py").write_text("x = 2\n")  # dirty, but NOT in this trace
    hits = loop._uncommitted_attributed_paths(
        _fake_tools(repo), _trace_edit("other_file.py")
    )
    assert hits == []


def test_attributed_probe_clean_tree_returns_empty(tmp_path):
    repo = _repo_with_committed_file(tmp_path)
    hits = loop._uncommitted_attributed_paths(_fake_tools(repo), _trace_edit("mod.py"))
    assert hits == []


def test_attributed_probe_skips_subagents(tmp_path):
    repo = _repo_with_committed_file(tmp_path)
    (repo / "mod.py").write_text("x = 2\n")
    hits = loop._uncommitted_attributed_paths(
        _fake_tools(repo, delegation_role="subagent"), _trace_edit("mod.py")
    )
    assert hits == []


def test_withhold_reloops_then_relents_after_budget(tmp_path):
    repo = _repo_with_committed_file(tmp_path)
    (repo / "mod.py").write_text("x = 2\n")
    tools = _fake_tools(repo)
    trace = {"reasoning_notes": [], **_trace_edit("mod.py")}
    progress: list[str] = []

    # First _MAX_UNCOMMITTED_DELIVERY_NUDGES calls withhold (re-loop).
    for i in range(loop._MAX_UNCOMMITTED_DELIVERY_NUDGES):
        msgs: list = []
        assert loop._withhold_prose_for_uncommitted_work(
            tools, msgs, trace, "done, changes on disk", progress.append
        ) is True
        assert any("UNCOMMITTED_WORK" in str(m.get("content", "")) for m in msgs)
        assert tools._ctx._uncommitted_delivery_nudges == i + 1

    # Budget spent → let finalization proceed unchanged.
    msgs = []
    assert loop._withhold_prose_for_uncommitted_work(
        tools, msgs, trace, "done", progress.append
    ) is False
    assert msgs == []


def test_withhold_noop_when_tree_clean(tmp_path):
    repo = _repo_with_committed_file(tmp_path)
    tools = _fake_tools(repo)
    trace = {"reasoning_notes": [], **_trace_edit("mod.py")}
    assert loop._withhold_prose_for_uncommitted_work(
        tools, [], trace, "analysis complete", lambda _s: None
    ) is False
    assert tools._ctx._uncommitted_delivery_nudges == 0


def test_reminder_names_the_files_and_both_exits(tmp_path):
    text = loop._uncommitted_delivery_reminder(["ouroboros/a.py", "tests/b.py"])
    assert "ouroboros/a.py" in text and "tests/b.py" in text
    assert "commit_reviewed" in text
    assert "git checkout --" in text
