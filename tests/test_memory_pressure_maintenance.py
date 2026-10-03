"""Existing Light memory operations relieve measured pressure without losing sources.

The pressure batch reads the scratchpad and the authored overview; the old dialogue
writer it used to call first is retired, so dialogue pressure buys no Light call and
the frozen legacy dialogue files stay byte-identical.
"""
import hashlib
import json

import pytest

from ouroboros import consolidator as c, reflection, knowledge as k
from ouroboros.context_fit import estimate_context_prompt_tokens
from ouroboros.memory import Memory
from ouroboros.tools.registry import ToolContext
from tests import test_consolidator_context_fit as fit_helpers

fit = fit_helpers.fit


def call(name, args, ident):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class SourceReader:
    """Fake actor advances through real read_file results and requests authored views."""
    def __init__(self, root, window, answer=None):
        self.root, self.window, self.answer = root, window, answer
        self.calls, self.sources, self.received, self.operation_turns = [], [], [], []
        self.ref, self.position, self.stage = None, 0, ""

    def finish(self, prompt):
        if self.answer:
            return self.answer
        if "scratchpad working memory has" in prompt:
            return json.dumps({"knowledge_entries": [], "compressed_block": "I retain the beginning, middle and last event, including unresolved questions."})
        return "I remember the complete episode including its final unresolved decision."

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        assert estimate_context_prompt_tokens(kwargs["messages"], kwargs["tools"]) + kwargs["max_tokens"] <= self.window
        assert {t["function"]["name"] for t in kwargs["tools"]} >= {"read_file", "compact_context", "knowledge_read"}
        first = kwargs["messages"][0]["content"]
        if len(kwargs["messages"]) == 1:
            self.operation_turns.append(kwargs["model_turn_state"])
            if not first.startswith("Complete source and instructions"):
                return {"content": self.finish(first)}, {"cost": 0.01}
            self.ref = json.loads(first.split("\n", 1)[1])
            self.position, self.stage = 0, "read"
            self.sources.append(self.ref)
            self.received.append("")
            assert (self.root / self.ref["read"]["arguments"]["path"]).read_bytes()
        else:
            assert kwargs["model_turn_state"] is self.operation_turns[-1]
            if self.stage == "read":
                result = kwargs["messages"][-1]["content"]
                body = result.split("\n[Tool result source view]\n", 1)[0].split("\n", 1)[1]
                source = (self.root / self.ref["read"]["arguments"]["path"]).read_text(encoding="utf-8")
                assert body == source[self.position:self.position + len(body)]
                assert body
                self.position += len(body)
                self.received[-1] += body
                if self.position == len(source):
                    return {"content": self.finish(self.received[-1])}, {"cost": 0.01}
                self.stage = "compact"
                return {"tool_calls": [call("compact_context", {
                    "working_note": f"I read through character {self.position}. The beginning remains part of the chronology; continue the original source and preserve its final unresolved decision.",
                    "keep_unit_ids": []}, f"compact-{len(self.calls)}")]}, {"cost": 0.01}
            receipt = json.loads(kwargs["messages"][-1]["content"])
            assert receipt["context_view"]["status"] == "applied"
            self.stage = "read"
        return {"tool_calls": [call("read_file", {
            **self.ref["read"]["arguments"], "max_lines": 2000, "start_char": self.position},
            f"read-{len(self.calls)}")]}, {"cost": 0.01}


def setup_memory(root):
    memory = Memory(root, root)
    memory.identity_path().parent.mkdir(parents=True, exist_ok=True)
    memory.identity_path().write_text("# Identity\nThe same unmodified identity.\n")
    return memory, ToolContext(repo_dir=root, drive_root=root, task_id="pressure-task")


def test_two_megabyte_reflection_retained_before_first_fit_and_read_completely(tmp_path, fit):
    fit.window = 50000
    text = "BEGINNING original event.\n" + "Decision and counterexample matter. " * 60000 + "\nDECISIVE FINAL EVENT."
    assert len(text.encode()) > 2_000_000
    actor = SourceReader(tmp_path, fit.window, "I retain the original beginning and DECISIVE FINAL EVENT.\nMEMORY_ACTIONS_JSON: []")
    entry = reflection.generate_reflection({"id": "big-reflection", "text": text, "drive_root": str(tmp_path)},
                                           {}, "trace", actor, {"rounds": 20, "cost": 1})
    assert not entry.get("memory_operation_errors"), (entry.get("memory_operation_errors"), len(actor.calls), actor.position, actor.stage)
    assert "DECISIVE FINAL EVENT" in entry["reflection"]
    ref = entry["source_ref"]
    stored = (tmp_path / ref["read"]["arguments"]["path"]).read_bytes()
    assert ref["sha256"] == hashlib.sha256(stored).hexdigest()
    assert text in stored.decode()
    assert actor.received == [stored.decode()]
    assert len(actor.calls) > 5
    assert len(actor.calls[0]["messages"][0]["content"]) < 3000
    assert all(ca["model_role"] == "light" for ca in actor.calls)


