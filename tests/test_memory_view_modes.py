"""The floor by mode and the physical starting mode (``memory_floor.render_view_for_mode``, ``physical_mode``).

On synthetic actors of the sizes of P3 §0.2 (Main, a Project room, consciousness, a child
whose parent is in a Project or in Main, a nanny) every cell of eight windows by three
preferred modes starts in a mode whose request plus Nano's headroom fits the window. The
starting mode drops only when the shortest view of my memory cannot fit, judged on the
route's calibrated estimate; an unknown window keeps the preferred mode and takes no
step. An owner-selected Low or Nano target is a second boundary for its own steps
only; task-local Low and a mode the window lowered have none (the owner's answer B,
D-32, D-37). Each rule is pinned in both directions.
"""
from __future__ import annotations

import pytest

from ouroboros import chat_chain
from ouroboros import context_budget as cb
from ouroboros import memory_floor as mf
from tests import _memory_view_synthetic as syn

WINDOWS = (1_050_000, 872_000, 640_000, 500_000, 400_000, 272_000, 200_000, 128_000)
RESERVE = {"max": 65_536, "low": 65_536, "nano": cb.NANO_MIN_HEADROOM_TOKENS}


def _start(name, window, preferred, *, ratio=1.0, known=True, snapshot=None):
    """The planned start of one task: its mode, estimated input, story, room and floor fact."""
    snapshot = snapshot or syn.actor(name)
    fixed = syn.FIXED[name]
    minimal = mf.minimal_view_tokens(snapshot, window_tokens=window)
    mode = mf.physical_mode(preferred, fixed, minimal, window_tokens=window, known_window=known,
                            reserve_by_mode=RESERVE, calibration_ratio=ratio)
    story, room, facts = mf.render_view_for_mode(
        snapshot, mode=mode, owner_mode=preferred, window_tokens=window, known_window=known, output_reserve=65_536,
        ratio=ratio, non_memory_tokens=fixed[mode], lowered_from=preferred if mode != preferred else None)
    return mode, fixed[mode] + mf.view_tokens(story) + mf.view_tokens(room), story, room, facts


def test_every_actor_window_and_mode_starts_in_a_request_that_fits():
    misses, modes = [], {}
    for name in syn.FIXED:
        for window in WINDOWS:
            for preferred in mf.MODES:
                mode, total, _story, _room, facts = _start(name, window, preferred)
                modes[(name, window, preferred)] = mode
                if total + cb.NANO_MIN_HEADROOM_TOKENS > window:
                    misses.append((name, window, preferred, mode, total))
                assert facts["floor"]["mode"] == mode and facts["floor"]["window_tokens"] == window
    assert misses == []
    assert len(modes) == 6 * 8 * 3
    # The window lowers Max only where it physically must (P3 §0.3): Main keeps Max from 500k up, drops to Low at
    # 400k-200k, to Nano at 128k; a child keeps Max everywhere.
    assert [modes[("main", w, "max")] for w in WINDOWS] == ["max"] * 4 + ["low"] * 3 + ["nano"]
    assert {modes[("child_project", w, "max")] for w in WINDOWS} == {"max"}
    assert all(modes[(name, w, "nano")] == "nano" for name in syn.FIXED for w in WINDOWS)


@pytest.mark.parametrize("window", [640_000, 500_000])
def test_in_the_500k_to_640k_band_max_holds_and_peoples_words_stay_verbatim(window):
    snapshot = syn.actor("main")
    mode, total, _story, room, facts = _start("main", window, "max", snapshot=snapshot)
    assert mode == "max" and total + 65_536 <= window
    steps = facts["floor"]["steps"]
    assert "F6" not in steps and "F7" not in steps
    for item in snapshot.room["lane1"]:
        if item["kind"] == "human":
            assert item["line"] in room
    if window == 500_000:  # the working margins still take host facts, pointers, the retold page and my replies
        assert list(steps) == ["F1", "F1b", "F3", "F4", "F2"]
        assert "### Physical floor\nThis window (500000 tokens, Max) does not hold" in room


