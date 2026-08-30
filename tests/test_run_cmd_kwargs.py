"""Regression tests for ouroboros.utils.run_cmd keyword-only kwargs.

These tests pin the bug fix that closes ``ibl-local-67f046589043``:

* ``ouroboros/tools/registry.py::_worktree_status_snapshot`` calls
  ``run_cmd(..., timeout=20)``. Before the fix, ``run_cmd`` only accepted
  ``(cmd, cwd)`` and raised ``TypeError`` on the unknown kwarg; the surrounding
  bare ``except Exception:`` swallowed the error and the method always
  returned ``"<status-unavailable>"``. Downstream
  ``_invalidate_advisory_if_worktree_changed`` then compared two equal
  sentinels and never invalidated advisory freshness on a worktree change
  from that path.

* After the fix, ``run_cmd`` accepts ``timeout`` and ``check`` as keyword-only
  kwargs; the existing positional ``(cmd, cwd)`` signature keeps working for
  every other caller. A ``subprocess.TimeoutExpired`` is converted to a
  ``RuntimeError`` with the SAME ``"Command failed: "`` prefix the
  non-zero-exit branch already produces, so callers/tests that match on that
  text keep parsing — the timeout fact is appended in parentheses.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from ouroboros.utils import run_cmd


def test_run_cmd_plain_success_returns_stripped_stdout() -> None:
    """(a) Plain success returns stdout stripped."""
    assert run_cmd(["echo", "hello"]) == "hello"


def test_run_cmd_nonzero_exit_raises_runtime_error_by_default() -> None:
    """(b) Non-zero exit still raises RuntimeError with the legacy prefix."""
    with pytest.raises(RuntimeError) as excinfo:
        run_cmd(["false"])
    msg = str(excinfo.value)
    assert "Command failed:" in msg
    assert "false" in msg  # the offending command is named


def test_run_cmd_check_false_returns_stdout_on_nonzero_exit() -> None:
    """(c) check=False suppresses the non-zero-exit raise and returns stdout."""
    out = run_cmd(["sh", "-c", "echo captured-output; exit 3"], check=False)
    assert out == "captured-output"


def test_run_cmd_timeout_raises_runtime_error_mentioning_timeout() -> None:
    """(d) timeout= on a slow command raises RuntimeError naming the timeout.

    The new ``Command failed:`` prefix matches the legacy non-zero-exit
    branch so callers/tests that grep the prefix keep working; the timeout
    fact is appended so a consumer can distinguish the two failure modes.
    """
    with pytest.raises(RuntimeError) as excinfo:
        run_cmd(["sleep", "5"], timeout=1)
    msg = str(excinfo.value)
    assert "Command failed:" in msg
    assert "TimeoutExpired" in msg
    assert "sleep" in msg
    # The numeric timeout value must be present so an operator can see what
    # bound was exceeded.
    assert "1" in msg
    # The wrapped TimeoutExpired is preserved as __cause__ for tracebacks.
    assert isinstance(excinfo.value.__cause__, subprocess.TimeoutExpired)


def test_run_cmd_cwd_is_honored() -> None:
    """The legacy ``cwd=`` kwarg keeps working — every existing call site
    uses it, so a regression here breaks the whole git tooling surface."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        repo = pathlib.Path(tmp) / "subdir"
        repo.mkdir()
        (repo / "marker.txt").write_text("marker\n")
        out = run_cmd(["ls"], cwd=repo)
        assert "marker.txt" in out


def test_run_cmd_legacy_positional_signature_unchanged() -> None:
    """Every existing caller passes positional ``cmd`` plus optional ``cwd=``.
    After the fix ``timeout`` and ``check`` are keyword-only, so the positional
    signature is byte-compatible."""
    # Positional (cmd) only.
    assert run_cmd(["printf", "%s", "abc"]) == "abc"
    # Positional (cmd, cwd).
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = run_cmd(["pwd"], pathlib.Path(tmp))
        assert out == tmp


def test_run_cmd_keyword_only_kwargs_rejected_positionally() -> None:
    """``timeout`` and ``check`` are keyword-only — passing them positionally
    must raise TypeError, never silently bind to a future parameter."""
    with pytest.raises(TypeError):
        run_cmd(["echo", "x"], None, 1.0)  # type: ignore[misc]
    with pytest.raises(TypeError):
        run_cmd(["echo", "x"], None, False)  # type: ignore[misc]


def test_worktree_status_snapshot_path_no_longer_returns_unavailable(
    tmp_path: pathlib.Path,
) -> None:
    """(e) Regression for ibl-local-67f046589043 — light integration check.

    Re-creates the EXACT call pattern at
    ``ouroboros/tools/registry.py::3503`` against a real tmp git repo:

        run_cmd(["git", "status", "--porcelain"], cwd=repo, timeout=20)

    Before the fix, ``run_cmd`` raised ``TypeError`` on the unknown kwarg and
    the surrounding bare-except returned ``"<status-unavailable>"``. After
    the fix this returns the real porcelain text — empty string for a clean
    repo. The assertion explicitly excludes the swallow sentinel so a future
    regression that re-introduces the TypeError or a similar guard would be
    caught here.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(repo),
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(repo),
        check=True,
    )
    (repo / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo),
        check=True,
        env={"LC_ALL": "C", "LANG": "C", "PATH": subprocess.os.environ.get("PATH", "")},
    )

    # The exact call pattern from registry.py line 3503 — cwd=, timeout=.
    result = run_cmd(["git", "status", "--porcelain"], cwd=repo, timeout=20)
    assert result != "<status-unavailable>"
    # Clean repo -> empty porcelain output (the snapshot path needs an empty
    # string to compare with the prior snapshot, not a swallow sentinel).
    assert result == ""
