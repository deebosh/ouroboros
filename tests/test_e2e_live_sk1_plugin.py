"""The live stand's SK1 probe plugin presents the host token only to the loopback Host Service
(docs/CHECKLISTS.md skill item 12, host_token_handling): a base URL naming any other host or
scheme is refused before a request is built. Pinned after the rc.15 paid stand, where the skill
review blocked the stand's own plugin for reading HOST_SERVICE_URL unvalidated."""
from __future__ import annotations

import types

import pytest

from devtools.e2e_live import scenarios


class _Token:
    def use_in_request(self) -> str:
        return "token"


class _Api:
    def __init__(self) -> None:
        self.tools: dict = {}

    def get_skill_token(self) -> _Token:
        return _Token()

    def register_tool(self, name, fn, **_kwargs) -> None:
        self.tools[name] = fn


def _echo_tool():
    module = types.ModuleType("sk1_plugin_probe")
    exec(compile(scenarios.SK1_PLUGIN, "plugin.py", "exec"), module.__dict__)
    api = _Api()
    module.register(api)
    return api.tools["echo"]


@pytest.mark.parametrize("base", ["http://evil.example.com:8767", "https://127.0.0.1:8767", "http://127.0.0.1:8767/proxy"])
def test_the_probe_refuses_a_non_loopback_host_service_base_before_any_request(monkeypatch, base):
    monkeypatch.setenv("HOST_SERVICE_URL", base)
    with pytest.raises(RuntimeError, match="loopback http URL"):
        _echo_tool()(None, "x")


def test_the_probe_accepts_the_loopback_base_and_only_then_reaches_the_transport(monkeypatch):
    monkeypatch.setenv("HOST_SERVICE_URL", "http://127.0.0.1:1")   # loopback, nothing listening
    with pytest.raises(Exception) as excinfo:
        _echo_tool()(None, "x")
    assert not isinstance(excinfo.value, RuntimeError) or "loopback" not in str(excinfo.value)
