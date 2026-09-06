"""Physical task-event continuation, without a second event store."""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from ouroboros.gateway.task_events import _TaskEventCursorFollower, api_task_events, iter_task_events
from ouroboros.gateway.task_list_scan import raw_result_facts
from ouroboros.task_results import write_task_result


def seed(root, task_id="root"):
    (root / "logs").mkdir(parents=True)
    write_task_result(root, task_id, "running", ts="2026-01-01T00:00:00Z")
    return root


def append(root, text, *, source="progress", task_id="root", **fields):
    path = root / "logs" / f"{source}.jsonl"
    raw = (json.dumps({"task_id": task_id, "content": text, **fields}) + "\n").encode()
    with path.open("ab") as handle:
        handle.write(raw)
    return len(raw)


def request(root, cursor=None, wait=0):
    body = json.dumps({"v": 2, "wait": wait, "cursor": cursor}).encode()
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/api/tasks/root/events",
        "path_params": {"task_id": "root"}, "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(drive_root=root))}, receive)


def stream(root, cursor=None, wait=0, on_event=None):
    async def consume():
        response = await api_task_events(request(root, cursor, wait))
        assert response.status_code == 200
        events = []
        async for frame in response.body_iterator:
            event = json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))
            events.append(event)
            if on_event:
                on_event(event)
        return events
    return asyncio.run(consume())


def content(events):
    return [row["data"]["content"] for row in events if row.get("data", {}).get("content")]


def test_reconnect_resumes_after_exact_row_and_keeps_backdated_append(tmp_path):
    root = seed(tmp_path / "data")
    first_bytes = append(root, "first", ts="2026-01-01T00:02:00Z")
    append(root, "second", ts="2026-01-01T00:01:00Z")
    first = stream(root)
    assert content(first) == ["first", "second"]
    first_cursor = first[0]["cursor"]
    assert first_cursor["positions"][str(root)]["progress"] == first_bytes
    # A cut connection after only the first frame must not acknowledge second.
    resumed = stream(root, first_cursor)
    assert content(resumed) == ["second"]
    append(root, "backdated", ts="2025-01-01T00:00:00Z")
    last = stream(root, resumed[-1]["cursor"])
    assert content(last) == ["backdated"]
    assert last[0]["seq"] > resumed[-1]["seq"]
    assert first[1]["event_id"] == resumed[0]["event_id"]


@pytest.mark.parametrize("source", ["progress", "chat", "events", "tools", "supervisor"])
def test_every_source_survives_two_rotations_and_legacy_replay(tmp_path, source):
    root = seed(tmp_path / "data")
    append(root, "first", source=source)
    initial = stream(root)
    (root / "archive").mkdir()
    live = root / "logs" / f"{source}.jsonl"
    for index in [1, 2]:
        append(root, f"before-{index}", source=source)
        os.replace(live, root / "archive" / f"{source}_20260101T00000{index}.jsonl")
        live.touch()
    append(root, "last", source=source)
    resumed = stream(root, initial[-1]["cursor"])
    assert content(resumed) == ["before-1", "before-2", "last"]
    assert content(iter_task_events(root, "root")) == ["first", "before-1", "before-2", "last"]


def test_late_child_and_changed_existing_lineage_replay_disclosed_view(tmp_path):
    root = seed(tmp_path / "data")
    append(root, "parent")
    append(root, "before-discovery", task_id="", subagent_task_id="child")
    write_task_result(root, "child", "running", delegation_role="subagent", parent_task_id="elsewhere")
    first = stream(root)
    assert content(first) == ["parent"]
    child = tmp_path / "child"
    (child / "logs").mkdir(parents=True)
    append(child, "child", task_id="child")
    write_task_result(root, "child", "running", parent_task_id="root", child_drive_root=str(child))
    resumed = stream(root, first[-1]["cursor"])
    assert resumed[0]["type"] == "cursor_replay"
    assert resumed[0]["reason"] == "view_changed"
    assert set(content(resumed)) == {"parent", "before-discovery", "child"}
    assert next(r for r in resumed if r.get("data", {}).get("content") == "parent")["event_id"] == first[0]["event_id"]


