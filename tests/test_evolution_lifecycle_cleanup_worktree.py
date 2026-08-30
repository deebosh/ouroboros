"""Regression tests for ibl-local-391e0574e267 (part A — data-loss guard).

The bug: ``_cleanup_worktree_after_cycle`` runs ``git reset --hard base_head`` when
a cycle closes without absorption. If ``base_head..HEAD`` contains a commit landed
by ANOTHER task mid-cycle, that bystander commit is destroyed. The fix enumerates
the range and refuses to reset if any SHA is not attributable to the cycle.

Each test uses a real isolated git repo (no live-rewrite risk), constructs the
transaction dict by hand, and asserts on ``tx['cleanup_status']`` plus the
on-disk git state.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import supervisor.git_ops as git_ops
import supervisor.queue as queue
from supervisor.evolution_lifecycle import _cleanup_worktree_after_cycle


# --- helpers ---------------------------------------------------------------


def _run_git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in the isolated repo with a clean env.

    argv-list form (``["git", ...]``) — NEVER a single-element list with shell
    operators, which would be treated as a literal executable name.
    """
    env = {
        **os.environ,
        # Author/committer identity (avoid global git config bleed)
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        # Don't touch any user/system config (and don't honour any signing
        # setting that could try to invoke gpg).
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        # HOME is preserved so git's per-user lock files don't wander,
        # but we never read ~/.gitconfig because GIT_CONFIG_GLOBAL is /dev/null.
        "LC_ALL": "C",
        "LANG": "C",
    }
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )


def _make_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Initialise a fresh git repo at tmp_path/repo with a single initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "--initial-branch=main")
    _run_git(repo, "config", "commit.gpgsign", "false")
    _run_git(repo, "config", "tag.gpgsign", "false")
    (repo / "README.md").write_text("hello\n")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-m", "initial commit")
    return repo


def _head_sha(repo: pathlib.Path) -> str:
    return _run_git(repo, "rev-parse", "HEAD").stdout.strip()


def _commit_file(repo: pathlib.Path, name: str, content: str, msg: str) -> str:
    (repo / name).write_text(content)
    _run_git(repo, "add", name)
    _run_git(repo, "commit", "-m", msg)
    return _head_sha(repo)


def _bind_cleanup_test_repo(
    repo: pathlib.Path, monkeypatch, drive_root: pathlib.Path,
) -> dict:
    """Wire up the cleanup function's runtime expectations.

    Rebinds git_ops.REPO_DIR, mocks the update lock (no real lock file),
    clears queue.RUNNING, and stubs repo_writer_admission_closed to return
    "" (i.e. no admission closure). Returns a dict of the patched handles
    so individual tests can override specific behaviours.
    """
    from contextlib import contextmanager

    monkeypatch.setattr(git_ops, "REPO_DIR", repo.resolve(strict=False))
    monkeypatch.setattr(git_ops, "DRIVE_ROOT", drive_root)

    # Mock the update lock: acquire returns a sentinel fh; release no-ops.
    sentinel_fh = object()
    monkeypatch.setattr(
        "supervisor.evolution_lifecycle.acquire_update_lock",
        lambda: sentinel_fh,
        raising=False,
    )
    monkeypatch.setattr(
        "supervisor.evolution_lifecycle.release_update_lock",
        lambda fh: None,
        raising=False,
    )

    # No other tasks running in the shared worktree.
    monkeypatch.setattr(queue, "RUNNING", {})

    # Repo writer admission is closed iff something returns a truthy reason;
    # stub it to always return "" (admission open).
    monkeypatch.setattr(
        "supervisor.evolution_lifecycle.repo_writer_admission_closed",
        lambda: "",
        raising=False,
    )

    return {
        "sentinel_fh": sentinel_fh,
    }


def _make_tx(base_head: str, *, commit_sha: str = "", transaction_id: str = "tx-test") -> dict:
    """Construct a minimal transaction dict the cleanup function accepts."""
    return {
        "transaction_id": transaction_id,
        "task_id": "task-test",
        "base_head": base_head,
        "base_branch": "ouroboros",
        "commit_sha": commit_sha,
        "cleanup_status": "pending",
    }


# --- tests -----------------------------------------------------------------


def test_reset_happens_when_only_own_commit_in_range(tmp_path, monkeypatch):
    """(a) ``base_head..HEAD`` contains only ``tx['commit_sha']`` -> reset happens.

    Setup: base = initial commit. Cycle makes its own commit on top (tx['commit_sha']
    matches HEAD). No foreign commits. Cleanup MUST hard-reset to base_head and
    record cleanup_status=reset_to_base + a leftover branch for recovery.
    """
    repo = _make_repo(tmp_path)
    drive = tmp_path / "drive"
    drive.mkdir()
    handles = _bind_cleanup_test_repo(repo, monkeypatch, drive)

    base_head = _head_sha(repo)
    own_sha = _commit_file(repo, "own.txt", "own cycle change\n", "cycle own commit")
    assert _head_sha(repo) == own_sha
    tx = _make_tx(base_head, commit_sha=own_sha)

    _cleanup_worktree_after_cycle(tx, "task-test")

    assert tx["cleanup_status"] == "reset_to_base", (
        f"expected reset_to_base, got {tx['cleanup_status']!r} "
        f"(foreign={tx.get('cleanup_foreign_commits')})"
    )
    assert tx.get("cleanup_preserved_ref", "").startswith("evolution-leftover-")
    # Hard-reset actually moved HEAD back to base.
    assert _head_sha(repo) == base_head


