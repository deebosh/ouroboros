"""Semantic-overlap advisory layer for the managed-update merge engine (v6.103.0).

Purely ADDITIVE to ``update_merge.py`` / ``update_merge_policy.py`` — never
weakens or bypasses the hardened transactional apply/rollback/gating machinery
(BIBLE P3). Git's own line-based 3-way merge already decides whether an update
is clean or conflicting; this module answers a DIFFERENT question git cannot
see: "did a file touched by BOTH the local fork's own history AND the incoming
upstream range get changed for possibly the SAME underlying reason?" — the
case where upstream fixed something we already fixed differently, on
non-overlapping lines, and git would auto-merge cleanly with zero signal.

Two tiers, matching the existing ``update_merge.py`` split (module-size
discipline, and cost discipline: the cheap tier runs on every planning pass,
the LLM tier runs at most once or twice per update cycle):

  - ``compute_overlap_candidates`` — pure git, cheap, fail-soft, safe to call
    from ``plan_managed_update_merge`` (called up to 3-4x per update cycle).
  - ``detect_semantic_overlap`` — the one function that calls a model. Bounded,
    LIGHT-model, fail-soft (never raises), mirrors
    ``ouroboros/project_naming.py``'s LLM-call pattern. Called from exactly
    two sites: ``ouroboros/gateway/control.py::api_update_preflight`` (outside
    any writer fence) and, on a cache miss only,
    ``_start_assisted_merge_fenced`` (inside the fence, still bounded/fail-soft
    so it cannot become a new hang/failure mode there).

A single-slot durable cache (``write_semantic_overlap_cache`` /
``read_semantic_overlap_cache``) lets the fenced apply path reuse a result
already computed at preflight time instead of paying for the model call twice.
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Any, Dict, List, Optional

from ouroboros.utils import atomic_write_json, read_json_dict, utc_now_iso
from supervisor import git_ops as _g
from supervisor.update_merge import _git_run

log = logging.getLogger(__name__)

SEMANTIC_OVERLAP_CACHE_NAME = "ouroboros-update-semantic-cache.json"

_VERDICTS = frozenset({"likely_duplicate", "likely_superseded", "related_not_duplicate", "unclear"})

_MAX_DIFF_CHARS_PER_COMMIT = 4000
_MAX_PROMPT_CHARS = 200_000


# ---------------------------------------------------------------------------
# Tier 1: pure git, cheap, fail-soft — safe on the hot planning path.
# ---------------------------------------------------------------------------

def _diff_name_only(rev_range: str, *, cwd: Optional[str] = None) -> List[str]:
    rc, out, _err = _git_run(["git", "diff", "--name-only", rev_range], cwd=cwd)
    if rc != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _diffstat_order(rev_range: str, paths: List[str], *, cwd: Optional[str] = None) -> List[str]:
    """``paths`` reordered largest-changed-first per ``git diff --stat``; any
    path the stat parse misses keeps its original relative order at the end."""
    if not paths:
        return []
    rc, out, _err = _git_run(
        ["git", "diff", "--numstat", rev_range, "--", *paths], cwd=cwd,
    )
    if rc != 0 or not out:
        return list(paths)
    weight: Dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        try:
            weight[path.strip()] = int(added if added != "-" else 0) + int(removed if removed != "-" else 0)
        except ValueError:
            continue
    return sorted(paths, key=lambda p: weight.get(p, 0), reverse=True)


def compute_overlap_candidates(
    base_sha: str,
    target_sha: str,
    *,
    max_files: int = 40,
    max_commits_per_side: int = 25,
) -> Dict[str, Any]:
    """Files touched by BOTH the local-only history (``merge_base..base_sha``)
    and the incoming upstream range (``merge_base..target_sha``), each with the
    commits (sha + subject) from either side that touched it. This is what
    catches a semantic duplicate on NON-overlapping lines, which git's own
    conflict detection (``--diff-filter=U``) never sees at all.

    ``merge_base`` — not this fork's first-ever divergence point — is the
    right boundary: this fork already periodically merges upstream (e.g.
    commit 982cce8e), so everything before the merge-base for THIS cycle is
    already-reconciled common ancestry; using a fixed historical fork point
    would re-flag that reconciled history forever.

    Never raises. Any git failure yields ``{"files": [], ...}`` — same
    best-effort contract as the rest of the managed-update planning path.
    """
    try:
        rc, merge_base, _err = _git_run(["git", "merge-base", base_sha, target_sha])
        if rc != 0 or not merge_base:
            return {"merge_base_sha": "", "files": [], "truncated": False}

        local_range = f"{merge_base}..{base_sha}"
        upstream_range = f"{merge_base}..{target_sha}"
        local_files = set(_diff_name_only(local_range))
        upstream_files = set(_diff_name_only(upstream_range))
        overlap = sorted(local_files & upstream_files)
        if not overlap:
            return {"merge_base_sha": merge_base, "files": [], "truncated": False}

        truncated = len(overlap) > max_files
        ordered = _diffstat_order(f"{merge_base}..{base_sha}", overlap) if overlap else []
        # Cap AFTER ordering so the kept set is the largest-diffstat subset.
        kept = (ordered or overlap)[:max_files]
        kept_set = set(kept)

        files: List[Dict[str, Any]] = []
        for path in kept:
            if path not in kept_set:
                continue
            # Per-path touching commits (not filtered from a range-wide list in
            # Python — `git log -- <path>` is what correctly attributes only
            # the commits that actually touched THIS path).
            rc_l, out_l, _e = _git_run(["git", "log", "--no-merges", "--format=%H\x1f%s", local_range, "--", path])
            rc_u, out_u, _e2 = _git_run(["git", "log", "--no-merges", "--format=%H\x1f%s", upstream_range, "--", path])
            local_pairs = _parse_log_output(out_l) if rc_l == 0 else []
            upstream_pairs = _parse_log_output(out_u) if rc_u == 0 else []
            files.append({
                "path": path,
                "local_shas": [s for s, _ in local_pairs[:max_commits_per_side]],
                "local_subjects": {s: subj for s, subj in local_pairs[:max_commits_per_side]},
                "upstream_shas": [s for s, _ in upstream_pairs[:max_commits_per_side]],
                "upstream_subjects": {s: subj for s, subj in upstream_pairs[:max_commits_per_side]},
            })
        return {"merge_base_sha": merge_base, "files": files, "truncated": truncated}
    except Exception:
        log.debug("compute_overlap_candidates failed", exc_info=True)
        return {"merge_base_sha": "", "files": [], "truncated": False}


def _parse_log_output(out: str) -> List[tuple[str, str]]:
    rows: List[tuple[str, str]] = []
    for line in (out or "").splitlines():
        if "\x1f" not in line:
            continue
        sha, _, subject = line.partition("\x1f")
        sha = sha.strip()
        if sha:
            rows.append((sha, subject.strip()))
    return rows


# ---------------------------------------------------------------------------
# Tier 2: LLM-bearing, fail-soft, bounded, LIGHT model.
# ---------------------------------------------------------------------------

def _semantic_overlap_model() -> str:
    """The light slot, resolved to a credentialed provider — mirrors
    ``project_naming._light_naming_model()``."""
    from ouroboros.config import get_light_model
    from ouroboros.provider_models import resolve_credentialed_model

    return resolve_credentialed_model(get_light_model())


def _semantic_overlap_timeout_sec() -> float:
    from ouroboros.update_channels import get_update_semantic_timeout_sec

    return get_update_semantic_timeout_sec()


def _commit_diff_excerpt(sha: str, path: str, *, cap: int = _MAX_DIFF_CHARS_PER_COMMIT) -> str:
    rc, out, _err = _git_run(["git", "show", "--format=", sha, "--", path])
    if rc != 0 or not out:
        return ""
    if len(out) > cap:
        return out[:cap] + f"\n…[+{len(out) - cap} chars omitted]"
    return out


_OVERLAP_PROMPT_HEADER = (
    "You are reviewing a managed self-update for an autonomous agent's own code repository. "
    "Below are files touched BOTH by this fork's own local commit history and by the incoming "
    "upstream commit history, since they last shared a common ancestor. For EACH file, decide "
    "whether the local and upstream changes look like they address the SAME underlying issue "
    "(possibly via different approaches), or are unrelated/complementary changes that merely "
    "happen to touch the same file.\n\n"
    "Return a JSON array, one object per file that has REAL signal (omit files you have nothing "
    "to say about — an empty array is a valid answer meaning nothing was flagged). Each object:\n"
    '{"path": "<file path>", "local_shas": ["<sha>", ...], "upstream_shas": ["<sha>", ...], '
    '"verdict": "likely_duplicate"|"likely_superseded"|"related_not_duplicate"|"unclear", '
    '"note": "<=240 chars explaining why"}\n\n'
    "verdict meanings: likely_duplicate = both sides appear to fix the same bug/issue "
    "independently; likely_superseded = upstream's change appears to supersede/improve on the "
    "local one for the same issue; related_not_duplicate = same file, genuinely different "
    "reasons (not a duplicate — do not flag ordinary unrelated co-location); unclear = real "
    "signal but not confident enough to pick one of the above.\n\n"
    "Output ONLY the JSON array, nothing else.\n\n"
)


def build_semantic_overlap_prompt(candidates: Dict[str, Any], base_sha: str, target_sha: str) -> str:
    parts = [_OVERLAP_PROMPT_HEADER]
    budget = _MAX_PROMPT_CHARS - len(_OVERLAP_PROMPT_HEADER)
    for entry in candidates.get("files") or []:
        path = str(entry.get("path") or "")
        local_shas = list(entry.get("local_shas") or [])
        upstream_shas = list(entry.get("upstream_shas") or [])
        local_subjects = entry.get("local_subjects") or {}
        upstream_subjects = entry.get("upstream_subjects") or {}
        section_lines = [f"## {path}", "Local commits:"]
        for sha in local_shas:
            section_lines.append(f"- {sha[:12]} {local_subjects.get(sha, '')}")
            excerpt = _commit_diff_excerpt(sha, path)
            if excerpt:
                section_lines.append(f"```diff\n{excerpt}\n```")
        section_lines.append("Upstream commits:")
        for sha in upstream_shas:
            section_lines.append(f"- {sha[:12]} {upstream_subjects.get(sha, '')}")
            excerpt = _commit_diff_excerpt(sha, path)
            if excerpt:
                section_lines.append(f"```diff\n{excerpt}\n```")
        section = "\n".join(section_lines) + "\n\n"
        if len(section) > budget:
            # Lowest-priority (later, per the caller's diffstat ordering) files
            # drop first — a disclosed truncation, not a silent one: the model
            # is told explicitly fewer files are shown than were candidates.
            parts.append(
                f"[…{len(candidates.get('files') or []) - len(parts) + 1} more candidate file(s) "
                "omitted for prompt size…]\n"
            )
            break
        parts.append(section)
        budget -= len(section)
    return "".join(parts)


def _validate_overlap_row(row: Any) -> bool:
    return (
        isinstance(row, dict)
        and isinstance(row.get("path"), str)
        and row.get("path")
        and str(row.get("verdict") or "") in _VERDICTS
    )


def detect_semantic_overlap(
    base_sha: str,
    target_sha: str,
    candidates: Dict[str, Any],
    *,
    use_local: Optional[bool] = None,
) -> Dict[str, Any]:
    """The one function that calls a model. Fail-soft: any exception, timeout,
    or malformed response yields ``{"available": False, "flags": [], ...}`` —
    never raises. Short-circuits with no model call when there is nothing to
    ask about."""
    files = candidates.get("files") or []
    if not files:
        return {"available": True, "flags": [], "model": "", "computed_at": utc_now_iso()}
    try:
        from ouroboros import model_concurrency
        from ouroboros.llm import LLMClient
        from ouroboros.triad_review import extract_json_array
        from ouroboros.usage_accounting import UsageScope, usage_scope

        model = _semantic_overlap_model()
        prompt = build_semantic_overlap_prompt(candidates, base_sha, target_sha)
        local_use = use_local
        if local_use is None:
            import os

            local_use = str(os.environ.get("USE_LOCAL_LIGHT", "") or "").lower() in ("true", "1")

        scope = UsageScope(
            drive_root=_g.DRIVE_ROOT,
            task_id="update_semantic_overlap",
            root_task_id="update_semantic_overlap",
            category="update_semantic_overlap",
            source="update_semantic_overlap",
        )
        with model_concurrency.model_call_slot(model, local_use):
            with usage_scope(scope):
                client = LLMClient()
                msg, _usage = client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                    tools=None,
                    reasoning_effort="low",
                    max_tokens=4096,
                    use_local=local_use,
                    timeout=_semantic_overlap_timeout_sec(),
                )
        raw = str((msg or {}).get("content", "") or "")
        parsed = extract_json_array(raw, validate_fn=lambda rows: all(_validate_overlap_row(r) for r in rows))
        if parsed is None:
            return {"available": False, "error": "unparseable model output", "flags": []}
        flags = [row for row in parsed if _validate_overlap_row(row)]
        return {"available": True, "flags": flags, "model": model, "computed_at": utc_now_iso()}
    except Exception as exc:
        log.debug("detect_semantic_overlap failed", exc_info=True)
        return {"available": False, "error": f"{type(exc).__name__}: {exc}", "flags": []}


# ---------------------------------------------------------------------------
# Durable single-slot cache — imitates UPDATE_TX_MARKER_NAME's _git_dir() marker idiom.
# ---------------------------------------------------------------------------

def _semantic_overlap_cache_path() -> pathlib.Path:
    return _g._git_dir() / SEMANTIC_OVERLAP_CACHE_NAME


def write_semantic_overlap_cache(base_sha: str, target_sha: str, result: Dict[str, Any]) -> None:
    try:
        atomic_write_json(
            _semantic_overlap_cache_path(),
            {"base_sha": base_sha, "target_sha": target_sha, "result": result, "written_at": utc_now_iso()},
        )
    except Exception:
        log.debug("write_semantic_overlap_cache failed", exc_info=True)


def read_semantic_overlap_cache(base_sha: str, target_sha: str) -> Optional[Dict[str, Any]]:
    try:
        data = read_json_dict(_semantic_overlap_cache_path())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("base_sha") != base_sha or data.get("target_sha") != target_sha:
        return None
    result = data.get("result")
    return result if isinstance(result, dict) else None
