"""Floor admission of the acceptance panel (owner R36 → R46 → R47): the real
wave gate and the real deadline reserve never refuse what the floor admits,
and the ONE typed disclosure fact — emitted by the panel at the paid seam,
after its dispatch fired — carries both the money and the time admission.

A separate module from the three-delivery contract suite on purpose: the
subject here is observability of a pacing decision (what is recorded, by whom,
exactly once, through the REAL supervisor event path), not what a delivery
row receives or how the panel is priced. Every offline fixture — the fake
triads, the scripted ledger-crossing reviewer, the seeded wallet, the priced
catalog row, the fake session gateway — is the delivery suite's, imported by
name; nothing here is a copy.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.test_acceptance_delivery import (
    _ACCEPTANCE_PACKET,
    _CLEAN_VERDICT,
    _ROW_API,
    _ROW_NATIVE,
    _EpisodeLLM,
    _acceptance_ctx,
    _offline_env,
    _priced_offline_model,
    _raw_timing,
    _real_panel,
    _root_scope,
    _roots,
    _seed_root_ledger,
    _spy_admission,
    _timing,
    _tool_call,
)
from ouroboros.reviewer_slot_config import REVIEWER_SLOTS_ENV


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
    reason = task_pacing.REASON_LAUNCHED_AT_FLOOR
    assert task_pacing.review_launch_allowed(short, estimated_sec=estimate) == (True, reason)
    assert task_pacing.improvement_pass_allowed(short, 0, {}, estimated_sec=estimate, ctx=ctx) == (True, reason)
    # The predicates are PURE: a floor launch is disclosed on the panel's dispatch
    # fact after the paid seam fires (see the real-path tests), never by asking.
    assert [e for e in iter_jsonl_objects(events) if e.get("type") != "task_acceptance_review_timing"] == []
    assert ctx.pending_events == []
    assert task_pacing.launch_at_floor_payload(short, estimated_sec=estimate) == {
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


def test_floor_launch_predicates_and_the_projection_record_nothing(monkeypatch, tmp_path):
    """R46: the launch predicates are pure and the read-only capacity projection
    observes without disclosing — asked repeatedly inside the floor band they
    write ZERO rows/events; the projection carries the decision as
    `launch_disclosure`. The only emitter is the panel's paid seam (below)."""
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
    monkeypatch.setattr(task_pacing, "observe_budget_snapshot", lambda _ctx, profile=None: short)
    estimate = task_pacing.acceptance_review_estimate_sec(tools_ctx, passes_done=1, delivery="api_chat")
    reason = task_pacing.REASON_LAUNCHED_AT_FLOOR
    assert estimate > 1000.0 > 200.0
    for _ in range(3):
        assert task_pacing.review_launch_allowed(short, estimated_sec=estimate) == (True, reason)
        assert task_pacing.improvement_pass_allowed(short, 0, {}, estimated_sec=estimate, ctx=tools_ctx) == (True, reason)
    monkeypatch.setattr(task_pacing, "acceptance_review_estimate_sec", lambda *_a, **_k: estimate)
    for _ in range(2):
        projection = project_task_acceptance_review_capacity(tools_ctx, task_id="root-delivery")
        assert projection["launch_disclosure"] == reason and projection["state"] != "unavailable"
    assert event_queue.empty()
    assert _rows(events, task_pacing.DISCLOSURE_DISPATCHED_AT_FLOOR) == []
    assert not hasattr(task_pacing, "record_launch_at_floor") and not hasattr(task_pacing, "DISCLOSURE_LAUNCHED_AT_FLOOR")
    assert task_pacing.launch_at_floor_payload(short, estimated_sec=estimate) == {
        "gate": "review_launch", "estimated_sec": round(estimate, 3), "floor_sec": 200.0, "spendable_sec": 1000.0}


def _floor_band_ctx(
    monkeypatch, tmp_path, *, seconds_left, claims=1, max_improvement_passes=None,
    policy="adaptive",
):
    """A REAL root task in the deadline band the projection prices: the packet
    triad, the 200 s configured floor, the 120 s grace as the WHOLE reserve
    (pct 0), `claims` really claimed cycles, and one 200 s timing row of that
    panel's own class (EWMA 200 → estimate 300). Nothing is patched — the
    projection reads the same contract, wallet state and timing history
    production reads. `max_improvement_passes` defaults to NONE so the shipped
    configuration (2 review cycles → 1 improvement pass) is what these
    regressions exercise; a task-local cap would mask the count axis."""
    import queue
    from datetime import timedelta

    from ouroboros import task_pacing
    from ouroboros.contracts.task_contract import build_task_contract
    from ouroboros.deadline_utils import utc_now
    from ouroboros.review_substrate import build_review_binding
    from ouroboros.task_results import (
        STATUS_RUNNING,
        claim_task_acceptance_review_cycle,
        write_task_result,
    )

    _offline_env(monkeypatch, _ROW_API)  # a packet panel: the timing row below is its class
    monkeypatch.setenv("OUROBOROS_ACCEPTANCE_REVIEW_EST_SEC", "200")  # the floor
    monkeypatch.setenv("OUROBOROS_FINALIZATION_GRACE_SEC", "120")  # the whole reserve (pct 0)
    monkeypatch.delenv("OUROBOROS_TASK_ABS_CEILING_SEC", raising=False)
    now = utc_now()
    profile = {"improvement_policy": policy, "reserve_finalization_pct": 0}
    if max_improvement_passes is not None:
        profile["max_improvement_passes"] = max_improvement_passes
    contract = build_task_contract({
        "deadline_at": (now + timedelta(seconds=seconds_left)).isoformat(),
        "budget_profile": profile,
    })
    metadata = {
        "root_task_id": "root-floor-band", "delegation_role": "root",
        "budget_drive_root": str(tmp_path), "task_contract": contract,
        "created_at": (now - timedelta(seconds=60)).isoformat(),
        "deadline_at": contract["deadline_at"],
    }
    ctx = SimpleNamespace(
        task_id="root-floor-band", root_task_id="root-floor-band", drive_root=tmp_path,
        budget_drive_root=str(tmp_path), task_contract=contract, task_metadata=metadata,
        pending_events=[], event_queue=queue.Queue(),
    )
    write_task_result(tmp_path, "root-floor-band", STATUS_RUNNING, root_task_id="root-floor-band",
                      delegation_role="root", task_contract=contract)
    for index in range(claims):
        claim = claim_task_acceptance_review_cycle(
            tmp_path, "root-floor-band",
            build_review_binding(candidate=f"deliverable v{index}", evidence=dict(_ACCEPTANCE_PACKET),
                                 fence_token_or_state="floor-band"),
            claimed_by_task_id="root-floor-band",
        )
        assert claim["status"] == "claimed"  # REAL paid cycles: the projection prices the next pass
    events = task_pacing.acceptance_timing_events_path(ctx)
    events.parent.mkdir(parents=True, exist_ok=True)
    _timing(events, duration_sec=200, delivery="api_chat")  # EWMA 200 -> 1.5x -> estimate 300
    return ctx, events


