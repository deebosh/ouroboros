"""The memory view by role, its current room and the helper's role line (``ouroboros.memory_view``).

The role is read in ``focus_signature``'s order with the one nanny fact; the current room
is ``own_room_chat`` with Main outside the Projects (a Presence turn keeps its own
conversation); a helper's ``## Working sources`` lists exactly what its spec loads. Every
rule is pinned in both directions on a fixture with three rooms besides Main.
"""
from __future__ import annotations

import dataclasses
import logging
from types import SimpleNamespace

import pytest

from ouroboros import memory_view as mv
from tests import _memory_inventory_shared as shared

NANNY_ROUTE = {"configured_subagent": {"id": "leaf", "route": {"kind": "agent_session", "harness": "claude"}}}
API_ROUTE = {"configured_subagent": {"id": "researcher", "route": {"kind": "api_model", "model": "m"}}}
PRESENCE = {"presence": {"binding_id": "b1", "event": {"provider": "telegram", "conversation_id": "c1"}}}


# --- roles ----------------------------------------------------------------------------------------

def test_role_defaults_are_the_table_of_the_spec():
    table = {  # story, room_page, room_lanes, origin_words, live_rooms, marks, knowledge, owner_words
        "integrator": (True, True, True, True, "lines", "all", True, False),
        "consciousness": (True, False, False, False, "lines", "all", True, False),
        "presence": (True, True, True, False, "none", "room_and_global", True, False),
        "child": (True, True, False, True, "none", "room_and_global", False, True),
        "nanny": (False, True, False, True, "none", "room_and_global", False, True),
    }
    assert set(mv.ROLE_DEFAULTS) == set(table) == set(mv.ROLES)
    for role, row in table.items():
        spec = mv.ROLE_DEFAULTS[role]
        assert spec.role == role and spec.room_id is None
        assert (spec.story, spec.room_page, spec.room_lanes, spec.origin_words, spec.live_rooms, spec.marks,
                spec.knowledge, spec.owner_words) == row, role


@pytest.mark.parametrize("meta,role", [
    ({}, "integrator"),
    ({"usage_category": "consciousness"}, "consciousness"),
    ({"usage_category": "consciousness_task"}, "integrator"),  # work a wake starts is a root
    (PRESENCE, "presence"),
    ({"delegation_role": "subagent"}, "child"),
    ({"delegation_role": "subagent", **API_ROUTE}, "child"),
    ({"delegation_role": "subagent", **NANNY_ROUTE}, "nanny"),
    ({**NANNY_ROUTE, "usage_category": "consciousness"}, "nanny"),  # the nanny fact comes first
    ({"delegation_role": "subagent", **PRESENCE}, "child"),
])
def test_the_view_role_follows_the_focus_signature_order(meta, role):
    from ouroboros.knowledge import focus_signature

    assert mv.view_role({"id": "t1", "metadata": meta}) == role
    if role != "presence":  # a Presence turn is named under metadata (or by the host's _presence_* flags)
        assert mv.view_role({"id": "t1", **meta}) == role  # top-level facts count the same
    focus = focus_signature(SimpleNamespace(task_metadata=meta, task_id="t1", is_direct_chat=False))["focus"]["role"]
    assert {"root": "integrator", "main": "integrator"}.get(focus, focus) == role


def test_a_nanny_sees_no_story_and_an_api_child_does(tmp_path):
    shared.projects(tmp_path)
    nanny = mv.view_spec_for_task({"id": "n1", "chat_id": 1, "delegation_role": "subagent", **NANNY_ROUTE}, tmp_path)
    child = mv.view_spec_for_task({"id": "c1", "chat_id": 1, "delegation_role": "subagent", **API_ROUTE}, tmp_path)
    assert (nanny.role, nanny.story, nanny.knowledge, nanny.owner_words) == ("nanny", False, False, True)
    assert (child.role, child.story, child.knowledge, child.owner_words) == ("child", True, False, True)


# --- the current room -----------------------------------------------------------------------------

