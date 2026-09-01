# PERSISTENCE.md — durable data-plane inventory (CPL-4)

Every durable entity under the runtime data root (`DATA_DIR`, default
`~/Ouroboros/data/`), with four decisions per entity (plan §7 item 4):
**schema_version** (present / not needed / needed→candidate),
**migration** path, **retention** (bounded / rotated / unbounded-accepted /
unbounded→candidate), and **reset** semantics (what deleting the entity while
the server is stopped does). Decisions are LOCAL per entity — there is no
generic persistence framework, by design.

Provenance disclosure: the plan references "§16 findings" (undocumented
planes, unbounded ledgers, mismatched temp); that findings document is not
recoverable in the plan, spec, or campaign archives, so this inventory was
built from scratch by an AST scan of every `data/`-path constructor in
`ouroboros/`, `supervisor/`, `server.py` and `launcher.py`, cross-checked by
manual reads of every writer. The scan is pinned as a verify test:
`tests/test_persistence_inventory.py` re-runs it and requires every scanned
data-relative path to be covered by a row here (count-anchored both ways).

## Shared idioms (the vocabulary the rows use)

- **Stamp** — the opt-in `_schema_version` key from
  `ouroboros/contracts/schema_versions.py` (ABI-2): missing/invalid reads as
  legacy 0; existing files are not retrofitted. Some pre-ABI-2 entities carry
  their own spelling (`schema_version`, `schema`, `state_version`) — noted per
  row; new stamps use the shared key.
- **GC retention** — `ouroboros/retention.py`: one owner knob
  `OUROBOROS_GC_RETENTION_DAYS` (default 7, clamped 1–365; legacy per-subsystem
  keys migrate). Governs ONLY subagent worktrees, headless/task drives, task
  trees, and service logs.
- **Rotation** — `supervisor/state.py::rotate_jsonl_log_if_needed`: >800 KB →
  atomic rename to `archive/<prefix>_<ts>.jsonl` under the append lock.
  Applied on the supervisor tick to `chat.jsonl`, `progress.jsonl` and — since
  the CPL4-C1..C4 train — `events.jsonl`, `tools.jsonl`, `supervisor.jsonl`,
  `task_reflections.jsonl`. Chain readers enumerate
  `archive/<stem>_*.jsonl` name-sorted (chronological by construction);
  `utils.jsonl_chain_handles` is the rotation-race-safe traversal
  (open-live-first + inode dedup).
- **`archive/` is durable history, never GC'd** (BIBLE P1): readers backfill
  from rotated segments; no retention sweep touches `archive/` and none may be
  added.
- **Atomic writes** — `atomic_write_json`/`atomic_write_text` (tmp+rename) and
  `update_json_locked` (sidecar `<file>.lock`); JSONL appends go through
  `append_jsonl` (O_APPEND + sidecar lock) unless noted.
- **Candidate fixes** — rows marked `→ CPL4-Cn` name real gaps; the candidate
  table lives in the campaign ledger (`docs/v7next/LEDGER_CORRECTIONS.md`,
  F5 lane B section). This lane changes no persistence code (plan rule).

## 1. Root files

| Path | Writer | Format | schema_version | Retention | Reset |
|---|---|---|---|---|---|
| `settings.json` | `ouroboros/config.py` save_settings (lock `settings.json.lock`, integrity guard) | JSON env-key map | none — not needed: migration is per-key inside `load_settings` (legacy keys migrate on read); external sha256 pin `OUROBOROS_SETTINGS_SHA256` makes it immutable when set | fixed-size overwrite | recreated from defaults; ALL owner secrets/modes lost — never delete casually |
| `settings.json.lock`, `*.lock` sidecars, `locks/**` | `ouroboros/platform_layer.py` lock family (+ `supervisor/state.py`, `supervisor/update_merge.py`, `ouroboros/skill_lifecycle_queue.py`) | O_EXCL lockfiles (unlinked on release) or flock files (persistent) | none — not needed | self-healing (mtime/pid staleness) | zero durable state; deleting while stopped is a no-op |

## 2. `state/` — singletons (fixed-size overwrite)

