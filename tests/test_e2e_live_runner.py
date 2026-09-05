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
import threading
import time
import urllib.error
import urllib.request

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from devtools.benchmarks.common import launcher_audit  # noqa: E402
from devtools.benchmarks.common.manifests import repo_provenance  # noqa: E402
from devtools.e2e_live import run_live_lanes, scenarios, stub_lane, ui_probe  # noqa: E402

FAKE_KEY = "sk-or-v1-e2e-live-test-key-value-never-printed-0123456789"


def _commit(repo: pathlib.Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e.invalid", "commit", "-q", "-m", message],
                   cwd=str(repo), check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), check=True,
                          capture_output=True, text=True).stdout.strip()


def _git_seed(root: pathlib.Path, *, dirty: bool = False) -> pathlib.Path:
    """A tiny SOURCE checkout: one committed VERSION, optionally a TRACKED uncommitted edit."""
    seed = root / "source"
    seed.mkdir()
    (seed / "VERSION").write_text("7.0.0-test\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(seed), check=True)
    _commit(seed, "seed")
    if dirty:
        (seed / "VERSION").write_text("7.0.0-dirty\n", encoding="utf-8")  # describe says -dirty
    return seed


def _fake_lane(job, args, out, template, stagger, states, seed, budget=None, *, key="", seed_sha=""):
    sid, attempt = job
    lane = out / "lanes" / f"{sid}_a{attempt}"
    ceiling = budget.ceiling(job) if budget is not None else None   # the real lane reads it before spending
    lane.mkdir(parents=True)
    row = {"scenario": sid, "attempt": attempt, "status": "pass", "checks": {"fake": True}, "error": "",
           "duration_sec": 0.1, "model_slots": {"OUROBOROS_MODEL": template.get("OUROBOROS_MODEL")},
           "lane_total_budget_usd": ceiling,
           "template_has_key": "OPENROUTER_API_KEY" in template, "key_handed": bool(key), "seed_sha": seed_sha}
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


def _short_tmp(monkeypatch) -> None:
    monkeypatch.setattr(run_live_lanes.tempfile, "gettempdir", lambda: "/tmp/short")


# --------------------------------------------------------------------------- #
# Structure: the shared launcher gate, the table, the argv bounds
# --------------------------------------------------------------------------- #

def test_launcher_passes_the_shared_structural_gate():
    """Admission is the outer boundary, confinement follows the handed source, only the seam
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
    # The budget reservation unit: SK1 mints two root tasks (author + dispatch), the others one
    # (SW1's scouts spend under their single root's ceiling).
    assert {sid: row.root_tasks for sid, row in scenarios.SCENARIOS.items()} == {"SM1": 1, "SW1": 1, "SK1": 2}
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
    _short_tmp(monkeypatch)
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
    assert args.seed == "HEAD" and args.source_repo == ""


def test_money_and_interval_arguments_must_be_finite_and_positive(monkeypatch):
    """A non-positive TOTAL_BUDGET means NO cap to the runtime and a non-positive tick is a hot
    loop: both are argument-shaped refusals, before anything touches the world."""
    _short_tmp(monkeypatch)
    for argv in (["--total-budget", "0"], ["--total-budget", "-5"], ["--total-budget", "inf"],
                 ["--total-budget", "nan"], ["--per-task-usd", "0"], ["--min-credit-usd", "0"],
                 ["--min-credit-usd", "inf"], ["--watch-interval", "0"], ["--watch-interval", "-1"],
                 ["--watch-interval", "nan"], ["--watch-interval", "1"], ["--seed", " "]):
        with pytest.raises(SystemExit):
            run_live_lanes.parse_args(["--stub", *argv])
    ok = run_live_lanes.parse_args(["--stub", "--watch-interval", str(run_live_lanes.WATCH_INTERVAL_MIN_SEC)])
    assert ok.watch_interval == run_live_lanes.WATCH_INTERVAL_MIN_SEC


def test_tmpdir_length_guard_refuses_fail_closed(monkeypatch):
    monkeypatch.setattr(run_live_lanes.tempfile, "gettempdir", lambda: "/tmp/" + "x" * 80)
    with pytest.raises(SystemExit):
        run_live_lanes.parse_args(["--stub"])


# --------------------------------------------------------------------------- #
# Effective settings: the tree's defaults, the budget knobs as settings keys, no env guesses
# --------------------------------------------------------------------------- #

def test_budget_and_per_task_caps_are_written_into_the_applied_settings(monkeypatch):
    _short_tmp(monkeypatch)
    args = run_live_lanes.parse_args(["--total-budget", "30", "--per-task-usd", "8"])
    cfg = run_live_lanes.effective_settings(args, FAKE_KEY)
    assert cfg["TOTAL_BUDGET"] == 30.0 and cfg["OUROBOROS_PER_TASK_COST_USD"] == 8.0
    assert cfg["OPENROUTER_API_KEY"] == FAKE_KEY and cfg["OUROBOROS_RUNTIME_MODE"] == "advanced"
    from ouroboros.provider_models import declared_model_settings
    for key, value in declared_model_settings({}).items():
        assert cfg[key] == value  # the defaults of the tree under test, written explicitly
    pinned = run_live_lanes.effective_settings(run_live_lanes.parse_args(["--model", "argv/model-y"]), FAKE_KEY)
    assert pinned["OUROBOROS_MODEL"] == "argv/model-y"
    # The run-root copy is redacted: the key survives only in memory and in the lane files.
    redacted = run_live_lanes.redacted_template(cfg)
    assert "OPENROUTER_API_KEY" not in redacted and redacted["OUROBOROS_MODEL"] == cfg["OUROBOROS_MODEL"]
    assert run_live_lanes.template_credentials(cfg) == {"OPENROUTER_API_KEY": FAKE_KEY}


def test_self_mod_is_off_by_default(monkeypatch):
    _short_tmp(monkeypatch)
    off = run_live_lanes.effective_settings(run_live_lanes.parse_args(["--stub"]), "")
    assert off["OUROBOROS_POST_TASK_EVOLUTION"] == "false" and "OUROBOROS_POST_TASK_EVOLUTION_CADENCE" not in off
    on = run_live_lanes.effective_settings(run_live_lanes.parse_args(["--stub", "--self-mod"]), "")
    assert on["OUROBOROS_POST_TASK_EVOLUTION"] == "true" and on["OUROBOROS_POST_TASK_EVOLUTION_CADENCE"] == "every_n:1"


def test_preflight_worker_cap_reaches_every_lane_and_is_recorded(tmp_path, monkeypatch):
    """The commit gate's hermetic pytest pass runs INSIDE the lane server and resolves ``-n auto``
    to the host CPU count (the 2026-09-04 paid run fanned out to >= 104 xdist workers per lane): the
    stand must set the runtime's own lever to ``max(2, 16 // lanes)`` in the process every lane
    server inherits, override an ambient value, and record the applied number in the manifest
    and in each lane row. The runtime reads exactly that key (pinned here, not modified)."""
    from ouroboros import preflight_runner
    _short_tmp(monkeypatch)
    assert run_live_lanes.PREFLIGHT_WORKERS_ENV == preflight_runner._PREFLIGHT_WORKERS_ENV
    assert run_live_lanes.PREFLIGHT_WORKERS_FLOOR == preflight_runner._MIN_PREFLIGHT_WORKERS
    assert run_live_lanes.parse_args(["--stub", "--lanes", "1"]).preflight_test_workers == 16
    assert run_live_lanes.parse_args(["--stub", "--lanes", "4"]).preflight_test_workers == 4
    assert run_live_lanes.parse_args(["--stub", "--lanes", "6"]).preflight_test_workers == 2   # floor at MAX_LANES
    monkeypatch.setenv(run_live_lanes.PREFLIGHT_WORKERS_ENV, "128")   # the operator shell must lose
    seen: dict = {}

    def lane(job, args, out, template, stagger, states, seed, budget=None, *, key="", seed_sha=""):
        seen[job] = (os.environ.get(run_live_lanes.PREFLIGHT_WORKERS_ENV),
                     run_live_lanes._lane_row(job, args)["preflight_test_workers"],
                     preflight_runner._preflight_worker_count())
        return _fake_lane(job, args, out, template, stagger, states, seed, budget, key=key, seed_sha=seed_sha)

    monkeypatch.setattr(run_live_lanes, "run_lane", lane)
    out = tmp_path / "out"
    rc = run_live_lanes.main(["--stub", "--source-repo", str(_git_seed(tmp_path)), "--out", str(out),
                              "--scenarios", "SM1,SW1", "--lanes", "3", "--watch-interval", "600"])
    assert rc == 0
    assert seen == {("SM1", 1): ("5", 5, 5), ("SW1", 1): ("5", 5, 5)}
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["extra"]["lanes"] == 3 and manifest["extra"]["preflight_test_workers"] == 5


def test_isolated_server_forwards_the_preflight_worker_cap_through_the_authoritative_sweep(tmp_path, monkeypatch):
    """The lane servers start in settings-authoritative mode, which strips the whole OUROBOROS_
    namespace; the worker cap is the one operational lever that must survive, while an ambient
    model slot still does not."""
    from devtools.benchmarks.common.server_runner import _AUTHORITATIVE_ENV_KEEP, IsolatedServer
    assert run_live_lanes.PREFLIGHT_WORKERS_ENV in _AUTHORITATIVE_ENV_KEEP
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(run_live_lanes.PREFLIGHT_WORKERS_ENV, "4")
    monkeypatch.setenv("OUROBOROS_MODEL", "ambient/model")
    env = IsolatedServer(tmp_path / "clone", tmp_path / "data", settings, settings_authoritative_env=True)._env()
    assert env[run_live_lanes.PREFLIGHT_WORKERS_ENV] == "4" and "OUROBOROS_MODEL" not in env


def test_stub_template_carries_only_the_loopback_slots(monkeypatch):
    _short_tmp(monkeypatch)
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
# The run-wide budget ledger
# --------------------------------------------------------------------------- #

def test_lane_spend_sums_durable_llm_usage_rows_and_counts_unknown_costs(tmp_path):
    logs = tmp_path / "data" / "logs"
    logs.mkdir(parents=True)
    rows = [{"type": "llm_usage", "cost": 1.5}, {"type": "llm_usage", "cost": 0.25},
            {"type": "llm_usage", "cost": None, "cost_known": False}, {"type": "task_done", "cost": 99.0},
            {"type": "llm_usage", "cost": True}]
    (logs / "events.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert run_live_lanes.lane_spend(tmp_path / "data") == (1.75, 2)
    assert run_live_lanes.lane_spend(tmp_path / "absent") == (0.0, 0)


def test_run_budget_reservation_rule_halts_new_attempts_at_the_cap(tmp_path):
    """spent (durable, re-read) + reserved (in flight) + this attempt's reservation must fit the
    cap; the first refusal halts the rest of the run; a lane's TOTAL_BUDGET is its OWN reservation,
    so the ceilings of the lanes in flight are disjoint and settled spend + in-flight ceilings
    never exceeds the cap (the first draft handed each lane cap - others' reservations: two
    concurrent lanes could sum above the cap)."""
    spend = {}
    budget = run_live_lanes.RunBudget(20.0, 8.0, reader=lambda root: (spend.get(root.name, 0.0), 0))
    assert budget.reservation(1) == 8.0 and budget.reservation(2) == 16.0 and budget.reservation(0) == 8.0
    ok, facts = budget.admit(("SM1", 1), 1, tmp_path / "a")
    assert ok and facts == {"cap_usd": 20.0, "spent_usd": 0.0, "reserved_usd": 0.0, "reservation_usd": 8.0,
                            "unknown_cost_rows": 0}
    assert budget.ceiling(("SM1", 1)) == 8.0            # its own reservation, never the whole cap
    ok, facts = budget.admit(("SW1", 1), 1, tmp_path / "b")
    assert ok and facts["reserved_usd"] == 8.0
    assert budget.ceiling(("SW1", 1)) == 8.0            # disjoint from lane a: 8 + 8 + spent 0 <= cap 20
    assert budget.ceiling(("SM1", 1)) + budget.ceiling(("SW1", 1)) <= 20.0
    spend["a"] = 5.0                                    # lane a spends while in flight: visible now
    ok, facts = budget.admit(("SK1", 1), 2, tmp_path / "c")   # 5 + 16 + 16 > 20
    assert not ok and facts["spent_usd"] == 5.0 and facts["halt"]["first_refused"] == "SK1_a1"
    ok, _facts = budget.admit(("SM1", 2), 1, tmp_path / "d")  # would fit (5+16+8 > 20: no) - halted anyway
    assert not ok and budget.not_run == ["SK1_a1", "SM1_a2"]
    budget.settle(("SM1", 1))
    budget.settle(("SW1", 1))
    snap = budget.snapshot()
    assert snap["spent_usd"] == 5.0 and snap["reserved_usd"] == 0.0 and snap["lanes_settled"] == 2
    assert snap["halted"] and snap["halt"]["reason"] == "budget_cap" and snap["attempts_not_run"] == ["SK1_a1", "SM1_a2"]
    assert snap["reservation_rule"] == run_live_lanes.RESERVATION_RULE
    # The ceiling ignores what OTHER lanes spend (it is this lane's reservation), and the floor
    # keeps it positive (the runtime reads a non-positive TOTAL_BUDGET as NO cap).
    tiny = run_live_lanes.RunBudget(10.0, 8.0, reader=lambda root: (20.0, 0))
    assert tiny.admit(("SM1", 1), 1, tmp_path / "x")[0]
    assert tiny.ceiling(("SM1", 1)) == 8.0
    assert tiny.ceiling(("never", 9)) == run_live_lanes.LANE_BUDGET_FLOOR_USD   # not admitted: the floor, not the cap
    # The floor is part of the ONE effective ceiling: admission reserves it, the lane receives it,
    # so micro reservations cannot sum past the cap (5 x 0.01 fit a 0.05 cap, the 6th is refused).
    micro = run_live_lanes.RunBudget(0.05, 0.001, reader=lambda root: (0.0, 0))
    assert micro.reservation(1) == run_live_lanes.LANE_BUDGET_FLOOR_USD
    for n in range(5):
        ok, facts = micro.admit(("SM1", n), 1, tmp_path / f"m{n}")
        assert ok and facts["reservation_usd"] == 0.01 and micro.ceiling(("SM1", n)) == 0.01
    assert not micro.admit(("SM1", 5), 1, tmp_path / "m5")[0]
    assert sum(micro.ceiling(("SM1", n)) for n in range(5)) <= 0.05
    below = run_live_lanes.RunBudget(0.005, 0.001, reader=lambda root: (0.0, 0))
    assert not below.admit(("SM1", 1), 1, tmp_path / "z")[0]     # the floored reservation exceeds the cap
    # Fractional reservations are never rounded upward (round(0.01006, 4) would hand out 0.0101):
    # two exact 0.01006 reservations fill a 0.02012 cap and each lane receives exactly 0.01006.
    frac = run_live_lanes.RunBudget(0.02012, 0.01006, reader=lambda root: (0.0, 0))
    assert frac.admit(("SM1", 1), 1, tmp_path / "f1")[0] and frac.admit(("SM1", 2), 1, tmp_path / "f2")[0]
    assert frac.ceiling(("SM1", 1)) == 0.01006 and frac.ceiling(("SM1", 2)) == 0.01006
    assert frac.ceiling(("SM1", 1)) + frac.ceiling(("SM1", 2)) <= 0.02012 and not frac.admit(("SM1", 3), 1, tmp_path / "f3")[0]


# --------------------------------------------------------------------------- #
# The watcher's key probe: informational, bounded, backing off, never on the tick's path
# --------------------------------------------------------------------------- #

def test_key_probe_failures_are_informational_and_back_off():
    stop = threading.Event()
    calls = {"n": 0}

    def flaky() -> float | None:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise TimeoutError("timed out")
        return 3.0

    probe = run_live_lanes.KeyProbe(flaky, floor=5.0, interval=30.0, stop=stop)
    assert probe.interval == run_live_lanes.PROBE_MIN_INTERVAL_SEC   # never more often than the floor
    assert probe.fragment() == "key probe pending"
    probe.poll_once()
    assert probe.failures == 1 and "ALERT" not in probe.fragment()
    assert probe.fragment().startswith("key probe failed: TimeoutError") and "informational" in probe.fragment()
    assert probe.next_wait() == 2 * run_live_lanes.PROBE_MIN_INTERVAL_SEC
    probe.poll_once()
    assert probe.failures == 2 and probe.next_wait() == 4 * run_live_lanes.PROBE_MIN_INTERVAL_SEC
    probe.failures = 10
    assert probe.next_wait() == run_live_lanes.PROBE_BACKOFF_MAX_SEC
    probe.poll_once()                                    # a good reading resets the back-off
    assert probe.failures == 0 and probe.next_wait() == run_live_lanes.PROBE_MIN_INTERVAL_SEC
    assert probe.fragment() == "key remaining $3.00 ALERT"   # ALERT only on a GOOD reading under the floor
    probe.seed(None)
    assert probe.fragment() == "key uncapped"


def test_watcher_tick_never_waits_on_the_key_probe(capsys):
    """A probe stuck in a provider call must not delay the tick: the watcher reads the probe's
    last fragment and prints the ledger's spend regardless."""
    stop, release = threading.Event(), threading.Event()

    def stuck() -> float | None:
        release.wait(10)
        return None

    probe = run_live_lanes.KeyProbe(stuck, floor=1.0, interval=30.0, stop=stop)
    probe.interval = 0.01
    probe.start()
    budget = run_live_lanes.RunBudget(50.0, 8.0, reader=lambda root: (2.5, 0))
    budget.admit(("SM1", 1), 1, pathlib.Path("/nonexistent/lane/data"))
    states = {("SM1", 1): ("running scenario", time.time())}
    thread = threading.Thread(target=run_live_lanes.watcher, args=(stop, states, 0.05, budget, probe), daemon=True)
    thread.start()
    seen = ""
    deadline = time.time() + 5
    while "[watch]" not in seen and time.time() < deadline:
        time.sleep(0.05)
        seen += capsys.readouterr().out
    stop.set()
    release.set()
    thread.join(timeout=5)
    line = next(ln for ln in seen.splitlines() if "[watch]" in ln)
    assert "spent $2.50/$50.00 reserved $8.00" in line and "SM1_a1=running scenario" in line
    assert "key probe pending" in line and "ALERT" not in line


# --------------------------------------------------------------------------- #
# Self-modification: a confirmed absorb, never an assumed one
# --------------------------------------------------------------------------- #

class _AbsorbServer:
    base_url = "http://127.0.0.1:1"

    def __init__(self, wait: dict, healthy: bool = True) -> None:
        self._wait, self._healthy = wait, healthy

    def wait_for_absorb(self, prev_sha, prev_absorbed, timeout=0):
        return dict(self._wait)

    def wait_for_health(self, timeout=0):
        return self._healthy


def _campaign(data_root: pathlib.Path, cycles: int, tx: dict | None = None) -> None:
    (data_root / "state").mkdir(parents=True, exist_ok=True)
    (data_root / "state" / "evolution_campaign.json").write_text(json.dumps(
        {"absorbed_cycles_done": cycles, "transaction_history": [tx] if tx else []}), encoding="utf-8")


def test_confirm_absorb_requires_positive_evidence(tmp_path, monkeypatch):
    clone = tmp_path / "clone"
    clone.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(clone), check=True)
    (clone / "f").write_text("1\n", encoding="utf-8")
    first = _commit(clone, "one")
    data_root = tmp_path / "data"
    _campaign(data_root, 0)
    state = {"sha": first[:8], "uptime": 100}
    monkeypatch.setattr(run_live_lanes, "_api", lambda base, method, path, payload=None, timeout=0: dict(state))
    pre = run_live_lanes.self_mod_snapshot(_AbsorbServer({}), clone, data_root)
    assert pre["head"] == first and pre["sha"] == first[:8] and pre["cycles"] == 0 and pre["state_read"]
    # No promotion: the runtime declined, and a liveness check alone would have said PASS.
    out = run_live_lanes.confirm_absorb(_AbsorbServer({"absorbed": False, "reason": "no_promotion"}), clone,
                                        data_root, pre, timeout=1, ready_timeout=1)
    assert out["confirmed"] is False and out["reason"] == "no_promotion" and out["head_moved"] is False
    # The wait said absorbed and the counter advanced, but the served uptime never reset: not restarted.
    (clone / "f").write_text("2\n", encoding="utf-8")
    second = _commit(clone, "two")
    _campaign(data_root, 1, {"commit_sha": second, "cycle_outcome": "absorbed", "restart_verified": True,
                             "verified_by": "boot_reconciliation"})
    state.update({"sha": second[:8], "uptime": 100})
    out = run_live_lanes.confirm_absorb(_AbsorbServer({"absorbed": True, "reason": "absorbed"}), clone,
                                        data_root, pre, timeout=1, ready_timeout=1)
    assert out["confirmed"] is False and out["reason"] == "not_restarted" and out["head_moved"] is True
    assert out["transaction"] == {"commit_sha": second, "cycle_outcome": "absorbed", "restart_verified": True,
                                  "verified_by": "boot_reconciliation"}
    # Every fact present: counter advanced, sha moved, uptime reset, healthy, serving the clone HEAD.
    state["uptime"] = 0
    out = run_live_lanes.confirm_absorb(_AbsorbServer({"absorbed": True, "reason": "absorbed"}), clone,
                                        data_root, pre, timeout=1, ready_timeout=1)
    assert out["confirmed"] is True and out["reason"] == "absorbed" and out["serving_head"] is True
    assert out["post"]["cycles"] == 1 and out["post"]["head"] == second
    unhealthy = run_live_lanes.confirm_absorb(_AbsorbServer({"absorbed": True, "reason": "absorbed"}, healthy=False),
                                              clone, data_root, pre, timeout=1, ready_timeout=1)
    assert unhealthy["confirmed"] is False and unhealthy["reason"] == "unhealthy"


