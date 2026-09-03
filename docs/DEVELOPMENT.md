# DEVELOPMENT.md — Development Principles & Module Guide

## Role and authority

This is Ouroboros's engineering handbook: imperative rules for changing the
body, grouped by change class, each naming the surface that enforces it (test,
gate, CI lane) or stating that none does. `BIBLE.md` owns constitutional
principles; `docs/ARCHITECTURE.md` owns the current structure, data flow, and
rationale map; `docs/DESIGN.md` owns visual and interaction semantics;
`docs/CHECKLISTS.md` owns reviewer items, severity, and output contracts. This
file does not duplicate their inventories or serve as a changelog.

Rules here describe current practice or a deliberately enforced standard. When
code and prose disagree, inspect the implementation and history, repair the
authoritative surfaces together, and retain the failure a non-obvious rule
prevents.

---

## Naming and boundaries

- Code identifiers, comments, docstrings, commit messages, and user-facing
  product UI strings are English.
- Follow PEP 8: modules and variables use `snake_case`, classes use
  `PascalCase`, constants use `UPPER_SNAKE_CASE`. Name the observable
  responsibility and authority, not the implementation fashion; prefer a clear
  function module over a class with no lifecycle.
- Contracts are typed shapes, not service objects. A manager is justified when
  it owns lifecycle or mutable state. LLM-callable Tools remain thin
  `{verb}_{noun}` functions that validate their public input, call the owning
  subsystem, and format a result. No universal `{Domain}Service`,
  `{Platform}Gateway`, or class layer is required.
- Dependency direction is the test: UI/CLI → inbound gateway → domain owner;
  runtime policy → small host-owned contract → outbound adapter (the
  `ouroboros/gateway/` inbound vs `ouroboros/gateways/` outbound map is
  ARCHITECTURE "Gateway Boundary v1"). Provider- or transport-specific
  decisions do not flow back into core policy.
- Chat authorship is stamped by the producer and preserved through persistence
  and replay; never infer it from text or promote host-selected intermediate
  output to a model final (`tests/test_terminal_provenance.py`).

Enforcement: naming and entity-type rules are scored in commit review by
CHECKLISTS item 2 `development_compliance` (a)/(b); the transport/core
dependency direction has one CI guard (the "Guard extracted transport imports
stay out of core" step in `.github/workflows/ci.yml`); the remaining boundary
rules have no automated surface — review-only.

### CLI and headless work

- CLI commands parse, call the existing gateway/scheduler, and render text or
  typed JSON/JSONL/SSE. They do not create a second task state machine.
- External workspace tasks keep governance bound to the system repository while
  contextual tools default through `ToolContext.active_repo_dir()`. Admission
  rejects overlap with the system repo/data and records a read-only preflight.
- Project focus changes the default target, not the top-level tool surface.
  Generic VCS selects active/system explicitly; advisory, reviewed commit,
  rollback, promotion, restart, and runtime control keep their intrinsic
  system-repository contracts. `executor_ref` selects a process backend, not an
  implicit sandbox.
- Project-local installs may run within the workspace policy. Global/system
  installs remain safety-reviewed, and `sudo` is non-interactive (`sudo -n`).
- Do not add a second scheduler for operator tooling or a generic CLI file
  manager. Use the task queue, attachments, logs, and artifact endpoints.

Enforcement: `tests/test_headless_cli.py` (task-API admission, typed refusal
terminality, attachment admission), `tests/test_cli_entrypoint.py` (the CLI
surface), and `tests/test_external_workspace_access.py` plus
`tests/test_workspace_authority_binding.py` (workspace policy and system-repo
collision blocking). The no-second-scheduler and no-generic-file-manager
rules have no automated surface — review-only.

### Cognitive quality

Do not lower model quality, reasoning effort, output budget, or context breadth
as an incidental latency/cost optimization (BIBLE P1 owns the principle). An
intentional narrowing is an owner-approved change reflected in the plan, docs,
tests, and evidence. No automated surface — review-only: commit review carries
it through CHECKLISTS items 1 (`bible_compliance`) and 21
(`capability_regression`: an accidental narrowing is the named failure class).

### LLM-first affordances

Do not repair a semantic tool-choice failure by adding one more keyword hint to
`prompts/SYSTEM.md`. Put stable discoverability in the tool schema or add a
typed affordance at the point of need. SYSTEM accretion trains around one
incident, bloats the resident prefix, and forks the authority.

What belongs in `prompts/SYSTEM.md` (tier-0 for every Main/task profile in both
context modes — Background Consciousness and the safety supervisor carry their
own prompts — and competing with the task for context): identity and tone, the decision
loop (answer / promote / route / delegate / do it myself), cross-tool policy
(which class of tool or lane for which situation, root semantics, memory only
through its own tools, untrusted external data), prohibitions and safety
invariants stated once, and the memory contract. What does NOT belong there:
how a tool or mechanism works. A tool's parameters, signatures, recipes,
typed outcomes, and "when to choose it" live in its `get_tools()` schema — each
profile receives its own visible schema set on every round (delegated, repair,
ephemeral, credential and contract filters narrow it), so the schema is the SSOT
of the per-tool contract and a prompt sentence about it is a second copy that
drifts, while SYSTEM.md stays the cross-tool selection policy; mechanism
documentation lives in ARCHITECTURE or here;
runtime facts (capabilities, queue, catalog, receipts, health) are injected per
turn. A new tool therefore requires NO SYSTEM.md mention. Before adding a
sentence to a prompt, check that the schema or runtime block does not already
carry it; before removing one, check that they do (or add the missing fact to
the schema without growing it into a paragraph). Local-model compaction keeps
only the text before the first `## ` heading (plus the BIBLE section), so the
load-bearing floor rules stay in that preamble. Every prompt change reports the before/after byte size
in the commit or PR.

Recoverable tool failures are evidence for the next LLM turn, not triggers for
a host-authored recovery workflow. Return a typed, redacted result naming the
failed stage, already-completed external effects, and an actionable repair
hint; the LLM decides whether to inspect, repair, retry, use another capability,
clean up, or stop. Host code remains responsible only for deterministic
integrity and authority boundaries plus truthful receipts; do not add
task-specific auto-retry, fallback, cleanup, resume, or terminal-flow state
machines.

Enforcement: the prompt-edit discipline is scored by CHECKLISTS item 13(b)
(a prompt edit never restates a tool schema and is never an incident patch);
the recoverable-failure boundary has no automated surface — review-only.

### Documentation contract

`docs/ARCHITECTURE.md` is the map of the body (BIBLE P6), written in the present
tense: what exists, where it lives and how it flows (structure and operation),
and WHY it is so. Every important WHY — cross-module, gate-level or module-local
— stays in the map, at least briefly (a second line under a module row or one
sentence in the owning section); mechanism detail beyond that lives in the
module's docstring, which the map points to by name and never copies.
`docs/DEVELOPMENT.md` is how the body is changed: imperative rules grouped by
change class, each naming the surface that enforces it (test, gate, CI lane) or
stating honestly that none does; mechanisms, reviewer criteria, constitutional
text and defaults belong to their owners (ARCHITECTURE, CHECKLISTS, BIBLE,
`config.py`) and are pointed to, not restated — the ARCHITECTURE settings and
endpoint tables are test-checked registries of those owners, not second
authorities. A change REPLACES the description of the node it touched; release
history lives in git and the README history table, leftovers go to issues.
Residue in the exact spellings of `DOC_RESIDUE_PATTERNS` — parenthesized version stamps, decision codenames, "used to / previously" narrative —
is caught by the shrink-only residue check in `tests/test_docs_sync.py`.

### Generality and emergence (P13)

Every non-trivial change picks a level: patch the case in front of you, solve
the class it belongs to, or build a framework for cases that do not exist yet.
The first fossilizes, the third speculates; aim for the second — BIBLE P13's
invariant question and stronger-mind question find it. The proof burden is
symmetric: promoting a case detail into shared structure requires showing it is
an invariant (several real variants, or one already-stable boundary); adding an
abstraction requires a demonstrated class — an imagined consumer is not one. In
doubt, generalize the meaning and the authority, keep the mechanism minimal and
local, and let the next real case pay for the next step. Reviewer findings are
evidence for this judgment, never policy that overrides it. No automated
surface — review-only.

### Pricing and admission

Never add hand-maintained model-price tables, inherited prefix tariffs, or
numeric fallback prices; preserve `cost=None` and `cost_final=false` when no
live source answers the exact route. Unknown price is neither free nor a
model-admission veto; known exhausted budget remains enforceable. Enforced by
`tests/test_pricing.py` (exact-route lookup, no prefix inheritance, unknown
cost stays `None`) and `tests/test_budget_limits.py` (a known exhausted budget
still fences); mechanism: ARCHITECTURE "Budget tracking".

### Anti-pattern: content-derived identity for host-minted records

If the host itself created a record — a chat message, a task, a binding — its
identity is captured at ingress and passed downstream BY VALUE as a typed
reference (e.g. `origin_message_ref` built where `log_chat("in", …)` writes
the canonical row). Never re-derive it later by searching logs/state for a
row whose text hash/equality/prefix matches: in an LLM-first system the text
is routinely rewritten between ingress and use, so content-derived lookup
fails exactly on the normal path. Content hashes are legitimate only as (a)
an INTEGRITY CHECK on an already-known identity, and (b) content-ADDRESSING
where the content IS the identity (artifact stores, observability blobs,
staged-diff review bindings). The enforcement shape is a REQUIRED typed
argument at the consuming seam (`bind_task_to_project(..., *, origin)`: a
valid ref or a closed-enum absence reason; omission raises), so a future call
site cannot silently skip the invariant — `tests/test_projects_v6640.py`
exercises that seam. For fuzzy entities use the LLM-first pattern
(`semantic_dedup`), never string equality.

One named exception inside role (b): a verification RECEIPT with no earlier
ingress point is reconciled by ONE TYPED IDENTITY KEY, matching on the key's
kind AND value, never across kinds — a per-component fallback chain is not an
equivalence relation (the outstanding set came out order-dependent), while
keying makes sameness the kernel of a function and fails safe: a false red
costs a human a second look, a false green costs the thing this surface
exists for. The full mechanism (masked-path rules, `IDENTITY_KINDS`,
projections, bounds, rendering stamps) lives in
`ouroboros/_outcome_receipts.py`, enforced by
`tests/test_v678_receipt_reconciliation.py`. Four rules generalize:

- **Whatever decides must be what is reported**: the reporting path reads the
  deciding path through one shared projection, never a re-derivation beside
  it — a host-attested artifact that misstates its own basis is worse than
  one that says nothing, because a reviewer cannot discount evidence whose
  provenance it was told wrongly.
- **A property of a closed set of kinds lives IN the set** (a table row per
  kind plus a total lookup that raises on a kind that skipped the table), so
  a new kind cannot be added without answering.
- **One canonical identity derivation** — comparison, hashing, counting, and
  projection read the same derived object, in the order canonicalize the RAW
  values → render → bound; a normalization that discards information the
  identity depends on is not a normalization.
- **Changing a stored rendering means versioning it**; reason about a format
  migration in BOTH directions — the false-green direction is the one that
  gets missed, and unknown must not clear a red.

Disclosed deferred limit: `tools/verify.py` bounds the DURABLE
`artifact_observation` path set at twenty with no omission count — advisory
only (a nudge and a disclosed reviewer flag, never a gate); fixing it means
changing the durable store and deserves its own scope. Beyond the typed seams
and tests above, this section is review-only.

### Mutable external-fact inventory

This table is a maintenance inventory, not a second runtime authority. External
facts change independently of Ouroboros releases; prefer live metadata or a
bounded probe where that can answer the exact question, and otherwise keep the
current conservative behavior visible. v6.67.0 documents these facts but does
not migrate their runtime representations. No automated surface checks these
rows — review-only maintenance.

| Location | Fact | Mutability | Current authority | Live/probe option | Risk | Recommendation |
|----------|------|------------|-------------------|-------------------|------|----------------|
| `ouroboros/provider_models.py::_VISION_MODEL_PREFIXES` / `_VISION_OVERLAY` | Which model families accept native image input | High as model families and route capabilities change | Conservative shipped prefixes, overridden by parsed OpenRouter `/models` `architecture.input_modalities` for exact model ids | Exact provider metadata when available; otherwise a bounded image-input capability probe | A stale positive sends unsupported image blocks; a stale negative needlessly captions them | Keep the conservative fallback and exact-model overlay; consider broader provider metadata only in a separately reviewed migration |
| `ouroboros/llm.py::supports_message_cache_control` | Which families support message cache controls | Medium/high as provider routing contracts change | Explicit family rules backed by provider behavior and dated live probes | Provider documentation plus a bounded cache-control send | A false positive can invalidate a request; a false negative loses the prompt cache | Retain the small explicit rules and re-probe when provider behavior changes; do not generalize by model-name resemblance |
| `ouroboros/reasoning_artifacts.py::SIGNED_PORTABLE` and its sealed classifier | Which families' SEALED reasoning artifacts (signed, encrypted, redacted, unrecognized) survive a same-model cross-provider replay; readable artifacts are portable by shape for every family | High; an upstream can bind a reasoning artifact to its endpoint without a routing-contract change | A short vouched family roster plus a shape-first classifier that fails closed on artifacts it cannot read | A same-model cross-provider replay probe of the exact family | A false positive 400s the replayed turn (the reactive strip-and-retry is the net); a false negative pins a portable transcript to one endpoint and forfeits same-model failover | Extend the roster only by a fresh cross-provider replay probe of the exact family, never by model-name resemblance; `openai/` was removed on 2026-07 field evidence despite an earlier passing probe |
| `ouroboros/provider_models.py::_ANTHROPIC_MODEL_ALIASES` / `migrate_model_value` | Direct-provider id spelling compatibility | Medium as providers rename ids and prefixes | Shipped compatibility mapping and current direct-provider id contract | Exact provider catalog/documentation can confirm a current id, but cannot establish whether a saved spelling was intentional | Removing an alias breaks upgrades; guessing aliases can silently reroute | Keep explicit compatibility aliases until a separately documented retirement window closes |
| `ouroboros/server_runtime.py::_RETIRED_MODEL_DEFAULT_REPLACEMENTS` and scope prior/legacy defaults | Which formerly shipped defaults are upgraded automatically | Release-dependent | Release history plus current `SETTINGS_DEFAULTS`; only known former defaults are migrated | A live catalog can show availability, but cannot infer user intent or whether a saved value was a default | Over-broad migration overwrites an explicit owner choice | Keep release-scoped exact replacements and regression tests; review retirement separately |
| `ouroboros/pricing.py::get_pricing` and `ouroboros/llm.py::fetch_openrouter_pricing` / `fetch_cloudru_pricing` | Exact-route model tariffs | High; pricing and FX drift independently | Exact provider catalog with nullable unknowns; provider-settled usage wins | Bounded live catalog fetch and provider-reported settled cost | Static prices look authoritative after becoming wrong and can corrupt admission | Preserve the live nullable design and cover it by regression; do not restore runtime tariff tables |

### Provider Independence

One configured provider must be sufficient for the agent loop, commit review,
scope policy, safety, and context/memory flows. Core capability must not
acquire a hidden OpenRouter or second-provider dependency. (CHECKLISTS item
2(h) and ARCHITECTURE both point here; this is the SSOT sentence.)

Tool-schema changes are provider-contract changes. Every shipped built-in
schema must pass general JSON Schema and the known cross-provider subset over
the complete registry; trusted integration CI sends that same registry in one
bounded tool canary per supported provider family/API surface, while
pull-request CI remains secretless. Malformed native arguments and invalid
schemas stay red, with diagnostics limited to structural facts, hashes, and
parse position. Do not add a prose parser, provider hop, or unbounded retry to
make that contract green (canary anatomy: ARCHITECTURE "CI topology").

When adding or changing a provider, update one coherent route contract:

1. credential/readiness detection and exact model-id migration;
2. Main/Light/Fallback and reviewer-slot defaults without overwriting explicit
   owner choices;
3. canonical tool/reasoning/image/cache intent at `llm.py`, with provider wire
   projection and exact-route recovery delegated to the small transport leaves;
4. nullable pricing/settlement and truthful capability omissions;
5. review and scope routing, including sourced context-window evidence;
6. direct-provider and single-provider regression tests.

Local-only installs keep their local route. Unreachable shipped remote defaults
may be cleared, but explicit owner values are not. Scope authority follows
BIBLE P3: owner-selected Max requires the applicable sourced window evidence;
owner-selected Low records the declared skip rather than pretending a partial
review occurred. Current model ids and defaults belong in code/config, not in
this handbook.

Use `provider_models.ACTIVE_MODEL_SETTING_KEYS` for any new active consumer
(provider detection, model catalog/provenance, credential planning, Provider
Test); `LEGACY_MODEL_SETTING_KEYS` exists only for migration/history. In
particular, `OUROBOROS_MODEL_HEAVY` and the paired `USE_LOCAL_HEAVY` may seed
an explicit configured API actor while the canonical list is absent, but must
never become an active slot, startup-readiness signal, test probe, or fallback.
Do not patch each consumer with its own Heavy exclusion; preserve the shared
split.

The `-pro` suffix is an OpenRouter routing slug, not an official OpenAI model
id; a direct OpenAI Chat slot uses the plain Sol id, because projecting the
slug into Chat Completions would turn an owner route choice into a guaranteed
404. This is a compatibility constraint, not a mutable capability table.

Provider-specific optional features may be unavailable on another single
provider, but the core loop must degrade explicitly rather than crash or
silently reroute.

Canonical assistant history and tool schemas are function-shaped across
providers; do not add a second stored transcript for a provider dialect. Direct
OpenAI tool conversations stay on Chat Completions — custom-first when
non-`none` reasoning is requested, an exact custom rejection may fall back to
function with the same effort, and explicit `none` is a task-local last resort
— and send `reasoning_effort` and `max_completion_tokens` provider-wide;
model-name prefixes are not admission authority.

All learned request-shape adaptation goes through the one provider-neutral
request-wire driver (`ouroboros/request_wire_contract.py`: exact-route
identity, closed action vocabulary, shared TTL, never executes provider prose
or switches route); do not add a second driver, and explicit `none` is never
durable. Direct Anthropic is the deliberate exception to a purely
reconstructed provider transcript: one private, route-bound byte-for-byte
replay receipt of the unfinished native tool turn
(`ouroboros/anthropic_native_custody.py`), scrubbed on any
provider/endpoint/API/model change and fenced from compaction. Do not
synthesize an effort-to-`budget_tokens` policy.

## Module Size & Complexity

P7 makes context fit a maintenance constraint, not a line-count aesthetic.

- Python modules everywhere (including `tests/` and `devtools/`) and
  first-party `web/**/*.js` modules (including `web/tests/`) target roughly
  1000 lines. The deterministic hard gates: 1600 lines per module (exact-path
  debt in `ouroboros/size_ratchet_manifest.py::GIANT_PATHS`; stale or newly
  oversized entries fail), 300 lines per non-grandfathered Python function
  (`FUNCTION_DEBT`, exact `(path, qualname)` keys), 200,000 UTF-8 bytes per
  module (`BYTE_DEBT`, shrink-only), and the exact-current 1001–1500 band in
  `BAND_PATHS` (a new or re-entered path requires a nonblank rationale).
  Regenerate the manifest with `scripts/regenerate_size_ratchet.py`; it
  validates the rendered candidate before writing and refuses an unmerged
  index with a typed error. Sources decode as strict UTF-8 and normalize to
  POSIX LF before counting, so checkout policy cannot change the inventory;
  vendored/minified assets are excluded, and the same production iterator
  drives the gates, smoke, health, and census.
- Methods above 150 lines and more than eight parameters are decomposition
  signals (BIBLE P7, CHECKLISTS item 2(c)), not deterministic gates. Existing
  baseline debt is not retroactively a failing tree. JavaScript currently has
  only the module line-count gate.
- Runtime Python function/method count stays under
  `ouroboros/review.py::MAX_TOTAL_FUNCTIONS` (that iterator excludes
  tests/devtools; the module gates include them). The ceiling is a high-water
  alarm with ample headroom; raising it requires a one-line campaign rationale
  in the same commit.
- Enforcement: the OFFICIAL repository's CI runs the dedicated `size_ratchet`
  pytest lane as a blocking third step in quick-test and full-test — manifest
  exactness against the tip tree plus the pairwise shrink-only transition
  against the event base (`OURO_SIZE_RATCHET_BASE_REF`). Local surfaces never
  block on size: the default pytest lanes exclude the marker, and
  `check_worktree_readiness` plus `codebase_health` report the same
  `validate_size_ratchet` findings as "official CI will enforce" warnings.
  There is no committed-history replay: the previous manifest resolves
  merge-aware from `HEAD` or any of its parents, and a checkout with no
  committed manifest anywhere bootstraps from its own tree — so a locally
  evolved fork can always take an official update without being trapped by
  structural debt it inherited, while the official line keeps ratcheting.
- Treat a size gate as pressure to reduce total complexity, not as a design
  reason for a helper or sibling module. First simplify where the change
  belongs: reduce control/data flow, delete dead, duplicate, or
  trivial-wrapper code, reuse an existing SSOT, and compact only redundant
  non-contract prose. Extract only when the new unit would still be the right
  boundary with the parent well under the cap: it owns a cohesive
  responsibility and explicit boundary, and is not a passthrough. Relocating
  the same complexity, or stripping contract-bearing comments, diagnostics, or
  tests to buy bytes, is not paydown. If neither a safe simplification nor a
  natural boundary exists, report the ratchet conflict instead of gaming or
  silently raising the cap.

### Pragmatic SOLID

SOLID is a direction for making changes legible to future agents, not a demand
for classes or extra framework surface:

- **SRP — Single Responsibility Principle:** keep one coherent reason and one
  clear authority for a unit to change.
- **OCP — Open/Closed Principle:** extend an existing stable seam when it
  preserves the contract instead of rewriting unrelated callers.
- **LSP — Liskov Substitution Principle:** an implementation or backend must
  preserve the caller-visible behavior of the contract it implements.
- **ISP — Interface Segregation Principle:** consumers should depend only on
  the capabilities they actually use, not a broad convenience interface.
- **DIP — Dependency Inversion Principle:** policy should depend on small,
  host-owned contracts rather than provider-specific or concrete details.

Apply these principles pragmatically. They do not require a class hierarchy,
DI container, numeric score, AST analyzer, or a new review pass. A SOLID or
minimalism finding must name the exact symbol or authority, the concrete
duplication or coupling, and a smaller alternative that still satisfies the
contract. Diff size, line count, and file count alone are not findings.
Enforcement: review-only — CHECKLISTS item 2(d) scores these rules in commit
review.

### Invariant: Projection over replay (hot readers of growing stores)

A reader that runs per INTERACTION — an HTTP request, a WS/SSE message, a poll
tick, a task turn — must not replay a growing store to produce its answer.
Interactive read cost must be O(response), achieved through a maintained
projection, a cursor, rotation, or a bounded tail — never a full-history scan
filtered down to the answer.

- **Per interaction is the unit.** Work that runs once per boot or per explicit
  owner action may scan history; work on a request/message/poll-tick/task-turn
  path may not. A scan that is cheap today is not the point — every growing
  store crosses the threshold eventually, and the reader degrades exactly when
  the system is most used.
- **Storage-agnostic.** A full-table read filtered in code IS a replay (a
  `SELECT *` narrowed in Python is the same failure as parsing a whole JSONL
  file for its tail), including unbounded collections INSIDE snapshot/state
  files.
- **Passive GET.** Read handlers perform no NEW steady-state durable writes.
  Exactly two named exceptions exist: (1) substrate-owned integrity repair
  under the substrate's own lock (the usage-ledger torn-tail quarantine in
  `ouroboros/usage_ledger.py`), and (2) one-time idempotent migrations guarded
  by a durable watermark (the legacy usage import). Anything else that "just
  materializes a bit of state" on a GET is a mutation hiding on a read path.
