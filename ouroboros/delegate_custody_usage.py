"""Pure usage and terminal-state projections for delegated-run custody."""

from __future__ import annotations

import hashlib
import pathlib
from contextlib import contextmanager
from typing import Any, Dict, Optional, Tuple


@contextmanager
def project_retirement_lock(drive_root: Any, project_id: str):
    """Cross-process lock for one project's settlement/retirement decision."""
    from ouroboros.platform_layer import (
        acquire_exclusive_file_lock, release_exclusive_file_lock,
    )

    digest = hashlib.sha256(str(project_id or "").encode("utf-8")).hexdigest()[:24]
    lock_path = pathlib.Path(drive_root) / "state" / "delegate_project_retirements" / f"{digest}.lock"
    fd = acquire_exclusive_file_lock(
        lock_path, timeout_sec=20.0, stale_sec=120.0, owner_aware_stale=True,
    )
    if fd is None:
        raise TimeoutError("project retirement lock is unavailable")
    try:
        yield
    finally:
        release_exclusive_file_lock(lock_path, fd)


def summary_of(detail: Dict[str, Any]) -> Dict[str, Any]:
    return detail.get("summary") if isinstance(detail.get("summary"), dict) else {}


def disclosed_spend(summary: Dict[str, Any]) -> Tuple[Optional[float], bool]:
    """The cash the harness reported AND whether it is settled — never one without both.

    ``spendUsd`` is only half the disclosure: the engine populates the sibling
    ``spendEstimated``. Reading the amount alone makes an estimate
    indistinguishable from settled cash, so callers receive the pair atomically.
    """
    raw = summary.get("spendUsd")
    if raw is None:
        return None, False
    try:
        return float(raw), summary.get("spendEstimated") is True
    except (TypeError, ValueError):
        return None, False


def disclosed_tokens(raw: Any) -> Optional[int]:
    """A reported token count, or ``None`` when the harness reported nothing.

    The control schema keeps token counts null until a harness reports them;
    converting absence to zero would erase that distinction.
    """
    if raw is None:
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


def is_terminal(detail: Dict[str, Any], terminal_states: frozenset[str]) -> bool:
    return str(summary_of(detail).get("state") or "") in terminal_states


def complete_custody_rows(path, marker: str, *, started_type: str = ""):
    """Every custody row, or ``None`` when the log's view is INCOMPLETE.

    The lenient reader skips unreadable lines to keep liveness surfaces
    working; an authority decision (removing a shared project) must instead
    fail closed: a marker-bearing line that cannot decode as strict UTF-8 or
    parse as a row, or a STARTED row missing its run identity, means a
    sibling's state may be invisible - no complete view exists. Streamed
    line-by-line (event logs grow to hundreds of MB)."""
    import json

    rows = []
    try:
        with path.open("rb") as handle:
            for raw in handle:
                if marker.encode("ascii") not in raw:
                    continue
                try:
                    row = json.loads(raw.decode("utf-8", errors="strict"))
                except (ValueError, UnicodeDecodeError):
                    return None
                if not isinstance(row, dict):
                    return None
                if not str(row.get("type") or "").startswith(marker):
                    continue  # a valid row of another event family
                if started_type and row.get("type") == started_type and not str(row.get("run_id") or ""):
                    return None
                rows.append(row)
    except FileNotFoundError:
        return rows
    except OSError:
        return None
    return rows
