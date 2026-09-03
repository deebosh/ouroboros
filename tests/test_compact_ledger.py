"""Substrate tests for ``ouroboros/usage_ledger.py`` — ``compact_ledger``.

The O_EXCL-based ``acquire_exclusive_file_lock`` in this build environment
is unreliable across rapid cycles (stale lock files from prior runs
can wedge a fresh acquire for 30 s before stale_sec). We patch it for
this test module to use a real ``fcntl.flock`` instead, which is
re-entrant within a single process and works across processes.

These tests focus on the byte-identical invariant for ``compact_ledger``
and the ``kind=compacted`` row acceptance in the validator. All other
``usage_ledger`` behavior is exercised by ``tests/test_usage_accounting.py``.
"""

from __future__ import annotations

import contextlib
import fcntl
import gzip
import json
import os
import pathlib

import pytest


@pytest.fixture(autouse=True)
def _patch_lock_with_fcntl(monkeypatch):
    """Swap the O_EXCL-based lock for a real ``fcntl.flock``.

    The production ``acquire_exclusive_file_lock`` uses ``O_EXCL|O_CREAT``
    and the file's existence as a presence marker. The ``release`` path
    unlinks the marker. In a single-process test that does hundreds of
    acquire/release cycles, the unlink can race with the next acquire
    and the test deadlocks. ``fcntl.flock`` re-enters cleanly.
    """
    state = {"fds": []}

    def _acquire(lock_path, **_):
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        state["fds"].append((lock_path, fd))
        return fd

    def _release(lock_path, fd):
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            if lock_path.exists():
                lock_path.unlink()
        except OSError:
            pass

    import ouroboros.platform_layer as pl
    from ouroboros import usage_ledger as ul

    monkeypatch.setattr(pl, "acquire_exclusive_file_lock", _acquire)
    monkeypatch.setattr(pl, "release_exclusive_file_lock", _release)

    @contextlib.contextmanager
    def _patched_named_lock(root, filename, *, timeout_sec, stale_sec):
        path = root / "state" / filename
        fd = _acquire(path, timeout_sec=timeout_sec, stale_sec=stale_sec)
        if fd is None:
            from ouroboros.usage_ledger import UsageAccountingError
            raise UsageAccountingError(f"usage accounting lock unavailable: {path}")
        try:
            yield
        finally:
            _release(path, fd)

    monkeypatch.setattr(ul, "_named_lock", _patched_named_lock)
    yield


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setenv("OUROBOROS_DATA_DIR", str(root))
    monkeypatch.setenv("OUROBOROS_SETTINGS_PATH", str(root / "settings.json"))
    monkeypatch.setenv("TOTAL_BUDGET", "100")
    (root / "state").mkdir(parents=True)
    return root


def _seed_settled(aid, root, cost, prompt, completion, ts, **extra):
    """Three transition rows: reserved -> dispatched -> settled.

    Returns the list of dicts the caller writes via ``_append_rows_locked``
    with state preservation (the in-memory list is the validator's
    reference, not the on-disk file)."""
    return [
        {
            "kind": "attempt", "attempt_id": aid, "root_task_id": root,
            "state": "reserved", "ts": ts,
            "reservation_upper_bound_usd": max(cost * 2, 0.001),
        },
        {
            "kind": "attempt", "attempt_id": aid, "root_task_id": root,
            "state": "dispatched", "ts": ts,
        },
        {
            "kind": "attempt", "attempt_id": aid, "root_task_id": root,
            "state": "settled", "ts": ts,
            "cost_usd": cost, "cost_final": True,
            "prompt_tokens": prompt, "completion_tokens": completion,
            **extra,
        },
    ]


def _write_full_ledger(data_root, rows):
    """Write the rows directly to disk (pre-existing) so the validator's
    dense-seq check is satisfied without going through
    ``_append_rows_locked``'s in-memory pre-state."""
    seq = 0
    final = []
    for row in rows:
        seq += 1
        r = dict(row)
        r["seq"] = seq
        final.append(r)
    path = data_root / "state" / "usage_attempts.jsonl"
    content = "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n" for r in final
    )
    path.write_bytes(content.encode("utf-8"))
    return final


