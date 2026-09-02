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


def _acceptance_ctx(tmp_path, *, evidence=None, task_metadata=None, content="deliverable",
                    fresh_result=True, **tool_ctx_fields):
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
    if fresh_result:
        write_task_result(
            tmp_path, "root-delivery", STATUS_RUNNING, root_task_id="root-delivery",
            delegation_role="root", task_contract=contract,
        )
    evidence = evidence if evidence is not None else {"evidence": "complete"}
    return loop_mod._TaskAcceptanceContext(
        tools=SimpleNamespace(_ctx=tool_ctx), content=content, task_id="root-delivery",
        task_type="task", llm_trace={"tool_calls": []}, drive_root=tmp_path,
        messages=[{"role": "system", "content": "policy"}, {"role": "user", "content": "goal"}],
        emit_progress=lambda _text: None, mode="required", subtree_statuses=[],
        budget_profile=contract["budget_profile"], passes_done=0, evidence=evidence,
        review_binding=build_review_binding(
            candidate=content, evidence=evidence, fence_token_or_state="delivery-test",
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


# ---------------------------------------------------------------------------
# The retrieving work order (R1/R4/R5/R15/R23) and the route-aware gates.
# ---------------------------------------------------------------------------

_ACCEPTANCE_PACKET = {
    "task_contract": {"objective": "ship it", "acceptance_claims": [{"id": "claim_1", "claim": "game boots"}]},
    "acceptance_support_refs": [{
        "criterion_id": "claim_1", "support_status": "supported",
        "support_refs": [{"ref": "verification_receipts[0]", "status": "pass"}],
    }],
    "verification_summary": {"count": 1, "failed_count": 0},
    "verification_receipts": [{
        "ref": "verification_receipts[0]", "status": "pass", "matched": True,
        "provenance": "host_attested", "criterion_id": "claim_1", "check": "pytest -q",
    }],
    "acceptance_obligations": [],
    "artifacts": [{"name": "report/summary.md", "size": 10, "preview": "PREVIEW-BYTES-OF-THE-ARTIFACT"}],
    "repo_diff": "diff --git a/x b/x",
    "tool_trajectory": [{"tool": "run_command", "status": "ok", "result": "TRAJECTORY-RESULT-3-passed"}],
    "reasoning_notes": "I believe the feature works.",
    "__provenance__": {
        "task_contract": "host_attested", "acceptance_support_refs": "host_attested",
        "verification_summary": "host_attested", "verification_receipts": "host_attested",
        "acceptance_obligations": "host_attested", "repo_diff": "host_attested",
        "artifacts": "artifact", "tool_trajectory": "tool_result", "reasoning_notes": "agent_supplied",
    },
}

_CLEAN_VERDICT = {
    "verdict": "PASS", "outcome_tier": "solved", "completion_coach": "",
    "criteria_used": [
        {"criterion": "game boots", "status": "supported", "evidence_refs": ["verification_receipts[0]"]},
        {"criterion": "receipt beside a fabricated ref", "status": "supported",
         "evidence_refs": ["verification_receipts[0]", "made_up_ref"]},
    ],
    "dialogue_status": "unreachable_here",
    "findings": [], "summary": "verified against the receipts",
}

_FAKE_ROSTER = {
    "enabled": True,
    "items": [{
        "subagent_id": "api-critic", "name": "API critic", "recommended_use": "Exact reviewer.",
        "route": {"kind": "api_model", "target_id": "openai/fake-reviewer"},
    }],
}
_ROW_API = {"slot_id": "t_api", "route": {"kind": "api_chat", "target_id": "openai/fake-reviewer"}}
_ROW_NATIVE = {"slot_id": "t_actor", "subagent_id": "api-critic"}
_ROW_SESSION = {"slot_id": "t_sess", "route": {"kind": "agent_session", "target_id": "fake-review=fake-small"}}
_ROW_SCOPE = {"slot_id": "s1", "route": {"kind": "api_chat", "target_id": "openai/fake-reviewer"}}


def _offline_env(monkeypatch, *rows):
    """An offline structured triad (fake model ids never reach a provider)."""
    monkeypatch.setenv("OUROBOROS_SUBAGENTS", json.dumps(_FAKE_ROSTER))
    monkeypatch.setenv(REVIEWER_SLOTS_ENV, json.dumps({"triad": list(rows), "scope": [_ROW_SCOPE]}))
    for key in ("OUROBOROS_REVIEW_MODELS", "OUROBOROS_REVIEW_ROUTES", "OUROBOROS_REVIEW_SESSION_ROUTE"):
        monkeypatch.delenv(key, raising=False)


class _EpisodeLLM:
    """Scripted `chat()` that crosses the real durable attempt ledger on every
    send — the production `LLMClient` does, and both the packet row and the
    native episode bind the acceptance stamp around that crossing — so the
    wallet claim fires exactly where production fires it."""

    def __init__(self, drive_root, script):
        self.drive_root = drive_root
        self.script = list(script)
        self.calls = []

    def _reply(self, kwargs):
        self.calls.append(dict(kwargs))
        if not self.script:
            raise AssertionError("script exhausted — an extra reviewer send was made")
        return self.script.pop(0), {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0}

    def chat(self, **kwargs):
        from ouroboros import usage_accounting as ua

        request = ua.AttemptRequest(
            model="local-review-test", provider="local", reservation_usd=0.0,
            drive_root=self.drive_root, task_id="review", root_task_id="review",
        )
        return ua.execute_physical_attempt(request, lambda: self._reply(kwargs))


def _real_panel(monkeypatch, llm):
    """Run the REAL substrate under the panel with a scripted LLM; capture requests."""
    import ouroboros.review_substrate as rs
    from ouroboros.tools import review_helpers

    seen = []
    original = rs.run_review_request

    def _run(request, **kwargs):
        seen.append(request)
        return original(request, llm=llm, **kwargs)

    monkeypatch.setattr(rs, "run_review_request", _run)
    monkeypatch.setattr(review_helpers, "review_wave_budget_gate", lambda *_a, **_k: None)
    return seen


def _roots(tmp_path):
    governance, workspace = tmp_path / "governance", tmp_path / "workspace"
    governance.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    return governance, workspace


def test_trap_retrieving_row_receipt_ref_resolves_against_the_full_packet(monkeypatch, tmp_path):
    """THE trap (brief §6.2 item 6): a retrieving row that cites a real receipt
    ref is resolved CLEAN — against the FULL packet the host built, never against
    the tail-less projection it was sent."""
    from ouroboros import loop as loop_mod
    from ouroboros.review_substrate import task_acceptance_is_clean

    _offline_env(monkeypatch, _ROW_NATIVE)
    llm = _EpisodeLLM(tmp_path, [{"content": json.dumps(_CLEAN_VERDICT)}])
    seen = _real_panel(monkeypatch, llm)
    governance, workspace = _roots(tmp_path)
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project")
    result = loop_mod._execute_task_acceptance_panel(ctx)

    (actor,) = result.actors
    assert actor["status"] == "ok" and result.aggregate_signal == "PASS"
    assert task_acceptance_is_clean(result) is True
    (row,) = actor["criteria_refs_unresolved"]  # only the criterion carrying the fabricated ref
    assert row["supported_evidence_resolves"] is True
    assert row["refs"][0]["ref"] == "verification_receipts[0]" and row["refs"][0]["resolved_as"]
    assert row["refs"][1] == {"ref": "made_up_ref", "resolved_as": ""}
    assert actor["usage"]["host_file_read_attestation"] == "host_observed"

    (request,) = seen
    order = request.slot_session_tasks["t_actor"]
    assert "TRAJECTORY-RESULT-3-passed" not in order and "PREVIEW-BYTES" not in order  # tail withheld
    assert "verification_receipts[0]" in order and "RETRIEVAL POINTERS" in order and str(tmp_path) in order
    assert request.evidence["tool_trajectory"][0]["result"] == "TRAJECTORY-RESULT-3-passed"  # FULL dict intact
    assert request.policy["native_data_root"] == str(tmp_path)
    assert request.session_root == str(workspace)
    sent = json.dumps(llm.calls[0]["messages"])
    assert "RETRIEVAL POINTERS" in sent and "TRAJECTORY-RESULT-3-passed" not in sent


def test_acceptance_request_carries_the_route_owned_work_order(structured_env, tmp_path):
    from ouroboros import loop as loop_mod
    from ouroboros.review_execution import (
        ReviewAssignment,
        _render_prompt_parts,
        _review_route_executor,
        review_output_contract,
    )
    from ouroboros.triad_review import ACCEPTANCE_SURFACE_RULES

    captured = _capture_panel(structured_env)
    governance, workspace = _roots(tmp_path)
    deadline = "2030-01-01T00:00:00+00:00"
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), task_metadata={"deadline_at": deadline},
                          repo_dir=str(governance), workspace_root=str(workspace), workspace_mode="project")
    loop_mod._execute_task_acceptance_panel(ctx)
    (request, kwargs), = captured
    api, sess, actor = kwargs["slots"]
    # R23, R5 and the carried Ф1 finding: owner deadline, real data root, THE acceptance contract.
    assert request.deadline_at == deadline
    assert request.session_root == str(workspace)  # the ACTIVE workspace, not the governance repo
    assert request.policy["native_data_root"] == str(tmp_path)
    contract = request.policy["output_contract"]
    assert contract == review_output_contract(request)
    assert ACCEPTANCE_SURFACE_RULES in contract and "criteria_used" in contract and "dialogue_status" in contract
    for slot in (sess, actor):
        executor = _review_route_executor(ReviewAssignment(request=request, slot=slot, call_id="c"))
        assert executor._output_contract() == contract
    # Session row: the FULL packet, absolute pointers and the access disclosure.
    order = request.slot_session_tasks
    assert set(order) == {"t_sess", "t_actor"}  # packet rows carry no work order
    assert "TRAJECTORY-RESULT-3-passed" in order["t_sess"] and "PREVIEW-BYTES" in order["t_sess"]
    assert "not guaranteed" in order["t_sess"] and str(workspace) in order["t_sess"]
    # Native row: the same packet minus its freely degradable tail, manifested.
    assert "TRAJECTORY-RESULT-3-passed" not in order["t_actor"] and "PREVIEW-BYTES" not in order["t_actor"]
    assert "retrieving_delivery" in order["t_actor"] and "report/summary.md" in order["t_actor"]
    assert "verification_receipts[0]" in order["t_actor"]
    # Both carry the task-stable contract the packet rows render; the FULL packet stays the authority.
    _stable, task_stable, _dynamic = _render_prompt_parts(request, api)
    assert task_stable.rstrip() in order["t_sess"] and task_stable.rstrip() in order["t_actor"]
    assert request.evidence["tool_trajectory"][0]["result"] == "TRAJECTORY-RESULT-3-passed"
    # The api pack states the contract once: route-owned keys never enter its rendered Policy JSON.
    assert "native_data_root" not in task_stable and "output_contract" not in task_stable


