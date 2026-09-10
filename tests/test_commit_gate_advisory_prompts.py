"""Advisory-prompt content, schema, and inline-retry tests split out of
test_commit_gate.py (ibl-978e5cd9258f — that file was over the size-ratchet
1600-line GIANT_PATHS cap). Covers: advisory/review-status tool schemas,
blocking-history prompt sections, prompt strictness/obligation-targeting
wording, and the advisory-gate inline-retry-on-freshness-gap path. No
behavior change — these tests moved verbatim, including their exact
module-lookup helpers.
"""
import importlib
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_git_module():
    return importlib.import_module("ouroboros.tools.git")


def _get_advisory_module():
    sys.path.insert(0, REPO)
    return importlib.import_module("ouroboros.tools.claude_advisory_review")


def _get_review_state_module():
    sys.path.insert(0, REPO)
    return importlib.import_module("ouroboros.review_state")


def test_advisory_pre_review_tool_schema_has_skip_param():
    """advisory_review schema must expose skip_advisory_review param."""
    adv_mod = _get_advisory_module()
    tools = adv_mod.get_tools()
    adv_tool = next(t for t in tools if t.name == "advisory_review")
    props = adv_tool.schema["parameters"]["properties"]
    assert "skip_advisory_review" in props
    assert props["skip_advisory_review"].get("default") is False


def test_repo_commit_schema_has_skip_advisory_param():
    """commit_reviewed schema must expose skip_advisory_review param."""
    git_mod = _get_git_module()
    tools = git_mod.get_tools()
    commit_tool = next(t for t in tools if t.name == "commit_reviewed")
    props = commit_tool.schema["parameters"]["properties"]
    assert "skip_advisory_review" in props


def test_advisory_choice_guidance_is_shared_across_model_facing_schemas():
    adv_mod = _get_advisory_module()
    git_mod = _get_git_module()
    advisory_tools = {tool.name: tool for tool in adv_mod.get_tools()}
    git_tools = {tool.name: tool for tool in git_mod.get_tools()}

    advisory_tool = advisory_tools["preflight_review"]
    status_tool = advisory_tools["review_status"]
    commit_tool = git_tools["commit_reviewed"]
    alias_tool = git_tools["vcs_commit_reviewed"]
    advisory_skip = advisory_tool.schema["parameters"]["properties"]["skip_advisory_review"]
    commit_skip = commit_tool.schema["parameters"]["properties"]["skip_advisory_review"]
    alias_skip = alias_tool.schema["parameters"]["properties"]["skip_advisory_review"]

    guidance = adv_mod.ADVISORY_REVIEW_CHOICE_GUIDANCE
    surfaces = [
        advisory_tool.schema["description"],
        advisory_skip["description"],
        status_tool.schema["description"],
        commit_tool.schema["description"],
        commit_skip["description"],
        alias_tool.schema["description"],
        alias_skip["description"],
    ]
    assert all(guidance in surface for surface in surfaces)
    assert all("skip_advisory_review=True" in surface for surface in surfaces)
    assert "bypasses only the requirements for advisory freshness" in guidance
    assert "records remain visible" in guidance
    assert "removes only advisory" not in guidance
    assert commit_tool.schema["description"] == alias_tool.schema["description"]
    assert commit_skip["description"] == alias_skip["description"]
    assert "advisory-readiness projection" in status_tool.schema["description"]
    assert "not the full commit gate" in status_tool.schema["description"]
    assert "bypass the entire commit gate" not in " ".join(surfaces).lower()


