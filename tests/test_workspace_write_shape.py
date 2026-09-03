"""Mode-aware write-shape classification for interpreter shell commands.

The coarse ``open(`` token marks a read-only ``open(p, 'rb')`` as writeish.
The light-mode runtime_data lane already re-judges that class mode-aware
("the original GAIA class", tests/test_runtime_reliability_v655.py); the
workspace write guard's ``writeish`` composition did not, so a pure-read
hash/compare one-liner in an external workspace was refused as a
"write-like shell command" — a false reason with no route. These tests pin
the mode-aware composition: pure interpreter reads are not write-shaped,
every real write shape still is, and the runtime/secret READ policy for
external workspaces stays intact via its own honest guard.
"""

from __future__ import annotations

import pathlib

import pytest

# Shares the real-subprocess-adjacent registry harness with
# test_external_workspace_access.py; keep it in the serial lane.
pytestmark = pytest.mark.serial

from ouroboros.tools.registry import ToolContext, ToolRegistry
from ouroboros.tools.shell_guards import interpreter_write_shape, shell_has_write_indicator


@pytest.fixture(autouse=True)
def _home_outside_tmp(tmp_path, monkeypatch):
    # Same premise as test_external_workspace_access.py: keep tmp scratch
    # outside $HOME on every platform so host-scratch reads stay non-runtime.
    fake_home = tmp_path / "_home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setattr(pathlib.Path, "home", lambda: fake_home)


def _registry(tmp_path: pathlib.Path, *, mode: str = "external") -> ToolRegistry:
    system = tmp_path / "system"
    workspace = tmp_path / "workspace"
    data = tmp_path / "data"
    for p in (system, workspace, data):
        p.mkdir(exist_ok=True)
    reg = ToolRegistry(repo_dir=system, drive_root=data)
    reg.set_context(
        ToolContext(
            repo_dir=system,
            drive_root=data,
            workspace_root=workspace,
            workspace_mode=mode,
            task_id="task-write-shape",
        )
    )
    return reg


READ_ONLY_HASH_SCRIPT = (
    "import hashlib\n"
    "def h(p):\n"
    "    with open(p, 'rb') as f:\n"
    "        return hashlib.sha256(f.read()).hexdigest()\n"
    "print(h({target!r}))\n"
)


# --- unit layer: the classifier itself -------------------------------------


def test_read_only_open_is_not_interpreter_write_shape():
    cmd = ["python3", "-c", "with open('f.bin', 'rb') as f:\n    print(len(f.read()))"]
    assert interpreter_write_shape(cmd) is False
    # The legacy coarse classifier keeps its pinned behavior for its other
    # consumers (_protected_shell_block, ws5 carryover).
    assert shell_has_write_indicator(cmd) is True


def test_write_mode_open_and_pathlib_open_stay_write_shaped():
    assert interpreter_write_shape(["python3", "-c", "open('/d/x', 'w').write('hi')"]) is True
    assert (
        interpreter_write_shape(
            ["python3", "-c", "from pathlib import Path; Path('/d/x').open('w')"]
        )
        is True
    )


def test_opaque_subprocess_and_library_saves_stay_write_shaped():
    assert (
        interpreter_write_shape(
            ["python3", "-c", "import subprocess; subprocess.run(['rm', '-rf', '/d/x'])"]
        )
        is True
    )
    assert interpreter_write_shape(["python3", "-c", "df.to_csv('out.csv')"]) is True
    assert interpreter_write_shape(["python3", "-c", "fh.writelines(rows)"]) is True
    assert (
        interpreter_write_shape(
            ["node", "-e", "const {writeFileSync} = require('fs'); writeFileSync('x', 'y')"]
        )
        is True
    )


def test_shell_level_signals_still_write_shaped_for_interpreters():
    assert interpreter_write_shape(["sh", "-c", "python3 -c 'print(1)' && rm -rf /tmp/x"]) is True
    assert interpreter_write_shape("python3 gen.py > out.txt") is True
    assert interpreter_write_shape(["sh", "-c", "python3 gen.py && cp out.txt /tmp/y"]) is True


def test_ruby_perl_pure_reads_are_not_write_shaped():
    """LIGHT_SHELL_WRITER_COMMANDS membership (a coarse shell-writer role) must not
    re-add the write shape the mode-aware classifier just re-judged."""
    assert interpreter_write_shape(["ruby", "-e", "puts File.read('/tmp/f.txt')"]) is False
    # perl 3-arg READ open: the filename's own letters (the 'x' in f.txt) must not
    # classify the mode.
    assert (
        interpreter_write_shape(["perl", "-e", "open(my $fh, '<', '/tmp/f.txt'); print <$fh>"])
        is False
    )
    assert interpreter_write_shape(["ruby", "-e", "File.write('/tmp/x', 'y')"]) is True


