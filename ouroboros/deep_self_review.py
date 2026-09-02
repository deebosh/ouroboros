"""Deep self-review of the whole Ouroboros system against BIBLE.md.

The review runs on the configured ``deep_review`` reviewer row
(``reviewer_slot_config.deep_review_slot``) and, like every other review
surface, has THREE deliveries chosen by the row's ``retrieves`` predicate:

* a direct ``api_chat`` row is the historical PACKED review — one 1M-context
  call carrying the Generated Deep Self-Review Atlas plus the full memory
  whitelist, assembled fail-closed (a required artifact that does not fit is
  a refusal, never a smaller pack);
* a configured-subagent api row is a NATIVE inspection episode — the reviewer
  reads the repository and the runtime memory itself through the host's
  read-only tools, so every read is host-observed and the mandatory BIBLE.md
  read is checked against the receipts afterwards;
* an ``agent_session`` row is a delegated read-only session — the same task,
  reads not host-observed (disclosed as ``unobserved``).

The retrieving deliveries ride the shared executor seam
(``review_execution._review_route_executor``) exactly like the advisory: the
product is free markdown (``triad_review`` shape ``report``), a bound landing
before the final answer delivers the collected draft marked INCOMPLETE, and
the host prepends a provenance header naming the delivery, model, rounds,
receipts, coverage and completeness so consecutive reports stay comparable.
"""

from __future__ import annotations

import logging
import os
import pathlib
import time
from typing import Any, Callable, Dict, Optional, Tuple

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
    load_governance_doc,
)
from ouroboros.utils import atomic_write_json, estimate_tokens, utc_now_iso  # noqa: E402
from ouroboros.config import get_context_mode  # noqa: E402
from ouroboros.provider_models import provider_for_model, provider_has_credentials  # noqa: E402
from ouroboros.context_layout import generate_doc_nav_map  # noqa: E402
from ouroboros.outcomes import (  # noqa: E402
    REASON_DEEP_SELF_REVIEW_ERROR,
    REASON_DEEP_SELF_REVIEW_UNAVAILABLE,
)
from ouroboros.reviewer_slot_config import ConfiguredReviewerSlot, deep_review_slot, row_effort  # noqa: E402
from ouroboros.triad_review import REVIEW_REPORT_CONTRACT  # noqa: E402