def test_wave_budget_gate_prices_only_the_api_money(structured_env, tmp_path):
    import ouroboros.review_substrate as rs
    from ouroboros import loop as loop_mod
    from ouroboros.tools import review_helpers

    gate_calls = []
    structured_env.setattr(
        rs, "run_review_request", lambda request, **kw: SimpleNamespace(aggregate_signal="PASS", actors=[]))
    structured_env.setattr(review_helpers, "review_wave_budget_gate", lambda _ctx, **kw: gate_calls.append(kw))
    governance, workspace = _roots(tmp_path)
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project")
    loop_mod._execute_task_acceptance_panel(ctx)
    (kw,) = gate_calls
    # The session row is subscription, not API money; the native row is one episode send.
    assert kw["models"] == ["openai/gpt-5.6-luna", "openai/gpt-5.6-terra"] and kw["prompt_chars"] > 0
    # An all-session panel spends no API money: the gate is not consulted at all.
    structured_env.setenv(REVIEWER_SLOTS_ENV, json.dumps({**_TRIAD, "triad": [_TRIAD["triad"][1]]}))
    loop_mod._execute_task_acceptance_panel(ctx)
    assert len(gate_calls) == 1


def test_partial_source_refusal_spares_retrieving_rows_and_core_overflow_refuses_all(monkeypatch, tmp_path):
    from ouroboros import loop as loop_mod

    _offline_env(monkeypatch, _ROW_API, _ROW_NATIVE)
    governance, workspace = _roots(tmp_path)
    llm = _EpisodeLLM(tmp_path, [{"content": json.dumps(_CLEAN_VERDICT)}])
    _real_panel(monkeypatch, llm)
    partial = {**_ACCEPTANCE_PACKET, "__unresolved_partial_artifacts__": True}
    result = loop_mod._execute_task_acceptance_panel(_acceptance_ctx(
        tmp_path, evidence=partial, repo_dir=str(governance),
        workspace_root=str(workspace), workspace_mode="project"))
    by_id = {a["slot_id"]: a for a in result.actors}
    # The packet row is refused free (a partial PROJECTION is not complete evidence);
    # the native row reads the exact source itself and runs.
    assert by_id["t_api"]["parsed"]["verdict"] == "DEGRADED" and "partial" in by_id["t_api"]["parsed"]["summary"]
    assert by_id["t_actor"]["parsed"]["verdict"] == "PASS"
    assert len(llm.calls) == 1 and "tools" in llm.calls[0]
    # The immutable-core overflow refuses EVERY delivery: no owner requirement is truncated for anyone.
    overflow = {**_ACCEPTANCE_PACKET, "__immutable_core_overflow__": True}
    result = loop_mod._execute_task_acceptance_panel(_acceptance_ctx(
        tmp_path, evidence=overflow, repo_dir=str(governance),
        workspace_root=str(workspace), workspace_mode="project"))
    assert [a["parsed"]["verdict"] for a in result.actors] == ["DEGRADED", "DEGRADED"]
    assert len(llm.calls) == 1  # nothing further was sent


