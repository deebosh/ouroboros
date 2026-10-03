"""Owner-approved room provenance; all history and registry data are synthetic."""
from __future__ import annotations

import json

import pytest

from ouroboros import projects_registry
from ouroboros.context import build_recent_sections
from ouroboros.dialogue_provenance import RoomLabelResolver, render_row_text, row_author
from ouroboros.memory import Memory


def _write_chat(root, rows):
    path = root / "logs" / "chat.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return path


def _registry(root, projects):
    path = root / "state" / "projects.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"projects": projects}), encoding="utf-8")
    return path


def _recent(memory, chat_id):
    sections = build_recent_sections(memory, None, thread_chat_id=chat_id)
    return next(s for s in sections if s.startswith("## Recent chat\n"))


def test_room_resolution_is_current_read_only_and_not_lineage(tmp_path, monkeypatch):
    project = projects_registry.create_project(tmp_path, "alpha", name="Original")
    projects_registry.update_project(tmp_path, "alpha", name="Renamed")
    path = tmp_path / "state" / "projects.json"
    before = path.read_bytes()
    read = projects_registry.list_reserved_projects
    calls = []
    monkeypatch.setattr(projects_registry, "list_reserved_projects", lambda root: (calls.append(root), read(root))[1])
    resolver = RoomLabelResolver(tmp_path)
    for _ in range(10):
        assert resolver.label({"chat_id": 1, "project_id": "alpha"}) == "Main"
        assert resolver.label({"chat_id": project["chat_id"], "project_id": "wrong-lineage"}) == (
            f"Project Renamed [chat_id={project['chat_id']}]"
        )
        assert resolver.label({"project_id": "alpha"}) == "Unresolved room [chat_id=missing]"
    assert calls == [tmp_path]
    assert path.read_bytes() == before


@pytest.mark.parametrize("lifecycle", ["active", "deleting", "tombstoned"])
def test_reserved_names_and_removed_or_ambiguous_rooms(tmp_path, lifecycle):
    row = {"id": "alpha", "chat_id": 1500, "name": "Alpha ] team", "lifecycle": lifecycle}
    path = _registry(tmp_path, [row])
    assert RoomLabelResolver(tmp_path).label({"chat_id": 1500}) == "Project Alpha ] team [chat_id=1500]"
    _registry(tmp_path, [{**row, "name": ""}])
    assert RoomLabelResolver(tmp_path).label({"chat_id": 1500}) == "Project name unavailable [chat_id=1500]"
    _registry(tmp_path, [row, {**row, "id": "other", "name": "Other"}])
    resolver = RoomLabelResolver(tmp_path)
    assert resolver.label({"chat_id": 1500}) == "Ambiguous room [chat_id=1500]"
    assert 1500 in resolver.project_chat_ids  # Label uncertainty must not widen focused visibility.
    _registry(tmp_path, [])
    assert RoomLabelResolver(tmp_path).label({"chat_id": 1500}) == "Unknown room [chat_id=1500]"
    path.unlink()
    assert RoomLabelResolver(tmp_path).label({"chat_id": 1500}) == "Unknown room [chat_id=1500]"
    assert not path.exists()


@pytest.mark.parametrize("entry,label", [
    ({}, "Unresolved room [chat_id=missing]"),
    ({"chat_id": None}, "Unresolved room [chat_id=missing]"),
    ({"chat_id": "oops"}, "Unresolved room [chat_id=oops]"),
    ({"chat_id": True}, "Unresolved room [chat_id=True]"),
    ({"chat_id": 1.2}, "Unresolved room [chat_id=1.2]"),
    ({"chat_id": "1"}, "Main"),
    ({"chat_id": 0}, "Hidden [chat_id=0]"),
    ({"chat_id": 987654}, "Unknown room [chat_id=987654]"),
])
def test_unknown_address_never_defaults_to_main(entry, label):
    assert RoomLabelResolver(projects=[]).label(entry) == label


