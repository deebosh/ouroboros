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
