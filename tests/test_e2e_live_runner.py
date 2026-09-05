"""Pins of the live E2E stand runner (``devtools/e2e_live``).

Default lane: no server, no sockets, no network (the provider probes are monkeypatched and the
lane pool is replaced by a fake). The one real-server test at the end is the keyless ``--stub``
rehearsal of SM1 and carries the same three gates as the system_e2e lane.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import urllib.request

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from devtools.benchmarks.common import launcher_audit  # noqa: E402
from devtools.benchmarks.common.manifests import BenchmarkAdmissionRefused  # noqa: E402
from devtools.e2e_live import run_live_lanes, scenarios, stub_lane, ui_probe  # noqa: E402

FAKE_KEY = "sk-or-v1-e2e-live-test-key-value-never-printed-0123456789"


def _git_seed(root: pathlib.Path, *, dirty: bool = False) -> pathlib.Path:
    seed = root / "seed"
    seed.mkdir()
    (seed / "VERSION").write_text("7.0.0-test\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(seed), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(seed), check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e.invalid", "commit", "-q", "-m", "seed"],
                   cwd=str(seed), check=True)
    if dirty:
        (seed / "VERSION").write_text("7.0.0-dirty\n", encoding="utf-8")  # a TRACKED edit: describe says -dirty
    return seed


def _fake_lane(job, args, out, template, stagger, states, seed):
    sid, attempt = job
    lane = out / "lanes" / f"{sid}_a{attempt}"
    lane.mkdir(parents=True)
    row = {"scenario": sid, "attempt": attempt, "status": "pass", "checks": {"fake": True}, "error": "",
           "duration_sec": 0.1, "model_slots": {"OUROBOROS_MODEL": template.get("OUROBOROS_MODEL")}}
    (lane / "result.json").write_text(json.dumps(row), encoding="utf-8")
    return row


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


def _fake_urlopen(key_body: bytes, credits_body: bytes, calls: list):
    def fake(req, timeout=0):
        assert req.headers["Authorization"] == f"Bearer {FAKE_KEY}"
        calls.append(req.full_url)
        return _Response(key_body if req.full_url.endswith("/key") else credits_body)
    return fake


# --------------------------------------------------------------------------- #
# Structure: the shared launcher gate, the table, the argv bounds
# --------------------------------------------------------------------------- #

def test_launcher_passes_the_shared_structural_gate():
    """Admission is the outer boundary, confinement follows the handed seed, only the seam
    publishes the manifest — the SAME gate the benchmark family is held to, by source."""
    source = (REPO_ROOT / "devtools" / "e2e_live" / "run_live_lanes.py").read_text(encoding="utf-8")
    assert launcher_audit.audit_source(source, name="run_live_lanes.py") == []


def test_scenario_table_shape():
    assert set(scenarios.SCENARIOS) == {"SM1", "SW1", "SK1"}
    for sid, row in scenarios.SCENARIOS.items():
        assert row.id == sid and row.prompt.strip() and row.title.strip()
        assert isinstance(row.settings_overrides, dict)
        assert callable(row.acceptance) and callable(row.stub_script)
    sm1 = scenarios.SCENARIOS["SM1"].settings_overrides
    assert sm1 == {"OUROBOROS_RUNTIME_MODE": "advanced", "OUROBOROS_REVIEW_ENFORCEMENT": "blocking"}
    roster = json.loads(scenarios.SCENARIOS["SW1"].overrides("openai-compatible::mock-child")["OUROBOROS_SUBAGENTS"])
    assert roster["items"][0]["route"]["target_id"] == "openai-compatible::mock-child"
    assert scenarios.SCENARIOS["SW1"].overrides("x")["OUROBOROS_MAX_SUBAGENT_DEPTH"] == 1
    # Stub scripts are role-keyed queues; SW1 needs every role the swarm wire interleaves.
    sw1 = scenarios.SCENARIOS["SW1"].stub_script(REPO_ROOT)
    assert set(sw1) == {"router", "agent", "child", "probe"}
    assert [s["tool"] for s in sw1["agent"] if isinstance(s, dict) and "tool" in s] == [
        "plan_task", "schedule_subagent", "schedule_subagent"]
    assert sum(1 for s in sw1["agent"] if callable(s)) == 3  # wait_tasks + two child dispositions


def test_css_accent_helpers_only_touch_the_root_token():
    css = ":root {\n    --accent: #c93545;\n    --accent-light: #f07a86;\n}\n.x { --accent: red; }\n"
    assert scenarios.accent_value(css) == "#c93545"
    changed = scenarios.css_with_accent(css, "#2f7de1")
    assert scenarios.accent_value(changed) == "#2f7de1"
    assert changed.count("#2f7de1") == 1 and "--accent-light: #f07a86" in changed and "--accent: red" in changed


def test_lane_count_and_stagger_bounds(monkeypatch):
    monkeypatch.setattr(run_live_lanes.tempfile, "gettempdir", lambda: "/tmp/short")
    assert run_live_lanes.parse_args(["--stub"]).lanes == 4
    assert run_live_lanes.parse_args(["--stub", "--lanes", "6"]).lanes == 6
    with pytest.raises(SystemExit):
        run_live_lanes.parse_args(["--stub", "--lanes", "7"])
    with pytest.raises(SystemExit):
        run_live_lanes.parse_args(["--stub", "--lanes", "0"])
    assert run_live_lanes.parse_args(["--stub", "--stagger", "10"]).stagger == 3.0
    assert run_live_lanes.parse_args(["--stub", "--stagger", "0.1"]).stagger == 2.0
    assert run_live_lanes.parse_args(["--stub", "--stagger", "2.4"]).stagger == 2.4
    with pytest.raises(SystemExit):
        run_live_lanes.parse_args(["--stub", "--model", "x/y"])
    with pytest.raises(SystemExit):
        run_live_lanes.parse_args(["--stub", "--scenarios", "SM1,NOPE"])
    with pytest.raises(SystemExit):
        run_live_lanes.parse_args(["--stub", "--attempts", "2", "--pass-of", "3"])
    args = run_live_lanes.parse_args(["--total-budget", "30"])
    assert args.min_credit_usd == 30.0 and args.key_env == run_live_lanes.DEFAULT_KEY_ENV


def test_tmpdir_length_guard_refuses_fail_closed(monkeypatch):
    monkeypatch.setattr(run_live_lanes.tempfile, "gettempdir", lambda: "/tmp/" + "x" * 80)
    with pytest.raises(SystemExit):
        run_live_lanes.parse_args(["--stub"])


# --------------------------------------------------------------------------- #
# Effective settings: the tree's defaults, the budget knobs as settings keys, no env guesses
# --------------------------------------------------------------------------- #

def test_budget_and_per_task_caps_are_written_into_the_applied_settings(monkeypatch):
    monkeypatch.setattr(run_live_lanes.tempfile, "gettempdir", lambda: "/tmp/short")
    args = run_live_lanes.parse_args(["--total-budget", "30", "--per-task-usd", "8"])
    cfg = run_live_lanes.effective_settings(args, FAKE_KEY)
    assert cfg["TOTAL_BUDGET"] == 30.0 and cfg["OUROBOROS_PER_TASK_COST_USD"] == 8.0
    assert cfg["OPENROUTER_API_KEY"] == FAKE_KEY and cfg["OUROBOROS_RUNTIME_MODE"] == "advanced"
    from ouroboros.provider_models import declared_model_settings
    for key, value in declared_model_settings({}).items():
        assert cfg[key] == value  # D-09: the defaults of the tree under test, written explicitly
    pinned = run_live_lanes.effective_settings(run_live_lanes.parse_args(["--model", "argv/model-y"]), FAKE_KEY)
    assert pinned["OUROBOROS_MODEL"] == "argv/model-y"


def test_self_mod_is_off_by_default(monkeypatch):
    monkeypatch.setattr(run_live_lanes.tempfile, "gettempdir", lambda: "/tmp/short")
    off = run_live_lanes.effective_settings(run_live_lanes.parse_args(["--stub"]), "")
    assert off["OUROBOROS_POST_TASK_EVOLUTION"] == "false" and "OUROBOROS_POST_TASK_EVOLUTION_CADENCE" not in off
    on = run_live_lanes.effective_settings(run_live_lanes.parse_args(["--stub", "--self-mod"]), "")
    assert on["OUROBOROS_POST_TASK_EVOLUTION"] == "true" and on["OUROBOROS_POST_TASK_EVOLUTION_CADENCE"] == "every_n:1"


def test_stub_template_carries_only_the_loopback_slots(monkeypatch):
    monkeypatch.setattr(run_live_lanes.tempfile, "gettempdir", lambda: "/tmp/short")
    cfg = run_live_lanes.effective_settings(run_live_lanes.parse_args(["--stub"]), "")
    assert cfg["OUROBOROS_MODEL"] == stub_lane.STUB_MODEL_SLUG == cfg["OUROBOROS_MODEL_LIGHT"]
    assert not any(k.startswith("OUROBOROS_MODEL") and v and v != stub_lane.STUB_MODEL_SLUG for k, v in cfg.items())
    assert "OPENROUTER_API_KEY" not in cfg


def test_config_sha256_is_secret_free_and_key_independent():
    base = {"OUROBOROS_MODEL": "m", "TOTAL_BUDGET": 1.0}
    a = run_live_lanes.config_sha256({**base, "OPENROUTER_API_KEY": "key-one"})
    b = run_live_lanes.config_sha256({**base, "OPENROUTER_API_KEY": "key-two"})
    assert a != b  # a different key is a different (fingerprinted) config...
    assert a == run_live_lanes.config_sha256({**base, "OPENROUTER_API_KEY": "key-one"})
    assert a != run_live_lanes.config_sha256({**base, "OPENROUTER_API_KEY": "key-one", "TOTAL_BUDGET": 2.0})


# --------------------------------------------------------------------------- #
# Admission and the typed refusals (persisted manifest, no footprint)
# --------------------------------------------------------------------------- #

def test_dirty_seed_refuses_and_persists_the_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(run_live_lanes.tempfile, "gettempdir", lambda: "/tmp/short")
    seed = _git_seed(tmp_path, dirty=True)
    out = tmp_path / "out"
    with pytest.raises(BenchmarkAdmissionRefused):
        run_live_lanes.main(["--stub", "--seed", str(seed), "--out", str(out)])
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["extra"]["outcome"] == "refused"
    assert manifest["extra"]["refusal"] == {"stage": "seed_gate", "reason": "seed_dirty", "exit_code": 1}
    assert manifest["seed_gate"]["describe"].endswith("-dirty")
    assert not (out / "lanes").exists() and not (out / "effective_settings.json").exists()


def test_run_root_confinement_refuses_before_anything_is_created(tmp_path, monkeypatch):
    monkeypatch.setattr(run_live_lanes.tempfile, "gettempdir", lambda: "/tmp/short")
    seed = _git_seed(tmp_path)
    with pytest.raises(ValueError, match="must not be under repo/"):
        run_live_lanes.main(["--stub", "--seed", str(seed), "--out", str(seed / "inside")])
    assert not (seed / "inside").exists()


def test_missing_key_env_is_a_typed_refusal_before_any_lane_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(run_live_lanes.tempfile, "gettempdir", lambda: "/tmp/short")
    monkeypatch.delenv("E2E_TEST_KEY_ENV", raising=False)
    monkeypatch.setattr(run_live_lanes, "run_lane", lambda *a, **k: pytest.fail("a lane started without a key"))
    seed = _git_seed(tmp_path)
    out = tmp_path / "out"
    rc = run_live_lanes.main(["--seed", str(seed), "--out", str(out), "--key-env", "E2E_TEST_KEY_ENV"])
    assert rc == 3
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["extra"]["outcome"] == "refused" and manifest["extra"]["exit_code"] == 3
    assert manifest["extra"]["refusal"] == {"stage": "credential", "reason": "key_env_absent", "env": "E2E_TEST_KEY_ENV"}
    assert not (out / "lanes").exists() and not (out / "effective_settings.json").exists()


def test_credit_preflight_takes_the_min_of_both_planes(tmp_path, monkeypatch):
    """Key limit says $50, the account behind it holds $1: the run is bounded by $1 and refused
    below a $5 floor. Both numbers are recorded; the key value never is."""
    monkeypatch.setattr(run_live_lanes.tempfile, "gettempdir", lambda: "/tmp/short")
    monkeypatch.setenv("E2E_TEST_KEY_ENV", FAKE_KEY)
    monkeypatch.setattr(run_live_lanes, "run_lane", lambda *a, **k: pytest.fail("a lane started under the floor"))
    calls: list = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(
        b'{"data":{"limit_remaining":50.0}}', b'{"data":{"total_credits":10.0,"total_usage":9.0}}', calls))
    seed = _git_seed(tmp_path)
    out = tmp_path / "out"
    rc = run_live_lanes.main(["--seed", str(seed), "--out", str(out), "--key-env", "E2E_TEST_KEY_ENV",
                              "--min-credit-usd", "5"])
    assert rc == 3
    assert calls == ["https://openrouter.ai/api/v1/key", "https://openrouter.ai/api/v1/credits"]
    raw = (out / "run_manifest.json").read_bytes()
    assert FAKE_KEY.encode() not in raw
    manifest = json.loads(raw)
    refusal = manifest["extra"]["refusal"]
    assert refusal["stage"] == "credit_preflight" and refusal["reason"] == "insufficient_remaining"
    assert refusal["remaining_usd"] == 1.0 and refusal["key_limit_remaining_usd"] == 50.0
    assert refusal["account_credits_usd"] == 1.0 and refusal["floor_usd"] == 5.0
    assert manifest["extra"]["credential_fingerprint"].startswith("sha256:")


def test_openrouter_account_credits_is_the_second_bound_only(monkeypatch):
    from devtools.benchmarks.common.manifests import openrouter_account_credits, openrouter_key_remaining

    calls: list = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(
        b'{"data":{"limit":null}}', b'{"data":{"total_credits":12.5,"total_usage":2.5}}', calls))
    assert openrouter_key_remaining(FAKE_KEY) is None            # uncapped key: not "$0", not "plenty"
    assert openrouter_account_credits(FAKE_KEY) == 10.0
    assert run_live_lanes.credit_preflight(FAKE_KEY)["remaining_usd"] == 10.0
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(b'{"data":{"limit":null}}', b'{"data":{}}', []))
    assert run_live_lanes.credit_preflight(FAKE_KEY) == {
        "key_limit_remaining_usd": None, "account_credits_usd": None, "remaining_usd": None}


# --------------------------------------------------------------------------- #
# The manifest names the APPLIED model; secrets stay out of every artifact
# --------------------------------------------------------------------------- #

def _fake_run(tmp_path, monkeypatch, argv: list[str]) -> tuple[pathlib.Path, dict]:
    monkeypatch.setattr(run_live_lanes.tempfile, "gettempdir", lambda: "/tmp/short")
    monkeypatch.setenv("E2E_TEST_KEY_ENV", FAKE_KEY)
    monkeypatch.setattr(run_live_lanes, "run_lane", _fake_lane)
    monkeypatch.setattr(run_live_lanes, "credit_preflight", lambda key: {
        "key_limit_remaining_usd": None, "account_credits_usd": None, "remaining_usd": None})
    seed = _git_seed(tmp_path)
    out = tmp_path / "out"
    rc = run_live_lanes.main(["--seed", str(seed), "--out", str(out), "--key-env", "E2E_TEST_KEY_ENV",
                              "--watch-interval", "600", *argv])
    assert rc == 0
    return out, json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))


def test_manifest_names_the_effective_model_not_argv(tmp_path, monkeypatch):
    """EQUALITY pin: the manifest's model is the one in the APPLIED settings file. argv pins
    model Y, the applied file carries X -> the manifest says X."""
    real = run_live_lanes.effective_settings

    def applied_differs(args, key):
        return {**real(args, key), "OUROBOROS_MODEL": "applied/model-x"}

    monkeypatch.setattr(run_live_lanes, "effective_settings", applied_differs)
    out, manifest = _fake_run(tmp_path, monkeypatch, ["--model", "argv/model-y", "--scenarios", "SM1"])
    applied = json.loads((out / "effective_settings.json").read_text(encoding="utf-8"))
    assert manifest["model_slots"]["OUROBOROS_MODEL"] == applied["OUROBOROS_MODEL"] == "applied/model-x"
    assert manifest["extra"]["effective_model"] == "applied/model-x"
    assert "argv/model-y" not in json.dumps(manifest["model_slots"])


def test_manifest_and_result_artifacts_never_carry_the_key_value(tmp_path, monkeypatch):
    out, manifest = _fake_run(tmp_path, monkeypatch, ["--model", "argv/model-y", "--scenarios", "SM1,SK1",
                                                      "--attempts", "2", "--pass-of", "2", "--lanes", "2"])
    assert manifest["model_slots"]["OUROBOROS_MODEL"] == "argv/model-y"
    settings_path = out / "effective_settings.json"
    assert (settings_path.stat().st_mode & 0o777) == 0o600
    assert json.loads(settings_path.read_text(encoding="utf-8"))["OPENROUTER_API_KEY"] == FAKE_KEY
    for artifact in (out / "run_manifest.json", *out.glob("lanes/*/result.json")):
        assert FAKE_KEY.encode() not in artifact.read_bytes(), artifact
    granted = manifest["provider_credentials"]["granted"]
    assert granted["OPENROUTER_API_KEY"]["present"] is True
    assert granted["OPENROUTER_API_KEY"]["fingerprint"].startswith("sha256:")
    assert manifest["requested_task_ids"] == ["SM1_a1", "SM1_a2", "SK1_a1", "SK1_a2"]
    assert manifest["extra"]["scenarios"] == {"SM1": {"attempts": 2, "passed": 2, "infra_errors": 0, "verdict": "pass"},
                                              "SK1": {"attempts": 2, "passed": 2, "infra_errors": 0, "verdict": "pass"}}
    assert manifest["extra"]["outcome"] == "completed" and manifest["extra"]["exit_code"] == 0
    assert manifest["extra"]["total_budget_usd"] == 100.0 and manifest["extra"]["per_task_usd"] == 8.0


def test_stagger_gate_spaces_lane_starts(monkeypatch):
    clock = {"t": 100.0}
    slept: list = []
    monkeypatch.setattr(run_live_lanes.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(run_live_lanes.time, "sleep", lambda s: slept.append(s))
    gate = run_live_lanes.Stagger(2.5)
    gate.wait_turn()
    gate.wait_turn()
    clock["t"] += 1.0
    gate.wait_turn()
    assert slept == [0.0, 2.5, 1.5]


def test_ui_client_degrades_typed_without_playwright(monkeypatch):
    monkeypatch.setattr(ui_probe, "_suite_client", lambda base_url: None)
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    assert ui_probe.resolve_ui_client("http://127.0.0.1:1") == (None, "ui_unavailable:playwright_not_installed")


def test_ui_client_prefers_the_suite_interface_when_it_has_this_surface(monkeypatch):
    class Landed:
        def __init__(self, base_url):
            self.base_url = base_url
            self.opened = False

        def open(self):
            self.opened = True
            return self

        def goto(self, path="/"): ...
        def computed_property(self, selector, prop): ...
        def send_chat(self, text, *, swarm=False): ...
        def screenshot(self, path): ...
        def rebind(self, base_url): ...
        def close(self): ...

    fake = type(sys)("tests.system_e2e.interfaces")
    fake.PlaywrightUIClient = Landed
    monkeypatch.setitem(sys.modules, "tests.system_e2e.interfaces", fake)
    client, reason = ui_probe.resolve_ui_client("http://127.0.0.1:1")
    assert isinstance(client, Landed) and client.opened and reason == ""


# --------------------------------------------------------------------------- #
# The keyless rehearsal: SM1 end-to-end on a real isolated server (--stub)
# --------------------------------------------------------------------------- #

@pytest.mark.integration
@pytest.mark.serial
def test_stub_sm1_end_to_end_on_a_real_isolated_server(tmp_path):
    """Real server, loopback stub model, no key: the commit lands through the review organ,
    the durable rows and receipts exist, and the manifest names the stub as the model."""
    if str(os.environ.get("OUROBOROS_E2E_DEEP") or "").strip().lower() != "mock":
        pytest.skip("set OUROBOROS_E2E_DEEP=mock to run the stub rehearsal (spawns a real isolated server)")
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "--no-hardlinks", "-q", str(REPO_ROOT), str(seed)], check=True)
    out = tmp_path / "out"
    rc = run_live_lanes.main(["--stub", "--lanes", "1", "--scenarios", "SM1", "--seed", str(seed),
                              "--out", str(out), "--watch-interval", "600"])
    row = json.loads((out / "lanes" / "SM1_a1" / "result.json").read_text(encoding="utf-8"))
    failed = sorted(k for k, v in row["checks"].items() if not v and not k.startswith("ui_"))
    assert rc == 0 and failed == [], (row["status"], failed, row["error"])
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_slots"]["OUROBOROS_MODEL"] == stub_lane.STUB_MODEL_SLUG == row["model_slots"]["OUROBOROS_MODEL"]
    assert row["digests"]["pre_head"] != row["digests"]["post_head"] and len(row["digests"]["diff_sha256"]) == 64
    assert not row["digests"]["seed_describe"].endswith("-dirty")
    assert (out / "result_index.jsonl").read_text(encoding="utf-8").count("\n") == 1
