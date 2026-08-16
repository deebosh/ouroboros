"""Tests for the ``bump_version`` tool wrapper (ouroboros/tools/version_release.py).

The pure carrier-sync logic (``bump_version_files``) is already covered by
tests/test_release_sync.py — this file covers the thin ToolContext-facing
wrapper: error-translation paths and tool registration.
"""

from __future__ import annotations

from ouroboros.tools.registry import ToolContext
from ouroboros.tools.version_release import _bump_version, get_tools


def _make_repo(tmp_path, version="4.99.1"):
    from tests.test_release_sync import _make_repo as _shared_make_repo

    return _shared_make_repo(tmp_path, version)


def _ctx(repo_dir):
    return ToolContext(repo_dir=repo_dir, drive_root=repo_dir)


def test_bump_version_reports_changed_carriers(tmp_path):
    repo = _make_repo(tmp_path, "4.99.1")
    result = _bump_version(_ctx(repo), "4.99.2", "Patch release for testing.")

    assert "Version bumped to 4.99.2" in result
    assert "VERSION" in result
    assert "NOT committed" in result
    assert (repo / "VERSION").read_text().strip() == "4.99.2"


def test_bump_version_invalid_version_is_a_typed_error(tmp_path):
    repo = _make_repo(tmp_path, "4.99.1")
    result = _bump_version(_ctx(repo), "not-a-version", "Bad version.")

    assert result.startswith("⚠️ BUMP_VERSION_INVALID:")
    # Nothing should have been written on a rejected version.
    assert (repo / "VERSION").read_text().strip() == "4.99.1"


def test_bump_version_empty_changelog_description_is_a_typed_error(tmp_path):
    repo = _make_repo(tmp_path, "4.99.1")
    result = _bump_version(_ctx(repo), "4.99.2", "")

    assert result.startswith("⚠️ BUMP_VERSION_INVALID:")
    assert (repo / "VERSION").read_text().strip() == "4.99.1"


def test_bump_version_unexpected_failure_is_a_typed_error_not_a_raise(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, "4.99.1")

    import ouroboros.tools.version_release as version_release

    def _boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(version_release, "bump_version_files", _boom)
    result = _bump_version(_ctx(repo), "4.99.2", "Patch release for testing.")

    assert result.startswith("⚠️ BUMP_VERSION_FAILED:")
    assert "disk full" in result


def test_bump_version_is_registered_as_a_worktree_mutating_tool():
    tools = get_tools()
    assert len(tools) == 1
    entry = tools[0]
    assert entry.name == "bump_version"
    assert entry.mutates_worktree is True
    assert entry.handler is _bump_version
    schema = entry.schema
    assert schema["parameters"]["required"] == ["new_version", "changelog_description"]
