# ADOPTION manifest — v7-side deltas over the v7next upstream base

Ф0 skeleton (plan §5.1 as revised by roast F2: artifact/train-based, not
one-row-per-commit). Direction: this manifest covers the **v7 → upstream**
lane — every owner-approved v7 delta and campaign decision that must be
re-applied ON TOP of the integration base `ouroboros_v7next @ b9f7597f`
(upstream tip at branch creation). The opposite lane — upstream trains landing
after the cutoff — is adopted by owner signal («иди забирай») and is NOT
enumerated here; it gets its own train rows when the owner signals.

Sources (frozen reference, read-only):

- `ouroboros_v7_wip @ 9f691656` — `MIGRATION_v7.md` (3902 rows) and its
  `APPROVED_SEMANTIC_DELTAS` registry (`scripts/v7_migration.py:130`):
  17 delta families `D02–D09, D11, D13, D18, D31, D33–D38`.
- `~/.claude/plans/v7next/V7NEXT_PLAN.md` — §2 mandatory returns, §6 ABI
  package 7.0, §7 completeness, §5.4 three-column rule, v1.0 decision digest.

Validator: `scripts/v7next_adoption.py` (unique ids; all 17 delta families
present; enum-valid disposition/status/phase; `--release` refuses any
`pending-decision` disposition or non-`done` status — "no unresolved rows at
release", plan §10).

Schema (fixed; one row per artifact-level delta family, never per commit):

- `id` — unique; `Dnn` = approved semantic delta family; `ABI-n` = plan §6
  item; `CPL-n` = plan §7 item; `R-*` = plan §2 class return.
- `kind` — `semantic-delta` | `plan-item` | `class-return`.
- `disposition` — `retain` (owner decision stands, apply as decided) |
  `re-prove` (re-apply and re-prove against the NEW bytes; plan §11: verbatim
  ledger rows must be re-proved, precedent «сдвиг базы фальсифицировал 9
  строк») | `superseded-by-upstream` (upstream already carries the semantics;
  residual named in `what`) | `pending-decision` (three-column resolution per
  plan §5.4 still owed to the owner batch — forbidden at release).
- `status` — `pending` | `in-progress` | `done`. All rows start `pending`.
- `phase` — target campaign phase (F1 calm domains, F2 hot organs, F3 ABI
  package, F5 completeness).
- `verification hook` — the suite/checker that proves the row when it lands
  (suites named from the frozen reference arrive with their domain transplant,
  plan §5.3 step 4; hooks marked "(new)" are named now, built in their phase).

| id | kind | what | disposition | status | phase | verification hook |
|---|---|---|---|---|---|---|
| D02 | semantic-delta | Typed ToolResult/ToolCodeSpec seam with closed code table replacing re-read result prose (§4.3.3), incl. the loop-side retirement of result-text classification — the «D02-петля» mandatory return (plan §2) | re-prove | pending | F1 | tests/test_tool_classification_differential.py + tests/test_tool_result.py |
| D03 | semantic-delta | Settings vocabulary seam: config.py → settings_defaults / settings_scales / model_slots / review_model_routes / runtime_limits (§4.3.5); = plan §2 "S1 settings seam" return | re-prove | pending | F1 | tests/test_config_extraction.py + tests/test_settings_read_seam.py |
| D04 | semantic-delta | Retired settings knobs (§4.3.6); knob set must be re-checked against the tip settings vocabulary before re-retiring | pending-decision | pending | F1 | tests/test_config_extraction.py (retired-knob clause) |
| D05 | semantic-delta | Safety host facts: `_safety_drive_root`, `_record_safety_usage`, `schedule_followup` = POLICY_SKIP (§4.3.8). CONFLICT: safety.py is protected and "апстрим = правда" (Q4=A); tip @ b9f7597f does NOT carry these facts — three-column owner call | pending-decision | pending | F1 | tests/test_safety_policy.py |
| D06 | semantic-delta | Events taxonomy: declared disposition of every event kind in four tiers, producer/answer pairing enforced (§4.3.12) | re-prove | pending | F1 | tests/test_event_taxonomy.py |
| D07 | semantic-delta | Emergency Stop 2A (§4.3.11) — re-derive against the upstream cancel machinery of the delegation/cancel organ (§5.4 three-column) | pending-decision | pending | F2 | tests/test_e2e_cancellation_scenarios.py (E1–E12) |
| D08 | semantic-delta | Cancellation/delegation fail-closed registries (§4.3.13) — same three-column pass as D07 | pending-decision | pending | F2 | tests/test_cancel_protocol_inventory_s6.py |
| D09 | semantic-delta | LLM one-physical-attempt-per-candidate ownership (§4.3.2). Upstream RE-INTRODUCED the retry bug: `ouroboros/llm.py:2487` `for attempt in range(3)` live at b9f7597f — mandatory return (plan §2) | re-prove | pending | F1 | tests/test_llm_extraction.py + Ф4 D09-invariant scenario (plan §8) |
| D11 | semantic-delta | FUNCTION_DEBT same-qualname relocation rule (§1.9/№8) — a ledger rule, carried into the v7next ledger discipline unchanged | retain | pending | F1 | scripts/v7_migration.py-style ledger checker (reference: v7_wip) |
| D13 | semantic-delta | supervisor/git_ops pre-init roots follow OUROBOROS_* env (owner-ratified batch №11) | retain | pending | F1 | tests/test_git_ops_default_roots.py |
| D18 | semantic-delta | Module-handle reads of rebound supervisor globals in queue/pool leaves (`_queue()` handle idiom; plan §5.3 keeps it) | re-prove | pending | F1 | tests/test_module_handle_extraction.py |
| D31 | semantic-delta | Contributor-review trust boundary: the lane always executes the review machinery of the target base via a detached trusted-base worktree (owner 2026-08-19) — mandatory return (plan §2) | re-prove | pending | F2 | tests/test_external_review_script.py |
| D33 | semantic-delta | L-B loop module-handle delta (`_loop()` call-time handle for the nine loop leaves; plan §5.3 keeps it) | re-prove | pending | F1 | tests/test_module_handle_extraction.py + tests/test_loop_owner_facades.py |
| D34 | semantic-delta | Carrier-aware update engine: shared span-substitution resolver + span-descriptor SSOT in release_sync.py, applied at the three managed-update insertion points — mandatory return over the #276-based upstream engine (plan §2, §5.4) | re-prove | pending | F2 | tests/test_update_carriers.py + tests/test_carrier_rebase_helper.py |
| D35 | semantic-delta | G1 git_ops module-handle delta (`_go()`; init rebinds REPO_DIR/DRIVE_ROOT/BRANCH_*) | re-prove | pending | F1 | tests/test_module_handle_extraction.py + tests/test_git_ops_owner_facades.py |
| D36 | semantic-delta | DEL1 delegate-family module-handle delta (delegate_custody / tools/delegate / delegate_integration / subagent_integration leaves) | re-prove | pending | F2 | tests/test_module_handle_extraction.py + tests/test_delegate_owner_facades.py |
| D37 | semantic-delta | L-C review-stack module-handle delta (`_rev()`/`_car()` over tools/review.py and tools/claude_advisory_review.py) | re-prove | pending | F2 | tests/test_module_handle_extraction.py + tests/test_review_owner_facades.py |
| D38 | semantic-delta | L-C2 module-handle delta: agent-dispatch and usage legacy-import leaves | re-prove | pending | F1 | tests/test_module_handle_extraction.py + tests/test_lc2_owner_facades.py |
| R-WINWAVE | class-return | Windows/cross-OS fix wave: re-apply by CLASS with a one-decision-per-class registry (both sides fixed independently — dedup, plan §2, risk §11) | re-prove | pending | F1 | full 3-OS CI matrix (`gh workflow run CI --ref <branch>` full-test) |
| ABI-1 | plan-item | PluginAPI 2.0 via the convergent design (§6-1 + §6.1-Δ, owner «A» 30.08): `api_generation()` = field or LEGACY "1.3", admission predicate at new-PASS issuance (RC≡GA), hash-bound-PASS grandfather, native_seed closed, review clobber-guard, preflight fail-open fix; bundled grants resync per owner «A» | retain | pending | F3 | tests/test_extension_plugin_api_matrix.py + packaged-artifact admission test (new) |
| ABI-2 | plan-item | task-result `_schema_version=1` per Q8=B safe-B: no legacy converter, quarantine+owner notification on future/malformed; state.json/queue_snapshot get a stamp on write, shape unchanged | retain | pending | F3 | schema_versions suite covering F12 semantics: future-refusal, malformed, idempotent migration, N−1, rollback (new) |
| ABI-3 | plan-item | Gateway ABI: drop compat aliases (cost_usd*, telegram_chat_id, project_last_viewed/hidden); executable ABI = JSON Schema derived from contracts.py, validated on ingress (Q7=A); ABI version separate from product version | retain | pending | F3 | tests/test_contracts.py + F11 per-alias inventory (ingress/egress/JS/producer/stored/migration/removal-test) |
| ABI-4 | plan-item | `ResolvedModelTarget` frozen dataclass (D02-owner); every lane consumes the typed target | retain | pending | F3 | typed-target suite (new, named in the F3 design note) |
| ABI-5 | plan-item | Q10 removals (all owner «A»): SCOPE_REVIEW_FLOOR (key, endpoint, UI, guards, SAFETY.md clause, tests), fail_tasks (pause = the only semantics; resumable-vs-terminal per Q9=A; E8 gets a successor scenario id), until_deadline/stall_rounds aliases (+bench adapters) | retain | pending | F3 | per-surface removal tests (F11 style) + E-suite E8 successor scenario |
| ABI-6 | plan-item | P1 hygiene: _call_llm_with_retry alias, _typed_or_adapted branch, failure-detector compat wrapper, 3 underscore renames, api_v1 shim, compute_cost_with_children, format_handoff_message; latent fix: CHECKLISTS:507 (the _updater_imports change was REJECTED — v7_evidence reads git_ops.py at the immutable BASELINE_SHA by design; see LEDGER_CORRECTIONS). Behavior-preserving part lands BEFORE the ABI window in small commits (F9) | retain | pending | F3 | ruff F + targeted per-item suites + grep-level absence checks |
| ABI-7 | plan-item | RC auditor/migrator: machine-readable scope + N−1 fixtures, remainder = owner attestation (F13); N−1 updater transition entry point/shim with crash-point tests (F14, Q10=A) | retain | pending | F3 | RC audit fixture suite (new, F13/F14) |
| ABI-8 | plan-item | Handler-ABI finale: tool handlers return ToolResult, not str (the true D02 finale). POST-RELEASE BACKLOG, not the v7.0 campaign: Q5=A kept it OUT of the ABI bundle; Q16=A retires the «7.1» label into post-release backlog, not into v7.0. (owner one-line confirm queued) | post-release | deferred | POST | tests/test_core_native_results.py + tests/test_control_native_results.py extended to handler signatures |
| ABI-9 | plan-item | Atomic publication of extension registrations: stage→validate→swap of the registration snapshot (not `_lock` around inserts), internal disposers list (NOT exposed in ABI), extension-generation digest in physical-call provenance — moved from §7-8 into Ф3 by F8 (before ABI provenance) | retain | pending | F3 | tests/test_extension_loader_extraction.py + registration atomicity suite (new, F8) |
| ABI-10 | plan-item | Reviewer comma-lists → actor rows: the model is already upstream (#384). Residual: sweep for comma-list remnants across settings/review lanes (§5.4) | superseded-by-upstream | pending | F3 | comma-list remnant sweep (grep-level checker, new) |
| CPL-1 | plan-item | domains.toml + domain checker: cycles=0 on domain nodes AT GATE TIME, dependency direction, literal-copy-fix ban; DOMAIN_MAP.md generated from the manifest (gen/verify pair). Report-only stage SHIPPED in Ф0: scripts/v7next_domains.toml + scripts/v7next_domain_report.py | retain | pending | F5 | scripts/v7next_domain_report.py (Ф0 report) → gate checker (F5, new) |
| CPL-2 | plan-item | gen/verify pairs: frozen-contracts table (ARCHITECTURE §11.1), data-layout tree (PERSISTENCE_OWNERS), facade inventory; staleness = red CI | retain | pending | F5 | gen/verify CI pair (new) |
| CPL-3 | plan-item | code_intelligence architecture facts: owner_of(path\|symbol), domain_dependencies(d), facade_consumers(sym), persistence_entities_written_by(sym), protected_contracts_affected(diff); consumer №1 = Ouroboros self-evolution (Q12=B: completeness ships whole in v7.0) | retain | pending | F5 | code_intelligence fact suite (new) |
| CPL-4 | plan-item | Persistence: schema_version/migration/retention/reset decision per durable entity, local (no generic framework); close §16 findings (undocumented planes, unbounded ledgers, mismatched temp) | retain | pending | F5 | persistence entity inventory checker (new) |
| CPL-5 | plan-item | Runtime invariant model-visible⟺logged, narrowed per F15: sealed model_send records at the last host-controlled pre-transport seam, typed exclusions (provider-native queries/transforms/secrets), reverse-⟺ for model_send only; canonicalization design note BEFORE code (batch-1 Q8=A confirmed) | retain | pending | F5 | invariant reconstruction suite (new; design note first) |
| CPL-6 | plan-item | Conformance contracts for multi-provider seams: LLM providers and the executor axis native\|harness — one normative shared suite every new provider must pass | retain | pending | F5 | shared conformance suite (new) |
| CPL-7 | plan-item | Skill manifest "Model Experience" section (prose: what the model sees / token effect) + teaching errors on registration refusal | retain | pending | F5 | skill manifest schema tests (extend tests/test_marketplace_api.py family) |

Notes:

- §7-8 (atomic extension-registration publication) is deliberately absent from
  the CPL family: roast F8 moved it into Ф3 — it is row ABI-9.
- The `pending-decision` rows (D04, D05, D07, D08) are exactly the deltas that
  need the plan §5.4 three-column executable matrix (upstream invariant /
  retained v7-delta / conflict→owner) before any re-splitting starts; deciding
  them here would fabricate owner decisions the batches have not taken.
- Hook suites named from the frozen reference do not exist on this tree yet;
  they arrive with their domain transplant (plan §5.3 step 4). The validator
  checks the manifest contract, not hook existence — hook existence becomes
  checkable per-phase.
