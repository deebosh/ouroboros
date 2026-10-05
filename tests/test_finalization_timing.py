"""Finalization observations must preserve the answer's existing delivery path."""

import json
import queue
import threading
from collections import deque
from types import SimpleNamespace

import pytest

from ouroboros import agent_task_pipeline as pipeline, delegate_custody, loop
from ouroboros.tools.registry import ToolRegistry
from ouroboros.utils import append_jsonl
from supervisor import events_chat_delivery as delivery


@pytest.fixture(params=["direct", "queued", "child", "wake"])
def completed_turn(tmp_path, monkeypatch, request):
    """Use the real loop, finalizer, result writer and sender; replace only I/O."""
    kind = request.param
    root = tmp_path / "canonical"
    drive = tmp_path / "child" if kind == "child" else root
    logs = drive / "logs"
    logs.mkdir(parents=True)
    task = {"id": "turn", "type": "task", "chat_id": 1, "text": "Say hello.",
            "_attempt": 1, "budget_drive_root": str(root),
            "_is_direct_chat": kind in {"direct", "wake"}}
    if kind == "child":
        task.update(parent_task_id="parent", root_task_id="parent", delegation_role="subagent")
    if kind == "wake":
        task["metadata"] = {"initiator": "consciousness"}
    order, sent, queued = [], [], []
    monkeypatch.setenv("OUROBOROS_TASK_REVIEW_MODE", "off")
    monkeypatch.setattr(pipeline, "in_worker_process", lambda: kind in {"queued", "child"})
    monkeypatch.setattr(delivery, "_DELIVERED_MESSAGE_IDS", deque(maxlen=256))

    def forbid_gateway(*args, **kwargs):
        pytest.fail("An empty custody release must not contact a daemon")

    monkeypatch.setattr("ouroboros.claudexor_daemon.ensure_owned_gateway", forbid_gateway)

    def observe(module, name, label):
        original = getattr(module, name)

        def call(*args, **kwargs):
            order.append(label + "_start")
            result = original(*args, **kwargs)
            order.append(label + "_end")
            return result

        monkeypatch.setattr(module, name, call)

    observe(loop, "_cleanup_loop_resources", "cleanup")
    observe(delegate_custody, "release_task_runs", "custody")
    observe(pipeline, "register_final_answer_owed", "outbox")
    observe(pipeline, "_store_task_result", "store")
    monkeypatch.setattr(pipeline, "_run_post_task_processing_async",
                        lambda *args, **kwargs: order.append("post_task"))

    class LLM:
        def default_model(self):
            return "openai-compatible::test"

        def chat(self, **kwargs):
            order.append("model_return")
            return {"content": "Hello.", "tool_calls": []}, {"cost": 0.0}

    class Events:
        def put(self, event):
            # Cross-process delivery owns a value snapshot, not the worker's dict.
            queued.append(json.loads(json.dumps(event)))
            order.append("enqueue")

    registry = ToolRegistry(repo_dir=tmp_path, drive_root=drive)
    registry._ctx.task_metadata = dict(task)
    registry._ctx.task_attempt = 1
    registry._ctx.owner_message_admission_lock = threading.RLock()
    registry._ctx.owner_message_admission_agent = SimpleNamespace(_accepting_owner_messages=True)
    text, usage, trace = loop.run_llm_loop(
        messages=[{"role": "user", "content": task["text"]}], tools=registry, llm=LLM(),
        drive_logs=logs, emit_progress=lambda *args, **kwargs: None,
        incoming_messages=queue.Queue(), task_id=task["id"], drive_root=drive,
    )
    pending = []
    pipeline.emit_task_results(
        SimpleNamespace(drive_root=drive, repo_dir=tmp_path), None, None, pending,
        task, text, usage, trace, 0.0, logs, ctx=registry._ctx, event_queue=Events(),
    )

    def send(chat_id, body, **kwargs):
        assert pipeline.load_task_result(drive, task["id"])["result"] == "Hello."
        order.append("sender_return")
        sent.append((chat_id, body, kwargs))

    host = SimpleNamespace(DRIVE_ROOT=root, RUNNING={}, append_jsonl=append_jsonl, send_with_budget=send)
    for event in queued + pending:
        if event["type"] == "send_message":
            delivery._handle_send_message(event, host)
    return SimpleNamespace(kind=kind, root=root, drive=drive, task=task, usage=usage,
                           trace=trace, text=text, pending=pending, queued=queued,
                           sent=sent, order=order, host=host)


def test_finalization_keeps_delivery_and_step_order(completed_turn):
    turn = completed_turn
    assert turn.text == "Hello."
    assert [(chat, text) for chat, text, _ in turn.sent] == [(1, "Hello.")]
    assert [event["type"] for event in turn.pending][:3] == ["send_message", "task_metrics", "task_done"]
    order = turn.order
    assert order.index("model_return") < order.index("cleanup_start")
    assert order.index("cleanup_start") < order.index("custody_start") < order.index("custody_end")
    assert order.index("custody_end") < order.index("cleanup_end") < order.index("store_start")
    assert order.index("store_start") < order.index("store_end") < order.index("sender_return")
    if turn.kind != "child":
        assert order.index("outbox_end") < order.index("store_start")
        assert order.index("store_end") < order.index("enqueue") < order.index("post_task")
        assert len([event for event in turn.queued if event["type"] == "send_message"]) == 1
    else:
        assert "post_task" not in order
