"""Tests for the work_uncommitted observability surface (ibl-local-27745117e0e1).

A task can finalize cleanly — no provider failure, no tool error — yet leave
tracked files modified/staged without a commit. ``detect_work_uncommitted`` is
the SSOT probe; ``list_work_uncommitted_tasks`` and the
``/api/review-continuations`` route surface it to the owner.
"""

from __future__ import annotations

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
