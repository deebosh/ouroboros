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


# --- a correction stands under its record, never instead of it ------------------------------------------

def _story(root, task=None):
    from ouroboros import memory_view as mv

    task = task or {"id": "turn0001", "chat_id": 1}
    return mv.render_story(mv.capture_memory_view(root, task, mv.view_spec_for_task(task, root)))


def _block(text, record_id):
    """One record's block of the story: from its ``### … part <id>`` header to the next header."""
    start = text.index(f" {record_id}\n")
    end = text.find("\n### ", start)
    return text[start:end if end >= 0 else len(text)]


def test_a_correction_stands_signed_under_the_records_words_wherever_the_record_is_shown(tmp_path):
    """The promise of the prompt and of chronicle_write: a correction is a signed revision beside the record,
    never a rewrite. The story, the room listing of memory_read and the fallback writer's input show the
    nanny's words and, under them, the mind's addition with its signature; a second correction hides neither."""
    from ouroboros import memory_fallback as mf
    from ouroboros.chronicle_store import correction_line
    from ouroboros.tools.chronicle import _memory_read
    from ouroboros.tools.registry import ToolContext

    made = shape.production(tmp_path)
    store = ChronicleStore(tmp_path)
    story = _story(tmp_path)
    block = _block(story, made.corrected)
    original, line = shape.part_text(f"legacy-b00-r{made.alpha}"), correction_line(store.get(made.addition))
    assert line.startswith(f"[correction {made.addition} by mind (root, task t1), 2026-")
    order = [block.index(f"  {original}"), block.index(f"  {line}"), block.index(f"  {shape.ADDITION}"),
             block.index("(drafted by a helper (nanny, task nan00001), accepted by me)")]
    assert order == sorted(order) and "corrected by me" not in story
    twice = _block(story, made.twice)
    first, second = correction_line(store.get(made.first_fix)), correction_line(store.get(made.second_fix))
    assert second.startswith(f"[correction {made.second_fix} by mind (consciousness, task wake0001), ")
    assert [twice.index(piece) for piece in (first, shape.FIRST_FIX, second, shape.SECOND_FIX)] == sorted(
        twice.index(piece) for piece in (first, shape.FIRST_FIX, second, shape.SECOND_FIX))
    # The room listing says the same, and its header signs the acting revision with the corrector, not the drafter.
    ctx = ToolContext(repo_dir=tmp_path, drive_root=tmp_path, task_id="root0001", current_chat_id=1)
    listed = _memory_read(ctx, room_id=made.alpha)
    header = next(line for line in listed.split("\n") if line.startswith(f"[part {made.corrected}; "))
    assert "helper (nanny, task nan00001)" in header and f"revision {made.addition} (corrected by mind (root t1))" in header
    assert listed.index(original) < listed.index(line) < listed.index(shape.ADDITION)
    # The fallback writer folds the acting text: a later part over this one starts from both.
    unit = mf.FallbackUnit(kind="part", room_id=made.alpha, key="k", member_ids=(made.corrected,))
    prompt = mf.build_writer_input(tmp_path, store, unit, budget_tokens=None).prompt
    assert original in prompt and line in prompt and shape.ADDITION in prompt
    assert f"### part {made.corrected} — by helper" in prompt  # the record's author, not the corrector's
    # A mark may quote the original's words or the correction's: both are the record's text.
    assert store.mark({"kind": "chronicle", "id": made.corrected}, "x", shape.MIND, room_id=made.alpha,
                      quote="counted twice").ok
    assert store.mark({"kind": "chronicle", "id": made.corrected}, "y", shape.MIND, room_id=made.alpha,
                      quote=original[:20]).ok
    # A record nobody corrected renders as its own words alone.
    plain = _block(story, made.parts["legacy-b00-r1"])
    assert "[correction" not in plain and f"  {shape.part_text('legacy-b00-r1')}\n" in plain


def test_a_correction_of_a_page_folded_twice_stands_once_under_the_acting_part_though_no_fold_carried_it(tmp_path):
    """Regression (the veto case): page -> part over it -> correction of the page -> part over the part. The
    writer of the outer part folded only the inner part's words, so the correction reached no fold; the story
    shows it under the acting outer part, once, and keeps showing it under every later fold. A filter that
    kept only corrections newer than the part would hide it: the correction precedes the outer part."""
    from ouroboros import memory_fallback as mf

    made = shape.production(tmp_path)
    store = ChronicleStore(tmp_path)
    assert store.get(made.page_fix)["sequence"] < store.get(made.outer)["sequence"]
    folded = mf.build_writer_input(tmp_path, store, mf.FallbackUnit(kind="part", room_id="1", key="k",
                                                                      member_ids=(made.inner,)), budget_tokens=None)
    assert shape.INNER in folded.prompt and shape.PAGE_FIX not in folded.prompt and shape.PAGE not in folded.prompt
    story = _story(tmp_path)
    fix = f"- correction by me of {made.page}:\n  {shape.PAGE_FIX}"
    assert story.count(fix) == 1 and fix in _block(story, made.outer)
    assert f" {made.inner}\n" not in story and f" {made.page}\n" not in story  # folded members show only through the part
    beyond = shape.part(tmp_path, "1", [made.outer], text="Main, folded a third time.")
    later = _story(tmp_path)
    assert later.count(fix) == 1 and fix in _block(later, beyond) and f" {made.outer}\n" not in later
    # The child's story shows the same block (an integrator and a child differ only in the retold first block).
    assert fix in _story(tmp_path, {"id": "kid00001", "chat_id": 1, "delegation_role": "subagent"})
