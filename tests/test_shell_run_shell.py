"""Behavioral tests for ``ouroboros.tools.shell._run_shell``.

Consolidated in v5.15.x from three previous files that all exercised the
same ``_run_shell`` entrypoint:

- ``test_shell_recovery.py``       — string/json/ast cmd recovery, malformed
                                    bracket refusal, env-ref policy, timeout
- ``test_shell_regex_hint.py``     — grep ``A\\|B`` argv-mode trap detection
                                    and auto-correct
- ``test_shell_no_match_semantics.py`` — grep/rg exit-1 without stderr is
                                         "no matches", not SHELL_EXIT_ERROR

The grep regex-hint matrix is collapsed into one parametrize table; the
recovery + env + timeout suite retains its scenarios (each tests a
distinct branch of the cascade).
"""
from __future__ import annotations

import pathlib
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from ouroboros.tools.shell import _resolve_effective_timeout, _run_shell


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _ctx(tmp_path):
    """Minimal ctx used by all _run_shell tests."""
    import pathlib

    return SimpleNamespace(
        repo_dir=tmp_path,
        drive_root=tmp_path,
        drive_logs=lambda: pathlib.Path(str(tmp_path)),
    )


def test_run_shell_preserves_leading_stdout_whitespace(tmp_path, fake_subprocess):
    fake_subprocess(stdout="  indented\n")
    result = _run_shell(_ctx(tmp_path), ["printf", "x"])
    assert "STDOUT:\n  indented\n" in result


def test_run_shell_accepts_task_drive_label_as_cwd(tmp_path, fake_subprocess):
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = _ctx(repo)
    ctx.drive_root = tmp_path / "drive"
    ctx.task_id = "task1"
    calls = fake_subprocess(stdout="ok")
    result = _run_shell(ctx, ["pwd"], cwd="task_drive")
    assert "SHELL_CWD_BLOCKED" not in result
    assert pathlib.Path(calls[0]["kwargs"]["cwd"]).parts[-2:] == ("task_drives", "task1")


