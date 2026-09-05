"""The scenario table of the live E2E stand: ``{id, prompt, settings_overrides, acceptance}``.

Every acceptance is a CALLABLE over durable artifacts (task_results rows, the lane clone's
git history, the task-drive ledgers, /proc) — never a keyword judgement of model prose
(BIBLE P5). The prompts are what a paid model sees; the ``stub_script`` of each row is the
$0 rehearsal of the same flow against the loopback stub model (``--stub``).

Owns its own reason to change (the product surface each scenario drives), so it lives
apart from the orchestration in ``run_live_lanes.py``.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
import re
import subprocess
import time
from typing import Any, Callable

from devtools.benchmarks.common.server_runner import _api, _api_status

SM1_NEW_ACCENT = "#2f7de1"
SM1_COMMIT_MESSAGE = "ui: e2e_live SM1 accent token change (reviewed commit)"
SM1_CSS_PATH = "web/style.css"
# ``web/onboarding.css`` is inlined into the standalone first-run page and mirrors the app's
# ``:root`` tokens BY VALUE; ``tests/test_web_typography_static.py`` pins that every token both
# files declare resolves to the same value. ``--accent`` is one of them, so the change lands in
# BOTH files in one reviewed commit (the first paid run edited style.css alone and the tests
# preflight of ``commit_reviewed`` refused the commit on the parity invariant).
SM1_MIRROR_CSS_PATH = "web/onboarding.css"
SM1_CSS_PATHS = (SM1_CSS_PATH, SM1_MIRROR_CSS_PATH)
_ACCENT_RE = re.compile(r"^(\s*--accent:\s*)([^;]+);", re.MULTILINE)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_TOKEN_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);")
# The runtime's typed refusal prefix on a blocked review tool result ("⚠️ CODE: ...").
_REFUSAL_CODE_RE = re.compile(r"⚠️\s*([A-Z][A-Z_]+):")
_REVIEW_TOOLS = ("preflight_review", "commit_reviewed")

SW1_OBJECTIVE = (
    "E2E_LIVE_SW1: survey this repository with TWO scouts running in parallel. Delegate "
    "'list the top-level directories and name the three largest' to scout A and 'list the "
    "test modules under tests/system_e2e and count them' to scout B via schedule_subagent "
    "(subagent_id 'scout'), wait for both with wait_tasks, then summarize both results."
)
SW1_ROSTER_ID = "scout"

SK1_SKILL = "e2e_live_probe"
SK1_SKILL_MD = f"""---
name: {SK1_SKILL}
description: Loopback probe extension authored by the live E2E stand SK1 scenario.
version: 0.1.0
type: extension
entry: plugin.py
plugin_api: "2.0"
permissions: ["tool", "inject_chat"]
model_experience:
  what_model_sees: 'E2E_LIVE_SK1 adds a loopback probe echo tool to the toolbox'
  token_effect: 'one catalogue line'
