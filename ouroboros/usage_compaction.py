"""Seq-preserving compaction of the monetary usage ledger (CPL4-C6, owner 1A).

Design contract: docs/v7next/DESIGN_USAGE_COMPACTION.md. Terminal, non-review
``kind="attempt"`` chains fold into a stamped baseline block (one
``usage_baseline`` header + per-attribution ``usage_baseline_group`` rows);
the raw pre-compaction bytes move verbatim into an append-only
``archive/usage_ledger/`` segment referenced (and hash-pinned) by the header.
Nothing is deleted, in-flight rows never fold, idempotency-bearing kinds
(subscription/external/legacy) never fold, and the pass commits ONLY after
proving, on the candidate bytes, that the production aggregation renders
byte-equal results — otherwise it aborts and the ledger stays byte-identical.

Monetary exactness rule (fixed by the design note): group sums are computed as
exact ``Decimal``s of the literals stored in the file and carried on group
rows as exact-decimal JSON strings (``_number`` accepts them everywhere);
retained rows are verified decimal-identical across re-serialization, and any
non-round-trippable foreign literal aborts the pass instead of approximating.

This module sits BESIDE the substrate: it imports from ``usage_ledger`` and
``_usage_rows`` and is called INTO by ``usage_accounting.reserve_attempt``
under the held monetary lock; the substrate never imports it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import threading
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Optional, Tuple

from ouroboros._usage_rows import _breakdown_bucket, _summary
from ouroboros.usage_ledger import (
    ARCHIVE_SEGMENT_DIR_REL,
    LEDGER_REL,
    UsageLedgerCorrupt,
    _drive_root,
    _final_rows,
    _number,
    _read_records_locked,
    _validate_records,
    _write_bytes_atomic_fsync,
    valid_archive_rel,
)
from ouroboros.utils import append_jsonl, utc_now_iso

log = logging.getLogger(__name__)

# States a folded attempt chain may terminate in. In-flight (reserved/
# dispatched) finals keep their WHOLE chain in the live file.
_FOLDABLE_FINAL_STATES = frozenset({"settled", "unresolved", "released"})
_BASELINE_KINDS = frozenset({"usage_baseline", "usage_baseline_group"})
_REVIEW_KEYS = ("review_skill", "review_wave_id", "review_slot_id")
_TOKEN_SUM_FIELDS = (
    "prompt_tokens", "completion_tokens", "cached_tokens", "cache_write_tokens",
)

# Thrash guard: last attempted (st_ino, st_dev, st_size) per resolved root.
# Purely per-process; the worst cost of losing it is one extra bounded pass.
_COMPACT_ATTEMPTS: Dict[str, Tuple[int, int, int]] = {}
_COMPACT_ATTEMPTS_LOCK = threading.Lock()

# Immutable-segment cache for the history readers: abs path -> (expected
# sha256, frozen attempt-id set, embedded prior header, file fingerprint).
# The fingerprint is part of the hit condition, so a segment deleted or
# rewritten after a warm read re-verifies (and fails) instead of answering
# from memory.
_SEGMENT_CACHE: Dict[
    str, Tuple[str, frozenset, Optional[Dict[str, Any]], Tuple[int, int, int, int]]
] = {}

# The union over one WHOLE chain, keyed by the chain's identity ((archive_rel,
# sha) per hop). A reverse sweep asks the join primitive once per seal; the
# per-segment sets are cached, but re-unioning them per question is the work
# that made a bulk reconcile quadratic.
_CHAIN_UNION_CACHE: Dict[Tuple[Tuple[str, str], ...], frozenset] = {}


def _decimal_of(value: Any) -> Decimal:
    """Exact decimal of a ledger monetary value (Decimal, int, or string)."""
    if isinstance(value, bool):
        raise InvalidOperation
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class _Abort(Exception):
    """Internal: leave the ledger untouched (policy abort, not an I/O error)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _fsync_dir(path: pathlib.Path) -> None:
    """fsync a directory so entries created in it survive a power loss.

    On POSIX this is MANDATORY and its failure is fatal to the pass: an
    unsynced directory means the archive segment's name may not exist after a
    crash, and the swap that follows would then be the only surviving copy of
    a history whose raw rows just vanished. Windows has no directory handle to
    fsync (``os.open`` on a directory fails), so there it is a disclosed
    no-op — selected by the platform predicate, never by swallowing OSError.
    """
    from ouroboros.platform_layer import IS_WINDOWS

    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        if IS_WINDOWS:
            return
        raise
    try:
        os.fsync(fd)
    except OSError:
        if not IS_WINDOWS:
            raise
    finally:
        os.close(fd)


