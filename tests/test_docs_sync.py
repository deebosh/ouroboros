"""Guardrails for architecture docs after UI/routing overhaul.

README prose pins were retired in v5.8.3-rc.5 — the README is intentionally
allowed to evolve its marketing copy without dragging tests along; the
ARCHITECTURE.md pins below are the load-bearing rationale-layer guards
(P6) that must survive every doc-touch commit.
"""

import os
import pathlib
import re

from ouroboros.tools.registry import ToolRegistry

REPO = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_architecture_mentions_shared_log_grouping_and_direct_provider_review_fallback():
    arch = _read("docs/ARCHITECTURE.md")

    assert "log_events.js" in arch
    assert "live task card" in arch
    assert "grouped task cards" in arch
    # Direct-provider fallback covers official OpenAI, Anthropic, MiniMax, Cloud.ru,
    # and GigaChat, while still excluding OpenRouter/OpenAI-compatible/mixed-provider configs.
    # Keep the generalized name ("Direct-provider review fallback") and a
    # reference to the legacy "OpenAI-only review fallback" phrase for
    # discoverability, and pin the honest scope language so the doc cannot
    # silently re-expand to claim symmetric coverage it does not have yet.
    assert "Direct-provider review fallback" in arch
    assert "OpenAI-only review fallback" in arch  # legacy name still referenced for discoverability
    assert "official OpenAI, Anthropic, MiniMax, Cloud.ru, and GigaChat" in arch
    assert "_exclusive_direct_remote_provider_env" in arch
    # v4.34.0: direct-provider fallback now documents the
    # `main_model.startswith(provider_prefix)` guard in get_review_models —
    # previously absent, allowing OpenAI/Anthropic-only setups with a
    # cross-provider free-text main model to silently miss the fallback.
    assert "migrate_model_value" in arch
    assert "already start with the exclusive provider prefix" in arch
    # The Claude Runtime Status surface is RETIRED with the Claude-SDK
    # advisory transport (owner-consented, 2026-08-29): the doc must not
    # resurrect its UI plumbing.
    assert "refreshClaudeCodeStatus" not in arch
    assert "claudeRuntimeHasError" not in arch


def test_architecture_documents_skill_schedule_lifecycle_and_evolution_light_block():
    arch = _read("docs/ARCHITECTURE.md")

    # v6.9 RC2: skill schedule readiness SSOT, lifecycle resync, tombstone
    # retention, DST contract, and the evolution light-mode hard block.
    assert "resync_skill_schedules()" in arch
    assert "skill_readiness_for_execution()" in arch
    assert "DST-aware system" in arch
    assert "hard-blocked in `light` runtime mode" in arch
    # Experience Review memory write-back data flow is documented.
    assert "MEMORY_ACTIONS_JSON" in arch
    assert "apply_memory_actions" in arch
    assert "never auto-written to `identity.md`" in arch


def test_consciousness_prompt_matches_scope_limited_contracts():
    consciousness = _read("prompts/CONSCIOUSNESS.md")

    assert "schedule subagents" in consciousness
    assert "wait on subagents" in consciousness
    assert "Update your scratchpad or identity" in consciousness
    assert "Message the user proactively" in consciousness
    assert "recent_tasks" in consciousness


def test_phase3_governance_language_is_pinned_without_new_qa_surface():
    bible = _read("BIBLE.md")
    development = _read("docs/DEVELOPMENT.md")
    system = _read("prompts/SYSTEM.md")
    authoring = _read("docs/CREATING_SKILLS.md")
    architecture = _read("docs/ARCHITECTURE.md")
    checklists = _read("docs/CHECKLISTS.md")
    development_flat = " ".join(development.split())

    assert (
        "Uncertainty calls for judgment, not permission: within its legitimate "
        "authority, Ouroboros decides autonomously."
    ) in bible
    assert (
        "Structural depth is not scope breadth: choose the smallest change that "
        "eliminates the proven failure class."
    ) in bible

    for principle in (
        "Single Responsibility Principle",
        "Open/Closed Principle",
        "Liskov Substitution Principle",
        "Interface Segregation Principle",
        "Dependency Inversion Principle",
    ):
        assert principle in development
    assert "DI container" in development
    assert "AST analyzer" in development
    assert "Diff size, line count, and file count alone are not findings" in development

    assert "Mutable external-fact inventory" in development
    for column in (
        "Location",
        "Fact",
        "Mutability",
        "Current authority",
        "Live/probe option",
        "Risk",
        "Recommendation",
    ):
        assert f"| {column} " in development
    assert "does not migrate their runtime representations" in development_flat

    for text in (development, system, authoring, architecture, checklists):
        flat = " ".join(text.split())
        assert "real consumer flow" in flat
        assert "screenshot" in flat.lower()
        assert "vision" in flat.lower()
        assert "not a universal" in flat or "not universal" in flat or "no universal" in flat
    assert "No visual-QA runner, endpoint, ledger" in " ".join(architecture.split())