def test_perl_ruby_native_write_idioms_stay_write_shaped():
    """fable-5 review: dropping the membership floor demands the vocabulary
    actually SEE perl/ruby write spellings — '>'-mode opens, File.delete,
    FileUtils with a variable argument, IO.binwrite."""
    assert interpreter_write_shape(["perl", "-e", "open(FH,'>','/outside/x'); print FH 'data'"]) is True
    assert interpreter_write_shape(["perl", "-e", "open(FH, '>>', $log); print FH $line"]) is True
    assert interpreter_write_shape(["ruby", "-e", "File.delete('/outside/x')"]) is True
    assert interpreter_write_shape(["ruby", "-e", "f='/x'; FileUtils.rm_rf(f)"]) is True
    assert interpreter_write_shape(["ruby", "-e", "IO.binwrite('a.bin', d)"]) is True


def test_ruby_file_open_is_mode_aware(tmp_path):
    """sol review: File.open must be a write target only with a write-mode 2nd arg —
    File.open('/x','r') is a READ and must not be reported as a write, while
    File.open('/x','w') and File.new('/x','w') stay writes."""
    from ouroboros.tools.shell_guards import writer_target_tokens
    read = ["ruby", "-e", "File.open('/tmp/r','r') { |f| puts f.read }"]
    # The literal path is NOT emitted as a write target for a read-mode open.
    assert "/tmp/r" not in writer_target_tokens(read)
    assert interpreter_write_shape(read) is False
    assert interpreter_write_shape(["ruby", "-e", "File.open('/tmp/o','w') { |f| f.puts 'x' }"]) is True
    assert "/tmp/o" in writer_target_tokens(["ruby", "-e", "File.open('/tmp/o','w') { |f| f.puts 'x' }"])
    assert interpreter_write_shape(["ruby", "-e", "File.new('/tmp/o','w')"]) is True
    assert interpreter_write_shape(["ruby", "-e", "File.new('/tmp/o','r')"]) is False


def test_keyword_mode_open_is_write_shaped():
    """sol review: open('/x', mode='w') with a statically-known keyword mode is a
    real write (distinct from the disclosed variable-mode residual)."""
    assert interpreter_write_shape(["python3", "-c", "open('/tmp/o', mode='w')"]) is True
    assert interpreter_write_shape(["python3", "-c", "open('/tmp/o', mode='rb')"]) is False


def test_unspaced_posix_redirect_is_write_shaped():
    """fable-5 review: `python3 gen.py>out.txt` is ONE shlex token; the redirect
    shape must be recognized mid-token, while located inline-code bodies keep
    their '>' comparisons/filehandles as reads."""
    assert interpreter_write_shape("python3 gen.py>out.txt") is True
    assert interpreter_write_shape("python3 gen.py>>log.txt") is True
    assert interpreter_write_shape(["python3", "-c", "print(1 if a > b else 2)"]) is False
    assert interpreter_write_shape(["python3", "-c", "x = {'k': 'v => w'}; print(x)"]) is False


def test_prose_words_are_not_write_shapes_for_interpreters():
    """Natural-language words in code text ('scp done', 'count deleted rows') are
    not write evidence; structural spellings (os.remove, rm in a compound) are."""
    assert interpreter_write_shape(["python3", "-c", "print(open('/tmp/f').read()); print('scp done')"]) is False
    assert interpreter_write_shape(["python3", "-c", "print('count deleted rows'); print(open('/tmp/f').read())"]) is False
    assert interpreter_write_shape(["python3", "-c", "print('results truncated')"]) is False
    assert interpreter_write_shape(["python3", "-c", "import os; os.remove('/tmp/x')"]) is True
    assert interpreter_write_shape(["python3", "-c", "f.truncate(0)"]) is True


# --- guard layer: workspace lanes ------------------------------------------


def test_external_pure_read_outside_runtime_is_allowed(tmp_path):
    """The census class (rows 1-5): a read-only interpreter hash/inspect over
    host scratch was refused as 'write-like'; it is a plain allowed read."""
    reg = _registry(tmp_path, mode="external")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    target = scratch / "artifact.bin"
    target.write_bytes(b"payload")
    cmd = ["python3", "-c", READ_ONLY_HASH_SCRIPT.format(target=str(target))]
    assert reg._run_shell_safety_check({"cmd": cmd, "cwd": str(tmp_path / "workspace")}, "advanced") is None


def test_workspace_mode_pure_read_outside_root_is_allowed(tmp_path):
    reg = _registry(tmp_path, mode="workspace")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    target = scratch / "report.txt"
    target.write_text("data", encoding="utf-8")
    cmd = ["python3", "-c", f"print(open({str(target)!r}, 'r').read())"]
    assert reg._run_shell_safety_check({"cmd": cmd, "cwd": str(tmp_path / "workspace")}, "advanced") is None


def test_external_sh_wrapped_pure_read_is_allowed(tmp_path):
    reg = _registry(tmp_path, mode="external")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    target = scratch / "blob.bin"
    target.write_bytes(b"x" * 16)
    inner = f"python3 -c \"print(open({str(target)!r}, 'rb').read(8))\""
    assert reg._run_shell_safety_check({"cmd": ["sh", "-c", inner], "cwd": str(tmp_path / "workspace")}, "advanced") is None


