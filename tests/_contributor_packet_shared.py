"""Shared fixture of the contributor packet golden
(``tests/fixtures/contributor_review_packet_golden.json``).

One installed body (a git checkout detached at the base commit) and one committed
proposal that rewrites the review flow, the wrapper and the checklist; the seats the
review returns are persisted as real observability receipts, retained answers and
settled session custody, so the wrapper's packet is built from production readers.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-02T03:04:05+00:00", "GIT_COMMITTER_DATE": "2026-01-02T03:04:05+00:00",
}
BASE_FILES = {
    "VERSION": "1.2.3\n",
    "README.md": "# Fixture project\n\nInstalled body.\n",
    "build.sh": "#!/bin/sh\necho build\n",
    "docs/CHECKLISTS.md": "# Checklist\n\n- installed rule\n",
    "ouroboros/config.py": "FIXTURE = 'installed'\n",
    "ouroboros/tools/review.py": "RULES = 'installed review flow'\n",
    "scripts/run_external_review.py": "# installed wrapper\n",
}
PROPOSAL_FILES = {
    "README.md": "# Fixture project\n\nInstalled body.\n\nProposed paragraph.\n",
    "build.sh": "#!/bin/sh\necho build --fast\n",
    "docs/CHECKLISTS.md": "# Checklist\n\n- proposal relaxes every rule\n",
    "docs/new_note.md": "A new note.\n",
    "ouroboros/tools/review.py": "RULES = 'proposal rewrites the review flow'\n",
    "scripts/run_external_review.py": "# proposal wrapper\n",
}
PROPOSAL_TITLE = "Proposal: faster build"

GOLDEN_CONFIG = {
    "profile": "external_pr_readiness",
    "provider": "configured_per_slot",
    "slot_config_source": "settings",
    "triad_slots": [
        {"slot_id": "t1", "route": {"kind": "api_chat", "target_id": "openai/gpt-5.6-sol"}, "effort": "high"},
        {"slot_id": "t2", "route": {"kind": "agent_session", "target_id": "codex=gpt-5.6-sol",
                                    "profile_id": "pinned"}, "effort": "high"},
    ],
    "scope_slots": [
        {"slot_id": "s1", "route": {"kind": "api_chat", "target_id": "openai/gpt-5.6-sol"}, "effort": "xhigh"},
    ],
    "triad_models": ["openai/gpt-5.6-sol", "codex=gpt-5.6-sol"],
    "triad_efforts": ["high", "high"],
    "scope_models": ["openai/gpt-5.6-sol"],
    "scope_efforts": ["xhigh"],
    "review_enforcement": "blocking",
    "context_mode": "max",
    "runtime_mode": "pro",
}
GOLDEN_STRUCTURED = {
    "triad_rows": [
        {"slot_id": "t1", "model": "openai/gpt-5.6-sol", "route": "api_chat", "effort": "high"},
        {"slot_id": "t2", "model": "codex=gpt-5.6-sol", "route": "agent_session", "effort": "high",
         "session_target": "codex=gpt-5.6-sol", "session_profile": "pinned"},
    ],
    "scope_rows": [{"slot_id": "s1", "model": "openai/gpt-5.6-sol", "route": "api_chat", "effort": "xhigh"}],
    "triad_quorum": 2,
    "scope_quorum": 1,
    "triad_prompt": "Triad brief of the frozen base..head subject.",
    "scope_brief": "Scope brief of the frozen base..head subject.",
}
ANSWERS = {
    "t1": json.dumps([{"item": "code_quality", "verdict": "PASS", "severity": "advisory",
                       "reason": "t1 read build.sh"}]),
    "t2": json.dumps([{"item": "tests_affected", "verdict": "PASS", "severity": "advisory",
                       "reason": "t2 session read the tests"}]),
    "s1": json.dumps([{"item": "intent_alignment", "verdict": "PASS", "severity": "advisory",
                       "reason": "s1 scope matches the title"}]),
}
SESSION_TRANSCRIPT = "full session transcript of t2\nEOF_SENTINEL"
SESSION_RUN_ID = "run-golden"


def git(repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args],
        cwd=str(repo), capture_output=True, text=True, check=True, env={**os.environ, **GIT_ENV},
    )
    return result.stdout.strip()


def _write(repo: pathlib.Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def init_installed_body(root: pathlib.Path) -> dict[str, str]:
    """The installed checkout, detached at the base, with the proposal on branch ``proposal``."""
    repo = (root / "installed").resolve()
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    _write(repo, BASE_FILES)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "installed body")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "branch", "base", base)
    git(repo, "checkout", "-q", "-b", "proposal")
    _write(repo, PROPOSAL_FILES)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "proposal")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "--detach", base)
    patch = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", f"{base}..{head}"],
        cwd=str(repo), capture_output=True, check=True,
    ).stdout
    return {
        "repo": str(repo), "base_sha": base, "head_sha": head,
        "head_tree_sha": git(repo, "rev-parse", f"{head}^{{tree}}"),
        "diff_sha256": hashlib.sha256(patch).hexdigest(),
    }


def isolation_record(drive: pathlib.Path) -> dict:
    return {"review_data_root": str(drive), "run_cap_usd": 5.0, "attach_host_engine": True}


def persist_golden_actors(drive: pathlib.Path) -> tuple[list[dict], dict]:
    """The seats' raw actor records, with their receipts persisted under ``drive``."""
    from ouroboros import delegate_custody as custody
    from ouroboros.observability import persist_call

    def prompt(call_id: str, slot: dict) -> dict:
        return persist_call(drive, task_id="review", call_id=call_id, call_type="prompt",
                            payload={"request": {"surface": "review"}, "slot": slot})

    def response(call_id: str, usage: dict, *, answer: str, transcript: str = "") -> dict:
        message = {"content": answer}
        if transcript:
            message["session_transcript"] = transcript
            usage = {**usage, "verdict_provenance": {
                "raw_transcript_chars": len(transcript),
                "raw_transcript_sha256": hashlib.sha256(transcript.encode("utf-8", "replace")).hexdigest(),
            }}
        return persist_call(drive, task_id="review", call_id=call_id, call_type="response",
                            payload={"message": message, "usage": usage})

    api_usage = {"provider": "openrouter", "resolved_model": "openai/gpt-5.6-sol"}
    t1 = {
        "slot_id": "t1", "model_id": "openai/gpt-5.6-sol", "status": "responded",
        "tokens_in": 1200, "tokens_out": 300, "cost_usd": 0.0125, "raw_text": ANSWERS["t1"],
        "prompt_ref": prompt("t1_prompt", {"slot_id": "t1", "model": "openai/gpt-5.6-sol", "effort": "high",
                                           "route": "api_chat", "session_target": "", "session_profile": ""}),
        "response_ref": response("t1_response", api_usage, answer=ANSWERS["t1"]),
    }
    t2 = {
        "slot_id": "t2", "model_id": "codex=gpt-5.6-sol", "status": "responded",
        "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "raw_text": ANSWERS["t2"],
        "prompt_ref": prompt("t2_prompt", {"slot_id": "t2", "model": "codex=gpt-5.6-sol", "effort": "high",
                                           "route": "agent_session", "session_target": "codex=gpt-5.6-sol",
                                           "session_profile": "pinned"}),
        "response_ref": response("t2_response", {
            "provider": "claudexor", "delegated_route": "codex", "resolved_model": "gpt-5.6-sol",
            "applied_profile": "pinned", "applied_access": "readonly", "delegated_run_id": SESSION_RUN_ID,
            "custody_durable": True, "output_conformance": "passed", "verdict_method": "schema",
        }, answer=ANSWERS["t2"], transcript=SESSION_TRANSCRIPT),
    }
    s1 = {
        "slot_id": "s1", "model_id": "openai/gpt-5.6-sol", "status": "responded",
        "tokens_in": 3000, "tokens_out": 500, "cost_usd": 0.02, "raw_text": ANSWERS["s1"],
        "prompt_ref": prompt("s1_prompt", {"slot_id": "s1", "model": "openai/gpt-5.6-sol", "effort": "xhigh",
                                           "route": "api_chat", "session_target": "", "session_profile": ""}),
        "response_ref": response("s1_response", api_usage, answer=ANSWERS["s1"]),
    }
    custody.record_started(drive, custody.RunCustody(
        run_id=SESSION_RUN_ID, task_id="review", project_id="review-project",
        project_owned=True, ledger_root=str(drive),
    ))
    for event in (custody.LEDGER_RECORDED, custody.SETTLED, custody.PROJECT_RETIRED):
        custody.emit(drive, event, {"run_id": SESSION_RUN_ID})
    custody._CUSTODY.clear()
    return [t1, t2], {"status": "responded", "model_id": "openai/gpt-5.6-sol", "raw_results": [s1]}


