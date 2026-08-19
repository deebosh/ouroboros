# Adoption log — upstream v6.105.0/v6.105.1 into the v7 branch

Lane `lane_adopt`, based on `0ccffa4c`. Merge commit **`734ac4fc`**
(parents `0ccffa4c` + `e7c84240`), followed by the provenance commits below.

Working reference: the operator's `ADOPTION_REPLAY_MAP.md`. It is treated as
evidence, not gospel: every claim below was re-verified against the two trees,
and the deviations are named.

---

## 0. Target: the SHA, not the moving ref

The task said `managed/ouroboros = e7c84240`. At execution time the ref had
already moved on to `8028f1df` — six commits further (PR #257, a Linux
browser-mode fix, plus provider-test and launcher-reaper follow-ups; still
VERSION 6.105.1). The map classifies 117 files against `e7c84240` and the task
names that SHA and that version pair, so the merge targets **the SHA**. Merging
the ref would have pulled 42 files of unclassified drift into a commit whose
review evidence does not cover them.

## 1. Baselines taken before the merge (all green)

| gate | result |
|---|---|
| `scripts/v7_evidence.py check` | `v7 evidence OK (5bce0cee…)` rc=0 |
| `scripts/v7_migration.py` | rc=0, silent |
| `ruff check . --select F` | All checks passed |
| `scripts/regenerate_size_ratchet.py --check` | rc=0 |

## 2. Buckets actually encountered

| bucket | map | actual | note |
|---|---|---|---|
| changed files | 117 | **117** | exact match (104 M, 13 A) |
| new upstream files | 13 | **13** | 12 added; `subagent_dispatch_notes.py` deliberately not created (§4) |
| git conflicts | ~25 predicted | **26** | see §3 |
| re-homed by hand | 17 | **18** | the map's 17 plus `tools/control.py::_attach_client_surface` |

The conflict set matched the map's prediction except that
`ouroboros/tools/plan_render.py` and `ouroboros/tools/plan_review_runtime.py`
**auto-merged** — which is precisely why the DEGRADED trap (§5) was dangerous.

## 3. Conflict resolutions (26 files)

### DROP — upstream's size-ratchet cosmetics (ours)

`cbd88e40`/`46214604` compressed lines under upstream's own 1600-line ceiling.
v7 already split every one of those files, so the compression carries no meaning
here and was taken as **ours**:

| file | disposition |
|---|---|
| `ouroboros/size_ratchet_manifest.py` | whole file ours, then regenerated (§7) |
| `ouroboros/llm.py` | ours + the ONE semantic hunk replayed by hand: `available_models` now reads `self.default_model()` |
| `ouroboros/loop.py` | ours whole; its two semantic hunks re-homed (§4) |
| `ouroboros/loop_tool_execution.py` | ours; the parser lives in `plan_render.py` on this branch (§5) |
| `ouroboros/config.py` | ours for the import block and the moved `SETTINGS_DEFAULTS`; UNION on `save_settings` — v7's `serialize_settings` + upstream's `replace_atomic` |
| `ouroboros/agent.py`, `delegate_custody.py`, `tools/control.py`, `tools/registry.py`, `supervisor/{events,queue,workers}.py`, `server.py` | ours (each conflict was a region v7 had emptied into leaves) |

Cosmetic hunks that git auto-merged were NOT reverted — reverting non-conflicts
is churn — except where a verbatim ledger row forced the question, which the
ledger test then decided for us in the opposite direction (§6).

### UNION — both sides changed, symbols disjoint

* `ouroboros/tools/plan_review.py` — 4 conflicts. Imports unioned; the replay
  branch takes upstream's three-way shape (`earned_delta` / `closed` /
  `stale`) with v7's typed publication seam substituted for upstream's
  `_render_wave` in all three exits. Verified by diffing the merged
  `_run_plan_review_async` against upstream's: the ONLY differences are those
  three substitutions.
* `ouroboros/review_substrate.py` — ours; the typed failure fields and
  `TYPED_FAILURE_FACT_KEYS` re-homed to `review_records.py` (§4), re-exported
  so `reviewer_slot_config` and `plan_review_runtime` keep their import.
* `ouroboros/headless.py`, `ouroboros/packaged_cli.py` — v7 structure +
  upstream's `replace_atomic`. **`headless.py` was a live trap**: the body hunk
  auto-merged but the import was inside the conflict, so taking ours alone
  would have shipped a `NameError` on the child-receipt publish path.
