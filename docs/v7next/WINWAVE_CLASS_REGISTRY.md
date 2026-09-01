# R-WINWAVE — one decision per cross-OS class

The v7 campaign and upstream fixed the same Windows/macOS territory
independently. Plan §2 mandates the return of the v7 cross-OS wave and plan §11
names the hazard: re-applying it commit-by-commit would fight the upstream
fixes and duplicate them. So the return is **by class**, and this file is the
registry the ADOPTION row points at — one recorded decision per class, and no
class silently re-applied twice.

Reference wave (frozen, `ouroboros_v7_wip @ 9f691656`): commits `18048261`
(14 files), `073c610d`, `640a249a`, `711c982b`, `6cdc8e16`, `b20c94a1`. None is
an ancestor of this branch or of `managed/ouroboros`. The upstream fixes for the
same territory ARE ancestors here: `7de26338`, `c15389f4`, `18a4e17d`,
`78d168b8`, `d2f6701a`, `0c4acfd9`, `10779106`, `d428f125`, `4a550589`.

Decision vocabulary, one per row:

- **re-applied** — the class exists only on our side; the campaign landed the
  reference form (the carrier files do not exist upstream at all).
- **superseded-by-upstream** — upstream solved the same class, differently or
  more broadly; the reference form is NOT transplanted, upstream's stands.
- **not-applicable** — the carrier the reference patched does not exist in this
  tree, so there is nothing to decide until a carrier arrives.

Sixteen classes from the reference wave. (The campaign audit's summary line
said "15"; the recount below separates the
`PermissionError`-beside-`IsADirectoryError` clause from the `tools.jsonl`
utf-8 read — they were one line there.) A seventeenth class was found by the
matrix itself and is recorded the same way, in its own section: a class this
registry learns about from a red run still owes exactly one decision.

## Re-applied (7)

| # | class | decision | where it lives now |
|---|---|---|---|
| 1 | `fchmod` POSIX guard + paid-lane fail-closed off POSIX (a non-POSIX host must refuse the paid lane rather than write a live key before chmod; reference `b20c94a1`) | re-applied | `tests/fixtures_e2e_cancellation.py` (guard + refusal), landed by `4fffefb1` |
| 2 | 0600-mode assertions skipped where the OS has no POSIX mode bits | re-applied | `tests/test_e2e_cancellation_scenarios.py`, landed by `4fffefb1` |
| 3 | Canonical-path compares instead of string equality (Windows short paths, drive-letter case) | re-applied | `tests/test_external_review_script.py` (`3b62c1d6`) and `tests/test_cancel_protocol_inventory_s6.py` (`4fffefb1`) |
| 4 | `os.sep` / `!r`-mirror expectations instead of hardcoded `/` in message pins | re-applied | `tests/test_core_native_results.py`, landed by `fa2f6fc5` |
| 5 | Per-scenario registry route pin (the `640a249a` lesson: `user_files` is path-dependent so Windows takes the native route, `cognitive` is arg-dependent so the adapter runs everywhere — one blanket platform pin was the over-generalisation) | re-applied | `tests/test_registry_core.py`, landed by `ccbb933a` |
| 6 | Planted-stamp comparison tolerant of the 15 ms clock tick | re-applied | `tests/test_owner_stop_fences_s6.py`, landed by `88479fa7` |
| 7 | utf-8 ARCHITECTURE fixture (explicit encoding on read/write, not the cp1252 default) | re-applied | `tests/test_update_carriers.py`, landed by `7f0a1124` |

## Superseded by upstream (3)

| # | class | decision | evidence |
|---|---|---|---|
| 8 | Text-mode CRLF translation in the atomic writer — the root cause of the original 61-red Windows run | superseded-by-upstream | Upstream `c15389f4` decomposed it into `write_bytes_atomic` (+ UTF-8 encode) instead of adding `O_BINARY` inside `write_text_atomic`; the corrective lane then proved upstream's form is the BROADER fix (the non-fsync `Path.write_text` lane translated too). Replaying the reference would invert that decision. Ledger row 15; pinned by `tests/test_atomic_write_v639.py` |
| 9 | Launcher reaper: normpath'd path literals + POSIX-only enumeration test | superseded-by-upstream | Upstream `7de26338` normpaths the same literals (and the python binary path the reference missed) and, instead of skipping the enumeration test off POSIX, stubs `getuid` so it runs on every OS. Ledger row 16; pinned by `tests/test_launcher_server_reaper.py` |
| 10 | Child-process environment forwarding (`SystemRoot`, `TEMP`, `TMP`, `USERPROFILE`) | superseded-by-upstream | Upstream `78d168b8`, pre-cutoff; pinned by `tests/test_evolution_state_integrity_v3.py` |

## Not applicable in this tree (6)

