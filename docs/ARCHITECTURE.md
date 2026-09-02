# Ouroboros v6.114.0 — Architecture & Reference

This file is NOT a changelog. Version history lives in README.md, git tags, and commit log.

This is the present-tense operational map of Ouroboros (BIBLE P6), in three layers: structure (what exists and where), operation (files, env keys, state paths, endpoints, flows), and rationale. Every important WHY stays here at least briefly; mechanism detail lives in the module docstring the map points to by name. Rationale must be self-contained — future maintainers should not need old commits to understand why a guard, review gate, or lifecycle exists.

---

## 1. High-Level Architecture

```
User
  │
  ▼
launcher.py (PyWebView)       ← desktop window; immutable release-reviewed outer shell running the packaged copy outside managed hot-swap
  │
  │  spawns subprocess
  ▼
server.py (Starlette+uvicorn) ← HTTP + WebSocket on configurable host:port (default localhost:8765; Docker/non-loopback via OUROBOROS_SERVER_HOST=0.0.0.0)
  │
  ├── web/                     ← Web UI (SPA with ES modules in web/modules/; §3)
  │   └── modules/review_presentation.js, review_dom_patch.js, harness_presentation.js ← Review Checkpoint grouping/status, keyed DOM reconciliation, neutral harness identity presentation; read-side only
  │
  ├── supervisor/              ← Background thread inside server.py
  │   ├── active_activity.py   ← In-memory registry of in-flight direct/ephemeral chat turns (`DirectActivityRegistry`); feeds `/api/state` `active_direct_turns` and WS typing frames (`activity_id`, `client_message_id`, `phase`, `kind`); no queue records
  │   ├── message_bus.py       ← Queue-based local message bus (Web UI + reviewed transport skills)
  │   ├── workers.py           ← Multiprocessing worker pool (fork on Linux, spawn on macOS/Windows)
  │   ├── state.py             ← Persistent state (state/state.json) with file locking
  │   ├── queue.py             ← Task queue (PENDING/RUNNING lists) + activity-based timeout enforcement; the ONE task-state authority — the lifecycle/publication/transition modules below extend it without becoming second authorities; exact-attempt main-LLM in-flight state spares only the idle rail
  │   ├── cognitive_operations.py ← Typed in-memory LLM/review/VLM operation leases for the idle rail; no scheduler or durable timing ledger
  │   ├── task_admission.py    ← Token-owned admission reservations fence duplicate user-ingress ids before Project/workspace/attachment side effects; schedule-dispatch refusals project through the same boundary; queue.py stays the state authority
  │   ├── task_lifecycle.py    ← Cancellation custody — the ONE settle owner of durable cancel intents: claim → capture → confirmed death → natural-completion re-check → owed delivery registration → settle → delivery/cleanup, plus the `sweep_cancel_intents` watchdog and the queue-owned root-budget admission fence; every custody rule it enforces is stated once in §10 (cancellation custody)
  │   ├── cancel_publication.py ← Cancellation settlement publication (split from task_lifecycle.py at the size boundary, re-imported there): typed CANCEL_* outcome vocabulary, artifact-honest cancelled result fields, physical-ledger cost reconstruction, salvage adapter, owed-before-settle outbox registration, publication of the STORED terminal truth, capture-miss terminalization/delivery adapter
  │   ├── queue_transitions.py ← Queue-owned lifecycle transitions that are not cancellation custody: acceptance-fence open/inspect/seal, explicit budget resume, typed `stop_evolution_tasks` (per-task typed outcomes through the durable-intent ingress, never an in-place prune; an incomplete stop leaves the campaign OPEN via the durable `evolution_owner_stopped` flag, the settle-time backstop in events.py closes it when the last live evolution task settles, and both start ingresses clear the flag BEFORE minting a fresh campaign), and fenced Project deletion (cascade only lineage ROOTS — descendants fall with their trees, one cascade and one summary per tree; tombstone only after provable quiescence; a settled-but-LIVE root still mints the coordination intent, and wind-down defers and RE-CHECKS bounded instead of re-running the cancel pass over a settled-lingering set, which would deliver duplicate owner summaries); imports nothing from task_lifecycle; `supervisor.queue` re-exports these names
  │   ├── terminal_delivery.py ← Durable terminal-answer delivery seam: restart-surviving `delivery_id` dedupe + bounded PENDING outbox `state/terminal_deliveries.json` (owed before enqueue, cleared in the delivering write, replayed on boot and on the supervisor tick), shared by natural final answers (every non-ephemeral root registers at durable-result persistence), cancel salvage, cascade digest, and non-retry reap; the cascade digest enumerates descendants by ANCESTRY (parent-chain walk, never `root_task_id` equality); eviction past outbox capacity is disclosed via typed `terminal_delivery_exhausted`, never a silent pop; salvage messages carry a bounded preview plus a full-copy receipt (path, size, full 64-hex sha256 or an explicit marker) and route by lineage chat — no resolvable chat records a typed `terminal_delivery_handoff` row; reads/mutations are row-strict per §10; the delivery id digests only the STABLE part (task id + status framing + core answer) so a replay whose rebuilt note shrank dedups instead of double-sending
  │   ├── task_reaper.py       ← Off-loop single-owner reaper thread: kill/join/archive/respawn a timed-out worker; STRICT fail-closed — an unconfirmed death holds the slot `reaping`, leaves the task RUNNING, emits `task_reaper_wedged` + an owner /restart hint (no terminal/retry/respawn while the worker may be alive); after confirmed death it reconciles the task's open delegated runs through the custody seam before the retry/respawn decision and discloses still-open runs; mints no cancel intents
  │   ├── owner_stop.py        ← Owner graceful stop: `finalize_then_cancel` policy as an axis on the SAME durable cancel intent (monotonic — immediate HARDENS a pending graceful, never softens back; hardening revokes an unread control via mailbox revocation, and the loop revalidates durable policy at drain); one deterministic typed `finalize_now` control whose first line is the `owner_requested_finalization` literal, routed by the loop to its own rail (zero or one tool-less turn); descendants settle first with a bounded child projection fed to the root's final turn; the grace budget starts at the durable control DRAIN (first drain wins), bounded by `request + OWNER_STOP_OUTER_CAP_SEC`, and neither anchor is ever progress-extended; `running_owner_stop_tasks` bypasses only the generic idle/finalization-grace rails; a COMPLETED finalize root suppresses the redundant cascade summary
  │   ├── schedule_time.py     ← Cron/timezone schedule time parsing helpers
  │   ├── evolution_lifecycle.py ← Evolution campaign state + transaction lifecycle: campaign file IO, start/pause, begin/update transaction, cycle-outcome recording, deterministic worktree cleanup, owner cycle reports, idle dispatch over queue-owned state, supervisor auto-restart request
  │   ├── events.py            ← Worker→supervisor event dispatcher with exact attempt/execution/round/call correlation for the process-local active main-LLM row; composes the frozen subagent task text (`_compose_subagent_text`; the acting `[WRITE SURFACE]` block states only the write-root authority boundary — actor identity comes from the immutable configured snapshot, startup/wake facts from bootstrap); an event type absent from `EVENT_HANDLERS` is DROPPED into a truncated `unknown_worker_event` row, and `tests/test_worker_event_registry.py` pins the registry by AST scan — an unexplained allowlist entry is exactly the silent blessing the scan exists to end; the scan is shape-bounded, and outside its reach the discipline is code review; holds shrink-only byte debt above the module byte ceiling
  │   ├── subagent_task_truth.py ← Delegation-truth enrichment of the subagent `task_done` transport frame (`enrich_task_done_event`); split from events.py at its byte ceiling
  │   ├── chat_delivery_events.py ← Photo/video/document/link delivery handlers, merged into events.py `EVENT_HANDLERS` via `**_CDE`
  │   ├── task_dispatch.py     ← Pure admitted-event→worker-payload construction, including the identical top-level/metadata depth and configured-route projections consumed by workers
  │   ├── log_addressing.py    ← Explicit audience for task-scoped live log events: `address_task_event` (lineage from the RUNNING row, project binding wins, explicit chat_id preserved — 0 is the real Skill Review session; A2A frames are suppressed at the `push_log` choke, not by dishonest addressing), `make_server_log_sink`, `address_handler_push`
  │   ├── steering.py          ← Owner steering-message delivery to running tasks: mailbox routing to the drive the worker drains, plus a typed refusal while a cancel intent is pending (steering is fenced during a stop — what makes the owner-stop single-turn rail safe)
  │   ├── telemetry_events.py  ← Durable handlers for RARE typed telemetry-only worker events (merged into `EVENT_HANDLERS` like `_CEH`): a type-agnostic passthrough appends each row verbatim to events.jsonl, beside the `task_message_injected` sibling shaping the A2A row; membership is bounded by contract — every dispatch is a durable append, so high-rate narration never joins this registry
  │   ├── git_ops.py           ← Git operations (clone, checkout, rescue, rollback, push, credential helper) and the shared bounded local-Git process runner
  │   ├── update_source.py     ← Official update-source selection + network policy through the bounded Git runner
  │   ├── update_recovery.py   ← Exact owner Restore/promotion: pinned prior HEAD, rescue-before-reset, one captured SHA for local/remote promotion
  │   ├── update_merge.py      ← Managed-update engine: exact-target 3-way plan, direct clean fast-forward, stash-first reviewed assisted merge (both lanes stash dirty work before the authoritative replan; VERSION + carrier tokens projected before the M0 pin), transaction (`m0_tree`, `tests_evidence`, `stash_sha`/`local_work_carrier`, `failed_update_ref`), verified rollback/smoke, phase-dispatched boot recovery (the marker-cleanup phase only retries the tx-marker unlink when the repository already holds its final state); disclosed residual: M0 is a pin-once forensic baseline in the resolver-writable tx marker — review discloses it and does not re-verify
  │   ├── update_candidate.py  ← Candidate/carrier primitives (re-exported by update_merge.py): private-index worktree tree serialization, rerere-neutral merge flags (a live rr-cache could silently replay resolutions), the deterministic `failed-update-<target12>` preservation branch, stash push/restore with the marker-guarded replay contract, and the single-run `tests_evidence` proof pinned to the exact candidate tree — the managed commit gate reuses it instead of a duplicate run
  │   └── update_merge_policy.py ← Presentation-only doc/code/hot conflict labels; every conflict uses the same reviewed assisted path
  │
  └── ouroboros/               ← Agent core (runs inside worker processes)
      ├── config.py            ← SSOT: paths, settings defaults, load/save, PID lock (§7)
      ├── version.py           ← Version string from the VERSION file with importlib.metadata fallback
      ├── secret_masking.py    ← Exact Settings/MCP wire-placeholder emitters/recognizers + top-level secret repair before env overlay and persistence
      ├── settings_integrity.py ← Strict settings-snapshot integrity pin; `OUROBOROS_SETTINGS_SHA256` enables the trust root
      ├── credential_shapes.py ← Filename shapes that commonly indicate credential material; blocks root read imports
      ├── update_channels.py   ← Closed Stable/QA/Development channel mapping and update-network defaults
      ├── colab_bootstrap.py   ← Google Colab source-mode bootstrap: official update source, stable local `ouroboros` branch, Drive-backed settings/data, personal origin, no-UI server command, native Telegram setup
      ├── cli.py               ← Source/headless CLI over gateway tasks, logs, settings, skills, marketplace, local-model, and MCP wrappers
      ├── packaged_cli.py      ← Packaged desktop CLI bridge: resolves bundle roots, bootstraps the launcher-managed repo, delegates to cli.py
      ├── packaged_cli_install.py ← Packaged CLI installer planning/execution for user-local command shims
      ├── agent.py             ← Task orchestrator; the dispatch-note pair lives in `subagent_dispatch_notes.py` (same-name re-exports)
      ├── agent_startup_checks.py ← Worker-boot verification: dirty repo, version sync, budget, memory files, health checks
      ├── agent_task_pipeline.py ← Task execution pipeline orchestration; freezes one shared non-final subtree-cost snapshot for summary/reflection before the terminal checkpoint records final spend; calls the swarm-efficiency rollup owned by task_finalization.py at pipeline end
      ├── task_finalization.py ← Terminal delivery + sealed final ground truth: live final-answer delivery before blocking post-task (final event selected by the finalizing task's id; buffered copy retained under one `delivery_id`), the sealed final package (delivered text + the durable result's own artifact manifest) fed to summary/reflection as a prompt input, never a validator; owns the per-task `swarm_efficiency` rollup (subagent_count / wave_count / inter-wave latency / `lanes_requested` — a rollup built from pre-dispatch fanout events cannot truthfully report effective lanes, which are per-child dispatch facts; `planned` stays null, never inferred as 0 from absent events; host-attested Swarm intent is the typed metadata `force_plan_source == "swarm"`, never prompt inspection, and a Swarm task that fanned out nothing records a minimal `no_fanout_observed` block instead of silence)
      ├── mutation_attribution.py ← Root-task baseline capture in the existing task result; clean-at-baseline Git candidate projection; terminal projection includes the committed interval delta
      ├── process_interpreters.py ← Interpreter resolvers for the user process launch surfaces: one-time pre-guard unversioned-Python resolver + post-gates Node ladder (PATH-first health probe, bundled fallback, attested child-env PATH prepend; the probe EXECUTES a candidate, so it runs only after the dispatch gates approve the call)
      ├── post_task_checkpoint.py ← Durable root post-task phase/final-cost checkpoint shared by task finalization and Project naming recovery
      ├── presence_profile.py  ← Strict reviewed `presence:` behavior-profile parser (instructions, context topics, runtime defaults, portable capability requests)
      ├── presence_runtime.py  ← Symbolic `main`/`light` defaults; owner-local overrides clamped to the global round limit
      ├── presence_capabilities.py ← Host-owned installation selections for portable Presence capability requests
      ├── presence_authority.py ← Immutable positive capability ceiling compiled from one reviewed profile and its selected exact targets
      ├── presence_bindings.py ← Owner-created revocable transport-room → behavior-skill bindings with exact origin/destination identity
      ├── presence_admission.py ← Fresh review/enablement/profile/state admission + immutable per-turn Presence snapshot
      ├── presence_context.py  ← Presence instructions, exact event facts, declared knowledge-topic projection
      ├── presence_runner.py   ← Fresh-agent Presence turns: cross-process installation cap, per-conversation serialization, idempotency, typed result, dialogue provenance
      ├── dialogue_provenance.py ← Shared exact transport-provenance rendering for history, memory, and consolidation
      ├── extension_companion.py ← Host-supervised companion processes for transport skills (§12)
      ├── extension_reconcile_queue.py ← Durable worker→server extension reconcile markers + server pickup loop
      ├── event_bus.py         ← Typed in-process event bus for skill subscriptions
      ├── evolution_checkpoints.py ← Append-only campaign/eval checkpoint ledger for evolution progress
      ├── evolution_fingerprint.py ← Canonical fingerprint for evolution-campaign objectives; SSOT for repeat gating
      ├── improvement_backlog.py ← Durable advisory improvement backlog: recurrence-counted dedup (bump count/last_seen, never drop), priority+recurrence+recency ranking, close-on-commit `close_backlog_items`, size-triggered `groom_backlog`; parser-safe locked writer; entries carry priority/kind
      ├── loop.py              ← High-level LLM tool loop and its one-shot finalization nudges, ordered nanny → red-verification → masked-verification → no-op-attempt; continuous `FINAL ANSWER:` latching captures the latest typed candidate every round (tool-count-stamped, no prose mining) so review/nudge/forced-finalization paths never erase a structured answer; all marker prompting is gated on `task_contract.answer_protocol="final_answer_line"` via the `answer_protocol_active` SSOT (the gate is sufficient — an empty `expected_output` cannot suppress it) while the latch/extractor stay unconditional; `extract_final_answer` structurally rejects the outcome-tier ledger identifiers as answers — internal enum vocabulary is never a deliverable, and `solved` stays extractable as an ordinary English word. The nanny nudge fires once for a harness child finalizing with ZERO durable start attempts (blocked and uncustodied attempts count, so an exact-route startup fault is not accused of skipping delegation); it reads custody evidence from the canonical (budget) root via `delegate_custody.custody_root` and branches PENDING ≠ FAILED — a started-but-unsettled run gets a wait reminder, never a failure accusation, which would invite a duplicate concurrent run; an actually-injected nudge is stamped by the WORKER as a durable custody row, and a COMPLETED harness child with zero runs carries the typed `nanny_finalized_after_nudge_without_delegation` disclosure (visibility, never a gate); a configured session child finalizing without a succeeded leaf run carries the typed `CONFIGURED_ACTOR_INCOMPLETE`/`CONFIGURED_ACTOR_UNKNOWN` fact (`subagent_bootstrap.actor_first_unresolved_fact`; host children ride along as auxiliary `direct_child_statuses`, never a substitute for the leaf), and a successful run's later silence stays proportional to the measured burn (`NANNY_METERED_OVERRUN` via `nanny_pacing`)
      ├── acceptance_dialogue.py ← Acceptance obligations/dialogue/decision machinery (moved whole out of loop.py; every name re-exported from `loop`): the closed typed reason set `ACCEPTANCE_DECISION_REASONS`, the sole decision merge point `_set_acceptance_decision`, obligation collection/reopen/disposition, the bounded `acceptance_dialogue_history`, and the paid identity + free-replay `_refuse_identical_acceptance`; the `dialogue_status` reducer stays in `review_substrate.aggregate_dialogue_status` (the vote SSOT)
      ├── loop_llm_call.py     ← Single-round LLM call + usage accounting
      ├── loop_transport.py    ← Transport-outage wait episodes and provider-failure terminal text; bounded backoff with free redial
      ├── delivery_protocol.py ← Delivery-finalization protocol vocabulary and pure parsers (candidate dataclass with inherited host-control-episode provenance, hold-control literals, control prompt, whole-body classification, duplicate-key/trailing-object parsers); loop re-exports the underscore names
      ├── task_pacing.py       ← Task-pacing SSOT: deadline/cost milestones, finalization reserve, BudgetSnapshot, acceptance launch/improvement rails; at least 200 s reserved for the first review, then `max(configured_floor, 1.5×EWMA)`; an explicit `max_improvement_passes` always binds, otherwise the shared `OUROBOROS_REVIEW_MAX_CYCLES` cap binds under every policy (`unlimited` = no local count cap; deadline/global rails still apply); legacy `until_deadline`/`stall_rounds_threshold` are accepted for one compatibility window with a deprecation event; workspace deliveries get one shared commit-neutral tree-flush sentence on the deadline/cost milestones — commit-neutral because acting self_worktree subagents cannot commit and a moved HEAD fails patch capture closed; disclosed residual: a forced tool-less exit inside one long round can still ship an unverified last edit — the structural verification-freshness seam is an owner-pending follow-up
      ├── vision_routing.py    ← Send-time image routing SSOT: inline vision vs generic captions vs placeholders on a per-send message copy (`OUROBOROS_IMAGE_INPUT_MODE`, `OUROBOROS_MODEL_VISION`)
      ├── fallback_cooldown.py ← Per-process 429-aware cooldown for the `OUROBOROS_MODEL_FALLBACKS` chain: a transiently-failed model is parked for a short window so fallback walks and repeated rounds skip it; advisory, default-on, fail-soft, passive heal; per-process only — honestly not a swarm-wide governor
      ├── model_concurrency.py ← Per-(model,use_local) `BoundedSemaphore` capping concurrent provider calls (`OUROBOROS_MODEL_MAX_CONCURRENCY`, default 3) so a task's own loop + subagent threads + status pings cannot self-DoS one model's rate limit; excess threads WAIT deadline-bounded; wraps only the provider call in `loop_llm_call.call_llm_with_retry`; per-process only
      ├── project_naming.py    ← SSOT for LLM-first project naming: bounded light-model title with deterministic fallback, shared by the proactive card namer (supervisor/workers.py), turn-into-project conversion (gateway/projects.py), and `ensure_project_scope`; the provider call goes through the model_concurrency slot
      ├── loop_tool_execution.py ← Tool dispatch and tool-result handling
      ├── deadline_utils.py    ← Shared deadline parsing/remaining-time helpers + the transport-vs-logical wait seam for loop milestones and process-tool/review timeouts
      ├── observability.py     ← Private forensic execution ledger: redaction, gzip CAS blobs, call manifests, trace refs
      ├── cancel_intents.py    ← Durable cancel-intent projection: compact locked `state/cancel_intents.json` of ACTIVE intents (request id, claim owner/pid + claim GENERATION fencing every mutation, `scope` recording single-vs-cascade so a watchdog replay re-runs the right shape) + forensic `cancel_intent` ledger rows; the ONE ingress `request_cancel` for the agent tool, HTTP single/cascade, and boot migration of legacy latch files — intent never rides the canonical task status; reads are strict and fail closed per §10 (typed `CancelIntentProjectionCorrupt`; enforcement degradation is owner-visible, never a silent "no intent"); a quarantined malformed row discloses once per row content — a ~20 s watchdog must not append the same disclosure forever, and a restart re-announcing once is honest; owns `claim_is_abandoned` and the `allow_settled_target` live-ownership exception (§10, cancellation custody)
      ├── owner_hurry.py       ← Owner "hurry": a typed TASK-LOCAL acceleration latch, never a chat message; the durable `owner_hurry` projection is written by `update_json_locked` touching only its own keys — never `write_task_result`, whose status-regression guard could drop concurrent terminal fields — keyed by the real attempt identity `task["_attempt"]`; while latched, the next acceptance panel is skipped with zero reviewer calls (`acceptance_skip_applied`), remaining improvement passes overlay to 0 through `effective_budget_profile` (the immutable task_contract is never rewritten), and force-plan becomes task-locally advisory; the effect DIES WITH THE ATTEMPT (`retry_reset` on every same-id requeue producer), a never-applied request is marked `not_applied_before_terminal`, and the non-chat `owner_hurry` events are hidden from chat by `log_events.js`
      ├── owner_quiz.py        ← Owner-quiz lifecycle projection: worker-side `record_asked`, request-id-idempotent first-answer-wins `record_answered` (option index validated against the STORED labels), structural-only `reconcile_terminal` (open → expired_terminal at task done; no host TTL), `quiz_states` replay; same locked-writer idiom as owner_hurry, touching only the `owner_quiz` key
      ├── routing_wait.py      ← Root-parameterized SSOT of the durable routing-receipt waits (`wait_for_promotion_admission`, `wait_for_routing_annotation`); tools/control.py keeps thin wrappers, so the gateway picker dispatcher confirms clicks through the SAME receipts the LLM routing tools poll
      ├── outcomes.py          ← Typed task-outcome and acceptance-decision authority keeping the lifecycle/execution/objective/review/artifact/verification/child-absorption axes separate; policy denials, cosmetic exits, and ignored outcomes never masquerade as genuine tool failures; receipt reconciliation lives in `_outcome_receipts.py`, trace classification in `_outcome_tool_errors.py`
      ├── outcome_receipt_store.py ← Durable verification-receipt path/append/read authority + exact-row union of forked-child and canonical replicas; owns the zero-run WRITE enum (`incomplete`/`unknown` only — a zero-run "complete" is unverifiable self-report); outcomes.py re-exports the compatibility names
      ├── depth_evidence.py    ← Pure requested/permitted/attempted/achieved depth projection for root acceptance; missing admitted permission stays evidence-unknown rather than reconstructed from mutable live config
      ├── _outcome_receipts.py ← Receipt parsing and the ONE canonical receipt identity (`receipt_canonical_identity` → `ReceiptIdentity`; invariant in §10): three independent components — `criterion_id`; structurally canonical `check` text PAIRED with its `check_rendering` stamp (quoted shell punctuation is data, not syntax, and receipts from different renderings are never the same verification — the stored string alone cannot say which renderer wrote it); and the raw-sorted `canonical_path_set` (whitespace untouched — a leading space is a legal filename byte); `ReceiptIdentity.key` selects ONE typed (kind, value) and sameness is that key's equality, never a match across kinds — the parts are disclosures, never the comparison; the per-kind normalization answer lives in the closed `IDENTITY_KINDS`/`KIND_NORMALIZES_COMMAND_TEXT` table, so a fourth kind must state its own answer in its own row rather than inherit a default; the outstanding sets `unreconciled_failed`/`unreconciled_masked` scan every candidate against ALL later reconcilers and collapse repeated failures of one check onto the freshest receipt; the shared disclosed projections (`receipt_identity_projection`, `disclosed_list_projection`) make every bound explicit — exact omitted counts plus a hash over the injective serialization, string bounding via the SSOT `utils.truncate_review_artifact`, never a hand-rolled slice; `verification_receipt_ledger_row` splats that projection, so a new receipt key is dropped unless added there
      ├── _outcome_tool_errors.py ← Leaf SSOT for tool-trace status vocabularies and execution-axis classification; outcomes.py re-exports
      ├── code_intelligence.py ← Internal code inventory: derived-only file facts, hashes, polyglot symbol/import/call extraction via tree-sitter with Python on stdlib `ast`, a visible `structural_unavailable` fallback when a grammar is missing, and an incremental JSON cache (no raw source)
      ├── code_search_rg.py    ← Optional ripgrep-backed search for search_code; every match is post-filtered through the protected/secret gates
      ├── pricing.py           ← Exact-route best-effort provider-catalog lookup with nullable estimates; no static model tariffs (they go stale) and not the monetary ledger
      ├── usage_accounting.py  ← Append-only physical-model-attempt monetary authority: reserved→dispatched→settled|unresolved (or reserved→released), short cross-process check+append+fsync lock, conservative global/root admission, validated replay/torn-tail quarantine, compatibility projections, resumable legacy import; application candidates carry exact raw/context identities + a pre-dispatch manifest on the same attempt id
      ├── _usage_response.py   ← Pure provider-response usage normalization for physical accounting; a zero-usage body error settles at a confirmed $0 to release the reservation, so a provider storm cannot manufacture phantom budget exhaustion
      ├── _usage_rows.py       ← Pure row arithmetic (summaries, limit/integrity decoration, physical-call counts, breakdown buckets, the exact Skill Review wave/slot projection); no I/O or locks; re-exported by usage_accounting.py
      ├── _usage_rows_memo.py  ← Validated-rows memo + fingerprint-keyed render cache + in-lock warm read cache; every cache resumes via the substrate's `LedgerResumeState` fingerprint and falls back to the authoritative locked read on any doubt
      ├── skill_review_usage.py ← Read-only cached projection of final physical-attempt rows for one exact `(review_skill, review_wave_id)`; no second ledger or persisted totals
      ├── usage_ledger.py      ← Durable append-only ledger substrate: cross-process locking, atomic append+fsync, row/transition validation, torn-tail quarantine; one-way seam — accounting imports it, never the reverse
      ├── cost_projection.py   ← The ONE cost projection every producer surface uses: `accounted_upper_bound_usd` is the honest name (`cost_usd` stays outbound as a deprecated alias carrying the same value — frozen wire contract; same pairing for `_with_children`); null projects as None on BOTH names, never $0.00; finality is never fabricated; `COST_OPENNESS_FIELDS` ride beside every amount
      ├── delegate_custody.py  ← Durable custody for delegated (Claudexor) runs: the SSOT is the `delegate_run_*` rows in the canonical event log plus ONE compact incident projection `<drive_root>/logs/containment_faults.jsonl` — the event log grows without bound, and a tail-bounded scan can bury an unresolved fault; OWNED/FOREIGN/UNKNOWN ownership replay survives worker restart; the per-intention invocation id rides the wire as `Idempotency-Key` (the deterministic per-logical-start hash is only the pending-invocation LOOKUP identity; reuse only via the explicit `retry_of` token); run settlement is decoupled from registration cleanup — `settled` follows the idempotent ledger row while the owned-registration obligation survives on `project_owned` with its own sharer tie-break and sweep, and the terminal audit discloses `deferred_project_retirements` additively; typed cancel vocabulary (confirmed | requested | failed | containment_fault_run_may_still_be_live) with durable faults riding the health invariants; one `daemon_says_absent` predicate decides everywhere that a 404 is the daemon ANSWERING the resource is gone, never a failure to find out; an ABSENT custody log is a positively-established clean state while an EXISTING-but-unreadable one audits as typed `delegated_run_state_unknown:custody_log_unreadable`, never cleanly reconciled; patch-apply intent rows (`delegate_run_patch_apply_started`/`_resolved` + the `patch_apply_pending` replay flag) make a crashed disposition typed-AMBIGUOUS instead of falsely rejected; `run_not_owned` refusals disclose `owner_task_id`/`run_settled`/`run_terminal_state`, and `run_ownership_unknown` names `get_task_result` as the ownership-free cross-task read
      ├── delegate_custody_usage.py ← Pure usage and terminal-state projections over delegated-run custody rows
      ├── delegate_hold.py     ← Unknown-provider hold: parks the task in supervised_wait, waits for the leaf wake, never resends
      ├── delegate_source_coverage.py ← Oversized-work-order source custody: canonical interval union/completeness, strict durable receipt bounds, replay-safe start binding, durable delivery confirmation for safe receipt retry, terminal cannot-verify projection, apply refusal — incomplete source cannot authorize a terminal PASS/apply; reuses `get_task_result` + the existing interaction seam; no alternate store
      ├── delegate_evidence.py ← Read-side execution-evidence projection over the custody rows (`task_execution_evidence`: started/settled/succeeded/failed counts, terminal-state axis, `evidence_read_failed`, disclosed subscription spend, `nanny_nudge_recorded`, and `delegate_start_attempted` counting blocked and uncustodied attempts too, so a refused-but-obedient nanny is never disclosed as nudge-ignoring); owns the stamp writers `record_nanny_nudge_stamp`/`record_start_blocked`; projects `applied_access_profiles` — the access the engine actually served, read off SETTLED rows only (empty = no receipt disclosed it, never "no access") — and `acceptance_patch_dispositions`, the bounded section over `delegate_run_patch_verdict` rows (cap 20 with the exact omitted count, `unreviewed_delegated_apply` headline); absence of the section means NO disposition was recorded, never "reviewed clean", and an unreadable custody log is the typed `evidence_read_failed` marker, never an empty-therefore-clean section
      ├── synthesis_cost_text.py ← Synthesis-prompt renderers for the pre-synthesis cost/outcome snapshot over the SSOT `cost_display`; re-exported by agent_task_pipeline.py
      ├── llm.py               ← Multi-provider LLM routing (OpenRouter/OpenAI/compatible/Cloud.ru/MiniMax/GigaChat/Anthropic); canonical conversations stay function-shaped while the physical-send seam delegates exact-route request adaptation to the request-wire leaves below
      ├── net_transport.py     ← Shared httpx transport construction for remote LLM clients; TCP-keepalive socket options
      ├── transport_custody.py ← Typed transport facts for the physical-attempt custody seam
      ├── openrouter_attribution.py ← Canonical OpenRouter application attribution, centralized so forks do not compete under the same external application identity
      ├── openai_chat_custom.py ← Pure direct-OpenAI Chat function→custom codec: deterministic compact schemas, exact full-schema/catalog binding, tool-choice projection, prior-call replay, canonical response normalization, parser-issued validation sidecars; no Responses transcript or second stored history
      ├── openai_chat_dispatch.py ← Direct-OpenAI Chat policy leaf: custom+requested reasoning first, exact-dialect fallback with the same reasoning, then task-local explicit `none` only when the physical-attempt rail still permits it; owns private sidecar consumption + the bounded schema-error continuation
      ├── request_wire_contract.py ← Provider-neutral exact-route request profile: closed `set_value`/`drop_field`/registered `replace_dialect` actions, 14-day success-only evidence store `data/state/request_wire_compatibility.json`; task-local explicit `none` can never become durable dispatch authority — a task-local availability fallback must not teach the route
      ├── request_wire_resolution.py ← Deterministic request-profile composition with source-predicated effort bounds/transitions; contradictions fail open to the requested effort with `conflict=True`
      ├── request_wire_receipts.py ← Factory-bound wire candidates + semantic-success receipts (exact serializer digests; tool-choice semantics cannot change)
      ├── request_wire_attempt.py ← Physical-attempt validation — a settled accounting attempt for the exact candidate; public `usage.request_wire` disclosure
      ├── request_wire_custom_validation.py ← Custom-call validation; a validation failure may prove wire acceptance but cannot authorize tool execution (`allows_execution` gate)
      ├── request_wire_recovery.py ← One sync/async wire-recovery machine: durable evidence applied immediately before send, reactive evidence committed only after terminal semantic success, ordered terminal disclosures
      ├── anthropic_native_custody.py ← Whole-block replay custody for Anthropic native reasoning (same provider/endpoint/API/model); opaque type/order/size/digest projections
      ├── reasoning_artifacts.py ← Sealed-vs-portable reasoning-artifact classification, shape-first and fail-closed; only sealed artifacts pin an endpoint — portable reasoning stays failover-eligible; the `SIGNED_PORTABLE` roster is a decaying external provider fact (inventory: docs/DEVELOPMENT.md), extended only by a fresh cross-provider replay probe
      ├── llm_observability.py ← Persists public call projections; strips private sidecars from durable records while returning them in-process
      ├── llm_probe.py         ← Oversized-context evidence probe + Provider Test transport with physical accounting; no retry, fallback, or learning
      ├── mcp_client.py        ← MCP client: parses MCP_SERVERS, validates transports, masks tokens, prefixes tools `mcp_<server>__<tool>`, guarded SDK import; MCP descriptions/results stay untrusted data (§6)
      ├── safety.py            ← Safety supervisor call with a bounded newest-first context budget (the omission marker is reserved INSIDE the budget); a 429 on the safety check is an infrastructure fact about the supervisor, not a verdict about the tool call — one deadline-capped retry, then the typed `⚠️ SAFETY_UNAVAILABLE` non-verdict telling the agent to retry the same call, not reword it; process-local storm latch answers in-window checks without provider calls; durable `safety_check_rate_limited` audit event; structured insufficient-quota keeps its PERMANENT classification and still blocks as a verdict; the fail-open cases are owned by prompts/SYSTEM.md
      ├── consciousness.py     ← Background thinking loop with live progress emission (§6)
      ├── consolidator.py      ← Dialogue consolidation with a generation-aware cursor over the ordered archive chain; an unfindable generation appends a loud durable `[MEMORY GAP]` block, never a silent offset reset
      ├── memory.py            ← Scratchpad, identity, chat history
      ├── project_facts.py     ← project_id resolution (explicit `--project-id` or workspace-path hash); per-project knowledge dir `projects/<id>/knowledge` isolated from `memory/knowledge`; journal/workpad helpers
      ├── task_tree_ledger.py  ← Append-only `data/task_trees/<root>/blackboard.jsonl`: EPHEMERAL swarm coordination in typed validated rows (prose is never authoritative), distinct from the durable project journal it is mirrored into by `tools/project_journal.py::mirror_tree_coordination_to_journal`; size-capped writes; exposed via `tree_note`/`tree_read` with the tail injected each turn; pruned on root terminal
      ├── projects_registry.py ← Durable `data/state/projects.json`: 80-char names, `active|deleting|tombstoned`; deletion preserves bindings/history/folder/memory; a tombstone blocks resurrection; reconcile registers existing stores and NEVER prunes
      ├── project_dialogue.py  ← Read-only chat lens + append-only `logs/chat_annotations.jsonl`; the sidecar never owns routing except the `needs_manual_target` decision card (token+options validate the click; first-wins closing rows); `build_owner_message_ref` mints refs at ingress
      ├── project_lease.py     ← One-writer-per-project lease in `assign_tasks`; same-project subagent swarms exempt; `""` is no lane
      ├── context.py           ← Main agent context assembly; places ordered subagent-catalog JSON under `## Available subagents` in the semi-stable block
      ├── main_context_authority.py ← Deep-copies the context authority; replaces only oversized raw result strings with source-resolvable narrative or a typed gap
      ├── client_surface.py    ← Closed-key bounded client-surface normalizer; surface identity excludes viewport/narrow_layout; mailbox surface-change note
      ├── context_fit.py       ← Deterministic Max/Low context projections from one immutable core with labelled measurement + typed reclaim deficit; no routing/retry/global-mode authority
      ├── context_budget.py    ← Context budget vocabulary + typed reclaim SSOT (owner-Low 200K economy target); owns `estimate_message_chars` (images counted at `IMAGE_BLOCK_CHAR_EQUIVALENT`) as the one shared bounded basis
      ├── context_mode_compat.py ← One-window compatibility shim for the retired persistent context auto-Low state
      ├── capability_evidence.py ← Sourced capability evidence in `data/state/capability_evidence.json` (confirmed/asserted/unprobeable/failed): authorizing readers require fresh evidence, and unknown fails the ≥1M gates closed; owns `observe_token_density` — the density witness calibrates on the bounded-proxy basis the fit estimator measures, while budget reservation keeps RAW, because over-counting money is the safe direction and the two consumers split on purpose; exact-route dispatch authority lives in `request_wire_compatibility.json`
      ├── context_layout.py    ← Doc-layout SSOT: tier-0 always full; ARCHITECTURE full in Max and a lossless fence-aware navigation map in Low; DEVELOPMENT full-or-pointer by task binding; reduction is by relocation with a visible pointer, never silent truncation
      ├── context_compaction.py ← Atomic-unit compaction: exact checkpoint, gap-free map/fold, provenance capsules, transactional apply on the caller's basis; an unfinished Anthropic native unit is ineligible; opaque custody never enters summarizer text
      ├── context_health.py    ← Health invariants for the reading task (`build_health_invariants`); delegated-run obligations stay globally visible — a preserved-and-invisible result is how work rots on disk — while the instruction is ownership-aware, so a non-owner is never handed a call that structurally refuses
      ├── headless.py          ← Child-drive isolation, workspace patch artifacts, memory export helpers; a sensitive-looking untracked credential is excluded per-file and disclosed as `sensitive_blocked`
      ├── workspace_patch_rules.py ← Pure patch-exclusion rules (env/cache sets, junk regex, lockfiles, credential-shaped names); the I/O checks + `untracked_capture_veto_reason` stay in headless
      ├── coop_checkpoint.py   ← Quiescent checkpoint commits of cooperative trees (detected by supervisor/events.py, run off the drain thread): a tree qualifies only through a MUTATIVE child's `write_root` — owner-attached folders are never auto-committed; credential-shaped files excluded + disclosed; quiescence revalidated pre-mutation
      ├── delegate_output.py   ← Atomic staged full outputs under `delegated_runs/<run>.json` (sha256+length); `acknowledge_staged_output_read` hooks read_file's task_drive path; once-per-run `delegate_run_output_consumed` row — a disclosure, never a gate
      ├── delegate_containment.py ← Engine-derived isolation facts: a home-isolation breach is exactly two facts (`harness_home_isolated: false`, or applied home == the operator's own); a home nested under `$HOME` is disclosed-unconfined (`home_nested_under_operator_home`), never relabelled as isolation; absence is reported unproven
      ├── delegate_progress.py ← Transport read bound + transient Git-object retry (`poll_bound`); renewal/wake policy lives in delegate_supervision
      ├── nanny_pacing.py      ← Metered-silence pacing: only `BASELINE_RESET_TOOLS` (`delegate_start`/`schedule_subagent`) reset the burn; supervision verbs advance the round baseline while dollars accumulate — coordination never buys metered silence
      ├── delegate_interactions.py ← Child-interaction custody: reported-interaction memo, bounded display scalars with whole answer keys, typed `waiting_on_user` with an immutable spill file, strict `_delegate_answer` validation with typed rejections (`subscription_window_exhausted` carries `reset_at`; transport death/5xx is `delivery_unknown`)
      ├── delegate_shared.py   ← Shared delegate leaf (`_fail`/`_emit`/`_owned_run` — OWNED/FOREIGN/UNKNOWN from durable rows); one-way seam, facade re-exports
      ├── route_spec.py        ← Neutral route primitive: kind/target/pin normalization + effort validation; semantic owners keep their own spelling
      ├── configured_subagents.py ← Canonical `OUROBOROS_SUBAGENTS` parser/serializer: strict validation, stable ids, fingerprinting; owner free text is never host-parsed
      ├── subagent_runtime.py  ← Immutable task-start subagent snapshots, exact `subagent_id` selection, typed alternatives, bounded deterministic legacy-input seam
      ├── subagent_work_order.py ← One complete work-order compiler under a single 250,000-char total wire budget (a serializer bound, not a context claim); over-budget yields a full-SHA source-selector partial lens, never a prefix; generic manifest capability read — no harness name interpreted
      ├── subagent_bootstrap.py ← Host pre-start of the exact snapshotted leaf BEFORE the first metered round, through the same wrapper as `delegate_start(prompt="")`; branch order recovery → fences → blocked → pre-start, and a fence-wake outranks every terminal because a fence may hide a live run; the host never waits (`configured_session_started` receipt); only a definite typed refusal ends unrun at $0 — everything ambiguous wakes the model, because a false "spent nothing" terminal over a possibly-live run is the one direction classification must never fail toward
      ├── delegate_supervision.py ← Event-only sleeping-nanny loop: quiet windows renew without a model call; terminal/interaction/fault/addressed/control (or one reasoned checkpoint) triggers a durable wake with fresh coordination context; replay returns the stored snapshot
      ├── delegate_start_instructions.py ← Stable host start instructions + a bounded separately-hashed appendix; host pre-start sends no appendix; over-budget refuses before provisioning
      ├── delegate_recovery.py ← Narrow exact-leaf recovery for proven crash + planned self-restart; validates bindings; vetoes every no-resume cause
      ├── delegate_registration_policy.py ← `persistent_registration` + the STARTED-row field tables
      ├── delegate_pending.py  ← Durable pending-invocation replay preserving the original idempotency key + canonical start body
      ├── delegate_terminal.py ← Terminal reconciliation + custody-audit persistence, split by surface: counters stay a frozen historical snapshot while `actual_substrate` and the envelope mirror are rewritten from live custody, so the executor chip reads live truth while history stays a snapshot; audit-only in both directions — unreadable custody proves nothing; `refresh_recently_settled_terminals` rides a durable byte-offset cursor (`state/delegate_terminal_refresh_cursor.json`, 5 MB per tick, deferred map for still-running parents)
      ├── subagent_dispatch_notes.py ← Dispatch-time executor notes for delegated children (configured-nanny charter note; non-configured branch keeps "decide your delegation plan first"); agent.py keeps re-exports
      ├── subagent_messages.py ← Bounded durable child-message identity shared by the final frame, recovery, compact persistence, and replay
      ├── subagents.py         ← Subagent envelopes + bounded legacy compatibility; `configured_subagent` snapshots dispatch through subagent_runtime
      ├── subagent_worktrees.py ← Worktree lifecycle + durable registry `state/subagent_worktrees.json` with ops lock and startup orphan reconciliation; `provision_genesis_project` (never registry/GC); execution snapshots pinned by `refs/ouroboros/delegated/`; standalone payload snapshots (CAS hash, writer-race abort); removal only explicit or custody-cross-checked startup GC — fail-closed: an unreadable custody log replays as "no open runs", so the prune skips entirely rather than destroy a child's only copy of its work (prune events are emitted at the server.py call site)
      ├── artifacts.py         ← Attachment staging into `artifact_store/attachments/` (secret-source skip, bounded, read_file manifest); artifact records exclude attachments + `chat_media/`; scratch fingerprints (`.scratch_manifest.json`, both roots) gate patch exclusion only while content matches; the undeclared-output guard stat-verifies post-exec; `delegated_capture_read_target` rebinds `delegated_runs/` reads to the canonical drive
      ├── retention.py         ← Unified GC retention SSOT: clamp/age-cutoff + legacy-key seed picker
      ├── workspace_preflight.py ← Read-only external-workspace git/manifest/toolchain snapshot used by gateway task creation
      ├── project_sources.py   ← Folder attach validation (realpath, not the home root, no repo/data overlap); opt-in `init_git`, NEVER auto-init; atomic server-side clone with `GIT_TERMINAL_PROMPT=0` and typed `auth_required`; provenance + `clone_url` recorded; attaching IS the trust grant (`trusted_at` automatic)
      ├── promotion_source.py  ← Promoted-task source admission off the event-drain loop, only after an executor/id reservation
      ├── workspace_admission.py ← Shared admission for `/api/tasks` + promotion: disjoint git root, Project binding, `workspace="none"`, bounded preflight, loud refusal of a broken binding; empty-Project promotion provisions idempotent genesis, and a failure is typed `workspace_provisioning_failed` on the promotion path (supervisor/workers.py) — never a fallback onto the system repo
      ├── local_model.py       ← Local LLM lifecycle (llama-cpp-python)
      ├── local_model_autostart.py ← Local model startup helper
      ├── deep_self_review.py  ← Deep-review atlas + full memory whitelist on a ≥1M route; a bounded in-prompt OMITTED section is reserved inside the fixed budget; `atlas_assembly_failed` retries with a compact manifest or ships no pack; centrality ranking (reverse in-degree) is deep-review-only
      ├── review.py            ← Repository size-ratchet inventory, code collection, and complexity metrics — the shrink-only module/function byte ceilings the commit gate enforces
      ├── size_ratchet_manifest.py ← Generated data-only size-debt manifest (regenerated by scripts/regenerate_size_ratchet.py)
      ├── review_execution_projection.py ← Pure read-side reviewer-execution projection: bounded rows, 2000-char post-redaction string bound, unknown shapes ship as disclosed JSON
      ├── preflight_runner.py  ← Hermetic pre-commit test runner: ONE hardened raw-bytes `git diff --binary … HEAD` capture, because a staged+unstaged diff pair cannot faithfully materialize unmerged entries; capture/apply failure is a typed `PREFLIGHT_CANDIDATE_ASSEMBLY` block, never a test verdict; node lane first, then the two-pass parallel/serial pytest split under one budget (`LANE_EXCLUSION_EXPR` is the marker-lane SSOT); a dead xdist worker or a missing required plugin is a distinct named block, never a retry or silent serial fallback
      ├── preflight_node.py    ← Content-keyed Node test lane: bundled signed node first then PATH, floor 20.11; typed `PREFLIGHT_NODE_MISSING`/`PREFLIGHT_NODE_TOO_OLD` hard blocks and `NODE_TESTS_FAILED` on a red suite, never a silent skip; both CI jobs mirror `cd web && node --test tests/*.test.js`
      ├── review_substrate.py  ← Review slot coordinator: duplicate ids run as independent slots; per-actor records keep transport/parse/verdict/coverage/quorum/hashes distinct with a compact projection outward; acceptance enforces adaptive quorum, one substantive call ≤2 physical attempts, provenance, and public-info anti-cheat; the delivery seam below it is owned by review_execution.py (§6 Review stack)
      ├── review_custody.py    ← Process-local physical review custody: independent deadlines, late-result settlement, stable retry identity, duplicate-dispatch suppression; not durable scheduling
      ├── review_owner_custody.py ← A paid attempt records its owner `(server session, pid)` before fan-out; only confirmed pid death or a later-generation observation settles tokenless rows; adds no scheduler
      ├── review_execution.py  ← The ONE review-delivery seam below the substrate: closed route vocabulary, immutable `ReviewAssignment` bound once via `_review_route_executor`, the single physical seam `_execute_slot_attempt`, typed `ReviewAttemptResult`; no cross-transport fallback — a route that cannot deliver raises on its own slot (`ReviewRouteUnavailable`), and the durable prompt record and both physical sends share one byte-identical rendering; `ApiChatReviewExecutor` renders lazily and memoizes (digest-pinned); `AgentSessionReviewExecutor` runs one delegated read-only session — `outputSchema` is sent only when the route's own live manifest declares it and trusted only on `outputConformance == "passed"`, else strict parsing then light-model extraction disclosed as `capability_delta`; per-row delivery via `OUROBOROS_REVIEW_ROUTES`/`OUROBOROS_SCOPE_REVIEW_ROUTES`, session target `OUROBOROS_REVIEW_SESSION_ROUTE` falling back to `OUROBOROS_SUBAGENT_HARNESS`; task acceptance stays pinned `api_chat` (owner policy)
      ├── review_native_episode.py ← Bounded native tool-round delivery for api_model reviewer rows (advisory included): one episode ≤ `OUROBOROS_REVIEW_NATIVE_MAX_ROUNDS` against a fresh instance-local read-only registry (network/web off); paid-stamped sends; ≤200 tool receipts + `host_observed` attestation; typed `native_rounds_exhausted`/`native_transcript_cap_exceeded`; a second attempt is LOCAL format repair, never a second paid episode
      ├── review_verdict_extraction.py ← Session/native verdict canonicalization: strict parse first, then light-model extraction to the review's own contract
      ├── review_session_custody.py ← Exact delegated-review recovery validation + pre-POST durable invocation checkpoint; no scheduler or state store
      ├── review_slot_cancel.py ← Slot-cancel honesty: a cancel outcome reports only what it PROVED ("host-cancelled" only on a confirmed own-state receipt; confirmed failed/interrupted is attributed to the run's own terminal); a succeeded run whose result read fails is typed `ReviewSessionSucceededResultUnavailable` after one bounded retry, never "may still be live"
      ├── review_actor_aggregation.py ← Contract aggregation for completed review actor rows; demotes non-contract-valid responses
      ├── review_session_usage.py ← UsageScope-to-custody attribution for delegated review sessions
      ├── review_thread_continuity.py ← Thin Claudexor thread operations for delegated plan reviewers
      ├── commit_admission.py  ← Deterministic commit-admission SSOT: release checks + auto-sync, staged-Python syntax compile, `run_tests_preflight_with_proof` binding the green preflight to the managed pre-commit proof; the advisory and commit gates both delegate here
      ├── reviewer_slot_config.py ← Structured reviewer-slot SSOT: stable ids, route targets, per-slot effort, disclosure-only execution records; a row is EITHER an inline route OR a `subagent_id` (materialized at load; unresolvable = typed refusal); `is_session` is transport while `retrieves` is delivery class; advisory shares the row vocabulary (+`enabled`, `disabled_reason`); malformed config refuses commit/scope/advisory/plan/skill review
      ├── review_state.py      ← Durable advisory pre-review state (`state/advisory_review.json`)
      ├── review_cycles.py     ← Shared paid-cycle cap SSOT (`OUROBOROS_REVIEW_MAX_CYCLES`, positive int or `unlimited`); the four per-gate meanings live in the module docstring (§10)
      ├── review_dispatch.py   ← Review row-identity mint + the write-ahead PAID stamp: typed pre-start refusals stay $0 while a late worker or crash cannot race away the durable paid fact; acceptance binds a strict exact-hash tree-wallet claim; a wallet/deadline/cancel veto releases the reservation
      ├── reviewer_window.py   ← ONE typed `ReviewerWindow` per route (window/status/stale/observed_at + computed `blocking_authority_allowed`); metadata-only probe, per-route-locked, rate-limited by the evidence TTL — never a process-lifetime memo that would outlive the record; fail-closed sub-floor with no evidence; reserves scale to sub-1M windows
      ├── triad_review.py      ← Shared review primitives: JSON-array extraction (repo + skill), per-actor records, quorum/degraded accounting; owns `REVIEW_JSON_ARRAY_CONTRACT`/`REVIEW_JSON_MATRIX_CONTRACT`; a clean verdict is the WHOLE response `[]` (± one fence, ± `NO_FINDINGS`) — a refusal cannot be distinguished from a benign preamble by structure, so prose or a bare sentinel is a parse failure; the contract text lives beside `empty_array_is_verified_clean` so the two cannot drift
      ├── onboarding_wizard.py ← Shared desktop/web onboarding bootstrap + validation (§2)
      ├── subscription_install_presets.py ← Pure sibling install compilers from one normalized draft + one discovery snapshot; output is linear, unpinned, exact-discovery-backed, all-or-nothing
      ├── settings_setup_contract.py ← SSOT for the setup contract, derived bootstrap state, payload validation
      ├── owner_mailbox.py     ← Per-task user message mailbox (compat module name)
      ├── launcher_bootstrap.py ← Bundle-to-repo bootstrap + managed sync helpers (used by launcher.py)
      ├── launcher_onboarding.py ← First-run onboarding as the desktop launcher presents it (serves the gateway /onboarding page; §2)
      ├── launcher_server_reaper.py ← POSIX same-install server discovery, pre-signal descendant capture, root-first termination, live identity revalidation, bounded survivor reporting; PID-lock-owning launcher only
      ├── provider_models.py   ← Model-ID helpers; the `ACTIVE_MODEL_SETTING_KEYS` vs `LEGACY_MODEL_SETTING_KEYS` split keeps Heavy out of startup/Provider Test/new consumers while migration/history still read it
      ├── runtime_mode_policy.py ← Protected-path policy (safety-critical files, frozen contracts, release/managed invariants) shared by the registry, git tools, and gateway guards
      ├── schedule_contract.py ← Schedule id, 5-field cron, IANA timezone validation SSOT
      ├── reflection.py        ← Execution reflection and pattern capture
      ├── post_task_evolution.py ← The worker writes a durable promotion signal; the supervisor idle tick applies it via the existing gated enqueuer (one-shot autostop); never enqueues from the worker, never fires from evolution/subagent tasks
      ├── repo_remotes.py      ← Role-based remotes: `managed` is the read/update-only official source; `origin` is the personal target auto-configured from the GitHub token
      ├── review_evidence.py   ← Bounded provenance-tagged task-acceptance evidence from effective task/plan claims, verification support, artifacts, tool trajectory, obligations, and retrieval facts; ingress claims win over the current closed plan wave, projected without mutating the live task contract; for harness-dispatched tasks the packet carries a host-attested `substrate_execution` section and the sibling `delegated_patch_dispositions` from delegate_evidence — VISIBILITY ONLY, zero typed rules tie substrate to the verdict: acceptance judges quality, never the execution route, and because `integrate_delegated_patch` has no review facts on its path the packet ATTESTS the apply rather than inventing a review
      ├── review_evidence_refs.py ← Leaf SSOT of the evidence-ref vocabulary + exact-membership resolver; unsupported claims cannot certify (`CLAIM_ID_UNSUPPORTED`)
      ├── semantic_dedup.py    ← LLM-first semantic dedup, fail-open None; consumed by improvement_backlog + review_state
      ├── betterleaks_runtime.py ← Pinned Betterleaks runtime resolver (six platform artifacts, packaged-resource-first)
      ├── skill_loader.py      ← Skill discovery over `data/skills/{native,clawhub,ouroboroshub,external}` + `OUROBOROS_SKILLS_REPO_PATH`; `.self_authored.json` marker; per-skill state under `data/state/skills/<name>/`
      ├── skill_readiness.py   ← Execution readiness: review gate, stale hash, enablement, grants, peer conflicts combined in one predicate
      ├── skill_dependencies.py ← Shared dependency-spec resolution for skills
      ├── skill_repair_admission.py ← Repair admission: `base_content_hash` admission + per-write `expected_content_hash` CAS with STALE terminalization — a repair CAS-checks only against the state IT produced; a foreign hash means a concurrent writer, and the honest fix is a fresh repair, not a restore (`last_known_good` deliberately carries no bytes)
      ├── skill_owner_attestation.py ← Owner attestation lane: the owner may skip the expensive LLM skill review for skills they authored themselves
      ├── skill_publish_snapshot.py ← Immutable captured-byte authority for publication
      ├── skill_publish_scanner.py ← Betterleaks scan adapter; literal `high` findings block
      ├── skill_publish_result.py ← Typed publish attempt/receipt + finalization veto
      ├── skill_publish_github.py ← GitHub publication transport after the local gates
      ├── skill_publish_eligibility.py ← Passive publish visibility + `task_start_allowed`
      ├── skill_review_status.py ← Verdict aggregation → `executable_review` (anchors the §13 readiness statuses)
      ├── skill_review_passes.py ← One multi-model pass or chunked quorum; the process-local custody key binds wave/chunk, not a restart index
      ├── skill_review.py      ← Skill review orchestration: preflight + advisory critic via `run_advisory_critic`; tri-model gate against the Skill Review Checklist (docs/CHECKLISTS.md) + docs/CREATING_SKILLS.md
      ├── skill_review_history.py ← Write-ahead review-history marker (`physical_attempt_v1`); append failures emit typed `skill_review_history_append_failed`
      ├── skill_review_cycles.py ← Paid skill-review cycle counting, $0 replay, typed `review_cycles_exhausted`, accepted-rebuttal ledger; the shared cap SSOT is review_cycles.py
      ├── extension_loader.py  ← Extension loading: in-process pure-Python via `PluginAPIImpl`, child-process proxies for isolated-dep/native extensions
      ├── extension_process_runner.py ← Extension child processes: scrubbed env, per-skill deps, timeouts, graceful host errors
      ├── extension_ui_validation.py ← The one host-owned declarative-schema-v1 widget validator
      ├── extension_isolated_deps.py ← Legacy/forced in-process bridge for isolated-dependency extensions
      ├── extension_health.py  ← Durable per-skill health vector at `data/state/skills/<name>/health.json`
      ├── skill_token.py       ← Opaque Host Service token minting/validation (§12)
      ├── marketplace/         ← ClawHub + OuroborosHub: `clawhub.py`, `ouroboroshub.py` (hub update via adopt transaction with VERIFIED `rolled_back`/`rollback_errors`, `.pre-adopt` retention disclosure), `fetcher.py`, `adapter.py`, `install.py`, `install_specs.py` (normalized third-party dependency install metadata for bounded per-skill prefixes), `isolated_deps.py`, `provenance.py` (+ publication receipt `state/skills/<n>/ouroboroshub.json`)
      ├── skill_lifecycle_queue.py ← Single FIFO skill-mutation lane + event snapshot (§13)
      ├── skill_review_runner.py ← Writes `review_job.json` + `skill_review_*` events; routes every executable through tri-model review
      ├── server_auth.py       ← Non-localhost network gate via `OUROBOROS_NETWORK_PASSWORD` (warns when unset; see §8 packaging note)
      ├── server_control.py    ← `restart_current_process` + `execute_panic_stop`
      ├── server_entrypoint.py ← CLI parsing + port binding helpers
      ├── server_runtime.py    ← Startup/onboarding wiring + WS liveness
      ├── server_web.py        ← `NoCacheStaticFiles`, web-dir resolver
      ├── task_continuation.py ← Durable review continuation state
      ├── task_results.py      ← Durable task results `task_results/<id>.json`; locked `task_acceptance_review_accounting` — the claim is minted at first physical reviewer dispatch, and a claim without a recoverable terminal host run is UNKNOWN, never permission to re-dispatch (double-spend fence)
      ├── task_status.py       ← Effective-status SSOT, lineage, bounded waits; worker-side `task_has_live_queue_ownership` (§10, cancellation custody)
      ├── git_shell_policy.py  ← Structural git argv classifiers for the shell guards
      ├── protected_artifacts.py ← Execute-only black-box policy for protected artifacts
      ├── shell_parse.py       ← One shell normalization shared by guard and execution (`recover_stringified_argv`, `normalize_check_argv`, `shell_tokens`, `shell_segments`, `canonical_command_text`): quoted shell punctuation is data, not syntax — over-splitting on a quoted `&&` is the fail-safe direction
      ├── argv_budget.py       ← Argv admission counts encoded bytes of argv PLUS environment (ARG_MAX charges both; per-arg `MAX_ARG_STRLEN`, Windows unit limit); asked by skill_exec before exec
      ├── workspace_executor.py ← Workspace process backends: `local` and network-none `docker_exec`
      ├── deliverables_paths.py ← Lexical + case-folded deliverables path views
      ├── tool_capabilities.py ← SSOT for the core/parallel-safe/untruncated/stateful-browser tool sets
      ├── tool_access.py       ← ToolProfile × ResourceRoot × Operation matrix, affordance map, closed-enum `required_capabilities` check
      ├── tool_policy.py       ← Round-one tool visibility (the sets live in tool_capabilities)
      ├── skill_payload_binding.py ← Skill payload targeting: `.seed-origin` distinguishes native vs external; read/list/search only for read profiles; bounded manifestless skill_publish recovery
      ├── utils.py             ← SSOT for atomic JSON, timestamps, hashes, sanitization, subprocess helpers, `truncate_review_artifact`
      ├── world_profiler.py    ← Generates WORLD.md
      ├── contracts/           ← Frozen ABI package (§11)
      │   ├── tool_context.py  ← ToolContextProtocol
      │   ├── tool_abi.py      ← ToolEntryProtocol + GetToolsProtocol
      │   ├── api_v1.py        ← Compatibility star-re-export of `gateway/contracts.py` (the active envelope owner)
      │   ├── chat_id_policy.py ← SSOT for human-visible vs synthetic chat ids (§12)
      │   ├── task_contract.py ← Task contract; `effective_acceptance_claims(task, closed_plan_wave)` is a pure read-time binder — ingress wins over a closed plan wave (§11.1)
      │   ├── task_constraint.py ← `VALID_WRITE_SURFACES` + surface/write_root validation, fail-closed
      │   ├── skill_payload_policy.py ← Payload path resolution/confinement/sidecar detection
      │   ├── skill_manifest.py ← Unified skill manifest parser (`VALID_SKILL_TYPES`: instruction|script|extension)
      │   ├── schema_versions.py ← Opt-in `_schema_version` stamping helpers (§11.2)
      │   └── plugin_api.py    ← PluginAPI, ExtensionRegistrationError, FORBIDDEN_EXTENSION_SETTINGS, VALID_EXTENSION_PERMISSIONS, VALID_EXTENSION_ROUTE_METHODS
      ├── gateways/            ← Thin outbound transport adapters; no business logic
      │   └── claudexor.py     ← Loopback daemon client: discovery (`discover_daemon_at` reads `<config_dir>/daemon/control-api.json`), handshake, runs, cached quota GET + explicit quota POST; the token stays inside; account-surface translations are read-only; prefers the OWNED daemon via `claudexor_daemon.owned_daemon_provisioned`
      ├── claudexor_runtime.py ← Reviewed engine pin (version/SHA/URL/SHA-256/size/protocol/Node/entrypoints, nullable CLI): seed-or-download, verify + staged extract + probe + atomic promote under `data/state/cx`; no mutable `current` pointer or background updater — the reviewed pin IS the next-spawn selection, and a `null` CLI honestly identifies a pre-CLI closure; read-only resolvers; `OUROBOROS_CLAUDEXOR_BIN` stays an explicit operator override
      ├── claudexor_daemon.py  ← Owned-daemon lifecycle over `data/claudexor` as `CLAUDEXOR_CONFIG_DIR`: supervision via `process_custody.spawn_supervised`, ATTACH-IF-ALIVE, OWN-ONLY-IF-SELF-STARTED, `ensure_owned_gateway` as the one seam, ownership marker `ouroboros-owned.json`; liveness is an AUTHENTICATED handshake — the bearer token is the identity proof, so a live responder refusing the token is a foreign daemon on a recycled port (typed `foreign_daemon`, disclosed, never killed), and a home whose marker names another data plane is refused before spawn because restarting there would be adoption; `servingMode` handshake-before-admission (so the spawn-wait predicate stays reachable), 503 `daemon_recovery_only`, bounded admission wait (~150 ms poll, ~5 s deadline, `admission_wait_sec` opt-down); owns `install_missing_harness_cli`
      ├── gateway/             ← Gateway Boundary v1: browser-facing route ownership + frontend contract SSOT (see below)
      │   ├── contracts.py     ← Active WS/HTTP envelope contract owner
      │   ├── endpoint_index.py ← `HTTP_ENDPOINTS` index (re-exported by contracts.py); routers own the Route objects
      │   ├── router.py        ← Starlette route collector for /api/* and /ws
      │   ├── ws.py            ← WS manager, extension WS dispatch, broadcast
      │   ├── state.py         ← /api/health + /api/state
      │   ├── tasks.py         ← Headless task create/list/get/cancel/events; cancel accepts `stop_policy` (empty = immediate; `finalize_then_cancel` → 202 + open intent → supervisor/owner_stop.py; unknown → 400)
      │   ├── task_events.py   ← Task-event SSE endpoint
      │   ├── task_hurry.py    ← POST hurry ingress: exact one-field `{request_id}` body — extra fields refused, because hurry carries no text by design and a smuggled field must not become a side channel; queue-owned admission; idempotent projection (semantics: owner_hurry.py)
      │   ├── task_decision.py ← ONE `POST /api/decisions` ingress with family-parsed ids (`quiz:` here, `routing:` → routing_decision.py, `interaction:` reserved); writes `KIND_QUIZ_ANSWER`, broadcasts `quiz_state` (lifecycle: owner_quiz.py; ABI: §11.1)
      │   ├── routing_decision.py ← Validates a click against the durable `needs_manual_target` row, recovers the original text, dispatches the existing `steer_task`/`promote_chat_to_task`, confirms through routing_wait receipts; replay-stable derived identities
      │   ├── logs.py          ← Read-only runtime log tail
      │   ├── onboarding.py    ← `POST /api/onboarding/complete`: install-time latch, shared validation, live engine read, preset compile, single settings write under lock; a typed 503 persists nothing (§2)
      │   ├── onboarding_host.py ← GET /onboarding: side-effect-free wizard page served as ES modules
      │   ├── owner_settings.py ← Settings-lock-as-precondition + `CommitBoundary`
      │   ├── settings.py      ← /api/settings + /api/owner/*; `GET /api/reviewer-slots` with row limits (triad 10 / scope 4 / advisory 1) and typed `config_error`, never a 500
      │   ├── presence_settings.py ← Owner-facing runtime overrides for reviewed Presence behavior skills
      │   ├── control.py       ← /api/reset, /api/command, /api/git/*, /api/update/*, /api/evolution-data HTTP handlers
      │   ├── schedules.py     ← Cron schedule HTTP surface
      │   ├── files.py         ← File Browser + chat upload
      │   ├── ui_preferences.py ← `state/ui_preferences.json`: widget order, nested subagent expansion
      │   ├── models.py        ← Model catalog + provider probes + local-model lifecycle
      │   ├── extensions.py    ← extensions/skills HTTP surface (GET /api/extensions, GET /api/extensions/<skill>/manifest, ALL /api/extensions/<skill>/<rest:path>, POST /api/skills/<skill>/toggle, POST /api/skills/<skill>/delete, POST /api/skills/<skill>/review, POST /api/skills/<skill>/grants)
      │   ├── skill_publish.py ← Read-only publish preflight with scan cache; one five-state response; no task or GitHub effect
      │   ├── marketplace.py   ← ClawHub + OuroborosHub HTTP surface
      │   ├── mcp.py           ← MCP HTTP surface backed by the shared MCPManager
      │   ├── claudexor_accounts.py ← Agent accounts HTTP surface (Settings → Agents → Accounts): six thin proxies — counted by HANDLER, two serving more than one route or action — over the owned daemon's account truth: GET /api/claudexor/status[?include=models] (side-effect-free daemon/runtime state + harness catalog + credential profiles + quota windows, each facet stamped with its read state; the additive `unified_accounts` feature fact reads the engine's own /v2/operations catalog — `get:account-pools` present = the unified account model, an unreadable catalog fails closed to the legacy rendering); POST /api/claudexor/wake (owner-initiated daemon start); POST /api/claudexor/login (one Connect intent: install/repair the managed runtime, start or attach the owned daemon, create or re-adopt its setup job; a structural `not_supported` missing-binary create response invokes `install_missing_harness_cli` — claudexor_daemon.py owns the whole operation — and creates the same login job exactly once more); GET/DELETE /api/claudexor/login/{job_id} (canonical snapshot/cancel); POST /api/claudexor/login/{job_id}/input (the owner's answer to a waiting engine prompt); POST /api/claudexor/login/{job_id}/reconcile (explicit proof-of-empty after `termination_unconfirmed` — a terminal job is not automatically release proof: custody holds until reconcile records `status=empty`); DELETE/PATCH /api/claudexor/credential-profiles/{harness}/{profile_id} (one route, two row actions, the engine's strict `{enabled}` body passed through). Zero auth logic and zero vendor recipes live here; the browser never sees the daemon token; the `{job, cursor, sequence, deviceCode?}` envelope passes through verbatim; `harness_login_cards.jobDetail()` renders the escaped untruncated message only beside a settled non-success verdict
      │   ├── claudexor_quota.py ← Explicit owner quota-refresh transport: POST /api/claudexor/quota/refresh discovers the already-owned daemon, performs the mandatory handshake (ordinary 60 s control-plane read bound), and delegates exactly once to the engine's quota POST (90 s foreground bound); the envelope returns verbatim; no lifecycle start, cached status composition, quota policy, retry, or daemon token crosses this boundary; GET /api/claudexor/status stays passive
      │   ├── host_service.py  ← Loopback-only Host Service API (§12)
      │   ├── history.py       ← Chat history + cost breakdown factories
      │   ├── projects.py      ← GET/POST /api/projects, /from-task, /update, /delete
      │   └── _helpers.py      ← Shared request-root/coercion/JSON error envelope
      ├── tools/               ← Auto-discovered tool plugins (registry.py owns discovery; frozen module list for packaged builds)
      │   ├── registry.py      ← Tool registry SSOT: loads tool modules, exposes schemas, executes safely; owns the shell-guard/process-tool membership sets
      │   ├── core.py          ← File/data tools (read_file, write_file, list_files) + code search and digest helpers
      │   ├── shell.py         ← Process tools `run_command`/`run_script` (in-process `_active_subprocesses` tracking; §9)
      │   ├── shell_guards.py  ← Shared shell-guard helpers for the process tools (re-exports write_shape)
      │   ├── read_inspection.py ← Pure-read-inspection carve for the shell-guard predicates (HEAD allowlist with option denial)
      │   ├── git.py           ← Git/write tools with the advisory, triad, and scope review commit gates (§8)
      │   ├── search.py        ← Web search tool (OpenAI Responses API, LLM-first overridable defaults)
      │   ├── browser.py       ← Playwright browser tools with per-ToolContext lifecycle and thread affinity (§6)
      │   ├── vision.py        ← Vision LLM tools for browser screenshots and uploaded images
      │   ├── knowledge.py     ← Persistent topic-based knowledge files with an auto-maintained index
      │   ├── memory_tools.py  ← Memory registry tools for tracking data sources, gaps, and trust
      │   ├── health.py        ← Codebase health tool: complexity metrics and self-assessment
      │   ├── compact_context.py ← LLM-requested tool-history compaction trigger; stores the pending request for the next round
      │   ├── control.py       ← Control tools: restart, timeout settings, scheduling, review, chat history, model switching; publishes the strict `subagent_id`+objective `schedule_subagent` contract, and `wait_task` emits the burst/absorb advisory and compact wait projections (§6)
      │   ├── control_delegation.py ← Delegation-budget and in-task project-scoping affordances (`ensure_project_scope` handler)
      │   ├── tool_discovery.py ← Tool-discovery meta-tools: confirm registration, no delayed capabilities
      │   ├── result_envelope.py ← Typed tool-result envelope; the producer stamps outcome facts; degrades on note append
      │   ├── review_response.py ← Pure response-envelope projection for multi-model review rows
      │   ├── output_export_policy.py ← Deliverable-export eligibility policy for declared process outputs
      │   ├── plan_review_artifacts.py ← Exact plan-review waves + reviewer-continuation inputs; reconstructs the API transcript
      │   ├── evolution_stats.py ← Generates evolution.json metrics from sampled git history
      │   ├── owner_delivery.py ← Owner event delivery: live queue XOR `pending_events` fallback with sticky deferral, preserving narrative order after the first live failure; lineage stamped; background-consciousness frames unstamped and deferred
      │   ├── deliverables_shell.py ← cp/mv/ln into deliverables with symlink checks
      │   ├── shell_audit.py   ← Post-exec custody audit for process tools
      │   ├── process_facts.py ← Typed process-fact seam consumed by loop_tool_execution for the same call; the regex harvest stays a read fallback
      │   ├── write_shape.py   ← `interpreter_write_shape`/`non_interpreter_write_shape` — the write-shape classification SSOT
      │   ├── extension_dispatch.py ← Extension tool dispatch (contracts preserved; discovery stays in registry.py)
      │   ├── release_sync.py  ← `sync_release_metadata` (version carriers) used by commit-admission preflight; `_preflight_check` uses `check_history_limit`; agents may call it directly
      │   ├── review_synthesis.py ← Shared synthesis helpers; the parser/aggregator lives in plan_spec.py
      │   ├── ci.py            ← CI trigger/monitoring
      │   ├── claude_advisory_review.py ← `preflight_review` with the callable `advisory_review` compat alias; admission policy lives here; api_chat rows ride review_native_episode, agent_session rows ride the AgentSessionReviewExecutor
      │   ├── recent_tasks.py  ← Read-only context recovery
      │   ├── commit_gate.py   ← Commit gate: `_record_commit_attempt` (LLM claim synthesis), `classify_review_block`/`attempt_block_class`, `check_identical_verdict_refusal`, `count_paid_review_cycles`/`check_review_cycles_ceiling`, `commit_review_contract_fingerprint`
      │   ├── git_rollback.py  ← Wraps `git_ops.rollback_to_version`
      │   ├── git_pr.py        ← Five PR tools (non-core)
      │   ├── github.py        ← Issue + PR tools (frozen tool module)
      │   ├── parallel_review.py ← Triad + scope review orchestration
      │   ├── plan_review_references.py ← Pure reference projection, never a second plan authority
      │   ├── plan_review.py   ← `plan_task` engine: evidence, packet, fan-out over the review substrate, `plan_review_state` v2, the shared `OUROBOROS_REVIEW_MAX_CYCLES` cap, free identical replays; no scouts, Atlas, or plan_class
      │   ├── plan_review_runtime.py ← Plan-review deadline rail, `ReviewSlot` rows, `plan_slot_fit` + `preflight_oversize`, health snapshot + `plan_wave_replay_decision`, `plan_review_advisory_open` emitter
      │   ├── plan_spec.py     ← Pure plan-spec parsing/aggregation (`resolve_constitutional`); no I/O
      │   ├── plan_evidence.py ← Bounded plan-evidence manifest; the runtime data plane is denied
      │   ├── plan_packet.py   ← Reviewer packet; the W3 governance pack inlines BIBLE + ARCHITECTURE in full for self-modification plans, nav maps otherwise
      │   ├── plan_render.py   ← Wave view + `PLAN_REVIEW_CONTROL_JSON` footer; no independent behaviour
      │   ├── review.py        ← Acceptance review + multi-review adapters
      │   ├── review_context_atlas.py ← Repository atlas for scope_review + deep_self_review (plan review does not consume it)
      │   ├── query_code.py    ← Read-only code intelligence; `root=user_files` with path guards, denied to subagents
      │   ├── edit_ops.py      ← `apply_patch` + `edit_batch` with shared `_syntax_check`/`_unified_diff` backing write_file
      │   ├── media.py         ← `ocr_pdf`, `youtube_transcript`, `extract_video_frames` (dependency-optional, typed capability envelopes; frames under `artifact_store/video_frames`)
      │   ├── verify.py        ← Independent check execution through the SAME pre-exec guard machinery (shell-guarded, deliberately NOT a process-command tool); receipts append to `<drive_root>/task_results/artifacts/<task_id>/verification_receipts.jsonl`; `expected_match` kinds: substring (default) · exact · exact_line · json_equals · bytes_equal; an owner-settings change is detected and reported as a typed note, never auto-reverted — an auto-revert would undo concurrent differences without proving causation, and POST-execution checks cannot gate a receipt already written, so verify rides the PRE-execution guards; verification reads only public task info (anti-cheat); the exit-masking sensor feeds the advisory nudge without changing status; `delegation_zero_run` writes only `incomplete`/`unknown` and only after the custody scan proves no open run — a self-reported "complete" with zero runs is unverifiable authority
      │   ├── review_helpers.py ← Shared review helpers (governance-doc loading, checklist section slicing, prompt-size SSOT)
      │   ├── review_binary_context.py ← Staged/parent Git object metadata for review packs
      │   ├── review_subject.py ← `ManagedReviewSubject`; `capture_review_diff` stays byte-identical for non-managed callers
      │   ├── review_admission.py ← Pre-dispatch fit for both packets ($0 on a deterministic block): `fit_triad_prompt`, typed `not_dispatched` seats, typed oversize outcome
      │   ├── review_revalidation.py ← Review-contract fingerprint revalidation
      │   ├── scope_review.py  ← Enforcement/budget-aware whole-repo scope reviewer (§6 Review stack)
      │   ├── scope_review_session.py ← Session scope delivery from the SAME `build_scope_review_prompt` (canonical docs as nav maps); coverage manifest is forensics, never a gate; session admission per BIBLE P3 (≥200K sourced window)
      │   ├── scope_window.py  ← `scope_window` resolution + ReviewerWindow constants
      │   ├── scope_review_contract.py ← Pure scope-item parser (`normalize_scope_items`); also consumed by scripts/validate_scope_receipt.py
      │   ├── services.py      ← Service mini-manager with process-group cleanup
      │   ├── skill_exec.py    ← list_skills/skill_review/toggle_skill/skill_exec; runtime allowlist python/python3/bash/node/deno/ruby/go; gated by enablement + fresh review + hash
      │   ├── skill_publish.py ← Thin publish transaction over the four leaves; success is PR-receipt-gated
      │   ├── skill_preflight.py ← Heal-safe read-only skill preflight
      │   ├── project_journal.py ← journal_write/read, workpad_read/write, journal_tail_digest (over-limit rejected); owns `mirror_tree_coordination_to_journal`, the durable-journal mirror of tree coordination
      │   ├── presence.py      ← configure_presence, initiate_presence, typed completion/cancel
      │   ├── task_tree.py     ← tree_note/tree_read (storage SSOT: task_tree_ledger.py)
      │   ├── followup.py      ← One deferred follow-up into `state/scheduled_tasks.json`; exactly one trigger (`once` ISO or 5-field cron+tz); cap 2 pending, over-limit refused
      │   ├── join_ledger.py   ← Child-result absorption: validates lineage + exact hashes; dispositions integrated/irrelevant/deferred; `CHILD_RESULT_STALE`; keeps peek_task/discard_child_result
      │   ├── delegate.py      ← Delegation facade verbs `delegate_start` (with `retry_of`), `delegate_wait`, `delegate_cancel`, `delegate_answer`; the host pre-start rides the same `delegate_start(prompt="")` wrapper and the shared `subagent_runtime.exact_start`; supervision/recovery/custody/transport live in the named leaf modules (§6)
      │   ├── delegate_integration.py ← Delegated-patch integration: `_mutation_authority`, `_provision_snapshot` (registered before the start intent), retry-binding validation, `_capture_terminal_patch`; the skill-payload cluster (`_payload_mutation_authority`, `_rebind_payload_reference`, `_write_payload_patch_artifacts` — git diff --binary; reserved paths refuse the WHOLE apply as `blocked_reserved_paths` with the candidate preserved) and `integrate_payload_patch` (CAS, index-free git apply, QUEUES the extension reconcile request via `request_extension_reconcile`)
      │   ├── subagent_integration.py ← integrate_subagent_patch (sha256, 3-way --index, protected-path gated, genesis refused), external-workspace audited verdict, `coop_already_in_tree` no-op, compare_subagent_patches
      │   └── patch_verdict.py ← The ONE verdict writer for both patch pipelines (re-exported as `_write_verdict`): verdict subjects are minted and classified by the writer (`run_<rid>`), never prefix-matched by readers; each decision lands twice — artifact + typed `delegate_run_patch_verdict` custody row — so the acceptance packet reads one replayable store; a failed artifact write is disclosed on the row
      ├── delegate_start_claims.py ← One short pre-transport transaction serializing the zero-run/custody recheck + `START_REQUESTED` append; the nested payload claim is taken only when selected; transport and waiting stay outside claim locks
      ├── process_containment.py ← Env-token container membership (`OURO_PROC_CONTAINER_*`; /proc environ on Linux, `ps -E` on macOS, kill-on-close Job Object on Windows) with live-state read at reap — containment is unconditional because a surviving descendant can become invisible to ordinary parent-child traversal once the controller exits, and Windows spawns suspended-then-adopt so a child cannot execute before Job membership takes effect; an alive-or-undeterminable member is an honest hard-block answer, never a kill guarantee; policy layered over platform_layer
      ├── platform_layer.py    ← Cross-platform process helpers, the descendant-enumeration seam, the Windows Job Object ABI
      └── node_runtime.py      ← Execution-probed Node runtime health (`node_runtime_health`, memoized by path/mtime/size — a missing binary is never cached, and a timeout verdict is re-probed only by a larger budget), `select_skill_node_runtime`, `skill_node_emergency_path_dir`

```

### Devtools boundary

`devtools/` (including `devtools/benchmarks/cybergym/`) lives outside the runtime and package discovery: no runtime imports, normal review, artifacts in an external output root; a sentinel-marked isolated root suppresses rotation warnings.

### Gateway Boundary v1

`ouroboros/gateway/` is the single inbound browser/CLI boundary: `contracts.py` owns the envelopes (with the endpoint index in `endpoint_index.py`), `router.py` collects the routes, and `files.py`/`host_service.py` stay separate trust boundaries; `ouroboros/contracts/api_v1.py` remains a compatibility re-export only. Domain handlers translate transport into calls on existing runtime owners and must not acquire a second copy of queue, review, settings, or lifecycle policy. The facade exists for dependency direction: the UI evolves without importing the agent body, and the runtime evolves without ad-hoc browser contracts.

`gateway/owner_settings.py` is the ONE owner-scoped settings WRITE seam (the generic POST, the single-decision endpoints, and onboarding; membership = calling `_owner_write_settings`). The settings lock is a precondition — a timed-out acquisition refuses before any precondition or write — and `CommitBoundary` marks the commit instant so a later-step failure is reported as that step, with `saved` a field on BOTH sides of the boundary: an envelope that merely omits the field would be ambiguous. The invariant prose lives in the module docstring.

Frontend calls go through `web/modules/api_client.js` with the JSDoc mirror `web/modules/api_types.js`; gateway parity tests pin the mirror. Extension HTTP lives under `/api/extensions/<skill>/…` with namespaced WS dispatch.

### CLI / Headless Boundary

`ouroboros.cli` is a client of the same gateway/queue — no second task engine. Its parser is the command-surface SSOT (server, run, tasks, chat, logs, evolve, schedule, settings, skills, marketplace, local-model, MCP); streaming commands reserve stdout for the final answer/patch/result/JSONL and send progress to stderr.

`POST /api/tasks` creates an ordinary managed root; `GET /api/tasks` is a non-materializing list; `GET /api/tasks/<id>` returns the effective durable result; `/events` is the archive-aware SSE stream (§3 Chat); `/artifacts/<name>` serves simple filenames confined to `data/task_results/artifacts/<task_id>/` — a stored arbitrary path is not a download capability. The CLI refuses any `delegation_role` other than `root`, the gateway rejects caller lineage/subagent labels, and only `schedule_subagent` creates children. Reserved service metadata is written after caller metadata. Admission reserves the task id plus a worker-pool slot under one queue lock; a failure rolls back only the token-owned row with a loud typed refusal. Attachments are copied into the effective task drive before enqueue; artifact-store references are not host-path authority.

Workspace tasks default `memory_mode=forked`; `shared` is rejected for an external workspace and materialized on a forked child drive for project scope — the stored `memory_mode` reports what was requested while `drive_root` reports where the task executes, so isolation does not depend on relabelling the request.

`--detach` returns only after durable admission; `--no-stream` polls to completion; waiters treat a result terminal only after the artifact state leaves `pending`/`finalizing`, and an explicitly-partial cost gets a bounded 60-second finality wait before partial flags stay visible. `ouroboros run` exits 0 only for a completed lifecycle, a clean execution axis, no failed/degraded objective, and a finished artifact bundle — strict exit semantics keep shell automation from interpreting "the model answered" as "the requested workspace deliverable exists"; `--patch`/`--patch-out` are stricter still (failed/missing patch, no-change, empty payload, or unfinished finalization is an error).

CLI schedules and skill-manifest schedules enqueue ordinary supervisor tasks — no parallel scheduler. `resync_skill_schedules()` mirrors manifests (executable skills with supervised-task permission only) into the same table after lifecycle changes and on ticks; a blank timezone means the DST-aware system zone, with a fixed-offset fallback only when the zone is unrecoverable; the active schedule digest rides task/consciousness context.

Packaged CLI artifacts are a tiny wrapper + installer, not a second PyInstaller runtime: `packaged_cli` locates `repo.bundle`, its manifest, and `python-standalone`, bootstraps the launcher-managed repo, and invokes the same `ouroboros.cli` under the embedded interpreter with canonical env. Packaged `server` is refused — it would bypass launcher-owned bootstrap, process identity, restart, and cleanup. `run --start` is loopback-only, starts the desktop app when no ready gateway answers, follows `data/state/server_port`, and waits for `/api/health` + `supervisor_ready`. Nested AppImage extract-and-run gets a private TMPDIR because the type-2 runtime keys extraction by TMPDIR + digest; a marker-gated AppRun custodian removes the verified extracted child after the launcher exits.

Release builds also carry Node and ripgrep. Skill-side Node resolves bundled-first via `node_runtime.select_skill_node_runtime()` (rolling back to a healthy PATH node when the bundled candidate fails the health probe), while the four generic process launch surfaces run the opposite policy (`process_interpreters.resolve_process_node`): a PATH candidate that passes the execution health probe stays byte-identical in argv and child env, and the bundled runtime substitutes only when that candidate is missing or probe-dead, attesting the child-env PATH prepend — never inside a non-local executor backend. The Node downloader verifies the official archive against published SHASUMS and the macOS signing pass re-signs it under the hardened runtime; ripgrep is archive-hash verified, and `search_code` still pre-enumerates policy-approved files before invoking it, so bundling a faster binary does not widen search authority. Every bundled consumer searches `bundled_resource_bases()`: `OUROBOROS_BUNDLE_DIR` → frozen root → interpreter-ancestor roots → source checkout — server/CLI children run from the managed repo with neither `_MEIPASS` nor an in-bundle module path, and ancestor recovery covers older launchers starting newer checkouts.

The embedded interpreter must never write into the signed application (codesign seal): entry processes suppress bytecode before project imports, and `embedded_python_env()` redirects bytecode to `data/state/pycache` and user installs to `data/state/python-userbase` (`pip_install_target_args()` adds `--user` only for `python-standalone`). Disclosed residual: the userbase outranks bundle site-packages and nothing prunes or versions it — recovery is manual (remove the directory, relaunch).

Workspace binding changes the contextual repo, never the system repo for BIBLE/prompts/review governance. `/api/tasks` and project-room promotion share `workspace_admission.validate_workspace_root()` (exists, exact worktree root, disjoint from the system repo and data drive, resolved/bidirectional/case-folded); an empty Project binding is idempotently provisioned as a standalone git repo unless `workspace="none"`. Binding changes the default file/process/VCS target plus memory/lease/preflight/finalization; it does not remove top-level tools or downgrade the Architecture context in Max mode (`root=system_repo` stays; `root=skill_payload` takes bucket+skill_name). The workspace executor is a process-routing boundary: `executor_ref` is host-owned, mappings must cover the workspace without overlapping system repo/data, `network=none` only when the backend implements it, and executor processes enter durable custody records.

Workspace preflight snapshots git state (bounded porcelain rows), manifests/scripts, and tool availability into the full `workspace_preflight.json` artifact with a bounded summary in metadata; `tools_on_path`/`tools_missing_from_path` are named that way because `shutil.which` measures PATH presence, not executability, and the structured keys stay frozen because they ride durable, replaying task metadata; a collection failure is a disclosed error summary, never a fictitious full artifact.

Completion compares against the captured preflight base (task-local commits stay in the delta, not `git diff HEAD`); the patch is bound to `task_constraint.base_sha`; a moved HEAD fails closed only for `self_worktree` (a shared tree relies on reverse-patch verification); an unborn repo diffs against the canonical empty tree. Patch capture streams the tracked binary diff plus admitted untracked files, excluding scratch/cache/junk/oversized/binary-untracked/incidental-lockfile entries with per-file reasons; a sensitive-looking untracked credential is excluded per-file and disclosed as `sensitive_blocked`. `workspace_patch.json` is written for EVERY workspace finalization (including no-change and failed) and is the truth source for CLI strict-patch — it distinguishes omitted vs no-op vs failed; `workspace.patch` exists only for `ready_with_changes`.

Forked/empty task state lives under `data/state/headless_tasks/<task_id>/data`: a forked drive copies `identity.md`, `WORLD.md`, `registry.md` (a project fork carries `memory/knowledge/patterns.md` and omits global knowledge; an empty drive starts blank); dialogue, scratchpad, mailbox, and history never cross. The child drive is execution state: the result copies back to the canonical root, declared artifacts rebase to `data/task_results/artifacts/<task_id>/` (missing source = copy failure; collisions get a deterministic suffix), and verification-receipt replicas union with exact-row de-dup. Once the canonical result is terminal, late copy-back and effective reads pass through the same pure field-custody projection — the parent-owned terminal marker and cost/round/token fields cannot be overwritten. `memory_export.json` is an explicit artifact, never merged automatically.

The two router continuation tools require an explicit `predecessor_task_id` (`""` = fresh; omission or `null` refused before lookup, enqueue, or spend); predecessor source metadata survives snapshot/restore, and Main receives a defensive provider-only copy (inline threshold, persisted narrative, bounded legacy row, or an explicit gap — never a raw head/tail substitute), while exact reads and work orders stay full.

System self-modification, external workspace, and genesis remain distinct task classes; a genesis child gets `deliverable_manifest.json` (≤10,000 files, streamed hashes through 64 MiB, size-only above). Global/system installs stay runtime-policy reviewed, and `sudo` is always non-interactive (`sudo -n`).

Startup GC removes a headless child drive only when the canonical parent is terminal, artifact finalization is terminal, retention has elapsed, and the recorded child path matches the expected directory — everything needed after child-drive deletion must cross the canonical handoff before a task is presented as settled; canonical results, artifacts, genesis repos, and memory exports survive.

### Runtime topology

Two continuity roles: `launcher.py` owns the PID lock, bundle bootstrap, the server process, presentation, the restart signal, and cleanup (the packaged launcher runs outside the managed repo); `server.py` is the self-editable inner runtime. Native packages ship an opt-in systemd user unit as an alternate ingress, not a third role — deliberately without a restart policy, because the launcher owns managed restart, the crash fuse, and panic-to-complete-stop (`KillMode=control-group`).

Spawn custody: POSIX children start in a new session/process group; Windows creates the child suspended, creates a kill-on-close Job Object, assigns, then resumes — failure to establish Job custody refuses to run. The launcher records `data/state/server_process.json` (PID, pgid, server/repo paths, requested and actual ports, argv, creation time) and re-proves identity (live PID, recorded group, expected server/repo, matching command line) before any group cleanup; a stale record is removed without killing, and a port sweep stays defense-in-depth.

Same-install reaper (`launcher_server_reaper.py`): holding the PID lock licenses the reap, which runs at `main()` preflight and at the top of every launcher generation. A PID is proven only on three live facts — the exact `<REPO_DIR>/server.py` argv token, `OUROBOROS_DATA_DIR`, and `OUROBOROS_MANAGED_BY_LAUNCHER=1` — revalidated immediately before the signal, with descendants captured before the root signal and the whole pass bounded to three rounds. Kills require the byte-exact /proc environment: `ps -E` output never authorizes a kill, because argv is indistinguishable from an env assignment there, so non-/proc hosts stay report-only. The reaper deliberately ignores the custody ledger — missing entries are the defect being repaired. POSIX-only; Windows orphans die with the kill-on-close Job Object; never on panic or window-close. The startup stray check is report-only and annotates `same_install`/`foreign`.

Durable process custody (`ouroboros/process_custody.py`): `spawn_supervised()` records every long-lived child in `data/state/process_ledger.jsonl` — `{pid, pgid, fingerprint{start_time, cmd_sha256}, purpose, scope task|session|daemon, owner_task, session_id}`. The custody reaper runs at server startup and on the 10-minute supervisor tick and kills only entries whose generation or task owner is gone, by STRICT fingerprint — never by command-line class, so dev and packaged instances can coexist. The start-time fingerprint is a downgrade-safe `/proc`-first + `ps -o lstart=` pair (a rollback meeting an unknown token would prune every row WITHOUT a kill, orphaning processes; the mint order and platform details live in the `process_custody.py`/`platform_layer.py` docstrings, §10). A recorded bare tick never authorizes a kill. Current-generation session processes take a cheap liveness check that only ever KEEPS; daemon entries are kept, with skill companions the exception — reaped on owner-uninstall or foreign generation, log-only by default (`process_would_reap`), fail-safe keep-all on an unknown live-skill set. `start_parent_lifeline()` gives our python entrypoints a ppid watchdog that group-suicides when the parent dies. `_active_subprocesses`, port sweeps, and Windows Job Objects stay unchanged complements.

Launcher lifecycle: the lifecycle thread removes stale port state, starts the server, follows the actual port file, and waits for health. Exit 42 requests a managed restart (refresh bundle metadata/remotes, sync dependencies); a failed dependency install retries once, then startup proceeds under the crash fuse — an offline install with already-satisfied dependencies may still be healthy. Five ordinary crashes within 120 seconds stop automatic restart; a panic exit performs full cleanup and terminates the outer process rather than the retry loop.

Presentation: the Linux browser fallback checks `DISPLAY`/`WAYLAND_DISPLAY` before touching pywebview; GTK needs a live `Gdk.Display.get_default()`, while Qt is trusted on env alone — probing it constructs a `QGuiApplication` that can itself abort, so the probe would cause the crash it exists to avoid. On probe failure the same launcher supervises the same server, prints the authoritative URL, and opens the system browser best-effort — the browser is the owner's application, deliberately outside process custody and teardown. A repeated launch that loses the PID lock soft-polls the port file (~10 s) and opens the last-read loopback URL best-effort. Browser-mode SIGINT/SIGTERM handlers only set the shutdown event; `sys.exit` paths leave PID-lock release to the registered `atexit` owner, because a second release could unlink a newer launcher's lock.

Extension children, delegated runtimes, services, the local model, and companions all sit beneath these roles: every long-lived process enters the custody ledger or a process group. Disclosed residual: shutdown admission is not atomic with publishing a spawned child (a signal can land between `Popen` and the record) — tracked, not a reason for a second launcher.

### Data layout (`~/Ouroboros/`)

`~/Ouroboros/` is the default application root; `APP_ROOT`, `DATA_DIR`, and `SETTINGS_PATH` are independently env-overridable (`ouroboros/config.py`, §7).

```
~/Ouroboros/
├── repo/                          ← the self-modifying git repository; launcher-managed git clone keeps server.py in sync (never copied per launch — §2)
│   ├── ouroboros/                 ← core package (module map above)
│   ├── supervisor/                ← supervisor package
│   ├── web/                       ← Web UI; ES-module pages under web/modules/
│   ├── docs/                      ← ARCHITECTURE.md (this map), DEVELOPMENT.md (engineering handbook), CHECKLISTS.md (review checklists SSOT), CHECKLISTS_ARCHIVE.md, CREATING_SKILLS.md, DESIGN.md, DEPLOYMENT.md
│   └── prompts/                   ← SYSTEM.md, SAFETY.md, CONSCIOUSNESS.md
├── data/
│   ├── settings.json              ← user settings (API keys, models, budget)
│   ├── task_results/              ← durable task results (task_results/<id>.json); artifacts/<task_id>/ holds .artifact_manifest.json (private metadata) + artifact files; .scratch_manifest.json declares ephemeral scratch {abs_path: sha256} excluded from patch capture only while content matches
│   ├── artifact_versions/<task_id>/ ← artifact recovery history, last 5 versions per name
│   ├── task_drives/<task_id>/     ← task-scoped scratch; startup prunes terminal tasks after the headless retention window
│   ├── task_trees/<root>/blackboard.jsonl ← append-only swarm blackboard + beacons; tree-scoped and ephemeral (task_tree_ledger.py), pruned on root terminal
│   ├── state/
│   │   ├── state.json             ← runtime state + compatibility cost projection; never the monetary authority
│   │   ├── queue_snapshot.json    ← durable PENDING/RUNNING recovery projection + worker counts + explicit worker_pool_disabled_reason
│   │   ├── usage_attempts.jsonl   ← append-only monetary authority: per-attempt id + state transitions; a settled attempt with cost=None and a numeric reservation bound counts at the bound
│   │   ├── usage_attempts.quarantine.jsonl ← loud quarantine of a proven-corrupt final row; the validated prefix stays readable
│   │   ├── usage_import_watermark.json ← resumable idempotent legacy-import watermark
│   │   ├── request_wire_compatibility.json ← cross-process locked, schema-versioned 14-day exact-route wire evidence (request_wire_contract.py)
│   │   ├── capability_evidence.json ← sourced model-capability evidence (capability_evidence.py)
│   │   ├── process_ledger.jsonl   ← durable process-custody ledger (process_custody.py; Runtime topology)
│   │   ├── server_port            ← active HTTP port for launcher/browser handoff
│   │   ├── server_process.json    ← launcher-owned server identity record for relaunch cleanup
│   │   ├── advisory_review.json   ← durable advisory/review ledger (runs, attempts, obligations, commit-readiness debts)
│   │   ├── deep_self_review_context.json ← last Atlas manifest + model metadata
│   │   ├── code_intel/<repo_key>/inventory.json ← code-inventory facts; no raw source cache
│   │   ├── evolution_metrics_cache.json ← per-tag metrics cache regenerated by /api/evolution-data
│   │   ├── evolution_campaign.json ← campaign objective/progress/history/budget
│   │   ├── evolution_checkpoints.jsonl ← append-only per-cycle checkpoints
│   │   ├── post_task_evolution_request.json ← worker-written one-shot promotion signal; consumed + deleted by the supervisor idle tick; dropped while evolution_owner_stopped
│   │   ├── post_task_evolution_counter.json ← per-drive every_n counter
│   │   ├── scheduled_tasks.json   ← cron (5-field + tz) and one-shot {type:"once", run_at} schedules; consumed one-shot receipts age out past the unified GC retention
│   │   ├── claudexor_rotation_provisioning.json ← receipt of the last rotation-reconcile settings POST
│   │   ├── projects.json          ← Project registry: immutable id/chat identity, working folder, lifecycle/routing fence, revision; tombstones are durable and never age-pruned
│   │   ├── project_task_bindings.json ← schema v1 root↔Project bindings with REQUIRED typed origin; one-way enrichment; tombstoning never removes a binding
│   │   ├── ui_preferences.json    ← owner-local layout preferences + monotonic project_seen_revision ACKs
│   │   ├── cancel_intents.json    ← compact locked projection of ACTIVE cancel intents; the forensic trail is typed cancel_intent rows in logs/supervisor.jsonl, never read back (cancel_intents.py)
│   │   ├── terminal_deliveries.json ← bounded delivered-dedupe + PENDING terminal-answer outbox (terminal_delivery.py)
│   │   ├── extension_companions.json ← runtime snapshot of live companion processes
│   │   ├── extension_reconcile/   ← worker-written markers consumed by the server lifespan pickup task
│   │   ├── review_continuations/  ← durable blocked-review continuations (+ corrupt/ quarantine; archived/ holds settled un-resumed rows ≥7 days, never deleted)
│   │   ├── workspace_executor_processes/ ← durable local/docker executor cleanup records
│   │   ├── consciousness_observations.jsonl ← append-only inbox; rows retained until a settled successful cycle appends an ACK; malformed rows stay visible as source gaps
│   │   ├── headless_tasks/<task_id>/data ← forked/empty child memory drives (CLI / Headless Boundary above)
│   │   ├── pycache/, python-userbase/ ← embedded-interpreter bytecode + user installs (packaged builds; CLI / Headless Boundary above)
│   │   ├── betterleaks/           ← versioned scanner runtime + archive cache, created only by the explicit source-checkout installer
│   │   ├── cx/                    ← managed Claudexor store: immutable <version>-<sha12>/ trees each with managed-runtime.json + node/, cache/ of verified archives, install.lock
│   │   └── skills/<name>/         ← per-skill state plane: review.json (content_hash, findings, reviewer_models, raw actor records, advisory_result — findings stay authoritative), owner_attestation.json (owner-issued marker; removal invalidates, content edit stales via content_hash, the agent can never forge it), review_history.jsonl (append-only terminal history; raw reviewer text never exposed to chat), accepted_rebuttals.json (injected into later review prompts), deps.json (isolated-dependency install fingerprint), auto_repair.json (marketplace auto-repair dedup by payload hash), ouroboroshub.json (publication receipt; malformed reads as published=null + typed diagnostic), health.json (durable health vector; flags live→broken regressions across restarts), auth_token.json (content-hash-bound Host Service token), enabled.json ({"enabled": bool, "updated_at": iso_ts}), extension_calls/ (transient per-call child-process payloads), __extension_imports/<pid>-<uuid>/skill/ (staged import trees — §13)
│   ├── claudexor/                 ← Ouroboros-owned Claudexor home (CLAUDEXOR_CONFIG_DIR): daemon descriptor/token, credential profiles, runs, ouroboros-owned.json, daemon.log; never the operator's ~/.claudexor
│   ├── memory/
│   │   ├── identity.md            ← durable identity
│   │   ├── scratchpad.md          ← auto-generated from scratchpad_blocks.json (FIFO, max 10 blocks)
│   │   ├── dialogue_blocks.json, dialogue_meta.json ← consolidated dialogue memory (dialogue_summary.md remains a read-only legacy fallback when present)
│   │   ├── WORLD.md               ← host profile generated on first run
│   │   ├── knowledge/             ← topic files + auto-maintained index; patterns.md (Pattern Register), improvement-backlog.md (backlog SSOT), *_journal.jsonl + *history.jsonl provenance
│   │   ├── deep_review.md         ← written by the deep-self-review task
│   │   ├── registry.md            ← memory awareness map
│   │   └── owner_mailbox/         ← per-task user message files
│   ├── projects/<id>/knowledge/   ← per-project facts + provenance sidecars; logs/task_reflections.jsonl holds full reflections with a bounded pointer row in the canonical log
│   ├── observability/             ← private forensic ledger: blobs/<sha256>.json.gz compressed CAS payloads (0600) + calls/<task_id>/<call_id>.json manifests
│   ├── services/<task_id>/<service>.log ← service runner logs; public tool output is bounded redacted tails + private blob refs
│   ├── logs/
│   │   ├── chat.jsonl             ← canonical chat: one logical message stored once, projected into Main/Project lenses
│   │   ├── chat_annotations.jsonl ← compact routing status by client_message_id; presentation-first, never routing authority
│   │   ├── progress.jsonl, events.jsonl, tools.jsonl, supervisor.jsonl ← runtime ledgers
│   │   ├── task_reflections.jsonl ← canonical reflection log
│   │   └── containment_faults.jsonl ← append-only compact projection of containment incidents (delegate_custody.py)
│   ├── archive/                   ← rotated logs, rescue snapshots, archived managed repos
│   └── uploads/                   ← chat file attachments (paperclip)
├── Deliverables/                  ← bare user_files filenames land here (OUROBOROS_DELIVERABLES_ROOT; sibling of projects/, outside repo/ and data/, never GC-pruned)
└── ouroboros.pid                  ← launcher PID lock; platform lock auto-released on crash
```

---

## 2. Startup / Onboarding Flow

Packaged startup is an ordered ownership transaction. The launcher prepares the platform UI runtime (or performs the Linux browser-mode probe), acquires the single-instance lock, verifies that Git is available, and validates and bootstraps the embedded managed-repo seed. Those are preconditions of the server itself, so they precede it. It then removes only identity-proven stale server state plus stale runtime ports, starts the lifecycle thread, and waits on `/api/health` at the authoritative port from `data/state/server_port`. Only then is first-run onboarding presented, against that live server, before the pywebview shell or the Linux browser presentation described above opens. The server starts the gateway first and starts the supervisor/worker pool only when provider configuration is structurally sufficient.

Onboarding runs after the gateway because a first-run owner must be able to reach `/api/*` — connecting an agent subscription is a live API conversation, not a form field. A gateway without a supervisor is exactly the state the readiness predicate below already produces, so this ordering needs no second server, mode, or onboarding state machine. `ouroboros/launcher_onboarding.py` owns that presentation (readiness decision, setup window, window-lifecycle bridge) so the launcher stays the process/window orchestrator. When completion reports that a boot-pinned value changed, the launcher recycles the managed server through its existing lifecycle loop rather than counting the exit as a crash. Neither the launcher's pre-server normalization nor the server's boot normalization may CREATE `settings.json`: on a genuinely fresh install the first bytes of that file are the owner's own onboarding save, and the fresh-install proofs are gated on its absence.

`has_startup_ready_provider()` is a structural gate, not a network, credential, entitlement, model, or local-process probe. It returns true for any non-empty recognized remote configuration: OpenRouter, OpenAI, Anthropic, MiniMax, Cloud.ru, an OpenAI-compatible base URL, GigaChat credentials, or the GigaChat user/password pair. It also accepts any active task-capable local routing flag (`USE_LOCAL_MAIN`, `USE_LOCAL_LIGHT`, or `USE_LOCAL_FALLBACK`). `USE_LOCAL_HEAVY` is legacy migration input only and cannot make a runtime startup-ready. `LOCAL_MODEL_SOURCE` by itself is insufficient, but a routing flag does not prove the model process is already live. When the predicate is false, the server marks startup complete without starting workers so the web UI can serve the blocking onboarding overlay; a later successful settings save hot-starts the supervisor.

Every host renders one served page. `GET /onboarding` returns `onboarding_template.html` with the `settings_setup_contract` bootstrap injected and links `onboarding.css` plus `web/modules/onboarding_wizard.js` as ordinary static assets, so wizard steps can import the same modules the rest of the UI uses — an inlined `srcdoc` string cannot. The desktop setup window opens that URL, the blocking overlay frames it, and a browser owner can open it directly. `GET /api/onboarding` remains the readiness probe: 204 once the structural gate passes, otherwise the same page. The route is side-effect-free. The flow is providers/access, agents, model slots, review enforcement plus initial runtime mode, budget, and summary; it does not configure context mode. The agents step sits directly after access because it explains what that access already bought: a compact three-rung ladder (one API key or local model runs Ouroboros, one agent plan moves delegated subagents and commit/scope review onto that plan, several accounts rotate) beside one static inline diagram of the rotation. It is skippable, owns no input, and mounts the shared login cards in `full` mode rather than `compact`, because compact omits the paste-code entry a Claude login needs when its localhost callback cannot complete. Every account fact it renders comes from the shared Claudexor status store, and what it observed becomes the completion payload's `subscriptionsConnected` declaration — a request to look at the daemon, never an authority. Provider fields may coexist, rare fields remain mounted inside the “More options” disclosure, and the visible model defaults update from the current provider profile. An Anthropic key typed but not yet saved still reveals the Claude-runtime card without presenting the backend's expected no-key state as an error.

Completion is one HTTP conversation on every host: `POST /api/onboarding/complete` (`gateway/onboarding.py`) replaces the earlier `POST /api/settings` + `POST /api/owner/runtime-mode` pair whose failure between the two writes left providers saved and runtime mode not. The order is fixed: re-prove install-time status server-side (a payload boolean is a request, never an authority), validate through the shared setup validator and the same structural startup gate, read ONE live agent-account/model snapshot when the payload declares that subscriptions were connected, compile the install preset, apply ordinary provider normalization FIRST and add the structured preset keys on top of it, persist settings + next-boot runtime mode + the fresh-install safety default + the one-shot preset marker + the durable completion fact in a single write whose eligibility is re-proved under the settings lock, and only then start the supervisor. Install time means three proofs together, because "no startup-ready provider" is a state an old install reaches whenever its key stops working: onboarding has never completed here (`OUROBOROS_ONBOARDING_COMPLETED_AT`, written by every completion, including a skipped or subscription-less one), no preset generation has been applied, and there is no `settings.json` yet — the same genuinely-fresh-install rule the wizard already uses for the `light` safety default. `GET /api/onboarding` is a pure read: it still normalizes what the wizard displays but never persists, because a read that creates `settings.json` silently disqualifies both install-time latches. Compatible-model discovery, Claude-runtime status/repair, and local-runtime controls likewise use the ordinary endpoints rather than a parallel desktop bridge. There is no second completion path: neither the `POST /api/settings` + `/api/owner/runtime-mode` pair nor a desktop `save_wizard` bridge survives, and the desktop setup window's bridge is window lifecycle only. The one reason a desktop-only save ever existed — authoring the initial `OUROBOROS_SAFETY_MODE=light`, which neither the shared validator nor the generic settings endpoint may do — is discharged by this endpoint on its own server-side freshness proof. A completion that fails AFTER the bytes reach disk reports that it saved, together with the stage that failed, rather than claiming nothing was written. A 2xx is not a completion by itself: only the exact success envelope is, because the saved runtime mode and the restart receipt both live in that body, and an unparseable body is unknown rather than empty. When completion changes something the running process pinned at boot — its runtime-mode baseline — the desktop launcher recycles the managed server it owns rather than showing a restart nag, the framed overlay shows its restart card, and a plain browser tab shows the wizard's own saved-but-restart-required screen instead of navigating into an app running a different mode than the owner chose. A provider save can start the previously absent supervisor in the current process. The overlay frames the wizard sandboxed but with popup permission: the agent sign-in link is the step's primary action and a sandbox without it blocks that click silently.

`ouroboros/subscription_install_presets.py` is a pure compiler with two sibling products, not a reviewer/subagent matrix. Its Available-subagent projection runs for every eligible completion: normalized API/local settings alone are enough for API-only and local-only installs, while a declared subscription causes `gateway/onboarding.py` to read exactly one live Claudexor snapshot before the settings transaction. That snapshot remains the authority for supported harnesses, exact model ids, enabled accounts and the daemon's quota-aware unpinned `next_up` fact; the browser and compiler do not re-derive account routing from profile names.

The task-actor compiler emits one real unpinned `agent_session` row for every connected supported harness (Claude, Codex, Cursor and Agy), then adds credentialed API/local actors linearly: a Light-derived Fast scout, and in the one-session case a distinct effective Main route as Independent perspective when it differs from the scout. It deduplicates identical routes and never constructs a powerset. Agy's automatic row is `gemini-3.7-flash-high`; other discovered Agy models remain editor choices. The reviewer compiler consumes only independently ratified Claude/Codex/Cursor policy; on the fresh-install path its slots are `subagent_id` REFERENCES into the roster the preset itself ships — a seat whose session route matches no task actor mints a `review-<harness>` roster row — while an owner-configured roster stays validate-only and its reviewer seats remain self-contained inline routes. Consequently Agy-only setup succeeds with its task actor and leaves existing provider-normalized API/local reviewer defaults intact, while mixed Agy+core setup produces the same reviewer bytes as the core subset alone. Missing required exact discovery returns a typed pre-write refusal and persists neither a partial task preset nor a partial reviewer preset.

`POST /api/onboarding/subagents/preview` runs the same compiler against the open provider/local draft and optional live snapshot without persisting. The editable result is the value later submitted as `OUROBOROS_SUBAGENTS`; an owner-edited canonical draft is validated and preserved rather than regenerated. Completion persists that actor value, the independent reviewer disposition, the preset receipt, completion facts and other onboarding settings through the existing one-write document-lock/fingerprint boundary. Network discovery happens before the lock. A daemon failure keeps the wizard open and offers the explicit finish-without-agent-defaults path; a read or failed refresh never rewrites saved intent.

Validation is deliberately structural. At least one exposed remote configuration or a local model source is required; local-only setup must also route at least one active lane locally. Main is required, while Light, Vision, Consciousness, and Fallback keep their documented inheritance/empty semantics. Heavy remains readable only for bounded migration into an explicit API actor and is absent from active validation/model selection. Review enforcement and runtime mode must be known enum values, budgets must be finite and positive, MiniMax region is closed to its supported values, and a Hugging Face local source needs a filename. Credential length is checked only when that field changed in the submitted payload. Rechecking an unchanged short legacy value would reject the whole form, including the replacement typed elsewhere, and make that value impossible to repair.

Closing the desktop setup window without saving is non-fatal: launcher startup continues and the main web surface remains available, where `/api/onboarding` still mounts the blocking overlay until structural readiness is satisfied. In Linux browser mode the setup window is skipped entirely and that same web overlay is the first-run owner surface. Claude-runtime repair remains optional and fail-soft so a broken Anthropic tooling lane does not prevent an otherwise configured provider from starting.

Provider readiness and provider defaulting are separate. With no OpenRouter, legacy OpenAI base, or OpenAI-compatible endpoint, exactly one registered direct provider receives explicit provider-prefixed defaults and migration of untouched shipped/legacy slot values; OpenAI, Anthropic, Cloud.ru, GigaChat, and MiniMax each use their own registered defaults. Multiple direct providers remain owner-editable rather than forcing one family. OpenRouter retains router-style routing. An arbitrary OpenAI-compatible endpoint receives no guessed model ids because compatible servers have no universal safe name; the wizard can fetch `/models`, but the owner must select explicit `openai-compatible::...` routes.

For a local-source installation with no remote provider, normalization clears only untouched shipped remote Light/Fallback values that would otherwise be unreachable; an owner-authored value and a slot explicitly routed local are preserved. This is migration of defaults, not a model allowlist, and it never treats a successful normalization as proof that the local server is running.

`scripts/build_repo_bundle.py` creates the packaged seed only from a clean named checkout. It requires the release tag matching `VERSION` to be annotated and point at HEAD, proves that HEAD is reachable from the configured source branch, writes a git bundle of that commit/tags, and records schema, application version, source SHA, release tag, bundle hash, and managed branch/remote metadata. The launcher validates required manifest fields, schema, application-version and release-tag spelling, and the bundle SHA-256. It does not validate a separately enumerated per-file “member set”; clone-time Git verification proves that the manifest source object exists and that the checked-out HEAD equals it.

`ensure_managed_repo()` owns packaged checkout bootstrap. A first install clones the bundle into a temporary checkout, verifies and configures the pinned source SHA and managed branches/remotes, then moves the completed checkout into `repo/`; an existing legacy non-git directory is archived first. Once a managed git checkout exists, a changed application manifest does not archive or replace its working tree. Bootstrap atomically refreshes managed metadata and the official `managed` remote in place, preserving the local branch tip and owner edits. Ordinary restart performs no automatic network fetch or movement to a remote branch. Network movement to an approved official SHA belongs to the pinned managed-update path in `supervisor/git_ops.checkout_and_reset`, not to bootstrap. `origin` remains optional personal persistence and is not the official update authority.

Bootstrap also creates the initial world profile when absent and seeds launcher-owned native skills without resurrecting an intentionally deleted seed on every launch. Dependency installation runs only when checkout/bootstrap metadata changed. Its boolean result reaches the launcher: first-boot failure is logged loudly but does not immediately brick an install whose dependencies may already be satisfied; server health and the crash fuse decide.

After exit code 42, the launcher refreshes the existing bundle metadata and runs dependency synchronization before starting the edited body. A failed install receives one visible retry after five seconds; a second failure is retained in logs and startup continues under the five-crashes-in-120-seconds fuse. The rationale is asymmetric: silently losing pip failure makes a later ImportError inexplicable, while refusing every offline restart would break a checkout whose requirements were already present.

Managed supervisor bootstrap is the sole owner of destructive dirty-tree recovery. Before any reset/clean, `supervisor.git_ops` writes a rescue directory containing porcelain status, a binary diff, a stash-created rescue object/ref when possible, copied untracked files with completeness metadata, unpushed-commit evidence, and `rescue_meta.json`. An incomplete snapshot blocks `rescue_and_reset`; it is not permission to discard what could not be captured. Normal managed bootstrap then cleans back to the local branch's own HEAD, not to `managed/<branch>`.

Managed update is the second user of that machinery, with the opposite failure policy. Every destructive rollback path (orphan watchdog, boot attempt cap, failed smoke, failed re-materialization) shares one choke point in `rollback_managed_update`, and the boot-resume re-materialization resets the tree on its own; both take a FRESH rescue before the first destructive command, because the pre-update snapshot was captured before the merge existed and holds none of the resolver's work. Every rescue status, topology, diff, and index-repair Git process is bounded by `OUROBOROS_RESCUE_GIT_TIMEOUT_SEC` and has its process tree terminated on timeout; ordinary Git calls keep their existing timing, while managed rollback remains disclosed fail-open. The hook is FAIL-OPEN by owner decision — a rescue that cannot be taken never blocks the rollback, it is logged and disclosed — and it writes one durable `supervisor.jsonl` line at capture time, BEFORE the destruction, so the record survives a crash between the reset and the terminal event; a `git status` that cannot answer counts as dirty. The snapshot understands merges — MERGE_HEAD, the unmerged path list and MERGE_MSG are recorded best-effort, `git stash create`'s refusal on an unmerged index is disclosed instead of silently leaving no ref, and `changes.diff` is written as raw bytes with a hardened capture argv and environment (no external diff/textconv drivers, no colour, pinned prefixes, no `GIT_DIFF_OPTS`), because it is the only carrier of a resolution stash cannot capture — and is deliberately NOT linked to an active evolution transaction, which would flip that campaign's cycle to abandoned for an unrelated reason. The update transaction carries a pointer to what was rescued, persisted before the first destructive command: a replayed rollback does not duplicate a snapshot it already took, a retry after a failed attempt drops the marker and re-rescues the tree it actually finds, and the resolver's own objective names the latest rescue directory (with an honest count when several were taken) — for the whole transaction, since re-materialization re-creates MERGE_HEAD and a dirty tree WITHOUT replaying the rescued edits and must never be read as their return.

An active Evolution transaction or managed-update merge uses `rescue_and_block`: recovery evidence is linked to the transaction, the tree is left intact, and Evolution is paused rather than erasing partially resolved work. With no such owner, startup uses `rescue_and_reset`. Source/local-development server startup skips the managed checkout/reset path and performs only dependency sync plus import test. Worker startup checks are diagnostic and warning-only: launcher-management environment variables propagate into worker, review, and test subprocesses, so allowing each constructor to auto-rescue would let an incidental child steal or clean another actor's in-progress edits.

`server.py` establishes `OUROBOROS_AGENT_PYTHON` from its actual interpreter immediately after binding the repo import root and before workers or review subprocesses start. Hermetic commit/review preflight uses that handle (then `sys.executable`, then `python3`) so tests run in the environment that contains Ouroboros dependencies; plugin verification is part of that preflight, not a separate launcher claim that every interpreter was live-probed at startup.

User process tools have a separate surface-aware resolver. For exact unversioned `python`/`python3` on `run_command`, `run_script`, `start_service`, and run-kind `verify_and_record`, registry pre-dispatch resolves once before deterministic guards so the guard and handler see byte-identical argv. Priority is a reviewed skill environment; backend `python3` for an executor mapping; project `.venv`, otherwise target `PATH`, for external/user work; then the verified agent interpreter for system-repo, task-drive, and artifact surfaces. Absolute or versioned interpreters, shell bodies, and non-Python commands remain literal. Resolution emits secret-free provenance, never silently installs dependencies, and fails closed only when a system-owned interpreter cannot be proven.
## 3. Web UI Pages & Buttons

The Web UI is a build-free vanilla-JavaScript SPA (`web/index.html`, shared CSS, and `web/modules/*`). The absence of a TypeScript or bundler step is deliberate: the running interface remains inspectable and editable by Ouroboros without regenerating opaque artifacts. `web/app.js` owns top-level page, Project-panel, and mobile-navigation state; feature modules own their presentation and domain-specific interactions. Temporary surfaces must pair every listener, timer, observer, stream, request controller, chart, and DOM subtree with the lifecycle that created it.

The same SPA is served in the desktop shell, ordinary browsers, Docker/web deployments, and the Linux browser fallback. On Linux, if the launcher cannot establish a usable GUI backend and display, it starts the normal managed server, prints and best-effort opens its loopback URL, and serves onboarding through the existing blocking web overlay. The browser is the owner's application and deliberately remains outside Ouroboros process custody. This fallback changes presentation only: it does not create another API, onboarding contract, runtime identity, or state owner.

The desktop shell exposes a small `MainApi` JS bridge (`window.pywebview.api`, `launcher.py`): the three native confirmation methods (runtime mode, reviewed-skill auto-grant, skill key grant), `download_file_to_downloads(url, filename, open_external)`, `open_file_with_default_app(url, filename)`, `open_external_url(url)` (absolute http(s) or mailto; a bounded join on the detached opener reports a settled failure honestly), and `save_bytes_to_downloads(filename, b64)` for live base64 payloads. Both loopback-file methods share one guard: loopback host, exact server port, and a path allowlist of `/api/files/download`, `/api/extensions/...`, and `/api/tasks/...` (durable chat-media artifacts). Because the embedded WebView has no new-window or download delegate, `ui_helpers.js` installs a shell-only link interceptor — a delegated click listener for `target="_blank"`/download anchors plus a `window.open` shim — only when the pywebview bridge is present (never in ordinary browsers), and in BOTH top-level documents, mirroring the Alt guard's two-document install: the SPA and the framed onboarding wizard, whose document resolves the bridge from its parent window. It classifies each URL: loopback file forms ride the existing bridge helpers, any other http(s)/mailto rides `open_external_url`, and `data:`/`blob:` payloads ride `save_bytes_to_downloads`. Bridge methods are feature-detected per call because the packaged launcher updates only on reinstall while the served frontend updates with the managed repo: a missing or failing `open_external_url` and a launcher with no file bridge at all degrade to copy-link-plus-toast, a missing `save_bytes_to_downloads` to an honest toast, and the file helpers keep their `open_file_with_default_app` → `download_file_to_downloads` → `window.open` skew chain.

One shared WebSocket is created for the application and connected only after feature listeners are registered and the initial complete set of Project chat ids has been fetched. Browser modules fan frames out by typed event and `chat_id`; Projects do not open independent sockets. REST remains the recovery and durable-read path, so the WebSocket transports live changes without becoming a second task, queue, Project, review, or settings state machine.

### Navigation and shared UI contracts

Primary navigation exposes Chat (Main), a collapsible Projects group, Files, Skills, Widgets, Dashboard, and Settings. About remains a Settings sub-tab. `syncNavigationState()` is the single presentation state machine for the active page, active Project, Projects expansion, mobile drawer, and panel backdrop; independent toggles must not leave multiple rows active or make a hidden surface appear selected. The sidebar and Project panel are resizable on desktop, and their widths are stored as owner-local UI preferences rather than runtime settings.

Each active or deleting Project has a sidebar row. Active rows can be opened, renamed, or deleted through pointer- and keyboard-operable controls; the backend owns the 80-character name limit and lifecycle truth. Unread Projects sort ahead of read Projects and then by durable activity. A deleting Project becomes non-openable and visibly remains in the transitional state until the server publishes authoritative registry state. On narrow screens navigation becomes an explicit drawer and the Project chat becomes a full-width overlay with a backdrop. There is no gesture-only navigation layer competing with message scroll, text selection, or the software keyboard.

Shared frontend primitives prevent pages from acquiring competing contracts. `page_header.js` owns page headers and tab strips; `page_icons.js` owns navigation/header icons; `api_client.js` owns browser API calls and typed error propagation; `api_types.js` mirrors the browser-facing contract shapes; `ui_helpers.js` owns shared status, safe-field, host-bridge, keyboard menu-lock suppression behavior, and the design-system action button for host-stamped system chat rows (`createSystemMessageAction`) (both top-level documents — the SPA and the onboarding wizard iframe — install its Alt guard on their own windows); `skill_card_renderer.js` owns installed-skill cards; `hub_sync.js` owns the one catalog×listing hub-card verdict (actions install/installed/update/adopt/wait_pr/none plus badges) consumed by the OuroborosHub tab and the My-skills hub badges — the hub tab joins the catalog with the global `/api/extensions` listing by canonical name and no longer reads the bucket-scoped installed endpoint; `client_surface.js` owns the send-time sending-surface snapshot (raw observables, no device taxonomy) that `chat.js` spreads into each chat frame; `chat_markdown.js` owns the chat rich-markdown renderer: a marked+DOMPurify pipeline, a chat-local URL policy for external http(s)/mailto plus the canonical `/api/files/download` form, KaTeX (`$$`/`\[..\]` display and `\(..\)` inline — single-`$` deliberately unsupported), lazy-loaded mermaid on the first fence, bounded ```` ```chart``` ```` rendering with forced responsive options, and an enhance/destroy lifecycle whose disposers `chat.js` invokes on bubble removal; its vendored rendering libraries are marked 18.0.7, DOMPurify 3.4.14, highlight.js 11.11.1, KaTeX 0.17.0, and mermaid 11.16.1, pinned in `web/vendor/VENDOR-MANIFEST.md`; `log_events.js` owns event classification and the shared technical outcome reducers plus one factual task-presentation projection consumed by Chat and Logs. The projection translates task truth only into `Working` / `Done` / `Done with warnings` / `Failed` / `Cancelled`; it does not own actions, incidents, or notifications, and compact headlines never expose raw reason codes. `toast.js`, `masonry.js`, `widget_frame.js`, `widget_job.js`, and CSS tokens own common notifications, framed-widget bootstrap/lifecycle, bounded widget request/job policy, and layout; `task_control_menu.js` owns the S3 task stop/hurry dropdown ("Wrap up" / "Hurry up" / "Stop now" — frozen owner wording; a host-attested budget-paused member swaps the working pair for "Resume" beside the stop escalation, POST /api/tasks/{id}/resume with server refusals surfaced verbatim) shared verbatim by Chat live cards and the Activity tab: eligibility gates differ per surface, but the actions, endpoint bindings (`stop_policy` mapping, stable per-task `request_id` retry), in-flight locking, and typed refusals do not; a pending cancel collapses the menu to the hard escalation only, dismissing the menu continues the run, and "Hurry up" acknowledges via LOCAL toast only — never a chat message (HQ1); because both consumers live inside clipped or scrolling containers, the temporary menu is page-owned, uses viewport-fixed flip/clamp placement, closes on ancestor/page scroll, window resize, or trigger visibility loss, and disposes its listeners and observer with the body portal instead of weakening container overflow. The reason is dependency control: frontend work should not require reimplementing supervisor, review, marketplace, extension, and provider semantics in each page.

`confirm_dialog.js::openConfirmDialog` is the one browser-dialog authority. Confirm mode resolves a strict boolean; input mode resolves `{confirmed, value}` and returns an empty value on cancellation; alert mode renders one acknowledgement button. Cancel, Close, backdrop, Escape, and supersession by a newer dialog all resolve as non-confirmation. Native `window.prompt`, `window.confirm`, and `window.alert` are forbidden in `web/modules`: they are visually and behaviorally inconsistent across shells, block the browser event loop, and `window.prompt` silently returns `null` in the macOS PyWebView shell because that backend has no prompt delegate. Critical controls therefore act only on the exact confirmed result; Panic's confirm-and-send sequence is one testable operation rather than a confirmation call detached from the command it guards.

### Chat and Projects

`web/modules/chat.js` owns the canonical message timeline, input recall and draft, attachment staging, runtime controls, budget projection, routing annotations, task cards, child cards, WebSocket subscriptions, thread routing, dedupe/insertion order, unread state, and reconnect reconciliation. Its per-instance `chat_media.js` controller owns delivered-media/link builders, task-keyed photo/file grouping, player registries, object URLs, dialogs, and their listeners/timers; `reset()` runs before a full history rebuild and `destroy()` joins the chat-instance teardown. Every ordinary message has one canonical durable chat row. Project views are lenses over those rows and task bindings; Project conversion does not create a second message, unread event, or cost record. Routing acknowledgements live in a compact sidecar keyed by `client_message_id` and update the existing owner message without adding a synthetic assistant bubble.

Top-level messages, media bubbles, and task-card roots are ordered by their raw numeric timestamps rather than formatted display text. An insertion precedes only siblings with a strictly later timestamp, so equal timestamps retain arrival order and timestamp-free transient nodes retain append order; the typing indicator remains last. Ordinary application-controlled Chat height mutations use a stable-viewport seam: when the pre-mutation distance to the live edge is at most 48 CSS pixels, the transcript follows the bottom; otherwise it restores the visible message or keyed nested-card/Reviews anchor. Awaited Load-older, reconnect reconciliation, browser-visibility return, and cross-instance restoration retain explicit lifecycle handling rather than extending that synchronous seam across an await or hidden layout. Native scroll anchoring may assist, but is not the application authority. While the reader is away, remote in-thread delivery coalesces into one instance-local activity marker; history/reconnect replay, local interactions, and layout-only changes do not set it, and landing at the bottom or using jump-to-latest clears it. Collapsed live cards reserve a one-line title band (a coined two-line name still clamps to two; the stable-viewport seam absorbs that growth), a two-line activity band (kept while the card runs and on a finished card that has narration; a finished card whose activity is empty folds the band) and a compact metadata band that the quiet `Reviews N` count shares, without an empty Reviews row or a fixed outer-card height; a collapsed nested child card reserves none of this — it is one identity row (status chip · `role · model` · notes/toggle) in quieter ink, and its activity and metadata appear only when it is expanded; the card's title, activity and metadata text is selectable and copyable, and a drag that selects text never toggles the card; the neutral 32-pixel jump control is centered above the composer. Scroll position is remembered per chat instance and restored after a Project panel is recreated.

History reconciliation is two-pass. Progress and system records first rebuild timestamped task-card state; cards and regular user/assistant messages are then inserted chronologically; only after that may terminal state seal a card. This prevents terminal replay from discarding earlier progress, preserves progress-only and nested-child cards, and recovers cards whose final summary was missed while disconnected. Live echoes and history rows are deduplicated with stable message identities, while a reconnect may still rebuild ordinary bubbles from durable history when the prior socket missed them.

The Main chat receives ordinary main-thread dialogue plus exactly the two host-stamped Project lifecycle rows — the agent-initiated `project_started` entry row and the terminal `project_completion_summary` — each rendered with the shared design-system "Open Project" action; all other Project traffic (progress, digests, logs, raw Project dialogue) stays in the Project thread. These lifecycle rows are plain dashboard text: the producer strips markdown from the excerpt once (before durable write and live send), history normalizes older persisted rows on read (`chat.jsonl` is never rewritten), and the renderer keeps any system row without `markdown: true` as escaped plain text (except `skill_review`, which keeps its dedicated renderer) — system rows that do carry the markdown flag (and assistant messages) still render rich. Project panels accept only their own registered `chat_id`. The complete Project chat-id set is separate from the sidebar's visible or bounded summary, so a file-less, inactive, or currently off-list Project cannot have an early frame misclassified as Main. A `projects_changed` frame adds the new chat id synchronously before the asynchronous state refresh.

The Main composer exposes one-shot Swarm planning, the owner Low/Max context choice, file attachment, and Send. Swarm places a structural `force_plan` fact on the next ordinary message and disarms after that send; it is not inferred from keywords. Low/Max uses the dedicated owner endpoint. An automatically derived Low fit never masquerades as an owner-selected Low posture, and selecting Max may invoke the existing exact-route capability acknowledgement when provider metadata cannot prove the requested window. Project panels intentionally omit global Restart, Panic, evolution, consciousness, review, and budget controls because those belong to the one Ouroboros process, not to an individual Project thread.

Files can be staged with the paperclip, pasted images, or drag-and-drop over the chat. One message accepts at most ten files, 50 MB per file, and 100 MB in total. Staging is local and upload begins only immediately before Send. Attachment messages are refused while offline instead of entering the ordinary in-memory WebSocket queue: queued attachment references could outlive or orphan their temporary uploads. If a partial upload or final socket send fails, already uploaded temporary files are deleted best-effort and the original staged batch remains available for retry. Structured attachment metadata lets the gateway expose a native image to a vision-capable route and stage the complete file set through the shared artifact substrate.

Simple text messages may appear immediately as pending local bubbles and reconcile against their echoed `client_message_id`; their copy control copies raw message text and carries no timestamp. Delivered documents use a durable download URL where available and rebuild from history without persisting base64. Image files preview in cards, other files use a document glyph, audio MIME types render as players, and a card opens an explicit Open/Download/Close dialog with open-externally available for durable files; desktop downloads retain the host bridge while browsers use the blob-anchor fallback. Photos group by role and task into galleries with per-image open/download/copy actions; videos use the controller-owned player with scrub, time, speed, repeat, mute, and fullscreen controls. Photos and videos keep base64 in the live frame only, while supported media is stored under the canonical task artifact root with a content-addressed URL so history replay, reconnect, and older-message rebuilds restore the same bubble. Structured `links` rows render at most twelve independently revalidated HTTP(S) buttons, identically for live delivery and replay. The supervisor transport owns durable media copies so file-backed media, in-memory screenshots, ephemeral turns, and split child drives all converge on the canonical data root; unsupported or failed persistence remains an honest caption row and cannot finalize a task card. Presentation must not imply that a socket write is durable acceptance: routing and terminal truth come from host receipts, durable chat history, task records, and subsequent state reconciliation.

Direct in-process chat turns and ephemeral decision turns do not create supervisor queue records. Their active execution state is tracked in a thread-safe, process-local memory registry (`supervisor.active_activity.DirectActivityRegistry`) and exposed authoritatively via `active_direct_turns` in `GET /api/state` and typed `activity_id`/`client_message_id`/`phase`/`kind` fields on WebSocket `typing` frames. `GET /api/state` additionally exposes `active_chat_activities`: the same direct/ephemeral rows united with ROOT managed queue tasks projected as `kind="managed_task"` with `phase` `queued` (PENDING), `working` (RUNNING), or `finalizing` (RUNNING with an open post-task checkpoint on the durable result), so a chat instance created after the task started still hydrates its running state from the queue authority instead of depending on transient typing frames. Typing frames from RUNNING queue roots carry `kind="managed_task"` (subagent and legacy frames stay kind-less), and the snapshot's deletion authority covers exactly the kinds it enumerates — kind-less entries are still concluded only by their own final or summary frames. The client-side status reducer derives chat header status only from connection and authoritative activity state (`Reconnecting...` -> `Working...` (live card or admitted managed work) -> `Thinking...` -> `Sending...` -> `Queued...` -> `Online`); a terminal failure remains a factual task result and never creates a reasonless header `Attention`. A local `Sending...` submission is retired only by an authoritative typing frame, a snapshot turn with its `client_message_id`, a durable routing receipt (live `routing_ack` frame or its persisted annotation replayed from history), the durably recorded user row replayed on reconnect, its turn's conclusion, or eviction from the offline outbound queue (`outbound_dropped`, the submission can no longer reach the server) — never by the live user-row echo or a socket write.

Owner-message continuity is journal-backed: each locally sent owner row is kept (bounded, with its routing annotation) until a fetched history response returns the same `client_message_id`. A full feed rebuild replays exactly what the server returned and then re-renders the unconfirmed journal rows, so a stale history snapshot — fetched before the send was logged — cannot erase a message the owner just sent; the durable history row remains the retirement authority, and a dropped offline submission is evicted from the journal (its bubble is marked undelivered, a presentation that does not survive a later full rebuild). Task finalization is presented honestly: a root's early final answer carries the typed `task_phase="finalizing"` marker (the same fact history replay derives from the open post-task checkpoint, which also withholds `task_terminal_status` while open), the card holds a sticky `Finalizing…` phase through post-task frames, and `task_cost_finalized` is bookkeeping that never resolves a card. Settled `task_done` remains the live fast path; if a previously observed managed root disappears from a queue-authoritative snapshot whose request began after the root was observed, the existing state-refresh fan-out immediately removes activity/cancel authority and starts one single-flight durable task-detail read. Page-wide request generations prevent an older state response from undoing a newer projection, while the request-start timestamp separately protects live frames that arrived after the request began. Truthful terminal detail reuses Main's terminal-card reducer and existing history synchronization, with the same snapshot fanned to open Project panels; missing or nonterminal detail remains retryable on a later existing refresh. Two liveness invariants close the stuck-"Working..." class at its root. First, the history window is lineage-closed: a subagent FINAL chat row older than the window's progress recency floor (and not actively running) is emitted without its lineage fields — the same floor rule the progress stream already enforces — so replay can no longer mint an unfinishable parent card for a task the window cannot describe (`ouroboros/gateway/history.py`, single strip point inside the terminal-truth annotation pass). Second, liveness is reconciled over the CARD SET, not only the activity registry: after every snapshot hydration, each connected, unfinished, non-subagent root card whose id the snapshot does not vouch for is fed through the existing missing-managed-task seam and finished ONLY by proven durable terminal detail; a card with no durable result honestly keeps its state (owner decision). The header badge has exactly one writer — the status reducer — with the panel-boot "Online" seed as the sole documented exception; the old replay-time bypass that painted "Working..." straight from DOM state is deleted.

Task activity is collapsed into a live task card per root instead of flooding the transcript with individual tool and progress bubbles. `log_events.js` keeps the live task card and grouped task cards on one reducer across Chat and Dashboard Logs. Non-terminal LLM/tool/checkpoint failures remain inspectable timeline/detail facts but do not promote the whole card, even when they are the first retained line; only authoritative terminal task truth changes the terminal status. Unknown Chat event names do not acquire severity from keyword substrings, while Logs retains its diagnostic categorization. A failed child keeps a local `Failed` chip and the existing neutral nested container while its root continues independently. A card keeps a concise latest activity line (a plain-text projection of the headline the timeline renders as markdown: the renderer's marker inventory — including double-backtick code spans and headings of any depth — is stripped line by line, so a stray pipe row or list marker the timeline would show literally is dropped here too; inside a timeline line a markdown heading of any level renders as an inline subsection label at body size, never as a page title) and an expandable chronological timeline; server-truncated result or trace rows fetch their complete typed task record on demand into a bounded viewer. The compact card is therefore a navigation/presentation projection, not the full result authority. Owner actions remain with their domain controls, and urgent toast/unread behavior remains confined to explicit incident facts such as `task_incident`; neither is inferred from task severity.

Subagents render as distinct child cards keyed by their actual child task ids. Parent cards retain lineage references without duplicating the child's final answer into the parent timeline. Nested children are collapsed by default to keep deep trees scannable; a child's headline is its identity, `role · model`, with the short task id added only when a sibling of the same parent shares that role and model (Logs keep the full `role · model (id) — status` form), and its status is carried by the chip alone. Reviews are not task lineage: a reviewer run's explicit execution receipt belongs inside the real owning task card, while its harness or neutral API mark identifies the delivery channel and never creates a synthetic child card or proves execution by itself. The selected `subagent_id`/configured snapshot, requested route, effective engine route/model/account and terminal execution evidence remain separate facts: saved or dispatch intent must not be redrawn as proof of where the run actually settled. Legacy lane/executor fields remain readable on historical cards only.

The task card's `Reviews` section is a read-only presentation projection over independent domain authorities. Skill history, typed plan-review state, task-acceptance evidence, and repository-review records retain their own lifecycle, verdict, enforcement, and raw evidence. A row is admitted only with stable review identity, an exact real presentation-owner task, typed domain state, and exact subject/candidate binding where that domain requires it; incomplete or unbound review remains on its domain surface rather than being attached by chat, repository, timestamp, model, or current activity. This delivery admits task-bound Skill Review, plan review, and task acceptance; advisory and commit review remain on their existing domain surfaces until a bounded exact task/candidate projection exists. When review history is the only retained fact for an exact owner, Chat may render an inert owner anchor with `Reviews`, but that anchor has no task phase, typing indicator, or chat-wide liveness until canonical task activity/status arrives. The projection never synthesizes a task-wide review verdict and changes no review routing, status, attention, or enforcement policy.

Task-bound Skill references are folded into groups and attempts by the existing bounded Chat-history reader before final window quotas, while plan review and task acceptance hydrate through the existing task-detail seam. A successful canonical plan-state write appends one empty typed `review_reference` to the existing bounded progress-history rail and emits the same live invalidation, carrying only its revision/fingerprint; Chat uses it for a single-flight refresh of that seam, while task-result `plan_review_state` remains authority. Without charging human messages or visible telemetry, the read side independently keeps the newest distinct Skill owners and the latest Plan reference per owner within the requested progress window; every Skill group and attempt belonging to a selected owner stays intact. Truncation uses the existing `quota` cause so Load older expands both bounded overlays. Duplicate Skill lifecycle acknowledgements are typed `lifecycle_pointer` rows with no task id: they may enrich an already-present exact owner but never mint a card; if that owner card is absent from the duplicate caller's chat, the producer text remains one subdued non-task progress acknowledgement instead of disappearing. Live frames and reconnect rebuilds update the same projection without controlling disclosure; opening and closing Reviews belongs only to the user (see `docs/DESIGN.md`). `chat_id=0` remains the hidden Skill Review partition and is never a Main Chat surface. No review inbox, generic review endpoint, review ledger, or second state machine is introduced.

Card cost is sticky task-scope evidence. Only frames that carry task accounting status, finality, subtree, reservation, or unknown-cost fields may update it; an unrelated per-call `cost_usd` delta is never relabelled as the task total. Compact task cards render one amount — the accounted upper bound stated once, worded as a ceiling (`up to`) while the ledger is open and plain once final; reserved/unresolved components and unmetered-call counts stay on Costs, Logs and task detail — preferring the complete subtree projection when present and falling back to the task's own projection; diagnostic Logs, Costs, and task detail retain their accounting breakdowns. A running root's existing heartbeat may carry a non-final aggregate projection from the physical-attempt ledger, so the current amount advances without a second timer, endpoint, or client-side sum. The precedence is unavailable, then pending, then final, with newer evidence winning within one class. Costless rendering frames cannot erase a known value, and a transient unavailable read cannot overwrite a later honest measurement. Dashboard accounting and task-detail accounting continue to derive from the physical-attempt ledger rather than from the card. Compact review rows copy or sum no money. New Skill waves expose exact attempt/slot money only inside the existing lazy exact-job detail, joined from the same canonical ledger through `physical_attempt_v1`; legacy rows remain honestly unattributable and no second total is persisted.

A Cancel action appears only on unfinished, unconverted, pooled root cards carrying the supervisor's host-attested `cancelable=true`; card shape alone is insufficient because direct in-process chat turns can look identical but have no queue entry. Chat and Activity ask for explicit confirmation and call the typed task endpoint with `cascade:true`, meaning the root and its live descendant subtree. The endpoint returns only after teardown or a typed refusal. Natural completion wins a race, a missing live task is reconciled from the durable record, and a process that cannot be proven dead remains a visible refusal rather than being painted Cancelled.

A Main root card may be turned into a Project. Conversion creates or reuses the Project, gives it an owner-facing name without asking for an internal task id, binds the task and its canonical origin message, moves the live work onto the Project lane, and replaces the Main action with a calm Project pointer. Follow-up tasks already bound to a Project never receive another conversion button. The pointer is a Main ROOT-card affordance built by the one shared project chip (`ui_helpers.js::renderProjectChip`, also the converted card's identity chip): a card inside a Project panel and a nested subagent card never receive it, and clicking it opens the panel or does nothing when that panel is already open (opening toggles; a pointer must never close what it points at). Naming reuses an already coined model title when available and otherwise falls back through the current server naming path; the UI does not invent a second name authority.

The New Project dialog supports exactly one source: no folder for research/chat, a fresh managed genesis workspace, an attached existing folder, or a cloned Git URL. Attach uses a server-side directory browser so the flow also works in ordinary web and Docker environments. A non-git attached folder is rejected unless the owner explicitly requests the attach-snapshot initialization; it is never initialized silently. Clone failures distinguish missing credentials. Attach, clone, and genesis disclose that Project tasks receive read, write, and shell access in the chosen folder, and provenance remains a durable historical fact rather than being recomputed from current git state.

Deleting a Project is lifecycle work, not filesystem deletion. The server first fences new admission, then cancels and quiesces the Project task subtree, and finally tombstones the registry entry. The UI may acknowledge that deletion started and show the transitional state, but it does not claim completion early. Project id, canonical chat history, task bindings, memory, provenance, and the working folder remain preserved; deleting the row is not permission to erase the owner's repository or the agent's history.

Project unread state is the durable comparison `visible_revision > project_seen_revision`. Only owner-visible assistant/result content, delivered media, or a real incident advances the visible revision; ordinary progress and heartbeat traffic do not. Opening a panel does not clear unread by itself. The browser refreshes and paints the exact Project history revision, verifies that the panel is still visible and connected, then posts that revision as the acknowledgement. The server clamps it to current truth and max-merges it with the stored cursor, so a stale tab cannot move the cursor backwards or acknowledge future output.

A chat instance has an explicit resource lifecycle. `ws.on()` registers listeners in insertion-ordered sets and returns a disposer; event emission iterates a snapshot so adding or removing a listener during dispatch cannot corrupt neighboring delivery. `destroy()` marks the instance dead, disposes all socket subscriptions, removes window/document listeners, disconnects its observer, clears history, header, and task timers, drops bounded in-memory collections, and removes the DOM last. Late animation frames, history requests, preference reads, full-result fetches, and paint acknowledgements check the destroyed flag.

`app.js` normally keeps at most one live Project chat instance. Closing or switching stashes only its scroll intent and destroys the instance. The narrow exception is unsendable client state: staged `File` objects or an upload already in flight. Such an instance is hidden and marked pending instead of destroyed, is reused if the Project is reopened, and returns to the ordinary destroy policy after that work settles. Typed but unsent text survives separately in per-thread session storage. This prevents hidden Project rooms from accumulating listeners, repainting, or acknowledging unseen revisions without discarding data the server cannot reconstruct.

Chat and progress logs rotate as one timeline and archived segments remain durable. Interactive history uses bounded, archive-aware readers that expand only far enough to satisfy the requested thread's filtered quota; Project history cannot be satisfied by unrelated Main rows. Terminal annotation occurs after the emitted window is chosen, and display reads avoid materializing or rebasing artifacts. The task-event stream performs an archive-aware replay and then follows appended bytes, handling rotation and newly discovered children; its terminal event performs the one materializing task read needed to deliver artifact-bearing final truth. Children are discovered by a scandir name-diff over the main root's task-results directory. For a subagent, queue insertion and the first durable scheduled-result row form one queue-lock transition: if the result write fails, the still-pending row is removed before assignment and the admission becomes a typed rejection. A replay whose exact task id already has live or durable custody exits before write-surface provisioning and is rechecked under that lock, so it cannot append a second physical task, replace the accepted transition id, or publish duplicate progress. A follow tick therefore decodes only result files it has not successfully read yet instead of re-projecting the whole store; the disclosed residual is that the per-tick directory scan itself stays proportional to the size of the task-results directory. The reason is bounded UI latency without treating log rotation as conversation loss or mutating artifact state merely to render status.

The agent-facing `chat_history` reader uses the same live-plus-rotated timeline and may narrow it by exact provider, account, conversation, thread, actor, and inclusive date bounds before applying the established count/offset/text-search window. Presence provenance is therefore searchable as structured transport fact rather than only as flattened user prose.

### Files

Files is a full gateway-backed file manager, not a chat attachment picker. It provides directory navigation, breadcrumbs, current-folder filtering, image and sandboxed PDF preview, text preview/editing, an explicit binary/unsupported state, new file and directory creation, save, upload by drag-and-drop, download, open in the default OS application, copy, move, paste, and recursive delete. The desktop host bridge and web fallback share the same download contract.

Unsaved text is guarded on file selection, directory change, page navigation, and browser unload; Save is available only for a writable complete text read, while a truncated text preview remains read-only. The backend is the path authority. Every file route resolves its requested path under the configured root; a symlink whose target leaves that root may be listed as a symlink but cannot be read, written, deleted, downloaded, or traversed. UI path strings and disabled buttons are presentation only and never replace resolved-path confinement.

### Skills and Widgets

Skills has three views: installed skills, ClawHub, and OuroborosHub. Marketplace panes initialize lazily and refresh installed state when revisited. The installed view merges extension truth with the serialized lifecycle queue so install, update, review, dependency work, enable, disable, repair, uninstall, and failure remain visible while an operation is queued or running instead of snapping back to stale card state.

Installation, deterministic preflight, LLM review, owner grants, dependency readiness, extension loading, enablement, and execution are separate lifecycle facts. A fresh executable review does not imply that requested keys were granted or dependencies installed, and `enabled=true` does not override a blocked review or load error. Owner attestation, where eligible, skips only the expensive LLM review; deterministic preflight and the normal post-pass dependency/extension reconciliation still run. Repair creates a real constrained managed task visible in Chat. Hub publication uses the selected-skill preflight and ordinary managed task described under Skills and extensions; the passive Installed projection neither runs Betterleaks nor claims publication readiness.

Widgets is a separate page because extension UI is an execution surface, not catalogue metadata. It renders only UI tabs registered by reviewed live extensions and supports three modes. An extension-route iframe uses an empty sandbox capability set. A declarative widget is rendered by host-owned code from a validated schema. A reviewed module widget runs in an opaque-origin `srcdoc` iframe with `allow-scripts` but without `allow-same-origin`. Framed declarations may set a bounded `height` from 320 to 8,192 pixels; a module without `height` starts at the 320-pixel floor and reports its existing `#root` content height through the nonce-bound bridge, capped by an optional module-only `max_height` (default 8,192). For module auto-height, the injected host bootstrap owns vertical viewport overflow: below the finite ceiling it suppresses only `overflow-y`, keeping the child's inline-size basis stable while block size is applied; at the ceiling it releases that rule so excess content is vertically reachable. Horizontal document overflow remains author-controlled and reachable. Fixed-height modules receive no host overflow rule; legacy route iframes retain their existing scroll behavior and remain explicit-height-only because their opaque document cannot be measured by the parent. A framed resize protocol is correct only when measurement and application converge to a fixed point. Geometry keys are rejected for declarative renders, which remain content-driven.

Declarative widgets support forms and actions, status/data/text/code/markdown, tables, tabs, charts, polls, jobs, streams, subscriptions, progress, media, files, maps, calendars, kanban, and composition through `group`, `metric`, and `callout`. One recursive validator limits the tree to depth 8 and 256 nodes and reports the exact failing path. Nested interactive components use an explicit id or stable tree path as identity; `subscription.render` remains transitively passive so an incoming event cannot smuggle a new active control tree past validation. Text, attributes, links, media routes, and field values are escaped or constrained for their actual sink.

Module widgets receive a narrow parent-mediated fetch bridge. The iframe's policy denies ambient network and origin authority; the parent accepts requests only to the exact owning extension prefix under `/api/extensions/<skill>/...` and returns the response through a nonce-bound message exchange. The host-generated module bootstrap observes the content-sized `#root` edge with `ResizeObserver` plus a load measurement, integer-deduplicates and clamps resize messages, and receives a nonce-bound dispose message that rejects pending child fetch promises and disconnects the observer. Module source loading is also bounded and aborted when a mount becomes stale. This preserves useful route I/O without giving reviewed skill JavaScript the SPA's cookies, DOM, or broad API authority. Chart.js is bundled locally, and module/declarative rendering must not depend on a third-party CDN.

A mounted widget owns its timers, abort controllers, chart objects, event streams, polls, jobs, and WebSocket message handlers through one disposer. Leaving Widgets or forcing a refresh disposes the mounted work, removes framed iframes, aborts host bridge requests, and ignores late resize/fetch messages; an async mount that finishes after the page generation changed disposes itself instead of registering a hidden frame. A later visit may repaint the last good extension payload and restore bounded widget session state without leaving the hidden copy running. Job polling keeps its `job_id` across bounded retryable transport/408/429/5xx failures and request timeouts, while explicit terminal job states remain terminal. A missing or malformed status envelope fails immediately; a non-empty producer-specific in-progress status remains pending but is still bounded by `max_ticks`. The existing interval and tick bounds remain the scheduler rather than a second polling service. Poll and WebSocket writers use monotonic progress for one job so an older response cannot rewind a newer event. Transient refresh failure preserves the last good widgets instead of blanking the page. Card order is keyboard- and drag-adjustable owner UI state stored through `/api/ui/preferences`; it never rewrites the extension manifest or changes the review boundary.

### Dashboard

Dashboard groups Logs, Evolution, Costs, Updates, and Activity under one page. Sub-tabs activate their own loading and refresh policy instead of running every expensive reader continuously while hidden. Charting uses the bundled local Chart.js copy.

Logs merges live WebSocket log frames with bounded REST backfill from events, tools, progress, and supervisor logs. It orders the merged backfill chronologically, deduplicates overlap with the live stream and reconnect backfills, groups related task events into bounded cards, exposes the raw record on demand, and applies shared category/severity/review presentation from `log_events.js`. Clearing the visible panel does not delete the underlying logs.

Activity shows running and pending queue entries, background-consciousness state, and scheduled work. It offers mechanical controls only where that surface is authoritative: typed cascade cancellation for a live task, start/stop for background consciousness, and enable/disable/delete for owner-managed schedules. A schedule reconciled from a skill manifest is shown read-only as managed by that skill because a direct edit here would be overwritten by the skill lifecycle and would falsely appear durable.

Costs is a projection of the physical-attempt ledger. It distinguishes confirmed, reserved, unresolved upper-bound, unknown or unmetered usage, open rows, and finality; unavailable data renders as unavailable rather than `$0`. Breakdowns by model, key, model category, and task category remain views over the same ledger. Total budget can hot-apply, while the per-task value is labelled and treated as a hard cost cap over the whole root tree for the next task, not as the obsolete own-task soft warning. Increasing a cap does not automatically resume work that already finalized or paused.

Evolution shows current evolution and background-consciousness state, campaign objective/progress, queue/failure/budget information, and durable evolution history. Starting a campaign uses the shared input dialog: cancellation starts nothing, confirmed empty input selects the backend's default autonomous objective, and entered text becomes the objective. Light runtime mode disables self-modifying campaigns rather than presenting a control that the backend will refuse.

Updates separates passive status from explicit mutation. Opening the page reads cached/local update and git state without fetching the network; the passive read now also carries the last real check's `checked_at`, the configured `official_repo_url`, and a minimal `update_tx` projection so a re-opened panel can see an assisted resolution in progress instead of reading as ordinary state. The screen renders through one exported pure verdict (`updates.js::updateVerdict(status, phase)`, unit-pinned by `web/tests/update_verdict.test.js`): durable server state and the transient client phase produce a status line (dot + headline at primary ink), a meta hint, fact chips, and exactly ONE action button whose label is always the real next continuation — Check for updates / Checking… / Update to X.Y.Z / Updating… / Restarting… / Restart now (the degraded case where the automatic restart callback failed; it posts the ordinary `/restart` command). "Up to date" is claimed only over an actual check result (a fresh `check_ok` or the cache-carried timestamp), a failed check keeps its actionable label, and unknown backend warning classes surface verbatim rather than vanishing. The apply flow verifies the preflight through the shared `verifiedUpdatePlan` helper and confirms BEFORE applying, naming the path: a clean update restarts the server, while a conflicting one starts the reviewed assisted task (model spend disclosed) whose progress lands in chat. The served-SHA decision remains the only page-reload authority: changed code reloads, while a same-SHA reconnect preserves in-page state and makes Updates re-read durable update status. The boot-owned `pending_boot_smoke` and `applying_replace` phases keep the synthetic `restarting` state until a post-reconnect `update_status_ready` proves that boot finalization returned; its durable verdict then takes over even when the marker remains for another recovery attempt. Typed apply-failure facts (reason, blockers, rollback/smoke state, stash note, assisted budget floor) reach the owner instead of being reduced to one string. Recovery is a collapsed section holding the separately confirmed replace action, the "Save recovery point" control (moves only this installation's local `ouroboros-stable` fallback; it never publishes or changes the official QA feed — see §8), and ONE restore list where local tags label the commits they point at (`/api/git/log` tags carry their peeled `sha`). The unavailable, divergent, dirty, unsafe, failed-check, rollback, and restart-required states all stay visible — as states, never as extra buttons. The sidebar update pill (`update_status.js`) is a pointer to this panel, not a second apply surface. The surface is design-system-migrated (DESIGN.md §8); its rules live inside the `design-system:migrated` region of `web/style.css`.

### Settings and onboarding

Settings has Providers, Secrets, Models, Agents, Behavior, Advanced, and About tabs, read as a sequence from credentials to runtime detail. Providers configures remote providers, custom compatible endpoints, local runtime entry points, and the optional non-loopback network gate. Secrets centralizes known provider/integration secrets, skill-requested keys, and owner-defined custom keys without returning stored secret values. Models contains ordinary model slots and effort lanes. Agents contains everything about the agents Ouroboros delegates to: one service banner, the subscription accounts, the review lanes, and delegation including the mutative permission, the per-root and depth limits, and the subagent path roots. Behavior contains owner choices such as context, safety-supervisor coverage, task acceptance, self-evolution, and prompt-cache posture. Advanced contains process, timeout, local-model, integration, source-control, and cleanup controls; worker count stays there because it is process capacity rather than an agent setting. About reports the current application/runtime identity.

Each provider card has one compact **Test** action backed by `POST /api/providers/test`. Its request is exactly `{provider_id, overrides?}` and its response is exactly `{ok, error?}`. Overrides are request-local: an omitted field reads the saved value, an explicitly edited empty field stays empty (and, for the compatible card, suppresses the corresponding legacy OpenAI fallback), while a field the owner did not edit preserves that legacy behavior. Draft credentials and endpoints never mutate Settings, process environment, or `LLMClient` caches. Model selection first reuses a configured route for that provider, then its maintained main default; only the generic OpenAI-compatible route performs bounded catalogue discovery when neither exists. A configured test sends the literal `Reply OK` as one physically accounted model request capped by `llm_probe.PROVIDER_TEST_MAX_TOKENS=16`, with one physical-attempt limit and no normal-chat retry, provider fallback, tools, reasoning, web, cache, response-format, or capability-learning path. The card renders only `Testing…`, `Works`, or `Not ready` with one controlled short reason; its tooltip says that the request may incur provider charges.

Desktop onboarding and the blocking web overlay are the same served `/onboarding` page — same provider, agents, model, review/runtime, budget, and summary steps, same backend normalization; context mode remains a separate owner setting. Startup readiness is structural: a recognized non-empty remote configuration or a task-capable local-routing flag is sufficient. An agent subscription strengthens Ouroboros but never satisfies that gate on its own. Credential validity, entitlement, model availability, and local-process health remain runtime status, not onboarding admission. Linux browser fallback therefore does not maintain a second setup flow. Every host completes through the single `POST /api/onboarding/complete` transaction described in the Startup / Onboarding Flow section, so a completed onboarding is all-or-nothing and install-time agent defaults are part of the same save.

Accounts is the owner-facing projection of Ouroboros's owned Claudexor daemon. The browser never receives its control token or interprets credentials. Status combines daemon/runtime readiness, login-capable harness discovery, credential profiles, honest vendor-live versus local-session verification, fresh quota windows, and optional model discovery. One `/v2/quota` envelope supplies both windows and typed per-subject absences to the status projection (`quota`, `quota_absences`), so a missing usage reading remains separate from login truth and route health never mixes two quota reads. API-key-only adapters do not acquire fake Login buttons merely because they appear in a broader execution catalogue.

Claudexor owns snapshot-versus-absence coverage at its response boundary. Ouroboros keeps only fresh snapshots eligible for percentages or exhaustion and reads typed absences by exact subject; a malformed optional absence list becomes empty rather than breaking the whole status response. Already-redacted absence detail is displayed as text but never selects semantics, and legacy subject aliases apply only to snapshots, never to credential absences.

Accounts are grouped into one card per agent family. Each card header carries the family name, an aggregate status that counts the accounts rotation can actually use — signed-in AND enabled, with all-disabled its own state — a fail-safe "Next up" badge naming who an unpinned run would take (read through the store's one dual-wire reader: the unified engine's `accountPools` first, the legacy per-harness `next_up` second; unknown kinds render as unknown, never a crash), and that card's own add action. Rows are ONE type on both engine generations: on a UNIFIED engine (the server-stamped `unified_accounts` feature fact) every account — migrated default logins included, under the reserved `<harness>-default` registry ids — is a named row carrying the same name, Enabled toggle (the engine's own per-profile PATCH) and Remove; on a LEGACY engine the native pseudo-row keeps the same two-line layout, is named by the identity the daemon observed (or "Default account"), and only its ACTIONS differ — no Remove and no toggle, because that engine has no route for either and a dead button would claim an effect this process cannot have. A row's first line is the account and its status; `verification=not_run` is neutral unknown/not verified while `failed` remains an error. When the engine explicitly reports `availability=unknown` with `verification=not_run`, the row says "Login status unknown" and its action re-runs the shared Refresh instead of starting a new sign-in; an auth probe failure is never treated as proof of logout, and the action remains available if the next read needs a real login. The second line is muted metadata in human words, including humanized quota (a migrated default row may inherit its pre-migration legacy-keyed window until the next refresh re-keys it; the exact subject always wins), a disabled row's own exclusion from rotation, and the humanized time of the last verification rather than a raw instant. The row also appends only the exact subject's typed quota absence: refresh/rate-limit/pacing gaps stay neutral and never offer login, genuine `not_logged_in`/`auth_revoked` remains distinct in words, unknown future reasons degrade to neutral usage-unavailable copy, and prose detail never selects semantics. Two migration-window residuals are accepted rather than patched: the legacy ''-keyed quota alias is granted only to the literal reserved `<harness>-default` registry id — a collision-suffixed migrated row (e.g. `codex-default-2`) does not inherit the legacy window until the next quota refresh re-keys it, and a pre-existing unrelated row that happens to bear the reserved name could borrow it (exact-keyed readings always win) — and pinned route health applies no legacy alias at all, so immediately after migration a pinned default may read UNKNOWN and fail open to the engine's own authoritative typed refusal (consistent with the strict-pin decision D-U6). Removing a named account is a request to the daemon's own credential-profile contract, and the complete deletion receipt is preserved: a refusal remains a refusal, while the exact vendor-owned / left-unchanged / OS-user disposition becomes a successful retained-credential warning rather than a false sign-out claim. A single service banner at the top of the tab explains a daemon or runtime problem once, per facet, instead of decorating rows with unavailability claims. Per-facet independence — a refused quota read leaving the catalogue and account facets authoritative — is real on both sides of the wire: `claudexor_accounts.py` fans the catalog, account and quota reads out independently, classifies each on its own, and stamps the result into the payload's `reads` block, so one refusal no longer collapses its siblings into the global `unreachable` verdict; the client's shared status store reads that stamp through its one facet reader. Only a legacy payload without the stamp is still read coarsely — a global refusal makes every facet indeterminate together rather than one of them being blamed.

Connect is link-first and harness-agnostic. A typed disclosure renders the sign-in URL and any one-time code; flows that may need a pasted callback code keep that optional field visible while active because the browser callback may complete without it. Current engines publish optional-without-default `setupLogin` on each exact harness row: `{mode: in_app}` maps an omitted browser request to an omitted setup transport, `{mode: external_terminal}` maps it to `client_pty`, and malformed present data is a capability gap. Explicit null is ambiguous while the vendor CLI is absent, so support is delegated to the exact pinned engine's typed setup/profile admission and the response is stamped `setup_job_admission`; an omitted transport stays omitted and explicit `client_pty` stays exact. An explicit `client_pty` recovery request remains explicit even when the normal mode is `in_app`; Codex pairs that transport with `browser_redirect`, and non-Codex requests never acquire `loginFlow`. Only genuine key absence on a legacy engine consults the older global operation signal, and the create response discloses that compatibility source instead of presenting it as per-harness host evidence. Typed `credential_profile_required` plus `add_named_account` selects the name-the-account face; unrelated 400/409 and prose never do. A typed duplicate profile is idempotent directly; a 3.6.0 generic 409 (`internal_error`, or the transport's `http_409` fallback) is idempotent only after the exact harness/profile row is read back. Typed pre-job refusals prove setup custody absent/released, while unmarked discovery and transport failures remain unknown. The exact `terminal_transport_unavailable`, `terminal_transport_unsupported`, `terminal_transport_probe_failed`, or `terminal_transport_failed` code plus its required action (pre-job) or durable `job.nativeCommand.errorCode` (post-create) offers an explicit external-terminal continuation through the same release guard; no prose selects it. Unsupported/unavailable does not repeat a retry the engine did not offer, while probe failure may offer both actions. If the exact pinned engine answers the synchronous first create with its structural pre-command missing-vendor-binary terminal job, the same owner action also consents to one hidden local install through the exact managed Claudexor CLI and one retry. No message text, harness name, PATH executable, system npm, later poll, or command-bearing `not_supported` job can trigger it. The installer is a hard-timeout new process group, stderr is discarded, and stdout is capped and parsed as exactly one strict JSON success object. Success requires Claudexor's post-install proof: an absolute non-empty `installedBinary` plus a non-empty `installedVersion` bounded to 256 characters; process exit zero without that proof is refused. A second login refusal is returned rather than looped. A newly created explicit `client_pty` job exposes its labelled POSIX-shell or PowerShell attach command immediately in both full and compact cards; ordinary delayed/legacy attach remains collapsed under Advanced in the full card. There is no embedded terminal login surface or cmd formatter. Terminal job state and the current account row are reconciled so a stale verification read during login cannot claim failure after the account actually connected.

Account status refresh runs immediately and on visible page/tab activation but does not make every hidden page pay for daemon round-trips. Entering Agents is also an explicit owner action: after the fresh read, an already-provisioned `stale` home is restarted through the existing wake endpoint, while `not_provisioned`, foreign-owned, and repair states remain behind Connect. Background polling stays read-only and never wakes the daemon. Job polling uses one request at a time, begins at the healthy cadence, backs off to a bounded delay on consecutive failures, and after ten consecutive failures stops with an honest unconfirmed state: lost contact does not prove either failure or settlement. One transition lock covers Start, Retry, and Dismiss. A new login begins only once release of the prior job is proven (`loginReleaseProven`): a terminal snapshot whose termination reason is not `termination_unconfirmed`, a reconciliation that found the setup empty, or a job proven absent by 404/410. A terminal `termination_unconfirmed` snapshot keeps the job fenced, and a successful (2xx) cancel response alone is not proof of release; a network or server failure retains the card and job id because dropping it could orphan a still-live server job. After each await the handler rechecks whether polling settled the job, so a stale cancel continuation cannot overwrite a terminal result.

Review lanes edits one structured reviewer configuration. Each triad, scope, or optional advisory row picks its reviewer from ONE flat select: the Available-subagents roster rows lead as references (facts-first labels), then the inline channels — API delivery or a coding-agent session — followed by its model, optional credential profile, and effort. API models use free text with catalogue suggestions; agent-session models come from the selected harness. Saved choices that disappear from discovery remain visible as unavailable rather than silently changing to the first option. Capability labels configure nothing; the server-returned limits and last effective execution disclose what a saved row actually ran as, including capability deltas. An unloaded or unreachable view authors no replacement. A successfully loaded empty triad/scope is sent as shown so backend validation returns the real error instead of the browser falsely reporting that nothing changed. A row pinned to an account discovery no longer lists keeps its pin and is disclosed once, above the rows, as unavailable rather than silently rerouted — and only on the word of a facet that was actually read: an account pin answers to the `accounts` facet and a model to `catalog`, and while that facet is unread or failed the row says the pin was not checked instead of "not in discovery". The all-delegated disclosure is neutral routing information: commit, scope, plan, advisory, and skill review follow their configured rows and wait for subscription capacity rather than falling back to API spend; task acceptance alone retains the owner-approved API/default projection.

**Available subagents** is the single task-actor editor. Its list-level Enabled flag and at most ten stable rows are the saved `OUROBOROS_SUBAGENTS` intent. The owner sees numbered cards and authors one prose field, Description (`recommended_use`), alongside the structured API-model or Agent-session route, optional effort and optional session account pin. Each card is compact (`docs/DESIGN.md` §6 row anatomy): a head with the ordinal, the harness mark, one status dot with two short words — the intent axis (Saved / Draft / Generated) and the availability axis (Available / Not checked / Unavailable / No account / Limit reached, or Checked at start for an API-model route), the dot's tone the worse of the two axes and the full sentences in its title — and Duplicate/Remove docked right; a one-line Description that grows with its text up to four lines (`field-sizing`; a shell without it keeps the one-line field and manual resize); the route controls; and one meta line. `subagent_status_primitives.sessionRouteVerdict` decides a session route's label, tone and sentence together, and `rowStatus` there composes the card's two axes from it. Identity is the stable internal `subagent_id` plus the route facts derived live from the row; the legacy display `name` is retired — parse accepts and drops it, nothing mints or fabricates one — and a changing visual ordinal never becomes durable identity. The editor shares only neutral route/model/account/status primitives with Review lanes; reviewer quorum, roles and schema stay separate. Empty session pin means Claudexor's compatible-account rotation. A saved route, model or pin that disappears from discovery remains visible and editable, labeled unavailable or not checked according to the exact status facet rather than silently rewritten. Add (in the section's toolbar, its group header) appends the entry and reveals it through the shared `ui_helpers.revealNewRow`, as Duplicate does for its copy; Review lanes and MCP servers reveal their added rows the same way, and each Review lanes group carries its Add in its own head. A fresh entry is not an error: its meta line carries a neutral hint until the owner tries to save — Settings' Save and the wizard's Finish report that attempt through `noteSaveAttempt` while `validate()` stays pure — after which the section-level line summarises and the offending card is tinted and names its error ("Subagent 4 …"), reconciled in place by one painter so a fix typed into a field cannot clear one without the other — including the footer message Save wrote for the roster, which the roster owns (`setStatus` owner) and clears when its judged rows come clean, never a newer message another surface wrote. In the wizard the Finish error shows on the summary step and the card is already tinted when the owner steps back to Agents.

Saved intent and live evidence are separate axes. The row status distinguishes saved/generated/draft intent from current availability and may show the last actual requested→effective run evidence. A status/catalog/accounts failure annotates the rows but never erases them. Settings GET may offer an unsaved migration/default candidate when no canonical value exists; the editor materializes it only on Save. A late bounded status or preview response may replace a still-clean generated baseline, never an owner-edited draft, and unchanged repaint preserves focus/caret. Generic Settings save strictly validates and canonicalizes the already-materialized value before the existing serialized off-event-loop owner transaction. A running task retains its immutable start snapshot and the save response says changes apply from the next task.

Mutative subagents use Off, Auto, and On. An explicit Off or On applies to every acting surface. Auto delegates the default to runtime mode: Advanced and Pro allow worktree, external-workspace, and genesis writers; Light allows only children that build outside the Ouroboros runtime (external workspace and genesis) and keeps a self-worktree child off. Read-only children remain available. The setting is owner-controlled, applies from the next task, and is independent of whether the execution route is API or a coding-agent subscription.

Prompt Cache TTL is one global owner choice: provider default, five minutes, or one hour. It applies to every lane rather than letting task, review, and safety builders drift into conflicting cache horizons. The provider-send finalizer applies the choice only to existing cache markers on compatible Anthropic-family payloads; the UI does not promise cache behavior on providers that manage it implicitly. The shipped one-hour posture favors reuse across long waits and review cycles; choosing another value takes effect from the next task.

Settings save classifies effects rather than claiming that every value became live at once. Total budget, tool timeout (the outer per-call cap reads settings.json live), GitHub metadata, update channel, and the MCP configuration (hot-reconfigured by the save itself; a failed reconfigure is surfaced as a save warning) hot-apply; the retained soft/hard timeout keys are accepted only as deprecated audited no-ops and the save reports them as retired instead of claiming any effect. Ordinary models, credentials, efforts, reviewer/subagent configuration, provider base-URL and region parameters (resolved per call from the task-start environment), per-task cost cap, safety posture, and prompt-cache TTL apply from the next task; a running task keeps its starting snapshot and the response says so. Worker count, bind host, host-service port, local-model runtime, the skills repo path (pooled workers load the extension registry once at spawn), and background-consciousness timing require restart; a restart-required save offers a Restart now action over the existing owner `/restart` command. A failed task-start settings reload is disclosed as a persisted, chat-visible `task_start_settings_reload_failed` event instead of silently keeping the previous configuration. Runtime mode and context mode keep dedicated owner paths because generic settings writes must not silently lower authority or cognitive/review posture. Every in-process owner-settings writer holds the shared `settings_document_mutation()` lock across its read/merge/write transaction; the file lock remains a write precondition rather than a substitute for that document transaction. Async handlers await request-body parsing only, then move synchronous selection, network, lock, and write work to `asyncio.to_thread`, keeping the event loop responsive. Loading reviewer and delegation settings waits no more than the `boundedStatusRefresh` two-second foreground beat for Claudexor; a cold refresh continues and repaints the bound surfaces when it lands, while a warm result is still adopted immediately. Backend failure and browser transport failure remain distinct; absence of a successful status read is never evidence that a runtime is healthy.

### Visual verification policy

A visible change is exercised in at least one relevant real consumer flow and the rendered result is inspected with vision. A saved screenshot alone is not verification. Mobile, WebKit, additional browsers, and special viewports are selected from the actual interaction risk rather than imposed as a universal matrix.

No visual-QA runner, endpoint, ledger, or mandatory device matrix is introduced by this policy.
## 4. Server API Endpoints

If `OUROBOROS_NETWORK_PASSWORD` is configured, non-loopback HTTP and WebSocket access requires authentication; loopback clients bypass the gate, and `/api/health` plus the middleware-owned login/logout paths remain reachable. Browser sessions use a server-keyed, expiring HttpOnly HMAC cookie; `Secure` is set only under TLS so a plain-HTTP LAN session does not enter a login loop. An unauthenticated WebSocket is closed with code 4401. With no configured password, non-loopback access remains open by explicit operator choice.

The executable browser/CLI route SSOT is `ouroboros/gateway/router.py`; file-browser routes are contributed by `gateway/files.py::file_browser_routes()`. `gateway/contracts.py` is the frozen descriptive envelope and endpoint index mirrored by `web/modules/api_types.js` and parity tests; its `TypedDict` classes do not perform runtime JSON validation. The loopback Host Service is a separate token-authenticated app assembled by `gateway/host_service.py::create_host_service_app`, not another public owner API.

Every `/api/files/*` operation resolves its requested path and refuses the operation when that resolution leaves the configured file root. In-root symlinks remain usable; out-of-root symlinks may be listed with `is_symlink: true` but cannot be read, written, downloaded, deleted, or traversed. The backend check is authoritative regardless of browser path presentation.
| Method | Path | Handler |
|---|---|---|
| GET | `/` | `server.index_page` |
| GET | `/api/health` | `gateway.state.api_health` |
| GET | `/api/state` | `gateway.state.api_state` |
| GET | `/api/extensions` | `gateway.extensions.api_extensions_index` (unique rows additionally carry `content_hash`, `published` (validated receipt object or null), `published_malformed`; identity-collision rows carry `identity_collision: true` and omit the receipt fields) |
| POST | `/api/skills/{skill}/publish-preflight` | `gateway.skill_publish.api_skill_publish_preflight` |
| GET | `/api/extensions/{skill}/manifest` | `gateway.extensions.api_extension_manifest` |
| GET | `/api/extensions/{skill}/module/{entry}` | `gateway.extensions.api_extension_module` |
| GET | `/api/extensions/{skill}/settings_section` | `gateway.extensions.api_extension_settings_section` |
| ANY | `/api/extensions/{skill}/{rest:path}` | `gateway.extensions.api_extension_dispatch` |
| GET | `/api/skills/daemons` | `gateway.extensions.api_skill_daemons` |
| POST | `/api/skills/{skill}/toggle` | `gateway.extensions.api_skill_toggle` |
| POST | `/api/skills/{skill}/delete` | `gateway.extensions.api_skill_delete` |
| GET | `/api/skills/lifecycle-queue` | `gateway.extensions.api_skill_lifecycle_queue` |
| POST | `/api/skills/{skill}/review` | `gateway.extensions.api_skill_review` |
| GET | `/api/skills/{skill}/review-history/{job_id}` | `gateway.extensions.api_skill_review_history_detail` (bounded read-only lazy detail for a `skill_review` chat reference row: searches a fixed tail window of `state/skills/<skill>/review_history.jsonl` for the exact `job_id` and returns the server-rendered normalized block; raw reviewer text and authority stay in the history file — degraded reviewers are disclosed by stable slot/legacy-actor identity plus status. A marked new wave also projects exact slot/attempt usage lazily from the canonical physical-attempt ledger. A missing job is 404, while a record outside the bounded window or obscured by an unreadable/incomplete tail is honestly unavailable rather than triggering a full replay.) |
| POST | `/api/owner/skills/{skill}/attest-review` | `gateway.extensions.api_owner_skill_attest_review` (C1, v6.39; v6.43 official-hub extension: OWNER-ONLY — skip the expensive LLM review for the owner's own external/self-authored skill or for a freshly hash-verified official OuroborosHub payload; the deterministic preflight floor still runs, 409 on failure; routes through `run_skill_review_lifecycle` for the post-pass deps/extension reconcile) |
| POST | `/api/skills/{skill}/grants` | `gateway.extensions.api_skill_grants` |
| POST | `/api/skills/{skill}/reconcile` | `gateway.extensions.api_skill_reconcile` |
| GET | `/api/marketplace/clawhub/search` | `gateway.marketplace.api_marketplace_search` |
| GET | `/api/marketplace/clawhub/installed` | `gateway.marketplace.api_marketplace_installed` |
| GET | `/api/marketplace/clawhub/info/{slug:path}` | `gateway.marketplace.api_marketplace_info` |
| GET | `/api/marketplace/clawhub/preview/{slug:path}` | `gateway.marketplace.api_marketplace_preview` |
| POST | `/api/marketplace/clawhub/install` | `gateway.marketplace.api_marketplace_install` |
| POST | `/api/marketplace/clawhub/update/{name}` | `gateway.marketplace.api_marketplace_update` |
| POST | `/api/marketplace/clawhub/uninstall/{name}` | `gateway.marketplace.api_marketplace_uninstall` |
| GET | `/api/marketplace/ouroboroshub/catalog` | `gateway.marketplace.api_ouroboroshub_catalog` |
| GET | `/api/marketplace/ouroboroshub/installed` | `gateway.marketplace.api_ouroboroshub_installed` |
| GET | `/api/marketplace/ouroboroshub/preview/{slug:path}` | `gateway.marketplace.api_ouroboroshub_preview` |
| POST | `/api/marketplace/ouroboroshub/install` | `gateway.marketplace.api_ouroboroshub_install` (also the adopt transport: `{adopt: true, expected_content_hash}` replaces an external same-name occupant with the sha256-verified catalog payload — gateway skips only its own pre-lifecycle identity precheck, the installer's stays; adopt forces `auto_review`, conflicts with `overwrite`, typed 400/409/502 codes ride the lifecycle payload) |
| POST | `/api/marketplace/ouroboroshub/update/{name}` | `gateway.marketplace.api_ouroboroshub_update` |
| POST | `/api/marketplace/ouroboroshub/uninstall/{name}` | `gateway.marketplace.api_ouroboroshub_uninstall` |
| GET | `/api/files/list` | `gateway.files.api_files_list` |
| GET | `/api/files/read` | `gateway.files.api_files_read` |
| GET | `/api/files/content` | `gateway.files.api_files_content` |
| GET | `/api/files/download` | `gateway.files.api_files_download` |
| POST | `/api/files/upload` | `gateway.files.api_files_upload` |
| POST | `/api/files/mkdir` | `gateway.files.api_files_mkdir` |
| POST | `/api/files/write` | `gateway.files.api_files_write` |
| POST | `/api/files/delete` | `gateway.files.api_files_delete` |
| POST | `/api/files/transfer` | `gateway.files.api_files_transfer` |
| GET | `/onboarding` | `gateway.onboarding_host.onboarding_page` |
| GET | `/api/onboarding` | `gateway.settings.api_onboarding` |
| POST | `/api/onboarding/complete` | `gateway.onboarding.api_onboarding_complete` |
| POST | `/api/onboarding/subagents/preview` | `gateway.onboarding.api_onboarding_subagents_preview` |
| GET | `/api/settings` | `gateway.settings.api_settings_get` |
| POST | `/api/settings` | `gateway.settings.api_settings_post` |
| GET | `/api/reviewer-slots` | `gateway.settings.api_reviewer_slots` |
| GET | `/api/claudexor/status` | `gateway.claudexor_accounts.api_claudexor_status` |
| POST | `/api/claudexor/quota/refresh` | `gateway.claudexor_quota.api_claudexor_quota_refresh` |
| POST | `/api/claudexor/wake` | `gateway.claudexor_accounts.api_claudexor_wake` |
| POST | `/api/claudexor/login` | `gateway.claudexor_accounts.api_claudexor_login` |
| GET | `/api/claudexor/login/{job_id}` | `gateway.claudexor_accounts.api_claudexor_login_job` |
| DELETE | `/api/claudexor/login/{job_id}` | `gateway.claudexor_accounts.api_claudexor_login_job` |
| POST | `/api/claudexor/login/{job_id}/input` | `gateway.claudexor_accounts.api_claudexor_login_job` |
| POST | `/api/claudexor/login/{job_id}/reconcile` | `gateway.claudexor_accounts.api_claudexor_login_job_reconcile` |
| DELETE | `/api/claudexor/credential-profiles/{harness}/{profile_id}` | `gateway.claudexor_accounts.api_claudexor_credential_profile` |
| PATCH | `/api/claudexor/credential-profiles/{harness}/{profile_id}` | `gateway.claudexor_accounts.api_claudexor_credential_profile` |
| POST | `/api/owner/runtime-mode` | `gateway.settings.api_owner_runtime_mode` |
| POST | `/api/owner/auto-grant` | `gateway.settings.api_owner_auto_grant` |
| POST | `/api/owner/context-mode` | `gateway.settings.api_owner_context_mode` |
| POST | `/api/owner/scope-review-floor` | `gateway.settings.api_owner_scope_review_floor` (DEPRECATED and ENFORCEMENT-INERT since v6.80.0; still mounted, still stores and audits — see below) |
| POST | `/api/owner/safety-mode` | `gateway.settings.api_owner_safety_mode` |
| POST | `/api/owner/skills/{skill}/presence-runtime` | `gateway.presence_settings.api_owner_skill_presence_runtime` |
| POST | `/api/owner/capability-ack` | `gateway.settings.api_acknowledge_capability` |
| GET | `/api/ui/preferences` | `gateway.ui_preferences.api_ui_preferences_get` |
| POST | `/api/ui/preferences` | `gateway.ui_preferences.api_ui_preferences_post` |
| GET | `/api/model-catalog` | `gateway.models.api_model_catalog` |
| POST | `/api/openai-compatible/models` | `gateway.models.api_openai_compatible_models` |
| POST | `/api/providers/test` | `gateway.models.api_provider_test` |
| POST | `/api/tasks` | `gateway.tasks.api_tasks_create` |
| GET | `/api/tasks` | `gateway.tasks.api_tasks_list` |
| GET | `/api/tasks/{task_id}` | `gateway.tasks.api_task_get` |
| GET | `/api/tasks/{task_id}/events` | `gateway.tasks.api_task_events` |
| GET | `/api/tasks/{task_id}/artifacts/{name}` | `gateway.tasks.api_task_artifact` |
| POST | `/api/tasks/{task_id}/cancel` | `gateway.tasks.api_task_cancel` |
| POST | `/api/tasks/{task_id}/hurry` | `gateway.tasks.api_task_hurry` |
| POST | `/api/tasks/{task_id}/resume` | `gateway.tasks.api_task_resume` |
| POST | `/api/decisions` | `gateway.tasks.api_decision_answer` |
| GET | `/api/schedules` | `gateway.schedules.api_schedules_list` |
| POST | `/api/schedules` | `gateway.schedules.api_schedules_upsert` |
| DELETE | `/api/schedules/{schedule_id}` | `gateway.schedules.api_schedules_delete` |
| POST | `/api/command` | `gateway.control.api_command` |
| POST | `/api/reset` | `gateway.control.api_reset` |
| GET | `/api/git/log` | `gateway.control.api_git_log` |
| POST | `/api/git/rollback` | `gateway.control.api_git_rollback` |
| POST | `/api/git/promote` | `gateway.control.api_git_promote` |
| GET | `/api/update/status` | `gateway.control.api_update_status` |
| POST | `/api/update/check` | `gateway.control.api_update_check` |
| POST | `/api/update/preflight` | `gateway.control.api_update_preflight` |
| POST | `/api/update/apply` | `gateway.control.api_update_apply` |
| GET | `/api/cost-breakdown` | `gateway.history.make_cost_breakdown_endpoint` |
| GET | `/api/evolution-data` | `gateway.control.api_evolution_data` |
| GET | `/api/projects` | `gateway.projects.api_projects_list` |
| POST | `/api/projects` | `gateway.projects.api_projects_create` |
| POST | `/api/projects/from-task` | `gateway.projects.api_project_from_task` |
| POST | `/api/projects/{project_id}/update` | `gateway.projects.api_project_update` |
| POST | `/api/projects/{project_id}/delete` | `gateway.projects.api_project_delete` |
| GET | `/api/fs/dirs` | `gateway.projects.api_fs_dirs` |
| GET | `/api/chat/history` | `gateway.history.make_chat_history_endpoint` |
| GET | `/api/logs/{name}` | `gateway.logs.api_logs_tail` |
| POST | `/api/chat/upload` | `gateway.files.api_chat_upload` |
| DELETE | `/api/chat/upload` | `gateway.files.api_chat_upload_delete` |
| POST | `/api/local-model/start` | `gateway.models.api_local_model_start` |
| POST | `/api/local-model/stop` | `gateway.models.api_local_model_stop` |
| GET | `/api/local-model/status` | `gateway.models.api_local_model_status` |
| POST | `/api/local-model/test` | `gateway.models.api_local_model_test` |
| POST | `/api/local-model/install-runtime` | `gateway.models.api_local_model_install_runtime` |
| GET | `/api/mcp/status` | `gateway.mcp.api_mcp_status` |
| POST | `/api/mcp/refresh` | `gateway.mcp.api_mcp_refresh` |
| POST | `/api/mcp/test` | `gateway.mcp.api_mcp_test` |
| WS | `/ws` | `gateway.ws.ws_endpoint` |
| STATIC | `/static/*` | `server.NoCacheStaticFiles` |
| GET | `127.0.0.1:${OUROBOROS_HOST_SERVICE_PORT:-8767}/identity` | `gateway.host_service._api_identity` |
| GET | `127.0.0.1:${OUROBOROS_HOST_SERVICE_PORT:-8767}/tools/schemas` | `gateway.host_service._api_tool_schemas` |
| POST | `127.0.0.1:${OUROBOROS_HOST_SERVICE_PORT:-8767}/chat/allocate-internal` | `gateway.host_service._api_allocate_internal` |
| POST | `127.0.0.1:${OUROBOROS_HOST_SERVICE_PORT:-8767}/chat/inject` | `gateway.host_service._api_chat_inject` |
| POST | `127.0.0.1:${OUROBOROS_HOST_SERVICE_PORT:-8767}/presence/turn` | `gateway.host_service._api_presence_turn` |
| GET | `127.0.0.1:${OUROBOROS_HOST_SERVICE_PORT:-8767}/presence/work/{work_ref}` | `gateway.host_service._api_presence_work` |
| POST | `127.0.0.1:${OUROBOROS_HOST_SERVICE_PORT:-8767}/ui/ws-message` | `gateway.host_service._api_ws_message` |
| WS | `127.0.0.1:${OUROBOROS_HOST_SERVICE_PORT:-8767}/events` | `gateway.host_service._ws_events` |

Rationale: `server.py` should own process startup/lifespan/static mounting, while `gateway/*` owns browser-facing HTTP/WS contracts. This keeps UI and runtime coupling explicit and testable.

### WebSocket protocol

`/ws` is the live browser delivery channel, not a durable state owner. Queue, task, Project, review, skill, settings, cost, and update modules persist their own truth; REST/history endpoints reconstruct that truth after reload or disconnection. `gateway/contracts.py` describes the frozen envelope shapes and message-type index for Python/JavaScript parity, but it is not a runtime parser. `gateway/ws.py` performs the actual transport checks: incoming text must decode to a JSON object, extension types must parse as an owned namespace, and built-in `chat` or `command` frames must carry a non-empty payload before they enter the message bridge.

When the optional network password is configured, the surrounding authentication middleware admits loopback clients directly, accepts a valid expiring session or request credential for non-loopback clients, and closes an unauthenticated WebSocket with code 4401 before `ws_endpoint` accepts it. With no password the socket follows the operator's explicitly open network posture. The public socket never receives the Host Service token or the owned Claudexor daemon token.

The browser constructs one socket for the whole SPA. Feature modules subscribe before connection; the initial complete Project chat-id set is fetched before the first open so an early Project frame cannot be mistaken for Main traffic. `ws.on(type, listener)` stores listeners in insertion-ordered sets and returns a disposer. Emission uses a listener snapshot: a listener added during dispatch does not receive the current frame, and disposing one listener cannot skip its neighbor. Every decoded frame first reaches the generic `message` event and then its type-specific event, which lets Widgets consume reviewed namespaced events without duplicating the socket.

A browser `chat` frame contains the owner text and may add `sender_session_id`, `client_message_id`, `force_plan`, uploaded attachment references, `chat_id`, `project_id`, and `client_surface` — raw sending-surface observables (pywebview bridge presence, ua, viewport, matchMedia booleans, `captured_at`) measured at SEND time because the pywebview JS bridge appears asynchronously after load. The gateway normalizes that payload through the closed-key bounded `client_surface.normalize_client_surface` (unknown keys dropped, strings bounded through the disclosed strict-bound SSOT), stamps host `received_at`, and carries it inside `task_metadata` (the force_plan rail); the canonical inbound chat row persists it as an optional `client_surface` column. Deliberately NOT the `transport` dict: transport is chat-scoped reply routing (last-write-wins, erased for the main chat, filtered on by transport skills), while the surface fact is per-message provenance. The fact is assembled at its PRODUCER, never inferred at render: non-web bridge ingress gets a host-stamped `{"channel": <source>}` fallback at routing (EXCEPT synthetic A2A chats — negative ids are machine traffic and never wear an owner surface); `/api/command` stamps `{"channel": "api_command"}`; external `/api/tasks`/CLI admissions stamp their caller-declared channel at admission, overwriting any caller-supplied `client_surface` metadata (the closed-key normalizer is web-ingress-only, and a caller-built `received_at` would impersonate a host stamp). Machine producers (scheduler `scheduled_task`/`skill_scheduled_task`, evolution) stamp nothing — no owner message stands behind them, so they render no `owner_client` at all (schedule templates cannot even smuggle one: `client_surface` is a reserved template key, rejected at admission and filtered from persisted records). Promotion (`promote_chat_to_task`/`route_to_project`) and steering synthesize no new fact but CARRY the originating owner turn's fact via `_attach_client_surface` beside the origin attach; mailbox entries carry it additively, and the loop injects a surface note only when the sending-surface identity (`client_surface_identity`: pywebview/coarse_pointer/ua/channel — viewport and narrow_layout deliberately excluded, a resize is not a device change) differs from the last one seen in the attempt, plus one neutrally-worded note for the first observed fact with no baseline. Absence is an honest gap: an old SPA, a WS `command` frame, or an internal producer renders no `owner_client` at all. The client generates a message id when absent and uses it to reconcile its pending local bubble, the echoed canonical user row, routing annotations, and mailbox delivery retries. The id is evidence for reconciliation and selected idempotent routes; a successful browser `send()` call means only that the frame was accepted by the current socket, not that a task was durably admitted.

Ordinary frames sent while disconnected enter a process-local queue capped at 100 entries; the oldest entry is dropped when the cap is exceeded. The queue is flushed in order after reconnect and is lost on page reload because it is not a second durable outbox. Attachment messages deliberately set `queue:false`: uploads occur immediately before send, so retaining only their socket frame would leave unowned temporary files or stale references. If the socket is unavailable or closes during upload/send, Chat refuses the attachment message, cleans uploaded temporaries best-effort, and retains the browser-staged files for an explicit retry.

For a chat frame, the gateway validates uploaded filenames as basenames confined under the upload root. It exposes the first eligible bounded image as native image content and forwards the complete validated attachment set as task-staging metadata, then calls the local message bridge with the exact thread, Project, sender-session, client-message, and planning facts. The web owner identity remains fixed; `chat_id` selects a thread and cannot mint an external owner identity. If the bridge is not initialized, the socket returns a visible assistant warning rather than accepting the message silently.

A built-in `command` frame carries a slash command and enters the same bridge with rebroadcast disabled; runtime command routing, owner authorization, queue authority, and typed outcomes remain outside the socket module. Main header controls therefore reuse the ordinary command contract for Restart, Panic, review, evolution, and background consciousness. Panic is sent only after the shared dialog returns the strict confirmed boolean. The socket does not infer intent from command-looking prose or implement a parallel command state machine.

Built-in outbound envelopes include `chat`, `photo`, `video`, `document`, `typing`, `log`, `heartbeat`, `extension_lifecycle`, `message_annotation`, `projects_changed`, `task_named`, and `update_status_ready`. Chat progress may carry task lineage, role, requested/effective model lane, delegated route, terminal execution evidence, review projection, cancellation eligibility, outcome axes, artifact references, and nullable cost/finality fields. These are additive presentation facts; consumers must not infer a missing execution receipt, cost, or task result from the absence of one optional field.

Thread routing is explicit. Project chat, typing, media, and log frames carry `chat_id`; a Project panel consumes its own thread, while Main admits exactly the two host-stamped Project lifecycle rows (`project_started` at agent-initiated creation and the terminal `project_completion_summary`) — all other Project progress, digests, and logs stay in the Project thread. `projects_changed` carries a new chat id so every tab can extend its fan-out set before fetching the complete registry. When even that ordering loses the race (a stale or frozen tab), the server-stamped `project_thread` marker on the frame itself keeps Main from adopting it. Outbound chat, typing, media, and log frames (and the owner-echo row) whose FINAL chat id is a reserved Project thread carry that stamp, set once at the message-bus broadcast choke from the registry (mtime-cached membership lens — never a numeric range, so external transport ids such as Telegram stay unstamped and route exactly as before); Main's fan-out gate (`chat_activity.mainThreadAccepts`) rejects a stamped frame even when the client has not yet learned that project's chat id. Task-scoped LOG events acquire that final chat id at supervisor ingress: worker diagnostics (`llm_api_error`, tool timeouts, …) carry only their own `task_id`, so `supervisor/log_addressing.py::address_task_event` (re-exported by `events.py`) fills missing lineage from the host-attested RUNNING row, lets the Project binding win (a post-hoc bound task keeps its original chat_id on the row), preserves an explicit event chat_id (0 is the real Skill Review session, never "missing"), and otherwise stamps the task row's own chat or the `DirectActivityRegistry` entry (direct/ephemeral turns additionally carry their chat BY VALUE: the turn-scoped queue proxy in `supervisor/workers.py::_TurnEventQueue` stamps the turn's own events at the producer, because the registry entry dies with the turn while its queued events drain later). Addressing is honest — an A2A synthetic id is stamped as the row says it, and the broadcast choke (`push_log`) suppresses A2A frames so machine traffic never reaches the browser. The same addressing runs in the server-process append sink (`make_server_log_sink`) and, via `address_handler_push`, at every supervisor handler that owns a suppressed type's explicit push, so a server-side producer cannot leak an unaddressed frame into Main; a genuinely unaddressable event (no row, no registry entry — e.g. `review_model_error`, which carries no task_id) keeps the legacy chat-0 frame. `message_annotation` updates one canonical owner message without creating another bubble. `task_named` updates a card only where that task already exists. Media/document consumers validate MIME, base64, and download-route shapes before building browser URLs.

Extension WebSocket traffic is structurally namespaced by `extension_loader.extension_surface_name()` so an extension cannot shadow a built-in type. On each incoming extension frame the gateway resolves the owning skill and reconciles whether its extension is still desired, reviewed, granted, enabled, and live. A missing or failed handler returns a visible log frame. Out-of-process handlers execute in their extension child off the event loop; in-process handlers first record the required execution/cost disclosure. A non-`None` result returns as `<request-type>.reply`. Exceptions become typed error log frames rather than terminating the server socket loop.

Server broadcasts snapshot the connected-client list and send to all clients concurrently. One slow or half-open browser therefore cannot head-of-line-block heartbeat and progress delivery to every other tab. Failed sends remove only the dead clients and append a durable `broadcast_partial_failure` event with message type and client counts; the original domain event remains owned by its durable producer. Restart shutdown closes remaining clients best-effort with code 1012 so they enter the ordinary reconnect path.

The browser reconnects with bounded exponential delay, shows the reconnect overlay, and resets the delay after a successful open. A watchdog closes an apparently open connection after 45 seconds without any inbound frame; heartbeat traffic therefore proves stream liveness rather than task progress. One served-SHA decision (`ws.js decide()`: keep / reload-changed / reload-unknown) governs both recovery paths so a transient network drop cannot destroy in-page state; an unversioned `/api/state` (sha is the empty string while `current_sha` is unset — run-from-source installs do serve a SHA once it is set) stays on keep when no non-empty SHA was ever remembered — the owner-selected default under uncertainty, accepting possibly-stale assets as the disclosed tradeoff — while a previously-known SHA that disappears or garbles still reloads. A client whose first post-open state fetch never completed still reloads once when a reconnect first reveals a served SHA (the narrow first-RTT reload window, retained base behavior). If the socket remains down while `/api/state` is healthy, delayed recovery probes consult it without adopting the served SHA (only the post-open refresh remembers it); a 200 response whose body is not a parseable object (a captive portal or interposed proxy) counts as a failed probe, never as health. An unchanged SHA keeps the page (and its queued outbound messages) and re-arms the probe, while a changed or unproveable SHA reloads; at most one probe is ever in flight per disconnect episode (probes are generation-scoped, so one that hangs across a reconnect discards its late result), and after several consecutive healthy probes with the socket still down, one forced reload per disconnect episode remains as the fuse for a stale browser runtime, and a probe that resolves after the socket already reopened does nothing. After reconnect the same decision reloads when the served repository SHA changed or can no longer be proven current, ensuring a restarted server does not keep old JavaScript or CSS alive in PyWebView.

Each Chat instance handles `open` by resynchronizing archive-aware durable history and handles `close` by withdrawing online/accounting presentation. Reconnect history deduplication covers overlap between live frames and REST replay. Logs performs the same pattern by merging bounded REST backfill with live frames. Large history parsing runs off the server event loop. Delivery is consequently live plus replay, not a promise that every transient frame is persisted: durable chat rows, task results, queue snapshots, Project revisions, review ledgers, cost ledgers, and lifecycle state remain the recovery authorities.
## 5. Supervisor Loop

`server.py::_run_supervisor()` is the single scheduler for pooled tasks. A healthy tick publishes liveness, rotates the paired chat and progress logs, checks worker health, drains worker, direct-chat, and consciousness events, accepts owner bridge input, enforces deadlines and schedules, runs throttled reconciliation and evolution admission, assigns eligible work, and persists `state/queue_snapshot.json`. Bridge intake deliberately precedes timeout, maintenance, evolution, and assignment work so a slow control-plane step cannot make a new owner message invisible. Three consecutive loop failures clear supervisor readiness, stop its watchdog generation, and notify the owner instead of leaving a healthy-looking server that no longer assigns work.

`PENDING` and `RUNNING`, guarded by `supervisor.queue._queue_lock`, are the live task-lifecycle authority. Admission reserves identity before project, workspace, attachment, or routing side effects can create a duplicate; rejects a disabled pool, duplicate task, project deletion, accepted or sealed root, exhausted root budget, or incompatible runtime mode; attaches the task contract; and preserves stable priority order. Assignment runs against the same locked state. It skips reaping slots, budget-paused work, closed project roots, conflicting project writers, and tasks that exceed the root's subagent capacity or depth reservation. Configured worker count is therefore not available capacity: the truthful value is the currently assignable idle count after custody, reaping, and admission fences.

`queue_snapshot.json` is an atomic recovery and diagnostic projection, not a second scheduler. It carries pending and running rows, acceptance and root-budget fences, actual worker and reaping state, assignable capacity, and any pool-disabled reason. Startup restores only a recent snapshot into an otherwise-empty pending queue; it never resurrects RUNNING work. Terminal tasks stay terminal, a task with an active durable cancel intent (or a legacy cancel-requested latch file) is left for cancellation custody rather than revived, descendants below an already accepted or sealed root are finalized as cancelled rather than revived, and malformed durable fence evidence fails closed. Snapshot capture copies the live containers under the queue lock because concurrent HTTP mutation once made the supervisor crash while iterating them.

Cancellation is intent-then-custody (Poltergeist phase A, 2026-08-11). Cancel INTENT never rides the canonical task status: every cancel ingress — the agent `cancel_task` tool, the HTTP single and cascade endpoints, evolution stop, Project deletion, the per-descendant mints of a cascade sweep, and the boot migration of legacy `cancel_requested` files — writes one durable row through `ouroboros/cancel_intents.request_cancel` into the compact locked projection `state/cancel_intents.json` (active intents only; every transition also appends a forensic `cancel_intent` row to the supervisor ledger). Every ingress fails CLOSED: an intent write that fails refuses that cancel with a typed error (tool `CANCEL_INTENT_WRITE_FAILED`, HTTP 503 `cancel_intent_write_failed`; a CORRUPT projection file gets its own honest refusal — tool `CANCEL_INTENT_PROJECTION_CORRUPT`, HTTP 503 `cancel_intent_projection_corrupt` — naming the preserved file and the `projection_corrupt_refused` forensic row instead of a "retry" that cannot succeed until repair; evolution-stop/project-delete skip the teardown and surface the failure — evolution stop covers PENDING evolution tasks through the same intent+custody ingress, keeps any task whose intent write failed, and reports the stop INCOMPLETE with typed per-task outcomes, "cancelled" naming only real cancellations) rather than tearing down without a durable, watchdog-replayable fence; a cascade descendant whose mint fails is still cancelled in-sweep, with the failure surfaced as a typed forensic row while the root's open `scope: cascade` intent lets the watchdog replay the whole cascade. The HTTP cascade ingress mints its intent WITH the cascade scope itself (the supervisor's own scope stamp is a loud second line of defense, warning + typed forensic row on failure), the recorded scope is WIDEN-ONLY (single→cascade; a narrowing re-request or `mark_intent_scope` call is refused with a forensic row), and a cascade over an ALREADY-SETTLED root with live descendants still mints the durable cascade coordination intent (`allow_settled_target`) — that intent is the watchdog's replay trigger for the subtree: per-task custody keeps it OPEN while any descendant is live (releasing its claim instead of settling), and only the cascade's no-live postcondition — judged on PHYSICAL queue/durable liveness, the intent itself excluded — settles it, after the tree's summary message is registered as owed under the deterministic per-intent delivery id `cascade:<root_tid>:<request_id>` (a replay of the same intent dedups even when the rebuilt digest's content differs, a later separate cancel request delivers its own; a summary that cannot be durably owed leaves the intent open for the watchdog). Timeout reaping is deliberately NOT a cancel ingress (owner 1=A names explicit cancellation): the reaper keeps its own custody protocol over the same `reaping` slot marker and never mints intents. `supervisor.task_lifecycle.cancel_task_custody` is the ONE settle owner: it claims the intent BEFORE any custody mutation (owner + generation, EXCLUSIVE while alive; a refused claim exits `failed` having touched nothing, so two racing custodies can never interleave into a double settle through the capture-miss lane, and a reaping-slot takeover is authorized only by a claim that provably took over the same intent's ABANDONED claim), then captures, confirms process death, re-checks the child's REAL settled result — natural completion WINS, a child that finished before the kill keeps its completed result, artifacts, and cost, and the cancel settles as already-settled — reconciles the task's open delegated runs from durable custody rows and ALWAYS re-audits them (open runs plus still-pending invocations are disclosed regardless of the reconcile outcome list's shape or exceptions), captures workspace artifacts from the real tree (a failed OR owed-but-unrunnable capture is `failed`, never `missing`; a shared-tree capture carries `attribution: shared_unproven`), writes the settled result with reconstructed-or-honestly-unknown cost (never a fabricated final $0), registers the owner's terminal answer as OWED in the durable outbox (or a typed no-chat handoff row), only then settles the intent, and only then publishes `task_done` — so a crash between the settle and the send replays the answer instead of losing both it and the watchdog trigger; the fast already-settled re-entry delivers idempotently before its generation-fenced settle too. `parent_decision` is stamped only at that OUTCOME. The two secondary settle sites — the pre-assignment pending drop and the budget-drain `fail_tasks` — hold the SAME claim/generation fence before they settle (a refused claim yields the task to its live owner), so no path can double-settle behind custody's back. The supervisor-tick watchdog (`sweep_cancel_intents`, ~20s) re-feeds unclaimed or ABANDONED-claim intents into custody — replaying a `scope: cascade` intent as a cascade, not as a single cancel that would settle the root and leave descendants running — so a lost control event or a custody attempt that died mid-teardown can no longer wedge a cancellation; a cascade mints a per-descendant intent so a crash leaves no live descendant unfenced. Queue restore and pre-assignment both consult the projection UNDER the queue lock so a cancelled pending task never starts, and the pre-assignment drop follows custody's own rules (stored status decides the outcome, a failed durable write releases the claim and leaves the intent open for the watchdog instead of publishing an unpersisted cancellation, and `parent_decision` is stamped from the intent). Readers see the typed public projection `cancel_state: "pending"` (with `cancel_reason` beside it when the intent carries one) on effective results (UI shows an interim "Cancelling…" — after a FAILED cancel request the prior phase and the Cancel button are restored only when a FETCHED live non-pending task detail proves the intent is not pending; a detail fetch that itself fails keeps the pending presentation and the disabled button for the next reconcile, and the task-detail reconcile consults the pending projection BEFORE the legacy terminal fallback; steering writes — `steer_task`, mailbox follow-ups on both the queue and direct-agent lanes, and `forward_to_worker` — are refused typed, the cancel-pending check runs BEFORE attachment staging and a refusal removes the just-staged inputs) until the settle; `task_done` is validated through the DURABLE result UNCONDITIONALLY for every non-ephemeral event, not through the event's own claim: a non-settled event status, a settled event claim over a non-settled durable row, and equally a BLANK event status (the primary producer's ordinary-completion shape, which now also stamps the durable status onto the event) over a non-settled or absent row, are refused as durable lifecycle faults — left to custody when a cancellation is pending, and otherwise terminalized as `failed` with a typed reason so the worker slot is never wedged by a refusal nobody owns — that synthetic terminal rides the NORMAL dispatch seam including the assisted-update orphan watchdog and the cooperative-checkpoint hooks (root-done and subagent tree-quiescence), exactly as an ordinary terminal fires them; the copy-back exception path neither skips this validation nor synthesizes a `completed` row for a task that never wrote one (`interrupted` keeps its restore-path exemption). Terminal answers ride one durable delivery seam (`supervisor/terminal_delivery.py`): restart-surviving `delivery_id` dedupe shared with the natural final-answer path, a loud UNREVIEWED salvage message (bounded preview with the exact omitted count plus a full-copy receipt) for cancelled and non-retry-reaped tasks (delivered BEFORE the reap's `task_done`, and also from the finalize-on-miss lane — a completed result found there ships as itself), one root message with a children digest for a cascade (digest MEMBERSHIP merges the root's durable descendants with this run's sweep outcomes — a watchdog replay after the children already terminalized still lists them; each child's line is rebuilt from its CURRENT durable status at digest build time, never a stale sweep outcome, and sweep outcomes win only for ids with no durable row yet), and nothing for a retryable reap; routing follows the task's lineage chat. The already-settled fast path and the finalize-on-miss lane run the same delegated-run audit as the kill path and thread `unreconciled_runs` into the miss-lane delivery, so a cancel over a dead task with live delegated runs never reads as a clean completion.

Stop POLICY is an axis on the same durable intent, independent of cascade scope (S3, Q1). Omitted/empty-body cancellation stays the legacy synchronous IMMEDIATE teardown — programmatic callers (Terminal-Bench, OSWorld, ProgramBench cleanup) keep their bounded budgets. An explicit `stop_policy=finalize_then_cancel` answers 202 with the intent OPEN and runs ONE bounded owner-stop finalization episode (`supervisor/owner_stop.py`): live descendants settle first and feed a bounded child-result projection into the root's final turn; the root receives a deterministic `finalize_now` control whose typed first line (`owner_requested_finalization`) routes to its own loop rail — zero or one tool-less model turn, retained-candidate reuse, terminalizing completed/best-effort under the honest owner reason, never the deadline's `acceptance_bypassed_deadline` falsehood. Held tasks bypass only generic idle/finalization-grace rails; a task's earlier explicit deadline and absolute ceiling remain independent hard axes and are never widened. The episode's grace budget starts when the loop drains the control (durable `control_drained_at`, first drain wins — a task inside a long tool call still gets its final turn when those hard bounds allow it), under an outer `OWNER_STOP_OUTER_CAP_SEC` cap from the request, with both anchors immutable; expiry, a hard-bound hit, a pending root, or an already-settled root feeds ordinary custody. Policy transitions are monotonic: an immediate request HARDENS the same pending graceful intent (including when the request names its physical timeout-retry leaf), preserves any durable cascade scope, revokes an unread finalization control, and is revalidated by the loop at drain; graceful can never soften an accepted immediate. Every ingress executes the current durable scope rather than the latest request body's raw shape, so Stop-now cannot narrow a cascade. A successful graceful root suppresses the redundant cascade summary; Panic bypasses both. The UI projects the pending soft stop through `cancel_state`+`stop_policy` ("Finalizing…" instead of "Cancelling…"). Beside stopping sits the owner "hurry" control (HQ1): a typed task-local `kind=hurry` owner-mailbox control (`ouroboros/owner_hurry.py`, `gateway/task_hurry.py`) that skips the next otherwise-eligible acceptance panel with a typed reason, zeroes remaining improvement passes, and makes force-plan projection task-locally advisory — NEVER a chat message, never a settings mutation, never a P3/commit/review-gate weakening; the effect is attempt-scoped (`task["_attempt"]`) and a shared `retry_reset` strips it on every same-id requeue (reaper timeout and crash requeue alike). These invariants hold for every install configuration class, not only advisory-enforcement installs.

The event bus is process-lifetime rather than worker-generation-lifetime. Full-pool and single-slot respawns reuse one manager-backed queue shared by workers, direct chat, and consciousness. A force-killed producer can leave a raw multiprocessing feeder frame corrupted, while rebuilding the queue on pool rotation strands surviving producers on the old endpoint. Synchronous manager serialization and isolated producer connections avoid both failures; the manager itself remains session-custody-tracked. Live-frame publication of persisted rows is exactly-once and process-symmetric: `ouroboros/utils.py::append_jsonl` streams only runtime `logs/*.jsonl` rows (never `chat.jsonl`, which has its own live channel, and never state/memory/receipt stores) into the process log sink, and each process suppresses the types whose live delivery has a dedicated owner — workers via `WORKER_LOG_SINK_SUPPRESSED_TYPES` (dedicated EVENT_Q siblings, plus types their producer already emits live under the same name), the server process via the superset `SERVER_LOG_SINK_SUPPRESSED_TYPES` inside `make_server_log_sink` (types whose supervisor handler performs the explicit addressed `push_log`, `llm_usage` included). One persisted event therefore produces exactly one live frame, pinned by `tests/test_log_forwarding.py` with the production sink installed.

Heartbeat and progress are different evidence. A heartbeat proves that a process or loop is alive; owner-visible progress and model-usage events prove that the task itself advanced. Fresh descendant progress or queued descendants can keep an orchestrator alive, while an explicit deadline, absolute ceiling, cancellation, and budget stop remain hard. After the typed finalization episode described in §6, timeout handling freezes its decision under the queue lock, removes the task from ordinary assignment, marks the worker `reaping`, and hands kill, join, salvage, retry, and respawn to the single off-loop reaper. An orchestrator with live descendants is not blindly retried because doing so would replay its plan and spawn a competing tree.

No retry or new assignment may occupy a timed-out slot until the original process is provably dead. If kill and join cannot establish death, the reaper preserves a low-rank RUNNING result, keeps the slot marked `reaping`, emits a visible wedged receipt and restart hint, and performs no terminal write, `task_done`, retry, or respawn. This intentionally sacrifices one slot rather than letting a still-running process race a replacement and overwrite its result. The next supervisor generation reconciles the durable record after old-generation process custody has run.

Unexpected worker death follows a separate three-way decision. An already-terminal durable result wins and is projected idempotently; a negative process exit code is terminal for every task because replaying the same infrastructure or platform signal usually repeats the failure and burns budget; only an otherwise eligible non-signal crash retries within `QUEUE_MAX_RETRIES`. Repeated busy-worker or all-workers-dead failures trip the crash-storm fence, disable pooled admission, and surface recovery instead of cycling workers indefinitely. Direct chat remains available because it is not owned by the pooled scheduler.

Startup and throttled maintenance reconcile three distinct residue classes. Process custody checks strict PID, start-time, command fingerprint, owner task, session, and generation evidence before reaping an owned process. Delegated-run reconciliation applies the same owner-gone reasoning to external harness rows; directly after the startup orphan reconcile, a once-per-generation backfill re-audits every stored terminal result still disclosing unreconciled delegated runs — a settlement from a previous generation appears in no current pass's outcomes, so only this reverse join from the stored rows can heal the stale projection — sharing one custody snapshot across all audits, writing and emitting nothing for a row whose audit is unchanged. The refreshed row keeps its `delegated_runs_*` counters as a historical snapshot from the original terminal write (owner decision); the `delegate_terminal_reconciliation` envelope (`trigger` + `open_run_ids`) is the current-liveness surface. Task, review, and project reconciliation repairs durable records whose producer no longer exists. These are not command-line-class kill sweeps, and one development or runtime instance must never reap another. The dedicated watchdog separately observes supervisor-loop liveness and a stuck in-process direct turn; it alerts and requests restart, but cannot safely unlock another thread's lock or kill work whose custody it does not own. Ephemeral owner turns remain a separate responsive lane, not a second scheduler.

Cooperative project checkpointing has two equivalent quiescence triggers. A host-minted genesis or cooperative tree is checked when its root settles with no live descendants, and again when the last child settles beneath a root that is already terminal. The second trigger is essential because a root-scope budget stop terminalizes the root before its children reach their own dispatch boundaries; the old root-only trigger saw a live tree once and never returned. Event dispatch only detects the condition after removing the finishing task from RUNNING. The bounded git chain runs on a daemon thread, revalidates quiescence under the queue lock immediately before mutation, and uses a per-root latch that remembers and replays a trigger arriving during an in-flight check. Only host-minted project roots are eligible; owner-attached folders are never auto-committed, credential-shaped files remain excluded and disclosed, and every material success, skip, or error receives a durable receipt.

The bridge recognizes `/panic`, `/restart`, `/review`, `/evolve [on|off]`, `/bg [start|stop|status]`, and `/status`; all other text enters ordinary agent routing. External transports may invoke these commands only with positive owner identity and a transport-specific owner-chat binding. The commands reuse runtime-mode, queue, cancellation, and typed-result authority rather than implementing parallel control paths. Chat and progress logs rotate on the same supervisor tick and archive readers preserve their joint timeline. Only explicitly isolated devtool roots may use the narrow rotation sentinel from §1; normal runtime roots never inherit it.
## 6. Agent Core

### Task lifecycle

A user message enters through a reviewed transport, is admitted by the supervisor queue, and runs in `OuroborosAgent`. The root pipeline captures the task contract and immutable context core, executes the LLM/tool loop, preserves a delivery candidate, stores the result and artifacts, emits lifecycle and usage evidence, performs the root-only post-task work, and publishes the typed outcome. Queue admission proves only that asynchronous work was durably accepted; completion, objective satisfaction, artifact finality, verification, and review acceptance remain separate facts.

`DeliveryCandidate` is retained before verification or review so a later notice, reviewer failure, deadline, or provider outage cannot erase a useful answer. It also carries sticky loop-local provenance that its lineage has seen a host-issued delivery-control episode: `_arm_delivery_control` sets the marker and every replacement inherits it, including ordinary acceptance improvements. `outcomes.py` combines execution, objective, review, artifact, and child-absorption axes without converting one axis into another. Verify-before-done receipts and exact artifact references are host-attested evidence; declarations and answer prose are not substitutes. A forced exit may publish the best current candidate only with its typed rail and evidence-freshness disclosure, and lifecycle may remain `completed` while the objective or review axis records a best-effort or unaccepted result. When a forced exit fires while the delivery-control latch is armed, the model's one forced answer may legitimately be the protocol object: `loop._resolve_forced_delivery_control` resolves it purely (valid `keep` → retained candidate, valid `replace` → `full_answer`, malformed → retained candidate with the typed `delivery_control_degraded` reason) before suffixes and publication and never re-loops. With the transient latch off, a lineage with no prior host-control episode still treats exact JSON as ordinary text. In a marked lineage, both ordinary and forced resolvers intercept only recognizable whole-body protocol envelopes: valid `keep`/`replace` resolves normally, while an unknown verb, duplicate protocol key, or invalid replacement preserves the retained complete candidate immediately and injects no repair prompt. Both latch-gated resolvers (ordinary and forced) first strip one whole-body markdown fence (normalization shared with `observability._is_delivery_control_payload`, which keeps its own latch-free salvage semantics) and treat a balanced protocol object at the very END of prose as a protocol attempt, never as publishable text (the trailing detection is the shared `utils.extract_trailing_json_object` — one forward string-aware O(n) pass with fences peeled, duplicate protocol keys flagged as repair intent, a RecursionError-deep body degraded to prose, and bounded line-anchor retries after an unbalanced prose brace or quote; the protocol-key judgment stays in `delivery_protocol.parse_delivery_control_body`, so an ordinary trailing JSON object and a protocol object nested inside one remain prose): the ordinary resolver takes its one repair round then degraded-preserve, the forced resolver preserves the retained candidate with the typed degraded reason. Three disclosed residuals of that containment rule: a control object quoted MID-prose stays prose — Ouroboros legitimately quotes the literal in its own PR bodies and docs, and the incident form was trailing; with the latch OFF the ordinary resolver passes prose with a trailing protocol object through as ordinary text (the history marker does not widen to embedded objects; this test-pinned passthrough protects answers that legitimately END with the quoted literal; the hold and owner-revision branches instead escalate a control attempt into the armed round); and on the FORCED rail a TRUNCATED trailing protocol fragment — the output was cut mid-object, so the braces never balance — passes through as prose even under an armed latch (a fragment is not a parseable object, and containing it would require the substring scanning the containment rule deliberately rejects; disclosed and test-pinned rather than scanned). The child-absorption gate is an action gate: while undispositioned direct children remain, the loop HOLDS the candidate (`child_absorption_or_revision_required`, same family as the skill-lifecycle hold) instead of arming the JSON-only control instruction — the absorption reminder holds, and a post-tool evidence change holds rather than arms while that hold is ACTIVE (before the first reminder places the hold no disposition instruction exists yet, so the ordinary evidence arm still applies), so the model never receives the disposition-tool instruction and the JSON-only instruction in one round; a reconsidered full prose answer may still proceed, a typed keep cannot close the gate, and after the one bounded reminder the gate forces the best-effort `children_unabsorbed` rail with a CURRENT `id [status] sha256` listing recomputed for the forced prompt and again for the acceptance panel's debt evidence. Provider death is the one forced rail that is NOT a best-effort completion: `_handle_provider_unavailable` still salvages the best available text into the result body, but stamps `infra_failed`, so the task terminalizes `failed` with the typed `provider_unavailable` reason and the supervisor sends the owner an immediate "provider outage — NOT completed" chat notification on the root's terminal dispatch. A waited-out transport outage reaches this rail through its deterministic no-resend branch (`transport_unavailable_no_resend`, keyed on the wait episode's latched cause): same salvage, same `infra_failed`/`provider_unavailable` truth, but no forced-final provider call is attempted over a proven-dead egress. That rail makes its one forced model call only while a call can still land: when the transport already spent its same-model retry wall — the attempt budget, or the deadline bounding the backoff — `call_llm_with_retry` stamps `_llm_retry_wall_exhausted` on the shared usage dict, and `_handle_provider_unavailable` reads it as a third sibling of its `context_overflow` and `provider_outcome_unknown` no-call gates (typed `source="retry_wall_exhausted_no_repay"`; every other forced rail keeps its one call, and the `deadline_local` shape keeps its grace call — the provider there is not proven dead) and ships the salvage DIRECTLY, running service finalization, the swarm-router short-circuit, status stamping and the ordinary candidate packaging while making no request at all (the unsent prompt never enters the transcript, so a replay cannot read it as a request the model ignored). The marker is stamped by the exception handler's ONE shared stop tail — every error class that reaches that tail is retry-same-request (permanent refusals already stopped inside `_record_llm_call_error` and leave the wall unspent, keeping the one forced call their class is entitled to) — and by the empty-response path only while the PROVIDER is failing (`finish_reason=None` glitch or a transient body error; an ordinary empty answer with `finish_reason="stop"/"length"` is a live provider, and the shorter forced tool-less prompt may well land). It is CLEARED AT ENTRY of every `call_llm_with_retry` invocation, because the primary and each fallback candidate share one usage dict; a transient-exhausted primary whose PERMANENT-failed fallback cleared the marker therefore re-pays one forced primary call — the disclosed residual of keeping the marker a last-invocation bool instead of a route-keyed ledger.

The one repair path emits the same host control prompt, so it records the episode on the retained candidate before returning `retry`; a successful repair replacement then inherits that provenance exactly like an ordinarily armed replacement.

Terminal delivery preserves producer authorship: complete model answers appear as Ouroboros; host-authored incident receipts appear as System; unreviewed intermediate output remains in durable task details.

Host-enforced task acceptance is a root-owned completion coach, not the P3 commit gate. `off` disables it. In `auto` and `required`, substantive queued, headless, and scheduled roots are eligible; direct chat becomes eligible only after an observable reviewable effect or a typed deliverable/criterion. Pure conversation, ordinary read-only exploration, routing turns, and cognitive-memory updates do not create eligibility. Child reviews are advisory evidence and are superseded by the root decision.

Before an eligible panel is called, `supervisor/task_lifecycle.py` closes subtask admission under the queue lock and `task_status.find_child_tasks` proves the recursive subtree terminal and quiescent. Revision reopens the fence; terminal or degraded completion seals it. The reviewer packet preserves verbatim owner directives, the full task contract and criteria, canonical deliverable identity and aliases, terminal child state, verification receipts, artifact/provenance references, and an explicit omissions manifest. A required component that cannot be assembled makes the affected actor `DEGRADED`; it is never silently dropped to make the prompt fit.

A paid panel is the scarce resource, so it is priced by what the agent actually CHANGED, not by what merely moved (owner ratification 2026-08-30). The host buys one panel per PAID IDENTITY — `sha256(candidate_hash + the sorted set of nonempty (obligation_id, disposition, sha256(reason)) tuples)` — so exactly two things mint a new panel: a changed candidate answer, or a new nonempty obligation disposition. An empty disposition reason hashes to nothing and buys nothing, mirroring the commit gate's rebuttal hash on the same principle that an empty rebuttal is not an argument. The evidence revision deliberately does NOT price: every cosmetic tool call shifts it, which is how one task bought twenty-one panels; it stays what it always was, stale-packet detection for the supersede paths. A resubmit whose paid identity is unchanged replays the recorded verdict for FREE and terminalizes with the typed `identical_acceptance_refused` reason (a BLOCKED objective, keyed on the status+reason pair like the spent-cap case) and must NOT re-enter the improvement capsule — feeding the note again asks for a round the agent already answered with nothing new. A replayed CLEAN pass is not that case: it terminalizes `accepted` on its own branch.

The configured slots are independent actors with adaptive quorum. Each actor receives one substantive interaction and no more than two physical sends on its bound route. Transport status, parse status, semantic verdict, criterion support, model/provider/route, quorum contribution, and binding hashes remain distinct so an unavailable or malformed response cannot masquerade as a negative judgment. `PASS`, `FAIL`, and `DEGRADED` are reviewer verdicts; the host-owned completion decision is separately `accepted`, `revision_requested`, or `finalized_unaccepted`, with its typed reason owned by `outcomes.py` and written only by `acceptance_dialogue._set_acceptance_decision`. Only a clean quorum may authorize `accepted`. The agent may add its own disposition and rationale but cannot overwrite the host decision.

A clean criterion is evidence-resolved, not merely well argued. Reviewer `evidence_refs` must be exact members of the host packet's enumerable reference vocabulary. A claim id resolves only through `acceptance_support_refs` linked to a passing host receipt for that claim; agent-supplied, declared-intent, unattested, unknown, and non-resolving sections never certify success. An unresolved reference preserves the actor's transport, parse, and semantic record for audit but removes its clean contribution. This total, fail-closed resolver is why the task cannot certify itself by echoing its expected outcome.

Actionable findings enter the durable obligation dialogue with stable identity. The agent can fix, rebut with an evidence-bearing disposition, or ask the reviewers to declare the issue unreachable here or a stable disagreement. Re-raises must name an existing obligation id or are disclosed as new findings; a valid rebuttal retires the row and an invalid one reopens it with both positions preserved, and the reviewer's stated counter-argument (`reviewer_rebuttal_response`) rides into the next panel's obligation catalog so the following panel can tell "already answered" from "never answered" instead of re-adjudicating one side only. Each panel also receives the bounded `acceptance_dialogue_history` — round, aggregate signal, dialogue status, vote distribution, new-versus-re-raised obligation counts — held OUTSIDE the hashed evidence material precisely so reading the history cannot mint a fresh revision, and therefore cannot mint a fresh paid binding.

Termination beyond a clean pass belongs to the reviewers, not to a host counter. Their typed `dialogue_status` votes reduce over the contributing actors gated by contract validity — a slot whose verdict did not reach the aggregate does not steer the loop either, and a PASS/FAIL signal beside a malformed parse never votes. Majority voting stays rejected: one contributing reviewer may hold the loop open, but only WITH MATERIAL — a continue vote counts only when the same response carries a concrete finding or a completion coach, otherwise it is disclosed as `continue_without_findings` and abstains, because a bare "keep going" is not a judgement the agent can act on and it bought a panel every round. Missing or invalid votes abstain the same way and never default into another paid round; one well-formed terminal vote ends the dialogue. Zero well-formed votes reduce to the typed `inconclusive` — a reducer output deliberately outside the reviewer vocabulary, which grants the dialogue no authority in either direction and falls through to the existing degraded, no-capsule and exhaustion terminals rather than minting a host verdict. The per-panel timing event carries `effective_max_cycles`, `cycles_source` (owner setting versus shipped default) and `total_paid_cycles` from the same ledger the wallet claim counts, so what bounded a panel and how many the tree has bought is visible without summing receipts. Required+Blocking continues until clean acceptance or a real deadline, budget, round, lifecycle, or configured improvement-pass rail. Required+Advisory may publish an honest non-clean result. Pacing reserves time for the first review and sizes later passes from observed duration; the improvement capsule reports the actual verdict, open obligation ids, remaining rails, and the concrete next moves rather than inventing a timer-based give-up.

Every forced deadline, budget, or round rail uses the common terminal recorder. If the task was eligible but no panel ran, the review axis records an eligible bypass with zero runs, the rail-specific trigger and acceptance reason; a pure eligibility probe does not run the panel, quiescence, or another model. The forced `children_unabsorbed` rail is the one exception (owner decision Q2A, 2026-08-10): for an acceptance-eligible root with a quiescent subtree it still runs the acceptance panel, with the undispositioned-children debt in its evidence, and a requested revision terminalizes as `finalized_unaccepted` (`revision_unavailable_on_forced_rail`) because the forced rail cannot loop. This keeps a forced delivery distinct from both clean acceptance and a task for which no panel was warranted. The root's post-task phases use the minimal `root_phase_checkpoint`: startup retries only a durable `pending_once` phase, while an indeterminate `running` phase is disclosed as degraded rather than replaying paid work.

The agent-callable `task_acceptance_review` does not call the panel for an eligible root. It validates and stores claims, checklist items, evidence references, and the optional agent disposition, then returns `deferred_to_host_acceptance` and `authoritative=false`. Structural eligibility is unchanged. Child-task review and `off` mode keep their separate behavior.

Finalization controls are typed owner-mailbox entries rather than injected owner prose. The supervisor may request one bounded tool-less answer, salvage the last persisted assistant text, and retain a full canonical copy when a preview would truncate it. A grace episode has one durable control and can be revoked atomically when the task itself resumes; descendant activity and host-authored narration may spare the task but do not count as the task's own progress. A process that cannot be killed remains visibly running, and custody checks prevent another runtime instance from reaping work it does not own.

Disclosed cancel-lifecycle residuals (phase A final gate, deliberately not fixed): a cascade over a tree with NO resolvable lineage chat whose typed handoff-row append ALSO fails still settles (two independent rare failures stacked); the cascade-settle auto-release touches only a fenced claim, but an empty-intent release can still add liveness noise to a foreign claim's forensic trail (bounded by the generation fences — never a settle or state change); cascade postcondition timing can flake under heavy load (a re-check races a finalizer; the watchdog re-feeds, so the cost is one retry, never a lost teardown); and the cost projection of a task whose delegated runs stayed open may read `cost_usd=0`/`cost_final=true` while a run is still live and spending — the disclosure line names the open runs, and the cost-side single source of truth is phase C's C2 work, noted here for that landing.

### Tool capability and execution

`tool_capabilities.py` is the SSOT for core, meta, parallel-safe, stateful-browser, untruncated, capped-result, and reviewed-mutative tool classes. `tool_policy.py` chooses the initial capability set; `ToolRegistry` remains the execution authority; `loop_tool_execution.py` owns timeouts, concurrency, live evidence, result handling, and mutative ceilings. Ordinary top-level presets share one built-in name surface: project focus changes the default target, while root policy, runtime mode, task-contract disables, credentials, resources, repair/ephemeral rules, and delegated-child profiles narrow independently. A tool being registered or discoverable is therefore not the same as being callable for a particular target. Lazy capability discovery returns an explicit capability omission or `CAPABILITY_UNAVAILABLE` fact when the advertised surface cannot be enabled; it does not silently disappear. `enable_tools`/discovery answer a REGISTERED tool filtered by real policy with a typed "hidden by policy: <reason>" (`ToolRegistry.policy_hidden_reason`, with the same predicates and order as `get_schema_by_name`), never the same "Not found" as a nonexistent name; the contract-disabled check precedes registration so a disabled extension/MCP name also reports its reason. The swarm-router's `promoted_task_toolset` is one LIVE `top_level_tools` projection with typed `unavailable_builtin_tools`; dynamic extension/MCP tools remain honestly unlisted. Child allowlists remain deliberate narrower principals, not a second top-level workspace catalog. Review output and cognitive artifacts remain outside ordinary result truncation.

Outcome classification keeps policy refusal separate from execution failure. In particular, `user_files_path_blocked`, `cwd_blocked`, and `artifact_output_undeclared` are typed non-failure/policy-denial surfaces; a declared output that cannot be registered remains the genuine `artifact_output_error`. This prevents an expected authority boundary from falsely becoming the task's headline failure while preserving real artifact loss.

#### Web access mechanisms (three distinct paths — do not conflate)

The three web paths differ in who chooses the query, which model reasons, which authority performs the fetch, and where evidence is recorded:

1. **Main-loop native search.** A provider server tool is attached to the main solve-model request only when the main-loop setting allows it. The same solve model decides whether to search; no second reasoning model enters the scaffold. Provider citations and request counts are folded into `llm_usage` and the task's host-attested retrieval fact. That fact is context for task acceptance, never a criterion by itself; absence means only that this native path recorded no search. The provider-side query is not available to the host and is not claimed as logged.
2. **`web_search` function tool.** `ToolRegistry` executes a separate search call through the configured web-search route or a keyless retrieval backend. A provider-backed call can therefore introduce a second model and its own cost. Arguments and bounded results are recorded in `tools.jsonl`; they do not become native-search usage on the answering call.
3. **Browser tools.** `browse_page` and `browser_action` drive a local stateful Playwright session and can fetch or act on arbitrary pages. Their arguments and result previews are tool evidence. Browser state and local action semantics are not equivalent to either provider-native retrieval path.

For delegated profiles, the URL, route, private-range, and control-plane
guards apply to both local-readonly and acting children. JavaScript `evaluate`
is intentionally exposed only to a valid acting child on its current page;
local-readonly execution and schema remain blocked, while the shared
owner/self-lowering checks still run for acting evaluation. Acting children
retain the pre-existing shell-to-loopback `/ws` route; this change does not add
WebSocket authentication or broaden that route to local-readonly children.

This separation is methodological authority: an evaluation or acceptance claim must name the path actually used instead of treating provider-native search, a separate search model, and a local browser as interchangeable.

#### Context fitting, retry, and compaction

`config.get_context_mode()` is the effective Main sizing/rendering source, while `config.get_owner_context_mode()` is the persistent owner-intent/P3 source. They differ only during the auto-Low compatibility window: bare env Low sizes Main as Low but remains owner Max unless the explicit false provenance tombstone authors owner Low. In Max, `ARCHITECTURE.md` is full-resident for every task class because it is Ouroboros's capability/tools/access map; in Low it is replaced by its lossless navigation map. This rule does not vary for project, evolution, external, headless, or delegated work. `DEVELOPMENT.md` is mode-independent: it is full when the active repository binding says the work targets Ouroboros's own body. A bound external workspace, including an auto-provisioned project tree, any subagent, or an API/CLI/scheduled external surface receives the visible on-demand pointer. Project membership is not the signal: a room turn with no external binding retains the handbook, while a project task bound to another tree does not. `workspace="none"` retains it; evolution and self-body work retain it; `context_requires_development` and `context_requires_self_body_docs` override the default. Context economy comes from dropping the self-engineering handbook for external work, never from hiding the capability map in Max. `prompts/SYSTEM.md` and `BIBLE.md` are tier-0 and full in both the Max and the Low projection (`context_layout.TIER0_ALWAYS_FULL`); the one path that omits SYSTEM.md sections is the local-model overflow compactor (`llm.py::_compact_local_text`, which keeps the preamble before the first `## ` plus the BIBLE section and replaces every other section with an omission marker) — hence the prompt's load-bearing floor lives in that preamble, and the prompt carries only identity, the decision loop, cross-tool policy, and invariants stated once; a tool's contract lives in its `get_tools()` schema, which `tool_policy.initial_tool_schemas` sends on every round, and mechanism documentation lives in this file — a prompt sentence restating either is a second copy that drifts (see DEVELOPMENT "LLM-first affordances").

`context_fit.py` renders Max and Low projections from one immutable core and measures each ordinary Main candidate on one labelled density basis against the selected route capacity. Owner Low additionally has an elastic 200K total-context economy target; crossing it is never a synthetic failure. With unknown capacity, owner Max gets one honest Max call while owner Low may still reclaim toward its known target before sending best effort. Predicted Max pressure retains the Max document projection and may request one deficit-sized mutable-history pass; only an actual provider overflow authorizes task-local Low. After that overflow, one same-route semantic recovery is permitted only when the final post-transform candidate has the same route, round and response reserve and strictly fewer context-bearing bytes. Owner mode and P3 applicability never change. P3 commit/scope review retains its separate fit and oversize policy.

`context_compaction.py` is a requested materializer, not a second threshold, timer or retry authority. Pure selection chooses a positive-reclaim prefix of completed assistant-call plus contiguous matching-result units; owner turns and malformed, interrupted or visually opaque units remain verbatim. A non-empty selection first writes an exact private checkpoint, then summarizes complete gap-free hashed map/fold input. Independent covered units may apply while a failed unit stays raw. Recompactable host-only capsules retain generation, lineage and checkpoint/CAS refs and are stripped from physical candidates. Selection and receipts use the caller's exact density plus the same bounded image projection as ContextFit. No eligible positive reclaim means no checkpoint, summarizer call or transcript mutation; the one route+round latch prevents repeating the same automatic pass without a hysteresis timer.

Ouroboros stores one provider-neutral, function-shaped conversation. Direct OpenAI agent/tool traffic remains on Chat Completions: `openai_chat_custom.py` projects only the physical copy into Chat custom tools and normalizes returned custom calls back into canonical function calls. For direct OpenAI Chat with tools and a requested non-`none` effort, every eligible call begins with custom and that exact effort. Generic parameter repair composes on the current rung before a dialect change. Only an exact custom-dialect rejection advances to a fresh function candidate with the original effort; only an exact function-dialect rejection may advance to explicit `none`. The ladder identities stay fixed at custom/function/none ordinals 1/2/3, and the custom→function control-flow fallback is never learned as a durable dialect action. The last candidate exists only inside the current call and only if the caller's physical-attempt rail admits it. The compatibility layer never migrates the conversation to Responses, changes the model/provider/API surface, or raises an attempt limit.

`request_wire_recovery.py` is the single provider-neutral adaptation driver for both exception and HTTP-200 body-error shapes, sync and async. It identifies one credential-free exact route/profile (provider, endpoint, API surface, resolved model, reasoning carrier, tool dialect/choice/strictness and relevant value predicate), applies fresh typed evidence before send, and may compose only bounded `set_value`, `drop_field`, and registered `replace_dialect` actions. A learnable reactive action is pending until a semantically valid normalized response is bound to the exact settled physical-attempt capture; only that success can write the 14-day cross-process store. A non-reasoning repair inside the already degraded task-local rung is disclosed as task-local and never becomes pending or durable. Provider prose cannot switch routes. Legacy model-global effort/rejected-parameter rows remain readable diagnostics and regression compatibility, but no longer decide scheduling or normal physical dispatch.

Every settled candidate is disclosed as `usage.request_wire`; nested callers aggregate terminal per-call disclosures in order as `request_wire_history` (bounded with an explicit omitted count). This history is not the physical-attempt ledger and does not enumerate every failed send: `state/usage_attempts.jsonl` remains the complete monetary/attempt authority. The private custom-argument receipts never enter stored history or public observability. Main, Background Consciousness, and structured context compaction consume them before executing a custom-origin call; invalid arguments use one bounded ordinary tool-error continuation, while function-origin behavior remains tolerant as before.

Direct Anthropic tool turns retain a private route-bound receipt containing the complete native assistant `content` list in original order, including thinking/redacted-thinking, `caller`, signatures, and future opaque members. The immediately matching same-route continuation replays that list byte-for-byte before its tool results. A provider, endpoint, API-surface, or model change scrubs the receipt. An unfinished native unit cannot be compacted; summarizer and public observability projections omit opaque values, while private checkpoints may retain the exact receipt for crash recovery. Owner-requested `none` is sent as `thinking.type=disabled`; a successful exact-route repair may disclose provider-default thinking, but no guessed legacy `budget_tokens` mapping is invented.

Retry budgets are failure-class specific. Empty/incomplete responses and transient 429/5xx/overload failures may retry the same model with deadline-bounded backoff; auth, quota, permanent bad requests, and already-confirmed oversize fail fast. Same-route request-wire recovery stays under the existing physical-attempt authority, and exhausted compatibility returns to the already configured model fallback chain rather than becoming a second router. `LLMClient` keeps leading system messages authoritative and demotes later notices to visibly marked user notices while preserving assistant-tool-result adjacency.

A REMOTE pre-dispatch transport failure is its own class, `transport_unavailable` (`loop_llm_call.classify_llm_exception`): released physical-attempt custody plus the typed pre-dispatch predicate plus a non-local provider. It takes exactly ONE physical attempt per call — no in-helper burst — because pacing belongs to the round-level wait episode (`ouroboros/loop_transport.py`): the round gate latches an episode, optionally walks the existing fallback chain once when `USE_LOCAL_FALLBACK` makes it local (remote candidates never dial over a proven dead egress; a chain candidate that itself dies pre-dispatch stops the walk — and when the primary failed generically with no episode yet, that mid-chain transport failure latches the episode itself), then waits — durable `network_wait` events (`entered`/`waiting`/`recovered`/`ended`) in events.jsonl, owner progress notes on a `min(NETWORK_WAIT_NOTE_INTERVAL_SEC, idle_timeout/2)` cadence that also keep the idle rail alive, an owner-signal-interruptible backoff sleep (4s doubling to the existing 60s transient cap), and a free redial of the SAME round (the round budget is not consumed, and the round top re-runs message drain, Stop/finalize controls, budget rails, and model overrides between redials). The wait is bounded only by the existing rails — owner deadline minus the dispatch-admission reserve (with one last free redial just before the window closes), budget, Stop, and the supervisor's absolute ceiling — never by a new setting; the acceptance-review percentage reserve is deliberately not a wait ceiling. Direct-chat and ephemeral decision turns never wait: they keep the responsive lane responsive and fail fast with an honest retry-when-connected message. Exact-model routes wait and redial their own pinned model with no local substitution. When the rails run out, `_handle_provider_unavailable` takes a deterministic no-resend terminal keyed on the episode's latched cause (`transport_unavailable_no_resend`, reason `provider_unavailable`, execution `infra_failed`) — salvage without a forced-final provider call, mirroring the `provider_outcome_unknown` no-resend shape. Consequence for single-model/benchmark runs: `OUROBOROS_TRANSIENT_RETRY_MAX` still bounds same-model attempts for transient PROVIDER failures, but a dead egress no longer dies after that burst — it holds the task until its deadline/ceiling. The mid-flight class is `provider_outcome_unknown`: the dispatched request itself is NEVER resent and stays billed at its reserved upper bound forever (the ledger's `unresolved` state is terminal), and review/safety/consciousness actors keep their own bounded send caps. For most tasks that class still terminalizes with its honest no-resend terminal. The one owner-ratified exception (nanny-leaf sprint) is a configured-session nanny with EXACTLY one live delegated leaf: instead of dying — and thereby cancelling a healthy leaf through the cause-blind terminal cleanup — the round gate latches a durable unknown-provider hold (`ouroboros/delegate_hold.py`) and the next round top parks the task in the ordinary `supervised_wait` (zero provider calls, real idle-rail lease, owner mail and controls live, durable `delegate_hold` events). A meaningful leaf wake resumes the task with a NEW round whose transcript carries the wake receipt — a unique host-attested input absent from the unknown request, which is what authorizes a new logical request; control wakes (Stop, deadline, finalize_now) exit through the unknown no-resend terminal with zero further calls, and a terminal-but-unsettled leaf never enters the hold (completion-wins reconciliation already preserves its output). Budget admission for wake rounds stays fail-closed against the eternal unresolved upper bound.

OpenAI-compatible response choices keep their outer `finish_reason` as the
bounded per-call usage fact `response_finish_reason` (and may retain a bounded
upstream `response_provider` label when the response supplies one). These are
observational fields only: their reserved usage keys are host-owned and any
provider-supplied values are discarded unless the designated outer response
field supplies them; they never enter canonical assistant history and do not
change the empty-response classifier or retry policy. The trusted provider
canary accepts a schema-valid native tool call even when the assistant also
returns text, recording only its length and hash as warning telemetry; the
trusted integration consumer emits that bounded record through the test
warning stream so it remains visible on a passing CI run; that list is
host-owned and provider extension fields are discarded before emission. A
malformed native argument, invalid schema, or missing call remains a red
contract failure; no prose parser, salvage, provider hop, or unbounded retry is
introduced, and diagnostics omit raw provider content and reasoning payloads.

Prompt caching is stable-first. Governance and task-stable contracts precede mutable evidence; review builders disclose the stable/dynamic boundary and keep untrusted payloads outside the governance cache block. Provider-specific cache hints are sent only where supported and receive one exact retry without the rejected hint. Rejection evidence is durable and route-specific. Cache identity must not weaken exact review bindings or create a second review authority.

Gateway response-cache recovery is narrower and reactive. The first call remains
cacheable; only a main-loop `provider_incomplete_response` arms a fresh-response
request for later attempts. The generic `openai-compatible` route is Ouroboros's
gateway/proxy route, so only it renders LiteLLM's documented
`extra_body.cache.no-cache` control. A strict compatible endpoint that explicitly
rejects `cache` receives the existing one exact cache-parameter removal retry.
Direct providers and OpenRouter never receive this LiteLLM field; no URL/model
heuristic or parallel gateway-capability subsystem is introduced.

#### Vision and local image evidence

`analyze_screenshot` and `vlm_query` are bounded secondary-model calls through `LLMClient.vision_query`; `view_image` attaches a local image natively to the active conversation. Send-time image routing works on a copy of the transcript so captioning or placeholder conversion never mutates canonical history. Image payloads are validated, capped/downscaled, and confined to readable roots derived from the Tool API policy matrix plus the protected-artifact rule; URLs and inline base64 are not accepted as local paths.

`vision.attach_local_image_to_context` is the single attachment seam for an explicit `view_image` call and a tool result carrying the typed `auto_attach_image` opt-in. Both paths make the same durable copy, enforce the same trust boundary, and obey the same live-image eviction budget. Auto-attachment failure is non-fatal: the tool result and path remain visible so the agent can inspect it explicitly. Vision/local-media tools are not web tools and may be withheld by the task contract.

#### Background consciousness and Evolution

Background Consciousness is the high-horizon awareness loop. It can groom memory, identity candidates, knowledge, and the improvement backlog, message the owner, and initiate an already configured reviewed Presence binding; it does not directly acquire the binding's tools or transport authority itself. It does not directly run shell/code work, subagents, reviews, commits, or evolution toggles. Evolution Campaigns perform self-improvement as ordinary governed tasks, so awareness can propose work without bypassing task, budget, and review authority.

Its observation inbox is the append-only `state/consciousness_observations.jsonl`
store under the active runtime data root. Existing producers write a stable-ID
`enqueue` row before the wake notification returns; `_snapshot_pending_observations`
reads a cached, insertion-ordered projection, and `_ack_observations` appends
ACK rows only after the thought receipt, tool receipts, budget settlement, and
other durable state writes for that snapshot have succeeded. A provider error,
overflow, cancellation, unreadable/malformed row, or unknown ACK leaves the
snapshot pending and exposes the existing actor-readable source reference
`read_file(root='runtime_data', path='state/consciousness_observations.jsonl')`.
The status surface reports only pending count, oldest timestamp, source, and gap
count; it never treats that bounded projection as the observation source itself.
When the same cycle can call `update_identity`, an observation gap joins the
existing identity-completeness envelope and blocks that destructive write until
the actor resolves the named source. This preserves direct BGC autonomy for a
complete source without adding an approval flow.

An active campaign owns an explicit objective, campaign id, transaction, and task claim. `evolution_mode_enabled` is only its scheduling projection. Dispatch, review, commit, publication, and restart revalidate that exact authority; a restored row without a live uncommitted claim is cancelled. A reviewed commit binds to the claim by exact SHA before publication. If authority changes after commit, the commit is moved to a private inspection ref and any attempt-created tag is removed from the normal namespace; concurrent index/worktree edits are not reset and no restart occurs. Restart verification and boot reconciliation decide whether the cycle is absorbed, abandoned, or still pending, and exact terminal replay resumes only incomplete effects without double-counting a cycle.

Campaign cleanup is deterministic and custody-aware. A no-op or abandoned cycle may restore the transaction base while preserving dirty/ahead work in recorded stash or local refs, but cleanup is skipped when another task, a live test, or the operator kill-switch makes reset unsafe. A byte-identical diff whose last terminal is a review-verdict block is refused for free from the first block (preflight failures and infra-blocks neither build nor reset the streak); changing the diff, or supplying a rebuttal whose content hash is new to the streak, creates a new paid reviewable case, and the shared cycle cap bounds paid triad+scope cycles per root task. Checkpoint and outcome rows preserve git/memory identity, cost, rounds, and explicit omissions so the promotion loop learns from failed as well as absorbed cycles. An agent-requested restart first drains heartbeat-fresh running tasks up to its configured bound and then fails closed rather than cutting another task silently.

Post-task evolution is owner-gated and default-off. The worker may recommend one backlog item by writing a request, but only the supervisor may convert that request into one normal campaign cycle. An owner stop closes the campaign, clears queued requests, persists a sentinel, and cannot be autonomously reversed; only an owner-authorized start clears it. Evolution is hard-blocked in `light` runtime mode at every entry point and uses the normal task/review path in `advanced` or `pro`.

Loop self-checkpoints remain plain user-message reminders. They deliberately avoid a second structured, tool-less reflection protocol in the hot loop: strict parsing and prompt-shape changes previously produced unusable records and destroyed cache continuity. Durable learning belongs to the post-task reflection flow below.
Tool API v2 exposes neutral canonical names directly. Public schemas use
`read_file`, `list_files`, `search_code`, `write_file`, `edit_text`,
`edit_batch`, `apply_patch`,
`run_command`, `run_script`, `verify_and_record`, service tools,
`commit_reviewed`, `vcs_*`, `schedule_subagent`, `schedule_followup`,
`wait_task`, and
`wait_tasks`. Legacy public tool names are a breaking rename in v6.3: they
are not exposed and are not translated at execute time.
The file tools share a path-based public ABI: `list_files` uses `path` like
`read_file`/`write_file`/`edit_text`/`search_code`, not a separate `dir`
parameter. `edit_batch` carries the same `path` field inside each
`edits[]` entry; `apply_patch` addresses files inside the patch text itself
(`*** Update File: <path>`), with the same root-aware resolution. Because those
paths ride inside the payload they miss the dispatch seam that rewrites a
`path` ARG (`_PATH_NORMALIZED_TOOLS`), so both ends canonicalize explicitly
through `tool_access.canonical_repo_relative_path`: the handler before its own
protected checks, and the dispatch gates via `_payload_write_paths`, which reads
apply_patch's targets back out of the real parser. One normalization contract is
what keeps a guard from judging `repo/BIBLE.md` while the write lands on
`BIBLE.md`; `_ROOT_ARG_REPO_WRITE_TOOLS` is the single set every repo-write
fence keys on.

Filesystem tool output is self-locating: file/search/edit/write results use
canonical `root:path` labels, and `run_command` / `run_script` echo the
resolved `cwd` in command result headers. This makes root mismatches visible
without collapsing the storage or safety boundaries between resource roots.
`user_files` is the first-class root for user-visible files under the owner's
home directory. It accepts relative home paths such as `Desktop/report.html`,
`~` paths, and safe absolute home paths, but rejects the Ouroboros repo and
runtime control-plane. `task_drive` is task-scoped scratch and
`artifact_store` is task-scoped under `data/task_results/artifacts/<task_id>/`;
external deliverables written through `user_files` or declared process
`outputs` are copied into that canonical artifact store for audit. Declared
directory outputs are stored as bounded manifest+zip pairs so generated sites or
reports remain a single auditable artifact bundle without leaking hidden/control
files. When a
user-visible file is rewritten through the same source path, the previous
canonical copy is retained outside the manifest under
`task_results/artifact_versions/<task_id>/` with last-5 retention; old versions
are recoverable but are not advertised as deliverables or served as task
artifacts. Two READ-ONLY orchestrator roots complete the set: `subagent_projects`
and `deliverables` are granted `read`/`list`/`search` only to orchestrator
profiles (never write/shell/cwd, never handed to a subagent) so a parent can
inspect a child-task project tree or a finished deliverable when synthesizing its
work. That logical `deliverables` root stays read-only; a top-level task may
write the separately configured physical Deliverables container through the
existing `user_files`-authorized paths, including the narrow shell seam, with declared `outputs` and the existing
undeclared-output diagnostic/custody flow still applying. That audit remains
best-effort rather than a full shell parser; relative writes after an in-command
`cd`, shell-variable/indirect destinations, and arbitrary inline-code path
construction remain deferred parser residuals. Direct `cp`/`mv`/`ln` directory
destinations derive their immediate child target (including attached `-tDIR` /
`-Ssuffix` forms, `cp --parents`, and symbolic-link creation via `cp -s` /
`--symbolic-link`); `ln --relative` resolves its source from the command cwd
before the payload is checked,
but recursive directory/archive copies are not walked for nested symlinks or
hidden descendants. For argv-visible targets, the
shell guard checks the lexical Deliverables origin before generic workspace or
executor roots, then checks the symlink-resolved destination, so hidden,
credential-like, protected, and symlink-escaping descendants do not inherit a
broader root's admission; the same target-first rule applies to declared-output
custody and Presence ceilings. Presence ceilings keep their logical
`user_files`-relative prefix when the physical Deliverables binding is remapped,
so a narrow prefix cannot become whole-container authority. Declared outputs
still use the normal custody path; a successful dynamic undeclared write may
lack the nudge. Inode aliases
(hardlinks) are a disclosed filesystem residual, not a new Deliverables
authority check in this change.

### Safety and runtime mode

Every tool call first crosses deterministic `ToolRegistry` and resource-root guards; policy-based LLM safety is added where `OUROBOROS_SAFETY_MODE` requires it. The deterministic layers run in every safety mode. When the safety model itself is rate-limited past its one bounded retry, the guarded call is refused with the typed non-verdict `⚠️ SAFETY_UNAVAILABLE` outcome (a plain tool error downstream, never `safety_violation`) plus a durable audit event — an unchecked guarded call is never executed and never accused (the documented local-FALLBACK lane instead fails open with an audited `SAFETY_WARNING`). The LLM check degrades to a visible `SAFETY_WARNING` (never silently) in exactly three cases: (a) no reachable safety backend — no remote provider key AND no `USE_LOCAL_*` lane; (b) provider mismatch — a remote key is configured but does not cover `OUROBOROS_MODEL_LIGHT`'s provider, and no local lane is available (when a local lane IS available, safety routes to it first and warns only if that fallback also raises); (c) the local lane was chosen only as a fallback and the local runtime raised (a 429 there also warns, `_rate_limited_outcome`). The deterministic layer stays in force in all three, so a degraded backend never hard-blocks tool creation; the agent sees the warning. `runtime_mode_policy.py` owns protected self-repo paths, frozen contracts, release/build and managed-repo invariants. Light blocks Ouroboros self-repo and control-plane mutation, not normal user deliverables under `user_files`, `task_drive`, or `artifact_store`. Advanced may evolve ordinary app code; Pro may leave protected edits on disk, but publication still requires the reviewed commit path. Runtime mode is a self-modification boundary, not an OS sandbox.

Every deterministic write/owner-control guard consumes ONE mode-aware write-shape seam (`ouroboros/tools/write_shape.py`) — the registry no longer even imports the coarse legacy scan, so a guard structurally cannot judge on a coarser fact. Interpreter argv (including an `sh -c` wrap) takes `interpreter_write_shape`: a read-only `open(p, 'rb')` is not a write shape. Non-interpreter argv takes `non_interpreter_write_shape`: unconditional writers (cp/mv/rm/mkdir/touch/chmod/…) keep the membership floor, the pure-filter utilities (sort/uniq/sed/tar/gzip) are write-shaped only through a REAL channel (`sort -o` in both spellings, `sed -i` in any spelling PLUS sed's in-script `w`/`W`/`e` commands and `-f` script files — a script not provably free of those fails closed, exotic non-`/` substitute delimiters are the disclosed fail-open residual — a second uniq operand, tar create/extract, a redirect, a reported writer target), and the bare prose words ('delete'/'trash'/'truncate') yield to the same read-carve the owner-control detectors use (`_is_pure_read_inspection`, the v6.80.0 scope-floor contract applied family-wide) — a provably read-only `grep -n delete ouroboros/safety.py` reads, an unprovable head (`osascript -e 'delete …'`) stays fail-closed. The protected-core lane's mention branch consumes the same composed fact, so a pure read that merely mentions a protected filename is no longer refused as a "modification". The coarse bare `open(` token used to feed pure interpreter reads into the workspace write guard, which refused them with a false "write-like" reason and no route — the same class the light-mode runtime_data lane has re-judged since v6.54.3 ("the original GAIA class"). Write-mode opens (python `[wax+]` modes AND perl `'>'`/`'>>'` spellings), pathlib `.open('w')`, library save-APIs, ruby's `File.delete`/`FileUtils.*`/`IO.binwrite`, opaque subprocess/exec escapes, and every shell-level indicator (redirects — token-initial or glued into an operand, `tee`, writer utilities) still classify as writes, and literal write targets stay covered by `writer_target_tokens`; the disclosed residuals (`open(p, m)` with the mode in a variable; a writer reached through an alias the regex cannot follow, e.g. `from os import remove as delete`; parenless perl builtins such as `rename $a, $b`) are covered for external workspaces by the runtime/secret read guard below plus the LLM safety supervisor — chasing them with more spellings is the arms race BIBLE P5/P13 forbids. Workspace write-guard block messages name the resolved offending path and the sanctioned route (gated `read_file`/`write_file`, `root=skill_payload` with bucket/skill_name, or writing inside the selected process root) instead of one byte-identical reasonless string across five return sites.

Read-only shell git is allowed everywhere; mutating shell git is allowed only when its resolved target is outside the Ouroboros system repository and runtime data drives. The target-aware `git_shell_policy` enforces that boundary, while network-disabled tasks still fence network git operations. Acting `self_worktree` children remain read-only because patch capture requires an unmoved HEAD. `git init` and `git clone` are judged by their destination rather than the current directory, including relative destinations and path-valued retargeting flags. In external-workspace mode, the runtime/secret read guard exempts only an all-read-only git command: mixed shell segments, `--no-index`, or a nominally read-only command with a writing `--output` path lose the exemption. `resolve_shell_cwd` canonicalizes the cwd once and every guard consumes that same path. These composition rules preserve ordinary local git power without turning git into a runtime-data read or write escape.

The generic Tool API VCS family (`vcs_status`, `vcs_diff`, `vcs_pull_ff`, `vcs_restore`, `vcs_revert`) defaults to `root=active_workspace` and accepts explicit `root=system_repo`; every result names the logical root and physical repository. Protected Ouroboros path names constrain generic restore/revert only on the explicit system target, so a project's own `BIBLE.md` or `contracts/` remains ordinary project content. `preflight_review`, `commit_reviewed`/`vcs_commit_reviewed`, `vcs_rollback`, and promotion remain system-repository lifecycles even when the calling task is focused on a project.

A task contract may declare `resource_policy.protected_artifacts[]` as execute-only black boxes. Registry guards allow the declared execution but refuse reads, copies, hashes, static inspection, and trace/debug wrappers over those paths; generated outputs remain ordinary artifacts unless separately protected. Light-mode cognitive writes are redirected to `update_identity`, `update_scratchpad`, or `knowledge_write` instead of encouraging raw memory-file edits. A corrected cognitive redirect is advisory, while an ignored user-file root correction remains a blocking deliverable failure.

### Review delivery (retired Claude runtime)

The Claude Agent SDK gateway is fully retired (owner-consented, 2026-08-29): its one remaining job — the read-only api-route advisory — moved onto the review substrate as the bounded native inspection episode (`review_native_episode.py`): an in-process episode of at most the configured round cap of `chat(tools=…)` calls against a fresh instance-local inspection registry (read_file/list_files/search_code/query_code/vcs_status/vcs_diff only, `local_readonly_subagent` constraint, network/web off), every physical send ledger-accounted under the episode's one logical operation, caps failing closed with typed refusals. Mutating external coding work uses the delegated subagent path.

Review delivery has two closed route kinds in `review_execution.py`: `api_chat` and `agent_session`; vendor and harness names are route targets, not new kinds. A slot is bound to one immutable route before its first send and never falls back to another transport. The API executor lazily builds and memoizes the assembled review messages, so its durable prompt record and its at-most-two physical sends use the same bytes. One logical interaction may use one bounded second send for transport or empty-output recovery, never while a dispatched outcome is unknown; task acceptance may also spend that second send on malformed-format repair. Transport, parsing, extraction, and semantic verdict remain distinct.

The hosted-agent executor instead starts one read-only delegated session through the shared Claudexor nanny. The session receives route-owned instructions and retrieval pointers and uses its own tools; it does not assemble the API review pack. A conforming structured result is preferred when the live harness manifest supports it. Otherwise strict parsing runs first and a light extractor canonicalizes the already-collected transcript. Extraction never launches a second hosted session. Custody, cancellation, full-artifact recovery, capability deltas, and settlement stay on the same delegated transport contract.

Advisory availability is evaluated from the current configured slot and route, not inferred from a stale stored verdict. A disabled advisory slot is an audited bypass; an `api_chat` row requires provider credentials for its RESOLVED model (same-model payable-spelling fallback included), while `agent_session` requires a resolvable session route. If the commit advisory is unavailable, the commit gate runs its compensating hermetic preflight only when tests remain independently applicable: the caller did not explicitly skip them and the diff is not documentation-only. Other bypasses record why tests were skipped. Optional skill advisory remains fail-open with disclosure. Malformed structured slot configuration is refused at save and becomes a typed loud review-time failure for commit triad, scope, advisory, plan, and skill review. Task acceptance retains the explicit owner-approved residual: it uses the projected legacy/default API panel when that structured configuration is malformed. No surface silently chooses the opposite route.

### Usage ledger substrate vs. accounting policy

`usage_ledger.py` owns the durable append-only physical-attempt ledger: cross-process locking, sequence and transition validation, append+fsync, replay, and loud tail quarantine. `usage_accounting.py` is the one-way policy layer above it: route pricing, reservations, settlement, scopes, budget fences, imports, projections, and admission. The substrate never imports policy. This is a structural boundary around the monetary authority: a pricing or budget-policy change cannot redefine valid ledger storage, and a locking or repair change cannot silently change what an attempt costs. Compatibility events, state mirrors, task fields, and UI projections may carry attempt ids and derived totals but never become a second charge source.
### Delegated subagents (Claudexor transport + the nanny)

Children coordinate through `tree_note` and `tree_read`; only the parent may use `override_delegation_constraint`. A `review_requested` note carries an exact evidence reference/hash, wakes the waited/direct parent, preserves distinct typed concerns, and starts no reviewer or paid cycle. The parent/root may inspect it, hire an ordinary critic child, or let host-verified bytes enter the final root acceptance packet. Only the latter uses the acceptance wallet: immediately before reviewer transport the canonical root result atomically claims the complete candidate/evidence/fence binding under the effective cycle cap. A pre-existing claim without its terminal host run is typed unknown and cannot be re-dispatched. Both read-only and acting children can use the existing descendant-scoped `forward_to_worker`, `peek_task`, `cancel_task`, and `discard_child_result` controls for their own children; the durable ancestry check grants no unrelated-task reach. Workspace children retain scoped `knowledge_read` and `knowledge_list`, and recursive delegation never widens filesystem, budget, depth, deadline, commit or owner authority.

`OUROBOROS_SUBAGENTS` is the active task-actor SSOT. Its strict `{enabled, items}` value contains at most ten `ConfiguredSubagent` rows. Each row has a stable `subagent_id`, owner-authored English `recommended_use`, one normalized route (`api_model` or `agent_session`), optional effort and an optional session credential pin; the legacy `name` key parses and is dropped (retired), so the canonical serialization never carries it. The description is selection context only: host code never parses, ranks or maps its words to task text. API models and session harnesses therefore occupy one LLM-selectable list without pretending they have the same topology.

`schedule_subagent` requires `subagent_id`, a focused `objective`, and `expected_output`. Its remaining public fields describe child-local context, constraints, memory, capability needs, write surface, narrower deadline, delegation budget and acceptance claims. There is no model-visible `model_lane` or `executor` axis and no public `effort` override: the selected row is the complete execution choice. Lineage, workspace/resource bounds, parent cognitive route and remaining budget are host-derived. Acceptance claims belong only to that child; blank values normalize away and omission never inherits the parent's claims.

At scheduling, `subagent_runtime.select_subagent_snapshot` validates the exact id against the canonical enabled list and copies an immutable normalized snapshot into the child task: selected id, config fingerprint, description provenance, source, route, effort and selection time. Settings edits affect later children only. An `api_model` row becomes an ordinary recursive API child on that exact model/effort. Its executable model keeps the saved direct-provider `provider::model` spelling; only the canonical ` (local)` marker is removed for a local call, while slash-normalized identity remains comparison/telemetry data and never selects transport. An `agent_session` row becomes an ordinary recursive Ouroboros nanny whose exact external session route is bound by the same snapshot. Requested route/model/account/access and effective engine facts remain separate in custody receipts; an explicit pin is strict, while an empty pin delegates compatible-account rotation to Claudexor.

The old `model_lane`/`executor` resolver remains only for old durable records and the one compatibility window. A live legacy-only call is accepted only when its explicit selectors map deterministically to exactly one migrated configured row; omitted/ambiguous `auto`, zero matches or multiple matches return `subagent_selection_required`. Supplying `subagent_id` with legacy selectors is a typed conflict. For those historical lane envelopes, `schedule_subagent` reports the requested lane only; effective facts remain on the dispatched child record rather than being invented before dispatch. `subagents.resolve_subagent_dispatch` immediately hands a task carrying `configured_subagent` to `subagent_runtime`, so legacy Heavy/lane/executor policy cannot reinterpret an active selection.

The existing `delegation_budget` is the authority for recursion: explicit
`may_delegate=false` refuses every descendant admission, while
`may_fan_out=false` permits one direct child and refuses later siblings. Omitted
legacy flags remain permissive, and the host never treats a free-form intent note
as authority. An incomplete task-result scan makes the direct-child count unknown:
only an explicit fan-out/child cap refuses on that fact, while an unbounded legacy
budget remains usable. The same budget carries additive depth provenance for
`requested_depth`, `permitted_depth`, `attempted_depth` and host-visible
`achieved_depth`; absent explicit root provenance stays unknown, and vendor-internal
children do not advance achieved depth without a Claudexor boundary receipt. Root
acceptance carries the per-child facts and a host-attested depth summary; persisted
admission facts are monotonic authority and outrank later Settings changes, except
that the explicit global depth setting `0` disables every new descendant and the
immutable hard ceiling remains authoritative over malformed persisted projections.
External task ingress and supervisor queue admission accept only non-negative
typed depths; malformed or negative persisted rows are terminalized before
assignment rather than clamped.
A normal over-cap `schedule_subagent` attempt writes a typed rejected child result
carrying the same provenance, and a lower permitted depth is reported as
`capability_reduced` rather than a silent flat tree.

`wait_task` and `get_task_result` return the full single-child handoff. They include a disclosed, ten-row verification-receipt projection ordered with every still-outstanding red or masked pass first and the newest remaining receipts after it; the exact omitted count is carried. Readers union the recorded child-drive and canonical replicas with exact-row de-duplication and stable receipt chronology (undated legacy rows before dated evidence), so source iteration order cannot let an older PASS hide a newer FAIL during the pre-copy-back window. `wait_tasks` deliberately remains batch-compact: `task_id, status, cost_usd, child_result_sha256, outcome_axes, result, trace_summary, capability_delta when the child has something to disclose, duplicate_of`.

`wait_task` and `wait_tasks` use `task_status.SETTLED_STATUSES`; a pending cancellation is the typed `cancel_state: "pending"` projection (the legacy `cancel_requested` status is read-path only), never completion. `wait_tasks` checks unknown ids across task results, queue state, and the tree ledger, returns typed unknown rows plus a bounded roster of actual direct children, and short-circuits an all-unminted set after the 30-second registration grace unless an id becomes real. `wait_task`, `wait_tasks`, and `delegate_wait` may add an advisory cache-horizon note only when the latest recorded send applied an explicit `5m` or `1h` TTL and the wait outlived it; bare `default`, absent, and unknown TTLs stay silent.

**What a delegated run COSTS, and the one thing the ledger cannot see.** Claudexor
reports an amount in `summary.spendUsd` and its EXACTNESS in the sibling
`summary.spendEstimated`, and `delegate_custody.disclosed_spend` is the single reader of
the pair — it returns `(amount, estimated)` together so no call site can ask half the
question. `delegate_custody.settle_run` and `_terminal_payload` both go through it, so
the ledger row and the payload the nanny relays to its parent cannot tell different
stories. Runs ask for `authPreference: subscription` explicitly, because the engine's
default is `auto` = subscription-first WITH policy fallback to a paid key, and that
fallback is invisible to the host. FOUR cases, each recorded as what it is: a DISCLOSED
SETTLED zero settles at `0.0` with `cost_final=true` and leaves the projection final (the
free-session case this row kind — the `subscription_session` usage-ledger row — exists for); a disclosed settled charge rides the ledger
as money and is final; an ESTIMATED amount rides as money with `cost_final=false`,
because an estimated zero is not a proven free session and an estimated charge is not a
closed book; an UNDISCLOSED spend writes `cost_usd: null`, which drops `cost_final` for
the projection and increments `unknown_unmetered`. Token counts follow the same rule one
axis over: `delegate_custody.disclosed_tokens` keeps `None` for a count the harness never
reported, because `int(x or 0)` made a run that disclosed nothing indistinguishable from
one that genuinely used zero.

The DISCLOSED BOUND: an undisclosed spend contributes `0.0` to `accounted_usd`, because
there is no amount to charge and inventing a conservative bound would be fabricating a
number the harness never gave (BIBLE P1 — the gap is represented, never filled in). So a
`TOTAL_BUDGET` fence cannot stop delegated spend it was never told about; what it gets
instead is an honest loss of finality. Closing this needs a spend disclosure from
Claudexor, not a guess here.

An `agent_session` subagent is an ordinary recursive task-tree child acting as a
**nanny** that may supervise at most one active bounded external leaf at a time.
The task node keeps lineage, authority, deadline, budget, acceptance, cancellation
and the ability to create descendants;
the Claude Code/Codex/Cursor/Agy process remains a non-recursive tool leaf. This is
why session rows do not flatten the task tree into harness processes. The nanny is
the host: verification receipts stay host-authored and harness output is a claim to
check, never proof. An `api_model` row has no nanny/leaf split; that API child is the
recursive actor itself.

`gateways/claudexor.py` is pure transport. It reads the daemon descriptor for
`{host, port, tokenPath}` — `<config_dir>/daemon/control-api.json` under the owned
daemon's `CLAUDEXOR_CONFIG_DIR` (D30), falling back to the operator's own
`~/.claudexor/v3/daemon/control-api.json` when none is provisioned — negotiates
`POST /v2/handshake` with `X-Claudexor-Protocol-Major: 3`, and refuses an engine older
than `config.CLAUDEXOR_MIN_VERSION`. **Token custody:** the daemon bearer token grants
the ENTIRE `/v2` surface, so it is read, held, and used only inside this module — never
in a `ToolContext`, a child's environment, or a harness sandbox. The HTTP client runs
with `trust_env=False` so an ambient proxy variable cannot intercept the loopback
control plane.

Discovery remains pure I/O and retains the explicit/operator read path for compatible
callers. Production starts do not ask that gateway to install or spawn: the four
start/probe call sites first obtain a handshaken owned gateway from
`claudexor_daemon.ensure_owned_gateway`. Keeping that lifecycle above transport is what
lets account status stay side-effect-free and keeps harness-specific mechanisms out of
Ouroboros.

**Custody is durable, because the run is not ours to kill.** A delegated run lives
inside the daemon, survives our worker, and the bearer token means anything that can
name it can reach it — so custody in a module dict was custody that died with the
process, leaving a LIVE mutating run nothing could wait on, cancel or settle while the
dict refused the OWNING task itself. `ouroboros/delegate_custody.py` makes the AUTHORITY
the durable rows the event log already carried (`delegate_run_started` and friends,
written to the canonical/budget root so a child drive's pruning cannot erase them); the
module dict is a pure memoization of those rows. A lookup answers OWNED, FOREIGN, or
UNKNOWN — collapsing UNKNOWN into "not yours" is what made a restarted owner
indistinguishable from an intruder. Every INTENDED start mints a fresh logical
invocation id (`new_invocation_id`, a per-intention UUID) that rides the wire verbatim
as the `Idempotency-Key`; the content hash of (task, route, access, root, prompt) is
only the LOOKUP identity for finding a pending invocation, never the wire key — a
content-stable key would hand a deliberate re-run of the same prompt the finished OLD
run. Reuse happens ONLY by explicit token: a start whose outcome is unknown returns
`pending_invocation_id`, and a `retry_of` call replays the STORED canonical body
byte-identically under the SAME key, so the engine's replay check returns the run it
already accepted instead of starting a second one (a re-derived body would digest
differently and 409). A pending invocation whose owner died before the run row landed
is recovered by the sweep the same way — stored body, stored key. `reconcile_orphaned_runs`
settles or cancels every open run whose owning task is no longer in the supervisor's
live set — the SAME owner-is-gone predicate `process_custody.reap_orphaned_processes`
uses, because a delegated run has no pid for the process reaper to find. Its in-process
twin, `release_task_runs`, runs at the loop's own resource-release point (beside service
teardown and mailbox cleanup), so a terminalizing parent releases what it holds
immediately instead of leaving it mutating until the next sweep; the durable path still
covers the worker that dies before reaching its teardown. `maxSeconds` is damage
limitation, never custody.

**Nothing reports terminal or cancelled without a verified terminal receipt.**
`delegate_cancel` returns one of four typed outcomes: `confirmed` (the run reads back
terminal), `requested` (accepted, not obeyed yet), `failed` (a reachable daemon refused
while the run keeps mutating), and `containment_fault_run_may_still_be_live` (the
attempt could not be verified at all). The last two record a durable containment fault
that surfaces as a CRITICAL health invariant until a terminal receipt or a settlement
clears it — an overpowered mutating run that may still be alive is an incident, not a
reassuring string in a tool result. `cancel_and_verify` short-circuits on the SAME
`settled` fact `settle_run` does, and a refused control is never a verdict about the RUN:
the state read decides, so a run that had already stopped is confirmed instead of
faulted.

**A 404 is scoped to the daemon that answered.** `daemon_says_absent` distinguishes a
reachable daemon's explicit absence from transport ignorance. For a project registration,
that absence discharges the retirement obligation. For a run, it does not prove that an
older child vanished across owned-daemon reprovisioning; custody therefore closes the run
as `delegate_run_closed_absent` (unreachable, not settled) only after registration
retirement, without inventing terminal detail, usage, or spend. Shared project
registrations use the lowest run id as the deterministic retirement retry owner while
siblings defer quietly; a later project 404 discharges them. A daemon that cannot be
reached still produces a containment fault.

**Settlement follows the durable fact.** Two independent, idempotent obligations — the
ledger row and retiring a registration we created — and `settled` is claimed only when
both landed AND the settlement row itself landed. Writing it over a suppressed failure
turned a ledger-lock timeout or an unreachable daemon into a permanent leak, because the
retry could then never happen. `emit` returns `append_jsonl`'s success signal (the
codebase's own predicate for an important write) instead of discarding it: the rows ARE
custody here, so a start whose row did not land reports `started_uncustodied` with
`custody_durable: false` rather than a plain `started` over a run only this process can
name.

**A large result is delivered, not severed.** `finalSummary`/`primaryOutput` carry the
run's real work product and Claudexor returns a preview of up to 256 KiB, while the
generic tool-result cap head-truncates at 15k — which cut the terminal JSON mid-string
and turned a review verdict into an unparseable fragment that still looked like an
answer. `delegate_wait` therefore bounds ITSELF against `tool_capabilities.tool_result_limit`
(the same function the truncator reads), stages the whole terminal detail under the
task's own `task_drive/delegated_runs/<run>.json`, and returns a typed `output_delivery`
block: `complete`, `consumed`, total chars, the artifact reference with its line count
and sha256, and the `read_file(root='task_drive', …, start_line=N, max_lines=M)` recipe —
the existing owner contract, whose `start_line` is a stable cursor over an immutable
file, rather than a parallel artifact system. The cut fields are RENAMED to
`*_preview`, so a consumer reading `primary_output` gets nothing instead of a fragment
it would mistake for the whole answer, and the result is declared NOT consumed until the
artifact has been read in full.

**Project registration is a required step, not an optimization.** The first
`delegate_start` against an unregistered root is answered with HTTP 404
`project_not_registered`, so the nanny registers the root first. Claudexor has had
`RunScope.ephemeral` — a one-shot root that never enters the durable registry — since
3.3.0, but Ouroboros does not yet use it, so a registration WE created is retired when
the run settles; a pre-existing registration is left alone. Exception (#362): a
STABLE-target registration — a workspaceRoot-capable engine writing into the user's own
tree (`delegate_registration_policy.persistent_registration`) — is marked
`project_persistent` and survives EVERY retire path (settlement, the orphan sweep,
recovered-invocation refusals and the pre-run refusal path); its ownership duty is
discharged durably (`PROJECT_RETIRED` with `project_kept`) without deleting the project,
and any persistent sharer makes the shared project undeletable for its siblings.

**Daemon lifecycle is owned, explicit, and lazy.** Reading status, booting the app,
and `delegate_wait`/`delegate_cancel` never install or spawn anything. Connect,
`delegate_start`, reviewer-session start, and the real executor readiness probe call
the single `claudexor_daemon.ensure_owned_gateway` seam. Explicitly opening Agents
also wakes only an already-provisioned `stale` home after its side-effect-free status
read; it never provisions a first-time install. The seam foreground-installs or
repairs the exact reviewed target through `claudexor_runtime`, then starts the daemon
under Ouroboros's isolated `CLAUDEXOR_CONFIG_DIR`. A new package finds the same archive
in its immutable resources; an older package updated through managed Git downloads it
from the pinned public URL. The same pin carries exact official Node archives for every
supported host. An exact packaged Node wins for the daemon. A source checkout or older
package that lacks it downloads the review-bound platform archive into
`data/state/cx/node` and extracts the named executable; existing schema-1 daemon-only
and schema-2 CLI-capable node metadata remain valid for their pinned artifacts. The
preserved serving-role reader rejects malformed or future schemas with a typed failure.
When a CLI-capable pin is provisioned, POSIX
promotion additionally extracts the regular-file npm subtree, while the bounded Windows
lane remains daemon-only. The Node version and managed metadata are verified before
probing Claudexor. The CLI resolver requires that managed POSIX pair even when an
executable-only packaged Node exists, so the CLI's npm entrypoint cannot fall through to
the host. Node, engine, and owner-triggered CLI preparation remain one foreground
Connect/lazy-ensure transaction and neither path imports or modifies the operator's
personal Claudexor home. Every ensure
also runs the best-effort rotation reconcile (B3): GET the daemon's settings, then a
conditional POST defaulting ONLY absent per-harness limit actions to `rotate` — a
persisted policy is never overwritten, an A6+ engine that owns kind-aware defaults is
skipped, a real change leaves the durable `state/claudexor_rotation_provisioning.json`
receipt, and any failure simply retries on the next ensure.

An authenticated live daemon is useful serving state, not an update casualty. A new
pin is extracted and probed beside it, while the current process continues to serve;
the staged version becomes active only when that daemon next starts naturally. A
temporary staging failure is shown in runtime status but does not kill or replace the
live process. If no daemon is serving and the exact target cannot be prepared, start
fails with the runtime's typed reason. There is no fallback from a reviewed pin to an
arbitrary PATH install. `OUROBOROS_CLAUDEXOR_BIN` is the one explicit operator override.
The owned daemon remains session-scoped and stop remains own-only-if-self-started, so
an attached or foreign process is never killed. This lifecycle changes only daemon
delivery; delegated-run custody below still follows the durable run receipts.

External-terminal login recovery binds to that useful serving state, not to the staged
next-spawn pin or a PATH CLI. Before profile registration or setup-job creation, the
authenticated handshake's exact engine version, build SHA, and absolute entry locate
one preserved managed tree and its exact Node version. A fresh side-effect-free probe
of that same entry must repeat the handshake identity and advertise the additive
`setup_attach` role; a 3.6-era probe without `roles` remains readable but yields a typed
409 with no mutation, while a failed or identity-mismatched probe yields retryable 503.
The one resolved absolute argv is retained while the job is created, then receives the
job id and is rendered as inert POSIX or PowerShell text (`&` before PowerShell's quoted
executable), with the owned config root and inherited daemon-socket override cleared.

**Four nanny verbs** (`tools/delegate.py`) remain: `delegate_start`,
`delegate_wait`, `delegate_cancel`, and `delegate_answer`. There is deliberately no
fake `hurry`: current Claudexor exposes cancel and answers to a pending interaction,
not truthful arbitrary in-place steering. `delegate_start` now takes an exact
`agent_session` `subagent_id` (or recovery-only `retry_of`). API actor ids are refused
there and must be scheduled as recursive children. For a scheduled configured nanny,
`subagent_bootstrap.bootstrap_before_context` freezes the route, compiles the complete
work order, and — after recovery adoption and the durable zero-run/unknown-evidence
fences have had their say — STARTS the exact snapshotted leaf before the first model
round, through the same `delegate_start(prompt="")` wrapper the model itself would use
(charter, owner decisions 2026-08-28/29). The first model turn arrives with the live
run's startup receipt and is for judgment: supervise with `delegate_wait` when it wants
the run's facts, schedule parallel auxiliary children, publish evidence, or — when the
leaf could not start and its absence is proven — record a typed zero-run decision.
Root-direct bounded work and same-nanny replacement use the same `exact_start`
primitive with an explicit id. The physical leaf is never started from a host fallback
or from a coordination prefix. `delegate_start(subagent_id=..., prompt=...,
root="skill_payload", bucket=..., skill_name=...)` remains the orthogonal exact-resource
selector: the id chooses transport while the existing resource binding chooses payload
authority.

The configured-session work-order compiler has one total 250,000-character wire
budget. This is an Ouroboros serializer integrity bound, not a vendor model
context limit. Fitting orders are byte-complete. When the complete order is
larger, the host never sends a prefix: it records the full-order SHA/size and
starts only a route whose live manifest declares an interactive question channel
with a compact coverage=partial source-request lens. The external actor asks
for exact source character ranges, and its nanny answers through the existing
waiting_on_user/delegate_answer transport from the canonical-work-order projection
of `get_task_result` (the same renderer the host validates).
The manifest observation is a point-in-time preflight, not a lease: the route may
lose its interaction capability before the later start POST. The probe is not
delivery evidence; only durable verified source-range coverage authorizes completion,
so a raced run remains `cannot_verify` and patch application stays refused until
coverage is complete.
An unavailable or unverified channel returns a typed source-channel refusal before
POST; `cannot_verify` remains the distinct verdict for incomplete interaction
evidence after a run exists. Crash recovery replays the durable compact request body and the full-order
fingerprint rather than recompiling the task. The request and the union of
host-verified source intervals are carried in delegated custody and survive replay.
`delegate_answer(source_response=...)` verifies the canonical selector, full digest,
range bounds, and exact rendered bytes before appending a receipt. A retry that receives
`already_resolved` may repair a lost receipt only when the durable delivery receipt proves
that the same interaction and exact source bytes previously returned `delivered`; a
timeout or unrelated earlier resolution remains `cannot_verify`. A terminal run
whose intervals do not cover the complete brief is typed `cannot_verify`; its
captured patch may be rejected but the existing integration seam refuses apply.

Live progress uses Claudexor's additive `textKind`/`textDelta` facts to join adjacent
text fragments of the same kind, attempt and harness without inserting punctuation
or changing whitespace. Complete messages, tools, statuses and legacy rows retain
event boundaries. The bounded preview uses the text body, with its existing omission
disclosure; cursor, wake and terminal-result semantics are unchanged.

`delegate_wait` is model-visible as an **event-only sleep**, not a caller-sized poll.
`delegate_supervision.supervised_wait` renews its low-level bounded transport windows
inside host code. Journal cursor advances continue streaming to the human and update
the durable cursor but do not return to the model. A meaningful terminal settlement,
new interaction, containment/transport/custody fault, addressed owner/task message,
direct-child attention beacon, direct-child terminal transition, cancel/deadline
control, or recovery judgment becomes a coalesced durable pending wake; that wake is
injected and acknowledged once, and is replayed after a worker interruption until
acknowledged. If the combined wake exceeds the model-visible result bound, the host
returns valid bounded JSON plus a hash-verified actor-readable source reference to the
exact full wake; failure to stage or deliver that reference leaves the pending wake
unacknowledged for replay instead of advancing its cursor. Child signals are filtered to the current task's direct lineage and
reuse the existing coordination cursor, pending-wake and acknowledgement records,
so sibling/vendor-internal activity does not create a mesh or a second event ledger.
Quiet renewal emits supervision telemetry and makes zero LLM calls. The existing
external-wait lease still protects legitimate host-side silence from the idle rail;
deadline, absolute ceiling, budget and cancellation remain outer bounds.

Main-loop model cognition is a separate typed in-flight fact. Immediately before
entering each exact provider-call seam (including its deadline-bounded model-slot
wait), the worker sends a direct supervisor `started` event bound to task attempt,
execution, round, call id and retry attempt; every success, empty or failed response
and accounting failure sends the matching terminal fact before return or retry
backoff. The supervisor keeps only that process-local active row in
the existing `RUNNING` metadata. It has no elapsed-time expiry and is consulted only
by the idle predicate: `OUROBOROS_LLM_TRANSPORT_READ_TIMEOUT_SEC` (2700 seconds by
default) remains a configurable dead-socket bound, not a cognition deadline or stall
detector, while explicit task deadline, budget, cancellation and the absolute ceiling
remain independent hard axes. A stale terminal from an earlier retry or task attempt
cannot clear the current row.

The cached OpenAI-compatible clients, the no-proxy per-call clients, and the
web-search OpenAI clients are built on one shared transport factory
(`net_transport.py`) that sets platform-guarded TCP keepalive socket options,
so a NAT/VPN mapping silently dropped during a long silent reasoning stretch
is detected by kernel probes instead of hanging until the read timeout: on
Linux/macOS the probe timing is tuned to detect within minutes, other
platforms get `SO_KEEPALIVE` with OS-default timing. The cached-client
transports also carry SDK-equivalent pool limits (an explicit transport
ignores Client-level limits). When any proxy httpx would honor is configured
(HTTP(S)_PROXY/ALL_PROXY env vars, macOS SystemConfiguration, the Windows
registry — mirrored via `urllib.request.getproxies()`), the cached and
web-search clients skip the explicit transport (httpx env-proxy mounts
require it absent) — disclosed residual: proxy-routed installs run without
keepalive tuning, as do httpx builds predating the transport
`socket_options` parameter (< 0.25). Further residuals without these socket
options: the native Anthropic `requests` session and the anthropic web-search
client, the GigaChat library client, and the short-lived `llm_probe`
ephemeral probe clients.

The configured-session startup/recovery receipt and every newly minted meaningful wake
also carry one host-rendered `coordination_context`: the complete parent-authored advisory
`delegation_budget.intent_note`, explicit-deadline time remaining, known/partial/unknown
root-tree settled and accounted spend, active host-visible descendants, and the
canonical root's remaining paid acceptance capacity. Vendor-internal descendants are
explicitly opaque. These are facts for LLM judgment, not thresholds or a scheduler;
replay returns the stored snapshot byte-for-byte, while the next acknowledged wake
recomputes it from the existing authorities. If the combined wake exceeds the tool
budget, the complete context stays in the existing exact wake source and the bounded
envelope carries a typed source-only projection. Active descendants are known only
from a fresh queue snapshot plus the targeted parent chains of those live rows; stale
queue state is unknown and unrelated historical corruption does not poison the subtree.

The nanny may deliberately request one future inspection by supplying both
`checkpoint_after_sec` and a free-text `checkpoint_reason`. It wakes once at that
time, or an earlier real event consumes the checkpoint. Every later inspection needs
a fresh model decision. There is no repeating cadence, journal-progress wake, host
stall classifier, or hidden polling loop. On a wake the nanny retains its full normal
tool surface and inherited parent model/effort route. Its role contract is to inspect,
coordinate, answer, wait, cancel/replace, evaluate, integrate or explicitly create a
different child — not to implement the same healthy leaf's assignment in parallel.
That semantic boundary is prompt/review/receipt enforced; host code does not reduce
the nanny to a controller allowlist.

**Recovery is exact and cause-specific, not generic task resurrection.** A proven
non-signal worker crash may reserve the same task id as a recoverable successor before
requeue; the custody orphan sweep receives that narrow fence, and the successor adopts
the exact run or pending invocation before any LLM call or new start. A planned agent
self-restart first persists `delegate_restart_transaction_prepared` for each exact
sleeping/wake-pending nanny, including config/work-order/authority/worktree/run or
pending-invocation/cursor/message/interaction/checkpoint fingerprints. The launcher
acknowledges the expected exit-42 transaction, and startup pre-adopts those handoffs
before ordinary custody cleanup. Adoption is durably recorded before supervision
continues; a terminal observed in the gap settles once.

A configured-session physical start — host pre-start or model-issued — proves its
actor/work order against the task-start snapshot. For a root-direct bounded start, or for a same-nanny replacement after
the predecessor is terminal and any captured physical result is disposed, the one
current durable custody holder is the immutable actor/config/work-order authority
for handoff. Task authority and the holder's exact
worktree/run binding are still re-proved, and zero or multiple current holders refuse;
recovery never reselects from mutable settings or issues a new semantic start.

Owner restart, panic, external or worker signal, deadline/timeout, explicit cancellation
and abrupt whole-app loss are explicit no-resume causes: they cancel/settle instead of
claiming continuity that was never handed off. Any missing, conflicting, vetoed or
ambiguous binding returns typed recovery-required/reconciliation and never starts a
duplicate mutator. This adds no public `PARKED` state: the task remains ordinary
`RUNNING` in one worker while sleeping. Key supervision telemetry is
`delegate_supervision_wait_entered`, `delegate_supervision_wait_renewed`,
`delegate_supervision_wake_pending`, `delegate_supervision_wake_replayed`,
`delegate_supervision_wake_acknowledged`,
`delegate_supervision_checkpoint_scheduled` and
`delegate_supervision_checkpoint_consumed`; recovery emits
`delegate_restart_transaction_prepared`,
`delegate_restart_transaction_acknowledged`, `delegate_recovery_pre_adopted` and
`delegate_run_adopted`.

**A run's question is the nanny's to answer** (owner decision 7=A). The engine's
run detail carries `pendingInteractions` — the full question text, header, options
and `multi_select`, read by the ONE normalizer `gateways.claudexor.pending_interactions`
— and supervision wakes IMMEDIATELY with a typed `status="waiting_on_user"`
payload when a NEW interaction appears, instead of burning the engine's answer timeout
in dead metered polling. An oversized
question set spills WHOLE to `task_drive` with a sha256/size receipt and a counted
bounded preview inline; the answer keys (`interaction_id` / `question_id`) ride
WHOLE, never truncated, because they are echoed verbatim into `delegate_answer`.
Supervision records delivered interaction ids durably and acknowledges them only after
transcript injection, so a question the nanny already received neither re-triggers a
model round nor disappears across worker recovery. The engine's interaction timeout
(benign decline; the run continues
on stated assumptions) is the backstop only for a question that CARRIES a
`timeout_at`: a null `timeout_at` means no automatic expiry — the run waits until
answered. `delegate_answer` is custody-gated like
cancel and relays the engine's OWN typed outcomes (`delivered` /
`already_resolved` / `not_found` / `rejected` — the last only for a
payload-semantic 4xx: 400/409/413/422); a spent subscription window is the
distinct `subscription_window_exhausted` outcome carrying `reset_at`; an
ambiguous transport becomes
`delivery_unknown` with a re-read of the detail, and a different answer is never
auto-retried. The policy the nanny carries: answer from the task context it holds;
a question above its authority (money, scope, external actions) rides the
escalation verb to its nearest LIVE ancestor (the owner sees a quiz card only
when no ancestor answers) while the nanny keeps waiting. Hosted REVIEW slots are
non-interactive by contract, so their poller handles a parked question
conditionally: a question whose engine expiry provably lands before the slot
deadline is waited out on the slot's own clock (the engine benign-declines and
the session resumes); otherwise the slot terminates early and typed
(`review_session_waiting_on_user`, cancelled through the verified-cancel path
with the outcome reported honestly — "host-cancelled" only on a verified
receipt, and a verify read that finds the run already succeeded consumes that
terminal as the slot's ordinary result). The codex lane has
no mid-run channel: a terminal with `outcome_facts.reason=input_required` is
answered by a plain NEW `delegate_start(subagent_id=..., prompt=...)` carrying the
assignment plus the answers —
never the engine's rerun/decision verb, which would start a run outside this
task's custody trail. `delegate_answer` is deliberately absent from
the configured session bootstrap's required tool set: a nanny without it is degraded
(a `timeout_at`-bearing question benign-declines; one without waits), never
custody-broken.

**Configured dispatch is exact.** `subagent_runtime.resolve_configured_actor_dispatch`
reads only the immutable task snapshot. An API row resolves to the exact API
model/effort and an ordinary recursive child. A session row re-checks the exact saved
route and resolves to a nanny on the parent's captured cognitive model/effort; it does
not force Light. Disabled/malformed/unknown/unavailable choices return a typed reason,
current alternatives and any known reset time with `host_fallback: false`. The host
never ranks alternatives, changes session work to API/native work, or picks the first
healthy row. Claudexor may rotate compatible accounts inside one unpinned route; that
is credential transport, not a different actor choice.

For a configured session row, the child's execution substrate was decided by its
PARENT: selecting an `agent_session` row IS the typed LLM decision that this work
executes on the harness, so the host executes that choice by construction and the
nanny never re-decides it (charter, owner decisions 2026-08-28/29). The WHY is
recorded so the next redesign cycle does not undo it: the typed parent choice is the
FLOOR the host hardcodes — truth, money, authorship — while topology, decomposition
and supervision judgment remain the model's CEILING (BIBLE P13, P5: code executes a
typed LLM decision, it does not choreograph cognition). Bootstrap therefore starts
the exact snapshotted leaf BEFORE the first metered round through the same
`delegate_start(prompt="")` wrapper the model itself uses — one start path, one set
of refusal shapes — after recovery adoption (first) and the durable
zero-run/unknown-evidence fences (second: a fence may hide a live prior run, so a
fence-wake outranks every terminal, blocked dispatch included). The host never waits
inside bootstrap: a live run — freshly started or adopted — hands the model its
first round immediately with a `configured_session_started` receipt carrying the run
id, and waiting is the model's own `delegate_wait` decision, so owner messages,
hurry controls, loop checkpoints and parallel auxiliary children (critics,
follow-ups) stay live during the whole run. A blocked dispatch or a DEFINITE start
refusal — a typed refusal with no custody handle whose reason is in the closed
`subagent_bootstrap._DEFINITE_UNRUN_REASONS` set or the access-profile mismatch
family — ends the child UNRUN and typed at
$0 through the existing `executor_blocked_outcome` (`agent.py` fills `cap_info` from
`ctx._configured_startup_refusal`); everything ambiguous — any custody handle,
`started_uncustodied`, unknown codes, unparseable output — wakes the model instead,
because a false "spent nothing" terminal over a possibly-live run is the one
direction the classification must never fail toward.

When no physical run exists and none can be started, the model may finish with a
typed zero-run receipt through `verify_and_record` carrying `zero_run_decision`
(`incomplete` or `unknown` — the WRITE enum, `ZERO_RUN_WRITE_DECISIONS`; the READ
enum additionally keeps historical `complete` rows valid so old receipts still fence
a second physical start, while the terminal projection degrades them to `unknown` +
disclosure — reason `historical_zero_run_complete` — never clean) and a
`zero_run_basis`. The canonical custody root must first prove that no open run,
ambiguous start invocation, or undisposed physical result remains. Once durably
recorded, the decision is terminal for that actor; a later physical start is refused
instead of contradicting the receipt. If no valid terminal row survives but the
receipt store is malformed or unreadable, the authority is typed unknown and a
physical start is likewise refused; the narrow zero-run tool can re-ground it, and
child copy-back never launders the corrupt source through a whole-file rewrite. A
valid terminal zero-run row still wins over unrelated malformed rows.

A session actor's terminal is CLEAN only through its own physical leaf
(a SUCCEEDED run or an adoption — a start merely accepted projects incomplete/unknown)
or a durable typed zero-run receipt. "Completed direct child ⇒
clean" is deleted: host children are auxiliary evidence, and the unresolved fact
(`physical_leaf_not_started`, with `direct_child_statuses` carried alongside) rides
the terminal projection as an incomplete/unknown execution axis. This remains an
LLM-first affordance, not a host topology/state machine: host code does not choose a
number or order of children — it executes the parent's typed substrate choice and
reports the truth. The canonical work order remains byte-complete and hash-bound;
any coordination context is additive and separately disclosed (the host's own
pre-start sends none), and an appendix over the instruction-field budget refuses
before provisioning rather than sending a truncated hash-mismatched prefix. When a
start or recovery actually occurs, the custody-durable receipt injects the exact
run/route facts and the actor supervises with the existing wait.
`started_uncustodied` is a startup custody fault, not a healthy third state: the
exact run/invocation is surfaced, quiet supervision does not begin, and no
replacement may start until the original run is proven absent or terminal and any
captured physical result is explicitly disposed. Recovery replays its stored
canonical request and idempotency key rather than issuing a second semantic start.

The public `delegate_start` and configured actor bridge share the same exact route-health and
start primitive. A retry must replay the already-bound invocation; a scheduled nanny
uses its task snapshot; root-direct or same-nanny replacement supplies an explicit
session `subagent_id`. No path chooses a first/healthy alternative. Tool visibility is
checked as part of configured bootstrap: a selected session whose required custody
verbs are unavailable returns a typed startup refusal. It never triggers the historical
preflight-native fallback or a second lane/model resolution.

`subagents.route_health` is that ONE health reader for ALL consumers — the
dispatcher, the nanny's own `delegate_start`, and the review slots
(`review_execution`, `plan_review_runtime`) — and under the charter it no longer
guesses admission (owner decisions 2026-08-28/29, «статус обманывает»). The harness
row's aggregate doctor `status` is NOT a refusal: it describes the DEFAULT
credential store while real accounts live in the engine's credential-profile pool,
so a pool-only harness read "unavailable" forever and blocked routes the engine
itself would admit. Admission belongs to the engine: a genuinely empty or exhausted
pool answers the start POST with its own typed refusal (INV-135
`credential_pool_exhausted` + earliest reset), which under the pre-start charter
costs $0 and zero model rounds. The row's `enabled` field IS still honored for
unpinned routes as `route_disabled` — the engine schema defines it as the OWNER's
settings toggle ("routing excludes it regardless of doctor status"), not an
observation; a pinned profile keeps its historical skip. The engine's belt
capability row (`delegation.available` — MCP injection for Claudexor's OWN delegate
strategy) is not consulted: Ouroboros runs never request the belt. The refusals that
remain are structural: `route_not_in_capability_catalog`, `route_disabled`
(unpinned), an access-profile mismatch, `engine_rejects_delegated_marker`, and
positive quota exhaustion for the route's own model. Review slots inherit
"the engine decides": a degraded-status reviewer slot now reaches the engine and
gets its typed refusal — never a silent fallback onto metered api spend. On the
`auto` lane the consequences split by layer: a dead DAEMON
(`ClaudexorUnavailable`) and an owner-disabled harness keep the native fallback
with its visible marker, while "daemon alive, pool empty" is now discovered at the
engine — the dispatched nanny learns the typed refusal there and does the work
natively with disclosure instead of being pre-refused on a status the pool
contradicts.

**Model-visible selection is a bounded semi-stable catalog.**
`subagent_runtime.model_visible_subagent_catalog(settings)` projects every saved row
in owner order, verbatim description included: stable id/name, route class, requested
model or session target, requested effort, and automatic-versus-pinned account policy.
It also carries the config fingerprint, source, LLM-first selection guidance and the
exact-start-or-typed-refusal dispatch contract. Invalid, undecided/unsaved, disabled or
empty configuration projects nothing, matching new-id dispatch authority rather than
advertising an actor the scheduler would refuse.

`current_model_visible_subagent_catalog()` reads the current saved settings plus the
task-start-normalized legacy environment overlay, and `context._capture_context_core` serializes the ordered JSON
under `## Available subagents` in the semi-stable context block before memory/knowledge.
There is no fresh Claudexor probe and no host ranking on each LLM round. Dated reviewer
and delegated-run observations remain in the dynamic `capabilities["delegation"]`
projection with the existing `historical, not live health` disclaimer; the retired
singleton `configured_route` fact is gone. Dispatch/start remains the authoritative
live check and returns current typed alternatives/reset evidence. A failure building
either projection drops only that fact, not the surrounding context.

**Nanny economics are structurally quiet.** The physical leaf is already live when the
nanny's first metered round arrives (pre-start above), so those rounds exist
for judgment, and the pressure machinery measures exactly that: the burn baseline
resets ONLY on real acts of delegation (`delegate_start` / `schedule_subagent`),
supervision verbs (`delegate_wait`/`delegate_answer`/`delegate_cancel`) advance the
round baseline while dollars keep accumulating, and coordination verbs are observed
for the reminder's phrasing but never buy metered silence (`nanny_pacing`, charter
2026-08-28). The machinery is armed whenever the harness was requested —
`_nanny_route_dispatched` = a configured `agent_session` row OR
`executor == "harness"` — including a blocked resolution: for a blocked start that is
moot (the task terminals unrun at $0), but a mid-run failure keeps the
reminders/nudges/chip alive on the wake loops. While the leaf is healthy, supervision
performs zero LLM calls regardless of journal activity. On a real wake the nanny uses
the parent's captured model and effort, with full ordinary reasoning power,
so difficult acceptance and recovery are not forced onto Light. The work-order fields
ride host-authored instructions and retry replays the stored body byte-identically.
Metered nanny calls, overlapping tool work, descendants and delegated spend all remain
visible in the ordinary usage/custody receipts; observability and review expose
co-building rather than a host semantic classifier trying to prevent it.

At completion, durable evidence still separates selected intent from execution fact.
`actual_substrate` (`harness_used` / `harness_attempted` / `native_only`) derives only
from custody rows; unreadable evidence yields no zero-count/native claim. Requested
and effective harness/model/account/access, start/settlement states, disclosed or
unknown spend, patch disposition and output-read completion remain queryable. A typed
startup refusal or selected route failure does not authorize productive work on a
different substrate. Another session route is an explicit exact start; API fallback is
an explicit separately visible recursive child selected by an LLM.

**Read-only and mutating session rows share one nanny transport.** The only
difference is the run shape, and the shape has ONE owner —
`subagents.delegated_run_shape`. For the ordinary workspace path it answers a single question —
is this an acting child? — asked of the live `ToolContext` by
`tools/delegate._derive_authority` (`tool_access.active_tool_profile`) and of
the configured task snapshot by `subagent_runtime.resolve_configured_actor_dispatch`
(`tool_access.predicted_subagent_profile`); neither reassembles the shape,
because a profile changed in one place and an isolation or a marker left behind in the
other is silent and unsafe in exactly one branch. The EXACT-RESOURCE lane is the
second, explicit entry: `delegate_start(subagent_id=..., prompt=..., root="skill_payload", bucket=..., skill_name=...)`
selects one installed user-managed skill payload, including a physical native
payload without `.seed-origin` through its logical `external` source, authorized through a fresh
`ResolvedResourceBinding` for `skill_payload.write` (top-level task profiles only)
rather than through the acting-child question.

| task authority | access | mode | isolation | `execution.delegated` |
|---|---|---|---|---|
| acting subagent (valid write surface) | `workspace_write` | `agent` | `live` | `true` |
| ROOT of an external-workspace task (validated active workspace) | `workspace_write` | `agent` | `live` | `true` |
| top-level task selecting an exact skill payload (`root="skill_payload"`) | `workspace_write` | `agent` | `live` | `true` |
| anything else, including a fail-closed subagent | `readonly` | `ask` | envelope (default) | not sent |

WHERE a mutating run's changes are destined and how they travel is the second,
separate record — the unified host-derived **mutation authority**
(`tools/delegate._mutation_authority` / `_payload_mutation_authority`), never
model-supplied:

| source | `target_root` derivation | `capture_mode` |
|---|---|---|
| `acting_constraint` | the child's own `task_constraint.write_root`, required to equal the genuinely ACTIVE workspace root | `delegated_snapshot` |
| `external_workspace_root` | the root task's validated active external workspace (a root holds no acting constraint — owner 2=A: it already holds write+shell inside the project; the prior gap was per-run provenance, which the snapshot+explicit-apply below records) | `delegated_snapshot` |
| `skill_payload` | the exact payload the fresh `skill_payload.write` binding resolved; the durable record also carries the semantic `resource_ref` (source/skill_name/CAS baseline hash) that retry and apply re-resolve | `delegated_snapshot` |
| `readonly` | ordinary active root (nothing to write) | `none` |

A payload target gets a STANDALONE private Git snapshot
(`subagent_worktrees.provision_payload_snapshot`): the live payload is never
initialized as Git; the loader-visible inventory is copied out, committed as a
synthetic baseline whose commit/tree identity is durably recorded in the
host-owned snapshot registry, and the run is scoped there. Capture trusts NOTHING
under the child-writable snapshot's `.git`: symlinked Git metadata is a typed
refusal, and the diff is built in a parent-owned control GIT_DIR with a fresh temp
index seeded from the registry-recorded baseline commit (child `.git/index` and
`.git/config` are never read or written — a child-forged index-only blob does not
exist for the capture). Both the baseline commit and the capture stage RAW bytes
(`stage_raw_payload_inventory`: `hash-object --no-filters` + `--index-info`, so a
`.gitattributes` eol/clean filter is inert content), regular modes are pinned to
baseline/100644 (an executable-bit flip never rides), a non-empty patch whose
result loader hash equals the baseline is a typed `unreviewable_metadata_change`
refusal, and after a real apply the live loader hash must equal the recorded
result hash or the run fails typed with its apply intent left PENDING (ambiguous
recovery) while the stale-extension reconcile marker is still queued — the
payload DID mutate — with no success or disposition recorded. At disposition
the apply is a LIVE, index-free `git apply`
into the non-Git payload guarded by a whole-payload content-hash CAS (drift =
typed conflict; identical content = idempotent applied), with reserved
lifecycle/control paths and escaping-symlink candidates refusing the WHOLE apply;
a successful apply QUEUES the extension reconcile request and the skill's
existing review becomes STALE for the new content hash.

**A mutating run executes in a PRIVATE EXECUTION SNAPSHOT, never in the shared
tree** (C1, owner 3=A — metered children keep sharing the tree; only delegated runs
are isolated). At `delegate_start` the host snapshots the target's REAL current
state — tracked + staged + eligible untracked, with the same sensitive/credential
veto the workspace-patch capture applies, DECIDED BEFORE ANYTHING IS HASHED (a
blanket `git add -A` would write a blob for every untracked file, `.env` included,
into the object database the execution worktree shares) — into a synthetic baseline
commit pinned by a `refs/ouroboros/delegated/` ref, checks out a detached worktree of it under the
subagent-worktree root (`subagent_worktrees.provision_execution_snapshot`). On
Claudexor `3.8.1+`, `scope.root` remains the stable authority target used for project
identity/config/history while `execution.workspaceRoot` names that one-shot snapshot;
the strict `3.8.0` wire stays byte-compatible by using the snapshot as `scope.root`.
The typed binding
`{target_root, execution_root, baseline_sha, authority_source, snapshot_id}` is recorded durably on the
custody request/start rows BEFORE the POST, the canonical request carries it, and an
engine whose strict schema supports the split receives the snapshot separately.
An explicit retry reproduces it exactly (a GC-collected snapshot is a typed
`execution_snapshot_missing` refusal, never a re-mint; a stored PRE-C1 mutating
invocation, whose recorded body scopes the run at the LIVE tree and carries no
binding to reproduce, is the typed `retry_binding_absent` refusal — the in-place
regime is not resurrected by a retry). The run still runs `live` —
in place FROM THE ENGINE'S view — which is why the scoped-HOME/`delegated` marker
below still applies unchanged.

At terminal, `delegate_wait` captures the run's diff against the baseline durably
into the task's artifact store and reports it as the `workspace_capture` block, and
NOTHING reaches the target automatically: the nanny EXPLICITLY applies or rejects
through `integrate_delegated_patch`. That orchestration — capture, explicit
disposition, durable rows, snapshot custody — is UNIVERSAL across both lanes. What
differs is the staging substrate. A GIT workspace target captures through
`write_workspace_patch_artifacts` (sensitive veto, binary/mode handling, sha256
manifest) and applies under the repo git lock: it PROVES first that no touched path
drifted from `baseline_sha` (a scratch index seeded from the baseline tree — a
plain `git apply` relocates hunks by offset, so a moved target would otherwise be
patched at a shifted position), then applies to the working tree and STAGES the
paths that exist or are indexed (a deleted UNTRACKED file has nothing to stage),
writes a verdict artifact and a durable disposition row — staged, never committed.
A SKILL-PAYLOAD target captures through the payload adapter over a parent-owned
trusted index (`_write_payload_patch_artifacts`, below) and applies LIVE into the
non-Git payload under the whole-payload content-hash CAS — nothing is staged into
any active root, no `.git`/index/staging is created in the payload, and a
successful apply queues the extension reconcile while the skill's existing review
goes stale pending a fresh `skill_preflight`/`skill_review`. Touched paths are
read NUL-safely from `git apply
--numstat -z` in BOTH directions (git-apply names only the paths a direction
writes, so a rename's source appears under `-R`). The Ouroboros protected-path gate
applies only when the target IS the Ouroboros body (no active workspace, or a
`self_worktree` surface) — a foreign project's `ci.yml` is that project's file, the
same way the shared-workspace branch of `integrate_subagent_patch` never gated it.
Review sees exactly the run's own diff and receipts carry real per-run authorship —
the Applied·review-blocked class is closed. A conflict (proven drift) is owned by
the (still-running) nanny: the snapshot and the captured patch persist until an
explicit resolution or discard. CLEANUP FOLLOWS THE DURABLE ROW, not the attempt:
an unwritten disposition returns typed `INTEGRATE_DISPOSITION_UNWRITTEN` and keeps
both, and a successful apply whose staging failed returns typed
`INTEGRATE_APPLIED_UNSTAGED` instead of claiming a conflict. Mutation itself rides
an APPLY-INTENT PROTOCOL (CR1): a durable `delegate_run_patch_apply_started` row is
written — after the protected-path checks, before any tree mutation — and every
provably-non-mutating outcome (lock error, baseline drift, apply failure, verified
revert) resolves it with `delegate_run_patch_apply_resolved`, while a successful
disposition retires it via the disposition row; an intent row that cannot be
written refuses to mutate (`INTEGRATE_INTENT_UNWRITTEN`). On replay, a pending
intent without a completed disposition means the tree MAY already carry the patch:
both decisions answer typed `INTEGRATE_DELEGATED_APPLY_AMBIGUOUS` instead of
guessing (pre-CR1 a restart forgot the in-process flag and a reject could falsely
claim "not applied" over a modified tree). The owner exit is explicit (CR2): re-run
`integrate_delegated_patch` with `acknowledge_ambiguous=true` after inspecting —
the pending intent is durably resolved as `owner_acknowledged` and the NORMAL
guards re-run from scratch (apply re-proves baseline drift, reject re-checks the
ready manifest and preserves the patch artifact); the flag is a no-op when nothing
is pending. Split-drive visibility (CR1): the capture lives on the canonical
drive, and `artifacts.delegated_capture_read_target` narrowly rebinds
`artifact_store` READ operations for the owning task's own `delegated_runs/`
prefix to the canonical root, so a child-drive nanny can inspect the patch it must
dispose without any widening of write authority. The startup GC removes
only snapshots custody proves closed (disposed, or a definitively refused start),
cross-checking open runs AND pending invocations. A read-only child has nothing to
write back and stays in Claudexor's default envelope — `execution.isolation='live'`
is agent-only and a non-agent run carrying it is refused at the boundary — so this
is one transport with one derived difference, not a second pipeline and not a second
slot. (Historical: before v6.98 a mutating run edited the nanny's own worktree IN
PLACE and rode out inside the nanny's own workspace patch — that regime produced
blind union diffs and is retired for delegated runs.)

**Terminal reconciliation captures only over PROVEN terminality, and
`patch_captured` means "a usable artifact exists".** When the OWNER task is gone
(orphan sweep, kill-path reconcile, in-process release), a settled mutating run's
diff is captured through the SAME drive-rooted primitive the nanny path uses
(`delegate_integration.capture_terminal_patch_for_drive`) — capture only, never an
apply: the apply/reject DECISION stays with an owner, and the pending obligation is
visible on the health surface (`delegate_custody.undisposed_patches` → "DELEGATED
PATCH AWAITS DISPOSITION") until the durable `PATCH_DISPOSED` row clears it.
Pending-invocation recovery carries the FULL snapshot binding into the recovered
run's STARTED row, so the startup GC — whose predicate is settled && patch_disposed
— never deletes the snapshot holding the child's only work. Capture is EAGER only
where a terminal receipt PROVES the run over (the `is_terminal(detail)` settle
path, and a cancel whose read-back verified a terminal state); a run closed on the
ABSENT branch (daemon 404) or left open as UNREADABLE captures NOTHING — across the
owned-daemon provisioning boundary the child may still be alive and writing, and an
eager capture there would freeze an incomplete patch and serve it forever. Instead
the snapshot stays preserved (undisposed → the GC keeps it), the health line words
the state truthfully ("changes captured" only when `patch_captured`; otherwise
"work preserved … captured at disposition"), and `integrate_delegated_patch`
performs capture-on-demand through the same core BEFORE applying or rejecting —
disposition is the retry point for a capture that failed earlier. A capture that
fails at disposition returns typed `INTEGRATE_DELEGATED_CAPTURE_FAILED` for BOTH
decisions (an escaping capture-core exception included); no disposition is
recorded and the obligation stays open. The capture core mints the durable
`patch_captured` row only over a manifest whose OWN status is ready
(ready_with_changes / ready_no_changes); a manifest reporting its own failure is
returned as the failed block but leaves the row uncaptured, so every retry point
(re-wait, sweep, disposition) stays open, a pre-existing durable row over a failed
manifest is re-checked and re-captured on replay rather than trusted, and the
reject branch re-checks the manifest before releasing the snapshot (rejecting a
READY_NO_CHANGES capture stays legitimate — nothing to lose).

**The stored `delegated_runs_unreconciled` projection is healed only from the
write side, at three seams.** Readers (`get_task_result`, task details, the
retry-lineage merge) serve the stored projection — projection-over-replay, no
live custody join — so a run settled AFTER its task's terminal write leaves the
stored row lying until a write-side refresh: the periodic sweep refreshes the
tasks named in its own reconcile outcomes (nanny-leaf S1); the boot backfill
(`delegate_terminal.backfill_terminal_reconciliations`, once per server
generation, after the startup orphan reconcile) re-audits every stored TERMINAL
row still carrying a non-empty disclosure under one shared custody snapshot —
the generation-crossing residual no outcome-driven refresh can reach; and the
kill paths clear a stale list — the running-kill cancel write and the reaper's
terminal/retry writes carry the fresh audit UNCONDITIONALLY (a clean audit
writes `[]` in the caller's own terminal write), while the fast already-settled
kill lane, which performs no terminal write of its own, runs the same guarded
refresh (`trigger=kill_path_clear`; it touches only a row that exists with a
non-empty stored list, so a fresh-task kill can never mint a row or pay a
second write). Finalize-on-miss has two branches: the newly-cancelled branch
stores the audited list AND the reconciliation envelope in its one terminal
write, while the already-settled branch — like the reaper's self-finalized
branch — runs the same guarded `kill_path_clear` refresh. Only the GR6-1b
settled-before-capture short-circuit and the natural-completion re-check leave
the stored row byte-identical by mandate (GR7-2); a stale settled row raced
into those two lanes heals on the next boot's backfill.
Every refresh is audit-only
(never cancels), never rewrites `reason_code` (owner Q5=A), and never
recomputes the frozen `delegated_runs_started/settled/succeeded/failed`
counters — those remain a HISTORICAL SNAPSHOT taken at the original terminal
write (owner decision Q2=B, custody-absorption sprint), so a healed row may
honestly read `unreconciled: []` beside `settled: 0`; current liveness lives in
the `delegate_terminal_reconciliation` envelope (`trigger` — the refresh
triggers are `sweep_refresh`/`boot_backfill`/`kill_path_clear`; other recorder
callers stamp their own, e.g. `loop_exit`, `cancel_publication`, the workers'
kill/terminalization triggers — plus
`open_run_ids`/`pending_invocation_ids`/`undisposed_patch_run_ids`). An
audit that MATCHES the stored disclosure performs no write and emits no custody
event, so a permanently-unreconcilable row (an undisposed patch awaiting its
owner) does not churn the row or events.jsonl on every boot — and patch debt
itself always survives a refresh as `patch:<run_id>`, never a blind clear.

Disclosed delegated-isolation residuals (phase C landing, deliberately not fixed):
disposition requires the OWNING task identity (`integrate_delegated_patch` refuses
FOREIGN), so an orphan's captured patch is disclosed and preserved but a fresh task
cannot apply it without the owner-law question being decided; a run whose snapshot
was GC-lost or whose capture keeps failing can never satisfy its obligation — the
typed refusal disclosing that is deliberate, closing such a ledger without a
capture is the same owner-law question; an UNDISPOSED snapshot (settled run, nobody
called integrate) persists on disk until explicit disposition — conflict material
persists until explicit resolution, an abandoned task's snapshot included; the
baseline is WORKTREE-PRIMARY (`git update-index --add --remove` stages each path's
CURRENT worktree content, so a staged-then-reverted file is captured at its
worktree content — the regime the run actually sees, matching the previous `git
add -A`); a crash BETWEEN `git worktree add` and the registry write leaves an
unregistered checkout plus its `refs/ouroboros/delegated/` ref that no GC sees
(recovered only by the idempotent re-provision of the SAME snapshot id or by
hand; the window is two statements wide and half-applies nothing); the git lock is
TASK-DRIVE scoped, so two nannies integrating into the SAME external tree can
interleave apply+stage sequences — real only in a multi-nanny swarm on one repo,
the drift check makes the loser's apply a typed conflict, and a repo-wide
cross-drive lock is deliberately NOT built; a credential-shaped file the CHILD
creates inside its snapshot is vetoed by the capture predicate and fails the whole
patch rather than shipping a partial diff — that run's other work is recoverable
only from the execution root directly. The root external-workspace lane holds
unit-level authority tests; the wire-level `delegate_start` flow is proven on the
acting lane (shared pipeline) and a dedicated root-lane wire test would be
additive. The `integrate_delegated_patch` flow is deliberately taught in-context
(tool description, started-note, `workspace_capture` block) rather than in
`prompts/SYSTEM.md`.

**A mutating run asks for containment, reads back what it got, and DISCLOSES the gap
instead of refusing the work.** In place is the one shape where Claudexor otherwise hands
the harness the operator's REAL `$HOME` — which holds the daemon token (the operator's own
daemon keeps it at `~/.claudexor/v3/daemon/token`; the D30 owned daemon relocates it under
`data/claudexor/`), a bearer for the entire `/v2` control API, so a careless or compromised child could
start its own runs at any access level and defeat every host-side authority derivation
above. Four things follow, and they are one mechanism, not four:

- **The marker travels with the isolation.** `execution.delegated: true` rides in the
  same record as `isolation: live`, built from `delegated_run_shape` in one place, so one
  cannot be sent without the other.
- **The version floor is about the SCHEMA, and says so.**
  `config.CLAUDEXOR_DELEGATED_MARKER_MIN_VERSION` (3.3.0) is the oldest engine whose
  `RunExecution` accepts `delegated` at all; below it the start is a 400 and no run
  exists. It is checked inside `subagents.route_health`, the ONE health reader, so the
  DISPATCHER refuses that engine before a token is spent and the nanny's own
  `delegate_start` gives the identical typed `engine_rejects_delegated_marker` blocker. It
  has to be a version and not a capability probe: `RunExecution` is STRICT (an unknown key
  is a 400, not an ignored field) and the catalog's `runControlKeys` are TOP-LEVEL request
  keys only, so a nested marker is undiscoverable. READ-ONLY delegation sends no marker
  and keeps the lower transport floor (`CLAUDEXOR_MIN_VERSION` = 3.2.0, the oldest engine
  that serves that lane and the one the operator actually runs). THE TWO FLOORS ARE
  DIFFERENT NUMBERS ON PURPOSE: an engine between them serves read-only and refuses
  mutating. What this floor is NOT is a proxy for "a boundary was applied" — it was pinned
  at 3.3.2 for exactly that reason and the proxy lied, because Claudexor's boundary is
  macOS-only (`docs/DELEGATED_CONFINEMENT.md` §8) and a build declares the same number on
  every host. Threat model, measured bands and non-coverage:
  `docs/DELEGATED_ADMISSION.md`.
- **What was APPLIED is asked of the attempt, never of the OS.** Claudexor records the
  applied facts — `harness_home_isolated` / `harness_home_dir`, and the boundary as
  `confinement_mechanism` plus the `confinement_verified_denied_path` it was proven
  against — on `attempts/<id>/attempt.yaml`. The HOME pair is projected onto no `/v2`
  response; the boundary is also on the run detail as `candidates[].confinement` (since
  3.3.6). `delegate_wait` reads the artifact, which answers both halves at once
  (`gateways.claudexor.attempt_containment`). The
  mechanism is an OPAQUE string, so a boundary shipped for a second OS needs no edit here,
  and `sys.platform` appears nowhere in the decision: Ouroboros does not know what the
  engine did, only what it recorded.
- **A missing boundary is disclosed in three places, not refused.** A run that recorded no
  mechanism still runs, and the fact reaches the durable event stream
  (`delegate_run_unconfined`), the child's own instructions (its boundary is stated as a
  request, so it does not describe itself as sandboxed), and the parent's terminal payload
  (`containment` carries `os_boundary`, `verified`, `disclosed`/`attempts`). That is
  AGENTS.md "Disclose instead of forbid": the child already holds a shell in this
  worktree, and cutting the lane on every host without a mechanism costs more than the
  marginal step it prevents. A recorded FALSE is still a fault — `harness_home_isolated:
  false`, or a scoped home that IS the operator's own, is cancelled as a typed containment
  fault, exactly like a widened access profile. Those two exact facts are the WHOLE breach
  rule (phase A3, 2026-08-11): a scoped home NESTED under `$HOME` — with or without a
  recorded boundary — is the engine's own layout on boundary-less hosts (every non-macOS
  host today) and flows to this disclosed-unconfined path instead of a post-factum
  cancellation; the engine's typed `confinement_unavailable_reason`, read from the same
  attempt artifact, rides the disclosure as telemetry, never as an admission token. A
  MISSING home fact is neither: the engine
  writes two attempt records and only the clean one carries those fields, so "a01 errored,
  a02 repaired it" legitimately discloses nothing for a01, and faulting on it cancelled
  healthy, finished, successful runs. Unproven is REPORTED, so silence is never read as
  success and never enforced as a breach.

**The model cannot widen its own authority.** `delegate_start` exposes `prompt`, exact
session `subagent_id`, `max_seconds`, recovery-only `retry_of`, and the exact-resource selector
(`root="skill_payload"` + `bucket` + `skill_name`). The retry may replay only the same task's
stored canonical body under its original idempotency key; prompt or ownership mismatch
is rejected, and it cannot change route, root, access, or permissions. There is no
access, mode, isolation, or scope argument, and the selector NAMES a resource rather
than granting one — it is authorized through the same `skill_payload.write` cell as a
direct payload write, so the child can request work but cannot choose its powers.
Every delegated run also carries host-authored `instructions` stating
the same prohibitions an ordinary subagent has — no commit or history move, no
self-review, no runtime controls, skills or memory, no writes outside the root (a
payload run's variant instead states the truth that editing THAT skill's
user-authored files in the private copy IS the assignment) — plus
the hosting task's own contract objective/expected_output (host-read, host-authored:
the model can neither widen nor forge the assignment block). Those
are a statement; the enforcement is the access profile plus the patch capture. And
because Claudexor DERIVES effective access rather than echoing the request,
`delegate_wait` verifies rather than assumes: every fetched run detail goes through
`_containment_breach`, the one reader for BOTH halves of containment — the access
profile and the harness HOME — because they fail identically, and a verification written
for one half leaves the other trusting an echo. A run enforced WIDER than the task is
entitled to is cancelled and returned as a typed `access_profile_widened` refusal. A
narrower effective profile is fine — live probing confirms the engine itself clamps
`workspace_write` down to `readonly` on an `ask` run.

**Harness-agnostic by construction.** An `agent_session` row holds an opaque Claudexor
target (`harness` or `harness=model`) plus optional credential pin and effort. Health
comes from the published manifest/catalog/quota surfaces; Ouroboros asks for an access
profile (`readonly` / `workspace_write`) derived from task authority and lets Claudexor
choose the harness-specific mechanism. No harness-name branch selects a capability or
fallback in core dispatch. The small login-wire asymmetries remain presentation/control
adapters in `gateway/claudexor_accounts.py`, not task-routing policy.

`OUROBOROS_SUBAGENTS` deliberately contains both API and session routes because it is
an actor-selection setting, not a provider-model sweep. `route_spec.py` shares the
neutral route/pin/effort primitive with reviewer rows while preserving their different
public spellings (`api_model` + `credential_profile_id` versus `api_chat` +
`profile_id`) and semantic owners. Provider credentials/base URLs stay global. The
active provider-model key set excludes Heavy so Provider Test, catalog/provenance and
credential planning cannot resurrect it.

The singleton `OUROBOROS_SUBAGENT_HARNESS` / `OUROBOROS_SUBAGENT_PROFILE`,
`OUROBOROS_MODEL_HEAVY`, `USE_LOCAL_HEAVY`, and live lane/executor fields are bounded
migration/history inputs only. When the canonical list is absent, a parseable singleton
produces a session candidate with its pin, custom Heavy may produce an explicit API row,
and Light may supply the Fast scout. Literal `off` becomes `enabled=false`; absent/empty
is `undecided`; malformed non-empty input fails closed. Reads do not persist the
candidate. Once a valid canonical list exists it wins, is not double-written back to
legacy keys, and every active task freezes its own snapshot.

**Subscription preference remains LLM-first.** Onboarding and a clean undecided
Settings draft surface every real connected session actor rather than a singleton, so
subscriptions are easy to choose and often reduce incremental API spend. They also
surface real API/local scouts and perspectives instead of hiding them as fallback.
After an exact selection the host either starts that route or reports why it could not;
it never turns the preference into a keyword router or an automatic API substitution.

**Read provenance on the accounts surface.** `GET /api/claudexor/status` carries a
`reads` block (`ClaudexorStatusReads`: `catalog` / `accounts` / `quota`, each `ok` |
`not_read` | `failed`) because the owned daemon starts LAZILY: an idle machine used to
serve empty collections under a 200, and every consumer read that as "no account
connected" while real accounts sat in the agent home. `ok` makes the matching collection
authoritative (empty means empty); `not_read` means nothing was asked; `failed` means it
was asked and no usable answer came back — the read refused, or the body arrived in a
shape the facet does not promise. Facets are independent — one fanned-out read can fail
while its siblings land, so each is classified on its own. Client-side the rule lives in
ONE reader, `facetReadState` in `web/modules/claudexor_status_store.js`, and every
surface consumes it through that module's ONE shared store (`claudexorStatus`): the
accounts panel, the review lanes, the delegation section, and the onboarding wizard's
agents step — the served `/onboarding` page imports the same modules as the rest of the
UI, so no surface restates the rules. The store is also the single WRITER of the
client-side snapshot (subscribers, visibility-gated polling, a poll hold for a live
login job, `dispose()`), so an owner-initiated wake commits its fresh reading through
the same path as the poll and the login confirmation and two writers never overlap; a
wake refusal is retired only on PROVEN recovery — a daemon that answered — never by a
reply that reports the daemon still down. The store adds the dimensions the wire cannot
carry: `transport` (the request itself never completed — it outranks whatever the last
payload said) and `unread` (this client has not read yet), and it reads a legacy
stamp-less payload coarsely — a genuinely-stopped daemon means every facet `not_read`, a
global refusal means every facet `indeterminate`, a verdict about the answer as a whole
that accuses no individual facet. A read block that is present but unusable, or a facet
value this build does not know, is `failed` — never authoritative — and neither is the
aggregate `daemon.state`, in either direction: it reports `unreachable` for a PARTIAL
refusal, and it goes on reporting `running` when a facet failed on SHAPE rather than by
raising. So the panel and the Refresh button ask the facets. The question is a
disjunction: an authenticated `running` handshake is positive evidence on its own, a
facet's own `ok` is the evidence when it is not, and the aggregate is never the negative
answer. The status line names the facets that did not answer rather than claiming what
is on screen was read — a partial failure lists the unread subjects in the store's own
words, and the installing/error/update-staged wordings append that list instead of
overriding it (`update_staged` may say the engine "keeps running" only over a payload
whose facets were actually read). Reviewer-slot pins are labelled each from ITS OWN
facet — an account pin from `accounts`, a model from `catalog` — and the client's facet
list is parity-tested against `ClaudexorStatusReads`, so a facet added to the contract
cannot go invisible client-side. On the daemon side a facet is `ok` only when EVERY key
its envelope promised arrived: a non-object body is collapsed by the transport into an
empty `{}`, an object whose keys have drifted arrives intact, and either would otherwise
be published as an authoritative nothing.

**Login-job custody and reconciliation.** The daemon remains the sole process/fence
authority; the browser keeps only the current card's custody evidence. The frozen
gateway success shape is one top-level `job` plus operation-specific metadata, while
`ClaudexorLoginJobProblem` carries required `error` and optional stable `code` /
bounded `required_actions`. Snapshot is already the daemon's canonical envelope and
is not wrapped again. A terminal state proves release except when its outcome reason is
`termination_unconfirmed` and no `terminationReconciliation.status=empty` exists.
Reconcile is an explicit POST, never passive polling: success updates the same job to a
safe face and a later, separate Connect creates or re-adopts work. Poll/cancel/reconcile
404/410 mean only that the browser job record is absent and pass through as such; input
keeps its distinct 404 capability result, and only input/reconcile expose typed 409s.

### Git and commit review

`tools/git.py` owns repository writes, staging, reviewed commit, rollback or restore, tags, push, and CI follow-up. File-edit tools validate their own atomic write shape; `mutation_attribution.py` captures the root-task baseline and projects only the clean-at-baseline system-repository delta. A changed pre-existing dirty path, stale or missing baseline, or failed scan blocks automatic staging. `commit_reviewed(paths=None)` stages only that attributed candidate, explicit paths must be a subset, and an empty candidate returns `GIT_NO_ATTRIBUTED_CHANGES`; managed update transactions keep their separate typed whole-tree authority.

A reviewed commit is bound to one staged fingerprint. A cheap LLM-first advisory pass may run before the expensive gates; it is intentionally advisory, and Ouroboros may skip it when it judges the lane unhealthy, unhelpful, unavailable, or too slow. Skipping advisory never skips independently applicable tests, triad, applicable scope review, aggregation, or exact-SHA binding. The hermetic preflight runs the candidate in a disposable worktree and data root. Triad and scope inspect the same staged snapshot, aggregation preserves actor evidence and obligations, and any mutation stales the binding. Managed exception: a managed-update resolution commit reviews the declared M0→S subject (`tools/review_subject.py`) — the resolution delta between the tx-pinned mechanical merge and the candidate — and the commit gate binds S to the exact index write-tree the fingerprint pins. External review wrappers report readiness but do not grant commit authority.

The exact binding includes the `git write-tree` SHA, ordered `HEAD` and `MERGE_HEAD` parents, indexed VERSION, expected `v{VERSION}` tag, any existing tag target, and the binary staged-diff hash. After commit, tree, parents, VERSION, and tag target are re-read before success or push is recorded; an existing release tag is never silently accepted or retargeted. Durable review state keeps attempts, obligations, readiness debt, raw actor evidence, and the final commit or tag binding.

Raw advisory output is not returned by `review_status(include_raw=true)`: that option exposes raw triad and scope attempt evidence for the commit attempt. Ouroboros retrieves advisory runs with `read_file(root="runtime_data", path="state/advisory_review.json")` and selects the matching record by `snapshot_hash` and `ts`.

BIBLE supplies review authority, CHECKLISTS supplies criteria, and Development supplies the procedure. Snapshot identity, advisory coverage or audited-skip evidence, deterministic results, actor evidence, and final Git identity must all describe the same material.

### Review stack

Review waiting has six independent axes: transport/dead-socket bound,
typed active-operation lease, logical slot/task deadline, budget/cancel,
absolute task ceiling, and late-result custody. `review_custody.py` is the
small worker-lifecycle seam used by `review_substrate.py`; it does not schedule
tasks or create a second timing ledger. Parallel slots hold independent
operation ids. The supervisor's active-operation map only prevents a live
physical call from being mistaken for idle; a deadline, budget, cancellation,
or ceiling still wins. Delegated-session expiry uses its existing verified
cancel path, while API/thread calls disclose `in_flight` and reconcile a late
answer before the same retry identity can dispatch again. In a mixed plan or
commit cycle, a settled terminal API error is retained as part of the exact
cycle's replayable actor roster, so a sibling cannot make the cycle lose its
terminal fact or buy a duplicate physical call. The typed actor state is
sufficient for this same-cycle custody fact when optional physical-attempt
capture metadata is absent; when that metadata is present it must say `settled`,
while explicit `reserved` or `released` states remain eligible for a real retry
rather than becoming sticky replay rows. `dispatched` or `unresolved` states
without a typed terminal HTTP status stay under the custody-lost/no-resend
classification; with such a status they are retained as terminal actors for
same-cycle replay, never as a second physical send. Physical custody is proved
by the capture/operation state, not by the synthetic operation id alone, so an
explicit pre-write-ahead `$0` `not_dispatched` row stays frozen and retryable;
a post-stamp checkpoint failure cannot rewind paid authority. A retry rail is
monotonic. A positive `settled`/`dispatched`/`unresolved` capture outranks a
contradictory synthetic `not_dispatched` label; only `reserved`/`released`
proves pre-dispatch, and `usage_accounting` owns the state vocabulary. A later
released reservation or budget refusal cannot erase an
earlier dispatch; an earlier unknown outcome remains custody-lost/no-resend,
while a preserved terminal status may replay. Frozen rows carry the typed
failure and capture facts needed to make that decision after reconciliation. A
retry token without a durable invocation is custody-lost before route health or
project registration for every delegated review surface except the separately
owned Skill Review restart contract. A durable token is valid only for its
recorded delegated surface, slot, and operation; an API row cannot use one to
impersonate delegated recovery. A new
retry cycle uses a new identity. Send-time VLM
captioning keeps its direct 90-second provider cap. Explicit VLM helpers order
their nested bounds as provider, killable child, then a ToolEntry minimum by
reusing one fixed structural settlement margin; the global owner tool-timeout
setting may widen the outer envelope, while the complete hierarchy is narrowed
inside the owner deadline and finalization reserve before dispatch. Anthropic's direct
route keeps its 120-second provider default, and neither provider value is used
as a generic review-reasoning cutoff. The non-delegated Claude advisory child
also inherits the remaining owner window, while its 900-second process cap is
kept when no owner deadline exists. A returned provider response or typed
terminal error
is settled even when its body is empty/incomplete, so bounded repair/retry may
apply. A dead socket or unterminated stream after dispatch is instead
`provider_outcome_unknown` and cannot trigger another paid route. Custody does
not infer pre-dispatch provenance from Python's implicit `__context__`, because a
fallback raised inside a prior provider handler can inherit that earlier attempt;
only an explicit `__cause__` or typed transport metadata can release a row. A low-level
main-call helper with no explicit reserve uses the raw owner deadline; the normal round
dispatcher passes the finalization reserve explicitly, keeping dispatch admission and
the transport bound on the same window. A spent owner window yields a typed `$0
not_dispatched` row before fan-out; under blocking enforcement an in-flight triad row
remains pending instead of becoming a final quorum verdict. A primary call that reaches
this deadline boundary enters the existing local finalization rail with reason
`deadline_local`; it does not get relabeled as a provider outage, while its single
zero-reserve grace call remains subject to the absolute deadline and normal custody.
A reviewed commit has no independent outer tool cutoff: the foreground caller retains
custody until settlement, while inner review/preflight/lock bounds and
the task/supervisor absolute deadline remain the actual stop axes. Retry custody
uses an explicit material/cycle
identity when supplied: mutable prompt or prior-round history is deliberately not
part of that identity, while a changed snapshot, owner intent, reviewer route, or
admitted cycle mints a new one. Commit review takes that material identity from
the canonical staged tree/parent binding. Before either parallel surface starts,
one locked write records `paid=True` plus both complete slot rosters and their
operation ids in the existing commit-attempt row, unless the owner window has
already spent its finalization reserve; that prepared roster stays a typed
unpaid `$0` wave and no paid stamp is fired. A delegated slot patches only
its exact reserved row with `pending_invocation_id` after `START_REQUESTED` and
before the provider POST. Exact resume preserves those rows and tokens, while a
missing or mismatched operation remains `custody_lost` under every enforcement
mode. The paid write-ahead stamp also records the process-custody server
session and pid that own its process-local reviewer threads. Starting another
Agent or respawning a sibling worker is not evidence that this owner died.
Tokenless rows settle as typed infrastructure failure only after a supervisor
death seam confirms that exact pid is gone, or after a later server generation
observes the prior-session pid already dead; a row with a durable delegated
token remains active for exact rejoin. A legacy row without owner identity
remains fail-closed. This owner-loss rule is not a TTL and cannot convert
elapsed time into resend authority. Plan review re-enters a recorded
in-flight cycle only when its process-local custody can join or replay every
previously dispatched row. That cycle retains its original physical actor set
and `$0` health/fit rows even if live readiness changes; partial
`need_evidence` findings enter the next envelope only after the whole cycle is
terminal. A
delegated poll that loses transport after a run id
exists preserves the exact durable invocation for retry custody; it does not
cancel an otherwise healthy unknown run or create a second one.

- Advisory pre-review (`claude_advisory_review.py`) is a cheap,
  staleness-aware error-finding pass. Ouroboros may skip it by LLM judgment;
  the audited skip covers only advisory admission and never authoritative
  review, independently applicable test policy, or snapshot binding.
- Triad diff review (`tools/review.py`) asks configured reviewer slots to cover the Repo Commit Checklist with JSON findings. Quorum is adaptive to the configured reviewer count via `config.adaptive_quorum` (v6.36.0): 2-of-N for N≥3, both for N=2, and a single configured reviewer for N=1 — the latter runs as a loud `single_reviewer_no_diversity` degraded mode (owner's explicit small-config choice), while a configured-≥quorum-but-fewer-responded shortfall stays a loud infra quorum failure. The same SSOT governs scope/plan/skill/acceptance review.
- Scope review (`tools/scope_review.py`) sees touched context plus a Generated Scope Atlas and checks intent/scope/coupling. The Atlas target is an 850K estimated-token assembled prompt under the 920K hard review budget; it raw-inlines selected protected/central files and accounts for every tracked path as full, already included, manifest-only, excluded, sensitive, binary/media, vendored/minified, oversized, read-error, or budget-omitted. Scope review is fail-closed on unreadable touched files and budget-aware on oversized prompts; whether findings block or downgrade to advisory follows `OUROBOROS_REVIEW_ENFORCEMENT`.
- Parallel orchestration (`tools/parallel_review.py`) runs a two-phase admission: BOTH gate packets (the triad api pack and every scope row's pack) are assembled and fit-checked before any reviewer is dispatched, so a deterministic assembly block anywhere dispatches nothing and spends $0 everywhere (typed `not_dispatched` placeholders); only the paid dispatches then run concurrently, and the agent receives all findings in one round.
- Shared helpers (`review_helpers.py`, `triad_review.py`) own pack building, checklist loading, JSON extraction, usage events, obligations/history prompt scaffolding, and reviewer actor records.

Task acceptance is a root-owned post-delivery system, separate from the P3 commit gate. `off` disables it; `auto` and `required` review queued/headless work plus direct work with effectful changes or an explicit typed deliverable/acceptance contract. Ordinary read-only research/tool use in direct conversation, pure conversation, and child authorities do not produce a competing root verdict. Before review, the supervisor closes subtree admission under the queue lock and requires recursive terminal quiescence. Split-drive fence acknowledgement, subtree lookup, and EWMA timing all use the canonical `budget_drive_root`; the one-shot `state/acceptance_fence_acks/` IPC sidecar is not a lifecycle authority, and each transition compacts rows older than one hour and bounds retained acknowledgements to 256. `_run_task_acceptance_review_once` then builds one immutable evidence core (verbatim owner directives and accepted decisions, deliverable and criteria, subtree statuses, verification/artifact references, canonical payload provenance, and explicit omissions) and gives it to the independently configured task-review panel. Each actor makes one substantive call and at most two physical attempts total (same-route transport retry or extraction-only repair); there is no acceptance scope actor. `adaptive_quorum` decides participation. A task-acceptance `FAIL` contributes only with the required outcome tier and a bounded correction rail; a bare veto abstains rather than terminalizing the task without an actionable path. `DEGRADED` abstains from quorum and obligations. A deliberate semantic DEGRADED with a concrete recommendation can still feed the advisory improvement capsule, while transport/unparseable no-quorum is recorded terminally (v6.78.0: `finalized_unaccepted` with `reason=review_degraded`), never as PASS and never as revision authority. A clean result requires quorum PASS, a `solved` tier, and supported evidence for every contributing criterion, where (D-Q5) each 'supported' criterion needs at least one `evidence_ref` that resolves by exact match against the packet's enumerable exhibit keys — a claim id counting only while the host support table shows it backed by a passing receipt (unresolvable refs demote only the clean bit, disclosed per-actor as `criteria_refs_unresolved`). Actionable gaps are exact-deduplicated and feed the existing improvement loop; an explicit `max_improvement_passes` binds every policy, otherwise the shared `OUROBOROS_REVIEW_MAX_CYCLES` cap (`improvement passes = cycles − 1`) binds every policy incl. Required+Blocking — no local count cap remains only under `unlimited` or on the non-Required+Blocking `until_deadline`-with-deadline alias path — and deadline/global lifecycle rails remain. The first review reserves at least 200 seconds; later passes reserve `max(configured floor, 1.5×EWMA)` using canonical existing timing events (`alpha=0.5`). The structured review axis is mirrored as top-level `review_status` for task-result/gateway/event compatibility. Post-task synthesis recovery runs only at startup and consults one checkpoint in the canonical `budget_drive_root` task result: it replays only `pending_once`, terminal-degrades indeterminate `running` without a second paid call, and ignores terminal markers. Normal supervisor child copy-back/artifact finalization remains responsible for materialization; a late copy-back may enrich the result but cannot overwrite a terminal canonical phase. Minority dissent and blocking-lane obligations remain typed, auditable inputs, but the root acceptance verdict and stop reason are stored separately from the terminal lifecycle/artifact result.

Rationale: diff reviewers catch line-level mistakes; scope reviewer catches cross-module contracts and forgotten touchpoints. Running both on the same staged snapshot prevents one reviewer result from hiding the other. Managed exception: for a managed-update resolution commit both lanes review the same declared M0→S subject instead of the whole-tree staged diff — the commit gate binds S to the index write-tree the review-binding fingerprint pins, so the shared-subject property is preserved on the delta.

Structural smoke gates are a deterministic BIBLE P3 codebase-size component.
`ouroboros/review.py::iter_gated_modules` is the one source inventory for smoke,
`codebase_health`, census, and the UTF-8 byte gate. Its Git candidate is cached plus
nonignored untracked files; exact-ref census injects immutable Git blobs. Module scope
is Python everywhere (including `tests/` and `devtools/`) plus first-party
`web/**/*.js` (including `web/tests/`), with vendored/minified payloads excluded. The
function iterator preserves the narrower runtime scope and exact lexical qualnames.

`ouroboros/size_ratchet_manifest.py` is a generated, data-only debt register consumed
through AST literals, never Python import execution. It records exact repo-relative
module debt above 1600 lines, exact `(path, qualname)` function debt above 300 lines,
the exact-current 1001-1500 band with rationale authority for new or re-entered paths,
and exact byte debt above 200,000 UTF-8 bytes. Validation
(`ouroboros/review.py::validate_size_ratchet`) proves the live and staged manifests
exact against their trees and shrink-only against the merge-aware committed authority:
the previous manifest resolves from `HEAD`'s tree, falls back to ANY parent whose tree
carries it, and a checkout with no committed manifest anywhere bootstraps from its own
tree (baselines must match that tree). There is no first-parent history replay — a
fork whose local line predates the manifest is never condemned by inherited topology.
Enforcement is split by surface: the OFFICIAL repository CI runs the blocking
`size_ratchet` pytest lane (tip exactness plus
`validate_size_ratchet_transition_against_base`, the pairwise base-vs-tip transition
against the CI event base), while every local surface — default pytest lanes,
`check_worktree_readiness`, `codebase_health` — reports the same findings as warnings.
Two residuals of that official line are accepted and disclosed: pairwise validation
covers only the `event.before`/`base.sha` → `HEAD` interval, so growth-and-rollback
inside one push/PR interval — including a same-interval retire-and-re-enter — is not
caught anywhere (accepted owner tradeoff); and the official line's only block is
post-push/PR CI, so its authority presupposes repository branch protection / required
status checks on the `ouroboros` branch — a repo-settings prerequisite outside this
codebase, escalated to the owner separately.
Within a validated pair, debt can shrink but cannot be swapped, re-entered without its
required authority, grow on the byte axis, or survive as a stale record.
`scripts/regenerate_size_ratchet.py` refuses an unmerged index, resolves its previous
manifest merge-aware, and validates the rendered candidate in memory
(`validate_size_ratchet_candidate`) before overwriting the checked-in file.
`MAX_TOTAL_FUNCTIONS` remains the coarse
runtime ceiling and any raise requires its one-line campaign rationale.
The same sprint added a deterministic hot-store growth health invariant:
`agent_startup_checks.py::hot_store_growth_notes` (surfaced in every task
context by `context.py::build_health_invariants` and reported once per worker
boot as the `hot_store_growth` check in `verify_system_state`) stats `logs/events.jsonl`,
`logs/tools.jsonl`, `logs/progress.jsonl`, and `state/usage_attempts.jsonl`
against justified byte thresholds in `ouroboros/context_budget.py` and emits a
WARNING with a remediation pointer; explicitly sentinel-marked isolated devtool roots suppress it because their
external reader owns the bounded run-local stores.

The shared hard prompt-size SSOT is `REVIEW_PROMPT_TOKEN_BUDGET = 920_000` in
`ouroboros/tools/review_helpers.py`. `review_context_atlas.py` targets 850K
estimated total prompt tokens for scope review and deep self-review, then leaves
the final 920K gate in each caller as the hard stop so oversized-context behavior
cannot drift between review entry points (plan review builds no Atlas: its packet
is sized per slot by `plan_review_runtime.plan_slot_fit`).

Scope review additionally reserves output headroom inside the reviewer's 1M
window. The 920K SSOT governs INPUT, but the scope reviewer also reserves
`_SCOPE_MAX_TOKENS` (100K) for OUTPUT and a tokenizer headroom margin because
provider accounting can exceed the local estimator on atlas-heavy prompts. 920K
input + 100K output exceeds 1M, which the provider rejects with a hard 400.
Such a physical rejection is UNCONDITIONALLY fail-closed in `max` mode: there is
no authoritative verdict, and since v6.80.0 no setting can turn it into a
non-blocking `budget_exceeded` skip — `OUROBOROS_SCOPE_REVIEW_FLOOR` still exists as
a stored owner setting but is enforcement-inert and consulted by nothing. The only
owner control over scope review is the context mode: `low` means whole-repository
scope review is declaredly not performed (typed `skipped_low_context_mode` row), and
`max` means this fail-closed gate. So
`scope_review.py` gates the assembled INPUT prompt on
`_SCOPE_INPUT_TOKEN_LIMIT = min(920K, 1M − _SCOPE_MAX_TOKENS − margin)`, with a
substantial tokenizer headroom margin (currently 155K tokens) — the 920K
SSOT itself is left untouched. The cap is additionally DENSITY-CALIBRATED: the chars/4
estimator tracks GPT-style tokenizers within that 155K margin, but Claude-family
tokenizers cut code-heavy packs at ~2.5 chars/token — a real scope pack estimated at
739,508 tokens measured 1,166,914 REAL tokens (1.58x) and was rejected 400 `prompt is
too long` by every upstream. The ratio is measured rather than keyed to a model-name
family. `usage_accounting.execute_physical_attempt` records timestamped
`(prompt_chars, real prompt_tokens, route_fp)` witnesses after settlement and outside
the ledger lock (fail-soft) in the existing `token_density` namespace of
`capability_evidence.json`. Density evidence lives ONLY in this canonical
data-root store (`capability_evidence.canonical_evidence_root()`); readers and
writers must never resolve a per-task child drive, so there are no child-drive
density stores to drift. Cache-bearing usage whose provider prompt semantics are
unknown records no witness. The Main reducer uses the newest fresh exact-route
witness, then newest exact-model witness, then neutral 1.0; it may move either
direction as current evidence changes. The review reducer uses the densest still-fresh
exact-model witness (otherwise a cross-model witness), applies the existing safety
factor and never drops below the conservative 1.65 cold floor. Retention keeps the
densest witnessed pair plus the newest bounded remainder, so ordinary lighter traffic
cannot evict its support; when that witness reaches TTL its unsupported value genuinely
disappears. Equal clock ticks use a persisted per-witness observation sequence only as
a recency tie-breaker; freshness and TTL still depend exclusively on the original
`observed_at`, so ordering cannot extend evidence authority. There is no independently
refreshed running-maximum scalar.
`review_helpers.calibrated_input_token_limit` still returns the STRICTEST of the 920K
budget cap, density form `(window − output_reserve) / density`, and historical
absolute-margin form, so expiry may loosen only within those existing conservative
bounds. Provenance reports the reducer branch. `scope_review._effective_scope_input_limit` computes it PER CALL
(an import-time constant froze the pre-measurement value for the whole process, so a
measurement could never reach it), and the triad (`tools/review.py`),
`plan_review.py`, and `deep_self_review.run_deep_self_review` consume the same helper. The scope cap is WINDOW-AWARE: a known reviewer window from
Capability Evidence (`_scope_window` -> `ouroboros.capability_evidence`;
no static table, v6.33.0) replaces the assumed 1M when computing the effective
input cap. (v6.87.9) That window resolution is no longer scope-only: the seam
lives in its own module — `reviewer_window.resolve_reviewer_window` /
`reviewer_context_window` / `window_scaled_reserves` (scope review delegates to
it and keeps its own sentinel SIZING policy), and the triad, plan review,
and deep self-review size their packs against each slot's REAL window instead of
a hardcoded 1M — a 200K reviewer treated as 1M-capable lost its whole review to
a deterministic prompt-too-long 400 — with a sub-1M window scaling its
output/tokenizer reserves rather than zeroing the slot. An UNKNOWN route keeps
the FULL-window assumption on those three surfaces, the same policy `context_fit`
applies to the main lane (unknown routes try Max, never a silent 200K). Sizing a
review pack down on a guess is not the safe direction: the governance packs run
~169K tokens, so a sub-floor guess declined plan review outright before dispatch
on every cold-evidence install. Only scope review fails CLOSED on absent
evidence, because its BLOCKING authority is what a wrong assumption would
forge, and it applies that sub-floor to the shared evidence seam itself.
(v6.87.44) The seam returns ONE typed `ReviewerWindow`
(`window_tokens`/`status`/`stale`/`observed_at`) instead of a
`(window, status)` tuple that dropped `stale` and the observation time on the floor, and
`blocking_authority_allowed` is a COMPUTED property of that evidence —
`capability_evidence.confirms_at_least(..., require_fresh=True)`, the predicate the
codebase already owns — never a side effect of which model name was configured. Two
routes to a forged verdict are closed by that one property: an EXPIRED or
outage-carried 1M record (dated evidence read as live) and the designated-default
sentinel (an invented window read as sourced). The sentinel survives as a SIZING
number only, so the review is still dispatched, and the shipped default now takes the
same metadata probe as every other route — the name-check that granted it
authority was also what denied it the one path to earning any. Concurrent resolutions
of one route serialise on a per-route lock so they share ONE fetch. That probe is
rate-limited by the evidence TTL and by nothing else (v6.87.45): the per-process memo
that used to gate it never expired while the record did, so a healthy, connected
install that stayed up past 24h re-read its own reviewer as EXPIRED on every later
resolution and blocked EVERY commit for the rest of the process's life. A known sub-1M reviewer remains advisory-only: in `max` its result is
preserved as evidence but cannot satisfy the gate, and the commit fails CLOSED —
the deprecated `OUROBOROS_SCOPE_REVIEW_FLOOR` no longer converts that into a
non-blocking `budget_exceeded` skip (the GigaChat-only / no-≥1M-reviewer case is answered by the
owner choosing `low`, where scope review is declaredly not performed and each
skipped commit records the typed `skipped_low_context_mode` row, or — since the
v6.87.6 P3 amendment, IMPLEMENTED in v6.89.0 — by an owner-declared RETRIEVING
scope slot at ≥200K sourced Capability Evidence, whose coverage is declared
unasserted; never by a weaker blocking gate). The same authority rule applies if the estimate-based gate passes but the
provider's REAL tokenizer rejects the prompt as oversized (`prompt is too long`,
`context_length_exceeded`, …). Every other provider or transport error remains
fail-closed. The calibration shrinks the PROMPT
for the same pinned reviewer — never the reviewer model or the ≥1M window floor
(P3). Plan review fans one shared prompt across mixed-family slots and (v6.80.0) now
sizes it PER SLOT from the same calibrated helper — closing the former "planned
follow-up work" gap that made a Claude plan slot 400 deterministically: a slot the
shared prompt cannot fit gets a FREE deterministic `preflight_oversize` record instead
of a guaranteed-400 call (`plan_review_runtime.plan_slot_fit`; the excluded slot stays a
configured row in the quorum denominator). The packet is not tiered — a self-modification
plan carries BIBLE.md and ARCHITECTURE.md inline (W3) — so there is no smaller rebuild: fewer
callable slots than quorum returns typed `PLAN_REVIEW_DEGRADED_PREFLIGHT_OVERSIZE` with no
reviewer called, naming each slot's cap.
Non-responded scope actor records also surface the provider failure text
(`error` field in `build_scope_actor_record`) so a deterministic 400 is visible
in the verdict without observability digging. The scope coverage contract
requires explicit `severity` only on FAIL rows (it decides blocking and stays
fail-closed); PASS rows default to `advisory` like the triad parser.

Scope prompt assembly is GUARANTEED-FIT (v6.30.0): the owner directive is that
scope review must actually run, so the assembler walks a deterministic
degradation ladder instead of skipping. 1) full atlas; 2) compact atlas (the
durable `context_manifest` keeps full per-file coverage while the visible
prompt keeps a compact path/disposition coverage index); 3) a REQUIRED file the
atlas cannot fit is a failure to ASSEMBLE, never a smaller pack (BIBLE P3): the
row is recorded as `budget_omitted` naming the artifact and the reason, the pack
status becomes `required_artifact_omitted`, and no consumer reviews the
remainder — the ladder keeps shrinking the FIXED part and retries, so the
refusal is a step, not the end (`budget_exceeded` is the sibling failure, when
even the content-free manifest cannot fit); 4) touched files degrade to
diff-only, FREELY DEGRADABLE ones first and largest-first within each tier — an
artifact owed in full is reached only after rung 5, since degrading one is a
typed assembly failure and can never buy a fitting pack; touched TEXT tests,
current and deleted alike, belong to that FREE tier (full snapshots / inlined
HEAD content while the budget allows, diff-only only under pressure) instead of
being atlas anchors the ladder could not reach, which used to make one large
touched test terminate the pack as `required_artifact_omitted` even though the
staged diff already carried its complete change — binary test fixtures stay out
(a text diff does not carry their changes, so a "changes included" row would be
a false claim), renamed paths conservatively stay out with them (the staged
diff may carry only a rename header), and a deleted test over the inline cap
keeps its own suppressed
marker rather than crediting the ladder with tokens the fixed part never held —
their full post-change snapshots are replaced by
an explicit `TOUCHED FILE BUDGET DEGRADATION NOTE` while their complete
changes remain visible in the staged diff, which is therefore captured
byte-exact and immune to operator diff config, and fails prompt assembly closed
rather than degrading to a placeholder; the ladder DECLARES those paths
to the atlas (`ReviewContextAtlasRequest.diff_only_included`, v6.87.15) so the
durable coverage row reads "changes included …; full snapshot omitted" instead
of the `already_included` claim the prompt itself contradicts — diff-only is
legal ONLY for merely-touched files, whose complete change-evidence is the
staged diff itself: an artifact owed in full regardless of the change
(`prompts/`, `ouroboros/contracts/`, protected runtime + review stack,
canonical docs) declared diff-only is the same typed `budget_omitted` assembly
failure as step 3, and a required artifact over the per-file 1MB cap fails the
same way instead of leaving a silent `oversized` row (requiredness is computed
BEFORE any disposition can drop an artifact); 5) unchanged hunk
context may be removed with `-U0`, preserving every file/hunk identity and every
`+`/`-` line.
Triad independently applies the same one-pass fit rule before dispatch: a
disclosed touched-path manifest can replace full snapshots duplicated by the
complete diff, followed by the same `-U0` fallback. Every step is a disclosed omission
(P1), never silent. TWO exhausted-ladder terminals remain and both fail CLOSED:
the irreducible prompt (checklist + canonical docs + staged diff) not fitting,
and a REQUIRED artifact that never assembled. The terminal STATUS still picks
the authority branch (`fixed_overflow` at ≥1M, `budget_exceeded` sub-floor)
while the CAUSE travels beside it on `_TouchedContextStatus.unassembled_required`
and is worded by one derivation, `_ladder_terminal_cause` (v6.87.15) — before
that, a missing-artifact stop was reported on BOTH branches as an overflow,
quoting a token count below the budget it claimed to exceed and prescribing a
diff split that cannot shrink an unchanged artifact. The refusal is also a
recorded `atlas_refused` ladder step naming what did not assemble, so the
terminal is explainable after the fact. Both atlas assembly failures are
classified by one predicate — `review_context_atlas.atlas_assembly_failed` over
`ATLAS_ASSEMBLY_FAILURE_STATUSES` — instead of each consumer re-deriving a
status test. Scope review and deep self-review remain strict consumers and do
not review the remainder. Plan review is no longer an Atlas consumer at all
(spec-gate redesign 2026-08-15): it reviews a typed SPEC with agent-declared
evidence, so there is no generated Atlas, no `context_level` and no scout wave
to fall back from — an evidence locator the host cannot attach is a named
omission in the manifest, and a packet that cannot be assembled with the
constitutional pack a self-modification plan requires is a typed failure, never
a silent reduction. `atlas_unassembled_required` reads
the ONE carrier (`manifest["unassembled_required"]`) that discriminates the
typed causes, and `ATLAS_MISSING_ARTIFACT_REMEDY` remains the strict-consumer
remedy. The two terminal causes are not exclusive: an atlas refusal that
dropped a required artifact can ITSELF be a hard-budget overflow, and that mixed
state reports BOTH causes and picks `ATLAS_MIXED_ASSEMBLY_REMEDY`, because either
single-cause remedy states something false about the other half (read the second
cause with `atlas_hard_budget_overflowed`; pinned by
`test_mixed_terminal_reports_both_causes_and_the_mixed_remedy`). For scope
review, `budget_exceeded` and provider-oversize outcomes are recorded
as evidence but never satisfy the P3 gate, and since v6.80.0
no setting makes them non-blocking — in `max` they block. The P3-aligned remedy
for a structurally oversized repo stays shrinking/splitting the reviewed tree,
never lowering the reviewer below the 1M context floor.

In owner-selected `low` context mode (v6.80.0) `run_scope_review` returns before
assembling anything — the predicate reads `config.get_owner_context_mode()`, never the
effective mode; since persistent system auto-Low was retired, no agent-reachable
settings write can author stored Low at all — only the owner endpoint does
(see the `/api/owner/context-mode` contract above): no reviewer is called,
the commit is not gated on scope, and a
typed non-blocking `status="skipped_low_context_mode"` result is recorded through the
SAME `build_scope_actor_record` review-evidence surface that carries the fail-closed
results, so a low-mode commit is never forensically confusable with "scope review
silently failed to launch" (P1). This is the owner's policy coupling, not a coverage
claim; the removed opt-in degraded advisory builder (`OUROBOROS_SCOPE_REVIEW_DEGRADED`,
`_LOW_SCOPE_INPUT_TOKEN_LIMIT`) is gone with it, and the one-pass gate keeps returning
the normal actor's authoritative or fail-closed status in `max`.

### Planning, deep review, reflection, memory

Plan review, task acceptance, commit review, and deep self-review answer different questions and never inherit one another's authority. Planning judges a proposed approach before implementation; task acceptance judges the delivered objective; commit review authorizes a staged self-change; deep self-review diagnoses the whole system. Post-task reflection and memory persistence learn from execution but approve none of those boundaries.

#### Plan construction and review

`plan_task` reviews an INTENTION before the work starts — the same organ whether the work is code, research, a deliverable, or an action in the world. The submitted envelope carries the goal, the plan prose, and a typed domain-neutral SPEC: `in_scope`, `non_goals`, `acceptance_claims`, `invariants`, `decisions` (choice + rejected alternatives + why), `deferred`, `affected_resources` (what the work will change) and `evidence` (what a reviewer should look at). `ouroboros/tools/plan_spec.py` normalizes it, mints the ids that are the only valid `breaks` targets (`goal`, `claim_N`, `invariant_N`, `decision_N`, `deferred_N`) and hashes it. Governance documents always come from the system repository; declared targets and evidence resolve against `active_repo_dir_for(ctx)`. A path escaping the active subject, a workspace/subject mismatch or an unreadable root is a named omission, never a silent gap.

ONE structural fact tiers the governance pack: `constitutional` is true iff a declared `affected_resources`/`evidence` PATH locator resolves under the Ouroboros system repository (owner decision D29 — the active binding alone never decides; skill-payload paths under the canonical data root stay exempt). A constitutional plan carries BIBLE.md in full and ARCHITECTURE.md inline for an `api_chat` row (a retrieving `agent_session` row gets the executor's compact form: both as mandatory full reads at their resolvable locators) — assembling that packet without either is a typed failure, never a disclosure — and every other plan carries the runtime heading-derived navigation maps of BIBLE.md and ARCHITECTURE.md (`context_layout.generate_doc_nav_map`, never a copy) plus resolvable pointers; a `need_evidence` locator a reviewer names is attached by the host on the next cycle through the same evidence policy, and it enters the manifest hash (W3). There is no plan-kind taxonomy, no agent-declared `plan_class`, no `context_level`, no planning scouts and no plan Atlas.

Declared evidence is resolved by `ouroboros/tools/plan_evidence.py` against exactly two allowed roots — the active workspace and the system repository — with the shared sensitive-name policy applied to both the lexical locator and its resolved target. Every locator that is refused, missing, truncated, too large, binary or a URL becomes a typed omission row in the manifest; the host never fetches a URL. The manifest hash joins the spec hash and `constitutional` in the wave fingerprint, so changing what the reviewers can see changes the identity of the review.

`ouroboros/tools/plan_packet.py` builds the lean packet: the task objective (always), the spec with ids, the plan prose, the attached evidence plus its omissions table, a bounded task-local exploration log, and — on cycle 2+ — all reviewers' findings from the previous cycle, the agent's dispositions and the spec delta with the convergence rule. Slots come from `reviewer_slot_config`; the transport is chosen by the existing `review_execution._review_route_executor` seam, so an `api_chat` row receives the assembled packet in-process and an `agent_session` row receives the same task as a retrieving reviewer whose surface is recorded `host_file_read_attestation: unobserved`. Reviewers return ONLY a typed findings array (`blocking` with a `breaks` id · `note` · `need_evidence` with a locator); the HOST validates membership, demotes a blocking finding with an invalid `breaks` to a note with disclosure, demotes a repeated `need_evidence` locator to a note that stays in the aggregate until the agent disposes of it (dropping it could turn the wave GREEN), keeps failed slots in the quorum denominator and computes the aggregate through `config.adaptive_quorum`. No reviewer emits GREEN as authority and no reviewer writes a competing plan.

`plan_review_state` v2 inside the root task result is the bounded durable authority: each wave records the frozen spec (including `acceptance_claims`, which bind task acceptance through `contracts/task_contract.effective_acceptance_claims`), the spec and evidence hashes, `constitutional`, the validated findings, the aggregate, the dispositions and whether the wave was paid; recent waves are kept in full and older ones compacted with an explicit omitted count. A paid actor that remains physically in flight keeps the wave open as `DEGRADED` with `review_late_result_pending`, even when the settled rows meet the arithmetic quorum, so a late blocking result cannot arrive after a false GREEN closure. If the owner deadline expires while that paid wave is still in flight, an identical envelope may still run the exact custody reconciliation; it settles the frozen physical set without buying a successor or extending cognition. A v1 record is read-only; public task-result copies add the canonical derived `legacy_v1_projection` without rewriting the stored record, so every public consumer sees the same compatibility semantics. An OPEN v1 wave projects `legacy_open_requires_resubmission` and is never auto-closed.

Closure follows the finding class. GREEN closes. REVIEW_REQUIRED (notes / `need_evidence`, or blocking below quorum) closes its notes and `need_evidence` through a disposition-only `plan_task` call naming the fingerprint and covering every finding once — no model call, no cost; a below-quorum blocking finding stays open until the spec changes or a paid delta cycle judges its rejection. REVISE_PLAN can never be closed by disposition: the agent changes the spec (a new fingerprint, the next paid cycle) or rejects a blocking finding with a rationale that rides into that cycle. Paid cycles per task are bounded by the owner's shared `OUROBOROS_REVIEW_MAX_CYCLES`; an identical envelope replays the recorded wave for free (a recorded DEGRADED wave only under the three replay conditions below), with ONE exception: an open wave whose BLOCKING findings all carry valid reject dispositions — REVISE_PLAN, or REVIEW_REQUIRED with a below-quorum blocking finding — has earned its promised delta cycle, so the same envelope buys exactly one more paid panel. A wave is paid iff at least one reviewer slot was physically dispatched: a dispatched DEGRADED panel (no parseable quorum) pays its cycle, records OPEN with per-slot typed failure facts (code and reset time), and reaches the agent as an honest DEGRADED control outcome with the quorum arithmetic — never a host-authored re-call imperative; only a nothing-dispatched wave of typed $0 skip rows stays unpaid and never replaces a paid predecessor. Before fan-out the engine captures ONE panel health snapshot (through `subagents.route_health`, the single manifest reader — route-level evidence, never per-credential-profile): a slot with positive structural evidence of a spent lane (a dated window exhaustion with a future reset, or a typed dead-pool code) becomes a $0 typed skip row that stays in the quorum denominator; unknown health dispatches (fail-open) and transient daemon states are never skip evidence. The wave records the snapshot-derived material health epoch (`{slot, code, reset_at}` rows, no observed_at) and the reviewer-roster fingerprint (slot ids, targets, routes, pinned session targets/profiles AND efforts): a recorded open DEGRADED wave replays free only under ALL THREE conditions — an identical envelope, a NON-EMPTY recorded structural epoch that a fresh snapshot still matches, and an unchanged reviewer roster (an effort change is a roster change); an empty-epoch DEGRADED wave (its slots died at dispatch time, invisible to the pre-fan-out snapshot) re-dispatches a PAID panel on the identical envelope, as does a healed or newly dead lane or a changed roster; a failed snapshot is transient-unknown and keeps the free replay. When the wave's own typed rows prove the quorum structurally unreachable (configured minus window-exhausted rows below the quorum), the wave carries `quorum_unreachable` plus the earliest recorded reset, and under blocking enforcement the finalization gate RELEASES (the review stays open, implementation stays held): the agent may honestly finalize — the objective terminalizes `blocked_with_evidence` with the typed reason `plan_review_quorum_unreachable` — or wait (a one-shot deferred follow-up through `schedule_followup` rides the existing supervisor scheduler) or ask the owner; the host adds facts only, never an answer template. Under blocking enforcement an open wave otherwise holds implementation and an exhausted cap escalates with the typed `review_cycles_exhausted` reason and an honest blocked terminal; under advisory the agent may proceed with the wave open — the host emits one typed owner-visible `plan_review_advisory_open` event when the open wave records, plus the loud disclosure at finalization. Unavailability, invalid state, budget refusal and deadline rails remain typed non-authoritative attempts, never substitutes for GREEN.

#### Deep self-review

A task with `type=deep_self_review` bypasses the ordinary tool loop and calls `deep_self_review.run_deep_self_review` once with `tools=None`. The caller resolves the dedicated model and its observed route window before building the pack. `ReviewContextAtlas` supplies repository coverage and the memory whitelist remains full: identity, scratchpad, registry, WORLD, full knowledge index, patterns, and improvement backlog. The prompt's omission section is bounded and points to the complete persisted coverage manifest. If a required Atlas artifact cannot assemble, the flow rebuilds only through its declared compact/final-fit path and otherwise returns an explicit failure; it never sends a silently incomplete review.

The call records normal usage evidence, writes the coverage manifest to `state/deep_self_review_context.json`, stores the report at `memory/deep_review.md`, and returns it as the task result. The model has no tools, cannot mutate the repository, and does not run plan, task-acceptance, or commit reviewers. Its report is durable diagnostic memory under BIBLE authority, not implementation or publication authority. Unavailable credentials/model, oversized required context, empty output, and transport failure remain visible task failures rather than a clean review.

#### Post-task reflection

The root post-task checkpoint decides whether an error-bearing or non-trivial run warrants Experience Review. `reflection.generate_reflection` sends the Light route a bounded task goal, trace summary, tool-use profile, concrete errors, structured review evidence, child evidence, and the same frozen non-final cost snapshot used by the task summary. Reflection runs outside the tool loop and records its own usage. Failure is logged and does not erase the delivered task result or change any review verdict.

A reflection lands where it durably belongs: a non-project root appends the full entry to the canonical `logs/task_reflections.jsonl`; a project-scoped root appends the full entry to its project drive (`projects/<id>/logs/task_reflections.jsonl`) and the canonical log receives only a bounded pointer row (task id, timestamp, project, path) — full project text never enters the canonical log, which feeds future global context. Project reflections are also read back, not only written: a project-bound task's context includes a bounded tail of its own project's reflections file (same limits as the canonical tail, clearly labeled as the project's own), so the project's full lessons remain visible where the canonical feed carries only pointer rows. The headless mirror drive of a split root is never the reflection home (it is prunable). The Pattern Register update stays on the canonical drive in both cases. Every entry carries its task identity, evidence, lessons, backlog candidates, and validated memory actions. `MEMORY_ACTIONS_JSON` permits only `scratchpad_append`, `knowledge_write`, and `identity_update_candidate`, at bounded count and size. `apply_memory_actions` routes accepted actions through provenance-preserving memory and knowledge APIs. An `identity_update_candidate` is recorded in the scratchpad for review and is never auto-written to `identity.md`. For a project-scoped task, only project knowledge is written; scratchpad and identity actions are skipped so local facts cannot contaminate the canonical self. Reflection may propose a future campaign or plan-review backlog item, but it cannot enqueue, review, commit, or enable one.

Only the root runs full post-task synthesis once. Split non-project work uses the canonical budget drive; project work uses its project drive and forwards only the sanitized backlog promotion to the canonical drive. Children contribute evidence to the root and do not run a second global synthesis. `root_phase_checkpoint` makes this paid phase at-most-once across restart. When synthesis runs blocking inside the worker, the owner's final answer does not wait for it: after the durable task result is stored, the final `send_message` is delivered immediately over the live worker→supervisor queue while the buffered-return copy is RETAINED (queue.put is not a delivery receipt); both copies carry one `delivery_id` and the supervisor suppresses the second via a bounded (256) in-memory deque backed by the DURABLE registry in `supervisor/terminal_delivery.py` (`state/terminal_deliveries.json`, bounded, atomic) — registered only after a successful send, so a failed live send never suppresses the buffered copy. Since phase A2 the same file also holds a bounded PENDING outbox — ONE seam for the normal, cancel, and reap terminal paths: a terminal answer is recorded as owed BEFORE it is enqueued and the row is cleared in the same write that marks it delivered, so a crash between the settle and the send replays it on boot and on the supervisor tick instead of losing it (the Poltergeist class). EVERY non-ephemeral root's final answer enters this outbox at durable-result persistence time (the worker mints the canonical `final:<tid>:<digest>` id onto the buffered send and registers it cross-process-locked against the canonical data root), regardless of the blocking/nonblocking post-task split — the nonblocking lane used to buffer the send with no delivery id and no owed registration, so a worker crash before the buffered drain lost the answer with nothing to replay; the blocking lane's live delivery re-registers the same id idempotently. Replays are spaced with exponential backoff and bounded; a row that exhausts its attempts — and equally the oldest owed row evicted past the outbox capacity by newer registrations — is dropped LOUDLY — full text preserved on disk, a typed `terminal_delivery_exhausted` event (with a distinct `outbox_capacity` reason for the eviction shape), and a chat notice naming the preserved copy — never silently. The dedupe now survives a restart; external transports stay at-least-once and that residual is disclosed rather than papered over. `task_done` still goes last through the buffered return — an early `task_done` would release the queue slot and start child-drive cleanup while post-task still runs — so a worker reaped during a hung synthesis has already delivered the answer, and the reaper's idempotent `task_done` against the terminal on-disk result stays the only terminal event. Synthesis itself receives a sealed final package as mandatory ground truth — the delivered result text plus the durable result's own artifact manifest (name/size/existence, from the same store authority that built the result; no second enumeration) — and its prompts state that these facts override failure impressions from the error trace, so a recovered deliverable is described as delivered instead of missing.

#### Durable memory and project focus

`context.py` assembles static governance, semi-stable memory, and dynamic task evidence without treating truncation as forgetting. `consolidator.py` replaces eligible dialogue or scratchpad history only with explicit summaries and generation-aware archive/provenance records; unreadable, required, or unrepresented material fails or receives a visible omission pointer. Knowledge topic mutation and its index rebuild share one stable lock; scratchpad block mutation and markdown regeneration likewise share one lock, so concurrent Presence and owner turns cannot publish an older projection over a newer write. Knowledge writes retain source metadata, and identity changes remain on their dedicated authority path. The final Development context matrix and `context_layout.py` own which reference form is resident; this section does not duplicate that matrix.

Ouroboros remains one identity across Main, project rooms, and Background Consciousness. A project is a focused working room, not an isolated sub-mind: unified dialogue memory remains available to the one agent, while an executing project task preferentially receives its own thread, journal, workpad, and project knowledge. `project_facts.py` routes project facts to `projects/<id>/knowledge`; subagents inherit the root's resolved project id and never derive a new one. There is no per-project identity or scratchpad, and only the current project's fact store is injected into its focused task context.

The projects registry owns immutable project identity, canonical chat id, optional working directory, lifecycle/tombstone state, routing generation, and activity revision. Admission persists the resolved project id in the task itself, and `project_lease.py` permits one top-level writer per project while allowing that task's own subagent tree. Binding/history files support routing and presentation; they are not the lease authority. Delete closes routing, cancels/quiesces the tree, and tombstones only after settlement, preserving the id, history, bindings, folder, journal, workpad, and memory for recovery.

`ensure_project_scope` can create or bind the current root to one project during execution. It marks the live queue/lease surface under the queue lock before persisting the binding, is idempotent for the same project, refuses a second scope, and cannot be invoked by a child to escape the inherited scope. This makes mid-task project creation a structural capability rather than a bare directory convention.

Project `journal.jsonl` records curated milestones and `workpad.md` retains active working context. Focused context includes the workpad in full and recent journal rows with a visible pointer to older entries rather than silent prefix slicing. On root completion, only high-signal swarm blockers, questions, interface contracts, and contracts are mirrored once from the ephemeral task-tree ledger into the durable project journal; ordinary cycle chatter is not. When a finished root's effective working tree is not the project's registered `working_dir` (or the registry has none), the same finalization writes one typed "work lives at <path> @ <sha>" journal row from facts the task record already holds — no git subprocess — so an off-registry tree stays visible to later continuation promotions. A project digest gives consciousness a concise completion signal without pretending that the digest is the raw project memory or a cognition boundary.

`promote_chat_to_task`, `route_to_project`, and `steer_task` become successful only after their token-matched supervisor facts are durable in the existing task result, queue snapshot, annotation, or mailbox authority. With several possible tasks, the LLM chooses; code auto-delivers only the unambiguous one-target case. An unconfirmed or stale receipt fails visibly and cannot launch a second root as a fallback. These paths reuse the normal task lane and do not create a parallel scheduler or message history. A routing/promote decision turn receives host-built ground truth rather than relying on chat memory: the Main routing manifest carries each project's registry `working_dir` and bounded typed projections of recent task results (identity, outcome, workspace facts, artifact references — never raw result text), and a project-room turn additionally receives the thread's most recent task result in the same bounded form. That lookup reads the registry row's durable `last_task_result_id` pointer first (stamped at project-task finalization), fetching one file directly regardless of how many newer foreign results exist; an absent or stale pointer falls back to the bounded newest-first mtime scan, then to a disclosed full-store scan (the lazy self-heal for pre-pointer projects; with zero matching results nothing is written back, so it repeats per lookup until a matching result exists). Only the absent-pointer case writes the pointer back from the scan: a non-empty pointer that failed to resolve is typically a split-drive result whose canonical copy-back has not landed yet, and overwriting it would regress the pointer to an older result. On a Swarm router turn the host-owned room still chooses scope, but only on a genuine conflict: in a projectless room an explicitly passed `project_name` is inherited, and the project is created and bound before the root launches.

Canonical owner routing and project UI are projections over those same task, chat, binding, registry, and result authorities. A routing receipt proves admission or mailbox delivery, not task completion; an unread indicator proves a visible revision, not memory isolation. This keeps room organization, focused context, and durable project facts useful without fragmenting identity or creating a second scheduler, ledger, or review system.
### Skills and extensions

Skill capability grows through distinct gates: discovery and manifest parsing (`skill_loader.py`), content-hash-bound review (`skill_review.py` / `skill_review_runner.py`), owner grants, dependency reconciliation (`marketplace/isolated_deps.py`), enablement, readiness (`skill_readiness.py`), and execution (`tools/skill_exec.py`). Discovery establishes identity, source, provenance, conflicts, and hash; it does not confer trust. Review status, grants, enablement, and dependency health remain independent durable facts under `data/state/skills/<name>/`. A visible or enabled skill is not executable until `skill_readiness_for_execution()` says the current payload satisfies every required gate.

A skill may additionally declare a reviewed `presence:` behavior profile. The profile carries instructions (inline or in a reviewed payload file), full knowledge topics, `main`/`light` runtime defaults with a bounded inline-round limit, and portable capability requests. Installation-local selections resolve those requests to exact built-in, extension, MCP, script, or confined resource targets. Admission requires the bound behavior skill to be installed, enabled, freshly reviewed, and complete for every required request, then compiles one immutable positive capability ceiling. The registry exposes and executes only targets inside that ceiling; the ceiling is copied through `task_contract` rather than reconstructed from mutable skill or Settings state during a turn.

Bundled native skills and editable marketplace/user payloads occupy separate payload-plane buckets, while owner and review state stays outside the payload. Declared conflicts are symmetric between enabled peers and never cause either payload to be deleted. `extension_loader.py` and the isolated-dependency layer load only a ready, hash-matching extension; a review PASS alone does not prove that its dependencies were installed or that its widget/extension can load.

`skill_lifecycle_queue.py` serializes install, update, review, grant, enable, and removal work and exposes queued/running/succeeded/failed plus stale metadata. Stale is recovery evidence, not a fake unlock of a still-running thread. Scheduled work is reconciled by `resync_skill_schedules()` and can run only after `skill_readiness_for_execution()`. Schedule evaluation is a DST-aware system using the shared cron/timezone contract. Evolution remains hard-blocked in `light` runtime mode. These separations let skills expand capability without turning discovery, a UI toggle, or old review state into execution authority.

#### Skill publication

The passive installed-skill projection never launches Betterleaks and never claims that the current bytes are publication-ready. It exposes visibility and the independent `task_start_allowed` fact. Selecting Publish calls `POST /api/skills/{skill}/publish-preflight`, which resolves one current payload, captures and scans its bytes, recomputes review staleness, and returns exactly one backend-authored state: `ready`, `warnings`, `needs_attention`, `repairable`, or `hard_block`. The browser only renders those facts. Warnings require explicit continuation; `needs_attention` and `repairable` still admit the ordinary managed `skill_publish` task; only `hard_block` prevents task creation.

The authoritative flow is:

`passive index (no scan) → selected preflight → explicit confirmation → ordinary managed task → fresh immutable capture bound to the stored review hash → payload scan → GitHub read-only planning → derived-output scans → first GitHub mutation → validated same-skill pull-request receipt → ordinary acceptance`

Every outbound byte derives from the capture; the mutable live payload is neither reread nor rehashed to authorize the transaction. Literal Betterleaks `high` findings block the current outbound call, while all lower or unknown confidence remains a redacted warning.

Packaged installs resolve the bundled `betterleaks-standalone` resource. Source checkouts resolve the exact managed runtime installed explicitly with `python -m ouroboros.betterleaks_runtime install`; Publish never downloads it. Recoverable failures return typed stage, completed-effect, and repair-hint facts to the ordinary agent loop under the DEVELOPMENT LLM-first rule.

A top-level `skill_publish` task can be accepted as successful only when pre-truncation metadata contains a validated pull-request receipt for the requested skill and configured Hub repository. The receipt never manufactures PASS, earlier failed attempts remain visible, and a later valid same-skill receipt may satisfy the narrow publication prerequisite.

### MCP and browser-facing external tools

`mcp_client.py` owns configured HTTP/SSE and local stdio MCP discovery and invocation. HTTP/SSE entries validate URLs and auth headers. `secret_masking.py` owns the shared exact MCP token placeholder shapes used by status and Settings; load-time legacy repair remains intentionally limited to top-level Settings secrets and does not migrate pre-existing nested MCP values. Stdio entries pass one executable `command` and an exact string `args` list directly to the MCP SDK, without a shell, custom environment, or custom working directory; the SDK context owns process shutdown. Settings shows URL/auth fields for HTTP/SSE and command plus one-argument-per-line args for stdio. When MCP is enabled, successfully discovered tools join the selected initial capability envelope. Discovery failure produces an explicit capability omission through `list_available_tools`; it never silently removes an expected surface. Descriptions and results remain untrusted data, and every call still crosses registry, resource, safety, timeout, and result-handling policy.

Browser tools are stateful and thread-sticky because Playwright sessions and greenlets have affinity; they cannot be scheduled as ordinary parallel stateless calls. A stateful-tool timeout therefore RETIRES the browser generation (#409/#440): the shared `browser_state` slot is replaced with a fresh object, the abandoned worker keeps writing only into its retired one, and the close is queued on the retiring executor so it runs on the owning worker thread when the hung call settles — the cognitive lease closes on that cleanup's settlement, and a late infrastructure-error retry that observes a replaced generation closes only its own retired state. Generation isolation is best-effort under concurrent replacement (narrow interleavings around a hung call can still touch the successor); the class closes fully only with the process-isolated worker below. A worker whose call never settles keeps its retired session open — bounded in-process: at most `_RETIRED_GENERATIONS_MAX` abandoned live sessions per task, after which opening another browser session is a typed `BROWSER_BACKLOG_RETIRED_SESSIONS` refusal; truly reclaiming a hung session would take a process-isolated browser worker (disclosed future design). Every in-page evaluation goes through `_evaluate_bounded`: Playwright's `evaluate` accepts no timeout and ignores the session default, so an awaited never-resolving promise used to hang until the outer tool timeout. Racing the expression against an in-page rejection bounds the ASYNC class honestly and no further — a synchronous event-loop block cannot be interrupted from inside the page, and the outer tool timeout remains its backstop. The caller's timeout also becomes the session default (`page.set_default_timeout`) so the extraction calls that honor that default share one bound instead of a stale prior value; on the action path it is floored so the five-second action default cannot strangle a capture, while an explicitly larger caller timeout widens it. Chromium is the default. WebKit and device descriptors are targeted tools for a real Safari/iOS risk, not a universal acceptance matrix and not a claim that a narrow Chromium viewport is Safari-equivalent. First-party PR helpers are normal built-ins, but their mutating operations remain subject to selected-root policy, runtime mode, delegated-child/repair constraints, credentials, and reviewed-publication authority.

### Budget tracking

`usage_accounting.py` is the single monetary policy authority for core-mediated model work over the physical-attempt ledger. Every provider send has a unique attempt id and durable lifecycle `reserved → dispatched → settled | unresolved`, or `reserved → released` before dispatch. A marked `dispatched` row may enter `released` only through the typed pre-dispatch transport seam, which accepts connection/pool failures that prove no request bytes could have been sent; ordinary timeouts and unknown errors remain `unresolved`. Each retry is a new attempt. For every inspectable application candidate, that same attempt id binds exact post-transform raw/context identities and the existing-CAS physical manifest before dispatch; persistence or a host-bound Main shrink precondition can release the reservation without claiming a provider call. Specialized SDK/stream boundaries outside the selected candidate seam keep the lifecycle receipt but are labelled opaque rather than claiming an exact payload digest. The wrapper covers main and direct calls, children, scouts, all review surfaces, safety, synthesis, reflection, consciousness, transport/format retries, and opaque SDK calls. Root scopes include their task tree and post-task/review work exactly once. Opaque adapters reserve their declared maximum and settle from provider cost when available. A reviewed external script or extension with model credentials is represented as unknown/unmetered at each host-observed opaque execution boundary unless authoritative settlement exists; ordinary non-model skill work does not make the root non-final.

Before summary, reflection, or consolidation starts, the root freezes one shared ledger snapshot containing settled subtree cost, live reservations, unresolved upper bound, unknown/unmetered count, integrity, timestamp, and explicit non-final/partial state. All post-task consumers receive that same snapshot. The final terminal checkpoint remains the only final cost authority; a read failure is unavailable/null, never `$0`, and there is no reconciliation LLM or parallel cost ledger.

The in-task pacing stop is resolved once as a typed `CostCeiling`: `disabled`, `active`, `exhausted_soft_land`, or `unknown`. An active ceiling is the minimum of the configured percentage of global remaining budget and the root-tree cap minus one small absolute planning margin; either finite axis works without the other. The loop decides against subtree-accounted spend including in-flight holds, disclosing an own-cost fallback as a lower bound when tree accounting is unavailable. Graceful finalization runs before the ledger fence and never weakens that fence; unknown is not zero.

Pre-dispatch pricing is an exact-route, bounded, best-effort lookup from the provider's current catalog. Only the normalized exact model id and provider-supplied fields count. There is no manual price table, prefix inheritance, numeric fallback, or admission allowlist disguised as pricing. Unknown price is nullable and fail-open for model admission while already-known spend remains below its limits. It reserves `None` and settles from provider-reported cost or a later exact price; if neither exists, cost stays `None` and `cost_final=false`. Unknown is not zero and does not excuse an already-exhausted known budget or a known reservation that exceeds the remainder.

A rejection settles at confirmed zero only when structural provider evidence proves it happened before upstream generation with zero usage. Generic auth, quota, policy, timeout, and transport failures keep their unresolved bound unless equivalent evidence exists. `review_wave_admission` applies the same per-attempt math plus the root remainder before skill, plan, or task-acceptance reviewers are launched, and the managed-update assisted-apply admission floor reuses the same estimator against the global budget remainder (`remaining_usd_override`) before any destructive merge step; it does not govern the P3 commit gate itself. An unpriced slot is disclosed and contributes no invented price, while priced siblings still bind. This prevents one unknown route from disabling admission control for the rest of a paid wave.

Validation, reservation, transition, append, and fsync share one short cross-process lock; network work remains outside it. A torn tail is quarantined loudly, the validated prefix remains readable, and affected projections stay integrity-degraded and non-final because paid work may be missing. Failed settlement persistence leaves the attempt dispatched/unresolved. A root budget refusal is durable and may be cleared on resume only after proving that no paid dispatch occurred or that a typed replay-safe checkpoint exists.

Interactive `usage_breakdown` / `usage_projection` reads use the PR-140 validated-rows memo: `_read_new_records_locked` resumes from `LedgerResumeState`, validates device/inode, size, alignment, sequence, transition legality, and same-size rewrite signals, and folds only appended bytes. Any distrust falls back to the normal full locked replay, which alone may quarantine. The monetary write paths (reserve/settle/transition/release/legacy-import) read through their own in-lock warm cache of the last validated full read (`_usage_rows_memo._read_records_locked_cached`, razzant/ouroboros#129): the same resume-fingerprint discipline parses only appended bytes under the held lock, any doubt falls back to the authoritative full locked read, and the cache keeps full ordered records — unlike the memo's O(final attempts) — because seq assignment and whole-history append validation need them (bounded to 8 drive roots, LRU). `_append_rows_locked` also guards its byte boundary: a crash can leave a newline-less final row, and the append prepends the missing newline so a torn tail costs at most itself instead of welding onto the next row (#138). Both caches change read cost, never accounting meaning. The same memo also carries a fingerprint-keyed cache of finished `usage_projection`/`usage_breakdown` renders — cleared on refold and on every non-empty advance, never populated for a non-resumable crash-tail fingerprint, and served as deep copies — which again changes only the cost of a repeated read, never the meaning of the accounting.

For a root task, `GET /api/tasks/{id}` derives `cost_breakdown` at read time from the same ledger: own spend, child spend, unattributed spend, disclosed delegated spend, subscription sessions, unknown/unmetered and non-final rows, finality, and authority. It is never persisted and is not a third sum. Non-root details omit it; an unreadable or unattributable ledger omits the entire object rather than returning a confident zero.

`state.json`, task results, `llm_usage`, `/api/state`, and `/api/cost-breakdown` are compatibility projections only. Startup's resumable importer records source hashes, archives non-secret legacy evidence, imports only attributable usage, and represents ambiguous or residual history explicitly without rewriting source logs or fabricating attempts.
## 7. Configuration (ouroboros/config.py)

`ouroboros/config.py` is the SSOT for paths (HOME, APP_ROOT, REPO_DIR, DATA_DIR, SETTINGS_PATH, PID_FILE, PORT_FILE), process constants (RESTART_EXIT_CODE 42, AGENT_SERVER_PORT 8765), every settings default below, and the load/save/env machinery: `load_settings()`, `save_settings()`, `apply_settings_to_env()` (copies hot-reloadable runtime keys into `os.environ`), `normalize_runtime_mode()` (one clamp shared by the save path, the read coercion, and onboarding validation), `get_runtime_mode()`/`get_skills_repo_path()`, and `acquire_pid_lock()`/`release_pid_lock()`; `ouroboros/update_channels.py` owns `get_update_channel()`/`get_update_branch()`.

Settings file: `data/settings.json` under the data root — `~/Ouroboros/data/settings.json` by default, with `APP_ROOT`, `DATA_DIR`, and `SETTINGS_PATH` independently env-overridable. Access is file-locked. `secret_masking.py` is the wire-placeholder authority for known and owner-defined top-level secrets: `load_settings()` repairs only recognized disk placeholders BEFORE environment precedence is resolved, so a real environment credential is never classified as a mask, and `prepare_settings_for_persist()` applies the same top-level repair at the common writer boundary; nested MCP values are never silently migrated.

`ouroboros/openrouter_attribution.py` is the application-identity SSOT for every first-party paid OpenRouter request (canonical URL + `X-OpenRouter-Title`); a fork must use its own URL rather than competing to rename one app record.

### LLM output token budgets

Providers name the same output-token budget differently: OpenRouter/Anthropic-compatible calls send `max_tokens`, while every official direct OpenAI Chat route sends `max_completion_tokens` — a real provider-wire boundary, not naming style. Direct OpenAI also sends the requested `reasoning_effort` provider-wide; model-name prefixes are not capability authority, and only exact-route success-confirmed wire evidence may adapt a request. Runtime floors (numeric SSOT: the constants in code, pinned by `tests/test_max_tokens_constants.py`):

| Surface | Output-token budget |
|---------|---------------------|
| `LLMClient.chat()` / `chat_async()` defaults | 65,536 |
| Main task loop (`loop_llm_call.MAIN_LOOP_MAX_TOKENS`) | 65,536 |
| `LLMClient.vision_query()` and VLM tools (`analyze_screenshot`, `vlm_query`) | 32,768 |
| Review synthesis dedup | 16,384 |
| Chat block consolidation, era compression, scratchpad consolidation | 16,384 |
| Execution reflection and pattern-register update | 16,384 |
| Post-task summary (`agent_task_pipeline`) | 16,384 |
| Improvement-backlog grooming (`improvement_backlog.groom_backlog`) | 8,192 |
| Post-task evolution promotion decision (`post_task_evolution`) | 8,192 |
| Context compaction round summaries | 32,768 |
| Skill publish PR body generation | 8,192 |
| Background consciousness loop | 65,536 |
| Project naming LIGHT one-shot (`project_naming.llm_project_name`) | 256 |
| Provider Test (`llm_probe.PROVIDER_TEST_MAX_TOKENS`) | 16 |

### Default settings

A registry of `config.SETTINGS_DEFAULTS` (exact defaults stay canonical in `config.py`; this table is test-mirrored against it). Rows marked env-only are operator environment levers with no settings.json carrier.

| Key | Default | Description |
|-----|---------|-------------|
| OPENROUTER_API_KEY | "" | OpenRouter credential |
| OPENAI_API_KEY | "" | Official direct-OpenAI credential |
| OPENAI_BASE_URL | "" | Legacy OpenAI base-URL override |
| OPENAI_COMPATIBLE_API_KEY | "" | OpenAI-compatible endpoint credential |
| OPENAI_COMPATIBLE_BASE_URL | "" | OpenAI-compatible endpoint base URL |
| CLOUDRU_FOUNDATION_MODELS_API_KEY | "" | Cloud.ru credential |
| CLOUDRU_FOUNDATION_MODELS_BASE_URL | `https://foundation-models.api.cloud.ru/v1` | Cloud.ru base URL |
| GIGACHAT_CREDENTIALS | "" | GigaChat auth key |
| GIGACHAT_USER | "" | GigaChat user login |
| GIGACHAT_PASSWORD | "" | GigaChat password |
| GIGACHAT_SCOPE | `GIGACHAT_API_PERS` | GigaChat API scope |
| GIGACHAT_BASE_URL | `https://api.giga.chat/v1` | GigaChat base URL |
| GIGACHAT_VERIFY_SSL_CERTS | `true` | GigaChat TLS verification |
| GIGACHAT_PROFANITY_CHECK | "" | GigaChat profanity filter passthrough |
| ANTHROPIC_API_KEY | "" | Official direct-Anthropic credential |
| MINIMAX_API_KEY | "" | MiniMax credential |
| MINIMAX_REGION | "" | MiniMax region (empty resolves `global_en`) |
| OUROBOROS_NETWORK_PASSWORD | "" | Non-localhost HTTP gate password (`server_auth.py`; unset only warns — see §8 packaging note) |
| OUROBOROS_SERVER_HOST | 127.0.0.1 | HTTP bind host (`0.0.0.0` for Docker/non-loopback) |
| OUROBOROS_UPDATE_CHANNEL | `stable` | Update channel: stable/qa/development (§8) |
| OUROBOROS_MANAGED_UPDATE_FETCH_TIMEOUT_SEC | 300 | Managed-update fetch ceiling |
| OUROBOROS_RESCUE_GIT_TIMEOUT_SEC | 300 | Per-process ceiling on rescue Git commands |
| OUROBOROS_TRUST_NONLOCAL_BIND_WITHOUT_PASSWORD | unset | Env-only: `1` permits saving a non-loopback bind without a password |
| OUROBOROS_MODEL | google/gemini-3.7-flash | Main model |
| OUROBOROS_MODEL_HEAVY | "" | Legacy slot: readable for migration/history only, out of active routing |
| OUROBOROS_MODEL_LIGHT | openai/gpt-5.6-luna | Light model |
| OUROBOROS_MODEL_VISION | "" | Vision model (empty inherits) |
| OUROBOROS_IMAGE_INPUT_MODE | auto | Send-time image routing (`vision_routing.py`) |
| OUROBOROS_VISION_CAPTION_TIMEOUT_SEC | 90 | Caption-generation ceiling |
| OUROBOROS_MODEL_CONSCIOUSNESS | "" | Background-consciousness model (empty inherits) |
| OUROBOROS_MODEL_FALLBACKS | openai/gpt-5.6-luna | Cross-model fallback chain (`fallback_cooldown.py`) |
| OUROBOROS_MODEL_MAX_CONCURRENCY | 3 | Per-(model,route) concurrent provider-call cap (`model_concurrency.py`) |
| OUROBOROS_MODEL_SLOT_MAX_WAIT_SEC | 180 | Concurrency-slot wait bound |
| OUROBOROS_PROJECT_NAMING_TIMEOUT_SEC | 60 | Project-naming call ceiling |
| OUROBOROS_PROJECT_NAMING_ASYNC_TIMEOUT_SEC | 8 | Proactive-namer async bound |
| OUROBOROS_FALLBACK_COOLDOWN_ENABLED | true | 429-aware per-process model cooldown |
| OUROBOROS_FALLBACK_COOLDOWN_SEC | 120 | Cooldown window |
| OUROBOROS_FALLBACK_ATTEMPTS_PER_MODEL | 1 | Attempts per model in the fallback walk |
| OUROBOROS_REVIEW_NATIVE_MAX_ROUNDS | 16 | Native review-episode round cap (`review_native_episode.py`) |
| OUROBOROS_REVIEW_NATIVE_MAX_TRANSCRIPT_CHARS | 900000 | Native review-episode transcript cap |
| OUROBOROS_MODEL_DEEP_SELF_REVIEW | openai/gpt-5.6-sol-pro | Deep-self-review model |
| OUROBOROS_MAX_WORKERS | 10 | Worker-pool size |
| OUROBOROS_MAX_ACTIVE_SUBAGENTS_PER_ROOT | 6 | Live-subagent cap per root (hard cap 500 ids; depth hard cap 10) |
| OUROBOROS_MAX_SUBAGENT_DEPTH | 3 | Subagent tree depth |
| OUROBOROS_DISABLE_MANAGED_UPDATES | (unset) | Env-only: `1` disables managed updates (`git_ops.py`) |
| OUROBOROS_ALLOW_MUTATIVE_SUBAGENTS | (empty) | Mutative-subagent Auto override |
| OUROBOROS_SUBAGENT_WORKTREE_ROOT | (empty) | Acting-worktree root (empty derives `~/Ouroboros/subagent_worktrees`) |
| OUROBOROS_SUBAGENT_PROJECTS_ROOT | (empty) | Projects root (empty derives `~/Ouroboros/projects`) |
| OUROBOROS_SUBAGENTS | (empty) | Canonical configured-subagent roster (`configured_subagents.py`; §6) |
| OUROBOROS_SUBAGENT_HARNESS | (empty) | Legacy narrow harness input |
| OUROBOROS_SUBAGENT_PROFILE | (empty) | Legacy narrow profile input |
| OUROBOROS_DELEGATE_WAIT_SEC | 120 | Default delegate_wait window |
| OUROBOROS_DELEGATE_WAIT_MAX_SEC | 1800 | delegate_wait ceiling |
| OUROBOROS_DELIVERABLES_ROOT | (empty) | Deliverables root (empty derives the `~/Ouroboros/Deliverables` sibling; `tool_access.py`) |
| OUROBOROS_GC_RETENTION_DAYS | 7 | Unified GC retention (`retention.py`) |
| OUROBOROS_RESTART_DRAIN_MAX_SEC | 120 | Restart drain bound |
| TOTAL_BUDGET | 200.0 | Global budget (USD) |
| OUROBOROS_PER_TASK_COST_USD | 50.0 | Per-task cost cap; also the tree ceiling basis (`task_pacing.py`) |
| OUROBOROS_RUB_USD_RATE | (empty) | Manual RUB→USD rate for RUB-priced providers |
| OUROBOROS_PRICING_TTL_SEC | 21600 | Provider-catalog pricing cache TTL |
| OUROBOROS_TOOL_TIMEOUT_SEC | 600 | Default tool timeout |
| OUROBOROS_PER_CALL_TIMEOUT_CEILING_SEC | 1800 | Per-call timeout clamp |
| OUROBOROS_FINALIZATION_GRACE_SEC | 120 | Finalization grace window |
| OUROBOROS_WEBSEARCH_MODEL | gpt-5.2 | web_search backing model |
| OUROBOROS_WEBSEARCH_BACKEND | auto | web_search backend selection |
| OUROBOROS_MAIN_WEB_SEARCH | off | Main-loop inline web search |
| OUROBOROS_MAIN_WEB_SEARCH_ENGINE | auto | Inline-search engine |
| OUROBOROS_MAIN_WEB_SEARCH_MAX_TOTAL_RESULTS | 10 | Inline-search result cap |
| OUROBOROS_OR_PROVIDER | "" | OpenRouter provider-routing preference merged into requests |
| OUROBOROS_SEARCH_CODE_WALL_SEC | 45 | search_code wall-clock budget |
| OUROBOROS_PRESENTATION | (unset) | Env-only: launcher-exported presentation (`desktop_window`/`browser_fallback`; absent renders `web`) |
| OUROBOROS_USER_FILES_ROOT | "" (home) | Env-only: user_files jail root (empty = `$HOME`) |
| OUROBOROS_OBSERVABILITY_KEEP_RAW | unset | Env-only: truthy enables raw observability payload persistence |
| OUROBOROS_GENERATIVE_PROBE | 1 (on) | Generative-write probe toggle |
| OUROBOROS_GENERATIVE_PROBE_CHARS | 5000000 | Generative-probe size companion |
| OUROBOROS_REVIEW_MODELS | google/gemini-3.7-flash,openai/gpt-5.6-terra,anthropic/claude-opus-5 | Legacy triad reviewer roster |
| OUROBOROS_REVIEWER_SLOTS | (empty) | Structured reviewer slots (SSOT parser: `reviewer_slot_config.py`) |
| OUROBOROS_SUBSCRIPTION_PRESET_VERSION | (empty) | One-shot install-preset marker; endpoint-authored, DISK-ONLY (`ENDPOINT_AUTHORED_SETTINGS`) — its absence authorizes nothing, which is why install time is proved by three facts (§2) |
| OUROBOROS_SUBAGENT_PRESET_RECEIPT | (empty) | Install-preset receipt; endpoint-authored, disk-only |
| OUROBOROS_ONBOARDING_COMPLETED_AT | (empty) | Durable completion fact; endpoint-authored, disk-only |
| OUROBOROS_SCOPE_REVIEW_MODELS | openai/gpt-5.6-terra | Scope reviewer (≥1M window required for the blocking gate) |
| OUROBOROS_SCOPE_REVIEW_FLOOR | blocking_1m | Deprecated, enforcement-inert; scope applicability follows the owner context mode (BIBLE P1/P3) |
| OUROBOROS_TASK_REVIEW_MODE | auto | Task acceptance-review mode |
| OUROBOROS_SAFETY_MODE | full | Safety supervision mode (shipped default `full`; a fresh desktop wizard may author `light`); lowering is owner-guarded. Runtime mode is a self-modification boundary, not an OS sandbox (see `config.py`) |
| OUROBOROS_SAFETY_MAX_TOKENS | 2000 | Safety-check output budget |
| OUROBOROS_SAFETY_CALL_TIMEOUT_SEC | 60 | Safety-check call ceiling |
| OUROBOROS_WEBSEARCH_TIMEOUT_SEC | 480 | web_search ceiling |
| OUROBOROS_LLM_TRANSPORT_READ_TIMEOUT_SEC | 2700 | LLM transport read timeout |
| OUROBOROS_PLAN_TASK_DEADLINE_MIN_SEC | 300 | plan_task deadline floor |
| OUROBOROS_ACCEPTANCE_REVIEW_EST_SEC | 200 | Acceptance-review time estimate for pacing |
| OUROBOROS_REVIEW_MAX_CYCLES | "2" | Shared paid review-cycle cap across the plan/acceptance/commit/skill gates (`unlimited` = no local count cap; per-gate semantics: `review_cycles.py` docstring, §10) |
| OUROBOROS_ACCEPTANCE_MAX_IMPROVEMENT_PASSES | (retired) | Retired alias: a stored value is MIGRATED into `OUROBOROS_REVIEW_MAX_CYCLES` (passes + 1) at settings load; a leftover env value is inert |
| OUROBOROS_ACCEPTANCE_RESERVE_PCT | 5 | Acceptance budget reserve percentage |
| OUROBOROS_OBSERVABILITY_RETENTION_DAYS | unset | Env-only: observability retention (absent = preserved indefinitely; the reader never deletes) |
| OUROBOROS_REVIEW_MODEL_TIMEOUT_SEC | (unset) | Env-only: logical review timeout (absent = route-owned behavior; late in-flight results stay in custody) |
| OUROBOROS_REVIEW_MAX_TOKENS | 65536 | Env-only: reviewer output budget, clamped to the 8192 floor |
| OUROBOROS_REVIEW_ENFORCEMENT | advisory | Review enforcement: advisory/blocking (closed enum; anything else coerces to the default) |
| OUROBOROS_PREFLIGHT_TIMEOUT_SEC | 900 | Env-only: TOTAL wall-clock budget for the hermetic pre-commit pytest preflight (node lane + both passes; teardown + containment semantics in `preflight_runner.py`/`process_containment.py`) |
| OUROBOROS_PREFLIGHT_SERIAL | unset | Env-only: `1` selects one serial pytest pass; scrubbed from the candidate environment |
| OUROBOROS_AUTO_GRANT_REVIEWED_SKILLS | true | Auto-grant manifest-declared permissions to cleanly reviewed skills (hash-bound; blocking findings never grant) |
| OUROBOROS_TRUST_NATIVE_SEEDED_SKILLS | true | Launcher seed/resync writes hash-pinned `native_seed` verdicts; acts only at seed/resync, no runtime grant endpoint |
| OUROBOROS_CONTEXT_MODE | max | Owner context mode (`max`/`low`); also decides scope-review applicability — an explicit owner policy coupling, not an inferred model limitation (BIBLE P1/P3); owner routes/CLI only |
| OUROBOROS_CONTEXT_MODE_AUTO_LOW | false | Task-local low-mode overflow retry toggle |
| OUROBOROS_RUNTIME_MODE | advanced | Runtime mode light/advanced/pro — a compatibility/self-modification boundary orthogonal to review enforcement; light blocks registry mutation, mutative git/writer argv, and self-elevation; advanced blocks protected core/contract/release paths; pro permits edits but not unreviewed commits; the owner endpoint persists only the next-boot value |
| OUROBOROS_SKILLS_REPO_PATH | "" | Extra skills checkout path (expanded at read time, never cloned/pulled) |
| MCP_ENABLED | false | MCP client toggle (§6 MCP) |
| MCP_SERVERS | [] | MCP server list (HTTP/SSE via URL/auth, stdio via command+args); persisted in settings, never env-exported |
| MCP_TOOL_TIMEOUT_SEC | 60 | Per-MCP-tool timeout |
| OUROBOROS_HUB_CATALOG_URL | `https://raw.githubusercontent.com/razzant/OuroborosHub/main/catalog.json` | OuroborosHub catalog URL (automatic fetch limited to catalog JSON; installs verify SHA-256) |
| OUROBOROS_CLAWHUB_REGISTRY_URL | `https://clawhub.ai/api/v1` | ClawHub registry URL |
| OUROBOROS_SCOPE_REVIEW_MODEL | openai/gpt-5.6-terra | Singular compatibility fallback for OUROBOROS_SCOPE_REVIEW_MODELS |
| OUROBOROS_PROMPT_CACHE_TTL | 1h | Prompt-cache tier (default/5m/1h). The policy acts at the final send-time wire boundary so it can legalize provider ordering without prompt builders creating provider-specific TTL policy; `review_helpers.cached_prompt_blocks` and `usage_accounting._reservation_cost` also consult it; usage records the applied tier |
| OUROBOROS_EFFORT_TASK | medium | Task reasoning effort (scale none/minimal/low/medium/high/xhigh/max/ultra; Settings exposes all but `minimal`); provider adaptation is exact-route, success-confirmed, disclosed in `request_wire` |
| OUROBOROS_EFFORT_EVOLUTION | high | Evolution effort |
| OUROBOROS_EFFORT_REVIEW | high | Review effort |
| OUROBOROS_EFFORT_SCOPE_REVIEW | high | Scope-review effort |
| OUROBOROS_EFFORT_DEEP_SELF_REVIEW | high | Deep-self-review effort |
| OUROBOROS_EFFORT_CONSCIOUSNESS | high | Consciousness effort |
| OUROBOROS_RETURN_REASONING | true | Ask OpenRouter to return reasoning; direct/local request copies strip OpenRouter-only fields |
| OUROBOROS_REASONING_SUMMARY | auto | Readable reasoning-summary rendering; presentation-only, never added to history or returned to providers |
| OUROBOROS_SOFT_TIMEOUT_SEC | 600 | Legacy no-op; a non-default value emits a deprecation event; queue metadata retains the key until 7.0.0 |
| OUROBOROS_HARD_TIMEOUT_SEC | 1800 | Legacy no-op (same retention contract) |
| OUROBOROS_TASK_IDLE_TIMEOUT_SEC | 900 | Idle timeout — requires absence of real task/subtree progress; the typed in-flight main-LLM row spares only this rail; a settled child result stamps parent progress, because delivery creates immediate integration work and must not coincide with idle termination |
| OUROBOROS_TASK_ABS_CEILING_SEC | 21600 | Absolute task ceiling, activity-independent; deadline and budget stay separate hard axes |
| OUROBOROS_SUPERVISOR_LIVENESS_DEADLINE_SEC | 90 | Supervisor/direct-turn liveness watchdog — alerts and requests restart but never frees an in-process lock held by a genuinely wedged turn |
| OUROBOROS_PACING_INTERVAL_SEC | 600 | Pacing reminder interval |
| LOCAL_MODEL_SOURCE | "" | Local-model source (HF repo or path) |
| LOCAL_MODEL_FILENAME | "" | Local-model GGUF filename (split first-shard expanded) |
| LOCAL_MODEL_CONTEXT_LENGTH | 16384 | Local-model context window |
| LOCAL_MODEL_N_GPU_LAYERS | 0 | GPU offload layers |
| USE_LOCAL_MAIN | false | Route Main locally |
| USE_LOCAL_HEAVY | false | Legacy migration input only; excluded from active routing |
| USE_LOCAL_LIGHT | false | Route Light locally |
| USE_LOCAL_CONSCIOUSNESS | false | Route Consciousness locally |
| USE_LOCAL_FALLBACK | false | Route fallback locally |
| OUROBOROS_MAX_ROUNDS | 200 | Max task rounds (hot-reloadable) |
| OUROBOROS_TRANSIENT_RETRY_MAX | 6 | Same-model transient retry budget; pre-dispatch `transport_unavailable` is a separate task-bounded outer wait episode, because no provider attempt was admitted on that route |
| OUROBOROS_SKILL_LIFECYCLE_TIMEOUT_SEC | 1800 | Skill lifecycle-lane timeout |
| OUROBOROS_CLAUDEXOR_HARNESS_INSTALL_TIMEOUT_SEC | 300 | Harness install ceiling (kills the tracked group, typed refusal) |
| OUROBOROS_CLAUDEXOR_QUOTA_REFRESH_TIMEOUT_SEC | 90 | Quota-refresh POST ceiling (clamped 1–90) |
| OUROBOROS_BUNDLE_DIR | (unset) | Env-only: launcher-owned bundle root propagated to embedded children for Node/ripgrep discovery |
| OUROBOROS_BG_MAX_ROUNDS | 10 | Background-consciousness round cap |
| OUROBOROS_BG_WAKEUP_MIN | 30 | Consciousness wakeup floor (s) |
| OUROBOROS_BG_WAKEUP_MAX | 7200 | Consciousness wakeup ceiling (s) |
| OUROBOROS_POST_TASK_EVOLUTION | false | Post-task evolution promotion toggle; agent self-enablement is blocked at the shell/browser/settings/data-write guards; choosing an objective routes through the Main slot because it is a high-leverage decision, while execution stays behind ordinary review and owner gates |
| OUROBOROS_POST_TASK_EVOLUTION_CADENCE | llm | Promotion cadence `llm` or `every_n:k` (malformed normalizes to `llm`) |
| OUROBOROS_POST_TASK_EVOLUTION_BUDGET_USD | 0.0 | Remaining-global-budget start floor, not a cycle cap |
| OUROBOROS_EVOLUTION_PERSISTENT_OBJECTIVE | "" | Owner-only persistent campaign bias; still passes review gates |
| LOCAL_MODEL_PORT | 8766 | Local-model server port |
| OUROBOROS_HOST_SERVICE_PORT | 8767 | Host Service port (loopback-only; §12) |
| OUROBOROS_PRESENCE_MAX_ACTIVE | 2 | Cross-process Presence turn cap (UI-bounded 1–20) |
| LOCAL_MODEL_CHAT_FORMAT | "" | Local-model chat template override |
| GITHUB_TOKEN | "" | GitHub token (push/PR/issues) |
| GITHUB_REPO | "" | Personal `origin` repository |
| OUROBOROS_FILE_BROWSER_DEFAULT | "" | File Browser default root (explicit root required for Docker/non-localhost) |

Direct-provider review fallback (legacy name: OpenAI-only review fallback): when exactly one official direct provider is configured, `config.get_review_models()` compiles that provider's declarative reviewer-role sequence using provider-prefixed model IDs. Current scope covers official OpenAI, Anthropic, MiniMax, Cloud.ru, and GigaChat; OpenRouter, legacy-base, OpenAI-compatible, and mixed-provider configurations stay outside it. OpenAI and Anthropic run three independent Main-model slots; MiniMax mixes Main/Light; Cloud.ru and GigaChat use their one role model for every slot. `_exclusive_direct_remote_provider_env` returns empty when OpenRouter, legacy `OPENAI_BASE_URL`, OpenAI-compatible keys, or multiple official direct providers are present, and the fallback requires `provider_models.migrate_model_value` to make the main model already start with the exclusive provider prefix — exact prefix checking prevents an arbitrary free-text model from silently entering a single-provider route. This is part of the single-provider independence invariant (docs/DEVELOPMENT.md "Provider Independence").

GigaChat provider specifics (`gigachat::`): routed through the native `gigachat` library, not OpenAI-compatible (`llm.py::_chat_gigachat`). OpenAI `tools` map to GigaChat `functions`; at most ONE `function_call` returns per turn, so parallel `tool_calls` collapse to the first; role `tool` results become role `function` and must be valid JSON (plain text wrapped as `{"result": ...}`); the `system` message must be first, so later system-reminders demote to `user`. `reasoning_effort` is deliberately omitted — hidden reasoning can consume the whole output budget and return empty content. GigaChat exposes no automatic live cost source, so cost stays nullable/unknown rather than a hand-maintained tariff. GigaChat models sit below the 1M scope-review floor; when no ≥1M reviewer is configured, the declared alternatives are the owner-selected `low` context mode (whole-repo scope review declaredly not performed; each commit records a typed `skipped_low_context_mode` evidence row) or an owner-selected retrieving scope slot at ≥200K sourced evidence (BIBLE P3); the blocking triad still reviews the full staged diff in both modes.

---

## 8. Git Branching, CI, and Build

`ouroboros` is the local working branch; the runtime setting independently selects one official feed: Stable is the newest plain release tag reachable from both `main` and `ouroboros-stable`, QA is the `ouroboros-stable` tip, Development is the `ouroboros` tip. Promotion and rollback are owner-controlled exact-SHA movements; ordinary restart preserves the local tip, while explicit update owns fetch, target validation, rescue, apply, and rollback. `managed` is the official read/update remote; `origin` is optional personal persistence. Desktop and Colab reject a target without a regular non-empty `BIBLE.md`. External pull requests target `ouroboros`, do not allocate a release version, and receive the collision-free version when maintainers land and re-review them (procedure: CONTRIBUTING.md, docs/DEVELOPMENT.md).

The local `ouroboros-stable` ref is also a recovery fallback maintained by explicit promotion; that local role does not select the official QA feed. Launcher metadata is bootstrap provenance only — runtime status, preflight, Colab bootstrap, and apply resolve the selected channel and exact fetched SHA themselves, so an older frozen launcher cannot silently redirect updates. Stable additionally requires the shared plain release tag; QA and Development do not use version comparison as an admission gate.

### CI topology

`.github/workflows/ci.yml` has 13 jobs, grouped by trigger and secret exposure:

| Tier | Jobs | Trust boundary |
|---|---|---|
| Fork-safe PR validation | `quick-test`, `betterleaks-platform-smoke` | `ouroboros` pushes, pull requests into `ouroboros`, manual runs; read-only, no provider secrets, never `pull_request_target` |
| Stable/tag matrix | `full-test` (no secrets), `skill-smoke` (`OPENROUTER_API_KEY`) | `ouroboros-stable` pushes, manual runs, `v*` tags — never pull requests |
| Trusted provider run | `integration-test` | provider secrets; `main`/`ouroboros`/`ouroboros-stable` pushes, manual runs and `v*` tags (the release chain needs it) — never pull requests |
| Tag-only gates and release chain | `marker-guards`, `ui-smoke`, `docker-ui-smoke`, `docker-portable-test` (manual runs or tags, no secrets); `release-preflight` (needs `full-test` + `integration-test`) → `build` (signing secrets) → `release` (needs `build`, `release-preflight`, `skill-smoke` and the four tag-only gates); `vendor-package-smoke` needs `build` and stays informational | tag-triggered; a reproducible provider-contract failure blocks tag builds, a typed inconclusive provider outage does not |

Quick and full jobs each run a dedicated blocking `size_ratchet` pytest step — the ONLY enforcing surface for the repository size gates (local runs exclude the marker and warn): manifest exactness on the tip plus the pairwise shrink-only transition against the event base in `OURO_SIZE_RATCHET_BASE_REF`; an unresolvable base degrades to the tip's parent manifest verified against the parent's own tree — never a skip — while a resolvable base without a manifest fails closed. Both jobs also run the browser-module suite (`cd web && node --test tests/*.test.js`), the same node lane the hermetic commit gate executes through `ouroboros/preflight_node.py`. Secret-bearing skill review runs before any step that imports downloaded plugin code — untrusted payload code must never share a process with provider credentials — and a missing required key is red rather than skipped.

Tool-schema compatibility has two layers. Fork-safe PR tests build the complete shipped catalog without MCP or extensions, validate every schema as general JSON Schema plus the cross-provider subset (no empty enum, no root `anyOf`/`oneOf`/`allOf`), and require the OpenRouter/function, direct-Anthropic, GigaChat, and direct-OpenAI projections to preserve the complete tool-name set. The trusted `integration-test` lane sends that same full registry in one bounded `delegate_start` canary per physical route without executing the returned call — OpenRouter Gemini/Opus/GPT/Grok/DeepSeek, the three shipped direct-OpenAI defaults (Main alone keeps a second-turn nonce-bearing continuation), direct Anthropic, and optional MiniMax/Cloud.ru/GigaChat. Each canary requires positive usage, exact provider/model identity, and a normalized schema-valid tool call; quota/billing, 429, 5xx, and timeout outcomes stay typed inconclusive while contract/auth/model/tool/reasoning 4xx are red; one same-route resend is allowed only for a runtime-classified semantic-empty response (bypassing response caches where supported). `response_finish_reason` is retained in host usage for bounded diagnostics only; malformed raw arguments are reported by type/position and hash without copying the provider payload.

`claudexor-platform-gate.yml` proves the managed Claudexor runtime on three OSes: a fixture lane (fake harness, offline, $0) always, and a live lane only on explicit API keys — subscription auth stays deliberately out of CI, because an interactive machine-bound token must not enter CI secrets. `dependency-graph.yml` reads `ouroboros/claudexor_runtime_pin.json` and submits that direct runtime relationship to GitHub's dependency graph (runs only on pin/workflow changes on `main`/`ouroboros`, plus manual dispatch; `contents: write` only) without presenting Claudexor as a Python or Node package dependency. The Scorecard workflow (`.github/workflows/scorecard.yml`) runs on `main` pushes and weekly, pins every action by full commit SHA, defaults permissions to read-only, and adds only `security-events: write` + `id-token: write` for SARIF upload and OpenSSF publication. `CODE_OF_CONDUCT.md` owns community rules; `CITATION.cff` owns the software and preferred technical-report citations; `site/paper/index.html` owns the canonical paper landing page; `docs/benchmarks/evidence.json` holds the release-bound public benchmark projection; README remains the claim SSOT (guarded by `tests/test_trust_metadata.py` and `tests/test_public_site_metadata.py`).

Dev-facing release/review scripts: `scripts/run_external_review.py` (dual-lane external review; `--contributor` binding; `READY_FOR_INTEGRATION` is evidence, never merge authority), `scripts/contributor_review_evidence.py` (route-neutral contributor-packet binding), `scripts/run_plan_review.py` (the same engine as `plan_task`; review-exempt dev tool), `scripts/validate_scope_receipt.py`, `scripts/claudexor_platform_smoke.py`, `scripts/fetch_claudexor_runtime.py` (pin SSOT: `claudexor_runtime_pin.json`), `scripts/cleanup_test_pollution.py` (dry-run-first). `site/` is the Vite source of the public pages (`site/scripts/sync-assets.mjs` syncs assets); `skills/telegram/` and `skills/unix_computer_use/` are the bundled skills; `packaging/cli/` holds the CLI wrappers and installers; `packaging/appimage/` the AppRun dispatch + desktop metadata; `packaging/systemd/` the opt-in user unit (§1 Runtime topology owns its no-restart rationale).

### Build scripts

`build.sh` (macOS → .dmg), `build_linux.sh` (→ .AppImage + .tar.gz), `scripts/build_appimage.sh`, `scripts/build_linux_packages.sh` (.deb/.rpm/.red80.rpm), `scripts/smoke_linux_packages.sh`, `build_windows.ps1` (→ .zip), and `scripts/build_repo_bundle.py` (writes `repo.bundle` + `repo_bundle_manifest.json`, the manifest `launcher.py` bootstrap validates) are the release-invariant owners. Linux PyInstaller runs under the same pinned portable Python shipped in the payload, so its bundled libpython keeps the payload's glibc floor instead of inheriting the release runner's newer ABI. The AppImage builder wraps that payload with digest-pinned tool and runtime bytes; the native Linux builder wraps the same x86_64 payload without replacing its runtime. Native package metadata declares the external Git required by bootstrap while bundled Python/Node/browser stay under `/opt/ouroboros`; the packages install the opt-in user unit at `/usr/lib/systemd/user/ouroboros.service` with no activation scriptlet. The release-gating package smoke installs through `apt`/`dnf` and proves Git resolution, desktop files, the installed user unit's launcher/cgroup/no-restart contract, the packaged CLI, and a bounded desktop-launcher start on Ubuntu 22.04/Fedora 42; Astra Linux and RED OS vendor-image runs stay informational evidence, because third-party registry availability cannot block publication. The macOS image keeps the app + Applications symlink + optional CLI installer layout. Release tag prerequisite: `scripts/build_repo_bundle.py` is the release-tag SSOT and verifies the annotated `v$(cat VERSION)` tag points at `HEAD` before packaging.

Betterleaks follows the same release-resource discipline: packaged builds stage the pinned binary and license under `betterleaks-standalone`, source checkouts install the exact managed runtime explicitly under `data/state/betterleaks/`, and the Publish path never downloads it (§13).

Python dependency resolution has one authority: direct requirements and group membership live in `pyproject.toml`, `uv.lock` records the universal solution under the pinned `tool.uv.required-version`, and source/CI sync with `--locked` so metadata drift is an error. The two-interpreter packaging boundary rides projections: build scripts export their PyInstaller input directly from `uv.lock`, the committed `requirements-runtime.lock` supplies embedded `python-standalone` and managed updates (pip, no bundled uv), and a one-line `requirements.txt` pointer serves already-released updaters; CI regenerates the export and requires a clean diff, so neither file becomes a second dependency authority.

Platform builds precompile bundled Python with unchecked-hash bytecode: sealing valid bytecode prevents runtime `__pycache__` writes from invalidating a macOS signature, and runtime children route caches outside the bundle. When signing is enabled, hardened runtime, notarization, xattr hygiene, and strict verification remain part of the stable-release path; prerelease artifacts may be unsigned and their evidence must report the actual signing state.

The release proof begins with the final DMG/AppImage/tarball/ZIP, never its staging directory — validating a staging tree does not establish that the published bytes contain or execute the same payload. Each platform shard checks the embedded repository bundle, packaged CLI, and managed Claudexor seed + Node by starting the owned daemon, completing a fixture task, and verifying an identity-bound stop. The AppImage is extracted for metadata/SBOM inspection, then run FUSE-independently to prove version output, CLI dispatch, browser-fallback readiness, payload lifetime, main-executable libraries, and clean shutdown (browser-fallback evidence, not a claim of a native GTK/Qt backend); the nested cleanup proof follows the live `runtime → AppRun custodian → launcher` chain and requires both the extraction and its private base absent. The proven Linux tarball payload is wrapped into the three native packages, each receiving its own digest-bound package-manager smoke receipt and provenance attestation; a digest-pinned Syft build produces CycloneDX inventories from extracted payload bytes, and the tarball inventory is reused for the identical-byte native wrappers while each wrapper keeps its own digest-bound installation proof. The release job accepts only the seven expected assets, recalculates digests, verifies both predicate types, writes the checksum/evidence capsule, and rechecks the remote annotated tag immediately before publication. Signing credentials stay step-scoped and absent from SBOM/attestation steps.

Public installer naming and links ride the same projection: `release_sync.py::RELEASE_ASSET_TEMPLATES` is the filename SSOT shared by the proof builder, README, and the install pages. A version bump rewrites only named download references and `data-release-download` anchors to immutable `/releases/download/v{VERSION}/...` URLs; the `/releases/latest/download/...` shape is forbidden because GitHub excludes prereleases from `latest`. Generated release notes expose direct links only for the seven proof-accepted assets. The default README and GitHub Pages deployment use the stable `main` boundary (`main:/docs` for Pages); stable promotion advances `main` only after the release is published with all seven proof-bound installers, so an unreleased development VERSION never exposes dead installer links and an omitted promotion leaves the previous working release public.

### Docker

Docker runs the web and server runtime without PyWebView. The image binds `0.0.0.0` and sets no network password by default: a missing password only warns, and `NetworkAuthGate` permits requests when no password is configured — publishing the container port without setting `OUROBOROS_NETWORK_PASSWORD` therefore exposes the owner surface. Set the password (or keep the port unpublished); packaging adds no stronger boundary of its own.

---

## 9. Shutdown & Process Cleanup

Closing the window or quitting must leave zero orphaned work. Normal shutdown signals the lifecycle loop, lets the server lifespan stop workers and services, waits for the recorded server process group or Job Object, escalates only when it remains alive, performs launcher-owned orphan cleanup, and releases the PID lock. Ordinary server teardown closes its own Host Service listener; blind port sweeps are reserved for launcher cleanup, recovery, and panic. Those listener sweeps encode reserved-port ownership, not process identity: an arbitrary direct or development listener on the configured runtime port or Host Service port may be terminated even though the identity reaper itself spared it. A success signal is emitted only after recorded death is verified.

Panic is a complete owner stop, not a restart. It stops consciousness, records the durable evolution owner-stop state, closes the campaign and queued promotion request, writes `panic_stop.flag`, stops the local model and any daemon this process itself spawned, then kills tracked foreground commands, executor processes, services, workers, and their process trees before the hard server exit. A daemon merely attached to this process is deliberately not killed; custody reconciles that disclosed residual on the next manual start. The launcher performs its final sweep and closes the window. On the next launch the panic or no-resume flag suppresses automatic work until the owner acts.

Bounded foreground `run_command`/`run_script` processes ride the in-memory `_active_subprocesses` panic registry; long-lived children — services, executor-backed processes, extension companions, delegated runtimes — enter durable exact-identity process custody via `spawn_supervised` (§1 Runtime topology). Unix process groups and Windows Job Objects provide tree cleanup; durable executor and service records let the host recover after worker death. Normal cleanup may archive logs, while panic skips nonessential finalization — agent-controlled or wedged cleanup must not delay an owner stop. Timeout and signal exits remain distinct in tool results so a killed command never resembles success.

## 10. Key Invariants

1. **Constitution and identity persist.** `BIBLE.md` is never deleted; `identity.md` remains a physical file even when its content evolves.
2. **Release metadata has one projection.** `VERSION` is canonical; `ouroboros/tools/release_sync.py::version_carrier_desyncs()` and `sync_release_metadata()` keep the PEP 440 form in `pyproject.toml` and the editable root entry in `uv.lock`, plus the author-facing version in `web/package.json`, `web/modules/api_types.js::GATEWAY_CONTRACT_VERSION`, the README badge, and this document's header. Changelog prose remains deliberate. Pull requests into `ouroboros` leave these carriers byte-identical to their target; integration assigns the release version.
3. **Configuration and messaging have single owners.** Defaults and paths live in `ouroboros/config.py`; messages go through `supervisor/message_bus.py`; concurrent state transitions use the owning file lock.
4. **The attempt ledger is monetary authority.** `state/usage_attempts.jsonl` records every physical model send. State, task, event, and UI totals are projections carrying attempt identity; unknown or unresolved cost never becomes false zero.
5. **Packaged bootstrap is manifest-bound.** A packaged install verifies `repo.bundle` and its manifest once, then runs the managed checkout. Restart preserves its local tip; only explicit update applies an approved exact SHA.
6. **Shutdown is custody-complete** (§9). Normal close verifies child death; panic stops all owned work without allowing agent code to delay it; an intentionally attached-daemon residual is disclosed.
7. **This document is the present-tense map.** Structural owners, APIs, durable data, UI surfaces, and the rationale for non-obvious guards update here in the same commit as the code (documentation contract: docs/DEVELOPMENT.md; residue ratchet: `tests/test_docs_sync.py`); release chronology lives in git and README.
8. **Skill gates do not collapse.** Discovery, deterministic preflight, content-hash-bound executable review, owner grants, dependency readiness, enablement, and execution remain separate. A PASS does not install dependencies, and `enabled=true` does not prove executable readiness.
9. **Startup rescue has one mutation owner.** Supervisor recovery writes rescue evidence before reset or blocks while preserving the tree. Worker or agent construction remains warning-only and never stages or commits inherited dirt.
10. **Projection over replay.** Interactive status, history, and cost reads are bounded, non-materializing projections; durable owners perform the one authoritative replay or terminal materialization.
11. **UI resources carry a disposer.** Every subscription, listener, observer, timer, stream, and live page instance has explicit teardown; navigation does not leave hidden instances mutating visible or durable state.
12. **Frozen contracts extend explicitly.** `ouroboros/contracts/` is a versioned, backward-compatible ABI — typed shapes together with their parsing/normalization/policy helpers (§11.0). New capability extends the frozen shape or ships an explicitly versioned successor; existing consumers keep working.
13. **Provider wire adaptation stays exact-route and success-confirmed.** Canonical history remains provider-neutral; typed physical projections may change values, fields, or a registered dialect on one provider/endpoint/API/model only. Failed candidates teach nothing durable, task-local cognition degradation never becomes future dispatch authority, and the physical-attempt ledger remains distinct from terminal request-wire history.
14. **Cancellation is intent-then-custody.** A cancel intent never rides the canonical task status; the settle owner takes an exclusive claim BEFORE any custody mutation and every mutation is fenced by the claim generation (a probed-ALIVE claimant is never abandoned); a settled RESULT does not prove a dead WORKER, so live ownership gates every settled-target cancel while the worker-side snapshot check fails OPEN toward liveness; natural completion wins a late cancel and the stored terminal row survives byte-identical — the kill is about the process, never the result; the deliverable is durably registered as OWED before the intent settles, and a registration failure leaves the intent open for the watchdog; a cascade acts on lineage ROOTS (one cascade, one summary per tree) and settles only on its no-live postcondition. Owners: `task_lifecycle.py`, `cancel_intents.py`, `cancel_publication.py`, `terminal_delivery.py` (§5 for the flow).
15. **Registries read row-strict.** In every durable registry/projection, an absent file is an empty state while a malformed file or row refuses mutation loudly and quarantines with the bytes kept — never a {}-collapse over the whole store, never empty-therefore-clean.
16. **Verification receipts reconcile by one typed identity key.** Sameness is equality of the single (kind, value) key — never a match across kinds — so reconciliation is an equivalence that fails SAFE toward strictly fewer reconciliations; disclosed parts are never the comparison (`_outcome_receipts.py`).
17. **Review spend has one ceiling.** `OUROBOROS_REVIEW_MAX_CYCLES` caps paid cycles across the plan, acceptance, commit, and skill gates; per-gate semantics live in `review_cycles.py`, the mechanism in §6 Review stack.

### 10.1 Continuity data-flow map

The table below is the canonical map for continuity changes. A bounded view is
an interface projection, never a new authority. The actor that makes the
decision must be able to resolve the named source through an existing reader;
otherwise the view is partial and the consumer remains non-final or abstains.

| Surface | Canonical source and owner | Bounded projection | Actor-readable source/ref | Decision and retention rule |
|---|---|---|---|---|
| Owner authority and biography | Canonical `logs/chat.jsonl`, archive generations, and `memory/dialogue_blocks.json` owned by the canonical drive | Main/Project context sections and archive-aware history windows | Existing `chat_history`/archive readers with generation and gap metadata | A known gap is disclosed; summaries/blocks never replace exact current owner directives. Raw generations and durable blocks follow their existing retention owner. |
| Execution evidence | Task results, observability call manifests/blobs, service logs, and process-custody records | Status cards, terminal rows, bounded tails, and compact child summaries | Exact artifact/blob/service-log refs carried by the task result or canonical promotion | A projection cannot certify a missing child/source. Referenced canonical artifacts are promoted before child-drive GC; disposable execution scratch follows unified GC. |
| Terminal task/project memory | Root terminal result plus existing task/project summary producers | Cognitive Main terminal summaries and the two Project-root UI lifecycle rows (started + terminal completion) | Task-result ID, project binding, and summary/source refs | Summary is a biography projection, not raw evidence. Terminal outcomes, including failed/cancelled/degraded, remain retained through their canonical result owner. |
| Background Consciousness observations | `data/state/consciousness_observations.jsonl`, append-only enqueue/ACK rows owned by `BackgroundConsciousness` | Pending count/oldest metadata and a bounded recent observation rendering | `read_file(root='runtime_data', path='state/consciousness_observations.jsonl')` | Unacknowledged rows survive restart/overflow/error. Gaps block ACK and the existing direct identity rewrite; only a settled successful cycle appends ACK. |
| Plan/review authority | Exact task-artifact/observability wave bodies, evidence selectors, reviewer route/thread receipts, and the bounded review hot index | Review status, latest wave, obligations, and compact findings | Exact artifact/source handle plus SHA/range/thread selectors | Missing or partial evidence is `DEGRADED`/`NOT_RUN`, never PASS. Exact artifacts remain bound to the reviewed candidate SHA; hot indexes may rotate only after the source is retained. |
| Canonical versus execution roots | Canonical budget/data root owns identity, authority, biography, results, and promoted observability; execution drives own tools, workspace, and transient trajectory | Project/fork/task lenses and status projections | Existing canonical-root resolver, task-result pointers, and source handles | A fork is an execution lens, not a second mind. Copy-back/promotion precedes GC for anything referenced by a canonical result; missing legacy bytes become an explicit gap. |

---

## 11. Frozen Contracts v1 (`ouroboros/contracts/`)

`ouroboros/contracts/` is the frozen ABI package for the skill/extension layer: typed protocols and shapes TOGETHER with their parsing, normalization, capability, and policy helpers (`task_contract.py`, `skill_manifest.py`, `plugin_api.py`, `skill_payload_policy.py`, `chat_id_policy.py`, `schema_versions.py`, `tool_context.py`, `tool_abi.py`, and the `api_v1.py` compatibility re-export). The frozen property is backward compatibility, not absence of behaviour: shapes and helper semantics stay stable for existing consumers, and the protocols are verified against the real implementations by `tests/test_contracts.py`. The browser-envelope ABI is additive and lives in `ouroboros/gateway/contracts.py` with the JSDoc mirror `web/modules/api_types.js`, pinned by the contract/parity suites — not a second file in this package.

### 11.1 What is frozen

| Contract (one line) | File(s) | Anchored by |
|---|---|---|
| Claudexor login/status envelopes — `ClaudexorLoginJobResponse` (required top-level `job`) and `ClaudexorLoginJobProblem` (required `error`, optional `code`, bounded `required_actions`); the daemon envelope passes through verbatim | `ouroboros/gateway/contracts.py` + `web/modules/api_types.js` | `tests/test_gateway_parity.py`, `tests/test_claudexor_owned_daemon.py` |
| `ToolContextProtocol` — the minimum context surface tools may rely on | `ouroboros/contracts/tool_context.py` | `tests/test_contracts.py` (duck + AST checks) |
| Tool module ABI — `ToolEntryProtocol` + `GetToolsProtocol`; every registry entry satisfies it | `ouroboros/contracts/tool_abi.py` | `tests/test_contracts.py` |
| Browser WS/HTTP envelope families and `TaskCreateRequest` (optional caller metadata; `executor_ref` host-owned; costs nullable — whole-object omission over a confident `$0`) | `ouroboros/gateway/contracts.py` + `web/modules/api_types.js` | `tests/test_contracts.py`, `tests/test_gateway_parity.py` |
| Provider Test request/response (allowlisted request-local overrides; bounded errors) | `ouroboros/gateway/contracts.py` | `tests/test_gateway_parity.py`, `tests/test_provider_key_test.py`, `web/tests/provider_test.test.js` |
| Presence settings card + CAS update (reviewed defaults, local overrides, state fingerprint; CAS touches only presence-profile state) | `ouroboros/gateway/presence_settings.py` | `tests/test_extensions_api.py`, `tests/test_gateway_parity.py` |
| `client_surface` — optional closed-key normalized/bounded client descriptor, host-stamped, propagated to task metadata and chat history; absence is an explicit honest gap | `ouroboros/client_surface.py` | `tests/test_contracts.py`, `tests/test_gateway_parity.py` |
| `cancelable` + cancel-response `cascade` — additive fields with host-attested UI gating semantics | `ouroboros/gateway/contracts.py` | `tests/test_gateway_parity.py`, cancel/history tests |
| Executor-route projection — an opaque dispatch decision distinct from execution evidence; empty means native/no chip; the sticky renderer is `log_events.js` `executorChip` | `ouroboros/agent.py`, `ouroboros/subagents.py`, `ouroboros/gateway/history.py`, `web/modules/log_events.js` | `tests/test_claudexor_owned_daemon.py`, `web/tests/review_truth.test.js` |
| `execution_evidence` — started/settled/succeeded/failed counts, `delegated_run_failure_states`, `evidence_read_failed`, `nanny_nudge_recorded`, `subscription_cost_usd` (None while undisclosed — never 0), `subscription_cost_estimated`, `harness_models`, `applied_access_profiles`; derived from durable custody rows by `delegate_evidence.task_execution_evidence`, attached in `subagents.envelope_from_task` at terminal statuses only (never overwriting `effective_executor`/`executor_route`), and enriched onto the pushed `task_done` frame by `supervisor/subagent_task_truth.enrich_task_done_event`; `actual_substrate` ∈ harness_used/harness_attempted/native_only from custody evidence only; `substrate_result_fields` = {actual_substrate, delegated_runs_started, delegated_runs_settled, delegated_runs_succeeded, delegated_runs_failed, delegated_runs_source_unresolved, native_contribution}; the `wait_tasks` compact projection carries `dispatch_executor` plus that same set; an unreadable custody log omits the substrate claim and counts everywhere — `evidence_read_failed` means UNKNOWN, never "no run", and absence of evidence on a pre-evidence stored result is never a zero-run receipt; `native_contribution` is the constant "unknown" (no share/ratio is derivable from custody rows); a verifiable `native_only` amends `capability_delta` with `delegated_substrate_unused`; the `log_events.js` executor chip renders layered truth with unverified work-order counts spelled out | `ouroboros/delegate_evidence.py`, `ouroboros/subagents.py`, `supervisor/subagent_task_truth.py`, `web/modules/log_events.js` | `tests/test_execution_evidence.py`, `tests/test_terminal_delegation_receipt.py`, `web/tests/review_truth.test.js`, `tests/test_task_status_flow.py` |
| `TaskCostBreakdown` (root-only, read-time, never persisted; `accounted_upper_bound_usd`; `authority="physical_attempt_ledger"`) + `cancel_state: "pending"` with `cancel_reason` beside it; the browser's one consumer is `log_events.js` `taskCancelPending` | `ouroboros/gateway/contracts.py` | `tests/test_gateway_parity.py`, `web/tests/cancel_run.test.js` |
| Task hurry ABI — `POST /api/tasks/{task_id}/hurry` with exactly `{request_id}` (extra fields refused), `duplicate` = idempotent success; `OwnerHurryProjection` attempt-keyed states; consumers `log_events.js` `taskSoftStopPending`/`ownerHurryProjection` (task-card only) | `ouroboros/gateway/task_hurry.py`, `ouroboros/gateway/contracts.py` | `tests/test_owner_hurry_s3.py`, `tests/test_owner_stop_s3.py`, `web/tests/task_control_menu.test.js` |
| `StateResponse.active_direct_turns`/`active_chat_activities` (phases queued/working/finalizing/budget_paused — one predicate `budget_pause_fact` decides budget pause); `TypingOutbound` activity fields; `ChatOutbound.task_phase`/`task_terminal_status` | `ouroboros/gateway/contracts.py`, `supervisor/active_activity.py`, `web/modules/chat_activity.js` | `tests/test_gateway_parity.py` + the activity test files |
| `project_thread` stamp on all seven outbound frame types, stamped at the message-bus broadcast choke; a stamped frame is never adopted by Main (`chat_activity.mainThreadAccepts`) | `supervisor/message_bus.py`, `ouroboros/projects_registry.py` | `tests/test_message_bus.py`, `web/tests/chat_thread_routing.test.js` |
| Media/link envelopes — media `task_id`/`size_bytes`/`download_url`; `LinkAction {label,url}` with at most twelve absolute HTTP(S) actions; `links` in `WS_MESSAGE_TYPES`; `chat.links` host topic | `ouroboros/gateway/contracts.py`, `ouroboros/tools/core.py`, `ouroboros/event_bus.py` | `tests/test_contracts.py` |
| Owner quiz ABI — `QuizOption {label, detail?}`, `QuizOutbound` (quiz_id, question, options, stake, required `assumption`, lifecycle state open/answered/expired_terminal/superseded), separate `QuizStateOutbound` discriminator, `chat.quiz` host topic; the producer is the one escalation verb `escalate(question, options, stake, assumption)` — a ROOT asks the owner, a SUBAGENT delivers a typed frame to its nearest LIVE ancestor, which answers via `forward_to_worker` or escalates verbatim, so the owner sees only what no ancestor answered; answers arrive through the ONE ingress `POST /api/decisions` (family ids `quiz:{task_id}:{quiz_id}`, `routing:{client_message_id}:{routing_token}`; `interaction:` reserved), request-id idempotent, first answer wins, validated against the STORED options; `option_index` is optional for the quiz family alone — a comment-only answer writes NO `answered_index`, because a stored 0 would replay as "chose the first option"; injected as the typed `KIND_QUIZ_ANSWER` mailbox control and broadcast as `quiz_state`; expiry is structural only (the task-done seam flips open quizzes to `expired_terminal`) and history replay merges the projection state | `ouroboros/gateway/contracts.py`, `ouroboros/gateway/task_decision.py`, `ouroboros/owner_quiz.py`, `ouroboros/tools/core.py` | `tests/test_gateway_parity.py`, `tests/test_quiz_display.py`, `tests/test_quiz_answer.py`, `web/tests/chat_decision.test.js` |
| Managed update ABI — preflight, `UpdateMergePlan`, pinned apply, `update_status_ready` WS notice | `ouroboros/gateway/contracts.py` | `tests/test_update_apply_routing.py` |
| `ChatOutbound.review_projection` — bounded actor findings via `utils.truncate_review_artifact`, at most `MAX_PROJECTED_ACTOR_FINDINGS` rows (`review_execution_projection.py`) | `ouroboros/gateway/contracts.py` | `tests/test_review_substrate_v2.py`, `web/tests/review_truth.test.js` |
| Skill preflight statuses — `preflight_failed` is fresh-only; a stale failure surfaces as `preflight_failed_stale`; absence means the caller could not know | `ouroboros/skill_review_status.py` | `tests/test_skill_preflight_repair.py`, `web/tests/skill_preflight_repair.test.js` |
| `chat_id_policy` — the SSOT for human-visible vs synthetic chat ids across message bus, history, memory, and consolidation (§12) | `ouroboros/contracts/chat_id_policy.py` | `tests/test_chat_id_policy.py` |
| `task_contract` — normalization + `effective_acceptance_claims(task, closed_plan_wave)`, a pure read-time binder where ingress wins over a closed plan wave (acceptance reads fresh claims without mutating the live contract); pacing interprets the budget profile via typed `task_pacing.CostCeiling`; Presence promotion and follow-ups copy the ceiling by value (§12) | `ouroboros/contracts/task_contract.py` | `tests/test_contracts.py` |
| `PluginAPI` v1.4 — the full 16-method extension surface, `ExtensionRegistrationError`, `FORBIDDEN_EXTENSION_SETTINGS`, `VALID_EXTENSION_PERMISSIONS`, `VALID_EXTENSION_ROUTE_METHODS` (route methods mirrored against server dispatch by `test_extension_route_methods_contract_matches_server_dispatch`); `skill_job_dir(job_id)` creates `jobs/<sanitized>-<hash>/{assets,output,tmp}`; host-mediated permissions (`companion_process`, `supervised_task`, `subscribe_event`, `inject_chat`, `presence`) require review/owner grants; the `ExecutionMode` capability matrix is the SSOT for what a per-call child can proxy | `ouroboros/contracts/plugin_api.py` | `tests/test_contracts.py`, `tests/test_extension_loader.py` |
| `SkillManifest` — unified frontmatter (instruction/script/extension), reviewed `scheduled_tasks` cron metadata, bounded canonical `conflicts`, `presence:` block parsed by `presence_profile.py`; `parse_skill_manifest_text()` tolerates missing optional fields and `validate()` returns warnings without raising | `ouroboros/contracts/skill_manifest.py` | `tests/test_contracts.py` |
| `schema_versions` — opt-in `_schema_version` stamping (`with_schema_version`/`read_schema_version`); wired by extension `health.json`, the projects registry/bindings, and presence bindings | `ouroboros/contracts/schema_versions.py` | `tests/test_contracts.py` |

### 11.2 What is NOT frozen (intentionally)

The full `ToolContext` dataclass (browser state, review history, model overrides, …) stays mutable implementation detail — the protocol pins only the minimum. Raw WebSocket/HTTP *values* are unpinned; only the shape keys are. The SKILL.md body is free-form; only the frontmatter schema is pinned. `state/state.json`, `queue_snapshot.json`, and `task_results/*.json` carry no `_schema_version` key and read as version 0.

### 11.3 What to do when extending

Add the field to the active frozen owner — `ouroboros/contracts/` for the package ABI, or `ouroboros/gateway/contracts.py` + `web/modules/api_types.js` for browser envelopes — keeping existing consumers working, and enforce the new surface in the contract/parity tests (CHECKLISTS item 17, `gateway_parity`, owns the review-time criteria). Removing anything from 11.1 is a deliberate ABI break: it requires an explicitly versioned successor and a migration note in the release row — the release ledger, not this map, is the SSOT for retirements.

---

## 12. Host Service, Companion Processes, and Chat IDs

The Host Service is a loopback, authenticated callback boundary for reviewed skills (`ouroboros/gateway/host_service.py`, `127.0.0.1:${OUROBOROS_HOST_SERVICE_PORT:-8767}`). Every request authenticates an opaque `x-skill-token` bound to the skill's content hash, executable review, enablement, and grants — secrets never enter the token, a payload edit stales it, and the client wrapper refuses stringification (`skill_token.py`). The frozen route family is exactly: `/identity`, `/tools/schemas`, `/chat/allocate-internal`, `/chat/inject`, `/presence/turn`, `/presence/work/{work_ref}`, `/ui/ws-message`, and WS `/events`; permissions still decide which route works for a given skill. Review of a transport skill evaluates identity binding, attribution, polling bounds, panic cleanup, token confinement, and exfiltration — an owner-bound reviewed transport may be a first-class control surface, not a screen-only integration. External slash commands bind a separate positive-identity external owner slot (`supervisor/state.py`), so an unidentified transport can never bind commands and the local web owner can never lock out a real remote owner. `wait_for_response` on an injected chat message requires an A2A-allocated chat: only A2A chats have single-conversation semantics — on a human chat the first non-progress frame can be any concurrent task's answer.

Presence flow: `POST /presence/turn` requires the content-hash-bound `presence` permission, one `binding_id`, one exact transport event, and optionally staged files confined to the skill's state root; the binding resolves from `state/presence_bindings.json` with exact provider/account/conversation/thread origin verification; cross-process locks enforce the installation-wide cap and serialize one `conversation_key`; a stable event-derived task id makes transport retries idempotent; input and output join ordinary dialogue history with full transport/actor provenance. The one typed outcome is message/silent/tool_delivered/deferred — `deferred` only with a correlated `work_ref`, because an unanchored "deferred" would be an unanchored promise — and `GET /presence/work/{work_ref}` polls the bound late result without exposing the general task API. Promotion of Presence work into a managed task clears requested Project/workspace/source widening: a public conversation may promote long work but cannot choose new authority, and the cost ceiling plus return destination follow the promoted root by value. `presence_cancel_work` acts only on a `work_ref` whose stored binding and conversation match the current turn; owner chat and Background Consciousness may `initiate_presence` on an existing enabled binding.

Companion processes are host-supervised: reviewed manifest-declared descriptors enter durable custody, reconcile after lifecycle changes and restart, and stop on disable/unload/panic; worker-side changes write durable reconcile requests (`state/extension_reconcile/`) rather than spawning server-owned children; restart-budget exhaustion persists a terminal reason in the skill's health state, cleared only by a later successful start. A companion's cwd is the reviewed payload directory, so a payload edit stales review before reload instead of silently mutating a live process. The live projection is `state/extension_companions.json`.

Chat IDs: `chat_id=0` is the Skill Review panel — a REAL destination, not falsy (every producer that tested `if chat_id:` dropped panel notices; delivery goes through `message_bus.notification_chat_route`); negative ids never enter a human stream; history replay EXCLUDES chat-0 rows from Main — explicit panel rows never become ordinary conversation history — and a chat-0 row reaches a project thread only via a durable lineage binding (the id policy SSOT is the §11.1 `chat_id_policy` row).

---

## 13. External Skills Layer

Native and external payloads live in separate data-plane buckets (`data/skills/{native,clawhub,ouroboroshub,external}`), with review, grants, enablement, dependencies, tokens, and health under `data/state/skills/<name>/` (§1 Data layout). Discovery and manifest parsing establish identity, source, hash, provenance, and conflicts — never trust; a conflict declared by either enabled peer is enforced symmetrically without deleting either payload.

The executable sequence is install → deterministic preflight → hash-bound multi-model review → grants → dependency readiness → enablement → execution, and the gates stay independent: a review PASS installs no dependencies, `enabled=true` does not prove readiness, and an extension additionally needs host registration (§10 invariant 8). Mutating lifecycle work flows through one deduplicated queue (`skill_lifecycle_queue.py`); review jobs retain task/source/hash/attempt/actor/terminal evidence in the private full record, with the compact UI history as a projection. Review ordinals are allocated only after a job starts under the lifecycle lock — retry history stays explainable across hash changes without a UI counter becoming review authority; a started failure/cancel/timeout consumes its number with one idempotent terminal row, while pre-start dedupe consumes none. Accepted rebuttals reduce reviewer thrash; a new payload hash still requires fresh evidence.

Skill review combines the deterministic preflight with the authoritative multi-model checklist review; an optional advisory stays fail-open and cannot replace it. Official catalog payloads get their reduced-noise profile only when the sidecar, catalog file set, local file set, and every SHA-256 match exactly. A deterministic preflight failure persists as PENDING, not BLOCKERS — BLOCKERS could be overridden under advisory enforcement, while PENDING is non-executable in every mode.

`skills/telegram/` is the bundled transport: an in-process extension owns binding/polling/injection/settings while a supervised companion owns the sidecar, tunnel, menu rollback, heartbeat, and singleton; the text bridge works when the Mini App/tunnel is unavailable. The payload is seeded with hash-bound native provenance and stays disabled until token/permission grants; it is never a marketplace install. Its state classes stay separate under `data/state/skills/telegram/` (settings/binding, companion config, menu rollback snapshot, delivery cursors, verified tunnel cache). Colab bootstrap waits for native discovery plus a fresh executable seed projection, grants only missing grantable items under the owner auto-grant policy, then enables.

Marketplace installs are bounded archives staged privately and landed atomically with per-file hash checks; install metadata drives isolated dependencies (`marketplace/install_specs.py`); manual instructions remain guidance, not execution. Extensions import through staged trees (`_stage_extension_import_tree` under `__extension_imports/`), so concurrent workers cannot remove a peer's live import and stale trees stay reclaimable. Per-call child processes may proxy tools/routes/WS/UI/settings/companion descriptors, while persistent subscriptions and supervised tasks require in-process or companion lifecycle — reported through the generic capability matrix, never inferred from a platform name. Isolated children run the same staged loader in a private base with a scrubbed env, so native crashes cannot kill `server.py`. In-process extensions are more powerful, which is why namespacing, declared permissions, per-skill tracking, and atomic unload are an executable contract rather than convention. Transport metadata records source and session generically; skill repair must enqueue a real managed `skill_repair` task with payload confinement and review authority — an ephemeral read-only turn cannot mutate or review the payload it was asked to fix, and the routing fix lives at task admission (`skill_repair_admission.py`).