# Output reservation inside the reviewer's 1M window (same class of fix as
# scope_review._SCOPE_INPUT_TOKEN_LIMIT): 920K input + 100K output exceeds 1M
# and yields a deterministic provider 400, so the assembled INPUT prompt is
# gated on min(SSOT budget, window − output − tokenizer margin).
_DEEP_MAX_OUTPUT_TOKENS = 100_000
_DEEP_MODEL_CONTEXT_WINDOW = 1_000_000
_DEEP_OUTPUT_MARGIN_TOKENS = 155_000
_DEEP_INPUT_TOKEN_LIMIT = min(
    REVIEW_PROMPT_TOKEN_BUDGET,
    _DEEP_MODEL_CONTEXT_WINDOW - _DEEP_MAX_OUTPUT_TOKENS - _DEEP_OUTPUT_MARGIN_TOKENS,
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

# The role half of the reviewer prompt is shared by every delivery; only the
# "how to work" half differs (a pack to read vs tools to read with).
_ROLE_PROMPT = """\
You are conducting a deep self-review of the Ouroboros project — a self-creating AI agent.

Primary directive: The Constitution (BIBLE.md) is your absolute reference.
Every finding must be checked against it.

What to look for: bugs, crashes, race conditions,
BIBLE.md violations (P0–P12), contradictions between code and docs,
security gaps, dead code, missing error handling, architectural issues,
known error patterns from patterns.md that remain unfixed, and ideas how to improve Ouroboros to work better and better comply with the Bible."""

# The PACKED system prompt — byte-identical to the pre-row deep review (pinned
# by a golden digest test): the packed delivery's wire payload is unchanged.
_SYSTEM_PROMPT = _ROLE_PROMPT + """

How to work: Use the generated atlas coverage manifest systematically. Raw code is
included for selected functional/protected surfaces; every tracked file is still
accounted for by hash, size, classification, and omission/manifest disposition.
Cross-reference interactions between modules. Prioritize: CRITICAL > IMPORTANT > ADVISORY.

Output: Structured report with prioritized findings, each citing the
specific file, line/section, the problem, and the proposed fix."""

# The RETRIEVING task: the reviewer reads the repository itself. BIBLE.md is a
# mandatory full read (host-checked on the native delivery), the memory files
# ride inline byte-exact, the three big docs are navigation maps read on demand.
_RETRIEVING_METHOD = """

How to work: you are reading the repository yourself with read-only tools. Read
`BIBLE.md` IN FULL first (about {bible_chars:,} chars — in bounded chunks): every
finding is checked against it, and a report that did not read it is not a deep
self-review. The memory files below are inlined byte-exact; `docs/ARCHITECTURE.md`,
`docs/DEVELOPMENT.md` and `docs/CHECKLISTS.md` are given as navigation maps — read the
sections you need on demand. Then inspect the code (search_code, query_code,
read_file), cross-reference interactions between modules and follow call chains out
of the files you open. Prioritize: CRITICAL > IMPORTANT > ADVISORY.

Output: Structured markdown report, MOST CRITICAL findings first, each citing the
specific file, line/section, the problem, and the proposed fix. Begin with a one-line
coverage header naming what you actually read (documents and files, in full or by
section) and what you did not — your host records only the reads it observed."""

# The report contract for a retrieving row: the shared report shape plus the
# deep review's own header requirement. Handed over as policy["output_contract"]
# because both retrieving executors fall back to the JSON ARRAY contract
# without it — and an array is not a report.
_REPORT_CONTRACT = REVIEW_REPORT_CONTRACT + (
    "Begin with one line naming what you read (in full or by section) and what you did "
    "not; the rest is the prioritized report."
)

_MANDATORY_READS = ("BIBLE.md",)
_NAV_MAP_DOCS = ("docs/ARCHITECTURE.md", "docs/DEVELOPMENT.md", "docs/CHECKLISTS.md")


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

    # Compact coverage is the atlas default (the durable manifest keeps full
    # per-file coverage either way), so there is no fuller form to fall back
    # from and no compact retry rung anymore.
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
        "total_chars": len(pack_text),
        "skipped": skipped,
        "context_manifest": atlas.manifest,
    }
    return pack_text, stats


# ---------------------------------------------------------------------------
# Availability — route-aware on the configured row.
# ---------------------------------------------------------------------------


def _packed_route(configured: str) -> Tuple[str, Optional[str]]:
    """The packed delivery's ``(unavailable_reason, sendable_model)``.

    Provider/credential knowledge comes from the provider registry SSOT; the
    one deliberate deep-review-specific rule kept here: ``openai::`` is only
    trusted when ``OPENAI_BASE_URL`` is unset (a redirected endpoint cannot be
    assumed to honor the 1M-context contract the packed review depends on).
    """
    provider = provider_for_model(configured)
    if provider == "openai":
        if provider_has_credentials("openai") and not os.environ.get("OPENAI_BASE_URL"):
            return "", configured
        return f"no direct OpenAI credentials for {configured} (or OPENAI_BASE_URL redirects the route)", None
    if configured.startswith("openai/"):
        # OpenRouter route with a direct-OpenAI rewrite fallback.
        if provider_has_credentials("openrouter"):
            return "", configured
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

                return "", OPENAI_DIRECT_DEFAULTS["deep_self_review"]
            return "", "openai::" + slug
        return f"no OpenRouter or direct OpenAI credentials for {configured}", None
    if provider_has_credentials(provider):
        return "", configured
    return f"no {provider} credentials for {configured}", None