| Path | Writer | schema_version | Retention | Reset |
|---|---|---|---|---|
| `state/state.json`, `state/state.last_good.json` | `supervisor/state.py` (lock `locks/state.lock`) | `_schema_version: 1` (ABI-2, stamp-on-write) | overwrite; legacy keys popped on load | falls back to last_good, then mints defaults; loses owner binding, spend counters, session id |
| `state/queue_snapshot.json` | `supervisor/queue_snapshot.py` | `_schema_version: 1` (ABI-2) | overwrite; self-expires (max_age 900 s) | absent = 0 restored; queued-unstarted tasks silently dropped |
| `state/advisory_review.json` | `ouroboros/review_state.py` (lock `locks/advisory_review.lock`) | own pre-ABI-2 spelling `state_version: 3` (+duplicate `schema_version`) — kept; `_schema_version` deliberately avoids this key | bounded on write: runs 10, attempts 50, debts 50; `open_obligations` coalesced | recreated empty; recorded blocking obligations forgiven, commit gate demands fresh review (fail-closed) |
| `state/scheduled_tasks.json` | `supervisor/queue_schedules.py` | `schema_version: 1` — but only defaulted on read, not authored on write → CPL4-C7 | consumed `once` receipts kept forever, 2 MB WARN names pruning unimplemented → CPL4-C7 | owner cron/once schedules lost; skill-manifest schedules resync automatically |
| `state/terminal_deliveries.json` | `supervisor/terminal_delivery.py` | own `schema_version: 2` | bounded: delivered 512, pending 64, replays 5 | dedupe + owed-outbox lost: possible double- or never-delivery of one buffered terminal answer |
| `state/cancel_intents.json` | `ouroboros/cancel_intents.py` | own `schema_version: 1` | self-draining (settled rows leave) | in-flight cancels lost: cancelled-unsettled task revives as pending; forensics survive in supervisor.jsonl |
| `state/capability_evidence.json` | `ouroboros/capability_evidence.py` | none — accepted (self-healing cache; TTLs on read) | TTL-expired on read but never deleted; unbounded route keys → CPL4-C8 | recreated; ≥1M-context gates fail closed to `unknown`, owner acks must be re-given |
| `state/evolution_campaign.json` | `supervisor/evolution_lifecycle.py` (CAS under state.lock) | own `schema_version: 1` (campaign) / 2 (active_transaction) | bounded histories (50) | in-flight self-modification transaction unabsorbable; anti-repeat fingerprints lost |
| `state/evolution_metrics_cache.json` | `ouroboros/utils.py` | own `schema: 1` (strictly validated) | one point per git tag, no prune — accepted (derived cache) | pure cache; recomputed from git |
| `state/projects.json`, `state/project_task_bindings.json` | `ouroboros/projects_registry.py` (sidecar locks) | `_schema_version: 2` / `1` (ABI-2) | never age-pruned (owner curates); deletes are durable tombstones | tombstones live here: losing it can resurrect deleted project rooms (marker unlink mitigates) |
| `state/ui_preferences.json` | `ouroboros/gateway/ui_preferences.py` | none — not needed (defaults contract + strict unknown-key rejection; retired keys dropped on write) | bounded (widget_order 200, cursors 1000) | cosmetic; defaults restored |
| `state/server_port` | `ouroboros/server_entrypoint.py` | none — not needed (1-line transport fact) | overwrite; pre-start unlink | readers fall back to default port |
| `state/server_process.json` | `launcher.py` | none — not needed (identity-verified before use) | one-shot, self-deleting | stray-server reap skipped once; stale file never dangerous |
| `state/auth_secret.key` | `ouroboros/server_auth.py` | none — not needed | write-once | regenerated; one forced owner re-login (accepted) |
| `state/worker_pids.json` | `supervisor/worker_pool_lifecycle.py` | none — not needed (legacy reap path; SSOT is process_ledger) | overwrite ≤ pool size | orphan reap falls to the custody reaper |
| `state/extension_companions.json` | `ouroboros/extension_companion.py` | none — not needed (runtime snapshot) | bounded by live companions | launcher loses force-kill map; leaked child processes possible |
| `state/crash_report.json` | none in this tree — reader-only orphan (`agent_startup_checks`, `context_health`) → CPL4-C9 | none | sticky until owner deletes (pinned by tests) | clears the CRITICAL crash-rollback health line |
| `state/pending_restart_verify.json` | `ouroboros/tools/control_runtime.py`, `supervisor/evolution_lifecycle.py` | none — not needed (one-shot marker, consumed by rename to `.claimed.<pid>.json`) | one-shot | evolution restart refused fail-closed without its receipt |
| `state/deep_self_review_context.json` | `ouroboros/deep_self_review.py` | none — not needed (write-only audit artifact by design) | last run wins | audit trail of last deep review lost; breaks nothing |
| `state/advisory_overrides.json` | `ouroboros/tools/review.py`, `ouroboros/tools/claude_advisory_review.py` | none — not needed (visibility counter; each bypass also durably in events.jsonl) | bounded (recent 10) | bypass counter lost; no gate reads it |
| `state/reviewer_slot_last_execution.json` | `ouroboros/reviewer_slot_config.py` | none — not needed (disclosure, never enforcement) | capped 64, oldest evicted | UI disclosure line lost until next review |
| `state/usage_import_watermark.json` | `ouroboros/usage_legacy_import.py` | none — completion boolean is the contract | write-once | legacy import re-runs, fingerprint-deduped against the ledger (safe) |
| `state/panic_stop.flag`, `state/owner_restart_no_resume.flag` | `ouroboros/server_control.py`, `server.py` (atomic pair) | none — not needed (one-shot flags, consumed on boot) | consumed | absence is the default; next boot auto-resumes |

