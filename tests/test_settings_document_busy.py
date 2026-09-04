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

    isolated_settings.write_text(json.dumps({"OUROBOROS_RUNTIME_MODE": "advanced"}), encoding="utf-8")
    monkeypatch.setattr(config, "get_settings_document_lock_timeout_sec", lambda: 1)
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