def test_advisory_auto_bypass_on_missing_key(tmp_path, monkeypatch):
    """advisory_pre_review auto-bypasses with audit when the advisory model's
    provider credentials are absent (the retired ANTHROPIC_API_KEY probe's
    successor: availability follows the routed model)."""
    import json
    import subprocess
    adv_mod = _get_advisory_module()
    rs_mod = _get_review_state_module()
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    (drive_root / "state").mkdir()
    (drive_root / "logs").mkdir()
    subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True)

    monkeypatch.delenv("OUROBOROS_REVIEWER_SLOTS", raising=False)
    # v7: OUROBOROS_ADVISORY_REVIEW_ROUTE is retired and ignored (see
    # preflight_review_run.py) — clear it for hygiene, but there is no module
    # constant to name it any more.
    monkeypatch.delenv("OUROBOROS_ADVISORY_REVIEW_ROUTE", raising=False)
    for _key in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                 "MINIMAX_API_KEY", "GIGACHAT_AUTH_KEY", "CLOUD_RU_API_KEY"):
        monkeypatch.delenv(_key, raising=False)
    progress_calls = []

    class FakeCtx:
        pass
    ctx = FakeCtx()
    ctx.repo_dir = str(repo_dir)
    ctx.drive_root = str(drive_root)
    ctx.task_id = "autobypass-task"
    ctx.drive_logs = lambda: drive_root / "logs"
    ctx.emit_progress_fn = lambda msg: progress_calls.append(msg)

    result_raw = adv_mod._handle_advisory_pre_review(ctx, commit_message="test commit")
    result = json.loads(result_raw)

    # Must be bypassed, not errored
    assert result["status"] == "bypassed"
    assert "no provider credentials" in result["bypass_reason"]

    # Must create a fresh advisory state (bypassed counts as fresh for gate)
    state = rs_mod.load_state(drive_root)
    assert state.latest() is not None
    assert state.latest().status == "bypassed"

    # Must audit bypass to events.jsonl
    events_path = drive_root / "logs" / "events.jsonl"
    assert events_path.exists(), "events.jsonl must exist after auto-bypass"
    events = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
    bypass_events = [e for e in events if e.get("type") == "advisory_review_bypassed"]
    assert len(bypass_events) == 1
    assert "no provider credentials" in bypass_events[0]["bypass_reason"]


def test_advisory_prompt_contains_blocking_history_when_blocked(tmp_path):
    """Advisory prompt must include blocking history section when last commit was blocked."""
    import subprocess
    adv_mod = _get_advisory_module()
    rs_mod = _get_review_state_module()

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    (drive_root / "state").mkdir()
    subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True)

    # Create a blocked commit attempt with structured critical findings
    state = rs_mod.AdvisoryReviewState()
    attempt = rs_mod.CommitAttemptRecord(
        ts="2026-04-02T22:00:00",
        commit_message="test blocked commit",
        status="blocked",
        block_reason="critical_findings",
        block_details=(
            "⚠️ REVIEW_BLOCKED: Critical issues found.\n"
            "  CRITICAL: [gpt-5.5] bible_compliance: Missing BIBLE.md update\n"
            "  CRITICAL: [gpt-5.5] tests_affected: No tests for new function\n"
            "  WARN: [opus] self_consistency: Minor doc drift"
        ),
        critical_findings=[
            {"verdict": "FAIL", "severity": "critical",
             "item": "bible_compliance", "reason": "Missing BIBLE.md update", "model": "m"},
            {"verdict": "FAIL", "severity": "critical",
             "item": "tests_affected", "reason": "No tests for new function", "model": "m"},
        ],
    )
    state.add_blocking_attempt(attempt)
    rs_mod.save_state(drive_root, state)

    # Build the advisory prompt with drive_root
    prompt = adv_mod._build_advisory_prompt(
        repo_dir, "test commit", drive_root=drive_root
    )

    # Must contain obligations section (new format)
    assert "Unresolved obligations" in prompt
    assert "bible_compliance" in prompt
    assert "tests_affected" in prompt
    assert "should explicitly address" in prompt


def test_advisory_prompt_no_blocking_history_when_succeeded(tmp_path):
    """Advisory prompt must NOT include blocking history when last commit succeeded."""
    import subprocess
    adv_mod = _get_advisory_module()
    rs_mod = _get_review_state_module()

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    (drive_root / "state").mkdir()
    subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True)

    state = rs_mod.AdvisoryReviewState()
    state.attempts = [rs_mod.CommitAttemptRecord(
        ts="2026-04-02T22:00:00",
        commit_message="test commit",
        status="succeeded",
    )]
    rs_mod.save_state(drive_root, state)

    prompt = adv_mod._build_advisory_prompt(
        repo_dir, "test commit", drive_root=drive_root
    )

    assert "## Unresolved obligations from previous blocking rounds" not in prompt


