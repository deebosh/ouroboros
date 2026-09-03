"""#496: a completed child stays visible while its parent still is.

The window's recency floor was used as a PROXY for "does this child belong to a
topology the window can still describe". It never was one: a root that keeps
producing telemetry pushes the floor past its own children's finished lifecycle
rows, so every rebuild (restart, panel reopen, reconnect) dropped the completed
children of a STILL-RUNNING parent and de-roled their chat finals — the swarm
looked like it never happened. These tests pin the honest predicate from the
2026-08-23 liveness doctrine instead: a child row is anchored when the child is
itself active, OR its parent is represented among the rows the response emits,
OR the parent is alive. The anti-zombie guarantee the floor was written for (an
ABSENT, dead-or-unknown parent) is pinned by the negative cases below.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from ouroboros.gateway.history import make_chat_history_endpoint
from ouroboros.subagent_messages import SUBAGENT_MESSAGE_FIELDS

ROOT_FLOOD = 70  # > the default n_progress=60, so the floor lands past the children


def _run_full(tmp_path, params=None):
    endpoint = make_chat_history_endpoint(tmp_path)
    resp = asyncio.run(endpoint(SimpleNamespace(query_params=params or {})))
    return json.loads(resp.body.decode("utf-8"))


def _run(tmp_path, params=None):
    return _run_full(tmp_path, params)["messages"]


def _lineage_row(ts, child, ev, *, parent="root", chat_id=1):
    return json.dumps({
        "ts": ts, "content": f"child {ev}", "task_id": child, "chat_id": chat_id,
        "delegation_role": "subagent", "parent_task_id": parent,
        "root_task_id": parent, "subagent_event": ev,
    })


def _root_flood(count=ROOT_FLOOD, *, task_id="root", chat_id=1):
    """Fresh non-lineage telemetry from ONE task — this is what sets the floor."""
    return [
        json.dumps({
            "ts": f"2026-06-05T02:{i // 60:02d}:{i % 60:02d}Z",
            "content": f"step-{i}", "task_id": task_id, "chat_id": chat_id,
            "delegation_role": "root",
        })
        for i in range(count)
    ]


def _child_final(ts, child, *, parent="root", chat_id=1, kind=""):
    row = {
        "ts": ts, "direction": "system" if kind else "out", "chat_id": chat_id,
        "text": f"{child} answer", "task_id": child,
        "delegation_role": "subagent", "parent_task_id": parent,
        "root_task_id": parent, "subagent_task_id": child,
    }
    if kind:
        row["type"] = kind
    return json.dumps(row)


def _write(tmp_path, *, chat_lines=(), progress_lines=(), results=None):
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    for name, lines in (("chat.jsonl", chat_lines), ("progress.jsonl", progress_lines)):
        body = "\n".join(lines) + ("\n" if lines else "")
        (logs / name).write_text(body, encoding="utf-8")
    if results:
        results_dir = tmp_path / "task_results"
        results_dir.mkdir(exist_ok=True)
        for task_id, payload in results.items():
            (results_dir / f"{task_id}.json").write_text(
                json.dumps(payload), encoding="utf-8",
            )


def _496_fixture(tmp_path, root_status, *, chat_id=1):
    """The reported shape: two children finished long before the root's own
    telemetry flood, root still (or only just) settled."""
    chat = [
        _child_final("2026-06-05T00:00:10Z", "child1", chat_id=chat_id),
        _child_final("2026-06-05T00:00:11Z", "child2", chat_id=chat_id),
        _child_final("2026-06-05T00:00:12Z", "child2", chat_id=chat_id,
                     kind="task_summary"),
    ]
    progress = []
    for child, offsets in (("child1", (1, 2, 3)), ("child2", (4, 5, 6))):
        for i, ev in zip(offsets, ("scheduled", "running", "completed")):
            progress.append(
                _lineage_row(f"2026-06-05T00:00:0{i}Z", child, ev, chat_id=chat_id)
            )
    progress += _root_flood(chat_id=chat_id)
    _write(tmp_path, chat_lines=chat, progress_lines=progress, results={
        "root": {"task_id": "root", "status": root_status},
        "child1": {"task_id": "child1", "status": "completed"},
        "child2": {"task_id": "child2", "status": "completed"},
    })


def _child_rows(messages, *, progress):
    return [
        m for m in messages
        if bool(m.get("is_progress")) is progress
        and str(m.get("task_id") or "").startswith("child")
    ]


def test_completed_children_anchor_to_working_represented_root(tmp_path):
    """#496 itself: the root is still Working and its own rows are in the
    window, so its finished children keep their lineage and their closer."""
    _496_fixture(tmp_path, "running")
    for params in ({}, {"n_progress": "5"}):
        payload = _run_full(tmp_path, params)
        messages = payload["messages"]
        lineage = _child_rows(messages, progress=True)
        assert {m["task_id"] for m in lineage} == {"child1", "child2"}
        assert len(lineage) == 6
        assert all(m.get("task_terminal_status") == "completed" for m in lineage)
        finals = _child_rows(messages, progress=False)
        assert len(finals) == 3
        assert all(m.get("parent_task_id") == "root" for m in finals)
        assert all(m.get("delegation_role") == "subagent" for m in finals)
        # The root is NOT terminal: replay must not close its card.
        root_rows = [m for m in messages if m.get("task_id") == "root"]
        assert root_rows and not any("task_terminal_status" in m for m in root_rows)
        assert payload["window"]["complete"] is False
        assert "quota" in payload["window"]["truncated_by"]


def test_completed_children_anchor_to_terminal_but_represented_root(tmp_path):
    """The 'represented' half on its own: a settled parent whose rows are in the
    window keeps its children AND carries its own terminal status, so keeping
    them cannot mint an unfinishable card."""
    _496_fixture(tmp_path, "completed")
    messages = _run(tmp_path)
    lineage = _child_rows(messages, progress=True)
    assert {m["task_id"] for m in lineage} == {"child1", "child2"}
    root_rows = [m for m in messages if m.get("task_id") == "root" and m.get("is_progress")]
    assert root_rows
    assert all(m.get("task_terminal_status") == "completed" for m in root_rows)


def test_children_of_alive_but_unrepresented_root_survive(tmp_path, monkeypatch):
    """The 'alive' half: the flood belongs to an unrelated task, so the parent
    has no emitted row — it is still running, so its child stays. Each
    task_results file is still read exactly once per request."""
    import ouroboros.task_status as ts_mod

    progress = [
        _lineage_row(f"2026-06-05T00:00:0{i}Z", "child", ev)
        for i, ev in zip((1, 2, 3), ("scheduled", "running", "completed"))
    ]
    progress += _root_flood(task_id="other")
    _write(tmp_path, progress_lines=progress)

    calls: dict = {}

    def fake_load(_dr, tid, **_kw):
        calls[tid] = calls.get(tid, 0) + 1
        return {"status": "running"} if tid == "root" else {"status": "completed"}

    monkeypatch.setattr(ts_mod, "load_effective_task_result", fake_load)

    lineage = [m for m in _run(tmp_path) if m.get("delegation_role") == "subagent"]
    assert len(lineage) == 3
    assert {m["task_id"] for m in lineage} == {"child"}
    assert calls.get("child") == 1
    assert calls.get("root") == 1


def test_absent_terminal_root_children_still_dropped(tmp_path):
    """Anti-zombie preserved: the parent has NO emitted row and is terminal, so
    its old lineage is dropped and its child's final is de-roled."""
    progress = [
        _lineage_row(f"2026-06-05T00:00:0{i}Z", "child", ev)
        for i, ev in zip((1, 2, 3), ("scheduled", "running", "completed"))
    ]
    progress += _root_flood(task_id="other")
    _write(
        tmp_path,
        chat_lines=[_child_final("2026-06-05T00:00:10Z", "child")],
        progress_lines=progress,
        results={
            "root": {"task_id": "root", "status": "completed"},
            "child": {"task_id": "child", "status": "completed"},
        },
    )
    messages = _run(tmp_path)
    assert _child_rows(messages, progress=True) == []
    final = next(m for m in messages if m.get("task_id") == "child")
    assert final["text"] == "child answer"  # the row survives, de-roled
    assert not any(field in final for field in SUBAGENT_MESSAGE_FIELDS)


