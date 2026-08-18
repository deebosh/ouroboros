# Worker Contract Templates

Pre-filled task contract templates for specific task classes identified in the
Improvement Backlog. Use as the basis when dispatching tasks via
`promote_chat_to_task` or `schedule_subagent`.

These templates were introduced because rot_fix workers and claudexord-stale-lock
workers both attempted `run_command git stash` on `/opt/ouboros` and got
`GIT_VIA_SHELL_BLOCKED`, then either stopped with a planning receipt or
proceeded to mutate the worktree without finishing the implementation
(see `ibl-rot-fix-1-stash-antipattern`). The rules below prevent the entire
class of failure.

## CRITICAL — Self-Repo Mutation Rule (applies to ALL rot_fix/* and claudexord-stale-lock tasks)

**Self-repo mutations on `/opt/ouboros` go ONLY through `commit_reviewed`.**

This means:

| Allowed | Why |
|---------|-----|
| `commit_reviewed(message, paths=[...])` | The ONLY way to mutate /opt/ouroboros |
| `commit_reviewed(message, skip_advisory_review=True)` | Bypass under explicit owner authorization (§15(c)) |
| `commit_reviewed(message, skip_tests=True)` | Skip pre-push pytest ONLY when test failures are unrelated to your diff (and document why) |

| Forbidden | Why |
|-----------|-----|
| `run_command(["git", "stash", ...], cwd="/opt/ouroboros")` | `GIT_VIA_SHELL_BLOCKED` and the wrong tool for WIP |
| `run_command(["git", "push"], cwd="/opt/ouroboros")` | `GIT_VIA_SHELL_BLOCKED` on /opt/ouroboros |
| `run_command(["git", "commit", ...], cwd="/opt/ouroboros")` | Use `commit_reviewed` instead |
| Any direct git-write-op via `run_command` on self-repo | Always blocked, never the right answer |

### WIP state preservation — choose ONE of these, never `git stash`

When you start a task and adjacent dirty work must be preserved across a new commit:

1. **Commit the WIP** with `commit_reviewed(message="wip: <description>")`
   — leaves a clean tree, owner can squash later.
2. **Stage narrow paths** with `commit_reviewed(paths=["file1.py", "file2.py"])`
   — only those files get committed; the rest stay dirty until you decide.
3. **Abandon and re-implement** — if WIP cannot be committed cleanly,
   start fresh. Never `git stash`.

### When `commit_reviewed` blocks on pre-existing rot

The reviewed path can be blocked by dirty state unrelated to your diff. This
is the canonical fallback (per §15(c) of `memory/identity.md`):

```bash
cp -r /opt/ouroboros /tmp/your-fix-name
cd /tmp/your-fix-name
git add <your files>
git commit -m "<message>"
git push origin ouroboros
git tag v<X.Y.Z>      # if version bumped
```

Then write `pending_restart_verify.json` for the owner's `/restart`. The owner
will see the marker and reconcile.

## rot_fix/* Task Template

Use for: tasks that address structural rot in the Ouroboros self-repository
(identified by `ibl-rot-fix-*` patterns in the Improvement Backlog).

```yaml
objective: |
  Apply backlog item ibl-<NAME>:
  <paste specific rot fix description here>

  Source: <task_id or backlog reference>

expected_output: |
  - New SHA on origin (or /tmp clone) with the fix
  - All 7 P9 carriers bumped atomically via bump_version() (if version changed)
  - Focused tests pass
  - Backlog item closed via improvement_backlog mechanism with the commit SHA

constraints:
  - Self-repo mutations on /opt/ouroboros go ONLY through commit_reviewed
    (see CRITICAL rule above — git stash via run_command is FORBIDDEN)
  - If pre-push test gate blocks on unrelated rot: use clone-and-push fallback
  - If commit attribution gate blocks on pre-existing dirty: use paths=[...]
    with the explicit subset, or clone-and-push

success_criteria:
  - The specific rot item from the backlog is closed
  - All targeted tests pass
  - No new pre-existing-dirty files introduced
  - crd-0003 / crd-0006 advisory debt status reported (still REOPENED if bypass)

typical_pitfalls:
  - DO NOT run `git stash` on /opt/ouroboros — use commit_reviewed(paths=...)
  - DO NOT chain `&&` operators in a single run_command element —
    use ["sh", "-c", "..."] form
  - DO NOT stop at the planning stage — your task contract's expected_output
    is the receipts, not a receipt for the plan
```

## claudexord-stale-lock Task Template

Use for: tasks addressing stale lock files, orphan processes, or recovery logic
in the Claudexord integration (identified by `ibl-claudexord-stale-lock` and
related patterns).

```yaml
objective: |
  Apply claudexord-stale-lock fix: <specific aspect>

  Reference: <plan or task_id>

expected_output: |
  - New SHA on origin with the claudexord recovery fix
  - All 7 P9 carriers bumped atomically (if version changed)
  - Focused tests pass (e.g., test_claudexor_lock_keeper.py)
  - Live verification: claudexord restart succeeds after stale lock injection

constraints:
  - Self-repo mutations on /opt/ouroboros go ONLY through commit_reviewed
  - Claudexord daemon restart requires /restart by owner — worker must write
    pending_restart_verify.json (or the v6.103.12 hook will)
  - If pre-push test gate blocks on unrelated rot: use clone-and-push fallback

success_criteria:
  - Stale lock detection works (verified by focused test)
  - Recovery path exercised end-to-end
  - No regression in claudexord startup under clean state

typical_pitfalls:
  - DO NOT use `run_command rm -rf` against live claudexord runtime files
    from outside an owned-daemon context — go through the supervisor
  - DO NOT skip the live post-restart verification — a test-only PASS is
    a partial verdict, not a complete one (§15(a))
```

## Reference loading

When dispatching any task whose identifier matches `ibl-rot-fix-*` or
`ibl-claudexord-stale-lock`, copy the relevant section above into the
`objective` field (or reference this file by name) so the worker sees the
prohibition before its first LLM round. The `WORKER_CONTRACTS.md` file itself
is reference material — it is NOT auto-loaded into context. Make it visible
by reference at dispatch time, not by waiting for the worker to discover it.