def test_the_projection_reports_the_improvement_floor_admission_of_the_2x_window(monkeypatch, tmp_path):
    """The DIVERGENCE band (owner R49): with an adaptive profile and
    `floor < estimate < spendable <= 2 x estimate` — 200 < 300 < 500 <= 600, and
    500 > 2 x 200 — the improvement gate admits AT THE FLOOR through its 2x
    window while the review-launch gate clears its 1x window outright. The
    projection evaluates BOTH purely and reports each admission in its OWN
    field: the improvement admission arrives as
    `improvement_launch_disclosure`, `launch_disclosure` stays absent, and the
    improvement gate never touches availability. THE BOUNDARY CASE: the shipped
    configuration (2 review cycles → 1 improvement pass), ONE paid claim and
    ZERO completed improvement passes — the count the real gate sees. Every
    number comes from the real readers and builders (the timing EWMA, the
    deadline snapshot, one really claimed cycle); no predicate is patched."""
    from ouroboros import task_pacing
    from ouroboros.review_cycles import acceptance_max_improvement_passes_from_cycles
    from ouroboros.task_results import project_task_acceptance_review_capacity
    from ouroboros.utils import iter_jsonl_objects

    ctx, events = _floor_band_ctx(monkeypatch, tmp_path, seconds_left=620)
    profile = task_pacing.resolve_budget_profile(ctx)
    estimate = task_pacing.acceptance_review_estimate_sec(ctx, passes_done=1)
    snapshot = task_pacing.build_budget_snapshot(ctx, profile=profile)
    assert profile["improvement_policy"] == "adaptive" and estimate == 300.0
    assert snapshot.reserve_sec == 120.0 and 400.0 < snapshot.spendable_sec <= 600.0
    assert 200.0 < estimate < snapshot.spendable_sec  # floor 200 < estimate < spendable <= 2x
    assert snapshot.spendable_sec > 2 * 200.0  # and above the 2x FLOOR window
    # The shipped default is the whole point: one improvement pass, no local cap.
    assert profile["max_improvement_passes"] is None
    assert acceptance_max_improvement_passes_from_cycles() == 1

    reason = task_pacing.REASON_LAUNCHED_AT_FLOOR
    # ONE paid panel means ZERO completed improvement passes — what production's
    # `ctx.passes_done` holds when the real gate runs after the first panel.
    assert task_pacing.improvement_pass_allowed(
        snapshot, 0, profile, estimated_sec=estimate, ctx=None) == (True, reason)
    assert task_pacing.review_launch_allowed(snapshot, estimated_sec=estimate) == (True, "")

    before = list(iter_jsonl_objects(events))
    projection = project_task_acceptance_review_capacity(ctx, task_id="root-floor-band")
    assert projection["state"] == "available" and projection["reason"] == ""
    assert projection["claimed_cycles"] == 1 and projection["remaining_cycles"] == 1
    # The projection prices the SAME 300 s estimate for both gates.
    assert task_pacing.acceptance_review_estimate_sec(
        ctx, passes_done=projection["claimed_cycles"]) == estimate
    assert projection["improvement_launch_disclosure"] == reason
    assert "launch_disclosure" not in projection  # the 1x window is cleared outright
    assert ctx.pending_events == [] and ctx.event_queue.empty()  # pure: nothing recorded
    assert list(iter_jsonl_objects(events)) == before


