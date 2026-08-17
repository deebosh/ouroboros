"""Regression tests for the bypass-commit post-commit restart hook.

Closes ibl-7ac44b7cd6d8: a successful ``skip_advisory_review=True`` commit
must queue ``pending_restart_verify.json`` so the supervisor reloads on
next boot. The hook is a NO-OP for reviewed commits, evolution tasks,
failed pushes, and when a verify file already exists.

These tests follow DEVELOPMENT.md conventions:

* fixtures use ``tmp_path`` (never fixed paths),
* all git subprocess calls are stubbed via ``monkeypatch``,
* ``monkeypatch.setenv`` is used for env mutation (no bare ``os.environ[...]``).
"""

from __future__ import annotations

import json
import pathlib

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _read_verify(drive_root: pathlib.Path):
    pending = drive_root / "state" / "pending_restart_verify.json"
    if not pending.exists():
        return None
    return json.loads(pending.read_text(encoding="utf-8"))


def _import_hook():
    from ouroboros.tools.commit_post_commit import (
        maybe_request_restart_after_bypass,
    )

    return maybe_request_restart_after_bypass


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def drive_root(tmp_path):
    return tmp_path


@pytest.fixture
def repo_dir(tmp_path):
    return tmp_path / "repo"


@pytest.fixture
def stubbed_run_cmd(monkeypatch):
    """Stub ouroboros.tools.commit_post_commit.run_cmd.

    Tests that exercise the bypass path must stub git subprocess calls —
    tmp_path is not a git repo, and the hook's plan-grade branch probe
    is intentionally strict (a failing git call is a no-op, see plan §3.4).
    Returns a setter so tests can choose what `git` returns.
    """
    responses = {"git": "ouroboros\n"}

    def _stub(argv, cwd=None):  # noqa: ARG001 — signature parity with run_cmd
        for key, value in responses.items():
            if argv and argv[0] == key:
                return value
        raise AssertionError(f"unexpected run_cmd argv: {argv!r}")

    monkeypatch.setattr(
        "ouroboros.tools.commit_post_commit.run_cmd", _stub
    )
    return responses


# ---------------------------------------------------------------------------
# plan §4.1 — positive cases
# ---------------------------------------------------------------------------