def test_live_partial_waits_but_immutable_partial_discloses_and_advances(tmp_path):
    root = seed(tmp_path / "data")
    path = root / "logs" / "progress.jsonl"
    partial = json.dumps({"task_id": "root", "content": "partial"}).encode()
    path.write_bytes(partial)
    first = stream(root)
    assert content(first) == []
    assert first[-1]["cursor"]["positions"][str(root)]["progress"] == 0
    with path.open("ab") as handle:
        handle.write(b"\n")
    second = stream(root, first[-1]["cursor"])
    assert content(second) == ["partial"]
    with path.open("ab") as handle:
        handle.write(b'{"torn":')
    (root / "archive").mkdir()
    os.replace(path, root / "archive" / "progress_20260101T000001.jsonl")
    path.touch()
    append(root, "after")
    third = stream(root, second[-1]["cursor"])
    assert [r["reason"] for r in third if r["type"] == "history_gap"] == ["invalid_archive_line"]
    assert content(third) == ["after"]
    assert content(stream(root, third[-1]["cursor"])) == []


def test_shortened_chain_refuses_without_reset(tmp_path):
    root = seed(tmp_path / "data")
    append(root, "first")
    cursor = stream(root)[-1]["cursor"]
    (root / "logs" / "progress.jsonl").write_bytes(b"")
    events = stream(root, cursor)
    assert len(events) == 1 and events[0]["type"] == "error"
    assert events[0]["reason"] == "cursor_unavailable"
    assert events[0]["cursor"]["positions"] == cursor["positions"]


def test_unreadable_chain_is_explicit(tmp_path, monkeypatch):
    from ouroboros.gateway import task_events
    root = seed(tmp_path / "data")
    def broken(*args, **kwargs):
        raise OSError("archive unreadable")
    monkeypatch.setattr(task_events, "jsonl_chain_handles", broken)
    events = stream(root)
    assert events[-1]["type"] == "error"
    assert events[-1]["reason"] == "cursor_unavailable"


def test_checkpoint_advances_unmatched_bytes_without_progress_or_sequence(tmp_path):
    root = seed(tmp_path / "data")
    first = stream(root)
    appended = False
    def after_initial(event):
        nonlocal appended
        if event["type"] == "task_result" and not appended:
            append(root, "unrelated", task_id="other")
            appended = True
    events = stream(root, first[-1]["cursor"], wait=1, on_event=after_initial)
    assert content(events) == []
    assert events[-1]["type"] == "cursor_checkpoint"
    assert events[-1]["seq"] == events[0]["seq"]
    assert events[-1]["cursor"]["positions"][str(root)]["progress"] > events[0]["cursor"]["positions"][str(root)]["progress"]
    assert content(stream(root, events[-1]["cursor"])) == []


def test_terminal_snapshot_materializes_once_and_legacy_ts_never_bounds_history(tmp_path, monkeypatch):
    from ouroboros.gateway import tasks
    root = seed(tmp_path / "data")
    (root / "archive").mkdir()
    (root / "archive" / "progress_20250101T000001.jsonl").write_text(
        json.dumps({"task_id": "root", "content": "early"}) + "\n", encoding="utf-8")
    write_task_result(root, "root", "completed", ts="2026-01-01T00:00:00Z", result="done")
    real = tasks.load_effective_task_result
    materialized = []
    def read(*args, **kwargs):
        materialized.append(kwargs.get("materialize_artifacts", True))
        return real(*args, **kwargs)
    monkeypatch.setattr(tasks, "load_effective_task_result", read)
    events = stream(root)
    assert content(events) == ["early"]
    assert events[-1]["type"] == "task_result" and events[-1]["data"]["result"] == "done"
    assert materialized.count(True) == 1
    assert content(iter_task_events(root, "root")) == ["early"]


