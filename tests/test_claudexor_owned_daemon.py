"""Owned Claudexor daemon (D30): the isolation root, discovery cutover and lifecycle.

This module owns the data-plane config dir, discovery of the owned home, the refusal
to kill a daemon it did not start, the stale-home lifecycle — dead daemon restarted
and reconciled, live foreign responder disclosed rather than killed, foreign home
never adopted, exact runtime pin never repaired in place, staged update activated
only at the next natural start — the spawn environment it hands the child, and the
proxy count the docs claim.

The account surface, the no-terminal login jobs, the executor fact on the chat frame
and the status payload fan-out were split verbatim into
``tests/test_claudexor_login_accounts.py``, ``tests/test_claudexor_login_jobs.py``,
``tests/test_claudexor_executor_frame.py`` and
``tests/test_claudexor_status_payload.py``.

Everything here is offline: no daemon is spawned, no network is touched. The
live login flow is the daemon's own product surface and is exercised by the
phase acceptance run, not by unit tests.
"""
import json
import pathlib

import pytest

from ouroboros import claudexor_daemon as owned


def _write_descriptor(config_dir: pathlib.Path, *, port: int = 45678) -> None:
    daemon_dir = config_dir / "daemon"
    daemon_dir.mkdir(parents=True, exist_ok=True)
    (daemon_dir / "token").write_text("tok-owned", encoding="utf-8")
    (daemon_dir / "control-api.json").write_text(json.dumps({
        "host": "127.0.0.1", "port": port, "tokenPath": str(daemon_dir / "token"),
    }), encoding="utf-8")


def test_owned_config_dir_is_data_plane():
    from ouroboros.config import DATA_DIR

    config_dir = owned.owned_config_dir()
    assert str(config_dir).startswith(str(DATA_DIR))
    # The operator's personal state must never be the owned root.
    assert ".claudexor" not in str(config_dir.relative_to(pathlib.Path(DATA_DIR)))


def test_attach_login_command_targets_the_owned_home():
    """The fallback card's copy-paste command (D30): the user's own terminal,
    the OWNED config dir — never a terminal surface inside the UI."""
    command = owned.attach_login_command("job-123")
    assert command.startswith(f"CLAUDEXOR_CONFIG_DIR={owned.owned_config_dir()} ")
    assert command.endswith("claudexor setup attach job-123")


def test_resolve_claudexord_explicit_setting_must_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("OUROBOROS_CLAUDEXOR_BIN", str(tmp_path / "missing"))
    assert owned.resolve_claudexord() == ""
    real = tmp_path / "claudexord"
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("OUROBOROS_CLAUDEXOR_BIN", str(real))
    assert owned.resolve_claudexord() == str(real)


def test_discover_daemon_prefers_owned_home_once_provisioned(monkeypatch, tmp_path):
    """The D30 cutover: default discovery flips to the owned daemon exactly
    when it is provisioned, and stays on the operator layout before that."""
    from ouroboros.gateways import claudexor as gateway_mod

    owned_dir = tmp_path / "data" / "claudexor"
    operator_home = tmp_path / "operator"
    monkeypatch.setattr(owned, "owned_config_dir", lambda: owned_dir)
    monkeypatch.setattr(owned, "owned_descriptor_path",
                        lambda: owned_dir / "daemon" / "control-api.json")
    monkeypatch.setattr(owned, "owned_daemon_provisioned",
                        lambda: (owned_dir / "daemon" / "control-api.json").is_file())
    monkeypatch.setattr(gateway_mod, "operator_home", lambda: operator_home)

    # Not provisioned: the operator layout is the discovery target (and its
    # absence is the typed refusal, proving the owned home was NOT consulted).
    with pytest.raises(gateway_mod.ClaudexorUnavailable) as err:
        gateway_mod.discover_daemon()
    assert "operator" in str(err.value)

    # Provisioned: the owned endpoint wins without any explicit home argument.
    _write_descriptor(owned_dir, port=45679)
    endpoint = gateway_mod.discover_daemon()
    assert (endpoint.port, endpoint.token) == (45679, "tok-owned")

    # An explicit home still reads that home verbatim (delegation callers).
    with pytest.raises(gateway_mod.ClaudexorUnavailable):
        gateway_mod.discover_daemon(home=operator_home)


