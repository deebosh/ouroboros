# C6 review packet — monetary usage-ledger compaction (CPL4-C6, owner 1A)

Lane: `v7next_c6`, base `74a03082`. Owner sanction: batch №8 item 1A
(2026-09-01) — compaction of `state/usage_attempts.jsonl` in its own reviewed
lane (monetary authority). Design note ratified before code:
`docs/v7next/DESIGN_USAGE_COMPACTION.md` (commit `a1063124`); implementation
+ pins in the follow-up commit; this packet + ledger section close the lane.
Round 2 (§6) is the fix-round for the external adversarial wave against
`e2801c52`: all nine findings accepted and fixed.

## 1. Diff map (what to read, in review order)

| surface | change | why |
|---|---|---|
| `docs/v7next/DESIGN_USAGE_COMPACTION.md` | NEW — the ratified contract | invariants, fold scope, decimal rule, seq policy, crash order, trigger, CPL-5 join |
| `ouroboros/usage_compaction.py` | NEW leaf (D16, ~490 lines) | fold policy + prove-then-swap + archive + history readers; imports FROM `usage_ledger`/`_usage_rows`, called INTO by `usage_accounting` — the one-way substrate seam is unchanged |
| `ouroboros/usage_ledger.py` | `_validate_records` learns the two baseline kinds | head-only baseline block, exactly one header at seq 1, group rows joined by `baseline_id` + positive `folded_attempt_count`; a baseline row in an appended tail or after any non-baseline row = corrupt. Everything else (locking, append arithmetic, quarantine, resume fingerprints) is untouched |
| `ouroboros/_usage_rows.py` | `_summary` + `_physical_call_count` baseline-aware | header skipped; group rows: count axes × `folded_attempt_count`, sums added once. Weight-1 paths are byte-equivalent to the previous code (pure refactor for existing kinds) |
| `ouroboros/usage_accounting.py` | +5 lines in `reserve_attempt` | the opportunistic trigger under the already-held monetary lock; contained (never raises into the reservation) |
| `ouroboros/config.py` | `USAGE_LEDGER_COMPACT_BYTES` (8 MB), `USAGE_LEDGER_COMPACT_RETRY_GROWTH_BYTES` (1 MB) | trigger policy from config SSOT, no env knob |
| `ouroboros/agent_startup_checks.py`, `ouroboros/context_budget.py` | warn-text updates only | the 20 MB WARN becomes the broken-compaction tripwire |
| `ouroboros/domains.toml`, `docs/DOMAIN_MAP.md` | new module seated D16; graph regenerated via `check_domains.py --write` | manifest completeness gate |
| `docs/PERSISTENCE.md` | usage-ledger row rewritten (bounded by compaction); NEW `archive/usage_ledger/segment_*.jsonl` row | inventory truth; scan pin 123→124 in `tests/test_persistence_inventory.py` |
| `tests/test_gateway_abi3_removals.py` | one per-site allowlist row | `_build_candidate` writes the internal ledger-plane `cost_usd` key (same class as every existing ledger writer row there) |
| `tests/test_usage_compaction.py` | NEW pin suite (31 tests after round 2) | see §3 and §6 |
| `ADOPTION_v7next.md` | CPL-4 row: C6 landed + verification hook | adoption gate |
| `docs/v7next/LEDGER_CORRECTIONS.md` | append-only C6 lane section | provenance |

## 2. Invariants to verify adversarially

1. **Money is decimal-exact.** Group sums are `Decimal`s of the exact JSON
   literals, carried as exact-decimal strings (`_number` accepts strings at
   every reader: validator, `_summary`, projections). Retained rows are
   verified Decimal-identical across re-serialization; a non-round-trippable
   foreign literal aborts the pass (never approximates).
2. **Prove-then-swap.** Commit happens only after the candidate bytes
   (a) re-validate structurally and (b) render EQUAL dicts through the
   production aggregation on every consumed surface (global summary, per-root
   summaries + min `root_limit_usd`, breakdown buckets on all five axes) and
   (c) match decimal money totals. Any inequality → abort → ledger
   byte-identical. Compaction is an optimization; it can only decline.
