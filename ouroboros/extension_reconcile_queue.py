"""File-backed worker->server extension reconcile requests.

Worker processes have their own in-memory extension registries and cannot own
the server's companion supervisor. When a worker enables/disables an extension,
it writes a small durable marker here; the server lifespan task picks it up and
reconciles the server-side extension surfaces/companions.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import pathlib
import re
import uuid
from typing import Any, Callable, Dict, List

from ouroboros.utils import append_jsonl, atomic_write_json, read_json_dict, utc_now_iso

log = logging.getLogger(__name__)

QUEUE_DIR = "extension_reconcile"
POLL_INTERVAL_SEC = 3.0
MAX_ATTEMPTS = 5
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _queue_root(drive_root: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(drive_root) / "state" / QUEUE_DIR


def _marker_name(skill_name: str, request_id: str) -> str:
    raw = str(skill_name or "").strip()
    safe = _SAFE_NAME_RE.sub("_", raw).strip("._-")[:48] or "skill"
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"{safe}-{digest}-{request_id}.json"


def request_extension_reconcile(
    drive_root: pathlib.Path,
    skill_name: str,
    *,
    reason: str = "",
    source: str = "worker",
) -> pathlib.Path:
    """Ask the server process to reconcile one extension skill.

    Each write creates a unique marker. The server reads desired state from
    durable skill state at processing time, so duplicate requests are harmless
    and an older marker can never unlink a newer one.
    """
    root = _queue_root(pathlib.Path(drive_root))
    root.mkdir(parents=True, exist_ok=True)
    request_id = uuid.uuid4().hex
    marker = root / _marker_name(skill_name, request_id)
    payload = {
        "skill": str(skill_name or ""),
        "reason": str(reason or ""),
        "source": str(source or "worker"),
        "request_id": request_id,
        "requested_at": utc_now_iso(),
        "attempts": 0,
    }
    atomic_write_json(marker, payload)
    return marker


def list_extension_reconcile_requests(drive_root: pathlib.Path) -> List[Dict[str, Any]]:
    root = _queue_root(pathlib.Path(drive_root))
    if not root.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        payload = read_json_dict(path)
        if not payload:
            continue
        skill = str(payload.get("skill") or "").strip()
        if not skill:
            continue
        item = dict(payload)
        item["_path"] = str(path)
        out.append(item)
    return out


def _mark_failed(
    path: pathlib.Path,
    payload: Dict[str, Any],
    error: str,
    *,
    drive_root: pathlib.Path,
) -> None:
    """Record one failed attempt; the LAST one becomes a durable terminal fact.

    The ``failed/`` marker is age-pruned as a dead flag (CPL4-C15) on the
    premise that "the failure fact is already durable in events.jsonl" — which
    was not true: nothing but the marker itself recorded why an extension gave
    up. The terminal failure is therefore appended to the event log BEFORE the
    marker is written, so the marker really is only a cache of it and the GC
    that clears it destroys nothing (audit #15-13).
    """
    attempts = int(payload.get("attempts") or 0) + 1
    payload = dict(payload)
    payload.pop("_path", None)
    payload.update({
        "attempts": attempts,
        "last_error": str(error or "")[:1000],
        "last_attempt_at": utc_now_iso(),
    })
    if attempts >= MAX_ATTEMPTS:
        payload["status"] = "failed"
        append_jsonl(pathlib.Path(drive_root) / "logs" / "events.jsonl", {
            "ts": payload["last_attempt_at"],
            "type": "extension_reconcile_failed",
            "skill": payload.get("skill"),
            "request_id": payload.get("request_id"),
            "reason": payload.get("reason"),
            "source": payload.get("source"),
            "attempts": attempts,
            "last_error": payload["last_error"],
        })
        failed_path = path.parent / "failed" / path.name
        atomic_write_json(failed_path, payload)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    atomic_write_json(path, payload)


def prune_failed_reconcile_markers(
    drive_root: pathlib.Path,
    retention_days: "int | None" = None,
    *,
    now: "float | None" = None,
) -> Dict[str, Any]:
    """Age-prune permanently failed reconcile markers (CPL4-C15).

    A marker lands in ``failed/`` after ``MAX_ATTEMPTS`` and was kept forever;
    ``_mark_failed`` appends the terminal failure to events.jsonl BEFORE
    writing the marker, so past GC retention the marker is a dead flag over a
    fact that survives it. Age by file mtime (written at the final failure); a
    refused unlink keeps the file and reports.
    """
    from ouroboros.retention import age_cutoff, get_gc_retention_days

    if retention_days is None:
        retention_days = get_gc_retention_days()
    cutoff = age_cutoff(retention_days, now)
    report: Dict[str, Any] = {"removed": [], "kept": 0, "errors": []}
    failed_root = _queue_root(drive_root) / "failed"
    try:
        entries = sorted(p for p in failed_root.glob("*.json") if p.is_file())
    except OSError:
        return report
    for path in entries:
        try:
            if path.stat().st_mtime >= cutoff:
                report["kept"] += 1
                continue
            path.unlink()
            report["removed"].append(path.name)
        except OSError:
            report["errors"].append({"entry": path.name, "error": "remove_failed"})
    return report


def process_extension_reconcile_requests(
    drive_root: pathlib.Path,
    settings_reader: Callable[[], Dict[str, Any]],
    *,
    repo_path: str | None = None,
) -> List[Dict[str, Any]]:
    """Process all pending server-side extension reconcile markers once."""
    from ouroboros.extension_loader import ensure_companions_running, reconcile_extension

    processed: List[Dict[str, Any]] = []
    for item in list_extension_reconcile_requests(drive_root):
        path = pathlib.Path(str(item.get("_path") or ""))
        if item.get("status") == "failed":
            continue
        skill = str(item.get("skill") or "").strip()
        try:
            state = reconcile_extension(
                skill,
                pathlib.Path(drive_root),
                settings_reader,
                repo_path=repo_path,
                retry_load_error=True,
            )
            companion_state = ensure_companions_running(
                skill,
                pathlib.Path(drive_root),
                settings_reader,
                repo_path=repo_path,
            )
            try:
                path.unlink()
                removed = True
            except FileNotFoundError:
                removed = True
            processed.append({
                "skill": skill,
                "action": state.get("action"),
                "reason": state.get("reason"),
                "marker_removed": removed,
                "companions": companion_state,
            })
        except Exception as exc:
            log.warning("server extension reconcile request failed for %s", skill, exc_info=True)
            _mark_failed(
                path, item, f"{type(exc).__name__}: {exc}",
                drive_root=pathlib.Path(drive_root),
            )
    return processed


async def extension_reconcile_pickup_loop(
    drive_root: pathlib.Path,
    settings_reader: Callable[[], Dict[str, Any]],
    *,
    repo_path_getter: Callable[[], str | None] | None = None,
    interval_sec: float = POLL_INTERVAL_SEC,
) -> None:
    """Server lifespan task: periodically process worker-written markers."""
    processing_task: asyncio.Task[Any] | None = None
    while True:
        try:
            repo_path = repo_path_getter() if repo_path_getter is not None else None
            processing_task = asyncio.create_task(
                asyncio.to_thread(
                    process_extension_reconcile_requests,
                    pathlib.Path(drive_root),
                    settings_reader,
                    repo_path=repo_path,
                ),
                name="extension-reconcile-process",
            )
            await asyncio.shield(processing_task)
            processing_task = None
        except asyncio.CancelledError:
            if processing_task is not None and not processing_task.done():
                await processing_task
            raise
        except Exception:
            log.debug("extension reconcile pickup loop failed", exc_info=True)
        await asyncio.sleep(max(0.5, float(interval_sec)))


__all__ = [
    "QUEUE_DIR",
    "POLL_INTERVAL_SEC",
    "request_extension_reconcile",
    "list_extension_reconcile_requests",
    "process_extension_reconcile_requests",
    "prune_failed_reconcile_markers",
    "extension_reconcile_pickup_loop",
]
