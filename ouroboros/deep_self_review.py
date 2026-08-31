"""Atlas-backed deep self-review against BIBLE.md using a large-context model."""

from __future__ import annotations

import logging
import os
import pathlib
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

# Pack filtering is shared with scope review.
from ouroboros.tools.review_context_atlas import (  # noqa: E402
    ReviewContextAtlasRequest,
    atlas_assembly_failed,
    atlas_assembly_failure_reason,
    compile_review_context_atlas,
)
from ouroboros.tools.review_helpers import (  # noqa: E402
    _MAX_FULL_REPO_FILE_BYTES,
    REVIEW_PROMPT_TOKEN_BUDGET,
    calibrated_input_token_limit,
)
from ouroboros.utils import atomic_write_json, estimate_tokens, utc_now_iso  # noqa: E402
from ouroboros.config import get_context_mode, get_deep_self_review_model, resolve_effort  # noqa: E402
from ouroboros.provider_models import provider_for_model, provider_has_credentials  # noqa: E402
from ouroboros.context_layout import generate_doc_nav_map  # noqa: E402

# Non-agent visual assets.
_SKIP_DIR_PREFIXES = (
    "assets/",
)

# Output reservation inside the reviewer's 1M window (same class of fix as
# scope_review._SCOPE_INPUT_TOKEN_LIMIT): 920K input + 100K output exceeds 1M
# and yields a deterministic provider 400, so the assembled INPUT prompt is
# gated on min(SSOT budget, window − output − tokenizer margin).
#
# v6.103.8 — cap lowered from 100_000 → 10_000. The 100k default was the rot
# captured by ibl-be9ba2d99b25: a single deep-review call burned $11.00 on
# task c7862982 because OpenRouter's account-level credit pool 402'd at
# "you can only afford 56 tokens of 100000 requested" — the entire spend was
# the failed request itself, not the review work. 10k covers any honest
# deep-review output (the reviewer's prose section is bounded by the atlas
# itself; only the findings enumeration needs slack, and 10k is ~7x what
# the largest historical review used). The cap is provider-agnostic — direct
# google_genai::gemini-3.7-flash now reaches the same review at a sane budget
# without a per-provider special case. If a future review genuinely needs more
# output, raise it WITH a backpressure rationale, not by accident.
_DEEP_MAX_OUTPUT_TOKENS = 10_000
_DEEP_MODEL_CONTEXT_WINDOW = 1_000_000
_DEEP_OUTPUT_MARGIN_TOKENS = 155_000
_DEEP_INPUT_TOKEN_LIMIT = min(
    REVIEW_PROMPT_TOKEN_BUDGET,
    _DEEP_MODEL_CONTEXT_WINDOW - _DEEP_MAX_OUTPUT_TOKENS - _DEEP_OUTPUT_MARGIN_TOKENS,
)

# Pack integrity floor (Gate A). A pathologically small pack invites hallucination
# structurally: the model has too little context to write a project-wide review.
# The char floor (~12K tokens) sits above what one or two files can produce; the
# file-count floor separates "real project atlas" from "memory-only stub"; the
# memory floor catches "atlas without the agent's own memory".
_DEEP_MIN_PACK_CHARS = 50_000
_DEEP_MIN_FILE_COUNT = 5
_DEEP_MIN_MEMORY_FILES = 3

# Response grounding floor (Gate B). The model's review must reference at least
# N distinct paths that exist in the assembled pack, so hallucinated prose
# cannot be presented as authoritative. URL-stripping pre-pass prevents a
# fabricated URL from grounding via its leaf path component.
_DEEP_MIN_PATH_REFS = 3
# Gate B corrective retry budget (closes ibl-8095de135be5). When the first
# response grounds fewer than _DEEP_MIN_PATH_REFS pack paths, the NON-CHUNKED
# Gate B appends a corrective nudge (with the actual grounded count + ~8
# verbatim pack paths the model can COPY) and re-issues the same chat_observed
# call. Bounded to ONE retry so this stays a rescue, not a retry loop — the
# expensive generation is no longer thrown away on a citation shortfall, but
# we still fail-closed if the corrective retry also falls short. Folded usage
# is the sum of both calls. Chunk (~813) and synthesis (~886) Gate B sites
# are LEAVE-UNCHANGED this pass (scope creep risk; follow-ups to land in a
# separate plan-review wave).
_DEEP_GATE_B_RETRIES = 1
_PACK_PATH_EXTS = ("py", "md", "js", "ts", "json", "yaml", "yml", "toml", "sh", "bash", "sql")
_URL_RE = re.compile(r"https?://\S+")
_PATH_REF_RE = re.compile(
    r"(?:^|(?<![A-Za-z0-9]))([A-Za-z0-9_./-]+\.(?:"
    + "|".join(_PACK_PATH_EXTS)
    + r"))"
)

_MEMORY_WHITELIST = [
    "memory/identity.md",
    "memory/scratchpad.md",
    "memory/registry.md",
    "memory/WORLD.md",
    "memory/knowledge/index-full.md",
    "memory/knowledge/patterns.md",
    "memory/knowledge/improvement-backlog.md",
]

# The omission section is appended to the pack AFTER the atlas has filled its
# budget, so it must be (a) bounded and (b) reserved inside atlas_fixed_tokens.
# An unbounded per-file listing here is exactly what historically overflowed the
# assembled prompt past the final gate by a few hundred tokens (the atlas filled
# to its ceiling, then the uncounted omission listing was appended on top).
_OMISSION_SECTION_RESERVE_TOKENS = 2_000
_OMISSION_SAMPLE_MAX_ENTRIES = 40

# Bonus scale for graph-centrality ranking (D2). Bounded well below the atlas's
# force/anchor/canonical tiers (10000/9000/8000) so protected and governance
# surfaces always outrank a merely well-connected module; meaningfully above the
# generic path-prefix bonuses (~200) so hub modules win among peers.
_CENTRALITY_MAX_BONUS = 600.0
_CENTRALITY_PER_IMPORTER = 30.0

# ----------------------------------------------------------------------
# Chunked pipeline (v6.109.x — closes ibl-deep-self-review-large-context-truncation,
# Modes 1+2 only; Mode 3 — required-artifact over budget — is structurally
# distinct and queued for a separate plan-review wave via knowledge topic).
#
# Per BIBLE P3 we never lower the reviewer floor: when a single assembled
# pack overshoots even the slim 1M-context reviewer after the deterministic
# final-shrink retry, split the review across N chunks. Each chunk carries
# the full system prompt + memory whitelist + a SLICE of the atlas
# (≥ _DEEP_CHUNK_MIN_FILE_COUNT files), runs per-chunk Gate B against its
# own slice paths, and a synthesis pass re-grounds the verdict against the
# UNION of all chunk paths (aggregate Gate B). Per-chunk Gate A applies the
# same structural integrity floors as the whole-pack Gate A — chunks that
# can't meet it return a typed refusal rather than proceeding, because a
# chunk too small to ground against is structurally incapable of producing
# authoritative findings.
#
# Memory whitelist is shared (full) across all chunks — duplicating the
# memory parts is intentional and cheap (~2K tokens/chunk); the alternative
# (splitting memory across chunks) would break Gate B's path grounding for
# identity / scratchpad / patterns references inside a chunk that doesn't
# contain that memory file.
_DEEP_CHUNK_MIN_FILE_COUNT = 5
_DEEP_CHUNK_MIN_CHARS = 50_000
_DEEP_CHUNK_MAX_FILES_PER_CHUNK = 30
_DEEP_MAX_CHUNKS = 8
# Per-chunk input budget — leaves ~1M window for the configured reviewer
# (after output reserve + tokenizer margin) at _DEEP_CHUNK_TOKEN_BUDGET;
# a chunked path is only dispatched when the WHOLE pack overshoots, so
# the chunk budget MUST stay below the reviewer's real window.
_DEEP_CHUNK_TOKEN_BUDGET = 200_000

