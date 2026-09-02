"""Single-element argv autocorrect: `["git && status && --porcelain"]` and
`["grep -rn foo . | head -20"]` are argv-boundary mistakes, not real shell
syntax. `_literal_argv_notes` (#447 A5) discloses the shape but the command
still dies `[Errno 2]`; these helpers rewrite the obvious mistake into the
argv the caller meant so the command runs, and disclose what they did.

Narrowness is the contract: any segment with internal whitespace, any other
shell metacharacter, a glued (non-bracketed) pipe, `||`, a leading shell
interpreter, or an already-correct multi-element argv is left untouched.
"""

from __future__ import annotations

import pathlib
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from ouroboros.tools.shell import _run_shell
from ouroboros.tools.shell_and_chain import (
    _maybe_split_single_element_and_chain,
    _maybe_wrap_single_element_pipeline,
)


def _ctx(tmp_path):
    return SimpleNamespace(
        repo_dir=tmp_path,
        drive_root=tmp_path,
        drive_logs=lambda: pathlib.Path(str(tmp_path)),
    )


@pytest.fixture
def fake_subprocess(monkeypatch):
    monkeypatch.setattr("ouroboros.tools.shell.load_settings", lambda: {})

    def _install(*, returncode: int = 0, stdout: str = "", stderr: str = ""):
        calls: list[dict] = []

        def fake_run(cmd, **kwargs):
            calls.append({"cmd": cmd, "kwargs": kwargs})
            return CompletedProcess(cmd, returncode, stdout, stderr)

        monkeypatch.setattr("ouroboros.tools.shell._tracked_subprocess_run", fake_run)
        return calls

    return _install


# --------------------------------------------------------------------------
# _maybe_split_single_element_and_chain
# --------------------------------------------------------------------------


class TestAndChainSplit:
    def test_splits_three_single_token_segments(self):
        out, note = _maybe_split_single_element_and_chain(["git && status && --porcelain"])
        assert out == ["git", "status", "--porcelain"]
        assert "SHELL_CMD_AUTO_SPLIT" in note

    def test_does_not_split_segment_with_internal_space(self):
        cmd = ["cd foo && make"]
        assert _maybe_split_single_element_and_chain(cmd) == (cmd, "")

    def test_does_not_split_segment_with_multiple_args(self):
        cmd = ["grep -rn foo && bar"]
        assert _maybe_split_single_element_and_chain(cmd) == (cmd, "")

    def test_does_not_split_when_other_metachar_present(self):
        cmd = ["echo $HOME && ls"]
        assert _maybe_split_single_element_and_chain(cmd) == (cmd, "")

    def test_multi_element_cmd_is_passthrough(self):
        cmd = ["git", "status"]
        assert _maybe_split_single_element_and_chain(cmd) == (cmd, "")

    def test_no_and_chain_is_passthrough(self):
        cmd = ["git status --porcelain"]
        assert _maybe_split_single_element_and_chain(cmd) == (cmd, "")

    def test_run_shell_splits_argv_boundary_mistake(self, tmp_path, fake_subprocess):
        calls = fake_subprocess(stdout="")
        result = _run_shell(_ctx(tmp_path), ["git && status && --porcelain"])
        assert "SHELL_CMD_AUTO_SPLIT" in result
        assert calls[0]["cmd"] == ["git", "status", "--porcelain"]


# --------------------------------------------------------------------------
# _maybe_wrap_single_element_pipeline
# --------------------------------------------------------------------------


class TestSingleElementPipeline:
    def test_wraps_bracketed_pipe(self):
        out, note = _maybe_wrap_single_element_pipeline(["grep -rn foo . | head -20"])
        assert out == ["sh", "-c", "grep -rn foo . | head -20"]
        assert "SHELL_CMD_AUTO_WRAP" in note

    def test_does_not_wrap_glued_pipe(self):
        cmd = ["grep 'a|b' file.txt"]
        assert _maybe_wrap_single_element_pipeline(cmd) == (cmd, "")

    def test_does_not_wrap_double_pipe(self):
        cmd = ["make || true"]
        assert _maybe_wrap_single_element_pipeline(cmd) == (cmd, "")

    def test_does_not_wrap_no_pipe(self):
        cmd = ["ls -la"]
        assert _maybe_wrap_single_element_pipeline(cmd) == (cmd, "")

    def test_does_not_wrap_leading_shell_interpreter(self):
        cmd = ["sh -c 'a | b'"]
        assert _maybe_wrap_single_element_pipeline(cmd) == (cmd, "")

    def test_multi_element_is_passthrough(self):
        for cmd in (["make", "&&", "test"], ["a", "|", "b"]):
            assert _maybe_wrap_single_element_pipeline(cmd) == (cmd, "")

    def test_run_shell_wraps_pipeline(self, tmp_path, fake_subprocess):
        calls = fake_subprocess(stdout="match\n")
        result = _run_shell(_ctx(tmp_path), ["grep -rn foo . | head -20"])
        assert "SHELL_CMD_AUTO_WRAP" in result
        assert calls[0]["cmd"] == ["sh", "-c", "grep -rn foo . | head -20"]
