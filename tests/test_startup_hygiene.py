import json
import pathlib
import types

import ouroboros.agent_startup_checks as startup_mod
import ouroboros.world_profiler as world_profiler
from ouroboros.memory import Memory


def test_check_version_sync_ignores_non_release_tag(tmp_path, monkeypatch):
    (tmp_path / "VERSION").write_text("4.7.0\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('version = "4.7.0"\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("**Version:** 4.7.0\n", encoding="utf-8")
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "ARCHITECTURE.md").write_text("# Ouroboros v4.7.0\n", encoding="utf-8")

    env = types.SimpleNamespace(
        repo_dir=tmp_path,
        repo_path=lambda rel: tmp_path / rel,
    )

    monkeypatch.setattr(
        startup_mod.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=0, stdout="v4.6.0-test1\n"),
    )

    result, issues = startup_mod.check_version_sync(env)

    assert issues == 0
    assert result["status"] == "ok"
    assert result["latest_tag"] == "4.6.0-test1"
    assert result["tag_sync"] == "ignored_non_release_tag"


def test_check_version_sync_accepts_rc_release_tag(tmp_path, monkeypatch):
    (tmp_path / "VERSION").write_text("4.50.0-rc.2\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('version = "4.50.0rc2"\n', encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "[![Version 4.50.0-rc.2](https://img.shields.io/badge/version-4.50.0--rc.2-green.svg)](VERSION)\n",
        encoding="utf-8",
    )
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "ARCHITECTURE.md").write_text("# Ouroboros v4.50.0-rc.2\n", encoding="utf-8")

    env = types.SimpleNamespace(
        repo_dir=tmp_path,
        repo_path=lambda rel: tmp_path / rel,
    )

    monkeypatch.setattr(
        startup_mod.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=0, stdout="v4.50.0-rc.2\n"),
    )

    result, issues = startup_mod.check_version_sync(env)

    assert issues == 0
    assert result["status"] == "ok"
    assert result["latest_tag"] == "4.50.0-rc.2"
    assert result["pyproject_version"] == "4.50.0rc2"


def test_check_version_sync_flags_malformed_rc_badge_url(tmp_path, monkeypatch):
    (tmp_path / "VERSION").write_text("4.50.0-rc.2\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('version = "4.50.0rc2"\n', encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "[![Version 4.50.0-rc.2](https://img.shields.io/badge/version-4.50.0-rc.2-green.svg)](VERSION)\n",
        encoding="utf-8",
    )
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "ARCHITECTURE.md").write_text("# Ouroboros v4.50.0-rc.2\n", encoding="utf-8")

    env = types.SimpleNamespace(
        repo_dir=tmp_path,
        repo_path=lambda rel: tmp_path / rel,
    )

    monkeypatch.setattr(
        startup_mod.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=0, stdout="v4.50.0-rc.2\n"),
    )

    result, issues = startup_mod.check_version_sync(env)

    assert issues == 1
    assert result["status"] == "warning"
    assert result["readme_badge_url_valid"] is False