_SYSTEM_PROMPT = f"""\
You are conducting a deep self-review of the Ouroboros project — a self-creating AI agent.

Primary directive: The Constitution (BIBLE.md) is your absolute reference.
Every finding must be checked against it.

What to look for: bugs, crashes, race conditions,
BIBLE.md violations (P0–P12), contradictions between code and docs,
security gaps, dead code, missing error handling, architectural issues,
known error patterns from patterns.md that remain unfixed, and ideas how to improve Ouroboros to work better and better comply with the Bible.

How to work: Use the generated atlas coverage manifest systematically. Raw code is
included for selected functional/protected surfaces; every tracked file is still
accounted for by hash, size, classification, and omission/manifest disposition.
Cross-reference interactions between modules. Prioritize: CRITICAL > IMPORTANT > ADVISORY.

Output: Structured report with prioritized findings, each citing the
specific file, line/section, the problem, and the proposed fix.

Citation requirement: cite at least {_DEEP_MIN_PATH_REFS} distinct real file paths
from the provided pack, quoted verbatim as they appear (e.g. ``ouroboros/loop.py``);
a review citing fewer will be rejected unpublished."""


def _dulwich_tracked_paths(repo_dir: pathlib.Path) -> tuple[list[str], list[str]]:
    """Return git-tracked paths through dulwich for macOS fork safety."""
    try:
        import dulwich.repo as _dulwich_repo  # local import — avoid top-level cost if unused
        _repo = _dulwich_repo.Repo(str(repo_dir))
        tracked = sorted(p.decode("utf-8", errors="replace") for p in _repo.open_index())
        if not tracked:
            raise RuntimeError("dulwich index is empty — cannot build review pack")
        return tracked, []
    except ImportError:
        return [], ["FATAL: dulwich not installed. Run: pip install dulwich"]
    except Exception as exc:
        return [], [f"FATAL: {exc}"]


def _append_memory_whitelist(
    parts: list[str],
    skipped: list[str],
    *,
    drive_root: pathlib.Path,
) -> int:
    file_count = 0
    for rel_mem in _MEMORY_WHITELIST:
        full_path = drive_root / rel_mem
        try:
            if not full_path.is_file():
                continue
            size = full_path.stat().st_size
            if size > _MAX_FULL_REPO_FILE_BYTES:
                skipped.append(f"drive/{rel_mem} (>{_MAX_FULL_REPO_FILE_BYTES // 1024}KB)")
                continue
            content = full_path.read_text(encoding="utf-8", errors="replace")
            if not content.strip():
                continue
            parts.append(f"## FILE: drive/{rel_mem}\n{content}\n")
            file_count += 1
        except Exception as exc:
            skipped.append(f"drive/{rel_mem} (read error: {exc})")
    return file_count


def _append_omission_section(parts: list[str], skipped: list[str]) -> None:
    """Append a BOUNDED omission summary: counts per reason + a capped sample.

    Full per-file coverage (hash, size, disposition, reason for every tracked
    path) already lives in the atlas coverage manifest persisted to
    ``state/deep_self_review_context.json`` — this in-prompt section is a
    summary with an explicit pointer, not the coverage SSOT. Its size is
    reserved via ``_OMISSION_SECTION_RESERVE_TOKENS`` in ``atlas_fixed_tokens``
    and enforced here, so the assembled prompt provably fits the gate the
    atlas budgeted for. The cap is an explicit, visible summarization with an
    omission note — not silent truncation.
    """
    if not skipped:
        return
    counts: dict[str, int] = {}
    for entry in skipped:
        tag = entry.split("(", 1)[1].split(":", 1)[0].strip() if "(" in entry else "other"
        counts[tag] = counts.get(tag, 0) + 1
    header = [
        "## OMITTED FILES (not included in review pack)",
        "Reasons: sensitive=secrets/keys, vendored/minified=third-party bundled asset, "
        "binary/media=images/fonts/compiled blobs, excluded_dir=non-agent-logic directory, "
        "excluded_test=wider tests excluded, oversized=>1MB, read_error=unreadable. "
        "(A required atlas file that does not fit never reaches this list: it fails "
        "the pack instead of shrinking it.)",
        "Full per-file coverage for every tracked path is in the atlas coverage "
        "manifest (persisted to state/deep_self_review_context.json).",
        "",
        "Omitted counts by reason: "
        + ", ".join(f"{tag}={count}" for tag, count in sorted(counts.items())),
        "",
    ]
    sample = skipped[:_OMISSION_SAMPLE_MAX_ENTRIES]
    lines = header + [f"Sample ({len(sample)} of {len(skipped)} entries):"]
    lines.extend(f"  - {entry}" for entry in sample)
    if len(skipped) > len(sample):
        lines.append(
            f"  - … {len(skipped) - len(sample)} more entries omitted here "
            "(complete list in the coverage manifest)"
        )
    section = "\n".join(lines) + "\n"
    # Defensive hard bound: pathological entry lengths must never exceed the
    # reserve the atlas budgeted for. Trim sample rows (never the header) with a
    # visible note until the section fits.
    while estimate_tokens(section) > _OMISSION_SECTION_RESERVE_TOKENS and sample:
        sample = sample[: max(0, len(sample) - 5)]
        lines = header + [f"Sample ({len(sample)} of {len(skipped)} entries):"]
        lines.extend(f"  - {entry}" for entry in sample)
        lines.append(
            f"  - … {len(skipped) - len(sample)} more entries omitted here to fit "
            "the reserved omission budget (complete list in the coverage manifest)"
        )
        section = "\n".join(lines) + "\n"
    parts.append(section)