def test_the_projection_reports_the_review_launch_floor_admission_alone_below_the_estimate(
        monkeypatch, tmp_path):
    """The OTHER band: `floor < spendable <= estimate` — 200 < 250 <= 300 —
    reverses the pair. The review-launch gate admits at the floor
    (`launch_disclosure`), the improvement gate refuses inside its 2x window
    (250 <= 2 x 200), so no `improvement_launch_disclosure` is set — and that
    refusal does NOT make the projection unavailable: availability stays the
    review-launch gate's answer."""
    from ouroboros import task_pacing
    from ouroboros.task_results import project_task_acceptance_review_capacity
    from ouroboros.utils import iter_jsonl_objects

    ctx, events = _floor_band_ctx(monkeypatch, tmp_path, seconds_left=370)
    profile = task_pacing.resolve_budget_profile(ctx)
    estimate = task_pacing.acceptance_review_estimate_sec(ctx, passes_done=1)
    snapshot = task_pacing.build_budget_snapshot(ctx, profile=profile)
    assert estimate == 300.0 and 200.0 < snapshot.spendable_sec <= estimate  # floor < spendable <= 1x

    reason = task_pacing.REASON_LAUNCHED_AT_FLOOR
    assert task_pacing.review_launch_allowed(snapshot, estimated_sec=estimate) == (True, reason)
    assert task_pacing.improvement_pass_allowed(
        snapshot, 0, profile, estimated_sec=estimate, ctx=None) == (
        False, "improvement_window_inside_reserve")

    before = list(iter_jsonl_objects(events))
    projection = project_task_acceptance_review_capacity(ctx, task_id="root-floor-band")
    assert projection["launch_disclosure"] == reason
    assert "improvement_launch_disclosure" not in projection
    assert projection["state"] == "available" and projection["reason"] == ""
    assert projection["claimed_cycles"] == 1 and projection["remaining_cycles"] == 1
    assert ctx.pending_events == [] and ctx.event_queue.empty()
    assert list(iter_jsonl_objects(events)) == before


def test_projecting_an_exhausted_cap_emits_no_review_cycles_exhausted_event(monkeypatch, tmp_path):
    """`ctx=None` is the whole point: the improvement gate EMITS the typed
    `review_cycles_exhausted` escalation when a ctx is supplied and the SHARED
    cap is exhausted under Required+Blocking — so a read-only projection that
    every poll calls must hand it none. With the shared cap at 2 cycles and 2
    cycles really claimed, projecting stays silent (the projection's own
    `review_cycles_exhausted` state is its existing remaining==0 semantics, not
    an event), while the same predicate called WITH this ctx does emit — the
    control that keeps this assertion from passing vacuously."""
    from ouroboros import task_pacing
    from ouroboros.outcomes import REASON_REVIEW_CYCLES_EXHAUSTED
    from ouroboros.task_results import project_task_acceptance_review_capacity

    monkeypatch.setenv("OUROBOROS_TASK_REVIEW_MODE", "required")
    monkeypatch.setenv("OUROBOROS_REVIEW_ENFORCEMENT", "blocking")
    monkeypatch.setenv("OUROBOROS_REVIEW_MAX_CYCLES", "2")  # 2 cycles => 1 improvement pass
    ctx, events = _floor_band_ctx(monkeypatch, tmp_path, seconds_left=620, claims=2)
    profile = task_pacing.resolve_budget_profile(ctx)
    estimate = task_pacing.acceptance_review_estimate_sec(ctx, passes_done=2)
    snapshot = task_pacing.build_budget_snapshot(ctx, profile=profile)
    assert profile["max_improvement_passes"] is None and estimate == 300.0

    projection = project_task_acceptance_review_capacity(ctx, task_id="root-floor-band")
    assert projection["cap_cycles"] == 2 and projection["claimed_cycles"] == 2
    assert projection["state"] == "unavailable"  # remaining == 0: the projection's own semantics
    assert projection["reason"] == REASON_REVIEW_CYCLES_EXHAUSTED
    assert "improvement_launch_disclosure" not in projection  # the count cap refuses first
    assert ctx.pending_events == [] and ctx.event_queue.empty()
    assert _rows(events, REASON_REVIEW_CYCLES_EXHAUSTED) == []  # ctx=None emitted nothing

    # Control: the SAME inputs with a ctx do emit — same derived counter
    # (2 paid cycles → 1 completed improvement pass) and same enforcement.
    assert task_pacing.improvement_pass_allowed(
        snapshot, 1, profile, required_blocking=True, estimated_sec=estimate, ctx=ctx) == (
        False, REASON_REVIEW_CYCLES_EXHAUSTED)
    assert len(_rows(events, REASON_REVIEW_CYCLES_EXHAUSTED)) == 1


def test_an_armed_owner_hurry_latch_removes_the_projected_improvement_pass(monkeypatch, tmp_path):
    """The projection resolves the EFFECTIVE profile the real acceptance path
    resolves (`owner_hurry.effective_budget_profile` over the raw one, exactly
    as `loop._run_task_acceptance_review_once` does). An armed hurry latch
    overlays `max_improvement_passes=0`, so the real gate refuses at zero
    completed passes and the projection reports NO improvement field — in the
    very band that discloses one without the latch (the test above). Reading the
    RAW profile would promise a pass the real gate refuses."""
    from ouroboros import owner_hurry, task_pacing
    from ouroboros.task_results import project_task_acceptance_review_capacity

    ctx, _events = _floor_band_ctx(monkeypatch, tmp_path, seconds_left=620)
    assert owner_hurry.latch_from_entry(ctx, {"msg_id": "m1", "ts": "2026-09-03T07:00:00Z"})
    profile = owner_hurry.effective_budget_profile(ctx, task_pacing.resolve_budget_profile(ctx))
    snapshot = task_pacing.build_budget_snapshot(ctx, profile=profile)
    assert profile["max_improvement_passes"] == 0
    assert task_pacing.improvement_pass_allowed(
        snapshot, 0, profile, estimated_sec=300.0, ctx=None) == (
        False, "improvement_passes_exhausted")

    projection = project_task_acceptance_review_capacity(ctx, task_id="root-floor-band")
    assert projection["state"] == "available"  # the PAID panel itself stays admissible
    assert projection["claimed_cycles"] == 1 and projection["remaining_cycles"] == 1
    assert "improvement_launch_disclosure" not in projection
    assert "improvement_admission" not in projection  # refused, not unavailable


