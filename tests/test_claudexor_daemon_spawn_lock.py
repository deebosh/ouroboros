"""Regression: claudexord spawn block must use an inter-process file lock.

Closes ibl-789ba1c62432 (and downstream ibl-40d68a55670f, ibl-700af8759241,
ibl-5845b8f705af, ibl-bee2172abf6b) — without a file lock around the spawn
block, two worker processes would each call spawn_supervised() concurrently
against the same home, the second claudexord loses the writer-lease on the
control socket, and the loser waits the full polling window before
surfacing a typed failure. That race is the root cause of ``crd-0003``
(advisory freshness debt) re-opening on every reviewed commit: the
triad's claude slot fails because the claudexord session is lost, quorum
falls to 1 of 2, advisory goes stale, worktree is never clean for fresh
advisory coverage.

The fix mirrors the existing pattern from ``claudexor_runtime._install``
(install.lock) — same ``acquire_exclusive_file_lock`` / ``release_exclusive_file_lock``
helpers from ``ouroboros.platform_layer``. Distinct lock file (spawn.lock
vs install.lock) because install and spawn are separate concerns:
install serializes runtime tree preparation under ``data/state/cx``, spawn
serializes daemon process startup under ``data/claudexor``.
"""

from __future__ import annotations

import json
import pathlib
import threading
from unittest.mock import MagicMock

import pytest

from ouroboros import claudexor_daemon as owned


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_descriptor(config_dir: pathlib.Path, port: int = 45678) -> None:
    """Write a minimal control-api.json + token that ``_classify_liveness`` accepts."""
    daemon_dir = config_dir / "daemon"
    daemon_dir.mkdir(parents=True, exist_ok=True)
    (daemon_dir / "token").write_text("tok-test", encoding="utf-8")
    (daemon_dir / "control-api.json").write_text(json.dumps({
        "host": "127.0.0.1", "port": port, "tokenPath": str(daemon_dir / "token"),
    }), encoding="utf-8")