- **House precedents — reuse these shapes:** chat log rotation with
  archive-aware readers (`supervisor/state.py::rotate_chat_log_if_needed`);
  the compact `containment_faults.jsonl` projection maintained beside an
  unbounded event log (`ouroboros/delegate_custody.py`); the fingerprint-keyed
  render cache in `ouroboros/_usage_rows_memo.py` — a projection cached while
  its input is unchanged, invalidated only by advance/refold, never by TTL.

Enforcement: Repo Commit Checklist item 24 (advisory) triggers on diffs that
add or change an endpoint/poller/subscription/timer or read a growing store;
the hot-store growth health invariant
(`agent_startup_checks.py::hot_store_growth_notes`, surfaced by
`context_health.py::build_health_invariants`, thresholds justified in
`ouroboros/context_budget.py`) is the deterministic runtime tripwire. A change
that introduces a new append-only store read on an interactive path must
enroll that store in the `ouroboros/context_budget.py` threshold table (with a
justified constant) in the same commit — an unenrolled hot store is invisible
to the tripwire.

### Invariant: Source-complete decision pipeline

Every new or changed continuity surface is reviewed as one narrow chain:

`producer → canonical full source → bounded projection → consumer → decision → retention/GC`.

- The producer records the complete event or artifact before it publishes a
  projection or wakeup. The canonical source owns identity, order, bytes, and
  integrity state; a cache or hot index is never a second authority.
- A bounded projection names what it omitted and carries a source reference
  that the *same actor* can resolve through an existing reader.
  `source_complete` is a coverage fact, not a permission to infer missing
  material.
- A consumer that can authorize PASS, a destructive rewrite, or replacement of
  a full contract must materialize the named source first. A known `partial`
  marker and an unverified claim that some host might retrieve more are not
  equivalent: the latter is not actor-attested coverage and cannot release the
  decision.
- Retention and GC are part of the chain. Anything referenced by a canonical
  result, review, identity decision, or project summary is retained or promoted
  before its execution root can be collected; an unavailable legacy source is
  represented as an explicit gap, never silently treated as complete.

**Control-plane distrust is metadata, not a data-plane operation.** Paid model
output is evidence until a typed validity predicate fails. Control-plane
distrust — profile, route, parser, window — may lower authority to
DEGRADED/SKIPPED/NOT_RUN, but it must not blank, rewrite, or relabel the
artifact or its original cause.

Enforcement: CHECKLISTS item 25 `source_completeness` (critical when
applicable) scores the chain in commit review; the presentation-adapter
contracts below are pinned by the named web tests.

#### Review presentation adapters

`web/modules/review_presentation.js` (grouping, identity, ordering, typed
presentation state), `ouroboros/review_execution_projection.py` (the bounded
cross-domain `executions[]` wire), `web/modules/review_dom_patch.js` (keyed
in-place DOM reconciliation) and `web/modules/harness_presentation.js`
(harness identity marks and labels) are pure read-side presentation — they
never author, mutate, or feed back canonical verdict, lifecycle, routing,
attention, or enforcement authority, and never infer that a requested route
executed. Admission is source-complete: an incomplete row is omitted, never
guessed from chat, repository, timestamps, model, tool name, or activity.
Reuse the existing chat-history, task-detail, and canonical physical-attempt
readers; do not add a review ledger, endpoint, persisted UI state, cost copy,
or enforcement layer. Compact review rows carry no dollars; exact Skill
attempt money appears only in the lazy detail when the history row declares
`physical_attempt_v1`, joining the canonical ledger by exact wave and slot.
Reconnect and folded-group bounds exist so one history rebuild cannot fan out
unbounded task-detail reads. Pin these contracts in
`web/tests/review_presentation.test.js` and
`web/tests/harness_presentation.test.js`; module headers carry the per-module
contracts.

#### Context and growth matrix

| Store / surface | Complete producer and source | Interactive projection / consumer | Growth and retention proof |
|---|---|---|---|
| Background observations | `BackgroundConsciousness.inject_observation` → `state/consciousness_observations.jsonl` enqueue rows | Cached pending/oldest status and bounded `_render_observations` view; identity-update consumer reads the gap marker and source ref | `BG_OBSERVATIONS_WARN_BYTES` in `context_budget.py` / `agent_startup_checks.py`; append-only rows, including unacknowledged rows, are not GC-pruned by the hot-store warning |
| Chat and biography | Canonical `logs/chat.jsonl`, rotated generations, and dialogue blocks | Main/Project context and archive-aware `chat_history` | Rotation/archive readers carry generation/gap coverage; blocks are the compression path, not a deletion of the horizon |
| Plan/review evidence | Exact task-artifact/observability bodies and reviewer route/thread receipts | Bounded review hot index, obligations, and latest-wave status | Exact artifact refs and candidate SHA bind the decision; index rotation cannot certify a missing or partial wave |
| Task/project execution | Canonical task result plus promoted child artifacts and summaries | Status cards, terminal rows, and Main/Project summary projections | Canonical promotion precedes child-drive GC; disposable task scratch follows the unified retention owner |

### Invariant: Continuation authority and bounded Main projection

Continuation is an explicit relation, not an inferred chat-memory feature. The
router contract requires `predecessor_task_id`: an empty string means a fresh
task, a non-empty value means continuation, and omission or `null` is a typed
refusal before any lookup, enqueue, or provider spend. Queue snapshot/restore
retains the predecessor source, so a restart cannot silently turn the selected
task into a fresh one.

The authored continuation narrative is written at the result owner together
with its exact `get_task_result(include_authority=True)` source. Main's
provider projection is defensive: it deep-copies the authority, removes only
the current task's duplicate nested predecessor, and thresholds only the
closed raw keys `result` and `final_answer` using
`context_budget.PREDECESSOR_RESULT_INLINE_CHARS`; oversized values resolve as
persisted narrative, bounded exact-key legacy lookup, or an explicit
source-resolvable gap — never a raw head/tail slice, an invented summary, or a
mutation of the canonical result.

The startup injection is a bounded continuation ENVELOPE, not a body copy,
minted by the one producer `contracts.task_contract.bounded_continuation_envelope`:
the predecessor's contract core inherits without its nested
`predecessor_authority`, and every field is whole-or-pointer against one strict
serialized budget (previews carry `full_chars` plus a named `source_ref`;
`previous_task_id` keeps the chain walkable). Durable `task_results` bodies are
the untouched SSOT. The bound is per-field, so a pathological row can still
exceed the wire budget — the refusal is typed and loud rather than a silent
$0, and no hop cap exists anywhere: depth belongs to the mind, the floor only
keeps bodies off the wire.

Provider context overflow is a typed recovery fact: after the useful reclaim
and one strictly-smaller same-route retry, a final `context_overflow` skips the
provider-unavailable/forced-provider path, keeps
`execution_status=infra_failed` and `reason_code=llm_api_error`, and records
the typed acceptance bypass and `failure.error_kind`. Ordinary provider
outages keep their existing recovery behavior. Enforcement:
`tests/test_continuation_context_authority.py`.

### Invariant: UI resources carry a disposer

Every long-lived acquisition in `web/` returns or records a disposer, and a UI
instance owns a `destroy()` that releases everything the instance acquired.
The resource kinds this covers: WS subscriptions (`ws.on(...)`),
`document`/`window` event listeners, observers (`ResizeObserver`,
`MutationObserver`, `IntersectionObserver`), timers, `requestAnimationFrame`
loops, and `EventSource`/streaming connections.