def test_reset_skipped_when_foreign_commits_in_range(tmp_path, monkeypatch):
    """(b) range contains a foreign SHA -> NO reset.

    Setup: base = initial. Cycle's own commit lands. Then a FOREIGN task lands a
    commit on top. Cleanup MUST refuse the reset, preserve HEAD, record
    skipped_foreign_commits_in_range + the foreign SHAs, AND still stash any
    dirty working-tree files. HEAD stays exactly where it was.
    """
    repo = _make_repo(tmp_path)
    drive = tmp_path / "drive"
    drive.mkdir()
    _bind_cleanup_test_repo(repo, monkeypatch, drive)

    base_head = _head_sha(repo)
    own_sha = _commit_file(repo, "own.txt", "own cycle change\n", "cycle own commit")
    foreign_sha = _commit_file(
        repo, "foreign.txt", "another task's work\n", "foreign task commit",
    )
    head_before = _head_sha(repo)
    assert head_before == foreign_sha

    # Add a dirty file to verify the stash path still runs.
    (repo / "dirty.txt").write_text("uncommitted cycle scratch\n")

    tx = _make_tx(base_head, commit_sha=own_sha)

    _cleanup_worktree_after_cycle(tx, "task-test")

    assert tx["cleanup_status"] == "skipped_foreign_commits_in_range", (
        f"expected skipped_foreign_commits_in_range, got {tx['cleanup_status']!r}"
    )
    assert tx.get("cleanup_preserved_ref", "").startswith("evolution-leftover-")
    assert foreign_sha in tx.get("cleanup_foreign_commits", []), (
        f"foreign SHA {foreign_sha} not in {tx.get('cleanup_foreign_commits')}"
    )
    assert tx.get("cleanup_stash", "").startswith("evolution-cycle-cleanup-"), (
        f"expected dirty stash to run; got {tx.get('cleanup_stash')!r}"
    )
    # HEAD UNCHANGED — bystander commits still present on the live branch.
    assert _head_sha(repo) == head_before, (
        f"HEAD moved: expected {head_before}, got {_head_sha(repo)}"
    )
    # Bystander commit's file is still in the working tree.
    assert (repo / "foreign.txt").exists()
    assert (repo / "foreign.txt").read_text() == "another task's work\n"
    # Stash exists (we can list it).
    stash_list = _run_git(repo, "stash", "list").stdout
    assert tx["cleanup_stash"] in stash_list, f"stash label not in git stash list:\n{stash_list}"


def test_already_clean_when_head_at_base(tmp_path, monkeypatch):
    """(c) HEAD == base_head (no dirty, no ahead) -> already_clean.

    No commits beyond base, no dirty files. Cleanup MUST short-circuit with
    cleanup_status=already_clean and never touch the preserve/reset machinery.
    """
    repo = _make_repo(tmp_path)
    drive = tmp_path / "drive"
    drive.mkdir()
    _bind_cleanup_test_repo(repo, monkeypatch, drive)

    base_head = _head_sha(repo)
    head_before = _head_sha(repo)
    tx = _make_tx(base_head, commit_sha="")

    _cleanup_worktree_after_cycle(tx, "task-test")

    assert tx["cleanup_status"] == "already_clean"
    assert _head_sha(repo) == head_before
    assert tx.get("cleanup_preserved_ref", "") == ""
    assert tx.get("cleanup_stash", "") == ""
    assert tx.get("cleanup_foreign_commits", "") == ""


def test_foreign_commits_with_no_own_commit_sha(tmp_path, monkeypatch):
    """No_op/abandoned cycles have commit_sha=''; a foreign commit still triggers the skip.

    When the cycle never produced a commit, ``tx['commit_sha']`` is empty. The
    enumeration must still treat every SHA in ``base_head..HEAD`` as foreign and
    refuse the reset — exactly the case where the original bug destroyed a
    bystander on 2026-08-28 (cycle 1c7911c4b4f9 over bbae5ece261a2a61).
    """
    repo = _make_repo(tmp_path)
    drive = tmp_path / "drive"
    drive.mkdir()
    _bind_cleanup_test_repo(repo, monkeypatch, drive)

    base_head = _head_sha(repo)
    foreign_sha = _commit_file(repo, "foreign.txt", "another task\n", "foreign commit")
    head_before = _head_sha(repo)

    tx = _make_tx(base_head, commit_sha="")  # no_op/abandoned cycle

    _cleanup_worktree_after_cycle(tx, "task-test")

    assert tx["cleanup_status"] == "skipped_foreign_commits_in_range"
    assert foreign_sha in tx.get("cleanup_foreign_commits", [])
    assert _head_sha(repo) == head_before