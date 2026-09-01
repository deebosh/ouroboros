"""Durable actionable improvement backlog stored in the knowledge base."""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List

from ouroboros.platform_layer import file_lock_exclusive, file_lock_shared, file_unlock
from ouroboros.utils import utc_now_iso

BACKLOG_TOPIC = "improvement-backlog"
BACKLOG_REL_PATH = f"memory/knowledge/{BACKLOG_TOPIC}.md"
BACKLOG_ARCHIVE_REL_PATH = f"memory/knowledge/{BACKLOG_TOPIC}.archive.md"
_BACKLOG_TITLE = "# Improvement Backlog"
_BACKLOG_PREAMBLE = (
    "This topic stores concrete, evidence-backed improvement items discovered during task execution.\n"
    "Items here are advisory backlog nominations, not auto-started work.\n"
    "Before implementation, run plan_task for non-trivial backlog items."
)
_DEFAULT_BACKLOG_TEXT = f"{_BACKLOG_TITLE}\n\n{_BACKLOG_PREAMBLE}\n"
_ARCHIVE_HEADER = (
    "# Improvement Backlog — Groom Archive\n\n"
    "This file accumulates backlog items dropped by `groom_backlog` (the size-triggered LLM groom).\n"
    "Items here are preserved FORENSICALLY so a cull is never a silent data loss "
    "(BIBLE P1: no silent amputation).\n"
    "Each entry carries `status: groom-culled` with a `closed_at` timestamp marking the groom that "
    "removed it. Re-import via `merge_backlog_text` after manual review.\n"
)


def backlog_path(drive_root: Any) -> pathlib.Path:
    return pathlib.Path(drive_root) / BACKLOG_REL_PATH


def archive_backlog_path(drive_root: Any) -> pathlib.Path:
    return pathlib.Path(drive_root) / BACKLOG_ARCHIVE_REL_PATH


def _append_groom_archive(archive_path: pathlib.Path, items: List[Dict[str, Any]]) -> None:
    """Append culled items to the groom archive (BIBLE P1: no silent amputation).

    Seeds a forensic header on first use; subsequent appends are strictly
    append-only. Each archived row carries ``status: groom-culled`` and a
    ``closed_at`` timestamp so a re-import via ``merge_backlog_text`` can
    distinguish archive rows from live items. Raises on any filesystem/lock
    failure so callers can fail closed (a cull with no archive is the bug).
    """
    if not items:
        return
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not archive_path.exists() or archive_path.stat().st_size == 0
    now = utc_now_iso()
    archived_blocks: List[str] = []
    for it in items:
        row = {k: v for k, v in it.items() if k != "_raw"}
        row["status"] = "groom-culled"
        row["closed_at"] = now
        archived_blocks.append(_serialize_item(row))
    with _locked_text_file(archive_path, mode="a") as fh:
        if needs_header:
            fh.write(_ARCHIVE_HEADER)
            fh.write("\n")
        for block in archived_blocks:
            fh.write("\n\n")
            fh.write(block)
        if archived_blocks:
            fh.write("\n")
        fh.flush()


@contextmanager
def _locked_text_file(path: pathlib.Path, mode: str, *, shared: bool = False) -> Iterator[Any]:
    fh = open(path, mode, encoding="utf-8")
    try:
        if shared:
            file_lock_shared(fh.fileno())
        else:
            file_lock_exclusive(fh.fileno())
        yield fh
    finally:
        try:
            file_unlock(fh.fileno())
        except Exception:
            pass
        fh.close()