def test_where_even_f1_to_f5_leave_too_much_peoples_words_go_last_other_rooms_first():
    snapshot = syn.snapshot(room_facts=syn.room("1", legacy=23, legacy_chars=8_400, lane2=19,
                                                lane1=[syn.spoken(0, "human", 9_000), syn.spoken(1, "ouroboros", 5_600),
                                                       syn.spoken(2, "human", 8_000)]),
                            story=syn.pointers(), live=[syn.live_room(i, words=2, word_chars=2_000) for i in range(12)],
                            mark_count=12, live_rooms="lines_with_words")
    fixed = syn.FIXED["main"]["max"]

    def steps(window):
        return mf.render_view_for_mode(snapshot, mode="max", owner_mode="max", window_tokens=window, known_window=True,
                                       output_reserve=65_536, ratio=1.0, non_memory_tokens=fixed)[2]["floor"]["steps"]

    roomy = steps(fixed + 65_536 + 40_000)  # after F1-F5 and F2 the words fit the window minus the reserve
    assert "F6" not in roomy and "F7" not in roomy and "F2" in roomy
    tight = steps(fixed + 65_536 + 9_000)  # after F1-F5 and F2 the words still do not fit
    assert list(tight) == ["F1", "F3", "F4", "F2", "F6", "F7"]  # every live room shows words: no F1b
    some = steps(fixed + 65_536 + 13_000)  # other rooms' words make room before this room's
    assert "F6" in some and "F7" not in some


def test_the_window_lowers_the_mode_on_the_calibrated_estimate_not_the_raw_one():
    snapshot = syn.actor("main")
    fixed = syn.FIXED["main"]
    minimal = mf.minimal_view_tokens(snapshot, window_tokens=400_000)
    border = fixed["max"] + minimal + 65_536 - 1_000  # raw: 1 000 tokens short of fitting Max
    pick = lambda ratio: mf.physical_mode("max", fixed, minimal, window_tokens=border, known_window=True,  # noqa: E731
                                          reserve_by_mode=RESERVE, calibration_ratio=ratio)
    assert pick(1.0) == "low"
    assert pick(0.98) == "max"  # the route counts 2 % fewer tokens than the host's estimate: Max fits
    assert pick(1.05) == "low"
    roomy = fixed["max"] + minimal + 65_536 + 1_000
    assert mf.physical_mode("max", fixed, minimal, window_tokens=roomy, known_window=True, reserve_by_mode=RESERVE,
                            calibration_ratio=1.0) == "max"
    assert mf.physical_mode("max", fixed, minimal, window_tokens=roomy, known_window=True, reserve_by_mode=RESERVE,
                            calibration_ratio=1.02) == "low"


def test_an_unknown_window_keeps_the_preferred_mode_and_takes_no_step():
    for preferred in ("max", "low"):
        mode, _total, _story, room, facts = _start("main", 128_000, preferred, known=False)
        assert mode == preferred
        assert facts["floor"]["window_tokens"] is None and facts["floor"]["allowance_tokens"] is None
        if preferred == "max":
            assert facts["floor"]["steps"] == {} and "### Physical floor" not in room
    known_mode, *_rest = _start("main", 128_000, "max")
    assert known_mode == "nano"  # the same window, known, does lower the mode


def test_a_lowered_mode_is_a_visible_fact_of_the_room_and_of_the_trace():
    mode, _total, _story, room, facts = _start("main", 400_000, "max")
    assert mode == "low" and facts["floor"]["mode_switch"] == {"from": "max", "to": "low"}
    assert ("This window (400000 tokens) cannot hold Max with even the shortest view of my memory; this task "
            "started in Low.") in room
    assert facts["floor"]["target_tokens"] is None  # the window chose Low; the owner did not, so no Low budget
    kept, _total, _story, room, facts = _start("main", 872_000, "max")
    assert kept == "max" and facts["floor"]["mode_switch"] is None and "cannot hold" not in room