def test_run_shell_accepts_user_files_label_as_safe_deliverables_cwd(tmp_path, fake_subprocess, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = _ctx(repo)
    user_home = tmp_path / "user-home"
    deliverables = user_home / "Deliverables"
    ctx.drive_root = tmp_path / "drive"
    monkeypatch.setenv("OUROBOROS_USER_FILES_ROOT", str(user_home))
    monkeypatch.setenv("OUROBOROS_DELIVERABLES_ROOT", str(deliverables))
    calls = fake_subprocess(stdout="ok")
    result = _run_shell(ctx, ["pwd"], cwd="user_files")
    assert "SHELL_CWD_BLOCKED" not in result
    assert calls[0]["kwargs"]["cwd"] == str(deliverables.resolve())


@pytest.fixture
def fake_subprocess(monkeypatch):
    """Patch _tracked_subprocess_run with a closure that returns a queued result.

    Usage:
        def test_X(fake_subprocess):
            calls = fake_subprocess(stdout="ok", returncode=0)
            _run_shell(...)
            assert calls[0]["cmd"] == [...]
    """
    monkeypatch.setattr("ouroboros.tools.shell.load_settings", lambda: {})

    def _install(*, returncode: int = 0, stdout: str = "", stderr: str = ""):
        calls: list[dict] = []

        def fake_run(cmd, **kwargs):
            calls.append({"cmd": cmd, "kwargs": kwargs})
            return CompletedProcess(cmd, returncode, stdout, stderr)

        monkeypatch.setattr("ouroboros.tools.shell._tracked_subprocess_run", fake_run)
        return calls

    return _install


# ---------------------------------------------------------------------------
# T3 (v6.35.0): per-call timeout_sec override for run_command/run_script
# ---------------------------------------------------------------------------


class TestPerCallTimeout:
    """An explicit timeout_sec (or its `timeout` alias) overrides the default,
    still clamped to the remaining task deadline."""

    def _ctx_with_deadline(self, tmp_path, secs):
        import pathlib
        from datetime import datetime, timedelta, timezone

        deadline = (datetime.now(timezone.utc) + timedelta(seconds=secs)).isoformat()
        return SimpleNamespace(
            repo_dir=tmp_path,
            drive_logs=lambda: pathlib.Path(str(tmp_path)),
            task_metadata={"deadline_at": deadline},
        )

    def test_resolve_override_no_deadline_passthrough(self):
        assert _resolve_effective_timeout(360, None, override_sec=5) == 5

    def test_resolve_override_clamped_by_deadline(self, tmp_path):
        # remaining ~100s -> cap = max(60, min(1800, 50)) = 60 -> min(99999, 60)
        ctx = self._ctx_with_deadline(tmp_path, 100)
        assert _resolve_effective_timeout(360, ctx, override_sec=99999) == 60

    def test_resolve_override_zero_falls_through_to_default(self, monkeypatch):
        # override 0 -> falls through to the config SSOT default (OUROBOROS_TOOL_TIMEOUT_SEC=600),
        # NOT the in-code 360 (the prior `!= default_setting` skip wrongly returned 360).
        monkeypatch.setenv("OUROBOROS_TOOL_TIMEOUT_SEC", "600")
        assert _resolve_effective_timeout(360, None, override_sec=0) == 600

    def test_resolve_override_none_is_default(self, monkeypatch):
        monkeypatch.setenv("OUROBOROS_TOOL_TIMEOUT_SEC", "600")
        assert _resolve_effective_timeout(360, None, override_sec=None) == 600  # config SSOT, not in-code 360

    def test_run_shell_threads_timeout_sec(self, tmp_path, fake_subprocess):
        calls = fake_subprocess(stdout="ok")
        _run_shell(_ctx(tmp_path), ["echo", "hi"], timeout_sec=5)
        assert calls[0]["kwargs"]["timeout"] == 5

    def test_run_shell_accepts_timeout_alias(self, tmp_path, fake_subprocess):
        calls = fake_subprocess(stdout="ok")
        _run_shell(_ctx(tmp_path), ["echo", "hi"], timeout=7)
        assert calls[0]["kwargs"]["timeout"] == 7

    def test_run_shell_default_timeout_when_omitted(self, tmp_path, fake_subprocess, monkeypatch):
        monkeypatch.setenv("OUROBOROS_TOOL_TIMEOUT_SEC", "600")
        calls = fake_subprocess(stdout="ok")
        _run_shell(_ctx(tmp_path), ["echo", "hi"])
        assert calls[0]["kwargs"]["timeout"] == 600  # config SSOT default (was a buggy effective 360)

    def test_schema_exposes_timeout_sec_and_timeout_alias(self):
        from ouroboros.tools.shell import get_tools

        entries = {e.name: e for e in get_tools()}
        for name in ("run_command", "run_script"):
            props = entries[name].schema["parameters"]["properties"]
            assert "timeout_sec" in props, f"{name} missing timeout_sec"
            assert "timeout" in props, f"{name} missing timeout alias"


# ---------------------------------------------------------------------------
# cmd recovery cascade (string → json → ast → shlex; bracket-prefix refusal)
# ---------------------------------------------------------------------------


class TestShellArgContract:
    """run_shell recovers string cmd via cascade, only errors on unrecoverable input."""

    def test_string_cmd_recovered_via_shlex(self, tmp_path, fake_subprocess):
        fake_subprocess(stdout="hello")
        result = _run_shell(_ctx(tmp_path), "echo hello")
        assert "SHELL_ARG_ERROR" not in result
        assert f"exit_code=0 (cwd={tmp_path.resolve()})" in result

    def test_json_array_string_recovered(self, tmp_path, fake_subprocess):
        fake_subprocess(stdout="ok")
        result = _run_shell(_ctx(tmp_path), '["echo", "hello"]')
        assert "SHELL_ARG_ERROR" not in result
        assert "exit_code=0" in result

    def test_python_literal_string_recovered(self, tmp_path, fake_subprocess):
        fake_subprocess(stdout="ok")
        result = _run_shell(_ctx(tmp_path), "['echo', 'hello']")
        assert "SHELL_ARG_ERROR" not in result
        assert "exit_code=0" in result

    def test_unrecoverable_string_returns_error(self, tmp_path):
        result = _run_shell(_ctx(tmp_path), "")
        assert "SHELL_ARG_ERROR" in result

    def test_string_cmd_still_discloses_env_refs(self, tmp_path, fake_subprocess):
        # #447 A5: literal shell syntax in direct argv is DATA — the command runs
        # and the literal pass-through is disclosed, not refused.
        fake_subprocess(stdout="ok")
        result = _run_shell(_ctx(tmp_path), 'curl -H "x-api-key: $SECRET"')
        assert "SHELL_LITERAL_ARGV_NOTE" in result
        assert "exit_code=0" in result

    # JSON-shape refusal — 2026-05-03 production bug. See module docstring
    # for the failure mode this guard prevents.

    def test_malformed_json_array_refused_not_shlex_split(self, tmp_path):
        result = _run_shell(_ctx(tmp_path), '["git", "log",')
        assert "SHELL_ARG_ERROR" in result
        assert "stringified array" in result.lower()
        assert "Errno" not in result

    def test_malformed_dict_literal_refused(self, tmp_path):
        result = _run_shell(_ctx(tmp_path), '{key: value, broken')
        assert "SHELL_ARG_ERROR" in result
        assert "Errno" not in result

    def test_valid_json_array_still_works_after_refusal_branch(self, tmp_path, fake_subprocess):
        fake_subprocess(stdout="ok")
        result = _run_shell(_ctx(tmp_path), '["echo", "ok"]')
        assert "SHELL_ARG_ERROR" not in result
        assert "exit_code=0" in result

    def test_legitimate_shell_string_still_recovers_via_shlex(self, tmp_path, fake_subprocess):
        fake_subprocess(stdout="hello")
        result = _run_shell(_ctx(tmp_path), "echo hello")
        assert "SHELL_ARG_ERROR" not in result
        assert "exit_code=0" in result

    def test_posix_bracket_test_command_still_recovers_via_shlex(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ouroboros.tools.shell.load_settings", lambda: {})

        def fake_run(cmd, **kwargs):
            assert cmd == ["[", "-f", "file.txt", "]"]
            return CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("ouroboros.tools.shell._tracked_subprocess_run", fake_run)
        result = _run_shell(_ctx(tmp_path), "[ -f file.txt ]")
        assert "SHELL_ARG_ERROR" not in result
        assert "exit_code=0" in result

    def test_refusal_message_points_at_correct_usage(self, tmp_path):
        result = _run_shell(_ctx(tmp_path), '["git", "log",')
        assert 'run_command(cmd=["git"' in result

    def test_list_cmd_is_accepted(self, tmp_path, fake_subprocess):
        fake_subprocess(stdout="ok")
        result = _run_shell(_ctx(tmp_path), ["echo", "ok"])
        assert "SHELL_ARG_ERROR" not in result
        assert "exit_code=0" in result


# ---------------------------------------------------------------------------
# Env-ref + timeout + nonzero-exit behavior
# ---------------------------------------------------------------------------


def test_run_shell_discloses_literal_env_refs_in_argv(tmp_path, fake_subprocess):
    # #447 A5: the argv is executed as-is (no shell interprets "$..."), and the
    # unexpanded pass-through is disclosed in the result.
    calls = fake_subprocess(stdout="ok")
    result = _run_shell(_ctx(tmp_path), ["curl", "-H", "x-api-key: $ANTHROPIC_API_KEY"])
    assert "SHELL_LITERAL_ARGV_NOTE" in result
    assert "$ANTHROPIC_API_KEY" in result
    assert "exit_code=0" in result
    assert calls, "the command must actually run"


def test_run_shell_allows_shell_expansion_via_sh_c(tmp_path, fake_subprocess):
    fake_subprocess(stdout="ok")
    result = _run_shell(_ctx(tmp_path), ["sh", "-c", "printf '%s' \"$ANTHROPIC_API_KEY\""])
    assert "SHELL_LITERAL_ARGV_NOTE" not in result
    assert "exit_code=0" in result


def test_run_shell_nonzero_exit_is_reported_as_failure(tmp_path, fake_subprocess):
    fake_subprocess(returncode=3, stderr="permission denied")
    result = _run_shell(_ctx(tmp_path), ["npm", "install", "-g", "@anthropic-ai/claude-code"])

    assert result.startswith("⚠️ SHELL_EXIT_ERROR:")
    assert f"exit_code=3 (cwd={tmp_path.resolve()})" in result
    assert "permission denied" in result


def test_run_shell_timeout_uses_settings_timeout(tmp_path, monkeypatch):
    def fake_timeout(cmd, **kwargs):
        raise __import__("subprocess").TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

    monkeypatch.setattr("ouroboros.tools.shell.load_settings", lambda: {"OUROBOROS_TOOL_TIMEOUT_SEC": 42})
    monkeypatch.delenv("OUROBOROS_TOOL_TIMEOUT_SEC", raising=False)
    monkeypatch.setattr("ouroboros.tools.shell._tracked_subprocess_run", fake_timeout)
    result = _run_shell(_ctx(tmp_path), ["sleep", "999"])

    assert "TOOL_TIMEOUT (run_command)" in result
    assert "42s" in result
    assert f"cwd={tmp_path.resolve()}" in result


def test_run_shell_deadline_derived_timeout_is_used_when_no_explicit_setting(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr("ouroboros.tools.shell.load_settings", lambda: {"OUROBOROS_TOOL_TIMEOUT_SEC": 0})
    monkeypatch.delenv("OUROBOROS_TOOL_TIMEOUT_SEC", raising=False)
    monkeypatch.setattr("ouroboros.deadline_utils.utc_now", lambda: datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc))
    ctx = SimpleNamespace(task_metadata={"deadline_at": "2026-06-10T00:20:00Z"})

    assert _resolve_effective_timeout(360, ctx) == 600


def test_run_shell_deadline_caps_real_default_timeout(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr("ouroboros.tools.shell.load_settings", lambda: {"OUROBOROS_TOOL_TIMEOUT_SEC": 600})
    monkeypatch.delenv("OUROBOROS_TOOL_TIMEOUT_SEC", raising=False)
    monkeypatch.setattr("ouroboros.deadline_utils.utc_now", lambda: datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc))
    ctx = SimpleNamespace(task_metadata={"deadline_at": "2026-06-10T00:10:00Z"})

    assert _resolve_effective_timeout(600, ctx) == 300


def test_run_shell_explicit_timeout_wins_over_deadline(monkeypatch):
    monkeypatch.setattr("ouroboros.tools.shell.load_settings", lambda: {"OUROBOROS_TOOL_TIMEOUT_SEC": 42})
    monkeypatch.delenv("OUROBOROS_TOOL_TIMEOUT_SEC", raising=False)
    ctx = SimpleNamespace(task_metadata={"deadline_at": "2026-06-10T00:20:00Z"})

    assert _resolve_effective_timeout(360, ctx) == 42


# ---------------------------------------------------------------------------
# grep/rg exit-1 without stderr semantics (no matches != shell error)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cmd", [
    ["grep", "-n", "missing", "file.py"],
    ["rg", "missing", "."],
])
def test_grep_or_rg_exit_one_without_stderr_is_no_match(cmd, tmp_path, fake_subprocess):
    fake_subprocess(returncode=1, stdout="", stderr="")
    result = _run_shell(_ctx(tmp_path), cmd)

    assert "SHELL_EXIT_ERROR" not in result
    assert "exit_code=1" in result
    assert f"cwd={tmp_path.resolve()}" in result
    assert "no matches" in result