def test_external_pure_read_of_runtime_still_blocked_via_read_guard(tmp_path):
    """The owner contract stands: external shell may not READ the runtime/data
    drive — but the block now comes from the honest read guard, which names
    the gated read_file route instead of calling a read 'write-like'."""
    reg = _registry(tmp_path, mode="external")
    data = tmp_path / "data"
    (data / "settings.json").write_text("{}", encoding="utf-8")
    cmd = [
        "python3",
        "-c",
        READ_ONLY_HASH_SCRIPT.format(target=str(data / "settings.json")),
    ]
    out = reg._run_shell_safety_check({"cmd": cmd, "cwd": str(tmp_path / "workspace")}, "advanced") or ""
    assert "WORKSPACE_SHELL_BLOCKED" in out
    assert "read_file" in out
    assert "write-like" not in out


def test_external_write_mode_open_to_runtime_still_blocked(tmp_path):
    reg = _registry(tmp_path, mode="external")
    data = tmp_path / "data"
    cmd = ["python3", "-c", f"open({str(data / 'x')!r}, 'w').write('hi')"]
    out = reg._run_shell_safety_check({"cmd": cmd, "cwd": str(tmp_path / "workspace")}, "advanced") or ""
    assert "WORKSPACE_SHELL_BLOCKED" in out
    # A bare write-mode open with NO .write( chain (truncation alone) as well.
    bare = ["python3", "-c", f"open({str(data / 'x')!r}, 'w')"]
    out2 = reg._run_shell_safety_check({"cmd": bare, "cwd": str(tmp_path / "workspace")}, "advanced") or ""
    assert "WORKSPACE_SHELL_BLOCKED" in out2


def test_external_ruby_pure_read_allowed_and_ruby_write_blocked(tmp_path):
    reg = _registry(tmp_path, mode="external")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    target = scratch / "f.txt"
    target.write_text("data", encoding="utf-8")
    read_cmd = ["ruby", "-e", f"puts File.read({str(target)!r})"]
    assert reg._run_shell_safety_check({"cmd": read_cmd, "cwd": str(tmp_path / "workspace")}, "advanced") is None
    data = tmp_path / "data"
    write_cmd = ["ruby", "-e", f"File.write({str(data / 'x')!r}, 'y')"]
    out = reg._run_shell_safety_check({"cmd": write_cmd, "cwd": str(tmp_path / "workspace")}, "advanced") or ""
    assert "WORKSPACE_SHELL_BLOCKED" in out


def test_external_pathlib_write_open_to_runtime_still_blocked(tmp_path):
    reg = _registry(tmp_path, mode="external")
    data = tmp_path / "data"
    cmd = [
        "python3",
        "-c",
        f"from pathlib import Path; Path({str(data / 'x')!r}).open('w')",
    ]
    out = reg._run_shell_safety_check({"cmd": cmd, "cwd": str(tmp_path / "workspace")}, "advanced") or ""
    assert "WORKSPACE_SHELL_BLOCKED" in out


def test_external_opaque_subprocess_naming_runtime_still_blocked(tmp_path):
    reg = _registry(tmp_path, mode="external")
    data = tmp_path / "data"
    cmd = [
        "python3",
        "-c",
        f"import subprocess; subprocess.run(['rm', '-rf', {str(data / 'x')!r}])",
    ]
    out = reg._run_shell_safety_check({"cmd": cmd, "cwd": str(tmp_path / "workspace")}, "advanced") or ""
    assert "WORKSPACE_SHELL_BLOCKED" in out


def test_pure_filter_reads_outside_root_are_allowed(tmp_path):
    """Scope-C: sort/uniq/sed -n/tar -tf/gzip -l READ invocations must not be
    'write-like' — membership alone is not a write channel."""
    reg = _registry(tmp_path, mode="external")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    data_file = scratch / "data.csv"
    data_file.write_text("b\na\n", encoding="utf-8")
    archive = scratch / "a.tar"
    archive.write_bytes(b"x" * 16)
    for cmd in (
        ["sort", str(data_file)],
        ["uniq", str(data_file)],
        ["sed", "-n", "1p", str(data_file)],
        ["tar", "-tf", str(archive)],
        ["gzip", "-l", str(archive)],
    ):
        out = reg._run_shell_safety_check({"cmd": cmd, "cwd": str(tmp_path / "workspace")}, "advanced")
        assert out is None, (cmd, out)


