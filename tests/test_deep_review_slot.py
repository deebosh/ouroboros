"""The optional ``deep_review`` reviewer row (Ф3, owner decisions R6/R7).

Deep self-review joins the shared reviewer-row vocabulary as ONE optional
singleton row: absent, the packed api row is synthesized from the legacy model
key ``OUROBOROS_MODEL_DEEP_SELF_REVIEW`` (the invisible migration source), so
every existing install keeps today's exact delivery; present, the row picks the
delivery through the same ``retrieves`` predicate every other surface uses, and
its own effort outranks the surface key.
"""

import asyncio
import json

import pytest

from ouroboros.reviewer_slot_config import (
    DEEP_REVIEW_SLOT_ID,
    REVIEWER_SLOTS_ENV,
    deep_review_slot,
    load_reviewer_slot_config,
    parse_reviewer_slots,
    reviewer_slot_save_check,
    row_effort,
)

_ROSTER = {
    "enabled": True,
    "items": [
        {"subagent_id": "api-critic", "name": "API critic", "recommended_use": "x",
         "route": {"kind": "api_model", "target_id": "openai/gpt-5.6-terra"}, "effort": "medium"},
        {"subagent_id": "session-critic", "name": "Session critic", "recommended_use": "y",
         "route": {"kind": "agent_session", "target_id": "codex=gpt-5.6-sol",
                   "credential_profile_id": "profile-1"}, "effort": "high"},
    ],
}


def _payload(deep_review=None, **extra):
    body = {
        "triad": [{"slot_id": "t1", "route": {"kind": "api_chat", "target_id": "openai/gpt-5.6-luna"}}],
        "scope": [{"slot_id": "s1", "route": {"kind": "api_chat", "target_id": "openai/gpt-5.6-terra"}}],
        **extra,
    }
    if deep_review is not None:
        body["deep_review"] = deep_review
    return json.dumps(body)


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("OUROBOROS_SUBAGENTS", json.dumps(_ROSTER))
    for key in ("OUROBOROS_REVIEW_MODELS", "OUROBOROS_SCOPE_REVIEW_MODELS", "OUROBOROS_SCOPE_REVIEW_MODEL",
                "OUROBOROS_ADVISORY_REVIEW_ROUTE", REVIEWER_SLOTS_ENV):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OUROBOROS_MODEL_DEEP_SELF_REVIEW", "openai/legacy-deep-model")
    monkeypatch.setenv("OUROBOROS_EFFORT_DEEP_SELF_REVIEW", "low")
    return monkeypatch


def _get_endpoint():
    from starlette.requests import Request

    from ouroboros.gateway.settings import api_reviewer_slots

    request = Request({"type": "http", "method": "GET", "path": "/api/reviewer-slots",
                       "headers": [], "query_string": b""})
    return json.loads(asyncio.run(api_reviewer_slots(request)).body)


def test_deep_review_row_parses_on_the_shared_vocabulary(env):
    """Direct api, direct session (with the manual pin) and a configured-subagent
    reference all parse through the ONE row parser; the identity is fixed."""
    api = parse_reviewer_slots(_payload(
        {"route": {"kind": "api_chat", "target_id": "openai/gpt-5.6-sol-pro"}, "effort": "xhigh"})).deep_review
    assert api.slot_id == DEEP_REVIEW_SLOT_ID and api.kind == "api_chat"
    assert api.target_id == "openai/gpt-5.6-sol-pro" and api.effort == "xhigh"
    assert api.retrieves is False and api.native_retrieval is False

    session = parse_reviewer_slots(_payload(
        {"route": {"kind": "agent_session", "target_id": "codex=gpt-5.6-sol", "profile_id": "koshak"}})).deep_review
    assert session.is_session and session.session_target == "codex=gpt-5.6-sol"
    assert session.profile_id == "koshak" and session.retrieves is True

    actor = parse_reviewer_slots(_payload({"subagent_id": "api-critic"})).deep_review
    assert actor.subagent_id == "api-critic" and actor.kind == "api_chat"
    assert actor.target_id == "openai/gpt-5.6-terra" and actor.effort == "medium"
    assert actor.native_retrieval is True and actor.retrieves is True

    # Absent is absent — never an empty placeholder row.
    assert parse_reviewer_slots(_payload()).deep_review is None


@pytest.mark.parametrize("row, fragment", [
    ({"route": {"kind": "api_chat", "target_id": "m"}, "slot_id": "mine"}, "unknown keys"),
    ({"route": {"kind": "api_chat", "target_id": "m"}, "enabled": True}, "unknown keys"),
    ({"route": {"kind": "api_chat", "target_id": "m"}, "bogus": 1}, "unknown keys"),
    ({"route": {"kind": "api_chat", "target_id": "m"}, "subagent_id": "api-critic"}, "either route or"),
    ({"subagent_id": "nobody"}, "does not resolve"),
    ({"route": {"kind": "agent_session", "target_id": "off"}}, "concrete harness route"),
    ({"route": {"kind": "api_chat", "target_id": "m"}, "effort": "turbo"}, "unknown effort"),
    ("openai/x", "must be an object"),
])
def test_deep_review_row_refuses_typed_like_every_other_row(env, row, fragment):
    with pytest.raises(ValueError, match=fragment):
        parse_reviewer_slots(_payload(row))