def _compute_graph_centrality(
    repo_dir: pathlib.Path,
    drive_root: pathlib.Path,
) -> Dict[str, float]:
    """Per-path centrality bonus from the code-intelligence import graph.

    Reverse-import in-degree over ``resolved_import_paths``: a module imported
    by many others is structurally load-bearing and the most useful raw code to
    inline in a bounded full-repo pack. Returns a bounded score bonus per
    rel_path; empty dict on any failure (ranking then falls back to the atlas's
    existing path/size heuristics — selection still works, just less informed).
    Deep-review-only: scope/plan review never pass centrality to the atlas.
    """
    try:
        from ouroboros.code_intelligence import build_code_inventory

        inventory = build_code_inventory(repo_dir, drive_root=drive_root, persist=True)
        in_degree: Dict[str, int] = {}
        for file in inventory.files:
            for target in file.resolved_import_paths or ():
                if target and target != file.path:
                    in_degree[target] = in_degree.get(target, 0) + 1
        return {
            path: min(_CENTRALITY_MAX_BONUS, count * _CENTRALITY_PER_IMPORTER)
            for path, count in in_degree.items()
            if count > 0
        }
    except Exception:
        # Keep the documented "empty dict on ANY failure" contract: inventory
        # shape drift must degrade to heuristic ranking, not kill the review.
        log.debug("Graph centrality unavailable; using heuristic ranking", exc_info=True)
        return {}


