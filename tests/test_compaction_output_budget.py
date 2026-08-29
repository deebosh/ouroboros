"""Per-call wire ``max_tokens`` sizing for the context summarizer.

The summarizer route is a reasoning model; a blanket ``max_tokens =
_SUMMARY_OUTPUT_TOKENS`` let it over-generate (measured ~17.5k completion tokens
for one chunk, ~280s wall-clock). ``_call_summarizer`` now sizes the wire budget
to the summaries the call actually requests — Σ per-part ``summary_budget_tokens``
× 2 + headroom — clamped to ``[_SUMMARY_WIRE_MIN_TOKENS, _SUMMARY_OUTPUT_TOKENS]``,
and drops the map phase to provider-floor reasoning effort.
"""

from __future__ import annotations

import json

import pytest

from ouroboros import context_compaction as cc
from ouroboros.context_budget import ContextReclaimRequest


_SPEC = {"model": "m", "effort": "low", "use_local": False, "output_budget": cc._SUMMARY_OUTPUT_TOKENS}


def _big_unit(call_id: str, fill_unit: str) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": f"reasoning-{call_id}",
            "tool_calls": [{
                "id": call_id,
                "function": {"name": "read_file", "arguments": fill_unit * 3_000 + call_id},
            }],
        },
        {"role": "tool", "tool_call_id": call_id, "content": fill_unit * 3_000 + call_id},
    ]


def _request(messages, goal):
    return ContextReclaimRequest(
        route_fp="main-route",
        round_id="round-1",
        transcript_sha256=cc.context_reclaim_transcript_sha256(messages),
        measurement_basis="cold_estimate",
        measurement_density=1.0,
        reclaim_goal_tokens=goal,
        allow_partial_shrink=True,
    )


def _capture_common(monkeypatch):
    from ouroboros import llm, llm_observability

    seen: dict = {}

    def fake_chat_observed(_client, **kwargs):
        seen.update(kwargs)
        source_ids = [
            p["source_id"]
            for p in json.loads(kwargs["messages"][0]["content"].split("\n", 3)[-1])
        ]
        arguments = json.dumps(
            {"summaries": [{"source_id": sid, "summary": "ok"} for sid in source_ids]}
        )
        return (
            {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "emit_context_summaries", "arguments": arguments}}
                ],
            },
            {"prompt_tokens": 5, "completion_tokens": 2, "provider": "test"},
        )

    monkeypatch.setattr(llm, "LLMClient", lambda: object())
    monkeypatch.setattr(llm_observability, "chat_observed", fake_chat_observed)
    return seen


def _run(monkeypatch, *, budgets, phase, spec_effort="low", tmp_path=None):
    seen = _capture_common(monkeypatch)
    parts = [cc._part(f"unit:{i}:0:{i:06d}", f"body {i} " * 50) for i in range(len(budgets))]
    summary_budgets = {p.root_id: b for p, b in zip(parts, budgets)}
    cc._call_summarizer(
        parts,
        drive_root=tmp_path,
        task_id="task",
        phase=phase,
        spec={"model": "m", "effort": spec_effort, "use_local": False},
        summary_budgets=summary_budgets,
        usage_total={},
    )
    return seen


def test_wire_max_tokens_tracks_single_budget(monkeypatch, tmp_path):
    seen = _run(monkeypatch, budgets=[2_000], phase="map", tmp_path=tmp_path)
    assert seen["max_tokens"] == 2_000 * 2 + cc._SUMMARY_WIRE_HEADROOM_TOKENS


def test_wire_max_tokens_sums_a_batch(monkeypatch, tmp_path):
    seen = _run(monkeypatch, budgets=[1_500, 1_500, 1_000], phase="map", tmp_path=tmp_path)
    assert seen["max_tokens"] == 4_000 * 2 + cc._SUMMARY_WIRE_HEADROOM_TOKENS


def test_wire_max_tokens_stays_sane_for_a_tiny_budget(monkeypatch, tmp_path):
    seen = _run(monkeypatch, budgets=[64], phase="map", tmp_path=tmp_path)
    # Headroom alone keeps a tiny-budget call well clear of truncation.
    assert seen["max_tokens"] >= cc._SUMMARY_WIRE_MIN_TOKENS
    assert seen["max_tokens"] == 64 * 2 + cc._SUMMARY_WIRE_HEADROOM_TOKENS


def test_wire_max_tokens_never_exceeds_the_output_ceiling(monkeypatch, tmp_path):
    seen = _run(monkeypatch, budgets=[40_000, 40_000], phase="map", tmp_path=tmp_path)
    assert seen["max_tokens"] == cc._SUMMARY_OUTPUT_TOKENS


def test_map_phase_uses_minimal_effort(monkeypatch, tmp_path):
    seen = _run(monkeypatch, budgets=[2_000], phase="map", spec_effort="low", tmp_path=tmp_path)
    assert seen["reasoning_effort"] == "minimal"


def test_fold_phase_keeps_configured_effort(monkeypatch, tmp_path):
    seen = _run(monkeypatch, budgets=[2_000], phase="fold", spec_effort="low", tmp_path=tmp_path)
    assert seen["reasoning_effort"] == "low"


def test_select_units_caps_summary_budget_at_the_per_unit_ceiling():
    # A large unit: the old formula allowed summary_budget up to source_tokens//2
    # (tens of thousands). It must now be clamped to the per-unit ceiling.
    messages = _big_unit("big", "abcdefghij " * 40)
    request = _request(messages, goal=200_000)  # goal far above any single unit
    selection, status = cc._select_units(
        messages,
        request,
        keep_recent=0,
        trace_refs_by_tool_call_id={},
        negative_memo=set(),
        spec=_SPEC,
    )
    assert status == "applied"
    assert selection.units
    for item in selection.units:
        assert item.summary_budget_tokens <= cc._PER_UNIT_SUMMARY_CEILING_TOKENS