class TestMaybeRequestRestartAfterBypass:
    """T1–T9 + N1–N2 from plan §4.1/§4.2."""

    def test_t1_bypass_push_succeeds_writes_pending(
        self, drive_root, repo_dir, stubbed_run_cmd
    ):
        """Bypass commit, push succeeded, no evolution, no pending — file written."""
        hook = _import_hook()
        notice = hook(
            repo_dir=repo_dir,
            drive_root=drive_root,
            task_type="task",
            skip_advisory_review=True,
            commit_sha="abc123def4567890",
            commit_message="fix: bypass commit",
            push_succeeded=True,
        )
        assert notice is not None
        assert "BYPASS_COMMIT_RESTART_QUEUED" in notice
        assert "abc123de" in notice
        data = _read_verify(drive_root)
        assert data is not None
        assert data["expected_sha"] == "abc123def4567890"
        assert data["source"] == "bypass_commit_post_hook_v1"
        assert "fix: bypass commit" in data["reason"]

    def test_t2_reviewed_commit_is_noop(self, drive_root, repo_dir):
        """skip_advisory_review=False — no-op, no file written."""
        hook = _import_hook()
        notice = hook(
            repo_dir=repo_dir,
            drive_root=drive_root,
            task_type="task",
            skip_advisory_review=False,
            commit_sha="abc123",
            commit_message="reviewed commit",
            push_succeeded=True,
        )
        assert notice is None
        assert _read_verify(drive_root) is None

    def test_t3_evolution_task_is_noop(self, drive_root, repo_dir):
        """task_type='evolution' — no double-trigger, no file written."""
        hook = _import_hook()
        notice = hook(
            repo_dir=repo_dir,
            drive_root=drive_root,
            task_type="evolution",
            skip_advisory_review=True,
            commit_sha="abc123",
            commit_message="evolution commit",
            push_succeeded=True,
        )
        assert notice is None
        assert _read_verify(drive_root) is None

    def test_t4_push_failed_is_noop(self, drive_root, repo_dir):
        """push_succeeded=False — never restart over an unpushed commit."""
        hook = _import_hook()
        notice = hook(
            repo_dir=repo_dir,
            drive_root=drive_root,
            task_type="task",
            skip_advisory_review=True,
            commit_sha="abc123",
            commit_message="bypass commit (push failed)",
            push_succeeded=False,
        )
        assert notice is None
        assert _read_verify(drive_root) is None

    def test_t5_pending_file_exists_is_noop(self, drive_root, repo_dir):
        """Existing pending_restart_verify.json — do not stack another on top."""
        hook = _import_hook()
        state_dir = drive_root / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        existing = state_dir / "pending_restart_verify.json"
        existing.write_text(
            json.dumps(
                {
                    "ts": "2026-08-17T18:00:00+00:00",
                    "expected_sha": "old_sha_value_xxxx",
                    "expected_branch": "ouroboros",
                    "reason": "preexisting",
                }
            )
        )
        notice = hook(
            repo_dir=repo_dir,
            drive_root=drive_root,
            task_type="task",
            skip_advisory_review=True,
            commit_sha="new_sha",
            commit_message="second bypass commit",
            push_succeeded=True,
        )
        assert notice is None
        data = _read_verify(drive_root)
        assert data is not None
        assert data["expected_sha"] == "old_sha_value_xxxx"  # unchanged

    def test_t6_branch_name_recorded(
        self, drive_root, repo_dir, stubbed_run_cmd
    ):
        """expected_branch in file matches git rev-parse --abbrev-ref HEAD."""
        stubbed_run_cmd["git"] = "ouroboros\n"
        hook = _import_hook()
        notice = hook(
            repo_dir=repo_dir,
            drive_root=drive_root,
            task_type="task",
            skip_advisory_review=True,
            commit_sha="def456",
            commit_message="bypass commit",
            push_succeeded=True,
        )
        assert notice is not None
        data = _read_verify(drive_root)
        assert data is not None
        assert data["expected_branch"] == "ouroboros"

    def test_t7_empty_commit_message(
        self, drive_root, repo_dir, stubbed_run_cmd
    ):
        """commit_message='' — reason field falls back to default text."""
        hook = _import_hook()
        notice = hook(
            repo_dir=repo_dir,
            drive_root=drive_root,
            task_type="task",
            skip_advisory_review=True,
            commit_sha="ghi789",
            commit_message="",
            push_succeeded=True,
        )
        assert notice is not None
        data = _read_verify(drive_root)
        assert data is not None
        assert (
            "auto-restart after skip_advisory_review=True bypass commit"
            in data["reason"]
        )

    def test_t8_wiring_smoke(self):
        """git.py imports the hook and exposes the call site.

        Full _repo_commit_push integration requires a real ToolContext +
        git repo; this is the lightweight version that confirms the import
        path resolves. End-to-end behavior is exercised by the unit tests
        above + manual smoke (see plan §4.4).
        """
        from ouroboros.tools import git as git_module

        assert hasattr(git_module, "_repo_commit_push")
        from ouroboros.tools.commit_post_commit import (
            maybe_request_restart_after_bypass,
        )

        assert callable(maybe_request_restart_after_bypass)

    def test_t9_long_commit_message_truncated_in_reason(
        self, drive_root, repo_dir, stubbed_run_cmd
    ):
        """commit_message of 500 chars — reason contains first 120 chars."""
        hook = _import_hook()
        long_msg = "x" * 500
        notice = hook(
            repo_dir=repo_dir,
            drive_root=drive_root,
            task_type="task",
            skip_advisory_review=True,
            commit_sha="jkl012",
            commit_message=long_msg,
            push_succeeded=True,
        )
        assert notice is not None
        data = _read_verify(drive_root)
        assert data is not None
        assert "x" * 120 in data["reason"]
        assert "x" * 121 not in data["reason"]

    # --- plan §4.2 — negative / mode / capability ----------------------------

    def test_n1_light_runtime_mode_still_queues(
        self, drive_root, repo_dir, monkeypatch, stubbed_run_cmd
    ):
        """runtime_mode='light' does NOT gate the bypass hook (owner-authorized)."""
        monkeypatch.setenv("OUROBOROS_RUNTIME_MODE", "light")
        hook = _import_hook()
        notice = hook(
            repo_dir=repo_dir,
            drive_root=drive_root,
            task_type="task",
            skip_advisory_review=True,
            commit_sha="mno345",
            commit_message="bypass in light",
            push_succeeded=True,
        )
        assert notice is not None
        assert _read_verify(drive_root) is not None

    def test_n2_read_only_subagent_still_queues(
        self, drive_root, repo_dir, stubbed_run_cmd
    ):
        """Read-only capability does NOT gate the bypass hook (bypass IS the auth).

        A read-only subagent cannot reach this code path anyway, but if it
        did the hook would still queue — bypassing the gate is itself an
        owner-authorized act.
        """
        hook = _import_hook()
        notice = hook(
            repo_dir=repo_dir,
            drive_root=drive_root,
            task_type="task",
            skip_advisory_review=True,
            commit_sha="pqr678",
            commit_message="bypass from hypothetical readonly",
            push_succeeded=True,
        )
        assert notice is not None
        assert _read_verify(drive_root) is not None


# ---------------------------------------------------------------------------
# robustness — git failures + filesystem edges
# ---------------------------------------------------------------------------


class TestHookRobustness:
    def test_returns_none_when_git_probe_fails(self, drive_root, repo_dir, monkeypatch):
        """Empty commit_sha + run_cmd raise — no restart queued (best-effort)."""
        hook = _import_hook()

        def _explode(argv, cwd=None):  # noqa: ARG001
            raise RuntimeError("git not available")

        monkeypatch.setattr(
            "ouroboros.tools.commit_post_commit.run_cmd", _explode
        )
        notice = hook(
            repo_dir=repo_dir,
            drive_root=drive_root,
            task_type="task",
            skip_advisory_review=True,
            commit_sha="",  # empty forces git probe
            commit_message="bypass",
            push_succeeded=True,
        )
        assert notice is None
        assert _read_verify(drive_root) is None

    def test_creates_state_dir_if_missing(
        self, drive_root, repo_dir, stubbed_run_cmd
    ):
        """Drive root has no state/ subdir — hook creates it on the fly."""
        hook = _import_hook()
        assert not (drive_root / "state").exists()
        hook(
            repo_dir=repo_dir,
            drive_root=drive_root,
            task_type="task",
            skip_advisory_review=True,
            commit_sha="stu901",
            commit_message="bypass",
            push_succeeded=True,
        )
        assert (drive_root / "state").exists()
        assert (drive_root / "state" / "pending_restart_verify.json").exists()