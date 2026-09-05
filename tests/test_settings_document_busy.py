"""The bounded settings-document lock answers the SAME typed refusal on every
writer endpoint.

``owner_settings.settings_document_mutation`` acquires the in-process document
lock within ``OUROBOROS_SETTINGS_DOCUMENT_LOCK_TIMEOUT_SEC`` and raises the
typed ``SettingsDocumentBusy`` otherwise (issue #464's second half: a writer
wedged inside its hot-reload effects must not hold every later writer
forever). Onboarding maps it to 503 ``settings_busy``; so must the generic
save and the four single-decision endpoints — one seam
(``settings._run_settings_writer``), never an untyped 500 from one of them
while another answers honestly.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from ouroboros.gateway.settings import (
    api_owner_auto_grant,
    api_owner_context_mode,
    api_owner_runtime_mode,
    api_owner_safety_mode,
    api_settings_post,
)

@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """A fresh on-disk settings document (SETTINGS_PATH/DATA_DIR patched on
    ouroboros.config, the runtime-mode baseline reset around the test)."""
    from ouroboros import config as cfg

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings_path = data_dir / "settings.json"
    monkeypatch.setattr(cfg, "DATA_DIR", data_dir, raising=True)
    monkeypatch.setattr(cfg, "SETTINGS_PATH", settings_path, raising=True)
    cfg.reset_runtime_mode_baseline_for_tests()
    yield settings_path
    cfg.reset_runtime_mode_baseline_for_tests()


WRITERS = [
    ("/api/owner/runtime-mode", api_owner_runtime_mode, {"mode": "light"}),
    ("/api/owner/auto-grant", api_owner_auto_grant, {"enabled": True}),
    ("/api/owner/context-mode", api_owner_context_mode, {"mode": "low"}),
    ("/api/owner/safety-mode", api_owner_safety_mode, {"mode": "full"}),
    ("/api/settings", api_settings_post, {"OUROBOROS_MAX_ROUNDS": "12"}),
]


@pytest.mark.parametrize(("path", "endpoint", "body"), WRITERS, ids=[w[0] for w in WRITERS])
def test_every_settings_writer_answers_the_typed_busy_within_the_bound(
    isolated_settings, monkeypatch, path, endpoint, body,
):
    import ouroboros.config as config
    import ouroboros.gateway.owner_settings as owner_settings

    from supervisor import workers

    isolated_settings.write_text(json.dumps({"OUROBOROS_RUNTIME_MODE": "advanced"}), encoding="utf-8")
    monkeypatch.setattr(config, "get_settings_document_lock_timeout_sec", lambda: 1)
    # The context-mode writer refuses max->low while agent work is live — a
    # pre-lock check that reads the supervisor's process-global maps. Pin them
    # idle so a sibling module's leftovers cannot turn this pin into a 409.
    monkeypatch.setattr(workers, "PENDING", [], raising=False)
    monkeypatch.setattr(workers, "RUNNING", {}, raising=False)
    monkeypatch.setattr(workers, "_chat_agent", SimpleNamespace(_busy=False), raising=False)
    app = Starlette(routes=[Route(path, endpoint=endpoint, methods=["POST"])])
    app.state.drive_root = isolated_settings.parent
    assert owner_settings._settings_document_lock.acquire(timeout=5)
    try:
        response = TestClient(app).post(path, json=body)
    finally:
        owner_settings._settings_document_lock.release()

    assert response.status_code == 503, (path, response.text)
    payload = response.json()
    assert payload["code"] == "settings_busy"
    assert payload.get("saved") is False
    assert "try again" in payload["error"]


def test_the_initiating_writer_returns_within_the_same_bound(monkeypatch):
    """The lock bounds only LATER writers; the INITIATING writer — the Save
    whose body wedges inside its post-commit effects — used to hold the
    request open forever. The seam now caps it at one lock wait plus one held
    episode (twice the lock bound) and answers a typed ``settings_save_timeout``
    with ``saved: null``: the body keeps running in its thread, so whether the
    bytes landed is unknown and neither ``true`` nor ``false`` would be honest.
    """
    import asyncio
    import threading
    import time

    import ouroboros.config as config
    from ouroboros.gateway.settings import _run_settings_writer

    monkeypatch.setattr(config, "get_settings_document_lock_timeout_sec", lambda: 0.2)
    release = threading.Event()
    finished = threading.Event()

    def wedged_writer(_request, _body):
        release.wait(10)
        finished.set()
        return None

    # A long-lived loop, as in the server: ``asyncio.run`` would drain its
    # default executor on exit and wait for the abandoned thread anyway.
    loop = asyncio.new_event_loop()
    try:
        started = time.monotonic()
        response = loop.run_until_complete(
            _run_settings_writer(wedged_writer, SimpleNamespace(), {})
        )
        elapsed = time.monotonic() - started
        assert elapsed < 5, elapsed
        assert not finished.is_set(), "the response must not wait for the wedged body"
        release.set()
        assert finished.wait(10), "the abandoned body must still run to completion in its thread"
        loop.run_until_complete(loop.shutdown_default_executor())
    finally:
        release.set()
        loop.close()

    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload["code"] == "settings_save_timeout"
    assert "saved" in payload and payload["saved"] is None
    assert "still running" in payload["error"]
    assert "reload Settings" in payload["error"]


def test_the_typed_busy_refusal_still_wins_under_the_bound(monkeypatch):
    """A body that raises the lock's typed refusal inside the bound is
    reported as ``settings_busy``, not as a timeout."""
    import asyncio

    import ouroboros.config as config
    from ouroboros.gateway.owner_settings import SettingsDocumentBusy
    from ouroboros.gateway.settings import _run_settings_writer

    monkeypatch.setattr(config, "get_settings_document_lock_timeout_sec", lambda: 0.5)

    def busy_writer(_request, _body):
        raise SettingsDocumentBusy("another settings write is still in progress after 0.5s; try again")

    response = asyncio.run(_run_settings_writer(busy_writer, SimpleNamespace(), {}))
    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload["code"] == "settings_busy"
    assert payload["saved"] is False
