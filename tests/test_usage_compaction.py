"""CPL4-C6 pins: seq-preserving compaction of the monetary usage ledger.

Design contract: docs/v7next/DESIGN_USAGE_COMPACTION.md. The invariants
pinned here are monetary-authority invariants (owner sanction 1A):

1. decimal-exact money before/after; the production projections render EQUAL;
2. in-flight (unsettled) rows never fold and stay transitionable;
3. crash between archive write and ledger swap leaves the ledger byte-identical;
4. budget enforcement sees the same numbers across compaction;
5. every pre-compaction attempt_id stays resolvable (live ∪ archive) — the
   CPL-5 reverse-sweep join surface — across chained compactions, with
   tamper-evident segments;
6. idempotent kinds (subscription/external/legacy) are never folded, so their
   replay dedup keeps working;
7. trigger policy: config SSOT threshold, thrash guard, verify-abort = no-op;
8. baseline rows are legal only as the leading block.
"""
from __future__ import annotations

import decimal
import errno
import hashlib
import json
import os
import pathlib
import shutil
import stat
import time
from decimal import Decimal

import pytest

from ouroboros import platform_layer
from ouroboros import usage_accounting as ua
from ouroboros import usage_compaction as uc
from ouroboros.usage_ledger import UsageLedgerCorrupt, _validate_records


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setenv("OUROBOROS_DATA_DIR", str(root))
    monkeypatch.setenv("OUROBOROS_SETTINGS_PATH", str(root / "settings.json"))
    monkeypatch.setenv("TOTAL_BUDGET", "100")
    (root / "state").mkdir(parents=True)
    (root / ua.IMPORT_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / ua.IMPORT_REL).write_text(
        json.dumps({"completed": True}), encoding="utf-8"
    )
    return root


def _request(data_root, **overrides):
    values = {
        "model": "openai/gpt-5.2",
        "provider": "openai",
        "reservation_usd": 1.0,
        "drive_root": data_root,
        "task_id": "child",
        "root_task_id": "root",
        "source": "test",
    }
    values.update(overrides)
    return ua.AttemptRequest(**values)


def _ledger_lines(data_root):
    path = data_root / ua.LEDGER_REL
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _ledger_rows(data_root):
    return [json.loads(line) for line in _ledger_lines(data_root)]


def _settle(data_root, *, cost=None, usage=None, cost_final=False, **request_overrides):
    reservation = ua.reserve_attempt(_request(data_root, **request_overrides))
    ua.mark_dispatched(reservation)
    ua.settle_attempt(reservation, usage or {"prompt_tokens": 10, "completion_tokens": 5},
                      cost_usd=cost, cost_final=cost_final)
    return reservation


def _seed_mixed_ledger(data_root):
    """A realistic ledger: settled (weird floats, unknown costs), unresolved,
    released, in-flight, sessions, external, review-attributed."""
    _settle(data_root, cost=0.123456789012345, cost_final=True)
    _settle(data_root, cost=1.1, task_id="t2", root_task_id="root2",
            root_limit_usd=50.0)
    _settle(data_root, cost=2.2, task_id="t2", root_task_id="root2",
            root_limit_usd=40.0)
    _settle(data_root, cost=None, usage={}, model="openai/gpt-5.2-mini")
    reservation = ua.reserve_attempt(_request(data_root, task_id="t3"))
    ua.mark_dispatched(reservation)
    ua.mark_unresolved(reservation, "provider went dark")
    reservation = ua.reserve_attempt(_request(data_root, task_id="t4"))
    ua.release_attempt(reservation, "not_dispatched")
    ua.record_subscription_session(
        "sess-1", drive_root=data_root, route="claudexor:claude", model="fable",
        task_id="t5", root_task_id="root", spend_usd=0.5, reset_at="2026-09-02T00:00:00Z",
    )
    ua.record_unmetered_external_dispatch(
        "ext-1", drive_root=data_root, model="ext-model", task_id="t6",
        prompt_tokens=7, completion_tokens=3,
    )
    with ua.usage_scope(ua.UsageScope(
        drive_root=data_root, task_id="rv", root_task_id="root",
        review_skill="skill-x", review_wave_id="w1", review_slot_id="s1",
    )):
        _settle(data_root, cost=3.5, cost_final=True)
    # In-flight chains that MUST survive: one reserved, one dispatched.
    reserved = ua.reserve_attempt(_request(data_root, task_id="open-r"))
    dispatched = ua.reserve_attempt(_request(data_root, task_id="open-d"))
    ua.mark_dispatched(dispatched)
    return reserved, dispatched


def _decimal_money(rows):
    """Exact decimal (cost, bound) totals over FINAL rows, strings included.

    Summed under an INDEPENDENT wide context — wider than the compactor's own
    — so this oracle stays an oracle: a helper that rounds the same way the
    code under test rounds cannot see the code under test round."""
    finals = {}
    for row in rows:
        finals[str(row.get("attempt_id"))] = row
    with decimal.localcontext() as context:
        context.prec = 200
        cost = Decimal(0)
        bound = Decimal(0)
        for row in finals.values():
            if str(row.get("kind") or "") == "usage_baseline":
                continue
            value = row.get("cost_usd")
            if value is not None and str(row.get("state") or "") == "settled":
                cost += Decimal(str(value))
            upper = row.get("reservation_upper_bound_usd")
            if upper is not None:
                bound += Decimal(str(upper))
    return cost, bound


def _compact(data_root):
    with ua._locked(data_root) as heartbeat:
        return uc.compact_usage_ledger_locked(data_root, heartbeat=heartbeat)


def _lock_path(data_root):
    return data_root / "state" / "usage_attempts.lock"


def _lock_is_held(data_root):
    """Whether the monetary lock is held RIGHT NOW by someone else."""
    path = _lock_path(data_root)
    if not path.exists():
        return False
    fd = platform_layer.acquire_exclusive_file_lock(
        path, timeout_sec=0.05, stale_sec=3600.0, poll_sec=0.01,
        owner_aware_stale=True,
    )
    if fd is None:
        return True
    platform_layer.release_exclusive_file_lock(path, fd)
    return False


