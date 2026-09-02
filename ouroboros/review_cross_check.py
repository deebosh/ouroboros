"""Reviewer-verdict cross-check: downgrade critical findings whose factual
claims cannot be substantiated against actual repo content (ibl-01b310c0ce18).

Extracted from ouroboros.review_execution so that module stays under the
size-ratchet giant threshold after the v6.110.0 merge.
"""

from __future__ import annotations

import os
import pathlib
import re
from typing import Any, Dict, List, Optional, Tuple


def _identifier_present_in_repo(identifier: str, repo_root: pathlib.Path) -> bool:
    """True if ``identifier`` appears in source files under ``repo_root``.

    Fast path (dotted-path module resolution): ``ouroboros.config`` →
    ``repo_root/ouroboros/config.py`` (or ``config/`` package). Catches both
    the real ouroboros.project_naming (file exists) and the hallucinated
    ``ouroborosproject_naming`` (no path matches because the dotted join
    fails the alnum check).

    Slow path (bounded substring walk): walks source files (.py, .js, .ts,
    .go, .rs, .rb, .java, .c, .cpp, .h, .hpp, .md, .rst, .txt), skips hidden
    and vendor/cache trees, caps files at 1MB. Used for tokens that are not
    module paths (CamelCase class names like ``ClaudexorUnavailable``).

    The walk is on the slow path by design: the cross-check runs once per
    reviewer verdict, on a repo that the host already owns — there is no
    exfiltration surface and no tenant boundary to defend. Errors default to
    "present" (return True) so a flaky walk never fabricates a downgrade.
    """
    if not identifier or len(identifier) < 3:
        return True  # trivially present, never claim hallucination on short tokens

    # Fail-closed tracker: any per-file I/O uncertainty (stat/open/exists
    # raising OSError on a file we wanted to inspect) is recorded here.
    # If the walk completes without finding the identifier AND we observed
    # at least one OSError, we cannot substantiate "absent" — the caller's
    # downgrade path must treat the identifier as present to avoid
    # fabricating a downgrade from incomplete evidence. Aligns the
    # per-file catch arms with the function docstring's "Errors default
    # to 'present'" contract.
    saw_any_oserror = False

    # Fast path 1: dotted-path module resolution. ``ouroboros.review_execution``
    # → ``repo_root/ouroboros/review_execution.py`` or
    # ``repo_root/ouroboros/review_execution/__init__.py``.
    if (
        "." in identifier
        and " " not in identifier
        and identifier.replace(".", "").replace("_", "").isalnum()
    ):
        try:
            parts = identifier.split(".")
            head = repo_root.joinpath(*parts[:-1])
            tail = parts[-1]
            for candidate in (
                head / f"{tail}.py",
                head / tail / "__init__.py",
                head / tail,
            ):
                try:
                    if candidate.exists():
                        return True
                except OSError:
                    saw_any_oserror = True
                    continue
        except (OSError, ValueError):
            pass

    # Slow path: bounded substring walk over source-ish files.
    SUSPECT_EXTS = (
        ".py", ".js", ".ts", ".tsx", ".go", ".rs", ".rb", ".java",
        ".c", ".cpp", ".h", ".hpp", ".md", ".rst", ".txt",
    )
    SKIP_DIRS = frozenset({
        "__pycache__", "node_modules", "venv", ".venv",
        "dist", "build", "target", "site-packages",
    })
    try:
        for dirpath, dirnames, filenames in os.walk(repo_root):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in SKIP_DIRS
            ]
            for fn in filenames:
                if not fn.endswith(SUSPECT_EXTS):
                    continue
                fp = pathlib.Path(dirpath) / fn
                try:
                    if fp.stat().st_size > 1_000_000:
                        continue
                except OSError:
                    saw_any_oserror = True
                    continue
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                        if identifier in fh.read():
                            return True
                except OSError:
                    saw_any_oserror = True
                    continue
    except OSError:
        return True  # walk failed: default to "present" to avoid false downgrade
    return saw_any_oserror


# Patterns used to extract code-shaped claims from a finding's reason/item.
# Order matters: backticks first (the official code-quoting signal), then
# dotted-path candidates, then CamelCase class names. The CamelCase pass is
# the catch-all that catches the ``ClaudexorUnavailable`` hallucination
# (``Claudexor.unavailable`` is split by the dot, both halves are CamelCase;
# ``ClaudexorUnavailable`` with the dot removed is one CamelCase token).
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_DOTTED_PATH_RE = re.compile(r"\b([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*){1,})\b")
_CAMEL_CASE_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]{2,})\b")

# "Imports", "uses", "typo", "missing", "should be" — phrases whose presence
# turns a vague critical into one that names a verifiable fact. The
# cross-check stays conservative: a critical that just says "FAIL" with no
# verifiable identifiers is LEFT ALONE; we never widen the downgrade scope
# beyond what the reviewer asserted.
_STRONG_CLAIM_RE = re.compile(
    r"\b(imports?|uses?\b|referenc(?:es?|ing)\b|missing|typo|"
    r"does\s+not\s+exist|doesn['\u2019]t\s+exist|"
    r"should\s+be|expected\s+to|supposed\s+to|"
    r"cannot\s+be\s+found|cannot\s+find|"
    r"undefined|undeclared|unresolved|"
    r"calls?\b|invokes?\b|invocation\s+of)\b",
    re.IGNORECASE,
)

