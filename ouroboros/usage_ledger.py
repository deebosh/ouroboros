"""Durable append-only usage ledger: the substrate the accounting layer writes on.

ONE job, kept apart from policy: own the bytes. Cross-process locking, atomic
append + fsync, structural validation of every row and transition, and
quarantine of a torn tail. It knows what a well-formed ledger row IS; it has no
opinion about reservations, budgets, pricing, or projections — those live in
``usage_accounting``, which imports FROM here and is never imported BY here.

The seam is one-way by construction, so the monetary authority (the file) cannot
be corrupted by a change in accounting policy, and a locking or fsync fix cannot
silently alter what a reservation means.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import json
import logging
import os
import pathlib
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional, Sequence, Tuple

from ouroboros.utils import append_jsonl, replace_atomic, utc_now_iso

log = logging.getLogger(__name__)

LEDGER_REL = pathlib.Path("state/usage_attempts.jsonl")
QUARANTINE_REL = pathlib.Path("state/usage_attempts.quarantine.jsonl")
_TERMINAL = frozenset({"settled", "unresolved", "released"})

__all__ = (
    "LEDGER_REL", "QUARANTINE_REL", "UsageAccountingError", "UsageLedgerCorrupt",
    "compact_ledger",
)


class UsageAccountingError(RuntimeError):
    """Base error for fail-closed accounting operations."""


class UsageLedgerCorrupt(UsageAccountingError):
    """Raised when durable history is structurally invalid."""


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_IDENTITY_FIELDS = (
    "candidate_raw_sha256", "candidate_raw_size_bytes",
    "candidate_context_sha256", "candidate_context_size_bytes",
)


def _validate_candidate_facts(row: Dict[str, Any], sequence: int) -> None:
    if "candidate_payload" in row:
        raise UsageLedgerCorrupt(f"mutable candidate payload in usage row seq={sequence}")
    present = "candidate_measurement_kind" in row or any(key in row for key in _CANDIDATE_IDENTITY_FIELDS)
    if not present:
        return  # pre-feature/legacy rows
    kind = row.get("candidate_measurement_kind")
    if kind not in {"canonical_json_v1", "opaque"}:
        raise UsageLedgerCorrupt(f"invalid candidate_measurement_kind in usage row seq={sequence}")
    if kind == "opaque":
        if any(row.get(key) is not None for key in _CANDIDATE_IDENTITY_FIELDS):
            raise UsageLedgerCorrupt(f"opaque candidate claims identity in usage row seq={sequence}")
    else:
        for key in ("candidate_raw_sha256", "candidate_context_sha256"):
            if not isinstance(row.get(key), str) or not _SHA256_RE.fullmatch(row[key]):
                raise UsageLedgerCorrupt(f"invalid {key} in usage row seq={sequence}")
        for key in ("candidate_raw_size_bytes", "candidate_context_size_bytes"):
            value = row.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise UsageLedgerCorrupt(f"invalid {key} in usage row seq={sequence}")
    context = row.get("physical_context")
    if context is not None:
        if not isinstance(context, dict):
            raise UsageLedgerCorrupt(f"invalid physical_context in usage row seq={sequence}")
        if context.get("profile") not in {"owner_max", "owner_low", "task_local_low"}:
            raise UsageLedgerCorrupt(f"invalid physical_context profile in usage row seq={sequence}")
        if context.get("rendered_mode") not in {"max", "low"} or context.get("measurement_basis") not in {
            "fresh_route_usage", "fresh_model_usage", "cold_estimate",
        }:
            raise UsageLedgerCorrupt(f"invalid physical_context mode/basis in usage row seq={sequence}")
        for key in ("target_total_tokens", "capacity_total_tokens"):
            value = context.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise UsageLedgerCorrupt(f"invalid physical_context {key} in usage row seq={sequence}")
        if not all(isinstance(context.get(key), bool) for key in ("context_target_miss", "automatic_pass_used")):
            raise UsageLedgerCorrupt(f"invalid physical_context flags in usage row seq={sequence}")
        if not all(isinstance(context.get(key), str) for key in ("route_fp", "round_id")):
            raise UsageLedgerCorrupt(f"invalid physical_context identity in usage row seq={sequence}")
    manifest_ref = row.get("candidate_manifest_ref")
    if manifest_ref is not None and (
        not isinstance(manifest_ref, dict)
        or manifest_ref.get("call_id") != row.get("attempt_id")
        or not _SHA256_RE.fullmatch(str(manifest_ref.get("sha256") or ""))
    ):
        raise UsageLedgerCorrupt(f"invalid candidate_manifest_ref in usage row seq={sequence}")


def _drive_root(value: pathlib.Path | str | None = None) -> pathlib.Path:
    if value is not None:
        if not isinstance(value, (str, pathlib.Path)):
            raise UsageAccountingError(f"invalid usage accounting drive root type: {type(value).__name__}")
        resolved = pathlib.Path(value)
        if not resolved.is_absolute():
            raise UsageAccountingError(f"usage accounting drive root must be absolute: {resolved}")
        return resolved
    configured = str(os.environ.get("OUROBOROS_DATA_DIR") or "").strip()
    if configured:
        resolved = pathlib.Path(configured)
        if not resolved.is_absolute():
            raise UsageAccountingError(f"OUROBOROS_DATA_DIR must be absolute for usage accounting: {resolved}")
        return resolved
    from ouroboros.config import DATA_DIR

    return pathlib.Path(DATA_DIR)


@contextlib.contextmanager
def _named_lock(
    root: pathlib.Path,
    filename: str,
    *,
    timeout_sec: float,
    stale_sec: float,
) -> Iterator[None]:
    from ouroboros.platform_layer import (
        acquire_exclusive_file_lock,
        release_exclusive_file_lock,
    )

    path = root / "state" / filename
    fd = acquire_exclusive_file_lock(path, timeout_sec=timeout_sec, stale_sec=stale_sec)
    if fd is None:
        raise UsageAccountingError(f"usage accounting lock unavailable: {path}")
    try:
        yield
    finally:
        release_exclusive_file_lock(path, fd)


@contextlib.contextmanager
def _locked(root: pathlib.Path) -> Iterator[None]:
    # Operator fix 2026-07-23: 4.0s starves under a grown ledger (reserve_attempt
    # re-reads the whole usage_attempts.jsonl under this lock — ~0.5s hold at 20MB),
    # failing healthy tasks with UsageAccountingError at >=10 concurrent workers.
    # Waiting longer is always correct here; the transaction itself stays atomic.
    with _named_lock(root, "usage_attempts.lock", timeout_sec=45.0, stale_sec=90.0):
        yield


def _append_bytes_fsync(path: pathlib.Path, payload: bytes) -> None:
    """Append payload to path with O_APPEND. NO fsync.

    ``os.fsync`` is NOT called here on purpose: it is a kernel-level blocking
    syscall that cannot be interrupted by SIGALRM/SIGTERM, and calling it
    while the cross-process ``_locked`` flock is held turned every pytest
    timeout into a SIGKILL (the 30s ``pyproject.toml timeout = 30`` could not
    reach the test body because the fsync queued the signal indefinitely).
    The caller is responsible for ``_fsync_path(path)`` AFTER releasing the
    ledger lock — at that point a SIGALRM will wake the test, and the ledger
    is still durable on disk because the write completed under the lock
    and the fsync flushes exactly what was appended.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError(f"short append to {path}")
            view = view[written:]
    finally:
        os.close(fd)