# --------------------------------------------------------------------------- #
# Scenario contracts: per-task check keys, the dispatch verdict, typed refusal facts, SM1 parity
# --------------------------------------------------------------------------- #

class _FakeServer:
    base_url = "http://127.0.0.1:1"

    def __init__(self, status: str = "completed") -> None:
        self.status = status

    def wait_task(self, task_id, timeout=0):
        return {"status": self.status, "reason_code": "final_message" if self.status == "completed" else "deadline_local"}

    def cancel_task(self, task_id):
        return {}


class _FakeHarness:
    @staticmethod
    def wait_durable_result(oracle, task_id, timeout=0):
        return {"status": "completed", "reason_code": "final_message", "task_id": task_id}


def _ctx(server=None) -> scenarios.LaneContext:
    return scenarios.LaneContext(server=server or _FakeServer(), clone=pathlib.Path("/x"), data_root=pathlib.Path("/y"),
                                 oracle=None, harness=_FakeHarness(), ui=None, ui_reason="", shots=pathlib.Path("/s"),
                                 log=lambda m: None, task_timeout=1, restart=lambda: None)


def test_wait_task_namespaces_checks_per_task_and_check_refuses_overwrites():
    ctx = _ctx()
    ctx.wait_task("t1", label="author")
    ctx.wait_task("t2", label="dispatch")
    assert set(ctx.checks) == {"author_http_terminal_completed", "author_durable_terminal_completed",
                               "dispatch_http_terminal_completed", "dispatch_durable_terminal_completed"}
    assert all(ctx.checks.values())
    assert ctx.facts["author_terminal"]["task_id"] == "t1" and ctx.facts["dispatch_terminal"]["task_id"] == "t2"
    assert ctx.facts["author_http_status"] == "completed" and ctx.facts["runtime_result"]["task_id"] == "t2"
    with pytest.raises(scenarios.DuplicateCheckKey):
        ctx.wait_task("t3", label="author")
    with pytest.raises(scenarios.DuplicateCheckKey):
        ctx.check("author_http_terminal_completed", True)
    # An unlabeled await keeps the plain keys for single-task scenarios.
    plain = _ctx()
    plain.wait_task("t9")
    assert set(plain.checks) == {"http_terminal_completed", "durable_terminal_completed"}


