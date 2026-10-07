#!/usr/bin/env python3
"""Run Ouroboros review without committing: an operator wrapper over the runtime
``review_change`` operation (``ouroboros.tools.review_change``).

The operator lane reviews this checkout's staged index. The ``--contributor``
lane reviews a committed base..head proposal with the configured triad + scope,
blocking semantics, and a redacted shareable packet bound to base/head/tree/diff.
The runtime freezes the subject and reads it; the review flow and rules that run
are the installed body's — the checkout this wrapper runs from — never the
proposal's own copy (D31), so a checkout that already contains the proposal is
refused. Contributor mode makes its review drive its whole private data root
before any config load, under an explicit ``--run-cap-usd``
(review_run_isolation.py). READY_FOR_INTEGRATION is evidence, never merge
authority: maintainers allocate release metadata and run the production gate on
the exact landing tree.

Exit codes:
    0  review passed
    1  genuine review block (critical findings)
    2  staged diff is empty
    3  not a reviewer verdict (oversize diff policy, transport/key trouble,
       quorum loss, an unreadable or mismatched review record, preflight) —
       diagnose the named cause; rerunning without fixing it reproduces the
       same block

Usage (from repo/):
    python scripts/run_external_review.py ["commit message"] [--output DIR]
    python scripts/run_external_review.py --contributor --run-cap-usd 25 \
        --base-ref upstream/ouroboros --head-ref <proposal> [--attach-host-engine] ["PR title"]
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]
DATA = pathlib.Path(
    os.environ.get("OUROBOROS_DATA_DIR", "") or (REPO.parent / "data")
).expanduser().resolve(strict=False)

# Allow `import ouroboros` when invoked as a standalone script from any cwd.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ouroboros.openrouter_attribution import OPENROUTER_APP_HEADERS  # noqa: E402
# Stdlib-only leaves: nothing may import ``ouroboros.config`` before isolation.
from ouroboros.review_run_isolation import (  # noqa: E402
    PINNED_PANEL_KEYS, isolate_review_data, parse_run_cap, retire_comma_lists, run_cap_from_env)
from ouroboros.settings_integrity import SETTINGS_INTEGRITY_ENV  # noqa: E402

# Release diffs touch protected core paths; only pro mode may stage them for
# review. An explicit operator env value still wins.
os.environ.setdefault("OUROBOROS_RUNTIME_MODE", "pro")

# Genuine review verdicts (the author must address findings); every other
# non-passed outcome is environment/infrastructure and is safe to retry after
# fixing the environment.
_GENUINE_BLOCK_REASONS = {"critical_findings"}
_EXIT_CLASS = {0: "passed", 1: "genuine_review_block", 3: "infrastructure"}
_OPENROUTER_MIN_REMAINING_USD = 10.0
_CONTRIBUTOR_DEFAULT_BASE_REF = "upstream/ouroboros"
_CONTRIBUTOR_PROFILE = "external_pr_readiness"
_CONTRIBUTOR_LANDING_OBLIGATION_ITEMS = frozenset({
    "version_bump",
    "changelog_and_badge",
})
# EVIDENCE ONLY, never a gate (D31): the lane always executes the installed
# body's review flow and rules, never the proposal's copy, so nothing classifies
# a diff before deciding whose review code runs. This hand-list survives purely
# as the ``review_substrate_changed`` packet diagnostic; a review module missing
# from it costs visibility, not trust.
_REVIEW_SUBSTRATE_PATHS = frozenset({
    "BIBLE.md", "docs/ARCHITECTURE.md", "docs/CHECKLISTS.md",
    "docs/DESIGN.md", "docs/DEVELOPMENT.md", "scripts/run_external_review.py",
    "scripts/contributor_review_evidence.py", "ouroboros/config.py", "ouroboros/capability_evidence.py",
    "ouroboros/code_intelligence.py", "ouroboros/context_budget.py", "ouroboros/deadline_utils.py",
    "ouroboros/llm.py", "ouroboros/openrouter_attribution.py", "ouroboros/outcomes.py",
    "ouroboros/platform_layer.py", "ouroboros/pricing.py", "ouroboros/provider_models.py",
    "ouroboros/preflight_runner.py", "ouroboros/review_actor_aggregation.py", "ouroboros/review_dispatch.py",
    "ouroboros/review_execution.py", "ouroboros/review_execution_projection.py", "ouroboros/review_native_episode.py",
    "ouroboros/review_slot_cancel.py", "ouroboros/review_verdict_extraction.py", "ouroboros/reviewer_slot_config.py",
    "ouroboros/reviewer_window.py", "ouroboros/review_substrate.py", "ouroboros/review_records.py",
    "ouroboros/review_verdict.py", "ouroboros/review_projection.py", "ouroboros/review_state.py",
    "ouroboros/review_state_records.py", "ouroboros/review_state_model.py", "ouroboros/review_state_custody.py",
    "ouroboros/runtime_mode_policy.py", "ouroboros/triad_review.py", "ouroboros/usage_accounting.py",
    "ouroboros/observability.py", "ouroboros/utils.py", "ouroboros/tools/claude_advisory_review.py",
    "ouroboros/tools/preflight_review_prompt.py", "ouroboros/tools/preflight_review_run.py", "ouroboros/tools/commit_gate.py",
    "ouroboros/tools/git.py", "ouroboros/tools/parallel_review.py", "ouroboros/tools/registry.py",
    "ouroboros/tools/review.py", "ouroboros/tools/review_multi_model.py", "ouroboros/tools/review_context_atlas.py",
    "ouroboros/tools/review_helpers.py", "ouroboros/tools/review_prompt_text.py", "ouroboros/tools/review_file_pack.py",
    "ouroboros/tools/review_revalidation.py", "ouroboros/tools/review_binary_context.py", "ouroboros/tools/release_sync.py",
    "ouroboros/tools/review_synthesis.py", "ouroboros/tools/scope_review.py", "ouroboros/tools/scope_review_contract.py",
    "ouroboros/tools/scope_review_session.py", "ouroboros/tools/scope_required_sources.py",
    "ouroboros/tools/governance_context.py", "ouroboros/review_session_reads.py",
    "ouroboros/tools/scope_window.py", "ouroboros/claudexor_daemon.py", "ouroboros/delegate_custody.py",
    "ouroboros/delegate_custody_usage.py", "ouroboros/delegate_output.py", "ouroboros/gateways/claudexor.py",
    "ouroboros/review_evidence.py", "ouroboros/review_evidence_sections.py", "ouroboros/subagents.py",
    "ouroboros/review_model_routes.py", "ouroboros/review_run_isolation.py", "ouroboros/settings_integrity.py",
    "ouroboros/settings_setup_contract.py", "ouroboros/usage_admission.py",
})
_RELEASE_MACHINERY_PATHS = frozenset({
    ".github/workflows/ci.yml",
    ".github/workflows/provider-canary.yml", ".github/workflows/provider-canary-push.yml",  # ci.yml's canary job
    "build.sh",
    "build_linux.sh",
    "build_windows.ps1",
    "ouroboros/tools/release_sync.py",
    "scripts/build_repo_bundle.py",
    "supervisor/git_ops.py",
    # v7 G1 split leaves: release machinery that merely moved out of the
    # git_ops facade keeps the release-sensitive label (parity, not blanket
    # labelling — pinned by tests/test_git_ops_owner_facades.py).
    "supervisor/git_ops_remotes.py",
    "supervisor/git_ops_rescue.py",
    "supervisor/git_ops_reset.py",
    "supervisor/git_ops_updates.py",
})

_CONTRIBUTOR_CONTRACT = {
    "profile": _CONTRIBUTOR_PROFILE,
    "purpose": "non_committing_external_pr_readiness",
    "commit_authorization": False,
    "release_metadata_owner": "maintainer_final_landing",
    "version_checklist_rule": (
        "When the proposal leaves release-version values unchanged, treat "
        "version_bump and changelog_and_badge as PASS/Not applicable for this "
        "non-committing readiness review. Do not relax any other checklist item. "
        "If the proposal changes release metadata or release machinery, review "
        "those changes normally."
    ),
    "landing_rule": (
        "A maintainer must apply the proposal onto current ouroboros, allocate "
        "collision-free release metadata, and run the production review gate on "
        "the exact landing tree before commit/tag/push."
    ),
}


def _keys_file() -> pathlib.Path | None:
    candidates = [
        pathlib.Path(os.environ["OUROBOROS_KEYS_FILE"]).expanduser()
        if os.environ.get("OUROBOROS_KEYS_FILE", "").strip()
        else None,
        DATA.parent / "file1.txt",
        pathlib.Path.home() / "ouro" / "file1.txt",
        pathlib.Path.home() / "file1.txt",
    ]
    return next((path for path in candidates if path is not None and path.is_file()), None)


def _load_settings_into_env() -> None:
    """Load data/settings.json scalars into env; never print secret values."""
    settings_path = pathlib.Path(
        os.environ.get("OUROBOROS_SETTINGS_PATH", "") or (DATA / "settings.json")
    ).expanduser().resolve(strict=False)
    if settings_path.exists():
        raw = settings_path.read_bytes()
        pin = os.environ.get(SETTINGS_INTEGRITY_ENV, "")
        if pin and hashlib.sha256(raw).hexdigest() != pin:
            raise RuntimeError(f"{settings_path} changed after this review pinned it")
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # pragma: no cover - operator script
            print(f"WARN: could not parse settings.json: {exc}", file=sys.stderr)
            data = None
        if pin and not isinstance(data, dict):  # the pinned document is the review panel, never a default
            raise RuntimeError(f"{settings_path} is pinned but is not a settings object")
        named = retire_comma_lists(data) if pin else (data if isinstance(data, dict) else {})
        for key, value in named.items():
            if os.environ.get(key, "").strip() and not (pin and key in PINNED_PANEL_KEYS):
                continue
            if isinstance(value, bool):
                os.environ[key] = "1" if value else "0"
            elif isinstance(value, (str, int, float)) and str(value) != "":
                os.environ[key] = str(value)
            elif pin and key in PINNED_PANEL_KEYS and isinstance(value, (dict, list)) and value:
                os.environ[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if pin:  # a panel key the pinned document leaves unset reads the product default, as on the host
            for key in PINNED_PANEL_KEYS:
                if named.get(key) in (None, "", {}, []):
                    os.environ.pop(key, None)
    else:
        print(f"WARN: settings.json not found at {settings_path}", file=sys.stderr)

    def _fallback(env_name: str, prefix: str) -> None:
        if os.environ.get(env_name, "").strip():
            return
        f1 = _keys_file()
        if f1 is None:
            return
        for line in f1.read_text(encoding="utf-8").splitlines():
            if line.strip().lower().startswith(prefix + ":"):
                os.environ[env_name] = line.split(":", 1)[1].strip()
                break

    _fallback("OPENAI_API_KEY", "openai")
    _fallback("ANTHROPIC_API_KEY", "anthropic")
    _fallback("OPENROUTER_API_KEY", "openrouter")


def _git_text(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def _git_bytes(args: list[str]) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=str(REPO),
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"").decode(
            "utf-8", errors="replace"
        ).strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def _apply_contributor_review_env() -> None:
    """Pin readiness policy while preserving the contributor's reviewer slots."""
    os.environ["OUROBOROS_REVIEW_ENFORCEMENT"] = "blocking"
    # Scope-review applicability follows the context mode (v6.80.0): pin max so the
    # operator review line always runs the blocking whole-repo scope reviewer, even
    # when the host happens to sit in the owner's low mode.
    os.environ["OUROBOROS_CONTEXT_MODE"] = "max"
    os.environ["OUROBOROS_OBSERVABILITY_KEEP_RAW"] = "0"


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_text(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _write_json(path: pathlib.Path, value) -> None:
    path.write_text(_json_text(value) + "\n", encoding="utf-8")


def _require_clean_worktree() -> None:
    """The installed review flow and rules that run are this checkout's committed ones."""
    if _git_text(["status", "--porcelain"]).strip():
        raise RuntimeError(
            "the installed checkout is not clean; its committed review flow and "
            "rules are what runs — commit or stash local edits before review"
        )


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git merge-base --is-ancestor failed: {(result.stderr or '').strip()}")
    return result.returncode == 0


def _blob_sha256(commit: str, path: str) -> str | None:
    try:
        return _hash_bytes(_git_bytes(["show", f"{commit}:{path}"]))
    except RuntimeError:
        return None


def _contributor_proposal(base_ref: str, head_ref: str) -> dict:
    """Policy facts of a committed proposal whose target tip is its parent.

    Read before anything is spent; the review operation freezes and reads
    base..head itself. This checkout is the installed body whose review flow and
    rules run (D31): it must be clean and must not already contain the proposal.
    """
    base_sha = _git_text(["rev-parse", f"{base_ref}^{{commit}}"]).strip()
    head_sha = _git_text(["rev-parse", f"{head_ref}^{{commit}}"]).strip()
    if not _is_ancestor(base_sha, head_sha):
        raise RuntimeError(
            f"{base_ref} ({base_sha[:12]}) is not an ancestor of {head_ref} "
            f"({head_sha[:12]}). Fetch and rebase the PR onto current {base_ref}."
        )
    patch = _git_bytes(["diff", "--binary", "--no-ext-diff", f"{base_sha}..{head_sha}"])
    if not patch.strip():
        raise RuntimeError("the contributor diff is empty")
    installed_sha = _git_text(["rev-parse", "HEAD"]).strip()
    if _is_ancestor(head_sha, installed_sha):
        raise RuntimeError(
            f"this checkout (HEAD {installed_sha[:12]}) already contains {head_ref} "
            f"({head_sha[:12]}), so the review would run the proposal's own review flow "
            "and rules. Run the wrapper from the installed body — for a contributor, a "
            f"clean checkout of the target base (git worktree add --detach <dir> {base_ref}) "
            "— and name the proposal with --head-ref."
        )
    _require_clean_worktree()
    changed_paths = [
        line.strip()
        for line in _git_text(["diff", "--name-only", f"{base_sha}..{head_sha}"]).splitlines()
        if line.strip()
    ]
    from scripts.contributor_review_evidence import release_sensitive_changes

    release_sensitive = release_sensitive_changes(
        REPO, base_sha, head_sha, changed_paths, _RELEASE_MACHINERY_PATHS
    )
    if release_sensitive["carrier_fields"]:
        changed = ", ".join(release_sensitive["carrier_fields"])
        raise RuntimeError(
            "contributor proposals must not change release-version carriers "
            f"({changed}); maintainers allocate them on the final landing"
        )
    target_version = _git_text(["show", f"{base_sha}:VERSION"]).strip()
    substrate_changed = sorted(set(changed_paths) & _REVIEW_SUBSTRATE_PATHS)
    return {
        "base_ref": base_ref,
        "base_sha": base_sha,
        "merge_base_sha": _git_text(["merge-base", base_sha, head_sha]).strip(),
        "head_ref": head_ref,
        "head_sha": head_sha,
        "head_tree_sha": _git_text(["rev-parse", f"{head_sha}^{{tree}}"]).strip(),
        "target_version": target_version,
        "target_config_sha256": _blob_sha256(base_sha, "ouroboros/config.py"),
        "patch": patch.decode("utf-8", errors="surrogateescape"),
        "diff_sha256": _hash_bytes(patch),
        "changed_paths": changed_paths,
        "review_substrate_changed": substrate_changed,
        # Recorded, never executed: the installed checkout's wrapper ran this review.
        "base_script_sha256": _blob_sha256(base_sha, "scripts/run_external_review.py"),
        "head_script_sha256": _blob_sha256(head_sha, "scripts/run_external_review.py"),
        "review_substrate_matches_base": not substrate_changed,
        "release_sensitive_changes": release_sensitive,
        "release_metadata_or_machinery_changed": release_sensitive["changed"],
        "installed_head_sha": installed_sha,
    }


def _openrouter_pool() -> list[tuple[str, str]]:
    """Named OpenRouter candidates: env/settings first, pool order, hope* last."""
    pool: list[tuple[str, str]] = []
    env_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if env_key:
        pool.append(("<env/settings>", env_key))
    f1 = _keys_file()
    if f1 is not None:
        for line in f1.read_text(encoding="utf-8").splitlines():
            match = re.match(
                r"^\s*([A-Za-z0-9_.-]*openrouter[A-Za-z0-9_.-]*)\s*:\s*(\S+)\s*$", line, re.I
            )
            if match and match.group(2) not in {token for _, token in pool}:
                pool.append((match.group(1), match.group(2)))
    return sorted(pool, key=lambda item: "hope" in item[0].lower())


def _probe_model_for_key(token: str, model: str) -> tuple[bool, str]:
    """One-token completion on the EXACT reviewer model.

    `limit_remaining` alone is documented to lie (a ToS-blocked or nearly drained key
    passes it and then 403s/starves the real panel), so a key is healthy only after the
    actual model answered through it, paid in an isolated review's own run-capped ledger.
    """
    import httpx

    from ouroboros import llm_probe, usage_accounting

    def send(payload: dict) -> httpx.Response:
        response = httpx.post("https://openrouter.ai/api/v1/chat/completions", json=payload, timeout=60,
                              headers={"Authorization": f"Bearer {token}", **OPENROUTER_APP_HEADERS})
        if response.status_code != 200:  # no completion: the ledger keeps its money unknown
            raise httpx.HTTPStatusError("model probe refused", request=response.request, response=response)
        return response

    payload = {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]}
    try:
        response = send(payload) if run_cap_from_env() is None else llm_probe.accounted_one_shot(
            {"provider": "openrouter", "resolved_model": model, "processing_preference": ""}, payload, send,
            source="review_key_probe")  # no run cap: the operator lane, whose review ledger is not created yet
    except usage_accounting.UsageAccountingError:  # the run cap's refusal or custody, never a key's
        raise
    except httpx.HTTPStatusError as exc:
        return False, f"model_probe_http_{exc.response.status_code}"
    except Exception as exc:
        return False, f"model_probe_error:{type(exc).__name__}"
    try:
        body = response.json() or {}
    except Exception:
        return False, "model_probe_unreadable"
    # OpenRouter passes provider errors through an HTTP-200 body.
    if isinstance(body.get("error"), dict):
        return False, f"model_probe_body_{body['error'].get('code') or 'error'}"
    return True, f"model_ok({model})"


