"""Update letter: Ouroboros's own short note about what a pending official update brings.

The letter is written by the LIGHT model slot with the ordinary task context (identity,
memory, recent dialogue, governance — ``context.build_llm_messages`` with a synthetic task
routed at the light model), fed the material of the update range: every first-parent commit
between the running base and the official target, plus the README "Version History" rows
those commits ADDED. Rows are recovered from the commit diffs rather than read from any
README snapshot, because the history table is capped (rows roll off inside one range) and
untagged releases have no tag to look up.

Floor (host code): exact versions and SHAs, the material and its disclosed bounds, an output
budget, physical-attempt accounting, no silent model substitution, and a letter that is never
deleted after the update lands. Ceiling (the mind): the SHAPE of the paragraph as well as its
language, register, and what matters to this human — the request asks for one short paragraph
that only says what the material says, and the host does not police the answer. Deliberate
(reviewed three times): a host shape gate would either edit a cognitive artifact (BIBLE P1) or
throw away a useful letter over its formatting, and ``UPDATE_LETTER_MAX_TOKENS`` already bounds
the size. The panel renders whatever comes back through the same sanitizing markdown pipeline
the chat uses.

Storage is one file, ``data/state/update_letter.json``, with one writer —
``refresh_after_check``, which runs synchronously inside a FETCHING update check (boot and
the Updates panel's "Check for updates" button) — and one reader shape, ``project_letter``,
shared by the Updates panel payload and the agent's Runtime context
(``official_update_projection``). The projection compares recorded SHAs with the live HEAD by
equality plus the check's own zero-behind fact (no git on the hot context path): a divergent
install consumes an official target through a merge commit (``supervisor/update_merge.py``), so
``applied`` cannot mean HEAD == target; a HEAD that merely moved elsewhere reads as ``other``.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ouroboros.utils import atomic_write_json, read_json_dict, truncate_within_limit, utc_now_iso

log = logging.getLogger(__name__)

RECORD_REL = pathlib.Path("state") / "update_letter.json"
SYSTEM_TASK_ID = "system:update_letter"
USAGE_CATEGORY = "update_letter"
# Output budget of the LIGHT one-shot (the letter is one short paragraph; docs/ARCHITECTURE.md §7).
UPDATE_LETTER_MAX_TOKENS = 1024
# Per-commit body bound inside the material (a disclosed cut, never a silent slice).
COMMIT_BODY_MAX_CHARS = 10000
# EVERY commit subject in the range reaches the author; only the bodies and the older
# release-row texts are bounded, because a long range must not become an invisible one.
DEFAULT_MAX_BODIES = 200
DEFAULT_MAX_ROWS = 60

_ROW_RE = re.compile(r"^\+\|\s*(\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?)\s*\|")
# The table's own furniture: the EXACT canonical header and the dashed separator under it
# carry no release and are the only added rows that may be skipped in silence. The match is
# whole-row on purpose — a row merely STARTING with "version" is a row whose content the
# author would otherwise never see, so it is counted and disclosed like any other.
_ROW_FURNITURE_RE = re.compile(
    r"^\+\|\s*version\s*\|\s*date\s*\|\s*description\s*\|\s*$"
    r"|^\+\|(?:\s*:?-{2,}:?\s*\|)+\s*$",
    re.IGNORECASE,
)
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_REFRESH_LOCK = threading.Lock()
# Bumped by every letter write under the lock, so a refresh that waited for the lock can
# tell "the writer just produced my key" (share it) from "an older record" (write anew).
_WRITE_SEQ = 0
_LAST_WRITTEN: Tuple[Dict[str, str], Optional[Dict[str, Any]]] = ({}, None)

GitCapture = Callable[[List[str]], Tuple[int, str, str]]


class MaterialUnavailable(RuntimeError):
    """The range could not be read at all (git failed), which is NOT an empty range.

    Collapsing the two would record "this update has nothing to say" over a repository
    the host could not read — a silent, durable falsehood in the letter's own state file.
    """


# ---------------------------------------------------------------------------
# Material: the update range as evidence
# ---------------------------------------------------------------------------

def _default_git() -> GitCapture:
    """Local git under the managed-update ceiling (config SSOT); never an unbounded wait
    while the single-flight lock is held."""
    from ouroboros.update_channels import get_managed_update_fetch_timeout_sec
    from supervisor.git_ops import git_capture

    limit = float(get_managed_update_fetch_timeout_sec())
    return lambda cmd: git_capture(cmd, timeout=limit)


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
    max_bodies: int = DEFAULT_MAX_BODIES,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> Dict[str, Any]:
    """Collect the first-parent commits and the README history rows they added.

    ``commits`` are EVERY first-parent commit of the range, newest-first: the subject of
    each one always reaches the author, so a long range is never an invisible one. Only
    the bodies are bounded — the newest ``max_bodies`` keep theirs, ``bodies_omitted``
    discloses the rest. ``releases`` are every row added anywhere in the range,
    newest-first with first-wins per version; the newest ``max_rows`` keep their text and
    older rows stay as version and date (``rows_summarized``), malformed ones are counted
    in ``omitted_rows``. ``versions`` are the VERSION files at both ends. Divergence
    counts stay on the status dict that triggered the letter — nothing is collected that
    neither the model nor the record reads. An empty range yields empty lists.
    """
    capture = git or _default_git()
    spec = f"{base_sha}..{target_sha}"
    material: Dict[str, Any] = {
        "base_sha": base_sha, "target_sha": target_sha,
        "commits": [], "bodies_omitted": 0, "releases": [], "omitted_rows": 0,
        "rows_summarized": 0, "versions": {},
    }
    rc, out, err = capture([
        "git", "log", "--first-parent", "--format=%H%x1f%aI%x1f%s%x1f%b%x1e", spec,
    ])
    if rc != 0:
        raise MaterialUnavailable(f"git log --first-parent {spec} failed (rc={rc}): {str(err)[:200]}")
    commits: List[Dict[str, Any]] = []
    if out.strip():
        for chunk in out.split("\x1e"):
            chunk = chunk.strip("\n")
            if not chunk.strip():
                continue
            parts = chunk.split("\x1f", 3)
            if len(parts) < 3:
                continue
            body = parts[3].strip() if len(parts) > 3 else ""
            keeps_body = len(commits) < max_bodies
            commits.append({
                "sha": parts[0].strip(), "date": parts[1].strip(), "subject": parts[2].strip(),
                "body": truncate_within_limit(body, COMMIT_BODY_MAX_CHARS) if (body and keeps_body) else "",
            })
            if body and not keeps_body:
                material["bodies_omitted"] += 1
    material["commits"] = commits

    rc, out, err = capture([
        "git", "log", "-m", "--first-parent", "-p", "-U0", "--format=%x01%H", spec, "--", "README.md",
    ])
    if rc != 0:
        raise MaterialUnavailable(f"git log -p README.md {spec} failed (rc={rc}): {str(err)[:200]}")
    seen_versions: set = set()
    releases: List[Dict[str, Any]] = []
    if out:
        current_sha = ""
        for line in out.splitlines():
            if line.startswith("\x01"):
                current_sha = line[1:].strip()
                continue
            if not line.startswith("+|") or _ROW_FURNITURE_RE.match(line):
                continue
            cells = _split_row(line)
            if cells is None or not _ROW_RE.match(line):
                # Wrong cell count, or a first cell that is not a version: either way this
                # row said something the letter's author would otherwise never learn.
                material["omitted_rows"] += 1
                continue
            version, date, text = cells
            if version in seen_versions:
                continue
            seen_versions.add(version)
            releases.append({"version": version, "date": date, "text": text, "commit": current_sha})
    for row in releases[max_rows:]:
        row["text"] = ""
        material["rows_summarized"] += 1
    material["releases"] = releases
    for label, sha in (("base", base_sha), ("target", target_sha)):
        material["versions"][label] = _version_at(capture, sha)
    return material


def _version_at(capture: GitCapture, sha: str) -> str:
    rc, out, _err = capture(["git", "show", f"{sha}:VERSION"])
    return out.strip() if rc == 0 else ""


def material_text(material: Dict[str, Any]) -> str:
    """Render the material as compact evidence text for the prompt (releases first)."""
    lines: List[str] = []
    releases = material.get("releases") or []
    # The disclosures live OUTSIDE the `releases` branch: a range whose every candidate row
    # was malformed has no rows to print and the omission is exactly what must still be said.
    if releases or material.get("omitted_rows"):
        lines.append("Release notes added in this range (newest first):")
        for row in releases:
            # The row's own commit travels with it: a row whose text is not rendered here
            # still names where to read it in full.
            head = f"- {row.get('version')} ({row.get('date')}, added in {row.get('commit') or 'unknown commit'})"
            lines.append(f"{head}: {row['text']}" if row.get("text") else head)
        if material.get("omitted_rows"):
            lines.append(f"- [{material['omitted_rows']} malformed history row(s) omitted]")
        if material.get("rows_summarized"):
            lines.append(f"- [the oldest {material['rows_summarized']} row(s) above carry version and date only]")
    commits = material.get("commits") or []
    if commits:
        lines.append("")
        lines.append("First-parent commits in this range (newest first, every one of them):")
        for commit in commits:
            # The FULL sha, not a display prefix: the subject is a summary, the sha is the
            # only thing that makes the rest of that commit retrievable.
            lines.append(
                f"- {commit.get('sha') or ''} {commit.get('date', '')[:10]} {commit.get('subject', '')}"
            )
            body = str(commit.get("body") or "").strip()
            if body:
                lines.append("  " + body.replace("\n", "\n  "))
        if material.get("bodies_omitted"):
            lines.append(
                f"- [the bodies of {material['bodies_omitted']} older commit(s) are omitted; "
                "their subjects are all listed above]"
            )
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
        + "\n\n(The official_update block in my Runtime context describes the state before "
        "this check; the facts above supersede it.)"
        + "\n\nWrite my human ONE short paragraph — no headings, no lists, no more than about "
        "120 words — about what this update brings, as myself and in the language my human and I "
        "use together. Use only what the material says; "
        "if the material does not say something, I do not invent it. This paragraph is shown on "
        "the Updates page and becomes part of my own context; the commit history stays readable "
        "for detail. Reply with the paragraph only.\n"
        "[/UPDATE LETTER REQUEST]"
    )


def _context_messages(env: Any, memory: Any, task: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The ordinary task context in the owner's mode, dropping to the Low projection only
    when the light route's KNOWN window cannot take Max (a light slot smaller than the main
    model must still be able to write the letter; unknown windows keep the owner's mode)."""
    from ouroboros.context import build_context_fit_plan

    plan = build_context_fit_plan(env, memory, task)
    mode = plan.initial_mode
    if plan.projection(mode).fits_known_window is False and plan.low_projection.fits_known_window:
        mode = "low"
    return plan.messages_for(mode)