def test_result_memo_reconnect_and_same_size_rewrite_invalidation(tmp_path, monkeypatch):
    from ouroboros.gateway import tasks
    root = seed(tmp_path / "data")
    write_task_result(root, "child", "running", delegation_role="subagent", parent_task_id="aaaa")
    reads = []
    real = tasks.read_json_dict
    monkeypatch.setattr(tasks, "read_json_dict", lambda path: reads.append(path.name) or real(path))
    stream(root)
    reads.clear()
    stream(root)
    assert reads == []
    path = root / "task_results" / "child.json"
    before = path.stat()
    path.write_bytes(path.read_bytes().replace(b'"aaaa"', b'"root"'))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    follower = _TaskEventCursorFollower(root, "root", {"v": 2, "seq": 0, "view": "", "positions": {}})
    follower.refresh_view()
    assert "child" in follower.task_filter_ids
    assert reads == ["child.json"]


def test_result_memo_drops_deleted_and_never_caches_torn_or_changed_read(tmp_path):
    root = seed(tmp_path / "data")
    path = root / "task_results" / "child.json"
    path.write_text('{"torn":', encoding="utf-8")
    rows, errors = raw_result_facts(path.parent)
    assert "child.json" in errors and "child.json" not in rows
    path.unlink()
    write_task_result(root, "child", "running", delegation_role="subagent", parent_task_id="root")
    rows, errors = raw_result_facts(path.parent)
    assert rows["child.json"]["parent_task_id"] == "root" and not errors
    path.unlink()
    assert "child.json" not in raw_result_facts(path.parent)[0]


def test_main_dialogue_uses_tail_and_preserves_original_ids(tmp_path, monkeypatch):
    from ouroboros.gateway import _helpers
    from ouroboros.server_routing_context import _main_routing_manifest
    root = seed(tmp_path / "data")
    path = root / "logs" / "chat.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for index in range(4000):
            handle.write(json.dumps({"text": str(index) + "x" * 400,
                "chat_id": 0, "client_message_id": f"m{index}", "task_id": "original"}) + "\n")
    (root / "archive").mkdir()
    (root / "archive" / "chat_20250101T000001.jsonl").write_text('{"text":"archive"}\n', encoding="utf-8")
    real = _helpers.iter_jsonl_objects
    reads = []
    def read(path, *args, **kwargs):
        reads.append((path, kwargs.get("tail_bytes")))
        return real(path, *args, **kwargs)
    monkeypatch.setattr(_helpers, "iter_jsonl_objects", read)
    ctx = SimpleNamespace(DRIVE_ROOT=root, RUNNING={}, PENDING=[])
    manifest = _main_routing_manifest(ctx)
    dialogue = manifest["recent_canonical_dialogue"]
    assert len(dialogue) == 20 and dialogue[0]["client_message_id"] == "m3980"
    assert dialogue[-1]["chat_id"] == 0 and dialogue[-1]["task_id"] == "original"
    assert len(reads) == 1 and reads[0][1] is not None
    assert manifest["omissions"]["dialogue_rows"] is None


def test_suppression_change_replays_previously_filtered_done(tmp_path, monkeypatch):
    from ouroboros.gateway import tasks
    root = seed(tmp_path / 'data')
    append(root, 'done-row', source='events', type='task_done')
    result = {'task_id': 'root', 'status': 'running', 'workspace_root': '/project', 'artifact_status': 'pending'}
    monkeypatch.setattr(tasks, 'load_effective_task_result', lambda *args, **kwargs: dict(result))
    first = stream(root)
    assert content(first) == []
    result['artifact_status'] = 'ready'
    second = stream(root, first[-1]['cursor'])
    assert second[0]['type'] == 'cursor_replay'
    assert content(second) == ['done-row']