# Tokens we never treat as verifiable code identifiers (common prose).
_NON_CODE_TOKENS = frozenset({
    "PASS", "FAIL", "OK", "TODO", "FIXME", "XXX", "NULL", "NONE",
    "PYTHON", "JSON", "YAML", "TOML", "MARKDOWN", "BASH", "SHELL",
    "GIT", "URL", "URI", "API", "CLI", "ENV", "PATH", "REPO", "FILE",
    "I", "A", "AN", "THE", "THIS", "THAT", "IT", "ITS",
    "AND", "OR", "NOT", "BUT", "FOR", "OF", "ON", "TO", "IN", "IS",
})


def _cross_check_findings(
    findings: List[Dict[str, Any]],
    repo_root: Optional[pathlib.Path] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Verify factual claims in critical findings against actual repo content.

    For each finding whose ``severity`` is ``"critical"``: extract code-shape
    identifiers from its ``reason`` and ``item`` text, then verify each
    identifier exists somewhere in ``repo_root``. If EVERY mentioned
    identifier is absent AND the reason carries a strong claim (imports,
    uses, missing, typo, should-be, …), downgrade ``severity`` to
    ``"advisory"`` and append a note to ``reason`` so the caller's gate
    sees it but the audit trail stays whole.

    The cross-check is conservative by design:
      - It only intervenes when concrete identifiers can be extracted.
      - It only downgrades when ALL mentioned identifiers are absent.
      - Vague or prose findings without verifiable identifiers are
        passed through unchanged.
      - It is OFF when ``repo_root`` is None — existing call sites see no
        behavioral change unless they opt in.

    Returns ``(findings, audit)``. ``audit`` records every cross-check
    decision so a downgraded finding is auditable end-to-end. The audit
    dict shape is stable: ``checked`` (int), ``downgraded`` (int),
    ``kept`` (int), ``entries`` (list of per-finding dicts).
    """
    audit: Dict[str, Any] = {
        "checked": 0,
        "downgraded": 0,
        "kept": 0,
        "entries": [],
    }
    if repo_root is None or not findings:
        return list(findings), audit
    try:
        repo_root_resolved = pathlib.Path(repo_root).resolve()
    except (OSError, TypeError, ValueError):
        return list(findings), audit
    if not (repo_root_resolved.exists() and repo_root_resolved.is_dir()):
        return list(findings), audit

    out: List[Dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            out.append(finding)
            continue
        if str(finding.get("severity", "")).lower() != "critical":
            out.append(finding)
            continue

        audit["checked"] += 1
        reason = str(finding.get("reason", "") or "")
        item = str(finding.get("item", "") or "")
        haystack = f"{reason}\n{item}"

        candidates: List[str] = []
        seen: set = set()
        # Process patterns in length-descending order so the longest
        # identifier wins; shorter sub-tokens (e.g., the ``Claudexor`` piece
        # of ``Claudexor.unavailable``) are then skipped as substrings of
        # something already accepted. This avoids two-name "comparative"
        # patterns (Compare ``A`` with ``B``) where the reviewer is anchoring
        # to one real identifier and mentioning another; we still treat that
        # as a separate claim and let the all-absent rule keep it critical.
        ordered_patterns = (
            list(_BACKTICK_RE.finditer(haystack))
            + list(_DOTTED_PATH_RE.finditer(haystack))
            + list(_CAMEL_CASE_RE.finditer(haystack))
        )
        matches_by_length = sorted(
            ordered_patterns, key=lambda m: len(m.group(1)), reverse=True,
        )
        for m in matches_by_length:
            cand = m.group(1).strip().rstrip(".,;:")
            if not cand or cand in _NON_CODE_TOKENS or cand in seen:
                continue
            # Skip if ``cand`` is a strict substring of an already-accepted
            # candidate (``Claudexor.unavailable`` already in; ``Claudexor``
            # alone is just a prefix of that and should not count as a
            # separate claim).
            if any(cand in existing and existing != cand for existing in seen):
                continue
            seen.add(cand)
            candidates.append(cand)
        if not candidates:
            audit["kept"] += 1
            out.append(finding)
            continue

        absent = [
            c for c in candidates
            if not _identifier_present_in_repo(c, repo_root_resolved)
        ]
        makes_strong_claim = bool(_STRONG_CLAIM_RE.search(haystack))

        if absent and len(absent) == len(candidates) and makes_strong_claim:
            new_finding = dict(finding)
            new_finding["severity"] = "advisory"
            note = (
                f"[cross-check] Downgraded from critical to advisory by "
                f"review_execution._cross_check_findings: identifier(s) "
                f"{absent!r} not located in repo; the reviewer's factual "
                f"claim cannot be substantiated against the codebase. "
                f"Re-raise as critical only after the identifier is verified "
                f"against actual code."
            )
            base_reason = new_finding.get("reason", "") or ""
            new_finding["reason"] = (
                f"{base_reason}\n\n{note}" if base_reason else note
            )
            audit["downgraded"] += 1
            audit["entries"].append({
                "item": item,
                "absent_identifiers": absent,
                "all_candidates": candidates,
                "action": "downgraded_to_advisory",
            })
            out.append(new_finding)
            continue

        audit["kept"] += 1
        out.append(finding)
    return out, audit