def test_sed_script_write_channels_stay_write_shaped(tmp_path):
    """fable-5 round-2: sed writes WITHOUT -i too — the POSIX in-script `w FILE`
    command, the `s///w` flag, GNU `s///e` execute, a -f script file (unprovable),
    and the GNU attached `-ibak` suffix. All must keep writer targets; plain
    filters and patterns containing prose words stay reads."""
    from ouroboros.tools.shell_guards import writer_target_tokens
    for cmd in (
        ["sed", "w out.py", "f"],
        ["sed", "-n", "s/a/b/w out.txt", "f"],
        ["sed", "s/x/y/e", "f"],
        ["sed", "-f", "script.sed", "f"],
        ["sed", "-e", "w dump.txt", "f"],
        ["sed", "-ibak", "s/x/y/", "f"],
    ):
        assert writer_target_tokens(cmd), cmd
    for cmd in (
        ["sed", "-n", "1,40p", "f"],
        ["sed", "-n", "/delete/p", "f"],
        ["sed", "s/hello/world/g", "f"],
    ):
        assert writer_target_tokens(cmd) == [], cmd
    # e2e: the in-script write to a runtime path is refused; the same shape
    # reading host scratch passes.
    reg = _registry(tmp_path, mode="external")
    data = tmp_path / "data"
    out = reg._run_shell_safety_check(
        {"cmd": ["sed", f"w {data / 'x'}", "/etc/hostname"], "cwd": str(tmp_path / "workspace")},
        "advanced",
    ) or ""
    assert "WORKSPACE_SHELL_BLOCKED" in out
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    target = scratch / "f.txt"
    target.write_text("x", encoding="utf-8")
    assert reg._run_shell_safety_check(
        {"cmd": ["sed", "-n", "/delete/p", str(target)], "cwd": str(tmp_path / "workspace")},
        "advanced",
    ) is None


def test_sol_r2_channel_grammar_writes_stay_write_shaped():
    """sol-max round-2: option/operand grammar gaps — every one of these is a
    real write and must report a target (or write shape)."""
    from ouroboros.tools.shell_guards import writer_target_tokens
    for cmd in (
        ["sort", "--output", "/o", "/etc/hosts"],
        ["uniq", "-", "/o"],
        ["sed", "-nibak", "s/a/b/", "/f"],
        ["sed", "-n", "1w /o", "/etc/hosts"],
        ["tar", "-cf/o.tar", "/etc/hosts"],
        ["tar", "--extract", "--file=/i.tar", "--directory=/od"],
        ["tar", "xf", "/a.tar"],
        ["gzip", "-S.tgz", "/f"],
    ):
        assert writer_target_tokens(cmd), cmd
    # Old-style/list/read spellings stay reads.
    for cmd in (
        ["tar", "tf", "/a.tar"],
        ["tar", "-tf", "/a.tar"],
        ["gzip", "-l", "/a.gz"],
        ["sed", "s/e/x/", "/f"],
        ["sed", "-n", "/e/p", "/f"],
    ):
        assert writer_target_tokens(cmd) == [], cmd


def test_sol_r2_compound_and_carve_holes_closed(tmp_path):
    """sol-max round-2: a pure-filter HEAD speaks only for its own segment, and
    uniq's '-' stdin operand cannot hide its output operand from the carve."""
    from ouroboros.tools.registry import _is_pure_read_inspection
    from ouroboros.tools.write_shape import non_interpreter_write_shape
    assert (
        non_interpreter_write_shape(
            "sort /etc/hosts && find /d -name x -delete",
            ["sort"], "sort", is_pure_read=_is_pure_read_inspection,
        )
        is True
    )
    assert _is_pure_read_inspection("printf x | uniq - data/settings.json") is False
    assert _is_pure_read_inspection("uniq /var/log/a.txt") is True


def test_inline_body_isolation_for_joined_and_wrapped_forms():
    """sol-max round-2: a '>' comparison inside a located body is not a redirect
    even when the body rides a joined flag or an sh -c wrap; real redirects
    outside the body still classify."""
    assert interpreter_write_shape(["python3", "-cprint(2 > 1)"]) is False
    assert (
        interpreter_write_shape(
            ["sh", "-c", "python3 -c 'print(2 > 1); print(open(\"/etc/hosts\").read())'"]
        )
        is False
    )
    assert interpreter_write_shape(["sh", "-c", "python3 gen.py > out.txt"]) is True


def test_pure_filter_write_channels_still_blocked(tmp_path):
    """The real channels stay writes: sort -o, sed -i, uniq's second operand,
    tar extract, gzip default all still take guard B."""
    reg = _registry(tmp_path, mode="external")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    src = scratch / "in.txt"
    src.write_text("x\n", encoding="utf-8")
    for cmd in (
        ["sort", "-o", str(scratch / "out.txt"), str(src)],
        ["sed", "-i", "s/a/b/", str(src)],
        ["uniq", str(src), str(scratch / "out.txt")],
        ["tar", "-xf", str(scratch / "a.tar"), "-C", str(scratch)],
        ["gzip", str(src)],
    ):
        out = reg._run_shell_safety_check({"cmd": cmd, "cwd": str(tmp_path / "workspace")}, "advanced") or ""
        assert "WORKSPACE_SHELL_BLOCKED" in out, (cmd, out)


def test_workspace_write_block_message_names_path_and_route(tmp_path):
    """The five formerly byte-identical guard-B messages carry the resolved
    offending path (the light-lane message is the exemplar)."""
    reg = _registry(tmp_path, mode="external")
    data = tmp_path / "data"
    cmd = ["python3", "-c", f"open({str(data / 'x')!r}, 'w').write('hi')"]
    out = reg._run_shell_safety_check({"cmd": cmd, "cwd": str(tmp_path / "workspace")}, "advanced") or ""
    assert "Blocked path:" in out
    assert "read_file" in out