def test_dispatch_verdict_requires_ok_status_and_the_exact_echo():
    gen = "f773dad013e846c793dccd7938188b46"
    failed = [{"tool": "ext_x", "status": "error", "result_preview": "boom",
               "tool_result_meta": {"extension_generation": gen, "physical_dispatch": True}}]
    verdict = scenarios.dispatch_verdict(failed, scenarios.SK1_ECHO_EXPECTED)
    assert verdict["generation_ok"] and verdict["status"] == "error" and not verdict["echo_ok"]
    good = [{"tool": "ext_x", "status": "ok", "result_preview": "echo: ping-e2e-live\n",
             "tool_result_meta": {"extension_generation": gen, "physical_dispatch": True}}]
    verdict = scenarios.dispatch_verdict(good, scenarios.SK1_ECHO_EXPECTED)
    assert verdict == {"row_present": True, "status": "ok", "generation": gen, "generation_ok": True,
                       "physical_dispatch": True, "echo_ok": True}
    assert scenarios.dispatch_verdict([], scenarios.SK1_ECHO_EXPECTED)["row_present"] is False
    assert scenarios.SK1_ECHO_EXPECTED == f"echo: {scenarios.SK1_ECHO_MESSAGE}"
    assert scenarios.SCENARIOS["SK1"].stub_script(REPO_ROOT)["agent"][4]["arguments"]["message"] == scenarios.SK1_ECHO_MESSAGE
    # The relayed line opens one owner-chat turn on the stub wire: a second closing final absorbs it.
    assert [list(s)[0] for s in scenarios.SCENARIOS["SK1"].stub_script(REPO_ROOT)["agent"]] == [
        "tool", "tool", "tool", "final", "tool", "final", "final"]


