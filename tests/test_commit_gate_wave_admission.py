"""Commit-gate wave admission (owner decision 2026-09-05, answer 2 = A).

The scope reviewer — the only constitutionally blocking seat — reserves its
budget FIRST, and the commit-gate wave (scope seats + triad seats) is admitted
all-or-nothing against the task's root fence BEFORE any paid seat is
dispatched. A wave that does not fit is a typed $0 pre-dispatch refusal naming
the shortfall, never a half-dispatched panel (the 4 September paid run: two
triad seats held the money, the third seat and the scope seat were refused
mid-wave, and the commit blocked with ~$5 of the $8 fence never spent).
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from ouroboros import usage_accounting as ua
from ouroboros.review_execution import ReviewRouteKind
from ouroboros.reviewer_window import ReviewerWindow
from ouroboros.tools import parallel_review, review, review_admission
from ouroboros.tools import scope_review as scope_mod
from ouroboros.tools.scope_review_contract import SCOPE_REQUIRED_ITEMS

SCOPE_MODEL = "scope/model"
TRIAD_MODELS = ["triad/a", "triad/b"]
BOUNDS = {SCOPE_MODEL: 3.0, "triad/a": 1.0, "triad/b": 1.0}
ROOT = "wave-root"


def _scope_matrix() -> str:
    return json.dumps([
        {"item": item, "verdict": "PASS", "severity": "advisory",
         "reason": "checked the relevant code path and its consumers thoroughly"}
        for item in sorted(SCOPE_REQUIRED_ITEMS)
    ])


class LedgerLLM:
    """Every chat performs ONE real ledger attempt in the bound review scope
    (the exact seam the substrate's api executor drives), so the ledger order
    and the fence are the product's own, not a mock's."""

    def __init__(self, delays=None):
        self.calls = []
        self.delays = dict(delays or {})
        self.lock = threading.Lock()

    def chat(self, **kwargs):
        model = kwargs["model"]
        time.sleep(self.delays.get(model, 0.0))
        reply = {"content": _scope_matrix() if model == SCOPE_MODEL else "[]"}
        ua.execute_physical_attempt(
            ua.AttemptRequest(model=model, provider="test", reservation_usd=BOUNDS[model]),
            lambda: reply,
            extractor=lambda _r: ({"prompt_tokens": 4, "completion_tokens": 2}, 0.01, True),
        )
        with self.lock:
            self.calls.append(model)
        return reply, {"prompt_tokens": 4, "completion_tokens": 2, "cost": 0.01}


@pytest.fixture
def gate(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "state").mkdir(parents=True)
    monkeypatch.setenv("OUROBOROS_DATA_DIR", str(root))
    monkeypatch.setenv("OUROBOROS_SETTINGS_PATH", str(root / "settings.json"))
    monkeypatch.setenv("TOTAL_BUDGET", "100")
    ua._reset_task_cache_splits()
    ua._ROOT_ACCOUNTING_TELEMETRY.pop(ROOT, None)
    # The reservation math is the product's; only the price catalog is pinned
    # (no live pricing fetch under test).
    monkeypatch.setattr(ua, "_reservation_cost", lambda request: BOUNDS[request.model])
    monkeypatch.setattr(scope_mod, "_scope_window", lambda *_a, **_k: ReviewerWindow(
        window_tokens=1_000_000, status="confirmed"))
    monkeypatch.setattr(parallel_review, "run_cmd", lambda *_a, **_k: "staged diff")
    monkeypatch.setattr(parallel_review, "scope_reviewer_slots", lambda *_a, **_k: [
        SimpleNamespace(model=SCOPE_MODEL, slot_id="scope_slot_1", route=ReviewRouteKind.API_CHAT,
                        effort="", session_target="", session_profile="", subagent_id="",
                        retrieves=False),
    ])
    monkeypatch.setattr(review_admission, "prepare_scope_review", lambda *_a, **_k: ({
        "prompt": "SCOPE PACK " * 50, "session_task": "", "repo_dir": tmp_path,
        "scope_model_id": SCOPE_MODEL, "delegated": False, "slot_id": "scope_slot_1",
        "route": ReviewRouteKind.API_CHAT, "slot_effort": "", "session_target": "",
        "session_profile": "", "subagent_id": "", "context_manifest": {}, "stable_prefix_len": 0,
    }, None))
    row_plan = {
        "models": list(TRIAD_MODELS), "routes": [ReviewRouteKind.API_CHAT] * 2,
        "slot_ids": ["slot_1", "slot_2"], "efforts": ["", ""], "subagent_ids": ["", ""],
    }
    monkeypatch.setattr(review, "_prepare_unified_review", lambda *_a, **_k: ({
        "prompt": "TRIAD PACK " * 20, "stable_prefix_len": 0, "models": list(TRIAD_MODELS),
        "routes": [ReviewRouteKind.API_CHAT] * 2, "row_plan": row_plan, "session_task": "",
        "target_repo": tmp_path, "blocking_review": True,
    }, None, False))
    from ouroboros import config as cfg

    monkeypatch.setattr(cfg, "get_review_enforcement", lambda: "blocking")
    return root


def _ctx(root, tmp_path):
    return SimpleNamespace(
        repo_dir=tmp_path, drive_root=root, task_id=ROOT,
        task_metadata={"root_task_id": ROOT, "budget_drive_root": str(root)},
        pending_events=[], _review_history=[], _review_advisory=[], _scope_review_history={},
        _review_iteration_count=0, _last_review_critical_findings=[], _review_degraded_reasons=[],
    )


def _run(root, tmp_path, monkeypatch, llm, *, fence: float):
    """Run the commit gate with the task's usage scope bound (the orchestrator
    thread) and the substrate's own root fence (its worker threads)."""
    monkeypatch.setenv("OUROBOROS_PER_TASK_COST_USD", str(fence))
    monkeypatch.setattr(review, "LLMClient", lambda: llm)
    monkeypatch.setattr(scope_mod, "LLMClient", lambda: llm)
    ctx = _ctx(root, tmp_path)
    scope = ua.UsageScope(drive_root=root, task_id=ROOT, root_task_id=ROOT, root_limit_usd=fence)
    with ua.usage_scope(scope):
        outcome = parallel_review.run_parallel_review(ctx, "wave admission commit")
    return ctx, outcome