def test_outside_root_write_block_message_names_path_and_root(tmp_path):
    """The outside-process-root variant names the blocked path and the selected
    process root, so the agent can self-correct instead of guessing."""
    reg = _registry(tmp_path, mode="external")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    cmd = ["python3", "-c", f"open({str(scratch / 'out.txt')!r}, 'w').write('hi')"]
    out = reg._run_shell_safety_check({"cmd": cmd, "cwd": str(tmp_path / "workspace")}, "advanced") or ""
    assert "WORKSPACE_SHELL_BLOCKED" in out
    assert "outside the selected process root" in out
    assert "Blocked path:" in out
    assert "Selected process root:" in out


@pytest.mark.parametrize(
    "cmd",
    (
        ["/usr/bin/python3", "-c", 'import os; os.write(1, b"x")'],
        ["/opt/homebrew/bin/node", "-e", "console.log(1)"],
    ),
    ids=("python-os-write-fd", "node-console-log"),
)
def test_unprovable_row_never_promotes_absolute_executable_to_write(tmp_path, cmd):
    from ouroboros.tools.registry import _workspace_write_candidates

    forced_uncertain_row = [(cmd, [], (cmd[-1],), True)]
    assert (cmd[0], True) not in _workspace_write_candidates(
        forced_uncertain_row, [], cmd
    )
    reg = _registry(tmp_path, mode="external")
    assert reg._run_shell_safety_check(
        {"cmd": cmd, "cwd": str(tmp_path / "workspace")}, "advanced"
    ) is None


def test_uncertain_python_body_naming_outside_path_stays_blocked(tmp_path):
    reg = _registry(tmp_path, mode="external")
    cmd = [
        "/usr/bin/python3", "-c",
        'import subprocess; subprocess.run(["rm", "/Users/Shared/x"])',
    ]
    out = reg._run_shell_safety_check(
        {"cmd": cmd, "cwd": str(tmp_path / "workspace")}, "advanced"
    ) or ""
    assert "outside the selected process root" in out
    assert "/Users/Shared/x" in out


def test_os_write_through_literal_os_open_outside_stays_blocked(tmp_path):
    from ouroboros.tools.shell_guards import _python_write_targets_and_unknown

    reg = _registry(tmp_path, mode="external")
    code = (
        'import os; fd=os.open("/Users/Shared/out", os.O_WRONLY|os.O_CREAT); '
        'os.write(fd, b"x")'
    )
    targets, unknown = _python_write_targets_and_unknown(code)
    assert targets == ["/Users/Shared/out"] and unknown is False
    out = reg._run_shell_safety_check(
        {"cmd": ["/usr/bin/python3", "-c", code], "cwd": str(tmp_path / "workspace")},
        "advanced",
    ) or ""
    assert "outside the selected process root" in out
    assert "/Users/Shared/out" in out


def test_split_redirections_grammar():
    """One redirect grammar: both the glued and the split spellings, reads and
    descriptor duplication yield no target, and an operator away from a token's
    start is left alone."""
    from ouroboros.shell_parse import shell_argv, split_redirections

    assert split_redirections(shell_argv("cp a b 2>/dev/null")) == (["cp", "a", "b"], [])
    assert split_redirections(shell_argv("cp x y >> log.txt")) == (["cp", "x", "y"], ["log.txt"])
    assert split_redirections(shell_argv("node t.js > out.log 2>&1")) == (
        ["node", "t.js"],
        ["out.log"],
    )
    assert split_redirections(shell_argv("printf x >&2")) == (["printf", "x"], [])
    assert split_redirections(shell_argv("tee out.txt < in.txt")) == (["tee", "out.txt"], [])
    assert split_redirections(shell_argv("cat <<'EOF' > out.txt")) == (["cat"], ["out.txt"])
    # The split spelling the operator-aware lexer emits: a bare descriptor token
    # belongs to the operator that follows it.
    assert split_redirections(["cp", "a", "b", "2", ">", "/dev/null"]) == (["cp", "a", "b"], [])
    assert split_redirections(shell_argv("echo hi >|out")) == (["echo", "hi"], ["out"])
    # A `>`/`<` away from position 0 is a literal byte, not an operator.
    assert split_redirections(["sed", "s/>/x/", "f"]) == (["sed", "s/>/x/", "f"], [])
    assert split_redirections(["git", "log", "--pretty=<x>"]) == (
        ["git", "log", "--pretty=<x>"],
        [],
    )


def test_writer_targets_recover_the_destination_behind_a_redirect():
    """The writer-target lane reads the operator-aware view, so a redirect no
    longer displaces the command's own destination."""
    from ouroboros.tools.shell_guards import writer_target_rows, writer_target_tokens

    assert writer_target_tokens(["cp", "x", "y", ">>", "log.txt"]) == ["y", "log.txt"]
    # A row is (segment_argv, targets, inline_code, unprovable); a `cd` operand
    # takes the target policy because it is how a later relative write escapes.
    assert writer_target_rows("cd . && cp src.txt /D/.env") == [
        (["cd", "."], ["."], (), False),
        (["cp", "src.txt", "/D/.env"], ["/D/.env"], (), False),
    ]


