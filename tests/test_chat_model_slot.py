"""`OUROBOROS_MODEL_CHAT` — a dedicated routable slot for interactive chat.

Chat turns (`ToolContext.is_direct_chat`) route through `config.get_chat_model()`
(empty -> Main). A headless task — including one launched from a chat session —
keeps `OUROBOROS_MODEL`. An explicit per-task `task_model_override` still wins.
"""

from __future__ import annotations

import queue

import pytest

import ouroboros.loop as loop_mod
from ouroboros import config
from ouroboros import provider_models
from ouroboros.loop import run_llm_loop
from ouroboros.tools.registry import ToolRegistry


# --- config.get_chat_model() -------------------------------------------------

def test_chat_model_empty_falls_back_to_main(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MODEL", "primary::big")
    monkeypatch.delenv("OUROBOROS_MODEL_CHAT", raising=False)
    assert config.get_chat_model() == "primary::big"


def test_chat_model_whitespace_only_falls_back_to_main(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MODEL", "primary::big")
    monkeypatch.setenv("OUROBOROS_MODEL_CHAT", "   ")
    assert config.get_chat_model() == "primary::big"


def test_chat_model_set_is_used_verbatim(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MODEL", "primary::big")
    monkeypatch.setenv("OUROBOROS_MODEL_CHAT", "qwen::qwen-plus")
    assert config.get_chat_model() == "qwen::qwen-plus"


def test_chat_slot_is_registered(monkeypatch):
    assert config.SETTINGS_DEFAULTS["OUROBOROS_MODEL_CHAT"] == ""
    assert "OUROBOROS_MODEL_CHAT" in provider_models.ACTIVE_MODEL_SETTING_KEYS
    assert "OUROBOROS_MODEL_CHAT" not in provider_models.LEGACY_MODEL_SETTING_KEYS


# --- run_llm_loop model selection -----------------------------------------

class _FakeLLM:
    def default_model(self):
        return "primary::big"


def _run_capture_model(tmp_path, monkeypatch, *, is_direct_chat, task_model_override=""):
    """Run one loop round; return the `active_model` the round call received."""
    seen = {}

    def fake_call_round_model(ctx):
        seen["active_model"] = ctx.active_model
        return {"role": "assistant", "content": "done"}, 0.0, "high"

    monkeypatch.setattr(loop_mod, "_call_round_model", fake_call_round_model)

    tools = ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path)
    tools._ctx.is_direct_chat = is_direct_chat
    if task_model_override:
        tools._ctx.task_model_override = task_model_override

    result, _usage, _trace = run_llm_loop(
        messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        llm=_FakeLLM(),
        drive_logs=tmp_path,
        emit_progress=lambda _t: None,
        incoming_messages=queue.Queue(),
        task_id="chat-slot",
        drive_root=tmp_path,
    )
    assert result == "done"
    return seen["active_model"]


def test_chat_turn_uses_chat_model(tmp_path, monkeypatch):
    monkeypatch.setenv("OUROBOROS_MODEL", "primary::big")
    monkeypatch.setenv("OUROBOROS_MODEL_CHAT", "qwen::qwen-plus")
    assert _run_capture_model(tmp_path, monkeypatch, is_direct_chat=True) == "qwen::qwen-plus"


def test_headless_task_ignores_chat_model(tmp_path, monkeypatch):
    monkeypatch.setenv("OUROBOROS_MODEL", "primary::big")
    monkeypatch.setenv("OUROBOROS_MODEL_CHAT", "qwen::qwen-plus")
    assert _run_capture_model(tmp_path, monkeypatch, is_direct_chat=False) == "primary::big"


def test_chat_turn_with_empty_chat_slot_uses_main(tmp_path, monkeypatch):
    monkeypatch.setenv("OUROBOROS_MODEL", "primary::big")
    monkeypatch.delenv("OUROBOROS_MODEL_CHAT", raising=False)
    assert _run_capture_model(tmp_path, monkeypatch, is_direct_chat=True) == "primary::big"


def test_task_model_override_wins_over_chat_model(tmp_path, monkeypatch):
    monkeypatch.setenv("OUROBOROS_MODEL", "primary::big")
    monkeypatch.setenv("OUROBOROS_MODEL_CHAT", "qwen::qwen-plus")
    got = _run_capture_model(
        tmp_path, monkeypatch, is_direct_chat=True, task_model_override="explicit::pinned"
    )
    assert got == "explicit::pinned"
