#!/usr/bin/env python3
"""Live E2E stand runner: K isolated real servers, staggered, one scenario attempt each.

    python -m devtools.e2e_live.run_live_lanes --stub --lanes 2                     # $0 rehearsal
    OUROBOROS_E2E_LIVE_OPENROUTER_KEY=... python -m devtools.e2e_live.run_live_lanes \
        --lanes 4 --scenarios SM1,SW1,SK1 --attempts 3 --pass-of 2 --total-budget 100  # paid

Order is load-bearing (the benchmark family's launcher gate, ``launcher_audit``): argument-shaped
work, then ``admit_benchmark_run`` (fail-closed on a dirty seed, persisted BEFORE anything can
fail), then — inside ``finalize_run_manifest`` — the key by NAME from the environment (never a
pool file, never printed), the credit preflight ``min(key limit remaining, account credits)``,
the effective settings written from the TREE'S DEFAULTS (D-09) with the budget knobs as
settings keys (never env), and the lane pool. The manifest names the model from the APPLIED
settings file, not argv. Every lane leaves ``lanes/<id>_a<n>/result.json`` (checks, digests,
grants by fingerprint, settings sha256, seed describe) plus screenshots when a browser client
exists; a watcher prints lane states, free disk on ``/`` and ``/mnt/data`` and the key headroom.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from devtools.benchmarks.common.manifests import (
    admit_benchmark_run,
    finalize_run_manifest,
    model_slot_snapshot,
    openrouter_account_credits,
    openrouter_key_remaining,
    provider_credential_disclosure,
    repo_provenance,
)
from devtools.benchmarks.common.result_index import append_result_index, runtime_terminal_disclosure, task_result_row
from devtools.benchmarks.common.run_roots import assert_outside_repo, repo_root_from_devtools, run_root
from devtools.benchmarks.common.secrets import credential_fingerprint, isolated_credential_grants
from devtools.benchmarks.common.server_runner import (
    IsolatedServer,
    _settings_json_bytes,
    absorbed_cycles_done,
    build_isolated_settings,
    seed_owner_state,
)
from devtools.e2e_live import stub_lane
from devtools.e2e_live.scenarios import SCENARIOS, LaneContext, diff_sha256, head_sha, now_iso
from devtools.e2e_live.ui_probe import resolve_ui_client
from ouroboros.provider_models import ALL_PROVIDER_CREDENTIAL_KEYS, declared_model_settings

MAX_LANES = 6
STAGGER_BOUNDS = (2.0, 3.0)
TMPDIR_MAX_CHARS = 70          # AF_UNIX 108-byte cap on the workers' Manager socket path
DEFAULT_KEY_ENV = "OUROBOROS_E2E_LIVE_OPENROUTER_KEY"
DISK_ALERT_GIB = {"/": 40.0, "/mnt/data": 60.0}


def _log(msg: str) -> None:
    print(f"[e2e_live {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lanes", type=int, default=4, help=f"concurrent isolated servers (1..{MAX_LANES})")
    ap.add_argument("--stagger", type=float, default=2.5, help="seconds between lane starts, clamped to [2, 3]")
    ap.add_argument("--scenarios", default="SM1,SW1,SK1")
    ap.add_argument("--attempts", type=int, default=1, help="attempts per scenario (every one runs and is recorded)")
    ap.add_argument("--pass-of", type=int, default=1, help="passes needed for a scenario verdict")
    ap.add_argument("--total-budget", type=float, default=100.0, help="TOTAL_BUDGET written into the lane settings")
    ap.add_argument("--per-task-usd", type=float, default=8.0, help="OUROBOROS_PER_TASK_COST_USD in the lane settings")
    ap.add_argument("--task-timeout", type=int, default=1500)
    ap.add_argument("--ready-timeout", type=int, default=300)
    ap.add_argument("--profile", choices=("full", "wiring"), default="full",
                    help="full = the scenario's own enforcement; wiring = advisory review, the cheap smoke")
    ap.add_argument("--self-mod", action="store_true", help="post-task evolution with a real re-exec restart (D-11)")
    ap.add_argument("--model", default="", help="pin OUROBOROS_MODEL (paid runs only)")
    ap.add_argument("--key-env", default=DEFAULT_KEY_ENV, help="NAME of the env var carrying the OpenRouter key")
    ap.add_argument("--min-credit-usd", type=float, default=None, help="refuse below this headroom (default: --total-budget)")
    ap.add_argument("--out", default="", help="run root (default: bench_runs/e2e_live/<run id>; never repo/ or live data/)")
    ap.add_argument("--seed", default="", help="seed checkout (default: this tree); must be clean")
    ap.add_argument("--stub", action="store_true", help="loopback stub model, no key, no money")
    ap.add_argument("--watch-interval", type=float, default=30.0)
    ap.add_argument("--prune-clones", action="store_true", help="delete lane clones after each lane (results stay)")
    args = ap.parse_args(argv)
    if not 1 <= args.lanes <= MAX_LANES:
        ap.error(f"--lanes must be within 1..{MAX_LANES} (R21), got {args.lanes}")
    args.stagger = min(max(float(args.stagger), STAGGER_BOUNDS[0]), STAGGER_BOUNDS[1])
    args.scenario_ids = [s.strip() for s in str(args.scenarios).split(",") if s.strip()]
    unknown = [s for s in args.scenario_ids if s not in SCENARIOS]
    if unknown or not args.scenario_ids:
        ap.error(f"unknown scenarios {unknown}; known: {sorted(SCENARIOS)}")
    if args.attempts < 1 or not 1 <= args.pass_of <= args.attempts:
        ap.error("--attempts must be >= 1 and 1 <= --pass-of <= --attempts")
    if args.stub and args.model:
        ap.error("--model cannot be combined with --stub (the stub IS the model)")
    if args.min_credit_usd is None:
        args.min_credit_usd = float(args.total_budget)
    tmpdir = tempfile.gettempdir()
    if len(tmpdir) > TMPDIR_MAX_CHARS:
        ap.error(f"TMPDIR {tmpdir!r} is longer than {TMPDIR_MAX_CHARS} chars: the workers' AF_UNIX "
                 "socket path would overflow (export a short TMPDIR, e.g. /tmp/claude-1006/x)")
    return args


# --------------------------------------------------------------------------- #
# Effective settings: the tree's defaults + the run's explicit knobs, written ONCE
# --------------------------------------------------------------------------- #

def effective_settings(args: argparse.Namespace, key: str) -> dict:
    """The run template (D-09: the defaults of the tree under test, never the owner's live
    settings). Every model slot is written explicitly so the manifest can name it from the FILE;
    in stub mode the slots name the loopback stub, which IS the model of that run."""
    slots = stub_lane.STUB_MODEL_SLOTS if args.stub else declared_model_settings({})
    overrides = {
        **slots,
        "OUROBOROS_RUNTIME_MODE": "advanced",
        "OUROBOROS_MAX_WORKERS": 4,
        "TOTAL_BUDGET": float(args.total_budget),
        "OUROBOROS_PER_TASK_COST_USD": float(args.per_task_usd),
        "OUROBOROS_POST_TASK_EVOLUTION": "true" if args.self_mod else "false",
    }
    if args.self_mod:
        overrides["OUROBOROS_POST_TASK_EVOLUTION_CADENCE"] = "every_n:1"
    if args.model:
        overrides["OUROBOROS_MODEL"] = str(args.model)
    if key:
        overrides["OPENROUTER_API_KEY"] = key
    return build_isolated_settings({}, **overrides)


def write_settings(path: pathlib.Path, cfg: dict) -> str:
    """0600-before-content write; returns the sha256 of the exact bytes on disk."""
    raw = _settings_json_bytes(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, raw)
    finally:
        os.close(fd)
    return hashlib.sha256(raw).hexdigest()


def config_sha256(cfg: dict) -> str:
    """Digest of the settings WITHOUT secret values: comparable across runs on different keys."""
    scrubbed = {k: (credential_fingerprint(v) if k in ALL_PROVIDER_CREDENTIAL_KEYS else v)
                for k, v in sorted(cfg.items())}
    return hashlib.sha256(json.dumps(scrubbed, sort_keys=True).encode("utf-8")).hexdigest()


def credit_preflight(key: str) -> dict:
    """``min`` over both planes; None everywhere = uncapped. Numbers only, never the key."""
    limit = openrouter_key_remaining(key)
    credits = openrouter_account_credits(key)
    bounds = [v for v in (limit, credits) if v is not None]
    return {"key_limit_remaining_usd": limit, "account_credits_usd": credits,
            "remaining_usd": min(bounds) if bounds else None}


# --------------------------------------------------------------------------- #
# Lane pool
# --------------------------------------------------------------------------- #

class Stagger:
    def __init__(self, seconds: float) -> None:
        self.seconds, self._last, self._lock = float(seconds), 0.0, threading.Lock()

    def wait_turn(self) -> None:
        with self._lock:
            delay = max(0.0, self._last + self.seconds - time.monotonic())
            time.sleep(delay)
            self._last = time.monotonic()


def clone_seed(seed: pathlib.Path, clone: pathlib.Path) -> None:
    subprocess.run(["git", "clone", "--no-hardlinks", "-q", str(seed), str(clone)], check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-q", "-B", "ouroboros"], cwd=str(clone), check=True, capture_output=True)
    subprocess.run(["git", "remote", "remove", "origin"], cwd=str(clone), check=False, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Ouroboros E2E stand"], cwd=str(clone), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "e2e-live@ouroboros.invalid"], cwd=str(clone), check=True, capture_output=True)


def run_lane(job: tuple[str, int], args: argparse.Namespace, out: pathlib.Path, template: dict,
             stagger: Stagger, states: dict, seed: pathlib.Path) -> dict:
    sid, attempt = job
    scenario = SCENARIOS[sid]
    lane = out / "lanes" / f"{sid}_a{attempt}"
    clone, data_root, shots = lane / "clone", lane / "data", lane / "shots"
    settings_path = data_root / "settings.json"
    started = time.time()
    row = {"schema": "ouroboros.e2e_live.lane_result.v1", "scenario": sid, "attempt": attempt,
           "title": scenario.title, "status": "infra_error", "stub": bool(args.stub), "profile": args.profile,
           "self_mod": bool(args.self_mod), "started_at": now_iso(), "checks": {}, "facts": {}, "error": "",
           "screenshots": [], "ui": {"available": False, "reason": ""}, "self_mod_absorb": None}

    def log(msg: str) -> None:
        _log(f"{sid}_a{attempt}: {msg}")
        states[job] = (msg[:60], time.time())

    server = stub = ui = None
    from tests.system_e2e import harness  # durable readers + /proc oracles (runtime-only import)
    try:
        log("cloning seed")
        lane.mkdir(parents=True, exist_ok=True)
        clone_seed(seed, clone)
        cfg = dict(template)
        child_model = str(cfg.get("OUROBOROS_MODEL") or "")
        if args.stub:
            stub = stub_lane.routed_stub_model(scenario.stub_script(clone)).__enter__()
            child_model = stub_lane.STUB_CHILD_SLUG
            cfg = stub_lane.stub_settings(stub, template)
        cfg.update(scenario.overrides(child_model))
        if args.profile == "wiring":
            cfg["OUROBOROS_REVIEW_ENFORCEMENT"] = "advisory"
        sha = write_settings(settings_path, cfg)
        seed_owner_state(data_root, evolution_enabled=args.self_mod)
        from supervisor import state as sstate
        (data_root / sstate.ISOLATED_BENCHMARK_SENTINEL).write_text("isolated e2e_live data root\n", encoding="utf-8")
        oracle = harness.ArtifactOracle(data_root)

        def start_server(expected_sha: str) -> IsolatedServer:
            srv = IsolatedServer(clone, data_root, settings_path, settings_authoritative_env=True,
                                 expected_settings_sha256=expected_sha)
            stagger.wait_turn()
            log(f"starting server on {srv.base_url}")
            srv.start(ready_timeout=args.ready_timeout)
            return srv

        def restart() -> IsolatedServer:
            nonlocal server
            if args.self_mod:
                log("waiting for the evolve/absorb re-exec")
                row["self_mod_absorb"] = server.wait_for_absorb(
                    server.current_sha(), absorbed_cycles_done(data_root), timeout=args.task_timeout)
                if not server.wait_for_health(timeout=args.ready_timeout):
                    raise RuntimeError("server unhealthy after the self-mod restart")
                return server
            log("restarting server on the committed tree")
            server.stop()
            server = start_server(hashlib.sha256(settings_path.read_bytes()).hexdigest())
            return server

        server = start_server(sha)
        row["attestation"] = dict(server.attestation)
        if scenario.needs_ui:
            ui, reason = resolve_ui_client(server.base_url)
            row["ui"] = {"available": ui is not None, "reason": reason}
        pre_head = head_sha(clone)
        ctx = LaneContext(server=server, clone=clone, data_root=data_root, oracle=oracle, harness=harness,
                          ui=ui, ui_reason=row["ui"]["reason"], shots=shots, log=log,
                          task_timeout=args.task_timeout, restart=restart)
        log("running scenario")
        scenario.acceptance(ctx)
        server = ctx.server
        row.update({"checks": ctx.checks, "facts": ctx.facts, "screenshots": ctx.screenshots})
        seed_desc = repo_provenance(seed)
        row["checks"]["seed_clean"] = not seed_desc.get("dirty") and bool(seed_desc.get("status_available"))
        post_head = head_sha(clone)
        applied = json.loads(settings_path.read_text(encoding="utf-8"))
        row["digests"] = {"settings_sha256": hashlib.sha256(settings_path.read_bytes()).hexdigest(),
                          "settings_config_sha256": config_sha256(applied),
                          "seed_head": seed_desc.get("head", ""), "seed_describe": seed_desc.get("describe", ""),
                          "pre_head": pre_head, "post_head": post_head,
                          "diff_sha256": diff_sha256(clone, pre_head, post_head)}
        row["model_slots"] = model_slot_snapshot(settings_path, env_overrides=False)
        row["grants"] = isolated_credential_grants(applied)
        row["runtime_outcome"] = runtime_terminal_disclosure(ctx.facts.pop("runtime_result", None))
        if args.stub:
            row["facts"]["stub_unconsumed"] = stub.consumed()
            kinds = stub.kinds()  # the review-organ branches the stub actually served
            row["facts"]["stub_call_kinds"] = {kind: kinds.count(kind) for kind in sorted(set(kinds))}
        row["status"] = "pass" if all(row["checks"].values()) else "fail"
    except Exception as exc:  # noqa: BLE001 - an infra failure is a recorded row, never a lost lane
        row["error"] = f"{type(exc).__name__}: {exc}"[:2000]
        log(f"infra error: {row['error'][:200]}")
    finally:
        for closer in (getattr(ui, "close", None), getattr(server, "stop", None)):
            if closer is not None:
                closer()
        if stub is not None:
            stub.__exit__(None, None, None)
        row["no_orphans_after_stop"] = bool(harness.wait_until(
            lambda: not harness.pids_with_env_value(str(data_root)), 30))
        if not row["no_orphans_after_stop"] and row["status"] == "pass":
            row["status"] = "fail"
        row["checks"]["no_orphans_after_stop"] = row["no_orphans_after_stop"]
        row["ended_at"], row["duration_sec"] = now_iso(), round(time.time() - started, 1)
        lane.mkdir(parents=True, exist_ok=True)
        (lane / "result.json").write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
        append_result_index(out, task_result_row(
            benchmark="e2e_live", instance_id=f"{sid}_a{attempt}", status=row["status"],
            runtime_result=row.get("runtime_outcome"), error=row["error"],
            details={"checks": row["checks"], "duration_sec": row.get("duration_sec")}))
        if args.prune_clones:
            shutil.rmtree(clone, ignore_errors=True)
        states[job] = (f"{row['status']} ({row.get('duration_sec')}s)", time.time())
    return row


def watcher(stop: threading.Event, states: dict, interval: float, key: str, floor: float) -> None:
    started = time.time()
    while not stop.wait(interval):
        lanes = " ".join(f"{sid}_a{n}={txt}" for (sid, n), (txt, _) in sorted(states.items()))
        disks = []
        for mount, alert in DISK_ALERT_GIB.items():
            if not pathlib.Path(mount).exists():
                continue
            free = shutil.disk_usage(mount).free / 2**30
            disks.append(f"{mount}={free:.0f}G" + (" ALERT" if free < alert else ""))
        line = f"t=+{time.time() - started:.0f}s lanes: {lanes or '-'} | free {' '.join(disks)}"
        if key:
            try:
                remaining = credit_preflight(key)["remaining_usd"]
                line += f" | key remaining ${remaining:.2f}" if remaining is not None else " | key uncapped"
                if remaining is not None and remaining < floor:
                    line += " ALERT"
            except Exception as exc:  # noqa: BLE001 - a failed probe is reported, not fatal
                line += f" | key probe failed: {type(exc).__name__}"
        _log("[watch] " + line)


# --------------------------------------------------------------------------- #
# main: admission is the outer boundary; everything else inside the finalization seam
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    seed = pathlib.Path(args.seed).expanduser().resolve(strict=False) if args.seed else repo_root_from_devtools()
    out = pathlib.Path(args.out).expanduser() if args.out else run_root("e2e_live")
    out = assert_outside_repo(out, seed)
    jobs = [(sid, n) for sid in args.scenario_ids for n in range(1, args.attempts + 1)]
    manifest_path = out / "run_manifest.json"
    manifest = admit_benchmark_run(
        manifest_path, benchmark="e2e_live", run_root=out, repo_dir=seed,
        requested_task_ids=[f"{sid}_a{n}" for sid, n in jobs], require_clean=True,
        settings_authoritative_env=True, isolated_data_root=str(out / "lanes"),
        output_paths={"lanes": str(out / "lanes"), "result_index": str(out / "result_index.jsonl"),
                      "effective_settings": str(out / "effective_settings.json")},
        extra={"outcome": "started", "lanes": args.lanes, "stagger_sec": args.stagger, "profile": args.profile,
               "self_mod": bool(args.self_mod), "stub": bool(args.stub), "scenarios": args.scenario_ids,
               "attempts": args.attempts, "pass_of": args.pass_of, "total_budget_usd": args.total_budget,
               "per_task_usd": args.per_task_usd, "key_env": args.key_env if not args.stub else ""},
    )
    _log(f"run root: {out}")
    with finalize_run_manifest(manifest_path, manifest) as final:
        key = ""
        if not args.stub:
            key = str(os.environ.get(args.key_env) or "").strip()
            if not key:
                final.update({"outcome": "refused", "exit_code": 3,
                              "refusal": {"stage": "credential", "reason": "key_env_absent", "env": args.key_env}})
                _log(f"refused: ${args.key_env} is empty (export the key under that NAME; the pool file is never read)")
                return 3
            final["credential_fingerprint"] = credential_fingerprint(key)
            try:
                headroom = credit_preflight(key)
            except Exception as exc:  # noqa: BLE001 - an unusable key is a typed refusal
                final.update({"outcome": "refused", "exit_code": 3,
                              "refusal": {"stage": "credit_preflight", "reason": "key_unusable",
                                          "error": f"{type(exc).__name__}: {exc}"[:300]}})
                return 3
            final["credit_preflight"] = {**headroom, "floor_usd": args.min_credit_usd}
            _log(f"key headroom: limit_remaining={headroom['key_limit_remaining_usd']} "
                 f"account_credits={headroom['account_credits_usd']} -> min={headroom['remaining_usd']}")
            if headroom["remaining_usd"] is not None and headroom["remaining_usd"] < args.min_credit_usd:
                final.update({"outcome": "refused", "exit_code": 3,
                              "refusal": {"stage": "credit_preflight", "reason": "insufficient_remaining", **headroom,
                                          "floor_usd": args.min_credit_usd}})
                return 3
        template = effective_settings(args, key)
        template_path = out / "effective_settings.json"
        write_settings(template_path, template)
        manifest["model_slots"] = model_slot_snapshot(template_path, env_overrides=False)
        manifest["provider_credentials"] = provider_credential_disclosure(template_path)
        final["effective_model"] = manifest["model_slots"].get("OUROBOROS_MODEL", "") if not args.stub else "loopback stub"
        final["settings_config_sha256"] = config_sha256(template)
        if manifest["provider_credentials"].get("fail_open") and not args.stub:
            _log(f"WARNING: declared slots without a credential: {manifest['provider_credentials'].get('planned_keys')}")
        states: dict = {}
        stop = threading.Event()
        threading.Thread(target=watcher, args=(stop, states, args.watch_interval, key, args.min_credit_usd),
                         daemon=True).start()
        rows: list[dict] = []
        gate = Stagger(args.stagger)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.lanes) as pool:
                futures = [pool.submit(run_lane, job, args, out, template, gate, states, seed) for job in jobs]
                rows = [f.result() for f in futures]
        finally:
            stop.set()
        verdicts = {}
        for sid in args.scenario_ids:
            passed = sum(1 for r in rows if r["scenario"] == sid and r["status"] == "pass")
            verdicts[sid] = {"attempts": args.attempts, "passed": passed,
                             "infra_errors": sum(1 for r in rows if r["scenario"] == sid and r["status"] == "infra_error"),
                             "verdict": "pass" if passed >= args.pass_of else "fail"}
        ok = all(v["verdict"] == "pass" for v in verdicts.values())
        for r in rows:
            failed = sorted(k for k, v in r["checks"].items() if not v)
            _log(f"{r['scenario']}_a{r['attempt']}: {r['status']} in {r.get('duration_sec')}s"
                 + (f" failed checks: {failed}" if failed else "") + (f" error: {r['error'][:160]}" if r["error"] else ""))
        _log(f"verdicts: {json.dumps(verdicts)}")
        final.update({"outcome": "completed" if ok else "failed", "exit_code": 0 if ok else 1,
                      "scenarios": verdicts, "lanes_run": len(rows)})
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