def test_the_view_room_is_the_task_own_room_and_main_outside_the_projects(tmp_path):
    rooms = shared.projects(tmp_path)  # binds task "bound" to alpha
    alpha, beta = str(rooms["alpha"]), str(rooms["beta"])
    cases = {
        "bound": ({"id": "bound", "chat_id": 1}, alpha),  # the binding wins over chat_id 1
        "beta": ({"id": "tb", "chat_id": rooms["beta"]}, beta),
        "main": ({"id": "tm", "chat_id": 1}, "1"),
        "hidden": ({"id": "th", "chat_id": 0}, "1"),
        "transport": ({"id": "tt", "chat_id": 777}, "1"),
        "letter": ({"id": "update-letter"}, "1"),  # no chat at all
        "child": ({"id": "kid", "chat_id": 1, "delegation_role": "subagent", "parent_task_id": "bound",
                   "root_task_id": "bound"}, alpha),
    }
    for name, (task, room) in cases.items():
        assert mv.view_spec_for_task(task, tmp_path).room_id == room, name
    presence = mv.view_spec_for_task({"id": "tp", "chat_id": 555, "metadata": PRESENCE}, tmp_path)
    assert (presence.role, presence.room_id) == ("presence", "555")  # Presence keeps its own conversation
    wake = mv.view_spec_for_task({"id": "w", "chat_id": 1, "metadata": {"usage_category": "consciousness"}}, tmp_path)
    assert wake.room_id is None


def test_the_view_room_matches_own_room_chat_inside_projects(tmp_path):
    from ouroboros.dialogue_evidence import own_room_chat

    rooms = shared.projects(tmp_path)
    for task in ({"id": "bound", "chat_id": 1}, {"id": "tb", "chat_id": rooms["beta"]},
                 {"id": "kid", "chat_id": 1, "parent_task_id": "bound", "root_task_id": "bound"}):
        ctx = SimpleNamespace(task_id=task["id"], current_chat_id=task["chat_id"],
                              task_metadata={k: v for k, v in task.items() if k.endswith("task_id")})
        assert mv.view_spec_for_task(task, tmp_path).room_id == str(own_room_chat(ctx, tmp_path))


def test_an_explicit_spec_is_taken_after_its_fields_check_and_a_bad_one_falls_back(tmp_path, caplog):
    shared.projects(tmp_path)
    task = {"id": "c1", "chat_id": 1, "delegation_role": "subagent"}
    chosen = mv.view_spec_for_task({**task, "memory_view": {"story": False, "knowledge": True}}, tmp_path)
    assert (chosen.role, chosen.story, chosen.knowledge, chosen.room_id) == ("child", False, True, "1")
    with caplog.at_level(logging.WARNING, logger="ouroboros.memory_view"):
        for bad in ({"stories": False}, {"story": "no"}, {"live_rooms": "all"}, ["story"]):
            assert mv.view_spec_for_task({**task, "memory_view": bad}, tmp_path) == dataclasses.replace(
                mv.ROLE_DEFAULTS["child"], room_id="1"), bad
    assert caplog.text.count("memory_view of task c1 refused") == 4


# --- the helper's role line -----------------------------------------------------------------------

def test_the_working_sources_line_lists_exactly_what_the_spec_loads():
    child = dataclasses.replace(mv.ROLE_DEFAULTS["child"], room_id="1")
    line = mv.working_sources_line(child)
    assert line.startswith("## Working sources\n\nLoaded above: ") and line.endswith(mv.CHILD_ROLE_TEXT)
    loaded, missing = line.split("Loaded above: ", 1)[1].split(" Not loaded: ", 1)
    assert "the top level of your story" in loaded and "the page of your parent's room" in loaded
    assert "the words of my human that caused this work" in loaded and "raw conversations" in missing
    assert "knowledge (overview, index, patterns)" in missing and "knowledge" not in loaded
    assert "shared biography" not in line
    # Each field moves its item: the line changes with the spec, never independently of it.
    for field, value, gone, now_missing in (
        ("story", False, "the top level of your story", "the top level of your story"),
        ("owner_words", False, "the words of my human that caused this work", None),
        ("knowledge", True, None, None),
    ):
        changed = mv.working_sources_line(dataclasses.replace(child, **{field: value}))
        assert changed != line, field
        new_loaded, new_missing = changed.split("Loaded above: ", 1)[1].split(" Not loaded: ", 1)
        if gone:
            assert gone not in new_loaded, field
        if now_missing:
            assert now_missing in new_missing, field
        if field == "knowledge":
            assert "knowledge (overview, index, patterns)" in new_loaded
            assert "knowledge (overview, index, patterns)" not in new_missing
    nanny = mv.working_sources_line(dataclasses.replace(mv.ROLE_DEFAULTS["nanny"], room_id="1"))
    assert "the top level of your story" in nanny.split(" Not loaded: ", 1)[1]
