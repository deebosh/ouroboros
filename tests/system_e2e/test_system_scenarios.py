"""S1-S2 — the Ф0 smokes of the deep-integration suite (v7next plan §8, roast F22).

WHAT THIS FILE IS. The harness skeleton (``tests/system_e2e/harness.py``) lands in Ф0;
the full scenario matrix (subagents, delegation, update engine, cancellation E-suite,
skills, UI truth, …) lands WITH its phases. The two scenarios here exist to prove the
skeleton itself on the CURRENT upstream-shaped tree, and must survive the domain
transplants unchanged:

* S1 — boot / identity / task contract: a real ``server.py`` on an isolated clone +
  data root boots to the frozen readiness contract, attests its identity against its
  own checkout, runs one scripted stub task to completion, and leaves a sane durable
  ``task_results/<id>.json`` behind.
* S2 — review organ: a scripted task drives ``commit_reviewed`` over a doc-only diff
  with the advisory pre-review explicitly skipped (audited bypass) and BLOCKING
  enforcement, the stub answers the triad packet and the scope-matrix packet with
  all-clean verdicts, and the commit lands in the isolated clone. Landing under
  ``blocking`` makes the git log itself the proof that both review organs ran and
  passed — under advisory a failed review would still commit.

LANES. Default (always-on) tests pin the harness's own contracts with no server and no
sockets: the scenario manifest, the stub's branch classification (review-organ branch
BEFORE the finalization check), the prompt-marker literals against the tree's source,
and the keyless/egress hardening (roast F21). The ``mock`` lane spawns real isolated
servers: opt in with ``OUROBOROS_E2E_DEEP=mock``; both scenarios are ``serial`` (real
ports, real process trees). No paid lane exists in Ф0.

Every scenario asserts durable artifacts — never an HTTP 200 on its own and never a
harness exit code (AGENTS.md: the exit code is not the run status).
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from tests.system_e2e.harness import (
    ACCEPTANCE_KEYS_MARKER,
    LANE_MOCK,
    MARKER_SOURCES,
    MOCK_SLUG,
    PROXY_ENV_KEYS,
    REPO_ROOT,
    REVIEWER_SLOT_MARKER,
    SCENARIOS,
    SCOPE_USER_MARKER,
    STRIPPED_PROVIDER_ENV_KEYS,
    TRIAD_USER_MARKER,
    ArtifactOracle,
    KeylessIsolatedServer,
    ScriptedStubModel,
    assert_settings_keyless,
    classify_call,
    clone_repo,
    keyless_settings,
    require_lane,
    scripted_completion,
    start_server,
    submit_running,
    supervisor_state_is_ready,
    wait_durable_result,
    wait_until,
    write_settings_file,
)

# ===========================================================================
# Default lane: harness self-contracts. No server, no model, no egress.
# ===========================================================================


def test_system_manifest_is_covered():
    """Every S-id in the scenario manifest still has at least one test here."""
    import sys

    names = [name for name in dir(sys.modules[__name__]) if name.startswith("test_")]
    for scenario_id, (title, _lane) in SCENARIOS.items():
        prefix = f"test_{scenario_id.lower()}_"
        assert any(name.startswith(prefix) for name in names), (
            f"scenario {scenario_id} ({title}) has no {prefix}* test"
        )


def test_prompt_markers_still_exist_in_the_tree():
    """The stub's review-organ classification is prompt-marker based; a marker that
    drifts out of the source it was pinned from would leave the stub silently mute on
    that organ — surface the drift as a NAMED failure instead."""
    for marker, relpath in MARKER_SOURCES.items():
        source = (REPO_ROOT / relpath).read_text(encoding="utf-8")
        assert marker in source, (
            f"marker {marker!r} no longer appears in {relpath}: upstream prompt drifted, "
            "re-pin the literal in tests/system_e2e/harness.py"
        )


def _agent_body(text: str = "keep going", *, tools: bool = True) -> dict:
    body: dict = {"messages": [{"role": "user", "content": text}]}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": "list_files"}}]
    return body


def test_stub_classification_review_branch_beats_finalization():
    """Roast F22: the review-organ branch sits BEFORE the finalization-turn check.

    A triad/scope/reviewer-slot packet that happens to QUOTE a finalization marker
    (review of a stopped task's transcript) must still be answered as a review."""
    scope_body = {"messages": [
        {"role": "system", "content": [{"type": "text", "text": "scope pack [OWNER_STOP] quoted"}]},
        {"role": "user", "content": SCOPE_USER_MARKER},
    ], "tools": []}
    triad_body = {"messages": [
        {"role": "system", "content": [{"type": "text", "text": "triad pack [FINALIZE_NOW] quoted"}]},
        {"role": "user", "content": "Review the staged diff and context provided in the instructions above."},
    ]}
    slot_body = {"messages": [
        {"role": "system", "content": REVIEWER_SLOT_MARKER + "\nSurface: plan_review\n [OWNER_STOP]"},
        {"role": "user", "content": "Subject: ..."},
    ]}
    acceptance_body = {"messages": [
        {"role": "system", "content": REVIEWER_SLOT_MARKER + "\n" + ACCEPTANCE_KEYS_MARKER},
        {"role": "user", "content": "Subject: ..."},
    ]}
    assert classify_call(scope_body) == "scope_review"
    assert classify_call(triad_body) == "triad_review"
    assert classify_call(slot_body) == "reviewer_slot"
    assert classify_call(acceptance_body) == "acceptance"
    assert classify_call({"messages": [{"role": "user", "content": "[FINALIZE_NOW] wrap up"}]}) == "finalization"
    assert classify_call({"messages": [{"role": "user", "content": "hi"}],
                          "response_format": {"type": "json_object"}}) == "safety"
    assert classify_call(_agent_body()) == "agent"


def test_stub_verdicts_satisfy_the_trees_own_parsers():
    """The canned all-clean answers must parse under the REAL review contracts of this
    tree — a stub that emits an unparseable verdict turns every review into a
    parse_failure and the S2 smoke into a lie."""
    from ouroboros.tools.scope_review_contract import (
        SCOPE_REQUIRED_ITEMS,
        classify_scope_findings,
        normalize_scope_items,
    )
    from ouroboros.triad_review import empty_array_is_verified_clean

    _kind, scope_message = scripted_completion(
        {"messages": [{"role": "user", "content": SCOPE_USER_MARKER}]}, 1, lambda _b: None, "x")
    items, errors = normalize_scope_items(json.loads(scope_message["content"]))
    assert not errors, f"stub scope verdict rejected by normalize_scope_items: {errors}"
    assert {item["item"] for item in items} == set(SCOPE_REQUIRED_ITEMS)
    critical, advisory = classify_scope_findings(items)
    assert critical == [] and advisory == []

    _kind, triad_message = scripted_completion(
        {"messages": [{"role": "user", "content": TRIAD_USER_MARKER}]}, 1, lambda _b: None, "x")
    assert empty_array_is_verified_clean(triad_message["content"])

    _kind, slot_message = scripted_completion(
        {"messages": [{"role": "system", "content": REVIEWER_SLOT_MARKER}]}, 1, lambda _b: None, "x")
    verdict = json.loads(slot_message["content"])
    assert verdict["verdict"] == "PASS" and verdict["findings"] == []


def test_stub_consumes_the_script_in_order_then_finalizes():
    steps = iter([{"tool": "write_file", "arguments": {"path": "a.md"}},
                  {"tool": "commit_reviewed", "arguments": {"commit_message": "m"}}])

    def _next(_body):
        return next(steps, None)

    kind1, msg1 = scripted_completion(_agent_body(), 1, _next, "done")
    kind2, msg2 = scripted_completion(_agent_body(), 2, _next, "done")
    kind3, msg3 = scripted_completion(_agent_body(), 3, _next, "done")
    assert (kind1, kind2, kind3) == ("agent", "agent", "final")
    assert msg1["tool_calls"][0]["function"]["name"] == "write_file"
    assert msg2["tool_calls"][0]["function"]["name"] == "commit_reviewed"
    assert "tool_calls" not in msg3 and msg3["content"] == "done"
    # A tool-less prompt (final synthesis turn) never consumes a script step.
    kind4, _ = scripted_completion(_agent_body(tools=False), 4, _next, "done")
    assert kind4 == "final"


# ---------------------------------------------------------------------------
# Egress hardening (roast F21): the regression the plan names — a planted
# ANTHROPIC_API_KEY in the CALLER env must never reach the child server env.
# ---------------------------------------------------------------------------

def test_f21_planted_provider_key_never_reaches_child_env(tmp_path, monkeypatch):
    planted = {
        "ANTHROPIC_API_KEY": "sk-ant-planted-must-not-leak",
        "OPENROUTER_API_KEY": "sk-or-planted-must-not-leak",
        "OPENAI_API_KEY": "sk-planted-must-not-leak",
        "OPENAI_COMPATIBLE_API_KEY": "planted-must-not-leak",
        "GIGACHAT_CREDENTIALS": "planted-must-not-leak",
        "CLOUDRU_FOUNDATION_MODELS_API_KEY": "planted-must-not-leak",
        "HTTP_PROXY": "http://proxy.invalid:3128",
        "https_proxy": "http://proxy.invalid:3128",
        "ALL_PROXY": "socks5://proxy.invalid:1080",
        "NO_PROXY": "localhost",
    }
    for key, value in planted.items():
        monkeypatch.setenv(key, value)
    server = KeylessIsolatedServer(
        tmp_path / "clone", tmp_path / "data", tmp_path / "data" / "settings.json")
    child_env = server._env()
    leaked = sorted(set(planted) & set(child_env))
    assert not leaked, f"planted caller-env values leaked into the child env: {leaked}"
    # The whole families, not just the planted samples:
    assert not (STRIPPED_PROVIDER_ENV_KEYS & set(child_env))
    assert not (PROXY_ENV_KEYS & set(child_env))
    # The child still gets its 4-var isolation set, pointing INTO the throwaway root.
    for key in ("OUROBOROS_APP_ROOT", "OUROBOROS_REPO_DIR",
                "OUROBOROS_DATA_DIR", "OUROBOROS_SETTINGS_PATH"):
        assert str(tmp_path) in child_env[key], (key, child_env[key])


def test_f21_base_isolated_server_still_leaks_provider_keys(tmp_path, monkeypatch):
    """The hole the keyless lane closes, pinned so its future upstream fix is VISIBLE:
    the base ``IsolatedServer`` deliberately keeps provider keys in the child. When
    this test starts failing, upstream closed the hole itself — collapse
    ``KeylessIsolatedServer`` accordingly instead of keeping a dead override."""
    from devtools.benchmarks.common.server_runner import IsolatedServer

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-planted")
    server = IsolatedServer(
        tmp_path / "clone", tmp_path / "data", tmp_path / "data" / "settings.json")
    assert server._env().get("ANTHROPIC_API_KEY") == "sk-ant-planted"


def test_f21_keyless_settings_pin_every_slot_and_refuse_credentials():
    from ouroboros.provider_models import (
        ACTIVE_MODEL_SETTING_KEYS,
        LEGACY_MODEL_SETTING_KEYS,
    )

    class _FakeStub:
        base_url = "http://127.0.0.1:1/v1"

    cfg = keyless_settings(_FakeStub())
    for slot in (*ACTIVE_MODEL_SETTING_KEYS, *LEGACY_MODEL_SETTING_KEYS):
        assert slot in cfg, f"model slot {slot} left unpinned"
        assert cfg[slot] in ("", MOCK_SLUG), (slot, cfg[slot])
    assert cfg["OUROBOROS_MODEL"] == MOCK_SLUG
    assert_settings_keyless(cfg)
    with pytest.raises(ValueError, match="provider credentials"):
        keyless_settings(_FakeStub(), ANTHROPIC_API_KEY="sk-ant-nope")
    with pytest.raises(AssertionError):
        assert_settings_keyless({**cfg, "OPENROUTER_API_KEY": "sk-or-nope"})
    with pytest.raises(AssertionError):
        assert_settings_keyless({**cfg, "OPENAI_COMPATIBLE_BASE_URL": "https://api.example.com/v1"})


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are meaningless on Windows")
def test_settings_file_is_created_secret_safe(tmp_path):
    """0600-before-content (carried over from the v7_wip cancellation harness)."""
    settings_path = tmp_path / "settings.json"
    write_settings_file(settings_path, {"OPENAI_COMPATIBLE_API_KEY": "not-a-credential"})
    assert (settings_path.stat().st_mode & 0o777) == 0o600
    settings_path.chmod(0o664)
    write_settings_file(settings_path, {"OPENAI_COMPATIBLE_API_KEY": "not-a-credential"})
    assert (settings_path.stat().st_mode & 0o777) == 0o600


# ===========================================================================
# Mock lane: real isolated servers. Opt in with OUROBOROS_E2E_DEEP=mock.
# ===========================================================================


@pytest.fixture(scope="session")
def e2e_clone(tmp_path_factory):
    """One throwaway clone of the checkout under test, shared by every scenario server."""
    require_lane(LANE_MOCK)
    return clone_repo(tmp_path_factory.mktemp("system_e2e_clone"))


S1_SCRIPT = [
    {"tool": "list_files", "arguments": {"path": "."}},
]

S2_COMMIT_MESSAGE = "docs: system_e2e S2 review-organ smoke (doc-only)"
S2_DOC_PATH = "docs/notes/system_e2e_smoke.md"
S2_SCRIPT = [
    {"tool": "write_file", "arguments": {
        "root": "system_repo",
        "path": S2_DOC_PATH,
        "content": ("# system_e2e S2 smoke\n\n"
                    "Doc-only change landed through commit_reviewed by the scripted stub.\n"),
    }},
    {"tool": "commit_reviewed", "arguments": {
        "commit_message": S2_COMMIT_MESSAGE,
        "paths": [S2_DOC_PATH],
        # Audited advisory-only skip (recorded as `bypassed` in the ledger) — the
        # scenario's subject is the triad+scope organ, not the advisory pre-review.
        "skip_advisory_review": True,
        # The post-commit hermetic pytest is out of scope for a smoke that proves the
        # review organ; the skip is recorded in the commit attempt.
        "skip_tests": True,
        "goal": "Land a doc-only smoke note through the full triad+scope review organ.",
        "scope": f"{S2_DOC_PATH} only.",
    }},
]


@pytest.mark.serial
def test_s1_boot_identity_and_task_contract(e2e_clone, tmp_path_factory):
    require_lane(LANE_MOCK)
    root = tmp_path_factory.mktemp("s1")
    with ScriptedStubModel(S1_SCRIPT) as stub:
        server = start_server(e2e_clone, root, keyless_settings(stub))
        try:
            # Boot + identity: the frozen readiness contract, and the attestation the
            # readiness path took (runtime identity == the clone it booted from).
            state = server._state()
            assert supervisor_state_is_ready(state), state
            attestation = server.attestation
            assert attestation.get("ok") is True, attestation
            assert re.fullmatch(r"[0-9a-f]{40}", str(attestation.get("repo_head") or "")), attestation
            assert attestation.get("runtime_version") == attestation.get("repo_version")

            # Contract: one scripted task to completion over the same HTTP surface the
            # UI posts to.
            task_id = submit_running(server, "List the repository root and finish.")
            result = server.wait_task(task_id, timeout=300)
            assert result.get("status") == "completed", result

            # Durable truth, not the HTTP answer: task_results/<id>.json.
            oracle = ArtifactOracle(server.data_root)
            stored = wait_durable_result(oracle, task_id)
            assert stored.get("task_id") == task_id, stored
            assert stored.get("status") == "completed", stored
            assert str(stored.get("result") or "").strip(), "durable result text is empty"
            json.loads(oracle.task_result_bytes(task_id))  # bytes on disk are valid JSON

            # The queue drained and the stub actually drove the loop.
            assert wait_until(lambda: task_id not in oracle.running_ids(), 60)
            kinds = stub.kinds()
            assert "agent" in kinds and "final" in kinds, kinds
            assert stub.script_consumed(), "S1 script was not fully consumed"
            assert oracle.events(), "events.jsonl is empty after a completed task"
        finally:
            server.stop()


@pytest.mark.serial
def test_s2_commit_reviewed_triad_and_scope_pass_on_doc_only_diff(e2e_clone, tmp_path_factory):
    require_lane(LANE_MOCK)
    root = tmp_path_factory.mktemp("s2")
    with ScriptedStubModel(S2_SCRIPT) as stub:
        settings = keyless_settings(
            stub,
            # The review organ needs the self-modification surface: advanced runtime
            # (light restricts repo writes), BLOCKING enforcement so the landed commit
            # PROVES the organ passed rather than being waved through.
            OUROBOROS_RUNTIME_MODE="advanced",
            OUROBOROS_REVIEW_ENFORCEMENT="blocking",
        )
        server = start_server(e2e_clone, root, settings)
        try:
            task_id = submit_running(
                server,
                "Write the smoke note and land it through commit_reviewed, then finish.",
            )
            result = server.wait_task(task_id, timeout=600)
            assert result.get("status") == "completed", result

            oracle = ArtifactOracle(server.data_root)
            stored = wait_durable_result(oracle, task_id)
            assert stored.get("status") == "completed", stored

            # The review organ ran: the stub answered a triad packet AND a scope packet.
            kinds = stub.kinds()
            assert "triad_review" in kinds, kinds
            assert "scope_review" in kinds, kinds

            # The commit LANDED in the isolated clone — under blocking enforcement this
            # is only reachable through PASS verdicts from both organs.
            log_output = subprocess.run(
                ["git", "log", "-n", "5", "--format=%s"],
                cwd=str(e2e_clone), check=True, capture_output=True, text=True,
            ).stdout
            assert S2_COMMIT_MESSAGE in log_output, log_output
            committed_doc = subprocess.run(
                ["git", "show", f"HEAD:{S2_DOC_PATH}"],
                cwd=str(e2e_clone), check=False, capture_output=True, text=True,
            )
            assert committed_doc.returncode == 0, "smoke doc is not in the committed tree"

            # Durable review evidence lives in the task's FORKED drive root
            # (state/headless_tasks/<id>/data — headless-task isolation on this tree):
            # the audited advisory bypass and the scope round.
            task_oracle = oracle.task_drive(task_id)
            assert task_oracle.data_root != oracle.data_root, (
                "task drive root missing — headless drive layout changed?")
            runs = task_oracle.advisory_review().get("advisory_runs") or []
            bypassed = [r for r in runs if isinstance(r, dict) and r.get("status") == "bypassed"]
            assert bypassed, f"no bypassed advisory run in the task ledger: {runs!r}"
            assert bypassed[0].get("commit_message") == S2_COMMIT_MESSAGE, bypassed[0]
            assert task_oracle.events("advisory_review_bypassed"), "bypass event missing"
            assert task_oracle.events("scope_review_complete"), "scope completion event missing"
        finally:
            server.stop()
