"""Tests for ibl-local-29ee0d0e37bc — sanctioned size-ratchet manifest update path.

The size-ratchet manifest sits on ``FROZEN_CONTRACT_PATHS`` so every shell
command reverts it via ``_restore_protected_runtime_paths``. But the OFFICIAL
CI ``size_ratchet`` lane ALSO blocks on size-ratchet drift, so an agent that
gets the manifest right should be able to KEEP its edit instead of losing
it to a reverter — the manifest still has to pass ``commit_reviewed``'s
own review gates, but it must be allowed to survive the shell-restore step.

These tests pin the four contract branches:

  (a) dirty manifest with a valid additive-debt transition + a shell command
      -> manifest SURVIVES, ``SIZE_RATCHET_MANIFEST_UPDATE_PRESERVED`` note
         present, ``PROTECTED_PATH_AUTO_RESTORED`` absent.
  (b) dirty manifest with an INVALID transition (undeclared growth) ->
      still reverted, ``PROTECTED_PATH_AUTO_RESTORED`` present, no
      preservation note.
  (c) manifest dirty AND another protected path (BIBLE.md) dirty ->
      both reverted, no preservation note.
  (d) manifest clean -> no behavior change, no preservation note, no
      auto-restore note.

The validator (``ouroboros.review.validate_size_ratchet``) walks the live
tree via ``collect_size_ratchet_inventory``. Each test sets up a real git
repo whose module inventory exactly matches the committed manifest, then
mutates the manifest to exercise one branch of the gate.
"""

from __future__ import annotations

import pathlib
import subprocess as sp
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from ouroboros.tools.shell import _run_shell