def test_polling_a_legacy_alias_metadata_poor_task_writes_and_latches_nothing(monkeypatch, tmp_path):
    """PURITY, pinned byte-for-byte (owner R49). The two writes reachable from
    the observed readers are the `deprecated_task_pacing_alias` row
    (`resolve_budget_profile`) and the fallback-anchor latch
    (`build_budget_snapshot`); a task carrying the legacy `until_deadline` alias
    AND no `created_at`/`started_at` arms both. Polling the projection must
    leave the events stream, the result/ledger file and the event queue
    byte-identical and touch no ctx attribute — while still answering. Without a
    usable window it makes no floor claim; `state` still derives from the wallet
    and the count axis. The controls fire both writes from the OWNING path, so
    these assertions cannot pass vacuously."""
    from ouroboros import task_pacing
    from ouroboros.task_results import project_task_acceptance_review_capacity, task_result_path

    ctx, events = _floor_band_ctx(
        monkeypatch, tmp_path, seconds_left=620, policy="until_deadline")
    del ctx.task_metadata["created_at"]  # metadata-poor: no anchor without a WRITE
    result_file = task_result_path(tmp_path, "root-floor-band", create=False)
    before = (events.read_bytes(), result_file.read_bytes())

    for _ in range(2):
        projection = project_task_acceptance_review_capacity(ctx, task_id="root-floor-band")
        assert projection["state"] == "available" and projection["claimed_cycles"] == 1
        assert "launch_disclosure" not in projection  # no window facts -> no floor claim
        assert "improvement_launch_disclosure" not in projection
        assert "improvement_admission" not in projection  # answered, not omitted
    assert (events.read_bytes(), result_file.read_bytes()) == before
    assert ctx.event_queue.empty() and ctx.pending_events == []
    assert not hasattr(ctx, "_time_budget_started_at")
    assert not hasattr(ctx, "_acceptance_pacing_deprecation_emitted")
    assert task_pacing.observe_budget_snapshot(
        ctx, profile=task_pacing.observe_budget_profile(ctx)).has_deadline is False

    # Controls: the OWNING path still emits the row and latches the anchor from
    # this same context — both writes are genuinely reachable here.
    assert task_pacing.resolve_budget_profile(ctx)["improvement_policy"] == "until_deadline"
    assert ctx._acceptance_pacing_deprecation_emitted is True
    assert len(_rows(events, "deprecated_task_pacing_alias")) == 1
    assert task_pacing.build_budget_snapshot(ctx).has_deadline is True
    assert getattr(ctx, "_time_budget_started_at", None) is not None


def test_required_blocking_until_deadline_agrees_with_the_real_gate_on_the_count_axis(
        monkeypatch, tmp_path):
    """Required+Blocking is where the `until_deadline` alias stops meaning "no
    count cap" (owner D10/D20): the shared review-cycle cap binds under EVERY
    policy. The projection must therefore pass the real gate's enforcement
    predicate — otherwise it projects an unlimited count axis, and in this band
    an improvement floor admission, while the real gate refuses
    `review_cycles_exhausted` at the very same numbers."""
    from ouroboros import task_pacing
    from ouroboros.outcomes import REASON_REVIEW_CYCLES_EXHAUSTED
    from ouroboros.task_results import (
        project_task_acceptance_review_capacity,
        task_acceptance_required_blocking,
    )

    monkeypatch.setenv("OUROBOROS_TASK_REVIEW_MODE", "required")
    monkeypatch.setenv("OUROBOROS_REVIEW_ENFORCEMENT", "blocking")
    ctx, events = _floor_band_ctx(
        monkeypatch, tmp_path, seconds_left=370, claims=2, policy="until_deadline")
    assert task_acceptance_required_blocking() is True
    profile = task_pacing.resolve_budget_profile(ctx)
    snapshot = task_pacing.build_budget_snapshot(ctx, profile=profile)
    # `until_deadline` keeps the 1x window: floor 200 < spendable <= estimate 300.
    assert 200.0 < snapshot.spendable_sec <= 300.0
    # 2 paid cycles -> 1 completed improvement pass, shared cap 1: exhausted.
    assert task_pacing.improvement_pass_allowed(
        snapshot, 1, profile, required_blocking=True, estimated_sec=300.0, ctx=None) == (
        False, REASON_REVIEW_CYCLES_EXHAUSTED)
    # Dropping the enforcement makes the alias claim no count cap at all, and
    # this band then admits at the floor — the exact drift being closed.
    assert task_pacing.improvement_pass_allowed(
        snapshot, 1, profile, estimated_sec=300.0, ctx=None) == (
        True, task_pacing.REASON_LAUNCHED_AT_FLOOR)

    projection = project_task_acceptance_review_capacity(ctx, task_id="root-floor-band")
    assert projection["cap_cycles"] == 2 and projection["claimed_cycles"] == 2
    assert projection["state"] == "unavailable"
    assert projection["reason"] == REASON_REVIEW_CYCLES_EXHAUSTED
    assert "improvement_launch_disclosure" not in projection
    assert _rows(events, REASON_REVIEW_CYCLES_EXHAUSTED) == []  # ctx=None: still silent