def test_advisory_prompt_no_blocking_history_without_drive_root(tmp_path):
    """Advisory prompt must gracefully skip blocking history when no drive_root."""
    import subprocess
    adv_mod = _get_advisory_module()

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True)

    prompt = adv_mod._build_advisory_prompt(repo_dir, "test commit")
    assert "## Unresolved obligations from previous blocking rounds" not in prompt


def test_advisory_prompt_strictness_formulations():
    """Advisory prompt must contain the same strictness language as blocking reviewers."""
    import subprocess
    adv_mod = _get_advisory_module()

    import pathlib as _pl
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        repo_dir = _pl.Path(d)
        (repo_dir / "BIBLE.md").write_text("test bible", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True)

        prompt = adv_mod._build_advisory_prompt(repo_dir, "test commit")

        # Key strictness formulations that must be present
        assert "same rigor" in prompt.lower() or "same severity threshold" in prompt.lower()
        assert "do not stop after finding the first issue" in prompt.lower()
        assert "distinct problem" in prompt.lower()
        assert "read the full content of every changed file" in prompt.lower()
        assert "all bugs, logic errors" in prompt.lower()
        # Must NOT contain the old relaxing language
        assert "findings do not directly block" not in prompt.lower()


def test_advisory_prompt_references_architecture_doc_via_read_tool():
    """Advisory prompt must inline ARCHITECTURE.md content when available.

    The v4.15.1 prompt restores ARCHITECTURE.md directly into the advisory context so
    the reviewer always sees version-sync and module-structure facts without an extra
    read step. The touched-file pack must avoid duplicating it separately.
    """
    import subprocess
    adv_mod = _get_advisory_module()

    import pathlib as _pl
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        repo_dir = _pl.Path(d)
        (repo_dir / "BIBLE.md").write_text("test bible", encoding="utf-8")
        (repo_dir / "docs").mkdir(parents=True, exist_ok=True)
        (repo_dir / "docs" / "ARCHITECTURE.md").write_text(
            "# Ouroboros v99.0.0 — Architecture", encoding="utf-8"
        )
        subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True)

        prompt = adv_mod._build_advisory_prompt(repo_dir, "test commit")

        assert "ARCHITECTURE.md" in prompt, "Prompt must include an ARCHITECTURE.md section"
        assert "## ARCHITECTURE.md" in prompt, "Prompt should expose ARCHITECTURE.md as a first-class section"
        assert "Ouroboros v99.0.0" in prompt, (
            "ARCHITECTURE.md content should now be inlined for advisory review"
        )


def test_advisory_prompt_strictness_concrete_fix_requirement():
    """Advisory prompt must require concrete fix suggestions for FAIL findings."""
    import subprocess
    adv_mod = _get_advisory_module()

    import pathlib as _pl
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        repo_dir = _pl.Path(d)
        subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True)

        prompt = adv_mod._build_advisory_prompt(repo_dir, "test commit")

        # Must require actionable fix suggestions
        assert "concrete" in prompt.lower()
        assert "fix" in prompt.lower()
        assert "how to fix" in prompt.lower() or "how to change" in prompt.lower() or "what to change" in prompt.lower()


