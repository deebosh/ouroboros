# C6 review packet — monetary usage-ledger compaction (CPL4-C6, owner 1A)

Lane: `v7next_c6`, base `74a03082`. Owner sanction: batch №8 item 1A
(2026-09-01) — compaction of `state/usage_attempts.jsonl` in its own reviewed
lane (monetary authority). Design note ratified before code:
`docs/v7next/DESIGN_USAGE_COMPACTION.md` (commit `a1063124`); implementation
+ pins in the follow-up commit; this packet + ledger section close the lane.
Round 2 (§6) is the fix-round for the external adversarial wave against
`e2801c52`. Round 3 (§7) is the fix-round for the second wave, against
`830aa35a`: five findings were re-opened as still-open (1, 2, 3, 4, 6) and
all five are fixed here; four (5, 7, 8, 9) the wave confirmed closed.
Round 4 (§8) is the fix-round for the third wave, against `d7b487ab`: the
lock's exclusion becomes kernel-enforced, ownership is proven adjacent to
every irreversible decision, the swap re-proves its snapshot inside the
atomic replace, and the archive symlink bound moves from check-then-use to
the open itself (dir-fd `O_NOFOLLOW`).

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
| `tests/test_usage_compaction.py` | NEW pin suite (49 tests after round 4 and its verification; the suite entered the 1001-1500 size band with a recorded rationale) | see §3, §6, §7 and §8 |
| `tests/test_lockfile_helpers.py` | +5 lock-ownership pins in round 3, +2 in round 4 | the finding-1 fixes are platform primitives, so they are pinned where those primitives live |
| `ouroboros/platform_layer.py` | lock ownership: `_lock_identity`, inode-guarded stale eviction and release, ownership-reporting heartbeat | round 3, finding 1 |
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
- round 3 adds sixteen more (five of them in `tests/test_lockfile_helpers.py`)
  for lock-file ownership on eviction/release/renewal, the pass abandoning a
  lost hold, heartbeats inside the long span, writer exclusion at the swap and
  the absence of any unlocked fallback, the post-swap re-read, retry
  durability of the directory chain, the archive epoch anchor and its orphan
  tolerance, source-range provenance, the segment-cache windows, and archive
  symlink bounds (§7)
