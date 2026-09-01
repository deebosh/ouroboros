"""Keeping the pool populated: spawn verification, pid records, reaping, respawn.

A spawned worker is not trusted until it reports the SHA it actually booted; the
pids it ran under are recorded durably so an orphan surviving a restart can be
reaped; a replaced worker's queue is closed under the lock before the new one
takes its slot.

The lifecycle serializer lives here too: it is a decorator, so it is applied at
import time and cannot be reached through a call-time handle. It is a primitive,
not pool state — nothing rebinds it — so the parent imports it back directly.
"""

from __future__ import annotations

import logging
from supervisor.worker_process import _current_custody_session_id, worker_main
import json
import os
import pathlib
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from supervisor.state import append_jsonl
from ouroboros.outcomes import EXECUTION_INFRA_FAILED, terminal_outcome_axes
from ouroboros.utils import utc_now_iso
from supervisor.queue import _queue_lock




log = logging.getLogger(__name__)


def _pool():
    """The parent module, read at call time.

    The parent owns the rebindable module state and the members tests
    monkeypatch there; reading them through the module at each call keeps
    one binding, where a from-import would freeze the value this leaf saw
    at import time (the owner-approved D18/D33 mechanical exception).
    """
    from supervisor import workers

    return workers


_WORKER_LIFECYCLE_LOCK = threading.RLock()


def _serialized_worker_lifecycle(fn):
    def wrapped(*args, **kwargs):
        with _WORKER_LIFECYCLE_LOCK:
            return fn(*args, **kwargs)

    return wrapped


def _write_failure_result(
    task_id: str,
    reason: str = "Worker process crashed (crash storm). Task was not completed.",
    status: str = "",
) -> str:
    """Write failure result for a crashed/orphaned task.

    Returns the FINAL persisted status: if the task already reached a terminal
    state, the monotonic guard preserves it and that existing status is returned
    (so the UI event matches disk); otherwise the written failure status.
    """
    if not task_id:
        return ""
    try:
        from ouroboros.task_results import (
            STATUS_FAILED, STATUS_COMPLETED, STATUS_REJECTED_DUPLICATE,
            STATUS_CANCELLED, load_task_result, write_task_result,
        )
        # STATUS_INTERRUPTED is not final; it is written before requeue.
        _FINAL_STATUSES = {STATUS_COMPLETED, STATUS_FAILED, STATUS_REJECTED_DUPLICATE, STATUS_CANCELLED}
        existing = load_task_result(_pool().DRIVE_ROOT, task_id, strict=True)
        if existing and existing.get("status") in _FINAL_STATUSES:
            return str(existing.get("status") or "")
        final_status = status or STATUS_FAILED
        # Reconstruct from durable llm_usage so an abnormally-finalized task does
        # not record zero cost/rounds (understating per-task + campaign metrics).
        f_cost_fields = _pool().reconstruct_task_cost(str(task_id), fields=True)
        stored = write_task_result(
            _pool().DRIVE_ROOT,
            task_id,
            final_status,
            strict_existing_dict=True,
            result=reason,
            reason_code="worker_terminal_failure" if final_status == STATUS_FAILED else str(final_status or ""),
            outcome_axes=terminal_outcome_axes(
                lifecycle=final_status,
                execution=EXECUTION_INFRA_FAILED if final_status == STATUS_FAILED else str(final_status or ""),
                reason_code="worker_terminal_failure" if final_status == STATUS_FAILED else str(final_status or ""),
                review_trigger="worker_terminal",
            ),
            **f_cost_fields,
        )
        persisted_status = str((stored or {}).get("status") or "").strip()
        if (
            not isinstance(stored, dict)
            or str(stored.get("task_id") or "") != str(task_id)
            or not persisted_status
        ):
            raise ValueError(
                f"failure result writer returned invalid durable identity for {task_id}"
            )
        return persisted_status
    except Exception:
        log.warning("Failed to write failure result for task %s", task_id, exc_info=True)
        raise