def _rewrite_header(data_root, header):
    """Replace the live ledger's leading row (tamper simulation)."""
    path = data_root / ua.LEDGER_REL
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = json.dumps(header, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    uc._SEGMENT_CACHE.clear()
    uc._CHAIN_UNION_CACHE.clear()


def _append_raw_row(data_root, row):
    """Append one already-legal row straight to the live ledger bytes."""
    path = data_root / ua.LEDGER_REL
    row = {**row, "seq": len(_ledger_lines(data_root)) + 1}
    with path.open("ab") as handle:
        handle.write((json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
    return row


def _projection_snapshot(data_root):
    return (
        ua.usage_projection(data_root),
        ua.usage_projection(data_root, root_task_id="root"),
        ua.usage_projection(data_root, root_task_id="root2"),
        ua.usage_breakdown(data_root),
        ua.usage_breakdown(data_root, root_task_id="root"),
        ua.usage_breakdown(data_root, task_id="t2"),
    )


# --- 1 + 4: monetary exactness and budget equality ---------------------------

def test_compaction_preserves_money_and_projections_exactly(data_root):
    _seed_mixed_ledger(data_root)
    before_rows = _ledger_rows(data_root)
    before_money = _decimal_money(before_rows)
    before_projection = _projection_snapshot(data_root)
    before_review = ua.skill_review_usage(
        data_root, review_skill="skill-x", review_wave_id="w1")

    receipt = _compact(data_root)
    assert receipt is not None
    assert receipt["folded_row_count"] > 0

    after_rows = _ledger_rows(data_root)
    assert len(after_rows) < len(before_rows)
    assert _decimal_money(after_rows) == before_money
    assert _projection_snapshot(data_root) == before_projection
    # Review-attributed attempts are retained: the per-attempt wave projection
    # is unchanged, attempt ids included.
    after_review = ua.skill_review_usage(
        data_root, review_skill="skill-x", review_wave_id="w1")
    assert after_review == before_review
    assert after_review["attempt_ids"]

    # Baseline block structure: one header first, groups after, dense seq.
    header = after_rows[0]
    assert header["kind"] == "usage_baseline"
    assert header["seq"] == 1
    assert header["source_sha256"]
    assert [row["seq"] for row in after_rows] == list(range(1, len(after_rows) + 1))
    group_kinds = {row["kind"] for row in after_rows[1:]}
    assert "usage_baseline" not in group_kinds
    # Money on group rows is carried as exact-decimal strings.
    groups = [row for row in after_rows if row["kind"] == "usage_baseline_group"]
    assert groups
    assert all(isinstance(row.get("cost_usd"), str)
               for row in groups if row.get("cost_usd") is not None)


def test_group_sums_survive_beyond_the_default_decimal_precision(data_root, monkeypatch):
    """10**28 + 1 is 29 digits: the ambient 28-digit context loses the 1."""
    monkeypatch.setenv("TOTAL_BUDGET", "1e40")
    exact = Decimal("10000000000000000000000000001")
    _settle(data_root, cost=1e28, cost_final=True)
    _settle(data_root, cost=1.0, cost_final=True)
    before = _decimal_money(_ledger_rows(data_root))
    assert before[0] == exact
    assert _compact(data_root) is not None
    rows = _ledger_rows(data_root)
    groups = [row for row in rows if row["kind"] == "usage_baseline_group"]
    assert len(groups) == 1
    assert Decimal(groups[0]["cost_usd"]) == exact
    assert _decimal_money(rows) == before


def test_budget_enforcement_sees_identical_numbers(data_root, monkeypatch):
    monkeypatch.setenv("TOTAL_BUDGET", "10")
    _settle(data_root, cost=4.0, cost_final=True, reservation_usd=4.0)
    _settle(data_root, cost=4.0, cost_final=True, reservation_usd=4.0)
    before = ua.usage_projection(data_root)
    assert _compact(data_root) is not None
    assert ua.usage_projection(data_root) == before
    # Remaining ≈ 2: a 1.5 reservation fits, a 3.0 reservation does not —
    # exactly as before compaction.
    reservation = ua.reserve_attempt(_request(data_root, reservation_usd=1.5))
    ua.release_attempt(reservation, "probe")
    with pytest.raises(ua.BudgetExceeded):
        ua.reserve_attempt(_request(data_root, reservation_usd=3.0))


def test_root_budget_enforcement_survives_compaction(data_root):
    _settle(data_root, cost=8.0, cost_final=True, reservation_usd=8.0,
            task_id="rt", root_task_id="rooted", root_limit_usd=10.0)
    assert _compact(data_root) is not None
    with pytest.raises(ua.BudgetExceeded) as excinfo:
        ua.reserve_attempt(_request(
            data_root, reservation_usd=5.0, task_id="rt2",
            root_task_id="rooted", root_limit_usd=10.0,
        ))
    assert excinfo.value.limit_scope == "root"
    projection = ua.usage_projection(data_root, root_task_id="rooted")
    assert projection["limit_usd"] == 10.0
    assert projection["settled_usd"] == 8.0


# --- 2: in-flight rows never fold -------------------------------------------

def test_unsettled_rows_survive_and_stay_transitionable(data_root):
    reserved, dispatched = _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    states = {
        str(row.get("attempt_id")): str(row.get("state"))
        for row in _ledger_rows(data_root)
    }
    assert states[reserved.attempt_id] == "reserved"
    assert states[dispatched.attempt_id] == "dispatched"
    # Their lifecycle continues over the compacted file.
    ua.settle_attempt(dispatched, {"prompt_tokens": 3, "completion_tokens": 1},
                      cost_usd=0.25, cost_final=True)
    ua.release_attempt(reserved, "not_dispatched")
    finals = {
        str(row.get("attempt_id")): str(row.get("state"))
        for row in _ledger_rows(data_root)
    }
    assert finals[dispatched.attempt_id] == "settled"
    assert finals[reserved.attempt_id] == "released"


# --- 3: crash-safety ---------------------------------------------------------

def test_crash_at_the_ledger_rename_leaves_ledger_intact(data_root, monkeypatch):
    _seed_mixed_ledger(data_root)
    ledger_path = data_root / ua.LEDGER_REL
    archive_dir = data_root / "archive" / "usage_ledger"
    before_bytes = ledger_path.read_bytes()
    real_replace = os.replace
    observed: dict = {}

    def crashing_replace(src, dst):
        if pathlib.Path(dst) != ledger_path:
            return real_replace(src, dst)
        # The power cut lands ON the rename itself. By contract the archive
        # segment is already durable at this instant and holds the exact source
        # bytes: a swap that happened first would find no segment here.
        observed["segments"] = [
            path.read_bytes() for path in sorted(archive_dir.glob("segment_*.jsonl"))
        ]
        raise OSError(errno.EIO, "injected power loss at the ledger rename")

    monkeypatch.setattr(os, "replace", crashing_replace)
    with ua._locked(data_root) as heartbeat:
        with pytest.raises(OSError):
            uc.compact_usage_ledger_locked(data_root, heartbeat=heartbeat)
    assert observed["segments"] == [before_bytes]
    assert ledger_path.read_bytes() == before_bytes
    _validate_records(_ledger_rows(data_root))
    assert not list(ledger_path.parent.glob(f".{ledger_path.name}.tmp.*"))
    # The orphaned archive segment is tolerated; the ledger keeps working and a
    # retry (without the injection) compacts.
    monkeypatch.setattr(os, "replace", real_replace)
    _settle(data_root, cost=0.5, cost_final=True)
    assert _compact(data_root) is not None


def test_archive_directory_chain_is_durable_before_the_swap(data_root, monkeypatch):
    _seed_mixed_ledger(data_root)
    archive_dir = data_root / "archive" / "usage_ledger"
    real_fsync = os.fsync
    synced: list = []
    swapped: list = []

    def recording_fsync(fd):
        try:
            info = os.fstat(fd)
            synced.append((info.st_dev, info.st_ino))
        except OSError:  # pragma: no cover - fstat on a live fd
            pass
        return real_fsync(fd)

    real_swap = uc._swap_ledger_fsync

    def watched_swap(*args):
        swapped.append(len(synced))
        return real_swap(*args)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(uc, "_swap_ledger_fsync", watched_swap)
    assert _compact(data_root) is not None
    assert swapped, "the swap never ran"
    before_swap = set(synced[: swapped[0]])
    # Every directory whose entry the archive chain created must be durable
    # BEFORE the live ledger is replaced — not just the segment's own parent.
    for directory in (archive_dir, archive_dir.parent, data_root):
        info = directory.stat()
        assert (info.st_dev, info.st_ino) in before_swap, directory


def test_posix_directory_fsync_failure_aborts_before_the_swap(data_root, monkeypatch):
    if platform_layer.IS_WINDOWS:  # pragma: no cover - platform predicate
        pytest.skip("directory fsync is a disclosed no-op on Windows")
    _seed_mixed_ledger(data_root)
    ledger_path = data_root / ua.LEDGER_REL
    before_bytes = ledger_path.read_bytes()
    real_fsync = os.fsync

    def failing_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "injected directory fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with ua._locked(data_root) as heartbeat:
        with pytest.raises(OSError):
            uc.compact_usage_ledger_locked(data_root, heartbeat=heartbeat)
    assert ledger_path.read_bytes() == before_bytes


def test_the_directory_chain_is_re_synced_on_the_retry_after_a_failed_pass(data_root, monkeypatch):
    """A pass that died on the directory fsync leaves the directories PRESENT
    but not durable; the retry must sync them again rather than skip them for
    existing, or a crash after its swap loses the archive the swap relies on."""
    _seed_mixed_ledger(data_root)
    archive_dir = data_root / "archive" / "usage_ledger"
    real_fsync = os.fsync

    def failing_dir_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "injected directory fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing_dir_fsync)
    with ua._locked(data_root) as heartbeat:
        with pytest.raises(OSError):
            uc.compact_usage_ledger_locked(data_root, heartbeat=heartbeat)
    assert archive_dir.is_dir()  # present after the failure, durability unknown

    synced: list = []
    swapped: list = []

    def recording_fsync(fd):
        info = os.fstat(fd)
        synced.append((info.st_dev, info.st_ino))
        return real_fsync(fd)

    real_swap = uc._swap_ledger_fsync

    def watched_swap(*args):
        swapped.append(len(synced))
        return real_swap(*args)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(uc, "_swap_ledger_fsync", watched_swap)
    assert _compact(data_root) is not None
    assert swapped, "the swap never ran"
    before_swap = set(synced[: swapped[0]])
    for directory in (archive_dir, archive_dir.parent, data_root):
        info = directory.stat()
        assert (info.st_dev, info.st_ino) in before_swap, directory



# --- 1b: the lock the pass runs under ----------------------------------------

def test_monetary_lock_is_owner_aware_and_the_pass_heartbeats_it(data_root, monkeypatch):
    _seed_mixed_ledger(data_root)
    requested: dict = {}
    real_acquire = platform_layer.acquire_exclusive_file_lock

    def spy(path, **kwargs):
        requested.update(kwargs)
        return real_acquire(path, **kwargs)

    monkeypatch.setattr(platform_layer, "acquire_exclusive_file_lock", spy)
    lock_path = _lock_path(data_root)
    with ua._locked(data_root) as heartbeat:
        # A LIVE owner is never evicted on elapsed time alone: a stolen monetary
        # lock means two writers rewriting the same authority.
        assert requested.get("owner_aware_stale") is True
        # A pass that outlives the staleness window keeps its lockfile young
        # for acquirers that judge by age only.
        os.utime(lock_path, (0.0, 0.0))
        assert uc.compact_usage_ledger_locked(data_root, heartbeat=heartbeat) is not None
        assert lock_path.stat().st_mtime > time.time() - 60


def test_append_between_snapshot_and_swap_aborts_instead_of_erasing_it(data_root, monkeypatch):
    _seed_mixed_ledger(data_root)
    before_money = _decimal_money(_ledger_rows(data_root))
    original_write = uc._write_new_file_fsync
    injected: dict = {}

    def racing_write(path, payload, root):
        # A writer that got the lock (age-broken lock, foreign repair) lands a
        # settled charge AFTER the compactor snapshotted the file.
        injected.update(_append_raw_row(data_root, {
            "kind": "subscription_session", "attempt_id": "sess-raced",
            "state": "settled", "ts": "2026-09-01T00:00:00+00:00",
            "cost_usd": 0.25, "cost_final": True, "model": "fable",
            "provider": "claudexor", "category": "task", "source": "subscription",
            "task_id": "t", "root_task_id": "root", "parent_task_id": "",
        }))
        original_write(path, payload, root)

    monkeypatch.setattr(uc, "_write_new_file_fsync", racing_write)
    assert _compact(data_root) is None  # refused the swap
    rows = _ledger_rows(data_root)
    assert injected["attempt_id"] in {str(row.get("attempt_id")) for row in rows}
    assert not any(row.get("kind") == "usage_baseline" for row in rows)
    _validate_records(rows)
    cost, bound = _decimal_money(rows)
    assert (cost, bound) == (before_money[0] + Decimal("0.25"), before_money[1])


def test_a_lost_lock_aborts_the_pass_instead_of_swapping(data_root):
    """A heartbeat is an OWNERSHIP verdict: losing it abandons the pass.

    A pass that keeps building after the lock left it would swap a snapshot
    over whatever the new owner appended in the meantime."""
    _seed_mixed_ledger(data_root)
    ledger_path = data_root / ua.LEDGER_REL
    before = ledger_path.read_bytes()
    for heartbeat in (
        lambda: False,                       # evicted / re-created under us
        lambda: (_ for _ in ()).throw(OSError("lock unreadable")),
    ):
        assert uc.compact_usage_ledger_locked(data_root, heartbeat=heartbeat) is None
        assert ledger_path.read_bytes() == before
    assert not (data_root / "archive" / "usage_ledger").exists()  # not even an orphan


def test_the_long_build_and_verification_section_beats_the_lock(data_root, monkeypatch):
    """The build/verify span is the long one; it must renew the hold WHILE it
    runs, not only at its edges."""
    _seed_mixed_ledger(data_root)
    beats: list = []
    seen: dict = {}
    real_build = uc._build_candidate

    def watched_build(*args, **kwargs):
        seen["entry"] = len(beats)
        result = real_build(*args, **kwargs)
        seen["exit"] = len(beats)
        return result

    monkeypatch.setattr(uc, "_build_candidate", watched_build)
    with ua._locked(data_root) as heartbeat:
        def counting():
            beats.append(1)
            return heartbeat()

        assert uc.compact_usage_ledger_locked(data_root, heartbeat=counting) is not None
    assert seen["exit"] > seen["entry"]   # the build itself renewed the hold
    assert len(beats) > seen["exit"]      # and so did the verification after it


def test_no_writer_can_append_between_the_snapshot_check_and_the_swap(data_root, monkeypatch):
    """The compare->replace window is closed by the lock, not by luck."""
    _seed_mixed_ledger(data_root)
    ledger_path = data_root / ua.LEDGER_REL
    real_replace = os.replace
    observed: dict = {}

    def probing_replace(src, dst):
        if pathlib.Path(dst) == ledger_path:
            # The legitimate append path IS this acquisition; at the instant of
            # the swap it must find the monetary lock held.
            observed["free"] = platform_layer.acquire_exclusive_file_lock(
                _lock_path(data_root), timeout_sec=0.2, stale_sec=3600.0,
                poll_sec=0.02, owner_aware_stale=True,
            )
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", probing_replace)
    assert _compact(data_root) is not None
    assert observed["free"] is None


def test_every_ledger_writer_refuses_when_the_lock_cannot_be_taken(data_root, monkeypatch):
    """No unlocked fallback: a lock that cannot be taken is a typed refusal.

    An append that gave up on the lock and wrote anyway is exactly the second
    writer the compaction snapshot cannot see."""
    _settle(data_root, cost=1.0, cost_final=True)
    reserved = ua.reserve_attempt(_request(data_root, task_id="probe"))
    ledger_path = data_root / ua.LEDGER_REL
    before = ledger_path.read_bytes()
    monkeypatch.setattr(
        platform_layer, "acquire_exclusive_file_lock", lambda *a, **k: None)
    for write in (
        lambda: ua.reserve_attempt(_request(data_root)),
        lambda: ua.mark_dispatched(reserved),
        lambda: ua.record_unmetered_external_dispatch(
            "ext-locked", drive_root=data_root, model="m", task_id="t"),
    ):
        with pytest.raises(ua.UsageAccountingError):
            write()
    assert ledger_path.read_bytes() == before


def test_a_swap_that_did_not_land_is_a_typed_failure_not_a_receipt(data_root, monkeypatch):
    """Post-verify: the receipt describes the bytes that are actually there."""
    _seed_mixed_ledger(data_root)
    ledger_path = data_root / ua.LEDGER_REL
    real_replace = os.replace

    def lying_replace(src, dst):
        real_replace(src, dst)
        if pathlib.Path(dst) == ledger_path:
            with open(dst, "ab") as handle:
                handle.write(b'{"kind":"attempt","attempt_id":"late"}\n')

    monkeypatch.setattr(os, "replace", lying_replace)
    with ua._locked(data_root) as heartbeat:
        with pytest.raises(UsageLedgerCorrupt):
            uc.compact_usage_ledger_locked(data_root, heartbeat=heartbeat)


def test_an_append_between_the_recheck_and_the_replace_aborts_without_loss(
    data_root, monkeypatch
):
    """The pre-swap re-check is not the last look: the live ledger is proven
    unchanged again INSIDE the swap, after the candidate bytes are durable and
    immediately before the rename — the last instant the replace can still be
    refused. A row that lands after the outer re-check therefore survives."""
    _seed_mixed_ledger(data_root)
    before_money = _decimal_money(_ledger_rows(data_root))
    real_check = uc._snapshot_intact
    injected: dict = {}
    calls: list = []

    def racing_check(path, raw):
        verdict = real_check(path, raw)
        calls.append(verdict)
        if len(calls) == 2 and verdict:
            # The charge lands AFTER the outer pre-swap re-check answered
            # "intact" and BEFORE the rename trusts that answer.
            injected.update(_append_raw_row(data_root, {
                "kind": "subscription_session", "attempt_id": "sess-last-instant",
                "state": "settled", "ts": "2026-09-01T00:00:00+00:00",
                "cost_usd": 0.25, "cost_final": True, "model": "fable",
                "provider": "claudexor", "category": "task", "source": "subscription",
                "task_id": "t", "root_task_id": "root", "parent_task_id": "",
            }))
        return verdict

    monkeypatch.setattr(uc, "_snapshot_intact", racing_check)
    assert _compact(data_root) is None  # the replace was refused
    rows = _ledger_rows(data_root)
    assert injected["attempt_id"] in {str(row.get("attempt_id")) for row in rows}
    assert not any(row.get("kind") == "usage_baseline" for row in rows)
    _validate_records(rows)
    ledger_path = data_root / ua.LEDGER_REL
    assert not list(ledger_path.parent.glob(f".{ledger_path.name}.tmp.*"))
    cost, bound = _decimal_money(rows)
    assert (cost, bound) == (before_money[0] + Decimal("0.25"), before_money[1])


def test_a_hold_lost_at_the_archive_is_seen_before_the_snapshot_is_trusted(
    data_root, monkeypatch
):
    """The pass proves ownership immediately BEFORE each snapshot look: a
    re-check answered while the lock already belongs to someone else is a
    meaningless answer, so the loss must abort before it is even asked."""
    _seed_mixed_ledger(data_root)
    ledger_path = data_root / ua.LEDGER_REL
    before = ledger_path.read_bytes()
    archive_dir = data_root / "archive" / "usage_ledger"
    checks: list = []
    real_check = uc._snapshot_intact

    def counting_check(path, raw):
        checks.append(1)
        return real_check(path, raw)

    monkeypatch.setattr(uc, "_snapshot_intact", counting_check)

    def heartbeat():
        # Ownership dies at the exact moment the archive segment lands.
        return not list(archive_dir.glob("segment_*.jsonl"))

    with ua._locked(data_root):
        assert uc.compact_usage_ledger_locked(data_root, heartbeat=heartbeat) is None
    assert ledger_path.read_bytes() == before
    assert len(checks) == 1  # the post-archive re-check never ran
    assert list(archive_dir.glob("segment_*.jsonl"))  # the orphan stays, disclosed


def test_a_hold_lost_after_the_recheck_aborts_before_the_swap(data_root, monkeypatch):
    """Ownership is proven once more between the re-check and the rename: a
    verdict that arrived while ours cannot license a replace that happens
    after the hold left us."""
    _seed_mixed_ledger(data_root)
    ledger_path = data_root / ua.LEDGER_REL
    before = ledger_path.read_bytes()
    state = {"checks": 0}
    real_check = uc._snapshot_intact

    def counting_check(path, raw):
        state["checks"] += 1
        return real_check(path, raw)

    monkeypatch.setattr(uc, "_snapshot_intact", counting_check)

    def heartbeat():
        # Ownership dies the moment the pre-swap re-check has answered.
        return state["checks"] < 2

    with ua._locked(data_root):
        assert uc.compact_usage_ledger_locked(data_root, heartbeat=heartbeat) is None
    assert ledger_path.read_bytes() == before  # no swap: byte-identical
    assert not any(row.get("kind") == "usage_baseline" for row in _ledger_rows(data_root))


def test_a_hold_lost_while_the_temp_is_written_refuses_the_replace(data_root):
    """Writing and fsyncing the candidate temp can take arbitrarily long, so
    an ownership proof taken before the swap began is stale by the rename.
    The proof therefore lives INSIDE the atomic writer — once the temp bytes
    are durable, immediately before the snapshot look and the rename — and it
    runs FIRST: a hold lost while the temp was written refuses the replace,
    and a charge the new holder appended survives byte-for-byte."""
    _seed_mixed_ledger(data_root)
    before_money = _decimal_money(_ledger_rows(data_root))
    ledger_path = data_root / ua.LEDGER_REL
    injected: dict = {}

    def heartbeat():
        # Ownership dies while the candidate temp is being written: the first
        # proof asked with the temp on disk answers False — and the new
        # holder's charge lands with it, exactly the row a rename would erase.
        if not list(ledger_path.parent.glob(f".{ledger_path.name}.tmp.*")):
            return True
        if not injected:
            injected.update(_append_raw_row(data_root, {
                "kind": "subscription_session", "attempt_id": "sess-new-holder",
                "state": "settled", "ts": "2026-09-01T00:00:00+00:00",
                "cost_usd": 0.25, "cost_final": True, "model": "fable",
                "provider": "claudexor", "category": "task", "source": "subscription",
                "task_id": "t", "root_task_id": "root", "parent_task_id": "",
            }))
        return False

    with ua._locked(data_root):
        assert uc.compact_usage_ledger_locked(data_root, heartbeat=heartbeat) is None
    rows = _ledger_rows(data_root)
    assert injected["attempt_id"] in {str(row.get("attempt_id")) for row in rows}
    assert not any(row.get("kind") == "usage_baseline" for row in rows)
    _validate_records(rows)
    assert not list(ledger_path.parent.glob(f".{ledger_path.name}.tmp.*"))
    cost, bound = _decimal_money(rows)
    assert (cost, bound) == (before_money[0] + Decimal("0.25"), before_money[1])



# --- 5: CPL-5 join surface ---------------------------------------------------

def test_every_attempt_id_stays_resolvable_across_chained_compactions(data_root):
    _seed_mixed_ledger(data_root)
    first_ids = {str(row["attempt_id"]) for row in _ledger_rows(data_root)}
    assert _compact(data_root) is not None
    # Second generation of traffic, second compaction.
    _settle(data_root, cost=0.75, cost_final=True, task_id="gen2")
    second_ids = {str(row["attempt_id"]) for row in _ledger_rows(data_root)}
    assert _compact(data_root) is not None

    live_ids = {str(row["attempt_id"]) for row in _ledger_rows(data_root)}
    archived = uc.archived_attempt_ids(data_root)
    for attempt_id in first_ids | second_ids:
        assert attempt_id in live_ids or attempt_id in archived
        assert uc.usage_attempt_recorded(data_root, attempt_id)
    assert not uc.usage_attempt_recorded(data_root, "never-recorded")


def test_tampered_archive_segment_is_detected(data_root):
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    header = _ledger_rows(data_root)[0]
    segment = data_root / header["archive_rel"]
    payload = segment.read_bytes()
    segment.write_bytes(payload.replace(b'"settled"', b'"sett1ed"', 1))
    with pytest.raises(UsageLedgerCorrupt):
        uc.archived_attempt_ids(data_root)


def test_rehashed_segment_still_fails_the_ledger_structure(data_root):
    """A tamperer who also repairs the hash still has to produce a LEDGER."""
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    header = _ledger_rows(data_root)[0]
    segment = data_root / header["archive_rel"]
    forged = segment.read_bytes().replace(b'"seq":2', b'"seq":9', 1)
    assert len(forged) == header["source_size_bytes"]
    segment.write_bytes(forged)
    _rewrite_header(data_root, {
        **header, "source_sha256": hashlib.sha256(forged).hexdigest(),
    })
    with pytest.raises(UsageLedgerCorrupt):
        uc.archived_attempt_ids(data_root)


def test_warm_segment_cache_revalidates_the_file_it_cached(data_root):
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    assert uc.archived_attempt_ids(data_root)  # warms the per-segment cache
    header = _ledger_rows(data_root)[0]
    segment = data_root / header["archive_rel"]
    segment.unlink()
    with pytest.raises(UsageLedgerCorrupt):
        uc.archived_attempt_ids(data_root)
    segment.write_bytes(b'{"kind":"attempt"}\n')
    with pytest.raises(UsageLedgerCorrupt):
        uc.archived_attempt_ids(data_root)


def _rewrite_segment_in_place(segment, marker):
    """Same size, same inode, same mtime: the fingerprint a rewrite can keep."""
    payload = segment.read_bytes()
    forged = payload.replace(b'"settled"', marker, 1)
    assert forged != payload and len(forged) == len(payload)
    info = os.stat(segment)
    with open(segment, "r+b") as handle:
        handle.write(forged)
    os.utime(segment, ns=(info.st_atime_ns, info.st_mtime_ns))


def test_a_rewrite_inside_the_timestamp_window_is_re_hashed_not_recalled(data_root):
    """Filesystem timestamps have granularity; a file touched about NOW cannot
    prove it is the file that was read, however well the fingerprint matches."""
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    header = _ledger_rows(data_root)[0]
    segment = data_root / header["archive_rel"]
    assert uc.archived_attempt_ids(data_root)  # warms the per-segment cache
    _rewrite_segment_in_place(segment, b'"sett1ed"')
    # Deterministically place the file INSIDE the settle window while keeping
    # the cache's fingerprint exact, so only the window can decide this read.
    entry = uc._SEGMENT_CACHE[str(segment)]
    os.utime(segment)
    info = os.stat(segment)
    uc._SEGMENT_CACHE[str(segment)] = entry[:3] + (
        (info.st_ino, info.st_dev, info.st_size, info.st_mtime_ns),
    ) + entry[4:]

    with pytest.raises(UsageLedgerCorrupt):
        uc.archived_attempt_ids(data_root)


def test_a_same_size_rewrite_is_caught_once_the_cache_entry_expires(data_root):
    """A verified read is evidence with a shelf life, not a standing answer."""
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    header = _ledger_rows(data_root)[0]
    segment = data_root / header["archive_rel"]
    settled = time.time() - 3600
    os.utime(segment, (settled, settled))  # well outside the settle window
    assert uc.archived_attempt_ids(data_root)
    _rewrite_segment_in_place(segment, b'"sett1ed"')
    entry = uc._SEGMENT_CACHE[str(segment)]
    uc._SEGMENT_CACHE[str(segment)] = entry[:4] + (entry[4] - 3600,)

    with pytest.raises(UsageLedgerCorrupt):
        uc.archived_attempt_ids(data_root)



# Everything the forger would carry over from the older genuine stamp — the
# MUTABLE epoch included, because a rollback that leaves the epoch behind is
# caught by the chain-step rule alone and proves nothing about this one.
_SOURCE_PROVENANCE_KEYS = (
    "archive_rel", "source_sha256", "source_size_bytes", "source_row_count",
    "source_first_seq", "source_last_seq", "folded_row_count", "retained_row_count",
    "compaction_epoch",
)


def _embedded_header(data_root, header):
    """The previous epoch's header, as embedded in the segment ``header`` names."""
    first = (data_root / header["archive_rel"]).read_text(
        encoding="utf-8").splitlines()[0]
    return json.loads(first)


def test_repointing_the_header_at_an_older_segment_is_corrupt(data_root):
    """Dropping the epochs between is a shortened chain, not a shorter history."""
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    for generation in ("gen2", "gen3"):
        _settle(data_root, cost=0.75, cost_final=True, task_id=generation)
        assert _compact(data_root) is not None
    header3 = _ledger_rows(data_root)[0]
    assert header3["compaction_epoch"] == 3
    everything = uc.archived_attempt_ids(data_root)
    # Each older header is embedded, verbatim and correctly hashed, as the
    # first row of the segment the newer one names — genuine references, all.
    header2 = _embedded_header(data_root, header3)
    header1 = _embedded_header(data_root, header2)
    assert (header2["compaction_epoch"], header1["compaction_epoch"]) == (2, 1)
    for skipped_to in (header2, header1):
        forged = {**header3, **{key: skipped_to[key] for key in _SOURCE_PROVENANCE_KEYS}}
        _rewrite_header(data_root, forged)
        _validate_records(_ledger_rows(data_root))  # structurally impeccable
        with pytest.raises(UsageLedgerCorrupt):
            uc.archived_attempt_ids(data_root)
    assert everything  # what the forgeries were trying to make disappear


def test_an_orphan_segment_of_the_live_generation_is_not_a_rollback(data_root, monkeypatch):
    """A pass that lost the snapshot race leaves a segment for an epoch that
    never committed. It holds THIS generation's bytes, so the archive still
    anchors the live stamp and the history stays readable."""
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    known = uc.archived_attempt_ids(data_root)
    original_write = uc._write_new_file_fsync

    def racing_write(path, payload, root):
        _append_raw_row(data_root, {
            "kind": "subscription_session", "attempt_id": "sess-orphaned",
            "state": "settled", "ts": "2026-09-01T00:00:00+00:00",
            "cost_usd": 0.25, "cost_final": True, "model": "fable",
            "provider": "claudexor", "category": "task", "source": "subscription",
            "task_id": "t", "root_task_id": "root", "parent_task_id": "",
        })
        original_write(path, payload, root)

    monkeypatch.setattr(uc, "_write_new_file_fsync", racing_write)
    _settle(data_root, cost=0.75, cost_final=True, task_id="gen2")
    assert _compact(data_root) is None  # refused the swap; the segment stays
    segments = sorted((data_root / "archive" / "usage_ledger").glob("*.jsonl"))
    assert len(segments) == 2  # one referenced, one orphan of THIS generation
    uc._SEGMENT_CACHE.clear()
    uc._CHAIN_UNION_CACHE.clear()
    assert uc.archived_attempt_ids(data_root) == known


def test_pre_compaction_seq_must_name_a_row_the_named_source_held(data_root):
    """The claim is provenance about an archived range, not a free number."""
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    rows = _ledger_rows(data_root)
    header = rows[0]
    carriers = [index for index, row in enumerate(rows) if "pre_compaction_seq" in row]
    assert carriers
    _validate_records([dict(row) for row in rows])  # control: the honest file
    forged = [dict(row) for row in rows]
    # Strictly increasing, so only the source range itself refuses it.
    forged[carriers[-1]]["pre_compaction_seq"] = header["source_last_seq"] + 1
    with pytest.raises(UsageLedgerCorrupt):
        _validate_records(forged)



def test_archive_reference_is_bounded_to_the_archive_directory(data_root):
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    rows = _ledger_rows(data_root)
    header = rows[0]
    for archive_rel in (
        "../../other.jsonl",
        "/etc/passwd",
        "archive/usage_ledger/../../other.jsonl",
        "archive/other/segment.jsonl",
        "archive\\usage_ledger\\segment.jsonl",
        "",
    ):
        with pytest.raises(UsageLedgerCorrupt):
            _validate_records([{**header, "archive_rel": archive_rel}, *rows[1:]])
    # And the reader refuses it even when the named file exists and hashes right.
    outside = data_root / "outside.jsonl"
    outside.write_bytes((data_root / header["archive_rel"]).read_bytes())
    _rewrite_header(data_root, {**header, "archive_rel": "../outside.jsonl"})
    with pytest.raises(UsageLedgerCorrupt):
        uc.archived_attempt_ids(data_root)


@pytest.mark.parametrize("level", ("archive", "archive/usage_ledger"))
def test_a_symlinked_archive_path_is_refused_by_writer_and_reader(data_root, tmp_path, level):
    """The archive directory must BE inside the data root, not a door to
    somewhere else: with a link at either level the segment and the directory
    resolve THROUGH the same link, so "next to the archive" proves nothing."""
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    ledger_path = data_root / ua.LEDGER_REL
    target = data_root / level
    elsewhere = tmp_path / "elsewhere"
    shutil.move(str(target), str(elsewhere))  # same segments, same hashes
    target.symlink_to(elsewhere, target_is_directory=True)
    uc._SEGMENT_CACHE.clear()
    uc._CHAIN_UNION_CACHE.clear()

    with pytest.raises(UsageLedgerCorrupt):
        uc.archived_attempt_ids(data_root)

    _settle(data_root, cost=0.5, cost_final=True, task_id="gen2")
    before = ledger_path.read_bytes()
    assert _compact(data_root) is None  # the writer refuses to feed it
    assert ledger_path.read_bytes() == before


def test_a_link_planted_after_the_writer_bound_check_cannot_receive_history(
    data_root, tmp_path, monkeypatch
):
    """The bound check and the write are not one instant: on POSIX the writer
    creates the segment through O_NOFOLLOW dir-fd handles, so a link swapped
    in AFTER the check passed still cannot receive monetary history."""
    if platform_layer.IS_WINDOWS:  # pragma: no cover - platform predicate
        pytest.skip("dir-fd anchoring is POSIX; Windows is a disclosed best effort")
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    _settle(data_root, cost=0.5, cost_final=True, task_id="gen2")
    ledger_path = data_root / ua.LEDGER_REL
    archive = data_root / "archive"
    elsewhere = tmp_path / "elsewhere"
    real_bound = uc._archive_dir_bounded

    def racing_bound(root):
        result = real_bound(root)
        if not archive.is_symlink():
            shutil.move(str(archive), str(elsewhere))
            archive.symlink_to(elsewhere, target_is_directory=True)
        return result

    monkeypatch.setattr(uc, "_archive_dir_bounded", racing_bound)
    before = ledger_path.read_bytes()

    assert _compact(data_root) is None  # the pass aborts at the write itself
    assert ledger_path.read_bytes() == before
    linked = list((elsewhere / "usage_ledger").glob("segment_*.jsonl"))
    assert len(linked) == 1  # only the pre-existing segment: nothing crossed the link


def test_a_link_planted_after_the_reader_bound_check_is_refused(
    data_root, tmp_path, monkeypatch
):
    """A byte-identical copy behind a planted link hashes perfectly — the
    only defense is that the read itself refuses to traverse a link, which
    the O_NOFOLLOW dir-fd open enforces at the open, not at an earlier look."""
    if platform_layer.IS_WINDOWS:  # pragma: no cover - platform predicate
        pytest.skip("dir-fd anchoring is POSIX; Windows is a disclosed best effort")
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    header = _ledger_rows(data_root)[0]
    segment = data_root / header["archive_rel"]
    copy = tmp_path / "copy.jsonl"
    copy.write_bytes(segment.read_bytes())  # identical bytes: the hash cannot object
    real_path = uc._segment_path

    def racing_path(root, rel):
        result = real_path(root, rel)
        if not segment.is_symlink():
            segment.unlink()
            segment.symlink_to(copy)
        return result

    monkeypatch.setattr(uc, "_segment_path", racing_path)
    uc._SEGMENT_CACHE.clear()
    uc._CHAIN_UNION_CACHE.clear()

    with pytest.raises(UsageLedgerCorrupt):
        uc.archived_attempt_ids(data_root)



def test_unreadable_leading_row_is_typed_corruption_not_absence(data_root):
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    folded = sorted(uc.archived_attempt_ids(data_root))
    assert folded
    path = data_root / ua.LEDGER_REL
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(['{"kind": "usage_baseline"'] + lines[1:]) + "\n",
                    encoding="utf-8")
    uc._SEGMENT_CACHE.clear()
    uc._CHAIN_UNION_CACHE.clear()
    with pytest.raises(UsageLedgerCorrupt):
        uc.archived_attempt_ids(data_root)
    # The CPL-5 join must reach UNKNOWN, never "no attempt row" (orphan seal).
    with pytest.raises(UsageLedgerCorrupt):
        uc.usage_attempt_recorded(data_root, folded[0], live_ids=set())


def test_archived_id_union_is_built_once_per_chain(data_root, monkeypatch):
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    _settle(data_root, cost=0.25, cost_final=True, task_id="gen2")
    assert _compact(data_root) is not None
    folded = sorted(uc.archived_attempt_ids(data_root))
    assert len(folded) > 4
    builds: list = []
    original = uc._union_segment_ids

    def counting(segment_ids):
        builds.append(len(segment_ids))
        return original(segment_ids)

    monkeypatch.setattr(uc, "_union_segment_ids", counting)
    uc._CHAIN_UNION_CACHE.clear()
    # A reverse sweep asks once per seal; the union over the chain is chain
    # work, not per-question work.
    for attempt_id in folded:
        assert uc.usage_attempt_recorded(data_root, attempt_id, live_ids=set())
    assert builds == [2]


# --- 6: idempotent kinds never fold ------------------------------------------

def test_subscription_replay_still_dedups_after_compaction(data_root):
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    before = len(_ledger_rows(data_root))
    attempt_id = ua.record_subscription_session(
        "sess-1", drive_root=data_root, route="claudexor:claude", model="fable",
        task_id="t5", root_task_id="root", spend_usd=0.5, reset_at="2026-09-02T00:00:00Z",
    )
    assert attempt_id.startswith("session-")
    assert len(_ledger_rows(data_root)) == before  # no duplicate row
    with pytest.raises(ua.UsageAccountingError):
        ua.record_subscription_session(
            "sess-1", drive_root=data_root, route="other-route", model="fable",
            task_id="t5", root_task_id="root", spend_usd=0.5,
        )
    before = len(_ledger_rows(data_root))
    ua.record_unmetered_external_dispatch(
        "ext-1", drive_root=data_root, model="ext-model", task_id="t6",
        prompt_tokens=7, completion_tokens=3,
    )
    assert len(_ledger_rows(data_root)) == before


def test_legacy_import_rows_are_retained_and_reimport_dedups(data_root):
    events = data_root / "logs" / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(json.dumps({
        "type": "llm_usage", "model": "m", "provider": "openai", "cost": 0.7,
        "prompt_tokens": 5, "completion_tokens": 2, "task_id": "lt",
    }) + "\n", encoding="utf-8")
    (data_root / ua.IMPORT_REL).unlink()
    ua.ensure_legacy_imported(data_root)
    legacy_ids = {
        str(row["attempt_id"]) for row in _ledger_rows(data_root)
        if str(row.get("kind", "")).startswith("legacy_")
    }
    assert legacy_ids
    _settle(data_root, cost=0.9, cost_final=True)
    assert _compact(data_root) is not None
    live_ids = {str(row["attempt_id"]) for row in _ledger_rows(data_root)}
    assert legacy_ids <= live_ids  # never folded
    # Watermark loss: the resumable import replays against the LIVE ledger and
    # appends nothing new.
    before = len(_ledger_rows(data_root))
    (data_root / ua.IMPORT_REL).unlink()
    result = ua.ensure_legacy_imported(data_root)
    assert result["rows_appended"] == 0
    assert len(_ledger_rows(data_root)) == before


# --- 7: trigger policy -------------------------------------------------------

def test_reserve_path_compacts_only_past_config_threshold(data_root, monkeypatch):
    _seed_mixed_ledger(data_root)
    # The pass rewrites the whole monetary authority, so it is correct ONLY
    # while the ledger lock is held: prove the hold at the moment of the call
    # rather than trusting where the call site sits.
    holds: list = []
    original = uc.compact_usage_ledger_locked

    def observing(root, **kwargs):
        holds.append(_lock_is_held(data_root))
        return original(root, **kwargs)

    monkeypatch.setattr(uc, "compact_usage_ledger_locked", observing)
    size = (data_root / ua.LEDGER_REL).stat().st_size
    monkeypatch.setattr("ouroboros.config.USAGE_LEDGER_COMPACT_BYTES", size * 10)
    uc._COMPACT_ATTEMPTS.clear()
    _settle(data_root, cost=0.1, cost_final=True)
    assert not any(row.get("kind") == "usage_baseline" for row in _ledger_rows(data_root))
    assert holds == []  # below threshold: the stat fast-path never enters the pass
    monkeypatch.setattr("ouroboros.config.USAGE_LEDGER_COMPACT_BYTES", 1)
    _settle(data_root, cost=0.1, cost_final=True)
    assert any(row.get("kind") == "usage_baseline" for row in _ledger_rows(data_root))
    assert holds == [True]


def test_unprofitable_pass_is_throttled(data_root, monkeypatch):
    # Only in-flight rows: nothing foldable, compaction must not thrash.
    for task in ("a", "b"):
        reservation = ua.reserve_attempt(_request(data_root, task_id=task))
        ua.mark_dispatched(reservation)
    monkeypatch.setattr("ouroboros.config.USAGE_LEDGER_COMPACT_BYTES", 1)
    monkeypatch.setattr(
        "ouroboros.config.USAGE_LEDGER_COMPACT_RETRY_GROWTH_BYTES", 10_000_000)
    uc._COMPACT_ATTEMPTS.clear()
    calls = []
    original = uc.compact_usage_ledger_locked

    def counting(root, **kwargs):
        calls.append(1)
        return original(root, **kwargs)

    monkeypatch.setattr(uc, "compact_usage_ledger_locked", counting)
    before = _ledger_lines(data_root)
    with ua._locked(data_root):
        assert uc.maybe_compact_usage_ledger_locked(data_root) is False
    assert _ledger_lines(data_root) == before  # nothing foldable -> no-op
    with ua._locked(data_root):
        assert uc.maybe_compact_usage_ledger_locked(data_root) is False
    assert len(calls) == 1  # second call throttled by the growth guard


def test_verify_abort_on_foreign_noncanonical_literal(data_root):
    _settle(data_root, cost=1.0, cost_final=True)
    ua.record_subscription_session(
        "sess-nc", drive_root=data_root, route="claudexor:claude", model="fable",
        task_id="t", root_task_id="root", spend_usd=0.5,
    )
    path = data_root / ua.LEDGER_REL
    lines = _ledger_lines(data_root)
    # A RETAINED row (subscription kind never folds) whose monetary literal is
    # NOT double-round-trippable: a foreign writer's long literal. Its value
    # survives as a double, but the exact decimal cannot be re-serialized, so
    # the pass must abort and leave the ledger untouched. (Folded rows are
    # immune by construction: their decimals are carried as exact strings.)
    doctored = lines[-1].replace(
        '"cost_usd":0.5', '"cost_usd":0.50000000000000002775557561563')
    assert doctored != lines[-1]
    path.write_text("\n".join(lines[:-1] + [doctored]) + "\n", encoding="utf-8")
    before_bytes = path.read_bytes()
    assert _compact(data_root) is None
    assert path.read_bytes() == before_bytes


# --- 8: structural validation ------------------------------------------------

def test_baseline_rows_are_rejected_outside_the_leading_block(data_root):
    _settle(data_root, cost=1.0, cost_final=True)
    rows = _ledger_rows(data_root)
    smuggled = {
        "kind": "usage_baseline_group", "attempt_id": "baseline-x-g0001",
        "state": "settled", "seq": len(rows) + 1, "ts": "2026-09-01T00:00:00Z",
        "baseline_id": "x", "folded_attempt_count": 2, "model": "m",
        "provider": "p", "category": "task", "source": "llm", "task_id": "t",
        "root_task_id": "t", "parent_task_id": "", "cost_usd": "1.0",
        "cost_final": True,
    }
    with pytest.raises(UsageLedgerCorrupt):
        _validate_records([*rows, smuggled])


def test_a_group_row_cannot_rejoin_the_block_after_it_closed(data_root):
    """The money-injection shape: a real group row, real ``baseline_id``, later."""
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    rows = _ledger_rows(data_root)
    template = next(row for row in rows if row.get("kind") == "usage_baseline_group")
    smuggled = {
        **template,
        "attempt_id": f"{template['attempt_id']}-dup",
        "seq": len(rows) + 1,
    }
    with pytest.raises(UsageLedgerCorrupt):
        _validate_records([*rows, smuggled])


def test_baseline_header_is_rejected_by_POSITION_not_by_shape(data_root):
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    rows = _ledger_rows(data_root)
    header = rows[0]
    groups = [row for row in rows if row.get("kind") == "usage_baseline_group"]
    assert groups
    # Control: this exact, unmodified block IS legal at the head of a file.
    _validate_records([header, *groups])
    # The SAME rows, changed in nothing but where they sit, are corrupt.
    displaced = [
        {"kind": "attempt", "attempt_id": "displacer", "state": "reserved",
         "seq": 1, "ts": header["ts"]},
        {**header, "seq": 2},
        *[{**group, "seq": 3 + offset} for offset, group in enumerate(groups)],
    ]
    with pytest.raises(UsageLedgerCorrupt):
        _validate_records(displaced)


def test_baseline_header_counts_must_close(data_root):
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    rows = _ledger_rows(data_root)
    header = rows[0]
    _validate_records(rows)
    for field, value in (
        ("compaction_epoch", 0),
        ("source_first_seq", 2),
        ("source_last_seq", header["source_last_seq"] + 1),
        ("folded_row_count", header["folded_row_count"] + 1),
        ("retained_row_count", header["retained_row_count"] + 1),
        ("group_count", header["group_count"] + 1),
        ("folded_attempt_count", header["folded_attempt_count"] + 1),
        ("source_sha256", "not-a-digest"),
        ("source_size_bytes", 0),
    ):
        with pytest.raises(UsageLedgerCorrupt):
            _validate_records([{**header, field: value}, *rows[1:]])


def test_pre_compaction_seq_is_a_checked_provenance_claim(data_root):
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    rows = _ledger_rows(data_root)
    carriers = [index for index, row in enumerate(rows) if "pre_compaction_seq" in row]
    assert len(carriers) >= 2
    duplicated = [dict(row) for row in rows]
    duplicated[carriers[1]]["pre_compaction_seq"] = rows[carriers[0]]["pre_compaction_seq"]
    with pytest.raises(UsageLedgerCorrupt):
        _validate_records(duplicated)
    # Claiming a folded epoch on a file that carries no baseline stamp is a
    # forged provenance; the same rows WITHOUT the claim are ordinary history.
    orphaned = [
        dict(row) for row in rows
        if row.get("kind") not in {"usage_baseline", "usage_baseline_group"}
    ]
    for index, row in enumerate(orphaned, start=1):
        row["seq"] = index
    with pytest.raises(UsageLedgerCorrupt):
        _validate_records(orphaned)
    _validate_records([
        {key: value for key, value in row.items() if key != "pre_compaction_seq"}
        for row in orphaned
    ])


def test_group_rows_require_a_leading_header(data_root):
    group = {
        "kind": "usage_baseline_group", "attempt_id": "baseline-x-g0001",
        "state": "settled", "seq": 1, "baseline_id": "x",
        "folded_attempt_count": 1, "cost_usd": "1.0", "cost_final": True,
    }
    with pytest.raises(UsageLedgerCorrupt):
        _validate_records([group])


def test_compacted_ledger_revalidates_and_quarantine_semantics_hold(data_root):
    _seed_mixed_ledger(data_root)
    assert _compact(data_root) is not None
    rows = _ledger_rows(data_root)
    _validate_records(rows)  # full-file validation accepts the baseline block
    # Torn tail on the compacted file still quarantines exactly as before.
    path = data_root / ua.LEDGER_REL
    with path.open("ab") as handle:
        handle.write(b'{"torn": ')
    projection = ua.usage_projection(data_root)
    assert projection["integrity_degraded"] is True
    assert (data_root / ua.QUARANTINE_REL).is_file()


def test_archive_segment_holds_exact_source_bytes(data_root):
    _seed_mixed_ledger(data_root)
    source = (data_root / ua.LEDGER_REL).read_bytes()
    receipt = _compact(data_root)
    segment = data_root / receipt["archive_rel"]
    payload = segment.read_bytes()
    assert payload == source
    assert hashlib.sha256(payload).hexdigest() == receipt["source_sha256"]
    header = _ledger_rows(data_root)[0]
    assert header["archive_rel"] == receipt["archive_rel"]
    assert header["source_sha256"] == receipt["source_sha256"]
    # Retained rows carry their pre-compaction seq for provenance.
    retained = [row for row in _ledger_rows(data_root)
                if row["kind"] not in {"usage_baseline", "usage_baseline_group"}]
    assert retained
    assert all(isinstance(row.get("pre_compaction_seq"), int) for row in retained)