def test_continuity_projection_contract_is_mirrored_across_governance_docs():
    """Keep the partial-input rule and its concrete data-flow map from drifting."""
    bible = " ".join(_read("BIBLE.md").split()).replace("**", "")
    architecture = " ".join(_read("docs/ARCHITECTURE.md").split())
    development = " ".join(_read("docs/DEVELOPMENT.md").split())
    checklists = _read("docs/CHECKLISTS.md")

    assert (
        "Disclosure is not sufficiency. An omission marker keeps a record honest; "
        "it does not make the record complete. Where material is omitted, the "
        "disclosure must name a source this actor can actually resolve. A view known "
        "to be partial may not authorize PASS, a destructive rewrite, or replacement "
        "of the full contract it was cut from."
    ) in bible
    assert "Continuity data-flow map" in architecture
    assert "state/consciousness_observations.jsonl" in architecture
    assert "Source-complete decision pipeline" in development
    assert "Context and growth matrix" in development
    for item in (
        "source_completeness",
        "actor_readable_projection",
        "canonical_memory_fork",
        "review_artifact_continuity",
        "display_identity_replay",
    ):
        assert item in checklists


def test_phase3_widget_authoring_docs_match_recursive_schema_v1():
    development = _read("docs/DEVELOPMENT.md")
    authoring = _read("docs/CREATING_SKILLS.md")
    architecture = _read("docs/ARCHITECTURE.md")
    checklists = _read("docs/CHECKLISTS.md")
    authoring_flat = " ".join(authoring.split())

    for text in (development, authoring, architecture, checklists):
        for component in ("group", "metric", "callout"):
            assert component in text
    assert "maximum depth of 8" in authoring
    assert "256 nodes" in authoring
    assert "stable tree path" in authoring
    assert "transitively passive" in authoring_flat
    assert "dynamic_ui_schema" in authoring


def test_architecture_mirror_matches_the_split_axes_contracts():
    """XG-2.2/XG-2.3 (v6.87.28 review gate): the P6 mirror tracks schedule-vs-dispatch.

    Every pin here failed on the pre-fix docs: the module map claimed
    `swarm_efficiency.lanes_used` while `_build_swarm_efficiency` emits
    `lanes_requested`; `subagents.py` was said to own task-group compaction after
    `compact_task_group` was deleted; the control map said `schedule_subagent`
    surfaces effective lane(s) after `_finalize_schedule_emission` went
    request-only; the `swarm_fanout` enumeration promised requested/effective
    lanes after `_emit_swarm_fanout` dropped `effective_model_lanes`; and both
    `wait_tasks` field enumerations omitted the emitted `capability_delta`.
    """
    arch = _read("docs/ARCHITECTURE.md")
    development = _read("docs/DEVELOPMENT.md")
    arch_flat = " ".join(arch.split())
    dev_flat = " ".join(development.split())

    # swarm_efficiency reports the REQUEST: lanes_requested, never lanes_used.
    assert "lanes_requested" in arch
    assert "lanes_used" not in arch
    # Task-group compaction left with the degenerate lane fan-out (v6.87.28).
    assert "task-group compaction" not in arch
    assert "compact_task_group" not in arch
    # schedule_subagent reports the request only; the axes resolve at dispatch.
    assert "schedule_subagent surfaces effective_lane(s)" not in arch_flat
    assert "`schedule_subagent` reports the requested lane only" in arch_flat
    # swarm_fanout carries the requested lane; a wave event written before any
    # child starts cannot know what the children ran on.
    assert "requested/effective lanes" not in arch
    # Both wait_tasks projection enumerations disclose capability_delta.
    assert "trace_summary, capability_delta when the child has something to disclose" in arch_flat
    assert "trace_summary, capability_delta when disclosable, duplicate_of" in dev_flat