def golden_review_change(calls: list[dict]):
    """A ``run_review_change`` stand-in: one wave of the golden seats, written as a
    real ledger record for the frozen subject, appended to ``calls``."""

    def run_review_change(ctx, **arguments) -> dict:
        from dataclasses import replace

        from ouroboros import review_ledger

        drive = review_ledger.ledger_root(ctx)
        repo = pathlib.Path(ctx.repo_dir)
        triad_raw, scope_raw = persist_golden_actors(drive)
        record = review_ledger.build_commit_gate_record({
            "repo_dir": str(repo), "goal": arguments.get("goal"), "scope": arguments.get("scope"),
            "structured": GOLDEN_STRUCTURED, "triad_raw": triad_raw, "scope_raw": scope_raw,
        }, drive_root=drive)
        base, head = str(arguments.get("base") or ""), str(arguments.get("head") or "")
        patch = subprocess.run(["git", "diff", "--binary", base, head], cwd=str(repo),
                               capture_output=True, check=True).stdout if head else b""
        subject = {"root_kind": "system_repo", "root": str(repo), "kind": arguments.get("subject"),
                   "base": base, "head": head,
                   "tree_sha": git(repo, "rev-parse", f"{head}^{{tree}}") if head else "",
                   "diff_sha": hashlib.sha256(patch).hexdigest(), "checkout": ""}
        payload = review_ledger.write_record(drive, replace(record, surface="change", subject=subject))
        calls.append({"arguments": dict(arguments), "repo_dir": str(repo), "drive": str(drive),
                      "pid": os.getpid(), "environ": dict(os.environ), "record_id": payload["record_id"]})
        return {"record_id": payload["record_id"], "aggregate": payload["verdict"]["aggregate"],
                "per_question": payload["verdict"]["per_question"], "panel": payload["panel"],
                "rows": payload["rows"], "subject": payload["subject"],
                "checklist": {"layer": "body", "body_fact": "true", "how": "dir"},
                "tests": payload["tests"], "cost": payload["cost"], "reused": False}

    return run_review_change


