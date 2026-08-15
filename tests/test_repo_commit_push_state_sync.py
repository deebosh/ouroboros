"""Regression test for the racy current_sha write in _repo_commit_push (rot ibl-ff22eb63afe2).

The rot pattern was: ``load_state() -> mutate -> save_state()`` — three
separate lock acquisitions. A concurrent supervisor reconciliation
(supervisor/git_ops.py:1185/1631) could overwrite the intermediate
snapshot between our load and our save, dropping the commit's
``current_sha`` update entirely. The cycle would then fail PHASE 0 on
the next reviewed attempt because ``state.current_sha`` no longer
matches ``git rev-parse HEAD``.

The fix: ``update_state(mutator)`` — one lock acquisition covers
load+mutate+save as a single critical section, so concurrent writers
cannot lose each other's changes.

This test pins three things so the rot cannot be reintroduced silently:

1. ``update_state`` actually persists the new ``current_sha`` to disk.
2. Two concurrent ``update_state`` writers writing different keys both
   land — the class-fix test for the lost-update race.
3. ``ouroboros/tools/git.py`` no longer imports the racy
   ``load_state, save_state`` pair together for ``current_sha`` writes,
   and it DOES import ``update_state`` (so the synchronous write is
   actually present).
"""
from __future__ import annotations

import inspect
import json
import threading


def _setup_state_root(tmp_path):
    """Redirect supervisor.state at tmp_path so the test is hermetic.

    init() resets the module-level globals (DRIVE_ROOT, STATE_PATH,
    STATE_LOCK_PATH) so the production data root never sees test
    writes. ensure_state_defaults() (called inside _load_state_unlocked)
    initialises ``current_sha`` to None; that is the starting state we
    then overwrite through update_state().
    """
    drive = tmp_path / "drive"
    from supervisor import state as state_mod
    state_mod.init(drive)
    return drive / "state" / "state.json"


def test_update_state_writes_current_sha_atomically(tmp_path):
    """update_state persists the new current_sha to disk."""
    state_path = _setup_state_root(tmp_path)
    new_sha = "deadbeef" * 4

    from supervisor import state as state_mod
    state_mod.update_state(lambda st: st.__setitem__("current_sha", new_sha))

    final = json.loads(state_path.read_text())
    assert final["current_sha"] == new_sha


def test_update_state_preserves_concurrent_writes(tmp_path):
    """Two concurrent update_state writers on different keys both land.

    Class-fix test for ibl-ff22eb63afe2. Under the racy load-then-save
    pattern, a writer could lose its change if another writer's save
    interleaved between its load and save. With update_state holding
    STATE_LOCK for the whole operation, both writes are serialised
    and neither is lost.
    """
    state_path = _setup_state_root(tmp_path)
    from supervisor import state as state_mod

    barrier = threading.Barrier(2)
    completed = []

    def writer_current_sha():
        barrier.wait()
        state_mod.update_state(
            lambda st: st.__setitem__("current_sha", "writer1_sha")
        )
        completed.append("current_sha")

    def writer_other():
        barrier.wait()
        state_mod.update_state(
            lambda st: st.__setitem__("other", "writer2_value")
        )
        completed.append("other")

    t1 = threading.Thread(target=writer_current_sha)
    t2 = threading.Thread(target=writer_other)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert sorted(completed) == ["current_sha", "other"], (
        f"concurrent writers did not both finish: {completed}"
    )

    final = json.loads(state_path.read_text())
    assert final["current_sha"] == "writer1_sha", (
        f"current_sha write lost to concurrent writer: {final}"
    )
    assert final["other"] == "writer2_value", (
        f"other write lost to concurrent writer: {final}"
    )


def test_git_py_uses_update_state_not_racy_load_state_save_state():
    """Static guard: the rot pattern must not return to git.py.

    The racy pattern manifested as a paired import
    ``from supervisor.state import load_state, save_state``. If that
    exact import reappears anywhere in ouroboros/tools/git.py, the
    current_sha write is no longer atomic. The fix imports
    ``update_state`` (alone) so the synchronous post-commit write is
    actually present.
    """
    from ouroboros.tools import git as git_module
    src = inspect.getsource(git_module)
    assert "from supervisor.state import load_state, save_state" not in src, (
        "git.py returned to the racy load_state/save_state pattern for "
        "current_sha. Use update_state(mutator) instead — see backlog "
        "ibl-ff22eb63afe2."
    )
    assert "from supervisor.state import update_state" in src, (
        "git.py no longer imports update_state — the post-commit "
        "synchronous current_sha write is missing entirely."
    )