# Identifiers the prompts legitimately name in backticks that are NOT tools:
# parameter names, resource roots, write surfaces, typed outcome/status tokens
# and runtime-context keys. A NEW snake_case identifier in a prompt must either
# be a real tool (or background-whitelisted tool) or be classified here on
# purpose — that classification step is the governance the prompt audit wants:
# a phantom or renamed tool name can no longer hide in the runtime prompts
# (`advisory_review` and the CONSCIOUSNESS "You can" catalog rotted that way).
# Scope: backticked names in all three prompts plus the bare snake_case names
# CONSCIOUSNESS.md writes without backticks; BIBLE.md is deliberately out of scope.
PROMPT_NON_TOOL_IDENTIFIERS = frozenset({
    # resource roots / write surfaces / write roots
    "active_workspace", "artifact_store", "external_workspace", "runtime_data",
    "skill_payload", "subagent_projects", "system_repo", "task_drive", "user_files",
    "write_root", "write_surface",
    # tool parameters named as cross-tool policy
    "project_id", "project_name", "recommended_use", "review_rebuttal",
    # typed outcomes / statuses / runtime-context keys
    "needs_manual_target", "started_uncustodied", "owner_client",
    # safety policy class names (ouroboros/safety.py TOOL_POLICY values) and
    # owner-setting values named as policy
    "check_conditional", "check", "off", "low",
    # package managers / interpreters named as acquisition or process choices
    "pip", "pip3", "uv", "brew", "apt", "python", "python3", "sudo", "grep", "env",
    # git branches / remotes / skill buckets / write surfaces named as policy
    "ouroboros", "main", "managed", "origin", "external", "genesis", "deliverables",
    # fenced-block languages the owner chat renders natively
    "mermaid", "chart",
    # backlog item status value in CONSCIOUSNESS.md
    "done",
})


def _prompt_backticked_identifiers(text: str) -> set:
    """Backticked lowercase identifiers, single-word ones included (a renamed
    single-word tool such as `escalate` must be caught too)."""
    found = set()
    for token in re.findall(r"`([^`]+)`", text):
        head = token.split("(", 1)[0]
        if re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", head):
            found.add(head)
    return found


def _prompt_bare_identifiers(text: str) -> set:
    """snake_case tokens written WITHOUT backticks (CONSCIOUSNESS.md's style);
    tokens that are part of a path or filename (`a/b_c`, `x_y.json`) are skipped."""
    return {
        m.group(1)
        for m in re.finditer(r"(?<![\w/.`-])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?![\w/.`-])", text)
    }


def test_prompt_tool_names_resolve_to_registered_tools(tmp_path):
    """Every backticked snake_case identifier in the three runtime prompts is
    either a registered tool (public schema), a background-consciousness tool,
    or a documented non-tool identifier. Completeness is deliberately NOT
    required (the schemas are the catalog); this only forbids phantoms and
    stale spellings, the drift class the prompt audit found in every prompt."""
    from ouroboros.consciousness import BackgroundConsciousness

    root = pathlib.Path(__file__).resolve().parent.parent
    registry = ToolRegistry(repo_dir=tmp_path / "repo", drive_root=tmp_path / "data")
    registered = {schema["function"]["name"] for schema in registry.schemas()}
    # The background whitelist is not taken on faith: every name in it must be a
    # registered public tool or a ToolEntry the consciousness module registers
    # itself (set_next_wakeup and friends), otherwise the whitelist has rotted.
    consciousness_src = (root / "ouroboros" / "consciousness.py").read_text(encoding="utf-8")
    bg_private = set(re.findall(r'ToolEntry\("([a-z0-9_]+)"', consciousness_src))
    stale_whitelist = set(BackgroundConsciousness._BG_TOOL_WHITELIST) - registered - bg_private
    assert not stale_whitelist, f"_BG_TOOL_WHITELIST names unregistered tools: {sorted(stale_whitelist)}"
    universe = (
        registered
        | set(BackgroundConsciousness._BG_TOOL_WHITELIST)
        | PROMPT_NON_TOOL_IDENTIFIERS
    )
    # CONSCIOUSNESS.md runs on the background registry, which admits ONLY the
    # whitelist (consciousness.py _tool_schemas/_execute_tool), so a public tool
    # that is not whitelisted is a phantom there.
    bg_universe = set(BackgroundConsciousness._BG_TOOL_WHITELIST) | PROMPT_NON_TOOL_IDENTIFIERS
    for rel, allowed in (
        ("prompts/SYSTEM.md", universe),
        ("prompts/SAFETY.md", universe),
        ("prompts/CONSCIOUSNESS.md", bg_universe),
    ):
        text = (root / rel).read_text(encoding="utf-8")
        unresolved = _prompt_backticked_identifiers(text) - allowed
        assert not unresolved, (
            f"{rel} names identifiers that are neither registered tools nor "
            f"classified non-tool identifiers: {sorted(unresolved)}"
        )
    # CONSCIOUSNESS.md writes tool names without backticks; its bare snake_case
    # tokens must resolve the same way (the runtime drift check in
    # context_health only catches names with known prefixes).
    bare = _prompt_bare_identifiers((root / "prompts" / "CONSCIOUSNESS.md").read_text(encoding="utf-8"))
    unresolved_bare = bare - bg_universe
    assert not unresolved_bare, (
        f"prompts/CONSCIOUSNESS.md names bare identifiers that are neither registered tools "
        f"nor classified non-tool identifiers: {sorted(unresolved_bare)}"
    )


