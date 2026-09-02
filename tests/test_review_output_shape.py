"""The review output SHAPE (array | object | report) is one form fact per surface.

Retrieving deliveries (delegated session, native tool-round episode) feed their
answer through ``canonicalize_session_verdict``; before this fact existed the
canonicalizer was array-only, so a whole-object acceptance verdict was reduced
to its findings list (schema branch) or destroyed by the array extraction ask —
and the coordinator then demoted a completed acceptance review to malformed.
"""

import json

import pytest

from ouroboros.review_execution import (
    ACCEPTANCE_SESSION_OUTPUT_SCHEMA,
    REVIEW_SESSION_OUTPUT_SCHEMA,
    review_session_output_schema,
)
from ouroboros.review_verdict_extraction import (
    _strictly_parseable,
    canonicalize_session_verdict,
)
from ouroboros.triad_review import (
    REVIEW_OUTPUT_SHAPES,
    object_verdict_payload,
    review_output_shape,
)

_OBJECT_VERDICT = {
    "verdict": "PASS",
    "outcome_tier": "solved",
    "completion_coach": "",
    "criteria_used": [
        {"criterion": "deliverable exists", "status": "supported",
         "evidence_refs": ["verification_receipts[0]"]},
    ],
    "dialogue_status": "unreachable_here",
    "findings": [],
    "summary": "clean",
}


def test_shape_table_is_form_only_and_defaults_to_array():
    assert REVIEW_OUTPUT_SHAPES == {"task_acceptance": "object", "deep_self_review": "report"}
    assert review_output_shape("task_acceptance") == "object"
    assert review_output_shape("deep_self_review") == "report"
    for surface in ("multi_model_review", "scope_review", "skill_review", "plan_review", "advisory_review", ""):
        assert review_output_shape(surface) == "array"


def test_object_verdict_payload_requires_verdict_and_list_findings():
    assert object_verdict_payload(_OBJECT_VERDICT) is _OBJECT_VERDICT
    assert object_verdict_payload({"findings": []}) is None  # no verdict key
    assert object_verdict_payload({"verdict": "PASS", "findings": "nope"}) is None
    assert object_verdict_payload([{"verdict": "PASS"}]) is None
    assert object_verdict_payload({"verdict": "FAIL"}) == {"verdict": "FAIL"}  # findings optional


def test_strict_object_keeps_the_whole_verdict():
    text = json.dumps(_OBJECT_VERDICT)
    canonical, method, usage = canonicalize_session_verdict(
        text, conformance_passed=False, shape="object")
    assert method == "strict"
    assert json.loads(canonical) == _OBJECT_VERDICT
    assert usage == {}


def test_schema_branch_keeps_the_whole_object_never_its_findings_list():
    text = json.dumps(_OBJECT_VERDICT)
    canonical, method, _ = canonicalize_session_verdict(
        text, conformance_passed=True, shape="object")
    assert method == "schema"
    assert json.loads(canonical) == _OBJECT_VERDICT
    # The array contract still reduces a structured payload to its findings.
    canonical_array, method_array, _ = canonicalize_session_verdict(
        json.dumps({"findings": [{"item": "x", "verdict": "PASS"}]}),
        conformance_passed=True, shape="array")
    assert method_array == "schema"
    assert json.loads(canonical_array) == [{"item": "x", "verdict": "PASS"}]


def test_empty_array_is_not_a_strict_object_verdict():
    """``[]`` is the constitutional clean ARRAY verdict; for an object contract
    it carries no verdict/tier/dialogue keys and must not pass strict."""
    assert _strictly_parseable("[]", "array") is True
    assert _strictly_parseable("[]\nNO_FINDINGS", "array") is True
    assert _strictly_parseable("[]", "object") is False
    assert _strictly_parseable(json.dumps(_OBJECT_VERDICT), "object") is True
    assert _strictly_parseable(json.dumps(_OBJECT_VERDICT), "array") is False


def test_refusal_quoting_the_contract_example_object_is_not_strict():
    """The historical array bug replayed for the object shape: prose around a
    quoted example must fall through to extraction, never pass as a verdict."""
    text = (
        "I reviewed NOTHING. The contract asked for an object like "
        + json.dumps(_OBJECT_VERDICT)
        + " but I could not open the workspace."
    )
    assert _strictly_parseable(text, "object") is False


def test_report_shape_passes_the_product_through_verbatim():
    report = "# Deep self-review\n\nCRITICAL: loop.py has a race.\n\n- item\n"
    canonical, method, usage = canonicalize_session_verdict(
        report, conformance_passed=True, shape="report", llm=object())
    assert (canonical, method, usage) == (report, "report", {})


class _ExtractLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"content": self.reply}, {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0}


def test_object_extraction_asks_for_the_object_and_recovers_it_from_a_fence(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MODEL_LIGHT", "openai/fake-light")
    narrative = "Here is my acceptance review.\n\n```json\n" + json.dumps(_OBJECT_VERDICT) + "\n```\n"
    llm = _ExtractLLM("```json\n" + json.dumps(_OBJECT_VERDICT) + "\n```")
    canonical, method, usage = canonicalize_session_verdict(
        narrative, conformance_passed=False, shape="object", llm=llm,
        contract="Return JSON with keys: verdict, findings, summary",
    )
    assert method == "light_model_extraction"
    assert json.loads(canonical) == _OBJECT_VERDICT
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "JSON object of the reviewer's verdict" in prompt
    assert "[] — when the reviewer COMPLETED" not in prompt  # the array ask never rides an object contract
    assert usage.get("model") == "openai/fake-light"


def test_object_extraction_that_yields_only_findings_is_unparsed(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MODEL_LIGHT", "openai/fake-light")
    llm = _ExtractLLM('[{"item": "x", "verdict": "PASS"}]')  # an array is not an object verdict
    canonical, method, _ = canonicalize_session_verdict(
        "prose only", conformance_passed=False, shape="object", llm=llm)
    assert method == "unparsed"
    assert canonical == "prose only"


def test_session_schema_follows_the_shape():
    assert review_session_output_schema("task_acceptance") is ACCEPTANCE_SESSION_OUTPUT_SCHEMA
    assert review_session_output_schema("multi_model_review") is REVIEW_SESSION_OUTPUT_SCHEMA
    assert review_session_output_schema("scope_review")["properties"]["findings"]["minItems"] == 1
    assert set(ACCEPTANCE_SESSION_OUTPUT_SCHEMA["required"]) == {"verdict", "findings", "summary"}
    for key in ("outcome_tier", "criteria_used", "dialogue_status"):
        assert key in ACCEPTANCE_SESSION_OUTPUT_SCHEMA["properties"]


@pytest.mark.parametrize("shape", ["array", "object"])
def test_extraction_bound_is_shape_independent(shape):
    canonical, method, _ = canonicalize_session_verdict(
        "x" * 400_001, conformance_passed=False, shape=shape, llm=object())
    assert method == "extraction_incomplete"
    assert len(canonical) == 400_001