## 3. `state/` — append-only ledgers

| Path | Writer | schema/record marker | Retention | Reset |
|---|---|---|---|---|
| `state/usage_attempts.jsonl` (+ `.quarantine.jsonl`, lock) | `ouroboros/usage_ledger.py` single chokepoint (own O_APPEND+fsync under named lock — NOT append_jsonl) | no `_schema_version`; its own validated contract: dense `seq`, `kind` discriminator, `state` machine, per-row `candidate_measurement_kind`, attribution `physical_attempt_v1` — accepted | unbounded; 20 MB WARN ("compaction is the remediation, tracked as issue"); torn tails quarantined, never GC'd → CPL4-C6 | monetary history destroyed, `seq` restarts, budget fences read $0; watermark survives so legacy import will NOT re-run — deleting the ledger alone is unrecoverable |
| `state/process_ledger.jsonl` | `ouroboros/process_custody.py` (spawn chokepoint) | none — downgrade-safe field split (`start_time`/`start_time_boot`) is the versioning device — accepted | self-compacting: reapers rewrite survivors-only | prior-generation supervised processes permanently orphaned (fingerprint index lost) |
| `state/evolution_checkpoints.jsonl` | `ouroboros/evolution_checkpoints.py` | `schema_version: 1` on every row (+`kind` on outcome rows) | unbounded append; read bounded (last 200) — accepted (structured solve-capability history is the product) | absorbed/abandoned objectives can be re-proposed (BUG3 regression) |
| `state/consciousness_observations.jsonl` | `ouroboros/consciousness.py` (append_jsonl under the shared sidecar-lock seam) | rows typed by the observation contract; no version key — accepted (bounded truthful replay reads the tail) | unbounded append; render bounded (last 10 / 12 K chars) → CPL4-C23 | Background Consciousness observation inbox lost; replay starts empty |

## 4. `state/` — directories

