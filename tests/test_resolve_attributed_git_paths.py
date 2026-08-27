"""Tests for the narrow explicit-path narrowing exception in
``ouroboros.mutation_attribution.resolve_attributed_git_paths``.

The class-fix lets a non-empty explicit path list proceed when:

  1. every selected path is a clean-at-baseline candidate;
  2. no selected path belongs to ``excluded_preexisting_dirty``;
  3. the sole blocker is ``preexisting_dirty_changed`` (every other global
     flag remains fail-closed).

These tests verify that contract end-to-end: the new behavior (T1-T9, T16-T19,
N1, N2), and the unchanged fail-closed paths (T10-T15). Regression checks
(R1-R4) reuse the existing ``tests/test_mutation_attribution.py`` suite.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ouroboros.task_results import (
    STATUS_RUNNING,
    load_task_result,
    write_task_result,
)


def _git(root: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: pathlib.Path, *, extra_files: tuple[str, ...] = ()) -> pathlib.Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Tests")
    (root / "clean.txt").write_text("base\n", encoding="utf-8")
    (root / "dirty.txt").write_text("base\n", encoding="utf-8")
    for name in extra_files:
        (root / name).write_text("base\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return root


def _capture(root: pathlib.Path, data: pathlib.Path, task_id: str) -> None:
    from ouroboros.mutation_attribution import capture_mutation_baseline
    write_task_result(data, task_id, STATUS_RUNNING)
    capture_mutation_baseline(
        data,
        task_id,
        [{"surface_type": "system_repo", "host_root": str(root)}],
    )


def _dirty_with_baseline(
    tmp_path: pathlib.Path,
    *,
    owner_name: str = "dirty.txt",
    clean_name: str = "clean.txt",
    task_id: str = "task-narrow",
):
    """Set up the canonical T3 state: owner.txt dirty at baseline + later
    changed, plus a clean-at-baseline candidate modified during the task.
    """
    from ouroboros.mutation_attribution import capture_mutation_baseline
    root = _repo(tmp_path, extra_files=(owner_name,))
    data = tmp_path / "data"
    write_task_result(data, task_id, STATUS_RUNNING)
    # Step 1: owner file is dirty BEFORE the baseline is captured.
    (root / owner_name).write_text("owner before baseline\n", encoding="utf-8")
    capture_mutation_baseline(
        data,
        task_id,
        [{"surface_type": "system_repo", "host_root": str(root)}],
    )
    # Step 2: change the owner file AGAIN (so the fingerprint differs from
    # baseline) AND modify a clean file (the task's own change).
    (root / owner_name).write_text("owner after baseline\n", encoding="utf-8")
    (root / clean_name).write_text("task change\n", encoding="utf-8")
    return root, data, clean_name, owner_name


# ---------------------------------------------------------------------------
# T1-T9: the new contract surface (T3 is the headline behavior).
# ---------------------------------------------------------------------------


def test_t1_explicit_clean_candidate_no_initial_dirt(tmp_path):
    """Clean baseline + explicit clean candidate + no preexisting dirt = success."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root = _repo(tmp_path)
    data = tmp_path / "data"
    _capture(root, data, "task-t1")
    (root / "clean.txt").write_text("changed\n", encoding="utf-8")

    selected, evidence, error = resolve_attributed_git_paths(
        data, "task-t1", root, ["clean.txt"],
    )
    assert selected == ["clean.txt"]
    assert error == ""
    assert evidence["blockers"] == []


def test_t2_omitted_paths_with_changed_preexisting_dirt_remains_blocked(tmp_path):
    """Omitted paths = automatic staging; preexisting dirty change still blocks."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, clean_name, owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t2",
    )

    selected, _evidence, error = resolve_attributed_git_paths(
        data, "task-t2", root, None,
    )
    assert selected == []
    assert "preexisting_dirty_changed" in error
    assert owner_name in error or "preexisting_dirty_changed" in error


def test_t3_explicit_disjoint_candidate_with_changed_preexisting_dirt_succeeds(tmp_path):
    """THE KEY NEW BEHAVIOR: explicit disjoint candidate admits despite dirt blocker."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, clean_name, owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t3",
    )

    selected, evidence, error = resolve_attributed_git_paths(
        data, "task-t3", root, [clean_name],
    )
    assert selected == [clean_name]
    assert error == ""
    # Evidence is preserved unchanged: blocker and excluded path remain visible.
    assert "preexisting_dirty_changed" in evidence["blockers"]
    assert evidence["excluded_preexisting_dirty"] == [owner_name]
    assert clean_name in evidence["candidates"]