- round 4 and its verification add eight (two of them in
  `tests/test_lockfile_helpers.py`): two racing reclaimers yield at most one
  holder, a heartbeat after an atomic replacement of the lock file answers
  False, an append between the pre-swap re-check and the rename aborts
  without loss, a hold lost at the archive is seen before the snapshot
  re-check is even asked, a hold lost after the re-check aborts before the
  swap, a hold lost WHILE the candidate temp is written refuses the replace
  (the verification round's panel fix), and a link planted after the bound
  check can neither receive (writer) nor serve (reader) history (§8)

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
6. **Orphan archive segments** (round 2, widened in round 3): a pass that
   loses the snapshot race, dies at its swap, or is abandoned by a lost lock
   can leave a written-but-never-referenced segment. It carries no money and
   no chain authority — readers start at the live header and follow only what
   it names, and the epoch anchor recognises an orphan of the live generation
   as legal. Repeated lost races nevertheless accumulate disk: LOW
   availability / forensic clutter, not correctness. No GC by design (§13 of
   the note).
7. **Warm segment-cache window** (round 3): the per-segment cache hit needs a
   matching fingerprint, an mtime settled for > 2 s and an entry younger than
   60 s. An in-place same-size rewrite that ALSO restores `mtime_ns` exactly
   can therefore still be answered from a warm entry for up to a minute.
   Closing it means re-hashing every segment on every question — the
   quadratic cost the cache exists to remove — for an attacker who already
   has write access to the data root and can be caught a minute later, by any
   other process, and by the chain hash on every segment an answer depends
   on.
8. **Ownership is defended, not guaranteed** (round 3): the lock primitives
   are ownership-exact and the pass heartbeats through its long span, but no
   claim is made that a pass can never be robbed of the lock — only that it
   cannot finish while robbed (a lost or unanswerable heartbeat aborts,
   leaving the ledger byte-identical), and that no writer can append in the
   compare→replace window, because every writer of this ledger takes the same
   owner-aware lock and has no unlocked fallback.
9. **Epoch anchoring reads content, not names** (round 3): a segment whose
   first row cannot be read (a torn segment from a crashed write) is treated
   as no evidence of any generation rather than as corruption, so a garbage
   file dropped into the archive cannot deny service to the whole history.
   Every segment an answer actually depends on is still fully verified by the
   chain walk.
10. **Kernel enforcement has platform tiers** (round 4): on POSIX the lock is
    flock-held and a live-but-WEDGED holder can no longer be evicted by age —
    the deliberate trade of an availability incident (the wedged writer must
    die first) for the correctness incident (age-evicting a live monetary
    writer). Windows cannot unlink an open file and has no `dir_fd`/
    `O_DIRECTORY`; filesystems without kernel-lock support (bare NFS) refuse
    the flock with a typed errno. Both tiers keep the round-3
    identity-re-check shapes as a best effort chosen by the platform
    predicate — disclosed here, never an exception swallowed. The epoch
    anchor's first-line reads (`_no_newer_archived_epoch`) stay path-based:
    they can only ever ADD a corruption verdict, never serve history.

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

Not changed by round 2, and deliberately so: the fold scope (§3 of the
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

## 7. Round 3 — second adversarial wave disposition (fix-round base `830aa35a`)

Verdict of the second wave: NEEDS FIXES. It re-read the round-2 fixes and
judged five of the nine findings still OPEN (1, 2, 3, 4, 6), closing 5, 7, 8
and 9. **All five accepted and fixed**; nothing was argued away. Each fix carries a pin verified RED against the exact mutation it
claims to catch, on this base, before the fix landed.

| # | round-2 verdict | what was still open | fix | red-first pin |
|---|---|---|---|---|
| 1 | HIGH, OPEN | ownership was never actually proven: stale inspection judged the path and then unlinked the path; release unlinked whatever now occupied the name; the POSIX heartbeat renewed the descriptor and answered success after it had been unlinked; `_beat` ignored the answer; nothing beat during the long build/verify span; and the snapshot compare→replace stayed a TOCTOU window | `platform_layer` now compares descriptor identity with path identity everywhere: the eviction removes only the exact file it judged (re-checked immediately before the unlink), the release removes only the file it still holds, and `refresh_exclusive_file_lock` returns an OWNERSHIP verdict. `_beat` aborts the pass on a lost or unanswerable hold and runs inside both candidate row walks and between every verification stage. The window is closed structurally — every ledger writer takes this same owner-aware lock with no unlocked fallback — and the swap re-reads what landed | `test_stale_eviction_never_removes_a_lock_re_created_under_it`, `test_release_never_unlinks_a_lock_that_was_stolen`, `test_heartbeat_reports_lost_ownership_instead_of_renewing` (+ deleted-lock variant) in `tests/test_lockfile_helpers.py`; `test_a_lost_lock_aborts_the_pass_instead_of_swapping`, `test_the_long_build_and_verification_section_beats_the_lock`, `test_no_writer_can_append_between_the_snapshot_check_and_the_swap`, `test_every_ledger_writer_refuses_when_the_lock_cannot_be_taken`, `test_a_swap_that_did_not_land_is_a_typed_failure_not_a_receipt` |
| 2 | HIGH, OPEN | durability was established only for the levels a pass CREATED, so the retry after a pass that died on its own fsync skipped directories that already existed but were not yet durable | `_mkdir_fsync_chain(path, root)` fsyncs the whole chain up to the data root unconditionally, every pass | `test_the_directory_chain_is_re_synced_on_the_retry_after_a_failed_pass` (fails the first pass on the directory fsync, then requires all three inodes fsync'd before the retry's swap) |
| 3 | HIGH, OPEN | the chain had no trusted live anchor — `compaction_epoch` is as mutable as the rest of the row, so repointing the header at an older genuine segment AND lowering the epoch walked a valid short chain; `pre_compaction_seq` was only required to increase | the archive anchors the stamp: no segment may carry a generation newer than the live one, derived from each segment's embedded header (content, not name), with an uncommitted orphan of the live generation explicitly legal. `pre_compaction_seq` must fall inside the header's declared source range | `test_repointing_the_header_at_an_older_segment_is_corrupt` — the forgery now copies `compaction_epoch` too, which the wave named as the pin's escape hatch; `test_pre_compaction_seq_must_name_a_row_the_named_source_held`; `test_an_orphan_segment_of_the_live_generation_is_not_a_rollback` guards the fix against over-reach |
| 4 | MEDIUM, OPEN | a fingerprint is not identity: an in-place same-size rewrite inside timestamp granularity, or with the mtime restored, kept the cache hit | a hit also requires an mtime settled for > 2 s and an entry younger than 60 s; past either, the bytes are hashed again. Remaining window disclosed as residual §5.7 | `test_a_rewrite_inside_the_timestamp_window_is_re_hashed_not_recalled`; `test_a_same_size_rewrite_is_caught_once_the_cache_entry_expires` |
| 6 | MEDIUM, OPEN | a symlink AT `archive/usage_ledger` escaped the resolved-parent bound, because segment and directory resolve through the same link | neither `archive/` nor `archive/usage_ledger` may be a link, the resolved directory must be exactly the resolved root's archive path, and no segment may be a link; the reader calls it corruption, the writer aborts its pass | `test_a_symlinked_archive_path_is_refused_by_writer_and_reader` (both levels, reader and writer) |
| 5, 7, 8, 9 | CLOSED by the wave | — | unchanged | unchanged |

ARCHITECTURE and the design note carried absolutes the wave was right to
call out (`never robbed of it`, unqualified `bounded`). Both now state the
contract with its residuals: ownership is defended and its loss is
survivable; the archive bound is exact about symlinks; the cache window and
the orphan segments are named where the mechanism is described (§5.6–5.9).

Round-3 code commits (author `ouroboros-agent`, single-intent): lock
ownership in `platform_layer` + its pins; the pass consuming ownership
(heartbeat abort, span checkpoints, post-swap verify); the unconditional
directory chain; the archive epoch anchor + source-range provenance; the
segment-cache shelf life; the archive symlink bound.

## 8. Round 4 — third adversarial wave disposition (fix-round base `d7b487ab`)

Verdict of the third wave: NEEDS FIXES. It judged the round-3 ownership and
bound fixes still short of the contract in four ways — the exclusion itself
was still only a name protocol, ownership was not proven adjacent to the
decisions it licenses, the recheck→replace gap remained, and the symlink
bound was still check-then-use. **All four accepted and fixed**; nothing was
argued away.

| # | what round 3 left open | fix | red-first pin |
|---|---|---|---|
| 1 | exclusion rested on the O_EXCL name protocol: the stale eviction re-checked the inode and then unlinked the PATH (a pause between the re-check and the unlink lets a second reclaimer remove the first one's freshly won lock — two writers on one monetary authority), and the release had the same window between its look and its unlink | the lock fd HOLDS a kernel lock (`fcntl.flock`; `LockFileEx` on Windows) from acquisition; a stale lock is evicted only while flock-holding the very fd that was judged, with the path re-checked under that hold, and a release unlinks BEFORE its close, under the still-held flock. Windows (no unlink of an open file) and filesystems without kernel locks keep the re-check-then-unlink shape as a best effort chosen by the platform predicate — disclosed, never an exception swallowed | `test_two_racing_reclaimers_never_yield_two_holders` (both reclaimers herded into the check-to-unlink window; RED on the round-3 code with both returning descriptors); `test_heartbeat_after_an_atomic_swap_of_the_lock_reports_false` (the path never absent, so an existence check would renew; red against the utime-only mutation) — both in `tests/test_lockfile_helpers.py` |
| 2 | the pre-swap re-check and the rename were separated by the tmp write and fsync: a row appended in that gap was erased by the swap, receipt and all | `_write_bytes_atomic_fsync` takes a `precondition` evaluated after the temp bytes are durable, immediately before `os.replace` — the last instant the replace can still be refused; the compactor passes `_snapshot_intact`, so the pass aborts with the ledger (and the landed row) byte-identical | `test_an_append_between_the_recheck_and_the_replace_aborts_without_loss` (RED on `d7b487ab`: the row was erased and a receipt returned; now the pass returns `None`, the row survives, money = before + that row, no temp residue) |
| 3 | ownership was beaten through the span but not adjacent to the decisions: nothing proved the hold immediately before the snapshot re-checks, and nothing at all between the final re-check and the swap | `beat()` now runs immediately before EACH snapshot look: a hold lost at the archive write aborts before the post-archive re-check is even asked (its answer would be meaningless), and a hold lost after that re-check aborts before the replace — the proof before the swap was moved INSIDE the atomic replace by the verification pass (panel FIX_FIRST; see the verification block below) | `test_a_hold_lost_at_the_archive_is_seen_before_the_snapshot_is_trusted` (asserts exactly ONE `_snapshot_intact` call; the "remove the beat before the re-check" mutation makes it two — red against that exact mutation); `test_a_hold_lost_after_the_recheck_aborts_before_the_swap` (RED on `d7b487ab`: the swap ran) |
| 4 | the symlink bound was check-then-use: `_archive_dir_bounded` / `_segment_path` judged paths, then the write and the read re-resolved those paths — a link planted in between received the segment (writer) or served a foreign file (reader) | POSIX opens the chain root→`archive/`→`usage_ledger` `O_DIRECTORY\|O_NOFOLLOW` handle-to-handle and creates/opens the segment `O_NOFOLLOW` via `dir_fd`, fingerprinting and reading from the open fd; directory durability is fsync'd through the same held handles. The path-based checks remain as the early typed abort and as the Windows best effort (no `dir_fd`/`O_DIRECTORY` there), chosen by the platform predicate | `test_a_link_planted_after_the_writer_bound_check_cannot_receive_history` (RED on `d7b487ab`: the segment crossed the link and the swap completed); `test_a_link_planted_after_the_reader_bound_check_is_refused` (byte-identical copy behind the link — the hash cannot object, only refusing the traversal defends; RED on `d7b487ab`) |

Confirmed rather than changed: every writer of this ledger already takes the
same owner-aware lock with no unlocked fallback (round-3 pin stands; what
changed is that the lock they all take is now kernel-held), and the
post-replace re-read stays, now after the in-swap re-proof.

New/updated residuals (also §5): a live-but-WEDGED holder can no longer be
evicted by age on POSIX — the kernel lock outlives the staleness clock until
the process dies. That is the deliberate trade: age-evicting a live writer
was the two-writers defect; a wedged monetary writer is an availability
incident, not a correctness one. Windows and kernel-lockless filesystems
(bare NFS and friends) run the round-3 identity-re-check shape as a disclosed
best effort selected by the platform predicate. `ouroboros/usage_compaction.py`
entered the 1001-1500 size band with a recorded rationale (the dir-fd
anchoring and the in-swap re-proof live beside the pass they defend);
`ouroboros/platform_layer.py` stays inside the band at 1498 lines, paid for
by prose compression in the same module.

### Round-4 verification (base `d7b487ab`; the round-4 work had shipped unexecuted)

Round 4 was authored in an execution-denied environment, so a dedicated
verification pass ran every claim for real. One finding of the round-4
review panel (codex, FIX_FIRST, accepted by the coordinator) was fixed in
the same pass:

- **The ownership proof stood before the swap, not inside it**: `beat()` ran
  immediately before `_swap_ledger_fsync`, but the atomic writer can spend
  arbitrarily long writing and fsyncing the candidate temp before its
  snapshot look and `os.replace` — a hold lost in that window let a new
  holder's charge (landing after the in-swap snapshot answer, before the
  rename) be erased by the swap. The proof of ownership now lives in the
  precondition of the atomic replace itself: once the temp bytes are
  durable, immediately before the rename, ownership FIRST and the snapshot
  compare only under a proven hold (`_swap_ledger_fsync` passes `beat` into
  `_write_bytes_atomic_fsync`'s precondition). Pin:
  `test_a_hold_lost_while_the_temp_is_written_refuses_the_replace` — RED on
  the round-4-as-authored shape (ownership died with the temp on disk; the
  snapshot-only precondition let the rename run and a receipt returned),
  green with the fix: the replace is refused and the new holder's charge
  survives byte-for-byte, money = before + that charge.

Every round-4 red-first claim was then observed, not argued — each pin was
run against the exact mutation or base it names (mutation applied, pin RED,
mutation reverted, pin green):

| pin | mutation | red observed |
|---|---|---|
| `test_two_racing_reclaimers_never_yield_two_holders` | `platform_layer.py` reverted to `d7b487ab` | both reclaimers returned descriptors: 2 holders |
| `test_heartbeat_after_an_atomic_swap_of_the_lock_reports_false` | identity comparison removed from `refresh_exclusive_file_lock` (utime-only) | heartbeat answered True for a replaced lock |
| `test_an_append_between_the_recheck_and_the_replace_aborts_without_loss` | swap precondition removed entirely | receipt returned; the injected row was erased |
| `test_a_hold_lost_at_the_archive_is_seen_before_the_snapshot_is_trusted` | post-archive `beat()` removed | 2 `_snapshot_intact` calls instead of 1 |
| `test_a_hold_lost_after_the_recheck_aborts_before_the_swap` | in-swap ownership proof removed (snapshot-only precondition, no outer beat) | the swap ran; a baseline landed |
| `test_a_hold_lost_while_the_temp_is_written_refuses_the_replace` | round-4-as-authored shape (outer `beat()` + snapshot-only precondition) | receipt returned while robbed |
| `test_a_link_planted_after_the_writer_bound_check_cannot_receive_history` | `usage_compaction.py` reverted to `d7b487ab` | the segment crossed the link; the swap completed |
| `test_a_link_planted_after_the_reader_bound_check_is_refused` | `usage_compaction.py` reverted to `d7b487ab` | the byte-identical copy was read through the link (no raise) |

Windows tier: the two new lockfile pins exercise POSIX mechanics (flock-held
eviction; replacing an open, kernel-locked file), so both carry
`skipif(IS_WINDOWS)` with the disclosed-best-effort reason; the compaction
pins are platform-neutral, and the two planted-link pins already skip on
Windows. `fcntl` is imported only inside `not IS_WINDOWS` branches of the
`platform_layer` primitives, so the module imports cleanly where `fcntl`
does not exist.

Round-4 verification gate evidence (this host, isolated env roots, venv
python 3.10.12 / pytest 9.1.1): recorded in
`docs/v7next/LEDGER_CORRECTIONS.md` §"From the C6 fix-round 4 verification"
— targeted usage/lockfile suites green; CI-shape non-serial battery EXIT=0;
`-m serial` EXIT=0; `-m size_ratchet` green; `ruff check . --select F`
clean; `scripts/check_domains.py` OK; `scripts/regenerate_inventories.py
--check` OK; `git diff --check` clean; `git rev-parse HEAD` verified after
every pytest run. With that run recorded, round 4 is verified, not merely
code-complete.