def test_retrieving_row_gets_no_format_repair_resend(monkeypatch, tmp_path):
    """A retrieving row canonicalizes its own answer (strict parse, then
    extraction over the collected transcript); the packet rows' second send for
    format repair never buys it a second episode."""
    from ouroboros import loop as loop_mod

    _offline_env(monkeypatch, _ROW_NATIVE)
    governance, workspace = _roots(tmp_path)
    llm = _EpisodeLLM(tmp_path, [{"content": "I looked around and it seems fine; no structured verdict."},
                                 {"content": "{}"}, {"content": "{}"}, {"content": "{}"}])
    _real_panel(monkeypatch, llm)
    result = loop_mod._execute_task_acceptance_panel(_acceptance_ctx(
        tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
        workspace_root=str(workspace), workspace_mode="project"))
    assert result.aggregate_signal == "DEGRADED"
    assert sum(1 for call in llm.calls if "tools" in call) == 1  # one episode; no repair resend


@pytest.mark.parametrize("rows", [(_ROW_API,), (_ROW_NATIVE,), (_ROW_API, _ROW_NATIVE)])
def test_wallet_stamp_claims_once_per_panel_on_api_native_and_mixed_rows(monkeypatch, tmp_path, rows):
    """R11: the paid identity is material, not route. One strict claim per
    panel whatever the rows' deliveries, and a spent wallet refuses a NEW paid
    identity before any reviewer is sent — on every delivery alike."""
    from ouroboros import loop as loop_mod
    from ouroboros.acceptance_dialogue import _total_paid_acceptance_cycles

    _offline_env(monkeypatch, *rows)
    governance, workspace = _roots(tmp_path)
    llm = _EpisodeLLM(tmp_path, [{"content": json.dumps(_CLEAN_VERDICT)}] * len(rows))
    _real_panel(monkeypatch, llm)
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project")
    result = loop_mod._execute_task_acceptance_panel(ctx)
    assert result.aggregate_signal == "PASS"
    assert _total_paid_acceptance_cycles(ctx) == 1
    assert len(llm.calls) == len(rows)
    # max_improvement_passes=0 → the tree may buy ONE panel: a changed candidate
    # (new paid identity) is refused fail-closed before any send.
    again = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), content="deliverable v2",
                            fresh_result=False, repo_dir=str(governance),
                            workspace_root=str(workspace), workspace_mode="project")
    refused = loop_mod._execute_task_acceptance_panel(again)
    assert refused.aggregate_signal == "DEGRADED"
    assert len(llm.calls) == len(rows) and _total_paid_acceptance_cycles(again) == 1