An instance that can be closed, hidden, or replaced (project chat panels are
the canonical case) must be destroyable without leaving any acquisition
behind; "hide the DOM node, keep the handlers" is the leak shape this
invariant forbids. Late async continuations check a `destroyed` flag before
touching state or re-arming loops.

Enforcement (honest disclosure): the deterministic leak test runs in the
release-tier `ui_browser` lane, not at commit tier; commit-tier coverage is
the advisory Repo Commit Checklist item 24. The class is closed
deterministically for the instrumented surfaces and advisorily for future
ones.

### Invariant: Embedded surfaces declare geometry and refresh semantics

Every owner-visible embedded or framed surface has an explicit host-owned
geometry/overflow contract, a paired disposer for every long-lived resource,
declared refresh/stream/error semantics, and a named real-consumer visual
verification path. Intentional omissions record why they are safe to defer.
For Widgets, framed `height` values are bounded and module auto-height is
host-controlled. Below its finite ceiling, applying a reported block size must
not change the child's inline-size basis; the host owns vertical scrollbar
mode without disabling the orthogonal horizontal overflow capability, and
content measurement includes the measured document's bottom padding and border
(three separate feedback-loop bugs encoded as one rule).
Feedback-sensitive verification is event-driven on the relevant engine: it
proves temporal convergence to a quiet fixed point with a real consumer or
production-derived fixture that crosses the known wrapping threshold, rather
than comparing two snapshots. Module source loading and declarative requests
have a bounded host timeout; declarative job widgets keep their `job_id` and
bounded retry/timeout behavior visible in the refresh contract. Missing or
malformed job status is an immediate protocol error, while unknown non-empty
in-progress labels remain bounded pending states for producer compatibility.
Repo Commit Checklist item 24 points lifecycle changes here instead of
re-deriving a second domain-specific rule; the widget geometry/refresh
contracts are pinned in `tests/test_widgets_ui_static.py` and
`tests/test_extension_surfaces.py`.

---

## Core Governance Artifacts

`BIBLE.md`, `docs/ARCHITECTURE.md`, and `docs/DEVELOPMENT.md` are **core
governance artifacts** — the constitutional, architectural, and procedural
ground truth of the system.

### Invariant: Full availability in reasoning flows

Any flow that requires architectural, constitutional, or procedural reasoning
MUST include these artifacts as **first-class context sections** — not as
optional or opportunistic inclusions via touched-file packs.

Plan review is the one flow whose governance pack is tiered, and by ONE
structural fact — whether the plan's declared targets resolve under the
Ouroboros system repository — never by prose and never by a plan-kind
taxonomy, which is what keeps classification un-gameable. This is a tiering,
not an omission: before any work exists the reviewer's subject is the
INTENTION, and every absence is a named pointer or a typed `need_evidence`
finding the host attaches on the next cycle under the same evidence policy —
the locator enters the manifest hash, so the next envelope is a new
fingerprint, never an idempotent replay; nothing is silently omitted (P1).
DEVELOPMENT.md is not resident in a plan-review packet; it is one such request
away. Packet composition, bounds, and wave/replay mechanics: ARCHITECTURE
"Plan construction and review" and `ouroboros/tools/plan_packet.py` /
`plan_spec.py`.

The context-delivery registry:

| Flow | BIBLE.md | ARCHITECTURE.md | DEVELOPMENT.md |
|------|----------|-----------------|----------------|
| Main task context (`context.py`) | full tier-0 | full in Max for every task class; lossless navigation map in Low | mode-independent: full when the active binding targets Ouroboros's system repo, including evolution/self-body work and a project-room turn without an external binding; visible on-demand pointer for a bound external workspace, subagent, or API/CLI/scheduled external surface. `workspace="none"` and explicit self-body overrides retain full Development. |
| Triad review (`tools/review.py`) | ✅ via preamble | ✅ via `load_governance_doc` | ✅ via `load_governance_doc` |
| ↳ Anti-thrashing | — | — | Open obligations loaded from `review_state` via `load_state(drive_root)` + `make_repo_key(repo_dir)`, injected unconditionally into `_build_review_history_section` prompt context. Same mechanism in `scope_review.py::_build_scope_prompt` (best-effort when `drive_root` available). |
| Background consciousness (`consciousness.py`) | ✅ full | ✅ full (max) / navigation map (low) | — (not yet required) |
| Advisory pre-review (`tools/claude_advisory_review.py`) | Two delivery classes: an `api_chat` row runs the bounded NATIVE inspection episode (governance docs reached through its read-only tools); an `agent_session` row receives a resolvable pointer marked MANDATORY FULL READ and the session reads the full doc itself — retrieval is disclosed (native reads are host-observed; vendor-session reads are not) | same two delivery classes | same two delivery classes |
| Scope review (`tools/scope_review.py`) | full canonical doc + Atlas accounting | full canonical doc + Atlas accounting | full canonical doc + Atlas accounting |
| Skill review (`skill_review.py`) | full inline (`api_chat`) / mandatory full source-root read (`agent_session`) | full inline (`api_chat`) / mandatory full source-root read (`agent_session`) | full inline (`api_chat`) / mandatory full source-root read (`agent_session`) |
| Plan review (`tools/plan_review.py`) | full for a SELF-MODIFICATION plan (structural path fact: a declared target resolves under the system repo); otherwise a heading-derived navigation map of BIBLE.md generated at runtime (never a copy) | inline, in full, for a self-modification plan; otherwise the lossless navigation map + a resolvable pointer (W3) | named on-demand pointer; a reviewer that needs it returns `need_evidence` and the host attaches it on the next cycle |
| Deep self-review (`deep_self_review.py`) | full canonical doc + Atlas accounting | full (max) / navigation map (low) + Atlas accounting | full canonical doc + Atlas accounting |

Skill Review keeps the full stable governance/host prefix for cache-friendly
API rows; a retrieving session reads those same canonical files from its
source-repository root and receives the byte-exact dynamic tail inline, so the
payload snapshot and per-chunk quorum stay identical without rebilling or
crowding the session window.

Planning has two distinct roots: governance documents are always loaded from
the system repository, while declared targets and evidence locators resolve
against `active_repo_dir_for(ctx)`. Exact user-managed installed-skill payload
paths are the one data-plane exception for CLASSIFICATION only — they never
make a plan a self-modification — and are not attachable as evidence: the
resolver allows only the active workspace and the system repository, so a
payload locator comes back as a named `denied_path` omission. Any declared
path escaping the active subject, a workspace/subject mismatch, or an
unavailable root fails loudly with a named omission. Do not fall back to
reviewing the Ouroboros repo for an external plan.

The SPEC must state the goal, acceptance claims, invariants, in-scope and
non-goals, the load-bearing decisions with their rejected alternatives, and
what is consciously deferred. Plan review publishes exactly `GREEN`,
`REVIEW_REQUIRED`, or `REVISE_PLAN`; findings are inputs the main agent may
accept, reject, or defer. Closure happens without a second LLM call through a
separate `plan_task` call containing `review_disposition` only —
`{review_fingerprint, items: [{finding_id, decision, rationale}]}` — covering
every finding exactly once; duplicates, contradictions, unknown, stale, or
incomplete dispositions fail closed, and mixed or vacuous calls fail before an
attempt is recorded. Never replay the plan envelope with the disposition.
Blocking `REVISE_PLAN` requires changed plan text and another panel; advisory
may proceed only under loud host disclosure and the agent's rationale.
Reviewers are findings-only — they never author a competing plan — a blocking
finding must name the spec id it breaks, and there is never a required number
of findings.

Force-plan is an LLM-first pre-implementation obligation on the admitted
managed root, not a mechanical permission check. `plan_review_state` owns
durable review authority and `config.get_review_enforcement()` owns
blocking/advisory policy. Every submitted envelope that reaches `plan_task`
supersedes prior authority, so a newer attempt cannot fall back to an older
GREEN. Wave recording, free replays, DEGRADED semantics, structurally dead
slots, and `quorum_unreachable` release live in ARCHITECTURE "Plan
construction and review"; paid cycles are bounded by the shared
`OUROBOROS_REVIEW_MAX_CYCLES`. The short-lived Swarm router admits one new
root and transfers the intent; it never runs `plan_task`, steers an existing
task, or publishes the work inline.

**Context mode (Low / Max).** `OUROBOROS_CONTEXT_MODE` controls the
Architecture projection in the agent's own context: Max keeps `ARCHITECTURE.md`
full for every task class, Low supplies its lossless navigation map.
`DEVELOPMENT.md` is mode-independent and follows the active repository
binding — the signal is the repo binding, a path fact, never a guess from
message text (full for system-repo work including a project room with no
external binding; a bound external workspace, subagent, or API/CLI/scheduled
external surface receives a visible on-demand pointer; explicit structured
overrides win). Tier-0 identity and constitutional context stays full in every
mode. `context_fit.py` renders Max and Low from one immutable captured core on
one labelled density basis; Owner Low has an elastic 200K total-context target
whose miss is non-terminal. Predicted Max pressure never swaps in Low
documents — only actual provider overflow may use a task-local Low projection,
followed by at most one same-route strictly-smaller call, and this never
changes owner mode or P3 commit/scope review.

### Invariant: Compaction must earn its rewrite

Context compaction is a deficit-requested materializer, not an independent
threshold, timer, route, or retry policy. It first performs pure selection
over completed atomic units (one assistant tool-call message plus all and only
its contiguous matching results); user turns are hard boundaries; malformed,
missing, delayed, duplicated, visually opaque, or corrupt-capsule units remain
byte-identical. No eligible positive reclaim means no checkpoint, summarizer
call, or transcript mutation.

For a non-empty selection, persist the exact actor-visible checkpoint before
calling the summarizer. Summary input covers complete stable hashed chunks
with gap-free offsets; only typed summarizer context overflow may split a
source recursively. A replacement publishes only after transcript/unit
binding, complete coverage, checkpoint provenance, and a strictly smaller
representation on the caller's ContextFit measurement basis are all proved
(the bounded image proxy and density must match; raw base64 byte count is not
token reclaim). Capsules carry host-only generation, source-hash, part,
checkpoint, and CAS-ref metadata so a later pass can recompact them without
losing the original provenance union. Enforcement: `tests/test_compaction.py`,
`tests/test_loop_compaction.py`, `tests/test_loop_compaction_policy.py`.

### Invariant: No silent truncation

If a core governance artifact cannot fit in the available context budget:

- Do **not** silently omit it or truncate it without a visible marker. Either
  adjust the budget/flow to accommodate it, or emit an explicit warning
  (`⚠️ OMISSION NOTE: ARCHITECTURE.md omitted due to budget constraints`) so
  the operator and the model both know the context is incomplete.
- A reviewer or agent operating without ARCHITECTURE.md MUST NOT be treated as
  operating with full context — findings may be incomplete.
- Tools that return multi-model review findings (`commit_reviewed`,
  `skill_review`, scope/advisory review helpers) MUST be listed in
  `UNTRUNCATED_TOOL_RESULTS` or have an explicit per-tool limit; the default
  15KB transport cap is not acceptable for review verdicts.
- A reference-doc **navigation map** (H2-H4 inclusive complete-subtree ranges,
  with parent rows overlapping descendants and full sections one `read_file`
  away) and a named on-demand pointer are visible, lossless representations —
  NOT silent truncation. The low context mode uses these; it never applies
  `[:N]` to a doc.
- String bounding goes through the SSOT `utils.truncate_review_artifact`,
  never a hand-rolled `text[:cap] + marker`. Besides the marker, that helper
  carries an anti-waste FLOOR: a cut saving fewer characters than its own
  omission note is pure damage, so below it the text passes through whole. A
  local re-implementation loses the floor and can return a value LONGER than
  the input it "shortened". The two bounded-string primitives serve different
  contracts: `truncate_review_artifact` produces DISPLAY previews (its floor
  may return the text whole), while `truncate_within_limit` enforces a STRICT
  wire/prompt bound — the omission marker lands INSIDE the limit and the
  result never exceeds it.
- Bounding a LIST is subject to the same rule: a `[:N]` slice must be
  accompanied by an explicit omitted COUNT, and — where the slice touches an
  identity that something downstream compares — a durable hash or reference
  for the full set (see `_outcome_receipts.receipt_identity_projection`).
  Bounding a set is allowed; hiding that you bounded it is the P1 violation.

Enforcement: `tests/test_tool_capabilities.py` (the `UNTRUNCATED_TOOL_RESULTS`
roster) and the truncation-floor coverage in
`tests/test_owner_facing_honesty.py`.

### Invariant: Owner-facing surfaces show the full text

Disclosed truncation (the `⚠️ OMISSION NOTE` marker) exists to protect **LLM
context budgets** — it is a model-bound mechanism, not a licence to shorten
what the owner reads:

- **Owner/UI-bound surfaces** (chat panels, task_results projections, review
  verdicts shown to a person) present the COMPLETE text, or carry a reference
  to a durable full copy (e.g. an observability `response_ref`). Reviewer
  rationale is a cognitive artifact (BIBLE P1): projecting it truncated while
  the full copy sits unreferenced in private blobs is partial memory loss.
- **Model-bound projections** (review packs, context sections, tool-result
  transport) keep their disclosed-truncation budgets — those are real context
  economics.
- **A cut cheaper than its own marker is forbidden everywhere** (the shared
  primitive enforces the floor — see "No silent truncation"). One named
  exception: tiny single-line identifier fields (limit < 100, e.g. a
  reflection backlog `kind`) keep a plain hard slice — a multi-line omission
  marker inside a one-line value is worse damage than the cut it discloses.

Enforcement: `tests/test_owner_facing_honesty.py`.

### Invariant: No "only if touched" gate for core artifacts

Core governance artifacts reach review/reasoning flows unconditionally — NOT
only when they appear in `touched_paths`. `build_touched_file_pack` is for
_changed_ files; core artifacts are a separate concern loaded independently.
No surface of its own — the per-flow presence tests required below are the
mechanical cover; otherwise review-only.

### When adding a new reasoning flow

If you add a new flow that reasons about code structure, system architecture,
or engineering standards, you MUST:

1. Explicitly load `ARCHITECTURE.md` (and BIBLE.md if constitutional reasoning
   applies).
2. Log a warning if the file is missing or unavailable — do not silently skip.
3. Add a test asserting the file is present in the assembled context/prompt.

That required presence test is the enforcing surface; CHECKLISTS item 11
(`context_building`, advisory) backstops the review.

---

## Review & Commit Protocol

Reviewed commits separate cheap improvement evidence from authoritative
candidate-bound authority. The operator sequence: finish all edits, run focused
tests, run the advisory when useful, then freeze and review the exact
candidate — do not interleave edits with repeated review calls.
`docs/CHECKLISTS.md` is the only reviewer-question, severity, and output SSOT;
ARCHITECTURE "Review stack" owns the dataflow.

1. **Cheap advisory preflight.** After edits, `preflight_review` may find
   omissions before the expensive gate. Without an explicit skip,
   `commit_reviewed` requires fresh advisory coverage and no open advisory
   obligations or commit-readiness debt; any edit makes coverage stale.
   `skip_advisory_review=True` bypasses only these advisory admission checks;
   the skip is chosen by LLM judgment and durably audited with its reason.
2. **Authoritative gate.** Independently configured deterministic test policy,
   staged fingerprinting, triad review, applicable scope review, aggregation,
   and pre/post revalidation. The fingerprint binds `git write-tree`, ordered
   `HEAD`/`MERGE_HEAD` parents, indexed VERSION, expected `v{VERSION}` tag and
   existing target, plus the binary staged-diff hash.