3. **In-flight rows never fold** (reserved/dispatched finals keep their whole
   chain, verbatim modulo seq) and their later transitions work unchanged.
4. **Idempotency-bearing kinds never fold** (subscription/external/legacy):
   their replay dedup + conflict checks read the live replay only. This is
   why their exclusion is structural, not an optimization choice.
5. **Crash-safety order**: archive segment written + fsync'd (file and every
   directory entry the chain created; POSIX failure is fatal, Windows is a
   disclosed no-op) BEFORE the atomic ledger swap, and the swap is refused if
   the live file changed since the snapshot. Crash anywhere = valid ledger
   (old or new generation); orphan segments harmless.
6. **seq policy**: dense-seq validation authority preserved by starting a
   fresh epoch; original seqs survive in the archive and as
   `pre_compaction_seq` on retained rows. Substrate append/resume arithmetic
   (`len(records)`-based) is deliberately UNCHANGED — check this holds.
7. **Concurrency**: everything under the existing monetary lock (`_locked`),
   which is owner-aware and heartbeaten so a long pass cannot be evicted by
   elapsed time; cache coherence is structural (atomic swap → new inode →
   every resume fingerprint refolds). No cooperative invalidation.
8. **CPL-5 join**: every pre-compaction `attempt_id` resolves through
   live ∪ `archived_attempt_ids` (hash-chained, tamper-evident, each segment
   bounded to the archive directory, revalidated as a ledger, cached per
   immutable segment BY FINGERPRINT, chain epochs stepping down to 1). The
   CPL-5 reverse sweep (NOT on this base — only its design note is) must
   consult this union and treat typed corruption as UNKNOWN/skip; contract
   recorded in the design note §10 and the ledger section.

## 3. Pins (tests/test_usage_compaction.py, 31 tests; round-2 pins in §6)

- exact money + whole-projection equality (incl. `skill_review_usage` waves)
- global + root budget refusal thresholds identical across compaction
- in-flight survival + post-compaction settle/release
- crash injection between archive and swap → byte-identical ledger, retry OK
- chained compactions: id resolution live ∪ archive; tampered segment raises
- subscription/external replay dedup + identity-conflict still enforced
- legacy import: rows retained; watermark-loss replay appends nothing
- trigger: config threshold gates the reserve path; growth-throttle after an
  unprofitable pass; verify-abort on a foreign non-canonical literal
- structure: baseline rows only at head (tail-smuggled row = corrupt; group
  without header = corrupt); quarantine + `integrity_degraded` on a compacted
  file unchanged; archive segment = exact source bytes, sha-pinned
- round 2 adds fifteen pins for lock ownership/heartbeat, the snapshot
  re-check, the archive directory chain and its fsync failure, header
  provenance and counts, bounded archive references, epoch-chain steps,
  segment revalidation, warm-cache integrity, typed corruption of an
  unreadable header, union caching, and decimal precision (§6)

Mutation-probed red (not just green-once): `_summary` weight math, group-sum
rounding, folding of dispatched rows — each flips at least one pin.

## 4. Gate evidence (this host, isolated env roots)

- targeted: usage family (7 files) green; budget family (5 files) green;
  persistence inventory + domain manifest + rotation train green;
  test_usage_compaction 16/16 green
- full CI-shape non-serial battery (`-m "not serial and not integration and
  not browser and not ui_browser and not ui_browser_docker and not
  portable_detail and not skill_smoke and not size_ratchet" -n 16 --dist
  loadscope --max-worker-restart=0 --timeout=300 --timeout-method=thread`):
  EXIT=0, ~13.4k outcomes, 0 failed (first run had exactly one red —
  the ABI-3 alias sweep discovering the new `cost_usd` emission site — fixed
  by the per-site allowlist row, battery relaunched whole and green)
