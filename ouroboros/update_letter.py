"""Update letter: Ouroboros's own short note about what a pending official update brings.

The letter is written by the LIGHT model slot with the ordinary task context (identity,
memory, recent dialogue, governance — ``context.build_llm_messages`` with a synthetic task
routed at the light model), fed the material of the update range: every first-parent commit
between the running base and the official target, plus the README "Version History" rows
those commits ADDED. Rows are recovered from the commit diffs rather than read from any
README snapshot, because the history table is capped (rows roll off inside one range) and
untagged releases have no tag to look up.

Floor (host code): exact versions/SHAs, the material, one paragraph, the truth rule ("only
what the material says"), physical-attempt accounting, no silent model substitution, and a
letter that is never deleted after the update lands. Ceiling (the mind): language,
register, what matters to this human, and whether to mention it at all.

Storage is one file, ``data/state/update_letter.json``, with one writer —
``refresh_after_check``, which runs synchronously inside a FETCHING update check (boot and
the Updates panel's "Check for updates" button) — and one reader shape, ``project_letter``,
shared by the Updates panel payload and the agent's Runtime context
(``official_update_projection``). The projection compares recorded SHAs with the live HEAD by
equality only (no git on the hot context path); a HEAD that descends from the target reads
as ``other``, a disclosed residual the panel, which has git, does not share.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from ouroboros.utils import atomic_write_json, read_json_dict, truncate_within_limit, utc_now_iso

log = logging.getLogger(__name__)

RECORD_REL = pathlib.Path("state") / "update_letter.json"
SYSTEM_TASK_ID = "system:update_letter"
USAGE_CATEGORY = "update_letter"
# Output budget of the LIGHT one-shot (the letter is one short paragraph; docs/ARCHITECTURE.md §7).
UPDATE_LETTER_MAX_TOKENS = 1024
# Per-commit body bound inside the material (a disclosed cut, never a silent slice).
COMMIT_BODY_MAX_CHARS = 1200
DEFAULT_MAX_COMMITS = 200

ERROR_KINDS = (
    "no_credentials", "budget_exhausted", "context_overflow", "timeout",
    "provider_unavailable", "empty_response",
)

_ROW_RE = re.compile(r"^\+\|\s*(\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?)\s*\|")
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_REFRESH_LOCK = threading.Lock()

GitCapture = Callable[[List[str]], Tuple[int, str, str]]


# ---------------------------------------------------------------------------
# Material: the update range as evidence
# ---------------------------------------------------------------------------

def _default_git() -> GitCapture:
    from supervisor.git_ops import git_capture

    return git_capture


def _split_row(line: str) -> Optional[Tuple[str, str, str]]:
    """Split one added history row into (version, date, text) on UNESCAPED pipes.

    Rows carry escaped pipes in prose (``incomplete\\|unknown``), so a raw split
    would shred them. Returns ``None`` when the row does not have exactly three
    cells — the caller counts that as a disclosed omission, never a silent skip.
    """
    cells = [cell.strip() for cell in _UNESCAPED_PIPE.split(line.lstrip("+").strip())]
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    if len(cells) != 3:
        return None
    return cells[0], cells[1], cells[2].replace("\\|", "|")


def collect_range_material(
    base_sha: str,
    target_sha: str,
    *,
    git: Optional[GitCapture] = None,
    max_commits: int = DEFAULT_MAX_COMMITS,
) -> Dict[str, Any]:
    """Collect the first-parent commits and the README history rows they added.

    ``commits`` are newest-first and capped at ``max_commits`` (``omitted_commits``
    discloses the rest); ``releases`` are every row added anywhere in the range,
    newest-first with first-wins per version; malformed rows are counted in
    ``omitted_rows``. An empty range yields empty lists.
    """
    capture = git or _default_git()
    spec = f"{base_sha}..{target_sha}"
    material: Dict[str, Any] = {
        "base_sha": base_sha, "target_sha": target_sha,
        "commits": [], "omitted_commits": 0, "releases": [], "omitted_rows": 0,
        "stats": {}, "tags": [],
    }
    rc, out, _err = capture([
        "git", "log", "--first-parent", "--format=%H%x1f%aI%x1f%s%x1f%b%x1e", spec,
    ])
    all_shas: List[str] = []
    commits: List[Dict[str, Any]] = []
    if rc == 0 and out.strip():
        for chunk in out.split("\x1e"):
            chunk = chunk.strip("\n")
            if not chunk.strip():
                continue
            parts = chunk.split("\x1f", 3)
            if len(parts) < 3:
                continue
            sha = parts[0].strip()
            body = parts[3].strip() if len(parts) > 3 else ""
            all_shas.append(sha)
            commits.append({
                "sha": sha, "date": parts[1].strip(), "subject": parts[2].strip(),
                "body": truncate_within_limit(body, COMMIT_BODY_MAX_CHARS) if body else "",
            })
    material["omitted_commits"] = max(0, len(commits) - max_commits)
    material["commits"] = commits[:max_commits]

    rc, out, _err = capture([
        "git", "log", "-m", "--first-parent", "-p", "-U0", "--format=%x01%H", spec, "--", "README.md",
    ])
    seen_versions: set = set()
    releases: List[Dict[str, Any]] = []
    if rc == 0 and out:
        current_sha = ""
        for line in out.splitlines():
            if line.startswith("\x01"):
                current_sha = line[1:].strip()
                continue
            if not line.startswith("+|"):
                continue
            match = _ROW_RE.match(line)
            if not match:
                continue
            cells = _split_row(line)
            if cells is None:
                material["omitted_rows"] += 1
                continue
            version, date, text = cells
            if version in seen_versions:
                continue
            seen_versions.add(version)
            releases.append({"version": version, "date": date, "text": text, "commit": current_sha})
    material["releases"] = releases

    stats: Dict[str, Any] = {"first_parent_count": len(all_shas)}
    rc, out, _err = capture(["git", "rev-list", "--left-right", "--count", f"{base_sha}...{target_sha}"])
    if rc == 0:
        try:
            ahead, behind = (int(part) for part in out.split())
            stats["ahead"], stats["behind"] = ahead, behind
        except ValueError:
            pass
    rc, _out, _err = capture(["git", "merge-base", "--is-ancestor", base_sha, target_sha])
    stats["base_is_ancestor"] = rc == 0
    for label, sha in (("base_version", base_sha), ("target_version", target_sha)):
        rc, out, _err = capture(["git", "show", f"{sha}:VERSION"])
        stats[label] = out.strip() if rc == 0 else ""
    material["stats"] = stats

    # for-each-ref has no %xNN escapes; tag names carry no whitespace, so split on it.
    rc, out, _err = capture([
        "git", "for-each-ref", "--format=%(refname:short) %(objectname) %(*objectname)",
        "refs/tags/v*", "refs/ouroboros-managed/tags/v*",
    ])
    if rc == 0 and out.strip():
        in_range = set(all_shas)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            peeled = parts[2].strip() if len(parts) > 2 else parts[1].strip()
            if peeled in in_range:
                material["tags"].append({"tag": parts[0].strip().split("/")[-1], "sha": peeled})
    return material


def material_text(material: Dict[str, Any]) -> str:
    """Render the material as compact evidence text for the prompt (releases first)."""
    lines: List[str] = []
    releases = material.get("releases") or []
    if releases:
        lines.append("Release notes added in this range (newest first):")
        for row in releases:
            lines.append(f"- {row.get('version')} ({row.get('date')}): {row.get('text')}")
        if material.get("omitted_rows"):
            lines.append(f"- [{material['omitted_rows']} malformed history row(s) omitted]")
    commits = material.get("commits") or []
    if commits:
        lines.append("")
        lines.append("First-parent commits in this range (newest first):")
        for commit in commits:
            head = f"- {str(commit.get('sha') or '')[:8]} {commit.get('date', '')[:10]} {commit.get('subject', '')}"
            lines.append(head)
            body = str(commit.get("body") or "").strip()
            if body:
                lines.append("  " + body.replace("\n", "\n  "))
        if material.get("omitted_commits"):
            lines.append(f"- [{material['omitted_commits']} older commit(s) omitted]")
    if not lines:
        lines.append("(no commits in this range)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generation: one LIGHT call with the ordinary context
# ---------------------------------------------------------------------------

def _request_text(status: Dict[str, Any], material: Dict[str, Any], target_version: str) -> str:
    import json

    from ouroboros import get_version

    facts = {
        "running": {"version": get_version(), "sha": str(status.get("current_sha") or "")},
        "official_target": {"version": target_version, "sha": str(status.get("latest_sha") or "")},
        "update_channel": str(status.get("update_channel") or ""),
        "commits_behind": status.get("behind"),
        "commits_ahead": status.get("ahead"),
        "checked_at": str(status.get("checked_at") or ""),
    }
    return (
        "[UPDATE LETTER REQUEST]\n"
        "An official update of my body is available. Facts (host-attested):\n"
        + json.dumps(facts, ensure_ascii=False, indent=2)
        + "\n\nMaterial — what changed between the running version and the target:\n"
        + material_text(material)
        + "\n\nWrite my human ONE short paragraph — no headings, no lists, no more than about "
        "120 words — about what this update brings, as myself and in the language my human and I "
        "use together. Use only what the material says; "
        "if the material does not say something, I do not invent it. This paragraph is shown on "
        "the Updates page and becomes part of my own context; the commit history stays readable "
        "for detail. Reply with the paragraph only.\n"
        "[/UPDATE LETTER REQUEST]"
    )


def _context_messages(env: Any, memory: Any, task: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The ordinary task context, projected for the synthetic light-slot task."""
    from ouroboros.context import build_llm_messages

    messages, _cap = build_llm_messages(env, memory, task)
    return messages


