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
                    fresh_result=True, max_improvement_passes=0, **tool_ctx_fields):
    """A root acceptance context whose wallet claim can be exercised for real."""
    from ouroboros import loop as loop_mod
    from ouroboros.contracts.task_contract import build_task_contract
    from ouroboros.review_substrate import build_review_binding
    from ouroboros.task_results import STATUS_RUNNING, write_task_result

    contract = build_task_contract({"budget_profile": {"max_improvement_passes": max_improvement_passes}})
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

    def __init__(self, drive_root, script, native_script=None, scoped=False, reservation_usd=0.0):
        self.drive_root = drive_root
        self.script = list(script)
        self.native_script = None if native_script is None else list(native_script)
        self.scoped = scoped  # True: the bound usage scope owns task/root ids and the root limit
        self.reservation_usd = float(reservation_usd)  # a PRICED send: reserved against the root wallet, then settled
        self.calls = []

    def _reply(self, kwargs):
        self.calls.append(dict(kwargs))
        script = self.native_script if ("tools" in kwargs and self.native_script is not None) else self.script
        if not script:
            raise AssertionError("script exhausted — an extra reviewer send was made")
        return script.pop(0), {"prompt_tokens": 10, "completion_tokens": 5, "cost": self.reservation_usd}

    def chat(self, **kwargs):
        from ouroboros import usage_accounting as ua

        ids = {} if self.scoped else {"task_id": "review", "root_task_id": "review"}
        # A PRICED send runs under the priced identity (the seeded catalog row), so
        # reservation and settlement follow the real wallet path; an unpriced send
        # keeps the free local identity the older ledger-shape pins expect.
        identity = ({"model": "openai/fake-reviewer", "provider": "openrouter"} if self.reservation_usd > 0
                    else {"model": "local-review-test", "provider": "local"})
        request = ua.AttemptRequest(
            reservation_usd=self.reservation_usd, drive_root=self.drive_root, **identity, **ids,
        )
        return ua.execute_physical_attempt(request, lambda: self._reply(kwargs))


def _real_panel(monkeypatch, llm, *, stub_gate=True):
    """Run the REAL substrate under the panel with a scripted LLM; capture requests.
    ``stub_gate=False`` leaves the real wave budget gate in place (it needs a
    bound usage scope and a priced model to decide anything)."""
    import ouroboros.review_substrate as rs
    from ouroboros.tools import review_helpers

    seen = []
    original = rs.run_review_request

    def _run(request, **kwargs):
        seen.append(request)
        return original(request, llm=llm, **kwargs)

    monkeypatch.setattr(rs, "run_review_request", _run)
    if stub_gate:
        monkeypatch.setattr(review_helpers, "review_wave_budget_gate", lambda *_a, **_k: None)
    return seen


def _priced_offline_model(monkeypatch):
    """Seed the PRICE SOURCE (the provider catalog cache, marked fresh) so the
    real gate can price `openai/fake-reviewer` at $1/M in, $1/M out — ≈$0.07 per
    send with the 65 536-token completion reserve. No gate code is patched."""
    import time

    from ouroboros import pricing

    monkeypatch.setitem(pricing._cached_pricing, "openrouter", {"openai/fake-reviewer": (1.0, None, None, 1.0)})
    monkeypatch.setitem(pricing._pricing_fetched_at, "openrouter", time.time())


def _root_scope(tmp_path, *, root_limit_usd):
    from ouroboros import usage_accounting as ua

    return ua.UsageScope(drive_root=tmp_path, task_id="root-delivery", root_task_id="root-delivery",
                         root_limit_usd=root_limit_usd)


def _seed_root_ledger(scope, *, cost=0.0):
    """`usage_projection(root_task_id)` derives the root limit from EXISTING
    ledger rows; before the first send there is none and the wave gate fails
    open. One scoped physical attempt makes the wallet real for the gate."""
    from ouroboros import usage_accounting as ua

    request = ua.AttemptRequest(model="openai/fake-reviewer", provider="openrouter", reservation_usd=cost)
    with ua.usage_scope(scope):
        ua.execute_physical_attempt(
            request, lambda: ({"content": "seed"}, {"prompt_tokens": 1, "completion_tokens": 1, "cost": cost}))


def _spy_admission(monkeypatch):
    """Record every real `review_wave_admission` call (models and result) and
    call through — the gate is observed, never replaced."""
    from ouroboros import usage_accounting as ua

    calls = []
    original = ua.review_wave_admission

    def _spy(drive_root, *, models, **kwargs):
        result = original(drive_root, models=models, **kwargs)
        calls.append({"models": list(models), **result})
        return result

    monkeypatch.setattr(ua, "review_wave_admission", _spy)
    return calls


def _roots(tmp_path):
    governance, workspace = tmp_path / "governance", tmp_path / "workspace"
    governance.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    (workspace / "greeting.txt").write_text("hello native reviewer\n", encoding="utf-8")
    return governance, workspace


