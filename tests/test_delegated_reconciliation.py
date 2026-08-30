"""Reconciliation of delegated runs by the supervisor generation's sweeps.

Started as the D11 slice of the reference theme split of
``tests/test_delegated_subagent_transport.py`` (v7 WIP 9f691656,
``tests/test_delegated_reconciliation.py``): the two tests here bind the
``ouroboros.server_maintenance`` owner the server composition split created, and
re-homing them is the byte-debt pressure valve — the giant shrinks, the pin
gains its family. The rest of the reference theme split (orphan-sweep predicate,
absent-run closure, release points) still lives in the giant and arrives with
the delegation-organ test split (F2).
"""

from __future__ import annotations


def test_the_startup_sweep_reconciles_delegated_runs_too(monkeypatch):
    """Nothing is running yet at supervisor startup, so every open delegated run is by
    definition ownerless. The only server-side test covered the PERIODIC tick, so the
    startup half could be deleted without a single failure — and it is the half that
    catches the runs the generation that died was watching."""
    import ouroboros.server_maintenance as sm
    import ouroboros.delegate_custody as dc
    import ouroboros.process_custody as pc

    seen = {}
    monkeypatch.setattr(pc, "reap_orphaned_processes", lambda root, **kw: [])
    monkeypatch.setattr(dc, "reconcile_orphaned_runs",
                        lambda root, **kw: seen.setdefault("live", kw.get("running_task_ids")) or [])
    monkeypatch.setattr(sm, "_installed_skill_names", lambda: None)
    sm._startup_custody_sweep()
    assert seen["live"] == set(), "an empty live set is the point: nothing survived the restart"


def test_both_custody_surfaces_see_the_same_live_task_set(monkeypatch):
    """The periodic sweep must hand the delegated reconciler the SAME live task set the
    process reaper gets. Two copies of "is the owner still running" is exactly how one
    custody surface ends up reaping while its twin does not."""
    import time

    import ouroboros.server_maintenance as sm
    import ouroboros.delegate_custody as dc
    import ouroboros.process_custody as pc
    import supervisor.queue as queue

    seen = {}
    monkeypatch.setattr(pc, "reap_orphaned_processes",
                        lambda root, **kw: seen.__setitem__("processes", kw.get("running_task_ids")) or [])
    monkeypatch.setattr(dc, "reconcile_orphaned_runs",
                        lambda root, **kw: seen.__setitem__("delegated", kw.get("running_task_ids")) or [])
    monkeypatch.setattr(sm, "_installed_skill_names", lambda: None)
    monkeypatch.setitem(queue.RUNNING, "t-live", {})
    sm._periodic_supervisor_maintenance([0.0], [time.time()])
    assert seen["processes"] == seen["delegated"] == {"t-live"}, seen
