"""The commit gate's cold-start density rung (owner decision 2026-09-05, answer 1 = A).

The commit gate gets the SAME cold-start tokenizer-density rung the packed deep
self-review has (``capability_evidence.cold_start_density_probe``), at the pre-dispatch
admission seam both packets share (``review_admission``):

- scope ladder: a cold store, a >=1M scope route and the owed-in-full required
  set refused at the floor cap -> exactly ONE bounded probe send on the exact
  model, the witness recorded, the pack rebuilt ONCE and assembled;
- a fresh exact-model witness -> no probe, the existing refusal path unchanged;
- a probe the paid ledger refuses -> typed disclosure (ladder step + review
  event), the existing refusal path, no crash;
- a pack that fits -> no probe (never on every commit);
- triad fit: the same rung before the degradation ladder, bounded, and never
  without a ctx (no drive root to record a witness on).
"""
from __future__ import annotations

import pathlib
import subprocess
from types import SimpleNamespace
from unittest import mock

import pytest

from ouroboros import capability_evidence as ce
from ouroboros.capability_evidence import DENSITY_PROBE_EFFORT, DENSITY_PROBE_MAX_TOKENS
from ouroboros.reviewer_window import ReviewerWindow
from ouroboros.tools import review_admission as admission
from ouroboros.tools import scope_review as sr
from ouroboros.tools.review_helpers import DENSITY_PROBE_SAMPLE_CHARS
from ouroboros.usage_accounting import BudgetExceeded

SCOPE_MODEL = "openai/gpt-5.6-terra"
WINDOW = 1_050_000
# Cold floor cap at a 1,050,000-token window: (1,050,000 - 100,000) / 1.65.
COLD_CAP = int((WINDOW - 100_000) / ce.COLD_START_TOKEN_DENSITY)
# Exact-model witness at 0.9 (x1.05 safety) admits the margin-bounded 795,000.
MEASURED_CAP = WINDOW - 100_000 - 155_000
# An owed-in-full artifact (prompts/) between the two caps, in chars/4 tokens.
OWED_IN_FULL_TOKENS = 650_000


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=T", *args],
                   cwd=str(repo), capture_output=True, check=True)