def events_log_cursor() -> Tuple[int, int, int]:
    """The live event log's ``(size, device, inode)`` — a cursor, not an offset.

    A byte offset alone cannot tell "the file I measured" from "a different
    file at the same path": ``_first_worker_event_since`` must know WHICH file
    the offset belongs to (audit #14-6c). Missing log = a zeroed cursor that
    reads from the start.
    """
    path = _pool().DRIVE_ROOT / "logs" / "events.jsonl"
    try:
        stat = path.stat()
        return int(stat.st_size), int(stat.st_dev), int(stat.st_ino)
    except OSError:
        return 0, 0, 0


def _first_worker_event_since(
    cursor: Tuple[int, int, int], event_type: str = "worker_boot"
) -> Optional[Dict[str, Any]]:
    """Read the first event of one worker lifecycle type after a cursor.

    The event log rotates (CPL4-C1), so the file the cursor was taken from may
    now BE an archive segment. Rotation is detected by IDENTITY, not by size:
    the previous test ("the live file is smaller than my offset") missed every
    rotation where the new live file had already grown past the old offset —
    exactly what happens under a busy supervisor — and then read the wrong
    file's bytes from a meaningless offset. When the identity moved, the
    matching segment is read from the SAME offset (the continuation is exact);
    a cursor whose file is gone entirely falls back to the newest segment
    whole.
    """
    offset_bytes, dev, ino = cursor
    path = _pool().DRIVE_ROOT / "logs" / "events.jsonl"
    if not path.exists():
        return None
    chunks: list[str] = []
    try:
        with path.open("rb") as f:
            stat = os.fstat(f.fileno())
            rotated = (dev, ino) != (0, 0) and (int(stat.st_dev), int(stat.st_ino)) != (dev, ino)
            if not rotated and not 0 <= offset_bytes <= stat.st_size:
                rotated = True  # same file, truncated under the cursor
            f.seek(0 if rotated else offset_bytes)
            chunks.append(f.read().decode("utf-8", errors="replace"))
        if rotated:
            from ouroboros.utils import jsonl_archive_segments

            segments = jsonl_archive_segments(path)
            carried = None
            for segment in reversed(segments):
                try:
                    seg_stat = segment.stat()
                except OSError:
                    continue
                if (int(seg_stat.st_dev), int(seg_stat.st_ino)) == (dev, ino):
                    carried = segment
                    break
            if carried is not None:
                with carried.open("rb") as sf:
                    sf.seek(min(offset_bytes, carried.stat().st_size))
                    chunks.insert(0, sf.read().decode("utf-8", errors="replace"))
            elif segments:
                chunks.insert(0, segments[-1].read_bytes().decode("utf-8", errors="replace"))
    except Exception:
        log.debug("Suppressed exception", exc_info=True)
        return None

    for line in "".join(chunks).splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            evt = json.loads(raw)
        except Exception:
            log.debug("Suppressed exception in loop", exc_info=True)
            continue
        if isinstance(evt, dict) and str(evt.get("type") or "") == event_type:
            return evt
    return None


def _first_worker_boot_event_since(cursor: Tuple[int, int, int]) -> Optional[Dict[str, Any]]:
    return _first_worker_event_since(cursor, "worker_boot")