---
Probe extension body: one echo tool, no host or network access.
"""
SK1_PLUGIN = (
    "def _echo(ctx, message='hi'):\n"
    "    return f'echo: {message}'\n"
    "\n"
    "def register(api):\n"
    "    api.register_tool(\n"
    "        'echo', _echo, description='echo probe',\n"
    "        schema={'type': 'object', 'properties': {'message': {'type': 'string'}}},\n"
    "    )\n"
)
# The ONLY privileged grant the stand ever issues: the manifest above requests exactly it,
# so "no host/network grant" holds by construction of the grant call, not by a denylist.
SK1_GRANTS = ["inject_chat"]
SK1_ECHO_MESSAGE = "ping-e2e-live"
SK1_ECHO_EXPECTED = f"echo: {SK1_ECHO_MESSAGE}"   # exactly what ``_echo`` in SK1_PLUGIN returns


def _git(args: list[str], cwd: pathlib.Path) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), check=False, capture_output=True, text=True)
    return (proc.stdout or "").strip()


def accent_value(css_text: str) -> str:
    match = _ACCENT_RE.search(css_text)
    return match.group(2).strip() if match else ""


def css_with_accent(css_text: str, value: str) -> str:
    return _ACCENT_RE.sub(lambda m: f"{m.group(1)}{value};", css_text, count=1)


def css_root_tokens(css_text: str) -> dict[str, str]:
    """``{token: value}`` of the FIRST ``:root`` block, comments stripped — the same reading
    ``tests/test_web_typography_static.py`` applies to both stylesheets."""
    css = _CSS_COMMENT_RE.sub("", css_text)
    end = css.find("\n}")
    root = css if end < 0 else css[:end]
    if not root.lstrip().startswith(":root"):
        return {}
    return {name: " ".join(value.split()) for name, value in _CSS_TOKEN_RE.findall(root)}


def css_mirror_drift(style_css: str, onboarding_css: str) -> dict[str, tuple[str, str]]:
    """Shared ``:root`` tokens whose values differ between the two files (empty == parity)."""
    style, onboarding = css_root_tokens(style_css), css_root_tokens(onboarding_css)
    return {token: (style[token], onboarding[token])
            for token in sorted(set(style) & set(onboarding)) if style[token] != onboarding[token]}


def commit_refusal_facts(ledger: dict, tools_rows: list, stored: dict) -> dict:
    """The TYPED trail of every ``commit_reviewed``/``preflight_review`` refusal of a task.

    Three durable sources, none of them model prose: the advisory ledger's attempt rows
    (``phase``/``status``/``block_reason``) and advisory-run statuses, the tools.jsonl rows of
    the two review tools (their typed ``status`` plus the runtime's own ``⚠️ CODE:`` refusal
    prefix — PREFLIGHT_BLOCKED, TESTS_PREFLIGHT_BLOCKED, SCOPE_REVIEW_BLOCKED, ...), and the
    task's terminal ``reason_code`` (``budget_exhausted`` = BudgetExceeded, ``deadline_local``
    = the deadline). The first paid run's SM1 lanes failed on exactly this ladder and the
    result rows named none of it."""
    attempts = [a for a in (ledger.get("attempts") or []) if isinstance(a, dict)]
    runs = [r for r in (ledger.get("advisory_runs") or []) if isinstance(r, dict)]
    calls = []
    for row in tools_rows:
        if str(row.get("tool") or "") not in _REVIEW_TOOLS:
            continue
        match = _REFUSAL_CODE_RE.search(str(row.get("result_preview") or ""))
        calls.append({"tool": str(row.get("tool") or ""), "status": str(row.get("status") or ""),
                      "code": match.group(1) if match else ""})
    return {
        "commit_attempts": [{"attempt": a.get("attempt"), "phase": str(a.get("phase") or ""),
                             "status": str(a.get("status") or ""), "block_reason": str(a.get("block_reason") or "")}
                            for a in attempts],
        "advisory_run_statuses": [str(r.get("status") or "") for r in runs],
        "review_tool_calls": calls,
        "refusal_codes": sorted({c["code"] for c in calls if c["code"]}),
        "terminal_status": str(stored.get("status") or ""),
        "terminal_reason_code": str(stored.get("reason_code") or ""),
    }


def dispatch_verdict(rows: list, expected_text: str) -> dict:
    """What the durable tools.jsonl rows of an extension surface prove about its dispatch.

    ``extension_generation`` alone is NOT proof of a successful physical call: the dispatcher
    stamps it on failed outcomes too. A dispatch counts only when the row's typed ``status`` is
    ``ok`` AND the recorded result is exactly the extension's own output."""
    last = rows[-1] if rows else {}
    meta = last.get("tool_result_meta") if isinstance(last.get("tool_result_meta"), dict) else {}
    digest = str(meta.get("extension_generation") or "")
    return {"row_present": bool(rows), "status": str(last.get("status") or ""),
            "generation": digest, "generation_ok": bool(re.fullmatch(r"[0-9a-f]{8,64}", digest)),
            "physical_dispatch": meta.get("physical_dispatch") is True,
            "echo_ok": str(last.get("result_preview") or "").strip() == expected_text}


class DuplicateCheckKey(RuntimeError):
    """A scenario wrote the same check key twice (see ``LaneContext.check``)."""


