# C6 review packet — monetary usage-ledger compaction (CPL4-C6, owner 1A)

Lane: `v7next_c6`, base `74a03082`. Owner sanction: batch №8 item 1A
(2026-09-01) — compaction of `state/usage_attempts.jsonl` in its own reviewed
lane (monetary authority). Design note ratified before code:
`docs/v7next/DESIGN_USAGE_COMPACTION.md` (commit `a1063124`); implementation
+ pins in the follow-up commit; this packet + ledger section close the lane.

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
| `tests/test_usage_compaction.py` | NEW pin suite (16 tests) | see §3 |
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
5. **Crash-safety order**: archive segment written + fsync'd (file and, on
   POSIX, directory) BEFORE the atomic ledger swap. Crash anywhere = valid
   ledger (old or new generation); orphan segments harmless.
6. **seq policy**: dense-seq validation authority preserved by starting a
   fresh epoch; original seqs survive in the archive and as
   `pre_compaction_seq` on retained rows. Substrate append/resume arithmetic
   (`len(records)`-based) is deliberately UNCHANGED — check this holds.
7. **Concurrency**: everything under the existing monetary lock
   (`_locked`); cache coherence is structural (atomic swap → new inode →
   every resume fingerprint refolds). No cooperative invalidation.
8. **CPL-5 join**: every pre-compaction `attempt_id` resolves through
   live ∪ `archived_attempt_ids` (hash-chained, tamper-evident, cached per
   immutable segment). The CPL-5 reverse sweep (NOT on this base — only its
   design note is) must consult this union; contract recorded in the design
   note §10 and the ledger section.

## 3. Pins (tests/test_usage_compaction.py, 16 tests)

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
3. Directory fsync is best-effort on Windows (POSIX guaranteed); worst case
   there is a lost archive dir entry AND a swap in the same crash window —
   mitigated by archive-first ordering, disclosed in the design note.
4. A float-boundary rounding coincidence can make the rounded projections
   differ pre/post → the pass aborts and the ledger simply stays uncompacted
   (correctness over availability; disclosed in design note §5).
5. CPL-5's sweep is not on this base; its contract (consult live ∪ archive;
   corrupt chain = UNKNOWN/skip) is recorded in the design note §10 for the
   lane that lands it. `model_send_seal`-targeted gates therefore do not
   exist on this base to run.