def _session_route_reason(row: ConfiguredReviewerSlot) -> str:
    """Why a delegated session row cannot run now, or '' — the substrate's own
    route health (the executor refuses on the same reader before it starts)."""
    from ouroboros.subagents import delegated_run_shape, parse_subagent_harness, route_health

    route = parse_subagent_harness(row.session_target or row.target_id)
    if route is None:
        return "session_target_unparsable"
    try:
        from ouroboros.claudexor_daemon import ensure_owned_gateway

        gateway = ensure_owned_gateway(admission_wait_sec=0)
    except Exception as exc:
        return f"agent_service_unavailable: {type(exc).__name__}: {exc}"
    try:
        unavailable, _reset_at = route_health(
            gateway, route.route_id, delegated_run_shape(False), route_model=route.model,
            pinned_profile=str(row.profile_id or getattr(route, "profile_id", "") or ""),
        )
    finally:
        gateway.close()
    return str(unavailable or "")


def deep_review_route(row: Optional[ConfiguredReviewerSlot] = None) -> Tuple[str, Optional[str]]:
    """``(unavailable_reason, identity)`` for the deep-review row.

    '' means available; ``identity`` is then the model the review runs on (the
    packed row's payable spelling, a native row's routed model, a session row's
    ``harness[=model]`` target). Availability is ROUTE-AWARE: the ≥1M /
    ``OPENAI_BASE_URL`` rule binds only the packed row; a native row needs the
    routed model's credentials; a session row needs a healthy delegated route.
    A malformed reviewer-slot setting is the typed reason, never a fallback.
    """
    try:
        row = row or deep_review_slot()
    except ValueError as exc:
        return str(exc), None
    if not row.retrieves:
        return _packed_route(row.target_id)
    if row.is_session:
        reason = _session_route_reason(row)
        return reason, (None if reason else (row.session_target or row.target_id))
    from ouroboros.provider_models import model_has_credentials

    if model_has_credentials(row.target_id):
        return "", row.target_id
    return f"no provider credentials for {row.target_id}", None


def is_review_available(row: Optional[ConfiguredReviewerSlot] = None) -> Tuple[bool, Optional[str]]:
    """Whether the configured deep-review row can run now, and on what."""
    reason, identity = deep_review_route(row)
    return (not reason), (identity if not reason else None)


def deep_review_unavailable_text(reason: str) -> str:
    """The ONE unavailable message (prefix classified by ``outcomes``)."""
    return (
        f"❌ Deep self-review unavailable: {reason}. Configure the deep-review row in "
        "Settings → Agents → Review lanes (or OUROBOROS_MODEL_DEEP_SELF_REVIEW) with a "
        "route this install can pay."
    )


# ---------------------------------------------------------------------------
# The three deliveries.
# ---------------------------------------------------------------------------


def _review_slot(row: ConfiguredReviewerSlot, model: str, timeout_sec: Optional[float]) -> Any:
    from ouroboros.config import review_model_uses_local
    from ouroboros.review_execution import ReviewRouteKind
    from ouroboros.review_substrate import ReviewSlot

    return ReviewSlot(
        slot_id=row.slot_id, model=model, effort=row_effort(row, "deep_self_review"),
        timeout_sec=timeout_sec, max_tokens=_DEEP_MAX_OUTPUT_TOKENS,
        role_hint="deep self-reviewer", use_local=review_model_uses_local(model),
        route=ReviewRouteKind.AGENT_SESSION if row.is_session else ReviewRouteKind.API_CHAT,
        session_target=row.session_target, session_profile=row.profile_id,
        subagent_id=row.subagent_id,
    )


def _record_execution(slot: Any, usage: Dict[str, Any], *, status: str, error: str = "") -> None:
    """«Выполняется как» (D22) for the deep-review row — disclosure, best-effort."""
    try:
        from ouroboros.review_substrate import ReviewActorRecord
        from ouroboros.reviewer_slot_config import record_reviewer_slot_executions

        actor = ReviewActorRecord(slot_id=slot.slot_id, model=slot.model, status=status,
                                  usage=dict(usage or {}), error=error)
        record_reviewer_slot_executions("deep_self_review", [actor], {slot.slot_id: slot})
    except Exception:
        log.debug("deep self-review last-execution write failed", exc_info=True)