3. **Publication binding.** The created commit/tag is checked against the same
   tree, parents, VERSION, tag, and reviewed fingerprint before push. Any
   mutation, rebase, conflict resolution, or changed landing parent
   invalidates exact-candidate authority and requires the applicable final
   gate again.

Triad slots review the staged diff against `docs/CHECKLISTS.md`; duplicate
model ids remain independent slots and `config.adaptive_quorum` owns quorum. A
managed-update resolution commit reviews the declared M0→S resolution delta
(`tools/review_subject.py`), bound to the index write-tree the fingerprint
pins. Scope slots inspect touched context plus the repository Atlas; the
assembler reduces optional and unchanged-diff context, records every
degradation, and fails closed when its irreducible pack cannot fit — an
artifact owed in full is reached only after the `-U0` rung and cannot buy fit
by degrading into an invalid review. Owner-selected Low records the distinct
BIBLE P3 scope skip; other route or assembly failure is not a clean verdict.
An agent-session scope slot delivers by retrieval: its verdict is
authoritative once its window is sourced at ≥200K, and "the host did not
observe which files it read" is a provenance disclosure, never a
missing-authority finding. The gate is one logical reviewer interaction per
API slot, with at most one bounded second physical send on a same-route
transport rail; a hosted agent-session slot is one multistep execution whose
local extraction reuses its collected transcript.

Paid review cycles across the gates are bounded by one shared owner knob,
`OUROBOROS_REVIEW_MAX_CYCLES` — a STRING, positive integer or `unlimited`,
default `"2"` (Settings → Behavior → "Max Review Cycles"). Its SSOT is
`ouroboros/review_cycles.py`, whose docstring defines the per-gate meaning
(the retired legacy key is migrated at settings load). `unlimited` removes only the local count —
deadline, budget, and lifecycle rails still bind — and a malformed value fails
closed to the default, logged once.

For task acceptance, the exact-binding tree-wallet claim is a strict
write-ahead stamp immediately before physical dispatch: panel assembly or any
pre-transport refusal consumes no claim, and an unavailable claim releases the
usage reservation and blocks every parallel panel slot rather than degrading
hard authority into fail-open cost telemetry. Task acceptance remains
API-only.

Never pay for byte-identical review material (`ouroboros/tools/commit_gate.py` owns
the mechanism): the commit gate refuses a byte-identical staged diff for free
from the FIRST verdict-block (`identical_diff_refused`, quoting the recorded
verdict), and skill review replays a recorded substantive verdict for an
identical snapshot at $0 while the persisted state still covers it. A rebuttal
is identified by CONTENT sha256 — a hash new to the streak buys exactly ONE
paid re-review; a repeated hash is refused free. The two axes stay distinct:
refusal-streak eligibility is about VERDICTS (a rebuttal is spent only by the
substantive verdict it bought), while money is about DISPATCH (every
physically dispatched wave counts whatever its terminal; infra facts refused
at assembly never dispatched and stay outside the count; the paid fact is
recorded write-ahead). Exhaustion is always the typed
`review_cycles_exhausted` event with honest exits — under advisory
enforcement a commit after exhaustion proceeds as a free replay with a loud
typed disclosure; blocking refuses it.

Scope of the review-contract fingerprint (deliberate): it covers the reviewer
roster, routes, enforcement, resolved efforts, and prompt constants —
including the session serialization only when Skill Review actually contains
an agent-session row — while governance-document CONTENTS — `BIBLE.md`,
`docs/CHECKLISTS.md`, `docs/ARCHITECTURE.md`, this handbook and
`docs/DESIGN.md` — are deliberately outside it, so editing those documents
neither lapses recorded verdicts nor frees replays. The accepted
trade-off is that an old verdict can replay under amended governance text;
this keeps routine documentation maintenance from repricing every recorded
review.

### External PR review is not commit authorization

The authoring agent freezes the final committed base-to-head range and gives
it to a separate agent context for read-only review; same-conversation
self-review does not count, and unavailable review is recorded `NOT_RUN`,
never silently presented as clean. `CONTRIBUTING.md` owns the public procedure
and evidence fields. `scripts/run_external_review.py --contributor` is
maintainer-grade large-window tooling: it freezes the configured triad/scope
rows, binds each row to its dispatched prompt receipt and observed response
receipt, and records exact base/head/tree/diff hashes, route/model/profile
facts, terminal settlement, capability deltas, and full redacted
agent-session transcripts; missing, tampered, drifted, unprovable, or
contradictory receipts make the packet `INCOMPLETE`. This evidence
establishes readiness; it does not authorize commit, push, merge, or
publication — maintainers choose the landing parent and release version,
preserve authorship, and run the normal final exact-candidate gate.

### Release sync

A pull request into `ouroboros` leaves every version carrier byte-identical to
its target: `VERSION`, `pyproject.toml`, the editable root version in
`uv.lock`, `web/package.json`,
`web/modules/api_types.js::GATEWAY_CONTRACT_VERSION`, the README badge and
latest Version History row, the named direct-download links in README and both
install pages, and the Architecture header. At integration,
`ouroboros/tools/release_sync.py::sync_release_metadata()` projects the chosen
version and `version_carrier_desyncs()` verifies the file carriers (the
history row is pinned by the packaging-sync test); changelog prose remains a
deliberate maintainer edit. The same projection owns the seven public
installer filename templates and rewrites the named direct-download links.
Those links use the immutable exact tag
(`/releases/download/v{VERSION}/...`), never
`/releases/latest/download/...`: prereleases are excluded from GitHub's latest
release, so a latest-style link would fail during an RC. The integration
branch may name installers that are not published yet; public onboarding uses
`main` and `main:/docs`, and stable promotion advances `main` only after the
release and all seven installers are public.

Hermetic preflight uses a disposable worktree, temporary
data/settings/pycache, and scrubbed runtime/secret-class environment. Tests
must rebind imported process-global roots and fail closed on the live data
root; setting only `OUROBOROS_DATA_DIR` is insufficient. A reviewed local
commit is the durability boundary; an `origin` push and CI are follow-up
signals, not prerequisites for local self-modification survival.

---

## Rules by change class

`docs/CHECKLISTS.md` remains the only reviewer scorer (its
`development_compliance` item points at this handbook as a whole). The
sections below are imperative rules per change class; each names its enforcing
surface or states that none exists.

### Tool registration and guard surfaces

- A new Tool: `get_tools()` exports it with the `ToolEntry` pattern from
  `registry.py`; an explicit entry goes in `ouroboros/safety.py::TOOL_POLICY`
  (`POLICY_SKIP` for trusted built-ins, `POLICY_CHECK` for opaque or
  outward-facing ones), and the capability class is declared in
  `ouroboros/tool_capabilities.py` (`CORE_TOOL_NAMES`, child profiles,
  parallel/truncation sets). Without the policy entry the tool falls through
  to `DEFAULT_POLICY = POLICY_CHECK` and pays a light-model LLM call per
  invocation. Add a tool to a child profile only when that narrower principal
  should receive it; test schema plus execution behavior rather than mirroring
  names into another catalog.
- A tool that WRITES the repo working tree needs the GUARD surfaces too, not
  only the visibility ones: add it to `_ROOT_ARG_REPO_WRITE_TOOLS` (the single
  set behind the acting-no-workspace fence, the protected-write gate, and the
  acting root-enum narrowing) and canonicalize its target paths — via
  `_PATH_NORMALIZED_TOOLS` for a top-level `path`, or
  `canonical_repo_relative_path` + `_payload_write_paths` for payload-borne
  paths. Visibility checks can all be green while these are missing, so tests
  must exercise the real guard chain, not only a mocked resolver.
- New memory/data files: decide whether they appear in LLM context
  (`context.py`) in the same change.

Enforcement: CHECKLISTS items 2(g) and 10 (`tool_registration`) in commit
review; the public schema/registry contract is pinned by
`tests/test_tool_api_v2_public_surface.py` and the safety-policy fallthrough
by `tests/test_local_routing_and_safety.py`; CHECKLISTS item 11 backstops the
memory/context decision.

### Skill repair and payload lanes

- Skill repair uses structured `task_constraint.mode="skill_repair"`, not
  prompt markers; edit paths are payload-relative. Use `edit_text` for one
  exact replacement and `write_file` (with `root=skill_payload`) for new files
  or intentional full rewrites; `edit_batch`/`apply_patch` are repo-lane tools
  and do not accept `root=skill_payload`. Finish with `skill_preflight` and
  `skill_review`; grants and enablement stay owner-controlled.
- Repair mode is a stricter UI lane, not the only authoring path: in every
  runtime mode, ordinary top-level tasks may mutate an exact user-managed
  payload via `root=skill_payload`, `bucket`, and `skill_name`;
  `skill_payload_binding.py` projects a markerless physical native payload as
  logical `external` while retaining its physical confinement. Marker-present
  launcher seeds, `data/state/skills/*`, marketplace/provenance/dependency
  sidecars, and direct `run_command` writes to repo targets remain blocked;
  the constrained `skill_repair` selector stays limited to
  `{external,clawhub,ouroboroshub}`.
- The direct `operator_control` and `local_readonly_subagent` profiles may
  inspect a selected native payload with `read`/`list`/`search` only; native
  mutation, owner state, grants/review/enablement, and acting-child selection
  remain closed.
- New path checks for skill edits use
  `ouroboros.contracts.skill_payload_policy`, never reimplemented bucket/path
  traversal logic.

Enforcement: `tests/test_skill_repair_hash_bind.py`; admission itself is the
skill review gate (`skill_preflight` → `skill_review`).

### Extension dispatch and isolated dependencies

- `type: extension` skills with reviewed isolated dependency envs must not
  import `plugin.py` or execute handlers inside `server.py`, even when the
  dependency tree looks pure-Python; payload-native marker files (`.so`,
  `.dylib`, `.dll`, `.pyd`) also force child dispatch — containment, not
  admission: a native payload still faces the skill-review checklist. Keep the split
  explicit: no-dependency pure-Python extensions may use `extension_loader`'s
  in-process PluginAPI; isolated-dep/native-marker extensions are cataloged
  and dispatched by `extension_process_runner` short-lived child processes.
- Proxies return normal tool errors / HTTP 502 / WS log messages on child
  crash, invalid JSON, timeout, or abort — a child `SIGABRT` is a handled
  extension failure, not a server crash. Children use scrubbed env, per-skill
  grants and isolated deps, process-group tracking, output caps, and timeout
  cleanup; do not add fallback code that imports native-risk plugin modules in
  the host process.

Enforcement: `tests/test_extension_dispatch_threaded.py`,
`tests/test_extension_isolated_deps.py`,
`tests/test_extension_process_runner.py`.

### Task contract resource policy

- `resource_policy.protected_artifacts` is enforced as a typed affordance
  policy in every runtime mode: execute-only black-box references may run;
  byte reads, copy/hash/static introspection, tracing, and debugging of
  declared paths are blocked.
- Observable Acceptance Claims are bounded, advisory, task-general criteria
  (`id`, `claim`, `surface`, `support`, `priority`); `success_criteria` is an
  input alias, not a second persisted carrier, and
  `effective_acceptance_claims` is the only binder; its read-time semantics
  live in ARCHITECTURE §11.1. A child receives only claims
  explicitly passed to its own `schedule_subagent` call. Reviewer
  `evidence_refs` resolve by exact membership in the already-built host
  packet — no fuzzy matching, filesystem reads, or re-execution — and
  resolution changes the clean bit and its disclosure, never actor parsing,
  quorum, or verdict. Do not turn claims into a hard acceptance gate or a
  surface-specific taxonomy.

Enforcement: `tests/test_protected_artifacts_policy.py` and
`tests/test_acceptance_claims_wiring.py`.

### Skill-defined Presence

- Keep behavior portable and authority installation-local: a reviewed
  `presence:` profile declares instructions, context topics, bounded runtime
  defaults, and conceptual tool/script/resource requests — never provider
  credentials, room ids, or one installed tool spelling.
  `presence_capabilities.py` stores the owner's exact selections outside the
  payload and fingerprints the request semantics that authorize them.
- Presence authority is a positive immutable ceiling, not a denylist or a
  prompt promise: admission requires the owner-created binding plus an
  installed, enabled, freshly executable behavior skill and every required
  selection, then freezes skill/profile/state/selection fingerprints, exact
  grants, argument bindings, runtime slot, and round limit into
  `task_contract.capability_ceiling`; schema discovery and execution enforce
  the same ceiling for built-ins, extensions, MCP tools, scripts, and
  resource roots.
- `state/presence_bindings.json` is host-owned authority: a transport token
  resolves only bindings naming that exact transport skill, and the submitted
  provider/account/conversation/thread must match the binding origin — never
  recover those identities from message text. Staged files stay inside the
  calling skill's state root before entering the ordinary attachment store.
- Run each admitted event with a fresh agent, a deterministic
  binding-plus-source-event task id, the cross-process installation-wide
  concurrency gate, and per-conversation serialization; the transport's
  durable provider custody owns arrival FIFO before Host admission. Do not
  add a transport-specific task scheduler, memory silo, core terminal outbox,
  or resident cross-room agent.
- Completion is exactly `message`, `silent`, `tool_delivered`, or `deferred`
  (deferred requires a successfully promoted `work_ref`; correlated lookup
  stays behind the same transport token and binding, and
  `presence_cancel_work` additionally requires the current binding and
  conversation to match). Promotion and `schedule_followup` copy the Presence
  metadata and capability ceiling by value; any new descendant producer
  preserves this ceiling or refuses the transition — reconstructing authority
  from mutable current state is forbidden.
- Knowledge-topic and scratchpad mutation each use one stable lock, so
  concurrent owner and Presence turns cannot overwrite a newer projection
  with an older render. Test the boundary at both layers (strict
  profile/state/ceiling parsing, stale/missing review admission, schema and
  direct-execution filtering, argument binding, binding/token/origin checks,
  event idempotency and conversation ordering, typed outcomes, late-work
  correlation, promotion/follow-up inheritance); provider adapter E2E is
  separate evidence. Enforcement: `tests/test_presence_admission.py` plus the
  both-layer boundary tests this list requires.

### Devtools isolation

`devtools/` is tracked operator code outside runtime package discovery and the
runtime import graph; runtime modules, `server.py`, web modules, and build
scripts must not import it. Touched devtool files receive normal triad/scope
review; unrelated files may remain manifest-only in broad Atlas packs so
operator code does not drown core review. Generated outputs live in an
explicit external root, never in `repo/` or live `data/`; domain-specific
architecture and methodology live beside the devtool, not in core governance
docs. No automated import guard — review-only (triad/scope review of touched
devtool files).

### Light mode and external deliverables

- `runtime_mode=light` is a self-modification boundary (`ouroboros/config.py`
  owns the semantics; ARCHITECTURE "Safety and runtime mode" states why). User-visible deliverables are allowed when they are outside the
  Ouroboros repo/control-plane.
- Preferred flow: `task_drive` for scratch, `artifact_store` for canonical
  deliverables, `user_files` for the owner's visible copy.
  `write_file(root=user_files)` and declared process `outputs` register/copy
  canonical task artifacts; rewrites keep the previous canonical artifact in
  non-manifest history with last-5 retention (history is for recovery, not a
  second deliverable list). The logical `root=deliverables` tool stays
  read/list/search-only and is not granted to children.
- For argv-visible targets, the shell guard checks lexical Deliverables origin
  before generic workspace or executor roots, then the symlink-resolved
  destination; direct `cp`/`mv`/`ln` directory destinations derive their
  immediate child target. The undeclared-output audit is best-effort, not a
  full shell parser: in-command `cd`, variable/indirect destinations, and
  inline-code path construction are disclosed parser residuals, and hardlinks
  remain a disclosed filesystem residual (`ouroboros/tools/shell_guards.py`,
  `ouroboros/tools/deliverables_shell.py`).
