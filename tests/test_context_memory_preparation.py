"""Only actual Nano startup relieves measured source pressure before Main.

The pressure batch no longer has a dialogue branch (the old writer is retired): dialogue
pressure buys nothing, scratchpad pressure is still relieved, and Max or a pure preview
never starts a maintenance model.
"""

import json


from ouroboros import context
from ouroboros.context_fit import estimate_context_prompt_tokens
from ouroboros.tools.registry import ToolContext
from tests.test_doc_context import _make_env_and_memory
from tests import test_consolidator_context_fit as fit_helpers
from tests.test_memory_pressure_maintenance import SourceReader

fit = fit_helpers.fit


def _setup(tmp_path):
    env, memory = _make_env_and_memory(tmp_path)
    chat = env.drive_root / "logs/chat.jsonl"
    raw = json.dumps({"chat_id": 1, "direction": "in", "text": "Complete original discussion. " * 16000 + "FINAL OWNER DECISION",
                      "ts": "2026-09-13T00:00:00Z"}) + "\n"
    chat.write_text(raw)
    task = {"id": "startup", "type": "task", "text": "Continue with my exact final decision."}
    ctx = ToolContext(repo_dir=env.repo_dir, drive_root=env.drive_root, task_id=task["id"])
    return env, memory, task, ctx, raw


def test_nano_dialogue_pressure_buys_no_maintenance_call_and_keeps_main(tmp_path, fit, monkeypatch):
    """The retired dialogue writer was the Nano pressure branch for chat: a huge dialogue
    now buys no Light call, the chat stays byte-identical and Main still gets its request."""
    env, memory, task, ctx, raw = _setup(tmp_path)
    monkeypatch.setattr(context, "get_context_mode", lambda: "nano")
    actor = SourceReader(env.drive_root, fit.window)
    messages, info = context.build_llm_messages(
        env, memory, task, ctx=ctx, llm=actor, tool_schemas=[],
        fit_candidate=lambda _messages, _tools: {"accepted": False},
    )
    receipt = info["context_memory_maintenance"]
    assert receipt["status"] == "no_progress" and not receipt["actions"]
    assert not actor.calls
    assert messages[-1]["content"] == task["text"]
    assert (env.drive_root / "logs/chat.jsonl").read_text() == raw
    assert not (env.drive_root / "memory/dialogue_blocks.json").exists()
    assert not (env.drive_root / "memory/dialogue_meta.json").exists()


def test_nano_scratchpad_pressure_still_consolidates_before_returning(tmp_path, fit, monkeypatch):
    fit.window = 50000
    env, memory, task, ctx, _raw = _setup(tmp_path)
    (env.drive_root / "logs/chat.jsonl").write_text("", encoding="utf-8")
    memory.mutate_scratchpad_blocks(lambda _current: [
        {"ts": "2026-09-01", "source": "task", "content": "Active complete source. " * 15000 + "FINAL QUESTION."}])
    monkeypatch.setattr(context, "get_context_mode", lambda: "nano")

    def fits(messages, tools):
        measured = estimate_context_prompt_tokens(messages, tools)
        return {"accepted": measured < 16000, "input_tokens": measured, "strict_bound_proven": False}
    actor = SourceReader(env.drive_root, fit.window)
    before = context.build_context_fit_plan(env, memory, task, preferred_mode="nano", ctx=ctx)
    assert not fits(before.messages_for("nano"), [])["accepted"]
    messages, info = context.build_llm_messages(env, memory, task, ctx=ctx, llm=actor,
                                               tool_schemas=[], fit_candidate=fits)
    receipt = info["context_memory_maintenance"]
    assert receipt["status"] == "fitting", receipt
    assert [action["owner"] for action in receipt["actions"]] == ["scratchpad_consolidation"]
    assert fits(messages, [])["accepted"] and actor.calls
    assert messages[-1]["content"] == task["text"]


def test_max_and_pure_preview_never_start_a_maintenance_model(tmp_path, fit, monkeypatch):
    env, memory, task, ctx, _raw = _setup(tmp_path)
    actor = SourceReader(env.drive_root, 50000)
    monkeypatch.setattr(context, "get_context_mode", lambda: "max")
    context.build_llm_messages(env, memory, task, ctx=ctx, llm=actor,
                               tool_schemas=[], fit_candidate=lambda m,t: {"accepted": False})
    monkeypatch.setattr(context, "get_context_mode", lambda: "nano")
    context.build_context_fit_plan(env, memory, task, preferred_mode="nano", ctx=ctx)
    assert not actor.calls