def _mkdir_fsync_chain(path: pathlib.Path) -> None:
    """``mkdir -p`` whose every CREATED directory entry is made durable.

    Syncing a directory persists the entries IT holds, so one
    ``mkdir(parents=True)`` needs a pass over each new level AND over the
    pre-existing ancestor that now carries the shallowest new entry. Syncing
    only the deepest level (the segment's own parent) leaves
    ``archive/usage_ledger`` itself unnamed on disk after a crash, while the
    swapped ledger survives — exactly the loss the archive-first order exists
    to prevent.
    """
    created: list = []
    probe = path
    while not probe.exists():
        created.append(probe)
        if probe.parent == probe:
            break
        probe = probe.parent
    path.mkdir(parents=True, exist_ok=True)
    for directory in (*created, probe):
        _fsync_dir(directory)


def _write_new_file_fsync(path: pathlib.Path, payload: bytes) -> None:
    """Create-exclusive write + fsync of file AND directory (archive segments)."""
    _mkdir_fsync_chain(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError(f"short write to {path}")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_dir(path.parent)


def _swap_ledger_fsync(path: pathlib.Path, payload: bytes) -> None:
    """Atomically replace the live ledger with the verified candidate bytes."""
    _write_bytes_atomic_fsync(path, payload)
    _fsync_dir(path.parent)


def _beat(heartbeat: Optional[Callable[[], bool]]) -> None:
    """Renew the held monetary lock's age at a pass checkpoint (never raises)."""
    if heartbeat is None:
        return
    try:
        heartbeat()
    except Exception:  # a heartbeat failure must never fail a reservation
        log.debug("usage-ledger lock heartbeat failed", exc_info=True)


def _snapshot_intact(ledger_path: pathlib.Path, raw: bytes) -> bool:
    """Whether the live ledger is still EXACTLY the snapshot being compacted.

    The swap replaces the WHOLE file, so any row appended after the snapshot
    would be silently dropped — a settled charge erased, a budget under-count,
    a replayable double charge. The lock makes that impossible in the normal
    case; this makes it impossible in the abnormal one (a lock broken by age,
    a foreign writer, a manual repair). Re-read under the same held lock and
    refuse the swap on ANY difference: the price of a lost race is a skipped
    compaction pass, never a lost row.
    """
    try:
        if os.stat(ledger_path).st_size != len(raw):
            return False
        return ledger_path.read_bytes() == raw
    except OSError:
        return False


def _dumps_row(row: Dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row_weight(row: Dict[str, Any]) -> int:
    if str(row.get("kind") or "") == "usage_baseline_group":
        return max(1, int(row.get("folded_attempt_count") or 1))
    return 1


def _group_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    """The attribution tuple that keeps every aggregation branch homogeneous."""
    pricing_known = row.get("pricing_known")
    return (
        str(row.get("state") or ""),
        str(row.get("model") or ""),
        str(row.get("provider") or ""),
        str(row.get("category") or ""),
        str(row.get("source") or ""),
        str(row.get("task_id") or ""),
        str(row.get("root_task_id") or ""),
        str(row.get("parent_task_id") or ""),
        str(row.get("prompt_cache_ttl") or ""),
        row.get("cost_usd") is not None,
        bool(row.get("cost_final")),
        pricing_known if isinstance(pricing_known, bool) else None,
        row.get("reservation_upper_bound_usd") is not None,
    )


class _Group:
    __slots__ = ("count", "cost", "bound", "tokens", "root_limit")

    def __init__(self) -> None:
        self.count = 0
        self.cost: Optional[Decimal] = None
        self.bound: Optional[Decimal] = None
        self.tokens: Dict[str, Optional[int]] = {field: None for field in _TOKEN_SUM_FIELDS}
        self.root_limit: Optional[Decimal] = None

    def absorb(self, row: Dict[str, Any]) -> None:
        """Fold one FINAL decimal-parsed row (attempt final or prior group)."""
        self.count += _row_weight(row)
        cost = row.get("cost_usd")
        if cost is not None:
            self.cost = (self.cost or Decimal(0)) + _decimal_of(cost)
        bound = row.get("reservation_upper_bound_usd")
        if bound is not None:
            self.bound = (self.bound or Decimal(0)) + _decimal_of(bound)
        for field in _TOKEN_SUM_FIELDS:
            value = row.get(field)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise _Abort(f"non-integer token field {field}")
            self.tokens[field] = (self.tokens[field] or 0) + max(0, value)
        limit = row.get("root_limit_usd")
        if limit is not None and _number(limit) is not None:
            limit_dec = _decimal_of(limit)
            self.root_limit = (
                limit_dec if self.root_limit is None else min(self.root_limit, limit_dec)
            )


def _render_fingerprint(finals: list) -> Dict[str, Any]:
    """The production-aggregation surfaces budget/display actually consume.

    Mirrors the composition of ``usage_projection`` (global summary, per-root
    summaries + min known ``root_limit_usd``) and ``usage_breakdown`` (global
    bucket, per-axis buckets with the legacy/empty-key unattributed rule),
    built from the SAME ``_summary``/``_breakdown_bucket`` production
    functions. Compared before/after on the candidate bytes; any inequality
    aborts the compaction.
    """
    per_root: Dict[str, Any] = {}
    grouped_roots: Dict[str, list] = {}
    for row in finals:
        rid = str(row.get("root_task_id") or "")
        if rid:
            grouped_roots.setdefault(rid, []).append(row)
    for rid in sorted(grouped_roots):
        rows = grouped_roots[rid]
        known = [
            value
            for value in (_number(row.get("root_limit_usd")) for row in rows)
            if value is not None
        ]
        per_root[rid] = (_summary(rows), min(known) if known else None)

    def grouped(field: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        groups: Dict[str, list] = {}
        unattributed: list = []
        for row in finals:
            key = str(row.get(field) or "")
            if str(row.get("kind") or "") in {"legacy_metadata", "legacy_delta"} or not key:
                unattributed.append(row)
            else:
                groups.setdefault(key, []).append(row)
        return (
            {key: _breakdown_bucket(groups[key]) for key in sorted(groups)},
            _breakdown_bucket(unattributed),
        )

    return {
        "summary": _summary(finals),
        "by_root": per_root,
        "breakdown": _breakdown_bucket(finals),
        "axes": {
            field: grouped(field)
            for field in ("model", "provider", "category", "task_id", "root_task_id")
        },
    }


def _parse_ledger_lines(raw: bytes) -> Tuple[list, list]:
    """(float_rows, decimal_rows) for the non-empty lines of ``raw``."""
    float_rows: list = []
    decimal_rows: list = []
    for chunk in raw.splitlines():
        line = chunk.strip(b"\r").strip()
        if not line:
            continue
        text = line.decode("utf-8")
        float_rows.append(json.loads(text))
        decimal_rows.append(json.loads(text, parse_float=Decimal))
    return float_rows, decimal_rows


def _foldable_attempt_ids(records: list) -> set:
    """Attempt ids whose whole chain folds: terminal, plain ``attempt`` kind,
    no review attribution — plus prior baseline group/header rows (re-folded)."""
    finals = _final_rows(records)
    foldable: set = set()
    for attempt_id, row in finals.items():
        kind = str(row.get("kind") or "attempt")
        if kind in _BASELINE_KINDS:
            foldable.add(attempt_id)
            continue
        if kind != "attempt":
            continue
        if str(row.get("state") or "") not in _FOLDABLE_FINAL_STATES:
            continue
        if any(str(row.get(key) or "") for key in _REVIEW_KEYS):
            continue
        if isinstance(row.get("cost_usd"), bool) or isinstance(
            row.get("reservation_upper_bound_usd"), bool
        ):
            continue  # fail-safe: malformed monetary value never folds
        foldable.add(attempt_id)
    return foldable


def _build_candidate(
    records: list, decimal_records: list, raw: bytes
) -> Tuple[bytes, Dict[str, Any]]:
    foldable = _foldable_attempt_ids(records)
    decimal_finals = _final_rows(decimal_records)
    prior_header = next(
        (row for row in records if str(row.get("kind") or "") == "usage_baseline"), None
    )

    groups: Dict[Tuple[Any, ...], _Group] = {}
    folded_row_count = 0
    folded_attempt_count = 0
    for attempt_id in foldable:
        final = decimal_finals[attempt_id]
        if str(final.get("kind") or "") == "usage_baseline":
            continue  # the prior stamp is superseded, not aggregated
        groups.setdefault(_group_key(final), _Group()).absorb(final)
        folded_attempt_count += _row_weight(final)
    for row in records:
        if str(row.get("attempt_id") or "") in foldable:
            folded_row_count += 1
    if folded_row_count == 0 or not groups:
        raise _Abort("nothing foldable")

    baseline_id = f"baseline-{uuid.uuid4().hex[:12]}"
    epoch = 1
    if prior_header is not None:
        epoch = max(1, int(prior_header.get("compaction_epoch") or 0)) + 1
    now = utc_now_iso()
    stamp = now.replace("-", "").replace(":", "").replace("+00:00", "Z")
    archive_rel = str(
        ARCHIVE_SEGMENT_DIR_REL / f"segment_ep{epoch:04d}_{stamp}_{uuid.uuid4().hex[:8]}.jsonl"
    ).replace(os.sep, "/")
    source_sha256 = hashlib.sha256(raw).hexdigest()

    group_rows: list = []
    for index, key in enumerate(sorted(groups, key=repr), start=1):
        group = groups[key]
        (state, model, provider, category, source, task_id, root_task_id,
         parent_task_id, ttl, cost_known, cost_final, pricing_known,
         bound_known) = key
        row: Dict[str, Any] = {
            "kind": "usage_baseline_group",
            "attempt_id": f"{baseline_id}-g{index:04d}",
            "state": state,
            "model": model,
            "provider": provider,
            "category": category,
            "source": source,
            "task_id": task_id,
            "root_task_id": root_task_id,
            "parent_task_id": parent_task_id,
            "review_skill": "",
            "review_wave_id": "",
            "review_slot_id": "",
            "baseline_id": baseline_id,
            "folded_attempt_count": group.count,
            "cost_final": cost_final,
            "ts": now,
        }
        if ttl:
            row["prompt_cache_ttl"] = ttl
        if pricing_known is not None:
            row["pricing_known"] = pricing_known
        if cost_known:
            row["cost_usd"] = format(group.cost or Decimal(0), "f")
        if bound_known:
            row["reservation_upper_bound_usd"] = format(group.bound or Decimal(0), "f")
        for field in _TOKEN_SUM_FIELDS:
            if group.tokens[field] is not None:
                row[field] = group.tokens[field]
        if group.root_limit is not None:
            row["root_limit_usd"] = format(group.root_limit, "f")
        group_rows.append(row)

    retained_lines: list = []
    retained_count = 0
    next_seq = 1 + len(group_rows)
    for float_row, decimal_row in zip(records, decimal_records):
        attempt_id = str(float_row.get("attempt_id") or "")
        kind = str(float_row.get("kind") or "attempt")
        if attempt_id in foldable or kind == "usage_baseline":
            continue
        next_seq += 1
        retained_count += 1
        updated = dict(float_row)
        updated["pre_compaction_seq"] = int(float_row.get("seq") or 0)
        updated["seq"] = next_seq
        line = _dumps_row(updated)
        expected = dict(decimal_row)
        expected["pre_compaction_seq"] = updated["pre_compaction_seq"]
        expected["seq"] = next_seq
        if json.loads(line, parse_float=Decimal) != expected:
            # A foreign literal that does not round-trip through a double:
            # never approximate the monetary history — keep the file as is.
            raise _Abort(f"non-round-trippable literal in retained row seq={float_row.get('seq')}")
        retained_lines.append(line)

    header = {
        "kind": "usage_baseline",
        "attempt_id": baseline_id,
        "state": "settled",
        "seq": 1,
        "ts": now,
        "baseline_id": baseline_id,
        "compaction_epoch": epoch,
        "archive_rel": archive_rel,
        "source_sha256": source_sha256,
        "source_size_bytes": len(raw),
        "source_row_count": len(records),
        "source_first_seq": int(records[0].get("seq") or 1),
        "source_last_seq": int(records[-1].get("seq") or len(records)),
        "folded_row_count": folded_row_count,
        "folded_attempt_count": folded_attempt_count,
        "group_count": len(group_rows),
        "retained_row_count": retained_count,
    }
    for offset, row in enumerate(group_rows, start=2):
        row["seq"] = offset
    lines = [_dumps_row(header), *(_dumps_row(row) for row in group_rows), *retained_lines]
    candidate = ("\n".join(lines) + "\n").encode("utf-8")

    receipt = {
        "baseline_id": baseline_id,
        "compaction_epoch": epoch,
        "archive_rel": archive_rel,
        "source_sha256": source_sha256,
        "source_size_bytes": len(raw),
        "compacted_size_bytes": len(candidate),
        "source_row_count": len(records),
        "folded_row_count": folded_row_count,
        "folded_attempt_count": folded_attempt_count,
        "group_count": len(group_rows),
        "retained_row_count": retained_count,
    }
    return candidate, receipt


def compact_usage_ledger_locked(
    root: pathlib.Path | str,
    *,
    heartbeat: Optional[Callable[[], bool]] = None,
) -> Optional[Dict[str, Any]]:
    """One compaction pass. MUST be called under the held monetary ledger lock.

    ``heartbeat`` is the lock renewal yielded by ``usage_ledger._locked``: a
    pass over a multi-megabyte ledger can outlive the lock's staleness window,
    and a lock stolen mid-pass is the one way a swap could drop a concurrently
    appended charge.

    Returns the commit receipt, or ``None`` when the pass aborts by policy
    (nothing foldable, no byte gain, any verification inequality, or a live
    ledger that changed under us) — an abort leaves the ledger byte-identical.
    I/O errors during the commit steps propagate; the archive segment is
    durable BEFORE the live file is touched, so a crash at any point leaves a
    valid ledger.
    """
    root = pathlib.Path(_drive_root(root))
    ledger_path = root / LEDGER_REL
    records = _read_records_locked(root)  # owns quarantine of a torn tail
    if not records:
        return None
    try:
        raw = ledger_path.read_bytes()
    except OSError:
        return None
    try:
        float_rows, decimal_rows = _parse_ledger_lines(raw)
        if len(float_rows) != len(records):
            raise _Abort("post-read line drift")
        candidate, receipt = _build_candidate(float_rows, decimal_rows, raw)
        if len(candidate) >= len(raw):
            raise _Abort("no byte gain")
        candidate_records, candidate_decimals = _parse_ledger_lines(candidate)
        _validate_records(candidate_records)
        finals_before = list(_final_rows(float_rows).values())
        finals_after = list(_final_rows(candidate_records).values())
        if _render_fingerprint(finals_before) != _render_fingerprint(finals_after):
            raise _Abort("aggregation fingerprint mismatch")

        def decimal_totals(rows: list) -> Tuple[Decimal, Decimal]:
            cost = Decimal(0)
            bound = Decimal(0)
            for row in _final_rows(rows).values():
                if str(row.get("kind") or "") == "usage_baseline":
                    continue
                value = row.get("cost_usd")
                if value is not None and str(row.get("state") or "") == "settled":
                    cost += _decimal_of(value)
                upper = row.get("reservation_upper_bound_usd")
                if upper is not None:
                    bound += _decimal_of(upper)
            return cost, bound

        if decimal_totals(decimal_rows) != decimal_totals(candidate_decimals):
            raise _Abort("decimal money totals mismatch")
        _beat(heartbeat)
    except _Abort as abort:
        log.info("usage-ledger compaction skipped: %s", abort.reason)
        return None
    except (UsageLedgerCorrupt, InvalidOperation, ValueError, TypeError, KeyError) as exc:
        # Never let a compaction defect become a monetary failure: abort clean.
        log.warning("usage-ledger compaction aborted: %s: %s", type(exc).__name__, exc)
        return None

    # Commit: archive first (durable before the live file is touched). The
    # literal path chain here is the scanner-visible writer of the
    # archive/usage_ledger plane (docs/PERSISTENCE.md row).
    _beat(heartbeat)
    if not _snapshot_intact(ledger_path, raw):
        log.warning("usage-ledger compaction abandoned before archive: ledger changed under the lock")
        return None
    segment_name = pathlib.PurePosixPath(receipt["archive_rel"]).name
    _write_new_file_fsync(root / "archive" / "usage_ledger" / segment_name, raw)
    _beat(heartbeat)
    if not _snapshot_intact(ledger_path, raw):
        # A row landed between the snapshot and here. Swapping now would erase
        # it; the written segment is an orphan (harmless, never referenced).
        log.warning("usage-ledger compaction abandoned before swap: ledger changed under the lock")
        return None
    _swap_ledger_fsync(ledger_path, candidate)
    try:
        append_jsonl(
            root / "logs" / "events.jsonl",
            {"type": "usage_ledger_compacted", "ts": utc_now_iso(), **receipt},
        )
    except Exception:
        log.exception("Failed to emit usage_ledger_compacted event")
    log.info(
        "usage ledger compacted: %s -> %s bytes, folded %s rows (%s attempts) into %s groups, archive %s",
        receipt["source_size_bytes"], receipt["compacted_size_bytes"],
        receipt["folded_row_count"], receipt["folded_attempt_count"],
        receipt["group_count"], receipt["archive_rel"],
    )
    return receipt


def maybe_compact_usage_ledger_locked(
    root: pathlib.Path | str,
    *,
    heartbeat: Optional[Callable[[], bool]] = None,
) -> bool:
    """Opportunistic trigger on the monetary write path (under the held lock).

    ``os.stat`` fast-path below ``config.USAGE_LEDGER_COMPACT_BYTES``; a
    per-process growth guard throttles re-attempts after an unprofitable or
    aborted pass. Every failure is contained: this never raises into the
    caller's reservation (a corrupt ledger still fails in the normal read)."""
    try:
        root = pathlib.Path(_drive_root(root))
    except Exception:
        return False
    ledger_path = root / LEDGER_REL
    try:
        stat = os.stat(ledger_path)
    except OSError:
        return False
    from ouroboros import config

    if stat.st_size < int(config.USAGE_LEDGER_COMPACT_BYTES):
        return False
    key = str(root.resolve(strict=False))
    with _COMPACT_ATTEMPTS_LOCK:
        prior = _COMPACT_ATTEMPTS.get(key)
    if prior is not None and prior[:2] == (stat.st_ino, stat.st_dev) and (
        stat.st_size < prior[2] + int(config.USAGE_LEDGER_COMPACT_RETRY_GROWTH_BYTES)
    ):
        return False
    receipt: Optional[Dict[str, Any]] = None
    try:
        receipt = compact_usage_ledger_locked(root, heartbeat=heartbeat)
    except Exception:
        log.exception("usage-ledger compaction failed; reservation continues uncompacted")
    if receipt is not None:
        with _COMPACT_ATTEMPTS_LOCK:
            _COMPACT_ATTEMPTS.pop(key, None)
        return True
    with _COMPACT_ATTEMPTS_LOCK:
        _COMPACT_ATTEMPTS[key] = (stat.st_ino, stat.st_dev, stat.st_size)
    return False


# --- History readers (CPL-5 reverse-sweep join surface; audits) --------------


def _live_baseline_header(root: pathlib.Path) -> Optional[Dict[str, Any]]:
    """The live ledger's leading baseline header, if the file is compacted.

    Lock-free by design: appends never touch line 1 and the compactor swaps
    the file atomically, so the first line is always a complete row of either
    generation. ``None`` therefore means exactly one thing — a readable
    leading row that is not a baseline stamp, i.e. no compaction has happened.
    A row that cannot be read AT ALL is corruption and says so: reporting it
    as "not compacted" would hand the CPL-5 sweep an empty archive and let it
    call a folded attempt an orphan seal.
    """
    try:
        with open(root / LEDGER_REL, "rb") as handle:
            first = handle.readline()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UsageLedgerCorrupt(f"usage ledger unreadable: {root / LEDGER_REL}") from exc
    line = first.strip(b"\r\n").strip()
    if not line:
        return None
    try:
        row = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageLedgerCorrupt("unreadable leading usage ledger row") from exc
    if not isinstance(row, dict):
        raise UsageLedgerCorrupt("leading usage ledger row is not an object")
    return row if str(row.get("kind") or "") == "usage_baseline" else None


def _segment_path(root: pathlib.Path, archive_rel: str) -> pathlib.Path:
    """Resolve a header's ``archive_rel`` INSIDE the archive directory or fail.

    Two independent bounds: the textual shape the substrate declares legal,
    and the RESOLVED location (so a symlink planted in the archive directory
    cannot make a file elsewhere on the host count as archived history)."""
    if not valid_archive_rel(archive_rel):
        raise UsageLedgerCorrupt(
            f"usage baseline archive reference is not bounded: {archive_rel!r}"
        )
    archive_dir = (root / ARCHIVE_SEGMENT_DIR_REL).resolve(strict=False)
    path = (root / archive_rel).resolve(strict=False)
    if path.parent != archive_dir:
        raise UsageLedgerCorrupt(
            f"usage archive segment escapes the archive directory: {archive_rel!r}"
        )
    return path


def _load_segment(
    root: pathlib.Path, header: Dict[str, Any]
) -> Tuple[frozenset, Optional[Dict[str, Any]]]:
    """Read one archived segment named (and fully described) by ``header``.

    Segments are immutable, so a verified read is cached — but the cache is
    keyed on what the file IS, not merely on what was once read from that
    path: a deleted or rewritten segment must surface as corruption even in a
    process that already loaded it, or an audit keeps answering "logged" from
    history that is no longer there.
    """
    expected_sha256 = str(header.get("source_sha256") or "")
    path = _segment_path(root, str(header.get("archive_rel") or ""))
    key = str(path)
    try:
        info = os.stat(path)
    except OSError as exc:
        raise UsageLedgerCorrupt(f"usage archive segment unreadable: {path}") from exc
    fingerprint = (info.st_ino, info.st_dev, info.st_size, info.st_mtime_ns)
    cached = _SEGMENT_CACHE.get(key)
    if cached is not None and cached[0] == expected_sha256 and cached[3] == fingerprint:
        return cached[1], cached[2]
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise UsageLedgerCorrupt(f"usage archive segment unreadable: {path}") from exc
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise UsageLedgerCorrupt(f"usage archive segment hash mismatch: {path}")
    if len(payload) != int(header.get("source_size_bytes") or -1):
        raise UsageLedgerCorrupt(f"usage archive segment size disagrees with its header: {path}")
    try:
        rows, _ = _parse_ledger_lines(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise UsageLedgerCorrupt(f"corrupt usage archive segment row: {path}") from exc
    if len(rows) != int(header.get("source_row_count") or -1):
        raise UsageLedgerCorrupt(f"usage archive segment row count disagrees with its header: {path}")
    # A segment IS a former generation of the ledger: hold it to the same
    # structural authority (dense seq, legal transitions, well-formed rows)
    # instead of scraping any JSON object that carries an ``attempt_id``.
    _validate_records(rows)
    ids = {str(row.get("attempt_id") or "") for row in rows}
    ids.discard("")
    prior_header = rows[0] if rows and str(rows[0].get("kind") or "") == "usage_baseline" else None
    frozen = frozenset(ids)
    _SEGMENT_CACHE[key] = (expected_sha256, frozen, prior_header, fingerprint)
    return frozen, prior_header


def _union_segment_ids(segment_ids: list) -> frozenset:
    ids: set = set()
    for chunk in segment_ids:
        ids |= chunk
    return frozenset(ids)


def archived_attempt_ids(root: pathlib.Path | str | None = None) -> frozenset:
    """Every ``attempt_id`` recorded in the archived ledger segments.

    Walks the tamper-evident chain: the live header names (and hash-pins) the
    newest segment; each segment's own leading header names the one before it.
    Because the source of epoch N is exactly the file epoch N-1 produced, the
    chain's epochs must step down by one and end at epoch 1 with a segment
    that embeds no header — so re-pointing a live header at an older genuine
    segment (dropping the epochs between) is corruption, not a shorter
    history. Segments are immutable, so per-segment reads and the union over a
    given chain are cached. An unreadable, hash-mismatched, mis-stepped, or
    cyclic chain raises ``UsageLedgerCorrupt`` — the CPL-5 reverse sweep must
    treat that as its existing UNKNOWN / skip-pass state, never as evidence of
    an orphan."""
    root = pathlib.Path(_drive_root(root))
    header = _live_baseline_header(root)
    chain: list = []
    segments: list = []
    seen: set = set()
    expected_epoch: Optional[int] = None
    while header is not None:
        archive_rel = str(header.get("archive_rel") or "")
        expected = str(header.get("source_sha256") or "")
        epoch = header.get("compaction_epoch")
        if not archive_rel or not expected or isinstance(epoch, bool) or not isinstance(
            epoch, int
        ) or epoch < 1:
            raise UsageLedgerCorrupt("usage baseline header lacks archive provenance")
        if expected_epoch is not None and epoch != expected_epoch:
            raise UsageLedgerCorrupt(
                f"usage archive chain epoch break: expected {expected_epoch}, found {epoch}"
            )
        if archive_rel in seen:
            raise UsageLedgerCorrupt(f"usage archive segment cycle at {archive_rel}")
        seen.add(archive_rel)
        segment_ids, header = _load_segment(root, header)
        chain.append((archive_rel, expected))
        segments.append(segment_ids)
        expected_epoch = epoch - 1
        if header is None and expected_epoch != 0:
            raise UsageLedgerCorrupt(
                f"usage archive chain ends before epoch {expected_epoch}"
            )
    key = tuple(chain)
    cached = _CHAIN_UNION_CACHE.get(key)
    if cached is not None:
        return cached
    union = _union_segment_ids(segments)
    _CHAIN_UNION_CACHE[key] = union
    return union


def usage_attempt_recorded(
    root: pathlib.Path | str | None,
    attempt_id: str,
    live_ids: Optional[set] = None,
) -> bool:
    """Membership of ``attempt_id`` in the live replay ∪ archived segments.

    The join primitive for per-attempt history questions on a compacted
    ledger (CPL-5 reverse sweep: an id absent HERE — not merely absent from
    the live replay — is what "no attempt row" means)."""
    attempt_id = str(attempt_id or "")
    if not attempt_id:
        return False
    if live_ids is None:
        from ouroboros._usage_rows_memo import _memoized_final_rows

        rows, _, _, _ = _memoized_final_rows(pathlib.Path(_drive_root(root)))
        live_ids = {str(row.get("attempt_id") or "") for row in rows}
    if attempt_id in live_ids:
        return True
    return attempt_id in archived_attempt_ids(root)
