# Design note — usage-ledger compaction (CPL4-C6, monetary authority)

Owner sanction: batch №8 item 1A (2026-09-01) — "seq-preserving compaction
snapshot (settled rows folded into a stamped baseline row + archive of the raw
segment)", excised from the CPL-4 persistence train into its own reviewed lane
because the ledger is the monetary authority.

## 1. Problem

`state/usage_attempts.jsonl` is the append-only monetary authority
(`ouroboros/usage_ledger.py`). Every reservation re-reads it under the
cross-process monetary lock; a ~20 MB ledger costs ~0.5 s per full re-read
under that lock (the 2026-07-23 lock-timeout incident;
`USAGE_LEDGER_WARN_BYTES` in `ouroboros/context_budget.py` warns at exactly
that point). The in-process warm caches (#129) bound the *steady-state* cost,
but every cold read (process start, refold on any doubt) still replays the
whole file, and the file grows without bound: each physical attempt appends a
2–4 row lifecycle chain that stays forever after it is terminal.

## 2. Sanctioned shape

Fold the terminal history into a **stamped baseline block** at the head of the
ledger and move the raw pre-compaction bytes, verbatim, into an append-only
archive segment. Nothing is deleted: the archive holds every original row
(with its original `seq`); the live file holds an exact, structurally
validated aggregate plus every row that is still live.

## 3. What folds, what never folds

| rows | disposition | why |
|---|---|---|
| `kind="attempt"`, final state `settled` / `unresolved` / `released`, and **no review attribution** (`review_skill`/`review_wave_id`/`review_slot_id` all empty) | folded (their whole seq chain) | terminal, id never re-asserted by any writer (`attempt_id` is a one-shot uuid4 minted at reserve time); aggregation-complete under §5 |
| `kind="attempt"`, final state `reserved` / `dispatched` (in-flight) | **retained verbatim** | INVARIANT: in-flight/unsettled rows are never folded — their terminal transition still has to join them by `attempt_id` in the live replay |
| `usage_baseline` / `usage_baseline_group` from a previous compaction | re-folded (header replaced, groups merged by key, exact-decimal sums added) | baselines must not accumulate per epoch |
| `kind="subscription_session"`, `"external_unmetered"` | **retained** | their `attempt_id` is deterministically re-derived from a stable external id and re-asserted on replay: `_append_single_settled_row` dedups and conflict-checks against the LIVE replay. Folding them would turn an idempotent replay into a silent double charge. Disclosed residual: these rows keep growing (slowly — one row per delegated run / external dispatch); a future lane may fold them behind an archived-identity membership check. |
| `kind="legacy_*"` | **retained** | same idempotency argument: `ensure_legacy_imported` dedups candidate rows against live `attempt_id`s if the completion watermark is ever lost mid-history. Bounded one-time set. |
| attempts with review attribution | **retained** | `skill_review_usage` projects historical waves per-attempt (`attempt_ids`, `attempts` lists) for durable review receipts; folding would erase that projection. Disclosed residual (skill-review waves only; ordinary task/review traffic carries no `review_*` attribution). |
| unknown future kinds | **retained** | fail-safe default: fold only what this design proves aggregation-complete |
| quarantine file | untouched | quarantined bytes are already out of the replay; `integrity_degraded` stays path-derived and unaffected |

## 4. Baseline block shape

The compacted file is `[header row] [group rows …] [retained rows …]`.

**Header** (`kind="usage_baseline"`, exactly one, always seq 1 when present):

```json
{"kind":"usage_baseline","attempt_id":"baseline-<hex12>","state":"settled",
 "seq":1,"ts":"…","baseline_id":"<hex12>","compaction_epoch":N,
 "archive_rel":"archive/usage_ledger/segment_….jsonl",
 "source_sha256":"…","source_size_bytes":B,"source_row_count":R,
 "source_first_seq":1,"source_last_seq":K,
 "folded_row_count":F,"folded_attempt_count":A,
 "group_count":G,"retained_row_count":T}
```

The header is a pure stamp: `_summary`/`_breakdown_bucket` skip it entirely
(it contributes no money, no counts, no tokens). `source_sha256` is the
SHA-256 of the archived segment's exact bytes — together with the segment's
own embedded previous header this forms a tamper-evident hash chain over the
whole history.

**Group rows** (`kind="usage_baseline_group"`): one per attribution tuple

```
key = (state, model, provider, category, source,
       task_id, root_task_id, parent_task_id,
       prompt_cache_ttl, cost_known, cost_final, pricing_known, bound_known)
```

carrying: the key fields verbatim; `folded_attempt_count` (int ≥ 1);
`cost_usd` / `reservation_upper_bound_usd` as **exact-decimal JSON strings**
(§5); token sums (`prompt_tokens`, `completion_tokens`, `cached_tokens`,
`cache_write_tokens`) as ints, absent when no folded row reported the field;
`root_limit_usd` = min over the group's known values (else absent);
`baseline_id` joining the header; empty `review_*` attribution.

Why per-group rows and not the literally single row of the sanction sketch:
budget enforcement is **per-root** (`reserve_attempt` filters finals by
`root_task_id`; `usage_projection` takes `min` of row `root_limit_usd`), and
`usage_breakdown` groups by model/provider/category/task/root. A single global
row would silently zero per-root accounting — a budget bypass. One stamped
baseline *block* whose group rows preserve the full attribution tuple keeps
every existing aggregation exact while remaining one atomic, stamped unit.

## 5. Monetary exactness rule (the fixed rule)

Monetary equality is defined **on decimals, never on float accumulation**:

- The compactor parses the source segment with `parse_float=Decimal` and sums
  each group's `cost_usd` / `reservation_upper_bound_usd` as exact `Decimal`s
  of the literals actually stored in the file.
- Group sums are stored as exact-decimal **JSON strings** (`"cost_usd":
  "12.3456789"`, `format(dec, "f")`, no exponent). `_number()` — the single
  row-level monetary parser used by validation, `_summary`, and every
  projection — already accepts numeric strings, so no reader changes shape.
  A float re-serialization would round the exact sum to the nearest double;
  the string keeps the invariant byte-checkable forever, including across
  re-compactions (group merges sum the decimal strings exactly).
- Retained rows are re-serialized from the standard float parse (shortest-repr
  round-trip). The compactor VERIFIES, per retained row, that the re-parsed
  `Decimal` view of the new line equals the `Decimal` view of the original
  line on every field (only `seq` / `pre_compaction_seq` may differ); every
  literal our writers ever emit is shortest-repr so this holds identically,
  and a foreign non-canonical literal (one that is not double-round-trippable)
  triggers **abort, not approximation**.
- Before committing, the compactor replays the candidate bytes through the
  PRODUCTION aggregation (`_final_rows` → `_summary`, per-root summaries with
  `min` limits, `_breakdown_bucket` global and per model/provider/category/
  task/root axis) and requires the rendered dicts to be **equal** to the same
  render of the source rows. Any inequality — including a sub-microdollar
  float-rounding boundary — aborts the compaction and leaves the ledger
  byte-identical. Compaction is an optimization; correctness never trades.

So: the decimal ledger-level sums are exactly preserved by construction, and
the float projection the budget enforcement actually reads is proven equal by
replay before the swap, else no swap.

## 6. seq policy

The live file keeps the substrate's strongest integrity property: **dense
`seq` from 1** (the validator's density check is what detects mid-file loss
and tampering). A gap-tolerant validator would have weakened that authority,
so instead the compacted file starts a fresh dense epoch:

- baseline header = seq 1, groups 2..G+1, retained rows follow densely in
  their original relative order;
- every retained row keeps its original seq as `pre_compaction_seq`;
- the header records `source_first_seq`/`source_last_seq`, and the archive
  segment holds every original row with its original `seq` untouched.

Monotonicity and density are preserved (the lane invariant); the original seq
values are never lost (archive + `pre_compaction_seq`). Nothing durable
references ledger rows by `seq` (cross-references are `attempt_id`s); resume
fingerprints are invalidated structurally by the inode change (§8).

Validator additions (`_validate_records`): baseline rows are legal only as
the leading block of a full-file validation (a baseline row in an appended
tail, or after any non-baseline row, is corrupt); exactly one header, first;
group rows must carry the header's `baseline_id` and a positive
`folded_attempt_count`; group state ∈ {settled, unresolved, released}; the
existing per-attempt transition and numeric checks apply unchanged (monetary
strings parse through `_number`).

## 7. Aggregation contract (`_usage_rows`)

`_summary` and `_physical_call_count`/`_breakdown_bucket` become
baseline-aware in the narrowest way:

- `usage_baseline` header: skipped (no money, no counts);
- `usage_baseline_group`: every **count** increment (`attempt_counts`,
  `unknown_unmetered`, `non_final_rows`, physical calls, `prompt_cache_ttls`)
  uses `weight = folded_attempt_count`; every **sum** adds the row's carried
  aggregate once. For all existing kinds `weight == 1` and the code path is
  byte-equivalent to today's.

The group key (§4) makes each group homogeneous in every branch predicate
`_summary` evaluates per row (`cost is None`, `cost_final`,
`pricing_known is False`, `bound is None`, state), so the per-group branch is
exactly the per-row branch taken `weight` times with the sums pre-added.

## 8. Concurrency, crash-safety, caches

- Compaction runs **only under the same monetary lock** (`_locked(root)`) as
  every read-check-append transaction, invoked from `reserve_attempt`'s
  locked section before its ledger read (§9). No second lock, no new lock
  ordering.
- Commit order: (1) build + fully verify the candidate in memory (§5, §6);
  (2) write the archive segment — exact source bytes — via O_EXCL write,
  `fsync` the file **and its directory** (directory fsync is best-effort on
  Windows, guaranteed on POSIX; disclosed); (3) atomically replace the live
  ledger (`_write_bytes_atomic_fsync`); (4) emit the
  `usage_ledger_compacted` event. A crash before (3) leaves the ledger
  byte-identical (an orphaned archive segment is harmless and disclosed); a
  crash during (3) leaves either the old or the new file — both valid.
- Cache coherence is structural, not cooperative: the swap changes the inode,
  so every resume fingerprint (`_LEDGER_READ_CACHE`, `_ROWS_MEMO`) refuses to
  warm-resume and refolds from the new file. No cache is asked to remember to
  invalidate.

## 9. Trigger policy (config SSOT, no env knob)

- `ouroboros/config.py`: `USAGE_LEDGER_COMPACT_BYTES = 8_000_000` (compact at
  ~0.2 s-per-replay scale, well under the 20 MB measured-degradation WARN) and
  `USAGE_LEDGER_COMPACT_RETRY_GROWTH_BYTES = 1_000_000`. Constants, not env
  handles.
- `reserve_attempt` calls `maybe_compact_usage_ledger_locked(root)` at the top
  of its locked section: an `os.stat` fast-path (~µs) below the threshold;
  above it, one compaction pass on exactly the path whose lock-hold the file
  size degrades. Every failure inside compaction is contained (logged +
  event), never fails the reservation; a structurally corrupt ledger still
  fails in the normal read path with the normal error.
- Thrash guard: a per-process memo of the last attempted (inode, size); after
  an unprofitable pass (nothing foldable / no shrink / verify-abort) the next
  pass runs only once the file grows by `…_RETRY_GROWTH_BYTES` or is replaced.
- `USAGE_LEDGER_WARN_BYTES` (20 MB) stays as the regression tripwire above the
  mechanism, exactly like the rotation-bounded log warns: it now fires only if
  compaction is broken or the unfoldable residue itself reaches 20 MB.

## 10. History readers: CPL-5 reconcile sweep, audits

CPL-5 (`DESIGN_MODEL_VISIBLE_LOGGED.md` §3.3, implementation not yet landed on
this base) reconciles `model_send` seals against "an attempt row in the
usage-accounting replay". After compaction a folded attempt is no longer in
the live replay, so this lane ships the join surface the sweep must use:

- `usage_compaction.archived_attempt_ids(root)` — the `attempt_id` set of
  every archived segment, walked through the tamper-evident header chain
  (live header → segment; segment's own embedded header → older segment, …),
  each segment verified against the recorded `source_sha256` and cached
  per-process by path+sha (segments are immutable).
- `usage_compaction.usage_attempt_recorded(root, attempt_id, live_ids)` —
  membership in live replay ∪ archive.

Contract for the CPL-5 lane (recorded here and in the review packet): the
reverse sweep's "no attempt row" verdict (`orphan_seal`) must consult this
union, not the live replay alone; an unreadable/mismatched segment is the
sweep's existing UNKNOWN → skip-pass case (fail-soft, the API raises typed
`UsageLedgerCorrupt`). The baseline header in the live file is the structural
signal that the live replay is not the full per-attempt history.

`legacy-import` needs no such lookup: its rows are never folded (§3), so its
existing live-replay dedup keeps working under a lost watermark.

## 11. Module placement

New leaf `ouroboros/usage_compaction.py` (domain D16): fold policy +
archive/verify/swap + history readers. It imports FROM `usage_ledger`
(substrate) and `_usage_rows` (aggregation leaf); `usage_accounting` calls
INTO it from `reserve_attempt`. The substrate stays policy-free (it learns
only the new row kinds' validation), the one-way seam
`usage_ledger ← usage_accounting` is unchanged, and the compactor — which must
know the aggregation semantics — lives beside the aggregation, not inside the
byte authority.

## 12. Invariants (pinned by tests/test_usage_compaction.py)

1. **Byte-exact money**: decimal sums of `cost_usd` /
   `reservation_upper_bound_usd` over finals are identical before/after; the
   full `usage_projection` (global + per-root incl. limits) and
   `usage_breakdown` (all axes) renders are equal dicts.
2. **Unsettled never fold**: reserved/dispatched chains survive verbatim
   (modulo seq) and settle correctly after compaction.
3. **Crash-safety**: an injected failure between archive write and ledger
   swap leaves a byte-identical, valid, further-usable ledger.
4. **Budget sees the same numbers**: root/global enforcement thresholds are
   unchanged across compaction.
5. **CPL-5 join survives**: every pre-compaction `attempt_id` remains
   resolvable through live ∪ archive, across chained compactions; a tampered
   segment is detected.
6. **Idempotency survives**: subscription/external replays after compaction
   dedup (no double charge) and still conflict-check; legacy import stays
   correct with and without its watermark.
7. **Trigger policy**: no compaction below threshold; thrash guard holds;
   verify-abort leaves the ledger untouched.
8. **Structure**: baseline rows only at head; tail-appended baseline rows are
   corrupt; quarantine/`integrity_degraded` semantics unchanged.

## 13. Explicitly out of scope

- Folding subscription/external/legacy/review-attributed rows (disclosed
  residuals, §3).
- Any GC of archive segments or the quarantine file (append-only, never).
- The CPL-5 sweep implementation itself (not on this base; §10 records its
  contract).
- Changing `USAGE_LEDGER_WARN_BYTES` or the lock timeouts.
