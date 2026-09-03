"""Payload custody: no-repository apply, the payload lock, and orphan disposition.

Sibling of ``tests/test_delegated_skill_payload.py`` (which owns the capability
itself). This module covers the custody obligations around it: that a Git
repository living ABOVE the runtime data root cannot make ``git apply`` skip
every hunk in silence, that a provable non-mutation is refused typed instead of
reported as a success, that a settled run whose OWNER TASK is terminal stops
holding its skill payload hostage forever, and that such an orphan may be
disposed by a live top-level task holding the same target.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

from ouroboros import delegate_custody as custody
from tests.test_delegated_skill_payload import (  # noqa: F401 - shared fixtures
    _captured,
    _exact_payload_start,
    _payload_ctx,
    _payload_entry,
    _provisioned,
    _seed_skill,
    _start_payload_run,
    _terminal_wait,
    _owned_gateway_uses_each_test_transport,
)


def _ancestor_repo(tmp_path: pathlib.Path) -> None:
    """A Git worktree ABOVE the runtime data root — the live shape that broke
    the payload apply (the operator's own checkout containing ``data/``)."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)


# -- PC-F01: the payload apply runs in NO-REPOSITORY mode -----------------------


def test_ancestor_git_repo_above_the_payload_cannot_silently_skip_the_apply(
        tmp_path, monkeypatch):
    """With an ancestor ``.git`` above the payload, git treats the payload as a
    subdirectory prefix: every hunk is skipped at rc=0 and ``--numstat`` prints
    nothing. The apply must still land the bytes."""
    from ouroboros.tools.subagent_integration import _integrate_delegated_patch

    _ancestor_repo(tmp_path)
    ctx, skill, handle, entry, capture = _captured(tmp_path, monkeypatch)
    assert capture["status"] == "ready_with_changes", capture
    patch_text = pathlib.Path(capture["patch_artifact"]).read_text(
        encoding="utf-8", errors="replace")
    # A GIT-FORMAT patch is what makes this fixture load-bearing: a plain
    # unified diff applies fine under an ancestor repo and would pin nothing.
    assert "diff --git" in patch_text, patch_text[:400]
    out = _integrate_delegated_patch(ctx, "run-p1", "apply", "")
    assert "✅ Integrated" in out, out
    assert (skill / "notes.txt").read_text(encoding="utf-8") == "DONE\n"
    assert not (skill / ".git").exists()
    assert entry.patch_disposed == "applied"
    custody._CUSTODY.clear()


def test_touched_path_reader_needs_the_ceiling_at_the_payload_parent(
        tmp_path, monkeypatch):
    """The empirical core of the fix, asserted directly on the numstat reader:
    blind without a ceiling, still blind with the ceiling pinned AT the payload,
    correct only with the ceiling at the payload's PARENT."""
    from ouroboros.subagent_worktrees import isolated_git_env
    from ouroboros.tools.subagent_integration import _patch_touched_paths

    _ancestor_repo(tmp_path)
    ctx, skill, handle, entry, capture = _captured(tmp_path, monkeypatch)
    patch_path = pathlib.Path(capture["patch_artifact"])

    blind, blind_err = _patch_touched_paths(patch_path, skill, env=isolated_git_env())
    assert blind == set() and not blind_err, (blind, blind_err)

    at_payload, _ = _patch_touched_paths(patch_path, skill, env={
        **isolated_git_env(), "GIT_CEILING_DIRECTORIES": str(skill.resolve())})
    assert at_payload == set(), at_payload

    at_parent, parent_err = _patch_touched_paths(patch_path, skill, env={
        **isolated_git_env(),
        "GIT_CEILING_DIRECTORIES": str(skill.resolve().parent)})
    assert not parent_err and "notes.txt" in at_parent, (at_parent, parent_err)
    custody._CUSTODY.clear()


def test_a_no_op_apply_is_refused_typed_even_with_no_recorded_result_hash(
        tmp_path, monkeypatch):
    """The guard sits BEFORE the result-hash conditional: a manifest that never
    recorded a result content hash used to route a provable non-mutation straight
    into the success finalizer."""
    import ouroboros.tools.delegate_integration as integration
    from ouroboros.extension_reconcile_queue import list_extension_reconcile_requests
    from ouroboros.tools.subagent_integration import _integrate_delegated_patch

    ctx, skill, handle, entry, capture = _captured(tmp_path, monkeypatch)
    manifest_path = pathlib.Path(capture["manifest_artifact"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("result_content_hash", None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    real = integration.payload_content_hash
    calls = {"n": 0}

    def _baseline_after_apply(root):
        calls["n"] += 1     # call 1 = pre-apply CAS check, call 2 = post-apply
        return handle.payload_hash if calls["n"] >= 2 else real(root)

    monkeypatch.setattr(integration, "payload_content_hash", _baseline_after_apply)
    out = _integrate_delegated_patch(ctx, "run-p1", "apply", "")
    assert "INTEGRATE_APPLY_NO_OP" in out, out
    assert "✅" not in out and "provable non-mutation" in out
    # PC-F09: the consequence and the closing move are named at the point of need.
    assert "decision='reject'" in out
    assert "Done with warnings" in out and "delegated_custody_unreconciled" in out
    # Nothing disposed, no reconcile queued, and the apply intent is RESOLVED.
    assert entry.patch_disposed == ""
    assert entry.patch_apply_pending is False
    assert list_extension_reconcile_requests(tmp_path / "data") == []
    custody._CUSTODY.clear()


def test_no_op_refusal_keeps_the_retry_lane_open(tmp_path, monkeypatch):
    """The resolved intent must not route the next attempt into APPLY_AMBIGUOUS:
    once the payload really mutates, the same run integrates normally."""
    import ouroboros.tools.delegate_integration as integration
    from ouroboros.tools.subagent_integration import _integrate_delegated_patch

    ctx, skill, handle, entry, capture = _captured(tmp_path, monkeypatch)
    real = integration.payload_content_hash
    calls = {"n": 0}

    def _baseline_after_apply(root):
        calls["n"] += 1
        return handle.payload_hash if calls["n"] >= 2 else real(root)

    monkeypatch.setattr(integration, "payload_content_hash", _baseline_after_apply)
    out = _integrate_delegated_patch(ctx, "run-p1", "apply", "")
    assert "INTEGRATE_APPLY_NO_OP" in out, out
    # The first attempt really did apply the bytes, so restore the payload before
    # retrying under the real hash function.
    (skill / "notes.txt").write_text("PENDING\n", encoding="utf-8")
    (skill / "extra.txt").unlink()
    monkeypatch.setattr(integration, "payload_content_hash", real)
    again = _integrate_delegated_patch(ctx, "run-p1", "apply", "")
    assert "APPLY_AMBIGUOUS" not in again, again
    assert "✅ Integrated" in again, again
    custody._CUSTODY.clear()


def test_apply_hash_mismatch_receipt_names_a_followable_recovery(
        tmp_path, monkeypatch):
    """The mismatch receipt used to prescribe ``decision='acknowledge_ambiguous'``
    — a value absent from the decision enum. The real flag is the boolean."""
    import ouroboros.tools.delegate_integration as integration
    from ouroboros.tools.subagent_integration import _integrate_delegated_patch

    ctx, skill, handle, entry, capture = _captured(tmp_path, monkeypatch)
    real = integration.payload_content_hash
    calls = {"n": 0}

    def _hash_diverges_after_apply(root):
        calls["n"] += 1
        return "0" * 64 if calls["n"] >= 2 else real(root)

    monkeypatch.setattr(integration, "payload_content_hash", _hash_diverges_after_apply)
    out = _integrate_delegated_patch(ctx, "run-p1", "apply", "")
    assert "INTEGRATE_APPLY_HASH_MISMATCH" in out, out
    assert "acknowledge_ambiguous=true after inspection" in out, out
    assert "decision='acknowledge_ambiguous'" not in out, out
    assert "decision='reject'" in out, out
    assert "Done with warnings" in out and "delegated_custody_unreconciled" in out
    custody._CUSTODY.clear()


# -- PC-F09: the consequence is discoverable from the tool schema ---------------


def test_integrate_schema_states_the_finalization_consequence_and_the_reject_exit():
    from ouroboros.tools.subagent_integration import get_tools

    entry = next(e for e in get_tools() if e.name == "integrate_delegated_patch")
    description = str(entry.schema["description"])
    assert "Done with warnings" in description
    assert "delegated_custody_unreconciled" in description
    assert "reject is the closing move" in description
    assert entry.schema["parameters"]["properties"]["decision"]["enum"] == [
        "apply", "reject"]