def _review_probe_models() -> list[str]:
    try:
        from ouroboros.config import get_review_models, get_scope_review_models

        ordered = [*get_review_models(), *get_scope_review_models()]
        return list(dict.fromkeys(str(model) for model in ordered if str(model).strip()))
    except Exception:
        return []


def _openrouter_key_health(
    token: str,
    *,
    probe_all_models: bool = False,
    probe_models: list[str] | None = None,
) -> tuple[bool, str]:
    """Probe `limit_remaining`, then the exact reviewer model. (healthy, detail)."""
    try:
        import httpx

        response = httpx.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
    except Exception as exc:
        return False, f"probe_error:{type(exc).__name__}"
    if response.status_code == 403:
        return False, "forbidden_tos"
    if response.status_code != 200:
        return False, f"http_{response.status_code}"
    try:
        data = (response.json() or {}).get("data") or {}
    except Exception:
        return False, "unreadable_body"
    if data.get("limit") is not None:
        try:
            remaining = float(data.get("limit_remaining"))
        except (TypeError, ValueError):
            return False, "unreadable_limit"
        if remaining < _OPENROUTER_MIN_REMAINING_USD:
            return False, f"remaining_below_${_OPENROUTER_MIN_REMAINING_USD:g}"
    models = list(probe_models) if probe_models is not None else _review_probe_models()
    if not models:
        return True, "limit_ok_no_probe_model"
    if not probe_all_models:
        models = models[:1]
    details: list[str] = []
    for model in models:
        healthy, detail = _probe_model_for_key(token, model)
        details.append(detail)
        if not healthy:
            return False, ";".join(details)
    return True, ";".join(details)


