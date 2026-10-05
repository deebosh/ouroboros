"""Every reviewer send is billed to a review round (#1544).

A caller may name its wave (skill review). Otherwise a review's paid-cycle
identity (``retry_key``: one per commit, scope or acceptance cycle) is its
wave, and a review without one gets a fresh wave. Either way every slot of
the round carries the same wave, on its usage row and on the returned run.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ouroboros.review_substrate import ReviewRequest, ReviewSlot, run_review_request
from tests._review_substrate_shared import FakeLLM

_SLOTS = [ReviewSlot(slot_id="slot_a", model="same/model"), ReviewSlot(slot_id="slot_b", model="other/model")]


def _review(tmp_path, request):
    ctx = SimpleNamespace(task_id=request.task_id, event_queue=None, pending_events=[])
    result = run_review_request(request, slots=_SLOTS, drive_root=tmp_path, llm=FakeLLM(), usage_ctx=ctx)
    rows = [event for event in ctx.pending_events if event.get("type") == "llm_usage"]
    assert sorted(row["review_slot_id"] for row in rows) == ["slot_a", "slot_b"]
    return rows, result


def test_every_slot_of_a_paid_cycle_is_billed_to_that_cycle(tmp_path):
    rows, result = _review(tmp_path, ReviewRequest(
        surface="task_acceptance", goal="review", task_id="t-cycle", retry_key="task_acceptance:rev-1"))
    assert {row["review_wave_id"] for row in rows} == {"task_acceptance:rev-1"}
    assert result.request["usage_attribution"]["review_wave_id"] == "task_acceptance:rev-1"


def test_a_review_without_a_cycle_key_gets_one_fresh_wave_for_all_its_slots(tmp_path):
    first, result = _review(tmp_path, ReviewRequest(surface="deep_self_review", goal="review", task_id="t-a"))
    second, _ = _review(tmp_path, ReviewRequest(surface="deep_self_review", goal="review", task_id="t-b"))
    [wave] = {row["review_wave_id"] for row in first}
    assert wave.startswith("wave-") and result.request["usage_attribution"]["review_wave_id"] == wave
    assert {row["review_wave_id"] for row in second} != {wave}  # the next review is its own round


def test_the_callers_wave_wins_over_the_cycle_key(tmp_path):
    rows, _ = _review(tmp_path, ReviewRequest(
        surface="skill_review", goal="review", task_id="t-skill", retry_key="skill:digest",
        usage_attribution={"review_skill": "weather", "review_wave_id": "skill-wave-7"}))
    assert {(row["review_skill"], row["review_wave_id"]) for row in rows} == {("weather", "skill-wave-7")}


def test_a_multi_model_fan_out_shares_one_wave_across_its_rows(monkeypatch):
    """Each fan-out row sends its own request, so a round without a cycle key
    names its wave once for all of them; a cycle key stays the wave."""
    import ouroboros.review_substrate as substrate
    from ouroboros.tools import review
    from ouroboros.tools.review_multi_model import _multi_model_review_async

    seen = []

    def fake_run_review_request(request, slots, **_kwargs):
        seen.append(dict(request.usage_attribution))
        return SimpleNamespace(actors=[{"status": "ok", "raw_text": "[]", "usage": {}, "slot_id": slots[0].slot_id,
                                        "prompt_ref": {}, "response_ref": {}}])

    monkeypatch.setattr(substrate, "run_review_request", fake_run_review_request)
    monkeypatch.setattr(review, "LLMClient", lambda: object())
    models = ["model/a", "model/b", "model/c"]
    asyncio.run(_multi_model_review_async("diff", "review this", models, None))
    assert len(seen) == 3 and len({row["review_wave_id"] for row in seen}) == 1
    assert seen[0]["review_wave_id"].startswith("wave-")
    seen.clear()
    asyncio.run(_multi_model_review_async("diff", "review this", models, None, retry_key="commit:cycle-2"))
    assert len(seen) == 3 and not any("review_wave_id" in row for row in seen)  # the substrate uses the key
