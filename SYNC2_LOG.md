# SYNC2 — final upstream adoption into v7

Operator artifact for the FINAL upstream-sync lane. Not product documentation;
moved out of the tree at integration.

- Lane branch: `lane_sync2`, base `45cf4570`.
- Upstream cutoff (frozen by owner decision 2026-08-20, "2=B"):
  `managed/ouroboros` @ `8028f1df`.
- Drift adopted: `e7c84240..8028f1df` — exactly one first-parent merge
  (PR #257 `ndrew1337:fix/linux-browser-mode-feedback`), 33 commits,
  42 files, +3456/−245. Verified: `git rev-list --count e7c84240..8028f1df`
  = 33; `git log --first-parent e7c84240..8028f1df` = 1 commit (8028f1df).
- `git merge-base 45cf4570 8028f1df` = `e7c84240` — the drift is exactly the
  upstream side of this merge; nothing else is being pulled in.
- VERSION carriers on both sides are byte-identical at `6.105.1`
  (VERSION, pyproject, web/package.json, api_types GATEWAY_CONTRACT_VERSION,
  gateway/contracts GATEWAY_CONTRACT_VERSION, ARCHITECTURE header, README).

## Protected files in the drift

None. The drift touches no `BIBLE.md`, `prompts/*`, `ouroboros/safety.py`,
`ouroboros/runtime_mode_policy.py`, `ouroboros/tools/registry.py`,
`ouroboros/contracts/`, `docs/CHECKLISTS.md`, `.github/workflows/ci.yml`,
`build*.{sh,ps1}`, `scripts/build_repo_bundle.py` or `supervisor/git_ops.py`.

`ouroboros/gateway/contracts.py` (a frozen gateway contract surface, but not on
the protected list) is touched additively only: two new TypedDicts
(`ProviderTestRequest`/`ProviderTestResponse`), one new `HTTP_ENDPOINTS` entry
and two `__all__` entries. `GATEWAY_CONTRACT_VERSION` is unchanged on both
sides, which is correct for an additive endpoint.

## Map — 42 drift files

### (a) trivial: same path, v7 did not modify it — 23 files

`ouroboros/agent_startup_checks.py`, `ouroboros/gateway/contracts.py`,
`ouroboros/gateway/models.py`, `ouroboros/gateway/router.py`,
`ouroboros/observability.py`, `ouroboros/platform_layer.py`,
`ouroboros/process_containment.py`, `tests/test_build_scripts.py`,
`tests/test_gateway_parity.py`, `tests/test_launcher_headless_fallback.py`,
`tests/test_launcher_sync.py`, `tests/test_observability_outcomes_v2.py`,
`tests/test_owner_settings_write_seam.py`, `web/modules/api_client.js`,
`web/modules/api_types.js`, `web/modules/claudexor_status_store.js`,
`web/modules/logs.js`, `web/modules/reviewer_slots.js`,
`web/modules/settings.js`, `web/modules/settings_ui.js`,
`web/modules/subagents_settings.js`, `web/tests/subagents_settings.test.js`,
`launcher.py` (see a2).

Overlap points named in the lane brief, checked and cleared:

- `web/modules/reviewer_slots.js` / `web/modules/subagents_settings.js`: v7 has
  **no** delta against `e7c84240` for either file (`git diff --name-status
  e7c84240 45cf4570` lists neither). The earlier lane's account-pin restore
  (`96718635`) restored upstream's exact bytes, so PR #257's
  `boundedStatusRefresh` hunks land on unmodified upstream text. The
  `OUROBOROS_SUBAGENT_PROFILE` account-pin block in `subagents_settings.js` is
  untouched by PR #257 (its only hunks are the `boundedStatusRefresh` import
  and the `reloadSubagentsSection` await).
- `tests/test_owner_settings_write_seam.py` is textually trivial, but the test
  it ADDS pins the structure of `ouroboros/gateway/settings.py`. See (b).