def test_grep_exit_one_with_stderr_still_surfaces_shell_error(tmp_path, fake_subprocess):
    fake_subprocess(returncode=1, stderr="grep: file.py: No such file or directory\n")
    result = _run_shell(_ctx(tmp_path), ["grep", "missing", "file.py"])

    assert "SHELL_EXIT_ERROR" in result
    assert "No such file or directory" in result


# ---------------------------------------------------------------------------
# grep \| regex-escape hint / auto-correct (2026-05-04 hint class)
# ---------------------------------------------------------------------------


def test_user_file_output_audit_extracts_windows_absolute_paths():
    from ouroboros.tools import shell

    body = r"from pathlib import Path; Path('C:\\Users\\anton\\Desktop\\out.html').write_text('x')"
    redirect = r"echo x > C:\\Users\\anton\\Desktop\\out.html"

    assert shell._EMBEDDED_OUTPUT_PATH_RE.findall(body) == [r"C:\\Users\\anton\\Desktop\\out.html"]
    assert shell._USER_FILE_REDIRECT_RE.search(redirect).group("bare") == r"C:\\Users\\anton\\Desktop\\out.html"


class TestGrepRegexHint:
    """``grep "A\\|B" file`` in argv mode is BSD's literal two-char trap.

    The hint catches the class and rewrites to ``grep -E "A|B"`` so smaller
    models that learned bash idioms don't get stuck. Explicit -E/-G/-F flags,
    egrep/fgrep, and valid BRE patterns (``\\(...\\)``, ``\\+``) must pass
    through without the hint.
    """

    def test_grep_with_backslash_pipe_auto_corrects(self, tmp_path, fake_subprocess):
        calls = fake_subprocess(stdout="match\n")
        result = _run_shell(_ctx(tmp_path), ["grep", "-n", "A\\|B", "/tmp/x"])
        assert "SHELL_REGEX_AUTO_CORRECTED" in result
        assert "SHELL_REGEX_HINT" not in result
        assert calls[0]["cmd"] == ["grep", "-E", "-n", "A|B", "/tmp/x"]
        assert "match" in result

    def test_grep_with_path_basename_auto_corrected(self, tmp_path, fake_subprocess):
        calls = fake_subprocess()
        result = _run_shell(_ctx(tmp_path), ["/usr/bin/grep", "A\\|B", "/tmp/x"])
        assert "SHELL_REGEX_AUTO_CORRECTED" in result
        assert calls[0]["cmd"] == ["/usr/bin/grep", "-E", "A|B", "/tmp/x"]

    @pytest.mark.parametrize("argv,reason", [
        (["grep", "\\(foo\\)", "/tmp/x"], "POSIX BRE grouping, not the \\| trap"),
        (["grep", "ab\\+c", "/tmp/x"], "BRE extension, not the \\| trap"),
        (["grep", "-E", "A\\|B", "/tmp/x"], "explicit -E means user knows what they want"),
        (["grep", "-rnE", "A\\|B", "/tmp/x"], "clustered -rnE still explicit extended regex"),
        (["grep", "-G", "A\\|B", "/tmp/x"], "explicit -G is intentional GNU BRE"),
        (["grep", "-F", "A\\|B", "/tmp/x"], "-F means literal strings, two chars"),
        (["grep", "-n", "pattern", "/tmp/x"], "plain pattern without escapes"),
        (["egrep", "A\\|B", "/tmp/x"], "egrep already chose regex flavor"),
        (["fgrep", "A\\|B", "/tmp/x"], "fgrep already chose string flavor"),
        (["echo", "A\\|B"], "non-grep commands untouched"),
    ])
    def test_grep_regex_hint_skips(self, argv, reason, tmp_path, fake_subprocess):
        fake_subprocess()
        result = _run_shell(_ctx(tmp_path), argv)
        assert "SHELL_REGEX_HINT" not in result, reason