- serial pass: EXIT=0 (622 passed / 39 skipped); size_ratchet: 5/5, exit 0
  (PIPESTATUS-preserved); `ruff check . --select F` clean;
  `scripts/v7next_adoption.py` OK; `git diff --check` clean;
  `git rev-parse HEAD` verified after every pytest run
- scale smoke: 24,000-row / 11.9 MB synthetic ledger → 183 KB (65×),
  280 groups, 1.16 s pass; projections byte-equal; post-compaction reserve
  correctly refused over the folded money (accounted $1228 > $200 limit)

## 5. Known residuals (disclosed, not defects)

1. Subscription/external/legacy/review-attributed rows never fold → slow
   residual growth on delegation- or skill-review-heavy installs; the 20 MB
   WARN now names exactly this case. A future lane may fold them behind an
   archived-identity membership check (design note §3).
2. The in-compactor render fingerprint mirrors the COMPOSITION of
   `usage_projection`/`usage_breakdown` (using the same production `_summary`
   / `_breakdown_bucket` primitives). A future divergence in that composition
   would weaken the self-check, not correctness (worst case: a lawful pass
   aborts); the end-to-end pin compares the real projection functions.
3. Directory fsync is a disclosed no-op on Windows (POSIX guaranteed and now
   FATAL on failure, round 2 / finding 2); worst case there is a lost archive
   dir entry AND a swap in the same crash window — mitigated by archive-first
   ordering, disclosed in the design note.
4. A float-boundary rounding coincidence can make the rounded projections
   differ pre/post → the pass aborts and the ledger simply stays uncompacted
   (correctness over availability; disclosed in design note §5).
5. CPL-5's sweep is not on this base; its contract (consult live ∪ archive;
   corrupt chain = UNKNOWN/skip) is recorded in the design note §10 for the
   lane that lands it. `model_send_seal`-targeted gates therefore do not
   exist on this base to run.

## 6. Round 2 — adversarial wave disposition (fix-round base `e2801c52`)

Verdict of the wave: NEEDS FIXES, nine findings. **All nine accepted and
fixed** — this is the monetary authority, so nothing was argued away as
theoretical. Every fix carries a pin that was verified RED against the exact
mutation it claims to catch (the mutation harness reverts one behaviour and
reruns the suite), and the three pins the wave called weak were rebuilt.