**a2 — `launcher.py`: trivial text, non-trivial size.** v7 activated the
`MODULE_DEBT_1500` layer with an EMPTY set, so the hard module ceiling on this
branch is 1500 lines and the layer is shrink-only (`review.py:646-660`).
Upstream's launcher.py is 1572 lines and upstream answered it by dropping
`launcher.py` from `BAND_PATHS` (its own ceiling is `MAX_MODULE_LINES=1600`).
On v7 that is a hard gate failure — a new `MODULE_DEBT_1500` entry cannot be
admitted, because the first-parent (`45cf4570`) >1500 inventory is empty.
Resolution: adopt the upstream hunks and extract enough of launcher.py into a
leaf, inside this merge, to keep it in the band. `BAND_PATHS["launcher.py"]`
must stay present with rationale `None` (surviving band rationales are
immutable).

### (b) both sides changed — manual union — 11 files

1. **`ouroboros/gateway/owner_settings.py`** — the two sides solve overlapping
   but different halves of the same race.
   - v7 (`78debeda`): `_owner_update_settings(transform, expected_digest, ...)`
     — the read-merge-write happens INSIDE the settings FILE lock, plus a
     document-digest precondition and `settings_document_digest()`;
     `_owner_write_settings` becomes a thin wrapper.
   - upstream: an in-PROCESS `threading.Lock` exported as
     `settings_document_mutation()`, held by every writer across
     read-merge-write **and** the post-commit env projection / side effects.
   - Union: keep v7's primitive, adopt upstream's lock verbatim (module-level
     `_settings_document_lock`, the contextmanager, the `__all__` entry). The
     two are complementary: the file lock cannot order the *env projection* of
     two in-process writers, and the digest cannot see a queue change.
2. **`ouroboros/gateway/settings.py`** — upstream moves the generic save and
   all five single-decision endpoints off the event loop
   (`asyncio.to_thread(_api_*_sync, ...)`) and wraps their bodies in
   `settings_document_mutation()`; v7 rewrote the same bodies to
   `_owner_update_settings(transform, digest)`. Union: adopt upstream's
   `_sync`/`_locked` split and lock placement verbatim; keep v7's write
   mechanism as the body. Upstream's in-lock `current = _owner_read_settings_raw()`
   re-read is dropped where v7's transform already reads inside the file lock.
   Upstream's under-lock re-prove of BOTH halves of the context-mode idle guard
   is kept — it is not subsumed by the digest, because `_has_running_agent_tasks()`
   reads the QUEUE, which changes without touching the settings document.
3. **`ouroboros/gateway/onboarding.py`** — v7 replaced the local fingerprint
   body with `settings_document_digest()`; upstream wrapped `_persist`'s write +
   env projection + supervisor start + hot-reload in
   `settings_document_mutation()`. Both adopted; they touch different lines
   except for the import block, which takes both names.
4. **`docs/ARCHITECTURE.md`** — v7 rewrote 341 lines of the module tree;
   upstream adds 9 net lines (llm_probe.py and launcher_server_reaper.py tree
   rows, models.py/platform_layer.py row rewordings, the PID-lock reaping
   paragraph, the Provider Test paragraph, the `/api/providers/test` route row,
   the Provider Test max-tokens row, the Provider Test ABI row). Union by hunk.
5. **`ouroboros/size_ratchet_manifest.py`** — generated data. Resolved by
   regenerating with `scripts/regenerate_size_ratchet.py` against the merged
   tree, not by merging text.
6. **`tests/fixtures/chat_logs_ui_static_checks.json`** — single-line JSON; git
   will conflict on the whole line. Upstream adds exactly 7 entries (all
   `test_logs_autoscroll_sticks_only_when_pinned`, all against
   `web/modules/logs.js`, which v7 does not modify); v7 rewrote 74 of its own.
   Union at the parsed-entry level, preserving upstream's insertion position.
7. **`ouroboros/usage_accounting.py`** — upstream redacts `mark_unresolved`
   reasons and makes `_provider_exception_facts` fail closed. v7's changes are
   elsewhere in the file; expect clean auto-merge, verified by AST parse.
8. **`tests/test_usage_accounting.py`** — additive test, anchor
   `test_attempt_lifecycle_and_root_projection` intact in v7.