def _letter_timeout_sec() -> float:
    """Transport ceiling for the LIGHT call (config.py SSOT, clamped getter)."""
    from ouroboros.config import get_update_letter_timeout_sec

    return get_update_letter_timeout_sec()


def _chat(client: Any, *, drive_root: pathlib.Path, **kwargs: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    from ouroboros.llm_observability import chat_observed

    return chat_observed(client, drive_root=drive_root, task_id=SYSTEM_TASK_ID, call_type=USAGE_CATEGORY, **kwargs)


def _classify(exc: Exception) -> Tuple[str, str]:
    from ouroboros.usage_accounting import BudgetExceeded
    from ouroboros.utils import sanitize_tool_result_for_log

    safe = sanitize_tool_result_for_log(f"{type(exc).__name__}: {exc}")[:300]
    if isinstance(exc, MaterialUnavailable):
        return "material_unavailable", safe
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


def _new_record(key: Dict[str, str], target_version: str) -> Dict[str, Any]:
    """The record every write starts from: failed until something better is proven."""
    from ouroboros import get_version

    return {
        "schema": 1, "key": key, "checked_head_sha": key["base_sha"], "state": "failed",
        "text": "", "author_version": get_version(), "target_version": target_version,
        "model": "", "written_at": utc_now_iso(), "attempt_id": "", "attempt_ids": [],
        "error_kind": "", "error_text": "", "last_good": None,
    }


def write_letter(
    status: Dict[str, Any],
    material: Dict[str, Any],
    *,
    drive_root: Optional[pathlib.Path] = None,
    llm_client: Any = None,
) -> Dict[str, Any]:
    """One accounted LIGHT-slot call; returns the letter record (``ready`` or ``failed``)."""
    from ouroboros.config import DATA_DIR, REPO_DIR, get_light_model

    data_root = pathlib.Path(drive_root or DATA_DIR)
    repo_root = pathlib.Path(REPO_DIR)
    key = _key_from_status(status)
    target_version = str((material.get("versions") or {}).get("target") or "")
    record = _new_record(key, target_version)
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
        timeout = _letter_timeout_sec()
        with model_concurrency.model_call_slot(model, use_local, deadline_ts=time.time() + timeout):
            with usage_scope(scope):
                msg, usage = _chat(
                    client, drive_root=data_root, messages=messages, model=model, tools=None,
                    reasoning_effort="low", max_tokens=UPDATE_LETTER_MAX_TOKENS,
                    use_local=use_local, timeout=timeout,
                )
        attempt_ids = [str(a) for a in ((usage or {}).get("ledger_attempt_ids") or [])]
        record["attempt_ids"] = attempt_ids
        record["attempt_id"] = attempt_ids[-1] if attempt_ids else ""
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
    llm_client: Any = None,
) -> Optional[Dict[str, Any]]:
    """Write the letter after a successful FETCHING check; never raise, never delete.

    Every successful check records the HEAD it checked (``checked_head_sha``), letter or
    not, so the Runtime fact can tell "checked and current" from "moved since". No
    available update leaves any stored letter as it is (an applied update keeps its
    letter — that is the "what changed in this version" text). ``check_ok`` other than
    True writes nothing. A concurrent refresh waits for the in-flight one (bounded by the
    letter timeout) and shares its result for the same key instead of paying twice.
    """
    try:
        current = read_record(drive_root)
        if status.get("check_ok") is not True:
            return current
        key = _key_from_status(status)
        if not key["base_sha"]:
            return current
        no_update = not status.get("available") or not key["target_sha"]
        seen = _WRITE_SEQ
        # EVERY record write goes through this lock, the letterless mark included: a
        # no-update check that read the record before a letter landed would otherwise
        # write its stale copy back over it.
        if not _REFRESH_LOCK.acquire(timeout=_letter_timeout_sec()):
            return read_record(drive_root)
        try:
            # The writer that held the lock may have produced this very letter: share it
            # (one physical attempt for concurrent checks of the same key).
            if _WRITE_SEQ > seen and _LAST_WRITTEN[0] == key:
                return _LAST_WRITTEN[1]
            current = read_record(drive_root)
            if no_update:
                return _mark_checked(current, key, drive_root)
            try:
                material = collect_range_material(key["base_sha"], key["target_sha"])
            except MaterialUnavailable as exc:
                # The range could not be READ. That is a typed failure with the previous
                # good letter kept, never "this update has nothing to say".
                kind, safe = _classify(exc)
                record = _new_record(key, "")
                record.update(error_kind=kind, error_text=safe)
                material = None
            else:
                if not material.get("commits") and not material.get("releases"):
                    return _mark_checked(current, key, drive_root)
                record = write_letter(status, material, drive_root=drive_root, llm_client=llm_client)
            if record.get("state") != "ready" and current:
                # D-KEEP for the supersede case too: a good letter is never lost to a
                # failed rewrite, whatever range the failed attempt was for.
                previous_good = current if current.get("state") == "ready" else current.get("last_good")
                record["last_good"] = previous_good or None
            atomic_write_json(record_path(drive_root), record)
            _note_written(key, record)
            return record
        finally:
            _REFRESH_LOCK.release()
    except Exception:
        log.debug("update letter refresh failed", exc_info=True)
        return read_record(drive_root)