def _tool_call(name, args, call_id="call_1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _fake_session(monkeypatch):
    """The offline Claudexor /v2 surface answering the acceptance object verdict."""
    from tests.test_review_agent_session_route import FakeGateway, _terminal_detail
    from ouroboros import claudexor_daemon
    from ouroboros import delegate_custody as custody
    from ouroboros.gateways import claudexor as gateway_module

    FakeGateway.reset()
    FakeGateway.detail = _terminal_detail(json.dumps(_CLEAN_VERDICT), conformance="passed")
    monkeypatch.setattr("ouroboros.gateways.claudexor.ClaudexorGateway", FakeGateway)
    monkeypatch.setattr(claudexor_daemon, "ensure_owned_gateway", lambda: gateway_module.ClaudexorGateway())
    custody._CUSTODY.clear()
    return FakeGateway


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
    # The executor labels the slot itself: a work order carries no `Slot:` line of its own.
    assert "Slot:" not in order["t_sess"] and "Slot:" not in order["t_actor"]
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
    from ouroboros import loop as loop_mod
    from ouroboros.acceptance_dialogue import _total_paid_acceptance_cycles
    from ouroboros.review_substrate import task_acceptance_is_clean

    FakeGateway = _fake_session(monkeypatch)
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
    # A mixed panel whose slowest class is the SESSION still teaches native rounds
    # (its native row ran); a packet-only panel (native_rows=0) teaches nothing.
    assert task_pacing.acceptance_native_rounds_estimate(ctx) == 1
    _timing(events, duration_sec=30, delivery="agent_session",
            deliveries=["api_chat", "agent_session", "native_tool_rounds"], native_rounds=6, native_rows=1)
    _timing(events, duration_sec=30, delivery="native_tool_rounds", native_rounds=6, native_rows=2)
    _timing(events, duration_sec=30, delivery="api_chat", deliveries=["api_chat"], native_rounds=0, native_rows=0)
    assert task_pacing.acceptance_native_rounds_estimate(ctx) == 5  # ewma(6, 3) = 4.5 → 5
    # One malformed durable row cannot degrade later panels; a poisoned history is capped.
    _timing(events, duration_sec=30, delivery="native_tool_rounds", native_rounds="six", native_rows=1)
    assert task_pacing.acceptance_native_rounds_estimate(ctx) == 5
    _timing(events, duration_sec=30, delivery="native_tool_rounds", native_rounds=100000, native_rows=1)
    # The bogus count enters the EWMA clamped to the cap (64), not as 100000:
    # ceil(0.5*64 + 0.5*4.5) = 35, and the result can never exceed the cap.
    assert task_pacing.acceptance_native_rounds_estimate(ctx) == 35 <= task_pacing.ACCEPTANCE_NATIVE_ROUNDS_ESTIMATE_CAP


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


def test_wave_gate_prices_a_native_row_by_the_rounds_a_real_mixed_panel_recorded(monkeypatch, tmp_path):
    """The REAL writer drives the estimator: a mixed api+session+native panel
    runs for real (its native row reads one file, then answers — two rounds),
    writes the timing event, and the NEXT panel's REAL wave gate (a seeded,
    priced wallet) decides on the floor wave and prices the rounds wave by
    those two rounds read-only (R36) although the recorded panel's slowest
    class was the session."""
    import ouroboros.review_substrate as rs
    from ouroboros import loop as loop_mod, task_pacing
    from ouroboros import usage_accounting as ua
    from ouroboros.utils import iter_jsonl_objects

    FakeGateway = _fake_session(monkeypatch)
    _offline_env(monkeypatch, _ROW_API, _ROW_SESSION, _ROW_NATIVE)
    _priced_offline_model(monkeypatch)
    admissions = _spy_admission(monkeypatch)
    governance, workspace = _roots(tmp_path)
    llm = _EpisodeLLM(
        tmp_path, [{"content": json.dumps(_CLEAN_VERDICT)}],
        native_script=[{"tool_calls": [_tool_call("read_file", {"path": "greeting.txt"})]},
                       {"content": json.dumps(_CLEAN_VERDICT)}],
        scoped=True,
    )
    _real_panel(monkeypatch, llm, stub_gate=False)
    scope = _root_scope(tmp_path, root_limit_usd=50.0)
    _seed_root_ledger(scope)
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project")
    with ua.usage_scope(scope):
        assert loop_mod._execute_task_acceptance_panel(ctx).aggregate_signal == "PASS"
    assert len(FakeGateway.instances[0].start_requests) == 1
    assert [len(a["models"]) for a in admissions] == [2]  # no history yet: the floor wave only (api + native)
    (event,) = [e for e in iter_jsonl_objects(task_pacing.acceptance_timing_events_path(ctx.tools._ctx))
                if e.get("type") == "task_acceptance_review_timing"]
    assert event["delivery"] == "agent_session"
    assert event["deliveries"] == ["api_chat", "agent_session", "native_tool_rounds"]
    assert event["native_rounds"] == 2 and event["native_rows"] == 1
    assert task_pacing.acceptance_native_rounds_estimate(ctx.tools._ctx) == 2

    del admissions[:]
    monkeypatch.setattr(
        rs, "run_review_request", lambda request, **kw: SimpleNamespace(aggregate_signal="PASS", actors=[]))
    with ua.usage_scope(scope):
        loop_mod._execute_task_acceptance_panel(ctx)
    # R36: the gate decides on the floor wave (api + native = 2 sends); the rounds-priced
    # wave — one api send + the native row at its two observed rounds — is checked read-only
    # (3 sends) with the floor wave beside it; the session row is never API money.
    assert [len(a["models"]) for a in admissions] == [2, 3, 2]
    assert all(a["fits"] and a["limit_usd"] == 50.0 and a["unpriced_slots"] == 0 for a in admissions)
    assert admissions[1]["models"] == ["openai/fake-reviewer"] * 3
    assert not any(e.get("type") == task_pacing.DISCLOSURE_DISPATCHED_AT_FLOOR for e in ctx.tools._ctx.pending_events)


def test_native_projection_never_turns_a_malformed_manifest_into_its_keys():
    from ouroboros.acceptance_dialogue import _retrieving_packet_projection

    projected = _retrieving_packet_projection({**_ACCEPTANCE_PACKET, "omissions_manifest": {"bad": "shape"}})
    assert [row["section"] for row in projected["omissions_manifest"]] == ["tool_trajectory", "artifact_previews"]
    assert "bad" not in json.dumps(projected["omissions_manifest"])
    # A well-formed manifest is extended, never replaced.
    kept = _retrieving_packet_projection({**_ACCEPTANCE_PACKET, "omissions_manifest": [{"section": "x", "reason": "y"}]})
    assert kept["omissions_manifest"][0] == {"section": "x", "reason": "y"} and len(kept["omissions_manifest"]) == 3
    # Nothing to omit: a PRESENT manifest of any non-list shape is normalized (None, dict,
    # str → []; a tuple is a sequence and is kept as a list); an absent key is not invented.
    bare = {k: v for k, v in _ACCEPTANCE_PACKET.items() if k not in ("tool_trajectory", "artifacts")}
    for malformed in (None, {"bad": "shape"}, "junk"):
        assert _retrieving_packet_projection({**bare, "omissions_manifest": malformed})["omissions_manifest"] == []
    row = {"section": "x", "reason": "y"}
    assert _retrieving_packet_projection({**bare, "omissions_manifest": (row,)})["omissions_manifest"] == [row]
    assert _retrieving_packet_projection({**_ACCEPTANCE_PACKET, "omissions_manifest": (row,)})["omissions_manifest"][0] == row
    assert "omissions_manifest" not in _retrieving_packet_projection(bare)


@pytest.mark.parametrize("tail", ["\n", "\r\n", "\n\n"])
def test_a_renderer_tail_ending_in_a_newline_still_loses_its_slot_label(structured_env, tmp_path, tail):
    """The executor labels the slot itself; the work order must carry no `Slot:`
    line even if the renderer's dynamic segment ever grows a trailing newline
    (or CRLF) after the label — the `.rstrip()` branch of the trim."""
    from ouroboros import review_execution
    from ouroboros.acceptance_dialogue import acceptance_retrieving_work_order
    from ouroboros.review_substrate import ReviewRequest, triad_delivery_slots

    original = review_execution._render_prompt_parts

    def _with_tail(request, slot):
        stable, task_stable, dynamic = original(request, slot)
        assert dynamic.endswith(f"Slot: {slot.slot_id}")  # the renderer's real tail today
        return stable, task_stable, dynamic + tail

    structured_env.setattr(review_execution, "_render_prompt_parts", _with_tail)
    request = ReviewRequest(surface="task_acceptance", goal="ship it", subject="deliverable",
                            evidence=dict(_ACCEPTANCE_PACKET), task_id="root-delivery",
                            policy={"classify_outcome_tier": True})
    retrieving = [slot for slot in triad_delivery_slots(role_hint="task acceptance") if slot.retrieves]
    assert [slot.slot_id for slot in retrieving] == ["t_sess", "t_actor"]
    acceptance_retrieving_work_order(request, retrieving, session_root=str(tmp_path), data_root=tmp_path)
    for slot_id, order in request.slot_session_tasks.items():
        assert "Slot:" not in order and not order.endswith(("\n", "\r"))
        assert "RETRIEVAL POINTERS" in order and "verification_receipts[0]" in order, slot_id


def _raw_timing(events, fields: str):
    """Append ONE raw `task_acceptance_review_timing` row. `json.dumps` cannot
    emit the token `1e999`, and a poisoned durable row is exactly what the real
    JSONL reader will hand back — so the row is written as text."""
    events.parent.mkdir(parents=True, exist_ok=True)
    with events.open("a", encoding="utf-8") as fh:
        fh.write('{"type": "task_acceptance_review_timing", "duration_sec": 30, "delivery": "native_tool_rounds", '
                 + fields + "}\n")


_POISON_TOKENS = ("1e999", '"Infinity"', "NaN", "-3", "0", "true", '"six"', "null", "[]")


@pytest.mark.parametrize("poison", _POISON_TOKENS)
def test_native_counters_that_are_not_finite_positive_numbers_are_skipped_by_the_real_reader(tmp_path, poison):
    from ouroboros import task_pacing

    ctx = SimpleNamespace(drive_root=tmp_path, task_metadata={})
    events = task_pacing.acceptance_timing_events_path(ctx)
    events.parent.mkdir(parents=True, exist_ok=True)
    _timing(events, duration_sec=30, delivery="native_tool_rounds", native_rounds=4, native_rows=2)  # honest: 2/row
    _raw_timing(events, f'"native_rounds": {poison}, "native_rows": 1')
    _raw_timing(events, f'"native_rows": {poison}, "native_rounds": 1')
    estimate = task_pacing.acceptance_native_rounds_estimate(ctx)
    assert type(estimate) is int and estimate == 2


def test_a_finite_bogus_count_is_clamped_before_the_ewma(tmp_path):
    """Only the clamp is pinned here. Whether the inflated estimate then
    refuses waves or is admitted at the floor is the open owner decision (Ф2
    item 3(i)); a hand-appended stream of honest panels would model panels
    that may never have dispatched, so no decay is asserted."""
    from ouroboros import task_pacing

    ctx = SimpleNamespace(drive_root=tmp_path, task_metadata={})
    events = task_pacing.acceptance_timing_events_path(ctx)
    events.parent.mkdir(parents=True, exist_ok=True)
    _timing(events, duration_sec=30, delivery="native_tool_rounds", native_rounds=2, native_rows=1)
    _raw_timing(events, '"native_rounds": 1e300, "native_rows": 1')  # finite, absurd: clamped to 64, not inf
    assert task_pacing.acceptance_native_rounds_estimate(ctx) == 33  # ceil(0.5*64 + 0.5*2), never above the cap


@pytest.mark.parametrize("rows", [(_ROW_API,), (_ROW_NATIVE,), (_ROW_API, _ROW_SESSION, _ROW_NATIVE)])
def test_a_poisoned_timing_row_never_refuses_or_crashes_a_later_acceptance_panel(monkeypatch, tmp_path, rows):
    """Leg (a): the rounds estimator runs for ANY paid row, before the panel's
    defensive try — so the poison must be harmless at the reader, not caught
    downstream. Packet-only, native-only and mixed panels all proceed and the
    gate receives a bounded integer multiplier."""
    from ouroboros import loop as loop_mod, task_pacing
    from ouroboros import usage_accounting as ua

    if _ROW_SESSION in rows:
        _fake_session(monkeypatch)
    _offline_env(monkeypatch, *rows)
    _priced_offline_model(monkeypatch)
    admissions = _spy_admission(monkeypatch)
    governance, workspace = _roots(tmp_path)
    llm = _EpisodeLLM(tmp_path, [{"content": json.dumps(_CLEAN_VERDICT)}] * 2, scoped=True)
    _real_panel(monkeypatch, llm, stub_gate=False)  # the REAL wave budget gate decides
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project")
    events = task_pacing.acceptance_timing_events_path(ctx.tools._ctx)
    for token in _POISON_TOKENS:
        _raw_timing(events, f'"native_rounds": {token}, "native_rows": {token}')
    _raw_timing(events, '"native_rounds": 1e999, "native_rows": 1')
    scope = _root_scope(tmp_path, root_limit_usd=50.0)
    _seed_root_ledger(scope)  # the wallet exists for the gate: it prices, it does not fail open
    with ua.usage_scope(scope):
        result = loop_mod._execute_task_acceptance_panel(ctx)
    assert result.aggregate_signal == "PASS"
    estimate = task_pacing.acceptance_native_rounds_estimate(ctx.tools._ctx)
    assert type(estimate) is int and 1 <= estimate <= 64
    (gate,) = admissions  # one real admission, numerically priced
    assert gate["limit_usd"] == 50.0 and gate["estimated_wave_usd"] > 0 and gate["unpriced_slots"] == 0
    paid_rows = sum(1 for row in rows if row is not _ROW_SESSION)
    assert paid_rows <= len(gate["models"]) <= paid_rows * 64
    assert not any(e.get("type") == "review_wave_budget_insufficient" for e in ctx.tools._ctx.pending_events)


@pytest.mark.parametrize("poison", ("1e999", '"Infinity"', "NaN", "-5", '"long"'))
def test_a_poisoned_duration_keeps_the_review_estimate_finite_and_a_finite_deadline_panel_admissible(tmp_path, poison):
    """Leg (b): the wall-clock EWMA reads the same durable rows. A non-finite or
    non-positive duration is skipped — the estimate stays finite (honest rows
    or the floor) and `review_launch_allowed` still admits a later panel with a
    finite deadline instead of silently suppressing it."""
    import math

    from ouroboros import task_pacing

    ctx = SimpleNamespace(drive_root=tmp_path, task_metadata={})
    events = task_pacing.acceptance_timing_events_path(ctx)
    events.parent.mkdir(parents=True, exist_ok=True)
    _raw_timing(events, f'"native_rows": 0, "native_rounds": 0, "duration_sec": {poison}, "delivery": "api_chat"')
    floor_only = task_pacing.acceptance_review_estimate_sec(ctx, passes_done=1, delivery="api_chat")
    assert floor_only == 200.0
    _timing(events, duration_sec=100, delivery="api_chat")
    estimate = task_pacing.acceptance_review_estimate_sec(ctx, passes_done=1, delivery="api_chat")
    assert math.isfinite(estimate) and estimate == 200.0  # 1.5 × 100 sits under the floor: the poison never entered
    snapshot = task_pacing.BudgetSnapshot(has_deadline=True, total_sec=3600.0, elapsed_sec=600.0,
                                          remaining_sec=3000.0, reserve_sec=300.0)
    assert task_pacing.review_launch_allowed(snapshot, estimated_sec=estimate) == (True, "")


@pytest.mark.parametrize("poison", ("1e12", "1e300", "1.3e308", "1.797e308"))
def test_a_finite_absurd_duration_is_bounded_to_the_task_ceiling_before_the_ewma(monkeypatch, tmp_path, poison):
    """A finite but absurd `duration_sec` contributes at most the task's absolute
    wall-clock ceiling (the one limit an honest panel cannot outlive — the
    configurable 3600 s clamp of the initial estimate is not a bound on
    observations), so the reserve is finite and at most 1.5 × that ceiling.
    Hermetic: the shell's `OUROBOROS_TASK_ABS_CEILING_SEC` is dropped and every
    number derives from the getter."""
    import math

    from ouroboros import task_pacing
    from ouroboros.config import get_task_abs_ceiling_sec

    monkeypatch.delenv("OUROBOROS_TASK_ABS_CEILING_SEC", raising=False)
    ceiling = float(get_task_abs_ceiling_sec())
    assert ceiling >= 300.0  # the setting's floor; the bound follows the getter, never a literal
    ctx = SimpleNamespace(drive_root=tmp_path, task_metadata={})
    events = task_pacing.acceptance_timing_events_path(ctx)
    events.parent.mkdir(parents=True, exist_ok=True)
    _raw_timing(events, f'"native_rows": 0, "native_rounds": 0, "duration_sec": {poison}, "delivery": "api_chat"')
    _timing(events, duration_sec=100, delivery="api_chat")
    estimate = task_pacing.acceptance_review_estimate_sec(ctx, passes_done=1, delivery="api_chat")
    assert math.isfinite(estimate) and estimate == 1.5 * (0.5 * 100 + 0.5 * ceiling) <= 1.5 * ceiling
    # An honest agent-session panel beyond an hour is a real observation, not a poison.
    session = min(4000.0, ceiling)
    _timing(events, duration_sec=session, delivery="agent_session")
    assert task_pacing.acceptance_review_estimate_sec(ctx, passes_done=1, delivery="agent_session") == 1.5 * session


def test_honest_timing_streams_are_priced_exactly_as_before(tmp_path):
    """The bounds change nothing for honest history: 300 random streams of
    durations across the whole honest range — packet seconds up to agent-session
    hours in (3600, 21600] — and rounds give byte-identical estimates to the
    plain alpha-0.5 EWMA formulas."""
    import math
    import random

    from ouroboros import task_pacing

    rng = random.Random(20260902)
    deliveries = ("api_chat", "native_tool_rounds", "agent_session")
    for i in range(300):
        ctx = SimpleNamespace(drive_root=tmp_path / f"s{i}", task_metadata={})
        events = task_pacing.acceptance_timing_events_path(ctx)
        events.parent.mkdir(parents=True, exist_ok=True)
        delivery = deliveries[i % 3]
        high = 21600.0 if delivery == "agent_session" else 3600.0
        low = 3600.0 if delivery == "agent_session" and i % 2 else 1.0
        durations = [rng.uniform(low, high) for _ in range(rng.randint(1, 6))]
        rounds = [(rng.randint(1, 64), rng.randint(1, 3)) for _ in durations]
        for duration, (per_row, rows) in zip(durations, rounds):
            _timing(events, duration_sec=duration, delivery=delivery,
                    native_rounds=per_row * rows, native_rows=rows)
        ewma_d = ewma_r = None
        for duration, (per_row, _rows) in zip(durations, rounds):
            ewma_d = duration if ewma_d is None else 0.5 * duration + 0.5 * ewma_d
            ewma_r = per_row if ewma_r is None else 0.5 * per_row + 0.5 * ewma_r
        assert task_pacing.acceptance_review_estimate_sec(
            ctx, passes_done=1, delivery=delivery) == max(200.0, 1.5 * ewma_d)
        assert task_pacing.acceptance_native_rounds_estimate(ctx) == min(64, max(1, math.ceil(ewma_r)))


@pytest.mark.parametrize("rows", [(_ROW_NATIVE,), (_ROW_API, _ROW_NATIVE)])
def test_a_poisoned_rounds_estimate_never_refuses_the_floor_priced_panel_through_the_real_gate(
        monkeypatch, tmp_path, rows):
    """R36: the rounds estimate is a pacing floor-raiser, never an admission
    ceiling. A wallet where one work-order send per paid row fits but the
    poisoned 33× wave does not → the panel DISPATCHES at the floor price through
    the REAL gate (priced: the root ledger is seeded), discloses the typed fact
    on every actor's usage, the timing row and a live event, and its honest
    timing event lands — so the NEXT estimate is lower. Without R36 the refusal
    happened before dispatch and the estimate could never decay."""
    from ouroboros import loop as loop_mod, task_pacing
    from ouroboros import usage_accounting as ua
    from ouroboros.utils import iter_jsonl_objects

    _offline_env(monkeypatch, *rows)
    _priced_offline_model(monkeypatch)
    admissions = _spy_admission(monkeypatch)
    governance, workspace = _roots(tmp_path)
    llm = _EpisodeLLM(tmp_path, [{"content": json.dumps(_CLEAN_VERDICT)}] * 4, scoped=True)
    _real_panel(monkeypatch, llm, stub_gate=False)
    scope = _root_scope(tmp_path, root_limit_usd=1.0)  # ≈14 sends fit; 33 do not
    _seed_root_ledger(scope)
    common = dict(evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance), workspace_root=str(workspace),
                  workspace_mode="project", max_improvement_passes=1)
    ctx = _acceptance_ctx(tmp_path, **common)
    with ua.usage_scope(scope):
        assert loop_mod._execute_task_acceptance_panel(ctx).aggregate_signal == "PASS"  # honest: 1 round/row
    events = task_pacing.acceptance_timing_events_path(ctx.tools._ctx)
    _raw_timing(events, '"native_rounds": 1e300, "native_rows": 1')
    poisoned = task_pacing.acceptance_native_rounds_estimate(ctx.tools._ctx)
    assert poisoned == 33  # ceil(0.5*64 + 0.5*1)

    again = _acceptance_ctx(tmp_path, content="deliverable v2", fresh_result=False, **common)
    with ua.usage_scope(scope):
        second = loop_mod._execute_task_acceptance_panel(again)
    assert second.aggregate_signal == "PASS"  # dispatched at the floor, not refused
    fact_name = task_pacing.DISCLOSURE_DISPATCHED_AT_FLOOR
    for actor in second.actors:
        fact = actor["usage"][fact_name]
        assert fact["native_rounds_estimate"] == 33 and fact["floor_slots"] == len(rows)
        assert fact["estimated_wave_usd"] > fact["remaining_usd"] >= fact["floor_wave_usd"] > 0
    # The gate itself saw the priced floor wave; the rounds-priced wave was checked read-only.
    floor_gate = [a for a in admissions if len(a["models"]) == len(rows)]
    priced_wave = [a for a in admissions if len(a["models"]) == len(rows) - 1 + 33]
    assert floor_gate and all(a["fits"] and a["limit_usd"] == 1.0 for a in floor_gate)
    assert priced_wave and not priced_wave[-1]["fits"]
    assert [e["type"] for e in again.tools._ctx.pending_events if e.get("type") == fact_name] == [fact_name]
    assert not any(e.get("type") == "review_wave_budget_insufficient" for e in again.tools._ctx.pending_events)
    timing = [e for e in iter_jsonl_objects(events) if e.get("type") == "task_acceptance_review_timing"]
    assert timing[-1][fact_name]["native_rounds_estimate"] == 33 and timing[-1]["native_rounds"] == 1
    assert task_pacing.acceptance_native_rounds_estimate(ctx.tools._ctx) == 17 < poisoned  # decayed by the honest event