* `docs/ARCHITECTURE.md` — 6 conflicts; v7's leaf map wins, upstream's new
  facts folded into the lines that own them on this branch (§8).
* `web/modules/chat.js` — ours + `import { clientSurfaceField }`; the payload
  spread auto-merged at the right site (`chat.js:705`, the map predicted 703).
* Test files — see §6.

## 4. Re-homed hunks (18 files, per-symbol)

| upstream site | v7 owner | what moved |
|---|---|---|
| `loop.py::_drain_incoming_messages` | `loop_round_limits.py` | `noted_owner_text(owner_ctx, entry, dmsg)` inside the owner-marking wrapper |
| `loop.py::_maybe_inject_finalization_nudges` | `loop_nudges.py` | `_code` + `record_nanny_nudge_stamp(...)` (B3 durable stamp) |
| `tools/control.py::_attach_client_surface` + 3 calls | `tools/control_routing.py` | the helper and its promote/route/steer call sites; `control.py` re-exports it |
| `tools/control.py::schedule_subagent_properties` | `tools/control_subagent_spec.py` | 3 schema descriptions (objective=outcome, context=work order, explicit lane overrides dispatch policy) |
| `config.py::SETTINGS_DEFAULTS` | (auto-merged into `settings_defaults.py`) | `OUROBOROS_SUBAGENT_PROFILE` |
| `review_substrate.py::ReviewActorRecord` | `review_records.py` | `failure_code` / `reset_at` / `http_status` + `TYPED_FAILURE_FACT_KEYS` |
| `server.py::_route_project_chat_to_running_task` | `server_owner_routing.py` | `client_surface=` on `write_owner_message` |
| `server.py::_route_owner_message` | `server_owner_routing.py` | the non-web channel fallback + the skill_repair drop disclosure |
| `server.py::_reconcile_delegated_runs` | `server_maintenance.py` | `gateway_factory=lambda: ensure_owned_gateway(admission_wait_sec=0)` |
| `server.py` (new) | `server_maintenance.py` | `_startup_worktree_prune`, `_startup_prune_sweeps` |
| `supervisor/events.py::_compose_subagent_text` | `supervisor/events_subagent_admission.py` | boundary-only `[WRITE SURFACE]` wording |
| `supervisor/queue.py::check_scheduled_tasks` | `supervisor/queue_schedules.py` | the whole one-shot rail + its three `schedule_time` imports |
| `supervisor/workers.py::promote_chat_to_task` | (auto-merged into `worker_promotion.py`) | `metadata.client_surface` |
| `agent.py::preflight_delegate_visibility._append_reason` | `agent_dispatch.py` | typed `reduction_reasons` seeded from the legacy string |
| `agent.py::capability_delta_prompt_block` | `agent_dispatch.py` | typed reason list + the harness-routed action clause |
| `delegate_custody.py::_recover_pending_invocation` | `delegate_custody_reconcile.py` | see §9 — verified already correct |
| `llm.py::available_models` | (in place) | `self.default_model()` |
| `tests/*` | see §6 | 15 symbols |

`check_scheduled_tasks` after the re-home was diffed against upstream's: the only
differences are v7's `_queue()` module-handle reads (the ratified D18 pattern).

## 5. TRAP S1 — honest DEGRADED, and why the map undercounted it

The map named two landing sites (`plan_render.py:27` and `:68`). The tree has
**four**, because v7 replaced upstream's single text-parse with a producer-metadata
seam (D02) and the outcome vocabulary is written out at each end of it:

1. `ouroboros/tools/plan_render.py::_PLAN_REVIEW_OUTCOMES` — `+ "DEGRADED"`
2. `ouroboros/tools/plan_render.py::_parse_plan_review_control` — `outcome in {"REVISE_PLAN","DEGRADED"} and closed` ⇒ reject
3. `ouroboros/loop_tool_execution.py::_extract_result_metadata` — the literal set and both clauses that validate the PRODUCER's metadata
4. `ouroboros/tools/plan_review_runtime.py::publish_plan_review_projection` — the publication guard (a v7-only symbol; upstream has no counterpart)

**The auto-merge made this worse than "silently fail-closed."** Upstream's
`_CONTROL_OUTCOME["DEGRADED"] = "DEGRADED"` landed cleanly with no conflict, so
`wave_control_state` began returning `DEGRADED` while site 4 still rejected it:
every DEGRADED wave would have raised `ValueError: invalid plan review aggregate
signal: 'DEGRADED'` at publication. Sites 1–3 would then have dropped or
refused it in turn.