| # | class | decision | why |
|---|---|---|---|
| 11 | Evidence/migration script hardening: JSON through stdin instead of argv (the Windows 32767-byte **command-line** cap, not a path-length cap), env forwarding, `PYTHONIOENCODING=utf-8`, stderr tail in the failure message | not-applicable | Carriers `scripts/v7_evidence.py` and `scripts/v7_migration.py` do not exist here |
| 12 | POSIX-scoped exactness pin of the prologue evidence | not-applicable | `tests/test_v7_prologue_evidence.py` does not exist here |
| 13 | Ledger test moved to the serial lane (its 72 MB RSS was a timeout symptom, not a memory limit) | not-applicable | The `test_v7_migration_ledger` carrier does not exist here |
| 14 | SIG9 exit-facts parity between the executor and the local shell | not-applicable | The asserting test is absent from `tests/test_shell_run_shell.py` and from everywhere else; the producer-side fallback exists (`ouroboros/tools/process_facts.py`, `tool_result.py`) |
| 15 | `tools.jsonl` read with an explicit utf-8 encoding | not-applicable | The reference test was re-derived as a `tests/test_tool_result.py` case, and the only reader of `tools.jsonl` content on this tree already passes `encoding="utf-8"` |
| 16 | `PermissionError` accepted beside `IsADirectoryError` on a directory write | not-applicable | The asserting directory-write test no longer exists in `tests/test_core_native_results.py` |

## Found by the matrix, not by the reference wave (1)

| # | class | decision | where it lives now |
|---|---|---|---|
| 17 | Source-text regex pins in the JS suite: a test that reads a source file off disk and matches a `\n`-bearing regex against it cannot pass on a CRLF checkout | re-applied | `web/tests/chat_plain_system_rows.test.js` normalizes CRLF to LF at both `readFileSync` reads, landed by `a0b35fcd` on the campaign integration branch — NOT on this worktree, which is why the row below still needs its own matrix leg |

Class 17 is upstream-born territory (`817a834f`, `dbd500cc`, both ancestors of
`managed/ouroboros`), so neither the reference wave nor the upstream fixes had
decided it; the red windows leg of run 33555971481 is what surfaced it. The
decision is the narrow one — normalize at the read, not a `.gitattributes`
checkout policy — because the assertion is about source TEXT, and only the two
reads that feed source-text regexes are touched.

## Open items on this row

1. **`tests/test_registry_core.py` still pins `os.name == "nt"`.** The accepted
   2026-08-30 audit item (`~/.claude/plans/v7next/V7NEXT_PLAN.md`; owner
   requirements archive, «os.name→alias-условие в route-пине») asks for the pin
   to name the actual alias condition instead of the platform.
   Class 5 above is landed; this is a follow-up on its expression, not on its
   decision.
2. **No green windows leg exists yet on any frozen SHA.** Class 17 is decided
   and fixed on the integration branch, but the run that would prove the fix
   has not reported. See the run log below.

## 3-OS matrix runs

The row's re-prove needs a green full-test 3-OS matrix on a frozen branch SHA
(`gh workflow run CI --ref <branch>` → `ci.yml` full-test). Runs so far:

| run id | SHA | ubuntu | macos | windows | verdict |
|---|---|---|---|---|---|
| [33555971481](https://github.com/razzant/ouroboros/actions/runs/33555971481) | `9a28e58f` (branch `ouroboros_v7next`, 2026-09-01 20:33Z) | green | green | **RED** (class 17) | not a re-prove |
| [33563498919](https://github.com/razzant/ouroboros/actions/runs/33563498919) | `196438c9` (branch `ouroboros_v7next`, carries the class-17 fix `a0b35fcd`) | pending | pending | pending | **pending** — no result at the time of writing |

The windows failure in run 33555971481, read from the run's own log rather than
from its exit code: two subtests of `web/tests/chat_plain_system_rows.test.js`
— «render arm order and enhancement guard are pinned in source» and «chat
bubble heading clamp is scoped in style.css». That is class 17 above, not a
regression of anything this campaign re-applied, and the same run's `full-test
(ubuntu-latest)` and `full-test (macos-latest)` were green. Also red in that
run and outside this row's scope: `ui-smoke` and `skill-smoke (ubuntu-latest)`.

Run 33563498919 is dispatched on `196438c9`, which has `a0b35fcd` as an
ancestor, so it is the first run that could clear class 17. Its result is
recorded here as **pending** deliberately: writing a verdict this file's author
did not read would be exactly the fabrication the one-decision-per-class rule
exists to prevent. Whoever reads that run appends the row's outcome.

Until a green windows leg exists on a frozen SHA, the ADOPTION row R-WINWAVE
stays non-`done` and its disposition `re-prove` is honestly unproven — the
per-class decisions above are recorded, the matrix that would prove them is
not.

Neither `a0b35fcd` nor `196438c9` is an ancestor of this worktree; the fix and
the pending run both live on the campaign integration line, and this branch
carries the registry, not the fix.