# ---------------------------------------------------------------------------
# Stuffed shell pipeline in a single argv element (v6.93.2 fix class)
#
# Closes the recurring "agent passes `["curl && -s && URL"]`" failure shape:
# a shell pipeline STUFFED into one argv element reaches subprocess as the
# executable name and dies silently with `[Errno 2]`. Pre-existing
# `_SHELL_OPERATORS.intersection` only catches `"&&"` as its OWN element;
# `_GLUED_REDIRECT_RE` only matches redirect-shaped prefixes. The new
# `_EMBEDDED_SHELL_OP_RE` regex fills the gap, gated by `_SHELL_INTERPRETERS`
# so `["sh", "-c", "echo a && echo b"]` passes through.
# ---------------------------------------------------------------------------


class TestEmbeddedShellPipeline:
    """_EMBEDDED_SHELL_OP_RE catches stuffed pipelines without breaking legit use."""

    # ------------------------------------------------------------------
    # Regression guard: existing _SHELL_OPERATORS.intersection still works.
    # (R4 from plan review: this check had ZERO test coverage before.)
    # ------------------------------------------------------------------
    def test_run_shell_blocks_standalone_shell_operator(self, tmp_path):
        """`["curl", "&&", "-s", "&&", "URL"]` — pre-existing _SHELL_OPERATORS check."""
        result = _run_shell(_ctx(tmp_path), ["curl", "&&", "-s", "&&", "URL"])
        assert "SHELL_CMD_ERROR" in result
        assert "&&" in result
        assert '"sh"' in result

    # ------------------------------------------------------------------
    # The NEW check: production failure shape (v6.93.2 fix).
    # ------------------------------------------------------------------
    def test_run_shell_autocorrects_stuffed_pipeline_in_element_zero(self, tmp_path, fake_subprocess):
        """The original shape that hit task 0b7e545d / 352130d1 / 7847c2aa / 91d68bd0.

        As of v6.110.x this len(cmd)==1 &&-of-single-tokens case is now
        AUTOCORRECTED (split into real argv) instead of rejected — the
        same argv-boundary mistake the SHELL_CMD_AUTO_SPLIT hint fixes.
        The case is still caught by *some* cascade step (the autocorrect
        cascade) and reaches subprocess with a sane argv; this test stays
        as a regression guard that the autocorrect cascade handles it
        rather than letting it through to subprocess as a single ENOENT
        literal. The explicit SHELL_CMD_ERROR assertion is retired.
        """
        calls = fake_subprocess(stdout="")
        result = _run_shell(
            _ctx(tmp_path),
            ["curl && -s && https://api.example.com/x"],
        )
        assert "SHELL_CMD_AUTO_SPLIT" in result
        assert "SHELL_CMD_ERROR" not in result
        assert calls[0]["cmd"] == ["curl", "-s", "https://api.example.com/x"]

    def test_run_shell_blocks_stuffed_pipeline_in_middle_element(self, tmp_path):
        """Stuffed pipeline inside element index 2 (two operators) names the index."""
        result = _run_shell(
            _ctx(tmp_path),
            ["git", "log", "--oneline -n 5 && grep TODO && head -3", "--"],
        )
        assert "SHELL_CMD_ERROR" in result
        assert "cmd[2]" in result

    def test_run_shell_blocks_double_pipe_stuffed(self, tmp_path):
        """`||` operator also caught."""
        result = _run_shell(
            _ctx(tmp_path),
            ["test -f foo || echo missing && exit 1"],
        )
        assert "SHELL_CMD_ERROR" in result
        assert "||" in result

    # ------------------------------------------------------------------
    # _SHELL_INTERPRETERS exemption: sh -c / bash -c / zsh -c pass through.
    # ------------------------------------------------------------------
    def test_run_shell_allows_sh_c_with_operators(self, tmp_path, fake_subprocess):
        """`["sh", "-c", "cmd1 && cmd2 && cmd3"]` runs to completion."""
        fake_subprocess(stdout="ok")
        result = _run_shell(_ctx(tmp_path), ["sh", "-c", "cmd1 && cmd2 && cmd3"])
        assert "SHELL_CMD_ERROR" not in result
        assert "SHELL_ENV_ERROR" not in result
        assert "exit_code=0" in result

    def test_run_shell_allows_bash_c_with_operators(self, tmp_path, fake_subprocess):
        """`["bash", "-c", "a || b"]` runs to completion."""
        fake_subprocess(stdout="ok")
        result = _run_shell(_ctx(tmp_path), ["bash", "-c", "a || b"])
        assert "SHELL_CMD_ERROR" not in result
        assert "exit_code=0" in result

    def test_run_shell_allows_zsh_c_with_operators(self, tmp_path, fake_subprocess):
        """All _SHELL_INTERPRETERS members carry the exemption."""
        fake_subprocess(stdout="ok")
        result = _run_shell(_ctx(tmp_path), ["zsh", "-c", "echo a && echo b"])
        assert "SHELL_CMD_ERROR" not in result
        assert "exit_code=0" in result

    # ------------------------------------------------------------------
    # False-positive guards: legitimate text must NOT be flagged.
    # ------------------------------------------------------------------
    def test_run_shell_allows_single_operator_in_legit_text(self, tmp_path, fake_subprocess):
        """`["echo", "a && b"]` is literal text — only one `&&`, not flagged."""
        fake_subprocess(stdout="a && b\n")
        result = _run_shell(_ctx(tmp_path), ["echo", "a && b"])
        assert "SHELL_CMD_ERROR" not in result
        assert "exit_code=0" in result

    def test_run_shell_allows_grep_regex_alternation(self, tmp_path, fake_subprocess):
        """`["grep", "a|b"]` is legitimate regex alternation — no whitespace context."""
        fake_subprocess(stdout="")
        result = _run_shell(_ctx(tmp_path), ["grep", "a|b"])
        assert "SHELL_CMD_ERROR" not in result

    def test_run_shell_allows_glued_double_ampersand(self, tmp_path, fake_subprocess):
        """`a&&b` (no whitespace) is NOT a shell operator — not flagged."""
        fake_subprocess(stdout="")
        result = _run_shell(_ctx(tmp_path), ["echo", "a&&b"])
        assert "SHELL_CMD_ERROR" not in result

    # ------------------------------------------------------------------
    # Pre-existing env-ref exemption still works (R5 from plan review:
    # this is a regression guard for behavior that pre-dates this PATCH).
    # ------------------------------------------------------------------
    def test_run_shell_env_ref_exemption_still_works(self, tmp_path, fake_subprocess):
        """`bash -c "printf '%s' \"$SECRET\""` — env-ref exemption unchanged."""
        fake_subprocess(stdout="ok")
        result = _run_shell(
            _ctx(tmp_path),
            ["bash", "-c", "printf '%s' \"$SECRET\" && true"],
        )
        assert "SHELL_ENV_ERROR" not in result
        assert "SHELL_CMD_ERROR" not in result
        assert "exit_code=0" in result