| Path | Writer | schema_version | Retention | Reset |
|---|---|---|---|---|
| `state/skills/<name>/` owner state (`review.json`, `review_job.json`, `grants.json`, `enabled.json`, `deps.json`, `self_authored.json`, `owner_attestation.json`, `accepted_rebuttals.json`, `health.json`, provenance sidecars, `auto_repair.json`, `presence_profile_state.json`) | `ouroboros/skill_loader.py`, `skill_review_runner.py`, `skill_owner_attestation.py`, `skill_review_cycles.py`, `extension_health.py`, `marketplace/*`, `ouroboros/gateway/marketplace.py`; allowlist SSOT `contracts/skill_payload_policy.py` | `deps.json`/`self_authored.json`/provenance: `schema_version: 1`; `review.json`/`enabled.json`/`grants.json`/`review_job.json`/`owner_attestation.json`/`accepted_rebuttals.json`: none, pinned by `content_hash` → CPL4-C10 | no GC; uninstall removes only `deps.json`, the rest outlives the payload forever → CPL4-C11 | absent state = disabled + pending review + grants revoked (fail-closed); `owner_attestation` absence invalidates its verdict |
| `state/skills/<name>/review_history.jsonl` + `review_dispatch/` (legacy `review_dispatch.json`) | `ouroboros/skill_review_history.py` | rows carry `usage_attribution_schema: physical_attempt_v1`; no version key — accepted (derived-counter SSOT, P7) | history unbounded per skill — accepted with BOUNDED reads (CPL4-C12): every reader windows the 4 MB tail (`find_history_job_bounded` idiom); lifecycle terminal rows persist their ordinals so counters stay exact inside the window (a group aged past it restarts low — under-counts, never over-blocks); per-skill archive rotation declined (no per-skill archive plane; disclosed) | review-cycle ceiling resets to zero; paid dispatches become free again |
| `state/skills/<name>/` transport dirs: `extension_calls/`, `__extension_imports/` | `ouroboros/extension_process_runner.py`, `extension_import_staging.py` | none — not needed (per-call transport files, staged import trees) | per-call files consumed; import leaves reaped owner-dead+grace | transient; recreated per call |
| `state/delegate_recovery/`, `state/delegate_recovery_transactions/` (+`active.json`), `state/delegate_supervision/` | `ouroboros/delegate_recovery.py`, `delegate_supervision.py` | own `schema: 1` on supervision/transactions; recovery rows fingerprinted, unversioned | never unlinked — one file per crashed/restarted task forever → CPL4-C13 | interrupted delegated runs cancelled instead of adopted; duplicate wake replay; planned handoffs vetoed |
| `state/delegate_actor_claims/*.lock`, `state/.payload_delegation_claim.lock` | `ouroboros/delegate_custody.py`, `delegate_start_claims.py` | none — locks | unlinked on release | no durable state |
| `state/code_intel/<root-sha>/inventory.json` | `ouroboros/code_intelligence.py` | own `schema_version: 2` (older/malformed rebuilt silently) | per-repo rewrite in place; root-dir count unbounded, stale roots never expire → CPL4-C14 | pure derived cache; one full re-index |
| `state/extension_reconcile/` (+`failed/`) | `ouroboros/extension_reconcile_queue.py` | none — not needed (one-shot markers) | consumed by server loop; after 5 attempts moved to `failed/` and kept forever → CPL4-C15 | pending worker→server reconciles lost; re-toggle heals |
| `state/workspace_executor_processes/` | `ouroboros/workspace_executor.py` | own `schema_version: 1` + owner tag | unlink on stop; stale rows filtered at read (pid/cmd-sha) | service processes survive unreaped |
| `state/acceptance_fence_acks/` | `supervisor/events_worker_reports.py` (writer), `ouroboros/agent.py` (read+unlink) | none — transport ack | inline GC on write (255 newest / 3600 s) | waiting worker fails closed (TimeoutError) |
| `state/headless_tasks/<id>/` (child data drives) | `ouroboros/headless.py` | child `state/state.json`: `schema_version: 1` | GC-retention prune at startup (terminal + age; skips artifacts-not-terminal / refs-unpromoted) | in-flight child drives and unpromoted child refs lost |
| `state/cx/` (managed Claudexor runtime) | `ouroboros/claudexor_runtime.py` | meta files versioned (`_NODE_META_SCHEMA_VERSION: 2`, pin 1) | deliberate keep-all (rollback selects older pins); staging/displaced temporaries reaped | re-downloaded on demand; nothing durable lost |
| `state/betterleaks/` (managed runtime + cache) | `ouroboros/betterleaks_runtime.py` | own manifests (`schema_version: 1`) | keep-all archive cache — accepted (managed runtime) | re-downloaded on demand |
| `state/pycache/`, `state/python-userbase/` | `launcher.py`, `ouroboros/packaged_cli.py`, `launcher_bootstrap.py` (PYTHONPYCACHEPREFIX / PYTHONUSERBASE) | none — caches | no GC; `python-userbase` is a documented manual-recovery hazard (outranks bundle site-packages) | `pycache`: always safe (recompile); `python-userbase`: deletes REAL user-installed deps |
| `state/project_source_locks/` | none in this tree — orphan plane seen in live layouts (removed feature leftover) | none | n/a | harmless; nothing reads or recreates it |