def test_unread_initial_source_cannot_authorize_a_reflection_action(tmp_path, fit):
    fit.window = 24000
    class Unread:
        def chat(self, **_kwargs):
            return {"content": 'Reflection.\nMEMORY_ACTIONS_JSON: [{"type":"knowledge_write","topic":"new","content":"Unread claim"}]'}, {"cost": 0.03}
    entry = reflection.generate_reflection({"id": "unread", "text": "large " * 100000, "drive_root": str(tmp_path)},
                                           {}, "trace", Unread(), {"rounds": 20})
    assert entry["memory_actions"] == []
    assert entry["memory_operation_errors"][0]["kind"] == "source_incomplete"
    assert entry["memory_operation_errors"][0]["response_ref"]
    assert (tmp_path / entry["source_ref"]["read"]["arguments"]["path"]).exists()


def test_pressure_relieves_the_scratchpad_and_leaves_frozen_dialogue_memory_alone(tmp_path, fit):
    fit.window = 50000
    memory, ctx = setup_memory(tmp_path)
    identity_before = memory.identity_path().read_bytes()
    blocks = [{"ts": "2024-01-01", "range": "2024-01-01", "type": "summary", "message_count": 100,
               "content": "BEGINNING. " + "History before gap. " * 9000},
              {"gap_id": "known-gap", "content": "[MEMORY GAP] An authentic discontinuity", "range": "2025-01-01"}]
    frozen = {name: tmp_path / "memory" / name for name in ("dialogue_blocks.json", "dialogue_meta.json")}
    c_json = json.dumps(blocks).encode("utf-8")
    frozen["dialogue_blocks.json"].write_bytes(c_json)
    frozen["dialogue_meta.json"].write_bytes(b'{"last_consolidated_offset": 0}')
    before = {name: path.read_bytes() for name, path in frozen.items()}
    scratch = {"ts": "2026-09-01", "source": "task", "content": "Active complete source. " * 15000 + "FINAL QUESTION."}
    memory.mutate_scratchpad_blocks(lambda _current: [scratch])

    def fits():
        messages = [{"role": "system", "content": memory.identity_path().read_text(encoding="utf-8")
                     + memory.scratchpad_path().read_text(encoding="utf-8")}]
        return estimate_context_prompt_tokens(messages) < 2000
    assert not fits() and not c.should_consolidate_scratchpad(memory)
    actor = SourceReader(tmp_path, fit.window)
    result = c.maintain_memory_pressure(memory, actor, ctx, fits=fits, current_topic="CURRENT GOAL: resolve the outstanding research question.")
    assert result["status"] == "fitting", result
    assert fits() and memory.identity_path().read_bytes() == identity_before
    assert [action["owner"] for action in result["actions"]] == ["scratchpad_consolidation"]
    assert result["usage"]["cost"] == pytest.approx(len(actor.calls) * 0.01)
    journal = [json.loads(line) for line in memory.journal_path().read_text(encoding="utf-8").splitlines()]
    assert next(row for row in journal if row["type"] == "blocks_consolidated")["source_blocks"] == [scratch]
    # Only the scratchpad source was retained and read; it carries the current goal.
    assert len(actor.sources) == 1 and all(actor.received)
    assert "CURRENT GOAL: resolve the outstanding research question." in actor.received[0]
    assert {name: path.read_bytes() for name, path in frozen.items()} == before
    assert all(str(frozen["dialogue_blocks.json"]) != row["path"] for row in result["changed_sources"])


def test_dialogue_pressure_buys_no_light_call_and_keeps_every_source(tmp_path, fit):
    """With nothing but a huge dialogue under pressure, no Light call is bought: the old
    writer is retired, and the frozen cursor is neither read for work nor written."""
    memory, ctx = setup_memory(tmp_path)
    chat = tmp_path / "logs/chat.jsonl"
    chat.parent.mkdir()
    chat.write_text(json.dumps({"text": "Huge single message. " * 10000, "chat_id": 1,
                                "ts": "2026-09-13T01:00:00Z"}) + "\n", encoding="utf-8")
    before = chat.read_bytes()

    class NoCall:
        def chat(self, **_kwargs):
            raise AssertionError("dialogue pressure must not buy a Light call")
    result = c.maintain_memory_pressure(memory, NoCall(), ctx, fits=lambda: False)
    assert result["status"] == "no_progress" and not result["actions"] and not result["changed_sources"]
    assert chat.read_bytes() == before
    assert not (tmp_path / "memory/dialogue_blocks.json").exists()
    assert not (tmp_path / "memory/dialogue_meta.json").exists()