def _ledger(root):
    path = root / ua.LEDGER_REL
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_fence_that_fits_the_whole_wave_dispatches_every_seat(gate, tmp_path, monkeypatch):
    llm = LedgerLLM()
    ctx, (review_err, scope_result, _reason, _adv) = _run(gate, tmp_path, monkeypatch, llm, fence=10.0)

    assert review_err is None, review_err
    assert scope_result.blocked is False and scope_result.status == "responded"
    assert sorted(llm.calls) == sorted([SCOPE_MODEL, *TRIAD_MODELS])
    rows = _ledger(gate)
    assert sorted(row["model"] for row in rows if row["state"] == "settled") == sorted(llm.calls)
    assert not [e for e in ctx.pending_events if e.get("type") == "review_wave_budget_insufficient"]


def test_fence_that_fits_only_the_triad_refuses_the_wave_at_zero_dollars(gate, tmp_path, monkeypatch):
    """Scope $3 + triad $1 + $1 = $5 against a $4 fence: the triad alone would
    have fit (the 4 September failure shape), so the WHOLE wave is refused
    before dispatch — no seat reserved, no ledger row, every seat typed $0."""
    llm = LedgerLLM()
    ctx, (review_err, scope_result, block_reason, _adv) = _run(gate, tmp_path, monkeypatch, llm, fence=4.0)

    assert llm.calls == [] and _ledger(gate) == []
    assert review_err and "commit-gate review wave declined before dispatch ($0 spent)" in review_err
    assert "reservation upper bound $5.000000" in review_err
    assert "per-task budget fence $4.000000" in review_err
    assert "accounted=$0.000000 (of which $0.000000 is reserved by other in-flight attempts)" in review_err
    assert "remaining=$4.000000, shortfall=$1.000000" in review_err
    # Every seat is named with its own bound, scope first.
    assert review_err.index("scope_review:scope_slot_1 scope/model $3.000000") < review_err.index(
        "multi_model_review:slot_1 triad/a $1.000000")
    assert block_reason == "review_wave_budget_insufficient"
    assert [r["status"] for r in ctx._last_triad_raw_results] == ["not_dispatched", "not_dispatched"]
    assert [r["slot_id"] for r in ctx._last_triad_raw_results] == ["slot_1", "slot_2"]
    assert scope_result.blocked is True and scope_result.status == "not_dispatched"
    assert scope_result.block_message.startswith("⚠️ SCOPE_REVIEW_BLOCKED: ")
    assert ctx._last_scope_raw_results[0]["status"] == "not_dispatched"
    assert any("triad_not_dispatched_budget_admission" in r for r in ctx._review_degraded_reasons)
    events = [e for e in ctx.pending_events if e.get("type") == "review_wave_budget_insufficient"]
    assert len(events) == 1 and events[0]["surface"] == "commit_gate"
    assert events[0]["seats"] == ["scope_review:scope_slot_1", "multi_model_review:slot_1",
                                  "multi_model_review:slot_2"]
    assert events[0]["slot_bounds"] == [3.0, 1.0, 1.0]