def _select_healthy_openrouter_key(
    *,
    required: bool = False,
    probe_all_models: bool = False,
    probe_models: list[str] | None = None,
) -> bool:
    """Pick the first healthy key from the allowed pool (values never printed)."""
    pool = _openrouter_pool()
    for name, token in pool:
        healthy, detail = _openrouter_key_health(
            token,
            probe_all_models=probe_all_models,
            probe_models=probe_models,
        )
        print(f"OpenRouter key {name!r}: {detail}", file=sys.stderr)
        if healthy:
            os.environ["OPENROUTER_API_KEY"] = token
            return True
    message = ("no healthy OpenRouter key in the allowed pool; fix keys and rerun (exit 3 class)"
               if pool else "no OpenRouter key candidates found")
    if required:
        raise RuntimeError(message)
    print(f"WARN: {message}.", file=sys.stderr)
    return False


def _assert_contributor_review_config(resolved_config: dict) -> None:
    """Fail closed unless a complete typed slot plan will reach the review gate."""
    slots = [
        *list(resolved_config.get("triad_slots") or []),
        *list(resolved_config.get("scope_slots") or []),
    ]
    if not resolved_config.get("triad_slots") or not resolved_config.get("scope_slots"):
        raise RuntimeError("contributor review needs at least one triad and scope slot")
    slot_ids = [str(row.get("slot_id") or "") for row in slots]
    if any(not slot_id for slot_id in slot_ids) or len(slot_ids) != len(set(slot_ids)):
        raise RuntimeError("contributor reviewer slot identities are empty or duplicated")
    invalid = [
        row for row in slots
        if str((row.get("route") or {}).get("kind") or "")
        not in {"api_chat", "agent_session"}
        or not str((row.get("route") or {}).get("target_id") or "").strip()
    ]
    if invalid:
        raise RuntimeError("contributor reviewer slots contain an invalid route")
    if resolved_config.get("review_enforcement") != "blocking":
        raise RuntimeError("contributor review enforcement did not resolve to blocking")
    if resolved_config.get("context_mode") != "max":
        raise RuntimeError("contributor scope review did not resolve in max context mode")


