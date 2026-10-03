"""Focus signature of memory writes (memory spec §6.3, K8, orchestrator correction to the nanny test).

The tool and set parity parts of this file arrive with the chronicle tools; this
part pins who the host says wrote a knowledge note, each role both ways.
"""
from __future__ import annotations

import json

from ouroboros import knowledge as knowledge_store
from ouroboros.consciousness_authority import consciousness_origin_metadata
from ouroboros.consciousness_wake import wake_task_metadata
from ouroboros.knowledge import UNKNOWN_STAMP, focus_signature
from ouroboros.tools import knowledge as tools
from ouroboros.tools.registry import ToolContext

CHILD = {"delegation_role": "subagent", "parent_task_id": "root0001", "root_task_id": "root0001",
         "configured_subagent": {}}


def _ctx(tmp_path, task_id, **fields):
    return ToolContext(repo_dir=tmp_path, drive_root=tmp_path, task_id=task_id, **fields)


def _history(tmp_path):
    shelf = knowledge_store.resolve_knowledge_address(tmp_path, "topic", "global").shelf
    path = shelf.parent / "knowledge_history.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _role(tmp_path, **fields):
    return focus_signature(_ctx(tmp_path, "task0001", **fields))["focus"]["role"]


def test_child_knowledge_write_carries_the_child_focus_in_history(tmp_path):
    child = _ctx(tmp_path, "kid00001", task_metadata=dict(CHILD), current_chat_id=1)
    assert "saved" in tools._knowledge_write(child, "people/rowan", "Rowan prefers short reports.", mode="append")
    root = _ctx(tmp_path, "root0001", current_chat_id=1)
    assert "saved" in tools._knowledge_write(root, "people/ada", "Ada reviews on Fridays.", mode="append")
    rows = {row["task_id"]: row for row in _history(tmp_path)}
    assert rows["kid00001"]["focus"] == {"role": "child", "task_id": "kid00001", "parent_task_id": "root0001",
                                         "root_task_id": "root0001", "chat_id": 1}
    assert rows["root0001"]["focus"]["role"] == "root" and rows["root0001"]["focus"]["parent_task_id"] == ""
    # A seam that names no focus keeps the honest unknown, like the other history stamps.
    address = knowledge_store.resolve_knowledge_address(tmp_path, "people/lee", "global")
    assert knowledge_store.write_knowledge_note(address, "Lee.", mode="append").ok
    assert {row["topic"]: row["focus"] for row in _history(tmp_path)}["people/lee"] == UNKNOWN_STAMP


def test_configured_agent_session_child_is_a_nanny_and_an_api_child_is_a_child(tmp_path):
    session = {**CHILD, "configured_subagent": {"route": {"kind": "agent_session"}}}
    assert _role(tmp_path, task_metadata=session) == "nanny"
    api = {**CHILD, "configured_subagent": {"route": {"kind": "api_model"}}}
    assert _role(tmp_path, task_metadata=api) == "child"
    # The default empty snapshot every API child carries is not a nanny.
    assert _role(tmp_path, task_metadata=dict(CHILD)) == "child"


def test_wake_is_consciousness_and_the_root_it_starts_is_a_root(tmp_path):
    wake = wake_task_metadata("observe", "heartbeat")
    assert _role(tmp_path, task_metadata=wake) == "consciousness"
    started = consciousness_origin_metadata(wake)
    assert started["initiator"] == "consciousness"
    assert _role(tmp_path, task_metadata=started) == "root"
    # A child of a wake is a child: the delegated role is decided before the ledger category.
    assert _role(tmp_path, task_metadata={**wake, **CHILD}) == "child"


def test_presence_main_and_root_roles(tmp_path):
    assert _role(tmp_path, task_metadata={"presence": {"binding_id": "b1"}}) == "presence"
    assert _role(tmp_path, task_metadata={"presence": {"binding_id": "b1"}}, is_direct_chat=True) == "presence"
    assert _role(tmp_path, is_direct_chat=True) == "main"
    assert _role(tmp_path) == "root"


def test_signature_carries_task_lineage_chat_and_observed_route(tmp_path):
    ctx = _ctx(tmp_path, "kid00001", task_metadata={**CHILD, "chat_id": 7})
    ctx._accumulated_usage = {"provider": "openrouter", "resolved_model": "openai/gpt-5.6"}
    signature = focus_signature(ctx)
    assert signature["kind"] == "mind" and signature["task_id"] == "kid00001"
    assert signature["focus"]["chat_id"] == 7 and signature["focus"]["root_task_id"] == "root0001"
    assert signature["route"] == {"provider": "openrouter", "model": "openai/gpt-5.6"}
    ctx.current_chat_id = 3
    assert focus_signature(ctx)["focus"]["chat_id"] == 3
    assert focus_signature(_ctx(tmp_path, "solo0001"))["focus"]["root_task_id"] == "solo0001"
