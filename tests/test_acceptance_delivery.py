"""Task acceptance on the configured triad rows (owner decisions R0/R2/R3,
2026-09-01, Ф2 of the agentic-review sprint).

ONE builder — ``reviewer_slot_config.triad_delivery_slots`` — turns the triad
rows into ``ReviewSlot`` objects for plan review, skill/commit review (as the
aligned vectors of ``commit_triad_delivery``) and task acceptance, so acceptance
carries every row's own delivery, effort, credential pin, configured-subagent
binding and stable slot id instead of an api-pinned projection. A malformed
structured configuration refuses acceptance typed (DEGRADED) exactly as it
refuses plan and skill review; a legacy comma-key config reproduces today's
panel byte for byte; child-task and ``off``-mode acceptance buy no retrieving
row.
"""

import json
from types import SimpleNamespace

import pytest

from ouroboros.review_execution import ReviewRouteKind
from ouroboros.reviewer_slot_config import REVIEWER_SLOTS_ENV, triad_delivery_slots

_ROSTER = {
    "enabled": True,
    "items": [{
        "subagent_id": "api-critic",
        "name": "API critic",
        "recommended_use": "Exact recursive API reviewer.",
        "route": {"kind": "api_model", "target_id": "openai/gpt-5.6-terra"},
        "effort": "medium",
    }],
}

_TRIAD = {
    "triad": [
        {"slot_id": "t_api", "route": {"kind": "api_chat", "target_id": "openai/gpt-5.6-luna"},
         "effort": "high"},
        {"slot_id": "t_sess",
         "route": {"kind": "agent_session", "target_id": "codex=gpt-5.6-sol", "profile_id": "acct-1"},
         "effort": "xhigh"},
        {"slot_id": "t_actor", "subagent_id": "api-critic"},
    ],
    "scope": [{"slot_id": "s1", "route": {"kind": "api_chat", "target_id": "openai/gpt-5.6-terra"}}],
}