def _repo(tmp_path: pathlib.Path, required_tokens: int) -> pathlib.Path:
    """A repo whose UNCHANGED ``prompts/`` artifacts are owed in full: together
    they do not fit the cold floor cap but fit the measured cap of a 1M reviewer
    (each below the atlas per-file cap, so only the INPUT cap refuses them)."""
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "CHECKLISTS.md").write_text(
        "## Intent / Scope Review Checklist\n\nplaceholder\n", encoding="utf-8")
    (repo / "docs" / "DEVELOPMENT.md").write_text("dev guide\n", encoding="utf-8")
    (repo / "BIBLE.md").write_text("constitution\n", encoding="utf-8")
    (repo / "prompts").mkdir()
    for index in range(3):
        (repo / "prompts" / f"owed_{index}.md").write_text(
            "x" * (required_tokens * 4 // 3), encoding="utf-8")
    (repo / "ok.py").write_text("print(1)\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    (repo / "ok.py").write_text("print(2)\n", encoding="utf-8")
    _git(repo, "add", ".")
    return repo


@pytest.fixture
def drive(tmp_path, monkeypatch):
    root = tmp_path / "drive"
    (root / "state").mkdir(parents=True)
    # The cap is computed through review_drive_root(None) -> config.DATA_DIR;
    # the probe records on ctx.drive_root: one root for both, as in production.
    monkeypatch.setattr("ouroboros.config.DATA_DIR", root)
    ce._DENSITY_MEMO.clear()
    return root


@pytest.fixture
def scope_window(monkeypatch):
    monkeypatch.setattr(sr, "_scope_window",
                        lambda _m, **_k: ReviewerWindow(window_tokens=WINDOW, status="confirmed"))
    assert sr._effective_scope_input_limit(scope_model=SCOPE_MODEL) == COLD_CAP


def _ctx(repo: pathlib.Path, drive: pathlib.Path, progress: list) -> SimpleNamespace:
    return SimpleNamespace(
        repo_dir=repo, drive_root=drive, task_id="commit-gate-test",
        emit_progress_fn=progress.append, pending_events=[],
    )


def _probe_chat(calls: list, density: float = 0.9):
    def chat(llm, **kwargs):
        calls.append(kwargs)
        chars = sum(len(m["content"]) for m in kwargs["messages"])
        return {"content": "OK"}, {"prompt_tokens": int(chars / 4 * density), "cost": 0.0}
    return chat


def test_cold_store_owed_in_full_set_probes_once_records_witness_and_assembles(tmp_path, drive, scope_window):
    repo = _repo(tmp_path, OWED_IN_FULL_TOKENS)
    progress: list = []
    calls: list = []
    with mock.patch("ouroboros.llm_observability.chat_observed", side_effect=_probe_chat(calls)):
        prepared, final = admission.prepare_scope_review(
            _ctx(repo, drive, progress), "test commit", scope_model=SCOPE_MODEL)

    assert final is None and prepared is not None, getattr(final, "block_message", final)
    assert [c["call_type"] for c in calls] == [admission.DENSITY_PROBE_CALL_TYPE]
    probe = calls[0]
    assert probe["model"] == SCOPE_MODEL and probe["tools"] is None
    assert probe["max_tokens"] == DENSITY_PROBE_MAX_TOKENS == 256
    assert probe["reasoning_effort"] == DENSITY_PROBE_EFFORT == "low"
    sample = probe["messages"][1]["content"]
    assert sample.startswith("### prompts/owed_"), "the sample is the refused required rows first"
    assert len(sample) <= DENSITY_PROBE_SAMPLE_CHARS + len("### prompts/owed_0.md\n\n")
    density, source = ce.resolve_review_token_density(drive, SCOPE_MODEL)
    assert source == "measured" and density < ce.COLD_START_TOKEN_DENSITY
    assert sr._effective_scope_input_limit(scope_model=SCOPE_MODEL) == MEASURED_CAP
    assert all(f"prompts/owed_{i}.md" in prepared["prompt"] for i in range(3))
    steps = prepared["context_manifest"]["ladder_steps"]
    assert steps[-1] == {"step": "density_probe", "model": SCOPE_MODEL, "outcome": "measured", "rebuilt": True}
    assert not any(s.get("unassembled_required") for s in steps), "the rebuilt trace is the assembled one"
    assert any("bounded probe" in p for p in progress) and any("Token density for" in p for p in progress)


def test_cold_store_probe_discloses_one_review_event(tmp_path, drive, scope_window):
    repo = _repo(tmp_path, OWED_IN_FULL_TOKENS)
    ctx = _ctx(repo, drive, [])
    with mock.patch("ouroboros.llm_observability.chat_observed", side_effect=_probe_chat([])):
        admission.prepare_scope_review(ctx, "test commit", scope_model=SCOPE_MODEL)
    events = [e for e in ctx.pending_events if e.get("type") == admission.DENSITY_PROBE_EVENT]
    assert len(events) == 1
    assert events[0]["surface"] == "scope_review" and events[0]["model"] == SCOPE_MODEL
    assert events[0]["outcome"] == "measured" and events[0]["task_id"] == "commit-gate-test"


def test_fresh_exact_model_witness_never_probes_and_keeps_the_refusal(tmp_path, drive, scope_window):
    repo = _repo(tmp_path, OWED_IN_FULL_TOKENS)
    # A fresh witness that still refuses the set: measured 1.6 x 1.05 = 1.68.
    ce.record_token_density(drive, SCOPE_MODEL, prompt_chars=400_000, prompt_tokens=160_000)
    assert ce.resolve_review_token_density(drive, SCOPE_MODEL)[1] == "measured"
    ctx = _ctx(repo, drive, [])
    calls: list = []
    with mock.patch("ouroboros.llm_observability.chat_observed", side_effect=_probe_chat(calls)):
        prepared, final = admission.prepare_scope_review(ctx, "test commit", scope_model=SCOPE_MODEL)

    assert calls == [], "a warm store must not spend a probe"
    assert prepared is None and final.blocked and final.status == "fixed_overflow"
    assert "prompts/owed_" in final.block_message
    assert not [s for s in final.context_manifest["ladder_steps"] if s["step"] == "density_probe"]
    assert not [e for e in ctx.pending_events if e.get("type") == admission.DENSITY_PROBE_EVENT]


def test_budget_refused_probe_is_a_typed_disclosure_on_the_existing_refusal(tmp_path, drive, scope_window):
    repo = _repo(tmp_path, OWED_IN_FULL_TOKENS)
    progress: list = []
    ctx = _ctx(repo, drive, progress)
    calls: list = []

    def refused(llm, **kwargs):
        calls.append(kwargs)
        raise BudgetExceeded("global budget exhausted", limit_scope="global")

    with mock.patch("ouroboros.llm_observability.chat_observed", side_effect=refused):
        prepared, final = admission.prepare_scope_review(ctx, "test commit", scope_model=SCOPE_MODEL)

    assert len(calls) == 1, "one admission attempt, never a retry"
    assert prepared is None and final.blocked and final.status == "fixed_overflow"
    assert "prompts/owed_" in final.block_message
    steps = final.context_manifest["ladder_steps"]
    assert steps[-1] == {"step": "density_probe", "model": SCOPE_MODEL, "outcome": "budget_refused", "rebuilt": False}
    events = [e for e in ctx.pending_events if e.get("type") == admission.DENSITY_PROBE_EVENT]
    assert len(events) == 1 and events[0]["outcome"] == "budget_refused"
    assert "global budget exhausted" in events[0]["reason"]
    assert any("refused by the budget" in p for p in progress)
    assert ce.resolve_review_token_density(drive, SCOPE_MODEL)[1] == "cold_conservative"


def test_failed_probe_keeps_the_cold_cap_and_the_refusal(tmp_path, drive, scope_window):
    repo = _repo(tmp_path, OWED_IN_FULL_TOKENS)
    ctx = _ctx(repo, drive, [])
    with mock.patch("ouroboros.llm_observability.chat_observed", side_effect=RuntimeError("provider down")):
        prepared, final = admission.prepare_scope_review(ctx, "test commit", scope_model=SCOPE_MODEL)
    assert prepared is None and final.status == "fixed_overflow"
    assert final.context_manifest["ladder_steps"][-1]["outcome"] == "failed"
    assert ce.resolve_review_token_density(drive, SCOPE_MODEL)[1] == "cold_conservative"


def test_a_fitting_pack_never_probes(tmp_path, drive, scope_window):
    repo = _repo(tmp_path, 2_000)  # fits the cold cap with room to spare
    ctx = _ctx(repo, drive, [])
    calls: list = []
    with mock.patch("ouroboros.llm_observability.chat_observed", side_effect=_probe_chat(calls)):
        prepared, final = admission.prepare_scope_review(ctx, "test commit", scope_model=SCOPE_MODEL)
    assert final is None and prepared is not None
    assert calls == [], "the rung never runs on a commit whose pack fits"
    assert not [s for s in prepared["context_manifest"]["ladder_steps"] if s["step"] == "density_probe"]
    assert ce.resolve_review_token_density(drive, SCOPE_MODEL)[1] == "cold_conservative"


def test_scope_pack_starved_is_size_only():
    assert admission._scope_pack_starved(sr._TouchedContextStatus(status="fixed_overflow"), {})
    assert admission._scope_pack_starved(sr._TouchedContextStatus(status="budget_exceeded"), {})
    assert not admission._scope_pack_starved(sr._TouchedContextStatus(status="omitted"), {})
    assert not admission._scope_pack_starved(sr._TouchedContextStatus(status="empty"), {})
    assert not admission._scope_pack_starved(None, {"ladder_steps": [{"step": "compact_atlas", "diff_only_files": 0}]})
    assert admission._scope_pack_starved(None, {"ladder_steps": [{"step": "compact_atlas", "diff_only_files": 2}]})
    assert admission._scope_pack_starved(None, {"ladder_steps": [{"step": "compact_atlas", "zero_context_diff": True}]})


# --- triad fit -----------------------------------------------------------------

def _triad_env(monkeypatch, prefix_tokens: int):
    import ouroboros.tools.review as review

    monkeypatch.setattr(review, "reviewer_context_window", lambda model: 1_000_000)
    monkeypatch.setattr(review, "run_cmd", lambda *a, **kw: "")
    monkeypatch.setattr(review, "_REVIEW_PROMPT_TEMPLATE_STABLE", "{preamble}")
    monkeypatch.setattr(review, "_REVIEW_PROMPT_TEMPLATE_DYNAMIC",
                        "{current_files_section}\n{diff_text}\n{changed_files}")

    def assemble(files_section, staged_diff):
        stable = review._REVIEW_PROMPT_TEMPLATE_STABLE.format(preamble="g" * (prefix_tokens * 4))
        dynamic = review._REVIEW_PROMPT_TEMPLATE_DYNAMIC.format(
            current_files_section=files_section, diff_text=staged_diff, changed_files="a.py")
        return stable + "\n" + dynamic, len(stable) + 1

    return assemble


def test_triad_cold_store_probes_the_overflowed_slot_once_and_fits(tmp_path, drive, monkeypatch):
    # 600K estimated tokens of irreducible prefix: above the cold cap of a 1M
    # slot (900,000 / 1.65 = 545,454), below its measured cap (745,000).
    assemble = _triad_env(monkeypatch, 600_000)
    model = "openai/gpt-5.6-terra"
    progress: list = []
    ctx = SimpleNamespace(drive_root=drive, task_id="triad-test", emit_progress_fn=progress.append,
                          pending_events=[])
    calls: list = []
    with mock.patch("ouroboros.llm_observability.chat_observed", side_effect=_probe_chat(calls)):
        prompt, _stable, overflow = admission.fit_triad_prompt(
            [model], assemble, "full snapshot of a.py", "+x", "a.py", tmp_path, ctx=ctx)

    assert overflow == "", overflow
    assert "full snapshot of a.py" in prompt, "no degradation rung was needed after the witness"
    assert [c["call_type"] for c in calls] == [admission.DENSITY_PROBE_CALL_TYPE]
    assert calls[0]["model"] == model and calls[0]["max_tokens"] == DENSITY_PROBE_MAX_TOKENS
    assert len(calls[0]["messages"][1]["content"]) == DENSITY_PROBE_SAMPLE_CHARS
    assert ce.resolve_review_token_density(drive, model)[1] == "measured"
    events = [e for e in ctx.pending_events if e.get("type") == admission.DENSITY_PROBE_EVENT]
    assert len(events) == 1 and events[0]["surface"] == "triad_review"


def test_triad_warm_store_never_probes(tmp_path, drive, monkeypatch):
    assemble = _triad_env(monkeypatch, 600_000)
    model = "openai/gpt-5.6-terra"
    ce.record_token_density(drive, model, prompt_chars=400_000, prompt_tokens=90_000)
    calls: list = []
    ctx = SimpleNamespace(drive_root=drive, task_id="t", emit_progress_fn=lambda _t: None, pending_events=[])
    with mock.patch("ouroboros.llm_observability.chat_observed", side_effect=_probe_chat(calls)):
        _prompt, _stable, overflow = admission.fit_triad_prompt(
            [model], assemble, "full snapshot of a.py", "+x", "a.py", tmp_path, ctx=ctx)
    assert overflow == "" and calls == []


def test_triad_budget_refused_probe_keeps_the_typed_fit_terminal(tmp_path, drive, monkeypatch):
    assemble = _triad_env(monkeypatch, 800_000)  # above even the measured cap
    model = "openai/gpt-5.6-terra"
    ctx = SimpleNamespace(drive_root=drive, task_id="t", emit_progress_fn=lambda _t: None, pending_events=[])

    def refused(llm, **kwargs):
        raise BudgetExceeded("global budget exhausted")

    with mock.patch("ouroboros.llm_observability.chat_observed", side_effect=refused):
        _prompt, _stable, overflow = admission.fit_triad_prompt(
            [model], assemble, "full snapshot of a.py", "+x", "a.py", tmp_path, ctx=ctx)
    assert "REVIEW_BLOCKED" in overflow
    events = [e for e in ctx.pending_events if e.get("type") == admission.DENSITY_PROBE_EVENT]
    assert len(events) == 1 and events[0]["outcome"] == "budget_refused"


def test_triad_without_a_ctx_never_sends(tmp_path, drive, monkeypatch):
    """A bare fit-check (no ctx, no drive root to record a witness on) is the
    pre-rung behaviour byte for byte: no send, the cold cap, the typed block."""
    assemble = _triad_env(monkeypatch, 600_000)
    calls: list = []
    with mock.patch("ouroboros.llm_observability.chat_observed", side_effect=_probe_chat(calls)):
        _prompt, _stable, overflow = admission.fit_triad_prompt(
            ["openai/gpt-5.6-terra"], assemble, "full snapshot of a.py", "+x", "a.py", tmp_path)
    assert "REVIEW_BLOCKED" in overflow and calls == []


def test_deep_review_and_gate_share_one_rung():
    import inspect

    from ouroboros import deep_self_review

    assert "cold_start_density_probe(" in inspect.getsource(deep_self_review._run_packed_review)
    assert "cold_start_density_probe(" in inspect.getsource(admission.density_probe_before_size_refusal)
    assert not hasattr(deep_self_review, "_cold_start_density_probe")
