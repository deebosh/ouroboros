import contextlib
import errno
import os
import pathlib
import re
import threading
import time

import pytest

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


@pytest.mark.skipif(platform_layer.IS_WINDOWS, reason="kill(pid, 0) is the POSIX liveness probe")
def test_a_pid_that_refuses_our_signal_is_alive_and_its_lock_is_not_reclaimed(tmp_path, monkeypatch):
    """EPERM from ``kill(pid, 0)`` means the process EXISTS and merely refuses
    our signal — another user's process on a shared host, or a pid recycled
    onto one. Reading it as dead let the owner-aware stale rule evict such a
    lock by age; it is alive, so the lock stays (the disclosed recycled-pid
    wedge), and only ESRCH is a process provably gone."""
    def answering(code):
        def kill(pid, sig):
            raise OSError(code, "kill refused")
        return kill

    monkeypatch.setattr(os, "kill", answering(errno.EPERM))
    assert platform_layer.pid_is_alive(4242) is True and platform_layer.pid_provably_gone(4242) is False
    lock_path = tmp_path / "state.lock"
    lock_path.write_text("pid=4242 ts=0\n", encoding="utf-8")
    os.utime(lock_path, (0.0, 0.0))  # aged past any staleness window
    assert acquire_exclusive_file_lock(
        lock_path, timeout_sec=0.3, stale_sec=1.0, poll_sec=0.02, owner_aware_stale=True,
    ) is None
    assert lock_path.read_text(encoding="utf-8") == "pid=4242 ts=0\n"
    monkeypatch.setattr(os, "kill", answering(errno.ESRCH))
    assert platform_layer.pid_is_alive(4242) is False and platform_layer.pid_provably_gone(4242) is True


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


@pytest.mark.skipif(
    platform_layer.IS_WINDOWS,
    reason="the swap mechanism is POSIX: Windows cannot replace an open, "
    "LockFileEx-held lock file",
)
def test_heartbeat_after_an_atomic_swap_of_the_lock_reports_false(tmp_path):
    """A thief that REPLACES the lock file atomically never leaves the path
    absent, not even briefly — so any verdict weaker than an identity
    comparison (an existence check, a successful utime) would renew a hold
    that is no longer ours."""
    lock_path = tmp_path / "state.lock"
    fd = acquire_exclusive_file_lock(lock_path, metadata="pid=old ts=0\n")
    assert fd is not None
    assert refresh_exclusive_file_lock(lock_path, fd) is True
    imposter = tmp_path / "imposter.lock"
    imposter.write_text("pid=1 ts=swapped\n", encoding="utf-8")
    os.replace(imposter, lock_path)

    assert refresh_exclusive_file_lock(lock_path, fd) is False

    os.close(fd)
    assert lock_path.read_text(encoding="utf-8") == "pid=1 ts=swapped\n"