def _repo_relative(path: Any, repo_dir: pathlib.Path) -> str:
    """A receipt path as a repo-relative POSIX path (absolute paths under the
    repository are relativized; anything else is kept normalized)."""
    text = str(path or "").replace("\\", "/")
    candidate = pathlib.PurePosixPath(os.path.normpath(text).replace("\\", "/"))
    if candidate.is_absolute():
        try:
            return pathlib.Path(text).resolve().relative_to(pathlib.Path(repo_dir).resolve()).as_posix()
        except (ValueError, OSError):
            return candidate.as_posix()
    return candidate.as_posix().removeprefix("./")


def _native_read_coverage(usage: Dict[str, Any], repo_dir: pathlib.Path) -> Dict[str, str]:
    """R8: which mandatory reads the host OBSERVED — from the episode's receipts.

    ``read`` = an executed read_file receipt names the path; ``missing`` = the
    receipts are complete and none does; ``unobserved`` = the receipt list was
    capped below the call count, so absence proves nothing. Disclosure, never
    a refusal: the report is delivered with the flag in its header.
    """
    receipts = [r for r in (usage.get("native_tool_receipts") or []) if isinstance(r, dict)]
    opened = {
        _repo_relative(r.get("path"), repo_dir)
        for r in receipts
        if r.get("tool") == "read_file" and r.get("outcome") == "executed" and r.get("path")
    }
    capped = int(usage.get("native_tool_calls") or 0) > len(receipts)
    return {rel: ("read" if rel in opened else ("unobserved" if capped else "missing")) for rel in _MANDATORY_READS}


def _provenance_header(facts: Dict[str, Any], human: str) -> str:
    """R9: the host's provenance header — a machine-readable comment and one
    human line — prepended to every delivered report so a reader (and the
    next task's context) can tell a packed report from a retrieved one."""
    comment = ", ".join(f"{key}={value}" for key, value in facts.items())
    return f"<!-- deep-review provenance: {comment} -->\n_{human}_\n\n"