# --- Documentation contract enforcement (DEVELOPMENT.md "Documentation contract") ---
#
# Three deterministic checks keep the two resident docs a present-tense map:
#   1. residue: version stamps, decision codenames and "used to / previously"
#      narrative may only SHRINK per `## ` section (baseline below, like the code
#      size ratchet — a section that reaches zero may never grow again);
#   2. the endpoint table mirrors the executable route registries;
#   3. the settings table mirrors `config.SETTINGS_DEFAULTS` (env-only and retired
#      rows are declared, not guessed).
# Language-tagged code fences (```yaml, ```python …) are examples and are not
# scanned; the plain ``` fence holding the §1 module tree IS scanned. The first
# ARCHITECTURE line carries the release version by contract and is skipped, as
# are DEVELOPMENT's "Mutable external-fact inventory" (dated provenance is the
# rule there) and the "Documentation contract" section that quotes the markers.

DOC_RESIDUE_PATTERNS = {
    "version_stamp": r"\((?:v\d+\.\d+(?:\.\d+)?(?:-rc\.\d+)?)\)",
    "version_narrative": r"\b(?:since|before|pre-)v\d+",
    "narrative": r"\b(?:used to|previously|formerly|was deleted|replaces the earlier|gate[- ]round|round \d+)\b",
    "codename_paren": r"\((?:GR|AR|BR|CR|D|Q|S|HQ|C|B)\d+[^)]{0,24}\)",
    "codename_word": r"\bPoltergeist\b|\bphase [A-C]\d?\b|owner(?:-| )(?:decision|ratif)",
}
DOC_RESIDUE_SKIPPED_SUBSECTIONS = {
    "docs/DEVELOPMENT.md": ("Mutable external-fact inventory", "Documentation contract"),
}


