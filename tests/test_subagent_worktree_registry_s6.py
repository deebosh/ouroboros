"""S6 C3/C4 — the private-snapshot registry when it cannot be read or written.

``state/subagent_worktrees.json`` is the third durable registry in the family
(beside ``state/cancel_intents.json`` and ``state/terminal_deliveries.json``)
and the only one that answers "malformed" with "empty". This module pins what
that costs: a live snapshot reads as missing, the startup GC reports a clean
sweep, and the next reconciliation overwrites the malformed bytes with a valid
empty registry — after which the checkout on disk and the ``refs/ouroboros/
delegated/*`` ref that pins its baseline are unreachable forever.

C4 pins the write half: the Git branch registers the snapshot AFTER the
worktree and ref exist and does not undo them when the registration fails,
while the payload sibling already cleans up (``tests/
test_delegated_skill_payload.py::test_registry_save_failure_leaves_no_orphan_snapshot_dir``).

CURRENT BEHAVIOUR (characterization only).
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

from ouroboros import subagent_worktrees as wt


MALFORMED = '"not a registry"'


def _git(cwd, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=check,
    )


def _seed_target(tmp_path: pathlib.Path) -> pathlib.Path:
    target = tmp_path / "target"
    target.mkdir()
    _git(target, "init")
    (target / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(target, "add", "-A")
    _git(target, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed")
    return target


def _registry(data_dir: pathlib.Path) -> pathlib.Path:
    return data_dir / "state" / "subagent_worktrees.json"


def _snapshot(tmp_path, snapshot_id="snapS6"):
    """One registered delegated execution snapshot; returns (target, snaps, data, handle)."""
    target = _seed_target(tmp_path)
    snaps, data = tmp_path / "snaps", tmp_path / "data"
    handle = wt.provision_execution_snapshot(
        target_root=target, task_id="t1", snapshot_id=snapshot_id,
        worktree_root=snaps, data_dir=data)
    return target, snaps, data, handle


# ---------------------------------------------------------------------------
# C3 — a malformed registry reads as an empty one
# ---------------------------------------------------------------------------


def test_c3_a_malformed_registry_reads_as_an_empty_one(tmp_path):
    """C3: every malformed shape collapses to the same answer as "no file"."""
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)

    assert wt._load_registry(data_dir=data) == [], "absent: an ordinary empty read"
    for payload in (MALFORMED, '{"worktrees": "nope"}', '{"worktrees": [', "\x00\x01"):
        _registry(data).write_text(payload, encoding="utf-8")
        assert wt._load_registry(data_dir=data) == [], payload
    assert wt.list_worktrees(data_dir=data) == []


def test_c3_a_live_snapshot_reads_as_missing_over_a_malformed_registry(tmp_path):
    """C3: the lookup that decides "does this binding still exist?" answers no
    while the checkout and its pinned baseline ref are both right there."""
    target, snaps, data, handle = _snapshot(tmp_path)
    assert wt.find_execution_snapshot("snapS6", data_dir=data) is not None
    _registry(data).write_text(MALFORMED, encoding="utf-8")

    assert wt.find_execution_snapshot("snapS6", data_dir=data) is None
    assert pathlib.Path(handle.path).is_dir(), "the checkout is still on disk"
    assert _git(target, "rev-parse", handle.baseline_ref).stdout.strip() == handle.baseline_sha


def test_c3_the_startup_gc_reports_a_clean_sweep_over_a_malformed_registry(tmp_path):
    """C3: ``prune_execution_snapshots`` says "nothing removed, nothing kept" —
    which reads as clean, not as unknowable."""
    _target, snaps, data, _handle = _snapshot(tmp_path)
    _registry(data).write_text(MALFORMED, encoding="utf-8")

    report = wt.prune_execution_snapshots(set(), worktree_root=snaps, data_dir=data)

    assert report == {"removed": [], "kept": []}


def test_c3_prune_orphans_overwrites_a_malformed_registry(tmp_path):
    """C3, the destructive half: startup reconciliation rewrites the malformed
    bytes as a valid EMPTY registry, so the recovery material is gone and the
    checkout plus its pinned ref are stranded with nothing naming them."""
    target, snaps, data, handle = _snapshot(tmp_path)
    _registry(data).write_text(MALFORMED, encoding="utf-8")

    report = wt.prune_orphans(worktree_root=snaps, data_dir=data)

    assert report == {"removed": 0, "kept": 0}
    assert json.loads(_registry(data).read_text(encoding="utf-8")) == {"worktrees": []}
    assert pathlib.Path(handle.path).is_dir(), "orphan checkout"
    assert _git(target, "rev-parse", handle.baseline_ref, check=False).returncode == 0, (
        "orphan baseline ref, pinning its commit against GC forever"
    )


def test_c3_a_new_snapshot_overwrites_a_malformed_registry_too(tmp_path):
    """C3: provisioning reads the registry, appends and writes it back, so a
    malformed file is replaced by a registry holding only the new row."""
    target, snaps, data, first = _snapshot(tmp_path, snapshot_id="snapOne")
    _registry(data).write_text(MALFORMED, encoding="utf-8")

    wt.provision_execution_snapshot(
        target_root=target, task_id="t1", snapshot_id="snapTwo",
        worktree_root=snaps, data_dir=data)

    rows = json.loads(_registry(data).read_text(encoding="utf-8"))["worktrees"]
    assert [row["snapshot_id"] for row in rows] == ["snapTwo"]
    assert pathlib.Path(first.path).is_dir(), "the first snapshot is now unregistered"


# ---------------------------------------------------------------------------
# C4 — a registry write that fails on the Git branch
# ---------------------------------------------------------------------------


def test_c4_a_git_branch_registry_write_failure_strands_the_worktree_and_ref(
    tmp_path, monkeypatch,
):
    """C4: the payload branch removes its snapshot directory when the registry
    write fails; the Git branch leaves both the checkout and the pinned
    baseline ref behind, unregistered and therefore invisible to disposal."""
    target = _seed_target(tmp_path)
    snaps, data = tmp_path / "snaps", tmp_path / "data"

    def _boom(*_a, **_k):
        raise OSError("registry disk full")

    monkeypatch.setattr(wt, "_save_registry", _boom)
    with pytest.raises(OSError, match="registry disk full"):
        wt.provision_execution_snapshot(
            target_root=target, task_id="t1", snapshot_id="snapFail",
            worktree_root=snaps, data_dir=data)

    leftovers = sorted(p.name for p in snaps.glob("dlg_*")) if snaps.exists() else []
    assert leftovers == ["dlg_t1_snapFail"], "the checkout survives unregistered"
    assert _git(
        target, "rev-parse", "refs/ouroboros/delegated/snapFail", check=False,
    ).returncode == 0, "the baseline ref survives, pinning its commit"