| # | wave finding | disposition | fix | red-first pin |
|---|---|---|---|---|
| 1 | HIGH — a long pass can be robbed of the lock (`stale_sec=90`, no `owner_aware_stale`), and a prior owner can unlink the new owner's lockfile; the swap then replays a stale snapshot over a concurrently appended charge | **accepted, fixed both ways** | `_named_lock` acquires owner-aware (a live PID is never evicted by age) and yields a heartbeat (`platform_layer.refresh_exclusive_file_lock`, descriptor-targeted so a stolen lock is never refreshed for the thief) that the pass beats at each checkpoint; **and** the swap is refused unless the live bytes still equal the snapshot, re-read under the same held lock right before the rename | `test_monetary_lock_is_owner_aware_and_the_pass_heartbeats_it`; `test_append_between_snapshot_and_swap_aborts_instead_of_erasing_it` (injected append → pass returns `None`, the row survives, money = before + that row) |
| 2 | HIGH — archive durability: only the segment's own parent is fsync'd, and `_fsync_dir` swallows every error, including on POSIX | **accepted, fixed** | `_mkdir_fsync_chain` syncs every directory entry the chain creates (segment parent, `archive/`, data root); `_fsync_dir` raises on POSIX and is a no-op on Windows *by the platform predicate*, not by a bare `except` | `test_archive_directory_chain_is_durable_before_the_swap` (fsync'd inodes recorded and required BEFORE the swap); `test_posix_directory_fsync_failure_aborts_before_the_swap` |
| 3 | HIGH — the baseline validator accepts a rolled-back hash chain and forged seq/epoch provenance; the archive reader scrapes ids instead of validating | **accepted, fixed** | substrate validates the stamp: epoch, bounded `archive_rel`, 64-hex sha, closing counts (`folded + retained == source`, first seq 1, last seq == source rows), block↔header agreement (`group_count`, summed `folded_attempt_count`), and `pre_compaction_seq` uniqueness/monotonicity under a stamp only. The reader runs each segment through `_validate_records` and requires the chain's epochs to step down by one to a header-less epoch 1 | `test_repointing_the_header_at_an_older_segment_is_corrupt` (three epochs; both skip shapes); `test_baseline_header_counts_must_close`; `test_pre_compaction_seq_is_a_checked_provenance_claim`; `test_rehashed_segment_still_fails_the_ledger_structure`; `test_a_group_row_cannot_rejoin_the_block_after_it_closed` |
| 4 | MEDIUM — a warm segment cache hides a deleted or replaced segment | **accepted, fixed** | the cache hit additionally requires the file's `(ino, dev, size, mtime_ns)` fingerprint; a miss re-reads, re-hashes and re-validates | `test_warm_segment_cache_revalidates_the_file_it_cached` (delete, then rewrite, both after a warm read) |
| 5 | MEDIUM — "decimal exactness" is bounded by the ambient 28-digit context, and the self-check rounds the same way | **accepted, fixed** | sums run under `_exact_money` (`prec=60`, `Inexact` trapped), so a loss past even that aborts instead of approximating; the pin's oracle sums in its own wider context | `test_group_sums_survive_beyond_the_default_decimal_precision` (10²⁸ + 1 keeps its last digit; red-first showed the dollar vanishing) |
| 6 | MEDIUM — `archive_rel` is not bounded to the archive directory | **accepted, fixed with 3** | `usage_ledger.valid_archive_rel` (textual bound, substrate-owned) plus a resolved-path bound in the reader (defeats a planted symlink) | `test_archive_reference_is_bounded_to_the_archive_directory` (six shapes rejected by the validator; an existing, correctly hashed file outside the archive rejected by the reader) |
| 7 | MEDIUM — a corrupt live header reads as "never compacted" | **accepted, fixed** | `_live_baseline_header` raises `UsageLedgerCorrupt` on an unreadable or non-object first row; `None` now means only "a readable row that is not a stamp" | `test_unreadable_leading_row_is_typed_corruption_not_absence` (the CPL-5 join raises → UNKNOWN, never an orphan verdict) |
| 8 | LOW — the join primitive re-unions the whole archived id set per question | **accepted, fixed** | the union is cached by chain identity ((`archive_rel`, sha) per hop); the stat-checked walk still runs, so finding 4's guarantee is not traded for the cache | `test_archived_id_union_is_built_once_per_chain` (H questions → exactly one union build) |
| 9 | MEDIUM — three pins do not pin what they claim | **accepted, all three rebuilt** | crash pin injects at `os.replace` itself and asserts the segment is already on disk with the exact source bytes (a swap-before-archive reorder now fails it); the threshold pin proves the lock is HELD at the call rather than trusting the call site; the head-only pin contrasts one unmodified baseline block that validates at the head with the same rows rejected purely for position | `test_crash_at_the_ledger_rename_leaves_ledger_intact`; `test_reserve_path_compacts_only_past_config_threshold`; `test_baseline_header_is_rejected_by_POSITION_not_by_shape` |

Not changed by this round, and deliberately so: the fold scope (§3 of the
design note), the per-group baseline shape the wave independently confirmed
preserves per-root enforcement and all five breakdown axes, the trigger
thresholds, and the ABI-3 allowlist row the wave found correctly scoped.

New residual disclosed by finding 1's fix: a pass that loses the snapshot
race leaves an orphan archive segment (already written, never referenced).
Orphan segments were disclosed as harmless before, and the archive is
append-only by design (§13); the alternative — swapping anyway — is the
defect being fixed.

Round-2 code commits (author `ouroboros-agent`, single-intent):
`9e99eb55` (findings 1, 2, 9-crash, 9-threshold), `0ed2dc2c` (findings 3, 4,
6, 7, 8, 9-position), `6b03212e` (finding 5 + the ARCHITECTURE ownership
line).
