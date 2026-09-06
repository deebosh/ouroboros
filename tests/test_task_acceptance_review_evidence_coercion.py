"""Regression coverage for ibl-329e1741d5f9.

task_acceptance_review crashed with `dictionary update sequence element #0
has length 1; 2 is required` when `evidence` arrived as something other than
a dict — most plausibly a JSON-encoded string, which some providers emit for
object-typed tool arguments instead of a nested object. `dict(some_str)`
iterates the string char-by-char, and each single character is not a
length-2 (key, value) pair, hence that exact error text.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from ouroboros import review_substrate as rs


def _ctx(tmp_path):
    return SimpleNamespace(
        task_id="t",
        drive_root=tmp_path,
        task_metadata={"root_task_id": "root", "parent_task_id": "root"},
        task_contract={},
    )


def _stub_review(monkeypatch):
    from ouroboros.tools.review import _handle_task_acceptance_review

    result = rs.ReviewRunResult(
        request={"surface": "task_acceptance"},
        actors=[
            {"slot_id": "s1", "signal": "PASS", "parsed": {"outcome_tier": "solved"}},
        ],
        parsed_findings=[], aggregate_signal="PASS",
    )
    monkeypatch.setattr(rs, "reviewer_slots", lambda **k: [object()])
    monkeypatch.setattr(rs, "run_review_request", lambda *a, **k: result)
    monkeypatch.setattr(
        "ouroboros.review_evidence.build_task_acceptance_evidence",
        lambda ctx, **k: {"claim": "x"},
    )
    return _handle_task_acceptance_review


def test_evidence_as_json_string_is_parsed_not_crashed(monkeypatch, tmp_path):
    handler = _stub_review(monkeypatch)
    ctx = _ctx(tmp_path)
    # A JSON-encoded object passed as a string, the way a misbehaving
    # provider might double-encode an object-typed argument.
    out = handler(ctx, claim="done", goal="g", evidence=json.dumps({"tests": "green"}))
    assert '"outcome_tier": "solved"' in out or "solved" in out


def test_evidence_as_plain_string_falls_back_to_raw_wrapper(monkeypatch, tmp_path):
    handler = _stub_review(monkeypatch)
    ctx = _ctx(tmp_path)
    # Not JSON at all — must not raise; gets wrapped rather than dropped.
    out = handler(ctx, claim="done", goal="g", evidence="not json and not a dict")
    assert "solved" in out


def test_evidence_as_list_does_not_crash(monkeypatch, tmp_path):
    handler = _stub_review(monkeypatch)
    ctx = _ctx(tmp_path)
    # A non-dict, non-string type (e.g. a list) must also degrade, not raise.
    out = handler(ctx, claim="done", goal="g", evidence=["a", "b"])
    assert "solved" in out