def _verify_worker_sha_after_spawn(
    events_cursor: Tuple[int, int, int], timeout_sec: float = 90.0,
) -> None:
    """Verify newly spawned workers booted at expected current_sha."""
    st = _pool().load_state()
    expected_sha = str(st.get("current_sha") or "").strip()
    if not expected_sha:
        append_jsonl(
            _pool().DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": utc_now_iso(),
                "type": "worker_sha_verify_skipped",
                "reason": "missing_current_sha",
            },
        )
        return

    deadline = time.time() + max(float(timeout_sec), 1.0)
    boot_evt = None
    while time.time() < deadline:
        boot_evt = _first_worker_boot_event_since(events_cursor)
        if boot_evt is not None:
            break
        time.sleep(0.25)

    if boot_evt is None:
        append_jsonl(
            _pool().DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": utc_now_iso(),
                "type": "worker_sha_verify_timeout",
                "expected_sha": expected_sha,
            },
        )
        return

    observed_sha = str(boot_evt.get("git_sha") or "").strip()
    ok = bool(observed_sha and observed_sha == expected_sha)
    append_jsonl(
        _pool().DRIVE_ROOT / "logs" / "supervisor.jsonl",
        {
            "ts": utc_now_iso(),
            "type": "worker_sha_verify",
            "ok": ok,
            "expected_sha": expected_sha,
            "observed_sha": observed_sha,
            "worker_pid": boot_evt.get("pid"),
        },
    )
    if not ok and st.get("owner_chat_id"):
        _pool().send_with_budget(
            int(st["owner_chat_id"]),
            f"⚠️ Worker SHA mismatch after spawn: expected {expected_sha[:8]}, got {(observed_sha or 'unknown')[:8]}",
        )


def _worker_pids_path() -> pathlib.Path:
    return _pool().DRIVE_ROOT / "state" / _pool()._WORKER_PIDS_FILENAME


def _record_worker_pids() -> None:
    """Persist current worker PIDs so a later server instance can reap any that
    survive an abrupt restart. Workers run in their own ``os.setsid`` session, so
    when the parent server dies they are reparented to init and outlive it."""
    try:
        from ouroboros.utils import atomic_write_json
        recs = [{"pid": int(w.proc.pid)} for w in _pool().WORKERS.values() if w.proc.pid]
        atomic_write_json(
            _worker_pids_path(),
            {"server_pid": os.getpid(), "ts": utc_now_iso(), "workers": recs},
            trailing_newline=True,
        )
    except Exception:
        log.debug("Failed to record worker pids", exc_info=True)
    # Write-through into the custody ledger (SSOT for the generation reaper);
    # worker_pids.json stays as the legacy session-leader reap path.
    try:
        from ouroboros.process_custody import record_process

        for w in _pool().WORKERS.values():
            if w.proc.pid:
                record_process(
                    _pool().DRIVE_ROOT,
                    pid=int(w.proc.pid),
                    cmd=f"ouroboros-worker-{w.wid}",
                    purpose=f"worker:{w.wid}",
                    scope="session",
                )
    except Exception:
        log.debug("Failed to ledger worker pids", exc_info=True)