## 5. `logs/`

| Path | Writer | Record marker | Retention | Reset |
|---|---|---|---|---|
| `logs/chat.jsonl` | `supervisor/message_bus.py` (+presence, project summaries) | `direction` + optional `type`; no version — accepted (projection replayed by chain-aware readers) | rotated 800 KB → `archive/chat_*.jsonl`; archive chain WARN at 100 MB | newest generation lost; consolidation cursor reports gap (recoverable) |
| `logs/progress.jsonl` | `supervisor/message_bus.py` (+consciousness, plan review) | `type: send_message`, `is_progress` | rotated 800 KB → `archive/progress_*.jsonl`; 8 MB WARN = rotation broken | current segment lost; readers archive-chain-aware |
| `logs/events.jsonl` | ~60 modules via `append_jsonl` (+`delegate_custody.emit`) | universal `type` discriminator — accepted (per-type payloads owned by emitters) | rotated 800 KB → `archive/events_*.jsonl` (CPL4-C1); custody readers (replay, fault tail-scan, `complete_custody_rows`, settled-terminal chain cursor, legacy-usage import, swarm rollup, worker-boot verify) are chain-aware; 8 MB live WARN = rotation broken; 100 MB chain WARN = replay degradation | delegated-run custody destroyed (chain incl. archive segments): open runs invisible/unreapable; lineage, citations, legacy-usage source lost |
| `logs/tools.jsonl` | `ouroboros/loop_tool_execution.py` (+budget-drive mirror, consciousness) | `type: tool_call`, untruncated args | rotated 800 KB → `archive/tools_*.jsonl` (CPL4-C2); tail readers (api_logs_tail, task_events) archive-backfill; 8 MB WARN = rotation broken | untruncated tool record + `result_ref` pointers lost |
| `logs/supervisor.jsonl` | supervisor family, `process_custody`, gateway control, server shutdown | `type` (+secondary `event_type`) | rotated 800 KB → `archive/supervisor_*.jsonl` (CPL4-C3) + 8 MB tripwire; tail readers (`memory.read_jsonl_tail`, api_logs_tail) archive-backfill | reap receipts, rescue disclosures, shutdown causes lost |
| `logs/task_reflections.jsonl` | `ouroboros/reflection.py` (+ project-scoped copy under `projects/<id>/logs/`) | full rows unversioned; pointer rows `type: project_reflection_pointer` | rotated 800 KB → `archive/task_reflections_*.jsonl` (CPL4-C4) + 8 MB tripwire; tail-20 read archive-backfills; project-scoped copies follow project retention (never age-pruned) | inter-task memory-carry signal lost |
| `logs/containment_faults.jsonl` | `ouroboros/delegate_custody.py` (mirrored to events.jsonl) | `type` ∈ CONTAINMENT_FAULT/RESOLVED joined on run_id | unbounded BY DESIGN — read whole so an open fault never ages out — accepted | health invariant degrades to the 4 MB events tail scan (the regression this file fixed) |
| `logs/agent_stdout.log` | `launcher.py` pipe-copy thread | unstructured text | bounded ~8 MB (2 MB × `.1..3` backups, rotated by the copy thread — CPL4-C5) | pre-logging crash output lost; nothing parses it |
| `logs/server.log` (+`.1..3`), `logs/launcher.log` | stdlib `RotatingFileHandler` (`server.py`, `launcher.py`) with secret-redacting filter | text | bounded ~8 MB (2 MB × 4) — the model citizen | stdlib log history lost; nothing parses it |

## 6. `memory/` (Ouroboros cognition — operator read-only)