def test_the_floor_priced_wave_that_does_not_fit_is_still_refused(monkeypatch, tmp_path):
    """The floor is the admission line, not a bypass: when even one send per
    paid row does not fit the remaining root budget, the panel is refused as
    before — through the real gate — and no reviewer is called."""
    from ouroboros import loop as loop_mod
    from ouroboros import usage_accounting as ua

    _offline_env(monkeypatch, _ROW_NATIVE)
    _priced_offline_model(monkeypatch)
    governance, workspace = _roots(tmp_path)
    llm = _EpisodeLLM(tmp_path, [{"content": json.dumps(_CLEAN_VERDICT)}] * 2, scoped=True)
    _real_panel(monkeypatch, llm, stub_gate=False)
    scope = _root_scope(tmp_path, root_limit_usd=0.5)
    _seed_root_ledger(scope, cost=0.45)  # $0.05 left: less than one send (~$0.07)
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project")
    with ua.usage_scope(scope):
        refused = loop_mod._execute_task_acceptance_panel(ctx)
    assert refused.aggregate_signal == "DEGRADED"
    assert any(r.startswith("review_wave_budget_insufficient") for r in refused.degraded_reasons)
    assert llm.calls == []  # no reviewer was called
    assert any(e.get("type") == "review_wave_budget_insufficient" for e in ctx.tools._ctx.pending_events)