def test_pressure_uses_read_revision_to_rewrite_the_authored_overview(tmp_path, fit):
    memory, ctx = setup_memory(tmp_path)
    address = k.resolve_knowledge_address(tmp_path, "overview", "global")
    old = k.write_knowledge_note(address, "---\nsummary: Full authored orientation.\n---\n" + "Detailed understanding. " * 500).current
    class Overview:
        calls = 0
        def chat(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"tool_calls": [call("knowledge_read", {"topic": "overview", "scope": "global"}, "read-overview")]}, {"cost": 0.01}
            assert old.text in kwargs["messages"][-1]["content"]
            # Pressure shortening is an explicit edit of the whole long span it replaces.
            return {"content": json.dumps({"knowledge_entries": [{"topic": "overview", "scope": "global",
                "summary": "Authored compact orientation.",
                "edits": [{"old_text": "Detailed understanding. " * 500,
                           "new_text": "Full scope retained with [detail](detail.md).",
                           "basis": "The complete current source is retained in the detailed note."}]}]})}, {"cost": 0.02}
    result = c.maintain_memory_pressure(memory, Overview(), ctx, fits=lambda: len(address.path.read_bytes()) < 1000)
    assert result["status"] == "fitting"
    assert result["actions"][0]["writes"][0]["ok"]
    current = k.read_knowledge_note(address)
    assert current.metadata == {"summary": "Authored compact orientation.", "type": "note"}
    assert current.text.endswith("---\nFull scope retained with [detail](detail.md).")
    history = [json.loads(line) for line in (tmp_path / "memory/knowledge_history.jsonl").read_text(encoding="utf-8").splitlines()]
    change = next(row for row in history if row.get("old_content") == old.text)
    assert change["writer"] == "knowledge_maintenance" and change["edits"][0]["basis"]
    assert change["summary"] == "Authored compact orientation."


def test_irreducible_identity_is_preserved_with_no_progress(tmp_path, fit):
    memory, ctx = setup_memory(tmp_path)
    raw = b"identity " * 20000
    memory.identity_path().write_bytes(raw)
    class NoCall:
        def chat(self, **_kwargs):
            raise AssertionError("No existing mutable memory source can relieve this core")
    result = c.maintain_memory_pressure(memory, NoCall(), ctx, fits=lambda: False)
    assert result["status"] == "no_progress" and not result["changed_sources"]
    assert memory.identity_path().read_bytes() == raw
    assert not result["actions"]


def test_failed_scratchpad_output_keeps_paid_usage_and_source(tmp_path, fit):
    memory, ctx = setup_memory(tmp_path)
    original = {"ts": "same", "source": "task", "content": "old" * 2000}
    memory.mutate_scratchpad_blocks(lambda _: [original])
    class Invalid:
        calls = 0
        def chat(self, **_kwargs):
            self.calls += 1
            return {"content": "This is not the requested JSON."}, {"cost": 0.25}
    actor = Invalid()
    result = c.maintain_memory_pressure(memory, actor, ctx, fits=lambda: False)
    assert actor.calls == 1 and result["usage"]["cost"] == 0.25
    assert result["usage"]["_consolidation_errors"][0]["kind"] == "scratchpad_consolidation_failed"
    assert memory.load_scratchpad_blocks() == [original]
    assert result["status"] == "no_progress"


@pytest.mark.parametrize("change_same_source", [False, True])
def test_scratchpad_pressure_cas_preserves_concurrent_sources(tmp_path, fit, change_same_source):
    memory, ctx = setup_memory(tmp_path)
    original = {"ts": "same", "source": "task", "content": "old" * 2000}
    memory.mutate_scratchpad_blocks(lambda _: [original])
    newer = {**original, "content": "new concurrent content"}
    appended = {"ts": "later", "source": "task", "content": "A new episode"}
    class Concurrent:
        def chat(self, **_kwargs):
            memory.mutate_scratchpad_blocks(lambda rows: [newer] if change_same_source else rows + [appended])
            return {"content": '{"knowledge_entries":[],"compressed_block":"Short retained understanding."}'}, {"cost": 0.01}
    c.consolidate_scratchpad(memory, tmp_path / "memory/knowledge", Concurrent(), pressure=True, knowledge_context=ctx)
    saved = memory.load_scratchpad_blocks()
    assert saved == [newer] if change_same_source else saved[1:] == [appended]
