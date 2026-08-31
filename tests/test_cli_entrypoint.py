from __future__ import annotations

import json
import sys
from types import SimpleNamespace


def test_server_subcommand_sanitizes_argv(monkeypatch):
    from ouroboros import cli

    seen = {}

    class FakeServer:
        @staticmethod
        def main():
            seen["argv"] = list(sys.argv)
            return 0

    monkeypatch.setitem(sys.modules, "server", FakeServer)
    monkeypatch.setattr(sys, "argv", ["ouroboros", "server", "--host", "127.0.0.1", "--port", "9000"])

    result = cli._server_command(SimpleNamespace(host="127.0.0.1", port=9000, no_ui=True))

    assert result == 0
    assert seen["argv"] == ["ouroboros"]
    assert json.loads(__import__("os").environ["OUROBOROS_SERVER_REEXEC_ARGV_JSON"]) == [
        "-m",
        "ouroboros.cli",
        "server",
        "--host",
        "127.0.0.1",
        "--port",
        "9000",
    ]
    assert sys.argv == ["ouroboros", "server", "--host", "127.0.0.1", "--port", "9000"]


def test_settings_context_mode_posts_owner_endpoint(monkeypatch):
    from ouroboros import cli

    seen = {}

    class FakeClient:
        def request(self, method, path, body=None):
            seen["request"] = (method, path, body)
            return {"ok": True, "context_mode": body["mode"]}

    monkeypatch.setattr(cli, "_client", lambda _args, **_kwargs: FakeClient())

    result = cli._owner_context_mode_command(SimpleNamespace(mode="low"))

    assert result == 0
    assert seen["request"] == ("POST", "/api/owner/context-mode", {"mode": "low"})


def test_chat_history_limit_maps_to_n_human(monkeypatch, capsys):
    """`chat history --limit N` requests the quota the server actually honors.

    The CLI sends the explicit `n_human` quota (the server separately honors
    legacy `limit` as a fallback default for already-shipped clients), so the
    flag is no longer a placebo (issue #172).
    """
    from ouroboros import cli

    seen = {}

    class FakeClient:
        def request(self, method, path, body=None):
            seen["request"] = (method, path)
            return {"messages": []}

    monkeypatch.setattr(cli, "_client", lambda _args, **_kwargs: FakeClient())

    result = cli._chat_history_command(SimpleNamespace(limit=25))

    assert result == 0
    assert seen["request"] == ("GET", "/api/chat/history?n_human=25")
    capsys.readouterr()