def test_a_raising_improvement_gate_never_refuses_the_admissible_paid_panel(monkeypatch, tmp_path):
    """The optional improvement computation has its OWN try. A raise inside
    `improvement_pass_allowed` used to degrade the whole projection to
    `state='unknown'`, and `review_dispatch.task_acceptance_preclaim_refusal`
    turns any non-available projection into a "no reviewer was called" refusal —
    so an auxiliary disclosure failure could suppress an admissible PAID panel.
    The launch-derived state survives, the disclosure is omitted with a typed
    reason, and the dispatch seam still admits."""
    from ouroboros import task_pacing
    from ouroboros.review_dispatch import task_acceptance_preclaim_refusal
    from ouroboros.task_results import project_task_acceptance_review_capacity

    ctx, _events = _floor_band_ctx(monkeypatch, tmp_path, seconds_left=620)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("improvement gate exploded")

    monkeypatch.setattr(task_pacing, "improvement_pass_allowed", _boom)
    projection = project_task_acceptance_review_capacity(ctx, task_id="root-floor-band")
    assert projection["state"] == "available" and projection["reason"] == ""
    assert projection["claimed_cycles"] == 1 and projection["remaining_cycles"] == 1
    assert "improvement_launch_disclosure" not in projection
    assert projection["improvement_admission"] == task_pacing.IMPROVEMENT_ADMISSION_UNKNOWN

    review_ctx = SimpleNamespace(
        tools=SimpleNamespace(_ctx=ctx), task_id="root-floor-band", review_binding={},
    )
    assert task_acceptance_preclaim_refusal(review_ctx) is None  # the PAID panel still runs


def test_the_reducer_inputs_equal_what_production_reads_at_its_two_call_sites(
        monkeypatch, tmp_path):
    """ONE reducer, one set of inputs — and why only the projection calls it.

    The real path evaluates the two gates at DIFFERENT moments and with
    different inputs: `loop._run_task_acceptance_review_once` asks only
    `review_launch_allowed` before the panel, while
    `acceptance_dialogue._apply_task_acceptance_result` asks only
    `improvement_pass_allowed` after it — with a ctx (so the escalation CAN
    fire) and the NEXT pass's estimate. A two-gate, ctx-free reducer there would
    change real-path behaviour, so it is deliberately not forced. What is pinned
    instead is that the projection feeds the reducer the same PROFILE, COUNTER
    and ENFORCEMENT those two call sites read."""
    import inspect

    from ouroboros import acceptance_dialogue, loop as loop_mod, owner_hurry, task_pacing
    from ouroboros.task_results import task_acceptance_required_blocking

    ctx, _events = _floor_band_ctx(monkeypatch, tmp_path, seconds_left=620)
    launch_src = inspect.getsource(loop_mod._run_task_acceptance_review_once)
    gate_src = inspect.getsource(acceptance_dialogue._apply_task_acceptance_result)

    # PROFILE: loop.py overlays owner-hurry onto the resolved profile and hands
    # THAT object to both real gates; the observation variant must agree.
    assert "effective_budget_profile(" in launch_src
    assert "task_pacing.resolve_budget_profile(tools._ctx)" in launch_src
    profile = owner_hurry.effective_budget_profile(ctx, task_pacing.resolve_budget_profile(ctx))
    assert owner_hurry.effective_budget_profile(
        ctx, task_pacing.observe_budget_profile(ctx)) == profile

    # ENFORCEMENT: the gate's own predicate, with `ctx.mode` being the config read.
    assert 'ctx.mode == "required" and loop.get_review_enforcement() == "blocking"' in gate_src
    assert "mode = get_task_review_mode()" in launch_src
    assert task_acceptance_required_blocking() == (
        loop_mod.get_task_review_mode() == "required"
        and loop_mod.get_review_enforcement() == "blocking")

    # COUNTER: production hands the gate `ctx.passes_done`
    # (`_task_acceptance_improvement_passes`, raised to `passes_done + 1` on
    # every fed-back capsule) — after N paid panels that is N-1.
    assert "ctx.passes_done," in gate_src
    assert "_task_acceptance_improvement_passes = ctx.passes_done + 1" in gate_src
    snapshot = task_pacing.build_budget_snapshot(ctx, profile=profile)
    for paid_cycles in (1, 2, 3):
        assert task_pacing.acceptance_admission_projection(
            snapshot, profile=profile, paid_cycles=paid_cycles, estimated_sec=300.0,
        ).improvement == task_pacing.improvement_pass_allowed(
            snapshot, paid_cycles - 1, profile, estimated_sec=300.0, ctx=None)


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
    # By money only: the wave was at the floor, the launch was not.
    assert row["wave_at_floor"] is True and row["launched_at_floor"] is False and row["launch_gate"] is None
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
    short = s.task_pacing.BudgetSnapshot(has_deadline=True, total_sec=1200.0, elapsed_sec=100.0,
                                         remaining_sec=1100.0, reserve_sec=100.0)
    decision = s.task_pacing.launch_at_floor_payload(short, estimated_sec=5000.0)

    def _no_fact(result):
        # No attribute carrier of a launch decision exists on the tool context (R47).
        assert not [name for name in vars(s.first.tools._ctx) if name.endswith("launch_decision")]
        assert result.aggregate_signal == "DEGRADED"
        drained = _drain_to_supervisor(s.event_queue, s.sup_ctx)
        assert not any(e.get("type") == fact for e in drained)
        assert _rows(s.events, fact) == []
        assert not any(fact in row for row in _rows(s.events, "task_acceptance_review_timing"))
        assert len(s.llm.calls) == sends  # no reviewer was called

    # (a) the SAME binding again: the wallet already claimed it → preclaim refusal —
    #     with the launch ALSO admitted at the floor (both flags would be true).
    s.first.launch_decision = dict(decision)
    with s.ua.usage_scope(s.scope):
        _no_fact(s.loop_mod._execute_task_acceptance_panel(s.first))
    # (b) zero-physical refusal: the immutable core overflowed → refused before any send.
    overflow = _acceptance_ctx(tmp_path, content="deliverable v2", fresh_result=False, launch_decision=dict(decision),
                               **{**s.common, "evidence": {**_ACCEPTANCE_PACKET, "__immutable_core_overflow__": True}})
    with s.ua.usage_scope(s.scope):
        _no_fact(s.loop_mod._execute_task_acceptance_panel(overflow))
    # (c) the wallet stamp refuses at the paid seam.
    import contextlib

    @contextlib.contextmanager
    def _veto(ctx):
        raise review_dispatch.TaskAcceptanceDispatchUnavailable("wallet_veto_for_test")
        yield  # pragma: no cover

    vetoed = _acceptance_ctx(tmp_path, content="deliverable v3", fresh_result=False, launch_decision=dict(decision),
                             **s.common)
    with monkeypatch.context() as m:
        m.setattr(review_dispatch, "bind_task_acceptance_paid_dispatch", _veto)
        with s.ua.usage_scope(s.scope):
            _no_fact(s.loop_mod._execute_task_acceptance_panel(vetoed))
    # (d) the stamp never fires: every row refused before its send (route unavailable) —
    #     the substrate returns undispatched actors and the paid seam was never crossed.
    import ouroboros.review_substrate as rs
    from ouroboros.review_substrate import ReviewRunResult

    unrouted = _acceptance_ctx(tmp_path, content="deliverable v4", fresh_result=False, launch_decision=dict(decision),
                               **s.common)
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