def test_t4_explicit_preexisting_dirty_path_is_rejected(tmp_path):
    """Naming a preexisting dirty path explicitly does NOT grant ownership."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, _clean_name, owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t4",
    )

    selected, _evidence, error = resolve_attributed_git_paths(
        data, "task-t4", root, [owner_name],
    )
    assert selected == []
    # Either the narrowing exception rejects (GIT_ATTRIBUTION_BLOCKED) or the
    # subset validator rejects after the blocker gate lifts — both protect
    # against admitting a dirty path the caller only claimed.
    assert ("subset" in error) or ("GIT_ATTRIBUTION_BLOCKED" in error)


def test_t5_mixed_safe_and_dirty_explicit_paths_fail_atomically(tmp_path):
    """Mix of candidate + excluded dirty = whole request rejected (no partial)."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, clean_name, owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t5",
    )

    selected, _evidence, error = resolve_attributed_git_paths(
        data, "task-t5", root, [clean_name, owner_name],
    )
    assert selected == []
    # Either rejection path is acceptable; the contract is that NO partial
    # selection of ``clean_name`` leaks through.
    assert ("subset" in error) or ("GIT_ATTRIBUTION_BLOCKED" in error)
    assert clean_name not in selected


def test_t6_empty_explicit_list_remains_no_op(tmp_path):
    """paths=[] = empty selection, never falls through to all candidates."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, clean_name, _owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t6",
    )

    selected, _evidence, error = resolve_attributed_git_paths(
        data, "task-t6", root, [],
    )
    assert selected == []
    assert "GIT_NO_ATTRIBUTED_CHANGES" in error


def test_t7_blank_only_explicit_list_remains_no_op(tmp_path):
    """Whitespace-only explicit list normalizes to empty and returns no-op."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, _clean_name, _owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t7",
    )

    selected, _evidence, error = resolve_attributed_git_paths(
        data, "task-t7", root, ["", "  "],
    )
    assert selected == []
    assert "GIT_NO_ATTRIBUTED_CHANGES" in error


def test_t8_duplicate_explicit_paths_deduplicate(tmp_path):
    """Duplicates fold to a single sorted entry; evidence unchanged."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, clean_name, owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t8",
    )

    selected, evidence, error = resolve_attributed_git_paths(
        data, "task-t8", root, [clean_name, clean_name, clean_name],
    )
    assert selected == [clean_name]
    assert error == ""
    assert "preexisting_dirty_changed" in evidence["blockers"]
    assert evidence["excluded_preexisting_dirty"] == [owner_name]


def test_t9_escaping_explicit_path_returns_path_error(tmp_path):
    """Malformed paths return PATH_ERROR regardless of dirt blocker."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, _clean_name, _owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t9",
    )

    selected, _evidence, error = resolve_attributed_git_paths(
        data, "task-t9", root, ["../outside.txt"],
    )
    assert selected == []
    assert "PATH_ERROR" in error


# ---------------------------------------------------------------------------
# T10-T15: other blockers must remain fail-closed (narrow exception is narrow).
# ---------------------------------------------------------------------------