def build_review_pack(
    repo_dir: pathlib.Path,
    drive_root: pathlib.Path,
    fixed_prompt_tokens: int = 0,
    hard_budget_reduction: int = 0,
    input_token_limit: int = 0,
) -> Tuple[str, Dict[str, Any]]:
    """Build bounded repo atlas + full memory whitelist pack.

    ``hard_budget_reduction`` lowers the budgets handed to the atlas — used by
    the final-shrink retry in ``run_deep_self_review`` when estimator drift
    between the atlas's per-section accounting and the final concatenation
    pushes the assembled prompt over the input gate. ``input_token_limit``
    overrides the default GPT-family cap with the model-family-calibrated cap
    resolved by the caller (Claude-family reviewers need a smaller estimated
    budget for the same 1M window — see review_helpers).
    """
    tracked, fatal = _dulwich_tracked_paths(repo_dir)
    if fatal:
        return "", {"file_count": 0, "total_chars": 0, "skipped": fatal}

    skipped: list[str] = []
    memory_parts: list[str] = []
    memory_count = _append_memory_whitelist(memory_parts, skipped, drive_root=drive_root)
    memory_text = "\n".join(memory_parts)

    # Low context mode: render ARCHITECTURE.md as a navigation map (full sections
    # read on demand) and exclude it from the atlas full-file selection instead of
    # inlining ~32K tokens. Reuses the atlas ``already_included`` mechanism so the
    # shared commit-gate atlas (scope / plan review) is unaffected.
    nav_parts: list[str] = []
    already_included: frozenset[str] = frozenset()
    if get_context_mode() == "low":
        try:
            arch_text = (repo_dir / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        except Exception:
            arch_text = ""
        if arch_text.strip():
            nav_parts.append(
                generate_doc_nav_map(
                    arch_text, title="ARCHITECTURE.md", rel_path="docs/ARCHITECTURE.md"
                )
                + "\n\nNote for this deep self-review call: this surface has no tool loop, "
                "so the navigation map is an index of omitted sections, not an actionable "
                "read_file instruction. Flag any needed full ARCHITECTURE.md section explicitly."
            )
            already_included = frozenset({"docs/ARCHITECTURE.md"})

    # Reserve the (bounded) omission section inside the atlas's fixed budget —
    # it is appended to the pack after the atlas fills, so an unreserved section
    # arithmetically guarantees overflow whenever the atlas reaches its ceiling.
    atlas_fixed_tokens = (
        int(fixed_prompt_tokens)
        + estimate_tokens(memory_text)
        + estimate_tokens("\n".join(nav_parts))
        + _OMISSION_SECTION_RESERVE_TOKENS
    )
    effective_limit = int(input_token_limit) or _DEEP_INPUT_TOKEN_LIMIT
    hard_budget = max(10_000, effective_limit - max(0, int(hard_budget_reduction)))
    centrality = _compute_graph_centrality(repo_dir, drive_root)

    def _compile(compact: bool):
        return compile_review_context_atlas(
            ReviewContextAtlasRequest(
                repo_dir=repo_dir,
                tracked_paths=tuple(tracked),
                already_included=already_included,
                fixed_prompt_tokens=atlas_fixed_tokens,
                target_total_tokens=min(850_000, hard_budget),
                hard_total_tokens=hard_budget,
                include_tests=False,
                title="Generated Deep Self-Review Atlas",
                compact_manifest=compact,
                centrality_scores=centrality,
            )
        )

    atlas = _compile(False)
    if atlas_assembly_failed(atlas):
        # Graceful compact retry (mirrors scope review): the durable manifest
        # keeps full per-file coverage while the visible prompt switches to the
        # compact coverage index, freeing manifest tokens for required files.
        atlas = _compile(True)
    if atlas_assembly_failed(atlas):
        # No pack: a review that could not assemble a required artifact does not
        # run on the remainder (BIBLE P3). The manifest carries the disclosure.
        return "", {
            "file_count": 0,
            "total_chars": 0,
            "skipped": [
                "FATAL: "
                + atlas_assembly_failure_reason(atlas)
                + " (even with the compact manifest)"
            ],
            "context_manifest": atlas.manifest,
        }
    skipped.extend(
        f"{record.rel_path} ({record.disposition}: {record.reason})"
        for record in atlas.omitted
        if record.disposition not in {"already_included", "manifest_only"}
    )
    parts = [atlas.text]
    parts.extend(nav_parts)
    parts.extend(memory_parts)
    file_count = len(atlas.selected) + memory_count
    _append_omission_section(parts, skipped)

    pack_text = "\n".join(parts)
    stats = {
        "file_count": file_count,
        "memory_count": memory_count,
        "total_chars": len(pack_text),
        "skipped": skipped,
        "context_manifest": atlas.manifest,
    }
    return pack_text, stats


def _coerce_path_strings(section) -> List[str]:
    """Extract ``rel_path`` strings from one manifest section.

    Defensive against both serialized-dict rows (the persisted form at
    ``deep_self_review_context.json``) and in-memory ``PathRecord`` dataclass
    rows (the atlas's natural form). ``getattr`` fallback returns ``None``
    for unknown shapes, never raises — caller filters with ``if rel:``.
    """
    out: List[str] = []
    for row in (section or []):
        if isinstance(row, dict):
            rel = row.get("rel_path")
        else:
            rel = getattr(row, "rel_path", None)
        if rel:
            out.append(rel)
    return out


def _ground_response_in_pack(text: str, manifest: Optional[dict]) -> Set[str]:
    """Return distinct pack-relative paths that the response text references.

    URL-shaped substrings are stripped BEFORE path extraction so a fabricated
    URL cannot ground via its leaf path component (e.g. an agent quoting
    ``https://example.com/ouroboros/deep_self_review.py`` while only the
    leaf ``ouroboros/deep_self_review.py`` is in the pack must NOT count as a
    grounded reference). The pack path set is ``atlas.selected ∪ atlas.omitted
    ∪ _MEMORY_WHITELIST`` — memory files appear in the user message but not in
    the atlas manifest, so adding them is required for any response that
    grounds in identity / scratchpad / patterns.
    """
    pack_paths: Set[str] = set()
    if manifest:
        for section_name in ("selected", "omitted"):
            for rel in _coerce_path_strings(manifest.get(section_name)):
                pack_paths.add(rel)
    for rel in _MEMORY_WHITELIST:
        pack_paths.add(rel)
    if not pack_paths:
        return set()  # empty manifest → fail-closed below
    cleaned = _URL_RE.sub(" ", text)
    refs: Set[str] = set()
    for match in _PATH_REF_RE.finditer(cleaned):
        candidate = match.group(1)
        if candidate in pack_paths:
            refs.add(candidate)
            continue
        # Try matching as basename or suffix of any pack path — the response
        # often references ``deep_self_review.py`` rather than the full
        # ``ouroboros/deep_self_review.py`` path, and both should count.
        for pack_path in pack_paths:
            if pack_path.endswith("/" + candidate) or pack_path == candidate:
                refs.add(pack_path)
                break
    return refs


def is_review_available() -> Tuple[bool, Optional[str]]:
    """Return whether a suitable large-context review model is configured.

    Provider/credential knowledge comes from the provider registry SSOT; the
    one deliberate deep-review-specific rule kept here: ``openai::`` is only
    trusted when ``OPENAI_BASE_URL`` is unset (a redirected endpoint cannot be
    assumed to honor the 1M-context contract this review depends on).
    """
    configured = get_deep_self_review_model()
    provider = provider_for_model(configured)
    if provider == "openai":
        if provider_has_credentials("openai") and not os.environ.get("OPENAI_BASE_URL"):
            return True, configured
        return False, None
    if configured.startswith("openai/"):
        # OpenRouter route with a direct-OpenAI rewrite fallback.
        # Prefer direct-OpenAI when available (no OPENAI_BASE_URL) — an
        # OpenRouter quota-billing path must NOT shadow a working direct route,
        # and the openrouter credit cascade is silent at the provider level
        # (closes ibl-ad4731a2f03e: discrimination bug that picked an
        # openrouter route when openai direct would have worked).
        if provider_has_credentials("openai") and not os.environ.get("OPENAI_BASE_URL"):
            slug = configured.split("/", 1)[1]
            if slug.endswith("-pro"):
                # A `-pro` suffix is an OpenRouter ROUTING slug (reasoning
                # mode), not an OpenAI model id — `gpt-5.6-sol-pro` 404s on
                # api.openai.com (live-probed 2026-07-29). Only for these does
                # the direct route's own default take over; an owner's explicit
                # pin of a REAL model keeps the mechanical rewrite below, so a
                # pinned openai/gpt-5.5 still runs deep review on gpt-5.5.
                from ouroboros.provider_models import OPENAI_DIRECT_DEFAULTS

                return True, OPENAI_DIRECT_DEFAULTS["deep_self_review"]
            return True, "openai::" + slug
        if provider_has_credentials("openrouter"):
            return True, configured
        return False, None
    if provider_has_credentials(provider):
        return True, configured
    return False, None


def _chunk_atlas_into_groups(
    selected_paths: List[str],
    *,
    max_chunks: int,
    max_files_per_chunk: int,
) -> List[List[str]]:
    """Split ``selected_paths`` into ≤``max_chunks`` balanced groups of ≤``max_files_per_chunk``.

    Deterministic, no LLM call, no I/O. Round-robin assignment balanced
    against chunk-size cap: chunk *i* gets path indices ``[i*chunk_size,
    (i+1)*chunk_size)`` where ``chunk_size = ceil(len / n_chunks)``.
    Trailing empty groups are dropped, so callers may receive fewer than
    ``max_chunks`` groups when ``len(selected_paths) < max_chunks *
    max_files_per_chunk``.

    The caller MUST verify ``len(groups[i]) >= _DEEP_CHUNK_MIN_FILE_COUNT``
    per chunk — this helper only enforces the file-cap invariant, not
    Gate A's structural integrity floor (a chunk that happens to receive
    fewer than the minimum files is a structural refusal, not a silent
    shrink — the whole ``run_deep_self_review`` path gates on it).
    """
    if not selected_paths or max_chunks <= 0 or max_files_per_chunk <= 0:
        return []
    n_chunks = min(
        max_chunks,
        max(1, -(-len(selected_paths) // max_files_per_chunk)),  # ceil division
    )
    chunk_size = -(-len(selected_paths) // n_chunks)  # ceil
    groups: List[List[str]] = []
    for i in range(n_chunks):
        start = i * chunk_size
        end = min(start + chunk_size, len(selected_paths))
        if start >= end:
            break
        groups.append(list(selected_paths[start:end]))
    return groups


def _can_chunk_review(model: str, deep_window: int, deep_margin: int) -> Tuple[bool, int]:
    """Return ``(eligible, per_chunk_token_budget)`` for the chunked pipeline.

    The per-chunk budget must fit a single chunk PLUS the reviewer's output
    reserve PLUS the tokenizer margin — a chunked call that overshoots the
    reviewer's REAL window defeats the whole point. The capability-evidence
    SSOT (``reviewer_window.resolve_reviewer_window``) is the only window
    authority; this function reads the cached value, not a hardcoded 1M.
    """
    per_chunk_window_needed = (
        _DEEP_CHUNK_TOKEN_BUDGET + _DEEP_MAX_OUTPUT_TOKENS + deep_margin
    )
    eligible = deep_window >= per_chunk_window_needed and deep_window > 0
    return eligible, _DEEP_CHUNK_TOKEN_BUDGET


def _build_synthesis_prompt(chunk_responses: List[str], chunk_paths: List[List[str]]) -> str:
    """Build the synthesis-pass user message: chunk findings + their path-grounding domains.

    The synthesis pass is itself a real review pass: it must produce a final
    verdict whose findings can be grounded against the UNION of all chunk
    paths (aggregate Gate B). The system prompt stays the same; this prompt
    tells the model it is SYNTHESIZING prior chunk findings, not re-reviewing
    the codebase raw — that work already happened, and the model has no
    ability to reach pack paths outside what the chunks saw.
    """
    parts: List[str] = [
        "# Synthesis pass",
        "",
        f"You have already completed {len(chunk_responses)} chunked review passes over this codebase.",
        "Each chunk produced findings grounded against its own file slice. Your job now is to",
        "synthesize those findings into a SINGLE coherent deep self-review report covering the WHOLE",
        "codebase. Do NOT introduce findings whose path references do not appear in the chunks below;",
        "the aggregate response grounding gate checks every reference against the union of chunk paths.",
        "",
        "## Per-chunk findings (in review order)",
        "",
    ]
    for i, (resp, paths) in enumerate(zip(chunk_responses, chunk_paths), 1):
        path_list = "\n".join(f"- {p}" for p in paths)
        parts.extend([
            f"### Chunk {i}/{len(chunk_responses)} — grounded against:",
            path_list,
            "",
            "Findings:",
            resp.strip(),
            "",
            "---",
            "",
        ])
    parts.extend([
        "## Your output",
        "",
        "A single unified deep self-review report with prioritized findings,",
        "each citing the specific file and line/section. Use the same severity",
        "ladder (CRITICAL > IMPORTANT > ADVISORY). Where two chunks identified",
        "the same issue from different angles, merge them and credit both chunks.",
        "Where chunks disagree, flag the disagreement explicitly rather than",
        "averaging it away — divergence between grounded reviewers is itself",
        "a finding worth naming.",
    ])
    return "\n".join(parts) + "\n"


def _run_chunked_deep_self_review(
    *,
    repo_dir: pathlib.Path,
    drive_root: pathlib.Path,
    llm: Any,
    emit_progress: Callable[[str], None],
    event_queue: Any,
    model: str,
    deep_window: int,
    deep_output_reserve: int,
    deep_margin: int,
    atlas_manifest: Optional[dict],
    fixed_prompt_tokens: int,
) -> Tuple[str, Dict[str, Any]]:
    """Chunked-pipeline path for large codebases (closes Modes 1+2).

    Triggered when the WHOLE-pack path overshoots the reviewer's REAL window
    after its deterministic final-shrink retry. Per BIBLE P3 we never lower
    the reviewer floor; instead we split the review across N sequential
    chunks, each carrying the full system prompt + memory whitelist + a slice
    of the atlas. Per-chunk Gate A guards each chunk's structural integrity;
    per-chunk Gate B grounds each chunk's response against its own slice
    paths; the synthesis pass + aggregate Gate B ground the final verdict
    against the UNION of all chunk paths.

    Capability-evidence-backed model selection: ``_can_chunk_review`` checks
    the reviewer's REAL window (from the capability-evidence SSOT) against
    the per-chunk input budget; a model whose window cannot fit a single
    chunk is refused at admission rather than producing half-grounded
    findings.
    """
    from ouroboros.llm_observability import chat_observed

    if not atlas_manifest:
        return (
            "❌ Chunked pipeline requires an assembled atlas manifest; got None.",
            {},
        )
    selected = _coerce_path_strings(atlas_manifest.get("selected", []))
    omitted = _coerce_path_strings(atlas_manifest.get("omitted", []))

    eligible, _ = _can_chunk_review(model, deep_window, deep_margin)
    if not eligible:
        return (
            f"❌ Chunked pipeline refused: configured model {model!r} window "
            f"({deep_window:,} tokens) cannot fit one chunk "
            f"({_DEEP_CHUNK_TOKEN_BUDGET:,} input + {_DEEP_MAX_OUTPUT_TOKENS:,} output + "
            f"{deep_margin:,} tokenizer margin). Per Bible P3 we do not lower "
            f"the reviewer floor; configure a reviewer with a window ≥ "
            f"{_DEEP_CHUNK_TOKEN_BUDGET + _DEEP_MAX_OUTPUT_TOKENS + deep_margin:,} tokens.",
            {},
        )
    if len(selected) < _DEEP_CHUNK_MIN_FILE_COUNT:
        return (
            f"❌ Chunked pipeline: only {len(selected)} files in atlas "
            f"(min {_DEEP_CHUNK_MIN_FILE_COUNT}). A single review pass is structurally "
            f"incapable; switch to the whole-pack path or expand the codebase first.",
            {},
        )

    groups = _chunk_atlas_into_groups(
        selected,
        max_chunks=_DEEP_MAX_CHUNKS,
        max_files_per_chunk=_DEEP_CHUNK_MAX_FILES_PER_CHUNK,
    )
    n_chunks = len(groups)
    for i, g in enumerate(groups, 1):
        if len(g) < _DEEP_CHUNK_MIN_FILE_COUNT:
            return (
                f"❌ Chunked pipeline: chunk {i}/{n_chunks} has only {len(g)} files "
                f"(min {_DEEP_CHUNK_MIN_FILE_COUNT}). Rebalance or split further.",
                {},
            )

    emit_progress(f"Chunked review: {n_chunks} chunks, {len(selected)} files total")

    # Re-derive atlas bounds the chunked pass needs: same fixed-prompt budget
    # as the whole-pack path, applied per chunk so each chunk's reserved budget
    # is bounded independently.
    chunk_input_limit = min(_DEEP_CHUNK_TOKEN_BUDGET, deep_window - deep_output_reserve - deep_margin)
    chunk_hard_budget = max(10_000, chunk_input_limit)
    chunk_fixed_tokens = (
        int(fixed_prompt_tokens)
        + estimate_tokens("")  # memory_text is rebuilt per chunk; estimate lazily
        + 1_000  # bounded reserve for the omission section's per-chunk share
    )

    chunk_responses: List[str] = []
    chunk_paths_used: List[List[str]] = []
    aggregated_usage: Dict[str, Any] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_usd": 0.0,
    }
    chunk_manifests: List[dict] = []

    for i, group in enumerate(groups, 1):
        other_groups = [g for j, g in enumerate(groups) if j != i - 1 for _ in (g,)]
        # Per-chunk atlas: same `tracked_paths`, but OTHER chunks' files marked
        # ``already_included`` so the atlas emits only THIS chunk's files in
        # body. Manifest still carries all paths for the aggregate Gate B.
        try:
            chunk_atlas = compile_review_context_atlas(
                ReviewContextAtlasRequest(
                    repo_dir=repo_dir,
                    tracked_paths=tuple(selected + omitted),  # full coverage in manifest
                    already_included=frozenset(other_groups),
                    fixed_prompt_tokens=chunk_fixed_tokens,
                    target_total_tokens=min(850_000, chunk_hard_budget),
                    hard_total_tokens=chunk_hard_budget,
                    include_tests=False,
                    title=f"Deep Self-Review Chunk {i}/{n_chunks}",
                    compact_manifest=True,
                )
            )
        except Exception as exc:
            log.error("Chunk %d atlas compile failed: %s", i, exc, exc_info=True)
            return f"❌ Chunk {i}/{n_chunks} atlas compile failed: {exc}", aggregated_usage

        if atlas_assembly_failed(chunk_atlas):
            return (
                f"❌ Chunk {i}/{n_chunks} atlas assembly failed: "
                f"{atlas_assembly_failure_reason(chunk_atlas)}",
                aggregated_usage,
            )

        # Per-chunk pack: atlas body + memory whitelist (shared across chunks,
        # full — duplicating is cheap; splitting would break Gate B for memory refs).
        memory_parts: List[str] = []
        _append_memory_whitelist(memory_parts, [], drive_root=drive_root)
        chunk_skipped = [
            f"{record.rel_path} ({record.disposition}: {record.reason})"
            for record in chunk_atlas.omitted
            if record.disposition not in {"already_included", "manifest_only"}
        ]
        chunk_parts: List[str] = [chunk_atlas.text]
        chunk_parts.extend(memory_parts)
        _append_omission_section(chunk_parts, chunk_skipped)
        chunk_text = "\n".join(chunk_parts)
        chunk_stats = {
            "file_count": len(group),
            "memory_count": len(memory_parts),
            "total_chars": len(chunk_text),
            "skipped": chunk_skipped,
            "context_manifest": chunk_atlas.manifest,
        }

        # Per-chunk Gate A: structural integrity floor for THIS chunk.
        if (chunk_stats["file_count"] < _DEEP_CHUNK_MIN_FILE_COUNT
                or chunk_stats["memory_count"] < _DEEP_MIN_MEMORY_FILES
                or chunk_stats["total_chars"] < _DEEP_CHUNK_MIN_CHARS):
            return (
                f"❌ Chunk {i}/{n_chunks} fails Gate A: file_count="
                f"{chunk_stats['file_count']} (min {_DEEP_CHUNK_MIN_FILE_COUNT}), "
                f"total_chars={chunk_stats['total_chars']:,} "
                f"(min {_DEEP_CHUNK_MIN_CHARS:,}). Refusing to send a pathologically "
                f"small chunk.",
                aggregated_usage,
            )

        # Per-chunk size gate.
        chunk_estimated_tokens = estimate_tokens(_SYSTEM_PROMPT + chunk_text)
        if chunk_estimated_tokens > chunk_input_limit:
            return (
                f"❌ Chunk {i}/{n_chunks} input overflow: {chunk_estimated_tokens:,} tokens "
                f"> {chunk_input_limit:,} cap. Rebalance or split further.",
                aggregated_usage,
            )

        emit_progress(
            f"Chunk {i}/{n_chunks}: {chunk_stats['file_count']} files, "
            f"~{chunk_estimated_tokens:,} tokens → {model}"
        )
        try:
            chunk_response, chunk_usage = chat_observed(
                llm,
                drive_root=drive_root,
                task_id="deep_self_review_chunked",
                call_type="deep_self_review",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": chunk_text},
                ],
                model=model,
                tools=None,
                reasoning_effort=resolve_effort("deep_self_review"),
                max_tokens=_DEEP_MAX_OUTPUT_TOKENS,
                temperature=None,
                no_proxy=True,
            )
        except Exception as exc:
            log.error("Chunk %d LLM call failed: %s", i, exc, exc_info=True)
            return f"❌ Chunk {i}/{n_chunks} LLM call failed: {exc}", aggregated_usage

        chunk_text_response = chunk_response.get("content") or ""
        if not chunk_text_response:
            return (
                f"⚠️ Chunk {i}/{n_chunks}: model returned empty response.",
                chunk_usage or aggregated_usage,
            )

        # Per-chunk Gate B: response grounded against THIS chunk's slice paths.
        grounded_refs = _ground_response_in_pack(chunk_text_response, chunk_atlas.manifest)
        if len(grounded_refs) < _DEEP_MIN_PATH_REFS:
            return (
                f"❌ Chunk {i}/{n_chunks} response ungrounded: "
                f"{len(grounded_refs)} distinct path references intersect chunk pack "
                f"(min {_DEEP_MIN_PATH_REFS}). Refusing to forward an ungrounded chunk.",
                chunk_usage or aggregated_usage,
            )

        chunk_responses.append(chunk_text_response)
        chunk_paths_used.append(group)
        chunk_manifests.append(chunk_atlas.manifest)
        # Aggregate usage across chunks; only the parts the ledger surfaces.
        for k in ("prompt_tokens", "completion_tokens"):
            if chunk_usage and isinstance(chunk_usage.get(k), (int, float)):
                aggregated_usage[k] = (
                    (aggregated_usage.get(k) or 0) + chunk_usage.get(k, 0)
                )
        if chunk_usage and isinstance(chunk_usage.get("cost_usd"), (int, float)):
            aggregated_usage["cost_usd"] = (
                (aggregated_usage.get("cost_usd") or 0.0) + chunk_usage["cost_usd"]
            )

    # Synthesis pass — the model reviews the chunk findings, not the raw code.
    synthesis_user_text = _build_synthesis_prompt(chunk_responses, chunk_paths_used)
    synthesis_input_limit = min(_DEEP_CHUNK_TOKEN_BUDGET, deep_window - deep_output_reserve - deep_margin)
    if estimate_tokens(_SYSTEM_PROMPT + synthesis_user_text) > synthesis_input_limit:
        return (
            f"❌ Synthesis overflow: {estimate_tokens(_SYSTEM_PROMPT + synthesis_user_text):,} tokens "
            f"> {synthesis_input_limit:,} cap. Reduce chunk count or shorten per-chunk output.",
            aggregated_usage,
        )
    emit_progress(f"Synthesis pass: {n_chunks} chunks → final verdict")
    try:
        synthesis_response, synthesis_usage = chat_observed(
            llm,
            drive_root=drive_root,
            task_id="deep_self_review_synthesis",
            call_type="deep_self_review",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": synthesis_user_text},
            ],
            model=model,
            tools=None,
            reasoning_effort=resolve_effort("deep_self_review"),
            max_tokens=_DEEP_MAX_OUTPUT_TOKENS,
            temperature=None,
            no_proxy=True,
        )
    except Exception as exc:
        log.error("Synthesis LLM call failed: %s", exc, exc_info=True)
        return f"❌ Synthesis LLM call failed: {exc}", aggregated_usage

    synthesis_text = synthesis_response.get("content") or ""
    if not synthesis_text:
        return "⚠️ Synthesis: model returned empty response.", synthesis_usage or aggregated_usage

    # Aggregate Gate B: final verdict grounded against the UNION of all chunk
    # path slices (selected + memory whitelist + nav), the same way Gate B
    # checks the whole-pack path against the whole-pack manifest.
    all_union_paths: List[str] = []
    for paths in chunk_paths_used:
        for p in paths:
            if p not in all_union_paths:
                all_union_paths.append(p)
    for rel in _MEMORY_WHITELIST:
        if rel not in all_union_paths:
            all_union_paths.append(rel)
    aggregate_manifest = {
        "selected": [{"rel_path": p} for p in all_union_paths],
        "omitted": [{"rel_path": p} for p in omitted],
    }
    grounded_union = _ground_response_in_pack(synthesis_text, aggregate_manifest)
    if len(grounded_union) < _DEEP_MIN_PATH_REFS:
        return (
            f"❌ Synthesis ungrounded against chunk-path union: "
            f"{len(grounded_union)} distinct path references intersect the "
            f"union of {len(all_union_paths)} chunk paths (min {_DEEP_MIN_PATH_REFS}). "
            f"Refusing to publish a synthesis whose findings cannot be tied "
            f"to chunk-grounded artifacts.",
            synthesis_usage or aggregated_usage,
        )

    # Aggregate usage with the synthesis layer added.
    if synthesis_usage:
        for k in ("prompt_tokens", "completion_tokens"):
            if isinstance(synthesis_usage.get(k), (int, float)):
                aggregated_usage[k] = (
                    (aggregated_usage.get(k) or 0) + synthesis_usage.get(k, 0)
                )
        if isinstance(synthesis_usage.get("cost_usd"), (int, float)):
            aggregated_usage["cost_usd"] = (
                (aggregated_usage.get("cost_usd") or 0.0) + synthesis_usage["cost_usd"]
            )

    # Persist an aggregate context manifest so the post-task pipeline can find
    # the chunked-path coverage the same way it finds the whole-pack path's.
    try:
        atomic_write_json(
            drive_root / "state" / "deep_self_review_context.json",
            {
                "ts": utc_now_iso(),
                "model": model,
                "context_manifest": aggregate_manifest,
                "chunked": True,
                "chunk_count": n_chunks,
            },
            trailing_newline=True,
        )
    except Exception:
        log.warning("Failed to persist chunked deep self-review context manifest", exc_info=True)

    emit_progress(
        f"Chunked deep self-review complete ({len(synthesis_text):,} chars, "
        f"{n_chunks} chunks, {len(grounded_union)} grounded paths)."
    )
    return synthesis_text, aggregated_usage


def run_deep_self_review(
    repo_dir: pathlib.Path,
    drive_root: pathlib.Path,
    llm: Any,
    emit_progress: Callable[[str], None],
    event_queue: Any,
    model: str = "",
) -> Tuple[str, Dict[str, Any]]:
    """Execute full-project deep review; return error text instead of raising.

    no_proxy=True avoids macOS fork-safety SIGSEGV by using a one-shot httpx
    client with trust_env=False in llm.py; regular task calls are unaffected.
    """
    try:
        # Resolve the reviewer BEFORE building the pack: the input cap is
        # model-family-calibrated (Claude-family tokenizers need a smaller
        # estimated budget for the same 1M window — see review_helpers).
        if not model:
            available, model = is_review_available()
            if not available:
                return (
                    "❌ Deep self-review unavailable: configure "
                    "OUROBOROS_MODEL_DEEP_SELF_REVIEW and the matching provider API key."
                ), {}
        # The reviewer's REAL window (Capability Evidence), not the assumed 1M:
        # the configured deep-review model may be a 200K route, and sizing its
        # pack for 1M loses the whole review to a prompt-too-long 400.
        from ouroboros.reviewer_window import (
            reviewer_context_window,
            window_scaled_reserves,
        )

        deep_window = reviewer_context_window(model)
        deep_output_reserve, deep_margin = window_scaled_reserves(
            deep_window,
            output_reserve=_DEEP_MAX_OUTPUT_TOKENS,
            tokenizer_margin=_DEEP_OUTPUT_MARGIN_TOKENS,
        )
        input_limit = max(0, calibrated_input_token_limit(
            model,
            context_window=deep_window,
            output_reserve=deep_output_reserve,
            tokenizer_margin=deep_margin,
        ))

        emit_progress("Building generated review atlas and memory pack...")
        pack_text, stats = build_review_pack(
            repo_dir,
            drive_root,
            fixed_prompt_tokens=estimate_tokens(_SYSTEM_PROMPT),
            input_token_limit=input_limit,
        )
        if not pack_text and stats.get("skipped"):
            return f"❌ Failed to build review pack: {stats['skipped'][0]}", {}

        # Gate A (pack integrity): refuse to send a pathologically small pack to
        # the reviewer. A model given 1-2 files and 800 chars has no choice but
        # to hallucinate, and the resulting "review" reads as authoritative. This
        # gate catches the structural class before review tokens are spent.
        if (stats.get("file_count", 0) < _DEEP_MIN_FILE_COUNT
                or stats.get("memory_count", 0) < _DEEP_MIN_MEMORY_FILES
                or len(pack_text) < _DEEP_MIN_PACK_CHARS):
            return (
                f"❌ Deep self-review pack integrity gate failed: "
                f"file_count={stats.get('file_count', 0)} (min {_DEEP_MIN_FILE_COUNT}), "
                f"memory_count={stats.get('memory_count', 0)} (min {_DEEP_MIN_MEMORY_FILES}), "
                f"total_chars={len(pack_text):,} (min {_DEEP_MIN_PACK_CHARS:,}). "
                "Refusing to send a pathologically small pack to the reviewer — "
                "the model would have no choice but to hallucinate.",
                {},
            )

        emit_progress(
            f"Review pack built: {stats['file_count']} files, "
            f"{stats['total_chars']:,} chars"
            + (f", {len(stats['skipped'])} skipped" if stats["skipped"] else "")
        )
        emit_progress(
            f"Gate B threshold: review must cite at least {_DEEP_MIN_PATH_REFS} "
            f"distinct verbatim pack paths ({stats['file_count']} files available); "
            f"a corrective retry is allowed if the first response falls short."
        )

        # Gate full system+pack like scope review: reserve output headroom
        # inside the 1M window (min(SSOT, window − output − margin)) so a large
        # pack cannot trigger the deterministic input+output>window provider 400.
        estimated_tokens = estimate_tokens(_SYSTEM_PROMPT + pack_text)
        if estimated_tokens > input_limit:
            # Deterministic final shrink (instead of the historical fatal error):
            # rebuild once with the atlas budget reduced by the measured overage
            # plus margin, so residual estimator drift between per-section
            # accounting and this final concatenation cannot kill the review.
            overage = estimated_tokens - input_limit
            emit_progress(
                f"Pack overshot the input limit by ~{overage:,} tokens; "
                "rebuilding with a tighter atlas budget..."
            )
            pack_text, stats = build_review_pack(
                repo_dir,
                drive_root,
                fixed_prompt_tokens=estimate_tokens(_SYSTEM_PROMPT),
                hard_budget_reduction=overage + 8_000,
                input_token_limit=input_limit,
            )
            if not pack_text and stats.get("skipped"):
                return f"❌ Failed to build review pack: {stats['skipped'][0]}", {}
            estimated_tokens = estimate_tokens(_SYSTEM_PROMPT + pack_text)
        full_prompt_chars = len(_SYSTEM_PROMPT) + len(pack_text)
        if estimated_tokens > input_limit:
            # Whole-pack path failed even after the deterministic final-shrink
            # retry. Per Bible P3 we do not lower the reviewer floor; instead
            # dispatch the chunked pipeline when the reviewer's REAL window
            # can fit one chunk's worth (capability-evidence-backed). The chunked
            # path is opt-in via this oversize branch — normal-size codebases
            # never enter it — and the single hard-stop below still applies when
            # the configured model is too small to chunk either.
            chunk_eligible, _ = _can_chunk_review(model, deep_window, deep_margin)
            if chunk_eligible and stats.get("context_manifest"):
                emit_progress(
                    f"Pack still overshoots after final-shrink retry; entering "
                    f"chunked pipeline (model window {deep_window:,} tokens)."
                )
                return _run_chunked_deep_self_review(
                    repo_dir=repo_dir,
                    drive_root=drive_root,
                    llm=llm,
                    emit_progress=emit_progress,
                    event_queue=event_queue,
                    model=model,
                    deep_window=deep_window,
                    deep_output_reserve=deep_output_reserve,
                    deep_margin=deep_margin,
                    atlas_manifest=stats.get("context_manifest"),
                    fixed_prompt_tokens=estimate_tokens(_SYSTEM_PROMPT),
                )
            return (
                f"❌ Review pack too large: ~{estimated_tokens:,} tokens "
                f"({full_prompt_chars:,} chars of system+pack, {stats['file_count']} files). "
                f"Maximum is ~{input_limit:,} tokens "
                f"({deep_window:,}-token window minus {deep_output_reserve:,} output reserve, "
                f"calibrated for {model}). "
                f"{'Chunked path refused: model window cannot fit one chunk. ' if not chunk_eligible else ''}"
                "Reduce codebase size or split review."
            ), {}

        if stats.get("context_manifest"):
            try:
                atomic_write_json(
                    drive_root / "state" / "deep_self_review_context.json",
                    {
                        "ts": utc_now_iso(),
                        "model": model,
                        "context_manifest": stats["context_manifest"],
                    },
                    trailing_newline=True,
                )
            except Exception:
                log.warning("Failed to persist deep self-review context manifest", exc_info=True)

        emit_progress(f"Sending to {model} (~{estimated_tokens:,} tokens). This may take several minutes...")

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": pack_text},
        ]

        # no_proxy prevents macOS fork-safety SIGSEGV in bundled child process.
        from ouroboros.llm_observability import chat_observed

        response, usage = chat_observed(
            llm,
            drive_root=drive_root,
            task_id="deep_self_review",
            call_type="deep_self_review",
            messages=messages,
            model=model,
            tools=None,
            reasoning_effort=resolve_effort("deep_self_review"),
            max_tokens=_DEEP_MAX_OUTPUT_TOKENS,
            temperature=None,
            no_proxy=True,
        )

        text = response.get("content") or ""
        if not text:
            return "⚠️ Model returned an empty response for the deep self-review.", usage or {}

        # Gate B (response grounding): the model's review must reference at least
        # N distinct paths that exist in the assembled pack, so hallucinated
        # prose cannot be presented as authoritative. URL-stripping pre-pass
        # prevents a fabricated URL from grounding via its leaf path component.
        #
        # Corrective retry (closes ibl-8095de135be5): when the first response
        # grounds too few pack paths, append the response as an assistant turn
        # + a user-turn nudge that states the grounded count and lists ~8
        # verbatim pack paths the model can COPY into a revised review, then
        # re-run the same chat_observed call. Bounded to _DEEP_GATE_B_RETRIES
        # so this is a rescue, not a retry loop — fail-closed if the
        # corrective retry also falls short. Folded usage = sum of both calls.
        # Chunk (~813) and synthesis (~886) Gate B sites are unchanged this
        # pass; they need a separate plan-review wave.
        grounded_refs = _ground_response_in_pack(text, stats.get("context_manifest"))
        if len(grounded_refs) < _DEEP_MIN_PATH_REFS and _DEEP_GATE_B_RETRIES >= 1:
            manifest_paths: List[str] = []
            manifest = stats.get("context_manifest")
            if isinstance(manifest, dict):
                for section_name in ("selected", "omitted"):
                    for row in manifest.get(section_name) or ():
                        rel = None
                        if isinstance(row, dict):
                            rel = row.get("rel_path")
                        else:
                            rel = getattr(row, "rel_path", None)
                        if rel:
                            manifest_paths.append(rel)
            # Memory whitelist paths are always valid verbatim examples even
            # when the atlas is empty / oversized.
            all_example_pool = sorted(set(manifest_paths) | set(_MEMORY_WHITELIST))
            example_paths = all_example_pool[:8] if len(all_example_pool) >= 8 else all_example_pool
            example_block = "\n".join(f"  - {p}" for p in example_paths) if example_paths else (
                "  (no pack paths available; cite any verbatim file path from the pack text)"
            )
            nudge = (
                f"Your prior response was grounded against the assembled pack but only "
                f"{len(grounded_refs)} distinct pack path(s) were referenced "
                f"(the Gate B floor is {_DEEP_MIN_PATH_REFS}).\n"
                f"Please revise the review so it cites at least {_DEEP_MIN_PATH_REFS} "
                f"distinct file paths VERBATIM as they appear in the pack, e.g.\n"
                f"{example_block}\n"
                f"Use the exact paths above (or other verbatim paths from the pack) and "
                f"make sure each appears at least once in the revised review."
            )
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": nudge})

            emit_progress(
                f"Gate B shortfall ({len(grounded_refs)} < {_DEEP_MIN_PATH_REFS}); "
                f"issuing one corrective retry with {len(example_paths)} example path(s)..."
            )
            retry_response, retry_usage = chat_observed(
                llm,
                drive_root=drive_root,
                task_id="deep_self_review",
                call_type="deep_self_review",
                messages=messages,
                model=model,
                tools=None,
                reasoning_effort=resolve_effort("deep_self_review"),
                max_tokens=_DEEP_MAX_OUTPUT_TOKENS,
                temperature=None,
                no_proxy=True,
            )
            merged_usage: Dict[str, Any] = dict(usage or {})
            if retry_usage:
                for k, v in retry_usage.items():
                    if isinstance(v, (int, float)) and isinstance(merged_usage.get(k), (int, float)):
                        merged_usage[k] = merged_usage[k] + v
                    elif k not in merged_usage:
                        merged_usage[k] = v
            retry_text = retry_response.get("content") or ""
            if retry_text:
                retry_grounded = _ground_response_in_pack(retry_text, stats.get("context_manifest"))
                if len(retry_grounded) >= _DEEP_MIN_PATH_REFS:
                    text = retry_text
                    usage = merged_usage
                    grounded_refs = retry_grounded
                    emit_progress(
                        f"Corrective retry grounded: {len(retry_grounded)} distinct "
                        f"pack path(s) (>= {_DEEP_MIN_PATH_REFS})."
                    )
                else:
                    return (
                        f"❌ Deep self-review response ungrounded: "
                        f"{len(retry_grounded)} distinct path references intersect the pack "
                        f"(min {_DEEP_MIN_PATH_REFS}, after a corrective retry was attempted). "
                        "Refusing to publish a review whose findings cannot be tied to pack artifacts.",
                        merged_usage,
                    )
            else:
                return (
                    f"❌ Deep self-review response ungrounded: the corrective retry returned "
                    f"an empty response (min {_DEEP_MIN_PATH_REFS}). "
                    "Refusing to publish a review whose findings cannot be tied to pack artifacts.",
                    merged_usage,
                )

        if len(grounded_refs) < _DEEP_MIN_PATH_REFS:
            return (
                f"❌ Deep self-review response ungrounded: "
                f"{len(grounded_refs)} distinct path references intersect the pack "
                f"(min {_DEEP_MIN_PATH_REFS}). "
                "Refusing to publish a review whose findings cannot be tied to pack artifacts.",
                usage or {},
            )

        emit_progress(f"Deep self-review complete ({len(text):,} chars).")
        return text, usage or {}

    except Exception as e:
        log.error("Deep self-review failed: %s", e, exc_info=True)
        return f"❌ Deep self-review failed: {type(e).__name__}: {e}", {}