def test_a_poisoned_reserve_still_launches_a_shorter_deadline_at_the_floor(monkeypatch, tmp_path):
    """R36 for the deadline reserve, through the real `review_launch_allowed`
    and `improvement_pass_allowed`: a bounded-but-inflated reserve (a poisoned
    duration contributes the task ceiling) launches a 24 h deadline silently;
    a spendable window shorter than the reserve but longer than the configured
    floor STILL launches, returning the typed fact for the launch owner to
    record; only a window at or below the floor is refused. Hermetic: the
    ceiling comes from the getter with the shell's override dropped."""
    from ouroboros import task_pacing
    from ouroboros.config import get_task_abs_ceiling_sec
    from ouroboros.utils import iter_jsonl_objects

    monkeypatch.delenv("OUROBOROS_TASK_ABS_CEILING_SEC", raising=False)
    ceiling = float(get_task_abs_ceiling_sec())
    ctx = SimpleNamespace(drive_root=tmp_path, task_metadata={}, task_id="root-delivery", pending_events=[])
    events = task_pacing.acceptance_timing_events_path(ctx)
    events.parent.mkdir(parents=True, exist_ok=True)
    _raw_timing(events, '"native_rows": 0, "native_rounds": 0, "duration_sec": 1e300, "delivery": "api_chat"')
    _timing(events, duration_sec=100, delivery="api_chat")
    estimate = task_pacing.acceptance_review_estimate_sec(ctx, passes_done=1, delivery="api_chat")
    assert estimate == 1.5 * (0.5 * 100 + 0.5 * ceiling) > 1000.0  # bounded, finite, inflated past the window
    day = task_pacing.BudgetSnapshot(has_deadline=True, total_sec=86400.0, elapsed_sec=0.0,
                                     remaining_sec=86400.0, reserve_sec=3600.0)
    assert task_pacing.review_launch_allowed(day, estimated_sec=estimate) == (True, "")
    short = task_pacing.BudgetSnapshot(has_deadline=True, total_sec=1200.0, elapsed_sec=100.0,
                                       remaining_sec=1100.0, reserve_sec=100.0)  # spendable 1000 s
    fact = task_pacing.DISCLOSURE_LAUNCHED_AT_FLOOR
    assert task_pacing.review_launch_allowed(short, estimated_sec=estimate) == (True, fact)
    assert task_pacing.improvement_pass_allowed(short, 0, {}, estimated_sec=estimate, ctx=ctx) == (True, fact)
    # The predicates are PURE: the fact is recorded by the launch owner (see the
    # owner/projection cardinality test), never by asking.
    assert [e for e in iter_jsonl_objects(events) if e.get("type") == fact] == [] and ctx.pending_events == []
    assert task_pacing.launch_at_floor_payload(short, gate="review_launch", estimated_sec=estimate) == {
        "gate": "review_launch", "estimated_sec": round(estimate, 3), "floor_sec": 200.0, "spendable_sec": 1000.0}
    tiny = task_pacing.BudgetSnapshot(has_deadline=True, total_sec=300.0, elapsed_sec=0.0,
                                      remaining_sec=300.0, reserve_sec=150.0)  # spendable 150 s < floor
    assert task_pacing.review_launch_allowed(tiny, estimated_sec=estimate) == (
        False, "review_skipped_deadline_reserve")
    assert task_pacing.improvement_pass_allowed(tiny, 0, {}, estimated_sec=estimate, ctx=ctx) == (
        False, "improvement_window_inside_reserve")