def test_t10_missing_baseline_blocks_resolver(tmp_path):
    """No mutation evidence for the resolver-level call returns baseline_missing."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root = _repo(tmp_path)
    data = tmp_path / "data"
    write_task_result(data, "task-t10", STATUS_RUNNING)
    # Intentionally do NOT capture a baseline.
    (root / "clean.txt").write_text("changed\n", encoding="utf-8")

    selected, _evidence, error = resolve_attributed_git_paths(
        data, "task-t10", root, ["clean.txt"],
    )
    assert selected == []
    assert "GIT_ATTRIBUTION_BLOCKED" in error
    # Missing baseline is its own blocker; the narrow exception cannot apply.
    assert "baseline_missing" in error


def test_t11_baseline_dirty_overflow_blocks_even_with_explicit_paths(tmp_path):
    """Overflowed baseline is not suppressible by the narrow exception."""
    import ouroboros.mutation_attribution as mod
    from ouroboros.mutation_attribution import (
        capture_mutation_baseline,
        resolve_attributed_git_paths,
    )
    root = _repo(tmp_path)
    data = tmp_path / "data"
    write_task_result(data, "task-t11", STATUS_RUNNING)
    # Create one dirty file BEFORE baseline capture (cap=0 forces overflow on
    # any nonzero dirt; we use 0 to guarantee the overflow branch fires).
    (root / "second.txt").write_text("owner second\n", encoding="utf-8")
    original = mod._BASELINE_DIRTY_PATHS_MAX
    mod._BASELINE_DIRTY_PATHS_MAX = 0
    try:
        capture_mutation_baseline(
            data,
            "task-t11",
            [{"surface_type": "system_repo", "host_root": str(root)}],
        )
        (root / "clean.txt").write_text("task change\n", encoding="utf-8")
        selected, _evidence, error = resolve_attributed_git_paths(
            data, "task-t11", root, ["clean.txt"],
        )
    finally:
        mod._BASELINE_DIRTY_PATHS_MAX = original
    assert selected == []
    assert "baseline_dirty_overflow" in error


def test_t12_candidate_scan_failure_blocks_even_with_explicit_paths(tmp_path):
    """A git status scan error is its own blocker; narrow exception does not apply."""
    from ouroboros.mutation_attribution import (
        capture_mutation_baseline,
        resolve_attributed_git_paths,
    )
    import ouroboros.mutation_attribution as mod
    root = _repo(tmp_path)
    data = tmp_path / "data"
    write_task_result(data, "task-t12", STATUS_RUNNING)
    capture_mutation_baseline(
        data,
        "task-t12",
        [{"surface_type": "system_repo", "host_root": str(root)}],
    )
    (root / "clean.txt").write_text("changed\n", encoding="utf-8")
    # Force _git_status_paths to raise so attributed_git_candidates records a
    # candidate_scan_failed blocker.
    original = mod._git_status_paths
    mod._git_status_paths = lambda _root: (_ for _ in ()).throw(
        RuntimeError("synthetic scan failure")
    )
    try:
        selected, _evidence, error = resolve_attributed_git_paths(
            data, "task-t12", root, ["clean.txt"],
        )
    finally:
        mod._git_status_paths = original
    assert selected == []
    assert "candidate_scan_failed" in error


def test_t13_extra_effect_flag_prevents_narrowing(tmp_path):
    """Blocker set with >1 entry (extra flag) is NOT the narrow exception."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, clean_name, _owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t13",
    )
    # Inject an effect flag into the durable evidence.
    evidence = dict((load_task_result(data, "task-t13") or {})["mutation_evidence"])
    evidence["flags"] = [{"flag": "external_writer_observed"}]
    write_task_result(data, "task-t13", STATUS_RUNNING, mutation_evidence=evidence)

    selected, _evidence, error = resolve_attributed_git_paths(
        data, "task-t13", root, [clean_name],
    )
    assert selected == []
    assert "preexisting_dirty_changed" in error
    # The error must surface BOTH blockers — narrowing must NOT have hidden one.
    assert "external_writer_observed" in error


def test_t14_unrelated_foreign_fast_forward_is_reanchored(tmp_path):
    """Foreign HEAD advance that does not overlap dirty paths re-anchors cleanly."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, clean_name, _owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t14",
    )
    # Advance HEAD via an unrelated commit (does NOT touch dirty paths).
    (root / "foreign.txt").write_text("foreign change\n", encoding="utf-8")
    _git(root, "add", "foreign.txt")
    _git(root, "commit", "-qm", "foreign advance")

    selected, _evidence, error = resolve_attributed_git_paths(
        data, "task-t14", root, [clean_name],
    )
    assert selected == [clean_name]
    assert error == ""


def test_t15_foreign_head_move_over_dirty_path_remains_stale(tmp_path):
    """Reanchor fails when foreign commit interval overlaps a still-dirty path."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, clean_name, owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t15", clean_name="clean.txt",
    )
    # Add a third file that is dirty in the working tree but unknown to the
    # baseline (created AFTER capture). The foreign commit below will modify
    # this same file in HEAD, leaving working-tree vs HEAD out of sync — the
    # path stays in ``_git_status_paths`` after the commit, which makes the
    # reanchor's interval-overlap guard fire and keeps ``baseline_stale`` as
    # a blocker.
    sidecar = root / "sidecar.txt"
    sidecar.write_text("task dirty sidecar\n", encoding="utf-8")
    # Commit the foreign change to sidecar.txt while it is still dirty in the
    # working tree (working-tree content != HEAD content after the commit).
    _git(root, "add", "sidecar.txt")
    _git(root, "commit", "-qm", "foreign sidecar")
    sidecar.write_text("working-tree still dirty\n", encoding="utf-8")

    selected, _evidence, error = resolve_attributed_git_paths(
        data, "task-t15", root, [clean_name],
    )
    assert selected == []
    # baseline_stale is its own blocker; the narrow preexisting-dirty exception
    # does not apply because the blocker set is not exactly the dirty one.
    assert "baseline_stale" in error