class TestSingleElementShellMetachar:
    """v6.101.0 fix: a lone &&/||/|/; in a ONE-element cmd — the coverage gap
    that let the >=2 stuffed-pipeline check (TestEmbeddedShellPipeline above)
    still miss the single-operator shape recurring across 11 backlog entries
    (ibl-5aa29f06571d through ibl-4ecff817c661, 2026-08-11..08-15)."""

    # ------------------------------------------------------------------
    # The gap this closes: exactly the production failure shapes reported
    # after the >=2 check (9224e188) had already landed.
    # ------------------------------------------------------------------
    def test_run_shell_blocks_single_ampersand_pair(self, tmp_path):
        """['ls -la && cat file.txt'] — one && in the sole element."""
        result = _run_shell(_ctx(tmp_path), ["ls -la && cat file.txt"])
        assert "SHELL_CMD_ERROR" in result
        assert '"sh"' in result

    def test_run_shell_blocks_single_double_pipe(self, tmp_path):
        """['find . -name \"*.py\" || echo none'] — one || in the sole element."""
        result = _run_shell(_ctx(tmp_path), ['find . -name "*.py" || echo none'])
        assert "SHELL_CMD_ERROR" in result

    def test_run_shell_auto_wraps_bare_pipe_in_sole_element(self, tmp_path, fake_subprocess):
        """``['echo hello | grep h']`` — a whitespace-bracketed bare pipe
        in the SOLE element. As of v6.110.x the pipe-autocorrect helper
        upstream of ``_SHELL_OPERATORS.intersection`` intercepts this
        shape (a pipe genuinely needs a shell, so unlike the &&
        boundary-mistake which can be safely split into real argv,
        this one wraps the raw string as ``["sh", "-c", <raw>]``).
        Closes ``ibl-db9d3608e096``.

        The other metachars (``&&``, ``||``, ``;``) keep their old
        SHELL_CMD_ERROR rejection — only bare pipes get autocorrected.
        """
        calls = fake_subprocess(stdout="hello\n")
        result = _run_shell(_ctx(tmp_path), ["echo hello | grep h"])
        assert "SHELL_CMD_AUTO_WRAP" in result
        assert "SHELL_CMD_ERROR" not in result
        assert calls[0]["cmd"] == ["sh", "-c", "echo hello | grep h"]

    def test_run_shell_blocks_semicolon_in_sole_element(self, tmp_path):
        """['echo a; echo b'] — semicolon-chained commands as one element."""
        result = _run_shell(_ctx(tmp_path), ["echo a; echo b"])
        assert "SHELL_CMD_ERROR" in result

    # ------------------------------------------------------------------
    # _SHELL_INTERPRETERS exemption still applies to a bare interpreter name.
    # ------------------------------------------------------------------
    def test_run_shell_allows_bare_interpreter_name(self, tmp_path, fake_subprocess):
        """['bash'] alone (interactive, no -c) is not flagged."""
        fake_subprocess(stdout="")
        result = _run_shell(_ctx(tmp_path), ["bash"])
        assert "SHELL_CMD_ERROR" not in result

    # ------------------------------------------------------------------
    # False-positive guard: the exact reason bare "|" and threshold 1 stay
    # OUT of the multi-arg check must still hold for len(cmd) > 1.
    # ------------------------------------------------------------------
    def test_run_shell_allows_operator_text_in_multi_arg_value(self, tmp_path, fake_subprocess):
        """['git', 'commit', '-m', 'step1 && step2 done'] — operator-looking
        text is legitimate content of a VALUE argument sitting alongside
        other argv elements; must not be flagged (len(cmd) > 1, out of this
        check's scope by design)."""
        fake_subprocess(stdout="")
        result = _run_shell(
            _ctx(tmp_path), ["git", "commit", "-m", "step1 && step2 done"]
        )
        assert "SHELL_CMD_ERROR" not in result

    def test_run_shell_allows_grep_pipe_alternation_as_separate_arg(self, tmp_path, fake_subprocess):
        """['grep', '-E', 'a|b', 'file.txt'] — regex alternation as its own
        argv element, len(cmd) > 1, must not be flagged."""
        fake_subprocess(stdout="")
        result = _run_shell(_ctx(tmp_path), ["grep", "-E", "a|b", "file.txt"])
        assert "SHELL_CMD_ERROR" not in result

    def test_run_shell_allows_ordinary_single_element_no_metachar(self, tmp_path, fake_subprocess):
        """['echo hello world'] — one element, no shell metacharacter, passes."""
        fake_subprocess(stdout="hello world")
        result = _run_shell(_ctx(tmp_path), ["echo hello world"])
        assert "SHELL_CMD_ERROR" not in result
        assert "exit_code=0" in result