- `scratch=[...]` is a DISTINCT channel from `outputs=[...]`: ephemeral
  in-cwd verification files, exempt from the undeclared-output guard, never
  registered as artifacts, adopted only with a declaration-time sha through
  the SSOT `artifacts.record_task_scratch`, and excluded from the workspace
  patch via `.scratch_manifest.json`. The guard verifies candidates post-exec
  by stat, so a mere path mention is not a write. Use `outputs` for
  deliverables, `scratch` for throwaway verification — never overload one for
  the other.
- cwd: omitted cwd selects `active_workspace`; a light direct task that needs
  writable scratch selects `task_drive` explicitly; long-running services in
  light use an explicit external/task/artifact cwd, and declared service
  `outputs` are copied when the service stops. Directory outputs become a
  bounded manifest plus zip; hidden/control/credential-shaped files and
  excessive counts/bytes fail closed. `run_script` stages its temporary
  script under the active workspace (`.ouroboros/tmp_scripts`) for a
  workspace-bound script and under the task drive otherwise — never the
  system-repo temp path — so relative imports, generated files and toolchain
  discovery observe the requested cwd (`ouroboros/tools/shell.py`;
  `tests/test_shell_run_shell.py`).
- Policy denials stay separate from execution failures:
  `user_files_path_blocked`, `cwd_blocked`, and `artifact_output_undeclared`
  are non-failure outcomes; failing to register a declared output remains
  `artifact_output_error`.
- The default shell lane carries the same target-aware git policy in every
  runtime mode: mutating git is blocked only when it targets the Ouroboros
  runtime (bidirectional, casefold, symlink-resolved containment;
  `commit_reviewed` is the remedy for self-repo changes); read-only git works
  everywhere; the network fence still applies; acting `self_worktree`
  children keep the strict no-commit policy. `git init`/`commit`/`push` in an
  external project tree is legitimate task work, not a violation.
- In external workspace mode, light-mode self-repo dirty checks snapshot the
  system repo, not the active workspace; workspace patches are captured
  against the preflight git base. Project-room promotion provisions a
  standalone repo through `ensure_project_workspace` and fails loudly on a
  broken binding or unreadable registry.
- `claude_code_edit` is a retired tool name whose compatibility contract is
  one-way and permanent: a saved task contract carrying
  `disabled_tools=["claude_code_edit"]` also withholds the successor
  `delegate_start` (registry `_disabled_tools`). The successor path is the
  configured session actor — including the exact-payload class via
  `delegate_start(subagent_id=..., prompt=..., root="skill_payload",
  bucket=..., skill_name=...)` — and the api-route advisory successor is the
  bounded native inspection episode (`review_native_episode.py`). Do not
  resurrect the tool name.
- Successor parity rule: a tool may be called replaced, retired with a
  successor, or fully migrated only after a persistent golden test proves
  every user-visible target class the predecessor supported through the
  successor to the final outcome. Deleted-test tombstones prove intentional removal,
  not successor parity; dropping a target class requires an explicit
  owner-approved record naming the lost user outcome.
- Do not recommend `runtime_data/uploads`, skill payloads, or owner state
  directories as generic artifact transport.

Enforcement: `tests/test_v674_light_mode_cwd.py` (cwd selection and what light
refuses), `tests/test_deliverables_layout.py` (deliverable placement and the
output manifest), `tests/test_git_shell_policy.py` and
`tests/test_shell_redirect_guard.py` (the shell surfaces); the
successor-parity and artifact-transport rules are review-only.

### Runtime cleanup and retention

- Age-based GC of disposable runtime artifacts shares ONE owner knob,
  `OUROBOROS_GC_RETENTION_DAYS` (default 7, hard max 365), and the
  cutoff/clamp helpers in `ouroboros/retention.py` (`age_cutoff`,
  `clamp_retention_days`, `get_gc_retention_days`); do not hand-roll cutoff
  math in new prune code. Prune functions keep an explicit `retention_days=`
  parameter for tests; only the default (None) resolution reads the knob, and
  startup prunes are wired from one place (`server.py`).
- If a subsystem genuinely needs its own lifetime, name it
  `OUROBOROS_<SUBSYSTEM>_RETENTION_DAYS` and add it as a fallback in
  `retention.LEGACY_RETENTION_KEYS` — the migration-safe extension pattern —
  but prefer the unified knob. The three retired per-subsystem keys are
  migrated at `config.load_settings`; do not reintroduce them.
- Durable artifacts are NOT age-pruned and stay out of the GC sweep: genesis
  projects (`OUROBOROS_SUBAGENT_PROJECTS_ROOT`) and forensic observability
  blobs (kept compressed indefinitely).
- Review continuations are recovery state, not disposable GC: archive a
  record (collision-safe move, never delete; the archive has no runtime
  reader) only when its owner task is settled, it stayed un-resumed past the
  seven-day threshold, and no recorded obligation remains open; any
  uncertainty or move error leaves the live record intact.

Enforcement: `tests/test_phase3c_observability_gc.py` (the unified knob and the cutoff math) and `tests/test_observability_retention.py` (blob pruning); the review-continuation archive rule has no automated surface — review-only.

### Live subagents

Mechanism — bootstrap branches, zero-run receipts, custody, work orders,
supervision, recovery — lives in ARCHITECTURE "Delegated subagents (Claudexor
transport + the nanny)" and the module docstrings it names. Review gate:
CHECKLISTS items 18 (`subagent_isolation`) and 23 (`delegated_transport`),
both critical. The imperatives:

- Schedule only through `schedule_subagent`; its public schema and the
  handler's closed keyword set are BOTH derived from
  `control.schedule_subagent_properties()` — a hand-maintained mirror is
  correct only until one side gains a parameter
  (`tests/test_tool_api_v2_public_surface.py`). No new
  `contracts/task_contract.py` fields for child needs: the closed capability
  enum declares them, never objective prose; for internal-only options the
  membership test is WHO DECIDES, not who currently calls. Delivery is
  at-least-once — an exact task id with live or durable custody is an
  idempotent no-op; never use semantic duplicate judgement as the physical
  identity fence.
- `subagent_id` selects one complete row from the canonical enabled
  `OUROBOROS_SUBAGENTS` list; freeze the normalized row at schedule time and
  dispatch/restart from that snapshot, never from mutable Settings. No
  second model/lane/executor selector, no host-side ranking, no substitute
  actor after a typed refusal; legacy selectors stay hidden handler-side
  compatibility.
- The typed parent-LLM substrate choice is the floor (truth, money, and
  authorship stay where the parent put them); topology, decomposition, and
  supervision judgment remain the model's ceiling (BIBLE P5/P13). Never
  reintroduce a host-side wait, poll, or supervised-wait in bootstrap:
  waiting is the model's own `delegate_wait` decision, which keeps owner
  messages, hurry controls, checkpoints, and parallel auxiliary children
  live for the whole run.
- Grow `subagent_bootstrap._DEFINITE_UNRUN_REASONS` only with reasons that
  PROVE no run can exist; everything ambiguous wakes the model — a false
  "spent nothing" terminal over a possibly-live run is the one direction
  this classification must never fail toward. Zero-run receipts write only
  `incomplete | unknown` (a zero-run "complete" is unverifiable
  self-report); a substrate swap is a disclosed incomplete execution, never
  a silent vendor/API fallback
  (`tests/test_configured_session_prestart.py`).
- Work orders: one total 250,000-character wire limit, byte-complete or —
  only on a route whose live manifest declares a question channel — a
  compact source-request lens. Reader and validator share one renderer so
  the bytes the actor sees are exactly the bytes the host verifies; the
  manifest observation is a preflight, not a lease. `subagents.route_health`
  is the ONE route reader for every consumer; quota readers project one
  `ClaudexorGateway.quota_state()` envelope
  (`tests/test_available_subagents_runtime.py`). Substrate facts in the
  acceptance packet are VISIBILITY ONLY — acceptance judges quality, never
  the execution route — and an unreadable custody log reads
  `evidence_read_failed`, never a proven-empty substrate.
- `task_constraint` boolean parsing is strict (`"false"` is false); deadlines
  only narrow, delegation budgets only reduce, absent depth requests stay
  unknown rather than inferred from prose; preserve the persisted
  requested/permitted/attempted/achieved depth facts and never recompute
  historical permission from current Settings.
- `active_tool_profile` fails closed to read-only, never to
  `self_modification`/`operator_control`; `external_tool_grants` is
  deny-by-default; acting children keep commit, review, runtime control,
  tool-enable, skills lifecycle, and cognitive-memory writes blocked; only
  `schedule_subagent` may create subagents (forged `delegation_role`
  rejected at API/CLI ingress); live `memory_mode=shared` stays disabled
  (`tests/test_acting_subagents.py`). The subagent browser boundary is DNS
  fail-closed with the loopback control-plane carve-out
  (`tests/test_browser_isolation.py`; full rules: CHECKLISTS item 18).
- The parent is the SOLE committer of the live body: acting children return
  a `workspace.patch`, the parent applies a chosen patch with
  `integrate_subagent_patch` and runs its own `commit_reviewed`. The shared
  `external_workspace` surface verifies and records without re-applying; a
  genesis project is durable because the project directory IS the
  deliverable. The canonical/replica terminal field-custody projection is
  ONE pure reducer reused by copy-back and effective reads — every change
  adds a stale-replica regression at BOTH seams
  (`tests/test_available_subagents_runtime_review_fixes.py`). Do not broaden
  generic data-tool behavior while fixing subagent isolation
  (`forward_to_worker` writes only to validated running tasks in the
  current task/root lineage).
- Outcome honesty: a delegating parent must not produce a clean no-tool
  final answer while direct children run undecided — one bounded absorption
  reminder, then best-effort (`children_unabsorbed`); while that gate is
  open the delivery candidate is HELD, and the delivery-control instruction
  never rides the reminder round, which would contradict the required
  disposition tool call (`tests/test_v6570_swarm_honesty.py`). `wait_tasks`
  stays batch-compact (`task_id, status, cost_usd` with its honest alias
  `accounted_upper_bound_usd` and `cost_final`, `child_result_sha256`,
  `outcome_axes`, `result`,
  `trace_summary, capability_delta when disclosable, duplicate_of`); full
  untruncated handoff belongs to `get_task_result` and `wait_task`; no
  shared ledgers, automatic memory merges, or new settings/endpoints unless
  the accepted plan calls for them. Push/live events are wakeups, not
  terminal authority — lifecycle changes must exercise lost/reordered
  terminal frames and reversed snapshot completion.

### Cancellation and effective status

Mechanism — durable intents, the claim/generation fence, the one settle
owner, owed terminal delivery, cascade postconditions — lives in ARCHITECTURE
"5. Supervisor Loop". Enforcement: `tests/test_cancel_intents_phase_a.py` and
`tests/test_cancel_cascade_v664.py`. The imperatives:

- Effective task status belongs in `ouroboros/task_status.py`; never duplicate
  child-drive merge or terminality logic in gateways/tools. Task waits use
  `SETTLED_STATUSES` and structured facts plus queue-heartbeat freshness —
  never keyword matching.
- Cancel INTENT is never a status value. Every cancel ingress writes a durable
  intent through `ouroboros/cancel_intents.request_cancel` and FAILS CLOSED
  when that write fails: a cancel without a durable, watchdog-replayable
  intent is refused with a typed error, never run unfenced (an evolution-stop
  whose intent write fails keeps the task and reports INCOMPLETE). A settled
  RESULT does not mean a dead WORKER: every ingress checks live physical
  ownership and passes `allow_settled_target` while a live row remains; the
  recorded scope is widen-only.
- Natural completion WINS a late cancel: a completed result is never
  overwritten or stripped — discarding is the parent's separate explicit
  `discard_child_result`. Timeout reaping is deliberately NOT a cancel
  ingress: the reaper keeps its own custody over the shared `reaping` slot
  marker and mints no intents.
- The intent and delivery registries read STRICT to rows: a malformed row
  refuses the mutation (bytes kept), and enforcement reads disclose once and
  quarantine. `task_done` validates through the DURABLE result
  unconditionally for every non-ephemeral event; only `interrupted` keeps its
  restore-path exemption, and the legacy `cancel_requested` status survives
  on a read-path only.
- `stop_policy` is an axis on the durable intent (absence = IMMEDIATE;
  `finalize_then_cancel` = 202-pending plus one bounded episode owned by
  `supervisor/owner_stop.py`; transitions are monotonic — immediate hardens,
  graceful never softens). The owner hurry control is typed and TASK-LOCAL:
  `kind=hurry` through the owner mailbox only — never a chat message, a
  global settings mutation, or a review-gate weakening; its durable
  projection writes only through `update_json_locked` on the `owner_hurry`
  keys, keyed by `task["_attempt"]`, and every same-id requeue producer calls
  the ONE shared `owner_hurry.retry_reset`. UI surfaces share
  `web/modules/task_control_menu.js`; the `owner_hurry` event family stays
  non-chat (`log_events.js` `visible=false`).
- Code owners stay narrow behind one public queue/lifecycle surface:
  retry-aware target/subtree-liveness in `supervisor/queue_transitions.py`,
  capture-miss terminalization/publication in
  `supervisor/cancel_publication.py`, owner-stop delivery/validation in
  `supervisor/owner_stop.py`.

### Onboarding and Settings surfaces

- One capability, one section: the task-actor story lives in Agents →
  Available subagents (`web/modules/subagents_settings.js`), editing one
  canonical `OUROBOROS_SUBAGENTS` object (list-level Enabled, at most ten
  stable rows, one prose field `recommended_use`; id and compatibility name
  stay automatic and hidden). Never derive durable identity from the visual
  ordinal — removing a preceding row must not change task snapshots or
  receipts — and never render a second control over the same settings key
  (`OUROBOROS_MAX_WORKERS` stays in Advanced because it sizes the process
  pool). Share only neutral route/model/account/effort/status primitives with
  reviewer rows (`route_editor_primitives.js`); task routes serialize
  `api_model` + `credential_profile_id`, reviewer routes `api_chat` +
  `profile_id`; an empty session pin means engine rotation;
  saved-but-undiscovered choices stay visible and editable; a compound effort
  slug plus a conflicting separate effort is a validation error, never two
  applied efforts.
- Saved intent, generated drafts, and live status are different axes: a
  status/catalog failure annotates a loaded row and never erases it; GET may
  return an unsaved candidate but only explicit Save or onboarding completion
  materializes it; a late preview updates a clean generated baseline only,
  never absorbing owner edits or dropping focus/caret.
- Owner switches expose the semantic choices the owner can actually make: for
  `OUROBOROS_ALLOW_MUTATIVE_SUBAGENTS`, Settings presents Off / Auto / On —
  Auto IS the unset, surface-aware runtime-mode default and saves the empty
  value (semantics: `config.get_allow_mutative_subagents`).
- Onboarding completes in ONE transaction: `POST /api/onboarding/complete`
  persists settings, the next-boot runtime mode, the fresh-install safety
  default, and the subscription preset in a single write; `GET
  /api/onboarding` normalizes for display and must never persist — a read
  that authors `settings.json` destroys the fresh-install latch. There is no
  second completion path on any host, and the client treats only the exact
  success envelope (`ok`/`runtime_mode`/`restart_required`) as completion: a
  2xx whose body will not parse is a failure the wizard shows, because a
  silent success discards the restart receipt. A failure after the bytes
  reach disk says so rather than claiming nothing was saved.
- There is ONE wizard host: the `GET /onboarding` page served by the gateway
  and loaded as an ES module from `/static`; do not reintroduce a pre-server
  or inlined copy. The frame is sandboxed WITH
  `allow-popups allow-popups-to-escape-sandbox` — a sandbox without those
  tokens blocks the sign-in click silently — and this is asserted
  behaviourally from the login card's own markup
  (`web/tests/onboarding_overlay_sandbox.test.js`). Onboarding and Settings
  share the setup contract; diagnostics must account for unsaved in-memory
  wizard values.