def test_refusal_names_money_held_by_other_in_flight_attempts(gate, tmp_path, monkeypatch):
    """The fence compares against settled + reserved holds: a refusal must say
    how much of `accounted` is a hold, not a bill."""
    with ua.usage_scope(ua.UsageScope(drive_root=gate, task_id=ROOT, root_task_id=ROOT, root_limit_usd=8.0)):
        held = ua.reserve_attempt(ua.AttemptRequest(model="triad/a", provider="test", source="main"))
        ua.mark_dispatched(held)  # an in-flight main-loop send: $1 upper bound held
        settled = ua.reserve_attempt(ua.AttemptRequest(model="triad/b", provider="test", source="main"))
        ua.mark_dispatched(settled)
        ua.settle_attempt(settled, {"prompt_tokens": 1, "completion_tokens": 1}, cost_usd=2.5, cost_final=True)
    llm = LedgerLLM()
    ctx, (review_err, _scope, _reason, _adv) = _run(gate, tmp_path, monkeypatch, llm, fence=8.0)

    assert llm.calls == []
    assert "accounted=$3.500000 (of which $1.000000 is reserved by other in-flight attempts)" in review_err
    assert "remaining=$4.500000, shortfall=$0.500000" in review_err
    assert len(_ledger(gate)) == 5  # the two seeded attempts only (reserved/dispatched/settled rows)


def test_scope_reserves_before_the_triad_even_when_it_is_slower(gate, tmp_path, monkeypatch):
    """Scope-first ordering is enforced by the orchestrator, not by luck: the
    scope seat is deliberately the slow one, and the triad is still held until
    the scope reservation is on the ledger."""
    llm = LedgerLLM(delays={SCOPE_MODEL: 0.4})
    ctx, (review_err, scope_result, _reason, _adv) = _run(gate, tmp_path, monkeypatch, llm, fence=10.0)

    assert review_err is None and scope_result.status == "responded"
    reserved = [row for row in _ledger(gate) if row["state"] == "reserved"]
    assert [row["category"] for row in reserved][0] == "scope_review_review"
    assert [row["model"] for row in reserved] == [SCOPE_MODEL, *sorted(TRIAD_MODELS)] or \
        [row["model"] for row in reserved] == [SCOPE_MODEL, *reversed(sorted(TRIAD_MODELS))]
    assert reserved[0]["review_slot_id"] == "scope_slot_1"