# ---------------------------------------------------------------------------
# Protected-runtime-path auto-restore: a shell command has no file-write guard
# the way write_file/edit_text do, so a dirtied protected path (BIBLE.md, etc.)
# is reverted after the command instead of silently landing.
# ---------------------------------------------------------------------------


def _init_protected_repo(tmp_path):
    """A real git repo (not faked) — the restore logic runs real `git diff`/
    `git checkout` against it, independent of fake_subprocess (which only
    stubs the command execution itself, not this tool's own git calls)."""
    import subprocess as _sp

    repo = tmp_path / "repo"
    repo.mkdir()
    _sp.run(["git", "init", "-q"], cwd=repo, check=True)
    _sp.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    _sp.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    _sp.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "BIBLE.md").write_text("original constitution\n")
    (repo / "README.md").write_text("original readme\n")
    _sp.run(["git", "add", "-A"], cwd=repo, check=True)
    _sp.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    return repo


def test_run_shell_reverts_a_protected_path_the_command_dirtied(tmp_path, fake_subprocess):
    repo = _init_protected_repo(tmp_path)
    # Simulate the effect of the (faked) shell command tampering with a
    # protected runtime file directly on disk.
    (repo / "BIBLE.md").write_text("tampered by shell command\n")
    fake_subprocess(stdout="ok")

    result = _run_shell(_ctx(repo), ["echo", "hi"])

    assert "PROTECTED_PATH_AUTO_RESTORED" in result
    assert "BIBLE.md" in result
    assert (repo / "BIBLE.md").read_text() == "original constitution\n"


def test_run_shell_leaves_unprotected_paths_alone(tmp_path, fake_subprocess):
    repo = _init_protected_repo(tmp_path)
    (repo / "README.md").write_text("edited by shell command\n")
    fake_subprocess(stdout="ok")

    result = _run_shell(_ctx(repo), ["echo", "hi"])

    assert "PROTECTED_PATH_AUTO_RESTORED" not in result
    assert (repo / "README.md").read_text() == "edited by shell command\n"


def test_run_shell_does_not_restore_outside_the_system_repo(tmp_path, fake_subprocess):
    """A protected-looking filename in an UNRELATED git repo (e.g. a subagent
    worktree or a skill's own repo) must never be auto-reverted — only the
    system repo (ctx.system_repo_dir / ctx.repo_dir) is in scope."""
    other_repo = _init_protected_repo(tmp_path)  # git root != ctx.system_repo_dir below
    (other_repo / "BIBLE.md").write_text("tampered\n")

    ctx = _ctx(other_repo)
    ctx.system_repo_dir = tmp_path / "elsewhere"  # deliberately NOT other_repo
    fake_subprocess(stdout="ok")

    result = _run_shell(ctx, ["echo", "hi"])

    assert "PROTECTED_PATH_AUTO_RESTORED" not in result
    assert (other_repo / "BIBLE.md").read_text() == "tampered\n"


# ---------------------------------------------------------------------------
# ibl-e357d33b9c54 regression surface — heredoc-via-run_command-bash-c class
# ---------------------------------------------------------------------------
#
# Background: a heredoc whose body lives inside a `bash -c "..."` script
# reaches the shell as a single argv element. The bash interpreter parses
# the script text natively, so a body of `<<EOF\nbody\nEOF` is valid
# shell syntax inside the script (the heredoc body is part of the script
# text, NOT external stdin). Before the fix, the argv-validator's glued-
# redirect check (step 4 of the cascade) matched `<<EOF` as if it were a
# free-standing redirect arg and surfaced a misleading "Use ['sh','-c',...]
# for redirection" error — even when the caller was ALREADY using
# `bash -c "..."`. The class-level fix gates step 4 on `_SHELL_INTERPRETERS`
# so shell-script argv reaches the interpreter untouched; non-shell argv
# still get the redirect-shape guard (the existing
# test_run_shell_blocks_glued_redirect regression in test_shell_redirect_guard
# confirms that half).


@pytest.mark.parametrize(
    "cmd",
    [
        # Heredoc at the very start of the script body — the previously-broken case.
        ["bash", "-c", "<<EOF\nbody\nEOF"],
        ["sh", "-c", "<<'MARK'\nbody\nMARK"],
        # Heredoc mid-script after a command — has always worked, included
        # to lock in that we did not regress.
        ["bash", "-c", "cat <<EOF\nbody\nEOF"],
        ["bash", "-c", "for i in 1 2 3; do cat <<EOF\nline $i\nEOF; done"],
        # Nested heredoc inside a conditional.
        ["bash", "-c", "if true; then\n    cat <<'EOF'\nnested-conditional-heredoc\nEOF\nfi"],
        # Heredoc with variable expansion in the body — proves the fix is not
        # tied to literal-only bodies.
        ["bash", "-c", "foo=bar; cat <<EOF\nval is $foo\nEOF"],
        # zsh / fish / pwsh surface — all in _SHELL_INTERPRETERS.
        ["zsh", "-c", "<<'EOF'\nzsh-heredoc\nEOF"],
        ["pwsh", "-c", "$x = 1; Write-Output $x"],
    ],
)
def test_run_shell_accepts_heredoc_inside_shell_script(cmd):
    """Class-level regression for ibl-e357d33b9c54: heredoc-shaped substrings
    are NOT a redirect arg when they are inside a `bash -c "..."` (or any
    _SHELL_INTERPRETERS-equivalent) script body. The validator must return
    an empty string, not the misleading redirect warning — bash itself
    parses the script text natively and reads the heredoc body from it
    (no external stdin required for `bash -c`)."""
    from ouroboros.tools.shell import _validate_shell_argv

    err = _validate_shell_argv(list(cmd))
    assert err == "", (
        f"_validate_shell_argv rejected legitimate shell-script heredoc argv "
        f"{cmd!r}: {err!r}"
    )