9. **`tests/test_max_tokens_constants.py`** — additive test importing
   `ouroboros.llm_probe.PROVIDER_TEST_MAX_TOKENS`; anchors intact.
10. **`tests/test_owner_facing_honesty.py`** — additive test; anchors
    (`test_stray_server_check_matches_packaged_install`,
    `test_stray_server_check_ok_when_clean`) intact in v7.
11. **`tests/test_packaged_runtime_and_lifecycle.py`** — one added monkeypatch
    line for `launcher._reap_same_install_strays`; anchor intact.

### (c) file was SPLIT / moved by v7 — re-home the hunks — 2 files

- **`ouroboros/llm.py`** — v7 gutted it (4000 → 716 lines). Owners verified by
  `def` search in the v7 tree:
  - `LLMClient._resolve_remote_target`, `_get_remote_client`,
    `probe_oversized_context` → `ouroboros/llm_routing.py`
  - `LLMClient._get_gigachat_client` → `ouroboros/llm_gigachat.py`
  - the helper `_new_remote_client` and the new `probe_provider_readiness`
    delegator go to the same leaves as their siblings.
  - `ouroboros/llm_probe.py` is a NEW upstream file (bucket d) and its
    imports must be re-pointed at the v7 leaves (`llm_attempt` owns
    `_physical_candidate` / `_attempt_request` / `_execute_candidate`).
- **`tests/test_extension_loader.py`** — v7 split it; the hunk's target
  `test_reload_all_called_on_settings_save` now lives in
  `tests/test_extension_reload_all.py`. Re-home there.

### (d) new upstream files — adopt — 6 files

| file | lines | ≤1500 |
| --- | --- | --- |
| `ouroboros/launcher_server_reaper.py` | 311 | yes |
| `ouroboros/llm_probe.py` | 395 | yes |
| `tests/test_launcher_server_reaper.py` | 560 | yes |
| `tests/test_port_sweep_listener_scope.py` | 91 | yes |
| `tests/test_provider_key_test.py` | 677 | yes |
| `web/tests/provider_test.test.js` | 55 | yes |

## Projected module sizes worth watching (band ceiling 1500)

| path | v7 now | + upstream net | projected |
| --- | --- | --- | --- |
| `launcher.py` | 1499 | +73 | **1572 — must be split** |
| `ouroboros/platform_layer.py` | 1468 | +30 | 1498 |
| `ouroboros/gateway/settings.py` | 1397 | +91 | ~1488 (union may differ) |
| `web/modules/settings.js` | 1307 | +148 | 1455 |
| `tests/test_usage_accounting.py` | 1389 | +34 | 1423 |
| `ouroboros/gateway/contracts.py` | 1375 | +13 | 1388 |
| `ouroboros/usage_accounting.py` | 1369 | +8 | 1377 |

## Resolution log

`git merge --no-commit --no-ff 8028f1df` produced 7 conflicted files. Everything
else auto-merged and was verified by reading the result, not by trusting git.

| conflict | resolution |
| --- | --- |
| `tests/test_extension_loader.py` | Whole upstream block dropped (`--ours`): v7 had already moved those tests to sibling suites. The one changed test's hunk was re-homed into `tests/test_extension_reload_all.py`, its owner. |
| `tests/fixtures/chat_logs_ui_static_checks.json` | Parsed-entry union: v7's 207 entries + upstream's 7, re-serialized with v7's exact spelling (`separators=(",", ":")`, `ensure_ascii=False`, no trailing newline), byte-identical prefix asserted. |
| `ouroboros/size_ratchet_manifest.py` | Generated data — kept v7's, then re-proved by `regenerate_size_ratchet.py --check`. Upstream's own edit (dropping `launcher.py` from `BAND_PATHS`, dropping `llm.py` byte debt) is meaningless here: launcher.py stays in the band after the split, and v7's llm.py is 716 lines. |
| `ouroboros/llm.py` | `--ours` (v7 gutted the file), hunks re-homed. See below. |
| `docs/ARCHITECTURE.md` | Two hunks. The module-tree block kept v7's ten-mixin listing with upstream's `llm_probe.py` row inserted after `llm_pricing.py`; the settings-save paragraph kept v7's retired-timeout sentence and took upstream's three new sentences (document lock, `asyncio.to_thread`, the bounded status beat) before the closing sentence. |
| `ouroboros/gateway/onboarding.py` | Import block takes both names (`settings_document_digest` + `settings_document_mutation`); upstream's `_persist` lock body auto-merged around v7's digest. |
| `ouroboros/gateway/settings.py` | Six body conflicts, resolved per the union rule below. |