def test_an_owner_low_target_takes_only_its_steps_and_task_local_low_has_none():
    snapshot = syn.actor("main")
    fixed = syn.FIXED["main"]

    def view(mode, owner_mode):
        return mf.render_view_for_mode(snapshot, mode=mode, owner_mode=owner_mode, window_tokens=1_050_000,
                                       known_window=True, output_reserve=65_536, ratio=1.0, non_memory_tokens=fixed[mode])

    budget = cb.OWNER_LOW_TARGET_TOKENS - 65_536 - fixed["low"]
    assert mf.view_tokens(mf.mv.render_story(snapshot)) + mf.view_tokens(mf.mv.render_room(snapshot)) > budget
    story, room, facts = view("low", "low")
    steps = facts["floor"]["steps"]
    assert steps and set(steps) <= set(mf.MODE_TARGET_STEPS) and facts["floor"]["by_budget"] == sum(steps.values())
    assert facts["floor"]["target_tokens"] == cb.OWNER_LOW_TARGET_TOKENS
    assert facts["floor"]["budget_allowance_tokens"] == budget - 31_250  # one landing level under the target (D-38)
    for item in snapshot.room["lane1"]:  # people's words and my replies: the window alone may take them
        assert item["line"] in room
    assert "The Low mode budget (250000 tokens) does not hold all of my memory verbatim" in room
    assert mf.view_tokens(story) + mf.view_tokens(room) <= budget - 31_250 + 200  # the note is not budgeted
    # The same view in Max, and in task-local Low under the owner's Max: no target, no step.
    for mode, owner in (("max", "max"), ("low", "max")):
        _story, room, facts = view(mode, owner)
        assert facts["floor"]["steps"] == {} and facts["floor"]["target_tokens"] is None, (mode, owner)
        assert "### Physical floor" not in room


def test_an_owner_nano_target_bounds_with_nanos_own_reserve_and_names_enable_tools():
    snapshot = syn.actor("project")
    _story, room, facts = mf.render_view_for_mode(
        snapshot, mode="nano", owner_mode="nano", window_tokens=1_050_000, known_window=True, output_reserve=65_536,
        ratio=1.0, non_memory_tokens=syn.FIXED["project"]["nano"])
    assert facts["floor"]["target_tokens"] == cb.OWNER_NANO_TARGET_TOKENS
    assert set(facts["floor"]["steps"]) <= set(mf.MODE_TARGET_STEPS)
    assert room.endswith("(memory_read is reachable through enable_tools)")
    lowered = mf.render_view_for_mode(  # Nano the window chose for the owner's Max: Nano's reserve, no Nano budget
        snapshot, mode="nano", owner_mode="max", window_tokens=1_050_000, known_window=True, output_reserve=65_536,
        ratio=1.0, non_memory_tokens=syn.FIXED["project"]["nano"])[2]
    assert lowered["floor"]["target_tokens"] is None and lowered["floor"]["steps"] == {}
    assert lowered["floor"]["physical_allowance_tokens"] == 1_050_000 - cb.NANO_MIN_HEADROOM_TOKENS - syn.FIXED[
        "project"]["nano"]


def test_the_floor_fact_names_the_newest_row_and_the_records_shown_by_address():
    snapshot = syn.actor("main")
    _mode, _total, _story, _room, facts = _start("main", 200_000, "max", snapshot=snapshot)
    floor = facts["floor"]
    assert {"F1", "F3", "F4", "F2"} <= set(floor["steps"]) and "F7" not in floor["steps"]
    lane1, lane2 = snapshot.room["lane1"], snapshot.room["lane2"]
    newest = max([(item["last_pos"], item["last"]) for item in lane2]
                 + [(item["pos"], item["address"]) for item in lane1 if item["kind"] == "ouroboros"])
    assert floor["newest_addressed_row"] == chat_chain.parse_address(newest[1])
    assert floor["pointer_records"] == [item["id"] for item in snapshot.room["legacy"]][:floor["steps"]["F4"]]
    assert facts["room_id"] == "1" and facts["role"] == "integrator"
    assert facts["story_status"] == {"folded": 0, "total": 23}
    whole = mf.render_view_for_mode(snapshot, mode="max", owner_mode="max", window_tokens=1_050_000, known_window=True,
                                    output_reserve=65_536, ratio=1.0, non_memory_tokens=syn.FIXED["main"]["max"])[2]
    assert whole["floor"]["newest_addressed_row"] is None and whole["floor"]["pointer_records"] == []