# ---------------------------------------------------------------------------
# R36 facts: bound to the launch owner and the paid seam, never to the helpers.
# ---------------------------------------------------------------------------


def _supervised(tmp_path):
    """A worker context with the REAL event queue plus the supervisor context
    that drains it through `dispatch_event`: what production does with a live
    review event."""
    import queue

    from ouroboros.utils import append_jsonl

    return queue.Queue(), SimpleNamespace(DRIVE_ROOT=tmp_path, append_jsonl=append_jsonl)


def _drain_to_supervisor(event_queue, sup_ctx):
    from supervisor.events import dispatch_event

    drained = []
    while not event_queue.empty():
        evt = event_queue.get_nowait()
        drained.append(evt)
        dispatch_event(json.loads(json.dumps(evt)), sup_ctx)
    return drained


def _rows(events_path, event_type):
    from ouroboros.utils import iter_jsonl_objects

    return [e for e in iter_jsonl_objects(events_path) if e.get("type") == event_type]


def test_floor_launch_fact_is_recorded_once_by_the_owner_and_never_by_predicates_or_the_projection(
        monkeypatch, tmp_path):
    """Item 1 (the class): the predicates are pure and the read-only capacity
    projection observes without disclosing — polled twice inside the floor band
    they write ZERO rows/events — while the launch owner records exactly ONE
    live event that the REAL supervisor path persists as exactly ONE row."""
    from ouroboros import task_pacing
    from ouroboros.task_results import project_task_acceptance_review_capacity

    monkeypatch.delenv("OUROBOROS_TASK_ABS_CEILING_SEC", raising=False)
    _offline_env(monkeypatch, _ROW_API)
    event_queue, sup_ctx = _supervised(tmp_path)
    governance, workspace = _roots(tmp_path)
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project", event_queue=event_queue)
    tools_ctx = ctx.tools._ctx
    events = task_pacing.acceptance_timing_events_path(tools_ctx)
    _raw_timing(events, '"native_rows": 0, "native_rounds": 0, "duration_sec": 1e300, "delivery": "api_chat"')
    short = task_pacing.BudgetSnapshot(has_deadline=True, total_sec=1200.0, elapsed_sec=100.0,
                                       remaining_sec=1100.0, reserve_sec=100.0)  # spendable 1000 s: floor band
    monkeypatch.setattr(task_pacing, "build_budget_snapshot", lambda _ctx, profile=None: short)
    estimate = task_pacing.acceptance_review_estimate_sec(tools_ctx, passes_done=1, delivery="api_chat")
    fact = task_pacing.DISCLOSURE_LAUNCHED_AT_FLOOR
    assert estimate > 1000.0 > 200.0

    # Predicates: pure, whatever they are asked.
    for _ in range(3):
        assert task_pacing.review_launch_allowed(short, estimated_sec=estimate) == (True, fact)
        assert task_pacing.improvement_pass_allowed(short, 0, {}, estimated_sec=estimate, ctx=tools_ctx) == (True, fact)
    # The projection: observes the floor band, discloses nothing, carries the case.
    # (It reserves the floor while nothing was claimed yet, so the poisoned
    # estimate is pinned to put its poll inside the band.)
    monkeypatch.setattr(task_pacing, "acceptance_review_estimate_sec", lambda *_a, **_k: estimate)
    for _ in range(2):
        projection = project_task_acceptance_review_capacity(tools_ctx, task_id="root-delivery")
        assert projection["launch_disclosure"] == fact and projection["state"] != "unavailable"
    assert event_queue.empty() and _rows(events, fact) == []
    # The owner: exactly one live event, exactly one canonical row via the REAL path.
    payload = task_pacing.record_launch_at_floor(tools_ctx, short, estimated_sec=estimate)
    drained = _drain_to_supervisor(event_queue, sup_ctx)
    assert [e["type"] for e in drained] == [fact]
    (row,) = _rows(events, fact)
    assert row["gate"] == "review_launch" and row["spendable_sec"] == 1000.0 and row["floor_sec"] == 200.0
    assert row["estimated_sec"] == payload["estimated_sec"] == round(estimate, 3)
    assert row["task_id"] == "root-delivery"
    # The adaptive improvement window scales the whole payload.
    adaptive = task_pacing.launch_at_floor_payload(short, gate="improvement_pass", estimated_sec=estimate,
                                                   profile={"improvement_policy": "adaptive"})
    assert adaptive["floor_sec"] == 400.0 and adaptive["estimated_sec"] == round(estimate * 2, 3)


