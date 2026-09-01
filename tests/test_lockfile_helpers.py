import os

from ouroboros import platform_layer
from ouroboros.platform_layer import (
    acquire_exclusive_file_lock,
    refresh_exclusive_file_lock,
    release_exclusive_file_lock,
    unlink_lockfile,
)


def _steal(lock_path, text="pid=1 ts=stolen\n"):
    """Replace the lock file the way an evictor + a second acquirer would."""
    lock_path.unlink()
    lock_path.write_text(text, encoding="utf-8")


def test_release_without_fd_does_not_unlink_existing_lock(tmp_path):
    lock_path = tmp_path / "state.lock"
    lock_path.write_text("owned elsewhere", encoding="utf-8")

    release_exclusive_file_lock(lock_path, None)

    assert lock_path.read_text(encoding="utf-8") == "owned elsewhere"


def test_release_with_fd_unlinks_owned_lock(tmp_path):
    lock_path = tmp_path / "state.lock"
    fd = acquire_exclusive_file_lock(lock_path, metadata="owned\n")
    assert fd is not None

    release_exclusive_file_lock(lock_path, fd)

    assert not lock_path.exists()


def test_path_only_git_lock_cleanup_remains_available(tmp_path):
    lock_path = tmp_path / "git.lock"
    fd = acquire_exclusive_file_lock(lock_path, metadata="owned\n")
    assert fd is not None
    os.close(fd)

    unlink_lockfile(lock_path)

    assert not lock_path.exists()


# --- Ownership: a lock is only ever OURS to renew or remove -------------------


def test_release_never_unlinks_a_lock_that_was_stolen(tmp_path):
    """A hold evicted as stale must not delete the new owner's lock on exit."""
    lock_path = tmp_path / "state.lock"
    fd = acquire_exclusive_file_lock(lock_path, metadata="pid=old ts=0\n")
    assert fd is not None
    _steal(lock_path)

    release_exclusive_file_lock(lock_path, fd)

    assert lock_path.read_text(encoding="utf-8") == "pid=1 ts=stolen\n"


def test_stale_eviction_never_removes_a_lock_re_created_under_it(tmp_path, monkeypatch):
    """Judge one file, unlink that same file — or none at all.

    Between the staleness judgement and the unlink the real owner may release
    and a third writer take the lock; removing THAT file would put two writers
    on one authority.
    """
    lock_path = tmp_path / "state.lock"
    lock_path.write_text("pid=424242 ts=0\n", encoding="utf-8")
    os.utime(lock_path, (0.0, 0.0))  # ancient: judged abandoned

    def racing_alive(pid):
        _steal(lock_path, "pid=999 ts=fresh\n")
        return False  # the judged owner really is gone

    monkeypatch.setattr(platform_layer, "pid_is_alive", racing_alive)
    fd = acquire_exclusive_file_lock(
        lock_path, timeout_sec=0.3, stale_sec=1.0, poll_sec=0.02,
        owner_aware_stale=True,
    )

    assert fd is None  # the lock was not ours to take
    assert lock_path.read_text(encoding="utf-8") == "pid=999 ts=fresh\n"


def test_stale_eviction_still_reclaims_a_genuinely_abandoned_lock(tmp_path, monkeypatch):
    lock_path = tmp_path / "state.lock"
    lock_path.write_text("pid=424242 ts=0\n", encoding="utf-8")
    os.utime(lock_path, (0.0, 0.0))
    monkeypatch.setattr(platform_layer, "pid_is_alive", lambda pid: False)

    fd = acquire_exclusive_file_lock(
        lock_path, timeout_sec=1.0, stale_sec=1.0, poll_sec=0.02,
        owner_aware_stale=True,
    )

    assert fd is not None
    release_exclusive_file_lock(lock_path, fd)


def test_heartbeat_reports_lost_ownership_instead_of_renewing(tmp_path):
    """The renewal is an OWNERSHIP statement: a stolen lock renews nothing."""
    lock_path = tmp_path / "state.lock"
    fd = acquire_exclusive_file_lock(lock_path, metadata="pid=old ts=0\n")
    assert fd is not None
    assert refresh_exclusive_file_lock(lock_path, fd) is True
    _steal(lock_path)

    assert refresh_exclusive_file_lock(lock_path, fd) is False

    os.close(fd)


def test_heartbeat_on_a_deleted_lock_reports_lost_ownership(tmp_path):
    lock_path = tmp_path / "state.lock"
    fd = acquire_exclusive_file_lock(lock_path, metadata="pid=old ts=0\n")
    assert fd is not None
    lock_path.unlink()

    assert refresh_exclusive_file_lock(lock_path, fd) is False

    os.close(fd)