def test_creation_floor_counts_skipped_bytes_in_cursor(tmp_path):
    root = seed(tmp_path / 'data')
    write_task_result(root, 'root', 'running', created_at='2026-01-01T00:01:00Z')
    (root / 'archive').mkdir()
    old = (json.dumps({'task_id': 'root', 'content': 'before-creation'}) + '\n').encode()
    (root / 'archive' / 'progress_20250101T000001.jsonl').write_bytes(old)
    recent_size = append(root, 'recent')
    first = stream(root)
    assert content(first) == ['recent']
    assert first[-1]['cursor']['positions'][str(root)]['progress'] == len(old) + recent_size
    append(root, 'later')
    assert content(stream(root, first[-1]['cursor'])) == ['later']


def test_stream_disconnect_closes_open_chain_handles(tmp_path, monkeypatch):
    import pathlib
    root = seed(tmp_path / 'data')
    append(root, 'first')
    append(root, 'second')
    handles = []
    real = pathlib.Path.open
    def opened(path, *args, **kwargs):
        handle = real(path, *args, **kwargs)
        if path.name == 'progress.jsonl':
            handles.append(handle)
        return handle
    monkeypatch.setattr(pathlib.Path, 'open', opened)
    async def consume_one():
        response = await api_task_events(request(root))
        await anext(response.body_iterator)
        assert any(not handle.closed for handle in handles)
        await response.body_iterator.aclose()
    asyncio.run(consume_one())
    assert handles and all(handle.closed for handle in handles)


def test_main_dialogue_preserves_two_archive_horizon(tmp_path):
    from ouroboros.server_routing_context import _main_routing_manifest
    root = seed(tmp_path / 'data')
    (root / 'archive').mkdir()
    for index in [1, 2, 3]:
        (root / 'archive' / f'chat_20260101T00000{index}.jsonl').write_text(
            json.dumps({'text': f'archive-{index}', 'client_message_id': f'm{index}', 'chat_id': -5}) + '\n',
            encoding='utf-8')
    (root / 'logs' / 'chat.jsonl').write_text('{"text": ""}\n', encoding='utf-8')
    rows = _main_routing_manifest(SimpleNamespace(DRIVE_ROOT=root, RUNNING={}, PENDING=[]))['recent_canonical_dialogue']
    assert [row['client_message_id'] for row in rows] == ['m2', 'm3']
    assert [row['chat_id'] for row in rows] == [-5, -5]


def test_reconnect_gets_fresh_terminal_result_without_replaying_consumed_logs(tmp_path, monkeypatch):
    from ouroboros.gateway import tasks
    root = seed(tmp_path / 'data')
    append(root, 'first')
    first = stream(root)
    assert first[0]['event_id'] and 'line' not in first[0]
    assert first[-2]['data']['status'] == 'running'
    write_task_result(root, 'root', 'completed', result='finished after reconnect')
    real = tasks.load_effective_task_result
    reads = []
    def read(*args, **kwargs):
        reads.append(kwargs.get('materialize_artifacts', True))
        return real(*args, **kwargs)
    monkeypatch.setattr(tasks, 'load_effective_task_result', read)
    second = stream(root, first[-1]['cursor'])
    assert content(second) == []
    assert second[-1]['type'] == 'task_result'
    assert second[-1]['data']['result'] == 'finished after reconnect'
    assert second[-1]['data']['status'] == 'completed'
    assert reads.count(True) == 1


def test_result_facts_cannot_admit_an_invalid_schema(tmp_path):
    from ouroboros.task_results import load_task_result
    root = seed(tmp_path / 'data')
    path = root / 'task_results' / 'child.json'
    raw = {'_schema_version': 999, 'task_id': 'child', 'status': 'running',
           'delegation_role': 'subagent', 'parent_task_id': 'root'}
    path.write_text(json.dumps(raw), encoding='utf-8')
    rows, _ = raw_result_facts(path.parent)
    assert rows['child.json']['schema_refusal'] == 'future_schema'
    follower = _TaskEventCursorFollower(root, 'root', {'v': 2, 'seq': 0, 'view': '', 'positions': {}})
    follower.refresh_view()
    assert 'child' not in follower.task_filter_ids
    with pytest.raises(ValueError, match='future_schema'):
        load_task_result(root, 'child', strict=True)
    assert json.loads(path.read_text()) == raw