def test_session_row_claims_the_same_wallet_and_receives_the_full_packet(monkeypatch, tmp_path):
    from tests.test_review_agent_session_route import FakeGateway, _terminal_detail
    from ouroboros import claudexor_daemon
    from ouroboros import delegate_custody as custody
    from ouroboros import loop as loop_mod
    from ouroboros.acceptance_dialogue import _total_paid_acceptance_cycles
    from ouroboros.gateways import claudexor as gateway_module
    from ouroboros.review_substrate import task_acceptance_is_clean

    FakeGateway.reset()
    FakeGateway.detail = _terminal_detail(json.dumps(_CLEAN_VERDICT), conformance="passed")
    monkeypatch.setattr("ouroboros.gateways.claudexor.ClaudexorGateway", FakeGateway)
    monkeypatch.setattr(claudexor_daemon, "ensure_owned_gateway", lambda: gateway_module.ClaudexorGateway())
    custody._CUSTODY.clear()
    _offline_env(monkeypatch, _ROW_SESSION)
    governance, workspace = _roots(tmp_path)
    llm = _EpisodeLLM(tmp_path, [])
    _real_panel(monkeypatch, llm)
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project")
    result = loop_mod._execute_task_acceptance_panel(ctx)
    assert result.aggregate_signal == "PASS" and task_acceptance_is_clean(result)
    assert _total_paid_acceptance_cycles(ctx) == 1
    assert llm.calls == []  # a conformant verdict: no extraction, no api pack
    (start,) = FakeGateway.instances[0].start_requests
    wire = json.dumps(start)
    assert "RETRIEVAL POINTERS" in wire and "TRAJECTORY-RESULT-3-passed" in wire and "not guaranteed" in wire
    assert str(workspace) in wire