def test_discover_daemon_at_reads_override_layout(tmp_path):
    from ouroboros.gateways.claudexor import discover_daemon_at

    _write_descriptor(tmp_path / "cfg")
    endpoint = discover_daemon_at(tmp_path / "cfg")
    assert (endpoint.host, endpoint.port) == ("127.0.0.1", 45678)


def test_stop_never_kills_a_daemon_it_did_not_start():
    manager = owned.OwnedClaudexorDaemon()
    assert manager.stop() is False  # nothing self-started -> nothing to kill


def test_ensure_running_without_binary_is_a_typed_refusal(monkeypatch, tmp_path):
    from ouroboros.gateways.claudexor import ClaudexorUnavailable
    from ouroboros import claudexor_runtime as runtime

    import ouroboros.config as config_mod
    # Ownership is verified FIRST (never adopt); this test is about the binary,
    # so the home must be legitimately ours: under the (patched) data plane.
    monkeypatch.setattr(config_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(owned, "owned_config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(owned, "owned_daemon_provisioned", lambda: False)
    class MissingRuntime:
        def ensure(self):
            raise runtime.ClaudexorRuntimeError(
                "claudexord_not_installed", "fixture runtime is absent"
            )

    monkeypatch.setattr(runtime, "get_runtime_manager", lambda: MissingRuntime())
    manager = owned.OwnedClaudexorDaemon()
    with pytest.raises(ClaudexorUnavailable) as err:
        manager.ensure_running()
    assert err.value.code == "claudexord_not_installed"


def test_status_payload_not_provisioned_never_spawns(monkeypatch, tmp_path):
    from ouroboros.gateway.claudexor_accounts import _status_payload

    monkeypatch.setattr(owned, "owned_config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(owned, "owned_daemon_provisioned", lambda: False)
    monkeypatch.setattr(owned, "get_owned_daemon", lambda: owned.OwnedClaudexorDaemon())
    payload = _status_payload(include_models=True)
    assert payload["daemon"]["state"] == "not_provisioned"
    assert payload["harnesses"] == [] and payload["quota"] == []
    assert not (tmp_path / "cfg").exists()  # read-only: nothing provisioned


# ---------------------------------------------------------------------------
# Stale owned-daemon lifecycle (owner directive, pre-synthesis): dead -> restart
# under the same supervision + reconcile; alive-but-foreign -> typed disclosure,
# no kill; foreign home -> never adopt.
# ---------------------------------------------------------------------------



def _stale_home(config_dir: pathlib.Path, *, marker_data_dir: str) -> None:
    """A provisioned home whose daemon is DEAD: descriptor points at a closed
    port, token present, ownership marker written."""
    import json
    import socket

    daemon_dir = config_dir / "daemon"
    daemon_dir.mkdir(parents=True, exist_ok=True)
    (daemon_dir / "token").write_text("tok-dead", encoding="utf-8")
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()  # port free again -> connection refused = dead daemon
    (daemon_dir / "control-api.json").write_text(json.dumps({
        "host": "127.0.0.1", "port": dead_port,
        "tokenPath": str(daemon_dir / "token"),
    }), encoding="utf-8")
    (config_dir / owned.OWNERSHIP_MARKER).write_text(json.dumps({
        "owner": "ouroboros", "data_dir": marker_data_dir,
    }), encoding="utf-8")


def _point_owned_home(monkeypatch, config_dir: pathlib.Path, data_dir: pathlib.Path) -> None:
    monkeypatch.setattr(owned, "owned_config_dir", lambda: config_dir)
    monkeypatch.setattr(owned, "owned_descriptor_path",
                        lambda: config_dir / "daemon" / "control-api.json")
    monkeypatch.setattr(owned, "owned_daemon_provisioned",
                        lambda: (config_dir / "daemon" / "control-api.json").is_file())
    import ouroboros.config as config_mod
    monkeypatch.setattr(config_mod, "DATA_DIR", data_dir)


def test_a_spawn_that_never_publishes_a_descriptor_does_not_leave_the_child_running(
        monkeypatch, tmp_path):
    """The timeout branch raised its typed refusal and walked away from the process it
    had just started. That child is OURS and it is alive — holding the config dir, its
    log, and whatever port it eventually binds — and `self._proc` still pointed at it,
    so the NEXT `ensure_running` spawned a SECOND daemon beside the first. Every retry
    added one. `stop()` could not clean up either: by contract it only ever terminates
    a daemon we successfully started, and this one never became reachable."""
    import sys

    from ouroboros.gateways.claudexor import ClaudexorUnavailable

    data_dir = tmp_path / "data"
    config_dir = data_dir / "claudexor"
    _point_owned_home(monkeypatch, config_dir, data_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OUROBOROS_CLAUDEXOR_BIN", sys.executable)
    monkeypatch.setattr(owned, "_SPAWN_WAIT_SEC", 0.3)
    monkeypatch.setattr(owned, "_SPAWN_POLL_SEC", 0.05)

    class _NeverReadyChild:
        """Alive, but it never writes a descriptor — so discovery never succeeds."""

        pid = 424242

        def __init__(self):
            self.terminated = 0

        def poll(self):
            return None                      # still running

        def terminate(self):
            self.terminated += 1

    child = _NeverReadyChild()
    import ouroboros.platform_layer as platform_layer
    monkeypatch.setattr(platform_layer, "process_group_id", lambda _pid: 0)
    monkeypatch.setattr(owned, "spawn_supervised", lambda *a, **k: child, raising=False)
    import ouroboros.process_custody as custody_mod
    monkeypatch.setattr(custody_mod, "spawn_supervised", lambda *a, **k: child)

    manager = owned.OwnedClaudexorDaemon()
    with pytest.raises(ClaudexorUnavailable) as err:
        manager.ensure_running()
    assert err.value.code == "daemon_spawn_failed"

    # The child we started is stopped, and the handle is forgotten so the next
    # attempt starts ONE daemon rather than a second one beside a live orphan.
    assert child.terminated == 1, "the spawned child was left running"
    assert manager._proc is None
    assert manager.stop() is False


def test_first_spawn_loser_attaches_to_the_winners_endpoint(monkeypatch, tmp_path):
    """A concurrent first-use winner is success, not a false spawn failure."""
    from ouroboros import claudexor_runtime as runtime
    from ouroboros.gateways.claudexor import DaemonEndpoint

    data_dir = tmp_path / "data"
    config_dir = data_dir / "claudexor"
    _point_owned_home(monkeypatch, config_dir, data_dir)
    monkeypatch.setattr(owned, "verify_owned_home", lambda: "")

    class ReadyRuntime:
        def ensure(self):
            return ["/fixture/node", "/fixture/claudexord.bundle.cjs"]

        def status(self):
            return {"source": "download"}

    monkeypatch.setattr(runtime, "get_runtime_manager", lambda: ReadyRuntime())

    class ExitedLoser:
        pid = 424243

        def poll(self):
            return 1

        def terminate(self):
            raise AssertionError("an exited loser must not be terminated")

    child = ExitedLoser()
    import ouroboros.process_custody as custody_mod

    monkeypatch.setattr(custody_mod, "spawn_supervised", lambda *_args, **_kwargs: child)
    endpoint = DaemonEndpoint(host="127.0.0.1", port=45681, token="winner-token")
    manager = owned.OwnedClaudexorDaemon()
    monkeypatch.setattr(manager, "_classify_liveness", lambda: (None, "not_provisioned", ""))
    monkeypatch.setattr(manager, "_alive_endpoint", lambda: endpoint)
    rotations = []
    monkeypatch.setattr(manager, "_enable_rotation", rotations.append)

    assert manager.ensure_running() is endpoint
    assert rotations == [endpoint]
    assert manager._proc is None
    assert manager.stop() is False


def test_dead_owned_daemon_is_restarted_and_reconciled(monkeypatch, tmp_path):
    """The stale case end-to-end: descriptor exists, daemon dead, ownership
    marker OURS -> ensure_running restarts under the same supervision
    chokepoint and reconciles by fresh discovery + an AUTHENTICATED handshake
    against the NEW descriptor the restarted daemon wrote.

    The scripted daemon serves its /v2/handshake IN-PROCESS (the sandbox kills
    exec'd children that bind sockets); the supervised child is a harmless
    sleeper, so the supervision arguments and the stop() path stay real.
    """
    import http.server
    import json as _json
    import subprocess as sp
    import sys
    import threading

    data_dir = tmp_path / "data"
    config_dir = data_dir / "claudexor"
    _point_owned_home(monkeypatch, config_dir, data_dir)
    _stale_home(config_dir, marker_data_dir=str(data_dir.resolve()))
    old_descriptor = (config_dir / "daemon" / "control-api.json").read_text()

    monkeypatch.setenv("OUROBOROS_CLAUDEXOR_BIN", sys.executable)
    spawned: dict = {}
    servers: list = []
    import ouroboros.process_custody as custody_mod

    def fake_spawn(cmd, **kwargs):
        # The SAME chokepoint ensure_running calls; acts like claudexord:
        # mint a fresh token, serve an authenticated handshake, REWRITE the
        # discovery descriptor — then hand back a real supervised child.
        spawned["cmd"] = list(cmd)
        spawned["kwargs"] = {k: kwargs.get(k) for k in ("purpose", "scope")}
        home = pathlib.Path(kwargs["env"]["CLAUDEXOR_CONFIG_DIR"])
        daemon_dir = home / "daemon"
        daemon_dir.mkdir(parents=True, exist_ok=True)
        token = "tok-restarted"
        (daemon_dir / "token").write_text(token, encoding="utf-8")

        class _Daemon(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                # Drain the request body or the NEXT keep-alive request parses
                # mid-stream (the "{json}GET" unsupported-method failure shape).
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                ok = self.headers.get("Authorization") == f"Bearer {token}"
                body = _json.dumps({"compatible": True, "protocolMajor": 3,
                                    "engine": {"version": "9.9.9"}}).encode() if ok else b"{}"
                self.send_response(200 if ok else 401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), _Daemon)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        (daemon_dir / "control-api.json").write_text(_json.dumps({
            "host": "127.0.0.1", "port": server.server_address[1],
            "tokenPath": str(daemon_dir / "token"),
        }), encoding="utf-8")
        # A NEW process group, exactly like the real spawn_supervised child:
        # stop() kills by GROUP id, and a group-sharing fake would take the
        # test process down with it (the SIGKILL-137 this fixture first hit).
        return sp.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                        stdin=sp.DEVNULL, stdout=sp.DEVNULL, stderr=sp.DEVNULL,
                        start_new_session=True)

    monkeypatch.setattr(custody_mod, "spawn_supervised", fake_spawn)

    manager = owned.OwnedClaudexorDaemon()
    assert manager.status_dict()["state"] == "stale"
    try:
        endpoint = manager.ensure_running()
        # Reconciled: the NEW descriptor was re-read and answered our token.
        new_descriptor = (config_dir / "daemon" / "control-api.json").read_text()
        assert new_descriptor != old_descriptor
        assert endpoint.port == _json.loads(new_descriptor)["port"]
        assert spawned["kwargs"] == {"purpose": "claudexor_daemon", "scope": "session"}
        assert manager.status_dict()["state"] == "running"
        # The provision moment (re)wrote OUR ownership marker.
        assert owned.read_ownership_marker()["data_dir"] == str(data_dir.resolve())
        # Restart-only-ours: stop() terminates the SELF-STARTED child.
        assert manager.stop() is True
    finally:
        manager.stop()
        for server in servers:
            server.shutdown()


def test_foreign_responder_on_stale_port_is_disclosed_not_killed(monkeypatch, tmp_path):
    """A live daemon that REFUSES our token on the stale port is foreign:
    typed disclosure, no kill — and it does not block restarting OUR daemon."""
    import http.server
    import json as _json
    import threading

    data_dir = tmp_path / "data"
    config_dir = data_dir / "claudexor"
    _point_owned_home(monkeypatch, config_dir, data_dir)
    _stale_home(config_dir, marker_data_dir=str(data_dir.resolve()))

    class _Refuser(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            body = b"{}"
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    foreign = http.server.HTTPServer(("127.0.0.1", 0), _Refuser)
    threading.Thread(target=foreign.serve_forever, daemon=True).start()
    try:
        descriptor = config_dir / "daemon" / "control-api.json"
        body = _json.loads(descriptor.read_text())
        body["port"] = foreign.server_address[1]
        descriptor.write_text(_json.dumps(body), encoding="utf-8")

        manager = owned.OwnedClaudexorDaemon()
        status = manager.status_dict()
        assert status["state"] == "foreign_daemon"
        assert "REFUSED our home's token" in (status["last_error"] or "")
        # No kill: stop() only ever touches a self-started process.
        assert manager.stop() is False
    finally:
        foreign.shutdown()


def _exact_runtime_pin(runtime_mod):
    """A valid exact pin (all five Node platforms) for lifecycle fixtures."""
    node_artifacts = {
        key: runtime_mod.NodeRuntimeArtifact(
            archive_url=f"https://node.example.test/node-v24.16.0-{key}.tar.gz",
            sha256="a" * 64,
            size_bytes=1,
            executable=f"node-v24.16.0-{key}/bin/node",
        )
        for key in ("darwin-arm64", "darwin-x64", "linux-arm64", "linux-x64", "win32-x64")
    }
    return runtime_mod.ClaudexorRuntimePin(
        version="3.4.0",
        build_sha="1" * 40,
        protocol_major=3,
        archive_url="https://example.test/releases/runtime.tar.gz",
        sha256="b" * 64,
        size_bytes=1,
        node_version="24.16.0",
        node_artifacts=node_artifacts,
        entrypoint="dist/claudexord.js",
    )


def test_live_daemon_serving_the_exact_pin_is_never_repaired_in_place(monkeypatch, tmp_path):
    """S1 guard: when the live authenticated handshake already matches the pin
    (version + build SHA), ensure_running returns the live endpoint WITHOUT
    touching its serving directory — even when the on-disk copy of that SAME
    target is broken. Disk repair belongs to the next natural start (owner
    decision 2A: side-by-side, current work is never touched)."""
    from ouroboros import claudexor_runtime as runtime
    from ouroboros.gateways.claudexor import DaemonEndpoint

    data_dir = tmp_path / "data"
    config_dir = data_dir / "claudexor"
    _point_owned_home(monkeypatch, config_dir, data_dir)

    pin = _exact_runtime_pin(runtime)
    manager = runtime.ClaudexorRuntimeManager(pin)
    monkeypatch.setattr(runtime, "get_runtime_manager", lambda: manager)

    # The daemon's own pinned target on disk is corrupted: any disk probe of
    # this target fails, which without the guard would drive _install and a
    # promote that replaces the serving directory under the live process.
    target = runtime.managed_runtime_dir(pin)
    target.mkdir(parents=True)
    (target / "managed-runtime.json").write_text("{corrupt", encoding="utf-8")

    def no_install(*_args, **_kwargs):
        raise AssertionError("a live pinned daemon's serving directory must not be repaired")

    monkeypatch.setattr(manager, "_install", no_install)
    import ouroboros.process_custody as custody_mod

    def no_spawn(*_args, **_kwargs):
        raise AssertionError("no spawn may happen while the pinned daemon is live")

    monkeypatch.setattr(custody_mod, "spawn_supervised", no_spawn)

    endpoint = DaemonEndpoint(host="127.0.0.1", port=45695, token="live")
    daemon = owned.OwnedClaudexorDaemon()

    def live_classify():
        daemon._engine_version = pin.version
        daemon._engine_build_sha = pin.build_sha
        return endpoint, "running", ""

    monkeypatch.setattr(daemon, "_classify_liveness", live_classify)
    before = sorted(p.name for p in target.parent.iterdir())

    assert daemon.ensure_running() is endpoint
    # The serving directory was not replaced, repaired, or cleaned up.
    assert (target / "managed-runtime.json").read_text(encoding="utf-8") == "{corrupt"
    assert sorted(p.name for p in target.parent.iterdir()) == before
    assert daemon._proc is None


def test_staged_update_activates_only_at_the_next_natural_start(monkeypatch, tmp_path):
    """Staged-activation orchestration (owner decision 2A), end to end: with a
    live OLD daemon, ensure() stages a DIFFERENT exact target and
    ensure_running still answers the old endpoint with no spawn and no stop;
    once the old daemon dies naturally, the next start spawns exactly the
    staged command."""
    from ouroboros import claudexor_runtime as runtime
    from ouroboros.gateways.claudexor import DaemonEndpoint

    data_dir = tmp_path / "data"
    config_dir = data_dir / "claudexor"
    _point_owned_home(monkeypatch, config_dir, data_dir)
    monkeypatch.setattr(owned, "verify_owned_home", lambda: "")

    new_command = ["/fixture/node", "/fixture/state/cx/3.4.0-111111111111/dist/claudexord.js"]
    ensures: list = []

    class _StagedPin:
        version = "3.4.0"
        build_sha = "1" * 40

    class StagingRuntime:
        pin = _StagedPin()

        def ensure(self):
            ensures.append("ensure")
            return list(new_command)

        def status(self):
            return {"source": "download"}

    monkeypatch.setattr(runtime, "get_runtime_manager", lambda: StagingRuntime())

    old_endpoint = DaemonEndpoint(host="127.0.0.1", port=45696, token="old")
    new_endpoint = DaemonEndpoint(host="127.0.0.1", port=45697, token="new")
    daemon = owned.OwnedClaudexorDaemon()
    old_alive = {"value": True}

    def classify():
        if old_alive["value"]:
            daemon._engine_version = "3.2.1"
            daemon._engine_build_sha = "2" * 40
            return old_endpoint, "running", ""
        daemon._engine_version = ""
        daemon._engine_build_sha = ""
        return None, "stale", "connection refused"

    monkeypatch.setattr(daemon, "_classify_liveness", classify)

    spawns: list = []

    class _LiveChild:
        pid = 424244

        def poll(self):
            return None

        def terminate(self):
            raise AssertionError("staging must never stop a daemon")

    import ouroboros.process_custody as custody_mod

    monkeypatch.setattr(
        custody_mod,
        "spawn_supervised",
        lambda command, **_kwargs: spawns.append(list(command)) or _LiveChild(),
    )
    monkeypatch.setattr(daemon, "_alive_endpoint", lambda: new_endpoint)
    monkeypatch.setattr(daemon, "_enable_rotation", lambda _endpoint: None)

    # Phase 1: the OLD endpoint keeps serving; the new target is only staged.
    assert daemon.ensure_running() is old_endpoint
    assert ensures == ["ensure"]
    assert spawns == []
    assert daemon._proc is None  # nothing spawned, nothing stopped

    # Phase 2: the old daemon died naturally; the next start selects the
    # staged exact target the previous ensure() prepared.
    old_alive["value"] = False
    assert daemon.ensure_running() is new_endpoint
    assert ensures == ["ensure", "ensure"]
    assert spawns == [new_command]


def test_a_home_marked_for_another_data_plane_is_never_adopted(monkeypatch, tmp_path):
    """The never-adopt rule: a marker naming a different data plane makes
    ensure_running refuse typed BEFORE any spawn — restart there = adoption."""
    from ouroboros.gateways.claudexor import ClaudexorUnavailable

    data_dir = tmp_path / "data"
    config_dir = data_dir / "claudexor"
    _point_owned_home(monkeypatch, config_dir, data_dir)
    _stale_home(config_dir, marker_data_dir=str(tmp_path / "someone-elses-data"))
    monkeypatch.setenv("OUROBOROS_CLAUDEXOR_BIN", "/bin/true")

    manager = owned.OwnedClaudexorDaemon()
    assert manager.status_dict()["ownership_problem"]
    with pytest.raises(ClaudexorUnavailable) as err:
        manager.ensure_running()
    assert err.value.code == "foreign_daemon_home"


def test_spawn_env_prepends_onto_the_hosts_own_path_key(monkeypatch, tmp_path):
    """WINDOWS PATH-KEY REGRESSION (first live 3-OS gate run on the 3.3.8 pin):
    os.environ materializes with the native "Path" key there, a plain dict
    lookup of "PATH" misses it, and the spawned daemon received a PATH holding
    only the Node bin dir — the engine then refused with git_missing. The env
    builder must prepend onto whichever casing the host actually has and must
    not add a second, differently-cased key beside it."""
    import os as os_mod
    import sys

    from ouroboros.gateways.claudexor import ClaudexorUnavailable

    data_dir = tmp_path / "data"
    config_dir = data_dir / "claudexor"
    _point_owned_home(monkeypatch, config_dir, data_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OUROBOROS_CLAUDEXOR_BIN", sys.executable)
    monkeypatch.setattr(owned, "_SPAWN_WAIT_SEC", 0.05)
    monkeypatch.setattr(owned, "_SPAWN_POLL_SEC", 0.01)

    # Windows-style environment: the variable exists only as "Path".
    fake_environ = {k: v for k, v in os_mod.environ.items() if k.upper() != "PATH"}
    fake_environ["Path"] = "C:/hostedtoolcache/git/bin"
    monkeypatch.setattr(os_mod, "environ", fake_environ)

    captured = {}

    class _NeverReadyChild:
        pid = 424244

        def poll(self):
            return None

        def terminate(self):
            pass

    def _capture_spawn(command, **kwargs):
        captured["env"] = kwargs["env"]
        return _NeverReadyChild()

    import ouroboros.platform_layer as platform_layer

    monkeypatch.setattr(platform_layer, "process_group_id", lambda _pid: 0)
    import ouroboros.process_custody as custody_mod

    monkeypatch.setattr(custody_mod, "spawn_supervised", _capture_spawn)

    manager = owned.OwnedClaudexorDaemon()
    with pytest.raises(ClaudexorUnavailable):
        manager.ensure_running()

    env = captured["env"]
    node_bin = str(pathlib.Path(sys.executable).parent)
    assert "PATH" not in env, "a second differently-cased PATH key was added"
    assert env["Path"].startswith(node_bin + os_mod.pathsep)
    assert env["Path"].endswith("C:/hostedtoolcache/git/bin")


def test_spawn_env_never_leaves_an_empty_path_component(monkeypatch, tmp_path):
    """EMPTY PATH COMPONENT == CWD. The env builder composes the child's PATH as
    f"{command_bin}{os.pathsep}{inherited}", so a host whose environment carries
    no PATH at all (a scrubbed service manager, a bare container unit) yields a
    TRAILING EMPTY component -- and an empty component means the current working
    directory on POSIX. That would make CWD an executable search root for a
    long-lived daemon that shells out to tools of its own.

    Measured, not argued: with a trailing-empty PATH a bare-name binary in the
    working directory EXECUTED (rc 0); the same exec with the empty component
    removed raised FileNotFoundError."""
    import os as os_mod
    import sys

    from ouroboros.gateways.claudexor import ClaudexorUnavailable

    data_dir = tmp_path / "data"
    config_dir = data_dir / "claudexor"
    _point_owned_home(monkeypatch, config_dir, data_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OUROBOROS_CLAUDEXOR_BIN", sys.executable)
    monkeypatch.setattr(owned, "_SPAWN_WAIT_SEC", 0.05)
    monkeypatch.setattr(owned, "_SPAWN_POLL_SEC", 0.01)

    # A host environment with no PATH key in any casing.
    fake_environ = {k: v for k, v in os_mod.environ.items() if k.upper() != "PATH"}
    monkeypatch.setattr(os_mod, "environ", fake_environ)

    captured = {}

    class _NeverReadyChild:
        pid = 424245

        def poll(self):
            return None

        def terminate(self):
            pass

    def _capture_spawn(command, **kwargs):
        captured["env"] = kwargs["env"]
        return _NeverReadyChild()

    import ouroboros.platform_layer as platform_layer

    monkeypatch.setattr(platform_layer, "process_group_id", lambda _pid: 0)
    import ouroboros.process_custody as custody_mod

    monkeypatch.setattr(custody_mod, "spawn_supervised", _capture_spawn)

    manager = owned.OwnedClaudexorDaemon()
    with pytest.raises(ClaudexorUnavailable):
        manager.ensure_running()

    env = captured["env"]
    path_key = next((k for k in env if k.upper() == "PATH"), "")
    assert path_key, "the child received no PATH at all"
    components = env[path_key].split(os_mod.pathsep)
    assert "" not in components, (
        "an empty PATH component reached the daemon's child environment "
        f"({env[path_key]!r}); on POSIX that is the current working directory"
    )
    assert components[0] == str(pathlib.Path(sys.executable).parent)


# ---------------------------------------------------------------------------
# The proxy count is a claim, and claims drift.
# ---------------------------------------------------------------------------


def test_the_proxy_count_in_the_docs_matches_the_handlers_that_exist(tmp_path):
    """The module said "three THIN proxies" while a handler inside it introduced
    itself as "A FOURTH thin proxy" — a file contradicting itself in the only two
    places a reader looks first. A hand-counted number in prose cannot be trusted
    to be re-counted when the fifth one lands, so it is asserted instead.

    ``docs/ARCHITECTURE.md`` carries the same count in its gateway map, and it is
    checked against the ROUTES rather than the handlers: a proxy the map never
    names is a proxy nobody discovers from the architecture doc.
    """
    import inspect
    import re

    from ouroboros.gateway import claudexor_accounts as accounts

    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
    handlers = sorted(
        name for name, obj in vars(accounts).items()
        if name.startswith("api_claudexor_") and inspect.iscoroutinefunction(obj)
    )
    expected = words[len(handlers)]
    docstring = (accounts.__doc__ or "").lower()
    assert f"{expected} thin proxies" in docstring, (
        f"{len(handlers)} handlers ({', '.join(handlers)}) but the module docstring "
        f"does not say \"{expected} THIN proxies\""
    )

    arch = (pathlib.Path(__file__).resolve().parents[1] / "docs" / "ARCHITECTURE.md") \
        .read_text(encoding="utf-8")
    line = next(ln for ln in arch.splitlines() if "claudexor_accounts.py" in ln)
    assert f"{expected} thin proxies" in line.lower(), (
        f"the gateway map still counts a different number of claudexor proxies: {line.strip()[:160]}"
    )

    # Every REGISTERED path is named in that map entry, so a new proxy cannot
    # land undocumented behind an updated count.
    from ouroboros.gateway.router import collect_routes

    paths = {
        route.path for route in collect_routes(data_dir=tmp_path)
        if getattr(route, "path", "").startswith("/api/claudexor/")
    }
    assert paths, "no /api/claudexor/ routes are registered"
    for path in sorted(paths):
        # The map spells path params by name, not by their brace form for the
        # two-segment removal route; compare on the stable prefix.
        prefix = re.split(r"\{", path)[0].rstrip("/")
        assert prefix in line, f"{path} is registered but the gateway map never names it"