def test_actual_main_context_opts_in_once_and_keeps_existing_visibility(tmp_path, monkeypatch):
    _registry(tmp_path, [{"id": "alpha", "chat_id": 1500, "name": "Alpha"}])
    rows = [
        {"chat_id": 1, "direction": "in", "text": "MAIN", "project_id": "alpha"},
        {"chat_id": 1500, "direction": "out", "text": "PROJECT"},
        {"chat_id": 987654, "direction": "system", "text": "UNKNOWN"},
        {"direction": "in", "text": "MISSING"},
        {"chat_id": 0, "direction": "system", "text": "HIDDEN"},
        {"chat_id": -10, "direction": "in", "text": "A2A EXCLUDED"},
    ]
    _write_chat(tmp_path, rows)
    memory = Memory(tmp_path)
    expected_rows, _ = memory.read_unconsolidated_chat({}, 1000)
    read = projects_registry.list_reserved_projects
    calls = []
    monkeypatch.setattr(projects_registry, "list_reserved_projects", lambda root: (calls.append(root), read(root))[1])
    recent = _recent(memory, 1)
    assert calls == [tmp_path]
    assert recent == "## Recent chat\n\n" + memory.summarize_chat(
        expected_rows, include_room_labels=True, room_resolver=RoomLabelResolver(projects=read(tmp_path)),
    )
    for marker in ("[room=Main]", "[room=Project Alpha [chat_id=1500]]",
                   "[room=Unknown room [chat_id=987654]]", "[room=Unresolved room [chat_id=missing]]"):
        assert marker in recent
    assert "A2A EXCLUDED" not in recent
    assert recent.index("MAIN") < recent.index("PROJECT") < recent.index("UNKNOWN") < recent.index("MISSING")


@pytest.mark.parametrize("ambiguous", [False, True])
def test_focused_project_and_explicit_history_remain_byte_identical(tmp_path, monkeypatch, ambiguous):
    monkeypatch.setattr("ouroboros.memory._chat_history_snapshot_id", lambda *_: "fixture")
    projects = [{"id": "alpha", "chat_id": 1500, "name": "Alpha"}]
    if ambiguous:
        projects.append({"id": "beta", "chat_id": 1500, "name": "Beta"})
    _registry(tmp_path, projects)
    base = {"ts": "2026-01-01T00:01:00Z", "direction": "in", "sender_label": "Alex"}
    _write_chat(tmp_path, [
        {**base, "chat_id": 1, "text": "main"},
        {**base, "chat_id": 1500, "text": "project\nsecond line", "transport": {"provider": "mail"}},
        {**base, "chat_id": 1501, "text": "sibling"},
        {**base, "chat_id": -10, "text": "a2a"},
    ])
    memory = Memory(tmp_path)
    assert _recent(memory, 1500).encode() == (
        "## Recent chat\n\n← 00:01 [Alex [provider=mail]] project\nsecond line"
    ).encode()
    expected_history = (
        "Showing 3 of 3 messages; 0 older remain. Continue with offset=3, snapshot=fixture."
        " Pagination used a live offset; repeating an offset without the returned snapshot"
        " is shiftable if history changes.\n\n"
        "← [2026-01-01T00:01] [Alex] main\n"
        "← [2026-01-01T00:01] [Alex [provider=mail]] project\nsecond line\n"
        "← [2026-01-01T00:01] [Alex] sibling"
    ).encode()
    for chat_id in (1, 1500):
        assert memory.chat_history(chat_id=chat_id).encode() == expected_history


@pytest.mark.parametrize("direction", ["in", "out", "outgoing", "system"])
def test_row_author_and_text_retain_author_direction_transport_and_body(direction):
    """The retired block formatter's provenance moved to ``row_author``/``render_row_text``:
    the author by source fields (a system row is the host's, never "Ouroboros"), the
    transport facts beside it, and the body byte-exact."""
    row = {"ts": "2026-01-01T00:00:00Z", "chat_id": 1500, "direction": direction,
           "sender_label": "Alex", "text": "line one\r\n\r\nЖ🙂 line two\n",
           "transport": {"provider": "mail", "account_id": "acct", "conversation_id": "conv",
                         "thread_id": "thread", "delivery": {"state": "accepted"}}}
    author = row_author(row)
    assert render_row_text(row) == row["text"]
    kind = {"in": "human", "out": "ouroboros", "outgoing": "ouroboros"}.get(direction, "host")
    assert author["kind"] == kind
    assert author["label"].startswith({"human": "Alex", "ouroboros": "Ouroboros", "host": "host"}[kind])
    assert "provider=mail; account=acct; conversation=conv; thread=thread; delivery=accepted" in author["label"]
    assert RoomLabelResolver(projects=[{"id": "alpha", "chat_id": 1500, "name": "Alpha"}]).label(row) == (
        "Project Alpha [chat_id=1500]")