class LaneContext:
    """What one lane hands its scenario: the live server, its clone/data root, the durable
    readers, the optional UI client, and the two verdict maps the acceptance fills."""

    def __init__(self, *, server: Any, clone: pathlib.Path, data_root: pathlib.Path, oracle: Any,
                 harness: Any, ui: Any, ui_reason: str, shots: pathlib.Path, log: Callable[[str], None],
                 task_timeout: float, restart: Callable[[], Any]) -> None:
        self.server = server
        self.clone = pathlib.Path(clone)
        self.data_root = pathlib.Path(data_root)
        self.oracle = oracle
        self.h = harness  # tests.system_e2e.harness: wait_until / wait_durable_result / proc oracles
        self.ui = ui
        self.ui_reason = ui_reason
        self.shots = pathlib.Path(shots)
        self.log = log
        self.task_timeout = float(task_timeout)
        self._restart = restart
        self.checks: dict[str, bool] = {}
        self.facts: dict[str, Any] = {}
        self.screenshots: list[str] = []

    def check(self, name: str, ok: bool, **facts: Any) -> bool:
        """One verdict per key. A second write to the same key is a scenario bug (the SK1
        author/dispatch awaits once shared ``http_terminal_completed`` and the later one
        erased the earlier), so it is refused loudly instead of silently winning."""
        if name in self.checks:
            raise DuplicateCheckKey(f"check {name!r} already recorded for this lane; namespace it per task")
        self.checks[name] = bool(ok)
        self.facts.update(facts)
        return bool(ok)

    def submit(self, description: str, *, metadata: dict | None = None) -> str:
        body = {
            "description": description, "memory_mode": "forked", "actor_id": "e2e_live",
            "source": "e2e_live", "timeout_sec": int(self.task_timeout),
            "metadata": {"source": "e2e_live", "delegation_role": "root", **(metadata or {})},
        }
        created = _api(self.server.base_url, "POST", "/api/tasks", body, timeout=60)
        task_id = str(created.get("task_id") or "")
        if not task_id:
            raise RuntimeError(f"task submit refused: {created!r}")
        return task_id

    def wait_task(self, task_id: str, *, label: str = "") -> dict:
        """Wait for the HTTP terminal, then for the DURABLE terminal row (they differ in time).

        ``label`` prefixes the two check keys (``author_http_terminal_completed``, ...) so a
        scenario awaiting several tasks keeps one verdict PER task instead of the last await
        overwriting the earlier ones."""
        prefix = f"{label}_" if label else ""
        result = self.server.wait_task(task_id, timeout=self.task_timeout + 300)
        if str(result.get("status") or "") == "timeout":
            self.server.cancel_task(task_id)
            result = self.server.wait_task(task_id, timeout=300)
        self.check(f"{prefix}http_terminal_completed", result.get("status") == "completed",
                   **{f"{prefix}http_status": str(result.get("status") or "")})
        stored = {}
        try:
            stored = self.h.wait_durable_result(self.oracle, task_id, timeout=180)
        except AssertionError as exc:
            self.facts[f"{prefix}durable_result_error"] = str(exc)[:500]
        self.check(f"{prefix}durable_terminal_completed", stored.get("status") == "completed")
        terminal = stored or result
        self.facts[f"{prefix}terminal"] = {"task_id": task_id, "status": str(terminal.get("status") or ""),
                                           "reason_code": str(terminal.get("reason_code") or "")}
        self.facts["runtime_result"] = terminal  # the lane's runtime disclosure: the LAST awaited task
        return terminal

    def wait_events(self, oracle: Any, event_type: str, predicate: Callable[[dict], bool], timeout: float = 90) -> list:
        """Rows of ``event_type`` matching ``predicate``, waiting for the ASYNC event queue: the
        durable task row lands before ``events.jsonl`` catches up, so a read right after the
        terminal would race the writer."""
        return self.h.wait_until(
            lambda: [row for row in oracle.events(event_type) if predicate(row)] or None, timeout) or []

    def check_paid_tokens(self, task_ids: list[str]) -> None:
        """NOT fail-open: a 0/0 llm_usage row is the crashed-subprocess / silent-403 signature."""
        ids = set(task_ids)
        rows = self.wait_events(self.oracle, "llm_usage",
                                lambda row: str(row.get("task_id") or "") in ids or str(row.get("root_task_id") or "") in ids)
        prompt_tokens = sum(int(row.get("prompt_tokens") or 0) for row in rows)
        self.check("prompt_tokens_positive", prompt_tokens > 0,
                   llm_usage_rows=len(rows), prompt_tokens=prompt_tokens,
                   completion_tokens=sum(int(row.get("completion_tokens") or 0) for row in rows))

    def screenshot(self, name: str) -> None:
        if self.ui is None:
            return
        path = self.shots / f"{name}.png"
        try:
            self.ui.screenshot(path)
            self.screenshots.append(str(path))
        except Exception as exc:  # noqa: BLE001 - the shot is evidence, never a gate
            self.facts[f"screenshot_error_{name}"] = f"{type(exc).__name__}: {exc}"[:300]

    def restart(self) -> None:
        self.server = self._restart()
        if self.ui is not None:
            self.ui.rebind(self.server.base_url)