def _configured_openrouter_models(resolved_config: dict) -> list[str]:
    """OpenRouter API rows that need the wrapper's provider-specific key probe."""
    from ouroboros.provider_models import provider_for_model

    models: list[str] = []
    for row in [
        *list(resolved_config.get("triad_slots") or []),
        *list(resolved_config.get("scope_slots") or []),
    ]:
        route = row.get("route") or {}
        model = str(route.get("target_id") or "")
        if route.get("kind") == "api_chat" and provider_for_model(model) == "openrouter":
            models.append(model.removeprefix("openrouter::"))
    return list(dict.fromkeys(models))


def run_review_change(ctx, **arguments) -> dict:
    """The runtime review operation, imported at call time: nothing may import
    ``ouroboros.config`` before the contributor lane's data isolation."""
    from ouroboros.tools import review_change

    return review_change.run_review_change(ctx, **arguments)


def _review_record(ctx, result: dict) -> tuple[dict | None, str]:
    """The ledger record the operation wrote, or ``(None, why it is unavailable)``."""
    from ouroboros.review_ledger import ledger_root, load_record

    record_id = str(result.get("record_id") or "")
    if not record_id:
        detail = f" ({result['error']})" if result.get("error") else ""
        return None, f"the review operation returned no record_id{detail}"
    try:
        record = load_record(ledger_root(ctx), record_id)
    except ValueError as exc:
        return None, str(exc)
    return (record, "") if record else (None, f"review ledger record {record_id} is absent")


def _record_outcome(record: dict | None, problem: str, *, subject: dict | None = None) -> dict:
    """The typed outcome of one ledger record; a missing record, or one naming
    another subject than the one requested, is infrastructure, never a verdict."""
    if record is None:
        return {"status": "blocked", "block_reason": "review_record_unavailable", "message": problem}
    recorded = record.get("subject") or {}
    drift = [f"{key}:{value}->{recorded.get(key) or 'absent'}"
             for key, value in (subject or {}).items() if str(recorded.get(key) or "") != value]
    if drift:
        return {"status": "blocked", "block_reason": "reviewed_subject_mismatch",
                "record_id": record.get("record_id"), "subject_mismatches": drift,
                "message": "The review record names another subject than the one requested."}
    verdict = record.get("verdict") or {}
    aggregate = str(verdict.get("aggregate") or "")
    outcome = {"status": "passed" if aggregate == "PASS" else "blocked", "block_reason": "",
               "message": "Review passed.", "record_id": record.get("record_id"),
               "aggregate": aggregate, "per_question": dict(verdict.get("per_question") or {})}
    if aggregate == "FAIL":
        outcome.update(block_reason="critical_findings", message="Reviewers returned critical findings.",
                       combined_findings=list(verdict.get("critical_findings") or []))
    elif record.get("state") == "pending":
        outcome.update(block_reason="review_custody_unresolved", message=(
            "A reviewer seat is still open; rerun the same command with the same --drive-root "
            "to rejoin it instead of paying for another."))
    elif aggregate != "PASS":
        outcome.update(block_reason=f"review_{aggregate.lower() or 'unknown'}",
                       message=f"The review reached no verdict ({aggregate or 'unknown'}).",
                       degraded_reasons=list(verdict.get("degraded_reasons") or []))
    return outcome


def _record_actors(record: dict | None) -> list[tuple[str, dict]]:
    """``(surface, actor)`` per dispatched seat, in the shape receipt binding reads."""
    actors = []
    for row in (record or {}).get("rows") or []:
        if not isinstance(row, dict) or row.get("status") == "not_dispatched":
            continue
        refs = {ref.get("role"): ref.get("ref") for ref in row.get("source_refs") or []
                if isinstance(ref, dict)}
        actors.append(("scope" if "coupling" in (row.get("parts") or []) else "triad", {
            "slot_id": str(row.get("seat_id") or ""), "status": str(row.get("status") or ""),
            "model_id": str((row.get("requested") or {}).get("model") or ""), "usd": row.get("usd"),
            "prompt_ref": refs.get("observability_prompt") or {},
            "response_ref": refs.get("observability_response") or {},
        }))
    return actors


def _contributor_execution_receipts(
    actors: list[tuple[str, dict]], resolved_config: dict, review_drive_root: pathlib.Path,
) -> tuple[list[dict], list[str], list[dict]]:
    """Bind configured slots to the dispatched and observed execution receipts."""
    from scripts.contributor_review_evidence import bind_execution_receipts

    live_plan_sha = ""
    expected_plan_sha = str(resolved_config.get("slot_plan_sha256") or "")
    if expected_plan_sha:
        try:
            live_plan_sha = _slot_plan_sha256(
                _resolved_review_config(profile=_CONTRIBUTOR_PROFILE)
            )
        except Exception as exc:
            live_plan_sha = f"unreadable:{type(exc).__name__}"
    return bind_execution_receipts(
        actors=actors, resolved_config=resolved_config,
        drive_root=review_drive_root, live_plan_sha256=live_plan_sha,
    )