def test_blocking_history_section_with_scope_blocked(tmp_path):
    """Blocking history should also work for scope_blocked commits."""
    adv_mod = _get_advisory_module()
    rs_mod = _get_review_state_module()

    drive_root = tmp_path
    (drive_root / "state").mkdir(parents=True)

    state = rs_mod.AdvisoryReviewState()
    attempt = rs_mod.CommitAttemptRecord(
        ts="2026-04-02T22:00:00",
        commit_message="scope blocked commit",
        status="blocked",
        block_reason="scope_blocked",
        block_details=(
            "⚠️ SCOPE_REVIEW_BLOCKED: Missing touchpoint.\n"
            "CRITICAL: [opus] forgotten_touchpoints: ARCHITECTURE.md not updated"
        ),
        critical_findings=[
            {"verdict": "FAIL", "severity": "critical",
             "item": "forgotten_touchpoints", "reason": "ARCHITECTURE.md not updated", "model": "opus"},
        ],
    )
    state.add_blocking_attempt(attempt)
    rs_mod.save_state(drive_root, state)

    section = adv_mod._build_blocking_history_section(drive_root)
    assert "Unresolved obligations" in section
    assert "scope_blocked" in section
    assert "ARCHITECTURE.md" in section


def test_review_blocked_message_prefers_fix_over_rebuttal():
    """REVIEW_BLOCKED coaching (issue #447, В8=A): fix first; rebuttal is legitimate
    for factual errors, unsupported severity, or disproportionate remedies — but it
    never overrides owner-chosen enforcement, and a repeated finding means fix."""
    from ouroboros.tools.review import _build_critical_block_message

    class FakeCtx:
        _review_iteration_count = 1
        _review_history = []

    msg = _build_critical_block_message(
        FakeCtx(), "test commit", ["bible_compliance: violation"], [], ""
    )
    # Whitespace-normalized: the message wraps lines mid-phrase.
    lowered = " ".join(msg.lower().split())
    assert "factually incorrect" in lowered
    # Proportionality channel is open: disproportionate remedies are arguable.
    assert "disproportionate" in lowered
    # Non-override clause: rebuttal is argument, not authority.
    assert "never overrides owner-chosen enforcement" in lowered
    # Repeat-finding coaching (v7 wording: re-verify evidence/proportionality
    # rather than the fork's blunt "implement the fix").
    assert "repetition alone does not validate a finding" in lowered


def test_review_blocked_5plus_hint_suggests_split():
    """v4.9.2: After 5+ attempts, hint suggests implementing the fix or splitting."""
    from ouroboros.tools.review import _build_critical_block_message

    class FakeCtx:
        # v4.33.0 lowered the threshold from 5 to 3 — 5 still triggers but
        # the phrasing changed from "report the blockage" to "send_user_message
        # to escalate" which carries the same semantic weight.
        _review_iteration_count = 5
        _review_history = []

    msg = _build_critical_block_message(
        FakeCtx(), "test commit", ["tests_affected: missing tests"], [], ""
    )
    lowered = msg.lower()
    assert "split" in lowered, f"missing split-the-diff guidance: {msg!r}"
    assert ("send_user_message" in lowered or "escalate" in lowered
            or "report" in lowered), (
        f"missing escalation guidance: {msg!r}"
    )


def test_review_blocked_message_requires_reaudit_after_first_block():
    """Blocked-review guidance should explicitly require a full-diff re-audit after the first block."""
    from ouroboros.tools.review import _build_critical_block_message

    class FakeCtx:
        _review_iteration_count = 2
        _review_history = []
        _last_review_critical_findings = [{"item": "code_quality"}]
        _last_review_advisory_findings = []

    msg = _build_critical_block_message(
        FakeCtx(), "test commit", ["code_quality: review mismatch"], [], ""
    )
    lowered = msg.lower()
    assert "re-read the full diff" in lowered
    assert "group obligations by root cause" in lowered
    assert "rewrite the plan" in lowered