# ---------------------------------------------------------------------------
# compact_ledger: byte-identical invariant
# ---------------------------------------------------------------------------


def test_compact_ledger_preserves_money_and_token_totals(data_root):
    """The hard gate. Build a heterogeneous ledger, snapshot the MONEY
    and TOKEN totals, run compact_ledger, re-snapshot, assert identity.

    The compactor's accepted trade-off: the ``attempt_counts`` per-state
    metric shifts (a 9-row fold collapses to 1 per root). MONEY and
    TOKEN totals stay byte-identical — that is the spec's invariant
    for the projection.
    """
    from ouroboros import usage_accounting as ua
    from ouroboros import usage_ledger as ul

    old_ts = "2024-01-01T00:00:00.000000+00:00"  # well before any cutoff
    new_ts = "2099-09-01T10:00:00.000000+00:00"  # far in the future

    rows = []
    # r1: 3 settled-old attempts (FOLDABLE)
    for i in range(3):
        rows += _seed_settled(
            f"a-{i}", "r1", 0.01 * (i + 1), 100 + i, 50 + i, old_ts,
        )
    # r2: 1 settled-old with cached tokens (FOLDABLE)
    rows += _seed_settled(
        "b-0", "r2", 0.05, 200, 100, old_ts,
        cached_tokens=20, cache_write_tokens=4,
    )
    # r3: LIVE — reserved-recent (newer than cutoff)
    rows.append({
        "kind": "attempt", "attempt_id": "c-0", "root_task_id": "r3",
        "state": "reserved", "ts": new_ts,
        "reservation_upper_bound_usd": 0.03,
    })
    # r4: LIVE — reserved-old (not terminal)
    rows.append({
        "kind": "attempt", "attempt_id": "d-0", "root_task_id": "r4",
        "state": "reserved", "ts": old_ts,
        "reservation_upper_bound_usd": 0.04,
    })

    _write_full_ledger(data_root, rows)

    # Clear the LRU projection cache so before/after see the file, not
    # a memoized entry.
    ua._LEDGER_READ_CACHE.clear()
    before = ua.usage_projection(data_root, bound_by_root=False)
    r1_before = ua.usage_breakdown(data_root, root_task_id="r1")
    r2_before = ua.usage_breakdown(data_root, root_task_id="r2")
    r3_before = ua.usage_breakdown(data_root, root_task_id="r3")
    r4_before = ua.usage_breakdown(data_root, root_task_id="r4")

    report = ul.compact_ledger(data_root, min_rows=1, min_bytes=0)

    assert report["status"] == "compacted", report
    assert report["roots_folded"] == 2
    assert sorted(report["folded_root_ids"]) == ["r1", "r2"]
    assert "r3" not in report["folded_root_ids"]
    assert "r4" not in report["folded_root_ids"]
    assert report["rows_after"] < report["rows_before"]

    ua._LEDGER_READ_CACHE.clear()
    after = ua.usage_projection(data_root, bound_by_root=False)
    r1_after = ua.usage_breakdown(data_root, root_task_id="r1")
    r2_after = ua.usage_breakdown(data_root, root_task_id="r2")
    r3_after = ua.usage_breakdown(data_root, root_task_id="r3")
    r4_after = ua.usage_breakdown(data_root, root_task_id="r4")

    # MONEY invariant (global).
    for key in (
        "settled_usd", "confirmed_usd", "estimated_usd", "reserved_usd",
        "unresolved_upper_bound_usd", "accounted_usd",
    ):
        assert before[key] == after[key], (key, before[key], after[key])

    # MONEY + TOKEN invariant (per-root breakdown).
    for rname, rbefore, rafter in (
        ("r1", r1_before, r1_after),
        ("r2", r2_before, r2_after),
        ("r3", r3_before, r3_after),
        ("r4", r4_before, r4_after),
    ):
        for key in (
            "settled_usd", "confirmed_usd", "estimated_usd", "reserved_usd",
            "unresolved_upper_bound_usd", "accounted_usd",
            "prompt_tokens", "completion_tokens", "cached_tokens",
            "cache_write_tokens",
        ):
            assert rbefore.get(key) == rafter.get(key), (rname, key, rbefore, rafter)