def _failed(text: str, reason: str, usage: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
    """A failure result: the text plus TYPED usage, so the caller keeps the
    previous report instead of overwriting durable memory with an error."""
    out = dict(usage or {})
    out.update({"execution_status": "infra_failed", "reason_code": reason})
    return text, out


def _retrieving_task(repo_dir: pathlib.Path, drive_root: pathlib.Path) -> Tuple[str, Dict[str, Any]]:
    """The route-owned task text for a retrieving row: role + method, the
    memory whitelist inline (byte-exact, as the packed pack carries it), and
    the governance navigation maps. BIBLE.md is a mandatory READ, never
    inlined — on the native delivery the host checks the receipts for it."""
    from ouroboros.tools.scope_review_session import governance_nav_maps

    bible = load_governance_doc(repo_dir, "BIBLE.md", on_missing="silent")
    if not bible.strip():
        raise RuntimeError("BIBLE.md is missing at the repository root — a deep self-review has no constitution to check against")
    memory_parts: list[str] = []
    skipped: list[str] = []
    memory_count = _append_memory_whitelist(memory_parts, skipped, drive_root=drive_root)
    parts = [
        _ROLE_PROMPT + _RETRIEVING_METHOD.format(bible_chars=len(bible)),
        "## Memory (runtime data root, inlined byte-exact)",
        *memory_parts,
    ]
    if skipped:
        parts.append("Memory files omitted: " + "; ".join(skipped))
    parts.append(governance_nav_maps(repo_dir, _NAV_MAP_DOCS))
    return "\n\n".join(parts), {"memory_files": memory_count, "memory_skipped": skipped, "bible_chars": len(bible)}


def _run_retrieving_review(
    repo_dir: pathlib.Path,
    drive_root: pathlib.Path,
    llm: Any,
    emit_progress: Callable[[str], None],
    row: ConfiguredReviewerSlot,
    *,
    task_id: str,
    deadline_at: str,
) -> Tuple[str, Dict[str, Any]]:
    """A retrieving row (native episode or delegated session) through the
    shared executor seam, exactly like the advisory: hand-built request, slot
    and assignment; the product is the report text."""
    from dataclasses import asdict, replace as _dc_replace

    from ouroboros.config import get_finalization_grace_sec, get_task_abs_ceiling_sec
    from ouroboros.deadline_utils import review_operation_timeout_sec
    from ouroboros.observability import persist_call
    from ouroboros.review_execution import ReviewAssignment, _review_route_executor
    from ouroboros.review_substrate import ReviewRequest
    from ouroboros.usage_accounting import UsageScope, current_usage_scope, usage_scope

    task_text, task_facts = _retrieving_task(repo_dir, drive_root)
    request = ReviewRequest(
        surface="deep_self_review",
        goal="Deep self-review of the whole Ouroboros system against BIBLE.md.",
        task_id=task_id, call_type="deep_self_review",
        max_tokens=_DEEP_MAX_OUTPUT_TOKENS, no_proxy=True,
        session_root=str(repo_dir), session_task=task_text,
        # The report contract MUST ride the policy: both retrieving executors
        # fall back to the JSON array contract without it. The data plane is
        # the REAL runtime root (R5): the reviewer reads memory itself.
        policy={"output_contract": _REPORT_CONTRACT, "native_data_root": str(drive_root)},
        deadline_at=deadline_at,
    )
    # The logical window: the task's absolute ceiling narrowed by the owner
    # deadline — the same clock the coordinator gives a slot; without it the
    # native episode would run with no window at all and a session would fall
    # to the transport's own defaults.
    window = review_operation_timeout_sec(
        float(get_task_abs_ceiling_sec()),
        route="agent_session" if row.is_session else "api_chat",
        deadline_at=deadline_at, reserve_sec=get_finalization_grace_sec(),
    )
    slot = _review_slot(row, row.target_id, window)
    assignment = ReviewAssignment(
        request=request, slot=slot, call_id=f"deep_self_review:{task_id or 'manual'}",
        call_type="deep_self_review", custody_root=pathlib.Path(drive_root),
    )
    executor = _review_route_executor(assignment, llm=llm)
    executor._logical_deadline_monotonic = time.monotonic() + window
    delivery = "agent_session" if row.is_session else "native_tool_rounds"
    emit_progress(
        f"Deep self-review via {delivery} on {row.target_id}: {task_facts['memory_files']} memory files "
        f"inlined, BIBLE.md ({task_facts['bible_chars']:,} chars) as a mandatory read; window {window:.0f}s..."
    )
    try:
        persist_call(
            pathlib.Path(drive_root), task_id=task_id or "deep_self_review",
            call_id=f"{assignment.call_id}_prompt", call_type="deep_self_review_prompt",
            payload={"request": asdict(request), "slot": asdict(slot), **executor.prompt_payload()},
            manifest={"surface": "deep_self_review", "slot_id": slot.slot_id, "model": slot.model},
        )
    except Exception:
        log.debug("deep self-review prompt custody write failed", exc_info=True)
    scope = _dc_replace(current_usage_scope() or UsageScope(), category="deep_self_review", source="deep_self_review")
    try:
        with usage_scope(scope):
            attempt = executor.execute()
    except Exception as exc:
        _record_execution(slot, executor.failure_custody(), status="error", error=f"{type(exc).__name__}: {exc}")
        raise
    usage = dict(attempt.usage or {})
    if delivery == "native_tool_rounds":
        coverage = _native_read_coverage(usage, repo_dir)
        for rel, state in coverage.items():
            if state != "read":
                usage.setdefault("capability_delta", []).append({
                    "kind": "capability_delta",
                    "requested": f"mandatory full read of {rel}",
                    "effective": (f"no executed read_file receipt for {rel}" if state == "missing"
                                  else f"receipts capped below the call count; the {rel} read is unobserved"),
                    "reason": f"deep_review_mandatory_read_{state}",
                })
    else:
        coverage = {rel: "unobserved" for rel in _MANDATORY_READS}
    _record_execution(slot, usage, status="responded")
    try:
        from ouroboros.anthropic_native_custody import public_custody_projection

        persist_call(
            pathlib.Path(drive_root), task_id=task_id or "deep_self_review",
            call_id=f"{assignment.call_id}_response", call_type="deep_self_review_response",
            payload={"message": public_custody_projection(attempt.message), "usage": usage},
            manifest={"surface": "deep_self_review", "slot_id": slot.slot_id, "model": slot.model},
        )
    except Exception:
        log.debug("deep self-review response custody write failed", exc_info=True)
    text = str(attempt.raw_text or "")
    if not text.strip():
        return _failed("⚠️ Model returned an empty response for the deep self-review.",
                       REASON_DEEP_SELF_REVIEW_ERROR, usage)
    if not usage.get("resolved_model"):
        usage["resolved_model"] = row.target_id
    incomplete = str(usage.get("native_incomplete") or "") or "none"
    model = str(usage.get("resolved_model") or row.target_id)
    coverage_text = ",".join(f"{rel}:{state}" for rel, state in coverage.items())
    facts: Dict[str, Any] = {
        "delivery": delivery, "model": model,
        "rounds": usage.get("native_rounds", "unobserved"),
        "tool_calls": usage.get("native_tool_calls", "unobserved"),
        "receipts": len(usage.get("native_tool_receipts") or []),
        "coverage": coverage_text, "incomplete": incomplete,
        "attestation": str(usage.get("host_file_read_attestation") or "unobserved"),
    }
    if delivery == "native_tool_rounds":
        facts.update({
            "end_reason": usage.get("native_end_reason", ""),
            "transcript": f"{usage.get('native_transcript_chars', 0)}/{usage.get('native_transcript_bound', 0)}",
            "landing": f"{usage.get('native_landing_notified', False)}/{usage.get('native_landing_sent', False)}",
        })
        reads = "; ".join(f"{rel} {'read' if state == 'read' else ('NOT read' if state == 'missing' else 'unobserved (receipts capped)')}"
                          for rel, state in coverage.items())
        human = (
            f"Deep self-review: native inspection episode on {model} — {facts['rounds']} rounds, "
            f"{facts['tool_calls']} tool calls ({facts['receipts']} host-observed receipts); {reads}; "
            + ("complete" if incomplete == "none" else f"INCOMPLETE: {incomplete}")
        )
    else:
        human = (
            f"Deep self-review: agent session {row.session_target or row.target_id}"
            + (f" (model {model})" if model and model != (row.session_target or row.target_id) else "")
            + " — reads not host-observed (coverage unobserved); "
            + ("complete" if incomplete == "none" else f"INCOMPLETE: {incomplete}")
        )
    emit_progress(f"Deep self-review complete ({len(text):,} chars; {delivery}, incomplete={incomplete}).")
    return _provenance_header(facts, human) + text, usage


def _run_packed_review(
    repo_dir: pathlib.Path,
    drive_root: pathlib.Path,
    llm: Any,
    emit_progress: Callable[[str], None],
    row: ConfiguredReviewerSlot,
    model: str,
) -> Tuple[str, Dict[str, Any]]:
    """The packed delivery: one 1M-context call carrying the Atlas + memory,
    byte-identical to the pre-row deep review. ``model`` is the payable
    spelling ``deep_review_route`` resolved for the row.

    no_proxy=True avoids macOS fork-safety SIGSEGV by using a one-shot httpx
    client with trust_env=False in llm.py; regular task calls are unaffected.
    """
    # Resolve the reviewer's REAL window (Capability Evidence), not the
    # assumed 1M: the configured deep-review model may be a 200K route, and
    # sizing its pack for 1M loses the whole review to a prompt-too-long 400.
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
        return _failed(f"❌ Failed to build review pack: {stats['skipped'][0]}", REASON_DEEP_SELF_REVIEW_ERROR)

    emit_progress(
        f"Review pack built: {stats['file_count']} files, "
        f"{stats['total_chars']:,} chars"
        + (f", {len(stats['skipped'])} skipped" if stats["skipped"] else "")
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
            return _failed(f"❌ Failed to build review pack: {stats['skipped'][0]}", REASON_DEEP_SELF_REVIEW_ERROR)
        estimated_tokens = estimate_tokens(_SYSTEM_PROMPT + pack_text)
    full_prompt_chars = len(_SYSTEM_PROMPT) + len(pack_text)
    if estimated_tokens > input_limit:
        return _failed(
            f"❌ Review pack too large: ~{estimated_tokens:,} tokens "
            f"({full_prompt_chars:,} chars of system+pack, {stats['file_count']} files). "
            f"Maximum is ~{input_limit:,} tokens "
            f"({deep_window:,}-token window minus {deep_output_reserve:,} output reserve, "
            f"calibrated for {model}). "
            "Reduce codebase size or split review.",
            REASON_DEEP_SELF_REVIEW_ERROR,
        )

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
        reasoning_effort=row_effort(row, "deep_self_review"),
        max_tokens=_DEEP_MAX_OUTPUT_TOKENS,
        temperature=None,
        no_proxy=True,
    )
    usage = dict(usage or {})
    slot = _review_slot(row, model, None)
    text = response.get("content") or ""
    if not text:
        _record_execution(slot, usage, status="error", error="empty response")
        return _failed("⚠️ Model returned an empty response for the deep self-review.",
                       REASON_DEEP_SELF_REVIEW_ERROR, usage)
    usage.setdefault("resolved_model", model)
    _record_execution(slot, usage, status="responded")
    emit_progress(f"Deep self-review complete ({len(text):,} chars).")
    header = _provenance_header(
        {"delivery": "api_packet", "model": model, "rounds": 1, "tool_calls": 0, "receipts": 0,
         "coverage": f"pack:{stats['file_count']}_files", "incomplete": "none", "attestation": "packed"},
        f"Deep self-review: one packed API review on {model} — {stats['file_count']} files + memory inlined; complete",
    )
    return header + text, usage


def run_deep_self_review(
    repo_dir: pathlib.Path,
    drive_root: pathlib.Path,
    llm: Any,
    emit_progress: Callable[[str], None],
    *,
    task_id: str = "",
    deadline_at: str = "",
    slot: Optional[ConfiguredReviewerSlot] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Execute the deep self-review on the configured row; never raises.

    Returns ``(text, usage)``. A delivered report carries the host provenance
    header; every failure returns its text with typed usage
    (``execution_status="infra_failed"`` + ``reason_code``) so the caller can
    keep the previous report instead of overwriting it with an error.
    ``slot`` overrides the configured row (tests, callers that already resolved it).
    """
    try:
        try:
            row = slot or deep_review_slot()
        except ValueError as exc:
            return _failed(deep_review_unavailable_text(str(exc)), REASON_DEEP_SELF_REVIEW_UNAVAILABLE)
        reason, model = deep_review_route(row)
        if reason:
            return _failed(deep_review_unavailable_text(reason), REASON_DEEP_SELF_REVIEW_UNAVAILABLE)
        if row.retrieves:
            return _run_retrieving_review(
                repo_dir, drive_root, llm, emit_progress, row, task_id=task_id, deadline_at=deadline_at,
            )
        return _run_packed_review(repo_dir, drive_root, llm, emit_progress, row, str(model or ""))
    except Exception as e:
        log.error("Deep self-review failed: %s", e, exc_info=True)
        return _failed(f"❌ Deep self-review failed: {type(e).__name__}: {e}", REASON_DEEP_SELF_REVIEW_ERROR)