- Install-time defaults are compiled from LIVE discovery with typed refusals,
  never guessed, never half-applied, never re-derived after onboarding.
  Install time is a conjunction of three proofs — no recorded completion
  (`OUROBOROS_ONBOARDING_COMPLETED_AT`), no preset generation, no
  `settings.json` — because "no working provider" is a state an old install
  reaches too. A once-only decision is never taken on a moment-in-time
  reading: a subscription whose window is spent during onboarding stays in
  the preset, and the `next_up` verdict is read dual-wire (unified
  `accountPools` first, legacy per-harness second, never re-derived from the
  profile list; an unknown kind is a fail-safe refusal).
- Agent sign-in consumes the harness row's `setupLogin` field as four states
  (absent = legacy catalog; null = the pinned engine's typed
  `setup_job_admission`; a valid object selects `in_app` or
  `external_terminal`; malformed present data is a gap); never add a
  harness-name branch for this choice. External-terminal recovery binds its
  argv to the live handshake's exact engine identity and requires the fresh
  `--probe` to advertise `setup_attach`; render the argv through the owning
  `claudexor_daemon.py` consumer and never execute the text.
  Credential-profile DELETE remains a thin receipt-preserving proxy; mirror
  additive response fields in Python TypedDicts and
  `web/modules/api_types.js`, and extend field parity plus fixtures together
  (`tests/test_gateway_parity.py`).
- Owner settings writes go through `gateway/owner_settings.py`. The settings
  lock is a PRECONDITION of the write, not a hint: `_acquire_settings_lock`
  answers `None` on timeout, and a writer that proceeds anyway is unlocked
  while claiming to be atomic. Once the bytes land, the response says so —
  carry a `CommitBoundary` through the write and report a later failure as
  that step failing, never as a failed save (BIBLE P1); `saved` is a FIELD on
  both sides, and pre-commit refusals answer through `unsaved_error`.
  `owner_write_guard` belongs only on endpoints that call
  `_owner_write_settings`.
- A setting only an ENDPOINT may author is disk-only in BOTH directions:
  `config.ENDPOINT_AUTHORED_SETTINGS` is consulted by the loader and by the
  environment projection, and the generic save's merge skip-list reads the
  same set — blocking only the request body is not enough, because an
  env-suppliable install-time fact closes its own window before the endpoint
  runs.
- A control the owner cannot use is worse than none: with no agent
  subscription the panel shows truthful configured/generated API or local
  actors and the session chooser points at Accounts instead of inventing a
  route; a saved unavailable session stays visible, and dispatch returns its
  typed refusal, never an API fallback. Harness lists come from one catalog
  path (`accountRows` over `/api/claudexor/status`; dual-shape
  unified/legacy, pins via `indexProfilesByHarness`).
- Install compilation stays linear and split by semantic owner: available
  subagents include every supported connected harness plus truthful API/local
  actors; reviewer defaults independently consume only ratified policies;
  fresh-install reviewer slots are `subagent_id` references into the shipped
  roster (unmatched seats mint `review-<harness>` rows) while an
  owner-configured roster is validate-only. API-only/local-only compilation
  performs zero Claudexor reads; never fabricate diversity or build a
  harness/account/model powerset. `POST /api/onboarding/subagents/preview` is
  the read-only compiler surface; completion commits the visible owner-edited
  value.
- Owner-facing copy says "agent", never "coding agent" — the same
  subscriptions build presentations and run arbitrary tasks; product names
  (Claude Code, Codex, Cursor) are trademarks and stay as they are.

### LLM call rules

Accounting and transport mechanism — attempt lifecycle, pricing lookup, lock
discipline, snapshots, projections — lives in ARCHITECTURE "Budget tracking"
and "Usage ledger substrate vs. accounting policy"; route contracts (Chat
Completions dialect, request-wire driver, Anthropic native custody) are owned
by "Provider Independence" above. Call-site imperatives:

- New LLM calls go through the shared `LLMClient`/`llm.py` layer — no ad-hoc
  HTTP clients or direct provider SDKs outside it (review gate: CHECKLISTS
  item 2(e)). Exception: skill/extension `plugin.py` modules may call
  providers directly until a host-mediated bridge lands; runtime callers
  inside `ouroboros/` must use `LLMClient`.
- Keep canonical messages/tools provider-neutral and function-shaped; a
  provider dialect is an outbound projection plus inbound normalization only
  and must not mutate stored history or create a second compaction/replay
  contract. Custom-origin receipts stay private and catalog-bound
  (`ouroboros/request_wire_custom_validation.py`); wire-dialect recovery
  uses the one request-wire driver, ladder ordinals stay fixed at 1/2/3,
  custom→function is never persisted as learned dialect, there is no
  Responses migration, and owner `none` on direct Anthropic is
  `thinking.type=disabled` (`tests/test_request_wire_contract.py`,
  `tests/test_openai_chat_custom_contract.py`,
  `tests/test_anthropic_native_custody.py`). `usage.request_wire` describes
  one call's terminal candidate; nested aggregation preserves ordered
  `request_wire_history` with explicit omission accounting.
- Every core-mediated physical provider send goes through
  `usage_accounting.execute_physical_attempt[_async]` (reserve → dispatched
  → settle/unresolve; `tests/test_usage_accounting.py`). A marked dispatch
  is released only by a typed pre-dispatch failure proving no request bytes
  were sent; a transport retry is a new attempt; projections carry attempt
  ids and are never a second monetary authority; unknown price reserves
  `None` and never blocks a model. An external skill bypassing core
  transport is unknown/unmetered, never `$0`. Custody classifiers use the
  explicit `__cause__` chain, never Python's implicit `__context__`; an
  ambiguous timeout remains unresolved (`tests/test_transport_custody.py`).
- Hold the usage-ledger cross-process lock only for budget check, validated
  append, and fsync — never over network I/O; preserve a paid response when
  settlement persistence fails and leave an honest dispatched/unresolved
  bound. Callers that own a finalization reserve pass it explicitly so
  admission and the transport bound cannot disagree.
- Tree-spend pacing decides on root-subtree ledger spend including in-flight
  holds; own cost is a disclosed lower-bound fallback, and unavailable is
  unknown, never `$0`. Refresh `usage_accounting.last_root_accounting` only
  at rare cache-breaking decision surfaces, never per round or inside a
  stable cached prefix (`tests/test_budget_limits.py`). Post-task
  consolidation/synthesis reads `usage_breakdown` once per root subtree and
  passes the same snapshot to summary and reflection; it is explicitly
  non-final because those flows have not spent yet — treating a read
  failure as `$0` would create false accounting certainty. No second
  ledger, no reconciliation LLM call.
- Runtime notices after the first user/assistant/tool turn are user notices
  (`[SYSTEM NOTICE]`), not new `role=system` messages; `LLMClient`
  defensively demotes non-leading system messages at the provider boundary.
- **Cache-friendliness invariant.** Byte-stable governance and task contracts
  precede mutable evidence; never place timestamps, hashes, counters, or
  task identity in a stable cached prefix — they fragment provider caches
  while conveying no stable policy. Builders declare bare breakpoints
  (`review_substrate.assert_cache_breakpoint_cap` keeps the count at four or
  fewer; `tests/test_review_prompt_caching.py`); only
  `LLMClient._normalize_payload_cache_ttl` finalizes the assembled wire
  payload. Prompt-cache support stays deliberately narrow — no provider
  hops, body rerouting, or a generic cache/retry framework. Review gate:
  CHECKLISTS item 22 (`cache_friendliness`).
- Provider fallback is disabled only when the transcript carries a SEALED
  reasoning artifact
  (`ouroboros/reasoning_artifacts.py::transcript_has_sealed_reasoning`),
  because only a sealed artifact is bound to the endpoint that minted it;
  readable reasoning stays failover-eligible for every family so one
  endpoint's outage does not strand valid work
  (`tests/test_llm_provider_routing.py`).
- Delegated agent sessions and the native review inspection episode preserve
  the full governance prompt; do not truncate
  BIBLE/ARCHITECTURE/DEVELOPMENT/CHECKLISTS to fit argv or transport limits.
- Delegated (subscription-harness) work is accounted on its OWN ledger row —
  `usage_accounting.record_subscription_session`, never
  `record_unmetered_external_dispatch` (it drops the sessions/quota axis).
  Its cash has three states and only the first is final: a disclosed zero
  settles `cost_usd=0.0, cost_final=True`; an estimate rides as money but
  never as finality; an undisclosed spend is `cost_usd: None`, counted
  unknown/unmetered, never a confident `0.0` — and token `None` means no
  harness reported it, not a run that used zero
  (`tests/test_gateway_usage_accounting.py`). Skill Review waves attribute
  every canonical usage row with the exact wave/slot identity; pre-marker
  waves stay "exact attribution unavailable" and are never reconstructed by
  time/model (`tests/test_skill_review_usage_accounting.py`).
- `cost_final` on a projection is a COUNT of open rows (`non_final_rows`),
  never a truthiness test on a dollar sum. A spent subscription window is
  `subscription_window_exhausted` — a TRANSIENT class carrying `reset_at` —
  never folded into `quota_exhausted`, which is correctly permanent for a
  billing refusal and wrong for a window whose only cure is waiting
  (`tests/test_reviewer_slot_config.py`).
- Classify provider failures before retrying the same request:
  quota/auth/billing, hard bad-request, and request-too-large failures are
  non-retryable as-is (record the exact category and surface a recovery
  hint); a typed 408/429/5xx or a failure proven pre-dispatch may retry; a
  dispatched request with no terminal provider outcome stops same-model and
  cross-model sends until reconciled.

#### Timeout & Wait Control

- For a session nanny, `delegate_wait` is event-only at the model surface:
  host supervision renews bounded transport windows with zero LLM calls,
  journal progress streams to the owner without waking the model, and only
  terminal/interaction/fault, an addressed task/owner message, a direct-child
  signal, control/recovery judgment, or a model-requested one-shot
  checkpoint wakes it. Do not reintroduce caller-visible `wait_sec`,
  repeating timers, progress wakes, or a host semantic stall detector.
- The wait/continue/stop decision is a structured fact — terminal status plus
  heartbeat freshness from `queue_snapshot.json` via `task_status.py` — never
  a keyword or regex over content (BIBLE P5). Fixed kill-timeouts (hard
  task/tool ceilings, watchdog) remain the outer safety bound; progress-aware
  waiting tunes the passive wait only.
- Timeout contract classes differ; keep the axes separate. A transport
  timeout only bounds a dead socket
  (`OUROBOROS_LLM_TRANSPORT_READ_TIMEOUT_SEC`) — it is not a reasoning cutoff
  and never evidence that reasoning stalled. API review uses its transport
  bound as a settlement fallback because that request ends there; a delegated
  agent session inherits the task absolute ceiling because the paid engine
  run can outlive an HTTP read; the owner deadline always narrows either
  route, and provider transport defaults (Anthropic, VLM captioning) are
  ceilings, not promises to run past it. Default reviewer slots intentionally
  have no short cognition cap; the outer `plan_task` envelope covers the
  session lifetime; `web_search` sizes its outer envelope for the complete
  configured paid cascade, recomputed under an owner deadline.
- New numeric timeout constants are an SSOT in `config.py`
  `SETTINGS_DEFAULTS` with a getter and env registration; do not scatter
  magic wait numbers across call sites (`tests/test_timeout_policy.py`).
- Nested process wrappers are ordered, never tied: the provider bound settles
  before its killable child, the child before the generic ToolEntry envelope
  (fixed structural settlement margin from `config.py`), so a child or
  provider result cannot arrive after its owner has abandoned custody.
- Every physical LLM/review/VLM/tool operation that can outlive a logical
  wait emits typed `cognitive_operation` start and terminal facts; the
  supervisor uses the active-operation map only to spare the idle rail, and a
  terminal fact must match task-attempt plus execution/round/call identity
  before it clears the row. A logical timeout with a live paid worker is
  custody/reconciliation-pending, never permission for a blind paid retry;
  late results settle the original attempt and stay bound to its retry
  identity.
- Once the owner deadline minus finalization reserve is spent, an unstarted
  review row is a typed `$0 not_dispatched` actor — no worker, paid stamp, or
  active lease; an already-paid in-flight wave stays eligible for exact
  custody reconciliation without authorizing a new dispatch. An in-flight
  reviewer never counts as final quorum, under either enforcement mode.
- A returned provider response (including an empty body) or typed terminal
  408/429/5xx is settled and may use the surface's bounded retry rail;
  `dispatched`/`unresolved` without a typed terminal status stays under the
  custody-lost/no-resend classification. Positive capture evidence outranks a
  contradictory synthetic `not_dispatched` label; across one bounded rail,
  retain the strongest earlier capture — any unknown prior outcome
  monotonically forces no-resend. A dispatched request whose socket or
  stream ends without terminal provider evidence is
  `provider_outcome_unknown`: THAT request is never resent by any route, its
  `unresolved` ledger row is terminal, and a NEW logical request is legal
  only with a unique host-attested input absent from the unknown one (e.g.
  the nanny-leaf hold contract in `ouroboros/delegate_hold.py`).
- A custody retry key names semantic material and an admitted cycle, not its
  rendered prompt: prior-round scaffolding may change while the same physical
  operation settles and must still join it; changed snapshots, owner intent,
  route/model rows, or a genuinely new cycle mint a new key. Skill Review
  keys additionally bind the exact skill, lifecycle wave, content, and frozen
  chunk digest/index. Commit review writes `paid=True`, the exact retry key,
  and both complete slot rosters with reserved operation ids in one locked
  write before either parallel surface starts; a window with no dispatch
  capacity leaves an unpaid `$0` wave and no paid stamp.
- A reviewed mutative wrapper retains foreground custody until the workflow
  settles; never use the generic 600s tool default or a guessed hard ceiling
  to abandon a still-live reviewer or commit pipeline.
- Cooperative cancellation applies where the route supports it (delegated
  sessions); API/thread routes disclose an in-flight custody state until the
  physical result settles. A typed transport failure after a delegated run
  has an id is an unknown outcome — retain the durable invocation token and
  replay that started run on the permitted retry instead of posting a second
  paid run; a supplied retry token with no valid durable invocation is
  `review_custody_lost`, never permission for a fresh paid session. Late
  settlement stays in custody: while the process lives, unknown local
  custody is a no-resend tombstone; a later process startup settles a
  tokenless local waiter as a typed paid infrastructure failure, while rows
  with durable delegated tokens stay pending for exact rejoin. Elapsed TTL
  alone never authorizes a resend, and a paid process-local review belongs
  to its exact process identity (server session + pid) — a new Agent, a
  sibling worker boot, heartbeat silence, or elapsed time is not owner
  death.

### Loop and acceptance state machines

#### Loop / State-Machine Changes

- Changes to `loop.py` or other task state-machine logic include adversarial
  tests for malformed output, false-completion prevention, replay/log
  durability, and failure modes — not just the happy path. Audit/checkpoint
  rounds must not silently reuse the normal final-answer path unless that
  invariant is explicitly tested and documented.
- Keep a complete loop-local `DeliveryCandidate` once a substantive answer
  exists, with host control exposure as sticky candidate provenance inherited
  through every replacement (mechanism and the disclosed test-pinned
  residuals: ARCHITECTURE "Task lifecycle" and
  `ouroboros/delivery_protocol.py`). A FORCED finalization resolves an armed
  control purely and without retry: valid keep/replace is honored, anything
  malformed preserves the retained candidate with a typed degraded reason,
  and protocol JSON never reaches chat or the durable result. Owner
  messages, tool effects, child results, and verification receipts advance
  the evidence revision and require fresh delivery/acceptance binding;
  finalize task-scoped service outputs/errors before host acceptance. The
  control must not bypass verification, acceptance, safety, skill
  finalization, deadline, child handoff, the unconditional `FINAL ANSWER:`
  latch, or the task-level answer protocol.