# --------------------------------------------------------------------------- #
# SM1 — a CSS custom property change lands through commit_reviewed (the S2 set + UI)
# --------------------------------------------------------------------------- #

def sm1_prompt() -> str:
    return (
        "Self-modification task. Change ONLY the value of the CSS custom property `--accent` inside "
        f"the top-level `:root` block to `{SM1_NEW_ACCENT}` in BOTH {SM1_CSS_PATH} and "
        f"{SM1_MIRROR_CSS_PATH} (the onboarding stylesheet is inlined standalone and mirrors the app's "
        "tokens by value; tests/test_web_typography_static.py pins that parity). Keep every other byte "
        "of both files identical. Then run preflight_review(commit_message=...) and land exactly those "
        f"two files through commit_reviewed with commit_message '{SM1_COMMIT_MESSAGE}', paths "
        f"{list(SM1_CSS_PATHS)!r}, goal 'Change the accent token for the live E2E stand' and scope "
        f"'{SM1_CSS_PATH} and {SM1_MIRROR_CSS_PATH} only'. If preflight_review answers PREFLIGHT_BLOCKED "
        "because VERSION is not in scope, do NOT bump the version: retry commit_reviewed with "
        "skip_advisory_review=true (the audited advisory-only skip; the tests preflight, the triad and "
        "the scope review still run). Finish once the commit has landed."
    )


def run_sm1(ctx: LaneContext) -> None:
    before = {path: (ctx.clone / path).read_text(encoding="utf-8") for path in SM1_CSS_PATHS}
    ctx.facts["accent_before"] = accent_value(before[SM1_CSS_PATH])
    task_id = ctx.submit(sm1_prompt())
    ctx.facts["task_id"] = task_id
    stored = ctx.wait_task(task_id)
    # The commit LANDED in the lane clone: under blocking enforcement that is only reachable
    # through PASS verdicts from both review organs.
    log_output = _git(["log", "-n", "5", "--format=%s"], ctx.clone)
    committed = {path: subprocess.run(["git", "show", f"HEAD:{path}"], cwd=str(ctx.clone),
                                      check=False, capture_output=True, text=True).stdout
                 for path in SM1_CSS_PATHS}
    ctx.check("commit_landed", SM1_COMMIT_MESSAGE in log_output)
    ctx.check("committed_css_carries_new_accent",
              all(accent_value(committed[p]) == SM1_NEW_ACCENT and committed[p] != before[p] for p in SM1_CSS_PATHS),
              accent_committed={p: accent_value(committed[p]) for p in SM1_CSS_PATHS})
    drift = css_mirror_drift(committed[SM1_CSS_PATH], committed[SM1_MIRROR_CSS_PATH])
    ctx.check("committed_css_mirror_parity", not drift, css_mirror_drift=drift)
    ctx.check("worktree_clean_after_commit", _git(["status", "--porcelain"], ctx.clone) == "")
    task_oracle = ctx.oracle.task_drive(task_id)
    ledger = task_oracle.advisory_review()
    runs = [r for r in (ledger.get("advisory_runs") or []) if isinstance(r, dict)]
    ctx.check("advisory_ledger_row_present", bool(runs))
    ctx.facts["commit_reviewed_refusals"] = commit_refusal_facts(ledger, task_oracle.tools_rows(), stored)
    ctx.check("scope_review_complete_event",
              bool(ctx.wait_events(task_oracle, "scope_review_complete", lambda _row: True)))
    ctx.check_paid_tokens([task_id])
    # R12: the computed style is read from the COMMITTED CSS after a restart.
    ctx.restart()
    if ctx.ui is None:
        ctx.check("ui_computed_style", False, ui_reason=ctx.ui_reason)
        return
    ctx.ui.goto("/")
    observed = str(ctx.ui.computed_property(":root", "--accent") or "").strip()
    # Only meaningful on a landed commit: a served working-tree edit would show the same value.
    ctx.check("ui_computed_style", observed == SM1_NEW_ACCENT and ctx.checks["commit_landed"],
              accent_computed=observed)
    ctx.screenshot("sm1_after_restart")