def doc_residue_counts(rel: str, text: str) -> dict:
    """Per-`## ` section counts of residue markers (see DOC_RESIDUE_PATTERNS)."""
    counts: dict = {}
    section = "(preamble)"
    fence_lang = None
    skipping = False
    skipped = DOC_RESIDUE_SKIPPED_SUBSECTIONS.get(rel, ())
    for lineno, line in enumerate(text.split("\n"), 1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            fence_lang = (stripped[3:].strip() or "") if fence_lang is None else None
            continue
        if fence_lang:  # language-tagged example block
            continue
        if fence_lang is None and line.startswith("## "):
            section, skipping = line.strip(), False
        if fence_lang is None and line.startswith("### "):
            skipping = any(name in line for name in skipped)
        if skipping or (rel == "docs/ARCHITECTURE.md" and lineno == 1):
            continue
        for kind, pattern in DOC_RESIDUE_PATTERNS.items():
            hits = len(re.findall(pattern, line))
            if hits:
                counts.setdefault(section, {k: 0 for k in DOC_RESIDUE_PATTERNS})[kind] += hits
    return counts


# Shrink-only baseline (07b53365, 2026-09-02). Regenerate a section's row ONLY
# downward, in the same commit that removed the residue; a section absent here
# must stay at zero.
DOC_RESIDUE_BASELINE = {
    "docs/ARCHITECTURE.md": {
        "## 1. High-Level Architecture": {
            "version_stamp": 0,
            "version_narrative": 0,
            "narrative": 0,
            "codename_paren": 0,
            "codename_word": 0
        },
        "## 2. Startup / Onboarding Flow": {
            "version_stamp": 0,
            "version_narrative": 0,
            "narrative": 0,
            "codename_paren": 0,
            "codename_word": 0
        },
        "## 3. Web UI Pages & Buttons": {
            "version_stamp": 0,
            "version_narrative": 0,
            "narrative": 0,
            "codename_paren": 0,
            "codename_word": 0
        },
        "## 4. Server API Endpoints": {
            "version_stamp": 0,
            "version_narrative": 0,
            "narrative": 0,
            "codename_paren": 0,
            "codename_word": 0
        },
        "## 5. Supervisor Loop": {
            "version_stamp": 0,
            "version_narrative": 0,
            "narrative": 0,
            "codename_paren": 2,
            "codename_word": 3
        },
        "## 6. Agent Core": {
            "version_stamp": 8,
            "version_narrative": 0,
            "narrative": 9,
            "codename_paren": 6,
            "codename_word": 15
        },
        "## 7. Configuration (ouroboros/config.py)": {
            "version_stamp": 0,
            "version_narrative": 0,
            "narrative": 0,
            "codename_paren": 0,
            "codename_word": 0
        },
        "## 11. Frozen Contracts v1 (`ouroboros/contracts/`)": {
            "version_stamp": 0,
            "version_narrative": 0,
            "narrative": 0,
            "codename_paren": 0,
            "codename_word": 0
        },
        "## 13. External Skills Layer": {
            "version_stamp": 0,
            "version_narrative": 0,
            "narrative": 0,
            "codename_paren": 0,
            "codename_word": 0
        }
    },
    "docs/DEVELOPMENT.md": {
        "## Naming and boundaries": {
            "version_stamp": 1,
            "version_narrative": 0,
            "narrative": 5,
            "codename_paren": 0,
            "codename_word": 1
        },
        "## Module Size & Complexity": {
            "version_stamp": 0,
            "version_narrative": 1,
            "narrative": 0,
            "codename_paren": 0,
            "codename_word": 0
        },
        "## Core Governance Artifacts": {
            "version_stamp": 2,
            "version_narrative": 0,
            "narrative": 0,
            "codename_paren": 0,
            "codename_word": 0
        },
        "## Review & Commit Protocol": {
            "version_stamp": 3,
            "version_narrative": 0,
            "narrative": 1,
            "codename_paren": 7,
            "codename_word": 8
        }
    }
}


def test_resident_docs_residue_only_shrinks():
    for rel, baseline in DOC_RESIDUE_BASELINE.items():
        current = doc_residue_counts(rel, _read(rel))
        for section, counts in current.items():
            allowed = baseline.get(section, {})
            for kind, hits in counts.items():
                assert hits <= allowed.get(kind, 0), (
                    f"{rel} {section!r}: {kind} residue grew to {hits} (baseline "
                    f"{allowed.get(kind, 0)}); replace the node's description instead of "
                    "appending history (DEVELOPMENT.md 'Documentation contract')"
                )


def _architecture_section(text: str, heading_prefix: str) -> str:
    lines = text.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith(heading_prefix))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def test_architecture_endpoint_table_mirrors_route_registries():
    """Every mounted browser/CLI route and Host Service route has exactly one table
    row in ARCHITECTURE §4, and no row names a route that is not mounted."""
    from ouroboros.gateway.endpoint_index import HTTP_ENDPOINTS
    from ouroboros.gateway import files as gateway_files

    section = _architecture_section(_read("docs/ARCHITECTURE.md"), "## 4.")
    rows = re.findall(r"^\| (GET|POST|PUT|PATCH|DELETE|ANY|WS|STATIC) \| `([^`]+)` \|", section, re.M)
    host_prefix = "127.0.0.1:${OUROBOROS_HOST_SERVICE_PORT:-8767}"
    documented_public = {f"{m} {p}" for m, p in rows if not p.startswith(host_prefix)}
    documented_host = {f"{m} {p[len(host_prefix):]}" for m, p in rows if p.startswith(host_prefix)}

    expected_public = set(HTTP_ENDPOINTS)
    for route in gateway_files.file_browser_routes():
        for method in sorted(getattr(route, "methods", None) or []):
            if method in ("HEAD", "OPTIONS"):
                continue
            expected_public.add(f"{method} {route.path}")
    # server-level surfaces that are not gateway routes but belong in the map
    expected_public |= {"GET /", "WS /ws", "STATIC /static/*"}
    # /api/extensions/{skill}/{rest:path} is mounted for every verb and documented once as ANY
    expected_public = {e for e in expected_public if not e.endswith("/api/extensions/{skill}/{rest:path}")}
    expected_public.add("ANY /api/extensions/{skill}/{rest:path}")

    host_src = _read("ouroboros/gateway/host_service.py")
    expected_host = set()
    for path, methods in re.findall(r'Route\("([^"]+)",\s*_api_\w+,\s*methods=\[([^\]]+)\]', host_src):
        method = methods.strip().strip("\"'")
        expected_host.add(f"{method} {path}")
    for path in re.findall(r'WebSocketRoute\("([^"]+)"', host_src):
        expected_host.add(f"WS {path}")

    assert documented_public == expected_public, (
        f"missing rows: {sorted(expected_public - documented_public)}; "
        f"stale rows: {sorted(documented_public - expected_public)}"
    )
    assert documented_host == expected_host, (
        f"missing host rows: {sorted(expected_host - documented_host)}; "
        f"stale host rows: {sorted(documented_host - expected_host)}"
    )
    assert len(rows) == len(documented_public) + len(documented_host), "duplicate endpoint rows"