def test_replayed_panel_keeps_the_delivery_it_actually_ran_on(monkeypatch, tmp_path):
    """Re-routing the triad after a panel does not re-buy it: the identical
    submission replays the recorded run, whose actors still say how they
    were executed (a native episode), not what the rows say today."""
    from ouroboros import loop as loop_mod
    from ouroboros.acceptance_dialogue import _prior_acceptance_run

    _offline_env(monkeypatch, _ROW_NATIVE)
    governance, workspace = _roots(tmp_path)
    llm = _EpisodeLLM(tmp_path, [{"content": json.dumps(_CLEAN_VERDICT)}])
    _real_panel(monkeypatch, llm)
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project")
    record = loop_mod._record_host_acceptance_run(ctx, loop_mod._execute_task_acceptance_panel(ctx))
    assert record["actors"][0]["usage"]["delivery"] == "native_tool_rounds"
    monkeypatch.setenv(REVIEWER_SLOTS_ENV, json.dumps({"triad": [_ROW_API], "scope": [_ROW_SCOPE]}))
    _cache, prior = _prior_acceptance_run(ctx.tools._ctx, ctx.llm_trace, ctx.review_binding["binding_hash"])
    assert prior is record and prior["actors"][0]["usage"]["delivery"] == "native_tool_rounds"
    assert len(llm.calls) == 1


# ---------------------------------------------------------------------------
# Delivery-aware pacing (R16).
# ---------------------------------------------------------------------------


def _timing(events, **row):
    from ouroboros.utils import append_jsonl

    append_jsonl(events, {"type": "task_acceptance_review_timing", **row})