def test_the_launch_owners_are_exactly_loop_and_the_improvement_gate():
    """A source-level pin of item 1's ownership: the recorder is called by the
    review launch in loop.py and the improvement-pass gate in
    acceptance_dialogue.py — once each — and never by the projection."""
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[1]
    counts = {name: (repo / "ouroboros" / name).read_text(encoding="utf-8").count("record_launch_at_floor(")
              for name in ("loop.py", "acceptance_dialogue.py", "task_results.py", "task_pacing.py")}
    assert counts == {"loop.py": 1, "acceptance_dialogue.py": 1, "task_results.py": 0, "task_pacing.py": 1}


def _floor_band_native_panel(monkeypatch, tmp_path, *rows):
    """A native (or api+native) triad on a seeded $1 wallet with a poisoned
    rounds estimate (33): the floor wave fits, the rounds-priced wave does not."""
    from ouroboros import loop as loop_mod, task_pacing
    from ouroboros import usage_accounting as ua

    _offline_env(monkeypatch, *rows)
    _priced_offline_model(monkeypatch)
    governance, workspace = _roots(tmp_path)
    llm = _EpisodeLLM(tmp_path, [{"content": json.dumps(_CLEAN_VERDICT)}] * 6, scoped=True)
    _real_panel(monkeypatch, llm, stub_gate=False)
    scope = _root_scope(tmp_path, root_limit_usd=1.0)
    _seed_root_ledger(scope)
    event_queue, sup_ctx = _supervised(tmp_path)
    common = dict(evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance), workspace_root=str(workspace),
                  workspace_mode="project", max_improvement_passes=2, event_queue=event_queue)
    first = _acceptance_ctx(tmp_path, **common)
    with ua.usage_scope(scope):
        assert loop_mod._execute_task_acceptance_panel(first).aggregate_signal == "PASS"  # honest: 1 round/row
    events = task_pacing.acceptance_timing_events_path(first.tools._ctx)
    _raw_timing(events, '"native_rounds": 1e300, "native_rows": 1')
    assert task_pacing.acceptance_native_rounds_estimate(first.tools._ctx) == 33
    _drain_to_supervisor(event_queue, sup_ctx)  # the honest panel emitted nothing of the fact type
    assert _rows(events, task_pacing.DISCLOSURE_DISPATCHED_AT_FLOOR) == []
    return SimpleNamespace(loop_mod=loop_mod, task_pacing=task_pacing, ua=ua, scope=scope, llm=llm,
                           events=events, event_queue=event_queue, sup_ctx=sup_ctx, common=common, first=first)