### llm.py hunks, re-homed

| upstream declaration | v7 owner | verbatim vs 8028f1df |
| --- | --- | --- |
| `LLMClient._resolve_remote_target` | `llm_routing.py::_ProviderRoutingMixin` | byte-identical |
| `LLMClient._new_remote_client` (new) | `llm_routing.py::_ProviderRoutingMixin` | byte-identical |
| `LLMClient._get_remote_client` | `llm_routing.py::_ProviderRoutingMixin` | byte-identical |
| `LLMClient.probe_oversized_context` | `llm_routing.py::_ProviderRoutingMixin` | byte-identical |
| `LLMClient.probe_provider_readiness` (new) | `llm_routing.py::_ProviderRoutingMixin` | byte-identical |
| `LLMClient._new_gigachat_client` (new) | `llm_gigachat.py::_GigaChatLaneMixin` | byte-identical |
| `LLMClient._get_gigachat_client` | `llm_gigachat.py::_GigaChatLaneMixin` | byte-identical |

All seven were compared declaration-by-declaration against `8028f1df:ouroboros/llm.py`
before the merge was committed, so the ledger's verbatim rows hold against the new base.
`llm_routing.py` shed the four `llm_attempt` imports and the three `usage_accounting`
imports the probe body used to need, and its module docstring now says where the probe
transport lives.

`ouroboros/llm_probe.py` is adopted whole apart from a deliberate re-point. Its exact
delta against `8028f1df` is `+8 / −6` in two hunks — one EXECUTABLE statement and two
pieces of prose that would otherwise have described the wrong owner:

- `_accounted_send`: the lazy import target changes from `ouroboros.llm` (the facade) to
  `ouroboros.llm_attempt` (the owner), because an `llm_*` leaf must never import its
  parent — pinned by
  `tests/test_llm_extraction.py::test_llm_leaves_never_import_their_parent`, which now
  covers `llm_probe` along with the other ten leaves. This is also where the executor a
  test patches actually lives, which is what made the adopted provider-probe suite pass
  again (see "Upstream assertions adapted at the test", item 1).
- the two-line comment above that import is replaced by a four-line one stating the leaf
  rule and that the laziness is now about startup cost, not about a cycle with `llm.py`.
- the module docstring sentence naming the owner of target resolution and client
  construction changes from `ouroboros.llm.LLMClient` to the routing leaf the client
  composes (`ouroboros.llm_routing`), which is where those methods live on this branch.

Nothing else in the file differs; the executable payload is otherwise byte-identical.

### The settings write seam — how the two designs compose

Both sides fixed the same class of race and neither is redundant:

- v7 `_owner_update_settings(transform, expected_digest)` moves the read-merge-write
  INSIDE the settings FILE lock and adds a document-digest precondition.
- upstream `settings_document_mutation()` is an in-PROCESS lock held across the write
  AND the post-commit environment projection, plus `asyncio.to_thread` so a save never
  freezes the event loop.

The file lock cannot order two in-process writers' `os.environ` projections; the digest
cannot see a queue change. So: upstream's structure is adopted verbatim (the `_sync`
split, the `to_thread` hop, the lock spans, the comments) and v7's write mechanism stays
as the body. The re-read dropped is upstream's `current = _owner_read_settings_raw()`
inside the FIVE dedicated endpoints, and only there: v7's transform already reads the
fresh document inside the mandatory file lock, and the digest carries the refusal. No
re-read was dropped on the generic save — it never had one on either parent (below).

Per endpoint:

- `runtime_mode` — digest and deciding read stay pre-lock (both sides had them there);
  the write is wrapped in the document lock.
- `auto_grant` — no digest (the body carries the whole decision); the write and the env
  projection share the lock.
- `context_mode` — upstream's under-lock re-prove of BOTH halves of the idle guard is
  kept. It is NOT subsumed by the digest: `_has_running_agent_tasks()` reads the queue,
  which changes without touching the settings document.
- `scope_review_floor`, `safety_mode` — their pre-lock read only fed the audit line's
  `previous` value, so digest and read moved INSIDE the lock; the audit now names the
  value the write actually replaced. This is the one place the resolution is tighter
  than either side alone.

#### The generic save merges a snapshot, on both parents — inherited, not introduced

The generic `POST /api/settings` does NOT re-merge the incoming keys onto a document read
under the file lock. It reads (`load_settings()` + `_owner_read_settings_raw()`), merges
the body into a FULL snapshot via `_merge_settings_payload`, and persists that snapshot.
Provenance, checked against both parents rather than asserted:

- upstream `8028f1df:ouroboros/gateway/settings.py` — the same two reads and the same
  `_owner_write_settings(settings_to_save, …)` snapshot write; its `_owner_write_settings`
  takes the file lock and writes the caller's dict with no read of its own at all.
- v7 `45cf4570` — structurally identical reads/merge/write. Its `_owner_write_settings`
  is `_owner_update_settings(lambda _current: settings, …)`: it DOES read the fresh
  document under the file lock and then deliberately discards it, keeping the caller's
  snapshot. That body is byte-identical at `45cf4570` and at HEAD (verified by diffing the
  function).

So the snapshot-staleness of the generic path is inherited from BOTH parents; this sync
introduced none of it, and the union dropped nothing load-bearing there. What makes the
generic save safe against the other GATEWAY writers is not the digest (it passes none)
but the transaction: its read and its write sit inside one `settings_document_mutation()`
hold, and every other gateway writer takes the same lock. `_owner_update_settings`'s
digest remains available and is used by the four dedicated endpoints that take a decision
from an earlier read; wiring it into the generic path would be a behaviour change to a
frozen final sync, so it is not done here — it is named in the backlog below.

Pinned by `tests/test_owner_settings_write_seam.py::test_a_second_gateway_writer_cannot_erase_the_first_writers_committed_key`:
the generic save is parked inside its lock with its snapshot already built, a dedicated
writer is started concurrently and proved unable to land while the lock is held, and after
both run each other's key survives. The pin was mutation-checked — with
`settings_document_mutation()` neutered to a bare `yield` it fails on exactly that
assertion.

#### Lock order and the boundary of the closed set

Within the gateway settings seam the acquisition order is document → file at every call
site, and no site acquires them the other way round, so no inversion exists.
`settings_document_mutation()` is a plain `Lock` and nothing under it re-enters it. The
closed set is exactly: the five dedicated owner endpoints, the generic
`POST /api/settings`, and the onboarding transaction — membership pinned by
`tests/test_owner_settings_write_seam.py::test_settings_save_body_runs_off_the_event_loop`,
which fails if a `_owner_write_settings` call site appears in `gateway/settings.py`
outside a locked writer.

It is NOT every settings writer in the tree, and the earlier phrasing of this section
("document → file at every call site") overclaimed that. Writers outside the set take the
FILE lock only and can still lose an update against a gateway save:

| writer | why it is outside |
| --- | --- |
| `ouroboros/tools/control_runtime.py::_set_tool_timeout` | full-document read → `save_settings` → `apply_settings_to_env`, no document lock. `git diff 45cf4570 HEAD -- ouroboros/tools/control_runtime.py` is EMPTY: unchanged by this sync, present on both parents. |
| `launcher.py::_load_settings` / `_save_settings` | same shape, and a DIFFERENT PROCESS — an in-process lock cannot serialize it even in principle. Untouched by this sync (no settings-write line appears in `git diff 45cf4570 HEAD -- launcher.py`). |
| `ouroboros/config.py` save helpers | the persistence primitives themselves; the file lock is their whole contract. |

