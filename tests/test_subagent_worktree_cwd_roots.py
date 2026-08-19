"""Regression tests for ibl-a7530ab93441.

Resource_root_path must mirror active_repo_dir_for (tools/registry.py:94) so
self_worktree subagents see their worktree in _process_root_candidates and
SHELL_CWD_BLOCKED stops firing for subagents whose ToolContext has
workspace_root + workspace_mode set.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ouroboros.tool_access import (
    _process_root_candidates,
    resource_root_path,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _ctx(**kwargs) -> SimpleNamespace:
    """Build a minimal namespace with the resource-resolution attributes."""
    defaults = {
        "repo_dir": "/opt/ouroboros",
        "drive_root": "/root/Ouroboros/data",
        "workspace_root": None,
        "workspace_mode": "",
        "active_repo_dir": None,
        "system_repo_dir": "/opt/ouroboros",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# --------------------------------------------------------------------------- #
# 1. Real partial ToolContext (integration shape)
# --------------------------------------------------------------------------- #
def test_real_partial_toolcontext_self_worktree_returns_workspace_root():
    """A real-shape ToolContext with workspace_root + workspace_mode='self_worktree'
    must resolve active_workspace to the worktree, even when active_repo_dir is not
    a callable (mirrors active_repo_dir_for semantics).
    """
    worktree = "/root/Ouroboros/subagent_worktrees/wt_abc123"
    ctx = _ctx(
        workspace_root=worktree,
        workspace_mode="self_worktree",
    )
    assert resource_root_path(ctx, "active_workspace") == pathlib.Path(worktree)


# --------------------------------------------------------------------------- #
# 2. Callable active_repo_dir returns its value
# --------------------------------------------------------------------------- #
def test_callable_active_repo_dir_returns_worktree():
    worktree = "/root/Ouroboros/subagent_worktrees/wt_xyz"
    ctx = _ctx(
        workspace_root="/tmp/should_be_ignored",
        workspace_mode="self_worktree",
        active_repo_dir=lambda: pathlib.Path(worktree),
    )
    # active_repo_dir wins — workspace_root is only the fallback.
    assert resource_root_path(ctx, "active_workspace") == pathlib.Path(worktree)


# --------------------------------------------------------------------------- #
# 3. Mock-returning active_repo_dir falls back to repo_dir (preserved branch)
# --------------------------------------------------------------------------- #
def test_active_repo_dir_returns_mock_falls_back_to_repo_dir():
    """A unittest.mock returned from active_repo_dir must trigger the Mock
    detection branch and fall back to repo_dir (existing behavior preserved).
    """
    ctx = _ctx(
        workspace_root=None,
        workspace_mode="self_worktree",
        active_repo_dir=lambda: MagicMock(),
    )
    assert resource_root_path(ctx, "active_workspace") == pathlib.Path("/opt/ouroboros")


# --------------------------------------------------------------------------- #
# 4. Non-callable active_repo_dir + workspace_mode set + workspace_root set
# --------------------------------------------------------------------------- #
def test_non_callable_active_repo_dir_uses_workspace_root():
    """When active_repo_dir is None (or otherwise non-callable) and
    workspace_mode is set with a real workspace_root, fall back to the
    workspace_root (the actual bug fix).
    """
    worktree = "/root/Ouroboros/subagent_worktrees/wt_fix_target"
    ctx = _ctx(
        workspace_root=worktree,
        workspace_mode="self_worktree",
        active_repo_dir=None,
    )
    assert resource_root_path(ctx, "active_workspace") == pathlib.Path(worktree)


# --------------------------------------------------------------------------- #
# 5. workspace_mode set but workspace_root is None → repo_dir
# --------------------------------------------------------------------------- #
def test_workspace_mode_set_no_workspace_root_falls_back_to_repo_dir():
    ctx = _ctx(
        workspace_root=None,
        workspace_mode="self_worktree",
        active_repo_dir=None,
    )
    assert resource_root_path(ctx, "active_workspace") == pathlib.Path("/opt/ouroboros")


# --------------------------------------------------------------------------- #
# 6. No workspace_mode → repo_dir (existing behavior preserved)
# --------------------------------------------------------------------------- #
def test_no_workspace_mode_returns_repo_dir():
    ctx = _ctx(
        workspace_root="/some/should/not/be/used",
        workspace_mode="",
        active_repo_dir=None,
    )
    assert resource_root_path(ctx, "active_workspace") == pathlib.Path("/opt/ouroboros")


# --------------------------------------------------------------------------- #
# 7. workspace_mode empty AND active_repo_dir callable returns worktree
# --------------------------------------------------------------------------- #
def test_callable_active_repo_dir_works_without_workspace_mode():
    """active_repo_dir is the primary path; workspace_mode is irrelevant when
    active_repo_dir returns a real path.
    """
    worktree = "/root/Ouroboros/subagent_worktrees/wt_direct"
    ctx = _ctx(
        workspace_root=None,
        workspace_mode="",
        active_repo_dir=lambda: pathlib.Path(worktree),
    )
    assert resource_root_path(ctx, "active_workspace") == pathlib.Path(worktree)


# --------------------------------------------------------------------------- #
# 8. _process_root_candidates includes the worktree in allowed_shell_cwd_roots
# --------------------------------------------------------------------------- #
def test_process_root_candidates_includes_worktree_for_self_worktree():
    """The actual shell cwd roots projection must include the worktree when
    workspace_mode='self_worktree' and active_repo_dir is non-callable. This
    is the production-bug repro.
    """
    worktree = pathlib.Path("/root/Ouroboros/subagent_worktrees/wt_proj_root")
    ctx = _ctx(
        workspace_root=str(worktree),
        workspace_mode="self_worktree",
        active_repo_dir=None,
        repo_dir="/opt/ouroboros",
    )
    candidates = _process_root_candidates(ctx, "shell")
    # candidates is a list of (label, root, source, selected_name) 4-tuples.
    roots = [str(root) for _label, root, _source, _selected_name in candidates]
    assert str(worktree) in roots, (
        f"worktree must be in allowed shell cwd roots; got {roots}"
    )


# --------------------------------------------------------------------------- #
# 9. workspace_root as string vs Path — coercion parity with active_repo_dir_for
# --------------------------------------------------------------------------- #
def test_string_workspace_root_is_coerced_via_os_fspath():
    """resource_root_path must coerce workspace_root the same way
    active_repo_dir_for does (via os.fspath inside _coerce_real_path). A string
    must produce the same Path object as a Path for the same value.
    """
    worktree = "/root/Ouroboros/subagent_worktrees/wt_str"
    str_ctx = _ctx(
        workspace_root=worktree,
        workspace_mode="self_worktree",
        active_repo_dir=None,
    )
    path_ctx = _ctx(
        workspace_root=pathlib.Path(worktree),
        workspace_mode="self_worktree",
        active_repo_dir=None,
    )
    assert resource_root_path(str_ctx, "active_workspace") == resource_root_path(
        path_ctx, "active_workspace"
    )


# --------------------------------------------------------------------------- #
# 10. Empty workspace_root string + workspace_mode set → repo_dir
# --------------------------------------------------------------------------- #
def test_empty_string_workspace_root_falls_back_to_repo_dir():
    """An empty-string workspace_root must NOT be used as the active_workspace
    (matches active_repo_dir_for's _coerce_real_path rejection of falsy values).
    """
    ctx = _ctx(
        workspace_root="",
        workspace_mode="self_worktree",
        active_repo_dir=None,
    )
    assert resource_root_path(ctx, "active_workspace") == pathlib.Path("/opt/ouroboros")