def ensure_backlog_file(drive_root: Any) -> pathlib.Path:
    path = backlog_path(drive_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _locked_text_file(path, mode="a+") as fh:
        fh.seek(0)
        current = fh.read()
        if not current:
            fh.write(_DEFAULT_BACKLOG_TEXT)
            fh.flush()
    return path


def _stable_fingerprint(summary: str, category: str, source: str) -> str:
    key = " | ".join(
        re.sub(r"\s+", " ", str(value or "")).strip().lower()
        for value in (summary, category, source)
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _parse_backlog_items(text: str) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    current: Dict[str, str] | None = None
    raw_lines: List[str] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("### "):
            if current is not None:
                current["_raw"] = "\n".join(raw_lines).rstrip()
                items.append(current)
            current = {"id": line[4:].strip()}
            raw_lines = [raw_line]
            continue
        if current is None:
            continue
        # Retain the verbatim block (incl. freeform/comment lines) so an unmodified
        # item can be re-serialized losslessly (BIBLE P1: no silent data loss).
        raw_lines.append(raw_line)
        if line.startswith("- ") and ": " in line:
            key, value = line[2:].split(": ", 1)
            current[key.strip()] = value.strip()

    if current is not None:
        current["_raw"] = "\n".join(raw_lines).rstrip()
        items.append(current)
    return items


def load_backlog_items(drive_root: Any) -> List[Dict[str, str]]:
    path = backlog_path(drive_root)
    if not path.exists():
        return []
    with _locked_text_file(path, mode="r", shared=True) as fh:
        text = fh.read()
    return _parse_backlog_items(text)


# Canonical entry field order for (re)serialization. Additive over the original
# schema: priority/count/last_seen/kind/closed_at were added in v6.23.2.
_ENTRY_KEYS = (
    "status",
    "priority",
    "kind",
    "created_at",
    "last_seen",
    "count",
    "source",
    "category",
    "task_id",
    "requires_plan_review",
    "fingerprint",
    "closed_at",
    "referent_status",
    "summary",
    "evidence",
    "context",
    "proposed_next_step",
)

_PRIORITY_RANK = {"high": 0, "med": 1, "medium": 1, "low": 2}
_VALID_PRIORITY = {"high", "med", "low"}


def _sanitize(value: Any, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    # Backlog values must stay on ONE line for the `- key: value` parser, so use a
    # single-line visible omission note — never a silent clip of a cognitive
    # artifact (BIBLE P1 / DEVELOPMENT.md).
    return text[:limit] + f" ⚠️ OMISSION NOTE: +{len(text) - limit} chars omitted"


def _priority(value: Any) -> str:
    raw = str(value or "med").strip().lower()
    raw = {"medium": "med"}.get(raw, raw)
    return raw if raw in _VALID_PRIORITY else "med"


def _count_of(item: Dict[str, Any]) -> int:
    try:
        return max(1, int(item.get("count") or 1))
    except (TypeError, ValueError):
        return 1


# --- Code-referent probe (ibl-74bd59bab040) -----------------------------------
# Phantom IBLs: reflection-nominated backlog items whose summary/evidence/
# proposed_next_step name a file/test/function that DOES NOT EXIST burn worker
# rounds chasing ghosts. We tag each NEW entry with `referent_status` so the
# downstream pipeline can skip / deprioritize unverified items without dropping
# them entirely (tag, never drop). Recurrence bumps preserve the existing tag.

_REFERENT_CAP = 12
_REFERENT_RESOLVE_DEADLINE_SEC = 3.0
_REFERENT_RESOLVE_FILE_CAP = 5000
_REFERENT_SKIP_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", "data",
    "archive", ".ouroboros", "dist", "build", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "site-packages", "node_modules",
})

_REFERENT_PATH_RE = re.compile(r"(?:[\w./-]+/)?[\w./-]+\.(?:py|js|ts|md|json|toml|sh)\b")
# Two identifier forms (ibl-74bd59bab040 spec): `def <name>` (declaration) AND
# `<name>(` (call site). A single regex is awkward because the call form requires
# `(` and the def form requires a word boundary, so we run them separately.
_REFERENT_DEF_FORM_RE = re.compile(r"\bdef\s+([A-Za-z_]\w{3,})\b")
_REFERENT_CALL_FORM_RE = re.compile(r"\b([A-Za-z_]\w{3,})\s*\(")
_REFERENT_TEST_PATH_RE = re.compile(r"\b(?:tests/test_\w+)\b")
# Capture just the `test_X` name (with or without the `tests/` prefix); ``\w+``
# (not ``\w{4,}``) so a 1-char suffix like ``test_x`` is still a valid referent —
# the ``test_`` prefix already enforces len>=4 on the identifier name.
_REFERENT_TEST_NAME_RE = re.compile(r"(?:\btests/)?\b(test_\w+)\b")


def _extract_code_referents(text: str) -> List[str]:
    """Pull code-shaped referents (paths, def names, test names) from a blob.
    Dedup and cap at ``_REFERENT_CAP``; bare ``x.py`` without a path separator
    is treated as too noisy unless the basename starts with ``test_``."""
    if not text:
        return []
    out: List[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        if token and token not in seen:
            seen.add(token)
            out.append(token)

    # (a) Paths with a code extension
    for m in _REFERENT_PATH_RE.finditer(text):
        s = m.group(0).rstrip("/")
        if "/" not in s and not s.startswith("test_"):
            continue
        _add(s)

    # (b) Function-call-style identifiers: `def <name>` (declaration) AND
    # `<name>(` (call site). Both forms qualify as a code referent.
    for m in _REFERENT_DEF_FORM_RE.finditer(text):
        _add(m.group(1))
    for m in _REFERENT_CALL_FORM_RE.finditer(text):
        _add(m.group(1))

    # (c) test names — `tests/test_X` paths first (preferred for the test module)
    # then plain `test_X` for the test-function name.
    for m in _REFERENT_TEST_PATH_RE.finditer(text):
        _add(m.group(0))
    for m in _REFERENT_TEST_NAME_RE.finditer(text):
        _add(m.group(1))

    return out[:_REFERENT_CAP]


def _identifier_exists_in_repo(name: str, repo_dir: pathlib.Path, repo_is_dir: bool) -> bool:
    """Bounded substring scan for ``name`` across ``.py/.js/.ts/.md`` files under
    ``repo_dir``. Walk is time- and file-capped; any exception or incompletion
    resolves the referent (fail-open: never fabricate a phantom flag from a
    probe error — the spec's safety default)."""
    if not repo_is_dir:
        return True
    deadline = time.monotonic() + _REFERENT_RESOLVE_DEADLINE_SEC
    try:
        file_count = 0
        incomplete = False
        for root, dirs, files in os.walk(str(repo_dir)):
            if time.monotonic() > deadline:
                incomplete = True
                break
            dirs[:] = sorted(d for d in dirs if d not in _REFERENT_SKIP_DIRS)
            for fn in files:
                if time.monotonic() > deadline:
                    incomplete = True
                    break
                if not fn.endswith((".py", ".js", ".ts", ".md")):
                    continue
                file_count += 1
                if file_count > _REFERENT_RESOLVE_FILE_CAP:
                    incomplete = True
                    break
                p = pathlib.Path(root) / fn
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                        if name in fh.read():
                            return True
                except Exception:
                    continue
            if file_count > _REFERENT_RESOLVE_FILE_CAP:
                incomplete = True
                break
        if incomplete:
            return True  # inconclusive walk -> fail-open resolved
        return False
    except Exception:
        return True  # probe error -> fail-open resolved


def _referents_resolve(referents: List[str], repo_dir: Any) -> tuple[int, int]:
    """For every extracted referent decide whether it actually exists in the
    repo. Returns ``(resolved, total)`` and is fail-open at the probe level:
    any exception resolving a single referent marks THAT referent as resolved,
    so a transient probe error never produces a phantom ``unverified`` flag."""
    total = len(referents)
    if total == 0:
        return (0, 0)
    try:
        repo_path = pathlib.Path(repo_dir) if not isinstance(repo_dir, pathlib.Path) else repo_dir
    except Exception:
        return (total, total)  # cannot construct a Path -> fail-open for everyone
    try:
        repo_is_dir = repo_path.is_dir()
    except Exception:
        repo_is_dir = False

    resolved = 0
    for ref in referents:
        try:
            if "/" in ref or ref.endswith((".py", ".js", ".ts", ".md", ".json", ".toml", ".sh")):
                if repo_is_dir and (repo_path / ref).exists():
                    resolved += 1
                elif not repo_is_dir:
                    resolved += 1  # probe error (broken repo_dir) -> fail-open
                # else: legitimate miss -> do not increment
            else:
                if _identifier_exists_in_repo(ref, repo_path, repo_is_dir):
                    resolved += 1
        except Exception:
            # Per-referent probe error -> fail-open resolved
            resolved += 1
    return (resolved, total)


def _classify_referent_status(resolved: int, total: int) -> str | None:
    """Map a referent probe to ``referent_status`` per the ibl-74bd59bab040 spec.
    Returns ``None`` when there is nothing to classify (total == 0)."""
    if total == 0:
        return "none"
    if resolved == 0:
        return "unverified"
    if resolved < total:
        return "partial"
    return "verified"


def _serialize_item(entry: Dict[str, Any]) -> str:
    # Unmodified items carry their verbatim source block (`_raw`): re-emit it
    # exactly, so a read-modify-write that only touches OTHER items never alters
    # or drops this one (incl. hand-added freeform/comment lines). Modified items
    # drop `_raw` (see append/close/groom) and are re-serialized canonically.
    raw = entry.get("_raw")
    if raw:
        return str(raw)
    block = [f"### {entry.get('id', 'ibl-?')}"]
    for key in _ENTRY_KEYS:
        value = entry.get(key)
        if value not in (None, ""):
            block.append(f"- {key}: {value}")
    # Preserve any hand-added fields outside the canonical schema.
    for key, value in entry.items():
        if key == "id" or key == "_raw" or key in _ENTRY_KEYS:
            continue
        if value not in (None, ""):
            block.append(f"- {key}: {value}")
    return "\n".join(block)


def _serialize_backlog(items: List[Dict[str, Any]]) -> str:
    head = f"{_BACKLOG_TITLE}\n\n{_BACKLOG_PREAMBLE}\n"
    if not items:
        return head
    return head + "\n" + "\n\n".join(_serialize_item(it) for it in items) + "\n"


def _rebuild_index(path: pathlib.Path) -> None:
    try:
        from ouroboros.consolidator import _rebuild_knowledge_index

        _rebuild_knowledge_index(path.parent)
    except Exception:
        pass


_DEDUP_CANDIDATE_CAP = 20


def _dedup_candidates(open_items: List[Dict[str, Any]], category: str, source: str) -> List[Dict[str, str]]:
    """Open backlog items sharing the new item's category or source, deterministically
    ranked (priority, recurrence count, recency) and capped — the candidate pool the
    semantic detector ranks a new item against."""
    same = [
        it for it in open_items
        if str(it.get("category") or "") == category or str(it.get("source") or "") == source
    ]
    same.sort(key=lambda it: str(it.get("last_seen") or it.get("created_at") or ""), reverse=True)
    same.sort(key=lambda it: (_PRIORITY_RANK.get(_priority(it.get("priority")), 1), -_count_of(it)))
    return [
        {"id": str(it.get("id") or ""), "text": str(it.get("summary") or "")}
        for it in same[:_DEDUP_CANDIDATE_CAP]
        if it.get("id") and it.get("summary")
    ]


def _semantic_redirect_fingerprints(
    drive_root: Any, path: pathlib.Path, items: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Two-phase semantic dedup (C9.2): snapshot the open backlog under a shared
    lock, RELEASE it, then for each NEW item that is an exact-fingerprint MISS ask
    the shared detector whether it duplicates an existing open item of the same
    category/source. A high-confidence dup has its fingerprint REDIRECTED to the
    match, so the locked exact pass below simply bumps that item's count/last_seen
    instead of minting a new ibl-*. The LLM call stays OUTSIDE the file lock; any
    failure (or a concurrent change before the exclusive write) is fail-open — the
    item keeps its own fingerprint and lands as new. Never raises."""
    try:
        with _locked_text_file(path, mode="r", shared=True) as fh:
            existing = _parse_backlog_items(fh.read())
    except Exception:
        return items
    existing_fps = {str(it.get("fingerprint") or "") for it in existing if it.get("fingerprint")}
    open_items = [
        it for it in existing
        if str(it.get("status") or "open").lower() != "done" and it.get("fingerprint")
    ]
    if not open_items:
        return items

    from ouroboros.semantic_dedup import find_semantic_duplicate_id

    out: List[Dict[str, Any]] = []
    for item in items:
        summary = _sanitize(item.get("summary", ""), 260)
        if not summary:
            out.append(item)
            continue
        category = _sanitize(item.get("category", "process"), 60) or "process"
        source = _sanitize(item.get("source", "task"), 60) or "task"
        fingerprint = str(item.get("fingerprint") or _stable_fingerprint(summary, category, source))
        if fingerprint in existing_fps:
            out.append(item)  # exact hit — the locked pass bumps it, no LLM needed
            continue
        candidates = _dedup_candidates(open_items, category, source)
        if not candidates:
            out.append(item)
            continue
        dup_id = find_semantic_duplicate_id(
            summary, candidates,
            subject="backlog improvement item",
            call_type="backlog_dedup",
            drive_root=drive_root,
        )
        target = next((it for it in open_items if str(it.get("id") or "") == dup_id), None) if dup_id else None
        if target and target.get("fingerprint"):
            item = {**item, "fingerprint": str(target["fingerprint"])}  # redirect -> bump
        out.append(item)
    return out


def append_backlog_items(
    drive_root: Any,
    items: List[Dict[str, Any]],
    repo_dir: Any = None,
) -> int:
    """Add/refresh backlog items. Recurrence (A): a repeat of an existing item is
    NOT dropped — its ``count`` and ``last_seen`` are bumped in place (and a
    previously-closed item re-opens). Priority/kind (B/D) are persisted. A reworded
    restatement that misses the exact fingerprint is caught by the semantic dedup
    pre-pass (C9.2) and folded into the item it duplicates.

    Optional ``repo_dir`` enables the ibl-74bd59bab040 phantom-referent tag: when
    provided, each NEW entry is probed for code-shaped referents in its
    summary/evidence/proposed_next_step, tagged ``verified|unverified|partial|none``,
    and a high-priority unverified entry is downgraded to ``med``. Recurrence bumps
    preserve the original tag — the count never re-probes. ``None`` (default) keeps
    the prior behaviour: every existing caller's tag-free legacy path is intact."""
    if not items:
        return 0

    path = ensure_backlog_file(drive_root)
    items = _semantic_redirect_fingerprints(drive_root, path, items)
    with _locked_text_file(path, mode="r+") as fh:
        existing_text = fh.read()
        existing = _parse_backlog_items(existing_text)
        # Preserve EVERY parsed item (read-modify-write), including parser-valid
        # entries that lack a fingerprint (e.g. hand-added) — they must survive.
        by_key: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        fp_to_key: Dict[str, str] = {}
        for idx, it in enumerate(existing):
            fp = str(it.get("fingerprint") or "")
            key = fp or f"__nofp_{idx}_{it.get('id', '') or idx}"
            by_key[key] = it
            order.append(key)
            if fp:
                fp_to_key[fp] = key
        now = utc_now_iso()
        changed = 0

        for item in items:
            summary = _sanitize(item.get("summary", ""), 260)
            if not summary:
                continue
            category = _sanitize(item.get("category", "process"), 60) or "process"
            source = _sanitize(item.get("source", "task"), 60) or "task"
            fingerprint = str(item.get("fingerprint") or _stable_fingerprint(summary, category, source))
            if fingerprint in fp_to_key:
                ex = by_key[fp_to_key[fingerprint]]
                ex["count"] = str(_count_of(ex) + 1)
                ex["last_seen"] = now
                # A recurring item that was marked done is evidently not resolved.
                if str(ex.get("status") or "").lower() == "done":
                    ex["status"] = "open"
                    ex.pop("closed_at", None)
                # NOTE: a recurrence bump NEVER recomputes referent_status — once
                # tagged it stays tagged (phantom-grade items stay phantom-grade, and
                # a real referent stays verified) until the groom / close path
                # explicitly retags.
                ex.pop("_raw", None)  # modified -> re-serialize canonically
                changed += 1
                continue
            created = _sanitize(item.get("created_at", now), 40)
            entry: Dict[str, Any] = {
                "id": str(item.get("id") or f"ibl-{fingerprint}"),
                "status": _sanitize(item.get("status", "open"), 40) or "open",
                "priority": _priority(item.get("priority")),
                "kind": _sanitize(item.get("kind", "improvement"), 40) or "improvement",
                "created_at": created,
                "last_seen": created,
                "count": "1",
                "source": source,
                "category": category,
                "task_id": _sanitize(item.get("task_id", ""), 80),
                "requires_plan_review": "yes" if item.get("requires_plan_review", True) else "no",
                "fingerprint": fingerprint,
                "summary": summary,
                "evidence": _sanitize(item.get("evidence", ""), 260),
                "context": _sanitize(item.get("context", ""), 400),
                "proposed_next_step": _sanitize(item.get("proposed_next_step", ""), 260),
            }

            # ibl-74bd59bab040 phantom-referent tag — keeps the item (tag, do NOT
            # drop) and downgrades a high-priority unverified item to ``med`` so
            # downstream workers don't burn 30-84 rounds on a no-such-referent.
            if repo_dir is not None:
                blob = " ".join(
                    [
                        str(entry.get("summary") or ""),
                        str(entry.get("evidence") or ""),
                        str(entry.get("proposed_next_step") or ""),
                    ]
                )
                referents = _extract_code_referents(blob)
                resolved, total = _referents_resolve(referents, repo_dir)
                status = _classify_referent_status(resolved, total)
                if status is not None:
                    entry["referent_status"] = status
                # A phantom-suspect is not high priority — drop one notch. Lower-
                # priority items also stay at their level (med stays med, low
                # stays low): the spec only downgrades high.
                if status == "unverified" and entry.get("priority") == "high":
                    entry["priority"] = "med"

            by_key[fingerprint] = entry
            order.append(fingerprint)
            fp_to_key[fingerprint] = fingerprint
            changed += 1

        if not changed:
            return 0

        new_text = _serialize_backlog([by_key[k] for k in order])
        fh.seek(0)
        fh.write(new_text)
        fh.truncate()
        fh.flush()

    _rebuild_index(path)
    return changed


def merge_backlog_text(drive_root: Any, text: str, repo_dir: Any = None) -> int:
    """Non-destructively merge backlog items parsed from ``text`` into the ONE
    global backlog (C10.1 Fix A). Routes through ``append_backlog_items`` so the
    write is a UNION (existing items preserved, new items added, reworded restatements
    folded by the dedup pre-pass) — never a truncating overwrite that could wipe the
    immune backlog. Returns the number of items merged, or ``-1`` (fail-closed) when
    ``text`` carries no parseable item — an unparseable overwrite must leave the
    backlog intact, even if the model believes it wrote something. Never raises.

    ``repo_dir`` is forwarded to the underlying ``append_backlog_items`` so a
    non-``None`` value triggers the ibl-74bd59bab040 phantom-referent tag for
    NEW items; ``None`` preserves the legacy tag-free behaviour."""
    try:
        items = [
            it for it in _parse_backlog_items(text or "")
            if str(it.get("summary") or "").strip()
        ]
    except Exception:
        return -1
    if not items:
        return -1
    # Only items NOT already present (by exact fingerprint) are merged: a verbatim
    # re-write of the backlog — the natural read-edit-write pattern — must not bump
    # every existing item's recurrence count by +1 (the count drives ranking and the
    # recurrence heuristics). Existing items stay untouched (non-destructive); only
    # genuinely new items flow through the SSOT append (its own semantic pre-pass +
    # exact merge still applies to those).
    try:
        existing_fps = {
            str(it.get("fingerprint") or "")
            for it in load_backlog_items(drive_root)
            if it.get("fingerprint")
        }
    except Exception:
        existing_fps = set()
    fresh: List[Dict[str, Any]] = []
    for it in items:
        summary = _sanitize(it.get("summary", ""), 260)
        category = _sanitize(it.get("category", "process"), 60) or "process"
        source = _sanitize(it.get("source", "task"), 60) or "task"
        fingerprint = str(it.get("fingerprint") or _stable_fingerprint(summary, category, source))
        if fingerprint in existing_fps:
            continue
        fresh.append(it)
    if not fresh:
        return 0  # everything already present — backlog intact, no inflation
    return append_backlog_items(drive_root, fresh, repo_dir=repo_dir)


def close_backlog_items(drive_root: Any, *, task_id: Any = None, ids: Any = None) -> int:
    """Close-on-commit (C): flip matching open items to ``status: done`` with a
    ``closed_at`` stamp. Match by originating ``task_id`` and/or explicit ``ids``."""
    path = backlog_path(drive_root)
    if not path.exists():
        return 0
    want_task = str(task_id or "").strip()
    want_ids = {str(i).strip() for i in (ids or []) if str(i or "").strip()}
    if not want_task and not want_ids:
        return 0
    with _locked_text_file(path, mode="r+") as fh:
        text = fh.read()
        items = _parse_backlog_items(text)
        now = utc_now_iso()
        closed = 0
        for it in items:
            if str(it.get("status") or "").lower() == "done":
                continue
            if (want_task and str(it.get("task_id") or "") == want_task) or (str(it.get("id") or "") in want_ids):
                it["status"] = "done"
                it["closed_at"] = now
                it.pop("_raw", None)  # modified -> re-serialize canonically
                closed += 1
        if not closed:
            return 0
        fh.seek(0)
        fh.write(_serialize_backlog(items))
        fh.truncate()
        fh.flush()
    _rebuild_index(path)
    return closed


def format_backlog_digest(drive_root: Any, *, limit: int = 5, max_chars: int = 2500) -> str:
    items = [item for item in load_backlog_items(drive_root) if item.get("status", "open") == "open"]
    if not items:
        return ""

    # Sort (B): priority, then recurrence count, then recency — so an important
    # older item outranks a junk burst. Recency uses last_seen (bumped on
    # recurrence), falling back to created_at, so a recently-recurred old item
    # is not unfairly demoted.
    items.sort(key=lambda item: str(item.get("last_seen") or item.get("created_at", "")), reverse=True)
    items.sort(key=lambda item: (_PRIORITY_RANK.get(_priority(item.get("priority")), 1), -_count_of(item)))
    visible = items[:limit]
    lines = [
        "## Improvement Backlog",
        "",
        f"- open_items: {len(items)}",
        "- policy: advisory backlog only; run plan_task before implementation",
    ]
    for item in visible:
        bits = [f"[{item.get('id', '?')}]", item.get("summary", "(missing summary)")]
        meta = [f"priority={_priority(item.get('priority'))}"]
        count = _count_of(item)
        if count > 1:
            meta.append(f"count={count}")
        if item.get("kind"):
            meta.append(f"kind={item['kind']}")
        if item.get("category"):
            meta.append(f"category={item['category']}")
        if item.get("source"):
            meta.append(f"source={item['source']}")
        if item.get("task_id"):
            meta.append(f"task={item['task_id']}")
        line = "- " + " ".join(bits)
        if meta:
            line += " (" + ", ".join(meta) + ")"
        lines.append(line)
    omitted = len(items) - len(visible)
    if omitted > 0:
        lines.append(f"- ⚠️ OMISSION NOTE: {omitted} additional open backlog items not shown")

    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[:max_chars] + f"\n⚠️ OMISSION NOTE: backlog digest truncated at {max_chars} chars; original length {len(text)}"
    return text


_GROOM_CAP = int(os.environ.get("OUROBOROS_BACKLOG_GROOM_CAP", "120"))

_GROOM_PROMPT = """You are grooming Ouroboros's improvement backlog. Goals:
- Merge near-duplicate items (keep the clearest summary + the higher priority; set the survivor's count to the summed count).
- Mark items that are clearly already resolved/obsolete as status "done".
- Drop pure noise.
- Keep at most {cap} of the most valuable OPEN items, ranked by priority (high>med>low), then recurrence count, then recency.

Current backlog items (JSON):
{items_json}

Return ONLY a JSON array of the items to KEEP. Each object: {{"id","status","priority","kind","summary","category","source","task_id","requires_plan_review","count","created_at","fingerprint","evidence","context","proposed_next_step"}}. Preserve the id/fingerprint/created_at of every kept item. Do NOT invent new work. Do NOT drop a high-priority or high-count item just to hit the cap."""


def groom_backlog(drive_root: Any, *, cap: int = _GROOM_CAP) -> int:
    """D: best-effort LLM grooming of AUTO-generated (fingerprinted) backlog items —
    merge near-dupes, mark resolved, re-rank, cap to <=cap. Hand-added
    (no-fingerprint) items are passed through UNCHANGED (never dropped). Runs on a
    size-triggered (NOT error-gated) schedule and re-serializes through the locked
    parser-safe writer. Returns the number of items written, or 0 when nothing was
    groomed. Fails closed (no write) on a bad/empty/oversized model reply OR if the
    backlog changed concurrently during the lock-free LLM call (no lost updates)."""
    import json as _json

    path = backlog_path(drive_root)
    if not path.exists():
        return 0
    with _locked_text_file(path, mode="r", shared=True) as fh:
        snapshot_text = fh.read()
    items = _parse_backlog_items(snapshot_text)
    if len(items) <= cap:
        return 0
    # Only AUTO-generated (fingerprinted) items are groomable; hand-added items
    # (no fingerprint) are user-curated and pass through untouched.
    fp_items = [it for it in items if str(it.get("fingerprint") or "").strip()]
    nofp_items = [it for it in items if not str(it.get("fingerprint") or "").strip()]
    if not fp_items:
        return 0

    try:
        from ouroboros.config import get_light_model
        from ouroboros.llm import LLMClient
        from ouroboros.llm_observability import chat_observed

        compact = [
            {
                k: it.get(k, "")
                for k in ("id", "status", "priority", "kind", "summary", "category",
                          "source", "task_id", "requires_plan_review", "count",
                          "created_at", "fingerprint")
            }
            for it in fp_items
        ]
        prompt = _GROOM_PROMPT.format(cap=cap, items_json=_json.dumps(compact, ensure_ascii=False))
        client = LLMClient()
        resp, usage = chat_observed(
            client,
            drive_root=pathlib.Path(drive_root),
            task_id="backlog_groom",
            call_type="backlog_groom",
            messages=[{"role": "user", "content": prompt}],
            model=get_light_model(),
            reasoning_effort="low",
            max_tokens=8192,
        )
        if usage:
            try:
                from supervisor.state import update_budget_from_usage

                update_budget_from_usage(usage)
            except Exception:
                pass
        content = (resp.get("content") or "").strip()
        start, end = content.find("["), content.rfind("]")
        if start < 0 or end <= start:
            return 0
        kept_raw = _json.loads(content[start:end + 1])
        if not isinstance(kept_raw, list):
            return 0
    except Exception:
        return 0

    # Anti-wipe: every kept item MUST map to an existing fingerprinted item — the
    # model may merge/drop/re-rank but NEVER invent survivors — and grooming may
    # not over-drop the auto items in one pass.
    by_fp = {str(it.get("fingerprint") or ""): it for it in fp_items}
    by_id = {str(it.get("id") or ""): it for it in fp_items if it.get("id")}
    kept_fp: List[Dict[str, Any]] = []
    seen_fp = set()
    for obj in kept_raw:
        if not isinstance(obj, dict):
            continue
        summary = _sanitize(obj.get("summary", ""), 260)
        if not summary:
            continue
        ofp = str(obj.get("fingerprint") or "").strip()
        oid = str(obj.get("id") or "").strip()
        base = (by_fp.get(ofp) if ofp else None) or (by_id.get(oid) if oid else None)
        if not base:
            continue  # invented item — drop it (never wipe-by-invention)
        fingerprint = str(base.get("fingerprint") or "")
        if not fingerprint or fingerprint in seen_fp:
            continue
        seen_fp.add(fingerprint)
        category = _sanitize(obj.get("category", base.get("category", "process")), 60) or "process"
        source = _sanitize(obj.get("source", base.get("source", "task")), 60) or "task"
        merged_count = _count_of(obj) if str(obj.get("count") or "").isdigit() else _count_of(base)
        kept_fp.append({
            "id": str(base.get("id") or f"ibl-{fingerprint}"),
            "status": _sanitize(obj.get("status", base.get("status", "open")), 40) or "open",
            "priority": _priority(obj.get("priority", base.get("priority"))),
            "kind": _sanitize(obj.get("kind", base.get("kind", "improvement")), 40) or "improvement",
            "created_at": _sanitize(base.get("created_at", utc_now_iso()), 40),
            "last_seen": _sanitize(base.get("last_seen", utc_now_iso()), 40),
            "count": str(max(1, merged_count)),
            "source": source,
            "category": category,
            "task_id": _sanitize(base.get("task_id", ""), 80),
            "requires_plan_review": "no" if str(obj.get("requires_plan_review", base.get("requires_plan_review", "yes"))).lower() in ("no", "false") else "yes",
            "fingerprint": fingerprint,
            "summary": summary,
            "evidence": _sanitize(base.get("evidence", ""), 260),
            "context": _sanitize(base.get("context", ""), 400),
            "proposed_next_step": _sanitize(base.get("proposed_next_step", ""), 260),
        })
    if not kept_fp or len(kept_fp) > cap or len(kept_fp) < max(1, min(len(fp_items), cap) // 2):
        return 0

    # ARCHIVE BEFORE DROP (BIBLE P1: no silent amputation). Fingerprinted items
    # the model is culling are written to the forensic archive FIRST; the live
    # write only happens if the archive succeeded. Items already closed
    # (status in done/wont_fix) carry their own closure trail and are NOT
    # re-archived — archival is for items lost to a size-driven cull.
    dropped: List[Dict[str, Any]] = [
        it for it in fp_items
        if str(it.get("fingerprint") or "") not in seen_fp
        and str(it.get("status") or "open").lower() not in ("done", "wont_fix")
    ]
    if dropped:
        try:
            _append_groom_archive(archive_backlog_path(drive_root), dropped)
        except Exception:
            return 0  # FAIL CLOSED: a cull without an archive is the bug.

    final = kept_fp + nofp_items  # hand-added items always survive, unchanged
    with _locked_text_file(path, mode="r+") as fh:
        # Abort if the backlog changed during the lock-free LLM call (a concurrent
        # append/close/recurrence) — never overwrite a concurrent update. The next
        # post-task tick will groom the fresh state.
        if fh.read() != snapshot_text:
            return 0
        fh.seek(0)
        fh.write(_serialize_backlog(final))
        fh.truncate()
        fh.flush()
    _rebuild_index(path)
    return len(final)