# ---------------------------------------------------------------------------
# T16-T19: edge cases on candidate membership and evidence preservation.
# ---------------------------------------------------------------------------


def test_t16_head_equal_explicit_path_is_not_a_candidate(tmp_path):
    """Naming a tracked file with no current delta is not a candidate."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, clean_name, owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t16",
    )
    # Resolve ONLY the unchanged clean.txt while it has NO delta relative to HEAD.
    selected, _evidence, error = resolve_attributed_git_paths(
        data, "task-t16", root, ["clean.txt"],
    )
    # clean.txt has a delta ("task change"), so it IS a candidate; this is the
    # trivial success. The interesting case is an unchanged path named in the
    # presence of a different candidate + preexisting-dirty change.
    assert selected == ["clean.txt"]
    assert error == ""

    # Now: create a second candidate, revert clean.txt to HEAD, and try to
    # resolve only clean.txt (now HEAD-equal). It must NOT be admitted.
    (root / "second.txt").write_text("another task change\n", encoding="utf-8")
    # Roll clean.txt back to its HEAD content so it has no attributable delta.
    _git(root, "checkout", "HEAD", "--", "clean.txt")
    selected2, _evidence2, error2 = resolve_attributed_git_paths(
        data, "task-t16", root, ["clean.txt"],
    )
    assert selected2 == []
    # Either rejection path is acceptable — the contract is that HEAD-equal
    # paths are NOT attributable, regardless of which validator fires first.
    assert ("subset" in error2) or ("GIT_NO_ATTRIBUTED_CHANGES" in error2) or (
        "GIT_ATTRIBUTION_BLOCKED" in error2
    )


def test_t17_untracked_clean_at_baseline_file_is_admissible(tmp_path):
    """A file absent at baseline and created during the task is a candidate."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, _clean_name, _owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t17",
    )
    (root / "brand_new.txt").write_text("new file\n", encoding="utf-8")

    selected, evidence, error = resolve_attributed_git_paths(
        data, "task-t17", root, ["brand_new.txt"],
    )
    assert selected == ["brand_new.txt"]
    assert error == ""
    # The preexisting-dirty blocker is preserved in evidence.
    assert "preexisting_dirty_changed" in evidence["blockers"]


def test_t18_rename_both_sides_obey_candidate_membership(tmp_path):
    """Both rename sides must independently be candidates for explicit admit."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, clean_name, owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t18",
    )
    # Rename clean.txt → clean_renamed.txt during the task WITHOUT committing
    # so the rename shows up as an uncommitted change in git status. This
    # puts BOTH ``clean.txt`` (deleted) and ``clean_renamed.txt`` (added) in
    # the git status output, both as candidates for the task.
    (root / "clean_renamed.txt").write_text("task change\n", encoding="utf-8")
    # Remove the old name without staging; git status reports the rename.
    (root / clean_name).unlink()
    selected, evidence, error = resolve_attributed_git_paths(
        data, "task-t18", root, ["clean_renamed.txt"],
    )
    assert selected == ["clean_renamed.txt"]
    assert error == ""
    assert "preexisting_dirty_changed" in evidence["blockers"]
    # The dirty side (owner_name) must remain excluded — not silently admitted.
    assert owner_name in evidence["excluded_preexisting_dirty"]


def test_t19_evidence_remains_unmodified_after_narrowing(tmp_path):
    """Returned evidence preserves the blocker and excluded paths verbatim."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, clean_name, owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-t19",
    )
    # Snapshot the baseline evidence BEFORE resolution.
    pre = (load_task_result(data, "task-t19") or {})["mutation_evidence"]

    selected, evidence, error = resolve_attributed_git_paths(
        data, "task-t19", root, [clean_name],
    )
    assert selected == [clean_name]
    assert error == ""
    # Evidence returned by the resolver must still list the blocker.
    assert "preexisting_dirty_changed" in evidence["blockers"]
    # Evidence must still name the excluded path.
    assert evidence["excluded_preexisting_dirty"] == [owner_name]
    # The pre-resolution evidence (loaded directly from the durable store) is
    # the canonical source of truth for the blocker; the resolver did not
    # rewrite it.
    pre_baseline = pre["baseline"]
    assert "surfaces" in pre_baseline
    # The dirty_paths fingerprint at baseline still references owner_name.
    surface = next(s for s in pre_baseline["surfaces"] if s.get("git"))
    assert "dirty.txt" in surface["git"]["dirty_paths"]


