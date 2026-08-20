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

(filled in as the merge is resolved)
