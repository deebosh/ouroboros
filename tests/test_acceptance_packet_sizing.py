"""Acceptance-packet sizing and the typed zero-physical row shape.

Two mechanisms meet here. The packet ceiling is resolved once from the review
quorum's real windows, so a per-slot backstop is what stops a narrower slot in
the same panel from being handed a prompt it cannot hold; and every refusal that
sent nothing is recorded as a typed ``not_dispatched`` actor carrying its own
cause, never as a synthetic verdict.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from ouroboros.review_dispatch import ACCEPTANCE_FIT_CHECK_MIN_CHARS
from ouroboros.review_substrate import (
    ReviewRequest,
    ReviewSlot,
    compact_review_projection,
    run_review_request,
)


class FakeLLM:
    def __init__(self):
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        body = {"verdict": "PASS", "findings": [], "summary": f"reviewed by {kwargs['model']}"}
        return {"content": json.dumps(body)}, {"prompt_tokens": 10, "completion_tokens": 5}


def _heavy_evidence(chars: int = 600_000) -> dict:
    return {"owner_requirements_and_decisions": "O" * chars, "__provenance__": {}}


def _pin_caps(monkeypatch, caps: dict) -> None:
    from ouroboros.tools import review_synthesis

    monkeypatch.setattr(
        review_synthesis, "per_slot_input_token_limits",
        lambda models, **kwargs: {str(m): int(caps.get(str(m), 0) or 0) for m in models},
    )


def _acceptance_request(evidence: dict) -> ReviewRequest:
    return ReviewRequest(
        surface="task_acceptance", goal="verify the final claim", subject="done",
        evidence=evidence, task_id="task-sizing",
    )


# ── the per-slot fit backstop ────────────────────────────────────────────────

def test_a_slot_whose_window_cannot_hold_the_prompt_is_a_typed_zero_cost_row(tmp_path, monkeypatch):
    _pin_caps(monkeypatch, {"wide-1": 1_000_000, "wide-2": 1_000_000, "narrow": 20_000})
    llm = FakeLLM()
    result = run_review_request(
        _acceptance_request(_heavy_evidence()),
        slots=[
            ReviewSlot(slot_id="slot_1", model="wide-1", effort="high"),
            ReviewSlot(slot_id="slot_2", model="wide-2", effort="high"),
            ReviewSlot(slot_id="slot_3", model="narrow", effort="high"),
        ],
        drive_root=tmp_path,
        llm=llm,
    )

    assert len(llm.calls) == 2                       # the two wide slots review
    assert {call["model"] for call in llm.calls} == {"wide-1", "wide-2"}
    rows = {actor["slot_id"]: actor for actor in result.actors}
    narrow = rows["slot_3"]
    assert narrow["status"] == "not_dispatched"
    assert narrow["error"].startswith("preflight_oversize:")
    assert "calibrated input cap 20,000" in narrow["error"]
    assert not (narrow.get("usage") or {})
    assert narrow["transport_status"] == "not_dispatched"
    assert result.aggregate_signal == "PASS"         # the quorum still reviewed


def test_every_slot_oversize_refuses_the_panel_for_zero_and_names_each_cap(tmp_path, monkeypatch):
    _pin_caps(monkeypatch, {"narrow-1": 20_000, "narrow-2": 20_000})
    llm = FakeLLM()
    paid: list = []
    result = run_review_request(
        _acceptance_request(_heavy_evidence()),
        slots=[
            ReviewSlot(slot_id="slot_1", model="narrow-1", effort="high"),
            ReviewSlot(slot_id="slot_2", model="narrow-2", effort="high"),
        ],
        drive_root=tmp_path,
        llm=llm,
        usage_ctx=SimpleNamespace(_review_paid_stamp=lambda: paid.append(True)),
    )

    assert llm.calls == []
    assert paid == []
    assert result.aggregate_signal == "DEGRADED"
    reasons = "\n".join(result.degraded_reasons)
    assert "slot_1:preflight_oversize" in reasons
    assert "slot_2:preflight_oversize" in reasons
    panel = compact_review_projection([{
        "request": {"surface": "task_acceptance"},
        "actors": [dict(actor) for actor in result.actors],
    }])["panels"][0]
    assert panel["transport_status"] == "not_dispatched"
    assert panel["coverage"]["transport_success"] == 0


def test_a_small_prompt_never_pays_for_the_calibration(tmp_path, monkeypatch):
    """The fit check is a backstop for oversize packets. Resolving Capability
    Evidence per slot inside the dispatch path costs real latency and would
    spend a slot's timeout budget before the send, so a prompt that no plausible
    reviewer window can reject skips it entirely."""
    from ouroboros.tools import review_synthesis

    calls: list = []

    def _explode(models, **kwargs):
        calls.append(list(models))
        raise AssertionError("calibration must not run for a small prompt")

    monkeypatch.setattr(review_synthesis, "per_slot_input_token_limits", _explode)
    llm = FakeLLM()
    result = run_review_request(
        _acceptance_request({"task_contract": {"requirements": "do X"}, "__provenance__": {}}),
        slots=[ReviewSlot(slot_id="slot_1", model="wide-1", effort="high")],
        drive_root=tmp_path,
        llm=llm,
    )

    assert calls == []
    assert len(llm.calls) == 1
    assert result.actors[0]["status"] == "ok"
    assert ACCEPTANCE_FIT_CHECK_MIN_CHARS > 0


# ── the typed zero-physical row ──────────────────────────────────────────────

def test_a_refused_panel_projects_not_dispatched_on_rows_and_panel(tmp_path):
    llm = FakeLLM()
    paid: list = []
    evidence = {"__unresolved_partial_artifacts__": [
        {"tool": "read_file", "status": "source_unavailable", "source_ref": {}},
    ]}
    result = run_review_request(
        _acceptance_request(evidence),
        slots=[
            ReviewSlot(slot_id="slot_1", model="wide-1", effort="high"),
            ReviewSlot(slot_id="slot_2", model="wide-2", effort="high"),
        ],
        drive_root=tmp_path,
        llm=llm,
        usage_ctx=SimpleNamespace(_review_paid_stamp=lambda: paid.append(True)),
    )

    assert llm.calls == []
    assert paid == []
    assert result.aggregate_signal == "DEGRADED"
    assert all(actor["status"] == "not_dispatched" for actor in result.actors)
    assert all(actor["transport_status"] == "not_dispatched" for actor in result.actors)
    panel = compact_review_projection([{
        "request": {"surface": "task_acceptance"},
        "actors": [dict(actor) for actor in result.actors],
    }])["panels"][0]
    assert panel["transport_status"] == "not_dispatched"
    assert panel["coverage"]["transport_success"] == 0
    # The CAUSE leads the degraded reasons and therefore the owner line.
    assert result.degraded_reasons[0].startswith("slot_1:degraded_partial_source:")
    # Disclosed residual: `_error_actor` leaves raw_text empty and the shared
    # aggregator's parse of "" also appends a bare `slot_N:degraded`. Collapsing
    # it needs a control-flow change in the aggregator shared with commit, plan
    # and skill review; pinned here so a later collapse is a deliberate flip.
    assert "slot_1:degraded" in result.degraded_reasons


def test_a_spent_owner_deadline_row_projects_not_dispatched_not_a_provider_error(tmp_path):
    llm = FakeLLM()
    result = run_review_request(
        ReviewRequest(
            surface="task_acceptance", goal="verify", subject="done",
            evidence={"task_contract": {"requirements": "do X"}, "__provenance__": {}},
            task_id="task-deadline", deadline_at="2020-01-01T00:00:00+00:00",
        ),
        slots=[ReviewSlot(slot_id="slot_1", model="wide-1", effort="high")],
        drive_root=tmp_path,
        llm=llm,
    )

    assert llm.calls == []
    row = result.actors[0]
    assert row["status"] == "not_dispatched"
    assert row["transport_status"] == "not_dispatched"
    assert "Owner deadline exhausted" in row["error"]
    assert "Owner deadline exhausted" in "\n".join(result.degraded_reasons)


def test_a_really_sent_row_still_projects_its_own_transport_state(tmp_path):
    """The new transport word must not swallow the existing distinctions."""
    rows = [
        {"status": "ok", "raw_text": "not json at all", "parse_status": "malformed"},
        {"status": "error", "error": "Timeout after 5s; physical review operation remains in flight"},
    ]
    panel = compact_review_projection([{
        "request": {"surface": "task_acceptance"}, "actors": rows,
    }])["panels"][0]
    assert panel["actors"][0]["transport_status"] == "success"
    assert panel["actors"][1]["transport_status"] == "timeout"
    assert panel["transport_status"] == "partial"