def reap_orphaned_workers() -> int:
    """Kill leftover worker process groups left by a PRIOR server instance.

    ``kill_workers`` only walks the in-memory ``WORKERS`` dict, so workers
    orphaned by an abrupt restart (reparented to init, ~one Python interpreter
    each) were never reaped and accumulated across restarts. On startup we read
    the prior pid record and force-kill any that are still alive AND verifiably
    ours — cmdline matches this interpreter/multiprocessing and the process is
    its own session leader (``pgid == pid``) — which guards against PID reuse and
    bounds the group kill to the worker's own setsid session."""
    try:
        from ouroboros.utils import read_json_dict
        from ouroboros.platform_layer import (
            force_kill_pid,
            kill_process_group_id,
            process_command,
            process_group_id,
        )
    except Exception:
        return 0
    data = read_json_dict(_worker_pids_path()) or {}
    prior = data.get("workers") or []
    if not isinstance(prior, list) or not prior:
        return 0
    current = {w.proc.pid for w in _pool().WORKERS.values() if w.proc.pid}
    killed: List[int] = []
    for rec in prior:
        try:
            pid = int((rec or {}).get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if not pid or pid in current or pid == os.getpid():
            continue
        cmd = process_command(pid)
        if not cmd:
            continue  # already dead
        if sys.executable not in cmd and "multiprocessing" not in cmd:
            continue  # PID reused by an unrelated process — do not touch it
        pgid = process_group_id(pid)
        if pgid and pgid == pid:
            kill_process_group_id(pgid)  # the worker's own setsid session
        force_kill_pid(pid)
        killed.append(pid)
    if killed:
        try:
            append_jsonl(
                _pool().DRIVE_ROOT / "logs" / "supervisor.jsonl",
                {"ts": utc_now_iso(), "type": "orphaned_workers_reaped", "pids": killed},
            )
        except Exception:
            log.debug("Failed to log orphaned worker reap", exc_info=True)
    return len(killed)


@_serialized_worker_lifecycle
def kill_workers_for_update(*, result_reason: str, terminal_status: str = "interrupted") -> List[str]:
    """Stop the current pool and return anything whose death could not be proven."""
    from ouroboros.platform_layer import kill_pid_tree

    with _queue_lock:
        fenced = list(_pool().WORKERS.values())
    teardown_error = ""
    try:
        kill_ok = _pool().kill_workers(
            result_reason=result_reason,
            terminal_status=terminal_status,
            disable_reason="managed_update",
            preserve_pending=True,
        )
        if kill_ok is False:
            teardown_error = "teardown:queue_snapshot_persist_failed"
    except Exception as exc:
        teardown_error = f"teardown:{type(exc).__name__}: {exc}"
    survivors: List[str] = []
    for worker in fenced:
        try:
            if worker.proc.is_alive() and worker.proc.pid:
                kill_pid_tree(worker.proc.pid)
                worker.proc.join(timeout=3)
            if worker.proc.is_alive():
                survivors.append(f"worker:{worker.proc.pid or worker.wid}")
            else:
                _pool()._reconcile_confirmed_dead_review_owner(
                    int(getattr(worker.proc, "pid", 0) or 0)
                )
        except Exception as exc:
            survivors.append(f"worker:{worker.wid}:{type(exc).__name__}")
    if teardown_error:
        survivors.append(teardown_error)
    return survivors


def _kill_survivors() -> None:
    """Force-kill any workers and their entire descendant trees."""
    from ouroboros.platform_layer import kill_pid_tree
    for w in _pool().WORKERS.values():
        pid = w.proc.pid
        if pid is None:
            continue
        if w.proc.is_alive():
            kill_pid_tree(pid)
            w.proc.join(timeout=2)


@_serialized_worker_lifecycle
def respawn_worker(wid: int) -> bool:
    """Replace one owned slot without forking under the queue RLock.

    The lifecycle lock makes the two-phase check/start/swap mutually exclusive
    with full-pool shutdown/start.  The identity check after ``proc.start()``
    prevents a replacement from being installed if the slot was removed while
    the queue lock was released.
    """
    with _queue_lock:
        old = _pool().WORKERS.get(wid)
    if old is None:
        return False
    ctx = _pool()._get_ctx()
    in_q = ctx.Queue()
    proc = ctx.Process(target=worker_main,
                       args=(wid, in_q, _pool().get_event_q(), str(_pool().REPO_DIR), str(_pool().DRIVE_ROOT),
                             _current_custody_session_id()))
    proc.daemon = True
    try:
        proc.start()
    except Exception:
        try:
            in_q.close()
            in_q.cancel_join_thread()
        except Exception:
            pass
        raise
    installed = False
    with _queue_lock:
        if _pool().WORKERS.get(wid) is old:
            _pool().WORKERS[wid] = _pool().Worker(wid=wid, proc=proc, in_q=in_q, busy_task_id=None)
            installed = True
    if not installed:
        try:
            from ouroboros.platform_layer import kill_pid_tree

            if proc.pid:
                kill_pid_tree(proc.pid)
            elif proc.is_alive():
                proc.terminate()
            proc.join(timeout=2)
        finally:
            try:
                in_q.close()
                in_q.cancel_join_thread()
            except Exception:
                pass
        return False
    # Close the crashed worker's old queue now that nothing can route to it,
    # otherwise its file descriptors / semaphores leak on every respawn.
    if old is not None and getattr(old, "in_q", None) is not None:
        try:
            old.in_q.close()
            old.in_q.cancel_join_thread()
        except Exception:
            log.debug("Failed to close old worker queue on respawn", exc_info=True)
    _record_worker_pids()
    # Do not reset _LAST_SPAWN_TIME here; respawn grace would hide crash storms.
    return True