SIZE_RATCHET_MANIFEST_RELPATH = "ouroboros/size_ratchet_manifest.py"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _init_ratchet_repo(
    tmp_path: pathlib.Path,
    *,
    include_large_module: bool = False,
) -> pathlib.Path:
    """Build a real git repo with a committed, valid size-ratchet manifest.

    The manifest is pinned to HEAD's actual SHA (via a second commit) so
    the validator's ``BASELINE_SOURCE_SHA is immutable`` rule holds across
    the transition we test below.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    sp.run(["git", "init", "-q"], cwd=repo, check=True)
    sp.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    sp.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    sp.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)

    # A second protected path so test (c) can dirty both at once.
    (repo / "BIBLE.md").write_text("original constitution\n")
    (repo / "README.md").write_text("original readme\n")

    manifest_dir = repo / "ouroboros"
    manifest_dir.mkdir()
    (manifest_dir / "__init__.py").write_text("")  # canonical POSIX paths
    manifest_text = (
        '"""Generated data-only size debt manifest fixture."""\n'
        "\n"
        'BASELINE_SOURCE_SHA = "PLACEHOLDER"\n'
        "\n"
        "GIANT_PATHS = ()\n"
        "FUNCTION_DEBT = ()\n"
        "BAND_BASELINE_PATHS = ()\n"
        "BAND_PATHS = {}\n"
        "BYTE_BASELINE_DEBT = {}\n"
        "BYTE_DEBT = {}\n"
    )
    (manifest_dir / "size_ratchet_manifest.py").write_text(manifest_text)

    if include_large_module:
        # 1100 ``x = 1`` lines + 1 preamble = 1101 total lines. Comfortably
        # in the 1001..1500 BAND range with TARGET_MODULE_LINES=1000 and
        # BAND_MODULE_MAX_LINES=1500.
        big = '"""big module."""\n' + "x = 1\n" * 1100
        (manifest_dir / "big_module.py").write_text(big)

    sp.run(["git", "add", "-A"], cwd=repo, check=True)
    sp.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)

    # Pin BASELINE_SOURCE_SHA to HEAD's SHA so the validator's
    # ``BASELINE_SOURCE_SHA is immutable`` rule holds across the test's
    # follow-on edit (otherwise the edit itself changes the SHA, which
    # is a valid transition-error).
    head_sha = sp.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    manifest_path = repo / SIZE_RATCHET_MANIFEST_RELPATH
    manifest_path.write_text(manifest_path.read_text().replace("PLACEHOLDER", head_sha))
    sp.run(["git", "add", "-A"], cwd=repo, check=True)
    sp.run(["git", "commit", "-q", "-m", "pin baseline sha"], cwd=repo, check=True)

    return repo


def _ctx(repo: pathlib.Path) -> SimpleNamespace:
    """Minimal ctx used by ``_run_shell`` tests; mirrors the existing pattern."""
    return SimpleNamespace(
        repo_dir=repo,
        drive_root=repo,
        drive_logs=lambda: pathlib.Path(str(repo)),
    )


def _stub_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``_tracked_subprocess_run`` so the shell command itself is a no-op."""
    monkeypatch.setattr("ouroboros.tools.shell.load_settings", lambda: {})
    monkeypatch.setattr(
        "ouroboros.tools.shell._tracked_subprocess_run",
        lambda cmd, **kwargs: CompletedProcess(cmd, 0, "ok", ""),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_size_ratchet_valid_additive_transition_preserved(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a) Dirty manifest with a valid additive band-path transition survives."""
    repo = _init_ratchet_repo(tmp_path, include_large_module=True)
    manifest_path = repo / SIZE_RATCHET_MANIFEST_RELPATH

    # Sanity: the committed manifest does NOT yet record big_module.py.
    committed = manifest_path.read_text()
    assert "big_module.py" not in committed

    # Valid additive transition: add the live 1101-line module to BAND_PATHS
    # with a nonblank rationale. The validator will see big_module.py in
    # the live inventory (it exists on disk and is tracked by git) and in
    # the manifest — exact match, plus the transition rule for new band
    # paths requires a nonblank rationale, which we supply.
    new_manifest = committed.replace(
        "BAND_PATHS = {}",
        'BAND_PATHS = {"ouroboros/big_module.py": "test fixture module entered the band"}\n',
    )
    assert "big_module.py" in new_manifest
    manifest_path.write_text(new_manifest)

    _stub_subprocess(monkeypatch)

    result = _run_shell(_ctx(repo), ["echo", "hi"])

    # Preservation note is emitted; the standard auto-restore note is NOT.
    assert "SIZE_RATCHET_MANIFEST_UPDATE_PRESERVED" in result
    assert "PROTECTED_PATH_AUTO_RESTORED" not in result
    # Manifest SURVIVES on disk — the additive entry is still there.
    on_disk = manifest_path.read_text()
    assert "big_module.py" in on_disk
    assert "test fixture module entered the band" in on_disk


def test_size_ratchet_invalid_transition_still_reverted(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b) A dirty manifest with an INVALID transition (undeclared growth) is reverted."""
    repo = _init_ratchet_repo(tmp_path)
    manifest_path = repo / SIZE_RATCHET_MANIFEST_RELPATH

    # Invalid: add a non-existent path to GIANT_PATHS. The validator will
    # compute the live inventory (the fake path does not exist) and report
    # ``GIANT_PATHS contains stale entry: 'ouroboros/fake_module.py'``.
    committed = manifest_path.read_text()
    new_manifest = committed.replace(
        "GIANT_PATHS = ()\n",
        'GIANT_PATHS = ("ouroboros/fake_module.py",)\n',
    )
    assert "fake_module.py" in new_manifest
    manifest_path.write_text(new_manifest)

    _stub_subprocess(monkeypatch)

    result = _run_shell(_ctx(repo), ["echo", "hi"])

    # Standard auto-restore fires; preservation note does NOT.
    assert "PROTECTED_PATH_AUTO_RESTORED" in result
    assert "SIZE_RATCHET_MANIFEST_UPDATE_PRESERVED" not in result
    # Manifest REVERTED: the stale entry is gone, original GIANT_PATHS is back.
    on_disk = manifest_path.read_text()
    assert "fake_module.py" not in on_disk
    assert on_disk == committed


def test_size_ratchet_with_another_protected_path_dirty_reverts_both(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(c) Manifest dirty AND another protected path (BIBLE.md) dirty -> both reverted.

    The sanctioned path is ONLY triggered when the size-ratchet manifest is
    the SOLE dirty protected path. If BIBLE.md (or any other protected path)
    is dirty alongside it, the existing revert-everything behavior wins.
    """
    repo = _init_ratchet_repo(tmp_path, include_large_module=True)
    manifest_path = repo / SIZE_RATCHET_MANIFEST_RELPATH
    bible_path = repo / "BIBLE.md"

    # The MANIFEST edit alone would be a valid additive band transition.
    committed = manifest_path.read_text()
    manifest_path.write_text(
        committed.replace(
            "BAND_PATHS = {}",
            'BAND_PATHS = {"ouroboros/big_module.py": "test fixture module entered the band"}\n',
        )
    )

    # ALSO dirty BIBLE.md (a different protected runtime path).
    bible_path.write_text("tampered by shell command\n")

    _stub_subprocess(monkeypatch)

    result = _run_shell(_ctx(repo), ["echo", "hi"])

    # Standard revert fires; BIBLE.md is named.
    assert "PROTECTED_PATH_AUTO_RESTORED" in result
    assert "BIBLE.md" in result
    # Preservation note does NOT fire — multi-dirty never partial-exempt.
    assert "SIZE_RATCHET_MANIFEST_UPDATE_PRESERVED" not in result
    # Manifest REVERTED (the valid-in-isolation entry is gone).
    on_disk = manifest_path.read_text()
    assert "big_module.py" not in on_disk
    assert on_disk == committed
    # BIBLE.md REVERTED (back to HEAD's original).
    assert bible_path.read_text() == "original constitution\n"


def test_size_ratchet_clean_manifest_no_behavior_change(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(d) Clean manifest, unprotected edit -> no preservation note, no auto-restore."""
    repo = _init_ratchet_repo(tmp_path)
    readme_path = repo / "README.md"
    readme_path.write_text("edited by shell command\n")

    _stub_subprocess(monkeypatch)

    result = _run_shell(_ctx(repo), ["echo", "hi"])

    # Neither note fires: the manifest was NOT dirty, so the sanctioned
    # path is irrelevant; an unprotected path is dirty, so the auto-restore
    # path is irrelevant.
    assert "SIZE_RATCHET_MANIFEST_UPDATE_PRESERVED" not in result
    assert "PROTECTED_PATH_AUTO_RESTORED" not in result
    # Unprotected edit SURVIVES (the existing pre-condition).
    assert readme_path.read_text() == "edited by shell command\n"