Proof of transfer: `ouroboros/tools/plan_render.py:29,72`,
`ouroboros/loop_tool_execution.py:360,363`,
`ouroboros/tools/plan_review_runtime.py:122,127`.
Tests: `tests/test_tool_execution_classification.py` gained a positive DEGRADED
case and the illegal `DEGRADED+closed` case in v7's metadata vocabulary (upstream's
version of that block is text-parse-shaped and could not be replayed verbatim);
`tests/test_plan_review_health.py` and the engine suite cover the rest.
## 6. Test re-homes

Upstream added 47 test symbols across 6 files that v7 had split. Placement:

| upstream file | new symbols | placement |
|---|---|---|
| `test_claudexor_owned_daemon.py` | 17 | 13 auto-merged into the parent (its own reconcile/ensure theme); `test_pinned_engine_serves_the_account_pools_marker_id` restored to the parent; 3 unified-accounts cases re-homed to `test_claudexor_login_accounts.py`, `test_claudexor_login_jobs.py`, `test_claudexor_status_payload.py`; `_create_login` updated to upstream's body |
| `test_context.py` | 6 | all 6 to `test_context_runtime_section.py`; `TestRuntimeEnvSection` updated to upstream's body (is_desktop retired → presentation + owner_client) |
| `test_plan_review_engine.py` | 15 | all 15 landed in the engine suite; the B2b panel-health section then split to `test_plan_review_health.py` (§7) |
| `test_review_agent_session_route.py` | 9 | 3 auto-merged; 3 admission/cooldown cases added to the parent; 3 to `test_review_session_delivery.py`; 2 changed bodies updated |
| `test_plan_review.py`, `test_review_cycles.py`, `test_smoke.py` | 5 | auto-merged cleanly |
| `web/tests/harness_accounts.test.js` | 1 assert | re-homed to `web/tests/harness_accounts_custody.test.js` (`'Default CLI login'` → `'Default account'`) |

**Deviation from the map (§5.2).** The map sends
`test_a_pool_exhausted_terminal_is_typed_like_a_spent_window` to the
routes-on-slots suite. It reads `_exhausted_window_detail`, which the v7 split
gave to `test_review_session_delivery.py` under a ledger row whose note says
"with its single reader suite". Honouring the map there would have made that
note false and forced a second helper move. It is placed in the delivery suite
instead — which also matches its subject (a delivered terminal's typed refusal).

**One helper genuinely had to move.** Two of upstream's typed-refusal tests DID
belong in the routes suite and both call `_run_session_directly`, which lived in
the delivery suite. With two reader suites, v7's own idiom applies (the
`fakeResponse` precedent): it moved to `tests/_review_session_route_shared.py`
and both suites import it. Ledger row re-keyed accordingly.

## 7. Three modules crossed v7's 1500-line ceiling — extractions, not a waiver

`MODULE_DEBT_1500` is EMPTY on this branch: v7 got every module under 1500.
Upstream's additions pushed three back over. Since `validate_size_ratchet` checks
every commit tree, this could not be deferred to a follow-up commit.

| module | before | after | new leaf |
|---|---|---|---|
| `ouroboros/subagents.py` | 1566 | 1382 | `ouroboros/subagent_route_health.py` — `route_health`, `_exhausted_window`, `_model_scope_matches`, `_cooldown_active` |
| `ouroboros/context.py` | 1523 | 1318 | `ouroboros/context_runtime_facts.py` — `_project_room_fact`, `_runtime_budget_info`, `_promoted_task_toolset`, `_delegation_capability_fact` |
| `tests/test_plan_review_engine.py` | 1560 | 1283 | `tests/test_plan_review_health.py` (B2b panel health) + `tests/_plan_review_engine_shared.py` (the harness both suites drive) |

Each parent re-exports every moved name; the leaf import of `DelegatedRunShape`
is `TYPE_CHECKING`-only so the facade cannot cycle.

`tests/test_claudexor_owned_daemon.py` (1136) re-entered the 1001–1500 band and
carries a `--band-rationale` in the regenerated manifest.
## 8. Protected files — adopted AS UPSTREAM WROTE THEM (owner disclosure)

