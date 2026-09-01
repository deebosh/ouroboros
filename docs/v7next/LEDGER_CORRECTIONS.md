# Ledger corrections discovered during v7next transplants (append-only)

Rows of the reference MIGRATION_v7.md / DOMAIN_MAP.md falsified by upstream drift,
with evidence, found lane by lane. Applied to the campaign's carried ledger at F5.

## From the D15 pilot (base b9f7597f, 2026-08-30)
1. MIGRATION row 351 (`tools/core.py::_filter_out_project_store` ->
   `project_facts.py::filter_out_project_store`, status "pending upstream
   transfer") — SUPERSEDED-BY-UPSTREAM: the tip already carries the extraction
   (project_facts.py byte-identical to the reference; core.py keeps only the
   import alias at :17 with two call sites).
2. DOMAIN_MAP §D15 "v7 delta" prose — remeasure from the new base: consolidator
   delta absorbed upstream (now 0); the true residue is +23/-12 in two files
   (consciousness.py, reflection.py), not +25/-14 in three.
3. RE-PROVE TRAP (D02 family, reflection.py): the reference's
   `_trace_call_errored` reads `_OK_TOOL_STATUSES` (with "untyped") from the v7
   leaf `_outcome_tool_errors`, which upstream does not have; a verbatim replay
   of the delta over upstream's own status handling would invert the fix. The
   D02 adoption must re-derive the delta against upstream bytes.
4. MIGRATION row 166 (retirement of 4 CLAUDE_CODE markers, id "none") — needs an
   explicit ADOPTION disposition (umbrella under D02 or its own row): zero
   production emitters of those markers exist at this tip (claim re-proven).

## From the D16 split pilot (base 5d3398c1, 2026-08-30)
5. MIGRATION row 3911 (`usage_accounting.py::_legacy_snapshot` ->
   `usage_legacy_import.py::_legacy_snapshot`, "verbatim extraction") —
   BYTE-FALSIFIED as a copy source, transform still valid: upstream e9bf6f14
   rewrote the settings-hash comment inside the span (two lines "... prove
   non-mutation by hash, but never copy / their contents into the usage
   archive." became one line "... never copy contents."). The tool's --check of
   the reference leaf against tip bytes fails token-lockstep on exactly this
   span (ast=True, tokens=False); re-emitting from tip bytes is proof-green on
   the first round with the reference declared set {_legacy_snapshot, _locked,
   _read_records_locked} unchanged. Copying the reference leaf verbatim would
   have silently reverted an upstream comment edit.
6. MIGRATION rows 3910-3914 status "pending upstream transfer" — RE-CONFIRMED
   at this tip (contrast with the D15 project_facts case, entry 1 above):
   upstream still carries the unsplit legacy import inside
   ouroboros/usage_accounting.py (1600 lines, exactly at the hard cap;
   IMPORT_REL at :60, the four defs at :1374-:1600). The extraction was
   performed by this lane from tip bytes.

## From the D03 lane (base f61ea3c2, 2026-08-30)
7. MIGRATION rows 3943-3946 (`ouroboros/context.py::{_project_room_fact,
   _runtime_budget_info,_promoted_task_toolset,_delegation_capability_fact}` ->
   `ouroboros/context_runtime_facts.py`, "pending upstream transfer") —
   RE-CONFIRMED pending at this tip (context.py 1590 lines, the four defs at
   :325-:544); the extraction was performed by this lane from tip bytes. The
   reference leaf is BYTE-FALSIFIED as a copy source for ONE of the four
   symbols: upstream b14ba397 ("expose available subagents in runtime
   context") rewrote `_delegation_capability_fact` (docstring collapsed to a
   one-line summary, `configured_route` dropped from the returned fact,
   requested/applied profile evidence and `selected_subagent_id` added, plus
   an all-absent -> None guard). Drift-probe `--check` of the reference leaf
   against tip bytes: 3/4 spans ast=tokens=bytes=True, this span
   ast=False/tokens=False; re-emitting from tip bytes was proof-green on the
   first round. Copying the reference leaf verbatim would have silently
   reverted the upstream subagent-profile feature.
8. MIGRATION row 3960 (`tests/test_context.py::
   test_delegation_fact_carries_configured_route_and_historical_rows` ->
   `tests/test_context_runtime_section.py::<same>`) — SOURCE SYMBOL FALSIFIED
   by the same upstream train: b14ba397 replaced the test with
   `test_delegation_fact_carries_historical_rows_and_profile_evidence`
   (asserts `"configured_route" not in delegation`). The upstream successor
   was moved to the row's destination as an identity continuation (tip
   bytes); the carried ledger must rename the row at F5.
9. MIGRATION row 1641 (`tests/test_context.py::
   test_runtime_section_includes_improvement_backlog_digest` ->
   `tests/test_context_runtime_section.py::<same>`) — SOURCE SYMBOL FALSIFIED:
   upstream 1b7f9497 replaced the test with
   `test_improvement_backlog_digest_is_actor_scoped` (the digest is now
   asserted ABSENT for ordinary/main/project/subagent tasks and present only
   for evolution/deep_self_review). Moved to the row's destination as an
   identity continuation (tip bytes); rename at F5.
10. S7a rows 1614-1640/1642-1648 — RE-CONFIRMED against tip bytes: every other
   moved symbol of the tests/test_context.py split is byte-identical between
   the tip monolith and the reference siblings (the D15-carried
   tests/test_context_memory.py re-derived from tip bytes came out identical
   — the carry was NOT stale), except row 1623's span
   (`test_force_plan_metadata_adds_structured_notice_without_rewriting_user_text`),
   which upstream drifted ADDITIVELY (rc-phaseC execution-shape assertions) —
   tip bytes transplanted. Note: between the D15 pilot and this lane the 15
   memory tests existed in BOTH tests/test_context.py and
   tests/test_context_memory.py on the integration branch (ran twice); this
   lane completed the split and deduplicated.
11. NO-ROW upstream additions (candidate rows for the carried ledger): 3459dd12
   added 8 recent-chat/archive-generation tests to tests/test_context.py
   (filters_archives_before_recent_bound, retention_proof_cross_thread,
   reads_only_bounded_generation_suffix, materializes_a_bounded_row_suffix,
   malformed_gap_even_when_search_matches_nothing,
   resumes_unconsolidated_archived_generation,
   archive_only_chat_chain_is_complete, missing_cursor_generation_hot_path).
   They have no MIGRATION rows, so this lane left them in the remainder
   tests/test_context.py (612 lines) rather than deciding their theme-home
   unilaterally; by the memory-file theme they are candidates for
   tests/test_context_memory.py at F5.
## From the D09 lane (base f61ea3c2, 2026-08-30)
7. MIGRATION rows 998-1013 (the 16-symbol task_lifecycle.py ->
   cancel_custody.py settle-owner extraction) — HOT-FALSIFIED as a transplant
   at this tip: upstream 65b5d19f ("Refactor cancellation ownership for size
   ratchet") re-decomposed the same ownership differently (task_lifecycle
   -408 lines into cancel_publication.py, owner_stop.py,
   queue_transitions.py, task_reaper.py, new evolution_lifecycle.py, new
   task_admission.py), then 3877e2ce/bea08137/21c59de2 reworked the
   survivors. Of the 16 declared symbols, _intent_outcome_fields now lives
   in cancel_publication.py:133 (task_lifecycle re-exports it at :26-35),
   _durable_settled_status no longer exists, and the remaining bodies were
   hardened by bea08137. Transplanting the reference cancel_custody.py would
   create a second ownership answer -> F2 (cancel/delegation organ, re-split
   from the upstream form).
8. MIGRATION rows 834-839 (cancel_intents.py D08 corrupt-projection rule) —
   PARTIALLY SUPERSEDED-BY-UPSTREAM: at this tip request_cancel and
   claim_intent already read strict and raise CancelIntentProjectionCorrupt
   (upstream custody train 34ca9b02/38196641/c8048f2c/bea08137 rewrote the
   module 888 -> 1281 lines), while release_claim, settle_intent,
   mark_intent_scope and mark_finalize_control_drained remain fail-open
   (AST probe over tip bytes; the reference pin
   test_cancel_intent_corruption_s6.py runs red on exactly those four).
   D08 must be re-derived against the rewritten bytes in F2 — same class as
   entry 3 (the re-prove trap).
9. MIGRATION rows 2152-2180 (the S7b split of
   tests/test_cancel_intents_phase_a.py) — falsified as a verbatim
   transplant: the giant drifted upstream since the merge-base, and the
   split's custody rows retarget monkeypatches to supervisor.cancel_custody,
   which this tip does not have (row 2171's own note binds the split to the
   extraction commit e3c107bd). Rides with entry 7 into F2.
10. DOMAIN_MAP §D09 pin test_subagent_worktree_registry_s6.py —
   cross-listed: the module it pins, ouroboros/subagent_worktrees.py, is a
   D07 owner, and the strict-registry behaviour the pin asserts lives in the
   reference's +104/-22 delta to that module (upstream never touched it:
   tip == merge-base). The pin transfers with D07's module delta, not with
   the D09 lane (11 of its tests are red without it).
11. DOMAIN_MAP §D09 pin test_daemon_token_containment_s6.py — HOT-DEFERRED
   with the delegation organ: its fixture's fresh delegate_start is refused
   at this tip with reason "subagent_selection_required" ("A fresh delegated
   start requires an explicit agent_session subagent_id. Only retry_of may
   replay a selectorless immutable invocation.") — the upstream
   delegation-by-construction train changed the entry contract the fixture
   drives.
12. Two reference pins byte-falsified by upstream drift, residual facts
   intact, re-pinned to tip bytes by this lane: (a)
   test_panic_stop_port_sweep.py — the panic's kill_workers call now carries
   reconcile_delegate_custody=False (dc4c0204), and this tree has 5
   ouroboros/server_*.py host leaves, not the reference's >= 11 (that floor
   returns with the D11 server split); (b) test_owner_stop_fences_s6.py C5 —
   _settle_descendants_hard now reuses the ordinary cascade's bounded
   re-sweep loop (65b5d19f), so one live child yields two token-less sweep
   calls instead of one; the pinned durable fact (the owner-stop sweep is
   token-less) is unchanged.
## From the D17 lane (base def681bd, 2026-08-30)
7. Runtime split rows 465-494 (`headless.py` -> `headless_status.py` (11) +
   `workspace_patch_capture.py` (19), "verbatim extraction") — RE-PROVEN at
   this tip: all 30 spans byte-identical between the reference leaves and
   `git show HEAD:ouroboros/headless.py` (hardened transplant --check, ast/
   tokens/bytes all green, both leaves, exit 0). The facade differs from the
   reference only by upstream residue drift (child_ref promotion machinery,
   `TASK_COST_META_FIELDS`/`replace_atomic` import changes) — replayed from
   tip bytes, 947 lines.
8. Test-split rows for `tests/test_workspace_executor.py` ->
   `test_workspace_executor_services.py` ("verbatim") — BYTE-FALSIFIED as a
   copy source for exactly two functions, transform still valid: upstream
   06339bb7 ("fix: preserve service readiness truth") rewrote
   `test_executor_local_service_lifecycle_hides_private_snapshot` (the READY
   marker is now planted before a 25k log suffix and asserted scanned) and
   upstream a849c9a6 ("fix: preserve executor probe uncertainty") extended
   `test_executor_service_status_and_durable_record_redact_secret_like_args`
   (adds the `'"readiness"' not in durable_text` clause). Both re-emitted
   from tip giant bytes; the other 26 moved wexec spans are byte-identical.
9. Reference residual `tests/test_headless_cli.py` and sibling
   `test_headless_workspace_shell.py` carry OTHER domains' v7 spellings
   inside 9 moved/kept spans (`_run_shell_safety_check(registry, ...)` typed
   result + `core_file_tools._repo_read` — D04/D05 split; `queue.init(path)`
   1-arg signature and `supervisor.state.QUEUE_SNAPSHOT_PATH` — D08/D33).
   On this tree those leaves/signatures do not exist; per §5.3-Δ item 2 every
   such span was reverse-mapped to the upstream spelling keyed to
   `git show HEAD:tests/test_headless_cli.py` (upstream: string-returning
   `registry._run_shell_safety_check`, module-binding `_repo_read`,
   `queue.init(path, 600, 1800)`, `queue.QUEUE_SNAPSHOT_PATH`). These
   adaptations return with their owning lanes, not with D17.
10. Thirteen upstream test functions written after the reference cutoff have
    NO ledger rows (hcli: 4 task-api + 1 artifact-endpoint; wexec: 6 docker
    stop/cleanup + 2 readiness). Placed by the split's own theme rule with
    imports satisfied by the target headers (task_api×4, task_artifacts×1,
    docker×6, services×2 + one `SimpleNamespace` header import); the carried
    ledger needs rows minted for them at F5. Placement is disclosed, not
    ledger-derived.
11. Row evidence `tests/test_headless_extraction.py` (rows 465-494): the
    reference pin imports `ouroboros.tool_module_inventory` (a D04-family v7
    leaf absent from this tree); the transplanted pin keeps every clause that
    types against THIS tree and replaces the frozen-tool-inventory clause
    with an oracle-SHA note — the clause returns with the tools lane.
12. `ouroboros/task_results.py` (upstream-hot, +555 lines drift): the ledger
    assigns NO D17 runtime split to it, and the reference copy is
    byte-identical to the merge base (zero v7 delta) — nothing to transplant,
    upstream bytes stand. Same zero-v7-delta fact re-proven for all 14
    non-split D17 runtime modules (task_status, retention, coop_checkpoint,
    projects_registry, project_dialogue, project_lease, project_naming,
    project_sources, tools/project_journal, workspace_admission,
    workspace_preflight, workspace_executor, workspace_patch_rules).
## From the D18 lane (base d830cdba, 2026-08-30)
13. MIGRATION rows 3998-4000 (`launcher.py::{_prepare_windows_webview_runtime,
    _show_windows_message,_windows_dll_dir_handles}` ->
    `ouroboros/launcher_windows_runtime.py`, "pending upstream transfer") —
    RE-CONFIRMED pending and transplanted by this lane. Drift-probe: hardened
    `--check` of the reference leaf against `git show d830cdba:launcher.py`
    is green on all three spans (ast=tokens=bytes=True, leaf invariants [],
    exit 0), so the reference leaf IS tip bytes; adopted verbatim. Facade =
    tip monolith minus the three spans plus the reference's re-export block;
    byte-diff against the reference facade is exactly upstream dc4c0204's
    +10 delegated-restart hunk (replayed from tip bytes). launcher.py
    1582 -> 1484 lines; band re-entry authorized via the official
    regenerator's --band-rationale.
14. MIGRATION row 917 (`ouroboros/packaged_cli.py::_save_settings`, semantic
    id D03: route the packaged bootstrap saver through the shared persistence
    prologue and serializer) — HOT-DEFERRED with the settings seam. At this
    tip `prepare_settings_for_persist` ALREADY EXISTS in ouroboros/config.py
    :1084 (upstream absorbed part of the seam with a different signature —
    an added `authored_keys` kwarg), while `serialize_settings` and the
    row's pin tests/test_settings_read_seam.py do not exist. A verbatim
    replay would bind a half-absorbed seam; the delta must be re-derived
    against the tip seam form when the D12 config/settings split lands.
    packaged_cli.py itself: tip == merge-base (zero upstream drift), so the
    module stays untouched by this lane.