@pytest.fixture()
def structured_env(monkeypatch):
    monkeypatch.setenv("OUROBOROS_SUBAGENTS", json.dumps(_ROSTER))
    monkeypatch.setenv(REVIEWER_SLOTS_ENV, json.dumps(_TRIAD))
    for key in ("OUROBOROS_REVIEW_MODELS", "OUROBOROS_REVIEW_ROUTES", "OUROBOROS_REVIEW_SESSION_ROUTE"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _acceptance_ctx(tmp_path, *, evidence=None, task_metadata=None, **tool_ctx_fields):
    """A root acceptance context whose wallet claim can be exercised for real."""
    from ouroboros import loop as loop_mod
    from ouroboros.contracts.task_contract import build_task_contract
    from ouroboros.review_substrate import build_review_binding
    from ouroboros.task_results import STATUS_RUNNING, write_task_result

    contract = build_task_contract({"budget_profile": {"max_improvement_passes": 0}})
    metadata = {
        "root_task_id": "root-delivery", "delegation_role": "root",
        "budget_drive_root": str(tmp_path), "task_contract": contract,
        **(task_metadata or {}),
    }
    tool_ctx = SimpleNamespace(
        task_id="root-delivery", drive_root=tmp_path, budget_drive_root=str(tmp_path),
        task_contract=contract, task_metadata=metadata, pending_events=[],
        **tool_ctx_fields,
    )
    write_task_result(
        tmp_path, "root-delivery", STATUS_RUNNING, root_task_id="root-delivery",
        delegation_role="root", task_contract=contract,
    )
    evidence = evidence if evidence is not None else {"evidence": "complete"}
    return loop_mod._TaskAcceptanceContext(
        tools=SimpleNamespace(_ctx=tool_ctx), content="deliverable", task_id="root-delivery",
        task_type="task", llm_trace={"tool_calls": []}, drive_root=tmp_path,
        messages=[{"role": "system", "content": "policy"}, {"role": "user", "content": "goal"}],
        emit_progress=lambda _text: None, mode="required", subtree_statuses=[],
        budget_profile=contract["budget_profile"], passes_done=0, evidence=evidence,
        review_binding=build_review_binding(
            candidate="deliverable", evidence=evidence, fence_token_or_state="delivery-test",
        ),
    )


def _capture_panel(monkeypatch):
    """Stub the substrate call and the wave gate; return the captured (request, kwargs)."""
    import ouroboros.review_substrate as rs
    from ouroboros.tools import review_helpers

    captured = []

    def _run(request, **kwargs):
        captured.append((request, kwargs))
        return SimpleNamespace(aggregate_signal="PASS", actors=[])

    monkeypatch.setattr(rs, "run_review_request", _run)
    monkeypatch.setattr(review_helpers, "review_wave_budget_gate", lambda *_a, **_k: None)
    return captured


# ---------------------------------------------------------------------------
# One builder for plan review, skill/commit vectors and task acceptance.
# ---------------------------------------------------------------------------


def test_triad_delivery_slots_is_the_one_builder_shared_by_plan_and_commit_vectors(structured_env):
    from ouroboros.reviewer_slot_config import commit_triad_delivery
    from ouroboros.tools.plan_review_runtime import (
        PLAN_REVIEW_EFFORT,
        PLAN_REVIEW_MAX_TOKENS,
        plan_review_slots,
    )

    acceptance = triad_delivery_slots(role_hint="task acceptance")
    plan = plan_review_slots()
    identity = lambda s: (s.slot_id, s.model, s.route, s.session_target, s.session_profile, s.subagent_id)  # noqa: E731
    assert [identity(s) for s in plan] == [identity(s) for s in acceptance]
    assert [s.slot_id for s in acceptance] == ["t_api", "t_sess", "t_actor"]
    # Plan review keeps its own slot properties on the shared rows.
    assert all(s.role_hint == "plan reviewer" and s.max_tokens == PLAN_REVIEW_MAX_TOKENS for s in plan)
    assert all(s.role_hint == "task acceptance" for s in acceptance)
    # Effort: explicit row → row; compound/none → the caller's default (plan) or the
    # roster row's own effort (actor row).
    assert [s.effort for s in plan] == ["high", "xhigh", "medium"]
    assert plan[0].effort != PLAN_REVIEW_EFFORT or PLAN_REVIEW_EFFORT == "high"
    # The commit/skill vectors are a projection of the same slots.
    vectors = commit_triad_delivery()
    assert vectors["slot_ids"] == [s.slot_id for s in acceptance]
    assert vectors["models"] == [s.model for s in acceptance]
    assert vectors["routes"] == [s.route for s in acceptance]
    assert vectors["session_profiles"] == ["", "acct-1", ""]
    assert vectors["subagent_ids"] == ["", "", "api-critic"]
    assert vectors["legacy_skill_fingerprint"] is False


def test_acceptance_panel_carries_each_rows_identity_effort_pin_and_binding(structured_env, tmp_path):
    from ouroboros import loop as loop_mod
    from ouroboros.config import adaptive_quorum

    captured = _capture_panel(structured_env)
    result = loop_mod._execute_task_acceptance_panel(_acceptance_ctx(tmp_path))
    assert result.aggregate_signal == "PASS"
    (request, kwargs), = captured
    slots = kwargs["slots"]
    assert [s.slot_id for s in slots] == ["t_api", "t_sess", "t_actor"]  # owner ids, not slot_N
    assert [s.route for s in slots] == [
        ReviewRouteKind.API_CHAT, ReviewRouteKind.AGENT_SESSION, ReviewRouteKind.API_CHAT,
    ]
    assert [s.effort for s in slots] == ["high", "xhigh", "medium"]  # per-row, not one global effort
    assert slots[1].session_target == "codex=gpt-5.6-sol" and slots[1].session_profile == "acct-1"
    assert slots[2].subagent_id == "api-critic" and slots[2].native_retrieval
    assert request.policy["min_successful_slots"] == adaptive_quorum(3)


def test_malformed_structured_config_refuses_acceptance_typed(structured_env, tmp_path):
    """R3: the same typed refusal plan and skill review give — never the silently
    projected default panel the retired residual used to run."""
    import ouroboros.review_substrate as rs
    from ouroboros import loop as loop_mod

    structured_env.setenv(REVIEWER_SLOTS_ENV, "{broken")
    structured_env.setattr(
        rs, "run_review_request",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no reviewer may be called")),
    )
    result = loop_mod._execute_task_acceptance_panel(_acceptance_ctx(tmp_path))
    assert result.aggregate_signal == "DEGRADED" and result.degraded
    assert any(
        r.startswith("reviewer_slot_config_invalid:") and "no reviewer was called" in r
        for r in result.degraded_reasons
    )
    assert result.actors == []


def test_legacy_comma_config_reproduces_todays_api_panel(monkeypatch, tmp_path):
    """The GAIA/CLB/SWE-Pro class: no structured key, a comma list — the panel is
    the same three api rows with the legacy `slot_N` ids and the configured
    Review effort, exactly what the projection used to hand acceptance."""
    from ouroboros import loop as loop_mod
    from ouroboros.config import resolve_effort

    monkeypatch.delenv(REVIEWER_SLOTS_ENV, raising=False)
    monkeypatch.delenv("OUROBOROS_REVIEW_ROUTES", raising=False)
    monkeypatch.setenv("OUROBOROS_REVIEW_MODELS", "openai/a,openai/b,openai/c")
    captured = _capture_panel(monkeypatch)
    loop_mod._execute_task_acceptance_panel(_acceptance_ctx(tmp_path))
    (_request, kwargs), = captured
    slots = kwargs["slots"]
    assert [(s.slot_id, s.model, s.route) for s in slots] == [
        ("slot_1", "openai/a", ReviewRouteKind.API_CHAT),
        ("slot_2", "openai/b", ReviewRouteKind.API_CHAT),
        ("slot_3", "openai/c", ReviewRouteKind.API_CHAT),
    ]
    assert all(s.effort == resolve_effort("review") and not s.retrieves for s in slots)


def test_child_and_off_acceptance_run_packet_rows_only(structured_env, tmp_path):
    """Child-task and `off`-mode acceptance is advisory evidence: it buys no
    retrieving panel (no agent session, no native episode) — it runs the
    configured PACKET rows, and refuses typed when none remain."""
    import ouroboros.review_substrate as rs
    from ouroboros import review_evidence as re_mod
    from ouroboros.tools.review import _handle_task_acceptance_review

    calls = []
    structured_env.setattr(re_mod, "collect_turn_diff", lambda ctx, **kwargs: "")
    structured_env.setattr(rs, "build_improvement_capsule", lambda _result: "")
    structured_env.setattr(rs, "dissent_findings", lambda _result: [])

    def fake_run(request, **kwargs):
        calls.append([s.slot_id for s in kwargs["slots"]])
        return SimpleNamespace(aggregate_signal="PASS", actors=[], parsed_findings=[])

    structured_env.setattr(rs, "run_review_request", fake_run)
    structured_env.setenv("OUROBOROS_TASK_REVIEW_MODE", "off")
    ctx = SimpleNamespace(
        drive_root=str(tmp_path), task_id="root", root_task_id="root",
        task_metadata={"root_task_id": "root"}, task_contract={},
    )
    # Mixed triad: only the api row is dispatched; the session and the actor
    # row are dropped without being called.
    json.loads(_handle_task_acceptance_review(ctx, claim="root done"))
    assert calls == [["t_api"]]

    # All-retrieving triad: a typed not_dispatched result, no reviewer called.
    all_retrieving = {**_TRIAD, "triad": _TRIAD["triad"][1:]}
    structured_env.setenv(REVIEWER_SLOTS_ENV, json.dumps(all_retrieving))
    payload = json.loads(_handle_task_acceptance_review(ctx, claim="root done"))
    assert payload["status"] == "not_dispatched" and payload["reason"] == "no_packet_reviewer_rows"
    assert calls == [["t_api"]]

    # Malformed configuration: the same typed refusal, never a default panel.
    structured_env.setenv(REVIEWER_SLOTS_ENV, "{broken")
    payload = json.loads(_handle_task_acceptance_review(ctx, claim="root done"))
    assert payload["status"] == "not_dispatched" and "invalid reviewer-slot configuration" in payload["error"]
    assert calls == [["t_api"]]