| Path | Writer | Marker | Retention | Reset |
|---|---|---|---|---|
| `memory/identity.md` | `ouroboros/tools/control_runtime.py` (full overwrite, ≥50 chars, shrink notice) | none | unbounded document — accepted (identity is the product) | reseeded default; identity lost (journal keeps history) |
| `memory/scratchpad.md` + `scratchpad_blocks.json` | `ouroboros/memory.py` (derived, regenerated from blocks under lock) | none | bounded: 10 blocks, eviction journaled first (fail-closed) | regenerated; evicted history in journal |
| `memory/WORLD.md` | `ouroboros/world_profiler.py` (write-once) | none | fixed | regenerates on restart — deletion IS the refresh mechanism |
| `memory/registry.md`, `memory/deep_review.md` | `ouroboros/tools/memory_tools.py` (section RMW), `ouroboros/agent.py` (overwrite) | none | unbounded / last-wins — accepted | recreated lazily |
| `memory/dialogue_blocks.json` + `dialogue_meta.json` | `ouroboros/consolidator.py` (locked atomic) | none | bounded by era compression (10 blocks, oldest 4 compressed) | blocks: compressed biography irreproducible; meta: full re-consolidation (cost, not loss) |
| `memory/dialogue_summary.md` | none — legacy read-only (reader in context.py) | none | frozen | legacy artifact; nothing writes it |
| `memory/knowledge/**` (topic .md + `index-full.md` + `patterns.md`) | `ouroboros/tools/knowledge.py`, `consolidator.py` (index rebuild), `reflection.py` (patterns CAS rewrite) | none | topic files unbounded — accepted (curated by consolidation); backlog topic merge-only fail-closed | recreated lazily; knowledge lost |
| `memory/*_journal.jsonl`, `memory/knowledge_history.jsonl`, `memory/knowledge/patterns_history.jsonl` | `ouroboros/memory.py`, `tools/control_runtime.py`, `tools/knowledge.py` (RAW `open("a")` — no append lock), `reflection.py` | scratchpad journal: `type` rows; others unversioned full-text snapshots | UNBOUNDED and worst-offenders by bytes: full old+new document text per write (O(doc×edits)) → CPL4-C16; unlocked appends → CPL4-C17 | undo/provenance record lost (live .md survives); eviction/rewrite paths fail closed when journal append fails |
| `memory/owner_mailbox/<task>.jsonl` + `.acks.jsonl` | `ouroboros/owner_mailbox.py` (append-only; revocation appends, reader resolves) | `kind` discriminator | lifecycle-bounded: unlinked at task terminal; a task that dies off-path leaks its mailbox → CPL4-C18 | undelivered owner directives + restart-surviving hurry latch lost; acks lost ⇒ re-delivery |

## 7. Skills payloads, tasks, uploads, projects, services