def test_a_floor_launch_by_time_is_disclosed_once_on_the_dispatch_fact(monkeypatch, tmp_path):
    """Item 2(e): a real panel whose LAUNCH was admitted at the floor (deadline
    reserve) and whose wave fits normally → exactly ONE fact, `launched_at_floor`
    true with `launch_gate="review_launch"` and the launch seconds, the wave
    fields showing the normal admission (`wave_at_floor` false) — and exactly
    one supervisor-persisted row through the REAL event path."""
    from ouroboros import loop as loop_mod, task_pacing
    from ouroboros import usage_accounting as ua

    monkeypatch.delenv("OUROBOROS_TASK_ABS_CEILING_SEC", raising=False)
    _offline_env(monkeypatch, _ROW_API, _ROW_NATIVE)
    _priced_offline_model(monkeypatch)
    governance, workspace = _roots(tmp_path)
    llm = _EpisodeLLM(tmp_path, [{"content": json.dumps(_CLEAN_VERDICT)}] * 2, scoped=True)
    _real_panel(monkeypatch, llm, stub_gate=False)
    scope = _root_scope(tmp_path, root_limit_usd=50.0)
    _seed_root_ledger(scope)
    event_queue, sup_ctx = _supervised(tmp_path)
    short = task_pacing.BudgetSnapshot(has_deadline=True, total_sec=1200.0, elapsed_sec=100.0,
                                       remaining_sec=1100.0, reserve_sec=100.0)  # spendable 1000 s
    decision = task_pacing.launch_at_floor_payload(short, estimated_sec=16275.0)  # the review launch's own
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project", event_queue=event_queue,
                          launch_decision=decision)
    with ua.usage_scope(scope):
        result = loop_mod._execute_task_acceptance_panel(ctx)
    assert result.aggregate_signal == "PASS"
    fact = task_pacing.DISCLOSURE_DISPATCHED_AT_FLOOR
    for actor in result.actors:
        assert actor["usage"][fact]["launched_at_floor"] is True
    drained = _drain_to_supervisor(event_queue, sup_ctx)
    assert [e["type"] for e in drained if e.get("type") == fact] == [fact]
    (row,) = _rows(task_pacing.acceptance_timing_events_path(ctx.tools._ctx), fact)
    assert row["launched_at_floor"] is True and row["launch_gate"] == "review_launch"
    assert row["launch_estimated_sec"] == 16275.0 and row["launch_floor_sec"] == 200.0
    assert row["launch_spendable_sec"] == 1000.0
    assert row["wave_at_floor"] is False and row["native_rounds_estimate"] == 1 and row["floor_slots"] == 2
    assert row["estimated_wave_usd"] == row["floor_wave_usd"] > 0 and row["remaining_usd"] == 50.0
    timing = _rows(task_pacing.acceptance_timing_events_path(ctx.tools._ctx), "task_acceptance_review_timing")
    assert timing[-1][fact]["launched_at_floor"] is True


def test_a_floor_launch_and_a_floor_wave_are_one_fact_with_both_flags(monkeypatch, tmp_path):
    """Item 2(g): the review-launch gate admitted THIS panel at the floor (its
    decision rides the panel context) and the panel's wave is at the floor too
    → ONE fact carrying both (`launch_gate="review_launch"`, `wave_at_floor`
    true) and exactly one persisted row."""
    monkeypatch.delenv("OUROBOROS_TASK_ABS_CEILING_SEC", raising=False)
    s = _floor_band_native_panel(monkeypatch, tmp_path, _ROW_NATIVE)
    fact = s.task_pacing.DISCLOSURE_DISPATCHED_AT_FLOOR
    short = s.task_pacing.BudgetSnapshot(has_deadline=True, total_sec=1200.0, elapsed_sec=100.0,
                                         remaining_sec=1100.0, reserve_sec=100.0)
    again = _acceptance_ctx(tmp_path, content="deliverable v2", fresh_result=False,
                            launch_decision=s.task_pacing.launch_at_floor_payload(short, estimated_sec=5000.0),
                            **s.common)
    with s.ua.usage_scope(s.scope):
        result = s.loop_mod._execute_task_acceptance_panel(again)
    assert result.aggregate_signal == "PASS"
    assert not [name for name in vars(again.tools._ctx) if name.endswith("launch_decision")]  # no attribute carrier (R47)
    drained = _drain_to_supervisor(s.event_queue, s.sup_ctx)
    assert [e["type"] for e in drained if e.get("type") == fact] == [fact]
    (row,) = _rows(s.events, fact)
    assert row["wave_at_floor"] is True and row["native_rounds_estimate"] == 33
    assert row["launched_at_floor"] is True and row["launch_gate"] == "review_launch"
    assert row["launch_estimated_sec"] == 5000.0 and row["launch_floor_sec"] == 200.0
    assert all(actor["usage"][fact] == {k: v for k, v in row.items() if k not in ("ts", "type", "surface", "task_id")}
               for actor in result.actors)


