"""Presentation-only labels and text for managed-update conflicts.

Git decides whether a merge is clean. Every conflict, regardless of pathname,
goes through the same reviewed assisted resolver. The doc/code/hot split only
helps the resolver and UI describe the plan; it grants or blocks nothing.
``assisted_objective`` renders the resolver task's objective text — presentation
only as well; the authority lives in the tx marker and its fingerprint.
"""

from __future__ import annotations

import posixpath
from typing import Any, Dict, List

DOCUMENT_EXACT = frozenset({"README.md"})
DOCUMENT_PREFIXES = ("docs/",)
HOT_CODE_PATHS = frozenset({
    "ouroboros/loop.py",
    "ouroboros/tools/control.py",
    "ouroboros/tools/registry.py",
    "ouroboros/config.py",
    "supervisor/queue.py",
    "supervisor/events.py",
})


def _norm(path: str) -> str:
    normalized = posixpath.normpath(str(path or "").replace("\\", "/"))
    return normalized[2:] if normalized.startswith("./") else normalized.lstrip("/")


def is_document_path(path: str) -> bool:
    p = _norm(path)
    if p in DOCUMENT_EXACT or posixpath.basename(p).upper().startswith("CHANGELOG"):
        return True
    return p.endswith(".md") and any(p.startswith(prefix) for prefix in DOCUMENT_PREFIXES)


def is_hot_code(path: str) -> bool:
    return _norm(path) in HOT_CODE_PATHS


def classify_conflicts(conflict_paths: List[str]) -> Dict[str, object]:
    """Return one route plus presentation labels; filenames never set policy."""
    paths = [str(path).strip() for path in (conflict_paths or []) if str(path).strip()]
    docs = [path for path in paths if is_document_path(path)]
    code = [path for path in paths if path not in docs]
    return {
        "kind": "conflicting" if paths else "clean",
        "doc_conflict_paths": docs,
        "code_conflict_paths": code,
        "hot_code_paths": [path for path in code if is_hot_code(path)],
    }


def rescue_pointer_note(tx: Dict[str, Any]) -> str:
    """One plain sentence pointing the resolver at rescued uncommitted work.

    Reads the latest rescue pointer (``progress_rescue``, falling back to
    ``rollback_rescue``); when several rescues were taken, only the latest is
    named plus a count — no history rendering. Returns "" when there is nothing
    to point at."""
    pointer = tx.get("progress_rescue") or tx.get("rollback_rescue")
    if not isinstance(pointer, dict) or not pointer.get("path"):
        return ""
    count = int(pointer.get("count") or 1)
    tally = f" ({count} rescues were taken; this is the latest)" if count > 1 else ""
    return (
        f" A previous attempt's uncommitted work was rescued to {pointer['path']}{tally}; "
        "changes.diff there is a plain diff against the reviewed base. Read the rescued "
        "files to re-apply prior resolutions — do not run git commands."
    )


def semantic_overlap_note(tx: Dict[str, Any]) -> str:
    """Advisory note flagging local/upstream commits that touched the same
    file for possibly-related reasons. "" when nothing was flagged."""
    flags = list(tx.get("semantic_overlap_flags") or [])
    relevant = [f for f in flags if str(f.get("verdict") or "") != "related_not_duplicate"]
    if not relevant:
        return ""
    lines = []
    for f in relevant[:8]:
        local = ", ".join(str(s)[:12] for s in (f.get("local_shas") or [])[:3])
        upstream = ", ".join(str(s)[:12] for s in (f.get("upstream_shas") or [])[:3])
        lines.append(
            f"- {f.get('path','')}: local commit(s) {local} may already address the same "
            f"issue upstream commit(s) {upstream} touch ({f.get('verdict','unclear')}). {f.get('note','')}"
        )
    return (
        "\n\nA semantic-overlap pre-check (advisory, not authoritative) flagged file(s) "
        "touched by BOTH your local history and this update's upstream history for "
        "possibly-related reasons — check whether upstream's approach should win, be "
        "merged with the local fix, or be intentionally dropped, rather than blindly "
        "reconciling text:\n" + "\n".join(lines)
    )


def assisted_objective(tx: Dict[str, Any]) -> str:
    """Objective text for the single authorized assisted-resolution task."""
    target = str(tx.get("target_sha") or "")[:12]
    conflicts = list(tx.get("conflict_paths") or [])
    if conflicts:
        work = (
            f"Resolve each conflicting file ({', '.join(conflicts)}), preserve both intents "
            "where possible, and remove every conflict marker (<<<<<<<, =======, >>>>>>>)."
        )
    else:
        work = (
            "The merge itself is clean, but it combines local and official history and therefore "
            "requires review. Inspect the staged combination and correct it if needed."
        )
    return (
        f"A managed Ouroboros update (target {target}) has been merged into your working tree by the "
        "supervisor: MERGE_HEAD is set and the combined tree is staged for review. Do NOT run any git "
        "command (fetch/merge/commit/checkout are blocked) — the merge is already staged for you. "
        f"{work} Do not discard either side merely because a file is normally restricted. When ready, "
        "run `advisory_review` with the commit message, then `commit_reviewed` (it will create the reviewed "
        "2-parent merge commit), then `request_restart` to finish landing the update."
        f"{rescue_pointer_note(tx)}"
        f"{semantic_overlap_note(tx)}"
    )