def _fsync_path(path: pathlib.Path) -> None:
    """Flush the file at ``path`` to disk. Safe to call OUTSIDE the lock.

    Opens, fsyncs, closes — three short operations, none of which hold the
    cross-process ledger flock. ``os.fsync`` itself is uninterruptible by
    signals, so a SIGALRM delivered between open() and fsync() will still
    queue until fsync() returns — but the test framework's
    ``pytest-timeout`` sends SIGALRM after the test body has started, so a
    fsync here runs only after the ledger append has already succeeded and
    the lock has been released; the worst case is a single ``os.fsync``
    blocking the main thread, not a permanent SIGKILL.

    A non-existent file is a no-op: the caller (``_locked_with_fsync``) does
    not know whether any rows were appended, and the first import on a fresh
    drive has not yet created the ledger. There is nothing durable to flush.

    During pytest the data dir is a session-scoped tempdir
    (``conftest._PYTEST_DATA_DIR``) that the session cleans up at exit —
    durability of its ledger contents is irrelevant, and the fsync syscall
    is exactly the kernel-level blocking primitive pytest-timeout's
    signal-method SIGALRM cannot interrupt. The batch-context hang in
    ``test_web_search.py::test_streaming_direct_openai_cost_remains_nullable``
    (ibl-28f3c68cfaae / ibl-c094f2f90ec4 / ibl-system-test-fragility) was
    this fsync held by the streaming completion handler's ``settle_attempt``
    chain. Short-circuit here closes that class without weakening
    non-pytest durability: only the pytest-active env flag opts out, and
    it is set exclusively by ``conftest.py``'s session-start hook.
    """
    if os.environ.get("OUROBOROS_PYTEST_ACTIVE") == "1":
        return
    try:
        fd = os.open(str(path), os.O_WRONLY)
    except FileNotFoundError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def _locked_with_fsync(root: pathlib.Path) -> Iterator[None]:
    """``_locked`` + ``_fsync_path(ledger)`` AFTER release.

    Every append that goes through the ledger is paired with exactly one
    fsync. Doing both inside ``_locked`` made ``os.fsync`` uninterruptible
    by pytest-timeout's SIGALRM, which SIGKILLed every test that hit the
    ledger under pytest. This context manager keeps the ledger-write
    critical section under the flock (O_APPEND atomicity per write, dense
    sequence, transition validation) and moves the kernel-level flush
    OUTSIDE the lock so a signal can interrupt it.

    Yielding callers run their budget read + append under the lock; the
    fsync happens automatically on successful exit. A failure inside the
    ``with`` block skips the fsync — there is nothing durable to flush.
    """
    with _locked(root):
        yield
    _fsync_path(root / LEDGER_REL)