@pytest.mark.parametrize(
    "arg",
    [
        "2>/dev/null", "2>&1", ">out.log", ">>app.log", "&>all.log", ">&2",
        "1>x", "2>>err", "<<EOF", "<<<word", "0<in.txt", "2<&1",
        # a bare "<" is a STANDALONE shell operator (still refused by
        # _validate_shell_argv step 3 / test_run_shell_blocks_standalone_shell_operator),
        # not a glued redirect — deliberately excluded here.
    ],
)
def test_run_shell_glued_redirect_still_flagged_without_shell(arg):
    """Regression guard: the _SHELL_INTERPRETERS gate must NOT extend to
    non-shell argv. A `find ... 2>/dev/null` style argv (no shell interpreter
    in cmd[0]) must STILL be flagged — since #447 A5 the flag is a DISCLOSURE
    note (`_literal_argv_notes`) rather than a refusal, but the original
    v6.37.0 safety intent (test_shell_redirect_guard.py) survives: the
    redirect-shaped element is surfaced with the ["sh","-c",...] hint. The
    validator itself no longer refuses it."""
    from ouroboros.tools.shell import _literal_argv_notes, _validate_shell_argv

    assert _validate_shell_argv(["somecmd", "arg1", arg]) == ""
    note = _literal_argv_notes(["somecmd", "arg1", arg])
    assert "SHELL_LITERAL_ARGV_NOTE" in note
    assert arg in note


def test_run_shell_bash_c_heredoc_runs_end_to_end(tmp_path, fake_subprocess):
    """End-to-end smoke: the actual subprocess invocation sees `bash -c "..."`
    reach it UNCHANGED after the validator. The fake_subprocess fixture
    records the argv that would have been executed; we assert the heredoc
    script body is passed through verbatim, NOT intercepted by
    `_run_shell` as a redirect error."""
    calls = fake_subprocess(stdout="hello via heredoc\n")
    script = "<<EOF\nhello via heredoc\nEOF\n"
    result = _run_shell(_ctx(tmp_path), ["bash", "-c", script])
    assert "SHELL_CMD_ERROR" not in result
    assert "Shell redirection" not in result
    assert calls, "fake_subprocess recorded no calls"
    argv_seen = calls[0]["cmd"]
    assert isinstance(argv_seen, (list, tuple))
    assert any("hello via heredoc" in str(item) for item in argv_seen), (
        f"Heredoc script body did not reach subprocess; argv was {argv_seen!r}"
    )


class TestAndChainSplit:
    """``["git && status && --porcelain"]`` is an argv-boundary mistake, not
    a real ``&&`` chain. The autocorrect splits the sole element into real
    argv tokens so the call reaches ``subprocess`` correctly. Closes the
    class behind ``ibl-21e0a036155d / ibl-51106385d9f1 / ibl-571ce5f8eec6 /
    ibl-78f783d0ac6a``.

    The autocorrect is intentionally narrow: any segment with internal
    whitespace (``cd foo && make``) or any other shell metacharacter
    (``echo $HOME && ls``) falls through to the existing ``SHELL_CMD_ERROR``
    path — these tests pin that boundary.
    """

    def test_run_shell_splits_git_status_porcelain_argv(self, tmp_path, fake_subprocess):
        """The headline shape: 3 single-token segments chained with &&.

        Subprocess receives real argv ``["git","status","--porcelain"]``
        (not a single ENOENT literal), and the disclosure note is in the
        operator-visible result.
        """
        calls = fake_subprocess(stdout="")
        result = _run_shell(_ctx(tmp_path), ["git && status && --porcelain"])
        assert "SHELL_CMD_AUTO_SPLIT" in result
        assert "SHELL_CMD_ERROR" not in result
        assert calls[0]["cmd"] == ["git", "status", "--porcelain"]

    def test_run_shell_splits_python_m_pytest_argv(self, tmp_path, fake_subprocess):
        """Same shape, different verbs. Uses ``-m`` rather than ``-c``
        because ``print(1)`` would trip the parentheses metachar guard —
        the autocorrect MUST refuse shapes with other metachars and the
        test case itself must avoid them, otherwise we're testing the
        wrong invariant.
        """
        calls = fake_subprocess(stdout="")
        result = _run_shell(_ctx(tmp_path), ["python3 && -m && pytest"])
        assert "SHELL_CMD_AUTO_SPLIT" in result
        assert "SHELL_CMD_ERROR" not in result
        assert calls[0]["cmd"] == ["python3", "-m", "pytest"]

    def test_does_not_split_segment_with_internal_space(self):
        """``cd foo && make`` has segment ``cd foo`` (internal whitespace);
        the autocorrect MUST return ``(cmd, "")`` and let the existing
        SHELL_CMD_ERROR path handle it. Otherwise we'd silently split a
        legitimate single-token-with-arg call.
        """
        from ouroboros.tools.shell_and_chain import _maybe_split_single_element_and_chain
        cmd = ["cd foo && make"]
        out_cmd, note = _maybe_split_single_element_and_chain(cmd)
        assert out_cmd == cmd
        assert note == ""

    def test_does_not_split_segment_with_multiple_args(self):
        """``grep -rn foo && bar`` has segment ``grep -rn foo`` (multiple
        whitespace-separated tokens); same fall-through guarantee."""
        from ouroboros.tools.shell_and_chain import _maybe_split_single_element_and_chain
        cmd = ["grep -rn foo && bar"]
        out_cmd, note = _maybe_split_single_element_and_chain(cmd)
        assert out_cmd == cmd
        assert note == ""

    def test_does_not_split_when_other_metachar_present(self):
        """``echo $HOME && ls`` — ``$`` is a shell metachar outside the
        ``&&`` rewrite scope. Fall through unchanged so SHELL_CMD_ERROR
        can fire."""
        from ouroboros.tools.shell_and_chain import _maybe_split_single_element_and_chain
        cmd = ["echo $HOME && ls"]
        out_cmd, note = _maybe_split_single_element_and_chain(cmd)
        assert out_cmd == cmd
        assert note == ""

    def test_multi_element_cmd_is_passthrough(self):
        """Already-correct multi-element argv is untouched and emits no
        note. This is the load-bearing contract: the autocorrect MUST
        never fire for cmd that is already valid."""
        from ouroboros.tools.shell_and_chain import _maybe_split_single_element_and_chain
        cmd = ["git", "status"]
        out_cmd, note = _maybe_split_single_element_and_chain(cmd)
        assert out_cmd == cmd
        assert note == ""


