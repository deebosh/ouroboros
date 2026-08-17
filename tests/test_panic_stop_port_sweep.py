"""Which ports a panic stop sweeps, and in what order it hard-exits.

Characterization of the Emergency Stop contract that must survive any change to
how ``server_control.execute_panic_stop`` learns the port the server bound: the
sweep targets the ACTUALLY bound main port (a custom-port install must not
panic-kill an unrelated listener on 8765), the host-service port follows it, and
nothing — including a failing sweep — may delay or reorder the hard exit.

Every destructive operation is neutralized here: no real process, port, daemon
or interpreter teardown runs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class _ExitCalled(RuntimeError):
    pass


def _harness(monkeypatch, tmp_path, *, port_kill=None):
    """Neutralize every destructive teardown op and record the panic timeline."""
    from ouroboros import server_control

    events: list = []

    def _record(name, value=None):
        events.append((name, value) if value is not None else (name,))

    def _kill_port(port):
        _record("kill_process_on_port", port)
        if port_kill is not None:
            port_kill(port)

    monkeypatch.setattr("supervisor.state.load_state", lambda: {})
    monkeypatch.setattr("supervisor.state.save_state", lambda _state: None)
    monkeypatch.setattr(
        "supervisor.evolution_lifecycle.complete_evolution_campaign", lambda *a, **k: {}
    )
    monkeypatch.setattr("ouroboros.post_task_evolution.drop_pending_request", lambda *a, **k: None)
    monkeypatch.setattr(
        "ouroboros.local_model.get_manager",
        lambda: SimpleNamespace(stop_server=lambda: _record("local_model_stop")),
    )
    monkeypatch.setattr(
        "ouroboros.claudexor_daemon.get_owned_daemon",
        lambda: SimpleNamespace(stop=lambda: _record("owned_daemon_stop")),
    )
    monkeypatch.setattr(
        "ouroboros.tools.shell.kill_all_tracked_subprocesses", lambda: _record("kill_shells")
    )
    monkeypatch.setattr(
        "ouroboros.workspace_executor.kill_all_foreground",
        lambda *a, **k: _record("kill_foreground"),
    )
    monkeypatch.setattr(
        "ouroboros.tools.services.kill_all_services", lambda *a, **k: _record("kill_services")
    )
    monkeypatch.setattr(
        "ouroboros.extension_companion.panic_kill_all", lambda: _record("panic_kill_companions")
    )
    monkeypatch.setattr("multiprocessing.active_children", lambda: [])
    monkeypatch.setattr("ouroboros.platform_layer.force_kill_pid", lambda *a, **k: None)
    monkeypatch.setattr("ouroboros.platform_layer.kill_process_on_port", _kill_port)
    monkeypatch.setattr("ouroboros.gateway.host_service.host_service_port", lambda: 8767)
    monkeypatch.setattr(
        server_control.os, "_exit", lambda code: (_ for _ in ()).throw(_ExitCalled(code))
    )
    return events, lambda **kw: _record("kill_workers", kw)


def _swept_ports(events: list) -> list:
    return [value for name, value in (e for e in events if len(e) == 2) if name == "kill_process_on_port"]


def test_panic_through_the_server_sweeps_the_actually_bound_port(monkeypatch, tmp_path):
    """A custom-port install must panic-kill ITS listener, never a stranger on 8765."""
    import server

    events, kill_workers = _harness(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "_ACTUAL_BOUND_PORT", 9123)

    with pytest.raises(_ExitCalled) as exit_info:
        server._execute_panic_stop(SimpleNamespace(stop=lambda: None), kill_workers)

    assert _swept_ports(events) == [9123, 8767]
    assert exit_info.value.args[0] == server.PANIC_EXIT_CODE


def test_panic_with_no_known_bound_port_sweeps_the_default_install_port(monkeypatch, tmp_path):
    """Nothing told this panic which port was bound, so the default install port
    is the last resort — the panic never skips the sweep."""
    from ouroboros import server_control

    events, kill_workers = _harness(monkeypatch, tmp_path)

    with pytest.raises(_ExitCalled):
        server_control.execute_panic_stop(
            consciousness=SimpleNamespace(stop=lambda: None),
            kill_workers_fn=kill_workers,
            data_dir=tmp_path,
            panic_exit_code=120,
            log=SimpleNamespace(critical=lambda *a, **k: None),
        )

    assert _swept_ports(events) == [8765, 8767]


def test_a_failing_main_port_sweep_still_sweeps_the_default_and_the_host_service(
    monkeypatch, tmp_path,
):
    """The main-port sweep is fail-soft: a raising kill must not cost the panic the
    default-port fallback or the host-service sweep that follows it."""
    import server

    def _boom(port):
        if port == 9123:
            raise OSError("port sweep failed")

    events, kill_workers = _harness(monkeypatch, tmp_path, port_kill=_boom)
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "_ACTUAL_BOUND_PORT", 9123)

    with pytest.raises(_ExitCalled):
        server._execute_panic_stop(SimpleNamespace(stop=lambda: None), kill_workers)

    assert _swept_ports(events) == [9123, 8765, 8767]


def test_panic_teardown_order_ends_with_the_port_sweep_then_the_hard_exit(monkeypatch, tmp_path):
    """Cleanup, then the fail-soft child/port sweep, then os._exit — and the
    durable panic flag is written before any of the killing starts."""
    import server

    events, kill_workers = _harness(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "_ACTUAL_BOUND_PORT", 9123)

    with pytest.raises(_ExitCalled):
        server._execute_panic_stop(SimpleNamespace(stop=lambda: None), kill_workers)

    assert [event[0] for event in events] == [
        "local_model_stop",
        "owned_daemon_stop",
        "kill_shells",
        "kill_foreground",
        "kill_services",
        "panic_kill_companions",
        "kill_workers",
        "kill_process_on_port",
        "kill_process_on_port",
    ]
    assert events[6][1] == {"force": True, "archive_service_logs": False}
    assert (tmp_path / "state" / "panic_stop.flag").read_text(encoding="utf-8") == "panic"


def test_emergency_process_cleanup_stays_a_separate_path_from_panic(monkeypatch, tmp_path):
    """The uvicorn-hang cleanup is NOT the panic: it finalizes running tasks with an
    honest interrupted reason and returns, where panic hard-exits. Keeping them
    separate is an explicit design decision, not an oversight."""
    import server

    worker_calls = []
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr("ouroboros.tools.shell.kill_all_tracked_subprocesses", lambda: None)
    monkeypatch.setattr("ouroboros.workspace_executor.kill_all_foreground", lambda *a, **k: None)
    monkeypatch.setattr("ouroboros.tools.services.kill_all_services", lambda *a, **k: None)
    monkeypatch.setattr("supervisor.workers.kill_workers", lambda **kw: worker_calls.append(kw))
    monkeypatch.setattr("multiprocessing.active_children", lambda: [])
    monkeypatch.setattr("ouroboros.platform_layer.force_kill_pid", lambda *a, **k: None)
    monkeypatch.setattr("ouroboros.platform_layer.kill_process_on_port", lambda _port: None)
    monkeypatch.setattr("ouroboros.extension_companion.panic_kill_all", lambda: None)
    monkeypatch.setattr("ouroboros.gateway.host_service.host_service_port", lambda: 8767)

    # Returns normally: no os._exit patch is needed, which is the whole point.
    server._emergency_process_cleanup(port_sweep=False)

    assert worker_calls and worker_calls[0]["force"] is True