@pytest.mark.skipif(
    platform_layer.IS_WINDOWS,
    reason="flock-held eviction is POSIX; Windows keeps the disclosed "
    "re-check-then-unlink best effort",
)
def test_two_racing_reclaimers_never_yield_two_holders(tmp_path, monkeypatch):
    """Kernel-enforced eviction: judge, re-check and unlink happen under a
    held flock on the very fd that was judged, so of two reclaimers racing
    over one abandoned lock at most ONE may evict — the other either fails
    the non-blocking flock or fails the inode re-check.  Without the kernel
    lock, a pause between the inode re-check and the unlink lets the second
    reclaimer remove the first one's freshly won lock: two writers on one
    monetary authority."""
    lock_path = tmp_path / "state.lock"
    lock_path.write_text("pid=424242 ts=0\n", encoding="utf-8")
    os.utime(lock_path, (0.0, 0.0))  # ancient: judged abandoned by age
    monkeypatch.setattr(platform_layer, "pid_is_alive", lambda pid: False)
    stale_ident = platform_layer._lock_identity(lock_path)
    barrier = threading.Barrier(2)
    arrival_lock = threading.Lock()
    arrivals: list = []
    real_identity = platform_layer._lock_identity

    def pausing_identity(target):
        # The pause lands between the inode re-check (this read of the PATH's
        # identity, still naming the judged stale file) and the unlink that
        # trusts it.  Both reclaimers are herded into the window together;
        # the first to arrive then yields, so the second evicts and acquires
        # while the first still believes its own, now stale, re-check.
        result = real_identity(target)
        if not isinstance(target, int) and result and result[:2] == stale_ident[:2]:
            with contextlib.suppress(threading.BrokenBarrierError):
                barrier.wait(timeout=0.4)
            with arrival_lock:
                first = not arrivals
                arrivals.append(1)
            if first:
                time.sleep(0.15)
        return result

    monkeypatch.setattr(platform_layer, "_lock_identity", pausing_identity)
    results: list = [None, None]

    def reclaim(slot):
        results[slot] = acquire_exclusive_file_lock(
            lock_path, timeout_sec=2.0, stale_sec=1.0, poll_sec=0.01,
            owner_aware_stale=True,
        )

    threads = [threading.Thread(target=reclaim, args=(slot,)) for slot in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    holders = [fd for fd in results if fd is not None]
    assert len(holders) == 1, "two writers hold one monetary lock"
    assert lock_path.exists()
    release_exclusive_file_lock(lock_path, holders[0])


@pytest.mark.skipif(
    platform_layer.IS_WINDOWS,
    reason="flock-held eviction is POSIX; Windows cannot unlink a creator's open file",
)
def test_a_creator_evicted_while_lock_less_never_returns_a_descriptor(tmp_path, monkeypatch):
    """Between its O_EXCL create and its kernel lock a creator holds nothing an
    evictor must respect: stalled there past ``stale_sec`` (SIGSTOP, a suspend,
    clock skew) its fresh file is judged abandoned and evicted, and the lock it
    then takes lands on an inode the path no longer names. That is not a hold:
    the acquisition proves the path still names its descriptor and re-contends.
    Belt: the owner pid is written BEFORE the lock, so an owner-aware reclaimer
    never judges the live creator's file empty."""
    lock_path = tmp_path / "state.lock"
    monkeypatch.setattr(platform_layer, "_KERNEL_LOCK_TIER", {})
    assert platform_layer.kernel_file_locks_enforced(lock_path) is True  # decided before the hook
    real_flock = platform_layer.file_lock_exclusive_nb
    seen: dict = {}

    def stalled_creator_flock(fd):
        if not seen:  # the creator's own first kernel lock: it stalls here
            seen["metadata"] = lock_path.read_text(encoding="utf-8")
            seen["reclaimer"] = None
            os.utime(lock_path, (0.0, 0.0))  # the stall aged its lock-less file
            seen["reclaimer"] = acquire_exclusive_file_lock(  # an age-only reclaimer
                lock_path, timeout_sec=2.0, stale_sec=1.0, poll_sec=0.01,
            )
        return real_flock(fd)

    monkeypatch.setattr(platform_layer, "file_lock_exclusive_nb", stalled_creator_flock)
    creator = acquire_exclusive_file_lock(lock_path, timeout_sec=0.5, stale_sec=1.0, poll_sec=0.01)

    holders = [fd for fd in (creator, seen["reclaimer"]) if fd is not None]
    assert len(holders) == 1, "two descriptors believed to be one lock"
    assert refresh_exclusive_file_lock(lock_path, holders[0]) is True
    assert f"pid={os.getpid()}" in seen["metadata"]  # known to any owner-aware evictor
    release_exclusive_file_lock(lock_path, holders[0])


# --- Tiers: kernel-enforced or name-only, chosen by predicate, never by a refusal


def _tier(monkeypatch, enforced):
    monkeypatch.setattr(
        platform_layer, "kernel_file_locks_enforced", lambda path: enforced, raising=False,
    )


def _refusing(code):
    def refuse(fd):
        raise OSError(code, "injected kernel refusal")
    return refuse


def test_a_kernel_refusal_that_is_not_contention_fails_closed(tmp_path, monkeypatch):
    """On the enforced tier a descriptor the kernel would not lock is not a
    hold: the acquisition answers None promptly (not after the timeout) and
    removes the file it created, instead of degrading to the name protocol —
    where the round-3 reclaimer race lives again."""
    lock_path = tmp_path / "state.lock"
    _tier(monkeypatch, True)
    monkeypatch.setattr(platform_layer, "file_lock_exclusive_nb", _refusing(errno.ENOLCK))
    started = time.time()

    fd = acquire_exclusive_file_lock(lock_path, timeout_sec=5.0, poll_sec=0.01)

    assert fd is None
    assert time.time() - started < 2.0
    assert not lock_path.exists()


def test_a_stale_lock_is_never_evicted_without_the_kernel_hold(tmp_path, monkeypatch):
    """Eviction happens only under a held kernel lock on the judged fd: a
    refusal of that hold that is not contention leaves the stale file where
    it is and fails the acquisition closed — never unlink-by-name instead."""
    lock_path = tmp_path / "state.lock"
    lock_path.write_text("pid=424242 ts=0\n", encoding="utf-8")
    os.utime(lock_path, (0.0, 0.0))
    monkeypatch.setattr(platform_layer, "pid_is_alive", lambda pid: False)
    _tier(monkeypatch, True)
    monkeypatch.setattr(platform_layer, "file_lock_exclusive_nb", _refusing(errno.EIO))

    fd = acquire_exclusive_file_lock(
        lock_path, timeout_sec=1.0, stale_sec=1.0, poll_sec=0.01, owner_aware_stale=True,
    )

    assert fd is None
    assert lock_path.read_text(encoding="utf-8") == "pid=424242 ts=0\n"


def test_the_name_tier_is_chosen_by_the_predicate_not_by_a_refusal(tmp_path, monkeypatch):
    """Where the predicate says the filesystem takes no kernel locks, the name
    protocol runs alone and NO kernel call is attempted — so a refusal can
    never be what decides the tier. Abandoned locks are still reclaimed there
    by the disclosed re-check-then-unlink shape."""
    lock_path = tmp_path / "state.lock"
    _tier(monkeypatch, False)
    kernel_calls: list = []
    monkeypatch.setattr(platform_layer, "file_lock_exclusive_nb", kernel_calls.append)

    fd = acquire_exclusive_file_lock(lock_path, metadata="pid=old ts=0\n")
    assert fd is not None and kernel_calls == []
    assert refresh_exclusive_file_lock(lock_path, fd) is True
    release_exclusive_file_lock(lock_path, fd)
    assert not lock_path.exists()

    lock_path.write_text("pid=424242 ts=0\n", encoding="utf-8")
    os.utime(lock_path, (0.0, 0.0))
    monkeypatch.setattr(platform_layer, "pid_is_alive", lambda pid: False)
    fd = acquire_exclusive_file_lock(
        lock_path, timeout_sec=1.0, stale_sec=1.0, poll_sec=0.01, owner_aware_stale=True,
    )
    assert fd is not None and kernel_calls == []
    release_exclusive_file_lock(lock_path, fd)


def test_the_capability_probe_decides_once_and_leaves_no_residue(tmp_path, monkeypatch):
    """Only the kernel's own "this filesystem cannot" answer selects the name
    tier; any other refusal keeps the enforced tier (where a live acquisition
    fails closed). The verdict is memoized per directory and the probe file
    is gone afterwards."""
    monkeypatch.setattr(platform_layer, "_KERNEL_LOCK_TIER", {})
    assert platform_layer.kernel_file_locks_enforced(tmp_path / "real.lock") is True
    lockless = tmp_path / "lockless"
    lockless.mkdir()
    answers: list = []

    def probing(fd):
        answers.append(fd)
        raise OSError(errno.ENOLCK, "no locks available")

    monkeypatch.setattr(platform_layer, "file_lock_exclusive_nb", probing)
    assert platform_layer.kernel_file_locks_enforced(lockless / "a.lock") is False
    assert platform_layer.kernel_file_locks_enforced(lockless / "b.lock") is False
    assert len(answers) == 1  # one probe per directory
    assert list(lockless.iterdir()) == []

    refusing = tmp_path / "refusing"
    refusing.mkdir()
    monkeypatch.setattr(platform_layer, "file_lock_exclusive_nb", _refusing(errno.EIO))
    assert platform_layer.kernel_file_locks_enforced(refusing / "a.lock") is True
    assert list(refusing.iterdir()) == []


def test_lockfileex_refusals_classify_by_the_win32_error():
    """Only ERROR_LOCK_VIOLATION (33) means held by someone: it reads as the
    busy errno and re-contends. Access denied (5) and sharing violation (32)
    land on EACCES beside it on Windows, so EACCES cannot be in the busy set
    (POSIX flock never answers it). ERROR_INVALID_FUNCTION (1) and
    ERROR_NOT_SUPPORTED (50) — what a redirector answers when the volume takes
    no byte-range locks at all — must reach the UNSUPPORTED set, or the name
    tier is unreachable on Windows and a lock-less volume fails every monetary
    append closed instead of degrading to the disclosed name protocol. Every
    other error is in neither set: it fails the acquisition closed at once, not
    after the timeout. The classified codes carry their errno themselves — the
    4-argument OSError form derives errno FROM the winerror on Windows and
    ignores the one passed, and answers 0 here — so this arithmetic is the same
    on both platforms."""
    assert errno.EACCES not in platform_layer._LOCK_HELD_ERRNOS
    busy = platform_layer._win32_lock_error(33)
    assert busy.errno in platform_layer._LOCK_HELD_ERRNOS and busy.winerror == 33
    for err in (1, 50):
        unsupported = platform_layer._win32_lock_error(err)
        assert unsupported.errno in platform_layer._LOCK_UNSUPPORTED_ERRNOS and unsupported.winerror == err
    for err in (5, 32, 6):
        answered = platform_layer._win32_lock_error(err).errno
        assert answered not in platform_layer._LOCK_HELD_ERRNOS
        assert answered not in platform_layer._LOCK_UNSUPPORTED_ERRNOS


def test_the_design_note_names_the_exact_kernel_refusal_sets():
    """Round 5.2 corrected the busy set in the code, in its pin and in the
    review packet, and left the RATIFIED design note saying EACCES means
    contention — the negation of what that same pin asserts. A reader who
    implements the note re-opens the finding: a genuine access-denied would
    re-contend for the whole 45 s monetary timeout instead of failing closed.
    So the note names both sets and this compares them, member for member, by
    the numbers (EWOULDBLOCK and ENOTSUP are aliases on Linux, not everywhere)."""
    note = pathlib.Path(__file__).resolve().parents[1] / "docs" / "v7next" / "DESIGN_USAGE_COMPACTION.md"
    spelled = re.findall(r"are exactly ((?:`[A-Z]+`/)+`[A-Z]+`)", note.read_text(encoding="utf-8"))
    assert len(spelled) == 2, spelled
    unsupported, held = ({getattr(errno, name.strip("`")) for name in group.split("/")} for group in spelled)
    assert held == set(platform_layer._LOCK_HELD_ERRNOS)
    assert unsupported == set(platform_layer._LOCK_UNSUPPORTED_ERRNOS)


@pytest.mark.skipif(
    not platform_layer.IS_WINDOWS,
    reason="LockFileEx error classification is Windows mechanics",
)
def test_windows_lockfileex_contention_reads_as_busy(tmp_path):  # pragma: no cover - Windows only
    """A refused LockFileEx carries the errno the acquisition classifies: a
    lock violation is contention (stand down, re-contend); anything else
    fails closed. An errno-less OSError used to fall into the degrade."""
    path = tmp_path / "held.lock"
    first = os.open(str(path), os.O_CREAT | os.O_RDWR)
    second = os.open(str(path), os.O_RDWR)
    try:
        platform_layer._win32_lock(first, exclusive=True, blocking=False)
        with pytest.raises(OSError) as refused:
            platform_layer._win32_lock(second, exclusive=True, blocking=False)
        assert refused.value.errno in platform_layer._LOCK_HELD_ERRNOS
        assert refused.value.winerror == 33  # ERROR_LOCK_VIOLATION
    finally:
        platform_layer._win32_unlock(first)
        os.close(second)
        os.close(first)


@pytest.mark.skipif(
    platform_layer.IS_WINDOWS,
    reason="the unlink-under-the-creator shape is POSIX; Windows cannot unlink an open file",
)
def test_a_lock_whose_identity_cannot_be_read_is_never_a_hold(tmp_path, monkeypatch):
    """``_lock_identity`` answers ``()`` for a descriptor it cannot ``fstat`` —
    ESTALE/EIO on exactly the network filesystems this tier exists for. Two
    unreadable sides are not a match: comparing them raw makes ``() == ()``
    vacuously true and hands back a descriptor for an inode the path no longer
    names (a reclaimer unlinked it inside its own re-contend window), i.e. a
    second holder of one monetary lock. Unprovable is not owned: the
    acquisition answers None — and the file it stamped with its own LIVE pid
    goes with it, or no owner-aware reclaimer may ever remove it again."""
    lock_path = tmp_path / "state.lock"
    monkeypatch.setattr(platform_layer, "_KERNEL_LOCK_TIER", {})
    assert platform_layer.kernel_file_locks_enforced(lock_path) is True  # decided before the hooks
    real_flock = platform_layer.file_lock_exclusive_nb
    real_identity = platform_layer._lock_identity

    def blind_descriptor(target):  # our own fd answers nothing; the path still answers
        return () if isinstance(target, int) else real_identity(target)

    def evicting_flock(fd):  # a reclaimer removed our file between the create and the lock
        if lock_path.exists():
            os.unlink(str(lock_path))
        return real_flock(fd)

    monkeypatch.setattr(platform_layer, "_lock_identity", blind_descriptor)
    monkeypatch.setattr(platform_layer, "file_lock_exclusive_nb", evicting_flock)
    assert acquire_exclusive_file_lock(lock_path, timeout_sec=0.3, poll_sec=0.01) is None

    monkeypatch.setattr(platform_layer, "file_lock_exclusive_nb", real_flock)
    assert acquire_exclusive_file_lock(lock_path, timeout_sec=0.3, poll_sec=0.01) is None
    assert not lock_path.exists(), "a live pid was stamped on a lock nobody may reclaim"