def test_pacing_estimate_follows_the_configured_panels_delivery_class(structured_env, tmp_path):
    from ouroboros import task_pacing

    ctx = SimpleNamespace(drive_root=tmp_path, task_metadata={})
    events = task_pacing.acceptance_timing_events_path(ctx)
    events.parent.mkdir(parents=True, exist_ok=True)
    _timing(events, duration_sec=100)                                   # pre-R16 row: a packet panel
    _timing(events, duration_sec=100, delivery="api_chat")
    _timing(events, duration_sec=900, delivery="agent_session")
    _timing(events, duration_sec=300, delivery="native_tool_rounds")
    # The mixed triad's slowest delivery is the session: paced by session wall clock.
    assert task_pacing.acceptance_panel_delivery(ctx) == "agent_session"
    assert task_pacing.acceptance_review_estimate_sec(ctx, passes_done=1) == 1350.0
    # A packet-only triad ignores the session and native history (EWMA 100 → floor).
    structured_env.setenv(REVIEWER_SLOTS_ENV, json.dumps({**_TRIAD, "triad": [_TRIAD["triad"][0]]}))
    assert task_pacing.acceptance_panel_delivery(ctx) == "api_chat"
    assert task_pacing.acceptance_review_estimate_sec(ctx, passes_done=1) == 200.0
    # An explicit class, a class without history, the first pass, a malformed panel.
    assert task_pacing.acceptance_review_estimate_sec(ctx, passes_done=1, delivery="native_tool_rounds") == 450.0
    assert task_pacing.acceptance_review_estimate_sec(ctx, passes_done=1, delivery="unknown") == 200.0
    assert task_pacing.acceptance_review_estimate_sec(ctx, passes_done=0) == 200.0
    structured_env.setenv(REVIEWER_SLOTS_ENV, "{broken")
    assert task_pacing.acceptance_panel_delivery(ctx) == "api_chat"
    # Native rounds: none observed → one send; observed → per-row EWMA, rounded up.
    assert task_pacing.acceptance_native_rounds_estimate(ctx) == 1
    _timing(events, duration_sec=30, delivery="native_tool_rounds", native_rounds=6, native_rows=1)
    _timing(events, duration_sec=30, delivery="native_tool_rounds", native_rounds=6, native_rows=2)
    assert task_pacing.acceptance_native_rounds_estimate(ctx) == 5  # ewma(6, 3) = 4.5 → 5


def test_timing_event_names_the_deliveries_and_the_native_rounds(monkeypatch, tmp_path):
    from ouroboros import loop as loop_mod
    from ouroboros import task_pacing
    from ouroboros.utils import iter_jsonl_objects

    _offline_env(monkeypatch, _ROW_API, _ROW_NATIVE)
    governance, workspace = _roots(tmp_path)
    llm = _EpisodeLLM(tmp_path, [{"content": json.dumps(_CLEAN_VERDICT)}] * 2)
    _real_panel(monkeypatch, llm)
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project")
    assert loop_mod._execute_task_acceptance_panel(ctx).aggregate_signal == "PASS"
    (event,) = [e for e in iter_jsonl_objects(task_pacing.acceptance_timing_events_path(ctx.tools._ctx))
                if e.get("type") == "task_acceptance_review_timing"]
    assert event["delivery"] == "native_tool_rounds"
    assert event["deliveries"] == ["api_chat", "native_tool_rounds"]
    assert event["native_rounds"] == 1 and event["native_rows"] == 1 and event["duration_sec"] > 0


def test_wave_gate_prices_a_native_row_by_its_observed_rounds(structured_env, tmp_path):
    import ouroboros.review_substrate as rs
    from ouroboros import loop as loop_mod, task_pacing
    from ouroboros.tools import review_helpers

    gate_calls = []
    structured_env.setattr(
        rs, "run_review_request", lambda request, **kw: SimpleNamespace(aggregate_signal="PASS", actors=[]))
    structured_env.setattr(review_helpers, "review_wave_budget_gate", lambda _ctx, **kw: gate_calls.append(kw))
    governance, workspace = _roots(tmp_path)
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project")
    events = task_pacing.acceptance_timing_events_path(ctx.tools._ctx)
    events.parent.mkdir(parents=True, exist_ok=True)
    _timing(events, duration_sec=40, delivery="native_tool_rounds", native_rounds=6, native_rows=1)
    loop_mod._execute_task_acceptance_panel(ctx)
    (kw,) = gate_calls
    assert kw["models"] == ["openai/gpt-5.6-luna"] + ["openai/gpt-5.6-terra"] * 6