def test_deep_review_identity_is_fixed_and_cannot_be_reused_by_another_row(env):
    """The singleton's id lives in the SAME identity space as the other rows:
    a triad row squatting on it collides, so receipts keep ONE history."""
    body = json.loads(_payload({"route": {"kind": "api_chat", "target_id": "m"}}))
    body["triad"][0]["slot_id"] = DEEP_REVIEW_SLOT_ID
    with pytest.raises(ValueError, match="appears twice"):
        parse_reviewer_slots(json.dumps(body))


def test_deep_review_slot_synthesizes_the_packed_row_from_the_model_key(env):
    """No row saved (structured without the key, or legacy comma keys): the
    delivery is today's exact one — a packed api row on the legacy model key,
    NEVER a retrieving row an install did not ask for."""
    for setup in ("structured", "legacy"):
        if setup == "structured":
            env.setenv(REVIEWER_SLOTS_ENV, _payload())
        else:
            env.delenv(REVIEWER_SLOTS_ENV, raising=False)
        row = deep_review_slot()
        assert row.slot_id == DEEP_REVIEW_SLOT_ID and row.kind == "api_chat"
        assert row.target_id == "openai/legacy-deep-model"
        assert row.retrieves is False and row.subagent_id == "" and row.effort == ""
    # A saved row wins over the key.
    env.setenv(REVIEWER_SLOTS_ENV, _payload({"route": {"kind": "api_chat", "target_id": "openai/saved"}}))
    assert deep_review_slot().target_id == "openai/saved"
    # A caller that already parsed the setting hands its config over (no second parse).
    config = load_reviewer_slot_config()
    assert deep_review_slot(config) is config.deep_review


def test_deep_review_row_effort_outranks_the_surface_key_only_when_set(env):
    """R6: the row's effort is the authority when it names one; the synthesized
    row (and a saved row with no effort) keeps the surface key."""
    env.setenv(REVIEWER_SLOTS_ENV, _payload({"route": {"kind": "api_chat", "target_id": "m"}, "effort": "xhigh"}))
    assert row_effort(deep_review_slot(), "deep_self_review") == "xhigh"
    env.setenv(REVIEWER_SLOTS_ENV, _payload({"route": {"kind": "api_chat", "target_id": "m"}}))
    assert row_effort(deep_review_slot(), "deep_self_review") == "low"
    env.setenv(REVIEWER_SLOTS_ENV, _payload())
    assert row_effort(deep_review_slot(), "deep_self_review") == "low"
    # A compound Cursor slug on a session row carries its own effort (shared rule).
    env.setenv(REVIEWER_SLOTS_ENV, _payload({"route": {"kind": "agent_session", "target_id": "cursor=cursor-grok-4.6-xhigh"}}))
    assert row_effort(deep_review_slot(), "deep_self_review") == "xhigh"


def test_malformed_deep_review_refuses_the_whole_setting(env):
    """The parser is ONE authority: a bad deep_review row is a save-time 400 and
    a runtime typed error, never a silent fallback onto the model key."""
    bad = _payload({"route": {"kind": "api_chat", "target_id": "m"}, "bogus": 1})
    with pytest.raises(ValueError, match="deep_review has unknown keys"):
        reviewer_slot_save_check(bad)
    env.setenv(REVIEWER_SLOTS_ENV, bad)
    with pytest.raises(ValueError, match="deep_review has unknown keys"):
        deep_review_slot()
    # A valid row passes the save check (and produces no acceptance warning).
    assert reviewer_slot_save_check(_payload({"subagent_id": "session-critic"})) == ""


def test_reviewer_slots_endpoint_reports_the_deep_review_row_and_its_limit(env):
    env.setenv(REVIEWER_SLOTS_ENV, _payload())
    body = _get_endpoint()
    assert body["limits"]["deep_review"] == 1
    # Synthesized: the effective row is shown AND labeled as not saved yet.
    assert body["deep_review"] == {
        "route": {"kind": "api_chat", "target_id": "openai/legacy-deep-model"},
        "effort": "",
        "synthesized_from": "OUROBOROS_MODEL_DEEP_SELF_REVIEW",
    }
    # Saved direct session row: the stored form round-trips with its pin, unlabeled.
    env.setenv(REVIEWER_SLOTS_ENV, _payload(
        {"route": {"kind": "agent_session", "target_id": "codex=gpt-5.6-sol", "profile_id": "koshak"}, "effort": "high"}))
    body = _get_endpoint()
    assert body["deep_review"] == {
        "route": {"kind": "agent_session", "target_id": "codex=gpt-5.6-sol", "profile_id": "koshak"},
        "effort": "high",
    }
    # Saved reference: the subagent_id IS the stored form; the route is disclosure only.
    env.setenv(REVIEWER_SLOTS_ENV, _payload({"subagent_id": "api-critic"}))
    row = _get_endpoint()["deep_review"]
    assert row["subagent_id"] == "api-critic" and "route" not in row and "slot_id" not in row
    assert row["resolved_route"] == {"kind": "api_chat", "target_id": "openai/gpt-5.6-terra"}
    # Legacy install: the synthesized row is reported the same way.
    env.delenv(REVIEWER_SLOTS_ENV, raising=False)
    body = _get_endpoint()
    assert body["source"] == "legacy"
    assert body["deep_review"]["synthesized_from"] == "OUROBOROS_MODEL_DEEP_SELF_REVIEW"