def test_workspace_rows_filter_bodies_without_weakening_the_light_fence():
    from ouroboros.tools.shell_guards import writer_target_rows, writer_target_tokens

    body = "print '/outside/mentioned.txt'"
    # The light fence keeps the historical unfiltered signal. The workspace
    # lane removes the body from its path targets; its mode-aware classifier
    # proves this body read-only, so it carries no row uncertainty.
    assert writer_target_tokens(["perl", "-e", body]) == [body]
    assert writer_target_rows(["perl", "-e", body]) == [
        (["perl", "-e", body], [], (body,), False),
    ]


def test_cp_source_outside_root_is_a_read_and_destination_still_blocked(tmp_path):
    """A writer's SOURCE operand is a read: copying an outside file INTO the
    process root is the sanctioned transfer route, while an outside DESTINATION
    stays refused."""
    reg = _registry(tmp_path, mode="external")
    workspace = str(tmp_path / "workspace")
    outside = tmp_path / "outside"
    outside.mkdir()

    def check(cmd):
        return reg._run_shell_safety_check({"cmd": cmd, "cwd": workspace}, "advanced")

    assert check(["cp", str(outside / "widget.js"), "widget.js"]) is None
    assert check(["ln", "-s", str(outside / "src"), "link.js"]) is None
    assert check(["cat", str(outside / "widget.js")]) is None
    destination_block = check(["cp", "widget.js", str(outside / "copy.js")]) or ""
    assert "outside the selected process root" in destination_block
    assert str(outside / "copy.js") in destination_block


def test_relative_protected_root_source_still_blocked(tmp_path):
    """A protected runtime path refuses on MENTION for every candidate, so a
    writer naming it as a SOURCE still cannot launder a read through shell."""
    reg = _registry(tmp_path, mode="external")
    workspace = str(tmp_path / "workspace")
    (tmp_path / "data" / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / "system" / "ouroboros").mkdir(parents=True, exist_ok=True)
    (tmp_path / "system" / "ouroboros" / "safety.py").write_text("x = 1\n", encoding="utf-8")

    for cmd, blocked in (
        (["cp", "../data/settings.json", "./x"], tmp_path / "data" / "settings.json"),
        (
            ["cp", "../system/ouroboros/safety.py", "./x"],
            tmp_path / "system" / "ouroboros" / "safety.py",
        ),
    ):
        out = reg._run_shell_safety_check({"cmd": cmd, "cwd": workspace}, "advanced") or ""
        assert "mentions Ouroboros system/data paths" in out, cmd
        # The blocked path is the FILE, i.e. the per-candidate containment branch
        # rather than the whole-command text scan.
        assert f"Blocked path: {blocked}" in out, cmd


def test_glued_operator_is_not_a_path_candidate(tmp_path):
    """A redirection glued to the following separator is not a path: `2>/dev/null;`
    was forged into the target `/dev/null;` and refused a pure read."""
    reg = _registry(tmp_path, mode="external")
    workspace = str(tmp_path / "workspace")
    outside = tmp_path / "outside"
    outside.mkdir()

    def check(cmd):
        return reg._run_shell_safety_check({"cmd": cmd, "cwd": workspace}, "advanced")

    assert check(["sh", "-c", "git reset HEAD scratch/ 2>/dev/null; rm -rf scratch/"]) is None
    assert check(["sh", "-c", "node build.js 2>/dev/null; echo ok"]) is None
    redirect_block = check(["sh", "-c", f"node t.js > {outside / 'out.log'} 2>&1"]) or ""
    assert "outside the selected process root" in redirect_block
    assert str(outside / "out.log") in redirect_block


def test_inline_code_segment_keeps_the_mention_scan(tmp_path):
    """An interpreter body contributes its EXTRACTED write targets as writes and
    everything else as a mention: regex punctuation stops being a forged path, an
    extracted outside write target still refuses, and a protected runtime mention
    inside the body still refuses.

    DISCLOSED FLIP: an outside path merely READ by an in-root writer no longer
    refuses — the same class as a cp source operand."""
    reg = _registry(tmp_path, mode="external")
    workspace = str(tmp_path / "workspace")
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = tmp_path / "data" / "settings.json"
    protected.write_text("{}", encoding="utf-8")

    def check(code):
        return reg._run_shell_safety_check(
            {"cmd": ["python3", "-c", code], "cwd": workspace}, "advanced"
        )

    # (a) regex punctuation harvested out of the body is not a path
    assert check("import re; re.sub('/[^/]+$','',''); open('x','w')") is None
    # (b) an extracted write target outside the root still refuses
    outside_write = check(f"open({str(outside / 'ok')!r},'w')") or ""
    assert "outside the selected process root" in outside_write
    assert str(outside / "ok") in outside_write
    # (c) a protected runtime path mentioned by an in-root writer still refuses
    protected_read = check(f"open('ok','w'); print(open({str(protected)!r}).read())") or ""
    assert "mentions Ouroboros system/data paths" in protected_read
    # (d) the disclosed flip: an ordinary outside path merely READ is allowed
    assert check(f"open('ok','w'); print(open({str(outside / 'y')!r}).read())") is None