def full_output_sections(text: str) -> dict[str, str]:
    """``full-output.txt`` split into ``{title: body}`` at its ``=`` separator lines."""
    lines, sections, index = text.splitlines(), {}, 0
    separator = "=" * 80
    while index < len(lines):
        if lines[index] == separator and index + 2 < len(lines) and lines[index + 2] == separator:
            title, index, body = lines[index + 1], index + 3, []
            while index < len(lines) and lines[index] != separator:
                body.append(lines[index])
                index += 1
            sections[title] = "\n".join(body)
        else:
            index += 1
    return sections


def _mask_volatile(value):
    if isinstance(value, dict):
        masked = {key: _mask_volatile(item) for key, item in value.items()}
        manifest = masked.get("manifest_ref")
        if isinstance(manifest, dict) and "sha256" in manifest:
            masked["manifest_ref"] = {**manifest, "sha256": "<manifest_sha256>"}
        if "compressed_size" in masked:  # the gzip header names the temporary file
            masked["compressed_size"] = "<compressed_size>"
        return masked
    if isinstance(value, list):
        return [_mask_volatile(item) for item in value]
    return value


def normalized(value, fixture: dict[str, str]):
    """Packet JSON with the fixture's commit/tree/diff identities named and the
    write-time fields (timestamps, manifest digests) masked."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    for key in ("base_sha", "head_sha", "head_tree_sha", "diff_sha256"):
        text = text.replace(fixture[key], f"<{key}>")
    data = _mask_volatile(json.loads(text))
    if isinstance(data, dict):
        for key in ("reviewed_at", "elapsed_sec"):
            if key in data:
                data[key] = f"<{key}>"
    return data