def test_hot_store_growth_notes_warns_when_task_results_dir_exceeds_threshold(
    tmp_path, monkeypatch
):
    """razzant/ouroboros#139 — task_results/*.json is glob-walked on every
    /api/tasks request and SSE tick. hot_store_growth_notes surfaces a
    WARNING with file count + total bytes once the dir exceeds
    TASK_RESULTS_DIR_WARN_BYTES, and the wording points at
    prune_task_results / discovery bounding (never shorter retention).
    """
    from ouroboros import context_budget as cb

    task_results_dir = tmp_path / "task_results"
    task_results_dir.mkdir()
    # 220 MB total across 4 files (each 55 MB). stat().st_size reflects
    # actual byte count, sparse or not — a single large write per file
    # is the cleanest fixture.
    big_block = b"\0" * (55 * 1024 * 1024)
    for name in ("a.json", "b.json", "c.json", "d.json"):
        (task_results_dir / name).write_bytes(big_block)

    assert (
        sum(p.stat().st_size for p in task_results_dir.glob("*.json"))
        > cb.TASK_RESULTS_DIR_WARN_BYTES
    )

    env = types.SimpleNamespace(
        drive_root=tmp_path,
        drive_path=lambda rel: tmp_path / rel,
    )

    notes = startup_mod.hot_store_growth_notes(env)
    matches = [n for n in notes if "task_results/*.json totals" in n]
    assert len(matches) == 1, f"expected exactly one task_results warning, got {notes}"
    warning = matches[0]
    assert "WARNING: HOT STORE GROWTH" in warning
    assert "4 files" in warning
    assert f"{cb.TASK_RESULTS_DIR_WARN_BYTES // 1_000_000} MB" in warning
    # Remediation wording MUST point at prune_task_results / discovery,
    # NEVER at shortening the GC retention.
    assert "prune_task_results" in warning
    assert "do NOT shorten the GC retention" in warning


def test_hot_store_growth_notes_silent_when_task_results_dir_below_threshold(
    tmp_path, monkeypatch
):
    """The tripwire must stay silent on a healthy dir — otherwise every
    boot would carry a warning."""
    from ouroboros import context_budget as cb

    task_results_dir = tmp_path / "task_results"
    task_results_dir.mkdir()
    (task_results_dir / "small.json").write_text("{}")

    env = types.SimpleNamespace(
        drive_root=tmp_path,
        drive_path=lambda rel: tmp_path / rel,
    )

    notes = startup_mod.hot_store_growth_notes(env)
    assert not any("task_results/*.json totals" in n for n in notes)
    # And confirm the threshold is sensibly large (we are NOT picking
    # an arbitrary knob — pinning it stops accidental shrinkage to a
    # no-op threshold).
    assert cb.TASK_RESULTS_DIR_WARN_BYTES >= 100_000_000


def test_memory_ensure_files_generates_world_profile(tmp_path, monkeypatch):
    calls = []

    def fake_generate(output_path: str):
        calls.append(output_path)
        pathlib.Path(output_path).write_text("# WORLD\n", encoding="utf-8")

    monkeypatch.setattr(world_profiler, "generate_world_profile", fake_generate)

    memory = Memory(drive_root=tmp_path, repo_dir=tmp_path)
    memory.ensure_files()
    memory.ensure_files()

    assert calls == [str(memory.world_path())]
    assert memory.world_path().read_text(encoding="utf-8") == "# WORLD\n"


def test_check_uncommitted_changes_never_commits_outside_launcher(monkeypatch, tmp_path):
    """Worker-side check_uncommitted_changes is warning-only; never commits."""
    env = types.SimpleNamespace(
        repo_dir=tmp_path,
        repo_path=lambda rel: tmp_path / rel,
    )
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return types.SimpleNamespace(returncode=0, stdout=" M server.py\n")
        raise AssertionError(f"Unexpected subprocess call: {cmd}")

    monkeypatch.delenv("OUROBOROS_MANAGED_BY_LAUNCHER", raising=False)
    monkeypatch.setattr(startup_mod.subprocess, "run", fake_run)

    result, issues = startup_mod.check_uncommitted_changes(env)

    assert issues == 1
    assert result["status"] == "warning"
    assert result["auto_committed"] is False
    assert result["auto_rescue_skipped"] == "supervisor_side_rescue_owns_this"
    assert calls == [["git", "status", "--porcelain"]]


def test_check_budget_reports_corrupt_state_json(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "state" / "state.json").write_text("{not-json", encoding="utf-8")
    env = types.SimpleNamespace(drive_path=lambda rel: tmp_path / rel)

    monkeypatch.setenv("TOTAL_BUDGET", "10")
    result, issues = startup_mod.check_budget(env)

    assert issues == 1
    assert result["status"] == "error"
    assert "state.json" in result["error"]