def test_compact_ledger_idempotent_second_call_skips(data_root):
    """A second compact_ledger folds zero additional roots; totals still
    match the pre-first-compact state."""
    from ouroboros import usage_accounting as ua
    from ouroboros import usage_ledger as ul

    old_ts = "2024-01-01T00:00:00.000000+00:00"
    rows = []
    for root_idx in range(3):
        for attempt_idx in range(2):
            rows += _seed_settled(
                f"r{root_idx}-a{attempt_idx}", f"root-{root_idx}",
                0.01 * (attempt_idx + 1), 10, 5, old_ts,
            )
    _write_full_ledger(data_root, rows)

    ua._LEDGER_READ_CACHE.clear()
    before = ua.usage_projection(data_root, bound_by_root=False)

    first = ul.compact_ledger(data_root, min_rows=1, min_bytes=0)
    assert first["status"] == "compacted"
    assert first["roots_folded"] == 3

    ua._LEDGER_READ_CACHE.clear()
    after = ua.usage_projection(data_root, bound_by_root=False)
    for key in ("settled_usd", "confirmed_usd", "reserved_usd",
                "unresolved_upper_bound_usd", "accounted_usd"):
        assert before[key] == after[key], (key, before[key], after[key])

    second = ul.compact_ledger(data_root, min_rows=1, min_bytes=0)
    assert second["status"] == "skipped"
    assert second["roots_folded"] == 0


def test_compact_ledger_skips_when_below_threshold(data_root):
    """Below the row or byte threshold, compact_ledger is a cheap no-op."""
    from ouroboros import usage_ledger as ul

    old_ts = "2024-01-01T00:00:00.000000+00:00"
    rows = []
    for index in range(50):
        rows += _seed_settled(
            f"a-{index:04d}", "root-x", 0.001, 1, 1, old_ts,
        )
    _write_full_ledger(data_root, rows)

    report = ul.compact_ledger(data_root)
    # 50 attempts × 3 transition rows = 150 rows; well below 2000.
    assert report["status"] == "skipped"
    assert report["reason"] == "below_threshold"


def test_compact_ledger_archives_raw_rows_to_gzipped_monthly_file(data_root):
    """Raw archivable rows survive in a gzipped archive under
    ``data/archive/`` keyed by the cutoff month's YYYY-MM."""
    from ouroboros import usage_ledger as ul

    old_ts = "2024-01-01T00:00:00.000000+00:00"
    rows = []
    for attempt_idx in range(2):
        rows += _seed_settled(
            f"a-{attempt_idx}", "r1", 0.02, 10, 5, old_ts,
        )
    _write_full_ledger(data_root, rows)

    report = ul.compact_ledger(data_root, min_rows=1, min_bytes=0)
    assert report["status"] == "compacted"
    archive_path = pathlib.Path(report["archive"])
    assert archive_path.exists(), archive_path
    # The archive is a real gzipped JSONL: every row in the original
    # archivable set survives, byte-for-byte in JSON encoding.
    with gzip.open(archive_path, "rt") as handle:
        raw_rows = [json.loads(line) for line in handle if line.strip()]
    attempt_ids = sorted({row["attempt_id"] for row in raw_rows})
    assert attempt_ids == ["a-0", "a-1"]