15. Reference `ouroboros/utils.py` +9/-1 delta (O_BINARY flag inside
    `write_text_atomic`'s fsync path) — SUPERSEDED-BY-UPSTREAM as a class,
    solved differently: upstream c15389f4 added `write_bytes_atomic`
    (utils.py:276, fd opened with `getattr(os, "O_BINARY", 0)`) for
    byte-canonical consumers and pinned `write_text_atomic` to "platform
    newline semantics" in its docstring — a deliberate two-writer
    decomposition. Replaying the reference's O_BINARY into
    write_text_atomic would invert that upstream decision. No transplant;
    cross-OS class registry should record ONE decision for this class
    (upstream's).
16. Reference `tests/test_launcher_server_reaper.py` +8/-3 delta (normpath'd
    REPO/DATA/OURS literals + POSIX-only skipif on
    test_candidate_enumeration_uses_one_unbranded_full_width_ps_read) —
    SUPERSEDED-BY-UPSTREAM as the same cross-OS class: upstream 7de26338
    normpaths the same three literals (also the python binary path, which
    the reference did not) and, instead of skipping the enumeration test off
    POSIX, monkeypatches `reaper.os` with a getuid stub so it runs on every
    OS. Upstream form stands; nothing transplanted; the module itself is
    byte-identical across tip/reference/base.
17. Reference `tests/test_packaged_runtime_and_lifecycle.py` +7/-2 delta —
    DEFERRED WITH ITS OWNERS, not D18's to land: the `_enforce_harness`
    clock hunk patches `supervisor.events_budget/events_chat_delivery/
    events_task_done` (D33 events-split leaves absent from this tree) and
    `test_cancel_and_timeout_paths_share_one_salvage_helper` retargets to
    `supervisor/cancel_custody.py` (HOT-FALSIFIED per D09 lane entry 7;
    rides into F2). Tip bytes stand (upstream b3c9860e's -1 drift included).
18. Reference `tests/test_packaging_sync.py` +17/-7 delta
    (test_system_prompt_lists_bible_in_safety_critical_set strengthened to
    set-equality of BOTH prompts' inventories against
    `runtime_mode_policy.SAFETY_CRITICAL_PATHS`) — UNROWED in MIGRATION_v7;
    left at tip bytes per the wave-1 rule (unrowed test deltas are not
    resolved unilaterally); candidate row for the carried ledger at F5.
    Disjoint upstream drift a23e12b1 (push_to_remote test retargeted to
    `_git_network_bounded`) stands.
## From the D04 lane (base d830cdba, 2026-08-30)
1. Registry-split rows RE-PROVEN against tip bytes for the four landed tools/
   leaves (tool_context, tool_catalog, tool_resolution, registry_guards,
   registry_guard_process — 74 symbols): 61 spans byte-identical between the
   reference leaves and `git show HEAD:ouroboros/tools/registry.py`; 13 spans
   BYTE-FALSIFIED as copy sources by PURE UPSTREAM DRIFT (oracle==merge-base,
   tip moved): _prepare_public_builtin_args, _executor_backend_candidate_allowed,
   _authorized_managed_update_resolver (404B -> 1843B hardening), _disabled_tools,
   _detect_runtime_mode_elevation, _SUBAGENT_SHELL_SECRET_MARKERS,
   _detect_mutative_toggle_self_change, _detect_evolution_owner_control_self_change,
   _detect_context_mode_self_lowering, _DENIED_READ_OPTIONS,
   _is_pure_read_inspection, _detect_safety_mode_self_lowering,
   _detect_owner_skill_attest_self_call. All re-emitted from tip bytes,
   transplant proof green (ast=tokens=bytes on every symbol, exit 0).
2. Rows whose reference destination carries the TYPED-RESULT cutover semantics
   (PURE V7 DELTA; tip==merge-base): 144 (_normalize_dispatch_path_args reduced
   to a projection), 184 (_binding_error_text native codes), 185
   (_payload_dispatch_constraint typed second element), 226
   (_managed_update_code_tool_block thin wrapper), 138 (ToolEntry shallow-frozen
   — also upstream-drifted: tip added the alias_for field). This lane moved the
   TIP bodies verbatim; the typed deltas are deliberately NOT ported — they ride
   with the F2 typed-result organ, not with a byte-preserving relocation of a
   protected file.
3. HOT-DEFERRED: ouroboros/tools/registry_core.py (rows 156, 167, 170, 171,
   174, 175). Evidence: tip ToolRegistry is a 2252-line class (probe: tip span
   124364B vs reference 49860B, ast_equal=False); the reference slimmed it via
   17 method->function extractions (rows 189, 224, 225, 230, 235-242, 287,
   291-293) which change the receiver (self -> registry) and are NOT
   byte-preserving relocation — out of bounds for the protected
   tools/registry.py under this lane's mandate. ToolRegistry and the four
   process/mutation constants stay in the facade; the class also would put the
   new leaf straight into the >1500 band. Re-split from the upstream form in F2.
4. HOT-DEFERRED: ouroboros/tools/tool_result.py. 32 of the reference leaf's 33
   top-level symbols do not exist at tip (the ToolResult/ToolCodeSpec organ,
   D02-family approved deltas); the single registry-sourced verbatim row 139
   (_compose_execute_result) also drifted at tip (661B vs 671B). Creating a
   one-symbol leaf under the organ's name would falsely anchor the F2 re-split;
   _compose_execute_result stays in the facade.
5. HOT-DEFERRED: rows 187/188 (ToolRegistry._dispatch_mcp_tool /
   _dispatch_extension_tool -> extension_dispatch typed dispatchers).
   tip tools/extension_dispatch.py == merge-base (116 lines); the reference's
   +177 lines are the producer-boundary ToolResult typing plus method
   retirement. Upstream bytes stand; the methods stay on the class.
6. loop_tool_execution.py D04 rows (157, 159-164, 826-828) are ALL
   retire/rename/type rows of the classifier cutover — nothing is emittable as
   a byte-preserving span. Shared-monolith convention honored: this lane did
   not touch ouroboros/loop_tool_execution.py at all (D01 owns the rest).
7. tools/core.py shared-leaf note (row 353, core.py::active_repo_dir_for ->
   tool_resolution.py): already satisfied at tip by an import alias
   (core.py:20 imports it from the registry; the registry facade now re-exports
   it from tool_resolution — same object). core.py untouched by this lane.
8. tool_access split rows 495-535 RE-PROVEN against tip bytes: 39/41 spans
   byte-identical; 2 BYTE-FALSIFIED as copy sources by PURE UPSTREAM DRIFT:
   _skill_payload_base (upstream re-homed the body into
   skill_payload_binding.resolve_skill_payload_base — copying the reference
   leaf would have reverted that refactor) and ResolvedResourceBinding
   (upstream added the logical_base_path field). Both re-emitted from tip
   bytes, proof green. The D1 mirror-path defect (safe_relpath lstrip('/'),
   lying "caller rejects" docstrings) travels in the moved tip bytes UNFIXED,
   per the lane instruction — it remains an upstream issue-candidate.
9. Pins carried with disclosed adaptations (identity continuations to tip
   bytes): tests/test_tool_owner_facades.py (+ the alias_for row in the
   ToolEntry contract — upstream drift); tests/test_tool_access_extraction.py
   (4 adaptations, listed in its docstring: tool_module_inventory clause
   dropped until that leaf lands, backedge check narrowed to import-time
   imports because the D18/D33 call-time handle is deliberate, one-matrix
   clause asserts through the facade re-export, size bounds kept);
   tests/test_workspace_authority_binding.py gains the reference's
   tool_resolution identity test while its typed companion
   (_normalize_dispatch_path_args_result) is NOT carried — it pins deferred
   machinery. test_registry_core.py, test_tool_result*.py and the
   classification-differential suites are NOT carried for the same reason.
10. Test-split rows 784-825 (tests/test_tool_capabilities.py -> 4 siblings)
    RE-PROVEN against tip bytes: 34/42 moved spans byte-identical to the
    reference siblings, 8 re-emitted from tip (test_search_code_has_result_limit,
    test_local_readonly_subagent_execute_blocks_forbidden_tools,
    test_local_readonly_subagent_initial_schemas_are_allowlisted,
    test_schedule_subagent_in_initial_schemas,
    test_schedule_subagent_inherits_workspace_executor_ref, and the three
    test_schedule_subagent_required_*_for_readonly tests). Lossless: 61 == 61
    test functions, zero lost, zero added, no duplicate names introduced
    (tree-wide AST dup scan; the 10 pre-existing identical-body duplicates
    between test_review_cycles_dispatch.py and test_review_cycles_skill_dispatch.py
    plus the test_tool_registered same-name pair predate this lane — D06/D05
    territory, reported not touched). 21 unrowed/kept tip tests remain in the
    remainder; 3 header imports that lost their last reader were dropped there.
11. Protection-surface closure (code-side, protective-only): the reference
    extends ouroboros/runtime_mode_policy.py::SAFETY_CRITICAL_PATHS and
    supervisor/update_merge_policy.py::HOT_CODE_PATHS over the registry split
    leaves — without that, guard bodies moved out of the protected registry
    become writable in advanced mode and lose the hot-code label (this tree's
    own parity rule, tests/test_lc2_owner_facades.py, pins the inverse
    direction). This lane mirrored the closure for the five leaves that exist
    here (registry_core.py / tool_result.py rows return with their leaves) and
    pinned it (tests/test_tool_owner_facades.py::
    test_registry_split_leaves_keep_protected_label_parity). NOT mirrored —
    for the owner/F5: the reference's prose updates to prompts/SAFETY.md:10
    and prompts/SYSTEM.md "Immutable Safety Files" (operator-off-limits
    runtime prompts; enforcement is code-side, prose enumerates only the
    facade for now), and the reference's extra HOT_CODE_PATHS row for
    ouroboros/tools/extension_dispatch.py (nothing moved there on this tree —
    adding it is an oracle delta beyond relocation parity).
## From the D12 lane (base d830cdba, 2026-08-30)
13. Split rows 855-867 (settings_scales), 868-879 (model_slots), 880-886
   (review_model_routes) — RE-PROVEN against tip bytes: every span of the three
   reference leaves is ast=tokens=bytes=True against
   `git show HEAD:ouroboros/config.py` (drift-probe first, exit 0); the leaves
   landed from tip bytes and differ from the reference only in BETWEEN-SPAN
   comments upstream rewrote inside config.py (EFFORT_SCALE header now names
   exact-route request-wire recovery; the PROMPT_CACHE_TTL comment rewrapped) —
   carried from tip, since the span proof is blind to inter-span comment lines.
14. Shared-leaf rows 840-846/852-854 (config.py) + 3238-3241 (provider_models.py)
   -> settings_defaults.py — BYTE-FALSIFIED as a copy source on 4 of 12 spans,
   transform still valid: upstream rewrote SETTINGS_DEFAULTS (advisory slot is
   the routed id `anthropic/claude-sonnet-5`, `CLAUDE_CODE_MODEL` retired,
   MAX_SUBAGENT_DEPTH default 2->3, `OUROBOROS_SOFT/HARD_TIMEOUT_SEC` live
   again with a display-only note, plus new PRESENCE/SUBAGENTS/CLAUDEXOR/
   REVIEW_NATIVE_* keys), RETIRED_SETTING_KEYS (upstream itself retired only
   PLAN_TASK_SWARM_HEARTBEAT_STALE_SEC and kept SOFT/HARD live — the
   reference's D04 retirement of those two knobs is DIVERGENT-SUPERSEDED and
   must be re-derived in its own return, not replayed), ENDPOINT_AUTHORED_
   SETTINGS (+OUROBOROS_SUBAGENT_PRESET_RECEIPT) and OPENROUTER_REVIEW_DEFAULTS
   (routed advisory id + comment). Leaf emitted FULL from BOTH parents (the
   shared-leaf convention: drift-probe per parent separately; the final
   transplant --check runs against the two parents concatenated into one
   upstream source so every span is verified in a single exit-0 report).
   provider_models.py was touched ONLY by span removal + the settings_defaults
   re-export import; its call-time `from ouroboros.config import ...` imports
   are tip truth (D02-owned) and stand.
15. Split rows 887-912 (runtime_limits) — 3 spans byte-falsified by upstream
   drift (get_websearch_timeout_sec docstring; get_search_code_wall_sec now
   routes through _clamped_number_setting; get_max_subagent_depth reads the
   named cap), all re-emitted from tip bytes. STRUCTURAL: upstream reshaped
   `MAX_ACTIVE_SUBAGENTS_HARD_CAP = 500` into the tuple statement
   `MAX_ACTIVE_SUBAGENTS_HARD_CAP, MAX_SUBAGENT_DEPTH_HARD_CAP = 500, 10`;
   the UNROWED twin (consumed by ouroboros/tools/control_delegation.py via
   config) rides the rowed statement into runtime_limits and the facade
   re-exports both — the carried ledger must mint its row at F5. Tool note:
   the hardened --check flags this one statement as `assignment to <complex
   target>` under undeclared_top_level even though BOTH bound names are
   requested symbols (Tuple-target blind spot; every span proof in the same
   report is green, leaf_invariants=[]) — the one lane gate that exits 2 with
   a proven false-positive cause; the tool wants Tuple support at F5.
16. Rows 918-920 (launcher_onboarding, semantic delta D03/settings seam,
   launcher half) — RE-PROVEN applicable and LANDED: the module is
   byte-identical between tip and merge-base (zero upstream drift), so the
   reference bytes apply verbatim; the pin renamed per row 920. The SERVER
   half of the same seam (rows 1080-1081, server.py lifespan) is NOT landed —
   server.py keeps the tip guarded write and its old pin; it returns with the
   D11 lane. Two unrowed oracle test adaptations were mirrored because they
   pin exactly this delta and go red without it: test_onboarding_wizard.py::
   test_the_launcher_onboarding_module_authors_no_onboarding_settings
   (reference bytes) and tests/test_server_runtime.py (launcher clause ->
   `"save_settings(" not in launcher_host`; the server clause KEEPS the tip
   guard-string assertion, diverging from the reference's both-sides form
   until D11 lands).
17. Rows 913-917 (the rest of the D03 settings seam: config.py
   normalize_settings_raw/serialize_settings, gateway/owner_settings digest +
   locked update, packaged_cli writer) — HOT-DEFERRED: upstream rewrote
   load_settings_lock_held's read path through the NEW post-cutoff
   settings_integrity module (read_settings_json_verified /
   SettingsIntegrityError raise-through), which the reference does not have;
   replaying the reference seam verbatim would revert the integrity feature
   (the re-prove-trap class, entry 3). The whole seam machinery re-derives
   against tip bytes in its own return; its pin tests/test_settings_read_seam.py
   (a DOMAIN_MAP D12 pin) defers WITH the machinery — not transplanted by this
   lane.
18. Pin adaptations recorded: test_settings_env_on_disk.py re-pinned one
   literal to tip bytes (ENDPOINT_AUTHORED_SETTINGS gains
   OUROBOROS_SUBAGENT_PRESET_RECEIPT — same upstream train as entry 14);
   test_config_extraction.py gains MAX_SUBAGENT_DEPTH_HARD_CAP in the owner
   inventory, Tuple-target parsing in its _top_level_names helper, and a
   narrowed provider_models clause (the reference's "no ouroboros.config
   import anywhere" + top-level model_slots import clauses type against the
   reference's D02 rework of provider_models and return with the D02 lane;
   the surviving clauses pin no IMPORT-TIME config read and leaf-object
   identity of both moved literals).
19. settings_integrity.py — NEW upstream module (post-cutoff, absent from the
   reference and the merge base), already D12 in scripts/v7next_domains.toml;
   no ledger rows; upstream bytes stand. Non-split D12 modules re-proven:
   colab_bootstrap.py / onboarding_wizard.py / secret_masking.py /
   update_channels.py byte-identical across tip==ref==merge-base;
   settings_setup_contract.py / subscription_install_presets.py pure upstream
   drift (ref==merge-base, zero v7 delta) — upstream bytes stand.

## From the integration seam (coordinator, base 0859b681, 2026-08-30)
1. Superseding note to D04 entry "four landed leaves": the lane landed FIVE
   registry leaves (tool_context, tool_catalog, tool_resolution,
   registry_guards, registry_guard_process) — the list in that entry is the
   authority, its count word is a typo (wave-2 conformance review item 6).
2. Superseding note to D12 entry on the tuple-target gate: the verifier fix
   landed in the wave-2 seam commit (unfold at any depth; non-Name leaves are
   complex targets; probes in tests/test_v7next_transplant.py) — the "future
   work / exits 2" claim in that entry is superseded.
3. Seam repair: [split_pending] registry row carries domain IDs again
   (["D04"]) and [split_pending_leaves] carries the two hot-deferred leaves —
   the first seam commit wrote the leaf list into the wrong section.
## From the D05 lane (base 0859b681, 2026-08-30)
1. Shell split rows 416-464 RE-PROVEN against tip bytes and landed. shell_process
   (11 spans, rows 416-426) and shell_effects (12 spans, rows 453-464): every
   reference-leaf span ast=tokens=bytes=True against
   `git show HEAD:ouroboros/tools/shell.py` (drift-probe first, exit 0) — the
   reference leaves ARE tip bytes, adopted verbatim. shell_outputs: only 16 of
   the row set remain in the tip monolith; 14 byte-identical, 2 re-emitted from
   tip: `_register_process_outputs` (ref moved, tip==merge-base — the
   reference's typed-cutover 3-tuple/artifact_registered plumbing is a PURE V7
   DELTA, deliberately NOT ported, rides with the F2 typed-result organ) and
   `_resolve_declared_output` (ref==merge-base, tip moved — PURE UPSTREAM DRIFT:
   the lexical deliverables/casefold machinery; tip bytes are the leaf).
2. Rows 429 (`_allowed_output_roots`), 439 (`_UNDECLARED_OUTPUTS_MARKER`), 445-451
   (the six output/user-file regexes + `_OUTPUT_STAT_SLACK_SEC`) and 452
   (`_mentioned_user_file_outputs_without_declaration`) — SUPERSEDED-BY-UPSTREAM
   as shell_outputs rows: upstream c7315c57 ("Relax scoped browser, native-read,
   and Deliverables false blocks") extracted those ten owners into its own NEW
   leaf `ouroboros/tools/shell_audit.py` (D05-owned, no ledger rows), and the tip
   facade already aliases/imports them from there. The carried ledger renames the
   destination of those ten rows at F5; the facade identity contract
   (tests/test_shell_extraction.py) covers them at their upstream owner.
3. Core split rows 311-349 RE-PROVEN against tip bytes and landed
   (core_file_tools 30 spans incl. row 311's tip alias form
   `_SKILL_OWNER_STATE_FILENAMES = SKILL_OWNER_STATE_FILENAMES`; core_artifacts
   9 spans): 29/39 byte-identical between the reference leaves and
   `git show HEAD:ouroboros/tools/core.py`; 10 BYTE-FALSIFIED as copy sources,
   ALL of the same class — tip==merge-base, reference moved (the typed-result
   cutover producers `_repo_read/_repo_list/_data_read/_data_list/_read_file/
   _list_files/_access_or_block/_send_photo/_send_video/_send_file`, i.e. the
   rows whose own notes disclose `_publish_tool_result`, including row 332's
   A.20 marker change). Tip bodies moved verbatim; the typed deltas ride with
   the F2 typed-result organ (same class as D04 entry 2). Both emitted leaves
   are proof-green (ast=tokens=bytes on every span, leaf_invariants=[], exit 0).
4. FACADE CONVENTION DIVERGENCE (disclosed): the reference cut core.py over with
   NO facade (rows 311-349 carry "-" in the re-export column; consumers rebound
   by rows 360-371 and unrowed edits to vision/query_code/edit_ops/
   delegate_output/shell_guards). This tree keeps a re-export facade on
   tools/core.py instead (the §5.3-Δ2 item-12 partial-split idiom, matching the
   shell facade): the tip consumer surface grew far beyond the reference's (6
   production modules + 20+ test files import the moved names from tools.core
   at this tip), and a no-facade cutover is a pure-hygiene consumer rebind that
   can land as its own wave at F5 without re-proving spans. Identity is pinned
   (`core.X is core_file_tools.X / core_artifacts.X`,
   tests/test_core_extraction.py::test_core_facade_reexports_every_moved_identity);
   the reference's `isdisjoint(vars(core))` clause is replaced by that pin.
5. Rowed TEST bindings landed: rows 363-371 (test_send_file/photo/video ->
   core_artifacts) and row 361 (test_filesystem_root_observability::_read_file ->
   core_file_tools). Row 362 (tests/test_headless_cli.py::_repo_read): the D17
   split moved that consumer into tests/test_headless_workspace_shell.py (D17
   lane entry 9 reverse-mapped it to the upstream spelling); this lane completed
   the row at its successor location (core_file_tools binding). Row 360
   (browser.py::_readonly_subagent) NOT landed: the reference's browser delta
   bundles a D01 rebinding (`loop_messages._append_or_merge_user_content`)
   absent from this tree; the facade preserves the exact object meanwhile —
   rides with the consumer-rebind wave.
6. Cross-domain core rows already satisfied at tip (SUPERSEDED-BY-UPSTREAM
   class, no action): the five tool_access rows (active_tool_profile,
   build_resolved_resource_binding, decide_tool_access, normalize_root,
   normalize_runtime_data_path — tip core.py imports them from
   ouroboros.tool_access), read_text -> utils, row 353 active_repo_dir_for ->
   tool_resolution (import alias, per D04 entry 7), _filter_out_project_store ->
   project_facts (per D15 entry 1), and the two contracts/skill_payload_policy
   rows (tip imports them as the `_policy_*` aliases). Registry rows 213/214
   (python_interpreter) and 246/247 (artifacts) ride with the HOT-DEFERRED
   registry_core leaf (D04 entry 3): tip registry.py still carries those import
   bindings (:59, :83); the protected file was not touched by this lane.
7. Unrowed reference deltas NOT replayed (candidate rows for the carried
   ledger): (a) code_intelligence.py `collect_top_level_python_imports` (+92) —
   its only consumer is the reference-only tests/test_top_level_import_graph.py
   (domain-graph tooling; F5/quotient territory); (b) mcp_client.py ToolResult
   cutover + `tool_name_collisions` field — F2 typed organ; (c) services.py
   `_publish_tool_result` cutover coupled to the 3-tuple
   `_register_process_outputs` — F2; (d) health.py module-debt band rendering —
   types against reference-only ratchet metrics keys (`module_debt_1500_active`
   etc.) that no producer on this tree emits, and the owner's Q11=B decision
   picked the upstream size law — DIVERGENT-SUPERSEDED, re-derive only if the
   debt-band UI returns; (e) vision/query_code/edit_ops/delegate_output/
   shell_guards import rebinds — pending with the consumer-rebind wave (all
   keep working through the facade).
8. Oracle test adaptations mirrored in this tree's equivalents (§5.3-Δ2 item
   10): load_settings monkeypatches retargeted to shell_process (its only
   reader moved there) in tests/test_shell_run_shell.py and
   tests/test_iteration2_fixes.py; module-object patch handles retargeted to
   core_file_tools in tests/test_repo_read_limits.py (read_text),
   tests/test_runtime_reliability_v655.py (_list_dir) and
   tests/test_workspace_authority_binding.py (build_resolved_resource_binding).
   Path-keyed mirror: tests/test_process_custody.py `_POPEN_ALLOWLIST` row
   "ouroboros/tools/shell.py" -> "ouroboros/tools/shell_process.py" (the
   facade's only Popen site moved with `_tracked_subprocess_run`; suite green).
9. Zero-v7-delta re-proofs for the rest of the domain: media.py /
   python_interpreter.py / code_search_rg.py byte-identical tip==ref==merge-base;
   artifacts.py / recent_tasks.py / search.py / verify.py pure upstream drift
   (ref==merge-base) — upstream bytes stand; shell_audit.py NEW upstream module
   (no rows, see entry 2). tools/core.py band re-entry (2283 -> 1373) recorded
   via the official regenerator's --band-rationale.
## From the D02 lane (base 0859b681, 2026-08-30)
1. llm.py split rows 1666-1793 + 4001-4003 (131 rows, ten leaves) RE-PROVEN
   against tip bytes: 100 spans byte-identical between the reference leaves and
   `git show HEAD:ouroboros/llm.py`; 28 spans BYTE-FALSIFIED as copy sources by
   PURE UPSTREAM DRIFT (oracle==merge-base for every non-D09 one) and re-emitted
   from tip bytes. The drift is the post-cutoff provider train: request-wire
   custody (041e6e39, issue-229 phase 2b — request_wire_scoped decorators and
   wire send/receipt hooks inside the send drivers and lanes), OpenRouter
   attribution rework (9a20df6a — OPENROUTER_APP_HEADERS), anthropic native
   custody (native_content_for_replay/retain_native_assistant_content),
   timeout/custody hardening (802f1056, f702439f). Transplant-tool verify:
   every module-level span ast=tokens=bytes=True, undeclared_top_level=[],
   leaf_invariants=[], plus a member-level byte proof for all 10 mixins
   (117 members byte-identical to the tip LLMClient members).
2. Row 1674 (`_applied_payload_cache_ttl`) — the ledger's own documented
   one-identifier requalification (LLMClient -> _PayloadCachePolicyMixin) kept
   from the reference; the only non-tip-byte span in the split besides row 1784.
3. Row 1784 (`_chat_local`, semantic id D09, the approved one-attempt delta) —
   CARRIED, but BYTE-FALSIFIED as a verbatim copy source: upstream 802f1056
   added the exception-owned capture custody clause INSIDE the retry loop the
   delta deletes; replaying the reference span verbatim would have silently
   reverted that upstream clause (the re-prove-trap class, D15 entry 3). The
   delta was re-derived on tip bytes: the `for attempt in range(3)` loop and its
   sleep/last_exc arms are gone (one physical attempt per call, transient
   failures surface to call_llm_with_retry), the custody clause and the
   warning/error identities are preserved. Pins: the reference's
   test_local_transport_makes_exactly_one_physical_attempt carried into
   tests/test_context_overflow_hint.py; the two sibling local-lane tests
   re-pinned per the reference (attempt count 3 -> 1, monkeypatches renamed to
   the owner leaf llm_local); upstream's post-cutoff
   test_local_retry_does_not_inherit_unrelated_physical_capture re-pinned the
   same way (its durable fact — exception-owned capture only, never the
   ContextVar — is unchanged; its `calls == 3` pinned the deleted loop).
4. D09 typed-policy-refusal subfamily (rows 1706, 1749, 1751, 1759, 1760 and
   the reference-only llm_attempt symbols PROVIDER_POLICY_REFUSAL /
   ProviderPolicyRefusal / _is_provider_policy_refusal) — HOT-DEFERRED with
   evidence: zero occurrences of `provider_policy_refusal` anywhere at this tip
   (no raiser, no classifier — loop_llm_call has no such code), and all five
   consuming ladder bodies drifted upstream (802f1056/f702439f hardened them);
   the refusal never surfaces without its D01-side classification, so carrying
   only the ladder half would ship dead semantics onto reworked bytes. The five
   bodies moved as TIP bytes; the reference pins
   tests/test_llm_typed_policy_refusal.py and the two
   `typed_policy_refusal` golden cases (fallback_ladder.json 17 -> 15) are NOT
   carried — they return with the delta's own re-derivation.
5. UNROWED tip symbols `_RESPONSE_METADATA_LABEL_MAX_CHARS` and
   `_bounded_response_metadata_label` (post-cutoff, llm.py top level) moved to
   ouroboros/llm_openai_compatible.py with their ONLY reader
   (`_normalize_remote_response`, row 1788); the facade re-exports both, so the
   tip import surface is unchanged. Candidate rows for the carried ledger at F5.
6. provider_models rows 840-886/3238-3241 note-contract COMPLETED: the rows'
   own notes say "provider_models now imports this leaf instead of lazily
   importing config"; D12 landed the leaves and left the consumption to D02.
   The two remaining call-time `from ouroboros.config import ...` reads
   (parse_fallback_chain at resolve_credentialed_model, SETTINGS_DEFAULTS at
   declared_model_settings) are now top-level leaf imports
   (model_slots/settings_defaults; cycle-free, verified at import). The
   reference pin test_provider_models_reads_the_shared_leaves_instead_of_
   importing_config is restored under its ledger name, superseding the D12
   lane's disclosed placeholder test_provider_models_reads_the_shared_defaults_
   leaf (its identity clauses are kept as a superset). Upstream's own
   provider_models evolution (ACTIVE/LEGACY_MODEL_SETTING_KEYS,
   *_in_settings twins, CLAUDE_CODE_MODEL retirement) is tip truth and stands.
7. ouroboros/llm_probe.py reference delta (+8/-6, tip==merge-base) ADOPTED
   verbatim: the lazy executor import redirects from the llm.py facade to the
   owner leaf llm_attempt (an llm_* leaf never imports its parent). Unrowed in
   MIGRATION; required by the leaf rule the carried pin
   tests/test_llm_extraction.py::test_llm_leaves_never_import_their_parent
   enforces. Candidate row at F5.
8. Provider-route goldens (tests/fixtures/llm_golden, 9 files) RE-BASELINED
   from tip behaviour via the suite's own `--write` entry: every diff class maps
   to a named upstream train — attribution headers (X-Title ->
   X-OpenRouter-Title + new referer, 9a20df6a), the `request_wire` disclosure
   block in usage (041e6e39), bounded `response_finish_reason` /
   `response_provider` labels, effort/dialect-ladder evolution, anthropic
   native-content retention. One suite adaptation: the per-process random
   `usage.request_wire.attempt_id` is projected to a presence flag (exactly the
   suite's existing ledger_attempt_ids treatment) — without it the recording is
   nondeterministic across processes.
9. Dead-patch class closed across tests: after the split,
   `execute_physical_attempt(_async)` is read in llm_attempt,
   `_execute_candidate`/`last_physical_attempt_capture` on the chat path in
   llm_fallback, and the local lane's executor in llm_local. Reference
   adaptations applied (test_capability_probe_accounting_v664,
   test_prompt_cache_v664, test_retry_bypass_response_cache verbatim —
   tip==base; test_effort_floor_v6732, test_usage_scope_transport_v664,
   test_provider_key_test re-derived on tip bytes); the same rule applied to
   two POST-CUTOFF upstream tests the reference never saw
   (test_openai_chat_dispatch, test_issue229_synthesis — llm ->
   llm_fallback, disclosed in-file); path-keyed mirror
   test_review_prompt_caching::test_global_ttl_docstrings_name_every_consumer
   re-pinned to `ouroboros/llm_attempt.py` (matches the reference's own bytes
   for that clause). Patches of names the facade still OWNS or that are read
   lazily through it (test_pricing fetch_openrouter_pricing, test_web_search
   server tools, all LLMClient-method patches) verified live and untouched.
10. Reference adaptations NOT carried (other domains' v7 spellings,
   reverse-mapped to tip per §5.3-Δ item 2): tests/test_multimodal_chat.py and
   tests/test_provider_failure_reporting.py retarget imports to
   loop_messages/loop_round_limits (D01 leaves absent here — tip bytes stand;
   tip already re-homed _provider_recovery_hint into loop_transport itself);
   the same import line in tests/test_context_overflow_hint.py keeps the tip
   spelling.

## From the D14 lane (base 92238298, 2026-08-30)
1. extension_loader.py split rows 2467-2519 (53 rows, six leaves) RE-PROVEN
   against tip bytes: 49 spans byte-identical between the reference leaves and
   `git show HEAD:ouroboros/extension_loader.py`; 4 spans BYTE-FALSIFIED as
   copy sources by PURE UPSTREAM DRIFT (oracle==merge-base 8028f1df for every
   one) and re-emitted from tip bytes: `_validate_child_ui_descriptor` and
   `PluginAPIImpl` (widget-geometry promotion `_widget_geometry_from_render`),
   `runtime_state_for_skill_name` / `runtime_state_for_loaded_skill` (durable
   companion-health overlay `_apply_durable_extension_health`). Transplant-tool
   verify per leaf: every span ast=tokens=bytes=True, undeclared_top_level=[],
   leaf_invariants=[], exit 0 (80 spans across the ten leaves of this lane).
2. UNROWED tip riders (candidate rows for the carried ledger):
   `_widget_geometry_from_render` -> ouroboros/extension_surface_names.py
   (readers live in two leaves — child_catalog and plugin_api — and it is the
   theme sibling of rowed `_widget_span_from_render`, which those same leaves
   already import); `_apply_durable_extension_health` ->
   ouroboros/extension_liveness.py (its only readers are the two moved
   runtime_state_* spans). The facade re-exports both; the carried identity
   suite pins both owners.
3. Row 2519-family `_ws_broadcaster`: moved to extension_plugin_api.py and
   deliberately NOT aliased on the facade (rebindable module global — a
   facade copy would freeze the value); RE-CONFIRMED as the reference
   contract, pinned by tests/test_extension_loader_extraction.py::
   test_the_broadcaster_slot_has_exactly_one_binding. server.py reaches it
   only through re-exported `set_ws_broadcaster`.
4. skill_review.py split rows (31 rows, four leaves): 25 executed from tip
   bytes. SIX rows SUPERSEDED by upstream's own re-decomposition (386e9417
   "Max Review Cycles" moved the accepted-rebuttal ledger and the wave-budget
   refusal whole into ouroboros/skill_review_cycles.py before this lane):
   `_accepted_rebuttals_path`, `_load_accepted_rebuttals`,
   `_persist_rebuttal_flips`, `_fail_items_from_history_entry`,
   `_record_accepted_rebuttal` (rebuttals-leaf rows) and
   `_review_wave_budget_block` (prompt-leaf row). Upstream ownership stands;
   the facade keeps the historical underscore aliases via tip's own cycles
   import; the carried identity suite pins that alias identity. The rebuttals
   leaf was emitted with its four remaining rows; the prompt leaf imports
   `load_accepted_rebuttals` from skill_review_cycles (tip truth), not from
   the rebuttals leaf as in the reference.
5. skill_review drifted spans re-emitted from tip bytes (5): `_read_skill_text`
   + `_build_skill_file_packs` (payload-snapshot digest gate,
   expected_content_hash), `_build_review_prompt` +
   `_run_skill_advisory_pre_review` (provider-neutral advisory critic rework
   f8d87c69 — "Optional Advisory Pre-Review", run_advisory_critic, hasattr
   no-op trap removed), `render_skill_review_block` (slot_id actor keys,
   distinct-item count, sanitize_tool_result_for_log).
6. Test rows tests/test_extension_loader.py (45, five siblings + shared):
   tip file has ZERO upstream drift since merge-base; 43 moved bodies
   byte-identical, 1 reference adaptation KEPT (dual supervisor patch in
   test_server_pickup_spawns_stops_and_redrives_missing_companion — PluginAPI
   owner reads the supervisor from its own leaf), 1 reference spelling
   REVERSE-MAPPED to tip (worker_main lives in supervisor/workers.py at this
   tip; the reference's supervisor/worker_process.py is the D08 split still
   pending here). Lossless: 52 test names before == 52 after, zero dup names.
7. Test rows tests/test_skill_review.py (65, five siblings + shared): 58
   moved bodies byte-identical; 3 re-emitted from tip bytes (pure test drift:
   advisory_model_credentials_missing label, provider-neutral advisory
   heading, review-delivery capture in
   test_review_skill_prompt_loads_core_governance_artifacts); 3 reference
   adaptations KEPT (patch retargets to leaf owners in
   test_review_skill_quorum_failure_on_one_responder and the two pack-budget
   tests). Row `test_skill_advisory_private_guards_precede_availability`
   SOURCE-FALSIFIED: upstream f8d87c69 deleted the test and replaced it with
   `test_skill_advisory_pytest_guard_precedes_availability` +
   `test_skill_advisory_missing_internal_symbol_is_loud_not_silent`; per the
   wave-2 rule the successors stay in the remainder with tip bytes (theme
   re-home is F5) and the reference copy of the deleted test was NOT carried.
   Lossless: 74 test names before == 74 after, zero dup names.
8. Identity suites carried: tests/test_extension_loader_extraction.py gains
   the two rider rows of entry 2; tests/test_skill_review_extraction.py
   adapted to tip — the reference's tool_module_inventory clauses dropped
   (v7-only mechanism, module absent at this tip; F5 restores it with its
   owner), a cycles-alias identity test added for the six superseded names,
   the facade size bound relaxed 800 -> 900 (tip retains the cycles gate,
   paid-fact stamping and _persist_reviewed_outcome the oracle-era monolith
   did not have), and the three tip-retained lifecycle members added to the
   patchable-seams pin.
9. Dead-patch class closed: the remainder's
   `patch("ouroboros.skill_review._run_skill_advisory_pre_review", ...)`
   retargeted to the prompt owner (mirrors the reference remainder :314);
   tests/test_extension_companion.py dual-patches get_global_supervisor on
   extension_plugin_api + extension_loader (2 tests, mirrors the reference
   adaptation; the single-module patch was proven dead by a red run). Every
   other facade-level patch site of moved names was verified LIVE: all
   production consumers of is_extension_live / runtime_state_for_* /
   `_lock`+`_tools` (skill_loader:1414) do call-time facade imports.
10. NOT carried, no ledger rows: the 8 post-cutoff D14 modules
   (betterleaks_runtime, skill_payload_binding, skill_publish_github/result/
   scanner/snapshot — secret-safe publishing train 8cc2ac69;
   skill_review_cycles — 386e9417; skill_review_usage — f18da8c3) stand on
   upstream bytes untouched. The reference's UNROWED `failure_kind` delta on
   ouroboros/extension_process_runner.py (typed timeout classification,
   consumed by the reference's tools/extension_dispatch.py:187) is NOT
   replayed — typed-dispatch family, Ф3 territory; tip bytes stand. The
   supervised-future leak (tip extension_plugin_api.py span of PluginAPIImpl)
   is preserved as-is per the plan (Ф3-acceptance carries the direct
   regression test). Pre-existing at base, untouched, for the record: 10
   ast-identical duplicate test bodies between
   tests/test_review_cycles_dispatch.py and
   tests/test_review_cycles_skill_dispatch.py.
## From the D08 lane (base 92238298, 2026-08-30)

1. Scope executed (the QUIET part): 16 leaves landed from tip bytes with the
   transplant tool (ast=tokens=byte-roundtrip=True on every span, exit 0,
   leaf_invariants=[], unread_declared=[]): control_events (rows 2520-2528),
   control_routing (2529-2536, 3954), control_runtime (2557-2568) — the D08
   half of the SHARED D07/D08 tools/control.py; queue_schedules (2029-2040 +
   alias rows 3950-3952); worker_promotion (2045-2054), worker_chat_lane
   (2055-2060), worker_pool_lifecycle (2065-2076), worker_process (1024-1029);
   events_chat_delivery (921, 923-929), events_budget (980-984),
   events_coop_checkpoint (964-969), events_project_routing (955-963),
   events_schedule_task (945-949, 951, 953-954), events_subagent_admission
   (930-944), events_worker_reports (985-991), events_runtime_controls
   (993-997). Facades = tip parent − moved spans + grouped re-export block
   (noqa discipline); facade audit green: every kept def/assign span
   byte-identical to `git show HEAD:<monolith>`, every moved name re-exported.
2. Drift-probe results (reference leaf --check against tip bytes, first step
   per leaf): whole-leaf byte-true — control_events 9/9, queue_schedules
   12/12, events_coop_checkpoint 6/6, events_subagent_admission 15/15;
   byte-falsified by pure upstream drift and re-emitted from tip bytes —
   control_routing 5/9 spans, control_runtime 7/12, worker_promotion 3/10,
   worker_chat_lane 2/6, worker_pool_lifecycle 2/12, worker_process 2/6,
   events_chat_delivery 4/8, events_budget 3/5, events_project_routing 6/9,
   events_schedule_task 2/9, events_worker_reports 4/7,
   events_runtime_controls 1/5. "Verbatim" in the ledger was re-proven by
   bytes in every case; no oracle semantics were replayed over tip drift.
3. SHARED-file convention (tools/control.py, D07/D08): this lane moved ONLY
   the D08 rows (control_events/routing/runtime per DOMAIN_MAP); the D07 rows
   (control_scheduling 2543-2556, control_subagent_spec 2537-2542,
   control_task_results 2569-2579) remain in the facade untouched for the D07
   lane. Unrowed post-cutoff predecessor-authority family
   (_MISSING_PREDECESSOR_SELECTOR, _predecessor_selector_error,
   _attach_predecessor_authority_from_metadata) rides with its only readers
   (_promote_chat_to_task/_route_to_project) into control_routing — a
   def-time default-argument read of the sentinel makes a facade-retained
   copy structurally impossible (F5 theme for the ledger's unrowed census).
4. HOT-DEFERRED, cancel/custody class (D09; upstream 65b5d19f re-decomposed
   this ownership — replaying the reference rows would be a second answer):
   - events_task_done rows 972-979: _resolve_lifecycle_fault reads
     cancel_intents, _maybe_notify_provider_death reads task_lifecycle,
     _task_done_durable_fault operates terminalization custody; the family is
     one dispatch cluster, deferred whole.
   - events_runtime_controls row 992 (_handle_cancel_task): the cancel ingress
     handler itself.
   - row 970 (_close_campaign_after_owner_stop -> queue_transitions.py) and
     row 971 (events_evolution_done): owner-stop family; 65b5d19f made
     queue_transitions.py its cancel-transition dumping ground, and the
     evolution-done handler calls the deferred campaign-closure symbol as a
     bare local name.
   - queue_snapshot rows 2017-2020: restore_pending_from_snapshot restores
     terminalization-retry rows and consults cancel_intents.has_active_intent
     (65b5d19f machinery); persist snapshots the same fences. Deferred whole
     (parse_iso_to_ts/_kept_service_pids ride only with their family).
   - queue_timeouts rows 2021-2028: _enforce_task_timeouts_locked drives
     cancel_intents/task_reaper/owner_stop.
   - queue_evolution rows 2041-2044: upstream itself moved
     _deliver_pending_owner_report/enqueue_evolution_task_if_needed into its
     own supervisor/evolution_lifecycle.py (65b5d19f); creating the reference
     leaf beside it would fork evolution-family ownership.
   - worker_assignment rows 2077-2079 (assign_tasks reshaped by 65b5d19f's
     600-line workers.py rework; _cancel_unauthorized_evolution) and
     worker_health rows 2061-2064 (_ensure_workers_healthy_locked writes
     STATUS_CANCELLED terminal outcomes and terminalizes admission-blocked
     retries). Both families stay on the facade.
5. Deferred SEMANTIC-DELTA rows (unsanctioned for this lane; tip bytes stand):
   1014-1015 (dispatch_event/EVENT_HANDLERS, delta D06 events taxonomy — the
   event_taxonomy.py leaf and tests/test_event_taxonomy.py are NOT created);
   1021/1022/2082 (queue.init/workers.init/refresh_timeouts_from_settings,
   delta D04 retired settings knobs — Q10/F3 territory); retired rows
   1017-1019, 1030, 2080-2081 (SOFT/HARD_TIMEOUT_SEC, TOTAL_BUDGET_LIMIT,
   QUEUE_SNAPSHOT_PATH — deletions are semantics, not relocation).
6. Row 2016 (_handle_schedule_task -> events_schedule_task.py) DEFERRED with
   a mechanism finding: the function carries the >300-line FUNCTION_DEBT entry
   keyed by (path, qualname), and THIS tree's transition validator
   (ouroboros/review.py::validate_manifest_transition) has no same-qualname
   relocation rule — that rule is reference delta D11, ratchet machinery out
   of this lane's bounds. The handler stays in the facade with its debt key;
   the eight quiet schedule-family rows moved. Every seam name it reads
   (_find_duplicate_task etc.) binds through the facade re-export, so existing
   facade-targeted test patches keep intercepting (verified green).
7. Reverse-mapped preamble spots (oracle spelling -> tip truth): queue_schedules
   `from supervisor.task_lifecycle import record_scheduled_admission` ->
   `from supervisor.task_admission import ...` (65b5d19f moved it); the two
   control leaves' `from ouroboros.tools.tool_result import ToolResult,
   _publish_tool_result` deleted — the module does not exist at tip (D04 lane
   hot-deferred that organ) and no tip span reads the names; alias mirrors
   from tip parents: _bound_project_chat_id (supervisor/log_addressing.py,
   upstream's own extraction), _build_scheduled_task_payload
   (supervisor/task_dispatch.py), _reject_if_no_chat_target
   (supervisor/task_admission.py), _once_due/_prune_consumed_once/
   _record_last_error (supervisor/schedule_time.py, rows 3950-3952 satisfied
   as leaf preamble imports exactly like the tip parent).
8. Handle idiom: queue_schedules/_queue, worker_promotion|chat_lane|
   pool_lifecycle/_pool declared sets re-derived on tip bytes (they grew past
   the reference table by the post-cutoff facade helpers:
   _announce_created_project, _apply_presence_promotion_authority,
   _promoted_scheduled_outcome, _reject_promoted_after_attachment_stage,
   _relocate_promoted_attachments, _stage_promoted_initial_attachments,
   _reconcile_confirmed_dead_review_owner); events_project_routing gained the
   D33-family handle `_events` for the single unrowed facade helper
   _routing_attachments. All sets pinned in
   tests/test_module_handle_extraction.py::LEAVES.
9. Path-keyed mirrors (Δ2 п.10): HOT_CODE_PATHS (supervisor/update_merge_policy.py)
   += the 12 carried hot leaves (D04-block precedent); FUNCTION_DEBT key NOT
   relocated (see 6); conftest _SERIAL_TEST_FILES needed no new rows (the new
   suites are structural). Dead-patch class re-pointed to owner leaves,
   mirroring the reference adaptations: test_coop_checkpoint_quiescence
   (events_coop_checkpoint, events_subagent_admission), test_evolution_redesign
   (queue_schedules._last_skill_schedule_sync), test_schedule_followup
   (queue_schedules._write_scheduled_tasks), test_worker_crash_retry
   (supervisor.worker_process trio), test_promote_chat_flow
   (control_events._wait_for_promotion_admission,
   control_routing._promotion_pool_disabled_from_snapshot),
   test_evolution_restart_claims (`control_runtime as control`, the reference's
   exact alias form), test_task_status_flow (control_runtime run_cmd/
   atomic_write_json), test_extension_loader (worker_main scan reads
   supervisor/worker_process.py), test_process_resource_leaks (reference
   bodies verbatim). All touched test files LOSSLESS (name multisets equal).
10. Pre-existing observation, NOT this lane's defect: tests/
   test_review_cycles_dispatch.py and tests/test_review_cycles_skill_dispatch.py
   share 10 ast-identical test bodies at the base SHA (D15-class dup, D06
   domain) — left for the D06 lane.
11. Unrowed tip top-level symbols stayed in their facades (F5 census):
   events.py _handle_main_llm_call_state/_parent_delegation_budget/
   _routing_attachments; queue.py 26 names (fences/admission/cancel seam);
   workers.py 88 names (65b5d19f terminalization-retry/custody machinery);
   control.py HIDDEN_LEGACY_SCHEDULE_PARAMS, _context_task_depth,
   _materialize_child_attachment_manifest, maybe_emit_delegated_run_fanout,
   get_tools + the predecessor family that rode into control_routing.
## From the integration seam (coordinator, D13 dispositions, 2026-08-30)
1. safety.py row 1016 (retire module-level supervisor import + _record_safety_usage,
   pin test_safety_module_has_no_import_time_dependency_on_the_supervisor) —
   LIVE, NOT landed on tip (import at :25, call at :1010). HOT-DEFERRED:
   protected file; rides the protected-surface wave (F2/F3) with owner-visible
   handling.
2. UNROWED live delta `_safety_drive_root` (fixes cwd-relative "../data" in
   safety.py, tip site :899; oracle had no ledger row, prose-only in
   DOMAIN_MAP). MUST gain a carried-ledger row before any replay; tip drift
   collapsed two mb sites into one — replay needs re-derivation. Candidate
   for the F5 carried-ledger mint. RISK: without this note the only useful
   unrowed safety delta would be silently lost.
3. shell_guards.py lazy-import rebind (tools.core → core_file_tools) —
   confirmed pending with the D05 consumer-rebind wave (D05 ledger §4(e));
   chain alive through the facade on tip.
4. runtime_mode_policy oracle delta remainder: registry_core.py +
   tool_result.py protection closure returns WITH those two hot-deferred
   leaves (D04 ledger §11); GIT_OPS_FAMILY_PATHS / RELEASE_INVARIANT_PATHS
   re-cut returns with the G1 git_ops split (D10 wave) — recording now would
   protect nonexistent files.
5. D13 census note: tip toml gives D13 eight owners vs oracle DOMAIN_MAP six —
   write_shape.py and deliverables_shell.py are new upstream surfaces
   post-freeze; not an oracle gap.
## From the D11 lane (base a56bb76a, 2026-08-30)
1. server.py split rows 1034-1078 + 3948-3949 (47 symbol rows): 43 landed into
   the six reference leaves (process 5, routing_context 13, owner_routing 5,
   liveness 4, maintenance 11, restart 5). Drift-probe FIRST per leaf: the
   reference leaves are byte-true against tip except 10 spans byte-falsified
   by upstream drift — _task_result_ground_truth (authority_source block),
   _stage_mailbox_attachments / _route_project_chat_to_running_task /
   _record_routing_receipt / _route_owner_message (attachment-report train),
   _start_supervisor_liveness_watchdog (OB-03 monotonic clock + pid-keyed
   toast), _periodic_supervisor_maintenance / _reconcile_delegated_runs
   (child-ref promotion replay + terminal-reconciliation refresh),
   _managed_update_pending_kwargs / _perform_supervisor_restart
   (planned-handoff train). All 43 landed spans emitted from tip bytes by the
   hardened transplant tool; --check green on every span (ast=tokens=bytes),
   leaf_invariants=[], no oracle semantics replayed over drift.
2. HOT-DEFERRED rows 1070/1072/1073/1074 (_pending_restart,
   _handle_restart_in_supervisor, _check_pending_restart_drain,
   _perform_supervisor_restart): the upstream delegation train re-decomposed
   restart ownership — _perform_supervisor_restart now WRITES the new module
   global _planned_delegate_restart_transaction_id that server.main() reads at
   the re-exec point; a byte-preserving relocation would fork that state (a
   leaf `global` write is invisible to the facade's from-import binding).
   D09-class "second answer about ownership" -> the four rows stay in the
   facade, the drain record stays beside its only two readers; the deferred
   inventory is pinned as the F2 work order in
   tests/test_server_extraction.py::_SERVER_OWNED.
3. Rows 1080-1081 (server.py::lifespan, semantic delta D03 settings-seam
   server half) HOT-DEFERRED: the reader-side halves of the same seam (rows
   913-917) are hot-deferred by the D12/D17 lanes (upstream rewrote the read
   path through post-cutoff settings_integrity); landing the boot half alone
   would leave provider normalization neither persisted nor re-derived.
   server.py keeps the tip guarded write and its old pin — exactly the state
   the D17 lane's note 16 anticipated.
4. Same-qualname ratchet delta (row 1033, semantic delta id D11) — LANDED.
   ouroboros/review.py verified NOT in the AGENTS.md protected list. The
   relocated_functions block replayed byte-identical from the oracle into the
   tip-shaped validate_manifest_transition (tip keeps its adjacent= interval
   form; the oracle-only MODULE_DEBT_1500 layer was NOT replayed — Q11=B keeps
   the upstream size law). The pin renamed per the row, oracle bytes
   (test_transition_rejects_function_swap_even_at_same_cardinality ->
   test_transition_allows_a_same_qualname_relocation_but_not_a_swap). This
   unblocks the D08 lane's row 2016 deferral (FUNCTION_DEBT relocation of
   _handle_schedule_task).
5. Rows 1192/1259 (theme split into tests/test_delegated_reconciliation.py):
   landed as the D11 SLICE only — the two tests that bind the
   server_maintenance owner. The in-place owner-retarget grew the shrink-only
   byte-debt giant test_delegated_subagent_transport.py by +40 bytes and the
   ratchet refused it; the re-home is the designed pressure valve (the giant
   shrinks 320340 -> 318310, the pin gains its family). The rest of the
   reference sibling (orphan-sweep predicate, absent-run closure, release
   points, _delegated_transport_shared helpers) arrives with the delegation
   organ's test split (F2). Row 1656 (TestStartupGCFailClosed): only the
   DATA_DIR owner-retarget mirrored; that file split also stays with F2.
6. Facade form: top from-import block (reference facade style), not an EOF
   re-export block — forced by module-level reads of moved state (PORT_FILE =
   DATA_DIR / ..., the logging bootstrap) before any def runs; base64 keeps a
   noqa: F401 exactly as the reference facade does (its only user moved).
   Facade audit green: every kept top-level span byte-identical to tip, no
   facade-new symbols, every moved name re-exported by identity. server.py
   3191 -> 1640 lines; it remains a GIANT_PATHS entry (>1600 upstream law) and
   only shrank, so the regenerated manifest changes one number (the transport
   giant's byte debt).
7. Leaf conventions: emitted leaves carry `from __future__ import annotations`
   (transplant-tool requirement; prior-lane convention) and tool span spacing.
   Zero declared names and NO module handles — the reference design homes the
   shared rebindable state in server_process (Events mutated in place, one
   DATA_DIR, one logger), so all six are projection-only leaves.
   Reverse-mapped preamble spots: server_liveness gains `import os` (drift:
   os.getpid() in the toast key); server_restart's preamble/docstring describe
   the landed five rows and name the deferral honestly.
8. Test adaptations mirrored path-keyed to THIS tree (Δ2 p.10): transport
   giant tests -> sm owner (see 5); test_delegated_run_isolation._server_gc ->
   server_maintenance.DATA_DIR (reference form); test_phase3c_observability_gc
   (two post-cutoff tests, no oracle counterpart) -> maintenance owner for
   DATA_DIR/_LAST_CANCEL_INTENT_SWEEP/time; test_project_routing_v664 ->
   server_routing_context patch, compressed to one line so the file stays at
   1000 lines (below the 1001 band); test_client_surface -> owner_routing text
   joined into the client_surface pin (reference form);
   test_ws3_wedge_resilience (post-cutoff OB-03 tests) -> fake clock retargets
   to server_liveness; test_panic_stop_port_sweep floor 5 -> 11 (the return
   the D09 lane's note 12(a) anticipated). Deliberately NOT retargeted:
   patches whose exercised readers stayed in the facade with the deferral
   (test_server_shutdown, test_evolution_restart_claims,
   test_restart_reconnect, test_promote_chat_flow, test_client_surface
   _process_bridge_updates block). All touched test files lossless (the one
   test rename is ledger row 1033; the two re-homed names moved whole).
9. Pre-existing base red, NOT this lane's defect:
   tests/test_smoke.py::test_size_ratchet_transition_against_explicit_base
   fails at pristine a56bb76a (probed in a throwaway worktree: 1 failed + 4
   passed) — parent 7d2dca49's manifest records
   tests/test_devtools_benchmarks.py at 328116 bytes while its own tree holds
   328195 (the +79-byte cherry-pick residue the seam commit message itself
   describes). The (a56bb76a -> this commit) pair is consistent: 327935 ==
   tree at the parent.
10. Module census, 34 D11 owners (tip vs merge-base 8028f1df vs oracle
   9f691656): 13 byte-identical in all three (client_surface,
   gateway/__init__, gateway/files, gateway/logs, gateway/mcp,
   gateway/onboarding_host, gateway/schedules, gateway/task_events,
   gateway/task_hurry, gateway/ui_preferences, server_auth, server_entrypoint,
   server_web); 17 pure upstream drift — tip bytes stand (gateway/_helpers,
   claudexor_accounts, contracts, control, extensions, history, host_service,
   marketplace, models, presence_settings, projects, router, skill_publish,
   state, tasks, ws, server_runtime); 3 carry ONLY D03 settings-seam /
   retired-knob (D04) oracle deltas -> HOT-DEFERRED with that seam
   (gateway/owner_settings, gateway/onboarding, gateway/settings; D12/D17
   precedent); server.py split per 1. Gateway ABI/alias retirements untouched
   (F3 territory); web/ untouched.
## From the D10 lane (base a56bb76a, 2026-08-30)
1. G1 split rows 3430-3457 (supervisor/git_ops.py -> 4 leaves, delta D35
   module-handle) executed for 26 of 28 rows from tip bytes with the transplant
   tool (ast=tokens=byte-roundtrip=True on every span, leaf_invariants=[],
   unread_declared=[], exit 0 per leaf). Drift-probe (reference leaf --check
   against `git show HEAD:supervisor/git_ops.py`, first step per leaf):
   git_ops_rescue 8/8 spans byte-true; git_ops_remotes 3/4 (push_to_remote
   BYTE-FALSIFIED by PURE UPSTREAM DRIFT — a23e12b1 routed the push through the
   bounded network runner; tip bytes emitted); git_ops_updates and
   git_ops_reset byte-true on every span except the two f-string rows below.
2. Rows 3439 (prepare_managed_update) and 3449 (safe_restart) DEFERRED — both
   spans stay facade DEFS: each reads the rebindable parent global BRANCH_DEV
   (safe_restart also BRANCH_STABLE) inside f-strings, and the hardened
   transplant gate fails closed on f-string reads of declared names ("the
   token proof cannot cover f-string internals"). The reference leaves carry a
   manual `_go().BRANCH_DEV` rewrite inside the f-strings, which this wave's
   ast=tokens=bytes gate cannot re-prove (tokens_equal=False on exactly those
   spans in the drift-probe). Their reads were dropped from the declared sets
   (tool-verified unread otherwise); tests/test_git_ops_owner_facades.py pins
   the two names as facade defs. Relocation returns if/when the tool grows
   f-string token support (D12 lane already noted the same gate wants Tuple
   support — same F5 tool-work theme).
3. git_ops rows 1031-1032 (DRIVE_ROOT/REPO_DIR config-aware pre-init defaults,
   semantic id D13) — HOT-DEFERRED: live semantic delta to a protected file
   (tip still binds `pathlib.Path.home()/"Ouroboros"` at :26-27, upstream did
   NOT absorb the hermetic-isolation fix). Not byte-preserving relocation, so
   out of this lane's mandate; rides the protected-surface wave with
   owner-visible handling (same class as the coordinator's safety.py row 1016
   disposition). Its pin tests/test_git_ops_default_roots.py is NOT carried.
4. update_merge split rows 3426-3429 (-> supervisor/update_merge_plan.py) —
   HOT-DEFERRED WHOLE with the update engine (F2 organ): rows 3427-3429 carry
   semantic id D34 (carrier engine insertion points, spans SSOT
   release_sync.py) and the single verbatim row 3426 (`_git_run`) is
   SOURCE-FALSIFIED — upstream's update-flow redesign DELETED _git_run from
   tip update_merge.py and rewrote the three D34 bodies (+517-line drift vs
   merge-base; post-cutoff supervisor/update_candidate.py exists at tip,
   absent from oracle AND merge-base). A one-symbol update_merge_plan.py would
   falsely anchor the F2 re-split (the D04 tool_result.py class). The oracle's
   +84 release_sync.py D34 delta (span-descriptor SSOT) defers with it; pins
   tests/test_update_merge_owner_facade.py / test_update_carriers.py /
   test_carrier_rebase_helper.py NOT carried.
5. tools/git.py split rows 374-415 executed for 41 of 42 rows from tip bytes
   (five leaves, proof green per leaf, exit 0). Drift-probe against tip bytes:
   git_plumbing 10/10 byte-true; git_evolution 3/5; git_repo_edit 2/4;
   git_vcs_ops 7/10; git_review_cycle 7/12. Falsified spans, two classes:
   (a) PURE UPSTREAM DRIFT (oracle==merge-base, tip moved):
   _finalize_blocked_review, _review_cycle_infra_failure,
   _check_evolution_commit_stage, _record_evolution_commit_receipt,
   _repo_write, _str_replace_editor, _ff_pull (+ the drifted halves of
   _run_reviewed_stage_cycle/_run_non_committing_review_cycle) — tip bytes
   emitted; (b) PURE V7 DELTA (tip==merge-base, reference typed by the
   git-control cutover a5e1cea3, oracle-only commit: _publish_git_error /
   _publish_review_blocked plumbing and the typed returns in _git_status,
   _git_diff, _stage_candidate_for_review and both stage cycles) — NOT
   replayed, rides with the F2 typed-result organ (same class as D04 entry 2
   / D05 entry 3). The reference-only plumbing symbols _publish_git_error and
   _publish_review_blocked were NOT created.
6. Row 392 (`_refuse_capped_attempt` -> git_review_cycle) — SOURCE-FALSIFIED:
   upstream 386e9417 ("Max Review Cycles") DELETED the symbol and re-derived
   the cap as the paid-cycle gate family (_free_cycle_gate,
   _install_paid_dispatch_stamp, _advisory_and_tests_gate,
   _repair_managed_merge_head, _finalize_pending_review,
   _review_custody_pending, _subject_binding_mismatch_outcome,
   _reconcile_and_clear_review_roster, _tests_preflight_block_message,
   _managed_candidate_needs_proof, _managed_committing_phase_error,
   _run_git_network_cmd — all unrowed post-cutoff facade symbols). The family
   STAYS in the facade (F5 unrowed census); moved spans read it through the
   call-time handle.
7. STRUCTURAL DIVERGENCE from the reference, disclosed: the reference's
   tools/git leaves bind cross-leaf/parent helpers with plain import-time
   from-imports; this tree's leaves declare EVERY parent-scope name their
   spans read and route it through the call-time `_git()` handle (the
   D18/D33/D35 mechanism, sets pinned in tests/test_module_handle_extraction.py).
   Reason, twice re-proven by red runs during the lane: the tip test surface
   monkeypatches those names on the PARENT facade
   (test_git_review_bypass_gate `_run_parallel_review`,
   test_update_status_cache `ensure_official_update_remote`), and an
   import-bound leaf copy makes every such patch silently dead — the
   monolith's module-global patchability is part of the moved behaviour. The
   only import-bound exceptions are the f-string reads the gate cannot
   rewrite (_sanitize_git_error in three leaves; format_protected_paths and
   utc_now_iso in one each), named in each leaf docstring; zero test patch
   surface exists for them today. The oracle's leaf-retarget test adaptations
   (rows 770/775 monkeypatch targets on git_review_cycle, test_commit_gate /
   test_vcs_target_binding / test_runtime_mode_registry_gating leaf imports)
   are therefore NOT mirrored — tip facade targets stay correct on this tree.
8. Test-split rows 3150-3191 (tests/test_git_ops_recovery.py -> 3 siblings +
   tests/_git_ops_recovery_shared.py) executed: 40/42 moved spans
   byte-identical to the reference siblings;
   test_official_fetch_timeout_kills_the_process_tree re-emitted from tip
   bytes (upstream communicate(input=...) drift, same train as the
   _run_git_process_bounded batch-stdin hunk); row 3179
   (test_dependency_sync_is_panic_tracked_and_killed_on_timeout) carried WITH
   the reference's hermetic root binding per its own row note (the tmp_path
   DRIVE_ROOT monkeypatch that keeps the mocked pip timeout from appending to
   the live supervisor log). Lossless: 48 == 48 test functions.
9. Test-split rows 765-783 (tests/test_git_review_pipeline.py -> 4 siblings +
   tests/_git_review_pipeline_shared.py) executed: 15/19 moved spans
   byte-identical; re-emitted from tip: _get_registry_module (reference
   imports the hot-deferred registry_core leaf — reverse-mapped to the tip
   registry spelling), TestAdvisorySkipTests (post-cutoff upstream autouse
   reviewer-slots fixture), TestBypassPathTestsRun / TestRouteSlotAwareBypassGate
   (reference monkeypatch retargets, entry 7). The reference shared module's
   unrowed `_get_git_review_cycle_module` accessor was NOT carried (nothing
   on this tree reads it; facade targets stay live through the handle).
   Lossless: 89 == 89 test callables. Path-keyed mirror: `_POPEN_ALLOWLIST`
   in tests/test_process_custody.py += supervisor/git_ops_reset.py
   (sync_runtime_dependencies moved with its waited+panic-tracked pip Popen —
   mirrors the reference's own allowlist row).
10. D13-remainder protective closure (coordinator LEDGER entry 4) landed by
   this lane per the D04 additive precedent: RELEASE_INVARIANT_PATHS
   (ouroboros/runtime_mode_policy.py, protected — strictly additive literal
   entries + comment) and scripts/run_external_review.py::
   _RELEASE_MACHINERY_PATHS += the four git_ops leaves; parity pinned by
   tests/test_git_ops_owner_facades.py (protection + hot-code-parity clauses).
   The reference's GIT_OPS_LEAF_MODULES/GIT_OPS_FAMILY_PATHS derived-set
   re-cut of the protected file is NOT replayed (a structural rewrite beyond
   additive closure — F5/owner decision); prompts-prose closure not touched
   (same as D04 entry 11). HOT_CODE_PATHS needs NO git rows (parent unlabeled
   at tip AND in the reference — parity, not blanket labelling).
11. Suite adaptation, disclosed: tests/test_module_handle_extraction.py
   `_module_bindings` gained Tuple-target unfolding (the git_ops facade binds
   its bounded-network aliases as `A, B = x, y` at :302-303) — same class as
   the D12 config-extraction Tuple fix. tests/test_git_extraction.py carried
   with adaptations named in its docstring (tool_module_inventory clauses
   dropped until that leaf lands; owner map minus the three reference-only /
   retired names; size bounds re-based on tip: facade <=1800 — it retains the
   paid-cycle gate family, the two deferred f-string spans and the catalog).
12. Zero-v7-delta re-proofs for the rest of the domain: repo_remotes.py,
   tools/git_rollback.py, version.py, update_recovery.py byte-identical
   tip==ref==merge-base; tools/ci.py, tools/commit_gate.py, tools/git_pr.py,
   tools/github.py, tools/review_revalidation.py, update_source.py pure
   upstream drift (ref==merge-base, zero v7 delta) — upstream bytes stand.
   update_candidate.py is a NEW post-cutoff upstream module (absent from
   reference and merge-base; no rows) — upstream bytes stand.
   update_merge_policy.py tri-divergence is fully owned by other lanes'
   landed HOT_CODE closures vs the reference's fuller loop/tool rows (their
   lanes) — no D10 action. size_ratchet_manifest.py regenerated with the
   official generator (git_ops.py and both split test giants left
   GIANT_PATHS; no new file enters any debt band).
13. Base-inherited, NOT this lane's defect:
   tests/test_smoke.py::test_size_ratchet_transition_against_explicit_base
   fails at the CLEAN base a56bb76a under its default HEAD-parent base
   (pre-proven in a detached worktree; BYTE_DEBT rows of four untouched files
   vs the wave-4 seam's parent); with the explicit base
   OURO_SIZE_RATCHET_BASE_REF=a56bb76a this lane's transition validates green
   (1 passed). Integration seam owns the default-base repair.
## From the D01 lane (base a56bb76a, 2026-08-30)
1. loop.py L-B split rows 3265-3425 (161 rows, nine leaves) executed against tip
   bytes: 150 spans landed with the transplant tool (drift-probe first per leaf;
   final --check per leaf: ast=tokens=bytes=True on every span,
   leaf_invariants=[], unread_declared=[], undeclared_top_level=[], exit 0).
   Drift-probe of the reference leaves against `git show HEAD:ouroboros/loop.py`:
   95/150 spans byte-identical, 55 BYTE-FALSIFIED as copy sources and re-emitted
   from tip bytes. Falsification class verified against the merge base
   (8028f1df): 52/55 pure upstream drift (oracle==merge-base, tip moved); the
   other 3 (_drain_incoming_messages, _check_budget_limits — oracle line-wraps
   around its own handle rewrites; _maybe_inject_finalization_nudges — oracle
   comment-prose rewording) carry NO code delta. Zero live v7 semantic deltas in
   the loop split; every span is tip truth.
2. ELEVEN loop rows SUPERSEDED-BY-UPSTREAM (upstream re-homed the symbol into
   its own leaf before this lane; tip ownership stands, no transplant):
   3273 (_last_assistant_text -> loop_transport.last_assistant_text),
   3312/3313 (_provider_failure_hint/_provider_recovery_hint ->
   loop_transport public pair; matches D02 lane entry 10), 3314
   (_task_deadline_epoch -> loop_transport.task_deadline_epoch), 3315/3316
   (_mark_owner_stop_control_drained/_owner_stop_window_elapsed ->
   supervisor/owner_stop.py, the 65b5d19f re-decomposition), 3340
   (_DELEGATE_ACTIVITY_TOOLS -> nanny_pacing.DELEGATE_ACTIVITY_TOOLS with a
   compat alias), 3341-3344 (the four _nanny_* helpers -> nanny_pacing.py
   public names; loop.py imports underscore aliases). The carried ledger
   renames those rows' destinations at F5.
3. Declared-set deltas against the reference LEAVES table, all tip truth,
   pinned in tests/test_module_handle_extraction.py: (a) same-leaf members tip
   tests monkeypatch on ouroboros.loop now read through _loop() even inside
   their own leaf (the reference instead re-pinned those tests to the leaf —
   its L3 wave; this tree keeps tip tests unchanged):
   _execute_task_acceptance_panel (acceptance_review);
   _compute_subagent_handoff, _resolve_delivery_control (delivery);
   _call_forced_model_once, _claimed_child_dispositions,
   _drain_forced_owner_directives (forced_finalization);
   _dispatch_round_model, _measure_round_main_fit, _run_main_reclaim
   (model_call); _skill_finalization_message (nudges);
   _mark_owner_stop_control_drained (round_limits; upstream re-homed the def
   into supervisor/owner_stop.py while tests still rebind it on the loop —
   proven by a red run of tests/test_owner_stop_s3.py before the declare).
   (b) round_limits gained _provider_unavailable_result and
   _append_or_merge_user_content as handle reads (tip drift); several oracle
   declared names dropped as unread on tip bytes (_last_assistant_text,
   _live_delivery_candidate in round_limits; _handle_forced_finalization in
   delivery) — the tool's unread_declared gate is the authority.
4. FACADE CONVENTION DIVERGENCE (disclosed, same class as D05 entry 4): the
   reference's L3 package trimmed the loop.py re-export surface
   (RETIRED_FROM_LOOP) after re-homing loop-private test imports to leaf
   owners. This tree keeps the FULL re-export surface (all 150 moved names,
   grouped per leaf at EOF) because the tip consumer set still addresses every
   moved name at ouroboros.loop; the L3 trimming is a consumer-rebind wave for
   F5, not part of the byte-preserving relocation.
   tests/test_loop_owner_facades.py is carried ADAPTED: the identity and
   hot-code-parity clauses survive over the full surface; the reference's
   RETIRED_FROM_LOOP absence clauses and the surviving-reason invariant are NOT
   carried (they pin the L3 state). HOT_CODE_PATHS closure mirrored for the
   nine loop leaves (D04/D08 precedent).
5. agent.py rows 3882-3897 -> agent_dispatch.py (D38 handle _agent, declared
   {write_task_result}): rows 3884-3897 executed from tip bytes (drift-probe:
   10/14 byte-identical, 4 pure upstream drift). Rows 3882-3883 SOURCE-
   FALSIFIED: upstream v6.105.0 moved dispatch_executor_note /
   executor_blocked_outcome into ouroboros/subagent_dispatch_notes.py; their
   live rows are 3935-3936 and the pair moved from THERE (shared-leaf
   convention: per-parent drift probes — the pair 0/2 byte-identical to the
   reference, both re-emitted from tip sdn bytes; final --check against the two
   parents concatenated, 16/16 green). subagent_dispatch_notes.py was touched
   ONLY by removing the pair spans + the re-export import (D01 part);
   its D07 rows 3937-3938 (SubagentExecutorResolution/SubagentLaneResolution
   bindings and the module-retirement question) stay untouched for the D07
   lane; the lost-reader imports keep the surface under noqa. agent_dispatch's
   tip spans additionally read _persist_early_origin_stub_impl (upstream
   re-homed the impl into agent_startup_checks.persist_early_origin_stub —
   tip-truth import, the D38 write_task_result handle read is intact).
6. agent_task_pipeline.py rows 3898-3909 -> post_task_synthesis.py: 11 rows
   executed from tip bytes (8/11 byte-identical, 3 pure upstream drift:
   _TASK_SUMMARY_PROMPT, _run_reflection, _run_task_summary). Row 3904
   (_summary_row_cost_fields) SUPERSEDED-BY-UPSTREAM: the symbol lives in
   ouroboros/synthesis_cost_text.py (public re-export list) — leaf imports it,
   ownership stands. The reference leaf has no handle; this tree's leaf is
   likewise projection-only (the auto-generated handle was stripped; zero
   declared). tests/test_lc2_owner_facades.py extended with the
   agent_dispatch/post_task_synthesis rows per the reference table, minus
   _summary_row_cost_fields (upstream home), with the sdn-facade note.
7. HOT-DEFERRED, typed-result/refusal class (tip bytes stand, nothing touched):
   loop_tool_execution.py rows 157-164/826-828 (classifier cutover; confirms
   D04 entry 6 from the D01 side — the shared monolith was not touched by
   either lane); loop_llm_call.py reference delta (+PROVIDER_POLICY_REFUSAL
   classification — imports llm_attempt symbols that do not exist at tip;
   rides with the D09 typed-refusal subfamily per D02 entry 4);
   _outcome_tool_errors.py reference delta (T1 status partitioning, D02
   family; re-prove trap per D15 entry 3); task_finalization.py reference
   delta (register-before-persist ordering — cancel/custody organ, 65b5d19f
   class, F2).
8. Test-split rows executed. tests/test_loop_misc.py (2037 lines, GIANT_PATHS)
   -> 4 siblings from tip bytes: test_loop_acceptance_gate.py (rows 3495-3505;
   6/11 spans byte-falsified by tip test drift, tip bytes moved;
   test_every_host_acceptance_writer_emits_a_canonical_status_and_typed_reason
   carried in the REFERENCE-ADAPTED form — the split spread the writers over
   loop.py + leaves and the reference's union-scan over loop_*.py is the
   identity continuation of the pin; the tip span byte-differs only by those
   two adaptation hunks), test_loop_image_attach.py (3506-3507),
   test_loop_skill_finalization.py (3508-3511), test_run_llm_loop.py
   (3512-3524; all byte-identical). UNROWED tip helper _seed_acceptance_root
   rode with its only readers into test_loop_acceptance_gate.py (F5 census).
   Rows 832-833 NOT executed: their destination suite pins the deferred typed
   cutover (D04 entry 9 class); the two tests stay in the remainder on tip
   bytes. Remainder 548 lines, left GIANT_PATHS; reader-less imports dropped.
   Lossless: 45 == 45 test names.
9. tests/test_agent_task_pipeline.py split rows: 22 of 34 rows
   SUPERSEDED-BY-UPSTREAM — upstream already extracted
   test_root_post_task_synthesis.py (3544-3556), test_post_task_reflection.py
   (3557-3560) and test_store_task_result.py (3561-3565) to the ledger's exact
   destinations. This lane executed the remaining two: test_task_summary.py
   (3535-3543) and test_collect_review_evidence.py (3566-3568), tip bytes,
   all byte-identical to the reference siblings. Lossless: 21 == 21.
10. Rowed import rebinds landed (identity continuations; the facade keeps both
   addresses live): 3915-3917/3932-3934 (test_v678_acceptance_state ->
   loop_acceptance / loop_acceptance_review), 3920-3921/3925-3928
   (test_loop_misc remainder -> nudges/round_limits/messages/acceptance), 3922
   (test_v6502_capability), 3923-3924 (test_budget_limits), 3929
   (test_nanny_finalization_nudge), 3930 (test_review_eligibility), 3931
   (test_transcript_seal), and the D02-deferred function-local retargets in
   tests/test_multimodal_chat.py (loop_messages; D02 entry 10 closure). Rows
   3918-3919 SUPERSEDED: tip already binds the provider hints from
   loop_transport (public names) — tip spelling stands.
11. Zero-v7-delta re-proofs for the rest of the domain (tip==ref==merge-base:
   _outcome_receipts.py, mutation_attribution.py; pure upstream drift,
   ref==merge-base: agent_startup_checks.py, deadline_utils.py, outcomes.py,
   owner_mailbox.py, post_task_checkpoint.py, synthesis_cost_text.py,
   task_pacing.py; NEW upstream modules, no rows: loop_transport.py,
   outcome_receipt_store.py) — upstream bytes stand. Ratchet: loop.py left
   GIANT_PATHS/BYTE_DEBT by extraction; agent_task_pipeline.py and
   loop_forced_finalization.py band entries recorded via the official
   regenerator's --band-rationale.
12. Post-battery closure per the D10 lane's lessons (superseding notes to
   entries 5-6): (a) MAXIMAL declared sets — a precise AST audit of every
   frozen leaf-preamble import against test patch surfaces (setattr on parent
   aliases + string-form patch targets) found three more dead facade patches
   and converted them to handle reads: agent_dispatch declared grew to
   {envelope_from_task, write_task_result} (test_available_subagents_runtime
   patches envelope_from_task on ouroboros.agent), and post_task_synthesis is
   NO LONGER projection-only — it carries the _atp() handle with declared
   {_is_root_post_task, load_task_result} (test_presence_post_task /
   test_agent_task_pipeline patch them on the pipeline), diverging from the
   reference's handle-less leaf, which froze _is_root_post_task by import.
   Zero f-string reads of rebindable globals were hit in any D01 emit (the
   tool's f-string gate never fired — no f-string HOT-DEFERRED spans in this
   lane). (b) tests/test_v7next_transplant.py loop probes re-pinned to the
   pre-split monolith bytes of the lane base (git show a56bb76a:ouroboros/
   loop.py, the D10 recipe) with a self-contained fallback that inverse-
   normalizes the landed loop_messages leaf — the suite is green either way.

## From the F2.1 D07-quiet lane (base 50377313, 2026-08-31)

1. Scope executed (the QUIET D07 part, F1 conveyor): 7 module leaves landed
   from tip bytes with the transplant tool (ast=tokens=byte-roundtrip=True on
   every span, exit 0, leaf_invariants=[], undeclared_top_level=[]):
   delegate_custody_reconcile (rows 3458-3466; D36 handle `_custody()`),
   delegate_payload_patch (3477-3483; `_di()`), subagent_integration_delegated
   (3484-3494; `_si()`), subagent_route_health (3939-3942; projection-only, no
   handle), and the D07 half of the SHARED tools/control.py —
   control_subagent_spec (2537-2542), control_scheduling (2543-2556),
   control_task_results (2569-2579). Facades = tip parent − moved spans +
   grouped EOF re-export block (noqa discipline for historical imports);
   facade audit green (every kept def/assign span byte-identical to `git show
   HEAD:<monolith>`, every moved name re-exported). Both 1600-hard-cap giants
   this lane was allowed to touch shrank: delegate_custody.py 1600→1305,
   control.py 2110→492 (control.py and the transport test giant LEAVE
   GIANT_PATHS/BYTE_DEBT); delegate_integration.py 1540→868,
   subagent_integration.py 1599→1027, subagents.py 1593→1370.
2. Drift-probe results (reference leaf `--check` against tip bytes, first
   step per leaf): delegate_custody_reconcile 2/9 spans byte-true (7
   re-emitted from tip); delegate_payload_patch 6/7 (integrate_payload_patch
   drifted); subagent_integration_delegated 10/11 (_integrate_delegated_patch
   drifted); subagent_route_health 2/4 (route_health, _exhausted_window
   drifted); control_subagent_spec 4/6 (schedule_subagent_properties,
   _validated_schedule_fields drifted); control_scheduling 9/14 rowed spans
   byte-true (5 drifted); control_task_results 7/11 (4 drifted). Every
   "verbatim" ledger claim was re-proven by bytes; no oracle semantics were
   replayed over tip drift (custody semantics: upstream is a strict superset
   — only the split FORM was taken from the reference).
3. Declared-set recalcs against the reference LEAVES table (tool
   unread/unresolved gates are the authority; new rows in
   tests/test_module_handle_extraction.py): `_custody()` dropped STARTED,
   START_REQUESTED, _CUSTODY, _iter_rows, event_log_path (tip drift stopped
   reading them) and gained retire_settled_registrations (upstream retirement
   decoupling 3226cc0c/8fe5a071); REVIEW_ATTRIBUTION_KEYS became a leaf
   preamble import (constant, never rebound in tests). `_di()`/`_si()` sets
   byte-matched the reference. control_scheduling declares exactly
   {load_settings} (tests rebind it on the facade); the reference control
   leaves were handle-free, the tip drift introduced that one read class.
4. Row 3467 (_capture_stranded_patch → delegate_custody_reconcile.py)
   SUPERSEDED-BY-UPSTREAM: 81194970 re-homed it as the public
   tools/delegate_integration.py::capture_stranded_patch and the body drifted
   further there; ownership stands with upstream, the row's destination needs
   an F5 rename (class: D01 lane entry 2).
5. Unrowed post-cutoff control.py neighbours ride with their only readers
   into control_scheduling: _context_task_depth (read only by
   _schedule_task), _materialize_child_attachment_manifest (same),
   maybe_emit_delegated_run_fanout (external reader tools/delegate.py:935
   does a call-time facade import — the facade re-export is load-bearing),
   HIDDEN_LEGACY_SCHEDULE_PARAMS. F2-matrix falsification: the matrix routed
   HIDDEN_LEGACY_SCHEDULE_PARAMS with the row-2541 reader
   (_validated_schedule_fields → control_subagent_spec); the tip readers are
   _schedule_task:865 and the module-level handler-attribute stamp
   `setattr(_schedule_task, "_hidden_legacy_params", …)`:1166 — probe beats
   matrix, the set moved with control_scheduling. The setattr Expr is the one
   facade statement RELOCATED below the re-export block (it reads two moved
   names at import time; consumer tools/tool_resolution.py:337 reads the
   attribute off the registered handler object, same object either way).
6. Transport test giant (6187 lines, 177 ledger rows): re-cut from tip bytes
   as the S7a theme split — 140 rowed tests moved to 10 destinations
   (cancellation_settlement 5, executor_axis 32, reconciliation 6 APPENDED to
   the file the D11 lane already created, result_delivery 12, run_accounting
   17, run_containment 11, run_custody 13, run_profile 15, wait_timeline 10,
   wait_window 19), 21 unrowed post-cutoff tests stayed in the remainder;
   lossless proven: 163 unique test names before == after (161 giant + 2
   pre-existing reconciliation), zero new duplicate names, every moved span
   byte-identical to the giant's bytes. Helper placement followed the rows
   (15 defs → tests/_delegated_transport_shared.py, private stubs → their
   sibling suites) with two documented lane placements: _plain_ctx went to
   the SHARED module instead of run_accounting (tip-only external consumer
   tests/test_delegation_account_pin.py imports it beside the autouse
   fixture; its import was re-pointed to the shared home), and the unrowed
   post-cutoff _transport_snapshot went to shared (the autouse fixture reads
   it). Four rows re-homing giant constants into runtime SSOTs
   (ACTING_SUBAGENT_TOOL_NAMES, LOCAL_READONLY_SUBAGENT_TOOL_NAMES →
   tool_capabilities; CLAUDEXOR_DELEGATED_MARKER_MIN_VERSION → config;
   MODEL_SETTING_KEYS → provider_models) are SATISFIED BY UPSTREAM (the tip
   giant already imports them). 44 oracle-rowed test names are absent from
   the tip giant (upstream renamed/re-homed/retired them; the upstream
   test_delegated_run_isolation.py / test_delegated_skill_payload.py themes
   were built upstream as its own files) — no rows executed for absent
   names, F5 census item; tip bytes stand.
7. Dead-patch class (D08 lane entry 9 recipe; oracle adaptations mirrored
   into THIS tree's file names): tests/test_task_status_flow.py — 3
   wait-grace sites re-pointed to control_task_results (mirror of oracle
   test_task_status_wait_tools.py) and the queue-fallback test's
   write_task_result patch alias re-pointed to control_scheduling (mirror of
   oracle test_task_status_scheduling.py); tests/test_subagents_phase3.py —
   prepare_task_drive patch alias → control_scheduling (mirror of oracle:94);
   tests/test_external_workspace_access.py — system/active_repo_dir_for
   patch alias → control_scheduling (mirror of oracle:86);
   tests/test_cache_optimization.py — two wait aliases → control_task_results
   (mirror of oracle:435/480). A sweep of every other moved/frozen name found
   no further facade patches whose exercised path reads the leaf scope (the
   join_ledger _emit_control_event patches stay live: that path re-imports
   through the facade at call time; subagents.route_health and the custody
   sweep patches stay live: their callers stayed in the facades / read
   through `_custody()`).
8. HOT-DEFERRED with evidence (owner forks — nothing emitted):
   - Ф-2 (delegate_terminal name collision): rows 3468-3476 NOT emitted;
     tools/delegate.py stays exactly at the 1600 hard cap (at, not above —
     ratchet green). Probe evidence recorded: 7/9 reference spans byte-true,
     _terminal_payload + _delivered_terminal_payload upstream-drifted; the
     reference facade-identity rows for this family are also held back.
   - Ф-1 (subagent_worktrees.py strict-registry, rows 1083-1092 + the
     280-line pin suite): in-place semantic delta, not transplanted without
     owner sanction; tip==merge-base for the module, so the delta stays
     cleanly appliable.
   - Ф-3 (subagent_dispatch_notes retirement): rows 3937-3938 verified
     SATISFIED as identity on tip (sdn:17 imports the pair from
     ouroboros.subagents under the D01-lane noqa marker); the 71-line facade
     stays; retirement is an F5 consumer-rebind item (agent.py at its size
     ceiling + 3 test files + 2 unrowed helpers).
   - Six D02 rows (2548 _build_acting_constraint, 2549
     _select_subagent_constraint, 2556 _schedule_task, 2571 _get_task_result,
     2574 _wait_for_task, 2579 _wait_for_tasks) were cut in TIP form —
     ouroboros/tools/tool_result.py does not exist on tip; the D02 delta
     returns as a package with the typed-result organ (the plan's mandatory
     "D02 loop" return).
9. Hot-code label parity: the three control leaves joined HOT_CODE_PATHS
   beside the D08 trio (control.py stays labeled); the delegate/subagents
   families are unlabeled and their leaves keep parity — pinned in the
   adapted tests/test_delegate_owner_facades.py (reference file minus the
   deferred delegate_terminal group and the superseded stranded-patch row).
10. Out-of-scope defect FOUND (not fixed here, D06/review-organ material,
    D15 class): tests/test_review_cycles_dispatch.py and
    tests/test_review_cycles_skill_dispatch.py carry 10 AST-identical
    duplicate test functions (pre-existing at this lane's base).
11. For the integration seam: scripts/v7next_domains.toml rows for the seven
    new runtime leaves (D07) and the ten new/regrown test siblings follow the
    established seam convention (lanes do not edit the map); quotient report
    regeneration likewise.

## From the F2 addendum (coordinator, base 3c425206, 2026-08-31)
1. MIGRATION row 2016 (_handle_schedule_task -> events_schedule_task.py) —
   EXECUTED: the D08 deferral was unblocked by the D11 same-qualname
   relocation rule. Proof: span emit via the tool (ast=tokens=bytes=True,
   one handle read `_parent_delegation_budget`); whole-leaf verify with
   leaf_owned = the leaf's prior residents (the emit-time top-level gate is
   structurally blind to append-into-existing-leaf — assembly ran with that
   gate bypassed and the hardened verify as the actual authority; a
   `--leaf-owned` CLI flag is an F5 tool candidate). FUNCTION_DEBT key
   relocated with the function; events.py 1947->1406 entered the 1001-1500
   band by extraction with rationale. D08's work-order pins flipped as
   designed (dispatch owners + facade census).
2. Stale docstring of tests/test_delegated_reconciliation.py refreshed
   (F2.1 conformance item 7): the file owns the full reconciliation theme.
3. Addendum round 2 (battery findings): (a) tests patching
   `events._find_duplicate_task` retargeted to the leaf module — the ORACLE's
   own adaptation shape (its tests patch `schedule_module`), 25 sites across
   three files; the declared-through-handle alternative is structurally
   refused by the tool for leaf-resident names (ambiguous ownership, by
   design). (b) `_build_scheduled_task_payload` restored as a noqa facade
   import — tests import it from supervisor.events.
4. SUPERSEDING correction to entry 1 of this section (audit 31.08 07:27, F5):
   the FINAL landed leaf carries TWO handle reads (_parent_delegation_budget
   AND get_max_subagent_depth - the latter added when the patch-surface scan
   found tests monkeypatching it on the facade), and the facade size after
   the final import restores is 1392 lines, not 1406/1389 as the earlier
   prose said. The proof chain (span emit + hardened verify with leaf_owned)
   was re-run at each state; entry 1's figures describe an intermediate
   state and are superseded by these.
5. Audit 31.08 F1 second name: tests patching `_resolve_subagent_constraint`
   on the facade (one negative sentinel in test_nested_rights_depth) were
   retargeted to the reading leaf module - same oracle retarget-to-owner
   shape as the _find_duplicate_task sites; the sentinel's teeth are
   restored (the leaf's import-bound name is the one the handler reads).

## From the F2 D07-finisher lane (base 2878560e, 2026-08-31)

1. Scope executed (the three D07 owner forks, decided 31.08 batch 5: 5.9A,
   5.10A, 5.11A): the deferred terminal leaf of tools/delegate.py, the
   subagent_worktrees.py strict-registry delta with its pin suite, and NO
   sdn retirement (the facade stays).
2. F5-RENAME record (owner fork F-2=A, ledger rows 3468-3476): the reference
   leaf destination `ouroboros/tools/delegate_terminal.py` is renamed at
   landing to `ouroboros/tools/delegate_terminal_evidence.py`. Rationale:
   upstream already owns `ouroboros/delegate_terminal.py` ("terminal
   reconciliation boundary", 189 lines) and the ledger name would put two
   different delegate_terminal modules in neighbouring packages — a
   permanent grep/reading trap. Same class as the D01/D03 F5 destination
   renames. Rows 3468-3476 read onto the renamed file unchanged otherwise.
3. Terminal leaf landed from tip bytes (rows 3468-3476, D36 handle
   `_delegate()`): drift-probe first (reference leaf `--check` against
   `git show HEAD:ouroboros/tools/delegate.py`): 7/9 spans byte-true,
   _terminal_payload and _delivered_terminal_payload upstream-drifted —
   matching the quiet lane's held-back probe evidence — so the leaf was
   EMITTED from tip bytes, no oracle semantics replayed. Final proof:
   ast=tokens=byte-roundtrip=True on all 9 symbols, leaf_invariants=[],
   undeclared_top_level=[], unread_declared=[], exit 0 (re-run after the
   manual TYPE_CHECKING preamble addition, the D07-quiet
   reconcile-leaf precedent).
4. Declared-set recalc, MAXIMAL form (D10 tools/git precedent, finisher
   work-order): the reference cut this leaf with plain preamble imports and
   declared only {_emit}; the landed leaf declares EVERY parent-scope name
   the moved spans read at call time — 12 names: _Breach,
   _PAYLOAD_ENVELOPE_HEADROOM, _emit, _home_isolation_breach,
   _preview_payload, _resolve_full_primary_output, _stage_full_output,
   _widened_access, add_terminal_source_verification, custody,
   home_nested_under_operator_home, tool_result_limit — so patches on the
   historical `ouroboros.tools.delegate` surface keep their teeth. Only
   stdlib (json) and typing stay preamble imports; annotation-only names
   (_Breach for its `-> Optional[_Breach]` use, _RunCustody, ToolContext,
   DelegatedRunShape) ride an `if TYPE_CHECKING:` block, inert under future
   annotations. New LEAVES row pinned in
   tests/test_module_handle_extraction.py.
5. Facade: tools/delegate.py = tip parent - the 9 moved spans (lines
   225-576 of the HEAD file) + the grouped EOF re-export block + noqa
   discipline: exactly four `# noqa: F401` markers on the import lines of
   parent members now read only through `_delegate()` at call time
   (_home_isolation_breach, _widened_access, home_nested_under_operator_home,
   add_terminal_source_verification — the bindings are load-bearing for the
   leaf and must survive ruff F). Every kept def/assign span proven
   byte-identical to `git show HEAD:ouroboros/tools/delegate.py` (the diff
   of the kept region is exactly those four marker lines); re-exports
   proven same-object by import smoke. tools/delegate.py 1600 -> 1263: the
   LAST 1600-hard-cap giant of the D07 organ leaves the cap and enters the
   1001-1500 band with a rationale. The reference facade-identity rows for this family (held
   back by the quiet lane) landed in tests/test_delegate_owner_facades.py
   under the renamed leaf.
6. Ф-1 strict-registry delta (rows 1083-1092, owner sanction 5.10A —
   SANCTIONED SEMANTIC DELTA in an otherwise byte-preserving lane):
   drift-probe first — tip blob of ouroboros/subagent_worktrees.py ==
   merge-base 8028f1df blob (fd2db424, upstream never touched the module),
   so the reference diff (+104/-22) applied clean; the landed module is
   byte-identical to the reference module (blob ee694e4d on both sides).
   Semantics: absent registry stays an ordinary empty registry; malformed
   registry raises typed SubagentWorktreeRegistryCorrupt for every author/
   destructor (provision_worktree, provision_execution_snapshot,
   provision_payload_snapshot, find_execution_snapshot,
   remove_execution_snapshot, prune_execution_snapshots, remove_worktree,
   prune_orphans) instead of silently collapsing to empty; bytes are kept;
   one durable subagent_worktree_registry_corrupt event; inspection reads
   stay soft; registration moves INSIDE the cleanup scope on all three
   provisioning branches. Pin suite
   tests/test_subagent_worktree_registry_s6.py copied verbatim from the
   oracle (281 lines, 11 tests, red without the delta per D09 entry 10):
   imports only stdlib + the module itself, zero v7-only names to reverse-
   map; its docstring's sibling reference
   (test_delegated_skill_payload.py::test_registry_save_failure_leaves_no_orphan_snapshot_dir)
   exists on tip; the oracle registered it in no conftest path-keyed table.
   The one pre-existing tip test touching the registry
   (tests/test_acting_subagents.py:1298) uses the soft read, whose
   signature and behavior are unchanged.
7. Ф-3 (sdn): no action, per owner 5.11A — the quiet lane's entry 8 stands
   (rows 3937-3938 satisfied as identity; retirement stays an F5
   consumer-rebind item).
8. Ratchet (official regenerator): ouroboros/tools/delegate.py enters the
   band by extraction (1600->1263, rationale recorded);
   ouroboros/subagent_worktrees.py enters the band by the sanctioned delta
   (1000->1082, rationale recorded). domains.toml untouched (coordinator
   seam owns the map).
## From the F2.4 update-engine lane (base 2878560e, 2026-08-31)
D34 return + 1A re-split, per owner answers Ф-1=A / Ф-2=A / Ф-3=A
(= plan rows 5.12-5.14A). Every re-derived body below is justified as
reference-fact ↔ tip-fact ↔ result.
1. Span-SSOT re-cut (ouroboros/tools/release_sync.py, merged ATOP the tip
   file, not a replacement). Reference: 8 descriptors (v7_wip
   release_sync.py:65-148). Tip inventory is WIDER: sync_release_metadata
   writes the two public install pages (tip :423-434) and the README
   direct-download reference block (:100-113); version_carrier_desyncs /
   update_candidate.py:697-698 check them. Result: 25 descriptors = the 8
   reference spans + readme_download_refs (the contiguous
   `[download-<id>]:` block) + 8 anchors per install page, derived from
   RELEASE_ASSET_TEMPLATES (a new installer automatically gets a span);
   macos-arm64 appears twice per page and is disambiguated by the
   quick-start step's literal "Click " prefix (lookaround pair) — a page
   restructure degrades to malformed/duplicate-anchor, never a guess.
   Latent-trap fix proven by span inspection: proof ids carry `x86_64`, so
   a `[a-z0-9-]` class matched the tip block ONCE but covered only its
   first 3 lines (wrong-coverage, silent partial substitution) — the class
   is `[a-z0-9_-]`, and the live-tree pin asserts full-block coverage
   indirectly through exactly-once anchoring of every descriptor.
2. supervisor/update_carriers.py returned WHOLE (no upstream analog); two
   bodies re-derived against the redesign train's bounded-plumbing rule
   (4795a810/c404c056 class): _run_git and the merge-file runner now start
   the child in its own process group and kill the WHOLE TREE on a 300s
   timeout (constant mirrors update_candidate._GIT_RUN_TIMEOUT_SEC) —
   insertion point 3 runs while the update lock is held. Byte-exact capture
   (text=False semantics) preserved from the reference. Deliberately NOT
   routed through git_ops._run_git_process_bounded: that helper imports
   ouroboros.tools.shell at call time (tool-registry package init), which
   would break the standalone operator rebase helper; the
   _active_subprocesses shutdown-tracking nicety is therefore not carried
   (short-lived waited children — disclosed residual). Docstring
   re-derived: insertion host is the re-cut update_merge_plan.py; the
   resolver never runs `git merge` (explicit index stages + `git
   merge-file`), so it is rerere-neutral by construction, in line with the
   train's _MERGE_NEUTRAL_FLAGS discipline; M0 note per Ф-2=A.
3. Three insertion points re-derived against the REWRITTEN tip bodies
   (reference bodies were pre-redesign; matrix rows MIGRATION:3427-3429):
   (a) point 1 (row 3428): reference update_merge_plan.py:334-344 ↔ tip
   plan_managed_update_merge (stash-first; snapshot via
   worktree_snapshot_tree instead of the temp-index) → resolution after
   the merge/inventory consistency check, BEFORE classify_conflicts; the
   single body serves both the preview plan and the authoritative
   build=True replan (control.py replans on the clean tree through the
   same function). `carrier_resolved_paths` restored to the ff-clean,
   base-conflict and main returns (reference shape).
   (b) point 2 (row 3427): reference :88-97 ↔ tip _build_clean_merge_commit
   (fast_forwardable early return, Q8 projection before write-tree) →
   resolution inside the rc_bm==1 branch BEFORE write-tree; the tip's
   `if base_conflicts: return` inverted to the reference's
   no-inventory-error + resolve + `if remaining: return` shape; the Q8
   projection now runs AFTER span resolution, so its postcondition also
   verifies the just-resolved carriers.
   (c) point 3 (row 3429): reference :454-469 ↔ tip materializer
   (rerere-off flags, mandatory Q8 projection, CAS re-parent, M0 pin) →
   resolution after MERGE_HEAD validation and BEFORE the projection and
   the M0 pin (Ф-2=A: span policy is part of the mechanical baseline;
   reviewers diff an M0 already free of carrier markers); the tip 3-tuple
   return (ok, message, m0_tree) preserved.
   Handle idiom: the reference `_um()` handle is retained ONLY for
   managed_update_constitution_present (monkeypatched on the parent facade
   — test_update_merge_assisted.py:973); update_candidate members are read
   through the `_uc` module object (test_update_hardening.py:99/125
   patches update_candidate.worktree_snapshot_tree) — the D10-lane entry-7
   patch-surface rule. The row-3426 verbatim `_git_run` relocation stays
   SUPERSEDED (upstream re-homed it to update_candidate; the leaf reads
   `_uc._git_run`).
4. Boot-recovery backfill window NOT extended (upstream recovery semantics
   = floor): _recover_assisted_on_boot's M0 backfill re-runs only the Q8
   projection; a carrier still conflicted through that crash window
   degrades to the assisted lane (fail-safe, never fail-wrong). Disclosed
   in the wiring pin's docstring; keeps the "3 resolver calls in the leaf,
   0 in the parent" invariant intact.
5. 1A re-split executed per Ф-3=A from the two-module tip form:
   update_merge.py 1593 → 1193 (tx/lock/rollback/boot-recovery facade,
   re-exports both leaves), new supervisor/update_merge_plan.py (490 =
   three tip bodies + the documented deltas). The reference leaf is the
   THEME (same three owners), not bytes. Ratchet: update_merge.py entered
   the 1001-1500 band by extraction with a rationale via the official
   generator; `-m size_ratchet` = 5 passed.
6. update_merge_policy.py coordination (matrix row "согласовать"):
   carrier_guidance's hand-list VERSION_CARRIER_PATHS (6 paths, already
   narrower than the tip's own carrier inventory) replaced by a call-time
   read of the span SSOT (CARRIER_SPAN_PATHS); prose re-derived — spans
   resolved mechanically never reach the resolver's list (verified:
   control.py:820 refreshes tx.conflict_paths from live_unmerged_paths
   after materialization), so the guidance now describes exactly the
   DEGRADED remainder and what degradation means.
7. Protection closure (the G1/D10 additive-literal precedent, coordinator
   LEDGER entry 4 class): RELEASE_INVARIANT_PATHS +=
   supervisor/update_merge_plan.py, supervisor/update_carriers.py —
   the split moved planner/materializer bodies out of a release-invariant
   file and the resolver rewrites worktree files under the update lock;
   parity pinned in tests/test_update_merge_owner_facade.py. DISCLOSED
   upstream inventory gap, NOT repaired (Q4=A, upstream owns protected
   surfaces): supervisor/update_candidate.py carries bodies upstream's own
   redesign moved out of the same protected parent, yet is absent from
   RELEASE_INVARIANT_PATHS — owner/Ф3 material.
8. Tests: test_update_carriers.py ported with re-derivations (leaf import
   path unchanged; materializer test unpacks the tip 3-tuple and pins that
   M0 names the official VERSION blob; corpus README fixture extended with
   the FULL 7-id download-refs block — the Q8 postcondition checks every
   RELEASE_ASSET_TEMPLATES member once a README opts into the projection,
   and the new span must anchor; SSOT pin re-cut to 25; an explicit
   "conflicted carrier never routes to assisted" strategy pin added per
   the work order). test_carrier_rebase_helper.py + the operator helper
   returned (helper docstring's carrier list re-cut).
   test_update_merge_owner_facade.py re-derived: owners = update_merge_plan
   (3 bodies) + update_candidate (the redesign's own boundary, identity
   now pinned); hot-code and release-invariant parity clauses.
   _POPEN_ALLOWLIST (tests/test_process_custody.py) +=
   supervisor/update_carriers.py (path-keyed mirror, D10 git_ops_reset row
   class).
9. NAME COLLISION tests/test_update_merge_plan.py resolved as SUPERSEDED,
   not transplanted: the oracle file's 13 test functions are
   name-set-identical to the tip file and the tip bodies are the
   upstream-evolved forms of the same assertions (stash status tuple
   "ok"/sha, failed-update-<target12> forensics naming) — zero unique
   oracle content; a rename-transplant would mint 13 AST-near-duplicates
   (the D15 class the wave mandate bans). Tip bytes stand.
10. Upstream test re-derived (falsified-by-D34 fixture, the "test pinning
   the gap" class): test_update_merge_assisted.py::
   test_materialize_projects_version_to_target_and_pins_m0 used a clean
   1.5.0-vs-2.0.0 VERSION token conflict, which the D34 planner now
   resolves (plan turns clean — the scenario could no longer reach the
   materializer's projection). The local token becomes a malformed anchor
   ("not-a-version"), so the span resolver degrades honestly and the Q8
   projection clause the test pins stays reachable; docstring says why.
11. Ф3 joints named, untouched (report-only): the future N−1 shim surface
   (finalize_managed_update_on_boot / _recover_assisted_on_boot /
   _recover_replace_on_boot / _finalize_pending_boot_smoke /
   apply_managed_merge_update / rollback_managed_update) stays WHOLE in
   the parent — the re-split does not dissect ABI-7/F14 material; the RC
   auditor's evidence surface (record_managed_tests_evidence /
   managed_tests_evidence_covers) untouched in update_candidate;
   git_ops.py:1031-1032 (D13) untouched — protected wave; Ф-4 derived
   FAMILY_PATHS not executed (coordinator's tail item — the additive
   entries in item 7 keep that door open).
12. Pre-existing at base, NOT this lane's defects (dup-scan receipts):
   10 AST-identical test pairs across test_review_cycles_dispatch.py /
   test_review_cycles_skill_dispatch.py (already named by the Ф2-plan) and
   an in-file duplicate def test_ripgrep_download_script_verifies_checksum
   in tests/test_build_scripts.py (the later def shadows the earlier —
   D15-class latent, review-organ/F5 material).
## From the F2.3a review-mechanics lane (base 2878560e, 2026-08-31)
1. FALSIFIED row: `tests/test_review_substrate_v2.py::_render_prompt ->
   review_substrate` (repoint to the "canonical substrate owner"). Upstream
   moved `_render_prompt`/`_render_prompt_parts` into review_execution
   (substrate back-imports them as compat re-exports), so the row's target is
   stale. Executed as re-derive: the split's prompts suite
   (tests/test_review_substrate_prompts.py) imports `_render_prompt` from
   ouroboros.review_execution.
2. ROW CORRECTION: `ouroboros/tools/scope_review.py::_load_canonical_context_docs
   -> scope_review_pack.py` was NOT executed as written — the symbol stays a
   facade def. Its body reads `load_governance_doc` inside an f-string (the
   byte gate refuses f-string reads of rebindable globals) and tests rebind
   that name on the parent (test_review_convergence_rule.py:122 et al.), so a
   leaf copy would go dead-patch. Same class as the D10 lane's
   safe_restart/prepare_managed_update facade retention. The pack leaf reads
   it through the `_sr()` handle; 19/20 pack rows moved.
3. NEW-OWNER leaf (owner decision 5.3=B, one-cut): ouroboros/review_state_custody.py
   carries nine post-cutoff upstream symbols no MIGRATION row names —
   unrowed F5 candidates, recorded here as adoption rows:
   review_state.py::{_ACTIVE_REVIEW_OPERATION_STATES, _attempt_review_roster_rows,
   _review_roster_row_is_pending, _attempt_has_active_review_custody,
   checkpoint_pending_review_invocation, _attempt_history_evictable,
   _STRIPPED_DETAILS_LIMIT, _STRIPPED_MESSAGE_LIMIT, _strip_attempt_heavy_payload}
   -> review_state_custody.py::<same name> (adaptive-timeout/custody train;
   tool-proof ast=tokens=bytes on every span). The four authority-shape
   deserialization symbols of the same train (_malformed_roster_row,
   _ATTEMPT_AUTHORITY_STRING_FIELDS, _ATTEMPT_AUTHORITY_BOOL_FIELDS,
   _validate_attempt_authority_shape) stay with the parent STORE by design.
4. SUPERSEDED rows honored (upstream home wins, Q4=A; leaves/tests do not
   replay them): review_evidence.py::{_ACCEPT_DELTA_CHILD_CAP,
   _accept_capability_deltas} -> delegate_evidence (facade reads
   acceptance_capability_deltas back at call time);
   tools/review.py::_parse_model_response -> tools/review_response.py
   (facade re-import is the single alias, pinned by
   test_review_owner_facades.py). RETIRED rows honored:
   tools/review.py::{DEFAULT_REVIEW_MODEL_TIMEOUT_SEC, _review_model_timeout_sec}
   died with the adaptive-timeout contract and are not restored.
5. Import-bound exceptions (f-string/import-time gate; named in each leaf
   docstring): review_multi_model: SLOT_ID_PREFIX (default argument);
   review_file_pack: format_prompt_code_block (f-string; unpatched in tests);
   scope_review_pack: format_review_history_entry,
   _HISTORY_VERIFICATION_ONLY_RULE, _ANTI_THRASHING_RULE_VERDICT,
   _CONVERGENCE_RULE_TEXT (f-strings; owner review_prompt_text);
   review_evidence_sections: DEFAULT_TOOL_RESULT_LIMIT (default argument);
   review_state_model: _STATE_SCHEMA_VERSION, _DEFAULT_ADVISORY_TOOL_NAME,
   _REVIEW_ATTEMPT_TTL_SEC, _REVIEW_ATTEMPT_GRACE_SEC (class-level defaults),
   _stable_digest (f-strings) — owner review_state_records;
   review_records/review_verdict: ReviewRouteKind / OUTCOME_TIER_* (class-level
   and module-level constants). None of these names is monkeypatched on the
   parents anywhere in tests/ (verified by grep before binding).
6. TEST DELETION disclosure (owner decision 5.2=A): ten AST-identical test
   functions plus seven byte-identical orphan helpers were deleted from
   tests/test_review_cycles_dispatch.py; the owner of those tests is
   tests/test_review_cycles_skill_dispatch.py (D14 family — they exercise
   skill_review_* modules only). Verified byte-level: ast.dump-identical in
   both files before deletion, zero shared-but-different defs. −510 lines of
   double-executed runtime; the dispatch file remains the D06 commit-gate
   paid-accounting suite.
7. Session-route split: three reference-authored tests absent from the tip
   giant were NOT replayed (skipped, F5 material):
   test_unhealthy_route_refuses_typed_never_falls_back,
   test_route_status_refusal_carries_its_typed_code,
   test_retry_of_a_pinned_session_health_checks_the_stored_account. Thirteen
   tip-only (post-cutoff) tests were placed with the sibling that owns their
   helpers (2 -> scope_wiring, 1 -> poller, 2 -> delivery, 8 stay in the
   remainder with FakeGateway/_run_session_directly imported from the shared
   module). Lossless: 102 == 102 test names across the five files, zero
   duplicate names.
8. Substrate split lossless: 71 == 71 test names across six files — the
   reference's five plus tests/test_review_substrate_custody.py, a NEW
   sibling created by this lane for the eighteen post-cutoff upstream tests
   (the adaptive-timeout/custody train theme, 907 lines of tip bytes); the
   remainder would otherwise have stayed a >1600 giant. Both re-derived
   extraction suites drop the reference's tool_module_inventory clauses
   (that module exists only on the reference).
9. Path-keyed mirrors (D10 additive-closure precedent):
   review_context_atlas._REVIEW_STACK_PATHS += the eight state/evidence/
   helpers/scope leaves (oracle placements) + the new custody leaf;
   scripts/run_external_review.py::_REVIEW_SUBSTRATE_PATHS += all eleven
   leaves beside their parents. The hand-list's structural rot (28/48 D06
   modules absent before this lane) is the Р1/D31 fork — Ф2.3b territory,
   not repaired here beyond the additive closure for our own leaves.
10. review_records is a projection-only leaf (zero handle reads, zero
   declared) and stays off the LEAVES table per the D07/D08 precedent; the
   other ten leaves carry tool-derived exact declared sets there.
## From the f22 lane (base 2878560e, 2026-08-31)
1. Drift-probes (recipe §5.3-Δ2 step 9) of every oracle leaf against this
   base's monolith bytes, before any emit. Byte-identical tip↔oracle:
   _task_done_review_projection, _PROVIDER_DEATH_NOTIFIED,
   _task_done_durable_fault, _handle_task_done, _handle_evolution_task_done,
   _close_campaign_after_owner_stop, _kept_service_pids, parse_iso_to_ts,
   all queue_timeouts symbols except _enforce_task_timeouts_locked,
   _evolution_assignment_error, _cancel_unauthorized_evolution,
   terminal_task_metadata, _emit_task_done_terminal, ensure_workers_healthy.
   Byte-FALSIFIED as copy-source (upstream drift, re-emitted from tip bytes):
   _authoritative_terminal_cost, _maybe_notify_provider_death,
   _finish_task_done_dispatch, _resolve_lifecycle_fault, _handle_cancel_task,
   persist_queue_snapshot, restore_pending_from_snapshot,
   _enforce_task_timeouts_locked, assign_tasks,
   _ensure_workers_healthy_locked. ALL families were emitted from tip bytes
   regardless (proof: ast=tokens=True per symbol, leaf_invariants=[]).
2. Q-a=A (owner, 2026-08-31): the sixteen settle-owner rows 998-1013
   (task_lifecycle -> supervisor/cancel_custody.py) are SUPERSEDED — the
   settle owner STAYS in task_lifecycle.py; the upstream custody cut
   (65b5d19f/bea08137) is the authoritative floor and cancel_custody.py is
   never created. tests/test_cancel_custody_extraction.py is NOT replayed
   (its identity/size clauses are form-dependent on the extraction; matrix
   §3.8). Row 1000 (_durable_settled_status) is doubly retired: upstream
   removed the symbol (fail-soft equivalent lives as
   cancel_intents.settled_status).
3. Q-b=A: rows 2041-2044 (queue.py -> supervisor/queue_evolution.py) are
   RESOLVED WITHOUT the reference leaf. Upstream itself moved
   _deliver_pending_owner_report and enqueue_evolution_task_if_needed into
   supervisor/evolution_lifecycle.py; get_evolution_status_snapshot and
   queue_deep_self_review_task stay on the queue facade by owner decision
   (do not fork the evolution-family ownership a second time).
4. Q-c=A: row 970 EXECUTED — _close_campaign_after_owner_stop moved to
   supervisor/queue_transitions.py (byte-identical span; the drift probe
   proved tip==oracle here), events.py re-exports it, and
   events_evolution_done reads it through the _events() handle (no bare
   local name survives the split). queue_transitions.py entered the
   1001-1500 band with a rationale and joined HOT_CODE_PATHS (parity: the
   span moved out of the hot events monolith).
5. Rows 971-979 (events_task_done + events_evolution_done), the cancel
   ingress row (file row 994 / D08-ledger row 992), rows 2017-2028
   (queue_snapshot + queue_timeouts) and rows 2061-2064/2077-2079
   (worker_health + worker_assignment) EXECUTED as reference-named leaves
   from tip bytes. Declared sets are MAXIMAL (wave-2 dead-patch lesson),
   larger than the oracle's: every parent global the spans read at call time
   routes through the handle, including same-leaf reads
   (_PROVIDER_DEATH_NOTIFIED — tests rebind it on the facade), the
   cross-family coop hooks (_checkpoint_coop_roots_on_root_done and
   _maybe_checkpoint_coop_on_tree_quiescence: the GR4-3 probes patch them on
   supervisor.events — a module-scope import here is the dead-patch class the
   first emit reproduced and the re-emit fixed), `time` (the
   enforce-harness in test_packaged_runtime_and_lifecycle rebinds
   events.time), `_bound_project_chat_id` (the terminal-frame delivery tests
   rebind it on supervisor.events — a MULTI-LINE setattr the first
   single-line patch-surface grep missed; the closing sweep is an ast.walk
   over every tests/*.py catching setattr in any form through module
   aliases) and `BUDGET_ROOT_FENCES` in queue_snapshot (tests rebind it on
   the queue facade while persist_queue_snapshot reads it at call time; the
   pre-split span read queue's own re-export binding). Facade imports that
   now serve ONLY leaf handle reads carry per-line noqa markers naming the
   leaf.
6. Delta-D08 RE-DERIVED on tip bytes (Q-d=A): mark_finalize_control_drained,
   mark_intent_scope, release_claim and settle_intent now read the projection
   strict (_load_intents(strict=True) + strict_existing_dict=True) and turn
   the typed ValueError into CancelIntentProjectionCorrupt via the
   _refuse_corrupt helper (oracle shape); the tip GR5-6 docstring that
   RATIONALIZED fail-open ("non-minting mutators find no row in {}") is
   deliberately rewritten — that was the semantic delta, not a drift.
   Upstream's own strict sites (request_cancel, claim_intent, active_intents)
   keep their tip bytes. Caller audit (every tip call site, what happens on
   raise): (a) task_lifecycle._settle_intent/_release_intent_claim wrappers —
   except Exception, log.debug: the intent stays OPEN/CLAIMED for the
   watchdog; (b) task_lifecycle cancel_task_by_id cascade postcondition —
   outer except, cascade intent stays open, watchdog re-runs the cascade;
   (c) task_lifecycle record-cascade-scope site — except Exception with
   log.warning + typed cascade_scope_record_failed forensic row (loud, second
   line of defense); (d) ouroboros/task_results.fail_tasks budget drain —
   both settle and release wrapped, log.debug, intent stays for the watchdog;
   its claim path already maps a raise to claim_refused and skips the task;
   (e) workers pending-drop lanes (_settle_cancelled_pending_row,
   _release_pending_claim, terminalization retry) — except Exception ->
   claim_unresolved -> the row is RETAINED in the terminalization-retry lane,
   nothing silently dropped; (f) owner_stop._mark_owner_stop_control_drained
   — outer except returns False: no drain stamp, the finalization episode
   stays bounded by the unstamped request anchor (a corrupt projection can
   not buy an unlimited final turn). No caller needed a code change; the pin
   is tests/test_cancel_intent_corruption_s6.py (C1/C2), re-keyed to the tip
   bool contract of release_claim (upstream fence-proof return; the oracle's
   `is None` clauses would pin a retired signature).
7. S7b split RE-DERIVED from tip bytes (rows 2152-2223): lossless — 107
   test functions / 112 expanded items before == after, zero duplicate
   names, all green. The oracle partition is honored row-by-row for every
   surviving name; tip-new (bea08137-class) objects were placed by theme and
   these MINTED rows are: retry-race custody family
   (_patch_retry_input_handoff, _root_retry_task,
   test_retry_cancel_before_admission_publishes_no_successor,
   test_retry_admission_before_cancel_canonicalizes_and_stops_leaf,
   test_retry_leaf_cannot_escape_a_logical_root_cascade_at_final_boundary,
   test_cancel_suppressed_retry_task_done_waits_for_summary_obligation,
   test_timeout_precheck_yields_retry_leaf_to_logical_root_cascade,
   test_retry_boundary_refuses_missing_physical_leaf_authority,
   test_terminal_retry_leaf_wins_even_when_predecessor_lineage_is_corrupt,
   test_terminal_before_retry_boundary_creates_no_scheduled_ghost,
   test_same_id_timeout_retry_cancels_exactly,
   test_retry_leaf_completion_between_request_and_custody_wins,
   test_graceful_single_retry_targets_leaf_and_stop_now_hardens_same_intent,
   test_task_lifecycle_keeps_scheduled_admission_import_surface,
   test_task_lifecycle_keeps_capture_miss_calling_convention)
   -> tests/test_cancel_custody.py; dispatch-authority family
   (test_assignment_blocks_when_cancel_intent_projection_is_unreadable,
   test_assignment_retains_pending_when_claim_authority_raises,
   test_timeout_reaper_does_not_clone_over_unreadable_cancel_authority,
   test_snapshot_restore_blocks_when_cancel_intent_projection_is_unreadable,
   test_cancel_authority_hold_never_releases_a_terminal_row_to_dispatch,
   test_preserve_pending_shutdown_keeps_cancel_authority_hold_nonterminal,
   test_drop_cancelled_pending_retains_custody_until_task_done_is_published,
   test_drop_cancelled_pending_releases_a_failed_intent_claim,
   test_drop_cancelled_pending_does_not_assume_settled_when_settle_helper_missing,
   test_drop_cancelled_pending_defers_when_intent_vanishes_before_settle)
   -> tests/test_cancel_queue_integration.py; durable-gate additions
   (test_blank_status_task_done_over_a_running_row_is_a_durable_fault,
   test_blank_status_task_done_over_a_settled_row_is_admitted,
   test_copy_back_exception_never_synthesizes_a_completed_row)
   -> tests/test_cancel_task_done_validation.py; projection-primitive
   additions (retry-lineage mint family rows 82-238 of the monolith,
   test_claim_intent_refuses_an_existing_corrupt_projection,
   test_claim_intent_absent_projection_is_a_read_only_miss)
   -> residual tests/test_cancel_intents_phase_a.py; and
   _write_root_retry_pair joined tests/_cancel_intents_shared.py (read by
   both the mint suite and the custody retry suite — a tip extension of the
   shared set, rows 2152-2155 class). The monolith's section-banner comments
   are not carried (the same inter-span-comment loss the D14 lane recorded
   for the emitter). tests/test_cancel_cascade_v664.py's source-scan clause
   retargeted to the owner leaf (events_task_done) and its now-unused facade
   import dropped.
8. Durable pins landed with tip re-keys: tests/test_e2e_cancellation_scenarios.py
   + tests/fixtures_e2e_cancellation.py (E-suite; the driver extensions —
   typed cancel_task with cascade/stop_policy, hurry_task, _api_status —
   ported into devtools/benchmarks/common/server_runner.py, options-free
   cancel keeps the legacy empty-body wire shape for the existing benchmark
   callers); E8 is RETIRED and superseded by E13 (F6 disposition, owner
   Q9=A/Q10=A: a budget-drained queued task PAUSES — durable scheduled
   result with reason_code=budget_exhausted plus the typed
   budget_scope_paused event — it is not failed); C5/R1 were already on tip
   (D09 quiet edge); tests/test_cancel_protocol_inventory_s6.py (C7-C10)
   re-keyed by symbol to the tip owners (settle-owner cluster in
   task_lifecycle, miss lane in cancel_publication, admission in
   task_admission, the F2.2 leaves) with the upstream retry/depth terminal
   lanes ADDED to both the C7 manifest and the no-deliverable enumeration;
   C9's task_finalization docstring (row 1093) corrected to the VERIFIED tip
   call order (emit_task_results registers the owed row, then stores).
9. Mock-lane execution proof (post-commit verification, then amended in):
   the eight mock scenarios (E4-E7, E9-E12) ran GREEN against a real isolated
   server on this exact tree — after ONE harness adaptation of the class the
   suite's own docstring predicts: upstream delegation-by-construction makes
   subagent selection explicit (`subagent_configuration_unsaved` /
   `subagent_selection_required`), so isolated_settings() now pins a saved
   one-row Available-subagents roster (api_model on the lane's own slug) and
   the stub's spawn turn passes subagent_id="mock-scout". Scenario semantics
   untouched; the same class the deferred
   test_daemon_token_containment_s6.py note in the matrix recorded.
10. tests/test_v7next_transplant.py queue probes re-pinned to the PRE-SPLIT
   monolith bytes of this lane's base (git show 2878560e:supervisor/queue.py)
   with the landed-leaf inverse-normalization fallback — the D01/D10 probe
   recipe.
11. Path-keyed mirrors updated in the same commit: HOT_CODE_PATHS gained the
    four F2.2 leaves + queue_transitions; test_contracts' literal
    progress_meta scan gained events_runtime_controls + events_task_done;
    test_heartbeat_presentation's message-seam scan gained the two worker
    leaves; tests/test_events_extraction.py flipped from the pinned
    partial-split work order to the completed shape; the five satisfied
    [split_pending]/[split_pending_leaves] rows left scripts/v7next_domains.toml
    with the two owner-retired leaves recorded in a comment.

## From the F2.3b review-semantics lane (base dcf8dd4b, 2026-08-31)

1. F5 leaf-name mint (advisory split): the reference leaves
   `ouroboros/tools/review_advisory_prompt.py` / `review_advisory_run.py` are
   NOT the landed names. The drift probe (`--check` of both reference leaves
   against `git show dcf8dd4b:ouroboros/tools/claude_advisory_review.py`)
   byte-falsified the organ's semantics: prompt leaf 4/5 rows byte-true with
   `_build_advisory_prompt` falsified (governance_by_retrieval pointer form);
   run leaf 10/18 byte-true with `_run_claude_advisory`,
   `_run_advisory_delegated`, `_llm_extract_advisory_items`,
   `advisory_review_route`, `advisory_slot_enabled`-adjacent route/gate
   projections falsified (native episode + reviewer-slot SSOT replaced the
   Claude-SDK transport). Landed as `preflight_review_prompt.py` /
   `preflight_review_run.py` — the organ's public rename vocabulary (Q1) —
   cut from tip bytes, tool proof green on every symbol.
2. Advisory row dispositions against the 30 ledger rows (3852-3881):
   23 transplanted-from-tip (5 prompt + 18 run); 3 SUPERSEDED —
   `_release_metadata_preflight`, `_auto_sync_release_metadata_if_needed`,
   `_syntax_preflight_staged_py_files` live with upstream's
   `ouroboros/commit_admission.py` (Q3=A SSOT; the parent keeps the alias
   monkeypatch seams, pinned by
   test_review_owner_facades.test_the_deterministic_preflights_live_with_commit_admission);
   4 RETIRED with the SDK transport — `_changed_paths` (upstream's
   `review_helpers.parse_changed_paths_from_porcelain` class),
   `advisory_route_requires_api_key`, `_advisory_session_deltas`,
   `_advisory_sdk_budget` (no tip bodies exist; not replayed).
3. Scope budget probe corrections (mandate: re-verify the matrix's
   6-superseded/1-retired): the reference leaf probed 7/8 byte-true against
   tip; ONE row falsified — `_SCOPE_REVIEW_SLOT_TIMEOUT_SEC` (reference `900`,
   tip `None`: the adaptive-timeout contract retired the constant; tip byte
   kept). The matrix called `_SCOPE_BUDGET_TOKEN_LIMIT` retired (#383) — the
   probe shows the NAME alive on tip as a private alias of
   `review_helpers.REVIEW_PROMPT_TOKEN_BUDGET` (the reference-era standalone
   constant is what died); it moved with the other five owner aliases
   (`_SCOPE_MODEL_DEFAULT`, `_SCOPE_FAILCLOSED_WINDOW`,
   `_SCOPE_MODEL_CONTEXT_WINDOW`, `_shared_window_scaled_reserves`,
   `_calibrated_input_token_limit`) plus `_is_provider_oversize_error` into
   the budget leaf per the ledger's rebind disposition, parent re-exports —
   import-frozen on both sides exactly as before the split, so no
   patch-visibility change.
4. D31 port (owner decision 5.1=A): `_run_on_trusted_base` re-derived from
   the reference (scripts/run_external_review.py:539 @ 9f691656) onto the tip
   script — the contributor lane now ALWAYS executes the target base's own
   review machinery (self-re-run from a detached base worktree with pinned
   base/head SHAs). `_REVIEW_SUBSTRATE_PATHS` demoted to EVIDENCE ONLY
   (`review_substrate_changed` packet diagnostic), never a gate:
   `_contributor_result` is exit-code-only (reference form), and
   `finalize_contributor_outcome` dropped both the `snapshot` parameter and
   the `trusted_base_rerun_required` downgrade (reference form; its one
   script call site and two test call sites re-derived). The fail-closed
   `INCOMPLETE_MAINTAINER_TRUSTED_BASE_RERUN_REQUIRED` vocabulary survives on
   the ONE non-portable path — a target base whose tree carries no review
   wrapper (new guard; the reference would have misfiled python's exit 2
   there as "empty diff").
5. D31 pin suite ported from the reference (probe script, handoff/forwarding/
   in-place/dirty/e2e pins) with tip reverse-mapping: the seeded repo stubs
   `ouroboros/openrouter_attribution.py::OPENROUTER_APP_HEADERS` (the tip
   wrapper's module-level import) where the reference seeded
   `runtime_mode_policy::GIT_OPS_FAMILY_PATHS` (its wrapper's import). NEW pin
   beyond the reference: the always-runs-on-base parametrization includes
   `ouroboros/review_native_episode.py` — a review-machinery module ABSENT
   from the evidence hand-list — plus a fail-closed pin for the wrapperless
   base. Disclosed test replacement: the old gate clause
   (`test_contributor_outcome_fails_closed_on_receipt_or_trust_drift`'s
   substrate-downgrade half) asserted the hand-list AS a gate — exactly the
   semantics the owner retired — and is replaced by the reference's
   receipt-drift-only pin plus `test_contributor_result_is_decided_by_the_exit_code_alone`.
6. Path-keyed mirrors in the same commit: `_REVIEW_SUBSTRATE_PATHS` (evidence
   list) and `review_context_atlas._REVIEW_STACK_PATHS` gained the three new
   leaves beside their parents; domains.toml gained the three D06 leaf rows
   and cleared both satisfied [split_pending]/[split_pending_leaves] entries
   (review_execution's row left untouched — matrix A2 marks it superseded by
   upstream's review_verdict_extraction, an integrator decision, and this
   lane's mandate excludes review_execution).

## From the integration seam (coordinator, F2 close-out, 2026-08-31)
1. split_pending row `review_execution.py -> review_session_verdict.py`
   retired as SUPERSEDED-BY-UPSTREAM (matrix D06 verdict A2, confirmed by the
   F2.3a lane): upstream performed the same extraction itself as
   review_verdict_extraction.py; the reference leaf name never materializes.
   The F2.3b lane left this disposition to the integrator - recorded here.
2. SUPERSEDING note to the F2.3b lane's atlas claim: the two advisory leaves
   entered _REVIEW_STACK_PATHS with the F2 close-out conformance fix, not
   with the lane commit (the lane's ledger entry overstated); a membership
   pin now accompanies them.
3. Cross-test fragility class (found by loadscope redistribution after the
   close-out fixes): supervisor.queue globals (PENDING/RUNNING/...) are
   rebound by init_queue_refs across ~35 upstream test sites with no restore
   - an upstream-wide convention, not to be mass-rewritten. READER-SIDE RULE
   for campaign pins: never assume those globals are empty; REPLACE the dict
   for the test's scope (monkeypatch.setattr), never append into the live
   one. Applied to test_both_custody_surfaces_see_the_same_live_task_set.

## From the f30 lane (F3.0 opening train, base db944347, 2026-08-31)
1. ABI-6 re-location on tip (the roast-session scratchpad that minted the P1
   inventory did not survive; the surviving primary source is
   V7NEXT_SYNTHESIS_DRAFT.md, which names items without addresses):
   (a) `_call_llm_with_retry` alias re-located at ouroboros/loop.py:74 -
   ZERO code readers on db944347 (every monkeypatch targets the public
   name); removed. (e) `compute_cost_with_children` (task_status.py:1001) +
   `format_handoff_message` (:1054) - zero production callers; the canonical
   with-children rollup lives in agent_task_pipeline/post_task_synthesis
   with cost_projection.py as projection SSOT; removed with their private
   helper and tests. (zh) "CHECKLISTS:507" re-located: the line number
   drifted on both b9f7597f and db944347; the actual finding (archive,
   sol audit 30.08) is the env_allowlist checklist row claiming
   TELEGRAM_BOT_TOKEN is in FORBIDDEN_SKILL_SETTINGS while
   contracts/plugin_api.py:23 does not contain it - doc aligned to code
   (10 keys), code deliberately unchanged.
2. ABI-6 items NOT re-locatable on tip - recorded, NOT replaced by
   invention (f3 plan instruction): "failure-detector compat wrapper" and
   "3 underscore renames". Evidence of the sweep: compat/alias comment grep
   across ouroboros/ (12 candidates read - none is a failure-detector
   wrapper); AST scan for one-line delegating wrappers with
   fail/retry/error/detect/classify names (single hit:
   git_review_cycle._handle_revalidation_failure, which is the D18/D33
   module-handle idiom, not a compat shim); targeted reads of
   llm*/loop*/transport modules. Disposition: superseded-by-upstream inside
   the ABI-6 row; a future lane finding the real item re-opens it with
   bytes, not memory.
3. ABI-5 execution corrections against the f3 plan text:
   - The two ws5 "read exemption" tests are NOT floor tests but family
     read-carve mechanism tests that used the floor detector as vehicle;
     deleted only the detector's own test, RETARGETED the two mechanism
     tests to the surviving `_detect_safety_mode_self_lowering` (same
     composition through `_owner_control_mention_blocks`).
   - `effective_max_improvement_passes(has_deadline=)` existed solely for
     the until_deadline count-axis branch; the parameter was removed with
     the alias (callers: task_results wrapper + wallet cap + rails line;
     BudgetSnapshot.has_deadline and every TIME rail untouched).
   - The wallet-authority test derives its uncapped lane from
     OUROBOROS_REVIEW_MAX_CYCLES=unlimited now (the alias lane is gone);
     v664's deprecation-noise test now pins that resolve_budget_profile
     emits NO deprecation events at all.
   - Bench adapters (programbench, swe_bench_pro) switch to
     improvement_policy=fixed: behavior-identical because their explicit
     max_improvement_passes=6 was always the binding count axis.
   - Ratchet: tests/test_v664_acceptance_planning.py briefly crossed the
     1001 band (1005 lines) after a test rewrite - shrunk back to 996
     instead of minting a band rationale; BYTE_DEBT for
     tests/test_devtools_benchmarks.py regenerated 327935->327888
     (reduction) by the official generator.
4. Disclosed consequence (rides ABI-2/Q8=B): a pre-7.0 stored root contract
   whose normalized profile says until_deadline is judged malformed by the
   acceptance-wallet authority (pre-existing unknown-policy behavior);
   pre-7.0 task-result history is quarantined wholesale by ABI-2 in the
   same release and the ABI-7 RC auditor names the migration.

## From the f31b lane (extensions, base 29e2b045, 2026-08-31)

1. Plan line-ref drift, re-verified on base bytes: the supervised-future
   leak pinned as "extension_plugin_api.py:459-466" lives at :460-466 on
   29e2b045 (future minted at :460, the second `_require_open_locked`
   re-check at :461-462). The leak itself is REAL and was reproduced red
   by the direct regression test
   (tests/test_extension_registration_atomicity.py::
   test_supervised_future_never_leaks_when_unload_wins_the_registration_race)
   before the ABI-9 fix: the factory ran despite the refusal.
2. ABI-9 semantic tightening, disclosed: `on_unload` callbacks registered
   during a FAILED registration are no longer executed on abort (on the
   base they ran via unload_extension because the bundle pre-existed the
   register() call). Staged side effects (event-bus subscriptions,
   supervised runners, companion spawns) are disposed/never-started
   instead; on_unload fires only for a published extension. No test on
   the base pinned the old failed-register callback behavior.
3. FORBIDDEN_EXTENSION_SETTINGS reader refs from the f3 plan
   ("extension_plugin_api.py:513/:664") re-located on base bytes to
   :513 (companion env filter) and :664 (get_settings protected set) —
   both verified before the ABI-1 alias collapse.
4. ABI-1 execution notes (owner-ratified design + batch №6 answers):
   - Admission timing: the ratified text anchors the predicate "at NEW-PASS
     issuance"; the lane evaluates it EAGERLY, after the $0 free-replay gate
     and BEFORE the paid panel dispatch — no outcome of a dispatched panel
     could mint a PASS for an inadmissible payload, so dispatching would only
     burn reviewer money. Byte-identical re-review of grandfathered bytes
     still free-replays the recorded PASS first.
   - Reload-aggregation hole found and closed: a persisted
     plugin_api_admission FAIL finding re-aggregated to WARNINGS (executable!)
     on load_review_state; aggregate_skill_review_status now treats it as a
     structural gate like skill_preflight (PENDING under every enforcement).
   - Preflight infra failures now fail closed WITHOUT persisting (a transient
     breakage must not clobber live review state); genuine payload gate
     failures keep persisting PENDING as before.
   - 6.2=A scope note: the declarative dependency fingerprint is enforced on
     the extension liveness path (deps_declaration_desync). Script-skill deps
     flow through the same read_deps_state/specs-hash gates but their
     readiness callers are outside this lane's files — residual disclosed for
     the RC auditor (ABI-7) inventory.
   - launcher_bootstrap plan ref ":565-579 resync grants" re-located: the
     grant-carry seam landed as _carry_grants_across_reseed called from
     _reseed_native_skill_in_place (the :565-579 span on 29e2b045 is
     _stamp_native_seed_trust's docstring).
5. Test pinned to the pre-2.0 contract, updated with disclosure:
   tests/test_native_seed_trust.py seed fixtures wrote type=extension seeds
   WITHOUT the plugin_api field and asserted the native-trust stamp — under
   ABI-1 that stamp is correctly refused. The fixtures now declare
   plugin_api: "2.0" (matching real bundled seeds); the field-less refusal
   itself is pinned in tests/test_plugin_api_admission.py::
   test_native_seed_trust_is_closed_to_fieldless_extensions.
## From the f31c lane (F3.1-C schema/updater, base 29e2b045, 2026-08-31)
1. ABI-2 reader seam widened beyond the plan's single address: the plan named
   `load_task_result` (:665) as THE reader, but the sibling
   `list_task_results` feeds UI/recent (gateway/tasks.py:796) from the same
   rows - quarantine is implemented at BOTH, batched per scan. Direct
   observational globs (server_routing_context.py:207, gateway/tasks.py:803)
   are deliberately untouched: after the first swept read they see nothing,
   and touching them would be compat machinery Q8=B forbids. The quarantine
   subdirectory is invisible to every `*.json` glob (non-recursive).
2. Plan section 7 item (3) "ONE durable event / chat notice per batch" is
   superseded by the batch-6 answer 6.3=B: visibility is the durable events
   log ONLY - one `task_results_quarantined` row per read/scan batch, no UI
   counter, no chat notice (pinned by a no-chat-jsonl test). The move itself
   is the dedupe: a row can appear in exactly one batch ever.
3. Writer-inventory correction against "writers stamp": five writer sites
   exist on tip, four stamp (write_task_result, the acceptance-state and
   plan-review merge-writers in task_results.py, the owner_hurry projection
   writer). The cancel-receipt amend-writer
   (supervisor/terminal_delivery.py:1284) deliberately does NOT stamp: it
   never creates a row, its dict-copy merge preserves whatever stamp the row
   carries (so no downgrade path exists), and the module sits exactly at the
   1500-line band edge - a stamp there is correctness-redundant. Disclosed
   residual: a pre-7.0 row whose ONLY post-upgrade write is a cancel receipt
   stays unstamped and is later quarantined with its receipt - consistent
   with wholesale pre-7.0 quarantine (f30 entry 4).
4. Module-size: the ABI-2 machinery lives in a new leaf
   `ouroboros/task_result_schema.py` (task_results.py re-exports; callers
   and tests import through the facade). Inlining it drove task_results.py
   to 1592/1600 against the hard cap; after the split the ratchet manifest
   is byte-identical to the base (task_results.py 1465, band entry kept).
5. Disclosed interaction: `restore_pending_from_snapshot` probes each
   snapshot-pending task's result with `load_task_result(strict=True)`
   (queue_snapshot.py:305). A pre-7.0 unstamped row now raises there, so the
   task is terminalized through the existing result-authority custody path
   instead of being revived - the N-1-snapshot restore of pre-7.0 tasks
   degrades fail-closed, consistent with Q8=B wholesale quarantine.
6. Strict-path contract stability: for MALFORMED rows the pre-ABI-2 strict
   messages are kept byte-stable ("task result authority is unreadable or
   invalid" / "task result is unreadable or invalid" - test_review_cycles
   pins the former); schema refusals raise the new typed message with
   reason=quarantined_schema. Strict reads never mutate storage: an
   authority probe is not allowed to be the mover.
7. ABI-7a: `read_update_tx_strict` grew the fourth status `"future"`
   (integer stamp above ours; raw tx returned as evidence). Full strict
   caller sweep on the base: update_merge internal consumers fail closed via
   existing `!= "valid"` branches; `update_tx_phase` raises the typed
   refusal without writing; `_safe_restart_serialized` defers the restart;
   git_ops_reset.py:326 keeps `tx_matches` false and clears the orphan
   intent (fail-closed). A NON-integer stamp reads `corrupt` (evidence kept
   on disk, `{}` returned) - only a genuine newer-release stamp is `future`.
   An unstamped marker stays `valid`: that IS the N-1 transition contract.
8. F2.4 boot-finalize family untouched byte-wise except the dispatch
   docstrings and the future branch in `finalize_managed_update_on_boot`;
   the carrier-conflict crash floor (F2.4 ledger entry 4) keeps its existing
   pins - the shim suite adds the N-1 byte-form fixtures for every phase
   seam plus the marker upgrade-on-first-rewrite assertion.
9. Fixture sweep: 9 test files hand-writing task-result rows as
   current-version writers now stamp them (acting_subagents, presence_tools,
   tasks_list_slice, headless_task_events, context_drive_state,
   gateway_history, host_service_api, plan_review_public_projection - plus
   the new F12 suite writes both forms deliberately).
## From the f31d lane (F3.1 polosa D: ABI-3 -> ABI-10, base 29e2b045, 2026-08-31)

1. ABI-3 F11 inventory frozen BEFORE the first removal in
   docs/v7next/ABI3_GATEWAY_ALIAS_INVENTORY.md (the RC-auditor feeder). Key
   falsification against the plan text: the plan named api_types.js as a
   removal surface, but the lane constraint (web/ untouchable; chat.js at its
   BYTE_DEBT ceiling) plus the JS evidence made it a NON-surface: no alias
   has a functional JS reader (`resolveCostPair` falls back to the honest
   name; telegram/prefs aliases are JSDoc-only), so NO alias was HOT-DEFERRED
   - only the stale JSDoc typedef lines and the GATEWAY_CONTRACT_VERSION
   carrier switch are deferred, and tests/test_gateway_parity.py excuses
   exactly that frozen extra-set and nothing else.
2. Cost-alias removal is CLASS-level at the SSOT seams (cost_projection.py
   emitters strip the retired spellings; read tolerance + deprecated-wins
   precedence for stored pairs kept verbatim). Consumer fixes rode along in
   files outside the polosa-D list (disclosed cross-lane touches):
   agent_task_pipeline.py, supervisor/events_task_done.py (polosa C
   neighborhood - different hunks from C's ABI-2 seam at state.py:198/
   task_results.py:665/723; task_results.py itself was NOT touched),
   post_task_synthesis.py + synthesis_cost_text.py (pre-synthesis snapshot
   and prompt renderer moved to the honest with_children name - the snapshot
   feeds task_summary chat rows, i.e. gateway egress), tools/recent_tasks.py,
   tools/control_task_results.py.
3. Cross-lane invariant relied upon (for the coordinator's integration
   check): the "stale stored cost_usd beside a fresh honest name after a
   post-upgrade merge-write" class is consumed by polosa C's ABI-2 Q8=B
   quarantine (pre-7.0 unstamped records never reach the merge path). Within
   this lane's own tree the class is test-visible only via records written by
   current code, which now write honest names.
4. Ingress validation (Q7=A) is inbound-only by test-pinned design: history
   replay is egress and gateway/history.py must never import validate_ingress
   (pinned). PEP 563 falsified `__required_keys__` on Python 3.10 (string
   annotations make every total-class key read required, ExecutorRef lost its
   Required["type"]) - requiredness is re-derived from resolved hints +
   per-class totality in gateway/schema.py.
5. UpdateApplyRequest.strategy: the runtime silently defaulted a missing
   strategy to auto_merge while the contract declares it REQUIRED; the
   executable schema now enforces the contract as written (web client always
   sends it; no test posted a bare body expecting the default).
6. ABI-10 default panel deliberately resolves through
   get_review_models()/get_scope_review_models() (derived env plane), NOT a
   static list: preserves the identical review models for every config class
   (shipped defaults, single-direct-provider adaptation, bench env overrides)
   - "review models change nowhere". The SETTINGS-plane comma keys die
   (RETIRED_SETTING_KEYS, ghost purge); a comma-only-settings install gets
   the default panel exactly as ratified (5.4=A).
7. server_runtime.apply_runtime_provider_defaults was a settings-plane WRITER
   of the retired comma keys (direct-provider path INTRODUCED them; the
   prior-scope-default migration would KeyError post-retirement): it now
   normalizes only values it is fed and never introduces a retired key; the
   read-time getters own the direct-provider review adaptation (pinned per
   provider by new read-time tests). model_slots' singular->plural promotion
   removed (dead after purge; both files outside the polosa-D list -
   disclosed).
8. Bench templates (continual_learning, gaia, swe_bench_pro x4) migrated to
   structured OUROBOROS_REVIEWER_SLOTS with byte-identical model sets; the
   comma keys were dropped from all bench settings JSONs. NOT touched:
   operator_patches/*.patch (append-only artifacts) and the env-plane
   forwarding lists (server_runner/manifests/cybergym_lifecycle) - the env
   spellings remain the legitimate derived/operational plane.
9. Removed-with-evidence test clauses (the "test pinning the bug" class):
   test_gateway_parity's ChatOutbound cost_usd JSDoc pin; the legacy
   migration tests of reviewer_slot_config (phase-5 route envs, session-row
   migration, legacy advisory materialization) replaced by
   retired-envs-are-ignored pins; test_git_review_bypass_gate's
   "unroutable enabled session advisory" state is UNREACHABLE by construction
   post-ABI-10 (the parser refuses an enabled session advisory without a
   concrete target at save AND load) - the defensive fail-open branch stays
   covered via a synthesized config in test_skill_advisory_pre_review.
10. Residuals disclosed (NOT executed, outside the named scope): the
    phase-5 env reads that survive in review_substrate.scope_reviewer_slots
    (route_env_key plumbing for explicit-models callers) and the
    OUROBOROS_ADVISORY_REVIEW_ROUTE mentions in
    preflight_review_run/claude_advisory_review prose/vocabulary - env-plane
    remnants, candidates for the F3.3 sweep extension; the JS-side typedef
    cleanup + GATEWAY_CONTRACT_VERSION carrier switch (web lane).
## From the F3.1 lane A (typed organ, base 29e2b045, 2026-08-31)
1. extension_dispatch.py typed dispatchers (D04 entry 5, rows 187/188) ADOPTED
   WHOLE from the reference WITH BYTE PROOF: the tip file, the merge-base
   (8028f1df) file and the v6.64.0 file are md5-identical (4e9ad3ba…), so
   reference == tip + delta exactly (the same adoption class the lane used for
   mcp_client.py). The ToolRegistry methods `_dispatch_extension_tool` /
   `_dispatch_mcp_tool` and the hoisted `_extension_dispatch_candidate` retire
   from registry_core; call sites read the module handle. The unknown-name
   answer for a registered-but-not-live extension is typed
   EXTENSION_UNAVAILABLE (the D02 liveness bit); the truly-unknown name keeps
   the tip's alias-filtered legacy text (tip drift the oracle lacks). The
   `failure_kind` delta on extension_process_runner (unrowed in MIGRATION,
   named by D14 entry 10 as Ф3 territory) lands here: `ExtensionProcessError`
   gains the kwarg, only the deadline kill raises `failure_kind="timeout"`.
2. D09 typed-policy-refusal subfamily (D02 entry 4): the five ladder bodies
   drifted upstream after the fork (`plan_next_wire_retry` state machine,
   request-wire custody, effort-clamp discard rules), so the reference deltas
   were RE-DERIVED onto the tip structure: the three planning rungs decline a
   refusal; the retries twins raise it out of the bounded state machine before
   any wire-retry planning and out of the reroute/strip body-error arms instead
   of absorbing it into the first errored response, discarding the pending
   effort-clamp note on each raise path (tip custody rule the reference
   predates). classify_llm_exception branch inserted before the prose
   heuristics, after the tip's provider_code read (the tip computes
   provider_message/classification_text the oracle lacks — refusal outranks
   them). tests/test_llm_typed_policy_refusal.py carried whole: 25 passed with
   zero adaptation. Goldens: the two typed_policy_refusal cases returned to
   fallback_ladder.json (15 -> 17) with `expected` RE-RECORDED from this
   tree's live code via the suite's own --write entry; the write left all 15
   existing cases untouched (append-only diff = no accidental drift), and both
   recorded blocks carry the oracle-intended semantics (refusal raises; the
   exception case spends exactly one physical send).
3. Reference test adaptations, each disclosed in-file at the non-verbatim
   spot (reverse-mapping rule §5.3-Δ item 2):
   - registry facade: the reference pins an exact-32-name minimal facade; this
     tree deliberately keeps the broad historical import surface, so the pin
     is re-derived as an AST "the facade module DEFINES nothing but the
     disclosed read-carve helper" plus owner-leaf homing/retirement asserts
     (test_registry_core, test_registry_guard_process ×2).
   - guard collaborator patch points follow this tree's `_registry()`
     call-time-handle idiom (protected_artifact_shell_block_reason,
     workspace_executor_state_write_block, build_resolved_resource_binding,
     resolve_shell_cwd, shell_cwd_block_message, system/active_repo_dir_for,
     light_shell_repo_mutation, runtime_data_guard_targets,
     workspace_git_safety_violation, run_shell_git_block_reason, run_cmd for
     git_vcs_ops); the reference's `shell_has_write_indicator` seam does not
     exist here — the tip write-shape seam is `non_interpreter_write_shape`.
   - the managed-update resolver pin re-targets the tip's TYPED
     `authorized_assisted_task_strict` (adds the corrupt-marker A4-channel
     clause the reference could not know).
   - SCOPE_REVIEW_FLOOR rows (detector signature, denial text, code-contract
     row, precede-safety parametrization) removed: the setting, guard and code
     were retired by ABI-5 (owner Q10=A) in F3.0.
   - detector-family signatures pin the tip's whole-family `writeish`
     read-carve; three constant cardinalities follow upstream drift
     (secret markers 17->18, denied read options 11->12, owner-state stems
     12->14); `_workspace_shell_write_block` pins the upstream
     `write_target_argvs` parameter.
   - plan-review pins: the reference's sync-side `_record_raw_plan_request_attempt`
     and the vacuous-note wrapper (`_reuse_or_disposition_plan_review`,
     `_VACUOUS_*_NOTE`) do not exist on tip — the parametrized wrapper test is
     re-derived over the tip's three projection paths (review mode, vacuous
     disposition fall-through, `_apply_disposition`).
   - `_parse_plan_review_control` readers (test_plan_spec, test_plan_review,
     plan_spec docstring) re-point to its new home tools/plan_render.
   - two loop fakes (test_openai_chat_dispatch._FakeTools,
     test_owner_hurry_s3._ProbeTools) gain `execute_result` adapting their
     text exactly as the registry adapts a legacy handler — the loop now reads
     the typed seam.
4. Reference DELTA re-applied, not replayed: the plan handler's pool hop wraps
   `asyncio.run` in `contextvars.copy_context().run` so the sidecar
   publication reaches the dispatching thread's slot; the reference's
   surrounding `asyncio.wait_for` wrapper-timeout machinery is NOT reproduced —
   the tip deliberately retired the nested wait (its comment explains the
   cancel-then-block hazard), and replaying the span verbatim would have
   reverted that decision (re-prove-trap class, D15 entry 3).
5. Protection closure: registry_core.py + tool_result.py membership in
   SAFETY_CRITICAL_PATHS/HOT_CODE_PATHS verified landed with the re-split
   commit (parity pin green); extension_dispatch.py — already safety-critical —
   JOINS HOT_CODE_PATHS here because the dispatch bodies moved onto it from
   the hot ToolRegistry class (the same parity rule; oracle carries the same
   membership), and the parity pin now lists it.
6. Owner 6.1=A edge checked and NOT implicated: lane A changes no
   admission/review/PASS semantics — the extension liveness refusal existed on
   tip and is only retyped (EXTENSION_UNAVAILABLE), so the auto_review=false
   contract (no PASS issued, nothing blocked) is untouched by the typed organ.
7. Function-size law: the EXTENSION_UNAVAILABLE branch pushed
   `_execute_legacy_text` to 303 lines; resolved by extracting the
   module-level `_unknown_tool_result` helper (behavior identical), not by a
   band exception. tests/test_tool_result.py enters the 1001-1500 band with a
   rationale via the official regenerator.
## From the F3.1 conformance fix-round (base 9edb9199, 2026-08-31)

Dispositions for the six blocking findings of the Ф3.1 conformance review
(GPT-5.6 Sol, read-only, range 29e2b045..9edb9199). One finding per entry;
every closure carries its pin.

1. ABI-9 ordering (finding 1) — FIXED. `_publish_registrations` is now
   validate -> effects -> swap under ONE `_lock` hold: the definitive
   unload/conflict validation runs BEFORE any deferred side effect
   (supervised runners, companion spawns, bus subscriptions), and the swap
   follows in the same critical section, so no concurrent unload/conflicting
   publication can interleave anywhere between the three steps (both mutate
   only under `_lock`; CompanionSupervisor uses its own lock and never takes
   the registry lock — checked). Event subscriptions are STAGED
   (`_StagedEventSubscription`; `EventBus.subscribe` accepts a pre-minted
   sub_id so the id returned by `subscribe_event` is the id the bus attaches
   at publication). Pins: tests/test_extension_registration_atomicity.py::
   test_conflict_refused_publication_has_zero_external_effects (conflict
   arising between staging and publication -> refusal with factory never
   started, bus untouched, bundle empty) and ::
   test_event_published_before_publication_never_invokes_the_handler
   (pre-publication invisibility, not eventual cleanup; sub_id fidelity).
   The pre-existing leak regression and disposer-ABI pins stay green.
2. ABI-2 readers (finding 2) — FIXED. POST /api/tasks identity-collision
   probe switched from the fail-soft loader (which QUARANTINED the probed row
   and then read "no result", freeing the id) to `load_task_result(strict=
   True)`: any stored row — admissible or not — keeps its identity occupied
   (409) and the probe never mutates storage. The unfiltered GET /api/tasks
   slice-before-projection path now runs the same fail-soft admission as
   `list_task_results` (quarantine + ONE batched `task_results_quarantined`
   event per scan, 6.3=B) via the schema primitives imported from
   `ouroboros.task_result_schema`. Pins: tests/test_headless_task_api.py::
   test_task_api_identity_collision_check_is_strict_not_fail_soft (both an
   unstamped and a torn row: 409, bytes unchanged, no quarantine dir) and
   tests/test_tasks_list_slice.py::
   test_unfiltered_list_slice_is_admission_aware_with_one_batched_event.
3. ABI-3 producers (finding 3) — FIXED as a CLASS, read tolerance untouched.
   Honest-name cutover of every remaining task-result/task-done producer:
   the four named files (supervisor/task_admission.py,
   events_schedule_task.py, workers.py x2 fallbacks, events_task_done.py
   root/subtree/unavailable branches) PLUS the same class found by the
   sweep in supervisor/queue.py, cancel_publication.py, task_lifecycle.py
   (x2 fallbacks), supervisor/state.py `reconstruct_task_cost` internals
   (fields seam kept as idempotent guard; the tuple path reads the honest
   key), ouroboros/post_task_checkpoint.py, and
   ouroboros/post_task_synthesis.py child-evidence rows (resolve stored pair
   deprecated-wins, emit honest name) — cross-file touches beyond the
   review's four examples are this disclosure. Fan-out pin:
   tests/test_gateway_abi3_removals.py::TestAliasProducerFanOutSweep — an
   AST sweep of EVERY ouroboros/ + supervisor/ module: (a) no
   `write_task_result` call passes a retired alias (kwarg or dict-literal
   arg; NO allowlist), (b) every dict-key/subscript emission of a retired
   spelling must be an allowlisted INTERNAL non-gateway plane (physical
   ledger rows, llm/usage observability events, review/evidence receipts,
   subagent envelope, evolution state, custody settlement events, reflection
   records — 20 rows, each with its plane named), (c) stale allowlist rows
   FAIL the test, (d) the three non-cost aliases have zero emissions with no
   allowlist at all. Explicitly NOT cut over (they are not the gateway
   alias): the internal planes above keep their own `cost_usd` field
   spellings — renaming ledger/receipt/envelope schemas is outside ABI-3's
   inventory and would be an unsanctioned break of their own producer/reader
   pairs.
4. ADOPTION (finding 4) — ABI-2/ABI-3/ABI-9 remain `done` LAWFULLY after the
   fixes above; each row's what/hook columns now name the fix-round closure
   and the new pins (`scripts/v7next_adoption.py` green). No residual was
   left open, so no row moved to in-progress.
5. Domain manifest (finding 5) — the stale `ouroboros/contracts/api_v1.py`
   row (module removed by ABI-3 lane D3) dropped from
   scripts/v7next_domains.toml [modules]; DOMAIN_QUOTIENT_REPORT.md
   regenerated by the official scripts/v7next_domain_report.py: 487 modules,
   "manifest drift: none (manifest == tracked population)".
6. Whitespace (finding 6) — `git diff --check 29e2b045..HEAD` is now clean
   (rc=0). Provenance checked BEFORE fixing, per the campaign's verbatim-
   bytes rule: all 50 trailing-whitespace lines are campaign-authored (the
   `_shell_guard_text` rewrite introduced by lane A commit 2e575b82; the
   frozen oracle v7_wip @ 9f691656 contains no `_shell_guard_text` call at
   all), so no byte-proved span was touched and nothing had to be declined.
   Blank-EOF fixes: tests/test_core_native_results.py, and
   ouroboros/tools/registry.py — a PROTECTED file; the delta is exactly the
   two trailing blank lines left by the lane-A facade assembly, zero code
   bytes (disclosed in the commit).
7. Function-size law (found by the fix-round's own gate run, not by the
   review): the ABI-2 strict-probe block pushed `api_tasks_create` to 310
   lines and `scripts/regenerate_size_ratchet.py --check` refused new
   function debt. Resolved per the lane-A entry-7 precedent — module-level
   helper extraction (`_task_identity_occupied`, behavior identical), no
   band exception, no manifest change. Enforcement note for auditors: this
   line's ratchet is pairwise base-vs-tip with NO committed-history replay
   (`ouroboros/review.py::validate_size_ratchet` docstring), so the four
   fix-round commits between the ABI-2 landing and the extraction carry the
   over-limit function in their trees without being audited surfaces; the
   final tree and the CI base (the pushed 9edb9199) are both clean, and the
   local degraded parent-tree check is green from this commit's parent on.
   DOMAIN_QUOTIENT_REPORT.md regenerated once more so its analyzed-inputs
   fingerprint matches the final runtime tree.

## From the F3.1 conformance fix-round-2 (base aae647fb, 2026-08-31)

Round-2 verdict (GPT-5.6 Sol): three findings NOT-CLOSED (ABI-9, ABI-2,
ABI-3 + the dependent ADOPTION claims); Domains manifest, git hygiene,
helper extraction and new-defects CLOSED with no action owed. Dispositions:

1. ABI-9 (finding 1) — FIXED. The round-1 order (validate -> effects ->
   swap) attached the bus subscription and started the supervised runner
   BEFORE the registry swap; EventBus.publish() takes only the bus's own
   lock, so a concurrent publish could invoke a handler of a
   not-yet-published extension, and the round-1 pins never exercised the
   window. `_publish_registrations` is now validate -> SWAP -> attach under
   the SAME single registry-lock hold: the validated snapshot becomes the
   authoritative bundle (digest minted, every attachable effect recorded on
   the bundle) BEFORE any effect attaches, so a handler is visible to the
   bus only for an already-published extension. A post-swap attach failure
   is disclosed (log.warning) and raised into the callers' standard
   dispose+unload path — load_extension/unload_extension reap everything the
   bundle recorded (surfaces, sub_ids, futures, companion names, on_unload).
   Pins (tests/test_extension_registration_atomicity.py): a REAL
   barrier-sequenced race — a publish interleaved between validation and
   attach never invokes the handler while the bundle is provably already
   published at attach time; the supervised effect observes a published
   bundle at its start; a post-swap attach failure ends with empty
   registries, an empty bus and the extension's on_unload having run.
   ARCHITECTURE rows (extension_loader, extension_plugin_api) and the
   registry-state staging docstrings restated to the true order.
2. ABI-2 (finding 2) — FIXED. `_raw_sorted_result_names()` no longer
   silently drops a file whose bytes fail to parse: malformed candidates are
   returned separately and `_tasks_list_payload` routes EVERY one through
   the same admission reader — quarantine plus a contribution to the SINGLE
   batched `task_results_quarantined` event of the scan — even when the
   candidate would have sorted beyond the slice window (the sort had read
   its bytes anyway). Torn-concurrent-write safety moved to where it truly
   lives: the quarantine primitive re-checks under the row's own write lock
   (kept_admissible), and a malformed name is never memoized. Disclosed
   residual (documented in the payload docstring): a PARSEABLE inadmissible
   row beyond the window is not classified by the sliced request — the next
   full/filtered scan quarantines it. TEST-CONTRACT DISCLOSURE: the pre-fix
   clause test_torn_result_file_is_skipped_then_recovered_without_
   poisoning_memo pinned the silent drop as "torn-write tolerance" — a test
   asserting the defect; replaced by
   test_malformed_result_file_is_quarantined_not_silently_dropped and the
   REAL slice-boundary pin
   test_malformed_candidate_beyond_the_slice_window_is_still_quarantined
   (rows > limit; one batch event spans both sides of the boundary).
3. ABI-3 (finding 3) — FIXED as the projection-boundary semantics. The ABI
   carries no alias: outbound surfaces (public_task_result, task detail,
   history frames, the cancel path through queue) emit ONLY honest names;
   stored legacy resolves deprecated-wins and NORMALIZES at projection and
   at re-write. Landed: TASK_COST_META_FIELDS honest-only;
   write_task_result merges over with_cost_aliases(existing) and normalizes
   the merged row (a legacy mutator's edit still wins its pair, then is
   stripped); public_task_result normalizes the top level + subagent
   envelope + loop-outcome usage; history mapper converts via
   carry_cost_meta at all three copy seams (task-summary replay,
   progress-meta replay, terminal-truth annotate); task_lifecycle
   stored/child cancel costs via carry_cost_meta; post_task_checkpoint
   task_cost_finalized event via carry_cost_meta, with an explicit SCRUB set
   that still pops the retired spellings (a stale legacy replica cannot
   smuggle an amount past deprecated-wins at the write seam). Producers
   whose data reaches the public projection are cut over and REMOVED from
   the sweep allowlist: build_subagent_envelope/envelope_from_task (key and
   kwarg now accounted_upper_bound_usd), the pipeline unavailable-patch, the
   loop-outcome usage sub-dict. Fan-out pin upgraded per the round-2
   mandate: (a) runtime projection-boundary pins
   (TestProjectionBoundaryNormalization — stored legacy row -> outbound
   payload deep-scanned for alias keys; rewrite normalization;
   legacy-mutator honor-then-strip) catch generic passthrough no AST scan
   can see; (b) the static sweep now also treats keyword args on ANY call
   as emission-shaped, and its allowlist is PER-SITE (file, alias,
   enclosing scope) — a new emission in an allowlisted file fails, any
   stale row fails; (c) a dedicated pin bans allowlisting outcomes.py /
   subagents.py / agent_task_pipeline.py. TEST-CONTRACT DISCLOSURE (the
   round-2 mandate names these as OLD-ABI contract tests): converted to
   honest-name assertions — tests/test_gateway_history.py (windowed anchor
   cost, terminal cost truth, override precedence, nullable bounds,
   task-summary flat-field passthrough), tests/test_tasks_list_slice.py
   compact-row cost clause, tests/test_cost_projection.py meta-field
   derivation + stored-legacy-tolerance clauses,
   tests/test_task_summary.py snapshot fixture (its legacy key was stale —
   the real _pre_synthesis_usage_snapshot emits the honest name),
   tests/test_headless_task_artifacts.py mirror-cost merge and
   finalized-accounting clauses, tests/test_task_result_monotonic.py
   kept-cost clause. NOT changed: the JS read seam keeps pair tolerance
   (web mirror switch stays deferred per the ABI-3 row), and the internal
   non-gateway planes (ledger rows, review/evidence receipts, evolution
   state, custody settlement events, reflection/consciousness records) keep
   their own spellings under anchored per-site allowlist rows.
4. ADOPTION claims (finding 4) — ABI-2/ABI-3/ABI-9 stay `done` LAWFULLY
   after the fixes above; each row now names the fix-round-2 closure and the
   new pins (scripts/v7next_adoption.py OK, 36 rows). The absolute ABI-9
   claim in docs/ARCHITECTURE.md:291 is restated to the true order
   (validate -> swap -> attach) including the post-swap-failure disclosure,
   so the "refused registration publishes nothing" clause is now exactly
   true (refusal = validation failure; a post-swap attach failure is a
   published-then-disposed bundle, said in the same sentence).
5. Findings 5-8 (domains manifest, git hygiene, helper extraction, new
   defects) — CLOSED by the verdict itself; no action owed, nothing
   changed there in this round.
6. Module-size law (found by the fix-round-2's own gate run, not by the
   review; the round-1 entry-7 precedent): the ABI-2 admission routing
   pushed ouroboros/gateway/tasks.py past the 1600-line hard cap, and the
   ABI-3 honest-name comment pushed outcomes.py::derive_loop_outcome past
   300 lines. Resolved by extraction, no band exception, no debt entry:
   the raw creation-ts sort scan + malformed-candidate admission moved to
   the new ouroboros/gateway/task_list_scan.py (tasks.py 1615 -> 1563;
   same objects imported back; ARCHITECTURE row added; module mapped to
   D11 in scripts/v7next_domains.toml, DOMAIN_QUOTIENT_REPORT regenerated
   by the official script — 488 mapped, drift none), and the loop-outcome
   usage snapshot became module-level `_loop_usage_snapshot`
   (derive_loop_outcome 304 -> ~292). Each extraction lands INSIDE the
   commit whose growth caused it, so every first-parent tree of this
   round satisfies its own manifest (no condemned intermediate commits —
   the local unpushed round-2 series was arranged for this before any
   push; the pushed tip aae647fb was not rewritten).
7. Serial-battery addendum (found by this round's full CI-shape battery,
   serial pass): tests/test_cancel_live_kill_path.py
   ::test_e2e_child_finishing_before_the_kill_keeps_its_completed_result
   asserted the OLD-ABI alias on the kept row and the task_done relay —
   converted to the honest name (same class as the entry-3 disclosure
   list; the child writer's legacy kwarg is honored deprecated-wins, then
   stripped by the write seam).

## From the F3.1 conformance fix-round-3 (base f8e579de, 2026-08-31)

Round-3 verdict (GPT-5.6 Sol, read-only @ f8e579de): ABI-9 and ABI-3
NOT-CLOSED on the enumerated tails, ADOPTION/ARCHITECTURE claims dependent on
them, one NEW UI defect; ABI-2, ratchet extractions and manifest CLOSED with
no action owed. Dispositions:

1. ABI-9 (finding 1) — FIXED as the disclosed STAGED PROTOCOL, not a false
   "one atomic publication" absolute. (a) The OOP load published surfaces
   and companions as TWO transactions (extension_loader
   _register_out_of_process_surfaces + _spawn_out_of_process_companions),
   with the second re-minting bundle.generation_digest without re-stamping
   published descriptors. Both are replaced by ONE staged publication:
   _stage_out_of_process_surfaces validates catalog descriptors through the
   same _stage_surface_locked seam the in-process register() window uses,
   and _publish_out_of_process_registration stages surfaces AND companion
   spawns on one PluginAPI snapshot — one validate -> SWAP -> attach
   transaction. The one structurally LATER publication that remains —
   server-side companion recovery (reconcile_server_companions) onto a live
   bundle — mints a fresh digest and _publish_registrations RE-STAMPS every
   already-published descriptor the bundle owns in the same lock hold, so
   per-surface provenance never diverges from bundle.generation_digest.
   (b) The recovery failure path was a silent _abort_registration leaving
   the extension half-alive; ANY failure of the shared seam now routes
   through the standard dispose+unload path (unload_extension). (c) Unload
   visibility: _unload_extension_locked popped the bundle and surfaces
   BEFORE the bus unsubscribe and runtime-API close; the order is now
   outside-in — subscription ids + the _unloading latch snapshot in ONE
   registry-lock hold (no publication can interleave), bus unsubscribe,
   runtime-API close, THEN bundle/surface removal, future cancel, companion
   stop, module purge. RESIDUAL BY DESIGN (pinned + disclosed in
   EventBus.publish's docstring): the bus COPIES subscribers under its own
   lock before invoking handlers, so a publisher that copied a handler
   before the unsubscribe may still invoke it after surfaces are gone; the
   supported guarantee is "a publish STARTED after unsubscribe never
   delivers", and the closed runtime API + _unloading latch make the late
   call a host no-op. Pins: test_out_of_process_surfaces_and_companions_
   publish_as_one_transaction, test_companion_recovery_failure_unloads_
   instead_of_silent_abort, test_late_publication_restamps_already_
   published_descriptors, test_unload_closes_bus_and_runtime_visibility_
   before_surfaces_leave, test_publish_started_after_unload_never_delivers.
   TEST-CONTRACT DISCLOSURE: test_spawn_out_of_process_companions_host_
   spawns_declared_name renamed to test_publish_out_of_process_registration_
   host_spawns_declared_name (the unified seam it exercises); the two
   catalog-revalidation suites and the loader-extraction _STAYED list now
   name the unified surface (_publish_out_of_process_registration /
   _stage_out_of_process_surfaces).
2. ABI-3 (finding 3) — FIXED in depth. (a) build_subagent_envelope
   normalizes the stored usage snapshot BEFORE embedding (deprecated-wins
   kept; the amount fallback reads the resolved honest name). (b+c) ONE
   shared normalizer cost_projection.normalize_task_result_cost_planes (top
   level + subagent envelope + envelope.usage + loop_outcome.usage) serves
   BOTH public_task_result and write_task_result (both merge passes) — the
   sanctioned known-paths + deep-test-scan variant of the round-3 mandate:
   internal evidence planes (review receipts, ledger rows) stay their own
   schemas per the round-2 disposition, named per-site in the sweep
   allowlist. (d) Evolution history: the update_evolution_campaign_after_
   task row now stamps accounted_upper_bound_usd (allowlist row REMOVED);
   the one internal reader (Recent Campaign Cycles prompt block) resolves
   the pair deprecated-wins; gateway/state._evolution_state_public converts
   stored legacy rows at the /api/state projection boundary (copy-on-write
   over the shared snapshot). (e) The deep-scan fixture now places the
   legacy alias on the ACTUALLY SUPPORTED producer path
   subagent_envelope.usage.cost_usd beside the envelope-root spelling, with
   resolved-amount assertions on the public projection, the task-detail
   endpoint and a new rewrite pin. (f) The sweep allowlist is COUNT-
   ANCHORED per site — (file, alias, scope) -> (reason, exact count); a new
   emission inside an allowlisted function breaks the anchor and fails.
   (g) Own AST sweep re-run: 55 emission-shaped sites dispositioned — 1
   honest cutover (the evolution history row), 3 sites under the
   events_evolution_done anchor re-classified honestly (2 internal
   lifecycle/checkpoint call kwargs + 1 supervisor.jsonl observability row,
   converted at the /api/logs boundary on replay), remaining 51 = internal
   planes kept per-site with exact counts.
3. New defect (finding 6) — FIXED. /api/logs emits the honest name but
   web/modules/log_events.js read only `cost_usd ?? cost`, so the LLM-round
   money column was empty after a reload. All three read sites now resolve
   through the existing SSOT JS helper accountedUpperBound() (the
   resolve_cost_pair mirror, deprecated-wins) with the live-frame `cost`
   spelling last — deliberately the shared precedence rule rather than a
   hand-ordered honest-first list, so a diverged stored pair tells the same
   story on every surface. Pinned in web/tests/cost_presentation.test.js
   (backfill name, live frame, diverged pair). chat.js (BYTE_DEBT ceiling)
   untouched; log_events.js stays in its band.
4. ADOPTION/ARCHITECTURE (finding 4) — restated to the post-fix truth:
   ABI-9 row + docs/ARCHITECTURE.md extension_loader/extension_plugin_api
   rows describe the staged protocol (single OOP transaction, recovery
   restamp + unload-on-failure, outside-in unload visibility, EventBus copy
   residual); ABI-3 row + the cost_projection ARCHITECTURE row describe the
   shared nested-plane normalizer and the boundary conversions
   (/api/logs, /api/state evolution history) with the internal-plane
   residual named. scripts/v7next_adoption.py OK (36 rows).
5. Findings 2 and 5 (ABI-2, ratchet extractions/manifest) — CLOSED by the
   verdict itself; nothing changed there in this round.

## From the F3.1 conformance fix-round-4 (base 163c2765, 2026-08-31)

Round-4 verdict (GPT-5.6 Sol, read-only @ 163c2765): ONE blocker — the ABI-9
companion-recovery lifecycle TOCTOU (finding 1); ABI-3 and Logs UI CLOSED with
no action owed; ADOPTION/ARCHITECTURE NOT-CLOSED only as a dependent of the
race. Dispositions:

1. ABI-9 recovery TOCTOU — FIXED as a GENERATION-BOUND protocol. The race:
   ensure_companions_running snapshotted liveness/bundle, then published
   WITHOUT the lifecycle lock (production re-invokes it from
   extension_reconcile_queue after locked reconciliation returns); a
   concurrent unload could complete in the window, after which the stale
   recovery re-created an empty bundle inside _publish_registrations
   (bundle-if-None branch) and started its companion — resurrecting a
   companion-only bundle after disable/unload. The fix, per the pinned
   protocol: (a) the recovery snapshot carries the observed
   bundle.generation_digest (read in the same registry-lock hold as the
   companion names); (b) the recovery publication runs UNDER the lifecycle
   lock and _publish_registrations(require_live_generation=...) re-validates
   under the registry lock that the observed publication is STILL live — a
   vanished or reloaded bundle raises the typed ExtensionStaleRecoveryError
   BEFORE any mutation (zero effects; ensure_companions_running surfaces it
   as the typed action "stale_recovery_refused"), and the recovery form of
   _publish_out_of_process_registration structurally REQUIRES a pre-existing
   live bundle (exactly-one-of form gate: current_hash XOR
   expected_generation), so recovery can never create a bundle; (c) the
   failure-disposal is generation-bound: unload_extension gained
   expected_generation and no-ops WITH DISCLOSURE (warning log) when the
   live generation is not the one this recovery observed or itself swapped
   in (_published_generation), so a failed recovery can never unload a newer
   publication. Pins: test_unload_completing_between_snapshot_and_
   publication_refuses_recovery (deterministic same-thread barrier — the
   unload completes between the snapshot and the publication), test_
   recovery_publication_refuses_on_generation_mismatch_without_effects,
   test_generation_bound_disposal_skips_a_newer_publication, plus the two
   recovery-form atomicity tests updated to the generation-bound call form.
   TEST-CONTRACT DISCLOSURE (test-that-pinned-the-bug): the clause of
   test_publish_out_of_process_registration_host_spawns_declared_name that
   asserted the recovery-form helper accepts NO pre-existing live bundle and
   spawns anyway pinned the resurrection defect itself; it is REPLACED by
   test_recovery_publication_requires_a_pre_existing_live_bundle (opposite
   pin: typed refusal, zero effects, nothing created), and the surviving
   host-spawn/trust-boundary clauses now use the initial-load form.
2. LOW — the stale reference to a nonexistent reconcile_server_companions in
   _publish_out_of_process_registration's docstring is gone; the docstring
   names the real recovery caller (ensure_companions_running) and the
   generation-bound contract. (The same stale name inside the round-3 ledger
   section above is historical record and stays as written.)
3. ADOPTION/ARCHITECTURE — restated to the post-fix truth: the ABI-9 row and
   the ARCHITECTURE extension_loader/extension_plugin_api rows now describe
   recovery as generation-bound onto the still-live observed publication
   (typed zero-effect refusal otherwise; generation-bound disposal), not as
   an unconditional "re-publishes onto the already live bundle".
4. RATCHET-DRIVEN MOVE (disclosed): extension_loader.py sits at the pinned
   <=1000-line extraction bound with 2 lines of headroom, so the fix is
   funded by moving _stage_out_of_process_surfaces whole into
   extension_child_catalog.py — its natural owner (it composes ONLY the
   child-catalog validators + registry maps + the PluginAPI staging seam and
   needs nothing from the loader); the loader re-exports it, the extraction
   contract (_MOVED_OWNERS/_STAYED) and the ARCHITECTURE child-catalog row
   are updated, and the leaf never imports the loader (DAG preserved).

## From the F3.1 conformance fix-round-5 (base c26c89a3, 2026-08-31)

Round-5 verdict (GPT-5.6 Sol, read-only @ c26c89a3): NEEDS FIXES — ONE HIGH
(the round-4 "zero effects" claim was false on the filesystem: recovery
mutated authorization state BEFORE the generation fence) plus one MEDIUM on
the strength of the round-4 test pins; verification points 2 and 5 CLOSED
(generation-bound disposal; extraction/sizes). Dispositions:

1. HIGH — stale recovery mutated `auth_token.json` before the generation
   fence: FIXED by post-fence token materialization. The defect:
   `register_companion_process` called `get_skill_token()` during descriptor
   build (before publication), and `mint_skill_token` WRITES
   `auth_token.json` whenever the stored token is missing or its bound
   content hash mismatches the live recompute; the fence sits in
   `_publish_registrations`, so a recovery that lost the race to an
   unload/reload holding a stale payload snapshot (an old skill root still
   on disk with the pre-update content) rotated the G2-bound token file and
   only THEN raised `ExtensionStaleRecoveryError` — the live G2 companion,
   spawned with the current token in its env while the Host Service rereads
   the file on every request (`host_service.authenticate_token_payload`),
   was left permanently unauthorized. The fix separates descriptor build
   (pure computation — env carries no HOST_SERVICE_TOKEN) from token
   materialization: `_publish_registrations` mints the token and injects it
   into every staged companion descriptor's env only inside the post-swap
   attach, AFTER `require_live_generation` admitted the publication, in the
   same registry-lock hold (a mint failure there routes through the
   standard dispose+unload path like any attach failure). The initial-load
   path is unchanged in effect — the token is still legitimately minted at
   its publication and the spawned descriptors reference it — and the
   runtime `get_skill_token()` API still mints on demand for a live
   extension. Pin (red pre-fix on both):
   test_stale_recovery_does_not_break_live_publication_authorization (the
   verdict's exact repro through the REAL entry — G1 loaded from an old
   root, in-window unload+reload of v2 content from a new root, stale
   refusal, byte-identical token, end-to-end
   HostServiceContext.authenticate_token_payload success for the G2
   spawn-env token) and the token-absence clause of
   test_recovery_publication_requires_a_pre_existing_live_bundle (a
   no-live-bundle refusal must not CREATE the token file).
2. MEDIUM — round-4 pin strength: (а) the unload-window interleaving test
   now also asserts `auth_token.json` is byte-untouched after the stale
   refusal, and the direct-call fixtures were moved off `drive_root/state`
   onto the production per-skill directory
   (`skill_state_dir(drive_root, name)` — the directory the Host Service
   actually scans); (б) the generation-mismatch test is rebuilt through the
   REAL recovery entry: `ensure_companions_running` with a deterministic
   in-window unload+reload (same payload), asserting the typed refusal, the
   preserved NEW generation, no recovery spawn, and the untouched token
   file — no directly supplied digest remains in that test; (в) the new
   live-G2-authorization test above. DISCLOSED: the byte-equality clauses
   in (а) and (б) are belt-and-suspenders rather than red-pre-fix pins —
   with an unchanged payload the pre-fix mint was a read (hash match, no
   rotation); the red-pre-fix coverage of the HIGH lives in the two pins
   named in item 1.
3. Docs — the ABI-9 row (ADOPTION_v7next.md), the ARCHITECTURE
   extension_loader/extension_plugin_api rows, and the
   `ExtensionStaleRecoveryError`/`mint_skill_token`/`_publish_registrations`
   docstrings now state the post-fence materialization explicitly: the
   round-4 "zero effects / before any mutation" wording is true only as of
   this round, and the round-4 ledger section above stays as written
   (append-only historical record).
4. Size pins: extension_loader.py untouched (998); extension_plugin_api.py
   998 after comment condensation (both within the <=1000 extraction pin,
   600 <= plugin API respected).

## From the F3.1 conformance fix-round-6 (base 1aae9868, 2026-08-31)

Round-6 verdict (GPT-5.6 Sol, read-only @ 1aae9868): NEEDS FIXES — ONE MEDIUM
(pre-fence filesystem side writes remained: the round-5 "pure computation"
claim was still false for `env_from_settings` manifests and the recovery
entry's state-dir mkdir) plus ONE LOW (a token rotation can strand an
already-running companion on its old spawn-env token); no HIGH; verification
points 3/4/5 CLOSED. Dispositions:

1. MEDIUM — pre-fence side writes: FIXED by extending the round-5 post-fence
   materialization to the WHOLE companion env. The defect:
   `register_companion_process` called `_scrub_env` during descriptor build,
   and for a manifest with `env_from_settings` that invokes
   `load_settings()` — which creates/unlinks the settings lock file
   (`config._acquire_settings_lock`) and can PERSIST a context-mode
   settings migration (`config.load_settings_lock_held` →
   `normalize_and_persist_context_mode_compat`) — before the generation
   fence in `_publish_registrations`; additionally `ensure_companions_running`
   resolved the state dir via the creating `skill_state_dir()` before the
   fence. The fix: the staged spawn now carries the manifest companion spec
   (`_StagedCompanionSpawn.spec`) and the descriptor is built with an EMPTY
   env; `extension_child_catalog.materialize_companion_env` fills it —
   settings-derived values, manifest env overlay, host bridge URL,
   isolated-dep PYTHONPATH and the auth token — only inside the post-swap
   attach, after `require_live_generation` admitted the publication, in the
   same registry-lock hold where the state dir is now created
   (`mkdir(parents=True, exist_ok=True)`) and the token is minted; the
   recovery entry resolves its path via the new non-creating
   `skill_state_path`. Env precedence is preserved (settings-derived base,
   then manifest overlay, then reserved bridge keys). Pin:
   test_stale_recovery_with_env_from_settings_has_zero_filesystem_effects —
   an `env_from_settings` manifest recovery losing the race to an unload is
   a typed refusal with ZERO `load_settings` calls from `_scrub_env` (the
   lock-file/migration hazard) and an unchanged data-root file tree; the
   same test proves the post-fence path still delivers the materialized env
   (skill name, token, bridge URL) to the publication's spawns.
   DISCLOSURE: the pin's semantic red-pre-fix content is the
   `load_settings` tripwire (pre-fix, descriptor build called `_scrub_env`);
   the test as written cannot execute verbatim on the pre-fix tree because
   its interleave seam (`skill_state_path`) is introduced by the fix itself
   — the round-4/5 interleave tests were migrated to the same seam.
2. LOW — stale spawn-env token after an accepted rotation: RESIDUAL BY
   DESIGN, no code change (proportionality: no degradation case exists).
   Preconditions analysis: `mint_skill_token` rotates ONLY when the stored
   token file is missing, carries no token (corrupt), or its bound
   content_hash mismatches the live recompute. The Host Service
   authenticates EVERY request against the file (token equality) AND
   against a freshly computed on-disk content hash
   (`authenticate_token_payload` + `_assert_active_token` →
   `find_skill().content_hash`), so in every rotation precondition the
   already-running companion's spawn-env token was ALREADY non-authorizing
   BEFORE the mint: missing/corrupt file fails the token compare;
   hash-stale file fails the "token is stale" check. Rotation therefore
   restores authorization for the publication's own spawns and can never
   revoke a still-valid token. Residual: recovery stages only MISSING
   companion names, and a supervisor auto-restart can re-spawn an old
   descriptor whose publication's start then reports success without
   replacing it — such a companion, de-authorized by a content change, is
   not healed by companion recovery (same dead-token state as before the
   recovery); the heal path is the ordinary unload/reload, which stops and
   re-spawns every companion with the fresh env.
3. Docs truth: with item 1 landed, the absolute claims are now factual and
   were tightened rather than weakened —
   `ExtensionStaleRecoveryError`'s docstring adds "no settings read/lock or
   state directory is materialized", `_publish_registrations` describes the
   whole-env post-fence materialization, the ARCHITECTURE
   extension_plugin_api row and the ADOPTION ABI-9 row carry the
   fix-round-6 clause plus the item-2 residual disclosure. The round-5
   ledger section above stays as written (append-only historical record);
   its "descriptor build stays pure" wording described the token plane only
   and is superseded by this section for the settings/state-dir planes.
4. Size pins: extension_plugin_api.py 982 and extension_loader.py 1000
   (<=1000 extraction pin, 600 <= plugin API respected); the env
   materialization helper and the non-creating path resolver live in
   extension_child_catalog.py (222), `_StagedCompanionSpawn.spec` in
   extension_registry_state.py (182).

## From the F3.1 conformance fix-round-7 (base 267b71bf, 2026-09-01) — FINAL

Round-7 verdict (GPT-5.6 Sol, read-only @ 267b71bf): NEEDS FIXES — ONE MEDIUM
(the round-6 zero-filesystem-effects claim is still absolute while the
liveness/grant projection legitimately reads settings/state pre-fence), ONE
LOW (a transient hash error rotates a live valid token), ONE LOW (pin blind
zones); verification points 2/3/6/8 CLOSED. This is the FINAL micro-round of
the Ф3.1 conformance cycle; the cycle is declared converged after it.
Dispositions:

1. MEDIUM — pre-fence filesystem effects: SCOPED AS CLAIMS, deliberately NOT
   fixed by rewriting the read layer. The pre-fence reads the verdict names —
   `health_path` → creating `skill_state_dir` (extension_health.py /
   skill_loader.py), the creating `skill_state_dir` inside
   `load_skill_grants`, and `config.load_settings` (settings lock, possible
   context-mode migration persist) inside `requested_core_setting_keys` — are
   the RUNTIME-WIDE settings/grant read idiom, used identically by status
   projections, the loader, skill exec and the UI; recovery merely calls the
   same projections every other caller uses. Carving a non-creating,
   non-locking read path through `skill_loader`/`config` for one caller would
   fork the SSOT read layer (over-engineering for a refusal path whose reads
   are idempotent infrastructure). Instead the false absolutes were removed:
   `ExtensionStaleRecoveryError`'s docstring and the ADOPTION ABI-9 row now
   state the exact contract — before the fence there are NO effects on
   authorization/token/registries/bundles/companion-env; infrastructure
   reads (settings lock inside `load_settings`, state-dir mkdir via the
   grant/health projection) MAY occur, as anywhere in the runtime. The
   round-6 sections above stay as written (append-only historical record);
   their "zero effects hold on every filesystem plane" wording is superseded
   by this scoped contract.
2. LOW — transient hash error rotates a valid token: FIXED in
   `mint_skill_token`. "Could not read/compute the hash" (transient) is now
   DISTINCT from "read fine and mismatched / token file missing or corrupt"
   (legitimate mint/rotate): on a transient `compute_content_hash` failure
   the stored token, when it parses, is returned byte-for-byte unrotated
   (`auth_token.json` untouched — the running companion whose spawn env
   holds it stays authorized against the file the Host Service rereads per
   request); with no reusable stored token the mint fails closed with the
   typed `SkillTokenHashUnavailableError` instead of minting a token bound
   to an empty hash. Pins:
   test_transient_hash_error_never_rotates_a_valid_token (red pre-fix: the
   old code collapsed the error to content_hash="" and rotated) and
   test_transient_hash_error_without_reusable_token_fails_closed (red
   pre-fix structurally — the typed error class arrives with the fix).
   RESIDUAL DISCLOSED (pre-existing, NOT introduced by this cycle, not fixed
   here): concurrent mints (publication attach / `get_skill_token` /
   process-runner child env) are read-decide-write over `auth_token.json`
   with no shared lock or CAS — the last writer can supersede a token just
   returned to another caller.
3. LOW — pin blind zones: FIXED by hardening
   test_stale_recovery_with_env_from_settings_has_zero_filesystem_effects:
   (a) the tripwire now covers BOTH seams — `skill_exec.load_settings`
   (`_scrub_env`) and the direct `config.load_settings`
   (`requested_core_setting_keys` and any other caller) — and measures
   exactly the window "after the recovery's state-dir resolution, up to the
   fence" (the counter clears where the interleave snapshot is taken), so
   the legitimate pre-fence grant/liveness projection calls are not broken;
   (b) `_data_root_tree` snapshots name + size + mtime_ns (an in-place
   rewrite is now caught) and the settings lock file is asserted absent in
   the window, with `SETTINGS_PATH` repointed test-locally so the assertion
   cannot race a concurrent test process on the shared run-wide path;
   (c) `EXT_DEMO_VALUE` is actually SET in the settings fixture, granted
   (custom-secret key) and asserted DELIVERED in the materialized spawn env,
   alongside the manifest companion env overlay (`EXT_OVERLAY`) and the
   isolated-dep `PYTHONPATH` (a real `.ouroboros_env` site dir) — the
   "settings-derived value silently lost" hole is closed.
4. Size pins: extension_loader.py untouched at 1000/1000;
   extension_plugin_api.py 1000 (<=1000 extraction pin, 600 <= plugin API
   respected); extension_child_catalog.py untouched (222).

## From the F3.2 lane A (ResolvedModelTarget, base 3ba9f452)

ABI-4 consumer sweep per docs/v7next/DESIGN_RESOLVED_MODEL_TARGET.md
(greenfield §6-design; zero occurrences on base — NOT a transplant).

Seam inventory (rg over comma/at model-string parsing beside resolution
seams) and dispositions:

| seam | prior output | consumers | migration |
| --- | --- | --- | --- |
| `model_slots.get_fallback_models` (cross-model ladder) | `list[str]` | `loop_model_call._run_cross_model_fallback_chain`; `tools/control_runtime` membership check | `provider_models.fallback_candidate_targets()` → `tuple[ResolvedModelTarget, ...]` (typed view over the ONE chain SSOT); the loop chain iterates typed candidates, `.model_id` crosses to a string only at the chat-API transport boundary |
| `review_model_routes.get_review_models` / `get_scope_review_models` | `list[str]` | reviewer slot builders (`reviewer_slots`, `structured_scope_review_slots`), review surfaces | typed views `get_review_targets` / `get_scope_review_targets` / `resolved_review_model_target` at the SAME seam; `review_model_uses_local` is applied ONCE at construction (`provider_route == "local"` ⇔ the predicate), and the slot builders read that fact off the dataclass instead of re-asking per string. MODELS UNCHANGED: purely typization, byte-identical per configuration class (structured, default panel, local-only route, exclusive-direct-provider rewrite) |
| `subagents.parse_subagent_harness` → `DelegationRoute` | typed route (already constructed at the parse seam) | `tools/delegate` run-request assembly | `DelegationRoute.resolved_model_target()` bridge; `_build_delegated_run_request` assembles harness pin, model, effort and credential pin from ONE typed target read |

Contract facts: frozen+slots, value equality/hash, ""/0 sentinels (no
Optional/None-vs-missing), NO pricing fields; `context_window` stays 0 at
these seams (windows remain Capability Evidence's fact, fail-open).
Home: `model_slots.py` (dataclass) + `provider_models.resolve_model_target`
(constructor) — the D02-owner seam; `config.py` facade re-exports every new
name (`test_config_extraction` owner inventory extended accordingly).

Verification hook: tests/test_resolved_model_target.py (name fixed by the
design note) — frozen-ness, value identity, construction at each seam,
consumer-sweep grep pins (no comma/at parsing in the swept consumers).
ADOPTION ABI-4 row: hook updated, status done.

DISCLOSED RESIDUAL (typed up to the transport boundary, per the lane note —
transports NOT rebuilt):

1. The Claudexor wire body serializes the typed target back to strings
   (`model`/`effort`/`credentialProfileId`/`harnesses`) — the engine's JSON
   contract; the adapter no longer re-parses a `harness[=model]` slug, the
   parse seam (`parse_subagent_harness`) remains the one string reader.
2. Reviewer agent_session rows keep their OPAQUE `harness[=model]` spec
   (RouteSpec/ReviewSlot vocabulary): a session spec is not an API model
   destination, so it is not forced into `ResolvedModelTarget`; the shared
   dataclass covers API-routed model destinations plus the delegated bridge.
3. `tools/control_runtime.py` still consumes `get_fallback_models()` as an id
   membership check (no parsing, no route facts) — left on the string list
   deliberately; `resolve_credentialed_model`/`vision` candidate walks are
   internal to the provider seam itself.
4. `ReviewSlot`/`commit_triad_delivery` keep their existing parallel-vector
   ABI (models/routes/efforts) — the sweep types the route-fact derivation,
   not the review delivery contract (review models/behaviour byte-identical).

## From the F3.2 lane B (dispatch digest read, base 3ba9f452)

1. The digest-API inventory the seam relied on is EXACTLY as the F3.1-B
   ledger promised — nothing new was built (reuse-first proven): the
   per-surface `extension_generation` stamp minted at publication
   (extension_plugin_api.py `_publish_registrations`) already reaches the
   dispatcher through `extension_loader.get_tool()`'s descriptor copy, and
   `extension_registry_state.extension_generation_digest` (re-exported by
   extension_loader) already serves the registry read. The Ф3.2 delta is
   confined to ouroboros/tools/extension_dispatch.py:
   `_dispatch_extension_tool_result` became a stamping wrapper over the
   verbatim inner dispatcher (`_dispatch_extension_tool_untagged`) plus the
   `_generation_digest_for` reader (descriptor stamp first, registry reader
   fallback for a descriptor predating the stamp).
2. Provenance seam REUSED, no new ledger: the digest rides the typed
   `ToolResult.meta` (`extension_generation` key), which the loop already
   projects into the tools.jsonl record via `_tool_result_fields` →
   `tool_result_meta` (loop_tool_execution.py). `ToolResult` is frozen with
   MappingProxyType meta, so the wrapper REBUILDS the result with the same
   status/code/text — the model-facing projection is byte-identical.
3. Scope of the stamp, disclosed: only outcomes of a PHYSICAL dispatch
   attempt carry the digest. The two pre-dispatch typed refusals —
   EXTENSION_UNAVAILABLE (liveness) and SAFETY_VIOLATION (safety block) —
   are excluded by `_UNDISPATCHED_CODES` and keep their exact pre-seam
   shape (pin: test_unavailable_refusal_keeps_the_pre_seam_typed_shape
   asserts meta == {"dynamic_provider": True}). No validation, no gate, no
   behavior branch reads the digest (P5: a provenance fact, not a gate).
4. Pins (tests/test_extension_registration_atomicity.py), red-proof done by
   running them against the base dispatcher:
   test_dispatch_provenance_carries_the_published_generation_digest (red
   pre-fix; also proves a reload's NEW publication puts the NEW digest on
   the next call) and
   test_dispatch_provenance_falls_back_to_the_registry_reader (red
   pre-fix); the unavailable-shape pin is an invariance pin (green on both
   sides by design).
5. Size pins: extension_loader.py and extension_plugin_api.py untouched at
   1000/1000; extension_child_catalog.py untouched (222);
   extension_registry_state.py untouched (182).

## From the F3.3 RC auditor (base 4fa2f01a)

Sources of each check class of the machine-readable scope
(scripts/rc_audit.py, ABI-7b/F13), file:line of the feeder inventory at this
base, consumed READ-ONLY (reuse-first — no parallel parsers or lists):

1. gateway-alias (5 checks) — the frozen F11 per-alias inventory
   docs/v7next/ABI3_GATEWAY_ALIAS_INVENTORY.md (cost_usd/cost_usd_with_children
   §1–2 at :16, telegram_chat_id §3 at :53, project_last_viewed/project_hidden
   §4–5 at :76). Stored-axis tolerance kept per the inventory, so on-disk hits
   (task_results alias keys, state/ui_preferences.json legacy keys) render as
   NOTES, never blocking findings; the live-client half is owner attestation.
2. retired-setting — ouroboros/settings_defaults.py::RETIRED_SETTING_KEYS
   (:314), imported at execution time; the ABI-5/Q10 semantics per
   tests/test_abi5_q10_removals.py. fail_tasks (no install-visible key) and
   until_deadline/stall_rounds_threshold (pacing knobs, not settings keys)
   live in the report PROSE plane exactly as the design note requires.
3. comma-list — ouroboros/settings_defaults.py::RETIRED_COMMA_LIST_SETTING_KEYS
   (:350, NEW in this commit): the ABI-10 classification INSIDE
   RETIRED_SETTING_KEYS, placed at the retirement SSOT so the auditor snaps
   the exact list at execution time instead of hardcoding it; subset
   membership is fail-closed in build_scope() and pinned by the suite.
4. plugin-api — ouroboros/contracts/plugin_api.py: PLUGIN_API_VERSION="2.0"
   (:29), LEGACY_PLUGIN_API_GENERATION="1.3" (:32, absent ≡ 1.3 by
   construction), extension_new_pass_admission_error (:285) reused as THE
   admission predicate; hash-bound grandfather adjudicated via
   skill_review_status.skill_review_gate over the install's
   state/skills/<name>/review.json (read without the creating
   skill_state_dir helper — read-only guarantee).
5. schema-stamp — ouroboros/task_result_schema.py:
   TASK_RESULT_SCHEMA_VERSION=1 (:34) and the PURE classifier
   task_result_schema_refusal (:39) reused directly (never
   load_task_result, which quarantines on read — a mutation). The Q8=B
   consequence is named verbatim in the scope check, in every schema-stamp
   finding, and in the owner-attestation list.

N−1 fixture catalog (F14, real bytes): tests/fixtures/nminus1/ —
settings_v6.113.4.json and task_result_v6.113.4.json were produced by RUNNING
the v6.113.4 code itself (git archive of the tag; config.save_settings /
task_results.write_task_result in an isolated mktemp root; all secret fields
empty), telegram_SKILL_v6.113.4.md is `git show f0313064:skills/telegram/SKILL.md`
(the commit before ABI-1 added the plugin_api field). The inline N−1 byte
forms of the ABI-2 quarantine suite and the ABI-7a updater shim remain where
they are; this catalog is the one FILE-shaped N−1 store (no second catalog).

Remaining owner attestation (F13 — printed by the auditor, never pretended
machine-checked): live custom gateway clients (send/read of the five removed
aliases), external automation treating the retired comma-list env spellings
as a settings surface, out-of-tree extension authors declaring plugin_api
"2.0" before new PASSes, reliance on fail_tasks / the removed pacing knobs,
and owner acceptance of the Q8=B quarantine consequence.

## From the F3.3 comma-sweep (base 1bd342b1, 2026-09-01)

1. The phase CI gate landed as tests/test_comma_list_remnant_sweep.py
   (named in the ABI-10 hook column beside the F3.1 sweep): retired-key
   mentions over ouroboros/ + web/ + supervisor/ snapped DYNAMICALLY from
   RETIRED_COMMA_LIST_SETTING_KEYS, comma-split parsing in model/review
   modules, phase-5 plumbing absence, and a retired-envs-are-ignored
   runtime pin - all count-anchored per site with reasons (the
   test_gateway_abi3_removals.py allowlist discipline).
2. Residual inventory matrix (remnant | live/dead | action | reason):
   - review_substrate.scope_reviewer_slots route_env_key plumbing
     (configured_review_routes + TRIAD/SCOPE_REVIEW_ROUTES_ENV in
     review_execution.py) | DEAD post-ABI-10 | REMOVED | the only
     production explicit-models caller (tools/scope_review.py:345)
     overrides the route itself ("the caller's fanned-out route is
     authoritative"); the models=None path reaches the env read only on a
     structured-config-less install exporting a RETIRED spelling - exactly
     the class ABI-10 retired. Rows built from plain model lists are now
     pinned api_chat; retired-envs-are-ignored pinned in the sweep and in
     test_review_agent_session_route.py (the phase-5 env-parsing clauses
     were replaced, not deleted silently: test_configured_review_routes_
     parsing / test_scope_rows_carry_their_configured_routes asserted the
     retired behavior). test_review_session_scope_wiring's mixed fan-out
     now builds its mixed panel from a structured OUROBOROS_REVIEWER_SLOTS
     payload.
   - OUROBOROS_ADVISORY_REVIEW_ROUTE (preflight_review_run.py constant +
     prose, claude_advisory_review.py messages) | DEAD (no os.environ read
     anywhere; advisory_review_route() reads only the structured SSOT) |
     prose/constant REMOVED | operator guidance now names the
     OUROBOROS_REVIEWER_SLOTS advisory row; vestigial setenv/delenv lines
     dropped from 4 test files (they were no-ops - nothing read the env).
   - JS typedef cleanup (web/modules/api_types.js) | stale | REMOVED | the
     8 HOT-DEFERRED JSDoc lines from the ABI-3 inventory (ChatOutbound
     cost_usd/cost_usd_with_children/telegram_chat_id, Photo/Video/
     DocumentOutbound telegram_chat_id, UiPreferencesResponse
     project_last_viewed/project_hidden); the _abi3_deferred_js_extras
     excuse set in tests/test_gateway_parity.py deleted - the browser
     mirror is exact again. api_types.js only shrank (ratchet-safe);
     chat.js untouched (BYTE_DEBT ceiling). node --test: 695/695 pass.
   - GATEWAY_CONTRACT_VERSION carrier switch (api_types.js '6.113.4' ->
     mirror of gateway.schema.GATEWAY_ABI_VERSION '7.0') | DEFERRED to the
     release tact | NOT executed | it rewrites a release version carrier
     and the test_gateway_parity pin that ties the JS constant to the
     VERSION file - version carriers move synchronously in release
     mechanics only.
   - Allowlisted-with-reason remnants (live, NOT removed): settings_
     defaults retirement SSOT; the derived env plane (reviewer_slot_config
     projection + review_model_routes/scope_review_budget readers);
     server_runtime raw-dict retired-model default refresh; provider_
     models declared-model surface; gateway/settings changed-key warning
     triggers; retirement prose in review_execution/preflight_review_run/
     web settings.js.
3. Same-commit collateral: ARCHITECTURE.md review-substrate paragraph now
   states per-row delivery comes from the structured OUROBOROS_REVIEWER_
   SLOTS rows (retired phase-5 envs ignored); plan_review's
   "Set OUROBOROS_REVIEW_MODELS in settings" operator error - a retired
   settings key - now points at Review lanes / OUROBOROS_REVIEWER_SLOTS;
   test_review_owner_facades' facade roster dropped the removed
   ADVISORY_REVIEW_ROUTE_ENV re-export.

## From the F3 adversarial fix-round (base 71e1f13f, 2026-09-01)

Disposition of the 14 findings of the F3.2+F3.3 adversarial wave (sol),
verified against the code before fixing; every fix landed in the four
single-intent commits of this round.

1. FIXED (HIGH, rollback fail-open). `rollback_managed_update` read the
   marker with the permissive `read_update_tx()`, so a FUTURE-schema tx was
   interpreted, re-phased and destructively reset. It now reads
   `read_update_tx_strict()` and refuses typed on `future` BEFORE any marker
   write or reset/checkout/clean (marker byte-identical, worktree and dirty
   local work untouched); a corrupt-stamp marker now refuses on the empty tx
   instead of being interpreted (the permissive reader returned the raw dict
   for a non-integer stamp). RELEASE_INVARIANT surface
   supervisor/update_merge.py: the delta is sanctioned by this fix-round and
   minimal (one strict read + one typed refusal). Pin: the future-schema
   suite now drives the direct rollback entry point (red pre-fix).
2. FIXED (HIGH, false grandfather PASS). rc_audit now looks review state up
   by the skill DIRECTORY basename (the runtime identity,
   skill_loader.load_skill) and verifies the stored PASS hash against the
   runtime's own `compute_content_hash` over the CURRENT payload bytes
   (read-only reuse); a mismatch is an INCOMPATIBLE stale review. Fixtures
   store the real computed hash; the "a"*64 form is now the stale-hash red
   fixture; a basename-vs-manifest.name divergence pin proves the lookup key.
3. FIXED (MEDIUM, exit contract). Chosen and documented in the module
   docstring: an unreadable/unparseable MANDATORY source (manifest, payload
   under a hash-bound PASS) is a BLOCKING `unauditable-source` finding
   (exit 1, its own check id, outside the five scope classes — an
   audit-integrity plane, not an ABI class); traversal OSError and
   report-write OSError map to exit 2, so a bare Python exit 1 can no longer
   read as "incompatibilities found". Pins on both planes.
4. FIXED WITH AN ADAPTED PIN (MEDIUM, bytecode). `sys.dont_write_bytecode =
   True` is set before any runtime import. The requested "prefix in audited
   root -> tree untouched" pin is PHYSICALLY unreachable for the naked
   launcher mode: the interpreter writes ~40 stdlib .pyc files under
   PYTHONPYCACHEPREFIX during startup, BEFORE the script's first line
   (measured on this host). The landed contract, both sides pinned: a prefix
   inside the audited root without startup bytecode suppression is REFUSED
   loudly (exit 2, the guarantee was already violated by the invoking
   environment); with PYTHONDONTWRITEBYTECODE=1 (or -B) the audit runs and
   the audited tree stays byte-for-byte identical.
5. FIXED (MEDIUM, inventory parity). `_iter_skill_dirs` now yields
   `skill_loader._walk_skill_packages(data_root/"skills")` — the runtime's
   own discovery (hidden excluded, `.replaced-`/`.staging-`/`.tmp-` orphans
   excluded, descent stops at a found package), read-only reuse instead of a
   parallel-rules mirror. Pin: orphan/hidden dirs with broken manifests in an
   otherwise clean install stay exit 0.
6. FIXED (MEDIUM, provenance). `sources.tree` appends `-dirty` when
   `git status --porcelain --untracked-files=no` is non-empty (tracked
   scope: untracked files supply no resolved classifier bytes), and
   REPO_ROOT is moved to the FRONT of sys.path (an earlier checkout later in
   PYTHONPATH could otherwise supply the classifiers). Pins: monkeypatched
   dirty/clean `_tree_sha`, sys.path[0] identity.
7. FIXED AS DISCLOSED HONESTY, BEHAVIOR BYTE-IDENTICAL (MEDIUM, ignored
   route). PROVEN OLD BEHAVIOR FIRST: on base 3ba9f452 the fallback loop
   iterated `get_fallback_models` strings and the dispatch lane was the one
   global USE_LOCAL_FALLBACK env flag — so per-candidate dispatch would be a
   BEHAVIOR CHANGE the sweep's byte-identical contract forbids (consuming
   `provider_route == "local"` per candidate would flip dispatch for a
   `"(local)"-suffixed model with the flag unset). Disposition branch taken:
   the ladder no longer fabricates the unconsumed fact —
   `fallback_candidate_targets` leaves `provider_route` the "" sentinel,
   docstrings on both sides and the loop comment state the lane contract,
   and an equivalence pin fixes the loop's global-flag read. The finding's
   mixed-ladder scenarios are therefore the PRE-EXISTING chain semantics,
   disclosed rather than silently re-engineered inside a typing sweep.
8. FIXED (MEDIUM, ABI-4 over-claim). ADOPTION row truth-scoped: typed
   consumers are the fallback ladder, the reviewer slot builders
   (`resolved_review_model_target` — production consumers in
   reviewer_slot_config) and the delegated lane; `get_review_targets`/
   `get_scope_review_targets` are marked typed views WITHOUT production
   consumers in their docstrings (wiring a whole-list consumer is
   review-surface work, not byte-identical); NAMED RESIDUAL:
   plan_review_runtime, review_multi_model and the reviewer parallel vectors
   keep their string ABI — their migration was NOT performed and review
   surfaces were not touched.
9. FIXED (MEDIUM, digest not in tools.jsonl). The DIRECT tools.jsonl record
   now carries `tool_result_meta` (bounded by the ToolResult contract:
   <=32 producer keys, <=8KB, JSON-safe — no secret plane), so the ABI-9
   generation digest survives a failed `persist_call`, exactly as the
   ADOPTION/ledger claims read. Consumers (memory summarizer, /api/logs
   tail) read named fields from JSON lines — the key is additive. Pin: the
   tools.jsonl row of a physical extension call carries
   `extension_generation` with persist_call forced to fail.
10. FIXED (MEDIUM, pre-handler stamp). The stamp now keys on a POSITIVE
    `physical_dispatch` meta fact set only when the handler / child process
    is actually invoked, replacing the `_UNDISPATCHED_CODES` exclusion list;
    the calling-convention resolution moved into its own pre-handler try.
    Pins on all three pre-handler EXTENSION_ERROR paths (runner import,
    disclosure gate, calling convention) plus a contrast pin that a genuine
    handler exception with the same code IS stamped.
11. FIXED (LOW, fallback-reader race). The registry digest is snapshotted
    BEFORE the handler call. Deterministic barrier pin: the handler itself
    republishes the extension mid-call; the result carries the pre-call
    digest while the live digest has moved on.
12. FIXED (MEDIUM, JS-parity hole). DocumentOutbound and
    UiPreferencesResponse joined the exact field loop in
    test_gateway_parity, plus explicit resurrection pins on
    cost_usd/cost_usd_with_children/telegram_chat_id/project_last_viewed/
    project_hidden in BOTH mirrors; the stale ABI-3 "JS mirror switch
    deferred / frozen excused set" ADOPTION claim replaced with the done
    state (the cleanup landed in the F3.3 comma-sweep tact).
13. FIXED (LOW, comma-gate evasion). The model/review comma-split scan is
    AST-level for Python (Attribute call `split`/`rsplit`, first positional
    or `sep=` keyword constant ",", any spacing/quotes/maxsplit) with a
    detector self-test on the evasion spellings; non-Python mirrors keep a
    hardened textual scan (no Python AST exists for them).
14. FIXED (LOW, shrink-only). The DelegationRoute typed-target bridge
    relocated to `provider_models.delegated_route_target` (the resolution-
    seam owner, headroom); ouroboros/subagents.py is back at 1380 lines and
    ouroboros/tools/delegate.py at 1263 — their 3ba9f452 base sizes.
    Monotonicity ENFORCEMENT deliberately not built (out of scope per the
    fix-round brief).

## From the F3 adversarial fix-round 2 (base d1d131df, 2026-09-01)

Disposition of the 8 defects the second adversarial wave (sol) left OPEN
against the round-1 dispositions above; every fix landed in the
single-intent commits of this round.

1. FIXED (HIGH, null stamp read as legacy). `read_update_tx_strict` used a
   plain `.get()`, so an explicit `_schema_version: null` was
   indistinguishable from the accepted pre-7.0 UNSTAMPED form and read
   `valid` — rollback would interpret and destructively act on a damaged
   stamp. A dict-get sentinel now distinguishes key ABSENCE (legacy, valid)
   from a stored `null` (corrupt, like every other non-integer stamp; no
   writer ever stamps null). Pin: null-stamped marker reads `("corrupt",
   {})` and the direct rollback entry point refuses typed — marker
   byte-identical, HEAD unmoved, dirty owner work untouched; `None` joined
   the invalid-stamp loop. Protected update_merge.py delta sanctioned by
   this round and minimal (sentinel + docstring).
2. FIXED (HIGH, admission-state divergence). The auditor's grandfather
   judgment trusted raw stored status/content_hash while the runtime admits
   through `load_review_state` (findings re-aggregation + provenance
   preconditions: official_hub sidecar, native_seed `.seed-origin`,
   owner_attested marker). `_review_gate_for` now calls `load_review_state`
   itself, with the runtime's identity inputs (directory basename, manifest
   type, module-widget shape, skill_dir). Mutation-free reuse:
   `load_review_state` now resolves state paths through the new
   NON-CREATING `skill_state_dir_path` (writers keep the created-on-demand
   `skill_state_dir`). Pin: native_seed PASS without `.seed-origin` →
   INCOMPATIBLE, with the marker (native bucket, hash-exempt) →
   grandfather note; both audits leave the install byte-identical.
3. FIXED (MEDIUM, both planes). (a) The audit walk no longer stands on the
   runtime's fail-soft `_safe_listdir`: `_walk_skill_packages` accepts an
   optional traversal reader (runtime default unchanged) and the auditor
   passes a strict lister whose OSError propagates to the existing exit-2
   traversal handler — an unreadable skills subtree can no longer audit
   clean. (b) `args.json.resolve()` moved under the OSError handler → exit
   2 (REPORT UNWRITABLE), never Python's bare exit 1. Pins on both.
4. FIXED (MEDIUM, fail-open provenance). `_tree_sha` with rev-parse OK but
   `git status` failing/erroring returned the bare SHA as if proven clean.
   Chosen and documented: the suffix `-unknown-dirty-state` (over the
   conservative bare `-dirty`, so an auditor can tell proven-dirty from
   could-not-check); only a zero-exit empty porcelain yields the bare SHA.
   Pins: status exit 128 and status OSError.
5. FIXED (MEDIUM, pre-Popen stamp). The OOP branch stamped
   `physical_dispatch` on EVERY exception from
   `dispatch_extension_tool_subprocess`, though resolve/load/env/staging and
   Popen itself fail BEFORE any child exists. Typed mechanism (not
   text-guessing): `_run_child` stamps a positive child-spawned marker onto
   every exception crossing the spawn boundary (both the on_spawn
   disclosure path and the drain/poll/result protocol path);
   `extension_child_was_spawned(exc)` reads it and the dispatcher's OOP
   error arm keys `dispatched` on it. Pins: pre-spawn failure → no stamp;
   marked post-spawn failure → stamp + digest; unit seam — Popen OSError
   raises unmarked, post-spawn protocol failure raises marked.
6. FIXED (LOW, pre-snapshot gap). Descriptor and legacy-fallback digest are
   now read under ONE lock hold: new combined reader
   `get_tool_with_generation` in `extension_registry_state` (the lock's
   home — extension_loader/plugin_api stay at their size caps); the
   dispatch candidate pre-stamps the snapshot digest onto the detached
   copy, so the separate registry fallback no longer runs on the live path.
   Pin: republish AFTER the descriptor is taken moves the live digest on
   while the dispatch result still names the snapshot generation.
7. FIXED (LOW, detector evasion). The comma-split AST detector now seeks
   the separator in the first TWO positionals and every `sep=` keyword, so
   the unbound forms `str.split(raw, sep=",")` / `str.split(raw, ",")` no
   longer evade; self-test extended with the unbound evasions and the
   `str.split(raw)` negative. Live allowlist counts unchanged.
8. FIXED (LOW, stale references). docs/ARCHITECTURE.md line 80 now names
   the landed bridge `provider_models.delegated_route_target` instead of
   the removed `DelegationRoute.resolved_model_target`. CORRECTION to the
   ABI-4 seam-inventory table above (the `subagents.parse_subagent_harness
   → DelegationRoute` row, written before the round-1 relocation): its
   migration cell reads `DelegationRoute.resolved_model_target()` — the
   landed form is the `provider_models.delegated_route_target(route)`
   bridge (round-1 disposition 14). The table itself stays as written:
   this ledger is append-only, corrections supersede in place of edits.

## From the F3 adversarial fix-round 3 (base 4f894191, 2026-09-01)

Disposition of the 5 defects the third adversarial wave (sol) left OPEN
against the round-2 dispositions above; every fix landed in the
single-intent commits of this round, and every pin was proven RED against
the base implementation before the fix.

1. FIXED (HIGH, grandfather-predicate divergence). The auditor keyed the
   grandfather on `skill_review_gate`'s `executable_review`, which under
   the DEFAULT advisory enforcement admits a BLOCKERS verdict — while the
   real PluginAPI grandfather (`plugin_api_admission_refusal_outcome`)
   accepts only clean|warnings under every enforcement mode. The predicate
   is now literally shared: new `review_status_grandfatherable` in
   `skill_review_status` (clean|warnings only, enforcement-independent) is
   consumed by BOTH the refusal path and the auditor's
   `_admission_state_for`; `skill_review_gate` left the auditor entirely.
   Pin: a hash-matching PASS carrying a critical blocker finding, audited
   with `OUROBOROS_REVIEW_ENFORCEMENT=advisory`, reports plugin-api
   INCOMPATIBLE and never the grandfather note.
2. FIXED (MEDIUM, audit identity ≠ runtime identity). The auditor bound
   review state to the LEXICAL walk name while the runtime resolves the
   directory first and derives state/tool identity from the sanitized
   RESOLVED basename (`load_skill`), refusing identity collisions before
   any review-state read. `_resolved_skill_identities` now mirrors that:
   `skill_dir.resolve()` (failure → blocking unauditable-source finding),
   dedup on the resolved path like the runtime inventory, identity =
   `_sanitize_skill_name(resolved.name)`; two directories sanitising to
   one identity emit a blocking collision finding and never reach
   `load_review_state`. Pins: a symlinked skill grandfathers only on the
   TARGET-basename state (link-name state → INCOMPATIBLE); a collision
   pair yields the blocking finding and no plugin-api judgment.
3. FIXED (MEDIUM, mandatory-source traversal class). `task_results`
   listing stood on fail-soft `Path.glob`, which on supported Python 3.10
   suppresses PermissionError — an unreadable directory audited clean.
   New `_strict_json_files` (same direct-child `*.json` selection, OSError
   raises to the exit-2 handler); a per-file read OSError now also
   propagates (exit 2) instead of masquerading as a "malformed →
   quarantine" verdict, and the `ui_preferences` reader keeps tolerating
   content damage but no longer swallows read OSError. The class sweep:
   settings already raised `InstallUnreadable` (exit 2), skills already
   used the strict lister — task_results and ui_preferences were the
   remaining fail-soft members. Pin: chmod-0 `task_results` → exit 2
   ("audit traversal failed"), never exit 0.
4. FIXED (MEDIUM, resolve-error exits). `data_root.resolve()` and the
   pycache-prefix resolve ran outside any handler, and the report-path
   handler caught only OSError — a 3.10 pathlib symlink loop raises
   RuntimeError, giving Python's bare exit 1 ("incompatibilities found" to
   automation). All three resolve points now catch (OSError, RuntimeError)
   and map to exit 2 (INSTALL UNREADABLE / READ-ONLY GUARANTEE UNPROVABLE
   / REPORT UNWRITABLE). Pins: RuntimeError from the data-root resolve →
   exit 2; RuntimeError from the report-path resolve → exit 2.
5. FIXED (MEDIUM, spawn-marker gaps). (a) The process REGISTRATION between
   Popen and the protected block could raise unmarked: the whole post-Popen
   span (registration, on_spawn disclosure, protocol body) now lives in ONE
   try whose handler stamps every BaseException — the separate on_spawn
   cleanup arm collapsed into the shared finally (same kill/reap/unlink
   semantics). (b) A cleanup failure in that finally could REPLACE a marked
   in-flight exception with an unmarked one: the finally's own guard now
   stamps the replacing exception too (original stays chained as context).
   (c) `_mark_child_spawned` silently tolerated an unattachable marker: a
   weak side-table (`_spawned_marker_fallback`, consulted by
   `extension_child_was_spawned`) now records the fact for exceptions that
   refuse setattr; the only unmarkable residue (no attributes AND no
   weakref support) is logged, never dropped. Pins: registration exception
   → stamped; cleanup exception over a marked one → stamp preserved on
   both; a setattr-refusing exception → stamped via the side-table.