def test_self_consistency_listed_as_critical_in_severity_rules():
    """self_consistency (item 13) must be treated as conditionally critical, not always advisory."""
    import pathlib
    checklists_path = pathlib.Path(__file__).parent.parent / "docs" / "CHECKLISTS.md"
    content = checklists_path.read_text(encoding="utf-8")

    # The severity rules section must describe self_consistency as conditionally critical
    assert "self_consistency" in content
    # Must NOT say items 11-13 are ALL advisory
    lines = content.split("\n")
    for line in lines:
        if "items 11-13 are advisory" in line.lower():
            raise AssertionError(
                f"Found old 'items 11-13 are advisory' rule — self_consistency "
                f"must now be conditionally critical:\n  {line}"
            )
    # Must say item 13 is conditionally critical
    assert "item 13" in content.lower() and "critical" in content.lower()
    # v4.33.0: the old "README test counts" example was folded into the
    # broader Critical surface whitelist. Narrative / prose / commentary
    # mismatches outside the whitelist must be explicitly advisory.
    assert "Critical surface whitelist" in content
    assert "advisory" in content.lower()
    # And the "narrative" framing of commit-message / doc wording remains.
    assert "narrative" in content.lower()


def test_development_compliance_checklist_expanded():
    """development_compliance description must include specific concrete checks."""
    import pathlib
    checklists_path = pathlib.Path(__file__).parent.parent / "docs" / "CHECKLISTS.md"
    content = checklists_path.read_text(encoding="utf-8")

    # All these concrete checks must appear in the checklist
    required_terms = [
        "snake_case",
        "PascalCase",
        "Gateway",
        "LLMClient",
        "[:N]",
        "ToolEntry",
    ]
    for term in required_terms:
        assert term in content, (
            f"development_compliance checklist must mention '{term}' for concrete checks, "
            f"but it's missing from CHECKLISTS.md"
        )


# test_triad_review_prompt_has_thoroughness_instructions and
# test_triad_review_reasoning_effort_is_medium_not_low removed in v5.15.x —
# both pinned exact prompt-template / inspect.getsource() substrings.
# Prompt quality and effort level evolve over time; the behavioral
# contract (review produces correct verdicts at adequate depth) is
# exercised by the actual triad-review integration tests in
# test_review_fidelity.py, test_review_observability.py, and the
# git+review pipeline suite.


def test_advisory_prompt_contains_obligation_targeting_instructions(tmp_path):
    """_build_advisory_prompt must instruct the reviewer how to target a specific
    obligation when multiple open obligations share the same checklist item.
    Without this, a generic item-name PASS cannot disambiguate which obligation
    was resolved, and the resolution logic leaves all same-item obligations open.
    """
    import tempfile
    import pathlib as _pl
    import subprocess as _sp
    adv_mod = _get_advisory_module()

    with tempfile.TemporaryDirectory() as d:
        repo_dir = _pl.Path(d)
        _sp.run(["git", "init"], cwd=str(repo_dir), capture_output=True)

        prompt = adv_mod._build_advisory_prompt(repo_dir, "test commit")

        # Must explain the (obligation <id>) suffix mechanism
        assert "obligation" in prompt.lower(), (
            "Prompt must mention 'obligation' targeting to allow per-finding resolution"
        )
        assert "(obligation" in prompt, (
            "Prompt must show the '(obligation <id>)' suffix syntax for targeting specific obligations"
        )
        # Must warn that a generic PASS won't resolve all same-item obligations
        assert "will NOT resolve" in prompt or "will not resolve" in prompt.lower(), (
            "Prompt must warn that generic item-name PASS won't resolve all same-item obligations"
        )


