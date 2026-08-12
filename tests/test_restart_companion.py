"""Tests for the manual companion-restart surface (ibl-65bd10ebca02).

Covers:
- CompanionSupervisor.restart() across the four states a manual restart
  can land in (alive, dead-but-persisted, never-registered, non-server).
- The per-key isolation of the restart-history reset (R-3): restarting
  companion A does not wipe companion B's window.
- The on-disk snapshot persistence (Option A — descriptor fields in
  ``extension_companions.json``) so restart() can recover a companion
  after ``_monitor_runtime`` cleaned up its runtime entry.
- Audit-log emission shape from the ``restart_companion`` tool.

The tool handler is exercised through a thin fake ``ToolContext`` (the
real ``ToolContext`` carries a ``drive_root`` and a ``task_id`` — both
are sufficient for the audit-log test, no full supervisor is needed).
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, List

import pytest

from ouroboros.extension_companion import (
    CompanionDescriptor,
    CompanionSupervisor,
    RestartResult,
    init_server_process_pid,
)


@pytest.fixture(autouse=True)
def _restore_server_pid():
    init_server_process_pid()
    yield
    init_server_process_pid()


def _start_sleepy(supervisor: CompanionSupervisor, tmp_path: pathlib.Path,
                  skill_name: str = "demo", name: str = "sleepy",
                  seconds: int = 30) -> CompanionDescriptor:
    descriptor = CompanionDescriptor(
        skill_name=skill_name,
        name=name,
        command=[sys.executable, "-c",
                 f"import time; time.sleep({seconds})"],
        cwd=tmp_path,
        env={},
    )
    assert supervisor.start(descriptor) is True
    return descriptor


def _kill(pid: int) -> None:
    try:
        subprocess.run(["kill", "-9", str(pid)], check=False, timeout=2)
    except Exception:
        pass


def _wait_until_dead(supervisor: CompanionSupervisor, key: str, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with supervisor._lock:
            if key not in supervisor._runtimes:
                return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# CompanionSupervisor.restart()
# ---------------------------------------------------------------------------


def test_restart_alive_companion_stops_then_respawns(tmp_path: pathlib.Path) -> None:
    """Restarting an alive companion stops it (capturing pid_before) and respawns."""
    supervisor = CompanionSupervisor(tmp_path)
    descriptor = _start_sleepy(supervisor, tmp_path)

    live = supervisor.snapshot()["demo:sleepy"]
    pid_before = int(live["pid"])

    result = supervisor.restart("demo", "sleepy", reason="manual test")

    assert isinstance(result, RestartResult)
    assert result.success is True
    assert result.error == ""
    assert result.pid_before == pid_before
    assert result.returncode_before is None  # process was alive when captured
    assert result.pid_after is not None
    assert result.pid_after != pid_before

    supervisor.panic_kill_all()


def test_restart_dead_companion_respawns_from_persisted_snapshot(tmp_path: pathlib.Path) -> None:
    """A companion that died (monitor-runtime popped the runtime entry but
    _known_descriptors keeps the descriptor) can still be restarted from the
    on-disk snapshot — the descriptor fields persisted in
    extension_companions.json are the source of truth."""
    supervisor = CompanionSupervisor(tmp_path)
    _start_sleepy(supervisor, tmp_path)

    # Kill the underlying process and wait for _monitor_runtime to pop the
    # RUNTIME entry (the descriptor stays via _known_descriptors).
    pid = int(supervisor.snapshot()["demo:sleepy"]["pid"])
    _kill(pid)
    assert _wait_until_dead(supervisor, "demo:sleepy")

    # The descriptor survives in the snapshot as ``alive=False``.
    snapshot_path = tmp_path / "state" / "extension_companions.json"
    assert snapshot_path.exists()
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert "demo:sleepy" in snapshot
    assert snapshot["demo:sleepy"]["alive"] is False
    assert snapshot["demo:sleepy"]["command"]  # descriptor fields present

    result = supervisor.restart("demo", "sleepy", reason="recover from crash")

    assert result.success is True
    assert result.pid_before is None  # no live runtime when restart started
    assert result.pid_after is not None
    # After restart, the entry is alive again.
    assert supervisor.snapshot()["demo:sleepy"]["alive"] is True

    supervisor.panic_kill_all()


def test_restart_unknown_companion_returns_not_registered(tmp_path: pathlib.Path) -> None:
    """A companion that was never started returns success=False, error=not_registered
    without spawning anything."""
    supervisor = CompanionSupervisor(tmp_path)

    result = supervisor.restart("ghost", "phantom", reason="nonexistent")

    assert result.success is False
    assert result.error == "not_registered"
    assert result.pid_before is None
    assert result.pid_after is None
    assert supervisor.snapshot() == {}


def test_restart_resets_auto_restart_history(tmp_path: pathlib.Path) -> None:
    """A manual restart wipes _restart_history[key] so a subsequent crash loop
    gets a fresh window. Without this, a manual restart could be followed
    immediately by hitting the auto-restart cap on the very next failure."""
    supervisor = CompanionSupervisor(tmp_path)
    descriptor = _start_sleepy(supervisor, tmp_path)

    # Force the restart history up to the cap.
    key = "demo:sleepy"
    with supervisor._lock:
        supervisor._restart_history[key] = [time.monotonic()] * descriptor.max_restarts
    assert len(supervisor._restart_history.get(key, [])) == descriptor.max_restarts

    result = supervisor.restart("demo", "sleepy", reason="reset history")

    assert result.success is True
    with supervisor._lock:
        assert key not in supervisor._restart_history  # wiped clean

    supervisor.panic_kill_all()


def test_restart_resets_history_for_correct_key_only(tmp_path: pathlib.Path) -> None:
    """Restarting companion A must not clear companion B's restart history (R-3).

    Pins the _key() contract from ouroboros/extension_companion.py: a future
    refactor that uses whole-dict clear instead of pop(key) would silently
    disable the restart cap for unrelated companions."""
    supervisor = CompanionSupervisor(tmp_path)
    _start_sleepy(supervisor, tmp_path, skill_name="skill_a", name="daemon")
    _start_sleepy(supervisor, tmp_path, skill_name="skill_b", name="worker")

    # Fill skill_b's history (leave skill_a's empty so the restart of A is
    # observable against B's untouched history).
    with supervisor._lock:
        supervisor._restart_history["skill_b:worker"] = [time.monotonic()] * 3
    b_history_before = list(supervisor._restart_history["skill_b:worker"])

    result = supervisor.restart("skill_a", "daemon", reason="isolate reset")

    assert result.success is True
    # skill_b history untouched
    with supervisor._lock:
        assert supervisor._restart_history.get("skill_b:worker") == b_history_before
        assert "skill_a:daemon" not in supervisor._restart_history

    supervisor.panic_kill_all()


def test_restart_in_non_server_process_returns_no_new_process(tmp_path: pathlib.Path) -> None:
    """CompanionSupervisor.start() short-circuits to False outside is_server_process()
    (extension_companion.py). restart() inherits this constraint: a non-server
    restart must not spawn anything (R-4)."""
    # Pin _SERVER_PROCESS_PID to a foreign value so is_server_process() returns False.
    init_server_process_pid(999_999)
    supervisor = CompanionSupervisor(tmp_path)

    # Seed the on-disk snapshot with a descriptor that restart() can find.
    # (In production this would have been written by start() in the server
    # process; here we bypass start() and write the file directly because
    # start() itself refuses to spawn outside the server process.)
    snapshot_path = tmp_path / "state" / "extension_companions.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps({
        "demo:sleepy": {
            "skill_name": "demo",
            "name": "sleepy",
            "command": [sys.executable, "-c", "import time; time.sleep(30)"],
            "cwd": str(tmp_path),
            "env": {},
            "ports": [],
            "restart_policy": "on_failure",
            "max_restarts": 5,
            "restart_window_sec": 300.0,
            "alive": False,
        },
    }), encoding="utf-8")

    result = supervisor.restart("demo", "sleepy", reason="non-server-process test")

    # start() short-circuited, so no new process was spawned.
    assert result.success is False
    assert result.pid_after is None
    assert supervisor.snapshot() == {}
    # The on-disk snapshot must still carry the descriptor (restart() doesn't
    # destroy it on failure — only on success via the subsequent start()).
    assert "demo:sleepy" in json.loads(snapshot_path.read_text(encoding="utf-8"))


def test_snapshot_persists_descriptor_after_monitor_exit(tmp_path: pathlib.Path) -> None:
    """When _monitor_runtime pops a dead RUNTIME entry, _known_descriptors
    keeps the descriptor and the snapshot still carries the descriptor fields
    (with alive=False). This is the Option-A invariant plus the
    _known_descriptors persistence: stop() and _monitor_runtime both call
    _write_runtime_snapshot(), and the snapshot now reflects the union of
    _known_descriptors (descriptor fields) and _runtimes (live overlay),
    so restart() can read the descriptor after the runtime entry is gone."""
    supervisor = CompanionSupervisor(tmp_path)
    _start_sleepy(supervisor, tmp_path)

    pid = int(supervisor.snapshot()["demo:sleepy"]["pid"])
    _kill(pid)
    assert _wait_until_dead(supervisor, "demo:sleepy")

    # After the monitor popped the runtime entry, the snapshot still carries
    # the descriptor (with alive=False).
    snapshot = json.loads((tmp_path / "state" / "extension_companions.json").read_text(encoding="utf-8"))
    assert "demo:sleepy" in snapshot
    assert snapshot["demo:sleepy"]["alive"] is False
    assert snapshot["demo:sleepy"]["command"]
    assert snapshot["demo:sleepy"]["cwd"]

    # And a live companion carries the same descriptor fields plus the live overlay.
    live_dir = tmp_path / "live"
    live_dir.mkdir(parents=True, exist_ok=True)
    supervisor_for_live = CompanionSupervisor(live_dir)
    live_descriptor = _start_sleepy(supervisor_for_live, live_dir,
                                    skill_name="live_demo", name="alive")
    live_snapshot = json.loads(
        (live_dir / "state" / "extension_companions.json").read_text(encoding="utf-8")
    )
    assert "live_demo:alive" in live_snapshot
    entry = live_snapshot["live_demo:alive"]
    assert entry["alive"] is True
    assert entry["command"] == list(live_descriptor.command)
    assert entry["cwd"] == str(live_descriptor.cwd)
    assert entry["env"] == dict(live_descriptor.env)
    assert entry["restart_policy"] == live_descriptor.restart_policy
    assert entry["max_restarts"] == live_descriptor.max_restarts
    assert entry["restart_window_sec"] == live_descriptor.restart_window_sec

    supervisor.panic_kill_all()
    supervisor_for_live.panic_kill_all()


def test_snapshot_cleaned_after_stop(tmp_path: pathlib.Path) -> None:
    """stop() pops the runtime entry and rewrites the snapshot — the descriptor
    fields disappear with the runtime. restart() on a stopped companion
    therefore returns not_registered (deliberate uninstall/reinstall lifecycle)."""
    supervisor = CompanionSupervisor(tmp_path)
    _start_sleepy(supervisor, tmp_path)

    # Confirm the entry exists.
    snapshot_path = tmp_path / "state" / "extension_companions.json"
    pre_stop = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert "demo:sleepy" in pre_stop

    supervisor.stop("demo", "sleepy", timeout_sec=1.0)
    post_stop = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert "demo:sleepy" not in post_stop

    result = supervisor.restart("demo", "sleepy", reason="post-stop test")
    assert result.success is False
    assert result.error == "not_registered"


# ---------------------------------------------------------------------------
# restart_companion tool handler (audit-log emission + input validation)
# ---------------------------------------------------------------------------


@dataclass
class _FakeToolContext:
    drive_root: pathlib.Path
    task_id: str = "test-task-123"


def _read_audit_rows(drive_root: pathlib.Path) -> List[dict]:
    events = drive_root / "logs" / "events.jsonl"
    if not events.exists():
        return []
    rows: List[dict] = []
    for line in events.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def test_tool_returns_not_registered_when_supervisor_missing(tmp_path: pathlib.Path) -> None:
    """When get_global_supervisor() returns None (typical in tests), the tool
    emits a typed COMPANION_RESTART_ERROR and an audit row carrying the
    supervisor_uninitialized error."""
    from ouroboros.tools.companion import _restart_companion

    ctx = _FakeToolContext(drive_root=tmp_path)
    result = _restart_companion(ctx, skill_name="demo", companion_name="ghost")

    assert "COMPANION_RESTART_ERROR" in result
    assert "supervisor is not initialized" in result

    audit_rows = [r for r in _read_audit_rows(tmp_path) if r.get("type") == "companion_restart"]
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row["skill_name"] == "demo"
    assert row["companion_name"] == "ghost"
    assert row["success"] is False
    assert row["error"] == "supervisor_uninitialized"
    assert row["requested_by"] == "agent"
    assert row["task_id"] == "test-task-123"
    assert "ts" in row


def test_tool_rejects_empty_skill_name(tmp_path: pathlib.Path) -> None:
    """Empty / oversized skill_name is a typed TOOL_ARG_ERROR and emits NO
    audit row (the row would only be noise — the validation is the audit)."""
    from ouroboros.tools.companion import _restart_companion

    ctx = _FakeToolContext(drive_root=tmp_path)
    result = _restart_companion(ctx, skill_name="", companion_name="x")

    assert result.startswith("⚠️ TOOL_ARG_ERROR")
    assert "skill_name" in result
    assert _read_audit_rows(tmp_path) == []


def test_tool_rejects_empty_companion_name(tmp_path: pathlib.Path) -> None:
    from ouroboros.tools.companion import _restart_companion

    ctx = _FakeToolContext(drive_root=tmp_path)
    result = _restart_companion(ctx, skill_name="x", companion_name="")

    assert result.startswith("⚠️ TOOL_ARG_ERROR")
    assert "companion_name" in result
    assert _read_audit_rows(tmp_path) == []


def test_tool_rejects_oversized_name(tmp_path: pathlib.Path) -> None:
    from ouroboros.tools.companion import _restart_companion

    ctx = _FakeToolContext(drive_root=tmp_path)
    long_name = "a" * 201
    result = _restart_companion(ctx, skill_name=long_name, companion_name="x")

    assert result.startswith("⚠️ TOOL_ARG_ERROR")
    assert "200" in result
    assert _read_audit_rows(tmp_path) == []


def test_tool_audit_row_shape_for_alive_restart(tmp_path: pathlib.Path) -> None:
    """End-to-end audit shape: an alive companion restart produces exactly one
    companion_restart row with pid_before, pid_after, returncode_before all
    set; success=True; error=''; requested_by='agent'; task_id from ctx."""
    supervisor = CompanionSupervisor(tmp_path)
    _start_sleepy(supervisor, tmp_path)
    pid_before = int(supervisor.snapshot()["demo:sleepy"]["pid"])

    # Patch the module-level get_global_supervisor to return our test supervisor.
    from ouroboros.tools import companion as companion_module

    original = companion_module.get_global_supervisor
    companion_module.get_global_supervisor = lambda: supervisor  # type: ignore[assignment]
    try:
        ctx = _FakeToolContext(drive_root=tmp_path, task_id="audit-task-99")
        result = companion_module._restart_companion(
            ctx, skill_name="demo", companion_name="sleepy", reason="alive test",
        )
    finally:
        companion_module.get_global_supervisor = original  # type: ignore[assignment]

    payload = json.loads(result)
    assert payload["success"] is True
    assert payload["skill_name"] == "demo"
    assert payload["companion_name"] == "sleepy"
    assert payload["pid_before"] == pid_before
    assert payload["returncode_before"] is None
    assert payload["pid_after"] is not None
    assert payload["pid_after"] != pid_before
    assert payload["reason"] == "alive test"
    assert payload["error"] == ""

    audit_rows = [r for r in _read_audit_rows(tmp_path) if r.get("type") == "companion_restart"]
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row["pid_before"] == pid_before
    assert row["returncode_before"] is None
    assert row["pid_after"] is not None
    assert row["success"] is True
    assert row["error"] == ""
    assert row["requested_by"] == "agent"
    assert row["task_id"] == "audit-task-99"
    assert row["reason"] == "alive test"

    supervisor.panic_kill_all()


def test_tool_returns_json_with_documented_fields(tmp_path: pathlib.Path) -> None:
    """The JSON payload always carries the documented keys (success,
    skill_name, companion_name, pid_before, pid_after, returncode_before,
    error, reason) — callers depend on this shape."""
    from ouroboros.tools import companion as companion_module

    original = companion_module.get_global_supervisor
    companion_module.get_global_supervisor = lambda: None  # supervisor_uninitialized path
    try:
        ctx = _FakeToolContext(drive_root=tmp_path)
        result = companion_module._restart_companion(
            ctx, skill_name="x", companion_name="y", reason="shape test",
        )
    finally:
        companion_module.get_global_supervisor = original  # type: ignore[assignment]

    # supervisor_uninitialized returns the typed error string, not JSON — but the
    # audit row still carries the documented keys. Verify the audit row.
    audit_rows = [r for r in _read_audit_rows(tmp_path) if r.get("type") == "companion_restart"]
    assert len(audit_rows) == 1
    row = audit_rows[0]
    for key in ("ts", "type", "skill_name", "companion_name", "reason",
                "pid_before", "pid_after", "returncode_before",
                "success", "error", "requested_by", "task_id"):
        assert key in row, f"audit row missing required key: {key}"