def _note_written(key: Dict[str, str], record: Dict[str, Any]) -> None:
    global _WRITE_SEQ, _LAST_WRITTEN
    _LAST_WRITTEN = (dict(key), record)
    _WRITE_SEQ += 1


def _mark_checked(current: Optional[Dict[str, Any]], key: Dict[str, str],
                  drive_root: Optional[pathlib.Path], *, git: Optional[GitCapture] = None) -> Dict[str, Any]:
    """Record the checked HEAD without touching any letter. A letterless record (``state: none``)
    follows the check it describes — its key and the official target's VERSION — so the Runtime
    fact can name the version an up-to-date install is current with."""
    record = dict(current) if current else {"schema": 1, "state": "none", "text": "", "last_good": None}
    if record.get("state") == "none":
        record["key"] = key
        record["target_version"] = _version_at(git or _default_git(), key["target_sha"] or key["base_sha"])
    record["checked_head_sha"] = key["base_sha"]
    atomic_write_json(record_path(drive_root), record)
    return record


# ---------------------------------------------------------------------------
# The one projection shared by the panel payload and the Runtime context
# ---------------------------------------------------------------------------

def project_letter(
    record: Optional[Dict[str, Any]],
    *,
    head_sha: str,
    latest_sha: str,
    consumed: bool = False,
) -> Optional[Dict[str, Any]]:
    """Relate the stored letter to the live HEAD and official target.

    ``consumed`` is the caller's proof that THIS letter's target is already in HEAD without
    being HEAD: a divergent install applies an official target as a merge commit
    (``supervisor/update_merge.py``), so HEAD never equals the target it consumed, and that
    proof — not SHA equality — is what makes such a letter ``applied``. The panel proves it
    with git ancestry; the Runtime fact, which has no git, proves it from the check that
    still describes this HEAD. It is never inferred from ``latest_sha``, which a passive
    read leaves empty exactly when the cached target has been consumed.
    """
    if not record or record.get("state") == "none":
        return None
    text = str(record.get("text") or "")
    last_good = record.get("last_good") if isinstance(record.get("last_good"), dict) else None
    provenance = record
    if record.get("state") != "ready" and not text and last_good:
        text, provenance = str(last_good.get("text") or ""), last_good
    # Relation, key and provenance all describe the TEXT shown: a good letter kept
    # through a failed rewrite may predate the range that failed (a moved target),
    # and relating it to that range would present it as the letter for the update
    # on offer. `has_last_good` says the text is the kept one.
    key = provenance.get("key") if isinstance(provenance.get("key"), dict) else {}
    base, target = str(key.get("base_sha") or ""), str(key.get("target_sha") or "")
    head, latest = str(head_sha or ""), str(latest_sha or "")
    if head and head == target:
        relation = "applied"
    elif consumed and target and head:
        relation = "applied"
    elif head and head == base and latest == target:
        relation = "pending"
    elif head and head == base:
        relation = "superseded"
    else:
        relation = "other"
    return {
        "state": "ready" if record.get("state") == "ready" else "failed",
        "relation": relation,
        "text": text,
        "author_version": str(provenance.get("author_version") or ""),
        "target_version": str(provenance.get("target_version") or ""),
        "written_at": str(provenance.get("written_at") or ""),
        "error_kind": str(record.get("error_kind") or ""),
        "error_text": str(record.get("error_text") or ""),
        "key": dict(key),
        "has_last_good": bool(last_good and last_good.get("text")),
    }