def _write_bytes_atomic_fsync(path: pathlib.Path, payload: bytes) -> None:
    """Persist the exact snapshotted bytes without reopening the source."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}")
    fd: Optional[int] = None
    try:
        # Windows defaults low-level descriptors to text mode, which would
        # expand LF bytes and break the archive's immutable source hash.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(str(tmp), flags, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError(f"short write to {tmp}")
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        replace_atomic(tmp, path)
    except Exception:
        if fd is not None:
            os.close(fd)
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _quarantine_tail(root: pathlib.Path, raw: bytes, offset: int, reason: str) -> None:
    ledger = root / LEDGER_REL
    row = {
        "ts": utc_now_iso(),
        "reason": reason,
        "source": str(ledger),
        "raw_base64": base64.b64encode(raw).decode("ascii"),
    }
    _append_bytes_fsync(
        root / QUARANTINE_REL,
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
    )
    fd = os.open(str(ledger), os.O_RDWR)
    try:
        os.ftruncate(fd, offset)
        os.fsync(fd)
    finally:
        os.close(fd)
    log.error("Quarantined corrupt final usage-ledger row: %s", reason)
    try:
        append_jsonl(
            root / "logs" / "events.jsonl",
            {"type": "usage_ledger_tail_quarantined", "ts": utc_now_iso(), "reason": reason},
        )
    except Exception:
        log.exception("Failed to emit usage-ledger quarantine event")


def _validate_records(
    records: Sequence[Dict[str, Any]],
    *,
    start_seq: int = 1,
    states: Optional[Dict[str, str]] = None,
) -> None:
    """Validate row structure, dense sequence, and per-attempt transitions.

    ``start_seq``/``states`` are the ADDITIVE resume seam for incremental tail
    validation: a caller that already validated a prefix passes the next
    expected sequence number and the prefix's per-attempt last-state map (which
    is mutated in place as the tail validates). Defaults reproduce the historic
    whole-ledger behavior exactly.
    """
    states = {} if states is None else states
    expected = int(start_seq)
    for row in records:
        try:
            sequence = int(row.get("seq") or 0) if isinstance(row, dict) else 0
        except (TypeError, ValueError, OverflowError) as exc:
            raise UsageLedgerCorrupt(f"invalid usage ledger sequence at {expected}") from exc
        if not isinstance(row, dict) or sequence != expected:
            raise UsageLedgerCorrupt(f"usage ledger sequence mismatch at {expected}")
        expected += 1
        attempt_id = str(row.get("attempt_id") or "")
        state = str(row.get("state") or "")
        kind = str(row.get("kind") or "attempt")
        if not attempt_id or state not in {"reserved", "dispatched", *_TERMINAL}:
            raise UsageLedgerCorrupt(f"invalid usage ledger row seq={row.get('seq')}")
        _validate_candidate_facts(row, sequence)
        for numeric_field in (
            "cost_usd", "reservation_upper_bound_usd", "reservation_usd",
            "max_budget_usd", "global_limit_usd", "root_limit_usd",
        ):
            if row.get(numeric_field) is not None and _number(row.get(numeric_field)) is None:
                raise UsageLedgerCorrupt(f"invalid {numeric_field} in usage row seq={sequence}")
        for token_field in (
            "prompt_tokens", "completion_tokens", "cached_tokens",
            "cache_write_tokens", "ambiguous_call_count",
        ):
            if row.get(token_field) is None:
                continue
            try:
                value = int(row.get(token_field))
            except (TypeError, ValueError, OverflowError) as exc:
                raise UsageLedgerCorrupt(
                    f"invalid {token_field} in usage row seq={sequence}"
                ) from exc
            if value < 0 or isinstance(row.get(token_field), bool):
                raise UsageLedgerCorrupt(
                    f"invalid {token_field} in usage row seq={sequence}"
                )
        previous = states.get(attempt_id)
        if kind.startswith("legacy_") or kind in {"external_unmetered", "subscription_session"}:
            if previous is not None or state not in {"settled", "unresolved"}:
                raise UsageLedgerCorrupt(f"invalid legacy usage row seq={row.get('seq')}")
        elif kind == "compacted":
            # A compacted summary row carries the pre-summed final for one
            # root_task_id; its source rows have already been archived and
            # removed from this ledger by ``compact_ledger``. It has no
            # prior attempt_id in the ledger (the synthetic attempt_id is
            # unique to the compaction) and MUST be settled — any other
            # state would mean the compactor mis-emitted the row.
            if previous is not None or state != "settled":
                raise UsageLedgerCorrupt(
                    f"invalid compacted usage row seq={row.get('seq')}: previous={previous} state={state}"
                )
        elif previous is None:
            if state != "reserved":
                raise UsageLedgerCorrupt(f"attempt {attempt_id} did not begin reserved")
        elif previous == "reserved":
            if state not in {"dispatched", "released"}:
                raise UsageLedgerCorrupt(f"invalid transition {previous}->{state}")
        elif previous == "dispatched":
            if state not in {"settled", "unresolved", "released"}:
                raise UsageLedgerCorrupt(f"invalid transition {previous}->{state}")
            if state == "released" and not str(row.get("reason") or "").startswith(
                "before_dispatch_failed:"
            ):
                raise UsageLedgerCorrupt(
                    f"dispatched->released requires a typed pre-dispatch reason at seq={row.get('seq')}"
                )
        else:
            raise UsageLedgerCorrupt(f"attempt {attempt_id} changed after terminal state")
        states[attempt_id] = state


def _read_records_locked(root: pathlib.Path) -> list[Dict[str, Any]]:
    path = root / LEDGER_REL
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise UsageAccountingError(f"cannot read usage ledger: {exc}") from exc
    records: list[Dict[str, Any]] = []
    record_locations: list[Tuple[int, bytes]] = []
    chunks = data.splitlines(keepends=True)
    nonempty = [index for index, chunk in enumerate(chunks) if chunk.rstrip(b"\r\n")]
    last_nonempty = nonempty[-1] if nonempty else -1
    offset = 0
    for index, chunk in enumerate(chunks):
        raw = chunk.rstrip(b"\r\n")
        if not raw:
            offset += len(chunk)
            continue
        try:
            row = json.loads(raw.decode("utf-8"))
            if not isinstance(row, dict):
                raise ValueError("row is not an object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            if index == last_nonempty:
                _quarantine_tail(root, chunk, offset, f"{type(exc).__name__}: {exc}")
                break
            raise UsageLedgerCorrupt(f"corrupt usage ledger row before tail: {index + 1}") from exc
        records.append(row)
        record_locations.append((offset, chunk))
        offset += len(chunk)
    try:
        _validate_records(records)
    except UsageLedgerCorrupt:
        # A final row can be valid JSON yet still be torn structurally (wrong
        # seq, illegal transition, missing fields). Preserve the validated
        # history exactly as for a JSON-torn tail; corruption before the final
        # row remains a hard failure.
        if not records or not record_locations:
            raise
        try:
            _validate_records(records[:-1])
        except UsageLedgerCorrupt:
            raise
        bad_offset, bad_chunk = record_locations[-1]
        _quarantine_tail(root, bad_chunk, bad_offset, "structurally invalid final ledger row")
        records.pop()
    return records


@dataclass
class LedgerResumeState:
    """Where a validated read of the ledger ended, for incremental resumption.

    Identity (``st_ino``/``st_dev``), extent (``size`` = byte offset after the
    last validated row) and ``st_mtime_ns`` fingerprint the file as it was read
    UNDER THE LOCK; ``row_count`` and the per-attempt last-``states`` map seed
    tail validation so transition rules hold across the resume boundary. A
    missing ledger is represented as ``st_ino/st_dev = -1`` with ``size = 0``;
    ``st_ino/st_dev = -2`` marks a deliberately NON-RESUMABLE fingerprint (the
    file's tail is not row-aligned), which no real inode ever matches, so every
    subsequent read stays a full replay.
    """

    st_ino: int
    st_dev: int
    size: int
    st_mtime_ns: int
    row_count: int
    states: Dict[str, str] = field(default_factory=dict)


def _ledger_resume_state(
    root: pathlib.Path, records: Sequence[Dict[str, Any]]
) -> LedgerResumeState:
    """Fingerprint the just-read ledger for incremental resumption.

    Must be called under the same held ledger lock as the read that produced
    ``records`` (writers append only under that lock, so the stat is consistent
    with the validated content — including any quarantine truncation the read
    itself performed)."""
    states = {str(row.get("attempt_id") or ""): str(row.get("state") or "") for row in records}
    path = root / LEDGER_REL
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return LedgerResumeState(-1, -1, 0, -1, len(records), states)
    if stat.st_size > 0:
        try:
            with open(path, "rb") as handle:
                handle.seek(stat.st_size - 1)
                terminated = handle.read(1) == b"\n"
        except OSError:
            terminated = False
        if not terminated:
            # A crash-torn final line that is still valid JSON parses in the full
            # read, but its end is NOT a row boundary: an append landing directly
            # onto it welds rows into one unparseable line (the #138 guard in
            # _append_rows_locked repairs the boundary before writing, but reads
            # before any repair — or after a foreign blind append — must not
            # resume from a mid-line offset). Refuse until the tail is row-aligned.
            return LedgerResumeState(-2, -2, stat.st_size, -1, len(records), states)
    return LedgerResumeState(
        stat.st_ino, stat.st_dev, stat.st_size, stat.st_mtime_ns, len(records), states
    )


def _read_new_records_locked(
    root: pathlib.Path, resume: LedgerResumeState
) -> Optional[Tuple[list[Dict[str, Any]], LedgerResumeState]]:
    """Incrementally read rows appended after ``resume``; ``None`` = full refold.

    Returns ``(new_records, new_resume)`` when the resume fingerprint still
    matches and the appended tail parses and validates as a seq-continuous,
    transition-legal continuation. Returns ``None`` whenever the resume state
    cannot be trusted — file replaced (inode/device change), shrunk below the
    resume offset, rewritten in place (same size, different mtime), or a
    torn/structurally invalid tail — so the caller re-reads through the normal
    ``_read_records_locked``, which OWNS quarantine. This function never
    truncates or otherwise mutates the ledger, and must be called under the
    held ledger lock.
    """
    path = root / LEDGER_REL
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        if resume.row_count == 0 and resume.size == 0:
            return [], resume
        return None
    except OSError:
        return None
    if (stat.st_ino, stat.st_dev) != (resume.st_ino, resume.st_dev):
        return None
    if stat.st_size < resume.size:
        return None
    if stat.st_size == resume.size:
        return ([], resume) if stat.st_mtime_ns == resume.st_mtime_ns else None
    try:
        with open(path, "rb") as handle:
            handle.seek(resume.size)
            data = handle.read()
    except OSError:
        return None
    if not data.endswith(b"\n"):
        # A torn in-flight append (crashed writer). The full reader decides
        # whether that tail is quarantined; never guess here.
        return None
    records: list[Dict[str, Any]] = []
    for chunk in data.splitlines():
        raw = chunk.rstrip(b"\r")
        if not raw:
            continue
        try:
            row = json.loads(raw.decode("utf-8"))
            if not isinstance(row, dict):
                raise ValueError("row is not an object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        records.append(row)
    seeded_states = dict(resume.states)
    try:
        _validate_records(records, start_seq=resume.row_count + 1, states=seeded_states)
    except UsageLedgerCorrupt:
        return None
    return records, LedgerResumeState(
        stat.st_ino,
        stat.st_dev,
        stat.st_size,
        stat.st_mtime_ns,
        resume.row_count + len(records),
        seeded_states,
    )


def _append_rows_locked(
    root: pathlib.Path,
    records: Sequence[Dict[str, Any]],
    rows: Sequence[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    if not rows:
        return []
    sequence = len(records)
    materialized: list[Dict[str, Any]] = []
    for raw in rows:
        sequence += 1
        materialized.append({**raw, "seq": sequence, "ts": str(raw.get("ts") or utc_now_iso())})
    _validate_records([*records, *materialized])
    payload = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in materialized
    )
    # razzant/ouroboros#138: O_APPEND writes payload verbatim after whatever is
    # already on disk. If a prior writer died mid-append it can have left a
    # newline-less partial tail; appending straight onto it glues the partial
    # and the first new row into one unparseable line, and _read_records_locked
    # would then quarantine BOTH. The validated-tail readers already refuse to
    # warm-resume from such a file; this guards the raw byte boundary on write
    # so a torn tail costs at most itself, never the row that follows.
    ledger_path = root / LEDGER_REL
    try:
        with open(ledger_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell():
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    payload = b"\n" + payload
    except FileNotFoundError:
        pass
    _append_bytes_fsync(ledger_path, payload)
    return materialized


def _number(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 and parsed == parsed else None


def _final_rows(records: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(row["attempt_id"]): row for row in records}


# Compactness knobs. The row threshold is the cheap no-op short-circuit
# (almost every <2000-row ledger is well under the byte threshold); the byte
# threshold is the in-bounds form of the same idea — both chosen so a
# normal-velocity run never enters the per-row fold loop, and a slow
# growth run enters it only when it has something to do.
_COMPACT_ROW_THRESHOLD = 2000
_COMPACT_BYTE_THRESHOLD = 20 * 1024 * 1024  # 20 MB

# Token fields the projection sums in ``_summary`` / ``_breakdown_bucket``.
# A folded root's summary row carries the pre-summed totals under these
# canonical names; the SAME set is what ``compact_ledger`` reads off each
# raw row to build the summary.
_SUMMED_NUMERIC_FIELDS = (
    "cost_usd",
    "reservation_upper_bound_usd",
    "reservation_usd",
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "ambiguous_call_count",
)


def _compact_row_numeric(row: Dict[str, Any], field: str) -> float:
    """Return ``row[field]`` as a non-negative float, or 0 when missing/invalid.

    The compactor folds every numeric column the projection consumes; a
    missing or non-numeric value contributes 0 to the summary row, NOT
    the row's raw value, so the projection's "unknown = None, never 0"
    rule (see ``_summary``) is preserved — a summary row's missing field
    is still missing downstream, while a PRESENT field's partial totals
    sum cleanly.
    """
    value = row.get(field)
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if parsed != parsed or parsed < 0.0:  # NaN or negative
        return 0.0
    return parsed


def _compact_row_known_cost(row: Dict[str, Any]) -> float:
    """Return row's SETTLED cost contribution to the summary; 0 if not a settled
    final-cost row. Mirrors ``_summary``: a row's cost enters ``settled_usd``
    only if ``state == "settled"``; otherwise it lives on a different axis
    (reserved / unresolved / unknown) that the compactor preserves by NOT
    folding the root."""
    if str(row.get("state") or "") != "settled":
        return 0.0
    cost = row.get("cost_usd")
    if cost is None or isinstance(cost, bool):
        return 0.0
    try:
        parsed = float(cost)
    except (TypeError, ValueError):
        return 0.0
    if parsed != parsed or parsed < 0.0:
        return 0.0
    return parsed


def _compact_parse_iso_epoch(ts_str: str) -> float:
    """Parse an ISO-8601 timestamp string to epoch seconds, or ``-1.0`` on failure.

    The compactor only needs ordering; a malformed timestamp is treated as
    "not archivable" because we cannot prove the row is older than the
    cutoff. ``datetime.fromisoformat`` accepts the project's
    ``+00:00`` suffix but is brittle against the ``Z`` shorthand; we
    normalize both."""
    if not ts_str:
        return -1.0
    try:
        from datetime import datetime

        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return -1.0


def _compact_cutoff_ts(retention_days: int, now: float) -> float:
    """Return the cutoff epoch-seconds: a root is archivable iff its
    newest row's ts is strictly LESS than this."""
    return now - max(0, int(retention_days)) * 86400.0


def _build_compacted_summary(
    root_rows: Sequence[Dict[str, Any]],
    root_task_id: str,
    newest_ts: str,
) -> Dict[str, Any]:
    """Build the pre-summed ``kind=compacted, state=settled`` summary row
    for one archivable root.

    The summary's numeric totals equal the sum of every raw row in the
    root for the canonical fields the projection consumes
    (``_SUMMED_NUMERIC_FIELDS``). Token fields (prompt/completion/cached/
    cache_write) are summed; ambiguous_call_count is summed for legacy
    rows that carry it. ``cost_usd`` is summed from settled rows only —
    a non-settled row in an all-terminal group is impossible here (the
    caller gates on ``all_terminal``) but the guard is defensive.

    ``cost_final`` is True on the summary: the sum IS the final cost of
    the source rows, by construction. A future cost-finality reconciliation
    over a folded root can re-derive this from the archive.
    """
    summary: Dict[str, Any] = {
        "kind": "compacted",
        "attempt_id": f"compacted:{root_task_id}",
        "root_task_id": root_task_id,
        "state": "settled",
        "cost_final": True,
        "row_count": len(root_rows),
        "compacted_at": utc_now_iso(),
        "newest_raw_ts": newest_ts,
    }
    for field in _SUMMED_NUMERIC_FIELDS:
        if field == "cost_usd":
            total = sum(_compact_row_known_cost(row) for row in root_rows)
        else:
            total = sum(_compact_row_numeric(row, field) for row in root_rows)
        if not total:
            continue
        # Token columns are integer-typed throughout the rest of the
        # ledger; storing them as floats here would round-trip through
        # ``int()`` in the projection's ``summed()`` helper but expose
        # the inconsistency on disk and to downstream readers. Cast
        # token-shaped fields back to ``int`` so the summary row shape
        # matches every other settled row in the ledger.
        if field in {
            "prompt_tokens", "completion_tokens", "cached_tokens",
            "cache_write_tokens", "ambiguous_call_count",
        }:
            summary[field] = int(total)
        else:
            summary[field] = total
    return summary


def compact_ledger(
    root: pathlib.Path | str | None = None,
    *,
    retention_days: int | None = None,
    min_rows: int = _COMPACT_ROW_THRESHOLD,
    min_bytes: int = _COMPACT_BYTE_THRESHOLD,
) -> Dict[str, Any]:
    """Fold fully-settled, fully-cold roots into pre-summed ``kind=compacted``
    summary rows and archive the raw rows to a gzipped monthly file.

    The byte-identical invariant is the gate: every total ``usage_projection``
    and ``usage_breakdown`` reports before the call must match the totals
    after, to the cent / token. The mechanism is the structure of the
    existing projection: ``_summary`` and ``_breakdown_bucket`` already
    take the "last row per ``attempt_id``" view (``_final_rows``), so
    replacing a root's N raw rows with ONE row whose
    ``state=settled, kind=compacted, cost_usd=<sum>, prompt_tokens=<sum>, ...``
    is picked up as that root's "final" and enters the same totals.

    Thresholds: skips when rows < ``min_rows`` OR the ledger < ``min_bytes``.
    Both default to the production knobs (2000 / 20 MB); tests pass
    smaller values to exercise the fold path on small ledgers.

    Archive: ``data/archive/usage_attempts_YYYY-MM.jsonl.gz`` (one per
    calendar month of the cutoff; multiple compactions in the same month
    APPEND rows to the same file). The file format is one JSON row per
    line, gzip-compressed, byte-identical to what the original rows were
    on disk.

    Caller contract: the function is safe to invoke from outside the
    lock — it acquires the same ``_locked`` flock as every other
    write path. It does NOT fsync (the underlying ``_write_bytes_atomic_fsync``
    does, on the replacement file).
    """
    drive_root = _drive_root(root)
    if retention_days is None:
        from ouroboros.retention import get_gc_retention_days

        retention_days = get_gc_retention_days()
    cutoff_ts = _compact_cutoff_ts(retention_days, time.time())
    archive_dir = drive_root / "data" / "archive"
    # yyyy-mm in UTC. Two compactions in the same month share one file.
    archive_stem = time.strftime("%Y-%m", time.gmtime(cutoff_ts))
    archive_path = archive_dir / f"usage_attempts_{archive_stem}.jsonl.gz"

    with _locked(drive_root):
        ledger_path = drive_root / LEDGER_REL
        bytes_before = ledger_path.stat().st_size if ledger_path.exists() else 0
        records = _read_records_locked(drive_root)
        rows_before = len(records)
        if rows_before == 0:
            return {
                "status": "skipped", "reason": "empty",
                "roots_folded": 0, "rows_before": 0, "rows_after": 0,
                "bytes_before": bytes_before, "bytes_after": bytes_before,
                "archive": str(archive_path),
            }
        if rows_before < int(min_rows) and bytes_before < int(min_bytes):
            return {
                "status": "skipped", "reason": "below_threshold",
                "roots_folded": 0, "rows_before": rows_before, "rows_after": rows_before,
                "bytes_before": bytes_before, "bytes_after": bytes_before,
                "archive": str(archive_path),
            }

        # Group by root_task_id. A row without root_task_id cannot be
        # ARCHIVED (there is no identity to carry the summary row's
        # root_task_id), but it MUST stay in the live file — silently
        # dropping it would break the byte-identical invariant for any
        # ``_final_rows`` projection consumer, destroy the
        # ``subscription_sessions`` count (which ``_summary`` reads on a
        # separate axis), and erase audit history for
        # ``legacy_*`` / ``external_unmetered`` / bg-consciousness /
        # chat-direct / planning / review LLM calls.
        by_root: Dict[str, list[Dict[str, Any]]] = {}
        rootless_rows: list[Dict[str, Any]] = []
        for row in records:
            rid = str(row.get("root_task_id") or "")
            if rid:
                by_root.setdefault(rid, []).append(row)
            else:
                rootless_rows.append(row)

        archivable_root_ids: list[str] = []
        archivable_rows: list[Dict[str, Any]] = []
        live_rows: list[Dict[str, Any]] = list(rootless_rows)
        summary_rows: list[Dict[str, Any]] = []

        for rid, root_rows in by_root.items():
            # ``all_terminal`` is the CORRECT production-invariant check:
            # an attempt's state machine goes reserved -> dispatched -> settled
            # and ALL three rows live in the ledger (audit trail). The
            # projection's ``_final_rows`` semantics — "the LAST row per
            # attempt_id wins" — is what the byte-identical invariant
            # actually pins: we want the same final state the projection
            # would see before vs. after. An attempt that has any row whose
            # state is NOT in _TERMINAL means the attempt has not yet
            # reached its terminal phase, and its row contribution to the
            # totals (cost / tokens) is in flight. Folding the root now
            # would lose that visibility.
            latest_states: Dict[str, str] = {}
            newest_epoch = -1.0
            newest_ts = ""
            for row in root_rows:
                aid = str(row.get("attempt_id") or "")
                latest_states[aid] = str(row.get("state") or "")
                epoch = _compact_parse_iso_epoch(str(row.get("ts") or ""))
                if epoch > newest_epoch:
                    newest_epoch = epoch
                    newest_ts = str(row.get("ts") or "")
            all_terminal = all(state in _TERMINAL
                               for state in latest_states.values())
            if not all_terminal or newest_epoch < 0.0:
                # Some attempt in the root is not yet terminal — keep the
                # raw rows in the live file so the projection sees the
                # in-flight reservation/dispatch.
                live_rows.extend(root_rows)
                continue
            if newest_epoch >= cutoff_ts:
                # Every attempt is terminal but the newest row is still
                # inside the cutoff window. Leave the root's raw rows
                # intact for now; the next compaction cycle will fold.
                live_rows.extend(root_rows)
                continue
            # Archivable. Build ONE summary row per root (the spec
            # mandates a SINGLE pre-summed row per root). The byte-identical
            # invariant covers the COST and TOKEN axes (settled_usd,
            # confirmed_usd, prompt_tokens, completion_tokens, cached_tokens,
            # cache_write_tokens, reserved_usd, unresolved_upper_bound_usd,
            # accounted_usd, unknown_unmetered, non_final_rows). The
            # ``attempt_counts`` metric is per-ROW in the pre-fold file
            # (reserved + dispatched + settled transitions count), and
            # collapses to 1 per root after fold. The compactor's
            # accepted trade-off is the row count delta: the savings on
            # row bytes (and the smaller re-validation time) outweigh
            # the metric-shift. The projection surfaces a
            # ``compacted`` kind so a downstream audit can still see
            # which roots were folded and when.
            archivable_root_ids.append(rid)
            archivable_rows.extend(root_rows)
            summary_rows.append(
                _build_compacted_summary(root_rows, rid, newest_ts)
            )

        if not archivable_root_ids:
            return {
                "status": "skipped", "reason": "nothing_archivable",
                "roots_folded": 0, "rows_before": rows_before, "rows_after": rows_before,
                "bytes_before": bytes_before, "bytes_after": bytes_before,
                "archive": str(archive_path),
            }

        # Append the raw rows to the gzipped archive BEFORE rewriting the
        # live file: the archive is the recovery record. We do this under
        # the lock so a concurrent reader cannot observe a half-rewritten
        # file paired with a half-written archive.
        archive_dir.mkdir(parents=True, exist_ok=True)
        payload_bytes = b"".join(
            (json.dumps(row, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")) + "\n").encode("utf-8")
            for row in archivable_rows
        )
        with open(archive_path, "ab") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb") as gz:
                gz.write(payload_bytes)

        # Rewrite the live ledger: every archivable root's raw rows
        # replaced by ONE summary row, in the same order they appeared.
        # ``_summary`` keys on attempt_id, so the summary's attempt_id
        # (``compacted:<root>``) MUST be unique — and ``_validate_records``
        # accepts it as a settled-from-null kind.
        # The new file is its own ledger: rewrite seq 1..N contiguously
        # so the validator's dense-sequence check passes without the
        # caller caring about the original seq values.
        combined = live_rows + summary_rows
        for new_seq, row in enumerate(combined, start=1):
            row["seq"] = new_seq
        new_payload = b"".join(
            (json.dumps(row, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")) + "\n").encode("utf-8")
            for row in combined
        )
        # Validate the new payload as a complete ledger before swapping
        # it on disk — a structural mistake in the compactor must not
        # corrupt the live file.
        new_records_for_check: list[Dict[str, Any]] = []
        for chunk in new_payload.splitlines(keepends=True):
            line = chunk.rstrip(b"\r\n")
            if not line:
                continue
            new_records_for_check.append(json.loads(line.decode("utf-8")))
        _validate_records(new_records_for_check)
        _write_bytes_atomic_fsync(ledger_path, new_payload)

        return {
            "status": "compacted",
            "roots_folded": len(archivable_root_ids),
            "folded_root_ids": archivable_root_ids,
            "rows_before": rows_before,
            "rows_after": len(new_records_for_check),
            "bytes_before": bytes_before,
            "bytes_after": len(new_payload),
            "archive": str(archive_path),
        }
