"""Periodic check-and-notify watcher for the managed-update mechanism (v6.103.0).

Read-only by construction: this thread only ever calls ``plan_managed_update_merge``
(isolated temp-worktree dry run) and ``detect_semantic_overlap`` (read-only git +
advisory LLM). It never calls ``apply_managed_merge_update`` or touches the writer
fence — auto-apply from this path is impossible by construction, not convention
(BIBLE P0: apply always stays owner-gated). Modeled on ``supervisor/task_reaper.py``'s
self-healing daemon-thread pattern.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

from ouroboros.utils import atomic_write_json, read_json_dict
from supervisor import git_ops as _g

log = logging.getLogger(__name__)

_WATCH_MARKER_NAME = "ouroboros-update-watch.json"
_TICK_SEC = 60

_watch_thread: "threading.Thread | None" = None
_watch_start_lock = threading.Lock()


def _watch_marker_path():
    return _g._git_dir() / _WATCH_MARKER_NAME


def _read_watch_marker() -> Dict[str, Any]:
    try:
        data = read_json_dict(_watch_marker_path())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_watch_marker(data: Dict[str, Any]) -> None:
    try:
        atomic_write_json(_watch_marker_path(), data)
    except Exception:
        log.debug("update_watch: failed to persist watch marker", exc_info=True)


def ensure_update_watch_started() -> None:
    """Start the watcher thread, or RESTART it if it ever died."""
    global _watch_thread
    t = _watch_thread
    if t is not None and t.is_alive():
        return
    with _watch_start_lock:
        t = _watch_thread
        if t is not None and t.is_alive():
            return
        if t is not None:
            log.warning("Update watch thread had died; restarting it.")
        _watch_thread = threading.Thread(target=update_watch_loop, name="update-watch", daemon=True)
        _watch_thread.start()


def update_watch_loop() -> None:
    while True:
        try:
            _maybe_run_watch_cycle()
        except Exception:
            log.error("update_watch: cycle failed", exc_info=True)
        time.sleep(_TICK_SEC)


def _maybe_run_watch_cycle() -> None:
    from ouroboros.update_channels import (
        get_update_autocheck_enabled,
        get_update_autocheck_interval_sec,
    )

    if not get_update_autocheck_enabled():
        return

    marker = _read_watch_marker()
    try:
        last_run_at = float(marker.get("last_run_at") or 0.0)
    except (TypeError, ValueError):
        last_run_at = 0.0
    interval = get_update_autocheck_interval_sec()
    if time.time() - last_run_at < interval:
        return

    from supervisor.update_merge import acquire_update_lock, active_update_tx, release_update_lock

    if active_update_tx():
        return  # an owner-initiated update is in flight; never contend with it
    try:
        lock_fh = acquire_update_lock()
    except RuntimeError:
        return  # lock held elsewhere; skip this cycle quietly
    try:
        _run_watch_cycle(marker)
    finally:
        release_update_lock(lock_fh)


def _run_watch_cycle(marker: Dict[str, Any]) -> None:
    from supervisor.update_merge import plan_managed_update_merge

    plan = plan_managed_update_merge(fetch=True)
    marker = dict(marker)
    marker["last_run_at"] = time.time()
    if not plan.get("available") or str(plan.get("kind") or "") == "current":
        _write_watch_marker(marker)
        return

    target_sha = str(plan.get("target_sha") or "")
    if not target_sha:
        _write_watch_marker(marker)
        return

    flags: list = []
    candidates = plan.get("overlap_candidates") or {}
    if candidates.get("files"):
        from supervisor.update_semantic_overlap import (
            compute_overlap_candidates,
            detect_semantic_overlap,
            read_semantic_overlap_cache,
            write_semantic_overlap_cache,
        )

        base_sha = str(plan.get("base_sha") or "")
        cached = read_semantic_overlap_cache(base_sha, target_sha)
        if cached is None:
            recomputed = compute_overlap_candidates(base_sha, target_sha)
            cached = (
                detect_semantic_overlap(base_sha, target_sha, recomputed)
                if recomputed.get("files")
                else {"flags": []}
            )
            write_semantic_overlap_cache(base_sha, target_sha, cached)
        flags = list(cached.get("flags") or [])

    relevant_flags = [f for f in flags if str(f.get("verdict") or "") != "related_not_duplicate"]
    fingerprint = f"{target_sha}:{len(relevant_flags)}"
    if marker.get("last_notified_fingerprint") == fingerprint:
        _write_watch_marker(marker)
        return

    _notify_owner_new_update(plan, relevant_flags)
    marker["last_notified_target_sha"] = target_sha
    marker["last_notified_fingerprint"] = fingerprint
    _write_watch_marker(marker)


def _notify_owner_new_update(plan: Dict[str, Any], relevant_flags: list) -> None:
    from supervisor.message_bus import send_with_budget
    from supervisor.state import load_state

    try:
        owner_chat_id = int(load_state().get("owner_chat_id") or 0)
    except (TypeError, ValueError):
        owner_chat_id = 0
    if not owner_chat_id:
        return

    channel = str(plan.get("update_channel") or "")
    kind = str(plan.get("kind") or "unknown")
    target_short = str(plan.get("target_sha") or "")[:12]
    lines = [
        f"🔔 A new official Ouroboros update is available on the {channel or 'configured'} channel "
        f"(target {target_short}, plan: {kind}).",
    ]
    if relevant_flags:
        lines.append(
            f"{len(relevant_flags)} file(s) may already be fixed locally — open Update to review "
            "the semantic-overlap notes before merging."
        )
    lines.append("Nothing is applied automatically — this is a notification only.")
    try:
        send_with_budget(owner_chat_id, "\n".join(lines))
    except Exception:
        log.debug("update_watch: failed to notify owner", exc_info=True)
