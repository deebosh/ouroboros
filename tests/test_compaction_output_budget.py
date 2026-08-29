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