def test_sk1_fixture_declares_exactly_the_permissions_its_plugin_exercises():
    """The SK1 manifest is honest by construction: every declared permission maps to source the
    plugin actually runs, the ONLY owner-granted one (``inject_chat``) is what the stand grants,
    and the prose states that narrow purpose. The first paid run declared ``inject_chat`` over an
    echo-only plugin and the skill review refused it 3/3 on ``permissions_honesty`` +
    ``inject_chat_minimization`` — a fixture defect, so this pins the fixture, not the reviewer."""
    import ast

    from ouroboros.contracts.skill_manifest import parse_skill_manifest_text
    from ouroboros.skill_loader import requested_skill_permissions

    manifest = parse_skill_manifest_text(scenarios.SK1_SKILL_MD)
    assert manifest.name == scenarios.SK1_SKILL and manifest.type == "extension" and manifest.entry == "plugin.py"
    exercised_by = {   # permission -> the source that performs it
        "tool": "api.register_tool(",
        "inject_chat": "/chat/inject",
        "net": "urllib.request",
    }
    assert set(manifest.permissions) == set(exercised_by)
    for permission, marker in exercised_by.items():
        assert marker in scenarios.SK1_PLUGIN, (permission, marker)
    assert requested_skill_permissions(list(manifest.permissions)) == scenarios.SK1_GRANTS == ["inject_chat"]
    # Host-token discipline (checklist item 12): the token is revealed at the request site only.
    assert scenarios.SK1_PLUGIN.count("get_skill_token().use_in_request()") == 1
    assert "print(" not in scenarios.SK1_PLUGIN and "log(" not in scenarios.SK1_PLUGIN
    # Owner binding: the destination is a module constant, never a tool argument.
    assert f"OWNER_CHAT_ID = {scenarios.SK1_OWNER_CHAT_ID}" in scenarios.SK1_PLUGIN and scenarios.SK1_OWNER_CHAT_ID == 1
    assert "'chat_id': OWNER_CHAT_ID" in scenarios.SK1_PLUGIN
    tree = ast.parse(scenarios.SK1_PLUGIN)
    register_call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Attribute) and n.func.attr == "register_tool")
    schema = ast.literal_eval(next(k.value for k in register_call.keywords if k.arg == "schema"))
    assert set(schema["properties"]) == {"message"}
    # The prose names the purpose and no longer denies what the code does.
    body = scenarios.SK1_SKILL_MD.split("---", 2)[2]
    assert "/chat/inject" in body and f"chat_id {scenarios.SK1_OWNER_CHAT_ID}" in body and "127.0.0.1" in body
    assert "no host or network access" not in body