| protected path | Δ | content |
|---|---|---|
| `ouroboros/safety.py` | +4 | `TOOL_POLICY["schedule_followup"] = POLICY_SKIP` (line 108), by analogy with `schedule_subagent`: registering a follow-up does not widen reach |
| `docs/CHECKLISTS.md` | +14/−2 | the plan-review DEGRADED criterion rewritten (paid cycle, health epoch, quorum_unreachable) — this CHANGES a review criterion |
| `prompts/SYSTEM.md` | +25/−1 | +24 lines of owner-surface-fact / presentation posture at ~453, plus a tool-choice edit at ~568 — this CHANGES the runtime system prompt |
| `ouroboros/tools/registry.py` | +1/−1 | **NOT applied.** Upstream adds `"followup"` to a literal `_FROZEN_TOOL_MODULES`; on this branch the file is a 39-line facade and the list is DERIVED by AST scan in `registry_core.py`. Verified: `tool_modules_for_runtime` returns `followup` among 35 modules with no manual step. The hunk is structurally inapplicable, not dropped work. |

None of the three adopted files was edited beyond the mechanical merge.

**Map deviation (addendum item 3).** The addendum predicted the real landing site
for `followup` would be the pin fixture at `tests/test_frozen_tool_inventory.py:95`.
It is not a hardcoded name list — it is a projection compared between frozen and
source mode — so it needed no edit, and `tests/test_frozen_tool_inventory.py`,
`test_tool_catalog.py` and `test_registry_core.py` all pass unchanged.

## 9. Verified-not-needed (map claims that did not survive contact)

* `delegate_custody.py::RunCustody.profile_id` and `_STARTED_STR_FIELDS`
  auto-merged into the parent (they never left it); only
  `_recover_pending_invocation`'s `profile_id=` needed the leaf, and that was
  already present at `delegate_custody_reconcile.py`.
* `_root_exploration_log` (trap S3): ONE home, `plan_review_runtime.py:467`.
  No duplicate, no extra ledger row.
* `plan_review_runtime.py` auto-merged as a perfect union — all 30 upstream
  symbols present plus v7's 9, verified by AST symbol-set comparison.
* `current_plan_review_wave` was in upstream's `plan_review.py` import list for
  a call site v7 had already moved to `plan_review_runtime.py`; importing it
  would have been dead. Dropped.

## 10. Version carriers

All nine auto-merged to **6.105.1**, as the map predicted (v7 kept them
byte-identical to the old base): `VERSION`, `pyproject.toml`,
`web/package.json`, `web/modules/api_types.js` (`GATEWAY_CONTRACT_VERSION`),
`README.md`, `docs/ARCHITECTURE.md` header, `uv.lock`, `docs/install/index.html`,
`site/install/index.html`. No version was authored by this lane.
## 11. What the GATES caught that hand-replay missed

Three genuine wiring gaps survived the manual pass and were found by running the
suites, not by reading the diff. Recording them because they are the same class:
**an upstream hunk whose file v7 split can be dropped silently by taking "ours".**

1. `ouroboros/headless.py` — the `replace_atomic(tmp, dest)` call auto-merged but
   its import sat inside the conflict. Ours-only would have shipped a `NameError`
   on the child-receipt publish path. (Caught by reading the resolution; fixed
   before commit.)
2. `ouroboros/tools/control.py::_attach_client_surface` — the helper and its three
   call sites were in the dropped region and were never re-homed. Caught by
   `scripts/v7_evidence.py check-migration`, which reported the symbol as
   moved/removed with no ledger row.
3. `supervisor/workers.py::promote_chat_to_task` — the `metadata.client_surface`
   hunk was assumed auto-merged; it was not. Caught by
   `tests/test_client_surface.py::test_promotion_lands_client_surface_under_metadata`.

## 12. Upstream tests adapted to v7 contracts (not to v7 convenience)

Four upstream assertions were written against shapes v7 deliberately changed.
Each was adapted at the TEST, never by weakening the runtime:

| upstream assertion | v7 fact |
|---|---|
| `queue.init(tmp_path, 600, 1800)` ×4 | v7 retired the three timeout parameters (D04); `init(drive_root)` reads the environment itself |
| AST-scan of `tools/control.py` for the three routing producers | they live in `tools/control_routing.py`; the facade re-exports them |
| `server.py` must contain `client_surface=(` twice | one call site moved to `server_owner_routing.py`; the pin now reads both owners as one surface, keeping the "neither may be dropped" intent |
| `"followup" in ToolRegistry._FROZEN_TOOL_MODULES` | v7 DERIVES that list by AST scan and caches it only after a registry loads its catalog; the assertion now reads `tool_modules_for_runtime` directly |
| `queue._write_scheduled_tasks` monkeypatch | `check_scheduled_tasks` and the writer both live in `queue_schedules.py`; patching the facade name would never have been called (a false-green tripwire) |

