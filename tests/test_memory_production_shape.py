"""The production-shaped memory installation (``tests._memory_production_shape``) and what memory shows of it.

The first test pins the forms themselves, as facts of the journal and the chat chain that no view
changes: a fixture that drifts away from them would let the tests built on it pass on a shape no
installation has.
"""
from __future__ import annotations

from ouroboros import memory_inventory as mi
from ouroboros.chronicle_store import ChronicleStore
from ouroboros.dialogue_provenance import render_row_text, row_author
from tests import _memory_production_shape as shape


def _units(root):
    return {unit.record_id: unit for unit in mi.legacy_units(ChronicleStore(root), root)}


def test_the_installation_holds_the_forms_found_on_a_live_one(tmp_path):
    made = shape.production(tmp_path)
    store, units = ChronicleStore(tmp_path), _units(tmp_path)
    alpha, beta = made.alpha, made.beta
    # Several rooms per block; Alpha and Beta take only their block's tail, Main has no row in block one.
    assert {unit.room_id for unit in units.values() if unit.block == 0} == {"1", alpha, "777"}
    assert {unit.room_id for unit in units.values() if unit.block == 1} == {"1", alpha, beta}
    for tail, block_start, own_start in ((f"legacy-b00-r{alpha}", "2026-09-01T00:00:00+00:00", "2026-09-01T00:02:00+00:00"),
                                         (f"legacy-b01-r{beta}", "2026-09-02T00:00:00+00:00", "2026-09-02T00:02:00+00:00")):
        recorded = store.get(made.parts[tail])["covers"]
        assert recorded["ts_span"]["start"] == block_start  # the journal keeps the block's period for the part
        assert units[tail].ts_span["start"] == own_start and units[tail].folded
    assert units["legacy-b01-r1"].rows == 0 and units["legacy-b01-r1"].ts_span is None
    # The parts of one block record one position, and their sequences run against the stream.
    for block, order in ((0, ["777", alpha, "1"]), (1, [beta, alpha, "1"])):
        parts = [store.get(made.parts[unit.record_id]) for unit in units.values() if unit.block == block]
        assert len({tuple(part["covers"]["stream_span"]) for part in parts}) == 1
        assert [part["room_id"] for part in sorted(parts, key=lambda part: part["sequence"])] == order
    assert all(store.get(part)["author"] == shape.NANNY for part in made.parts.values())
    assert {record["status"] for room in ("1", alpha, beta, "777") for record in store.room_records(room)
            if record["id"] in made.parts.values()} == {"accepted"}
    assert mi.legacy_progress(list(units.values())) == {"periods": 2, "folded": 2, "pending": []}
    # A correction that adds: none of the part's words are in it; Beta's part has two, the second a wake's.
    assert store.get(made.addition)["target_id"] == made.corrected
    assert not set(shape.part_text(f"legacy-b00-r{alpha}").split()) >= set(shape.ADDITION.split())
    fixes = [record for record in store.records(beta, kinds=("correction",)) if record["target_id"] == made.twice]
    assert [(fix["id"], fix["author"]["focus"]["role"]) for fix in fixes] == [(made.first_fix, "root"),
                                                                              (made.second_fix, "consciousness")]
    # The nested fold: the outer part reaches the page through the inner one, and the correction of the page
    # was written between the two parts.
    assert store.get(made.outer)["covers"]["member_ids"] == [made.inner]
    assert store.folded_members(made.outer) == [made.inner, made.page]
    sequence = {name: store.get(getattr(made, name))["sequence"] for name in ("page", "inner", "page_fix", "outer")}
    assert sequence["page"] < sequence["inner"] < sequence["page_fix"] < sequence["outer"]
    assert store.get(made.page_fix)["target_id"] == made.page
    # A page with a gap: rows 12 and 16 of Main are sealed, row 15 between them is open.
    open_main = [pos for _address, _meta, pos in mi.open_room_rows(tmp_path, "1")]
    assert store.get(made.gap_page)["covers"]["stream_span"] == [12, 16] and open_main == [15, 18, 19]
    assert store.get(made.gap_page)["host_stamp"]["tasks"][0]["task_id"] == "t2"
    # A note no page has sealed, in a Project room.
    assert f"note:{made.note}" not in store.sealed_row_refs(alpha) and store.get(made.note)["room_id"] == alpha
    # The owner's answer to a card, in the Project room: the frame repeats the question before the choice.
    answer = [meta for _address, meta, _pos in mi.open_room_rows(tmp_path, alpha) if meta.get("type") == "quiz_answer"]
    assert len(answer) == 1 and row_author(answer[0])["kind"] == "human"
    chosen = "The owner chose option 2: " + shape.OPTIONS[1]
    assert len(shape.QUESTION) > 500 and made.answer["text"].index(chosen) > 500
    assert render_row_text(made.answer) == f'[answer q-inventory] chose (2) {shape.OPTIONS[1]} — "{shape.COMMENT}"'