- Every direct child result needs an exact-hash disposition through the
  existing `tree_note(kind="decision")` tagged payload
  (`type=child_result_disposition`; the batch form validates entries
  individually by index). The typed task-tree row is the sole authority;
  task-result disposition fields are derived reads, never a mirrored write.
  Binding the complete-result SHA-256 means a parent cannot claim it
  integrated a result that later changed. `deferred` suppresses only the
  reminder and forces an honest degraded/best-effort terminal answer until
  resolved. A child wedged in the legacy `cancel_requested` latch is intent,
  not outcome — it stays visible as cancel-pending until custody settles it.
- Host task acceptance is root-only; eligibility uses structured facts
  (`outcomes.turn_has_reviewable_effects` plus a typed
  deliverable/criterion), never keywords (BIBLE P3/P5). The agent-callable
  `task_acceptance_review` stores evidence but makes zero reviewer calls and
  returns `deferred_to_host_acceptance`, `authoritative=false`. Before root
  acceptance, atomically fence new descendants under the queue lock and
  prove recursive subtree quiescence from the task-status SSOT; a revision
  must explicitly reopen the fence, and terminal/degraded outcomes seal it.
- The host buys one authoritative acceptance panel per PAID IDENTITY —
  `sha256(candidate_hash + the sorted set of nonempty (obligation_id,
  disposition, sha256(reason)) tuples)`; an empty disposition reason hashes
  to `""` and buys nothing. Only a changed candidate answer or a new
  nonempty obligation disposition mints a paid panel — the evidence revision
  must NOT (every cosmetic tool call moves it) — and an unchanged paid
  identity replays the recorded verdict for FREE, terminalizing with the
  typed `identical_acceptance_refused` reason. This prices changed
  substance, never cosmetic evidence revision.
- The host acceptance decision is written ONLY by
  `acceptance_dialogue._set_acceptance_decision`, with exactly three
  owner-facing states (`accepted | revision_requested |
  finalized_unaccepted`), each with a typed reason from the closed set; an
  unknown status fails closed. When you add a writer, add its reason to the
  closed set AND check every value-keyed reader —
  `outcomes.derive_loop_outcome` keys degradations and blocked terminals on
  status+reason PAIRS, and breaking a pairing is a silent false green. The
  reviewer verdict vocabulary `PASS|FAIL|DEGRADED` is NOT narrowable;
  `adaptive_quorum` applies, any contributing FAIL fails, DEGRADED abstains,
  and no quorum is a terminal HOST decision. Chat and Logs use the same
  severity reducer; degraded review or a best-effort objective must never
  render as green solved. Do not add task scope review or reuse the commit
  gate.
- The acceptance improvement loop is a reviewer-authored DIALOGUE: obligation
  identity comes from the reviewer's typed
  `disposition_kind`/`obligation_id` (an unknown re-raise id fails closed to
  `new`, disclosed); a re-raise reopens the row without wiping the agent's
  argument; termination beyond a clean PASS/accepted rebuttal happens ONLY
  via the reviewers' `dialogue_status` judgement or a real rail — no host
  counters, no keyword gates (P5). One contributing reviewer may hold the
  loop open only WITH MATERIAL (a `continue_actionable` vote without a
  concrete finding is disclosed and abstains); missing/invalid votes abstain
  and never default to continue; zero well-formed votes reduce to the typed
  `inconclusive`, which grants the dialogue no authority and falls through
  to the existing terminals. Changes here must cover malformed reviewer
  output, unknown/stale re-raise ids, partial panel failure, multi-slot
  status disagreement, replay/restart durability of obligation rows, false
  completion, and the backward-compatible default when new fields are
  absent.
- An explicit `max_improvement_passes` binds under every legacy policy;
  otherwise the shared review-cycle cap binds under EVERY policy, giving
  `improvement passes = cycles − 1` (the retired acceptance key is migrated
  into the shared key at settings load and never binds at runtime).

Enforcement: the adversarial tests the first bullet mandates, plus
`tests/test_child_result_disposition.py`, `tests/test_acceptance_fence.py`,
`tests/test_v674_acceptance_dialogue.py`, and `tests/test_review_cycles.py`
(cap migration).

#### Cognitive Artifact Integrity

- Cognitive artifacts (identity.md, scratchpad, task reflections, review
  outputs, pattern register) must NOT use hardcoded `[:N]` truncation. When
  content must be shortened, summarize explicitly — attempts, changes, and
  conclusions survive — and disclose the omission with a resolvable
  reference; an omission marker alone is disclosure, not sufficiency
  (BIBLE P1).
- All primary reasoning flows include the core governance artifacts as
  first-class sections — see "Core Governance Artifacts". A new reasoning
  flow MUST follow that contract, not rely on touched-file inclusions.

Enforcement: review-only — CHECKLISTS item 2(f) scores the no-`[:N]` rule in
commit review.

---

## Managed Update Rule

- Keep the local work branch and the official update feed separate; the
  channel and branch topology live in ARCHITECTURE "8. Git Branching, CI,
  and Build" (`ouroboros/update_channels.py`).
- A preflight chooses one exact official target SHA. Apply binds to the
  disclosed base/target, closes new writers, drains direct/ephemeral turns,
  stops workers and tracked services, then re-plans before mutation. Write
  the update transaction before mutation; reopen writers only after a
  verified abort/rollback or a healthy restart. Delayed evolution cleanup
  acquires the same update lock and honors the same admission owner.
- Dirty local work never enters merge history: the apply stashes it and
  restores it as uncommitted content; a conflicting restore keeps the stash
  and discloses the recovery command. The reviewed assisted resolver runs
  only when Git reports a real conflict; filenames do not create a second
  update policy. Managed materialization and rollback run their internal
  `git reset --hard` without interactive confirmation; the
  explicit-confirmation rule applies to the owner-facing generic restore
  seam.
- The authorized resolver stages the complete merge including tracked binary
  files, and review receives their exact staged mode/blob/size plus the
  parent object ids; missing exact metadata blocks. This exception does not
  weaken the ordinary commit pipeline's binary policy.
- A managed merge commits only with proof that the full suite ran green on
  the exact candidate tree ("The commit gate mirrors the CI split" names the
  proof authority). Any non-commit terminal of the resolver rolls the live
  tree back and best-effort preserves the attempt on the deterministic
  failed-update branch; the fresh rescue snapshot, not that branch, is the
  carrier rollback itself depends on.
- Take a fresh rescue before every destructive rollback and before
  boot-resume re-materialization — the pre-update snapshot predates the
  merge and holds none of the resolution. The hook is fail-open (never block
  a rollback on it), but its outcome is disclosed durably at capture time,
  before the destruction; record the pointer in the update transaction so a
  replayed rollback does not re-snapshot and a retry rescues what appeared
  since.
- Manual Restore reuses the same writer fence and pins the previous HEAD on
  a local recovery branch before reset. Promotion resolves the development
  SHA once and uses that exact SHA for both the local QA ref and any remote
  push.

Enforcement: `tests/test_update_merge_policy.py` (what the merge policy
refuses), `tests/test_update_dirty_stash.py` (the dirty-tree path),
`tests/test_update_hardening.py` and
`tests/test_update_tx_corrupt_quarantine.py` (transaction integrity and
quarantine).

## Mutation Attribution Rule

- Attribution is evidence, not exclusion: the host captures a `system_repo`
  baseline when a queued root task starts and a terminal candidate snapshot
  at outcome derivation; blockers (pre-existing dirt, stale/missing
  baseline, failed scan) ride into review and acceptance evidence for the
  LLM panels to weigh — pre-existing owner work creates ambiguity a
  reviewing actor must see without the host inventing a semantic outcome. Do
  not turn blockers into structural outcome vetoes, and do not add a
  lease/holder service, a second ledger, or runtime writer keyword scanners.
- Git staging is attribution-based: `paths=None` means the clean-at-baseline
  candidate set, an explicit list must be its subset, and empty never means
  `git add -A`. Preserve pre-existing user dirt as excluded evidence.
  Whole-tree staging belongs only to typed managed update/release
  transactions and the typed external patch-capture transaction; contexts
  without a captured baseline keep the legacy staging contract.
- Resolve unversioned Python only for `run_command`, `run_script`,
  `start_service`, and run-kind `verify_and_record`, once BEFORE the shell
  guard; guard and handler receive identical argv. Resolve bare Node for the
  same four surfaces but once AFTER the dispatch gates — the node health
  check executes an argv[0]-steered candidate, and probing before the gates
  would run a planted PATH shim for a request the fences would refuse.
  Never rewrite explicit paths, versioned interpreters, shell bodies, or
  remote execution; never install a dependency in response to
  `ModuleNotFoundError`; with no usable runtime the argv runs as written and
  fails honestly (a rewritten absolute shebang is a disclosed residual).
- Skill Review ordinals and provenance stay in `review_job.json` and the
  append-only `review_history.jsonl`: allocate under the lifecycle lock,
  consume a round only after actual start, write one terminal row per
  `job_id`, and compute legacy ordinals at read time without rewriting
  history.

Enforcement: `tests/test_mutation_attribution.py`.

## Process Custody Rule

Long-lived OS processes (anything `subprocess.Popen`-ed or `mp.Process`-ed
without a bounded wait in the same call) MUST be spawned through
`ouroboros.process_custody.spawn_supervised(cmd, drive_root=..., purpose=...,
scope=...)` — or, when an existing manager owns the Popen call, registered
via `record_process(...)` write-through immediately after spawn. The custody
ledger (`data/state/process_ledger.jsonl`) is what lets the orphan reaper
find children after an abrupt worker/server death; an unledgered process may
evade durable generation-aware reaping (custody complements the in-process
tracking, port sweeps, and Windows Job Objects — it does not replace them).
Scopes are `task`, `session`, and `daemon`; skill companions are the
documented daemon-scope exception, reaped only when the owning skill is
uninstalled or the entry is from a foreign dead server generation — log-only
by default and fail-safe: an unknown install set means keep-all, never a
mass-kill, and same-session companions of installed skills are always kept.
The reaper kills strictly by (pid, start_time, cmd_sha256) fingerprint —
never add command-line-class matching, which would let a dev instance reap a
packaged instance's processes. `tests/test_process_custody.py` enforces the
chokepoint with an explicit allowlist for bounded synchronous helpers.

## Platform Abstraction Rule

Platform-specific code goes through `ouroboros/platform_layer.py`: platform
modules (`fcntl`, `resource`, `grp`, `pwd`, `msvcrt`, `winreg`,
`ctypes.windll`), direct `os.kill`/`os.killpg`/`os.setsid`/`os.getpgid`,
`signal.SIGKILL`/`signal.SIGTERM`, and platform-conditional subprocess flags
(use `subprocess_new_group_kwargs()` / `subprocess_hidden_kwargs()`). Use
`pathlib.Path` for filesystem paths, never string concatenation with
hardcoded separators. One documented exception: a function-local import of a
platform module under an explicit `sys.platform` guard is allowed outside
the layer (e.g. the guarded `resource` import in
`ouroboros/extension_process_runner.py`) — the AST guard inspects only
top-level imports and does not see function-local ones, so review must.

Enforcement: `tests/test_platform_guard.py` scans `ouroboros/`,
`supervisor/`, and `server.py` for top-level platform imports, the four `os`
calls, and the two signals; `launcher.py` and subprocess flag patterns are
deliberately not scanned — code review and the `cross_platform` checklist
item cover them — and the CI matrix runs tests on Ubuntu, Windows, and
macOS. New platform behavior: add the cross-platform wrapper to
`platform_layer.py`, use it in callers, and add platform-conditional tests
when behavior differs across OSes.

### Shared state-file helpers

Durable state files use the SSOT helpers in `ouroboros/utils.py`:
`atomic_write_json` / `read_json_dict`, and `write_text_atomic` /
`write_bytes_atomic` sharing one atomic full-overwrite seam (temp-sibling +
`os.replace`, permission bits preserved) — a crash leaves the old complete
file intact, and appends are intentionally NOT atomic (a separate contract).
Prefer these over bare `Path.write_text`/`write_bytes` for full-file
overwrites; lockfiles go through
`platform_layer.acquire_exclusive_file_lock` /
`release_exclusive_file_lock`. Narrow exceptions: `supervisor/state.py`
keeps `atomic_write_text` for its mirrored state writes, and
`ouroboros/config.py` keeps its settings-file lock because the settings path
is bootstrapped before broader runtime helpers may depend on settings state.
Enforcement: `tests/test_atomic_write_v639.py`; the prefer-the-helper rule is
review-only.

## Design System

`docs/DESIGN.md` owns visual and interaction semantics; this section owns
the engineering rules that preserve them — where values may live, which
component is the SSOT, what counts as review debt, how a visual change is
verified. `web/style.css` custom properties and shared component classes are
the value SSOT; documentation keeps semantic roles and failure-prevention
rules, not a copied color/radius/dimension inventory.

- A text declaration on a migrated surface names a `--type-*` size token AND
  a named foreground token: a rule that declares a size and no colour is the
  exact defect that made secondary text inherit near-white primary ink.
  `tests/test_web_typography_static.py` keeps the class closed on the
  migrated files; migrating a new surface and extending that guard are the
  same commit.
- The variable contract is checked in BOTH directions across the whole
  stylesheet by the same test file: a `var(--x)` must resolve — an
  undeclared one silently renders its hardcoded fallback, which becomes the
  real value nobody can find — and a `:root` token must have a reader,
  because a token that resolves nowhere is what makes surfaces reach for
  literals. Fix a dangling name by pointing it at an existing token, not by
  declaring a new one.
- Layout and controls: top-level pages use a fixed `renderPageHeader`
  outside an independently scrolling body; page icons come from
  `web/modules/page_icons.js`; primary actions (including Refresh) live in
  the `renderPageHeader({ actionsHtml })` slot; tab strips are one
  design-system control (`renderTabStrip` + `.app-tab-strip`/`.app-tab` +
  the `--pill-*` tokens); scroll bodies share the `.scroll-fade-y` mask;
  masonry packing uses `web/modules/masonry.js::applyMasonry` (CSS Grid row
  packing leaves row gaps under shorter cards); widget order persists
  through `/api/ui/preferences` + `data/state/ui_preferences.json`, never
  in extension manifests. New visual dimensions become CSS variables first
  and are consumed by shared classes; new inline `style=""` markup and
  `.style.<property>` assignments are review debt (a dynamic measured value
  may update a narrowly named custom property when that is the real runtime
  data flow).
- One semantic button variant expresses one action role: neutral Settings
  and onboarding controls use the existing `.btn.btn-default`; a one-action
  result row uses the named `.settings-action-row` contract (status first,
  action docked right); notifications use the shared toast host. Working,
  warning, error, and destructive states keep consistent meaning across
  Chat, Logs, Settings, and Skills.
- A list editor reveals the entry it just added through
  `ui_helpers.revealNewRow(row, field)` — the one seam for "scrolled into
  view, caret in the first field" — and a freshly added entry shows no
  error before the owner tries to save.
  `tests/test_available_subagents_ui_static.py` pins the seam; the
  `ui_browser` acceptance in `tests/test_ui_smoke_agents_panel.py` pins the
  behaviour.