def test_paid_seats_are_priced_seat_by_seat_scope_first(gate, tmp_path, monkeypatch):
    """The admission prices each seat with ITS pack and output reservation —
    the scope pack beside the triad pack — through the shared gate, one value
    per slot, and asks the gate as ONE wave."""
    seen = {}

    def _gate(ctx, *, surface, models, prompt_chars, max_completion_tokens, extra=None):
        seen.update(surface=surface, models=models, prompt_chars=prompt_chars,
                    max_completion_tokens=max_completion_tokens, extra=extra)
        return None

    monkeypatch.setattr("ouroboros.tools.review_helpers.review_wave_budget_gate", _gate)
    from ouroboros.tools.review_multi_model import _review_output_budget

    llm = LedgerLLM()
    _run(gate, tmp_path, monkeypatch, llm, fence=10.0)

    assert seen["surface"] == "commit_gate"
    assert seen["models"] == [SCOPE_MODEL, *TRIAD_MODELS]
    scope_chars, triad_chars = seen["prompt_chars"][0], seen["prompt_chars"][1]
    assert seen["prompt_chars"] == [scope_chars, triad_chars, triad_chars]
    # The scope pack is measured as the exact message pair the substrate sends;
    # the triad pack carries the constitutional preamble + BIBLE ahead of it.
    assert scope_chars > len("SCOPE PACK " * 50) and triad_chars > len("TRIAD PACK " * 20) + 1000
    assert seen["max_completion_tokens"] == [100_000, _review_output_budget(), _review_output_budget()]


def test_review_wave_admission_prices_per_slot_and_discloses_holds(gate, monkeypatch):
    requests = []
    monkeypatch.setattr(ua, "_reservation_cost", lambda request: requests.append(request) or 0.5)
    admission = ua.review_wave_admission(
        gate, root_task_id="fresh-root", models=["prov/a", "prov/b"],
        prompt_chars=[400, 1600], max_completion_tokens=[1000, 2000], task_id="fresh-root",
        root_limit_usd=0.75,
    )
    assert [(r.prompt_tokens_estimate, r.max_completion_tokens, r.task_id) for r in requests] == [
        (100, 1000, "fresh-root"), (400, 2000, "fresh-root"),
    ]
    # No ledger row yet: the caller's bound fence is the limit, not a fail-open.
    assert admission["limit_usd"] == 0.75 and admission["accounted_usd"] == 0.0
    assert admission["estimated_wave_usd"] == 1.0 and admission["fits"] is False
    assert admission["slot_bounds"] == [0.5, 0.5] and admission["reserved_usd"] == 0.0
    # A scalar still broadcasts (the task-level callers are unchanged), and a
    # root with neither a ledger row nor a bound fence keeps failing open.
    requests.clear()
    scalar = ua.review_wave_admission(
        gate, root_task_id="fresh-root", models=["prov/a", "prov/b"], prompt_chars=400,
        root_limit_usd=10.0,
    )
    assert [r.prompt_tokens_estimate for r in requests] == [100, 100]
    assert [r.max_completion_tokens for r in requests] == [65536, 65536]
    assert scalar["fits"] is True and scalar["limit_usd"] == 10.0
    unfenced = ua.review_wave_admission(gate, root_task_id="fresh-root", models=["prov/a"], prompt_chars=400)
    assert unfenced["fits"] is True and unfenced["limit_usd"] is None


def test_wave_without_paid_seats_neither_waits_nor_refuses(gate, tmp_path, monkeypatch):
    """An all-retrieving wave rides the owner's subscription: nothing to price."""
    from ouroboros.tools.parallel_review import _admit_commit_gate_wave, _commit_gate_paid_seats

    session_slot = SimpleNamespace(model="m/session", slot_id="scope_slot_1",
                                   route=ReviewRouteKind.AGENT_SESSION, subagent_id="")
    seats = _commit_gate_paid_seats(
        {"prompt": "", "models": ["m/session"], "routes": [ReviewRouteKind.AGENT_SESSION],
         "row_plan": {"models": ["m/session"], "routes": [ReviewRouteKind.AGENT_SESSION]}},
        False, [{"slot": session_slot, "prepared": {"prompt": ""}, "final": None}],
    )
    assert seats == []
    assert _admit_commit_gate_wave(_ctx(gate, tmp_path), seats) is None
    started = time.monotonic()
    parallel_review._await_scope_reservation(SimpleNamespace(done=lambda: False), seats, started)
    assert time.monotonic() - started < 0.5