def test_the_one_fact_has_exactly_one_emission_site_after_the_paid_seam():
    """Item 2(d) at the source: nothing in `_apply_task_acceptance_result` (the
    improvement-pass gate, the dialogue-terminal / empty-capsule / fence-reopen
    branches) emits the fact; the only emitter is the panel, after
    `bind_task_acceptance_paid_dispatch` and the `stamp.fired` check."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "ouroboros" / "acceptance_dialogue.py").read_text(encoding="utf-8")
    emits = [i for i in range(len(src)) if src.startswith('"type": task_pacing.DISCLOSURE_DISPATCHED_AT_FLOOR', i)]
    assert len(emits) == 1
    panel = src.index("def _execute_task_acceptance_panel(")
    seam = src.index("with bind_task_acceptance_paid_dispatch(ctx) as usage_ctx:", panel)
    fired = src.index('getattr(stamp, "fired", False)', seam)
    assert panel < seam < fired < emits[0]
    apply = src.index("def _apply_task_acceptance_result(")
    apply_end = src.index("\ndef ", apply + 1)
    assert "emit_review_event" not in src[apply:apply_end] and "record_launch_at_floor" not in src


def _improvement_gate_at_the_floor(monkeypatch, tmp_path, ctx, *, variant):
    """Drive the REAL `_apply_task_acceptance_result` with the improvement gate
    inside the floor band (spied, never stubbed) through one of the returns
    that never lead to a pass — an empty capsule, a dialogue-terminal quorum,
    a failed fence reopen — then perform the supersede-style reset loop.py
    does (`_task_acceptance_reviewed` cleared, checkpoint dropped)."""
    from ouroboros import loop as loop_mod, task_pacing
    from ouroboros.acceptance_dialogue import _apply_task_acceptance_result
    from ouroboros.review_substrate import ReviewRunResult
    from tests.test_acceptance_a_material import _fail_result, _no_fence

    _no_fence(monkeypatch)
    if variant == "fence_reopen_failed":
        monkeypatch.setattr(loop_mod, "_end_task_acceptance_fence", lambda *_a, **_k: False)
    tools_ctx = ctx.tools._ctx
    events = task_pacing.acceptance_timing_events_path(tools_ctx)
    # The poisoned duration must sit in the CONFIGURED panel's delivery class:
    # the estimator paces by class (R16), so a row under another class is ignored.
    _raw_timing(events, '"native_rows": 0, "native_rounds": 0, "duration_sec": 1e300, "delivery": "%s"'
                % task_pacing.acceptance_panel_delivery(tools_ctx))
    short = task_pacing.BudgetSnapshot(has_deadline=True, total_sec=1200.0, elapsed_sec=100.0,
                                       remaining_sec=1100.0, reserve_sec=100.0)  # spendable 1000 s: floor band
    monkeypatch.setattr(task_pacing, "build_budget_snapshot", lambda _ctx, profile=None: short)
    decisions = []
    original = task_pacing.improvement_pass_allowed

    def _spy(*args, **kwargs):
        decision = original(*args, **kwargs)
        decisions.append(decision)
        return decision

    monkeypatch.setattr(task_pacing, "improvement_pass_allowed", _spy)
    # An EMPTY capsule is a FAIL verdict with nothing actionable at all — no tier,
    # no coach, no finding (the capsule builder prints the tier label alone).
    empty = ReviewRunResult(
        request={"surface": "task_acceptance", "policy": {"min_successful_slots": 1}},
        actors=[{"slot_id": "s0", "signal": "FAIL",
                 "parsed": {"verdict": "FAIL", "dialogue_status": "continue_actionable"}}],
        parsed_findings=[], aggregate_signal="FAIL",
    )
    result = {
        "empty_capsule": empty,
        "dialogue_terminal": _fail_result(dialogue_status="unreachable_here"),
        "fence_reopen_failed": _fail_result(),
    }[variant]
    another_round = _apply_task_acceptance_result(ctx, result, record_run=False)
    assert another_round is False, variant
    assert decisions == [(True, task_pacing.REASON_LAUNCHED_AT_FLOOR)]  # the gate WAS at the floor
    if variant != "empty_capsule":
        assert ctx.llm_trace["acceptance_decision"]["reason"] == variant
    # The owner-supersede-style reset (loop.py) — nothing else is touched.
    tools_ctx._task_acceptance_reviewed = False
    tools_ctx._task_acceptance_fence_generation_mismatch = False
    ctx.llm_trace.pop("root_phase_checkpoint", None)
    assert not [name for name in vars(tools_ctx) if name.endswith("launch_decision")]


_IMPROVEMENT_FIELDS = dict(_task_acceptance_reviewed=False, _task_acceptance_improvement_passes=0,
                           _task_acceptance_seen_bindings={}, is_direct_chat=False)


@pytest.mark.parametrize("variant", ["empty_capsule", "dialogue_terminal", "fence_reopen_failed"])
def test_a_floor_improvement_decision_that_never_became_a_pass_leaves_no_fact_on_a_later_panel(
        monkeypatch, tmp_path, variant):
    """Item 2(a)/(b): the improvement gate admitted at the floor, the pass never
    happened (empty capsule / dialogue terminal / fence reopen failed), the
    supersede-style reset ran — a later NORMALLY admitted paid panel carries no
    fact at all: nothing stored the decision, so nothing can leak."""
    from ouroboros import loop as loop_mod, task_pacing
    from ouroboros import usage_accounting as ua

    monkeypatch.delenv("OUROBOROS_TASK_ABS_CEILING_SEC", raising=False)
    _offline_env(monkeypatch, _ROW_API, _ROW_NATIVE)
    _priced_offline_model(monkeypatch)
    governance, workspace = _roots(tmp_path)
    llm = _EpisodeLLM(tmp_path, [{"content": json.dumps(_CLEAN_VERDICT)}] * 2, scoped=True)
    _real_panel(monkeypatch, llm, stub_gate=False)
    scope = _root_scope(tmp_path, root_limit_usd=50.0)
    _seed_root_ledger(scope)
    event_queue, sup_ctx = _supervised(tmp_path)
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project", event_queue=event_queue,
                          max_improvement_passes=2, **_IMPROVEMENT_FIELDS)
    _improvement_gate_at_the_floor(monkeypatch, tmp_path, ctx, variant=variant)
    later = _acceptance_ctx(tmp_path, content="deliverable v2", fresh_result=False, evidence=dict(_ACCEPTANCE_PACKET),
                            repo_dir=str(governance), workspace_root=str(workspace), workspace_mode="project",
                            event_queue=event_queue, max_improvement_passes=2, **_IMPROVEMENT_FIELDS)
    with ua.usage_scope(scope):
        result = loop_mod._execute_task_acceptance_panel(later)
    fact = task_pacing.DISCLOSURE_DISPATCHED_AT_FLOOR
    assert result.aggregate_signal == "PASS"
    assert all(fact not in actor["usage"] for actor in result.actors)
    assert not any(e.get("type") == fact for e in _drain_to_supervisor(event_queue, sup_ctx))
    events = task_pacing.acceptance_timing_events_path(later.tools._ctx)
    assert _rows(events, fact) == [] and not any(fact in row for row in _rows(events, "task_acceptance_review_timing"))


def test_a_floor_improvement_decision_then_a_money_floor_panel_discloses_money_only(monkeypatch, tmp_path):
    """Item 2(a), money variant: after a floor improvement decision that never
    became a pass, a later panel whose WAVE is at the floor carries the money
    fact with `launched_at_floor=false`, `launch_gate=null` — exactly one row."""
    monkeypatch.delenv("OUROBOROS_TASK_ABS_CEILING_SEC", raising=False)
    s = _floor_band_native_panel(monkeypatch, tmp_path, _ROW_NATIVE)
    fact = s.task_pacing.DISCLOSURE_DISPATCHED_AT_FLOOR
    gate_ctx = _acceptance_ctx(tmp_path, content="deliverable v2", fresh_result=False, **{**s.common, **_IMPROVEMENT_FIELDS})
    _improvement_gate_at_the_floor(monkeypatch, tmp_path, gate_ctx, variant="empty_capsule")
    later = _acceptance_ctx(tmp_path, content="deliverable v3", fresh_result=False, **{**s.common, **_IMPROVEMENT_FIELDS})
    with s.ua.usage_scope(s.scope):
        result = s.loop_mod._execute_task_acceptance_panel(later)
    assert result.aggregate_signal == "PASS"
    drained = _drain_to_supervisor(s.event_queue, s.sup_ctx)
    assert [e["type"] for e in drained if e.get("type") == fact] == [fact]
    (row,) = _rows(s.events, fact)
    assert row["wave_at_floor"] is True and row["launched_at_floor"] is False and row["launch_gate"] is None
    assert row["launch_estimated_sec"] is None and row["launch_floor_sec"] is None


def test_a_malformed_configuration_after_a_floor_improvement_decision_stores_nothing(monkeypatch, tmp_path):
    """Item 2(c): after a floor improvement decision, a malformed reviewer
    configuration refuses the next panel typed and nothing is stored anywhere —
    no fact, no row, no attribute on the tool context."""
    from ouroboros import loop as loop_mod, task_pacing

    monkeypatch.delenv("OUROBOROS_TASK_ABS_CEILING_SEC", raising=False)
    _offline_env(monkeypatch, _ROW_API)
    governance, workspace = _roots(tmp_path)
    event_queue, sup_ctx = _supervised(tmp_path)
    ctx = _acceptance_ctx(tmp_path, evidence=dict(_ACCEPTANCE_PACKET), repo_dir=str(governance),
                          workspace_root=str(workspace), workspace_mode="project", event_queue=event_queue,
                          max_improvement_passes=2, **_IMPROVEMENT_FIELDS)
    _improvement_gate_at_the_floor(monkeypatch, tmp_path, ctx, variant="empty_capsule")
    monkeypatch.setenv(REVIEWER_SLOTS_ENV, "{broken")
    later = _acceptance_ctx(tmp_path, content="deliverable v2", fresh_result=False, evidence=dict(_ACCEPTANCE_PACKET),
                            repo_dir=str(governance), workspace_root=str(workspace), workspace_mode="project",
                            event_queue=event_queue, **_IMPROVEMENT_FIELDS)
    result = loop_mod._execute_task_acceptance_panel(later)
    fact = task_pacing.DISCLOSURE_DISPATCHED_AT_FLOOR
    assert result.aggregate_signal == "DEGRADED" and result.actors == []
    assert any(r.startswith("reviewer_slot_config_invalid") for r in result.degraded_reasons)
    assert not any(e.get("type") == fact for e in _drain_to_supervisor(event_queue, sup_ctx))
    assert _rows(task_pacing.acceptance_timing_events_path(later.tools._ctx), fact) == []
    assert not [name for name in vars(later.tools._ctx) if name.endswith("launch_decision")]