def project_letter_for_panel(
    status: Dict[str, Any],
    *,
    drive_root: Optional[pathlib.Path] = None,
    git: Optional[GitCapture] = None,
) -> Optional[Dict[str, Any]]:
    """The Updates panel's projection of the stored letter.

    The panel HAS git, so it proves a consumed target by ancestry instead of inferring it
    from the status: a passive read leaves ``latest_sha`` empty EXACTLY when the cached
    target has already been consumed, which is when the answer matters most. The Runtime
    fact reaches the same verdict without git in ``official_update_projection``; keeping
    both here is what stops the panel and the mind from describing one install differently.
    """
    record = read_record(drive_root)
    head = str(status.get("current_sha") or "")
    target = str(((record or {}).get("key") or {}).get("target_sha") or "")
    consumed = False
    # Ancestry is asked only where it can change the answer: a letter about the target
    # still ON OFFER is pending by definition, and HEAD == target needs no git. Everything
    # else — including an applied older target under a newer available one — is asked.
    offered = str(status.get("latest_sha") or "")
    if record and head and target and head != target and target != offered:
        try:
            consumed = (git or _default_git())(
                ["git", "merge-base", "--is-ancestor", target, head]
            )[0] == 0
        except Exception:  # noqa: BLE001 — an unprovable ancestry is simply not proof
            log.debug("consumed-target ancestry check failed", exc_info=True)
    return project_letter(
        record, head_sha=head, latest_sha=str(status.get("latest_sha") or ""), consumed=consumed,
    )