def sm1_stub_script(clone: pathlib.Path) -> dict:
    writes = [{"tool": "write_file", "arguments": {
        "root": "system_repo", "path": path,
        "content": css_with_accent((clone / path).read_text(encoding="utf-8"), SM1_NEW_ACCENT)}}
        for path in SM1_CSS_PATHS]
    return {"agent": [
        *writes,
        {"tool": "preflight_review", "arguments": {"commit_message": SM1_COMMIT_MESSAGE}},
        # The deterministic release-metadata preflight blocks a VERSION-less diff (BIBLE P9);
        # the S2 set lands it through the AUDITED advisory-only skip (recorded as bypassed).
        # ``skip_tests`` is a documented residual of the $0 rehearsal, NOT of the paid prompt
        # (which runs the hermetic suite as its tests preflight): the loopback lane's
        # ``OPENAI_COMPATIBLE_BASE_URL`` is projected into the server environment and
        # ``preflight_runner._preflight_env`` scrubs only ``OUROBOROS_*``/secret-suffixed keys,
        # so ``tests/test_settings_effort.py`` routes on it and fails deterministically inside
        # any loopback lane (observed: four ``test_get_review_models_*`` failures). The mirror
        # parity is proven here by ``committed_css_mirror_parity`` over the landed commit.
        {"tool": "commit_reviewed", "arguments": {
            "commit_message": SM1_COMMIT_MESSAGE, "paths": list(SM1_CSS_PATHS), "skip_tests": True,
            "skip_advisory_review": True,
            "goal": "Change the accent token for the live E2E stand",
            "scope": f"{SM1_CSS_PATH} and {SM1_MIRROR_CSS_PATH} only."}},
        {"final": "SM1 done: the accent token change landed through commit_reviewed."},
    ]}


# --------------------------------------------------------------------------- #
# SW1 — Swarm: force_plan + roster, >=2 children, fanout receipt, cost rollup, no orphans
# --------------------------------------------------------------------------- #

def sw1_roster(child_model: str) -> str:
    return json.dumps({"enabled": True, "items": [{
        "subagent_id": SW1_ROSTER_ID,
        "recommended_use": "Read-only scout for parallel repository surveys.",
        "route": {"kind": "api_model", "target_id": child_model},
        "effort": "low",
    }]})


def _find_root_task(ctx: LaneContext, marker: str) -> str:
    results_dir = ctx.data_root / "task_results"
    for path in sorted(results_dir.glob("*.json")) if results_dir.is_dir() else []:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(row, dict) and not row.get("parent_task_id") and marker in json.dumps(row):
            return str(row.get("task_id") or path.stem)
    return ""