def test_sed_in_script_target_survives_the_narrowed_scan(tmp_path):
    """sed's in-script `w FILE` hides the path inside the script operand, so the
    parsed targets keep their embedded-path pass."""
    reg = _registry(tmp_path, mode="external")
    workspace = str(tmp_path / "workspace")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    out = reg._run_shell_safety_check(
        {"cmd": ["sed", f"w {scratch / 'x'}", "f"], "cwd": workspace}, "advanced"
    ) or ""
    assert "outside the selected process root" in out
    assert f"Blocked path: {scratch / 'x'}" in out


def _outside_write_result(tmp_path, cmd):
    reg = _registry(tmp_path, mode="external")
    workspace = str(tmp_path / "workspace")
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    rendered = cmd(outside)
    return rendered, reg._run_shell_safety_check(
        {"cmd": rendered, "cwd": workspace}, "advanced"
    ) or ""


def test_nested_shell_write_outside_workspace_stays_blocked(tmp_path):
    cmd, out = _outside_write_result(
        tmp_path,
        lambda outside: ["sh", "-c", f"sh -c 'echo x > {outside / 'nested.txt'}'"],
    )
    assert "WORKSPACE_SHELL_BLOCKED" in out, cmd


def test_find_exec_rm_outside_workspace_stays_blocked(tmp_path):
    cmd, out = _outside_write_result(
        tmp_path,
        lambda outside: ["find", str(outside), "-exec", "rm", "{}", ";"],
    )
    assert "WORKSPACE_SHELL_BLOCKED" in out, cmd


def test_python_shutil_copy_outside_workspace_stays_blocked(tmp_path):
    cmd, out = _outside_write_result(
        tmp_path,
        lambda outside: [
            "python3", "-c", f"import shutil; shutil.copy('a','{outside / 'copy.txt'}')",
        ],
    )
    assert "WORKSPACE_SHELL_BLOCKED" in out, cmd


def test_f1_find_exec_tee_placeholder_stays_blocked(tmp_path):
    cmd, out = _outside_write_result(
        tmp_path,
        lambda outside: f"find {outside} -exec tee {{}} \\;",
    )
    assert "WORKSPACE_SHELL_BLOCKED" in out, cmd


def test_f2_python_unknown_widens_even_with_recovered_target(tmp_path):
    cmd, out = _outside_write_result(
        tmp_path,
        lambda outside: [
            "python3", "-c",
            f'import subprocess; open("inside","w"); subprocess.run(["rm","{outside / "py-mixed"}"])',
        ],
    )
    assert "WORKSPACE_SHELL_BLOCKED" in out, cmd


def test_f3_env_chdir_is_effective_cwd_for_wrapped_write(tmp_path):
    for option in ("-C", "--chdir"):
        cmd, out = _outside_write_result(
            tmp_path,
            lambda outside: [
                "env", option, str(outside), "python3", "-c", 'open("relative","w")',
            ],
        )
        assert "WORKSPACE_SHELL_BLOCKED" in out, cmd
    cmd, out = _outside_write_result(
        tmp_path,
        lambda outside: [
            "env", f"--chdir={outside}", "python3", "-c", 'open("relative","w")',
        ],
    )
    assert "WORKSPACE_SHELL_BLOCKED" in out, cmd


def test_f4_perl_body_uncertainty_ignores_unrelated_operand(tmp_path):
    cmd, out = _outside_write_result(
        tmp_path,
        lambda outside: [
            "perl", "-e", f'open(F, ">", "{outside / "perl-out"}"); print F "x"',
            "input.txt",
        ],
    )
    assert "WORKSPACE_SHELL_BLOCKED" in out, cmd


def test_f5_ruby_fileutils_move_uses_destination(tmp_path):
    cmd, out = _outside_write_result(
        tmp_path,
        lambda outside: [
            "ruby", "-e",
            f'require "fileutils"; FileUtils.mv("inside", "{outside / "ruby-move"}")',
        ],
    )
    assert "WORKSPACE_SHELL_BLOCKED" in out, cmd


def test_f6_node_opaque_exec_widens_with_recovered_target(tmp_path):
    cmd, out = _outside_write_result(
        tmp_path,
        lambda outside: [
            "node", "-e",
            f'require("fs").writeFileSync("inside","x"); '
            f'require("child_process").execSync("rm {outside / "node-mixed"}")',
        ],
    )
    assert "WORKSPACE_SHELL_BLOCKED" in out, cmd


def test_f7_python_literal_heredoc_read_stays_allowed(tmp_path):
    reg = _registry(tmp_path, mode="external")
    outside = tmp_path / "outside"
    outside.mkdir()
    command = f"python3 - <<'EOF'\nprint(open(\"{outside / 'read'}\").read())\nEOF"
    assert reg._run_shell_safety_check(
        {"cmd": command, "cwd": str(tmp_path / "workspace")}, "advanced"
    ) is None


def test_f8_uncertain_perl_row_does_not_widen_independent_cat(tmp_path):
    reg = _registry(tmp_path, mode="external")
    outside = tmp_path / "outside"
    outside.mkdir()
    command = f"perl -e 'print 1' && cat {outside / 'read'}"
    assert reg._run_shell_safety_check(
        {"cmd": command, "cwd": str(tmp_path / "workspace")}, "advanced"
    ) is None


