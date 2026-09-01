"""Digest-only compaction of old memory-journal snapshots (CPL4-C16, owner 4A).

``memory/identity_journal.jsonl``, ``memory/knowledge_history.jsonl`` and
``memory/knowledge/patterns_history.jsonl`` record the FULL old+new document
text on every write — O(doc×edits) growth, the worst byte offenders in the
memory plane. Owner decision 4A: entries younger than the unified GC
retention keep their full text; older entries become digest-only — the
content keys are replaced by their sha256 + length (existing hashes are
never overwritten) and the row is marked ``content_digested``.

Strictly fail-closed per line: an unparseable line, a row without a
readable ``ts``, or a row with nothing to digest is carried through
BYTE-IDENTICAL. The rewrite happens under the same sidecar lock the
appenders hold, via tmp+rename. The scratchpad journal (typed rows, its own
eviction contract) and the consciousness observation inbox (unacknowledged
rows must survive verbatim) are deliberately NOT in scope.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
from typing import Any, Dict, Optional

from ouroboros.deadline_utils import parse_deadline_ts
from ouroboros.platform_layer import acquire_exclusive_file_lock, release_exclusive_file_lock
from ouroboros.utils import jsonl_append_lock_path

log = logging.getLogger(__name__)

_JOURNAL_RELS = (
    pathlib.Path("memory") / "identity_journal.jsonl",
    pathlib.Path("memory") / "knowledge_history.jsonl",
    pathlib.Path("memory") / "knowledge" / "patterns_history.jsonl",
)
_CONTENT_KEYS = ("old_content", "new_content")


def _digest_row(row: Dict[str, Any]) -> bool:
    """Replace full-text keys with sha256+len; True when anything was dropped."""
    digested = False
    for key in _CONTENT_KEYS:
        value = row.get(key)
        if not isinstance(value, str):
            continue
        prefix = key[: -len("_content")]
        row.setdefault(f"{prefix}_sha256",
                       hashlib.sha256(value.encode("utf-8")).hexdigest() if value else "")
        row.setdefault(f"{prefix}_len", len(value))
        del row[key]
        digested = True
    if digested:
        row["content_digested"] = True
    return digested


def _compact_one(path: pathlib.Path, cutoff: float) -> Optional[int]:
    """Digest one journal in place; count of digested rows, None on lock miss."""
    lock_path = jsonl_append_lock_path(path)
    lock_fd = acquire_exclusive_file_lock(lock_path, timeout_sec=2.0, stale_sec=10.0)
    if lock_fd is None:
        return None
    try:
        raw_lines = path.read_bytes().splitlines(keepends=True)
        out: list[bytes] = []
        digested = 0
        for raw in raw_lines:
            stripped = raw.strip()
            if not stripped:
                out.append(raw)
                continue
            try:
                row = json.loads(stripped.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                out.append(raw)  # fail-closed: never rewrite what cannot be read
                continue
            if not isinstance(row, dict):
                out.append(raw)
                continue
            parsed_ts = parse_deadline_ts(str(row.get("ts") or ""))
            if parsed_ts is None or parsed_ts.timestamp() >= cutoff:
                out.append(raw)  # fresh, or age unknowable: keep full text
                continue
            if not _digest_row(row):
                out.append(raw)
                continue
            out.append(json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n")
            digested += 1
        if digested:
            tmp = path.with_name(path.name + ".compact.tmp")
            tmp.write_bytes(b"".join(out))
            os.replace(tmp, path)
        return digested
    finally:
        release_exclusive_file_lock(lock_path, lock_fd)


def compact_memory_journal_snapshots(
    drive_root: Any,
    retention_days: Optional[int] = None,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Digest old full-text snapshots in the three memory journals."""
    from ouroboros.retention import age_cutoff, get_gc_retention_days

    if retention_days is None:
        retention_days = get_gc_retention_days()
    cutoff = age_cutoff(retention_days, now)
    report: Dict[str, Any] = {"digested": {}, "errors": []}
    root = pathlib.Path(drive_root)
    for rel in _JOURNAL_RELS:
        path = root / rel
        if not path.exists():
            continue
        try:
            digested = _compact_one(path, cutoff)
        except OSError:
            report["errors"].append({"journal": rel.as_posix(), "error": "io_error"})
            continue
        if digested is None:
            report["errors"].append({"journal": rel.as_posix(), "error": "lock_unavailable"})
        elif digested:
            report["digested"][rel.as_posix()] = digested
    return report


__all__ = ["compact_memory_journal_snapshots"]