| Path | Writer | Marker | Retention | Reset |
|---|---|---|---|---|
| `skills/{native,clawhub,ouroboroshub,external}/<name>/**` (+`.staging/`, `.ouroboros_env/` with `cache/ tmp/ home/`) | `ouroboros/marketplace/*`, `launcher_bootstrap.py` seed, agent self-authoring | provenance sidecars `schema_version: 1`; env `fingerprint.json: 1` | no age GC (payloads are installed software); staging rmtree'd per install, crash orphans recognized by name fragments; package caches live with the skill | bucket recreated empty; native seeds NOT re-seeded (deletion intent preserved) except post-bootstrap set; orphaned `state/skills/` rows keep stale grants → CPL4-C11 |
| `task_results/<id>.json` | `ouroboros/task_results.py` (locked merge) | `_schema_version: 1` (ABI-2); unstamped/future/malformed → quarantine, no conversion (Q8=B) | UNBOUNDED — one file per task forever, no GC — accepted for 7.0 (lifecycle authority; prune candidates need an owner decision, see CPL4-C19) | lifecycle authority lost; drive prunes degrade to age-only; strict authority reads break |
| `task_results/quarantine/` | `ouroboros/task_result_schema.py` (same-dir rename) | quarantined bytes unchanged | NEVER GC'd (pinned); recovery is manual owner re-stamp | quarantined evidence lost |
| `task_results/artifacts/<id>/**` (+`verification_receipts.jsonl`), `task_results/artifact_versions/` | `ouroboros/artifacts.py`, `headless.py`, `outcome_receipt_store.py` | artifact manifest `schema_version: 1`; scratch manifest 2 | artifact versions bounded (5 per name); artifacts live with their result | deliverable bytes lost; results keep dangling manifests |
| `task_drives/<id>/**` (+`tmp_scripts/`) | `ouroboros/headless.py`, `tools/tool_context.py`, `tools/shell.py` | child stamps as above | GC-retention prune at startup (terminal + age, default 7 d); `data/tmp_scripts` fallback never swept → CPL4-C20 | scratch lost; canonical artifacts survive |
| `task_trees/<root>/blackboard.jsonl` | `ouroboros/task_tree_ledger.py` | rows unversioned; snapshot digest `schema_version: 1` | GC-retention prune at startup (root terminal + age) | swarm coordination facts lost for live trees |
| `state/subagent_worktrees.json` (registry; checkouts live OUTSIDE data root) | `ouroboros/subagent_worktrees.py` | none — malformed → typed refusal (absent = empty is the designed asymmetry) — accepted | prune_orphans (age + missing checkout; skips delegated_exec) + custody-cross-checked snapshot prune (fail-closed on unreadable custody) | permanent leak of checkouts + pinned refs (nothing else names them) |
| `uploads/**` (+`screenshots/`, `views/`, `.uploading` temps) | `ouroboros/gateway/files.py`, `tools/browser.py`, `tools/vision.py`, `server_owner_routing.py` | none — raw owner bytes | NO retention of any kind; 50 MB per-file cap; delete is owner-explicit only → CPL4-C21 | chat attachments dangle (readers skip missing); staged task copies survive |
| `services/<task>/*.log` | `ouroboros/workspace_executor.py`, `tools/services.py` | none — raw text; archived content becomes observability blob with events.jsonl receipt | GC-retention prune at startup (archive-then-unlink); per-task terminal archive; oversize logs retained live | live tails lost; archived blobs survive |
| `projects/<id>/**` (knowledge, journal, workpad, reflections, `.project.json` marker) | `ouroboros/project_facts.py`, `projects_registry.py` | marker unversioned; registry stamped (§2) | NEVER age-pruned (owner curates; delete = durable tombstone) | per-project memory lost; registry row survives, room reappears empty |
| `archive/**` (rotated segments, `rescue/`, `usage_import/`, `managed_repo/`) | rotation + `supervisor/git_ops_rescue.py`, `usage_legacy_import.py`, `launcher_bootstrap.py` | segments inherit source shape; usage_import carries sha256 sidecar | UNBOUNDED BY DESIGN — durable history, never GC'd (P1) — accepted | memory horizon truncated; rescue copies of uncommitted work destroyed |
| `observability/{calls,blobs,salvaged}/**` | `ouroboros/observability.py` (private 0700/0600, CAS gzip) | call manifests `schema_version: 1` + custody/redaction honesty markers; blob refs sha-verified on read | preserved indefinitely BY CONTRACT (prune function counts, never deletes); `OUROBOROS_OBSERVABILITY_RETENTION_DAYS` is parsed but inert → CPL4-C22 | every recorded `result_ref`/`manifest_ref` dangles (strict readers raise); salvaged outputs unrecoverable |
| `claudexor/**` | EXTERNAL writer — the claudexord daemon (Ouroboros only mkdirs, appends `daemon.log`, writes `ouroboros-owned.json` marker) | marker unversioned | daemon-owned; grows unbounded under our root — disclosed external plane | owner harness logins/profiles lost (fresh device-auth required) |
| `playwright-browsers/` | `ouroboros/tools/browser.py` (vendor install) | none — vendor tree | no GC — accepted (vendor cache) | re-downloaded on next browser use |

## Reset ladder (summary)

Always safe (pure caches, recreated): `state/pycache`, `state/code_intel`,
`state/evolution_metrics_cache.json`, `playwright-browsers/`, `state/cx`,
`state/betterleaks`, lock files, `state/server_port`.
Safe with bounded cost: `WORLD.md` (regenerates), `dialogue_meta.json`
(re-consolidation), `state/usage_import_watermark.json` (safe re-import),
`ui_preferences.json`, `auth_secret.key` (one re-login).
Fail-closed losses (system stays correct, work/authority is forgone):
skill state dirs, `advisory_review.json`, `capability_evidence.json`,
`pending_restart_verify.json`.
Dangerous (authority/history destruction): `settings.json`,
`state/usage_attempts.jsonl`, `task_results/**`, `logs/events.jsonl`,
`memory/**`, `archive/**`, `observability/**`, `state/subagent_worktrees.json`
(leak), `claudexor/**`, `state/python-userbase` (real deps).