Import retargets in the two new upstream suites (`test_delegation_account_pin.py`,
`test_plan_review_epoch.py`) follow the same rule and carry ledger rows.
## 13. Provenance

* `scripts/v7_migration.py::MERGE_BASE_SHA` → `e7c84240fc2aa73a798e045b207df4f39ddd355d`
  (verified an ancestor of HEAD). `BASELINE_SHA` and
  `tests/fixtures/v7_prologue_baseline.json` untouched, as the map requires:
  the campaign's acceptance is measured from that anchor.
* **The two rows the map predicted (3859/3860 at map time, 3869/3870 here) were
  NOT repaired the way it prescribed.** Re-keying
  `ouroboros/agent.py::dispatch_executor_note` to
  `ouroboros/subagent_dispatch_notes.py::...` would have been correct only if
  upstream's module survived. It does not (§4.1 option A). The existing rows
  stayed valid — `agent.py` still re-exports both names — and the new base's
  extraction is recorded as its own pair of rows instead.
* **62 ledger rows added, 2 repaired.** Far more than the map's "~11": it
  counted only the test re-homes and missed the 1500-ceiling extractions, the
  import-binding moves in the two new upstream suites, and the
  `_attach_client_surface` / `TYPED_FAILURE_FACT_KEYS` / startup-sweep moves.
* **Nine verbatim rows were re-synced, not re-noted.** With `MERGE_BASE_SHA`
  moved, `test_v7_verbatim_moves` reads each row's old text at the NEW base — so
  upstream's cosmetic reflow of a declaration v7 had extracted turns a true
  "verbatim" note into a false one. The reflowed text was adopted into the leaf
  in each case (`llm_capability_policy`, `llm_pricing`, `loop_acceptance`,
  `loop_nudges`, `loop_budget`, `test_post_task_reflection`,
  `chat_timeline_anchor`, `chat_live_cards` ×2). This is the ONE place where
  upstream's line-compression had to be taken rather than dropped, and the
  reason is provenance, not style.

## 14. Open flags for the operator

1. **D02 goldens — not regenerated, as instructed.** `tools/followup.py` is a new
   tool producer, but `tests/test_tool_classification_differential.py` passes
   untouched: the new tool returns plain `ERROR: FOLLOWUP_*` strings and the
   corpus harvests producer shapes that already cover that form. No
   `_PRODUCER_SHAPES` entry was added and no golden was regenerated. **Flag, not
   a fix:** if the owner wants `schedule_followup` to publish a native typed
   `ToolResult` like the other v7 producers, that is a D02 decision with a
   golden regeneration attached.
2. **`os.replace` class-fix remains incomplete (upstream's own gap, disclosed
   not silently fixed).** Upstream's `replace_atomic` conversion covers
   `config.py`, `packaged_cli.py`, `headless.py`. Still unconverted on this
   branch: `claudexor_runtime.py` (6 sites) and `supervisor/state.py:73,960` —
   the latter untouched by upstream despite its commit subject naming the
   "atomic state writer". Not extended here (Proportionality: no requirement,
   and widening an upstream class-fix inside an adoption merge hides it).
3. **Upstream has drifted 6 commits past the adopted SHA** (`8028f1df` at time
   of writing, still 6.105.1). Unclassified by the map; a separate decision.
4. **`prompts/SYSTEM.md` and `docs/CHECKLISTS.md` changed** — a runtime prompt
   and a review criterion, adopted verbatim from upstream. Owner-visible by
   the workspace's own rules even though the content is upstream-authored.

## 15. Gate receipts

| gate | command | result |
|---|---|---|
| ruff | `ruff check . --select F` | All checks passed |
| size ratchet | `scripts/regenerate_size_ratchet.py --check` | rc=0 (manifest regenerated with one band rationale) |
| migration | `scripts/v7_evidence.py check-migration` | `MIGRATION_v7.md OK`, rc=0 |
| migration script | `scripts/v7_migration.py` | rc=0 |
| node | `cd web && npm test` | **581 pass / 0 fail** |
| full parallel | `pytest tests/ -q -n 16` (default addopts) | 3 failures on the first pass, all fixed (§16); clean on re-run |
| serial | `pytest tests/ -q -m serial` | rc=0, 100%, zero failures |
| ledger | `pytest tests/test_v7_migration_ledger.py tests/test_v7_prologue_evidence.py` | three rounds: an inventory desync, a facade disagreement, and finally the registration the ledger test requires — see §18 |