## Disclosed residuals (post-7.0 backlog, none introduced by this sync)

Each was checked against both parents before being classified as inherited.

1. **Settings writers outside the document-locked seam** (the table above). A real
   lost-update window: an agent reads in `_set_tool_timeout`, an owner save commits and
   projects env, the agent then persists its stale full document and projects stale env.
   Closing it needs a settings-transaction contract every writer passes through — the
   in-process lock cannot be it, because the launcher is a separate process. Related:
   the generic save could take a digest today; both belong to the same decision.
2. **Onboarding does blocking settings I/O on the event loop.** `api_onboarding_complete`
   calls `_settings_fingerprint()` and `_prepared_settings()` (→ `load_settings()`, which
   can wait up to the settings-lock timeout) BEFORE its `asyncio.to_thread(_persist, …)`
   hop, and runs `_owner_audit` synchronously after it. Provenance: the whole
   `api_onboarding_complete` body is **byte-identical** at `8028f1df`, at `45cf4570` and at
   HEAD (diffed as a region against both parents) — this sync moved nothing into or out of
   the thread hop. `docs/ARCHITECTURE.md`'s adopted sentence about moving synchronous
   selection/lock/write work to a thread describes the generic save and the five dedicated
   endpoints, which do; onboarding's pre-thread preparation is the exception it does not
   name.
3. **The context-mode idle guard is not linearized with the queue.** `PENDING.pop()` and
   the `RUNNING` insert in `supervisor/worker_assignment.py` are not atomic, so both the
   pre-lock and the under-lock `_has_running_agent_tasks()` can answer "idle" while a task
   is being assigned. The document lock cannot fix that; it needs a queue/settings
   handshake. Pre-dates this sync and is disclosed in the endpoint's own comment — the
   under-lock re-prove this merge adopted narrows the window, it does not close it.

### launcher.py: the size gate

Upstream's launcher.py is 1572 lines. v7 activated `MODULE_DEBT_1500` with an EMPTY set,
and the layer is shrink-only (`ouroboros/review.py`), so no new >1500 entry can be
admitted — the first-parent census that would authorize one is empty. The Windows-only
pythonnet/pywebview preparation (`_show_windows_message`,
`_prepare_windows_webview_runtime`, `_windows_dll_dir_handles`, 102 lines) moved verbatim
into `ouroboros/launcher_windows_runtime.py` INSIDE the merge; launcher.py re-exports the
two functions under their original names. Result: launcher.py 1474 lines, still in the
band, `BAND_PATHS["launcher.py"]` unchanged (`None`, and surviving band rationales are
immutable).

Why this cluster and not the server-record cluster: the record helpers are monkeypatched
through the launcher module in four suites (`launcher.DATA_DIR`, `launcher.pid_is_alive`,
…), so moving them would have forced test rewrites. The Windows cluster has no
monkeypatch surface at all and only one source-scan pin, which the re-export satisfies.

### Upstream assertions adapted at the test (never by weakening the runtime)

1. `tests/test_provider_key_test.py::_bypass_accounting` patched
   `ouroboros.llm.execute_physical_attempt`. In v7 `_execute_candidate` lives in
   `llm_attempt.py` and reads the name from THAT module, so the patch was dead and six
   tests failed. Re-pointed at `llm_attempt` — the same seam, named at its owner, exactly
   as ten existing v7 suites already do.
2. `tests/test_settings_read_seam.py::test_every_owner_endpoint_reaches_the_same_normalized_read`
   named the async endpoints. After upstream's thread hop the document work lives in the
   `_sync` bodies (and one level deeper for the generic save), so the expected set names
   those. The contract — one seam, not six patches — is unchanged.
3. `tests/test_llm_extraction.py` gained `llm_probe` in `_LEAVES`, so the leaf rules
   (never import the parent, no cycles, ≥200 and ≤1000 lines) bind it too.

## Follow-ups