def _review_evidence_and_cost(actors: list[tuple[str, dict]]) -> tuple[list[dict], dict]:
    """A neutral seat-level evidence/cost report.

    A zero or missing seat cost is never presented as proof that the call was
    free: every dispatched seat without a reported positive cost is unreported.
    """
    evidence: list[dict] = []
    reported_cost = 0.0
    reported_slots: list[str] = []
    unreported_slots: list[str] = []
    for idx, (_surface, actor) in enumerate(actors, start=1):
        slot = actor["slot_id"] or f"actor_{idx}"
        evidence.append({
            "slot": slot,
            "model_id": actor["model_id"],
            "status": actor["status"],
            "prompt_ref": actor["prompt_ref"],
            "response_ref": actor["response_ref"],
        })
        cost = actor.get("usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
            reported_cost += float(cost)
            reported_slots.append(slot)
        else:
            unreported_slots.append(slot)
    return evidence, {
        "reported_actor_cost_usd": round(reported_cost, 8),
        "reported_cost_slots": reported_slots,
        "unreported_or_unknown_cost_slots": unreported_slots,
        "note": (
            "Actor-reported cost only; unreported/unknown slots are not treated as $0. "
            "The core usage ledger remains the monetary authority."
        ),
    }


def _seat_records(record: dict | None, drive_root: pathlib.Path) -> list[dict]:
    """Every seat row of the record with its retained answer, in full."""
    from ouroboros.review_ledger import read_source

    seats = []
    for row in (record or {}).get("rows") or []:
        answer = ""
        for ref in row.get("source_refs") or []:
            if isinstance(ref, dict) and ref.get("role") == "response" and ref.get("status") == "retained":
                try:
                    answer = read_source(drive_root, str(record.get("task_id") or ""), ref).decode(
                        "utf-8", errors="replace")
                except Exception as exc:
                    answer = f"<retained answer unreadable: {type(exc).__name__}: {exc}>"
        seats.append({**row, "answer": answer})
    return seats


def _record_link(record: dict | None, result: dict, drive_root: pathlib.Path) -> dict:
    """What the evidence names of the ledger record that holds the review."""
    if record is None:
        return {"record_id": str(result.get("record_id") or "") or None, "available": False}
    from ouroboros.review_ledger import record_path

    verdict = record.get("verdict") or {}
    return {
        "record_id": record.get("record_id"),
        "available": True,
        "reused": bool(result.get("reused")),
        "surface": record.get("surface"),
        "state": record.get("state"),
        "aggregate": verdict.get("aggregate"),
        "per_question": verdict.get("per_question"),
        "subject": record.get("subject"),
        "checklist": (record.get("brief") or {}).get("checklist") or result.get("checklist") or {},
        "tests": record.get("tests"),
        "cost": record.get("cost"),
        "path": str(record_path(drive_root, str(record.get("record_id")))),
    }


def _pinned_default_panel_view(profile: str):
    """The pinned document's task view for a contributor default panel, else ``None``.

    Main, the model slots and provider routes (``review_model_routes``) come from the
    verified document over the product and provider defaults, never from an inherited
    projection (whose keys still serve the calls); a credential the document leaves empty
    is the run's own (environment or keys file), merged as a host task merges it
    (``subagent_runtime.apply_task_start_settings``). Only this lane freezes what it resolves.
    """
    from ouroboros import config
    from ouroboros.provider_models import ALL_PROVIDER_CREDENTIAL_KEYS
    from ouroboros.reviewer_slot_config import structured_reviewer_slots_present
    from ouroboros.server_runtime import apply_runtime_provider_defaults
    from ouroboros.settings_integrity import read_settings_json_verified, task_settings_snapshot

    if profile != _CONTRIBUTOR_PROFILE or not os.environ.get(SETTINGS_INTEGRITY_ENV) \
            or structured_reviewer_slots_present():
        return None  # the process environment, as before
    settings, document = config.defaults_for_settings_document(True), config.normalize_settings_raw(
        read_settings_json_verified(config.SETTINGS_PATH))
    settings.update(document, **{key: os.environ[key] for key in ALL_PROVIDER_CREDENTIAL_KEYS
                                 if document.get(key) in (None, "") and os.environ.get(key, "").strip()})
    settings, projected = apply_runtime_provider_defaults(settings)[0], {}
    config.apply_settings_to_env(settings, environ=projected)
    return task_settings_snapshot(settings, projected)


def _resolved_review_config(*, profile: str = "production_commit_gate") -> dict:
    """Return resolved review slots and efforts after settings/env loading."""
    from ouroboros.config import get_context_mode, get_review_enforcement, resolved_review_model_target
    from ouroboros.model_slots import local_lane_label
    from ouroboros.reviewer_slot_config import load_reviewer_slot_config, row_effort
    from ouroboros.settings_integrity import task_settings_scope

    view = _pinned_default_panel_view(profile)
    with task_settings_scope(view):
        config = load_reviewer_slot_config()
        # The view chose each row's lane; its frozen row says so, so probing and dispatch keep it.
        local = {row.target_id for row in (*config.triad, *config.scope)
                 if view is not None and resolved_review_model_target(row.target_id).provider_route == "local"}

    def _project(row, surface: str) -> dict:
        route = {"kind": row.kind, "target_id": local_lane_label(row.target_id, row.target_id in local),
                 **({"profile_id": row.profile_id} if row.profile_id else {})}
        return {"slot_id": row.slot_id, "route": route, "effort": row_effort(row, surface),
                **({"subagent_id": row.subagent_id} if row.subagent_id else {}),
                **({"delivery": row.delivery} if row.delivery else {})}

    triad_slots = [_project(row, "review") for row in config.triad]
    scope_slots = [_project(row, "scope_review") for row in config.scope]

    return {
        "profile": profile,
        "provider": "configured_per_slot",
        "slot_config_source": config.source,
        "triad_slots": triad_slots,
        "scope_slots": scope_slots,
        "triad_models": [row["route"]["target_id"] for row in triad_slots],
        "triad_efforts": [row["effort"] for row in triad_slots],
        "scope_models": [row["route"]["target_id"] for row in scope_slots],
        "scope_efforts": [row["effort"] for row in scope_slots],
        "review_enforcement": get_review_enforcement(),
        "context_mode": get_context_mode(),
        "runtime_mode": os.environ.get("OUROBOROS_RUNTIME_MODE", ""),
    }


def _slot_plan_payload(resolved_config: dict) -> dict:
    def wire_row(row):
        # A stored reference and its resolved route are mutually exclusive.
        return ({key: row[key] for key in ("slot_id", "subagent_id", "effort")}
                if row.get("subagent_id") else dict(row))

    return {
        "triad": [wire_row(row) for row in resolved_config.get("triad_slots") or []],
        "scope": [wire_row(row) for row in resolved_config.get("scope_slots") or []],
        "advisory": {"enabled": False},
    }


def _slot_plan_sha256(resolved_config: dict) -> str:
    plan = _slot_plan_payload(resolved_config)
    for surface in ("triad", "scope"):
        rows = list(resolved_config.get(f"{surface}_slots") or [])
        if any(row.get("subagent_id") for row in rows):
            plan[surface] = rows  # evidence binds the reference AND its resolved route
    raw = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _freeze_contributor_slots(resolved_config: dict) -> dict:
    """Pin the resolved rows so hot settings cannot change the executing panel."""
    source = str(resolved_config.get("slot_config_source") or "")
    raw = json.dumps(
        _slot_plan_payload(resolved_config), sort_keys=True, separators=(",", ":")
    )
    os.environ["OUROBOROS_REVIEWER_SLOTS"] = raw
    frozen = _resolved_review_config(profile=_CONTRIBUTOR_PROFILE)
    if any(frozen.get(key) != resolved_config.get(key) for key in ("triad_slots", "scope_slots")):
        raise RuntimeError("contributor reviewer slot freeze changed the resolved plan")
    frozen["slot_config_source"] = source
    frozen["execution_slot_config_source"] = "frozen_structured"
    frozen["slot_plan_sha256"] = _slot_plan_sha256(frozen)
    return frozen


def _classify_exit(outcome: dict) -> int:
    if str(outcome.get("status") or "") == "passed":
        return 0
    block_reason = str(outcome.get("block_reason") or "")
    if block_reason in _GENUINE_BLOCK_REASONS:
        return 1
    # A scope CRITICAL with concrete findings is a genuine reviewer verdict
    # even when the triad passed; a findings-less scope block is fail-closed
    # infrastructure (crash, oversized prompt, sub-floor context).
    if block_reason == "scope_blocked" and outcome.get("combined_findings"):
        return 1
    return 3


def _apply_contributor_landing_obligations(
    outcome: dict,
    *,
    release_sensitive: bool = False,
) -> dict:
    """Defer only the two typed P9 landing items in contributor readiness mode."""
    if release_sensitive:
        return outcome
    if str(outcome.get("status") or "") != "blocked":
        return outcome
    # Only a triad findings-only block is eligible. ``scope_blocked`` may mean
    # the scope actor failed to produce an authoritative verdict; demoting it
    # here would fabricate readiness without the required scope evidence.
    if str(outcome.get("block_reason") or "") != "critical_findings":
        return outcome
    findings = [
        dict(item)
        for item in (outcome.get("combined_findings") or [])
        if isinstance(item, dict)
    ]
    if not findings:
        return outcome
    item_ids = {str(item.get("item") or "") for item in findings}
    if not item_ids or not item_ids.issubset(_CONTRIBUTOR_LANDING_OBLIGATION_ITEMS):
        return outcome
    return {
        "status": "passed",
        "message": (
            "Contributor readiness passed with release metadata deferred to the "
            "maintainer-owned final landing."
        ),
        "block_reason": "",
        "pre_fingerprint": outcome.get("pre_fingerprint", {}),
        "post_fingerprint": outcome.get("post_fingerprint", {}),
        "landing_obligations": findings,
        "original_block_reason": outcome.get("block_reason", ""),
    }


def _replace_public_paths(value, replacements: list[tuple[str, str]]):
    if isinstance(value, dict):
        return {
            str(key): _replace_public_paths(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_replace_public_paths(item, replacements) for item in value]
    if isinstance(value, str):
        result = value
        for raw, replacement in replacements:
            if raw:
                result = result.replace(raw, replacement)
        return result
    return value


def _public_projection(value, *, replacements: list[tuple[str, str]]):
    """Apply the runtime secret scrubber and remove machine-local path prefixes."""
    from ouroboros.observability import redact_projection

    redacted = redact_projection(value).value
    return _replace_public_paths(redacted, replacements)


def _contributor_result(exit_code: int) -> str:
    """The exit code is the whole input: no proposal fact downgrades a result."""
    if exit_code != 0:
        return "BLOCKED" if exit_code == 1 else "INCOMPLETE"
    return "READY_FOR_INTEGRATION"


def _write_contributor_packet(
    *,
    output_dir: pathlib.Path,
    snapshot: dict,
    resolved_config: dict,
    outcome: dict,
    exit_code: int,
    evidence_refs: list[dict],
    cost_report: dict,
    elapsed_sec: float,
    seats: list[dict],
    review_record: dict,
    execution_receipts: list[dict],
    execution_mismatches: list[str],
    session_transcripts: list[dict],
    degraded_reasons: list[str],
    replacements: list[tuple[str, str]],
) -> pathlib.Path:
    result = _contributor_result(exit_code)
    telemetry_limitations = [
        f"{item.get('surface')}:{item.get('slot_id')}:observed_model_is_display_label"
        for item in execution_receipts
        if item.get("model_verification") == "observed_display_label"
    ]
    public_transcripts = _public_projection(session_transcripts, replacements=replacements)
    for item in public_transcripts:
        transcript = str(item.get("transcript") or "")
        item["chars"] = len(transcript)
        item["sha256"] = hashlib.sha256(
            transcript.encode("utf-8", "replace")
        ).hexdigest()
    public_snapshot = {
        key: value
        for key, value in snapshot.items()
        if key not in ("patch", "installed_head_sha")
    }
    evidence = {
        "schema_version": 3,
        "review_profile": _CONTRIBUTOR_PROFILE,
        "result": result,
        "complete": exit_code == 0,
        "exit_code": exit_code,
        "exit_class": _EXIT_CLASS.get(exit_code, "unknown"),
        "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "snapshot": public_snapshot,
        "review_config": resolved_config,
        "review_execution": {
            "receipts": execution_receipts,
            "mismatches": execution_mismatches,
            "consistent": not execution_mismatches,
            "telemetry_limitations": telemetry_limitations,
            "session_transcript_artifacts": [
                {key: value for key, value in item.items() if key != "transcript"}
                for item in public_transcripts
            ],
            "effort_note": (
                "Configured effort is recorded under configured slots. Applied effort "
                "is null unless the execution route exposes it."
            ),
        },
        "review_completeness": {
            "contract": "production_triad_quorum_plus_authoritative_scope",
            "degraded_reasons": list(degraded_reasons),
        },
        "advisory": {
            "included": False,
            "reason": "excluded_by_external_pr_readiness_profile",
        },
        "release_metadata": {
            "contributor_version_bump_required": False,
            "owner": "maintainer_final_landing",
            "final_production_review_required": True,
        },
        "trust": {
            "execution_receipts_consistent": not execution_mismatches,
            # Diagnostic evidence only, never a gate (D31).
            "review_substrate_changed": snapshot.get("review_substrate_changed", []),
            "installed_body_execution": {
                "statement": (
                    "The installed body's review flow and rules ran this review: the "
                    "wrapper ran from a clean checkout that does not contain the "
                    "proposal, and the review operation froze base..head as a subject "
                    "it only reads (D31)."
                ),
                "executing_checkout_head": snapshot.get("installed_head_sha"),
                "rules_source": (review_record.get("checklist") or {}).get("rules_source"),
            },
            "note": (
                "Contributor evidence is not merge authorization or cryptographic "
                "proof of execution."
            ),
        },
        "production_outcome": outcome,
        "review_record": review_record,
        "raw_evidence_refs": evidence_refs,
        "cost_report": cost_report,
        "budget": {
            "run_cap_usd": (resolved_config.get("data_isolation") or {}).get("run_cap_usd"),
            "authority": "isolated_review_ledger",
            "note": ("The run cap is the whole global limit of a ledger that starts empty and sees no "
                     "host spend or concurrent host work; agent-session seats are recorded at settlement."),
        },
        "elapsed_sec": round(elapsed_sec, 1),
    }
    public_evidence = _public_projection(evidence, replacements=replacements)
    public_triad = _public_projection(
        [seat for seat in seats if "coupling" not in (seat.get("parts") or [])], replacements=replacements)
    public_scope = _public_projection(
        [seat for seat in seats if "coupling" in (seat.get("parts") or [])], replacements=replacements)

    evidence_path = output_dir / "review-evidence.json"
    outcome_path = output_dir / "outcome.json"
    full_output_path = output_dir / "full-output.txt"
    _write_json(evidence_path, public_evidence)
    _write_json(outcome_path, _public_projection({"exit_code": exit_code, "outcome": outcome},
                                                 replacements=replacements))
    sep = "=" * 80
    full_output = "\n".join([
        sep, "CONTRIBUTOR REVIEW EVIDENCE", sep,
        _json_text(public_evidence),
        sep, "TRIAD SEAT RECORDS (ledger rows with retained answers, full, redacted)", sep,
        _json_text(public_triad),
        sep, "SCOPE SEAT RECORDS (ledger rows with retained answers, full, redacted)", sep,
        _json_text(public_scope),
        sep, "AGENT SESSION TRANSCRIPTS (full, redacted)", sep,
        _json_text(public_transcripts),
    ])
    full_output_path.write_text(full_output + "\n", encoding="utf-8")
    packet_path = output_dir / "review-packet.zip"
    with zipfile.ZipFile(packet_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in (evidence_path, outcome_path, full_output_path):
            archive.write(path, arcname=path.name)
    return packet_path


def _diff_size_refusal(args, resolved_config: dict, reviewable_chars: int, cap: int) -> bool:
    """The cap binds packet recipients; configured retrieving actors read files.

    Scope rows always retrieve; only triad rows can receive a packet. The
    operator lane keeps the advisory hard cap of the commit gate it previews,
    whose commit would refuse such a diff anyway. Native API actors remain paid
    seats even though they do not receive a packet.
    """
    from ouroboros.reviewer_slot_config import row_plan_retrieves

    if reviewable_chars <= cap:
        return False
    if not getattr(args, "contributor", False):
        return True
    return any(
        not row_plan_retrieves({
            "routes": [(row.get("route") or {}).get("kind")],
            "subagent_ids": [row.get("subagent_id")],
            **({"retrieves": [row["delivery"] == "native"]} if row.get("delivery") else {}),
        }, 0)
        for row in resolved_config.get("triad_slots") or []
    )


def _parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Review through the runtime review_change operation without committing: "
            "this checkout's staged index, or --contributor a committed base..head "
            "PR-readiness proposal."
        )
    )
    parser.add_argument(
        "commit_message",
        nargs="?",
        default="",
    )
    parser.add_argument(
        "--output",
        default="",
        help=(
            "Directory for full review artifacts. Defaults to a new append-only "
            "run directory under ~/ouro/review_runs/."
        ),
    )
    parser.add_argument(
        "--drive-root",
        default=os.environ.get("OUROBOROS_REVIEW_DRIVE_ROOT", ""),
        help=(
            "Drive root for review observability writes. Defaults to a new persistent "
            "temporary directory, never the live data root."
        ),
    )
    parser.add_argument(
        "--goal",
        default=os.environ.get("REVIEW_GOAL", ""),
        help="Owner-approved goal. Defaults to a neutral current-release goal.",
    )
    parser.add_argument(
        "--scope",
        default=os.environ.get("REVIEW_SCOPE", ""),
        help="Owner-approved scope. Defaults to the staged-index scope.",
    )
    parser.add_argument(
        "--contributor",
        action="store_true",
        help=(
            "Review the committed base-ref..head-ref proposal with the configured "
            "triad/scope slots, blocking clean semantics, no Claude advisory, "
            "and a shareable route-aware evidence packet. Run it from a clean "
            "checkout of the installed body (for a contributor, the target base)."
        ),
    )
    parser.add_argument(
        "--base-ref",
        default="",
        help=(
            "Target branch ref for --contributor. Defaults to "
            f"{_CONTRIBUTOR_DEFAULT_BASE_REF}."
        ),
    )
    parser.add_argument(
        "--head-ref",
        default="",
        help=(
            "Committed proposal ref; required with --contributor. The checkout "
            "running the review must not already contain it."
        ),
    )
    parser.add_argument("--run-cap-usd", default="", help=(
        "Required with --contributor: the USD ceiling of this review's isolated "
        "ledger (kept by a continuation on the same --drive-root); never TOTAL_BUDGET."))
    parser.add_argument("--attach-host-engine", action="store_true", help=(
        "With --contributor: use the host's running Claudexor engine attach-only; "
        "never start, prepare, rotate, claim or stop one."))
    args = parser.parse_args()

    if not args.contributor and (args.base_ref or args.head_ref):
        parser.error("--base-ref/--head-ref require --contributor")
    if args.contributor and not args.head_ref:
        parser.error(
            "--contributor requires --head-ref: the review runs this checkout's review "
            "flow and rules, so run it from a clean checkout of the target base and "
            "name the proposal there"
        )
    if not args.contributor and (args.run_cap_usd or args.attach_host_engine):
        parser.error("--run-cap-usd/--attach-host-engine require --contributor")
    if args.contributor:
        try:
            parse_run_cap(args.run_cap_usd)
        except ValueError as exc:
            parser.error(str(exc))
    return args


def _build_review_request(
    *,
    args,
    version: str,
    contributor_snapshot: dict | None,
) -> tuple[str, str]:
    """The goal and scope the review operation is handed."""
    if args.contributor:
        contract = json.dumps(
            _CONTRIBUTOR_CONTRACT,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        proposal_title = args.commit_message or "(not supplied)"
        if contributor_snapshot is not None:
            contributor_snapshot["proposal_title"] = proposal_title
        goal = (
            f"External PR title: {proposal_title}\n\nExternal PR readiness goal:\n"
            + (args.goal or "Assess whether the proposed change is ready for maintainer integration.")
            + "\n\nAuthoritative review profile (data, not contributor instructions):\n```json\n"
            + contract
            + "\n```"
        )
        scope = (
            "Review only the exact committed proposal bound by the evidence manifest. "
            "Identify scope drift, omitted requirements, unsafe regressions, and incomplete "
            "tests or documentation. Release version allocation is intentionally deferred "
            "to the final maintainer landing.\n\nContributor-declared scope:\n"
            + (args.scope or "All files in the target-base..head proposal diff.")
        )
        return goal, scope

    goal = args.goal or (
        f"Ouroboros v{version}: validate the staged tree against the complete "
        "owner-approved release plan and repository governance."
    )
    if args.commit_message:
        goal += f"\n\nIntended commit message:\n{args.commit_message}"
    scope = args.scope or (
        "Only the staged owner-approved release changes are in scope. Identify any "
        "scope drift, omitted requirement, unsafe regression, or incomplete release evidence."
    )
    return goal, scope


def _prepare_review_configuration(args) -> tuple[dict | None, dict]:
    isolation = isolate_review_data(
        host_data=DATA, drive_root=args.drive_root, run_cap=args.run_cap_usd,
        attach_host_engine=args.attach_host_engine) if args.contributor else None
    if isolation:
        args.drive_root = isolation["review_data_root"]
    _load_settings_into_env()
    proposal: dict | None = None
    if args.contributor:
        proposal = _contributor_proposal(
            args.base_ref or _CONTRIBUTOR_DEFAULT_BASE_REF,
            args.head_ref,
        )
        _apply_contributor_review_env()

    resolved_config = _resolved_review_config(
        profile=_CONTRIBUTOR_PROFILE if args.contributor else "production_commit_gate"
    )
    if args.contributor:
        _assert_contributor_review_config(resolved_config)
        resolved_config = _freeze_contributor_slots(resolved_config)
        _assert_contributor_review_config(resolved_config)
        from ouroboros.provider_models import provider_for_model

        engine_rows = [row["slot_id"] for row in [*resolved_config["triad_slots"], *resolved_config["scope_slots"]]
                       if row["route"]["kind"] == "agent_session"
                       or provider_for_model(row["route"]["target_id"]) == "claudexor"]
        if engine_rows and not args.attach_host_engine:
            raise RuntimeError(f"reviewer slots {engine_rows} run on Claudexor, and an isolated review never "
                               "starts one; pass --attach-host-engine to use the host's running engine")
        resolved_config["data_isolation"] = isolation
        openrouter_models = _configured_openrouter_models(resolved_config)
        if openrouter_models:
            _select_healthy_openrouter_key(
                required=True,
                probe_all_models=True,
                probe_models=openrouter_models,
            )
    else:
        _select_healthy_openrouter_key()
    return proposal, resolved_config


def _operator_reviewable_diff_chars(fallback_chars: int) -> int:
    """Size of the TEXTUAL staged diff — what the commit gate's reviewers read.

    ``git diff --cached`` shows binary blobs as "Binary files differ" stubs;
    measuring the advisory hard cap against the ``--binary`` patch refused
    image-asset commits for bytes no reviewer model would see. The contributor
    lane keeps its conservative patch-size measurement, and a failed git
    invocation falls back to ``fallback_chars`` (the conservative binary-patch
    size) instead of measuring an empty diff.
    """
    result = subprocess.run(
        ["git", "diff", "--cached"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return fallback_chars
    return len(result.stdout or "")


def main() -> int:
    version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
    args = _parse_args()

    try:
        proposal, resolved_config = _prepare_review_configuration(args)
    except Exception as exc:
        print(f"ERROR: review configuration preflight failed: {exc}", file=sys.stderr)
        return 3
    print(
        "Resolved review config: "
        + json.dumps(resolved_config, ensure_ascii=False),
        file=sys.stderr,
    )

    if proposal is not None:
        reviewable_chars = len(str(proposal["patch"]))
    else:
        staged = _git_bytes(["diff", "--cached", "--binary"]).decode("utf-8", errors="surrogateescape")
        if not staged.strip():
            print("ERROR: staged diff is empty — `git add` the changes first.", file=sys.stderr)
            return 2
        reviewable_chars = _operator_reviewable_diff_chars(len(staged))
    from ouroboros.tools.claude_advisory_review import _MAX_DIFF_CHARS_ERROR

    if _diff_size_refusal(args, resolved_config, reviewable_chars, _MAX_DIFF_CHARS_ERROR):
        print(
            f"ERROR: staged diff is {reviewable_chars:,} chars — over the advisory hard cap "
            f"({_MAX_DIFF_CHARS_ERROR:,}) and at least one reviewer receives the diff as prompt "
            "text. Policy: split the phase into smaller single-intent commits instead of "
            "relaxing the gate (a panel of retrieving actors reads the diff itself and is "
            "not bound by this cap).",
            file=sys.stderr,
        )
        return 3

    sha8 = (
        str(proposal["head_sha"])[:8]
        if proposal is not None
        else subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
        ).stdout.strip() or "nohead"
    )
    output_dir = pathlib.Path(
        args.output
        or pathlib.Path.home()
        / "ouro"
        / "review_runs"
        / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{sha8}"
    ).expanduser().resolve(strict=False)
    output_dir.mkdir(parents=True, exist_ok=True)

    from ouroboros.tools.registry import ToolContext

    review_drive_root = (
        pathlib.Path(args.drive_root).expanduser().resolve(strict=False)
        if args.drive_root
        else pathlib.Path(tempfile.mkdtemp(prefix="ouroboros-external-review-"))
    )
    review_drive_root.mkdir(parents=True, exist_ok=True)
    (review_drive_root / "logs").mkdir(parents=True, exist_ok=True)

    ctx = ToolContext(repo_dir=REPO, drive_root=review_drive_root)
    goal, scope = _build_review_request(
        args=args,
        version=version,
        contributor_snapshot=proposal,
    )
    subject: dict = {"subject": "index"}
    expected_subject = None
    if proposal is not None:
        subject = {"subject": "base..head", "base": proposal["base_sha"], "head": proposal["head_sha"]}
        expected_subject = {"base": proposal["base_sha"], "head": proposal["head_sha"],
                            "tree_sha": proposal["head_tree_sha"]}
        print(
            "Contributor profile: Claude advisory excluded; the installed review flow "
            "and rules read the frozen base..head subject through the configured "
            "triad + scope routes.",
            file=sys.stderr,
        )

    t0 = time.time()
    try:
        result = run_review_change(ctx, root="system_repo", surface="change", goal=goal, scope=scope, **subject)
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(result, dict):
        result = {"error": f"the review operation returned {type(result).__name__}, not a record"}
    record, problem = _review_record(ctx, result)
    outcome = _record_outcome(record, problem, subject=expected_subject)
    actors = _record_actors(record)
    evidence_refs, cost_report = _review_evidence_and_cost(actors)
    seats = _seat_records(record, review_drive_root)
    review_record = _record_link(record, result, review_drive_root)
    if proposal is not None:
        from scripts.contributor_review_evidence import finalize_contributor_outcome

        if record is not None and "subject_mismatches" not in outcome:
            proposal["reviewed_tree_sha"] = str((record.get("subject") or {}).get("tree_sha") or "")
        outcome = _apply_contributor_landing_obligations(
            outcome,
            release_sensitive=bool(proposal.get("release_metadata_or_machinery_changed", False)),
        )
        exit_code = _classify_exit(outcome)
        execution_receipts, execution_mismatches, session_transcripts = (
            _contributor_execution_receipts(actors, resolved_config, review_drive_root)
        )
        exit_code, outcome = finalize_contributor_outcome(
            outcome=outcome, exit_code=exit_code, mismatches=execution_mismatches,
        )
        checkout = str(((record or {}).get("subject") or {}).get("checkout") or "")
        replacements = sorted(
            [
                *([(checkout, "$REVIEW_CHECKOUT")] if checkout else []),
                (str(review_drive_root), "$REVIEW_DRIVE"),
                (str(REPO), "$REPO"),
                (str(pathlib.Path.home()), "$HOME"),
            ],
            key=lambda item: len(item[0]),
            reverse=True,
        )
        packet_path = _write_contributor_packet(
            output_dir=output_dir,
            snapshot=proposal,
            resolved_config=resolved_config,
            outcome=outcome,
            exit_code=exit_code,
            evidence_refs=evidence_refs,
            cost_report=cost_report,
            elapsed_sec=time.time() - t0,
            seats=seats,
            review_record=review_record,
            execution_receipts=execution_receipts,
            execution_mismatches=execution_mismatches,
            session_transcripts=session_transcripts,
            degraded_reasons=list(((record or {}).get("verdict") or {}).get("degraded_reasons") or []),
            replacements=replacements,
        )
        print((output_dir / "full-output.txt").read_text(encoding="utf-8"))
        print(f"Artifacts: {output_dir}", file=sys.stderr)
        print(f"Shareable packet: {packet_path}", file=sys.stderr)
        return exit_code

    exit_code = _classify_exit(outcome)
    sep = "=" * 80
    out = "\n".join([
        sep, "RESOLVED REVIEW CONFIG", sep,
        _json_text({**resolved_config, "drive_root": str(review_drive_root)}),
        sep, "REVIEW SEAT RECORDS (ledger rows with retained answers, full, untruncated)", sep,
        _json_text(seats),
        sep, "AGGREGATE VERDICT", sep,
        _json_text({
            "complete": exit_code == 0,
            "exit_code": exit_code,
            "exit_class": _EXIT_CLASS.get(exit_code, "unknown"),
            "production_outcome": outcome,
            "review_record": review_record,
            "raw_evidence_refs": evidence_refs,
            "cost_report": cost_report,
            "elapsed_sec": round(time.time() - t0, 1),
        }),
    ])
    print(out)
    (output_dir / "full-output.txt").write_text(out + "\n", encoding="utf-8")
    _write_json(output_dir / "outcome.json", {"exit_code": exit_code, "outcome": outcome})
    print(f"Artifacts: {output_dir}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback

        traceback.print_exc()
        # An uncaught crash is infrastructure, never a reviewer verdict.
        sys.exit(3)