def test_check_budget_uses_unresolved_ledger_upper_bound(tmp_path, monkeypatch):
    from ouroboros import usage_accounting as ua

    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    (tmp_path / "state" / "state.json").write_text(
        '{"spent_usd":0,"spent_calls":0}\n', encoding="utf-8",
    )
    (tmp_path / "settings.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "logs" / "events.jsonl").write_text("", encoding="utf-8")
    ua.ensure_legacy_imported(tmp_path)
    reservation = ua.reserve_attempt(ua.AttemptRequest(
        model="openai/gpt-5.5",
        provider="openrouter",
        reservation_usd=1.0,
        drive_root=tmp_path,
        global_limit_usd=1.25,
    ))
    ua.mark_dispatched(reservation)
    ua.mark_unresolved(reservation, "timeout")
    env = types.SimpleNamespace(drive_path=lambda rel: tmp_path / rel)

    monkeypatch.setenv("TOTAL_BUDGET", "1.25")
    result, issues = startup_mod.check_budget(env)

    assert issues == 1
    assert result["status"] == "emergency"
    assert result["spent_usd"] == 1.0
    assert result["remaining_usd"] == 0.25
    assert result["unresolved_upper_bound_usd"] == 1.0
    assert result["accounting_authority"] == "physical_attempt_ledger"
    assert result["cost_final"] is False


def test_check_budget_uses_canonical_root_for_split_worker(tmp_path, monkeypatch):
    from ouroboros import usage_accounting as ua

    canonical = tmp_path / "canonical"
    child = tmp_path / "child"
    for root in (canonical, child):
        (root / "state").mkdir(parents=True)
        (root / "logs").mkdir()
        (root / "state" / "state.json").write_text("{}\n", encoding="utf-8")
        (root / "settings.json").write_text("{}\n", encoding="utf-8")
        (root / "logs" / "events.jsonl").write_text("", encoding="utf-8")
    ua.ensure_legacy_imported(canonical)
    reservation = ua.reserve_attempt(ua.AttemptRequest(
        model="openai/gpt-5.5", provider="openrouter", reservation_usd=4.0,
        drive_root=canonical, global_limit_usd=10.0,
    ))
    ua.mark_dispatched(reservation)
    ua.settle_attempt(reservation, {}, cost_usd=4.0, cost_final=True)
    env = types.SimpleNamespace(
        drive_path=lambda rel: child / rel,
        budget_drive_root=canonical,
    )

    monkeypatch.setenv("TOTAL_BUDGET", "10")
    result, issues = startup_mod.check_budget(env)

    assert issues == 0
    assert result["status"] == "ok"
    assert result["spent_usd"] == 4.0
    assert result["remaining_usd"] == 6.0
    assert result["cost_final"] is True


def test_verify_restart_reports_corrupt_claim_json(tmp_path):
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "logs").mkdir(parents=True)
    (tmp_path / "state" / "pending_restart_verify.json").write_text("{bad", encoding="utf-8")
    env = types.SimpleNamespace(drive_path=lambda rel: tmp_path / rel)

    startup_mod.verify_restart(env, "abc123")

    events = [
        json.loads(line)
        for line in (tmp_path / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["type"] == "restart_verify"
    assert events[-1]["ok"] is False
    assert events[-1]["error"] == "pending_restart_verify_invalid"


def test_verify_restart_closes_promoted_backlog_only_on_absorb(tmp_path):
    import ouroboros.improvement_backlog as ib

    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "logs").mkdir(parents=True)
    ib.append_backlog_items(tmp_path, [{
        "summary": "promoted fix", "category": "c", "source": "post_task",
        "evidence": "e", "fingerprint": "fp-x", "id": "ibl-x",
    }])
    (tmp_path / "state" / "pending_restart_verify.json").write_text(
        json.dumps({"expected_sha": "goodsha"}), encoding="utf-8")
    (tmp_path / "state" / "evolution_campaign.json").write_text(json.dumps({
        "status": "active",
        "post_task_backlog_id": "ibl-x",
        "active_transaction": {"transaction_id": "tx1", "task_id": "t1", "commit_sha": "goodsha"},
    }), encoding="utf-8")
    env = types.SimpleNamespace(drive_path=lambda rel: tmp_path / rel, drive_root=tmp_path)

    startup_mod.verify_restart(env, "goodsha")  # sha matches -> absorbed

    by_id = {i["id"]: i for i in ib.load_backlog_items(tmp_path)}
    assert by_id["ibl-x"]["status"] == "done"  # closed only after absorb
    camp = json.loads((tmp_path / "state" / "evolution_campaign.json").read_text(encoding="utf-8"))
    assert "post_task_backlog_id" not in camp
    assert int(camp.get("absorbed_cycles_done") or 0) == 1


def test_verify_restart_absorb_persists_cycle_outcome_and_owner_report(tmp_path):
    """The _mark (pending-claim) absorb path must record cycle_outcome='absorbed'
    into the DURABLE transaction_history entry and stage a pending_owner_report.

    transaction_history is appended as ``dict(tx)`` (a copy), so cycle_outcome has
    to be set BEFORE the append — otherwise the durable record loses the outcome.
    This pins that ordering and the staged owner-report the server later delivers.
    """
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "logs").mkdir(parents=True)
    (tmp_path / "state" / "pending_restart_verify.json").write_text(
        json.dumps({"expected_sha": "goodsha"}), encoding="utf-8")
    (tmp_path / "state" / "evolution_campaign.json").write_text(json.dumps({
        "status": "active",
        "active_transaction": {"transaction_id": "tx1", "task_id": "t1", "commit_sha": "goodsha"},
    }), encoding="utf-8")
    env = types.SimpleNamespace(drive_path=lambda rel: tmp_path / rel, drive_root=tmp_path)

    startup_mod.verify_restart(env, "goodsha")  # expected == observed -> absorbed via _mark

    camp = json.loads((tmp_path / "state" / "evolution_campaign.json").read_text(encoding="utf-8"))
    assert "active_transaction" not in camp
    assert int(camp.get("absorbed_cycles_done") or 0) == 1
    history = camp.get("transaction_history") or []
    assert history, "absorbed transaction must be appended to durable history"
    absorbed = history[-1]
    assert absorbed["transaction_id"] == "tx1"
    assert absorbed["cycle_outcome"] == "absorbed"  # set before the dict(tx) append
    report = camp.get("pending_owner_report") or {}
    assert report.get("cycle_outcome") == "absorbed"
    assert report.get("commit_sha") == "goodsha"