def test_dispatched_at_floor_fact_lands_once_through_the_real_supervisor_path(monkeypatch, tmp_path):
    """Item 2/8 positive leg: a real floor dispatch → exactly ONE registered
    live event, dispatched by the supervisor into exactly ONE canonical row —
    beside the actor-usage attachment and the timing-row field."""
    monkeypatch.delenv("OUROBOROS_TASK_ABS_CEILING_SEC", raising=False)
    s = _floor_band_native_panel(monkeypatch, tmp_path, _ROW_NATIVE)
    fact = s.task_pacing.DISCLOSURE_DISPATCHED_AT_FLOOR
    again = _acceptance_ctx(tmp_path, content="deliverable v2", fresh_result=False, **s.common)
    with s.ua.usage_scope(s.scope):
        result = s.loop_mod._execute_task_acceptance_panel(again)
    assert result.aggregate_signal == "PASS"
    assert all(actor["usage"][fact]["native_rounds_estimate"] == 33 for actor in result.actors)
    drained = _drain_to_supervisor(s.event_queue, s.sup_ctx)
    assert [e["type"] for e in drained if e.get("type") == fact] == [fact]
    (row,) = _rows(s.events, fact)
    assert row["native_rounds_estimate"] == 33 and row["floor_slots"] == 1 and row["surface"] == "task_acceptance"
    assert row["estimated_wave_usd"] > row["remaining_usd"] >= row["floor_wave_usd"] > 0
    timing = _rows(s.events, "task_acceptance_review_timing")
    assert timing[-1][fact]["native_rounds_estimate"] == 33  # the timing-row carrier, once
    assert s.task_pacing.acceptance_native_rounds_estimate(again.tools._ctx) == 17


def test_a_refused_panel_in_the_floor_band_leaves_no_dispatched_at_floor_fact(monkeypatch, tmp_path):
    """Item 2 negative legs, all in the floor band (the prospective fact IS
    computed): an already-claimed binding retried (preclaim refusal), a
    zero-physical refusal (immutable-core overflow), a wallet-stamp refusal at
    the paid seam, and a panel whose stamp never fired (every row refused
    before its send) — none emits, attaches or persists the fact."""
    from ouroboros import review_dispatch

    monkeypatch.delenv("OUROBOROS_TASK_ABS_CEILING_SEC", raising=False)
    s = _floor_band_native_panel(monkeypatch, tmp_path, _ROW_NATIVE)
    fact = s.task_pacing.DISCLOSURE_DISPATCHED_AT_FLOOR
    sends = len(s.llm.calls)

    def _no_fact(result):
        assert result.aggregate_signal == "DEGRADED"
        drained = _drain_to_supervisor(s.event_queue, s.sup_ctx)
        assert not any(e.get("type") == fact for e in drained)
        assert _rows(s.events, fact) == []
        assert not any(fact in row for row in _rows(s.events, "task_acceptance_review_timing"))
        assert len(s.llm.calls) == sends  # no reviewer was called

    # (a) the SAME binding again: the wallet already claimed it → preclaim refusal.
    with s.ua.usage_scope(s.scope):
        _no_fact(s.loop_mod._execute_task_acceptance_panel(s.first))
    # (b) zero-physical refusal: the immutable core overflowed → refused before any send.
    overflow = _acceptance_ctx(tmp_path, content="deliverable v2", fresh_result=False,
                               **{**s.common, "evidence": {**_ACCEPTANCE_PACKET, "__immutable_core_overflow__": True}})
    with s.ua.usage_scope(s.scope):
        _no_fact(s.loop_mod._execute_task_acceptance_panel(overflow))
    # (c) the wallet stamp refuses at the paid seam.
    import contextlib

    @contextlib.contextmanager
    def _veto(ctx):
        raise review_dispatch.TaskAcceptanceDispatchUnavailable("wallet_veto_for_test")
        yield  # pragma: no cover

    vetoed = _acceptance_ctx(tmp_path, content="deliverable v3", fresh_result=False, **s.common)
    with monkeypatch.context() as m:
        m.setattr(review_dispatch, "bind_task_acceptance_paid_dispatch", _veto)
        with s.ua.usage_scope(s.scope):
            _no_fact(s.loop_mod._execute_task_acceptance_panel(vetoed))
    # (d) the stamp never fires: every row refused before its send (route unavailable) —
    #     the substrate returns undispatched actors and the paid seam was never crossed.
    import ouroboros.review_substrate as rs
    from ouroboros.review_substrate import ReviewRunResult

    unrouted = _acceptance_ctx(tmp_path, content="deliverable v4", fresh_result=False, **s.common)
    with monkeypatch.context() as m:
        m.setattr(rs, "run_review_request", lambda request, **kw: ReviewRunResult(
            request={"surface": "task_acceptance"}, actors=[{"slot_id": "t_actor", "status": "error",
            "operation_state": "not_dispatched", "usage": {}}], parsed_findings=[], aggregate_signal="DEGRADED",
            degraded=True, degraded_reasons=["route_unavailable"]))
        with s.ua.usage_scope(s.scope):
            result = s.loop_mod._execute_task_acceptance_panel(unrouted)
    assert result.aggregate_signal == "DEGRADED" and fact not in result.actors[0]["usage"]
    assert not any(e.get("type") == fact for e in _drain_to_supervisor(s.event_queue, s.sup_ctx))
    assert _rows(s.events, fact) == [] and len(s.llm.calls) == sends