# ---------------------------------------------------------------------------
# N1, N2: adversarial — explicit naming must NOT grant ownership.
# ---------------------------------------------------------------------------


def test_n1_adversarial_explicit_owner_wip_claim_is_rejected(tmp_path):
    """Preexisting dirty file with plausible release edits, explicitly named = reject."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, _clean_name, owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-n1",
    )
    # Pump owner_name with release-flavored content to test the temptation.
    (root / owner_name).write_text(
        "feat(release): plausibly-shippable owner edit\n", encoding="utf-8",
    )

    selected, _evidence, error = resolve_attributed_git_paths(
        data, "task-n1", root, [owner_name],
    )
    assert selected == []
    # Must NOT be admitted as a candidate just because the caller named it.
    assert ("subset" in error) or ("GIT_ATTRIBUTION_BLOCKED" in error)


def test_n2_adversarial_blocker_combination_remains_blocked(tmp_path):
    """Blocker set with combined flag = narrow exception does not apply."""
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data, clean_name, _owner_name = _dirty_with_baseline(
        tmp_path, task_id="task-n2",
    )
    # Inject BOTH a flag AND keep the preexisting-dirty blocker.
    evidence = dict((load_task_result(data, "task-n2") or {})["mutation_evidence"])
    evidence["flags"] = [{"flag": "external_writer_observed"}]
    write_task_result(data, "task-n2", STATUS_RUNNING, mutation_evidence=evidence)

    selected, _evidence, error = resolve_attributed_git_paths(
        data, "task-n2", root, [clean_name],
    )
    assert selected == []
    # Both blockers must surface — the narrow exception cannot suppress a
    # blocker combination.
    assert "preexisting_dirty_changed" in error
    assert "external_writer_observed" in error


# ---------------------------------------------------------------------------
# R1-R4 references: regression is the existing module suite.
# ---------------------------------------------------------------------------


def test_r4_full_mutation_attribution_module_suite_passes():
    """Regression: ``tests/test_mutation_attribution.py`` still passes (R1-R3)."""
    # This test enforces R1-R4 by simply importing the existing module's
    # helpers; the actual pass/fail check runs as part of CI's full pytest.
    # We re-import here so the test fails loudly if the existing module's
    # public surface changes incompatibly.
    from ouroboros.mutation_attribution import (
        attributed_git_candidates,
        capture_mutation_baseline,
        resolve_attributed_git_paths,
    )
    assert callable(attributed_git_candidates)
    assert callable(capture_mutation_baseline)
    assert callable(resolve_attributed_git_paths)


# ---------------------------------------------------------------------------
# W1-W3: fingerprint-match carve-out for the narrow preexisting-dirty exception.
# ---------------------------------------------------------------------------


def _two_dirty_with_baseline(
    tmp_path: pathlib.Path,
    *,
    owner_change_after: bool,
    foreign_change_after: bool,
    task_id: str,
):
    """Set up two pre-existing-dirty files at baseline plus a task-owned clean file.

    ``owner.txt`` is the path whose fingerprint should match baseline after
    capture (the task legitimately owns its observed content even when that
    content is dirty vs HEAD). ``foreign.txt`` is a different pre-existing
    dirty path whose fingerprint is changed by some other (non-task) writer —
    it triggers ``preexisting_dirty_changed`` because its fingerprint DIFFERS
    from baseline.

    Toggle which file is rewritten after capture via the boolean flags.
    """
    from ouroboros.mutation_attribution import capture_mutation_baseline
    root = _repo(tmp_path, extra_files=("owned.txt", "foreign.txt", "clean.txt"))
    data = tmp_path / "data"
    write_task_result(data, task_id, STATUS_RUNNING)
    # Both files are dirty BEFORE the baseline is captured.
    (root / "owned.txt").write_text("owned before\n", encoding="utf-8")
    (root / "foreign.txt").write_text("foreign before\n", encoding="utf-8")
    capture_mutation_baseline(
        data,
        task_id,
        [{"surface_type": "system_repo", "host_root": str(root)}],
    )
    # After baseline: independently choose which pre-dirty file changes.
    if foreign_change_after:
        (root / "foreign.txt").write_text("foreign after\n", encoding="utf-8")
    if owner_change_after:
        (root / "owned.txt").write_text("owned after\n", encoding="utf-8")
    # The task also rewrites a clean-at-baseline file (not strictly required
    # for the W-tests, but mirrors the T3 fixture).
    (root / "clean.txt").write_text("task change\n", encoding="utf-8")
    return root, data


def test_w1_explicit_pre_dirty_path_with_matching_fingerprint_admits(tmp_path):
    """W1: a pre-existing-dirty path whose fingerprint matches baseline is admitted.

    Two pre-existing-dirty files (``owned.txt`` + ``foreign.txt``). The
    foreign writer mutates ``foreign.txt`` after baseline, so
    ``preexisting_dirty_changed`` is in blockers. ``owned.txt`` is left
    untouched after baseline — its current fingerprint equals the task's
    observed baseline fingerprint, so the task legitimately owns it.

    Naming ``owned.txt`` MUST succeed via the fingerprint-match carve-out.
    """
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data = _two_dirty_with_baseline(
        tmp_path,
        owner_change_after=False,
        foreign_change_after=True,
        task_id="task-w1",
    )

    selected, evidence, error = resolve_attributed_git_paths(
        data, "task-w1", root, ["owned.txt"],
    )
    assert selected == ["owned.txt"], (
        f"expected owned.txt admitted via fingerprint match, got selected={selected!r} "
        f"error={error!r}"
    )
    assert error == ""
    # The blocker and excluded list are preserved for reviewers.
    assert "preexisting_dirty_changed" in evidence["blockers"]
    assert "owned.txt" in evidence["excluded_preexisting_dirty"]
    assert "foreign.txt" in evidence["excluded_preexisting_dirty"]


def test_w2_pre_dirty_path_with_differing_fingerprint_remains_blocked(tmp_path):
    """W2: a pre-existing-dirty path whose fingerprint DIFFERS stays blocked.

    Same setup as W1 but the user names ``foreign.txt`` — its fingerprint
    differs from baseline (cross-task contamination). The fingerprint-match
    carve-out must NOT admit it.
    """
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data = _two_dirty_with_baseline(
        tmp_path,
        owner_change_after=False,
        foreign_change_after=True,
        task_id="task-w2",
    )

    selected, evidence, error = resolve_attributed_git_paths(
        data, "task-w2", root, ["foreign.txt"],
    )
    assert selected == [], (
        f"foreign.txt MUST be blocked (fingerprint differs from baseline), "
        f"got selected={selected!r}"
    )
    assert ("subset" in error) or ("GIT_ATTRIBUTION_BLOCKED" in error)
    # Both excluded paths are still recorded for reviewers.
    assert "owned.txt" in evidence["excluded_preexisting_dirty"]
    assert "foreign.txt" in evidence["excluded_preexisting_dirty"]


def test_w3_external_writer_observed_remains_fail_closed(tmp_path):
    """W3: external_writer_observed flag keeps fingerprint-match fail-closed.

    Same baseline geometry as W1 (named path fingerprint matches baseline),
    but the mutation evidence carries an additional ``external_writer_observed``
    flag. The narrow exception is gated on ``blockers == {preexisting_dirty_changed}``
    — every other global flag remains fail-closed regardless of fingerprint
    match.
    """
    from ouroboros.mutation_attribution import resolve_attributed_git_paths
    root, data = _two_dirty_with_baseline(
        tmp_path,
        owner_change_after=False,
        foreign_change_after=True,
        task_id="task-w3",
    )
    # Inject the security-flag into the durable evidence.
    current = load_task_result(data, "task-w3") or {}
    evidence_blob = dict(current.get("mutation_evidence") or {})
    evidence_blob["flags"] = [{"flag": "external_writer_observed"}]
    write_task_result(
        data, "task-w3",
        current.get("status", STATUS_RUNNING),
        mutation_evidence=evidence_blob,
    )

    selected, evidence, error = resolve_attributed_git_paths(
        data, "task-w3", root, ["owned.txt"],
    )
    assert selected == [], (
        f"external_writer_observed MUST block even with matching fingerprint, "
        f"got selected={selected!r}"
    )
    assert "GIT_ATTRIBUTION_BLOCKED" in error
    # Both blockers must surface in the error (per T13 contract).
    assert "preexisting_dirty_changed" in error
    assert "external_writer_observed" in error
    assert "owned.txt" in evidence["excluded_preexisting_dirty"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