def run_sw1(ctx: LaneContext) -> None:
    ui_path = ctx.ui is not None
    if ui_path:
        # The owner's path: the Swarm button arms force_plan on the WS chat frame.
        ctx.ui.goto("/")
        ctx.ui.send_chat(SW1_OBJECTIVE, swarm=True)
        ctx.screenshot("sw1_swarm_sent")
        parent_id = ctx.h.wait_until(lambda: _find_root_task(ctx, "E2E_LIVE_SW1"), 300) or ""
    else:
        parent_id = ctx.submit(SW1_OBJECTIVE, metadata={"force_plan": True, "force_plan_source": "swarm"})
    ctx.check("ui_swarm_path_exercised", ui_path, ui_reason=ctx.ui_reason)
    ctx.check("root_task_admitted", bool(parent_id), task_id=parent_id)
    if not parent_id:
        return
    stored = ctx.wait_task(parent_id)
    ctx.check("plan_review_engaged", isinstance(stored.get("plan_review_state"), dict))
    # The quiescent path, not the forced one: every child result absorbed before the final.
    ctx.check("clean_finalization", str(stored.get("reason_code") or "") != "children_unabsorbed",
              parent_reason_code=str(stored.get("reason_code") or ""))
    children = ctx.oracle.child_task_ids(parent_id)
    ctx.check("at_least_two_children", len(children) >= 2, children=children)
    lineage_ok = bool(children)
    for child_id in children:
        row = ctx.oracle.task_result(child_id)
        provenance = row.get("depth_provenance") if isinstance(row.get("depth_provenance"), dict) else {}
        lineage_ok = lineage_ok and (
            row.get("parent_task_id") == parent_id and row.get("root_task_id") == parent_id
            and row.get("delegation_role") == "subagent" and int(provenance.get("achieved_depth") or 0) >= 1
            and row.get("status") == "completed")
    ctx.check("children_causal_lineage", lineage_ok)
    fanouts = ctx.oracle.task_drive(parent_id).events("swarm_fanout")
    fanned = {str(t) for row in fanouts for t in (row.get("task_ids") or [])}
    ctx.check("swarm_fanout_receipt_covers_children", bool(children) and set(children) <= fanned,
              fanout_task_ids=sorted(fanned))
    done = ctx.wait_events(ctx.oracle, "task_done", lambda row: str(row.get("task_id") or "") == parent_id)
    ctx.check("cost_rollup_with_children",
              bool(done) and "accounted_upper_bound_usd_with_children" in done[-1]
              and ctx.h.retired_cost_alias_paths(done[-1]) == [],
              accounted_upper_bound_usd_with_children=(done[-1].get("accounted_upper_bound_usd_with_children") if done else None))
    tree = set(ctx.h.process_tree_pids(ctx.server.proc.pid))
    carriers = ctx.h.pids_with_env_value(str(ctx.data_root))
    ctx.check("no_orphans_during_run", bool(carriers) and all(pid in tree for pid in carriers),
              env_carrier_pids=len(carriers))
    ctx.check_paid_tokens([parent_id, *children])
    ctx.screenshot("sw1_done")


def sw1_stub_script(_clone: pathlib.Path) -> dict:
    child_id_re = re.compile(r"Subagent request queued ([0-9a-f]{8})")
    # The wait_tasks projection pairs each child id with its exact result hash.
    child_result_re = re.compile(r'"task_id": "([0-9a-f]{8})".{0,400}?"child_result_sha256": "([0-9a-f]{64})"', re.DOTALL)

    def wait_step(text: str) -> dict:
        ids = sorted(set(child_id_re.findall(text)))
        if len(ids) < 2:
            return {"final": "E2E_SCRIPT_ERROR: fewer than two scheduled child ids visible"}
        return {"tool": "wait_tasks", "arguments": {"task_ids": ids, "timeout_sec": 300, "mode": "all_terminal"}}

    def dispose_step(index: int):
        def step(text: str) -> dict:
            pairs = sorted(set(child_result_re.findall(text)))
            if len(pairs) <= index:
                return {"final": "E2E_SCRIPT_ERROR: child result hash missing for the disposition"}
            child_id, sha = pairs[index]
            return {"tool": "tree_note", "arguments": {
                "kind": "decision", "text": f"Absorbed scout {child_id} into the summary.",
                "payload": {"type": "child_result_disposition", "child_task_id": child_id,
                            "disposition": "integrated", "child_result_sha256": sha}}}
        return step

    def scout(label: str, objective: str) -> dict:
        return {"tool": "schedule_subagent", "arguments": {
            "subagent_id": SW1_ROSTER_ID, "objective": f"Scout {label}: {objective}",
            "expected_output": "A short listing."}}

    return {
        "router": [{"tool": "promote_chat_to_task", "arguments": {
            "objective": SW1_OBJECTIVE, "title": "SW1 swarm survey", "predecessor_task_id": ""}},
            {"final": "Routed the Swarm request into a managed task."}],
        "agent": [
            {"tool": "plan_task", "arguments": {
                "goal": "Survey the repository with two parallel scouts.",
                "plan": "Schedule two scouts, wait for both, summarize.",
                "spec": {"deliverables": ["Two scout results summarized."],
                         "acceptance_claims": ["Both scouts completed and were absorbed."]}}},
            scout("A", "list the top-level directories"),
            scout("B", "list the test modules under tests/system_e2e"),
            wait_step,
            dispose_step(0),
            dispose_step(1),
            {"final": "SW1_PARENT_DONE: both scouts absorbed."},
        ],
        "child": [{"final": "SW1_CHILD_DONE: survey complete."}],
        "probe": [{"final": "No existing task duplicates this request."}],
    }