def test_absent_unknown_root_children_still_dropped(tmp_path):
    """An unknown parent (result pruned, no rows) is not 'alive' — the child is
    dropped exactly as before, so a pruned swarm cannot resurface."""
    progress = [
        _lineage_row(f"2026-06-05T00:00:0{i}Z", "child", ev)
        for i, ev in zip((1, 2, 3), ("scheduled", "running", "completed"))
    ]
    progress += _root_flood(task_id="other")
    _write(
        tmp_path,
        chat_lines=[_child_final("2026-06-05T00:00:10Z", "child")],
        progress_lines=progress,
        results={"child": {"task_id": "child", "status": "completed"}},
    )
    messages = _run(tmp_path)
    assert _child_rows(messages, progress=True) == []
    final = next(m for m in messages if m.get("task_id") == "child")
    assert not any(field in final for field in SUBAGENT_MESSAGE_FIELDS)


def test_receipt_lands_on_final_when_progress_rows_unread(tmp_path):
    """Executor attribution stays discoverable without "Load older": when the
    child's progress rows are outside the read tail, the persisted receipt lands
    on the one final row the window still holds."""
    _write(
        tmp_path,
        chat_lines=[_child_final("2026-06-05T00:00:10Z", "child")],
        progress_lines=_root_flood(),
        results={
            "root": {"task_id": "root", "status": "running"},
            "child": {
                "task_id": "child", "status": "completed",
                "subagent_envelope": {
                    "execution_evidence": {"delegated_runs_succeeded": 1},
                    "actual_substrate": "harness_used",
                },
            },
        },
    )
    final = next(m for m in _run(tmp_path) if m.get("task_id") == "child")
    assert final["parent_task_id"] == "root"
    assert final["execution_evidence"] == {"delegated_runs_succeeded": 1}
    assert final["actual_substrate"] == "harness_used"


def test_project_thread_keeps_children_of_a_working_root(tmp_path):
    """A CLI tree homed into a Project room: the floor there is the root's OWN
    telemetry, so without the anchor every critic vanished on reopen. Main is
    untouched."""
    from ouroboros.projects_registry import create_project

    chat_id = int(create_project(tmp_path, "cli-tree")["chat_id"])
    _496_fixture(tmp_path, "running", chat_id=chat_id)

    project_rows = _run(tmp_path, {"chat_id": str(chat_id)})
    lineage = _child_rows(project_rows, progress=True)
    assert {m["task_id"] for m in lineage} == {"child1", "child2"}
    assert _child_rows(project_rows, progress=False)

    main_rows = _run(tmp_path, {"chat_id": "1"})
    assert _child_rows(main_rows, progress=True) == []
    assert _child_rows(main_rows, progress=False) == []