def test_the_per_send_wallet_fence_still_binds_after_a_floor_admitted_dispatch(monkeypatch, tmp_path):
    """Item 11: "the per-send wallet binding at dispatch still protects money" —
    proven with PRICED sends. On a nearly spent wallet the floor wave is
    admitted (one send fits), the native episode's first send is reserved and
    settled, its second send is refused by the ledger (`budget_exhausted`),
    and the accounted total never exceeds the root limit."""
    from ouroboros import loop as loop_mod, task_pacing
    from ouroboros import usage_accounting as ua

    monkeypatch.delenv("OUROBOROS_TASK_ABS_CEILING_SEC", raising=False)
    _offline_env(monkeypatch, _ROW_NATIVE)
    _priced_offline_model(monkeypatch)
    governance, workspace = _roots(tmp_path)
    llm = _EpisodeLLM(
        tmp_path, [], scoped=True, reservation_usd=0.06,
        native_script=[{"tool_calls": [_tool_call("read_file", {"path": "greeting.txt"})]},
                       {"content": json.dumps(_CLEAN_VERDICT)}],
    )
    _real_panel(monkeypatch, llm, stub_gate=False)
    limit = 0.1  # one priced send (0.06) fits; the second (0.12 cumulative) does not
    scope = _root_scope(tmp_path, root_limit_usd=limit)
    _seed_root_ledger(scope)
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project")
    events = task_pacing.acceptance_timing_events_path(ctx.tools._ctx)
    _raw_timing(events, '"native_rounds": 1e300, "native_rows": 1')  # the rounds wave (64×) can never fit: floor band
    with ua.usage_scope(scope):
        result = loop_mod._execute_task_acceptance_panel(ctx)
    (actor,) = result.actors
    assert task_pacing.DISCLOSURE_DISPATCHED_AT_FLOOR in actor["usage"]  # admitted and dispatched at the floor
    assert len(llm.calls) == 1  # the first send went out; the second never reached the model
    assert actor["usage"]["native_end_reason"] == "budget_exhausted"
    assert result.aggregate_signal == "DEGRADED"
    projection = ua.usage_projection(tmp_path, root_task_id="root-delivery")
    spent = float(projection["limit_usd"]) - float(projection["remaining_known_usd"])
    assert projection["limit_usd"] == limit and 0.06 <= spent <= limit


# ---------------------------------------------------------------------------
# The one-time R12 migration disclosure at save time.
# ---------------------------------------------------------------------------


def test_the_save_that_first_makes_the_triad_retrieve_discloses_once_with_numbers(monkeypatch, tmp_path):
    """R12: an owner whose triad gains a retrieving row hears ONCE, with the
    measured numbers, that every substantive task's acceptance panel now runs
    on it; keeping that triad on later saves discloses nothing again, and a
    packet-only triad never did."""
    from tests.test_settings_honesty import _save
    from ouroboros import config as cfg

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings_path = data_dir / "settings.json"
    monkeypatch.setattr(cfg, "DATA_DIR", data_dir, raising=True)
    monkeypatch.setattr(cfg, "SETTINGS_PATH", settings_path, raising=True)
    cfg.reset_runtime_mode_baseline_for_tests()
    try:
        monkeypatch.setenv("OUROBOROS_SUBAGENTS", json.dumps(_ROSTER))
        packet_only = json.dumps({**_TRIAD, "triad": [_TRIAD["triad"][0]]})
        assert _save(monkeypatch, settings_path, {REVIEWER_SLOTS_ENV: packet_only}).get("warnings") in (None, [])
        # The transition save: legacy/packet-only → a triad with a session and a native row.
        data = _save(monkeypatch, settings_path, {REVIEWER_SLOTS_ENV: json.dumps(_TRIAD)})
        (disclosure,) = [w for w in data.get("warnings") or [] if "Task acceptance now follows" in w]
        assert "t_sess (agent session codex=gpt-5.6-sol" in disclosure
        assert "t_actor (native inspection via api-critic → openai/gpt-5.6-terra)" in disclosure
        assert "≈12 s and ≈$0.07 per model row per task" in disclosure and "≈75 s / ≈$0.82" in disclosure
        assert "minutes of your subscription window" in disclosure and "keeps a packet panel" in disclosure
        # Saving the same retrieving triad again is silent — the notice is one-time.
        data = _save(monkeypatch, settings_path, {REVIEWER_SLOTS_ENV: json.dumps(_TRIAD)})
        assert not [w for w in data.get("warnings") or [] if "Task acceptance now follows" in w]
        # A roster-only save keeps the stored triad: still silent.
        data = _save(monkeypatch, settings_path, {"OUROBOROS_SUBAGENTS": json.dumps(_ROSTER)})
        assert not [w for w in data.get("warnings") or [] if "Task acceptance now follows" in w]
    finally:
        cfg.reset_runtime_mode_baseline_for_tests()