# --------------------------------------------------------------------------- #
# SK1 — the model authors a skill; the owner side reviews, grants, enables, dispatches
# --------------------------------------------------------------------------- #

def sk1_prompt() -> str:
    return (
        f"Author a new external skill named '{SK1_SKILL}' using write_file with root='skill_payload', "
        f"bucket='external', skill_name='{SK1_SKILL}'. Write exactly two files. SKILL.md:\n"
        f"{SK1_SKILL_MD}\nplugin.py:\n{SK1_PLUGIN}\nThen run skill_preflight(skill='{SK1_SKILL}') and "
        "finish; do not review, enable or grant anything yourself."
    )


def _skill_entry(base_url: str, name: str) -> dict:
    listing = _api(base_url, "GET", "/api/extensions", timeout=30)
    rows = listing if isinstance(listing, list) else (listing.get("extensions") or listing.get("skills") or [])
    return next((row for row in rows if isinstance(row, dict) and row.get("name") == name), {})


def run_sk1(ctx: LaneContext) -> None:
    from ouroboros.extension_surface_names import extension_surface_name

    payload_dir = ctx.data_root / "skills" / "external" / SK1_SKILL
    author_id = ctx.submit(sk1_prompt())
    ctx.facts["author_task_id"] = author_id
    ctx.wait_task(author_id, label="author")
    ctx.check("payload_authored_by_model", (payload_dir / "SKILL.md").is_file() and (payload_dir / "plugin.py").is_file())
    preflight_rows = [r for r in ctx.oracle.task_drive(author_id).tools_rows() if r.get("tool") == "skill_preflight"]
    ctx.check("skill_preflight_called", bool(preflight_rows))
    if not payload_dir.is_dir():
        return
    review = _api_status(ctx.server.base_url, "POST", f"/api/skills/{SK1_SKILL}/review", {}, timeout=900)
    review_state = ctx.oracle._json(f"state/skills/{SK1_SKILL}/review.json")
    findings = [f for f in (review_state.get("findings") or []) if isinstance(f, dict)]
    ctx.check("review_all_pass",
              review["status"] == 200 and review["body"].get("status") == "clean" and bool(findings)
              and all(str(f.get("verdict") or "") == "PASS" for f in findings),
              review_status=review["body"].get("status"), findings=len(findings),
              findings_failed=[f.get("item") for f in findings if str(f.get("verdict") or "") != "PASS"])
    grants = _api_status(ctx.server.base_url, "POST", f"/api/skills/{SK1_SKILL}/grants", {"items": SK1_GRANTS}, timeout=120)
    granted = ctx.oracle._json(f"state/skills/{SK1_SKILL}/grants.json").get("granted_permissions")
    ctx.check("grants_exactly_requested", (grants["body"].get("grants") or {}).get("all_granted") is True
              and granted == SK1_GRANTS, granted_permissions=granted)
    toggled = _api_status(ctx.server.base_url, "POST", f"/api/skills/{SK1_SKILL}/toggle", {"enabled": True}, timeout=300)
    entry = _skill_entry(ctx.server.base_url, SK1_SKILL)
    ctx.check("enabled_live_loaded", toggled["body"].get("enabled") is True and not toggled["body"].get("error")
              and entry.get("live_loaded") is True and entry.get("dispatch_live") is True)
    ctx.screenshot("sk1_enabled")
    surface = extension_surface_name(SK1_SKILL, "echo")
    dispatch_id = ctx.submit(f"Call the tool `{surface}` once with message '{SK1_ECHO_MESSAGE}', then finish.")
    ctx.facts["dispatch_task_id"] = dispatch_id
    ctx.wait_task(dispatch_id, label="dispatch")
    rows = [r for r in ctx.oracle.task_drive(dispatch_id).tools_rows() if str(r.get("tool") or "") == surface]
    verdict = dispatch_verdict(rows, SK1_ECHO_EXPECTED)
    ctx.check("dispatch_durable_row_with_generation", verdict["row_present"] and verdict["generation_ok"],
              extension_generation=verdict["generation"])
    ctx.check("dispatch_physical_call_ok_with_echo", verdict["status"] == "ok" and verdict["echo_ok"],
              dispatch_status=verdict["status"], dispatch_echo_ok=verdict["echo_ok"],
              dispatch_physical=verdict["physical_dispatch"])
    ctx.check_paid_tokens([author_id, dispatch_id])
    _api_status(ctx.server.base_url, "POST", f"/api/skills/{SK1_SKILL}/toggle", {"enabled": False}, timeout=300)
    deleted = _api_status(ctx.server.base_url, "POST", f"/api/skills/{SK1_SKILL}/delete", {}, timeout=120)
    ctx.check("deleted_payload_and_state", deleted["status"] == 200 and not deleted["body"].get("error")
              and not payload_dir.exists() and not (ctx.data_root / "state" / "skills" / SK1_SKILL).exists())