def _chat(client: Any, *, drive_root: pathlib.Path, **kwargs: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    from ouroboros.llm_observability import chat_observed

    return chat_observed(client, drive_root=drive_root, task_id=SYSTEM_TASK_ID, call_type=USAGE_CATEGORY, **kwargs)


def _classify(exc: Exception) -> Tuple[str, str]:
    from ouroboros.usage_accounting import BudgetExceeded
    from ouroboros.utils import sanitize_tool_result_for_log

    safe = sanitize_tool_result_for_log(f"{type(exc).__name__}: {exc}")[:300]
    if isinstance(exc, BudgetExceeded):
        return "budget_exhausted", safe
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return "timeout", safe
    try:
        from ouroboros.loop_llm_call import classify_llm_exception

        kind = str(classify_llm_exception(exc, safe).kind or "")
    except Exception:
        kind = ""
    if kind == "context_overflow":
        return "context_overflow", safe
    return "provider_unavailable", safe


def write_letter(
    status: Dict[str, Any],
    material: Dict[str, Any],
    *,
    drive_root: Optional[pathlib.Path] = None,
    repo_dir: Optional[pathlib.Path] = None,
    llm_client: Any = None,
) -> Dict[str, Any]:
    """One accounted LIGHT-slot call; returns the letter record (``ready`` or ``failed``)."""
    from ouroboros import get_version
    from ouroboros.config import DATA_DIR, REPO_DIR, get_light_model, get_update_letter_timeout_sec

    data_root = pathlib.Path(drive_root or DATA_DIR)
    repo_root = pathlib.Path(repo_dir or REPO_DIR)
    key = _key_from_status(status)
    target_version = str(material.get("stats", {}).get("target_version") or "")
    record: Dict[str, Any] = {
        "schema": 1,
        "key": key,
        "checked_head_sha": key["base_sha"],
        "state": "failed",
        "text": "",
        "author_version": get_version(),
        "target_version": target_version,
        "model": "",
        "written_at": utc_now_iso(),
        "attempt_id": "",
        "error_kind": "",
        "error_text": "",
        "last_good": None,
    }
    model = get_light_model()
    use_local = str(os.environ.get("USE_LOCAL_LIGHT", "") or "").lower() in ("true", "1")
    record["model"] = model
    if not use_local:
        from ouroboros.provider_models import model_has_credentials

        if not model_has_credentials(model):
            record.update(error_kind="no_credentials",
                          error_text=f"no credentials for the light model slot ({model})")
            return record
    try:
        from ouroboros import model_concurrency
        from ouroboros.agent import Env
        from ouroboros.llm import LLMClient
        from ouroboros.memory import Memory
        from ouroboros.usage_accounting import UsageScope, usage_scope

        env = Env(repo_dir=repo_root, drive_root=data_root)
        memory = Memory(drive_root=data_root, repo_dir=repo_root)
        task = {
            "id": SYSTEM_TASK_ID, "type": "update_letter", "source": USAGE_CATEGORY,
            "model": model, "use_local_model": use_local, "metadata": {},
            "text": _request_text(status, material, target_version),
        }
        messages = _context_messages(env, memory, task)
        try:
            global_limit = float(os.environ.get("TOTAL_BUDGET", "0") or 0)
        except (TypeError, ValueError):
            global_limit = 0.0
        scope = UsageScope(
            drive_root=data_root, task_id=SYSTEM_TASK_ID, root_task_id=SYSTEM_TASK_ID,
            category=USAGE_CATEGORY, source=USAGE_CATEGORY,
            global_limit_usd=global_limit if global_limit > 0 else None,
        )
        client = llm_client or LLMClient()
        with model_concurrency.model_call_slot(model, use_local):
            with usage_scope(scope):
                msg, usage = _chat(
                    client, drive_root=data_root, messages=messages, model=model, tools=None,
                    reasoning_effort="low", max_tokens=UPDATE_LETTER_MAX_TOKENS,
                    use_local=use_local, timeout=get_update_letter_timeout_sec(),
                )
        attempt_ids = list((usage or {}).get("ledger_attempt_ids") or [])
        record["attempt_id"] = str(attempt_ids[-1]) if attempt_ids else ""
        text = str((msg or {}).get("content") or "").strip()
        if not text:
            record.update(error_kind="empty_response", error_text="the model returned no text")
            return record
        record.update(state="ready", text=text, written_at=utc_now_iso())
        return record
    except Exception as exc:  # noqa: BLE001 — every failure becomes a typed, secret-free record
        kind, safe = _classify(exc)
        log.debug("update letter generation failed (%s)", kind, exc_info=True)
        record.update(error_kind=kind, error_text=safe)
        return record


# ---------------------------------------------------------------------------
# Storage and the one refresh seam
# ---------------------------------------------------------------------------

def record_path(drive_root: Optional[pathlib.Path] = None) -> pathlib.Path:
    from ouroboros.config import DATA_DIR

    return pathlib.Path(drive_root or DATA_DIR) / RECORD_REL


def read_record(drive_root: Optional[pathlib.Path] = None) -> Optional[Dict[str, Any]]:
    record = read_json_dict(record_path(drive_root))
    return record if record and isinstance(record.get("key"), dict) else None


def _key_from_status(status: Dict[str, Any]) -> Dict[str, str]:
    return {
        "base_sha": str(status.get("current_sha") or ""),
        "target_sha": str(status.get("latest_sha") or ""),
        "update_channel": str(status.get("update_channel") or ""),
        "target_ref": str(status.get("target_ref") or ""),
    }


def refresh_after_check(
    status: Dict[str, Any],
    *,
    drive_root: Optional[pathlib.Path] = None,
    repo_dir: Optional[pathlib.Path] = None,
    llm_client: Any = None,
) -> Optional[Dict[str, Any]]:
    """Write the letter after a successful FETCHING check; never raise, never delete.

    ``check_ok`` other than True or no available update leaves the stored record as it
    is (an applied update keeps its letter — that is the "what changed in this version"
    text). A second concurrent refresh is a no-op that returns the current record.
    """
    try:
        current = read_record(drive_root)
        if status.get("check_ok") is not True or not status.get("available"):
            return current
        key = _key_from_status(status)
        if not key["base_sha"] or not key["target_sha"]:
            return current
        if not _REFRESH_LOCK.acquire(blocking=False):
            return current
        try:
            material = collect_range_material(key["base_sha"], key["target_sha"])
            if not material.get("commits") and not material.get("releases"):
                return current
            record = write_letter(status, material, drive_root=drive_root, repo_dir=repo_dir,
                                  llm_client=llm_client)
            if record.get("state") != "ready" and current and current.get("key") == key:
                previous_good = current if current.get("state") == "ready" else current.get("last_good")
                record["last_good"] = previous_good or None
            atomic_write_json(record_path(drive_root), record)
            return record
        finally:
            _REFRESH_LOCK.release()
    except Exception:
        log.debug("update letter refresh failed", exc_info=True)
        return read_record(drive_root)


# ---------------------------------------------------------------------------
# The one projection shared by the panel payload and the Runtime context
# ---------------------------------------------------------------------------

def project_letter(
    record: Optional[Dict[str, Any]],
    *,
    head_sha: str,
    latest_sha: str,
) -> Optional[Dict[str, Any]]:
    """Relate the stored letter to the live HEAD and official target by SHA equality."""
    if not record:
        return None
    key = record.get("key") if isinstance(record.get("key"), dict) else {}
    base, target = str(key.get("base_sha") or ""), str(key.get("target_sha") or "")
    head, latest = str(head_sha or ""), str(latest_sha or "")
    if head and head == target:
        relation = "applied"
    elif head and head == base and latest == target:
        relation = "pending"
    elif head and head == base:
        relation = "superseded"
    else:
        relation = "other"
    text = str(record.get("text") or "")
    last_good = record.get("last_good") if isinstance(record.get("last_good"), dict) else None
    if record.get("state") != "ready" and not text and last_good:
        text = str(last_good.get("text") or "")
    return {
        "state": "ready" if record.get("state") == "ready" else "failed",
        "relation": relation,
        "text": text,
        "author_version": str(record.get("author_version") or ""),
        "target_version": str(record.get("target_version") or ""),
        "written_at": str(record.get("written_at") or ""),
        "error_kind": str(record.get("error_kind") or ""),
        "error_text": str(record.get("error_text") or ""),
        "key": dict(key),
        "has_last_good": bool(last_good and last_good.get("text")),
    }


def official_update_projection(
    head_sha: str,
    *,
    drive_root: Optional[pathlib.Path] = None,
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The typed fact for the Runtime context: O(1), no git, never raises.

    ``status`` is honest about freshness: ``up_to_date`` only when HEAD equals the last
    checked official target, ``update_available`` only when the letter was written for
    this exact HEAD, ``moved_since_check`` when HEAD matches neither, ``unchecked`` when
    no fetching check has completed. ``status_as_of`` names the check the fact comes from.
    """
    try:
        from ouroboros import get_version

        head = str(head_sha or "")
        if state is None:
            from supervisor.state import load_state

            state = load_state() or {}
        cache = state.get("managed_update_cache") if isinstance(state, dict) else None
        cache = cache if isinstance(cache, dict) else {}
        record = read_record(drive_root)
        latest = str(cache.get("latest_sha") or "")
        letter = project_letter(record, head_sha=head, latest_sha=latest)
        checked_head = str((record or {}).get("checked_head_sha") or "")
        if not cache:
            status = "unchecked"
        elif head and head == latest:
            status = "up_to_date"
        elif cache.get("available") and checked_head and checked_head == head:
            status = "update_available"
        else:
            status = "moved_since_check"
        target = None
        if latest:
            target = {
                "version": str((record or {}).get("target_version") or ""),
                "sha": latest,
            }
        return {
            "status": status,
            "status_as_of": str(cache.get("checked_at") or ""),
            "running": {"version": get_version(), "sha": head},
            "update_channel": str(cache.get("update_channel") or ""),
            "target": target,
            "behind": cache.get("behind"),
            "ahead": cache.get("ahead"),
            "letter": letter,
        }
    except Exception as exc:  # noqa: BLE001 — a fact that raises would take the whole context with it
        log.debug("official_update projection failed", exc_info=True)
        return {"status": "unknown", "error": type(exc).__name__}