## 16. What the FULL battery caught (and the fixes)

The focused suites were green while these three were not — worth recording,
because each is a different failure mode of the same adoption:

1. `test_control_extraction.py::test_control_catalog_schema_bytes_and_handler_owners_are_stable`
   — the schedule_subagent schema byte-hash moved because upstream rewrote three
   descriptions. **Re-pinned to the new hash with the reason in the test**: the
   pin exists to make a prompt-contract change visible, not to forbid it.
2. `test_v7_verbatim_moves` — `route_health` broke its own verbatim claim because
   the extraction QUOTED its `DelegatedRunShape` annotation. Reverted to the bare
   name: `from __future__ import annotations` already makes it lazy, so the
   `TYPE_CHECKING` import is enough and the text stays byte-identical.
3. `test_v7_migration_ledger` — the two provenance gates disagreed about the
   shared session fixtures. `check-migration` derives "a facade is required" from
   whether the OLD path re-exports the symbol, so adding `FakeGateway` and
   `_run_session_directly` to the routes suite's module-level imports made it
   demand facades; the ledger inventory records both as facadeless split rows.
   Resolved in favour of the inventory both times — each test that needs one
   imports it INSIDE the function, so the module surface is unchanged and the two
   gates read the same fact. Worth naming as a rule: **adding a module-level
   import of a split helper silently changes that helper's provenance shape.**

## 17. Where the map was right, and where it was not

Right, and load-bearing: the 117-file classification, the carrier prediction,
the DEGRADED trap existing at all, the `subagent_dispatch_notes` collision and
its recommended resolution, the `_FROZEN_TOOL_MODULES` hunk being obsolete, the
`queue.py → queue_schedules.py` and `server.py → server_owner_routing.py`
re-home targets, and the warning that `plan_render`/`plan_review_runtime` would
NOT conflict.

Not right, or incomplete:

| map claim | reality |
|---|---|
| DEGRADED has 2 landing sites | 4 — v7's typed-metadata seam duplicates the vocabulary at both ends |
| ~11 new ledger rows | 62 added, 2 repaired |
| repair rows 3859/3860 by re-keying to `subagent_dispatch_notes.py` | those rows stayed valid; the base's extraction needed its OWN rows instead |
| the `followup` landing site is the frozen-inventory pin fixture | that fixture derives; no edit needed. The real pin that moved was the control-catalog schema hash |
| 17 re-homes | 18 (`_attach_client_surface` was missed) |
| `test_a_pool_exhausted_terminal…` → routes suite | delivery suite (helper ownership, §6) |
| — (not mentioned) | three modules crossing the 1500 ceiling, requiring extractions inside the merge commit |
| — (not mentioned) | the verbatim-drift class: moving MERGE_BASE_SHA falsifies "verbatim" notes wherever upstream reflowed an extracted declaration |
| — (not mentioned) | five upstream test assertions written against pre-split shapes |

## 18. The ledger test wants rows REGISTERED, not merely well-formed

`scripts/v7_evidence.py check-migration` and
`tests/test_v7_migration_ledger.py` check different things, and passing the
first says little about the second. `check-migration` validates each row's
shape and resolution. The test additionally classifies EVERY row into one of
three buckets, and a row that belongs to none of them fails:

1. `implemented` — built from the dicts in `tests/_v7_ledger_inventories.py`;
2. `retired_current` — deliberate retirements;
3. everything else — must be a not-yet-built destination whose owner PATH is
   listed in `v7_migration.APPROVED_PENDING_OWNERS`.

New rows for moves that ALREADY happened therefore land in bucket 3 and are
rejected, because their owners are real files rather than approved pending
destinations. The v6.104 adoption solved this with a `merge_adopt_*` block; this
adoption follows it exactly: 31 facade rows and 32 no-facade rows added to
`_v7_ledger_inventories.py` as data, expanded into `implemented`,
`existing_process_owner_rows` and `registry_extraction_no_facade_rows` beside
the v6.104 block.

Worth carrying forward: **an adoption is not finished when the ledger validates
— it is finished when the ledger test can classify every row it added.**