def test_verify_restart_rejects_stale_claim_against_active_evolution_transaction(tmp_path):
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "logs").mkdir(parents=True)
    (tmp_path / "state" / "pending_restart_verify.json").write_text(
        json.dumps({"expected_sha": "oldsha"}),
        encoding="utf-8",
    )
    (tmp_path / "state" / "evolution_campaign.json").write_text(
        json.dumps({
            "status": "active",
            "active_transaction": {
                "transaction_id": "tx1",
                "task_id": "task1",
                "commit_sha": "newsha",
                "restart_required": True,
            },
        }),
        encoding="utf-8",
    )
    env = types.SimpleNamespace(drive_path=lambda rel: tmp_path / rel)

    startup_mod.verify_restart(env, "oldsha")

    events = [
        json.loads(line)
        for line in (tmp_path / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    campaign = json.loads((tmp_path / "state" / "evolution_campaign.json").read_text(encoding="utf-8"))
    assert events[-1]["type"] == "restart_verify"
    assert events[-1]["ok"] is False
    assert campaign["active_transaction"]["restart_required"] is True
    assert campaign["active_transaction"]["restart_verified"] is False
    assert campaign["active_transaction"]["restart_mismatch"]["active_commit_sha"] == "newsha"


def test_verify_restart_reconciles_reachable_dangling_evolution_transaction(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "logs").mkdir(parents=True)
    (tmp_path / "state" / "evolution_campaign.json").write_text(
        json.dumps({
            "status": "active",
            "active_transaction": {
                "transaction_id": "tx1",
                "task_id": "task1",
                "commit_sha": "goodsha",
                "restart_required": True,
                "restart_verified": False,
            },
        }),
        encoding="utf-8",
    )

    def fake_run(*_args, **_kwargs):
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(startup_mod.subprocess, "run", fake_run)
    env = types.SimpleNamespace(drive_path=lambda rel: tmp_path / rel, repo_dir=tmp_path, drive_root=tmp_path)

    startup_mod.verify_restart(env, "headsha")

    campaign = json.loads((tmp_path / "state" / "evolution_campaign.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (tmp_path / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "active_transaction" not in campaign
    assert campaign["absorbed_cycles_done"] == 1
    assert campaign["transaction_history"][0]["verified_by"] == "boot_reconciliation"
    assert campaign["transaction_history"][0]["cycle_outcome"] == "absorbed"
    assert events[-1]["type"] == "evolution_tx_reconciled"
    # Block 5C: the post-restart resolution must reach the solve-capability ledger.
    checkpoints = [
        json.loads(line)
        for line in (tmp_path / "state" / "evolution_checkpoints.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    tags = [c for c in checkpoints if c.get("kind") == "cycle_outcome"]
    assert tags and tags[-1]["cycle_outcome"] == "absorbed"
    assert tags[-1]["task_id"] == "task1"
    assert tags[-1]["source"] == "boot_reconcile"


def test_verify_restart_abandons_unreachable_dangling_evolution_transaction(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "logs").mkdir(parents=True)
    (tmp_path / "state" / "evolution_campaign.json").write_text(
        json.dumps({
            "status": "active",
            "active_transaction": {
                "transaction_id": "tx1",
                "task_id": "task1",
                "commit_sha": "lostsha",
                "restart_required": True,
                "restart_verified": False,
            },
        }),
        encoding="utf-8",
    )

    def fake_run(*_args, **_kwargs):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(startup_mod.subprocess, "run", fake_run)
    env = types.SimpleNamespace(drive_path=lambda rel: tmp_path / rel, repo_dir=tmp_path, drive_root=tmp_path)

    startup_mod.verify_restart(env, "headsha")

    campaign = json.loads((tmp_path / "state" / "evolution_campaign.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (tmp_path / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "active_transaction" not in campaign
    assert campaign["transaction_history"][0]["cycle_outcome"] == "abandoned"
    assert campaign["transaction_history"][0]["abandoned_reason"] == "commit_not_reachable_at_boot"
    assert events[-1]["type"] == "evolution_tx_abandoned"


def test_lifespan_calls_apply_settings_to_env_before_supervisor(monkeypatch):
    """apply_settings_to_env must be called in server lifespan before _start_supervisor_if_needed.

    Regression test for: ANTHROPIC_API_KEY from settings.json not visible to
    resolve_claude_runtime at server startup because apply_settings_to_env was
    only called inside the _run_supervisor background thread.
    """
    import ast
    import pathlib

    server_src = (pathlib.Path(__file__).parent.parent / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(server_src)

    # Find the async lifespan function
    lifespan_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan":
            lifespan_fn = node
            break
    assert lifespan_fn is not None, "lifespan async function not found in server.py"

    # Collect (lineno, name) for every Call node anywhere inside the lifespan,
    # sorted by source line so the ordering check is meaningful.
    calls_by_line = []
    for node in ast.walk(lifespan_fn):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                calls_by_line.append((node.lineno, fn.id))
            elif isinstance(fn, ast.Attribute):
                calls_by_line.append((node.lineno, fn.attr))
    calls_by_line.sort()
    call_names = [name for _, name in calls_by_line]

    assert "_apply_settings_to_env" in call_names, (
        "_apply_settings_to_env must be called inside lifespan"
    )
    assert "_start_supervisor_if_needed" in call_names, (
        "_start_supervisor_if_needed must be called inside lifespan"
    )

    env_line = next(ln for ln, name in calls_by_line if name == "_apply_settings_to_env")
    supervisor_line = next(ln for ln, name in calls_by_line if name == "_start_supervisor_if_needed")
    assert env_line < supervisor_line, (
        f"_apply_settings_to_env (line {env_line}) must appear before "
        f"_start_supervisor_if_needed (line {supervisor_line}) in lifespan"
    )


def test_check_uncommitted_changes_never_commits_even_when_launcher_managed(monkeypatch, tmp_path):
    """Regression for v4.36.1: worker-side startup check must never run git
    add/commit, even under OUROBOROS_MANAGED_BY_LAUNCHER=1. Rescue is owned by
    supervisor-side safe_restart(rescue_and_reset) in _bootstrap_supervisor_repo.
    """
    env = types.SimpleNamespace(
        repo_dir=tmp_path,
        repo_path=lambda rel: tmp_path / rel,
        branch_dev="ouroboros",
        launcher_managed=True,
    )
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return types.SimpleNamespace(returncode=0, stdout=" M server.py\n")
        raise AssertionError(
            f"Unexpected subprocess call {cmd}: worker-side check_uncommitted_changes "
            "must not mutate git state (no add/commit)"
        )

    monkeypatch.setenv("OUROBOROS_MANAGED_BY_LAUNCHER", "1")
    monkeypatch.setattr(startup_mod.subprocess, "run", fake_run)

    result, issues = startup_mod.check_uncommitted_changes(env)

    assert issues == 1
    assert result["status"] == "warning"
    assert result["auto_committed"] is False
    assert result["auto_rescue_skipped"] == "supervisor_side_rescue_owns_this"
    assert calls == [["git", "status", "--porcelain"]]

# --- check_worker_memory (closes ibl-worker-memory-bloat) -----------------

def _write_worker_ledger(tmp_path, *, rows):
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    ledger = state_dir / "process_ledger.jsonl"
    with ledger.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _mock_proc_status(monkeypatch, rss_kb_by_pid):
    """Replace open() so /proc/<pid>/status returns synthetic VmRSS lines."""
    from io import StringIO
    real_open = open

    def fake_open(path, *args, **kwargs):
        path_str = str(path)
        if path_str.startswith("/proc/") and path_str.endswith("/status"):
            tail = path_str[len("/proc/"):-len("/status")]
            try:
                pid = int(tail)
            except ValueError:
                return real_open(path, *args, **kwargs)
            rss_kb = rss_kb_by_pid.get(pid)
            if rss_kb is None:
                return real_open(path, *args, **kwargs)
            return StringIO(f"Name:\tpython\nPid:\t{pid}\nVmRSS:\t{rss_kb} kB\n")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)


def _mock_pid_is_alive(monkeypatch, alive_pids):
    def fake_is_alive(pid):
        return pid in alive_pids

    monkeypatch.setattr("ouroboros.platform_layer.pid_is_alive", fake_is_alive)


def test_check_worker_memory_warns_when_over_threshold(tmp_path, monkeypatch):
    """A worker above 2GB RSS returns a WARNING with issues=1."""
    pid = 99001
    _write_worker_ledger(tmp_path, rows=[
        {"ts": "2026-08-17T00:00:00+00:00", "pid": pid, "pgid": pid,
         "purpose": "worker:0", "scope": "session"},
    ])
    env = types.SimpleNamespace(
        drive_root=tmp_path,
        drive_path=lambda rel: tmp_path / rel,
    )
    _mock_pid_is_alive(monkeypatch, {pid})
    # 3 GB in kB > 2 GB threshold.
    _mock_proc_status(monkeypatch, {pid: 3 * 1024 * 1024})

    result, issues = startup_mod.check_worker_memory(env)

    assert issues == 1
    assert result["status"] == "memory_bloat"
    assert result["workers_checked"] == 1
    assert len(result["workers_warned"]) == 1
    warned = result["workers_warned"][0]
    assert warned["pid"] == pid
    assert warned["rss_bytes"] == 3 * 1024 * 1024 * 1024
    assert warned["rss_mb"] == 3221.2


def test_check_worker_memory_ok_below_threshold(tmp_path, monkeypatch):
    """A worker under 2GB RSS returns ok with issues=0."""
    pid = 99002
    _write_worker_ledger(tmp_path, rows=[
        {"ts": "2026-08-17T00:00:00+00:00", "pid": pid, "pgid": pid,
         "purpose": "worker:1", "scope": "session"},
    ])
    env = types.SimpleNamespace(
        drive_root=tmp_path,
        drive_path=lambda rel: tmp_path / rel,
    )
    _mock_pid_is_alive(monkeypatch, {pid})
    # 1 GB in kB < 2 GB threshold.
    _mock_proc_status(monkeypatch, {pid: 1 * 1024 * 1024})

    result, issues = startup_mod.check_worker_memory(env)

    assert issues == 0
    assert result["status"] == "ok"
    assert result["workers_checked"] == 1
    assert "workers_warned" not in result


def test_check_worker_memory_ok_when_ledger_missing(tmp_path, monkeypatch):
    """No process_ledger.jsonl returns ok (nothing to check)."""
    env = types.SimpleNamespace(
        drive_root=tmp_path,
        drive_path=lambda rel: tmp_path / rel,
    )
    _mock_pid_is_alive(monkeypatch, set())
    _mock_proc_status(monkeypatch, {})

    result, issues = startup_mod.check_worker_memory(env)

    assert issues == 0
    assert result["status"] == "ok"
    assert result["workers_checked"] == 0


def test_check_worker_memory_skips_dead_workers(tmp_path, monkeypatch):
    """A worker that vanished since the ledger write is skipped, not warned."""
    pid = 99003
    _write_worker_ledger(tmp_path, rows=[
        {"ts": "2026-08-17T00:00:00+00:00", "pid": pid, "pgid": pid,
         "purpose": "worker:2", "scope": "session"},
    ])
    env = types.SimpleNamespace(
        drive_root=tmp_path,
        drive_path=lambda rel: tmp_path / rel,
    )
    _mock_pid_is_alive(monkeypatch, set())  # pid NOT in alive set
    _mock_proc_status(monkeypatch, {pid: 3 * 1024 * 1024})

    result, issues = startup_mod.check_worker_memory(env)

    assert issues == 0
    assert result["status"] == "ok"
    assert result["workers_checked"] == 0


def test_check_worker_memory_ignores_non_worker_ledger_rows(tmp_path, monkeypatch):
    """Rows whose purpose does NOT start with 'worker:' are ignored."""
    env = types.SimpleNamespace(
        drive_root=tmp_path,
        drive_path=lambda rel: tmp_path / rel,
    )
    _write_worker_ledger(tmp_path, rows=[
        {"ts": "2026-08-17T00:00:00+00:00", "pid": 100, "pgid": 100,
         "purpose": "session", "scope": "session"},
        {"ts": "2026-08-17T00:00:01+00:00", "pid": 101, "pgid": 101,
         "purpose": "daemon", "scope": "daemon"},
    ])
    _mock_pid_is_alive(monkeypatch, {100, 101})
    _mock_proc_status(monkeypatch, {100: 3 * 1024 * 1024, 101: 3 * 1024 * 1024})

    result, issues = startup_mod.check_worker_memory(env)

    assert issues == 0
    assert result["status"] == "ok"
    assert result["workers_checked"] == 0