def official_update_projection(
    head_sha: str,
    *,
    drive_root: Optional[pathlib.Path] = None,
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The typed fact for the Runtime context: O(1), no git, never raises.

    ``status`` is honest about freshness. It follows the CHECK, not the letter:
    ``update_available``/``up_to_date`` while the last check still describes this HEAD (the
    availability is the cache's, so an available update may carry a failed letter or none at
    all), ``up_to_date`` also when HEAD is the checked target itself, ``moved_since_check``
    when HEAD matches neither, ``unchecked`` before any fetching check has completed.
    ``status_as_of`` names the check the fact comes from.
    ``head_sha`` is the HEAD the caller already resolved for its own repository; a
    child worktree that committed reads ``moved_since_check`` for its own tree, which
    is a true statement about that tree, not about the canonical body.
    """
    try:
        from ouroboros import get_version
        from ouroboros.config import DATA_DIR

        head = str(head_sha or "")
        if state is None:
            # Lock-free read, like the neighbouring drive-state section: this runs on
            # every task round and consciousness cycle, never under the state lock.
            state = read_json_dict(pathlib.Path(drive_root or DATA_DIR) / "state" / "state.json") or {}
        cache = state.get("managed_update_cache") if isinstance(state, dict) else None
        cache = cache if isinstance(cache, dict) else {}
        record = read_record(drive_root)
        latest = str(cache.get("latest_sha") or "")
        checked_head = str((record or {}).get("checked_head_sha") or "")
        record_key = (record or {}).get("key") if isinstance((record or {}).get("key"), dict) else {}
        # No git here: the check itself is the proof, and only while it still describes
        # THIS head and names THIS letter's target as the one with nothing incoming.
        consumed = bool(
            head and head == checked_head
            and cache.get("behind") == 0
            and latest and latest == str(record_key.get("target_sha") or "")
            and head != latest
        )
        letter = project_letter(record, head_sha=head, latest_sha=latest, consumed=consumed)
        if not cache:
            status = "unchecked"
        elif head and head == checked_head:
            status = "update_available" if cache.get("available") else "up_to_date"
        elif head and head == latest:
            status = "up_to_date"
        else:
            status = "moved_since_check"
        target = None
        if latest:
            same_target = str(record_key.get("target_sha") or "") == latest
            target = {
                "version": str((record or {}).get("target_version") or "") if same_target else "",
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