- `899a912f` — `MERGE_BASE_SHA` → `8028f1df`; three MIGRATION_v7.md rows for the launcher
  extraction (two facade, one not); `tests/_v7_ledger_inventories.py` gains
  `merge_adopt_pr257_facade_rows` / `merge_adopt_pr257_no_facade_rows`, expanded into
  `implemented` beside the v6.105 block so the ledger test does not drop them into the
  pending bucket; `tests/test_launcher_sync.py` gains the facade characterization test.
- Review fix round (adversarial review, GPT-5.6 Sol xhigh, NEEDS FIXES). Accepted and
  applied: the ledger had no rows for the three methods the cutoff ADDED to `LLMClient`
  and the merge re-homed — `_new_remote_client` and `probe_provider_readiness` into
  `_ProviderRoutingMixin`, `_new_gigachat_client` into `_GigaChatLaneMixin`. Three
  MIGRATION_v7.md rows in the style of the other seven llm re-homes, the same three names
  added to `llm_mixin_symbols_by_owner` (which is what buckets them as implemented) and to
  `_MIXIN_OWNERS` (the exact inventory the extraction test enforces), and the composed
  member digest moved once with its reason recorded in the test's own docstring. The
  remaining findings were resolved by provenance, not by code: see the generic-save
  subsection and the disclosed-residuals list above.

## Version carriers

All declared carrier spans (`ouroboros/tools/release_sync.py::VERSION_CARRIER_SPANS`)
agree at `6.105.1` after the merge, and both sides carried the same value going in:
`VERSION`, `pyproject.toml`, `web/package.json`,
`web/modules/api_types.js` (`GATEWAY_CONTRACT_VERSION`), the README badge, the README
Version History block, the `docs/ARCHITECTURE.md` header, and `uv.lock`'s root package.
`GATEWAY_CONTRACT_VERSION` is deliberately unmoved: PR #257's gateway change is purely
additive (`ProviderTestRequest`/`ProviderTestResponse`, one route, two `__all__` names).

## Gate receipts

| gate | result |
| --- | --- |
| `scripts/v7_evidence.py check-migration` | `MIGRATION_v7.md OK` — rc 0 |
| `scripts/v7_evidence.py check` | `v7 evidence OK (5bce0cee…047e8)` — rc 0 |
| `scripts/regenerate_size_ratchet.py --check` | silent pass — rc 0 (no regeneration needed: nothing entered or left a tracked debt band) |
| `ruff check . --select F` | `All checks passed!` — rc 0 |
| `pytest tests/test_v7_verbatim_moves.py` | `1 passed in 58.72s` — rc 0 |
| `pytest tests/test_v7_migration_ledger.py` | `1 passed in 228.49s` — rc 0 |
| `pytest tests/ -n 16` (default lanes, not serial) | `10312 passed, 3 skipped, 28 warnings in 430.91s` — rc 0 |
| `pytest tests/ -m serial` | `431 passed, 12 skipped, 10406 deselected in 236.01s` — rc 0 |
| `cd web && npm test` | `# pass 584 / # fail 0` — rc 0 |

`git rev-parse HEAD` was re-read after every pytest invocation and never moved.

### Review fix round

| gate | result |
| --- | --- |
| `pytest tests/test_v7_migration_ledger.py tests/test_llm_extraction.py tests/test_v7_verbatim_moves.py tests/test_owner_settings_write_seam.py tests/test_settings_read_seam.py tests/test_onboarding_complete_endpoint.py` | `103 passed, 1 warning in 306.22s` — rc 0 |
| `scripts/v7_evidence.py check-migration` | `MIGRATION_v7.md OK` — rc 0 |
| `scripts/v7_evidence.py check` | `v7 evidence OK (5bce0cee…047e8)` — rc 0 |
| `scripts/regenerate_size_ratchet.py --check` | silent pass — rc 0 |
| `ruff check . --select F` | `All checks passed!` — rc 0 |

Mutation check on the new pin: with `settings_document_mutation()` neutered to a bare
`yield`, `test_a_second_gateway_writer_cannot_erase_the_first_writers_committed_key` fails
on the serialization assertion. The mutation was scratch-only and reverted before any
gate ran; `git diff HEAD -- ouroboros/gateway/owner_settings.py` was empty afterwards.