class TestSingleElementPipeline:
    """``["grep -rn foo . | head -20"]`` is a one-element form of a real
    shell pipeline. Unlike the && argv-boundary mistake (which can be
    safely split into real argv), a pipe needs a shell — the autocorrect
    wraps it as ``["sh", "-c", <raw>]`` instead of refusing the call.
    Closes ``ibl-db9d3608e096``.

    The autocorrect is intentionally narrow:
      * a glued ``|`` (``grep "a|b"`` regex alternation) does NOT
        match — only a whitespace-bracketed pipe does,
      * ``||`` is excluded by the lookbehind/lookahead on ``|``,
      * a leading shell interpreter (``["sh -c 'a | b'"]``,
        ``["bash -c '...'"]`` etc.) is left for the existing
        interpreter path so we do not duplicate the disclosure,
      * multi-element cmd (already-correct multi-token argv or
        existing standalone-operator argv) falls through to the
        existing cascade untouched.
    """

    def test_run_shell_wraps_grep_pipe_to_head(self, tmp_path, fake_subprocess):
        """Headline shape from the bug report: a single-element pipeline
        reaches subprocess as ``[\"sh\", \"-c\", \"grep -rn foo . | head -20\"]``
        with the disclosure note visible in the operator result.
        """
        calls = fake_subprocess(stdout="match\n")
        result = _run_shell(
            _ctx(tmp_path),
            ["grep -rn foo . | head -20"],
        )
        assert "SHELL_CMD_AUTO_WRAP" in result
        assert "SHELL_CMD_ERROR" not in result
        assert calls[0]["cmd"] == ["sh", "-c", "grep -rn foo . | head -20"]

    def test_run_shell_wraps_cat_pipe_to_wc(self, tmp_path, fake_subprocess):
        """Second headline shape: ``cat a.txt | wc -l`` wraps the same way."""
        calls = fake_subprocess(stdout="3\n")
        result = _run_shell(_ctx(tmp_path), ["cat a.txt | wc -l"])
        assert "SHELL_CMD_AUTO_WRAP" in result
        assert "SHELL_CMD_ERROR" not in result
        assert calls[0]["cmd"] == ["sh", "-c", "cat a.txt | wc -l"]

    def test_does_not_wrap_glued_pipe_in_quotes(self):
        """``grep 'a|b' file.txt`` has a glued pipe inside single quotes
        with no whitespace around it — likely regex alternation, not a
        shell pipeline. The autocorrect MUST return ``(cmd, "")``
        untouched.
        """
        from ouroboros.tools.shell_and_chain import _maybe_wrap_single_element_pipeline
        cmd = ["grep 'a|b' file.txt"]
        out_cmd, note = _maybe_wrap_single_element_pipeline(cmd)
        assert out_cmd == cmd
        assert note == ""

    def test_does_not_wrap_no_pipe(self):
        """``ls -la`` carries no pipe at all; the helper MUST pass through
        ``(cmd, \"\")`` so the existing cascade handles it normally.
        """
        from ouroboros.tools.shell_and_chain import _maybe_wrap_single_element_pipeline
        cmd = ["ls -la"]
        out_cmd, note = _maybe_wrap_single_element_pipeline(cmd)
        assert out_cmd == cmd
        assert note == ""

    def test_does_not_wrap_leading_shell_interpreter(self):
        """``[\"sh -c 'a | b'\"]`` is a one-string form of the legitimate
        shell-script argv — wrapping it as ``[\"sh\",\"-c\",<raw>]`` would
        still work, but the existing interpreter branch already handles
        this shape and we MUST NOT duplicate the disclosure. Helper
        returns ``(cmd, \"\")`` so the interpreter branch runs.
        """
        from ouroboros.tools.shell_and_chain import _maybe_wrap_single_element_pipeline
        cmd = ["sh -c 'a | b'"]
        out_cmd, note = _maybe_wrap_single_element_pipeline(cmd)
        assert out_cmd == cmd
        assert note == ""

    def test_does_not_wrap_multi_element_and_chain_split_output(self):
        """``[\"make\", \"&&\", \"test\"]`` and ``[\"a\", \"|\", \"b\"]`` are
        multi-element argv that the existing standalone-operator check
        (downstream of this helper) handles. The pipe helper sees
        ``len(cmd) > 1`` and falls through immediately.
        """
        from ouroboros.tools.shell_and_chain import _maybe_wrap_single_element_pipeline
        for cmd in (
            ["make", "&&", "test"],
            ["a", "|", "b"],
        ):
            out_cmd, note = _maybe_wrap_single_element_pipeline(cmd)
            assert out_cmd == cmd, cmd
            assert note == "", cmd

    def test_run_shell_does_not_break_cd_foo_and_make(self, tmp_path):
        """``[\"cd foo && make\"]`` has a segment with internal whitespace
        (``cd foo``). The && chain split helper leaves it untouched
        (segment-with-space guard) and the cascade downstream keeps
        doing its job — pipe helper adds no regression on top of that.
        """
        result = _run_shell(_ctx(tmp_path), ["cd foo && make"])
        # The existing SHELL_CMD_ERROR path fires (segment has space);
        # what matters here is that the pipe helper did NOT wrap it.
        assert "SHELL_CMD_ERROR" in result
        assert "SHELL_CMD_AUTO_WRAP" not in result

