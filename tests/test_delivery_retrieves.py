"""ONE delivery-class predicate (`review_execution.delivery_retrieves`).

A reviewer row RETRIEVES the subject with its own tools when it is a hosted
session or a configured-subagent api row (native tool rounds); every other api
row receives the assembled packet. Before this predicate existed, four callers
carried their own inline copy of that rule — a class that can drift.
"""

from ouroboros.review_execution import ReviewRouteKind, delivery_retrieves
from ouroboros.review_substrate import ReviewSlot
from ouroboros.reviewer_slot_config import ConfiguredReviewerSlot
from ouroboros.tools.plan_review_runtime import slot_retrieves


def test_predicate_accepts_route_kind_or_wire_string():
    assert delivery_retrieves(ReviewRouteKind.AGENT_SESSION, "") is True
    assert delivery_retrieves("agent_session", "") is True
    assert delivery_retrieves(ReviewRouteKind.API_CHAT, "api-critic") is True
    assert delivery_retrieves("api_chat", " ") is False
    assert delivery_retrieves(ReviewRouteKind.API_CHAT, "") is False
    assert delivery_retrieves(None, "") is False


def test_slot_properties_and_plan_review_facade_share_the_predicate():
    api = ReviewSlot(slot_id="t1", model="m", effort="low")
    native = ReviewSlot(slot_id="t2", model="m", effort="low", subagent_id="api-critic")
    session = ReviewSlot(slot_id="t3", model="m", effort="low", route=ReviewRouteKind.AGENT_SESSION)
    assert [s.retrieves for s in (api, native, session)] == [False, True, True]
    assert [slot_retrieves(s) for s in (api, native, session)] == [False, True, True]
    assert native.native_retrieval is True and session.native_retrieval is False

    rows = [
        ConfiguredReviewerSlot(slot_id="a", kind="api_chat", target_id="m", effort="low"),
        ConfiguredReviewerSlot(slot_id="b", kind="api_chat", target_id="m", effort="low", subagent_id="api-critic"),
        ConfiguredReviewerSlot(slot_id="c", kind="agent_session", target_id="codex=m", effort="low"),
    ]
    assert [r.retrieves for r in rows] == [False, True, True]