# Rows the settings table documents on purpose although `config.SETTINGS_DEFAULTS`
# has no such key: operator env-only levers (never a settings.json carrier) and the
# retired alias whose migration the table still explains (pinned by test_review_cycles).
SETTINGS_TABLE_ENV_ONLY_ROWS = frozenset({
    "OUROBOROS_TRUST_NONLOCAL_BIND_WITHOUT_PASSWORD", "OUROBOROS_DISABLE_MANAGED_UPDATES",
    "OUROBOROS_PRESENTATION", "OUROBOROS_USER_FILES_ROOT", "OUROBOROS_OBSERVABILITY_KEEP_RAW",
    "OUROBOROS_OBSERVABILITY_RETENTION_DAYS", "OUROBOROS_REVIEW_MODEL_TIMEOUT_SEC",
    "OUROBOROS_REVIEW_MAX_TOKENS", "OUROBOROS_PREFLIGHT_TIMEOUT_SEC", "OUROBOROS_PREFLIGHT_SERIAL",
    "OUROBOROS_BUNDLE_DIR",
})
SETTINGS_TABLE_RETIRED_ROWS = frozenset({"OUROBOROS_ACCEPTANCE_MAX_IMPROVEMENT_PASSES"})


def _normalize_default_cell(cell: str) -> str:
    value = cell.strip()
    value = re.sub(r"^`(.*)`$", r"\1", value)
    if value in ('""', "(empty)", "(unset)", "unset", ""):
        return ""
    value = re.sub(r'^"(.*)"$', r"\1", value)
    return value.split()[0].lower() if value else ""


def test_architecture_settings_table_mirrors_config_defaults():
    """Every `config.SETTINGS_DEFAULTS` key has one row in ARCHITECTURE's Default
    settings table with the shipped default; every other row is a declared env-only
    lever or retired alias."""
    from ouroboros import config

    section = _architecture_section(_read("docs/ARCHITECTURE.md"), "## 7.")
    table = section[section.index("### Default settings"):]
    rows = re.findall(r"^\| ([A-Z][A-Z0-9_]+) \| ([^|]*?) \|", table, re.M)
    keys = [k for k, _ in rows]
    assert len(keys) == len(set(keys)), f"duplicate settings rows: {sorted(k for k in set(keys) if keys.count(k) > 1)}"
    documented = set(keys)
    expected = set(config.SETTINGS_DEFAULTS)
    assert expected <= documented, f"settings missing from the table: {sorted(expected - documented)}"
    undeclared = documented - expected - SETTINGS_TABLE_ENV_ONLY_ROWS - SETTINGS_TABLE_RETIRED_ROWS
    assert not undeclared, f"table rows that are neither shipped defaults nor declared env-only/retired: {sorted(undeclared)}"
    mismatched = [
        (key, cell, str(config.SETTINGS_DEFAULTS[key]))
        for key, cell in rows
        if key in expected and _normalize_default_cell(cell) != str(config.SETTINGS_DEFAULTS[key]).lower()
    ]
    assert not mismatched, f"documented default differs from config.SETTINGS_DEFAULTS: {mismatched}"