def test_advisory_gate_runs_advisory_inline_on_freshness_gap(monkeypatch, tmp_path):
    """ibl-b1f10cece0eb: when the only block is 'no fresh advisory run found',
    _advisory_and_tests_gate runs advisory_review inline against the current
    snapshot and proceeds — instead of bouncing the agent out to run it by
    hand. A real advisory record is written, so the compensating test-preflight
    coupling is unchanged (advisory_gate_unavailable stays the arbiter)."""
    git_mod = _get_git_module()

    calls = {"freshness": 0, "inline_advisory": 0, "preflight": 0}

    def fake_freshness(ctx, commit_message, skip=False, paths=None, *, review_rebuttal="", decision=None):
        calls["freshness"] += 1
        # First call: freshness gap. After the inline advisory runs, it clears.
        if calls["inline_advisory"] == 0:
            if decision is not None:
                # v7: the gate now consults decision["refresh_required"] (set by
                # the real _check_advisory_freshness) instead of re-classifying
                # the error text — a self-recoverable "no fresh run" gap.
                decision["refresh_required"] = True
            return (
                "⚠️ ADVISORY_PRE_REVIEW_REQUIRED: No fresh advisory run found for "
                "this snapshot (hash=abc123).\nNo advisory runs recorded yet.\n"
            )
        return None

    def fake_inline_advisory(ctx, commit_message, paths=None, skip_tests=False,
                             *, review_rebuttal="", goal="", scope="", prepared=False):
        calls["inline_advisory"] += 1
        return "{}"

    monkeypatch.setattr(git_mod, "_check_advisory_freshness", fake_freshness)
    monkeypatch.setattr(git_mod, "_handle_advisory_pre_review", fake_inline_advisory)
    monkeypatch.setattr(git_mod, "advisory_gate_unavailable", lambda: False)
    monkeypatch.setattr(git_mod, "_diff_is_doc_only", lambda paths: False)
    monkeypatch.setattr(git_mod, "_managed_candidate_needs_proof", lambda ctx: False)
    monkeypatch.setattr(
        git_mod, "_run_review_preflight_tests",
        lambda ctx: pytest.fail("preflight must not run when advisory is real"),
    )

    class FakeCtx:
        repo_dir = tmp_path
        drive_root = tmp_path

        def emit_progress_fn(self, *_a, **_k):
            pass

    result = git_mod._advisory_and_tests_gate(
        FakeCtx(),
        "test commit",
        0.0,
        classification_paths=["ouroboros/foo.py"],
        advisory_paths=["ouroboros/foo.py"],
        skip_advisory_pre_review=False,
        skip_tests=False,
    )

    assert result is None, f"gate should proceed after inline advisory, got: {result}"
    assert calls["inline_advisory"] == 1, "inline advisory_review must run exactly once"
    assert calls["freshness"] == 2, "freshness must be re-checked after the inline run"


def test_advisory_gate_does_not_retry_inline_on_syntax_preflight_block(monkeypatch, tmp_path):
    """A SyntaxError preflight block reproduces identically on a re-run, so the
    gate must NOT burn a ~2min inline advisory on it — it returns the block."""
    git_mod = _get_git_module()

    calls = {"inline_advisory": 0}

    def fake_freshness(ctx, commit_message, skip=False, paths=None, *, review_rebuttal="", decision=None):
        # A syntax-preflight-blocked run is NOT self-recoverable — the gate must
        # leave decision["refresh_required"] falsy so no inline advisory fires.
        return (
            "⚠️ ADVISORY_PRE_REVIEW_REQUIRED: Last advisory run for this snapshot "
            "was blocked by the syntax preflight (hash=abc123). The Claude SDK "
            "advisory was skipped because a staged `.py` file has a SyntaxError.\n"
        )

    def fake_inline_advisory(*a, **k):
        calls["inline_advisory"] += 1
        return "{}"

    monkeypatch.setattr(git_mod, "_check_advisory_freshness", fake_freshness)
    monkeypatch.setattr(git_mod, "_handle_advisory_pre_review", fake_inline_advisory)
    monkeypatch.setattr(git_mod, "run_cmd", lambda *a, **k: "")
    monkeypatch.setattr(git_mod, "_record_commit_attempt", lambda *a, **k: None)

    class FakeCtx:
        repo_dir = tmp_path
        drive_root = tmp_path

        def emit_progress_fn(self, *_a, **_k):
            pass

    result = git_mod._advisory_and_tests_gate(
        FakeCtx(),
        "test commit",
        0.0,
        classification_paths=["ouroboros/foo.py"],
        advisory_paths=["ouroboros/foo.py"],
        skip_advisory_pre_review=False,
        skip_tests=False,
    )

    assert result is not None and result["block_reason"] == "no_advisory"
    assert calls["inline_advisory"] == 0, "must not run inline advisory for a syntax block"
