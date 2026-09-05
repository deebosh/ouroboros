import json
import pathlib
from types import SimpleNamespace

import ouroboros.agent_task_pipeline as pipeline


def test_emit_task_results_queues_restart_after_final_events(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_store_task_result", lambda *args, **kwargs: None)
    memory_calls = []
    monkeypatch.setattr(pipeline, "_run_chat_consolidation", lambda *args, **kwargs: memory_calls.append("chat"))
    monkeypatch.setattr(pipeline, "_run_scratchpad_consolidation", lambda *args, **kwargs: memory_calls.append("scratchpad"))
    monkeypatch.setattr(pipeline, "_run_post_task_processing_async", lambda *args, **kwargs: memory_calls.append("post_task"))

    pending_events = []
    ctx = SimpleNamespace(pending_restart_reason="apply timeout fix")
    env = SimpleNamespace(drive_root=tmp_path)
    drive_logs = tmp_path / "logs"
    drive_logs.mkdir(parents=True)

    pipeline.emit_task_results(
        env=env,
        memory=object(),
        llm=object(),
        pending_events=pending_events,
        task={"id": "task-1", "type": "task", "chat_id": 1, "text": "do it"},
        text="All done",
        usage={"rounds": 2, "cost": 0.2},
        llm_trace={"tool_calls": [], "reasoning_notes": []},
        start_time=0.0,
        drive_logs=drive_logs,
        ctx=ctx,
    )

    assert [evt["type"] for evt in pending_events] == [
        "send_message",
        "task_metrics",
        "task_done",
        "restart_request",
    ]
    assert pending_events[-1]["reason"] == "apply timeout fix"
    assert ctx.pending_restart_reason is None
    # Consolidations now run inside the single post-task worker; replacing that
    # worker in this ordering test intentionally replaces the whole phase.
    assert memory_calls == ["post_task"]

    pending_events.clear()
    evolution_ctx = SimpleNamespace(
        pending_restart_reason="apply reviewed evolution",
        pending_restart_is_evolution=True,
    )
    pipeline.emit_task_results(
        env=env,
        memory=object(),
        llm=object(),
        pending_events=pending_events,
        task={"id": "evo-1", "type": "evolution", "chat_id": 1, "text": "improve"},
        text="All done",
        usage={"rounds": 2, "cost": 0.2},
        llm_trace={"tool_calls": [], "reasoning_notes": []},
        start_time=0.0,
        drive_logs=drive_logs,
        ctx=evolution_ctx,
    )
    assert [evt["type"] for evt in pending_events] == ["send_message", "task_metrics", "task_done"]
    assert evolution_ctx.pending_restart_reason is None
    assert evolution_ctx.pending_restart_is_evolution is False

    pending_events.clear()
    memory_calls.clear()
    pipeline.emit_task_results(
        env=env,
        memory=object(),
        llm=object(),
        pending_events=pending_events,
        task={
            "id": "child-1", "type": "task", "chat_id": 1, "text": "inspect",
            "delegation_role": "subagent", "memory_mode": "shared",
            "parent_task_id": "parent-1", "root_task_id": "root-1", "role": "critic",
        },
        text="summary",
        usage={"rounds": 2, "cost": 0.2},
        llm_trace={"tool_calls": [], "reasoning_notes": []},
        start_time=0.0,
        drive_logs=drive_logs,
        ctx=SimpleNamespace(pending_restart_reason=""),
    )
    assert [evt["type"] for evt in pending_events] == ["send_message", "task_metrics", "task_done"]
    assert pending_events[0]["progress_meta"] == {
        "subagent_task_id": "child-1",
        "root_task_id": "root-1",
        "parent_task_id": "parent-1",
        "delegation_role": "subagent",
        "subagent_role": "critic",
        "write_surface": "",
        "task_group_id": "",
        "model_lane": "",
        "effective_model_lane": "",
        "model": "",
        "executor_route": "",
    }
    assert memory_calls == []


def test_lineage_child_without_delegation_role_cannot_run_global_post_task(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_store_task_result", lambda *args, **kwargs: None)
    memory_calls = []
    monkeypatch.setattr(pipeline, "_run_chat_consolidation", lambda *a, **k: memory_calls.append("chat"))
    monkeypatch.setattr(pipeline, "_run_scratchpad_consolidation", lambda *a, **k: memory_calls.append("scratchpad"))
    monkeypatch.setattr(pipeline, "_run_post_task_processing_async", lambda *a, **k: memory_calls.append("post_task"))
    drive_logs = tmp_path / "logs-child-lineage"
    drive_logs.mkdir()

    pipeline.emit_task_results(
        env=SimpleNamespace(drive_root=tmp_path),
        memory=object(),
        llm=object(),
        pending_events=[],
        task={
            "id": "child-2",
            "root_task_id": "root-1",
            "parent_task_id": "root-1",
            "type": "task",
            "chat_id": 1,
        },
        text="child result",
        usage={"rounds": 1, "cost": 0.0},
        llm_trace={"tool_calls": [], "reasoning_notes": []},
        start_time=0.0,
        drive_logs=drive_logs,
        ctx=SimpleNamespace(pending_restart_reason=""),
    )
    assert memory_calls == []


def test_split_drive_root_runs_one_canonical_post_task_synthesis(tmp_path, monkeypatch):
    child = tmp_path / "child"
    canonical = tmp_path / "canonical"
    child.mkdir()
    canonical.mkdir()
    (child / "logs").mkdir()
    (canonical / "logs").mkdir()
    monkeypatch.setattr(pipeline, "_store_task_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "_run_chat_consolidation", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_run_scratchpad_consolidation", lambda *a, **k: None)
    calls = []

    def fake_post(env, task, *_args, **_kwargs):
        calls.append((pathlib.Path(env.drive_root), task.get("child_drive_root")))
        return {"backlog_candidates": []}

    monkeypatch.setattr(pipeline, "_run_post_task_processing_async", fake_post)
    pipeline.emit_task_results(
        env=SimpleNamespace(repo_dir=tmp_path, drive_root=child),
        memory=object(),
        llm=object(),
        pending_events=[],
        task={
            "id": "root-split",
            "root_task_id": "root-split",
            "type": "task",
            "chat_id": 1,
            "budget_drive_root": str(canonical),
        },
        text="done",
        usage={"rounds": 2, "cost": 0.1},
        llm_trace={"tool_calls": [], "reasoning_notes": []},
        start_time=0.0,
        drive_logs=child / "logs",
        ctx=SimpleNamespace(pending_restart_reason=""),
    )
    assert calls == [(canonical, str(child))]


def test_task_result_and_task_done_mirror_authoritative_review_status(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_run_post_task_processing_async", lambda *args, **kwargs: None)
    pending_events = []
    trace = {
        "tool_calls": [],
        "reasoning_notes": [],
        "review_decision": {"eligibility": "eligible", "trigger": "review_run"},
        "review_runs": [{
            "authority": "host_root",
            "aggregate_signal": "PASS",
            "actors": [{
                "signal": "PASS",
                "parsed": {"outcome_tier": "solved"},
            }],
        }],
    }
    drive_logs = tmp_path / "logs"
    drive_logs.mkdir()

    pipeline.emit_task_results(
        env=SimpleNamespace(drive_root=tmp_path, repo_dir=tmp_path),
        memory=object(),
        llm=object(),
        pending_events=pending_events,
        task={
            "id": "review-mirror",
            "root_task_id": "review-mirror",
            "type": "task",
            "chat_id": 1,
            "text": "verify",
        },
        text="done",
        usage={"rounds": 1, "cost": 0.0},
        llm_trace=trace,
        start_time=0.0,
        drive_logs=drive_logs,
        ctx=SimpleNamespace(pending_restart_reason=""),
    )

    stored = pipeline.load_task_result(tmp_path, "review-mirror")
    assert stored["review_status"] == stored["outcome_axes"]["review"]
    assert stored["review_status"]["status"] == "pass"
    done = next(row for row in pending_events if row["type"] == "task_done")
    assert done["review_status"] == stored["review_status"]


def test_emit_task_results_ephemeral_turn_skips_all_durable_memory(tmp_path, monkeypatch):
    """WS10 idempotency contract (claudexor B5): an ephemeral same-route turn must
    write NO durable memory — not chat/scratchpad consolidation, not reflection/
    evolution — while still delivering its reply."""
    store_calls = []
    monkeypatch.setattr(pipeline, "_store_task_result", lambda *args, **kwargs: store_calls.append(1))
    memory_calls = []
    monkeypatch.setattr(pipeline, "_run_chat_consolidation", lambda *args, **kwargs: memory_calls.append("chat"))
    monkeypatch.setattr(pipeline, "_run_scratchpad_consolidation", lambda *args, **kwargs: memory_calls.append("scratchpad"))
    monkeypatch.setattr(pipeline, "_run_post_task_processing_async", lambda *args, **kwargs: memory_calls.append("post_task"))

    pending_events = []
    drive_logs = tmp_path / "logs2"
    drive_logs.mkdir(parents=True)
    pipeline.emit_task_results(
        env=SimpleNamespace(drive_root=tmp_path),
        memory=object(),
        llm=object(),
        pending_events=pending_events,
        task={"id": "eph-1", "type": "task", "chat_id": 1, "text": "2+2?", "_is_direct_chat": True, "_ephemeral_turn": True},
        text="4",
        usage={"rounds": 1, "cost": 0.01},
        llm_trace={"tool_calls": [], "reasoning_notes": []},
        start_time=0.0,
        drive_logs=drive_logs,
        ctx=SimpleNamespace(pending_restart_reason=""),
    )
    assert "send_message" in [evt["type"] for evt in pending_events]  # reply still delivered
    inline = next(evt for evt in pending_events if evt["type"] == "send_message")
    assert inline["progress_meta"] == {
        "ephemeral_decision": True, "task_terminal_status": "completed",
    }
    assert memory_calls == []  # NO durable memory writes for an ephemeral turn
    assert store_calls == []  # CW3: no durable task_result for a transient decision turn
    # CW3: task_done carries _ephemeral so the supervisor handler skips the missing-result fallback.
    done = next(evt for evt in pending_events if evt["type"] == "task_done")
    assert done.get("_ephemeral") is True
    assert done.get("ephemeral_decision") is True


def test_ephemeral_typed_routing_delivers_nonempty_final_and_keeps_receipt_metadata(tmp_path, monkeypatch):
    """A typed receipt annotates the owner message; normalized final model prose
    remains one durable assistant reply for every routing action."""
    monkeypatch.setattr(pipeline, "_store_task_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "_run_chat_consolidation", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "_run_scratchpad_consolidation", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "_run_post_task_processing_async", lambda *args, **kwargs: None)
    drive_logs = tmp_path / "routing-logs"
    drive_logs.mkdir(parents=True)

    for action in (
        "route_to_project",
        "steer_task",
        "promote_chat_to_task",
        "routing_manual_target",
    ):
        pending_events = []
        pipeline.emit_task_results(
            env=SimpleNamespace(drive_root=tmp_path),
            memory=object(),
            llm=object(),
            pending_events=pending_events,
            task={
                "id": f"eph-{action}",
                "type": "task",
                "chat_id": 1,
                "text": "route this",
                "_is_direct_chat": True,
                "_ephemeral_turn": True,
            },
            text=f"Receipt prose for {action}",
            usage={"rounds": 1, "cost": 0.01},
            llm_trace={"tool_calls": [{"tool": action}], "reasoning_notes": []},
            start_time=0.0,
            drive_logs=drive_logs,
            ctx=SimpleNamespace(
                pending_restart_reason="",
                _typed_routing_action_emitted=action,
            ),
        )
        sends = [evt for evt in pending_events if evt["type"] == "send_message"]
        assert len(sends) == 1
        assert sends[0]["text"] == f"Receipt prose for {action}"
        assert sends[0]["log_text"] == f"Receipt prose for {action}"
        assert sends[0]["progress_meta"] == {
            "ephemeral_decision": True, "task_terminal_status": "completed",
        }
        done = next(evt for evt in pending_events if evt["type"] == "task_done")
        assert pending_events.index(sends[0]) < pending_events.index(done)
        assert done["ephemeral_decision"] is True
        assert done["typed_routing_action"] == action


def test_emit_project_scoped_parent_drive_gets_only_global_backlog_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_store_task_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "load_task_result", lambda *args, **kwargs: {})
    monkeypatch.setattr(pipeline, "_run_chat_consolidation", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "_run_scratchpad_consolidation", lambda *args, **kwargs: None)

    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    reflection = {"backlog_candidates": [{"summary": "workspace tool friction"}], "memory_actions": [{"kind": "note"}]}
    post_calls = []
    global_calls = []

    def fake_post(env, task, *_args, **kwargs):
        post_calls.append((pathlib.Path(env.drive_root), task.get("project_id")))
        callback = kwargs.get("on_reflection")
        if callback is not None:
            callback(reflection, object())
        return reflection

    def fake_global(env, task, entry, _llm):
        global_calls.append((pathlib.Path(env.drive_root), task.get("project_id"), entry))

    monkeypatch.setattr(pipeline, "_run_post_task_processing_async", fake_post)
    monkeypatch.setattr(pipeline, "_run_global_backlog_promotion_only", fake_global)

    pending_events = []
    env = SimpleNamespace(repo_dir=tmp_path, drive_root=child, drive_path=lambda rel: child / rel)
    pipeline.emit_task_results(
        env=env,
        memory=object(),
        llm=object(),
        pending_events=pending_events,
        task={
            "id": "task-project",
            "type": "task",
            "chat_id": 1,
            "text": "fix workspace",
            "project_id": "proj-1",
            "budget_drive_root": str(parent),
        },
        text="Done",
        usage={"rounds": 2, "cost": 0.2},
        llm_trace={"tool_calls": [], "reasoning_notes": []},
        start_time=0.0,
        drive_logs=child / "logs",
        ctx=SimpleNamespace(pending_restart_reason=""),
    )

    assert post_calls == [(child, "proj-1")]
    assert global_calls == [(parent, "proj-1", reflection)]


def test_truncate_with_notice_uses_utils_ssot():
    """_truncate_with_notice in agent_task_pipeline is now truncate_review_artifact from utils.
    Verify it truncates long strings and adds a visible omission note (no silent clipping)."""
    from ouroboros.utils import truncate_review_artifact
    # The alias in agent_task_pipeline should be the same object
    assert pipeline._truncate_with_notice is truncate_review_artifact

    short = "hello"
    assert pipeline._truncate_with_notice(short, 100) == short

    long_text = "x" * 200
    result = pipeline._truncate_with_notice(long_text, 50)
    assert result.startswith("x" * 50)
    assert "50" in result  # omission note mentions limit
    assert len(result) > 50  # note appended, not just raw slice

    # Handles None gracefully
    assert pipeline._truncate_with_notice(None, 10) == ""


def test_emit_task_results_surfaces_receipt_absent_flag_in_event_stream(tmp_path, monkeypatch):
    # Regression: the receipt_absent / expected_output_ungrounded objective-axis flag must reach
    # the task_eval (events.jsonl) and task_metrics (pending_events) monitoring streams — where the
    # day-1 kill-switch metric reads it — not only the stored task_result.json. Previously the flag
    # was applied inside _store_task_result, AFTER the events were already emitted from an un-flagged
    # outcome, so the event stream never saw it.
    captured = {}
    monkeypatch.setattr(pipeline, "_store_task_result", lambda *a, **k: captured.update(k))
    monkeypatch.setattr(pipeline, "_run_chat_consolidation", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_run_scratchpad_consolidation", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_run_post_task_processing_async", lambda *a, **k: None)

    pending_events = []
    env = SimpleNamespace(drive_root=tmp_path)
    drive_logs = tmp_path / "logs"
    drive_logs.mkdir(parents=True)

    # reviewable effects (commit_reviewed) + empty receipt store -> receipt_absent
    pipeline.emit_task_results(
        env=env, memory=object(), llm=object(),
        pending_events=pending_events,
        task={"id": "flagme", "type": "task", "chat_id": 1, "text": "do it"},
        text="All done",
        usage={"rounds": 2, "cost": 0.2},
        llm_trace={"tool_calls": [{"tool": "commit_reviewed", "status": "ok"}], "reasoning_notes": []},
        start_time=0.0,
        drive_logs=drive_logs,
        ctx=SimpleNamespace(pending_restart_reason=""),
    )

    # task_metrics event (pending_events) carries the flag
    metrics = next(e for e in pending_events if e["type"] == "task_metrics")
    assert metrics["outcome_axes"]["objective"].get("warning") == "receipt_absent"

    # task_eval event (events.jsonl) carries the flag
    events = [json.loads(line) for line in (drive_logs / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    task_eval = next(e for e in events if e.get("type") == "task_eval")
    assert task_eval["outcome_axes"]["objective"].get("warning") == "receipt_absent"

    # single source: the SAME flagged loop_outcome is threaded to _store_task_result (not re-derived)
    assert captured["loop_outcome"]["outcome_axes"]["objective"].get("warning") == "receipt_absent"


def test_stopped_direct_turn_pays_no_post_task_synthesis(tmp_path, monkeypatch):
    """"Stop now" on a direct-chat turn: ZERO model calls after the stop, end to
    end through the real post-task lane. The loop's hard stop records the
    existing ``_skip_post_task_synthesis`` marker on the tool context;
    ``emit_task_results`` copies it onto the task before the root predicate
    runs, so the summary/reflection worker is never dispatched and no open
    ``root_phase_checkpoint`` is seeded for the boot reconciler to re-pay. A
    positive control (the same turn, not stopped) proves the recording model
    would have seen the paid summary + reflection calls."""
    import ouroboros.llm as llm_mod
    from ouroboros.outcomes import REASON_OWNER_REQUESTED_FINALIZATION
    from supervisor.owner_stop import REASON_OWNER_STOPPED_DIRECT_TURN
    from tests.test_delivery_forced_finalization import _forced_test_context

    calls = []

    class RecordingLLM:
        def __init__(self, *_a, **_k):
            pass

        def chat(self, messages=None, model=None, **kw):
            calls.append({"model": model, "has_tools": bool(kw.get("tools"))})
            return ({"role": "assistant", "content": "recorded"},
                    {"cost": 0.0, "prompt_tokens": 1, "completion_tokens": 1})

    monkeypatch.setattr(llm_mod, "LLMClient", RecordingLLM)

    class InlineThread:  # the non-blocking lane, run inline so any paid call is recorded
        def __init__(self, *, target, daemon):
            assert daemon is True
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(pipeline.threading, "Thread", InlineThread)
    root = tmp_path / "data"
    (root / "logs").mkdir(parents=True)
    (root / "memory").mkdir()
    (root / "repo").mkdir()
    env = SimpleNamespace(drive_root=root, repo_dir=root / "repo", drive_path=lambda rel: root / rel)
    # Non-trivial and above the reflection threshold: both paid post-steps would fire.
    llm_trace = {"reasoning_notes": [], "tool_calls": [
        {"tool": "list_files", "arguments": {}, "result": "ok", "is_error": False}]}

    def _turn(task_id, *, stopped):
        loop, registry, ctx, _trace = _forced_test_context(root, usage={"rounds": 20, "cost": 0.02})
        registry._ctx.task_id = task_id
        registry._ctx.task_metadata = {"budget_drive_root": str(root), "root_task_id": task_id}
        if stopped:
            text, usage, _t = loop._handle_forced_finalization(ctx, REASON_OWNER_STOPPED_DIRECT_TURN)
            assert usage["reason_code"] == REASON_OWNER_REQUESTED_FINALIZATION
        else:
            text, usage = "Listed the root.", dict(ctx.accumulated_usage)
        task = {"id": task_id, "type": "task", "chat_id": 1, "_is_direct_chat": True,
                "text": "List the repository root and keep listing it until the owner stops you."}
        events = []
        calls.clear()
        pipeline.emit_task_results(
            env, object(), object(), events, task, text, usage, dict(llm_trace), 0.0,
            root / "logs", ctx=registry._ctx, event_queue=None,
        )
        return task, events, list(calls)

    task, events, stopped_calls = _turn("stopped1", stopped=True)
    assert stopped_calls == [], stopped_calls
    assert task.get("_skip_post_task_synthesis") is True
    assert [e["type"] for e in events] == ["send_message", "task_metrics", "task_done"]
    stored = pipeline.load_task_result(root, "stopped1") or {}
    assert stored.get("status") == "failed", stored
    assert "root_phase_checkpoint" not in stored, stored  # nothing for the boot reconciler to re-pay

    def _summary_rows(task_id):
        chat_log = root / "logs" / "chat.jsonl"
        rows = chat_log.read_text(encoding="utf-8").splitlines() if chat_log.exists() else []
        return [row for row in rows if "authored_root_summary" in row and task_id in row]

    assert _summary_rows("stopped1") == []

    _task, _events, control_calls = _turn("control1", stopped=False)
    assert len(control_calls) >= 2 and all(not c["has_tools"] for c in control_calls), control_calls
    assert len(_summary_rows("control1")) == 1  # the reader sees the phase when it does run