def test_presence_rows_of_one_room_share_one_label_from_transport_facts():
    from ouroboros.presence_bindings import conversation_key
    from ouroboros.presence_runner import _stable_numeric_id

    base = {"provider": "telegram", "account_id": "900", "conversation_id": "-100", "thread_id": ""}
    chat_id = _stable_numeric_id("presence-conversation", conversation_key("telegram", "900", "-100", ""))
    resolver = RoomLabelResolver(projects=[])
    inbound = {"chat_id": chat_id, "direction": "in", "transport": {**base, "conversation": {"title": "Aika ] admin"}}}
    receipt = {"chat_id": chat_id, "direction": "out", "type": "presence_delivery", "transport": {**base, "thread_id": "0"}}
    initiated = {"chat_id": chat_id, "direction": "in", "transport": dict(base)}
    summary = {"chat_id": chat_id, "direction": "system", "type": "task_summary",
               "presence_provenance": {**base, "binding_id": "1" * 32}}  # the turn's summary row carries no transport
    expected = f"Presence telegram -100 [chat_id={chat_id}]"
    # One room, four row types, one label: the correspondent-controlled title never enters it.
    assert {resolver.label(inbound), resolver.label(receipt), resolver.label(initiated), resolver.label(summary)} == {expected}
    assert resolver.label({**summary, "presence_provenance": {**base, "conversation_id": "-101"}}) == f"Unknown room [chat_id={chat_id}]"
    topic_chat = _stable_numeric_id("presence-conversation", conversation_key("telegram", "900", "-100", "42"))
    assert resolver.label({"chat_id": topic_chat, "transport": {**base, "thread_id": "42"}}) == (
        f"Presence telegram -100 topic 42 [chat_id={topic_chat}]"
    )
    # Transport facts that do not re-derive this exact chat id never name the room.
    assert resolver.label({"chat_id": chat_id + 1, "transport": dict(base)}) == f"Unknown room [chat_id={chat_id + 1}]"
    assert resolver.label({"chat_id": chat_id, "transport": {**base, "provider": ""}}) == f"Unknown room [chat_id={chat_id}]"
    # Brackets in a provider fact can never break the [room=...] marker.
    weird_chat = _stable_numeric_id("presence-conversation", conversation_key("telegram", "900", "x]y[z", ""))
    assert resolver.label({"chat_id": weird_chat, "transport": {**base, "conversation_id": "x]y[z"}}) == (
        f"Presence telegram x y z [chat_id={weird_chat}]"
    )


def test_terminal_projection_row_of_a_presence_turn_carries_the_room_facts(tmp_path):
    """The canonical terminal summary of a presence turn labels its room like every other row of it."""
    from ouroboros.presence_bindings import conversation_key
    from ouroboros.presence_runner import _stable_numeric_id
    from ouroboros.project_dialogue import append_terminal_task_projection
    from ouroboros.task_results import write_task_result

    chat_id = _stable_numeric_id("presence-conversation", conversation_key("telegram", "900", "-100", ""))
    event = {"provider": "telegram", "account_id": "900", "conversation_id": "-100", "thread_id": "",
             "source_event_id": "telegram:900:1", "conversation_key": "telegram:900:-100:0", "actor": {"id": "u1"}}
    write_task_result(tmp_path, "presence-turn-1", "completed", result="Done", terminal_origin="model_final",
                      metadata={"source": "presence", "presence": {"binding_id": "1" * 32, "event": event}})
    stored = __import__("ouroboros.task_results", fromlist=["load_task_result"]).load_task_result(tmp_path, "presence-turn-1")
    assert append_terminal_task_projection(tmp_path, "presence-turn-1", {"id": "presence-turn-1", "chat_id": chat_id},
                                           stored, {"status": "completed", "chat_id": chat_id})
    row = next(json.loads(line) for line in (tmp_path / "logs" / "chat.jsonl").read_text(encoding="utf-8").splitlines()
               if json.loads(line).get("type") == "task_summary")
    assert row["presence_provenance"]["conversation_id"] == "-100"
    assert RoomLabelResolver(projects=[]).label(row) == f"Presence telegram -100 [chat_id={chat_id}]"
    # A non-presence terminal row carries no presence facts at all.
    write_task_result(tmp_path, "plain-task", "completed", result="Done")
    plain = __import__("ouroboros.task_results", fromlist=["load_task_result"]).load_task_result(tmp_path, "plain-task")
    assert append_terminal_task_projection(tmp_path, "plain-task", {"id": "plain-task", "chat_id": 5}, plain,
                                           {"status": "completed", "chat_id": 5})
    rows = [json.loads(line) for line in (tmp_path / "logs" / "chat.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "presence_provenance" not in next(r for r in rows if r.get("task_id") == "plain-task")