- Task outcome truth stays in `log_events.js::taskOutcomeSeverity` and
  `taskTerminalPhase`; `taskPresentation` is the one compact factual
  projection consumed by chips, live completion, history replay, and child
  terminal presentation. A non-terminal diagnostic may add a timeline fact
  but must not promote the whole task; unknown event names never acquire
  Chat severity from `error`/`crash`/`fail` keyword matching. The Chat
  header reports connection and server-authoritative activity only; failed
  task status does not synthesize header attention, a toast, unread state,
  or an owner action.
- Chat viewport invariant: sample live-edge intent before an ordinary
  transcript mutation — native scroll anchoring is not proof the owner's
  visible message stays stable, so focused regressions disable it. Follow
  only inside the 48 CSS-pixel zone, otherwise preserve the visible keyed
  message, nested-card, or Reviews anchor; route late
  application-controlled DOM writes through the existing stable-viewport
  seam, keeping awaited Load-older, reconnect reconciliation, and
  cross-instance restoration as explicit lifecycle transactions. Browser
  coverage is chosen by risk; this WebKit-sensitive contract requires the
  engines exercised by its marker-gated UI smoke.

### Responsive and accessible behavior

Navigation, headers, controls, and dialogs stay operable by pointer and
keyboard, preserve focus order, and fit the relevant narrow viewport without
stealing usable text space; use the shared responsive component before
adding a page-specific layout. A visible change is inspected with vision in
at least one relevant real consumer flow. A stored screenshot alone is not
verification; mobile or WebKit is not a universal requirement and is
selected from risk. Review-only: scored by CHECKLISTS items 2(i) and 30
(`web_design_system`).

### Browser dialogs

`window.prompt`, `window.confirm`, and `window.alert` are forbidden in
`web/modules`: PyWebView shells implement them inconsistently, native
dialogs bypass the design system and browser tests, and the macOS shell has
no prompt delegate, so `window.prompt` silently returns `null`. Use
`confirm_dialog.js::openConfirmDialog` — confirm mode returns a strict
boolean, input mode returns `{confirmed, value}`, alert mode renders one
acknowledgement action; Close, Cancel, backdrop, Escape, and supersession
are always non-confirming. Critical actions test the exact confirmed result
and keep the confirmation plus side effect in one injectable flow.
`tests/test_web_dialogs_static.py` keeps the native-dialog class closed.

### Declarative widgets

`web/modules/widgets.js` is the host for reviewed widget declarations:
forms/actions, text/data/media, tabs/charts, async jobs, files,
map/calendar/kanban, and composition through `group`, `metric`, and
`callout`. Nested interactive components use stable identity and one
disposer; `subscription.render` is transitively passive. Escape text and
attributes for their actual HTML contexts, constrain media to extension
routes or safe data URLs, and keep charts accessible through a semantic
table. Rare `kind: "module"` UI runs only in a sandboxed opaque-origin
iframe with no `allow-same-origin`; its parent bridge proxies only the
owning extension route — never load skill JavaScript into the SPA origin.
Long-running actions use a durable job id and resumable status polling.
Every timer, listener, observer, stream, abort controller, chart, and
mounted widget has a paired disposer. Enforcement:
`tests/test_widgets_ui_static.py` at commit tier;
`tests/test_widgets_ui_browser.py` in the release-tier `ui_browser` lane.

## MCP Client Integration

The base runtime is an optional CLIENT for trusted HTTP/SSE and local stdio
MCP servers — never an MCP server (structure and module ownership:
ARCHITECTURE "MCP and browser-facing external tools";
`ouroboros/mcp_client.py`). MCP descriptions and results are untrusted data,
not policy: configuration trust must not turn remote prose into policy.
Enabled tools join the initial capability envelope, still pass runtime
safety, and remain unavailable in repair/heal contexts; discovery failure
becomes a visible capability omission. Stdio accepts one executable command
and an exact string argument list — no shell, custom environment, or custom
working directory. Tokens never appear in status responses
(`ouroboros/secret_masking.py` owns the shared placeholders). Resources,
prompts, and MCP server behavior remain separate architecture changes.
Enforcement: `tests/test_mcp_client.py`.

## Gateway Boundary Pattern

Browser-facing backend work enters through `ouroboros/gateway/` and frontend
calls go through `web/modules/api_client.js` (structure: ARCHITECTURE
"Gateway Boundary v1"; the endpoint index lives in
`ouroboros/gateway/endpoint_index.py`, re-exported by `contracts.py`).
Outbound provider/harness adapters belong in `ouroboros/gateways/` and carry
no domain policy — do not copy policy into an adapter, promote the
`gateway/host_service.py` callback surface into a general owner/task API,
or require a class where established function owners already preserve the
boundary. Enforcement: CHECKLISTS item 17 (`gateway_parity`) and
`tests/test_gateway_parity.py`.

## Build & CI

### Python dependency locks

`pyproject.toml` is the direct-dependency SSOT and `uv.lock` the reviewed
cross-platform resolution; local and CI use `uv sync --locked`, and no
independent hand-written requirements authority exists. Release packaging
exports build requirements ephemerally and commits
`requirements-runtime.lock` for embedded pip; `requirements.txt` is a
generated pointer for older managed updaters, never an authority. A
dependency change updates the metadata, runs `uv lock`, regenerates the
runtime export with the exact README command, and leaves the CI clean-diff
check green. The pinned `tool.uv.required-version` and digest-pinned
`setup-uv` action make resolver changes deliberate rather than an ambient
CI upgrade. Documentation may pair the checkout-free `uv tool install` form
with a full commit SHA to pin the source revision, but must not claim it
locks dependencies or describe it as a release-artifact install or
contributor development environment.

### Pytest marker lanes

Default local pytest excludes seven costly or environment-dependent lanes —
`integration`, `browser`, `ui_browser`, `ui_browser_docker`,
`portable_detail`, `skill_smoke`, and `size_ratchet` — and CI opts into
them explicitly (job topology and provider matrix: ARCHITECTURE "CI
topology"):

- `integration` runs real provider checks, including the trusted
  direct-OpenAI canary rows derived from `OPENAI_DIRECT_DEFAULTS`. Missing
  core credentials are red in the official repository job; explicit
  quota/429/5xx/timeout may be typed inconclusive, while
  contract/auth/model/reasoning/tool 4xx stay red. Secretless request-wire
  and Anthropic-custody contracts remain in ordinary pull-request tests: do
  not move provider secrets into PR jobs or duplicate the trusted lane.
- `browser` / `ui_browser` / `ui_browser_docker` launch real Playwright
  engines (agent browser tools / the host UI / the `ouroboros-web:test`
  container; the docker lane skips cleanly when Docker is unavailable
  locally). `portable_detail` covers build/portable artifact invariants.
- `skill_smoke` installs the nine pinned official skills from the LIVE
  catalog (list in `tests/test_skill_smoke_official.py`) and runs as the
  dedicated 3-OS CI job in serial pytest invocations with real network and
  real pip; red means investigate — there is deliberately no fallback-skip.
  Its paid review tier runs as a SEPARATE pytest step (fresh process) that
  alone carries the provider key, ORDERED FIRST and ubuntu-only: the other
  tiers import downloaded plugin code in-process, and running the secret
  step first means the runner has never executed payload code while the
  secret was present. A missing key is a hard red, not a skip.
- `size_ratchet` carries the live-repo size gates and is the ONLY blocking
  surface for repository size (rules under "Module Size & Complexity"; only
  checks against the live repo carry the marker). The base fallback
  verifies the parent manifest against the parent's own tree — accepting a
  copied manifest would allow debt laundering, so a resolvable base that
  lost its manifest fails closed.

`skill_smoke` and `size_ratchet` tests must NOT also carry the `serial`
marker or join `_SERIAL_TEST_FILES`: the `and not <lane>` markexprs in
quick/full-test are the lane barrier, and single-lane assignment keeps each
test's placement unambiguous. When adding a new opt-in lane, register the
marker in `pyproject.toml`, add a collect-only zero-test guard in CI, and
keep the default local addopts free of network and Docker requirements.

### Parallel CI and the `serial` marker

CI runs the default suite in parallel — `python -m pytest tests/` with
`-m "not serial and <the seven lane exclusions>"`, `-n auto --dist
loadscope --max-worker-restart=0 --timeout=300 --timeout-method=thread` —
followed by a serial pass for `-m "serial and <the same exclusions>"`
(`.github/workflows/ci.yml`, jobs `quick-test` / `full-test`). Two rules
keep new tests from breaking that:

- Mark real-process / real-port tests, and tests that mutate process-global
  state WITHOUT reliable fixture isolation, `@pytest.mark.serial` (or add
  the file to `_SERIAL_TEST_FILES` in `tests/conftest.py`). Under `-n` such
  a test flakes on kill/reap or port-reclaim timing, or crashes its
  worker — and with `--max-worker-restart=0` a dead worker fails its WHOLE
  co-located batch, showing up as spurious failures in unrelated files.
- Keep every other test parallel-safe so it stays in the fast pass: use
  `tmp_path` (never a fixed path), `monkeypatch.setenv`/`delenv`/`setattr` for
  environment and attribute changes, and no execution-order assumptions. The
  autouse `tests/conftest.py::_os_environ_isolation` snapshot restores
  `os.environ` at every test boundary, so a bare assignment no longer leaks;
  monkeypatch stays the rule because it reverses exactly the named change
  inside the test, before the snapshot runs. A
  module-global mutation that is reliably snapshot-and-restored by a
  fixture may stay in the parallel pass — the pattern is
  `tests/conftest.py::_isolate_workspace_executor_globals`.

### The commit gate mirrors the CI split

`ouroboros/preflight_runner.py::run_hermetic_pytest` mirrors CI in one
disposable checkout and scrubbed temporary data root: the node test lane
(`cd web && node --test tests/*.test.js`, content-keyed — a candidate
without web tests never requires node, while an active web suite cannot
silently disappear when node is missing), then the same two logical pytest
passes (parallel `not serial`, then flag-free `serial`).
`LANE_EXCLUSION_EXPR` and `PARALLEL_PASS_FLAGS` are executable SSOTs pinned
against both CI jobs; the candidate is captured as one hardened
worktree-vs-`HEAD` binary diff, and a capture or apply failure is the typed
`PREFLIGHT_CANDIDATE_ASSEMBLY` hard block, never a test failure.
Contributor rules:

- The candidate cannot weaken the pass: `PYTEST_*`/`NODE_OPTIONS` are
  scrubbed, required plugins are probed outside candidate control and
  forced on with host-owned worker evidence, post-commit checks also
  inspect `HEAD~1` so suite deletion cannot hide after the commit exists,
  and exit status owns the verdict — rendered diagnostics do not.
  `OUROBOROS_PREFLIGHT_SERIAL=1` is the explicit temporary rollback lever,
  never a silent fallback.
- A red post-commit gate is warning-only for an ordinary commit (the local
  commit is preserved for forensics); evolution publication refuses to
  auto-push while the warning stands, and inside a managed update the gate
  blocks boot promotion and routes through rollback — an incomplete
  rollback leaves `gate_blocked` so boot retries recovery instead of
  promoting the rejected merge.
- The managed mandate is "the full suite provably ran green on the exact
  committed tree", not "run it twice": the reuse authority is the
  process-held exact-tree proof (`ctx._managed_tests_proof_trees`); the
  durable `tests_evidence` record is forensic telemetry the gate never
  consults, so a restart forces a rerun. Review-binding and tag-binding
  mismatches use the same managed failure route.
- Process containment is unconditional, including after a green pass:
  Windows uses a kill-on-close Job Object; POSIX uses an environment
  membership token plus a process-group enumeration backstop and promises
  honest detection with a fail-closed verdict, not guaranteed teardown of
  an arbitrary detached process. A crashed worker, a timeout-killed
  worker, a missing plugin, containment failure, and ordinary test failure
  keep distinct diagnostics.
- Mark process/port/global-state tests `serial`; make a merely slow test
  faster or split it — marking it serial removes the 300s per-test timeout
  and lets it consume the remaining total gate budget.

### GitHub Actions: secrets in step-level `if:` conditions

GitHub Actions rejects `secrets.*` inside step-level `if:` expressions, and
a step's own `env:` block is not visible to that same step's `if:`. Derive
a non-secret boolean in the job-level `env:` block, gate steps with that
boolean, and map the actual credentials only inside the first-party steps
that need them — later SBOM and attestation steps then inherit none of
them.

```yaml
jobs:
  build:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest]
    env:
      HAS_APPLE_SIGNING: ${{ matrix.os == 'macos-latest' && secrets.BUILD_CERTIFICATE_BASE64 != '' && secrets.P12_PASSWORD != '' && secrets.KEYCHAIN_PASSWORD != '' && secrets.APPLE_TEAM_ID != '' && 'true' || 'false' }}
    steps:
      - name: Import Apple signing certificate
        if: env.HAS_APPLE_SIGNING == 'true'
        env:
          BUILD_CERTIFICATE_BASE64: ${{ secrets.BUILD_CERTIFICATE_BASE64 }}
          P12_PASSWORD: ${{ secrets.P12_PASSWORD }}
          KEYCHAIN_PASSWORD: ${{ secrets.KEYCHAIN_PASSWORD }}
        run: |
          echo "${BUILD_CERTIFICATE_BASE64}" | base64 -d > cert.p12
          security create-keychain -p "${KEYCHAIN_PASSWORD}" build.keychain
          security import cert.p12 -k build.keychain -P "${P12_PASSWORD}"
      - name: Cleanup keychain
        if: always() && matrix.os == 'macos-latest' && env.HAS_APPLE_SIGNING == 'true'
        run: security delete-keychain build.keychain
```

```yaml
# ❌ WRONG — workflow fails to parse
- name: Bad
  if: secrets.BUILD_CERTIFICATE_BASE64 != ''   # parse error
  env:                                          # not visible to this step's if:
    P12_PASSWORD: ${{ secrets.P12_PASSWORD }}
```

`tests/test_build_scripts.py::TestMacOSSigning::test_ci_uses_env_context_for_condition`
enforces this for `.github/workflows/ci.yml` only; other workflow files are
not scanned by it.

### Apple signing & notarization (macOS Build job)

Prerelease artifacts may intentionally be unsigned and must report that
state; stable publication applies the configured signing and notarization
policy rather than implying credentials or success that were absent. Only
the non-secret `HAS_APPLE_SIGNING` gate is job-wide; certificate/keychain
values exist only in the import step and Apple ID notarization values only
in the first-party build step. Notary/stapler failures are soft outcomes
recorded through `NOTARIZE_OUTCOME`, so a transient Apple service problem
does not silently drop an otherwise valid signed artifact; cleanup uses the
`always()` plus matrix/env guards, and signing material never persists
across runs.

### Release proof capsule

The artifact pipeline — per-platform archive smokes, native Linux packages,
the AppImage custody chain, SBOM and attestation binding, and the
seven-asset release job — lives in ARCHITECTURE "8. Git Branching, CI, and
Build" and `.github/workflows/ci.yml`. The honesty invariants a change must
preserve:

- Publication is draft-first with a per-tag concurrency group; the remote
  annotated tag is revalidated against the event SHA immediately before
  draft creation AND again before publication, and a published release is
  never overwritten by a rerun.
- Vendor-distribution smokes (Astra, RED OS) are reported evidence, never
  release authority — third-party registry reachability is outside the
  publication pipeline's control.
- The AppImage smoke deliberately makes no native GTK/Qt claim; packaged
  native webview coverage remains a separate Linux distribution contract.
- `OUROBOROS_SKIP_PLAYWRIGHT_INSTALL_DEPS=1` is only a local-builder escape
  hatch — it skips Playwright's host-library installation, not
  browser-binary bundling — and a build using it must disclose that browser
  host compatibility was not locally proven.
- Never represent a later checksum inventory as build-time provenance, an
  SBOM, or packaged smoke evidence that the original build did not create.