def _isolate_home(monkeypatch, tmp_path) -> pathlib.Path:
    """Patch the per-process paths so two OwnedClaudexorDaemon instances
    (two threads in the same process, mirroring two worker OS processes)
    see the same config home."""
    import ouroboros.config as config_mod

    cfg = tmp_path / "cfg"
    monkeypatch.setattr(config_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(owned, "owned_config_dir", lambda: cfg)
    monkeypatch.setattr(owned, "owned_descriptor_path", lambda: cfg / "daemon" / "control-api.json")
    monkeypatch.setattr(owned, "owned_daemon_provisioned",
                        lambda: (cfg / "daemon" / "control-api.json").is_file())
    return cfg


# ---------------------------------------------------------------------------
# structural: lock is acquired + released around the spawn block
# ---------------------------------------------------------------------------


def test_spawn_block_acquires_and_releases_inter_process_file_lock(monkeypatch, tmp_path):
    """The structural fix: spawn block wrapped in acquire/release_exclusive_file_lock."""
    from ouroboros.gateways.claudexor import ClaudexorUnavailable
    from ouroboros import claudexor_runtime as runtime

    cfg = _isolate_home(monkeypatch, tmp_path)
    cfg.mkdir(parents=True, exist_ok=True)

    acquired_paths: list[str] = []
    released: list[tuple[str, object]] = []

    def fake_acquire(path, *, timeout_sec, stale_sec, metadata=""):
        acquired_paths.append(str(path))
        return 42  # fake fd

    def fake_release(path, fd):
        released.append((str(path), fd))

    import ouroboros.platform_layer as platform_layer
    monkeypatch.setattr(platform_layer, "acquire_exclusive_file_lock", fake_acquire)
    monkeypatch.setattr(platform_layer, "release_exclusive_file_lock", fake_release)

    # liveness = stale so spawn proceeds; first _alive_endpoint() raises to
    # bail the polling loop without a real daemon
    monkeypatch.setattr(owned.OwnedClaudexorDaemon, "_classify_liveness",
                        lambda self: (None, "stale", ""))

    call_state = {"n": 0}

    def fake_alive_endpoint(self, *, timeout_sec=None):
        call_state["n"] += 1
        if call_state["n"] == 1:
            return None
        raise ClaudexorUnavailable("test_bail", "polling loop ended")

    monkeypatch.setattr(owned.OwnedClaudexorDaemon, "_alive_endpoint", fake_alive_endpoint)

    class FakeRuntime:
        def ensure(self):
            return ["/bin/true"]

        def status(self):
            return {"source": "test"}

    monkeypatch.setattr(runtime, "get_runtime_manager", lambda: FakeRuntime())
    monkeypatch.setattr("ouroboros.process_custody.spawn_supervised",
                        lambda *args, **kwargs: MagicMock(poll=MagicMock(return_value=1)))

    manager = owned.OwnedClaudexorDaemon()
    with pytest.raises(ClaudexorUnavailable):
        manager.ensure_running()

    # Lock acquired exactly once, on the spawn.lock path, released with the same fd
    assert acquired_paths == [str(cfg / "spawn.lock")], acquired_paths
    assert released == [(str(cfg / "spawn.lock"), 42)], released


# ---------------------------------------------------------------------------
# happy path: liveness found inside the lock, no spawn happens
# ---------------------------------------------------------------------------


def test_live_daemon_found_inside_lock_skips_spawn(monkeypatch, tmp_path):
    """If we acquire the spawn lock and discover a live daemon already exists
    (the previous holder just succeeded), we MUST reuse it — no duplicate spawn."""
    cfg = _isolate_home(monkeypatch, tmp_path)
    cfg.mkdir(parents=True, exist_ok=True)
    _write_descriptor(cfg, port=45679)

    # Force every liveness probe to find a live endpoint
    live_endpoint = object()
    monkeypatch.setattr(owned.OwnedClaudexorDaemon, "_classify_liveness",
                        lambda self: (live_endpoint, "running", ""))
    monkeypatch.setattr(owned.OwnedClaudexorDaemon, "_alive_endpoint",
                        lambda self, *, timeout_sec=None: live_endpoint)

    spawn_calls: list[tuple] = []

    def fake_spawn(*args, **kwargs):
        spawn_calls.append((args, kwargs))
        raise AssertionError("spawn_supervised must not be called when liveness is alive")

    monkeypatch.setattr("ouroboros.process_custody.spawn_supervised", fake_spawn)

    import ouroboros.platform_layer as platform_layer
    monkeypatch.setattr(platform_layer, "acquire_exclusive_file_lock",
                        lambda *a, **kw: 99)
    monkeypatch.setattr(platform_layer, "release_exclusive_file_lock",
                        lambda path, fd: None)

    # No runtime manager needed — endpoint is alive so we never reach ensure()
    from ouroboros import claudexor_runtime as runtime
    monkeypatch.setattr(runtime, "get_runtime_manager", lambda: MagicMock())

    manager = owned.OwnedClaudexorDaemon()
    endpoint = manager.ensure_running()
    assert endpoint is live_endpoint
    assert spawn_calls == []


# ---------------------------------------------------------------------------
# lock timeout: another worker holds the lock past the wait ceiling
# ---------------------------------------------------------------------------


def test_lock_timeout_rechecks_liveness_and_returns_live(monkeypatch, tmp_path):
    """If acquire times out AND liveness re-check finds a live daemon, return
    the live endpoint — never raise. (This is the "other worker about to
    finish" path the owner spelled out.)"""
    cfg = _isolate_home(monkeypatch, tmp_path)
    cfg.mkdir(parents=True, exist_ok=True)
    _write_descriptor(cfg, port=45680)

    live_endpoint = object()
    monkeypatch.setattr(owned.OwnedClaudexorDaemon, "_classify_liveness",
                        lambda self: (live_endpoint, "running", ""))

    # acquire returns None (= timeout); release is a no-op because fd is None
    import ouroboros.platform_layer as platform_layer
    monkeypatch.setattr(platform_layer, "acquire_exclusive_file_lock",
                        lambda *a, **kw: None)
    monkeypatch.setattr(platform_layer, "release_exclusive_file_lock",
                        lambda path, fd: None)

    import ouroboros.claudexor_runtime as runtime
    monkeypatch.setattr(runtime, "get_runtime_manager", lambda: MagicMock())

    manager = owned.OwnedClaudexorDaemon()
    endpoint = manager.ensure_running()
    assert endpoint is live_endpoint


def test_lock_timeout_with_no_live_endpoint_raises_typed_error(monkeypatch, tmp_path):
    """If acquire times out AND no live daemon exists, raise the typed
    daemon_spawn_race_lost error — the holder genuinely failed."""
    from ouroboros.gateways.claudexor import ClaudexorUnavailable

    cfg = _isolate_home(monkeypatch, tmp_path)
    cfg.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(owned.OwnedClaudexorDaemon, "_classify_liveness",
                        lambda self: (None, "stale", ""))

    import ouroboros.platform_layer as platform_layer
    monkeypatch.setattr(platform_layer, "acquire_exclusive_file_lock",
                        lambda *a, **kw: None)
    monkeypatch.setattr(platform_layer, "release_exclusive_file_lock",
                        lambda path, fd: None)

    import ouroboros.claudexor_runtime as runtime
    monkeypatch.setattr(runtime, "get_runtime_manager", lambda: MagicMock())

    manager = owned.OwnedClaudexorDaemon()
    with pytest.raises(ClaudexorUnavailable) as err:
        manager.ensure_running()
    assert err.value.code == "daemon_spawn_race_lost"


# ---------------------------------------------------------------------------
# concurrency: two threads, only one spawn happens
# ---------------------------------------------------------------------------


def test_concurrent_ensure_running_spawns_exactly_once(monkeypatch, tmp_path):
    """Two threads call ensure_running() concurrently against the same home.
    The file lock must serialize them so spawn_supervised is called exactly
    once; the second thread reuses the first thread's live endpoint.

    This is the regression for ibl-789ba1c62432 — without the file lock,
    the second thread would either (a) lose the socket lease and wait the
    full polling window before failing, or (b) spawn a second claudexord
    process that immediately exits. With the lock, only one spawn happens.

    Sequencing: thread A acquires ``self._lock``, sees liveness stale,
    acquires the spawn lock, spawns. After spawn, the polling loop sees
    liveness alive (set by the spawn hook). Thread B was blocked on
    ``self._lock``; once it runs, liveness is alive everywhere — its
    in-process classification returns live immediately, so it never reaches
    spawn path."""
    from ouroboros import claudexor_runtime as runtime

    cfg = _isolate_home(monkeypatch, tmp_path)
    cfg.mkdir(parents=True, exist_ok=True)
    _write_descriptor(cfg, port=45681)

    spawn_lock = threading.Lock()
    spawn_state = {"count": 0}
    live_endpoint = object()

    class FakeProc:
        def poll(self):
            return None  # alive

    def fake_spawn(*args, **kwargs):
        with spawn_lock:
            spawn_state["count"] += 1
        # Once we have spawned, liveness is alive from any later probe.
        return FakeProc()

    monkeypatch.setattr("ouroboros.process_custody.spawn_supervised", fake_spawn)

    # Liveness gating: stale UNTIL spawn completes, then alive.
    alive_after_spawn = threading.Event()

    def fake_classify_liveness(self, *, timeout_sec=None):
        if alive_after_spawn.is_set():
            return live_endpoint, "running", ""
        return None, "stale", ""

    def fake_alive_endpoint(self, *, timeout_sec=None):
        return live_endpoint if alive_after_spawn.is_set() else None

    monkeypatch.setattr(owned.OwnedClaudexorDaemon, "_classify_liveness",
                        fake_classify_liveness)
    monkeypatch.setattr(owned.OwnedClaudexorDaemon, "_alive_endpoint",
                        fake_alive_endpoint)

    class FakeRuntime:
        def ensure(self):
            return ["/bin/true"]

        def status(self):
            return {"source": "test"}

    monkeypatch.setattr(runtime, "get_runtime_manager", lambda: FakeRuntime())

    # Patch spawn_supervised so the Event is set right after the spawn call,
    # mirroring a real claudexord publishing its descriptor within the polling
    # window — so thread A's polling loop sees alive and returns; thread B's
    # later classification also sees alive and skips the spawn path.
    def spawn_then_publish(*args, **kwargs):
        result = fake_spawn(*args, **kwargs)
        alive_after_spawn.set()
        return result

    monkeypatch.setattr("ouroboros.process_custody.spawn_supervised", spawn_then_publish)

    barrier = threading.Barrier(2)
    results: list[object] = []
    results_lock = threading.Lock()

    def worker() -> None:
        try:
            manager = owned.OwnedClaudexorDaemon()
            barrier.wait(timeout=5)
            endpoint = manager.ensure_running()
        except Exception as exc:
            endpoint = exc
        with results_lock:
            results.append(endpoint)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 2, f"both threads should have completed, got {results!r}"
    assert spawn_state["count"] == 1, (
        f"expected exactly 1 spawn across two concurrent threads, got {spawn_state['count']}"
    )
    for r in results:
        assert r is live_endpoint, f"both threads must reuse the live endpoint, got {r!r}"