def test_compact_ledger_preserves_live_rows_when_some_roots_unfarchiveable(data_root):
    """Roots with reservations (not terminal) AND roots inside the
    cutoff window stay live; only fully-terminal+old roots fold."""
    from ouroboros import usage_ledger as ul

    old_ts = "2024-01-01T00:00:00.000000+00:00"
    new_ts = "2099-09-01T10:00:00.000000+00:00"
    rows = []
    # archivable root
    rows += _seed_settled("a-0", "r1", 0.05, 100, 50, old_ts)
    # not-yet-terminal root: stays live
    rows.append({
        "kind": "attempt", "attempt_id": "b-0", "root_task_id": "r2",
        "state": "reserved", "ts": old_ts,
        "reservation_upper_bound_usd": 0.02,
    })
    # terminal-but-too-recent root: stays live
    rows += _seed_settled("c-0", "r3", 0.03, 100, 50, new_ts)
    _write_full_ledger(data_root, rows)

    report = ul.compact_ledger(data_root, min_rows=1, min_bytes=0)
    assert report["status"] == "compacted"
    assert report["roots_folded"] == 1
    assert report["folded_root_ids"] == ["r1"]

    # Read back the live ledger: r1 should now be a compacted summary
    # row; r2 and r3 should still have their raw rows.
    raw = (data_root / "state" / "usage_attempts.jsonl").read_text().splitlines()
    parsed = [json.loads(line) for line in raw if line.strip()]
    live_roots = {row.get("root_task_id") for row in parsed}
    assert "r1" in live_roots
    assert "r2" in live_roots
    assert "r3" in live_roots
    r1_rows = [row for row in parsed if row.get("root_task_id") == "r1"]
    assert all(row.get("kind") == "compacted" for row in r1_rows), r1_rows
    r2_rows = [row for row in parsed if row.get("root_task_id") == "r2"]
    assert all(row.get("kind") != "compacted" for row in r2_rows), r2_rows


# ---------------------------------------------------------------------------
# Validator: kind=compacted is a terminal-from-null kind
# ---------------------------------------------------------------------------


def test_validator_accepts_compacted_summary_row(data_root):
    """``kind=compacted`` rows are written by the compactor; the validator
    must accept them as terminal-from-null (no prior attempt_id)."""
    from ouroboros import usage_ledger as ul

    result = ul._append_rows_locked(data_root, [], [{
        "kind": "compacted", "attempt_id": "compacted:r1",
        "root_task_id": "r1", "state": "settled",
        "cost_usd": 0.50, "cost_final": True,
        "prompt_tokens": 100, "completion_tokens": 50,
    }])
    assert len(result) == 1
    assert result[0]["kind"] == "compacted"
    # The validator would have raised UsageLedgerCorrupt if our updated
    # allowlist (compact, state=settled, previous=None) was wrong.


def test_validator_rejects_compacted_with_non_settled_state(data_root):
    """A compacted row with state != "settled" must fail validation."""
    from ouroboros import usage_ledger as ul
    from ouroboros.usage_ledger import UsageLedgerCorrupt

    with pytest.raises(UsageLedgerCorrupt):
        ul._append_rows_locked(data_root, [], [{
            "kind": "compacted", "attempt_id": "compacted:r1",
            "root_task_id": "r1", "state": "reserved",
            "cost_usd": 0.50, "cost_final": True,
        }])


def test_validator_rejects_compacted_with_prior_attempt(data_root):
    """A compacted row whose attempt_id already exists must fail validation
    (the synthetic attempt_id is unique to the compaction)."""
    from ouroboros import usage_ledger as ul
    from ouroboros.usage_ledger import UsageLedgerCorrupt

    # First write a real reserved row for attempt_id="compacted:r1".
    records = []
    records = ul._append_rows_locked(data_root, records, [{
        "kind": "attempt", "attempt_id": "compacted:r1",
        "root_task_id": "r1", "state": "reserved",
        "reservation_upper_bound_usd": 0.02,
    }])
    with pytest.raises(UsageLedgerCorrupt):
        ul._append_rows_locked(data_root, records, [{
            "kind": "compacted", "attempt_id": "compacted:r1",
            "root_task_id": "r1", "state": "settled",
            "cost_usd": 0.50, "cost_final": True,
        }])