def test_round3_old_block_coverage_stays_blocked(tmp_path):
    import shlex

    reg = _registry(tmp_path, mode="external")
    workspace = str(tmp_path / "workspace")
    outside = tmp_path / "outside"
    outside.mkdir()

    def nested_shell(depth):
        body = f"cp x {outside / 'nested'}"
        for _ in range(depth - 1):
            body = f"sh -c {shlex.quote(body)}"
        return ["sh", "-c", body]

    commands = (
        ("eval_quoting_layers", ["sh", "-c", f'eval "cp x {outside / "eval"}"']),
        ("nested_depth_3", nested_shell(3)),
        ("nested_depth_4_fallback", nested_shell(4)),
        ("timeout_shell", ["timeout", "5", "sh", "-c", f"cp x {outside / 'timeout'}"]),
        ("nohup_shell", ["nohup", "bash", "-c", f"echo x > {outside / 'nohup'}"]),
        ("xargs_visible_producer", f"printf {outside / 'xargs'} | xargs -I{{}} cp x {{}}"),
        ("xargs_custom_placeholder", f"printf {outside / 'xargs-custom'} | xargs -I@ cp x @"),
        ("cd_relative_write", f"cd {outside} && echo x > rel"),
        ("pushd_relative_write", f"pushd {outside} && echo x > rel"),
        (
            "env_python_shutil_move",
            ["env", "FOO=1", "python3", "-c", f'import shutil; shutil.move("x","{outside / "py-move"}")'],
        ),
        (
            "python_heredoc_write",
            f"python3 - <<'EOF'\nopen(\"{outside / 'py-heredoc'}\",\"w\")\nEOF",
        ),
        ("perl_open_write", ["perl", "-e", f'open(F, ">", "{outside / "perl"}")']),
        ("ruby_file_write", ["ruby", "-e", f'File.write("{outside / "ruby"}", "x")']),
        (
            "node_write_file_sync",
            ["node", "-e", f'require("fs").writeFileSync("{outside / "node"}","x")'],
        ),
        ("awk_redirect", f"awk '{{print $1}}' input > {outside / 'awk'}"),
        ("rsync_destination", ["rsync", "src", str(outside / "rsync")]),
        ("tar_chdir_extract", ["tar", "-C", str(outside), "-xf", "a.tar"]),
        ("append_redirect", f"echo x >> {outside / 'append'}"),
        ("stderr_redirect", f"awk '{{print $1}}' input 2> {outside / 'stderr'}"),
        ("combined_redirect", f"awk '{{print $1}}' input &> {outside / 'combined'}"),
        ("windows_drive", ["cp", "x", r"C:\outside\drive.txt"]),
        ("windows_unc", ["cp", "x", r"\\server\share\unc.txt"]),
    )
    for name, command in commands:
        out = reg._run_shell_safety_check({"cmd": command, "cwd": workspace}, "advanced") or ""
        assert "WORKSPACE_SHELL_BLOCKED" in out, (name, command, out)


def test_round3_body_uncertainty_variants_are_row_scoped():
    from ouroboros.tools.shell_guards import writer_target_rows

    find_row = writer_target_rows(["find", "/tree", "-exec", "tee", "copy-{}", ";"])[0]
    assert find_row[1] == []
    assert find_row[3] is True
    for method in ("exec", "spawn", "execSync", "spawnSync"):
        row = writer_target_rows(
            ["node", "-e", f'child_process.{method}("rm /outside")']
        )[0]
        assert row[3] is True, method
    ruby_row = writer_target_rows(
        ["ruby", "-e", 'FileUtils.mv("/outside/source", destination)']
    )[0]
    assert ruby_row[3] is True


def test_round3_ordinary_outside_reads_stay_allowed(tmp_path):
    reg = _registry(tmp_path, mode="external")
    workspace = str(tmp_path / "workspace")
    outside = tmp_path / "outside"
    outside.mkdir()
    commands = (
        ("grep", ["grep", "x", str(outside / "read")]),
        ("rg", ["rg", "x", str(outside / "read")]),
        ("cat", ["cat", str(outside / "read")]),
        ("ls", ["ls", str(outside)]),
        ("git_status", ["git", "-C", str(outside), "status"]),
        ("pytest", ["pytest", str(outside / "test.py")]),
        (
            "python_open_read",
            ["python3", "-c", f'print(open("{outside / "read"}").read())'],
        ),
        (
            "python_heredoc_read",
            f"python3 - <<'EOF'\nprint(open(\"{outside / 'read'}\").read())\nEOF",
        ),
        ("mixed_perl_cat", f"perl -e 'print 1' && cat {outside / 'read'}"),
        (
            "perl_open_read",
            ["perl", "-e", f'open(my $fh, "<", "{outside / "read"}"); print <$fh>'],
        ),
    )
    for name, command in commands:
        out = reg._run_shell_safety_check({"cmd": command, "cwd": workspace}, "advanced")
        assert out is None, (name, command, out)