class _FakeSkillToken:
    def __init__(self, value: str) -> None:
        self._value = value

    def use_in_request(self) -> str:
        return self._value


class _FakeExtensionApi:
    """Only the two PluginAPI members the probe plugin touches."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.tools = {}

    def register_tool(self, name, handler, *, description, schema, timeout_sec=60):
        self.tools[name] = handler

    def get_skill_token(self):
        return _FakeSkillToken(self.token)


def _inject_sink(status: int, hits: list):
    """A loopback HTTP server standing in for the Host Service ``/chat/inject`` route."""
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            hits.append({"path": self.path, "token": self.headers.get("X-Skill-Token"),
                         "body": json.loads(body.decode("utf-8"))})
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *_args):  # keep pytest output clean
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_sk1_plugin_relays_one_bounded_line_into_the_owner_chat(monkeypatch):
    """The plugin text the model is told to write, executed: one POST to the loopback
    ``/chat/inject`` per call, owner chat pinned, the skill token only in the header, the text
    bounded, the same text returned — and a Host Service refusal surfaces as a tool error."""
    hits: list = []
    sink = _inject_sink(202, hits)
    try:
        monkeypatch.setenv("HOST_SERVICE_URL", f"http://127.0.0.1:{sink.server_port}")
        namespace: dict = {}
        exec(compile(scenarios.SK1_PLUGIN, "plugin.py", "exec"), namespace)  # noqa: S102 - the fixture under test
        api = _FakeExtensionApi("tok-e2e")
        namespace["register"](api)
        echo = api.tools["echo"]
        assert echo(None, message=scenarios.SK1_ECHO_MESSAGE) == scenarios.SK1_ECHO_EXPECTED
        assert hits == [{"path": "/chat/inject", "token": "tok-e2e", "body": {
            "text": scenarios.SK1_ECHO_EXPECTED, "chat_id": scenarios.SK1_OWNER_CHAT_ID,
            "sender_label": scenarios.SK1_SKILL}}]
        long = echo(None, message="x" * (scenarios.SK1_ECHO_MAX_CHARS + 50))
        assert long == hits[-1]["body"]["text"] == "echo: " + "x" * scenarios.SK1_ECHO_MAX_CHARS
        assert len(hits) == 2   # exactly one line per call, no retry
    finally:
        sink.shutdown()
    refusing = _inject_sink(403, [])
    try:
        monkeypatch.setenv("HOST_SERVICE_URL", f"http://127.0.0.1:{refusing.server_port}")
        with pytest.raises(urllib.error.HTTPError):
            echo(None, message="denied")
    finally:
        refusing.shutdown()
    # The acceptance reads the HOST's attribution of that line, never the plugin's claim.
    rows = [{"direction": "in", "chat_id": 1, "source": f"skill:{scenarios.SK1_SKILL}", "text": scenarios.SK1_ECHO_EXPECTED},
            {"direction": "out", "chat_id": 1, "source": f"skill:{scenarios.SK1_SKILL}", "text": scenarios.SK1_ECHO_EXPECTED},
            {"direction": "in", "chat_id": 1, "source": "web", "text": scenarios.SK1_ECHO_EXPECTED},
            {"direction": "in", "chat_id": 2, "source": f"skill:{scenarios.SK1_SKILL}", "text": scenarios.SK1_ECHO_EXPECTED},
            {"direction": "in", "chat_id": 1, "source": f"skill:{scenarios.SK1_SKILL}", "text": "echo: other"}]
    assert scenarios.owner_chat_relay_rows(rows, scenarios.SK1_SKILL, scenarios.SK1_ECHO_EXPECTED) == rows[:1]


def test_commit_refusal_facts_name_every_typed_refusal():
    ledger = {"attempts": [
        {"attempt": 1, "phase": "preflight", "status": "blocked", "block_reason": "tests_preflight_blocked"},
        {"attempt": 2, "phase": "blocking_review", "status": "blocked", "block_reason": "scope_blocked"},
        {"attempt": 3, "phase": "late_wait", "status": "reviewing", "block_reason": "review_late_result_pending"}],
        "advisory_runs": [{"status": "stale"}, {"status": "bypassed"}]}
    tools = [
        {"tool": "preflight_review", "status": "ok",
         "result_preview": '{\n  "status": "preflight_blocked",\n  "error": "⚠️ PREFLIGHT_BLOCKED: VERSION is not in scope'},
        {"tool": "commit_reviewed", "status": "blocked", "result_preview": "⚠️ TESTS_PREFLIGHT_BLOCKED: Tests must pass"},
        {"tool": "commit_reviewed", "status": "blocked", "result_preview": "⚠️ SCOPE_REVIEW_BLOCKED: the review pack"},
        {"tool": "write_file", "status": "ok", "result_preview": "⚠️ NOT_A_REVIEW_TOOL: ignored"},
        {"tool": "commit_reviewed", "status": "ok", "result_preview": "⚠️ REVIEW_PENDING: physical reviewer work"}]
    facts = scenarios.commit_refusal_facts(ledger, tools, {"status": "failed", "reason_code": "budget_exhausted"})
    assert facts["refusal_codes"] == ["PREFLIGHT_BLOCKED", "REVIEW_PENDING", "SCOPE_REVIEW_BLOCKED", "TESTS_PREFLIGHT_BLOCKED"]
    assert [a["block_reason"] for a in facts["commit_attempts"]] == [
        "tests_preflight_blocked", "scope_blocked", "review_late_result_pending"]
    assert facts["advisory_run_statuses"] == ["stale", "bypassed"]
    assert facts["review_tool_calls"][1] == {"tool": "commit_reviewed", "status": "blocked", "code": "TESTS_PREFLIGHT_BLOCKED"}
    assert facts["terminal_status"] == "failed" and facts["terminal_reason_code"] == "budget_exhausted"


def test_sm1_changes_both_stylesheets_and_keeps_the_mirror_parity():
    """web/onboarding.css mirrors web/style.css BY VALUE (tests/test_web_typography_static.py):
    the scenario edits, commits and validates both files, in the prompt, the stub and the
    acceptance. Both the paid prompt and the stub run the tests preflight that pins the
    invariant: neither carries ``skip_tests`` (the former loopback residual closed when
    ``preflight_runner._preflight_env`` began scrubbing every projected settings key)."""
    assert scenarios.SM1_CSS_PATHS == ("web/style.css", "web/onboarding.css")
    prompt = scenarios.sm1_prompt()
    assert "web/style.css" in prompt and "web/onboarding.css" in prompt and "['web/style.css', 'web/onboarding.css']" in prompt
    script = scenarios.sm1_stub_script(REPO_ROOT)["agent"]
    writes = [s for s in script if s.get("tool") == "write_file"]
    assert [w["arguments"]["path"] for w in writes] == list(scenarios.SM1_CSS_PATHS)
    commit = next(s for s in script if s.get("tool") == "commit_reviewed")["arguments"]
    assert commit["paths"] == list(scenarios.SM1_CSS_PATHS) and "skip_tests" not in prompt.lower()
    assert "skip_tests" not in commit, "the stub rehearsal must run the tests preflight like the paid prompt"
    edited = {w["arguments"]["path"]: w["arguments"]["content"] for w in writes}
    for path, text in edited.items():
        original = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert scenarios.accent_value(text) == scenarios.SM1_NEW_ACCENT and text != original
        assert len(text.splitlines()) == len(original.splitlines())
    assert scenarios.css_mirror_drift(edited["web/style.css"], edited["web/onboarding.css"]) == {}
    # The parser reads the SAME :root block the invariant reads, and the invariant is real.
    style_tokens = scenarios.css_root_tokens((REPO_ROOT / "web/style.css").read_text(encoding="utf-8"))
    onboarding_tokens = scenarios.css_root_tokens((REPO_ROOT / "web/onboarding.css").read_text(encoding="utf-8"))
    assert "--accent" in style_tokens and "--accent" in onboarding_tokens and len(set(style_tokens) & set(onboarding_tokens)) > 20
    lopsided = scenarios.css_with_accent(edited["web/style.css"], "#000000")
    assert scenarios.css_mirror_drift(lopsided, edited["web/onboarding.css"]) == {"--accent": ("#000000", scenarios.SM1_NEW_ACCENT)}


# --------------------------------------------------------------------------- #
# Seed: a clean detached clone of the requested ref, never the operator's live worktree
# --------------------------------------------------------------------------- #

def test_materialize_seed_is_a_clean_detached_clone_of_the_ref(tmp_path):
    source = _git_seed(tmp_path)
    first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(source), check=True, capture_output=True, text=True).stdout.strip()
    (source / "VERSION").write_text("7.0.1-test\n", encoding="utf-8")
    second = _commit(source, "bump")
    (source / "VERSION").write_text("7.0.2-wip\n", encoding="utf-8")   # dirty source, never under test
    seed = tmp_path / "seed"
    record = run_live_lanes.materialize_seed(source, "HEAD~1", seed)
    assert record["resolved_sha"] == first and record["policy"] == run_live_lanes.SEED_POLICY
    assert (seed / "VERSION").read_text(encoding="utf-8") == "7.0.0-test\n"
    detached = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=str(seed), check=False, capture_output=True)
    assert detached.returncode != 0   # no branch checked out
    provenance = repo_provenance(seed)
    assert run_live_lanes.seed_is_clean(provenance, first) and not run_live_lanes.seed_is_clean(provenance, second)
    assert not provenance["describe"].endswith("-dirty")
    with pytest.raises(run_live_lanes.SeedMaterializeRefused) as exists:
        run_live_lanes.materialize_seed(source, "HEAD", seed)
    assert exists.value.reason == "seed_dir_exists"
    with pytest.raises(run_live_lanes.SeedMaterializeRefused) as bogus:
        run_live_lanes.materialize_seed(source, "no-such-ref", tmp_path / "seed2")
    assert bogus.value.reason == "ref_unresolved"


def test_dirty_source_runs_the_committed_ref_from_a_clean_detached_seed(tmp_path, monkeypatch):
    _short_tmp(monkeypatch)
    monkeypatch.setattr(run_live_lanes, "run_lane", _fake_lane)
    source = _git_seed(tmp_path, dirty=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(source), check=True, capture_output=True, text=True).stdout.strip()
    out = tmp_path / "out"
    rc = run_live_lanes.main(["--stub", "--source-repo", str(source), "--out", str(out), "--scenarios", "SM1",
                              "--watch-interval", "600"])
    assert rc == 0
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"]["dirty"] is True and manifest["seed_gate"]["allow_dirty_seed"] is True
    assert manifest["seed"]["resolved_sha"] == head and manifest["seed"]["clean"] is True
    assert manifest["seed"]["requested_ref"] == "HEAD" and manifest["extra"]["seed_policy"] == run_live_lanes.SEED_POLICY
    assert manifest["extra"]["seed_head"] == head and not manifest["extra"]["seed_describe"].endswith("-dirty")
    assert (out / "seed" / "VERSION").read_text(encoding="utf-8") == "7.0.0-test\n"   # committed, not the edit
    row = json.loads((out / "lanes" / "SM1_a1" / "result.json").read_text(encoding="utf-8"))
    assert row["seed_sha"] == head


def test_unresolvable_seed_ref_is_a_typed_refusal_before_any_lane(tmp_path, monkeypatch):
    _short_tmp(monkeypatch)
    monkeypatch.setattr(run_live_lanes, "run_lane", lambda *a, **k: pytest.fail("a lane started without a seed"))
    source = _git_seed(tmp_path)
    out = tmp_path / "out"
    rc = run_live_lanes.main(["--stub", "--source-repo", str(source), "--seed", "no-such-ref", "--out", str(out),
                              "--watch-interval", "600"])
    assert rc == 3
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["extra"]["outcome"] == "refused" and manifest["extra"]["exit_code"] == 3
    assert manifest["extra"]["refusal"]["stage"] == "seed_materialize"
    assert manifest["extra"]["refusal"]["reason"] == "ref_unresolved"
    assert not (out / "lanes").exists() and not (out / "effective_settings.json").exists()


# --------------------------------------------------------------------------- #
# Admission and the typed refusals (persisted manifest, no footprint)
# --------------------------------------------------------------------------- #

def test_run_root_confinement_refuses_before_anything_is_created(tmp_path, monkeypatch):
    _short_tmp(monkeypatch)
    source = _git_seed(tmp_path)
    with pytest.raises(ValueError, match="must not be under repo/"):
        run_live_lanes.main(["--stub", "--source-repo", str(source), "--out", str(source / "inside")])
    assert not (source / "inside").exists()


def test_missing_key_env_is_a_typed_refusal_before_any_lane_starts(tmp_path, monkeypatch):
    _short_tmp(monkeypatch)
    monkeypatch.delenv("E2E_TEST_KEY_ENV", raising=False)
    monkeypatch.setattr(run_live_lanes, "run_lane", lambda *a, **k: pytest.fail("a lane started without a key"))
    source = _git_seed(tmp_path)
    out = tmp_path / "out"
    rc = run_live_lanes.main(["--source-repo", str(source), "--out", str(out), "--key-env", "E2E_TEST_KEY_ENV"])
    assert rc == 3
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["extra"]["outcome"] == "refused" and manifest["extra"]["exit_code"] == 3
    assert manifest["extra"]["refusal"] == {"stage": "credential", "reason": "key_env_absent", "env": "E2E_TEST_KEY_ENV"}
    assert not (out / "lanes").exists() and not (out / "effective_settings.json").exists() and not (out / "seed").exists()


def test_credit_preflight_takes_the_min_of_both_planes(tmp_path, monkeypatch):
    """Key limit says $50, the account behind it holds $1: the run is bounded by $1 and refused
    below a $5 floor. Both numbers are recorded; the key value never is."""
    _short_tmp(monkeypatch)
    monkeypatch.setenv("E2E_TEST_KEY_ENV", FAKE_KEY)
    monkeypatch.setattr(run_live_lanes, "run_lane", lambda *a, **k: pytest.fail("a lane started under the floor"))
    calls: list = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(
        b'{"data":{"limit_remaining":50.0}}', b'{"data":{"total_credits":10.0,"total_usage":9.0}}', calls))
    source = _git_seed(tmp_path)
    out = tmp_path / "out"
    rc = run_live_lanes.main(["--source-repo", str(source), "--out", str(out), "--key-env", "E2E_TEST_KEY_ENV",
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
    assert run_live_lanes.credit_preflight(FAKE_KEY, timeout=3) == {
        "key_limit_remaining_usd": None, "account_credits_usd": None, "remaining_usd": None}


# --------------------------------------------------------------------------- #
# The manifest names the APPLIED model; secrets stay out of every run-level artifact
# --------------------------------------------------------------------------- #

def _fake_run(tmp_path, monkeypatch, argv: list[str], *, lane=_fake_lane, expect_rc: int = 0) -> tuple[pathlib.Path, dict]:
    _short_tmp(monkeypatch)
    monkeypatch.setenv("E2E_TEST_KEY_ENV", FAKE_KEY)
    monkeypatch.setattr(run_live_lanes, "run_lane", lane)
    monkeypatch.setattr(run_live_lanes, "credit_preflight", lambda key, **_kw: {
        "key_limit_remaining_usd": None, "account_credits_usd": None, "remaining_usd": None})
    source = _git_seed(tmp_path)
    out = tmp_path / "out"
    rc = run_live_lanes.main(["--source-repo", str(source), "--out", str(out), "--key-env", "E2E_TEST_KEY_ENV",
                              "--watch-interval", "600", *argv])
    assert rc == expect_rc
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


def test_run_root_template_is_redacted_and_the_key_reaches_only_the_lanes(tmp_path, monkeypatch):
    out, manifest = _fake_run(tmp_path, monkeypatch, ["--model", "argv/model-y", "--scenarios", "SM1,SK1",
                                                      "--attempts", "2", "--pass-of", "2", "--lanes", "2"])
    assert manifest["model_slots"]["OUROBOROS_MODEL"] == "argv/model-y"
    template_path = out / "effective_settings.json"
    if os.name == "posix":
        assert (template_path.stat().st_mode & 0o777) == 0o600
    else:   # Windows: chmod only toggles read-only; the mode reads 0o666 — the redaction is the guarantee there
        assert template_path.is_file()
    template = json.loads(template_path.read_text(encoding="utf-8"))
    assert "OPENROUTER_API_KEY" not in template and template["OUROBOROS_MODEL"] == "argv/model-y"
    for artifact in (out / "run_manifest.json", template_path, *out.glob("lanes/*/result.json")):
        assert FAKE_KEY.encode() not in artifact.read_bytes(), artifact
    creds = manifest["provider_credentials"]
    assert creds["granted"] == {}                       # the file grant: nothing
    assert creds["runtime_granted"]["OPENROUTER_API_KEY"]["present"] is True
    assert creds["runtime_granted"]["OPENROUTER_API_KEY"]["fingerprint"].startswith("sha256:")
    for row_path in out.glob("lanes/*/result.json"):
        row = json.loads(row_path.read_text(encoding="utf-8"))
        assert row["template_has_key"] is False and row["key_handed"] is True   # injected per lane, in memory
    assert manifest["requested_task_ids"] == ["SM1_a1", "SM1_a2", "SK1_a1", "SK1_a2"]
    assert manifest["extra"]["scenarios"] == {
        "SM1": {"attempts": 2, "passed": 2, "infra_errors": 0, "not_run": 0, "verdict": "pass"},
        "SK1": {"attempts": 2, "passed": 2, "infra_errors": 0, "not_run": 0, "verdict": "pass"}}
    assert manifest["extra"]["outcome"] == "completed" and manifest["extra"]["exit_code"] == 0
    assert manifest["extra"]["total_budget_usd"] == 100.0 and manifest["extra"]["per_task_usd"] == 8.0
    budget = manifest["extra"]["budget"]
    assert budget["cap_usd"] == 100.0 and budget["halted"] is False and budget["attempts_not_run"] == []
    assert budget["reservation_rule"] == run_live_lanes.RESERVATION_RULE and "stop_reason" not in manifest["extra"]


def test_run_wide_cap_stops_new_attempts_and_records_not_run_rows(tmp_path, monkeypatch):
    """cap $20, reservation unit $8, every settled lane read back at $5: SM1_a1 (0+0+8), SM1_a2
    (5+0+8) run; SK1_a1 (10+0+16 > 20) is refused, SK1_a2 follows; both are recorded rows."""
    monkeypatch.setattr(run_live_lanes, "lane_spend",
                        lambda root: (5.0, 0) if pathlib.Path(root).parent.exists() else (0.0, 0))
    out, manifest = _fake_run(tmp_path, monkeypatch, ["--scenarios", "SM1,SK1", "--attempts", "2", "--lanes", "1",
                                                      "--total-budget", "20", "--per-task-usd", "8"], expect_rc=1)
    budget = manifest["extra"]["budget"]
    assert budget["halted"] is True and budget["halt"]["first_refused"] == "SK1_a1"
    assert budget["halt"]["spent_usd"] == 10.0 and budget["halt"]["reservation_usd"] == 16.0
    assert budget["attempts_not_run"] == ["SK1_a1", "SK1_a2"] and budget["spent_usd"] == 10.0
    assert manifest["extra"]["stop_reason"] == "budget_cap" and manifest["extra"]["lanes_run"] == 2
    assert manifest["extra"]["scenarios"]["SK1"] == {"attempts": 2, "passed": 0, "infra_errors": 0, "not_run": 2,
                                                     "verdict": "fail"}
    rows = {json.loads(p.read_text(encoding="utf-8"))["attempt"]: json.loads(p.read_text(encoding="utf-8"))
            for p in out.glob("lanes/SM1_*/result.json")}
    assert rows[1]["lane_total_budget_usd"] == 8.0 and rows[2]["lane_total_budget_usd"] == 8.0   # each: its reservation
    refused = json.loads((out / "lanes" / "SK1_a1" / "result.json").read_text(encoding="utf-8"))
    assert refused["status"] == "not_run" and refused["reason_code"] == "budget_cap"
    assert refused["refusal"]["code"] == "budget_cap" and refused["budget"]["halt"]["first_refused"] == "SK1_a1"
    index = [json.loads(ln) for ln in (out / "result_index.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [(r["instance_id"], r["status"], r["reason_code"]) for r in index if r["instance_id"].startswith("SK1")] == [
        ("SK1_a1", "not_run", "budget_cap"), ("SK1_a2", "not_run", "budget_cap")]


def test_self_mod_run_level_gate_fails_every_unconfirmed_lane(tmp_path, monkeypatch):
    def lane(job, *a, **k):
        row = _fake_lane(job, *a, **k)
        row["self_mod_absorb"] = {"confirmed": job == ("SM1", 1), "reason": "absorbed" if job == ("SM1", 1) else "no_promotion"}
        return row

    _out, manifest = _fake_run(tmp_path, monkeypatch, ["--self-mod", "--scenarios", "SM1,SW1", "--lanes", "1"],
                               lane=lane, expect_rc=1)
    assert manifest["extra"]["self_mod"] == {"lanes": 2, "absorb_unconfirmed": ["SW1_a1"]}
    assert manifest["extra"]["outcome"] == "failed" and manifest["extra"]["exit_code"] == 1
    assert manifest["extra"]["scenarios"]["SW1"]["verdict"] == "pass"   # the lane verdict alone would have passed


def test_lane_infra_failure_is_a_typed_refusal_in_both_artifacts(tmp_path, monkeypatch):
    _short_tmp(monkeypatch)

    def refuse(seed, clone):
        raise run_live_lanes.SeedMaterializeRefused("clone_failed", "git clone exploded")

    monkeypatch.setattr(run_live_lanes, "clone_seed", refuse)
    args = run_live_lanes.parse_args(["--stub", "--out", str(tmp_path / "out"), "--watch-interval", "600"])
    out = tmp_path / "out"
    states: dict = {}
    row = run_live_lanes.run_attempt(("SM1", 1), args, out, {}, run_live_lanes.Stagger(2.0), states, tmp_path / "seed",
                                     run_live_lanes.RunBudget(100.0, 8.0, reader=lambda root: (0.0, 0)),
                                     key="", seed_sha="abc")
    assert row["status"] == "infra_error" and row["reason_code"] == "infra_error:clone_failed"
    assert row["refusal"] == {"type": "SeedMaterializeRefused", "code": "clone_failed", "message": "git clone exploded"}
    stored = json.loads((out / "lanes" / "SM1_a1" / "result.json").read_text(encoding="utf-8"))
    assert stored["refusal"]["code"] == "clone_failed" and stored["error"].startswith("SeedMaterializeRefused:")
    index = json.loads((out / "result_index.jsonl").read_text(encoding="utf-8").strip())
    assert index["status"] == "infra_error" and index["reason_code"] == "infra_error:clone_failed"
    assert index["details"]["refusal"]["code"] == "clone_failed"


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
    """Real server, loopback stub model, no key: the commit lands through the review organ
    (both stylesheets, through the same hermetic tests preflight as the paid prompt),
    the durable rows and receipts exist, the
    seed is a clean detached clone of this tree's HEAD and the manifest names the stub as
    the model."""
    if str(os.environ.get("OUROBOROS_E2E_DEEP") or "").strip().lower() != "mock":
        pytest.skip("set OUROBOROS_E2E_DEEP=mock to run the stub rehearsal (spawns a real isolated server)")
    out = tmp_path / "out"
    rc = run_live_lanes.main(["--stub", "--lanes", "1", "--scenarios", "SM1", "--source-repo", str(REPO_ROOT),
                              "--seed", "HEAD", "--out", str(out), "--watch-interval", "600"])
    row = json.loads((out / "lanes" / "SM1_a1" / "result.json").read_text(encoding="utf-8"))
    failed = sorted(k for k, v in row["checks"].items() if not v and not k.startswith("ui_"))
    assert rc == 0 and failed == [], (row["status"], failed, row["error"])
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_slots"]["OUROBOROS_MODEL"] == stub_lane.STUB_MODEL_SLUG == row["model_slots"]["OUROBOROS_MODEL"]
    assert row["digests"]["pre_head"] == manifest["seed"]["resolved_sha"] != row["digests"]["post_head"]
    assert len(row["digests"]["diff_sha256"]) == 64 and not row["digests"]["seed_describe"].endswith("-dirty")
    assert (out / "result_index.jsonl").read_text(encoding="utf-8").count("\n") == 1


def test_orphan_after_stop_fails_a_passing_lane_with_a_typed_reason(tmp_path):
    """A process still carrying the lane's data root after stop flips a passing lane to fail with
    reason_code=checks_failed, in result.json AND in result_index.jsonl — never an empty reason."""
    def row():
        return {"scenario": "SM1", "attempt": 1, "status": "pass", "reason_code": "", "checks": {"fake": True},
                "error": "", "duration_sec": 1.0, "budget": {}, "refusal": None, "runtime_outcome": "completed"}
    clean = row()
    run_live_lanes._apply_orphan_scan(clean, True)
    assert clean["status"] == "pass" and clean["reason_code"] == "" and clean["checks"]["no_orphans_after_stop"] is True
    absent = row()
    run_live_lanes._apply_orphan_scan(absent, None)   # no procfs (macOS, Windows): a typed fact, no check
    assert absent["status"] == "pass" and absent["orphan_scan"] == "unavailable:no_procfs"
    assert absent["no_orphans_after_stop"] is None and "no_orphans_after_stop" not in absent["checks"]
    dirty = row()
    run_live_lanes._apply_orphan_scan(dirty, False)
    assert dirty["status"] == "fail" and dirty["reason_code"] == "checks_failed"
    assert dirty["checks"]["no_orphans_after_stop"] is False and dirty["no_orphans_after_stop"] is False
    out = tmp_path / "run"
    lane = out / "lanes" / "SM1_a1"
    lane.mkdir(parents=True)
    run_live_lanes._record_row(out, lane, dirty)
    recorded = json.loads((lane / "result.json").read_text(encoding="utf-8"))
    assert recorded["status"] == "fail" and recorded["reason_code"] == "checks_failed"
    index = [json.loads(ln) for ln in (out / "result_index.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [(r["instance_id"], r["status"], r["reason_code"]) for r in index] == [("SM1_a1", "fail", "checks_failed")]