def sk1_stub_script(_clone: pathlib.Path) -> dict:
    from ouroboros.extension_surface_names import extension_surface_name

    payload = {"root": "skill_payload", "bucket": "external", "skill_name": SK1_SKILL}
    return {"agent": [
        {"tool": "write_file", "arguments": {**payload, "path": "SKILL.md", "content": SK1_SKILL_MD}},
        {"tool": "write_file", "arguments": {**payload, "path": "plugin.py", "content": SK1_PLUGIN}},
        {"tool": "skill_preflight", "arguments": {"skill": SK1_SKILL}},
        {"final": "SK1_AUTHORED: payload written and preflighted."},
        {"tool": extension_surface_name(SK1_SKILL, "echo"), "arguments": {"message": SK1_ECHO_MESSAGE}},
        {"final": "SK1_DISPATCH_DONE: echo absorbed."},
    ]}


# --------------------------------------------------------------------------- #
# The table
# --------------------------------------------------------------------------- #

@dataclasses.dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    prompt: str
    settings_overrides: dict
    needs_ui: bool
    acceptance: Callable[[LaneContext], None]
    stub_script: Callable[[pathlib.Path], dict]
    # ROOT tasks the scenario mints: the runner's run-wide budget reserves
    # ``per_task_usd x root_tasks`` per attempt (the runtime fences each root task TREE at
    # OUROBOROS_PER_TASK_COST_USD, so SW1's scouts spend under their one root's ceiling).
    root_tasks: int = 1

    def overrides(self, model: str) -> dict:
        out = dict(self.settings_overrides)
        if self.id == "SW1":
            out["OUROBOROS_SUBAGENTS"] = sw1_roster(model)
        return out


SCENARIOS: dict[str, Scenario] = {
    "SM1": Scenario(
        "SM1", "CSS custom property change lands through commit_reviewed (advanced, blocking)",
        sm1_prompt(), {"OUROBOROS_RUNTIME_MODE": "advanced", "OUROBOROS_REVIEW_ENFORCEMENT": "blocking"},
        True, run_sm1, sm1_stub_script),
    "SW1": Scenario(
        "SW1", "Swarm: force_plan + roster, two children, fanout receipt, cost rollup, no orphans",
        SW1_OBJECTIVE, {"OUROBOROS_MAX_WORKERS": 4, "OUROBOROS_MAX_SUBAGENT_DEPTH": 1},
        True, run_sw1, sw1_stub_script),
    "SK1": Scenario(
        "SK1", "Skill lifecycle: model authors SKILL.md+plugin.py, preflight, review, grants, enable, dispatch",
        sk1_prompt(), {}, False, run_sk1, sk1_stub_script, root_tasks=2),
}


def diff_sha256(clone: pathlib.Path, pre_head: str, post_head: str) -> str:
    if not pre_head or not post_head or pre_head == post_head:
        return ""
    diff = subprocess.run(["git", "diff", "--binary", pre_head, post_head], cwd=str(clone),
                          check=False, capture_output=True).stdout
    return hashlib.sha256(diff).hexdigest()


def head_sha(clone: pathlib.Path) -> str:
    return _git(["rev-parse", "HEAD"], clone)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
