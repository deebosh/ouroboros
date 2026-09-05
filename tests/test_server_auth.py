"""Tests for the minimal network password gate.

The gate tests pin the GATE, so they take the configured password through the
module's resolver seam (``get_configured_network_password``) instead of the
ambient environment: one windows-latest xdist worker saw a password with none
configured and then no password right after one was set — ambient os.environ
pollution from an earlier module on the same ``--dist loadscope`` worker (the
conftest snapshot restores os.environ between tests; a daemon thread applying a
settings dict to the environment does not wait for it). Exactly ONE test reads
the real resolution order, and it asserts the pre-state it relies on by name so a
polluted worker fails there, not as a downstream 200/404.
"""

import os
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import ouroboros.server_auth as server_auth


async def _ok(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _make_client(monkeypatch, password: str = "secret") -> TestClient:
    monkeypatch.setattr(server_auth, "get_configured_network_password", lambda: password)
    app = server_auth.NetworkAuthGate(Starlette(routes=[
        Route("/", endpoint=_ok),
        Route("/api/health", endpoint=_ok),
        Route("/api/secret", endpoint=_ok),
    ]))
    return TestClient(app)


def test_configured_password_resolution_env_over_settings_then_empty(monkeypatch):
    """The ONE resolution pin: env wins over settings, a blank env falls through
    to settings, a missing/blank/unreadable settings value is the empty password."""
    key = server_auth.NETWORK_PASSWORD_KEY
    monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(server_auth, "load_settings", lambda: {})
    # The polluter is a THREAD (a leaked daemon re-applying a settings dict to
    # os.environ), so the pre-state is read over a short window, not once: a
    # writer racing this test is named HERE instead of surfacing downstream.
    for _ in range(20):
        assert os.environ.get(key) is None, (
            f"{key} is present in os.environ after monkeypatch.delenv: "
            "ambient environment pollution on this worker"
        )
        assert server_auth.get_configured_network_password() == "", (
            f"a password resolves with {key} unset and settings empty: "
            "ambient environment pollution on this worker"
        )
        time.sleep(0.001)

    monkeypatch.setattr(server_auth, "load_settings", lambda: {key: " from-settings "})
    assert server_auth.get_configured_network_password() == "from-settings"
    monkeypatch.setenv(key, " from-env ")
    assert server_auth.get_configured_network_password() == "from-env"
    monkeypatch.setenv(key, "   ")
    assert server_auth.get_configured_network_password() == "from-settings"

    monkeypatch.delenv(key)
    monkeypatch.setattr(server_auth, "load_settings", lambda: {key: "  "})
    assert server_auth.get_configured_network_password() == ""

    def _unreadable():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(server_auth, "load_settings", _unreadable)
    assert server_auth.get_configured_network_password() == ""


def test_validate_network_auth_configuration_allows_open_bind_without_password(monkeypatch):
    monkeypatch.setattr(server_auth, "get_configured_network_password", lambda: "")

    assert server_auth.validate_network_auth_configuration("127.0.0.1") is None
    assert server_auth.validate_network_auth_configuration("0.0.0.0") is None


def test_get_network_auth_startup_warning_warns_but_allows_open_bind(monkeypatch):
    monkeypatch.setattr(server_auth, "get_configured_network_password", lambda: "")

    assert server_auth.get_network_auth_startup_warning("127.0.0.1") is None
    warning = server_auth.get_network_auth_startup_warning("0.0.0.0")
    assert warning is not None
    assert "without OUROBOROS_NETWORK_PASSWORD" in warning

    monkeypatch.setattr(server_auth, "get_configured_network_password", lambda: "secret")
    assert server_auth.get_network_auth_startup_warning("0.0.0.0") is None


def test_network_auth_gate_is_open_without_a_configured_password(monkeypatch):
    with _make_client(monkeypatch, password="") as client:
        assert client.get("/api/secret").status_code == 200


def test_network_auth_gate_blocks_non_local_requests(monkeypatch):
    with _make_client(monkeypatch) as client:
        html_resp = client.get("/", follow_redirects=False)
        assert html_resp.status_code == 401
        assert "Enter the network password" in html_resp.text

        api_resp = client.get("/api/secret")
        assert api_resp.status_code == 401
        assert api_resp.json()["error"] == "Authentication required."

        health_resp = client.get("/api/health")
        assert health_resp.status_code == 200


def test_network_auth_gate_accepts_header_and_login_cookie(monkeypatch):
    with _make_client(monkeypatch) as client:
        header_resp = client.get("/", headers={"x-ouroboros-password": "secret"})
        assert header_resp.status_code == 200
        assert header_resp.json() == {"ok": True}

    with _make_client(monkeypatch) as client:
        login_resp = client.post(
            "/auth/login",
            json={"password": "secret", "next": "/"},
            follow_redirects=False,
        )
        assert login_resp.status_code == 200

        cookie_resp = client.get("/")
        assert cookie_resp.status_code == 200
        assert cookie_resp.json() == {"ok": True}


def test_login_next_url_is_escaped(monkeypatch):
    with _make_client(monkeypatch) as client:
        resp = client.get('/auth/login?next=/"><script>alert(1)</script>', follow_redirects=False)
        assert resp.status_code == 200
        assert "<script>" not in resp.text
        assert 'value="/"' in resp.text
