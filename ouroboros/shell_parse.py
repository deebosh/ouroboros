"""Small shell argv parsing helpers shared by tool guardrails."""

from __future__ import annotations

import ast
import json
import pathlib
import re
import shlex
from typing import Any, List


EMBEDDED_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.\-])/[^\s'\"\\),;\]]+")
_HTML_CLOSING_TAG_PATH_RE = re.compile(r"/[A-Za-z][A-Za-z0-9:-]*>")
# A path absolute in WINDOWS grammar: a drive letter plus a separator, or a UNC
# share. The UNC alternative REQUIRES both segments (`\\server\share`) because a
# bare `\\`-plus-text is not a path at all — it is the commonest escape idiom in
# every other language (`s.replace(/\\/g,'/')`, `"a\\b"`, `'\\b'`), and matching it
# made the light-mode inline fence refuse ordinary `node -e` payloads that named no
# repo path (v6.89.x). A share segment cannot be spelled by accident.
# A BACKTICK is deliberately NOT a delimiter, in either grammar. Treating it as one
# (added and then removed 2026-08-05) closed the template-literal exact-root hole
# (``rmSync(String.raw`<root>`)`` harvests root+backtick = a sibling, so deleting the
# exact root is ALLOWED — only the root itself; anything under it still resolves
# inside) but also newly refused writes to real sibling paths with a literal backtick
# in the name, which the base allowed. Owner policy: protection may be weakened, never
# strengthened, and a review-found adjacent hole is disclosed, not fenced. The hole is
# pinned as disclosed in test_interpreter_family_write_fence.py.
EMBEDDED_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:"
    r"[A-Za-z]:[\\/][^\s'\"),;\]]+"
    r"|\\\\[^\s'\"),;\]\\/]+[\\/][^\s'\"),;\]]+"
    r")"
)
_SHELLS = {"sh", "bash", "zsh"}


def recover_stringified_argv(text: Any) -> List[str] | None:
    """Recover a stringified argv list — ``'["go","test"]'`` (JSON) or ``"['go','test']"``
    (Python literal) — into a real ``["go", "test"]`` argv, or ``None`` when ``text`` is not
    a parseable list-of-strings (a plain command string is NOT shell-split here — the caller
    owns that fallback). SSOT shared by ``run_command`` (``shell._run_shell``) and
    ``verify_and_record`` (``verify._normalize_check``) so any command-taking tool recovers
    the same stringified-argv mistake identically (Bible P7 DRY / P2 class-fix). ``json``'s
    ``JSONDecodeError`` is a ``ValueError`` subclass, so the two parsers share one guard."""
    if not isinstance(text, str):
        return None
    for parse in (json.loads, ast.literal_eval):
        try:
            parsed = parse(text)
        except (ValueError, SyntaxError):
            continue
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            return list(parsed)
    return None


def normalize_check_argv(check: Any) -> List[str] | None:
    """Normalize a verify_and_record ``check`` into the argv that is BOTH executed and
    shell-guard-inspected — ONE SSOT so the guard sees exactly what runs (they previously
    each hardcoded ``sh -lc`` and could drift). A string is first recovered as a stringified
    argv (``'["go","test"]'`` → ``["go","test"]``) via ``recover_stringified_argv``; a genuine
    command string runs as a NON-login ``sh -c`` one-liner — non-login so it inherits the
    bootstrapped PATH instead of a profile-reset PATH, matching run_command's toolchain
    resolution. A list/tuple becomes a trimmed argv. Empty / other type → ``None``."""
    if isinstance(check, str):
        text = check.strip()
        if not text:
            return None
        recovered = recover_stringified_argv(text)
        return recovered if recovered is not None else ["sh", "-c", text]
    if isinstance(check, (list, tuple)):
        argv = [str(part) for part in check if str(part or "").strip()]
        return argv or None
    return None


def shell_argv(raw_cmd: Any) -> List[str]:
    if isinstance(raw_cmd, list):
        return [str(x) for x in raw_cmd if str(x).strip()]
    try:
        return [str(x) for x in shlex.split(str(raw_cmd or "")) if str(x).strip()]
    except ValueError:
        return [str(x) for x in str(raw_cmd or "").split() if str(x).strip()]


def unwrap_env_argv(argv: List[str]) -> List[str]:
    if not argv or pathlib.PurePath(argv[0]).name.lower() != "env":
        return argv
    idx = 1
    options_with_arg = {"-u", "--unset", "-C", "--chdir", "--argv0"}
    while idx < len(argv):
        token = argv[idx]
        if token == "--":
            idx += 1
            break
        if token == "-S" and idx + 1 < len(argv):
            return shell_argv(argv[idx + 1])
        if token.startswith("--split-string="):
            return shell_argv(token.split("=", 1)[1])
        if token in options_with_arg:
            idx += 2
            continue
        if (
            any(token.startswith(prefix + "=") for prefix in ("--unset", "--chdir", "--argv0"))
            or token.startswith("-")
            or ("=" in token and not token.startswith("="))
        ):
            idx += 1
            continue
        break
    return argv[idx:] if idx < len(argv) else []


def env_chdir_operand(argv: List[str]) -> str:
    """Return an ``env`` wrapper's effective cwd operand, if one is present."""
    if not argv or pathlib.PurePath(argv[0]).name.lower() != "env":
        return ""
    index = 1
    while index < len(argv):
        token = str(argv[index])
        if token == "--":
            break
        if token in {"-C", "--chdir"}:
            return str(argv[index + 1]) if index + 1 < len(argv) else ""
        if token.startswith("--chdir="):
            return token.split("=", 1)[1]
        if token in {"-u", "--unset", "--argv0"}:
            index += 2
            continue
        if token == "-S" or token.startswith("--split-string="):
            break
        if token.startswith("-") or ("=" in token and not token.startswith("=")):
            index += 1
            continue
        break
    return ""


def interpreter_reads_program_from_stdin(argv: List[str]) -> bool:
    """Whether an interpreter argv has no inline body or script-file operand."""
    return bool(argv) and not any(
        str(token) != "-" and not str(token).startswith("-") for token in argv[1:]
    )


def sequential_effective_cwds(target_rows: list, initial_cwd: pathlib.Path) -> list[pathlib.Path]:
    """Symlink-resolved cwd in force at the start of each parsed command row."""
    current = pathlib.Path(initial_cwd).resolve(strict=False)
    result: list[pathlib.Path] = []
    for argv, _targets, _inline, _unknown in target_rows:
        result.append(current)
        head = pathlib.PurePath(str(argv[0])).name.lower() if argv else ""
        if head not in {"cd", "pushd"}:
            continue
        operand = next((str(item) for item in argv[1:] if str(item) and not str(item).startswith("-")), "")
        if operand:
            path = pathlib.Path(operand).expanduser()
            current = (path if path.is_absolute() else current / path).resolve(strict=False)
    return result


def directory_destination_child_name(command: str, argv: List[str], source: str) -> str:
    """Return the child path a directory destination receives from ``source``."""
    source_text = str(source or "").replace("\\", "/").rstrip("/")
    if command == "cp" and "--parents" in {str(item) for item in argv[1:]}:
        parts = [part for part in pathlib.PurePosixPath(source_text).parts if part not in {"", ".", "/"}]
        if not parts or ".." in parts:
            return ""
        return "/".join(parts)
    return pathlib.PurePath(source_text).name


def replacement_placeholders(argv: List[str]) -> frozenset[str]:
    """Replacement words are templates, never concrete filesystem targets."""
    executable = pathlib.PurePath(str(argv[0])).name.lower() if argv else ""
    placeholders = {"{}"} if executable in {"find", "xargs"} else set()
    if executable != "xargs":
        return frozenset(placeholders)
    index = 1
    while index < len(argv):
        token = str(argv[index])
        if token in {"-I", "--replace"} and index + 1 < len(argv):
            placeholders.add(str(argv[index + 1]))
            index += 2
            continue
        if token.startswith("--replace="):
            placeholders.add(token.split("=", 1)[1])
        elif token.startswith("-I") and len(token) > 2:
            placeholders.add(token[2:])
        index += 1
    return frozenset(item for item in placeholders if item)


def replacement_target_uncertain(
    argv: List[str], targets: List[str], *, write_shaped: bool,
) -> tuple[List[str], bool]:
    """Drop template-shaped targets and report when replacement hides a write."""
    placeholders = replacement_placeholders(argv)
    uncertain = any(
        placeholder in str(target)
        for target in targets for placeholder in placeholders
    )
    executable = pathlib.PurePath(str(argv[0])).name.lower() if argv else ""
    uncertain = uncertain or (
        executable == "xargs" and write_shaped
        and any(placeholder in str(token) for token in argv[1:] for placeholder in placeholders)
    )
    concrete = [
        target for target in targets
        if not any(placeholder in str(target) for placeholder in placeholders)
    ]
    return concrete, uncertain


def strip_leading_env_assignments(argv: List[str]) -> List[str]:
    idx = 0
    while idx < len(argv) and "=" in argv[idx] and not argv[idx].startswith("="):
        idx += 1
    return argv[idx:]


_SEGMENT_SEPARATORS = frozenset({";", ";;", "&&", "||", "|", "|&", "&", "(", ")", "\n"})
# The characters the punctuation lexer below treats as SYNTAX. Every one of them is also
# a legal byte inside an argument, so which role a given occurrence plays is decided by
# QUOTING — information ``shlex`` discards. ``_LITERAL_MARK`` carries it across the lexer.
_PUNCTUATION_CHARS = ";&|()<>"
# NUL is the one byte that can never reach a real command line (``execve`` rejects it),
# which makes it the safe marker for "this character came from inside quotes / an escape".
# A NUL already present in the raw text is doubled on entry, so the marking round-trips.
_LITERAL_MARK = "\x00"


def _normalize_shell_source(text: str) -> str:
    """Rewrite a command into the form the punctuation lexer can tokenize WITHOUT
    losing either of the two things it would otherwise destroy.

    Unquoted newlines and backtick command substitutions become ``;``. The shell treats
    an unquoted newline like ``;``; ``shlex.split`` instead folds it into surrounding
    whitespace, which let ``cmd1\\ncmd2`` masquerade as a single command and slip a glued
    ``git`` invocation past per-segment inspection. Backslash-newline line-continuations
    collapse to a space (also matching the shell); quoted newlines are preserved verbatim.
    Unquoted backticks (legacy command substitution `` `git -C <runtime> reset` ``) become
    ``;`` so the substituted command is its own segment and is inspected — ``$()`` is
    already split by the punctuation lexer, backticks are not. Single-quoted backticks
    stay literal.

    And in the OTHER direction: a punctuation character that is QUOTED (or backslash
    escaped) is a literal argument byte, not syntax, so it is emitted preceded by
    ``_LITERAL_MARK``. ``shlex`` strips quotes and hands back bare text, after which
    ``echo '&&' x`` and ``echo && x`` are the same token list — two DIFFERENT commands
    with one identity, which is a false-green path in ``_outcome_receipts``. The mark
    survives lexing inside its token, so ``shell_tokens_typed`` can still tell the two
    apart; it is stripped again before any token is returned.
    """
    out: List[str] = []
    quote: str | None = None
    i = 0
    text = text.replace(_LITERAL_MARK, _LITERAL_MARK * 2)
    n = len(text)

    def emit_literal(ch: str) -> None:
        if ch in _PUNCTUATION_CHARS:
            out.append(_LITERAL_MARK)
        out.append(ch)

    while i < n:
        c = text[i]
        if quote:
            emit_literal(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                emit_literal(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            out.append(c)
        elif c == "\\" and i + 1 < n and text[i + 1] == "\n":
            out.append(" ")
            i += 2
            continue
        elif c == "\\" and i + 1 < n and text[i + 1] in _PUNCTUATION_CHARS:
            # ``\&`` is a literal ampersand, not the operator — the shell's own rule.
            # OUTSIDE quotes the mark alone does not suffice: the lexer would still see
            # a bare punctuation character and split the token there, so the character is
            # also re-quoted (it can never be ``'`` itself) and the adjacent quoting is
            # concatenated back into one token exactly as the shell would.
            out.append(_LITERAL_MARK + "'" + text[i + 1] + "'")
            i += 2
            continue
        elif c == "\n":
            out.append(";")
        elif c == "`":
            out.append(";")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _unmark(token: str) -> str:
    """Drop the ``_LITERAL_MARK`` bytes ``_normalize_shell_source`` inserted, restoring
    the token exactly as the shell would pass it."""
    if _LITERAL_MARK not in token:
        return token
    out: List[str] = []
    i = 0
    while i < len(token):
        if token[i] == _LITERAL_MARK and i + 1 < len(token):
            out.append(token[i + 1])
            i += 2
            continue
        out.append(token[i])
        i += 1
    return "".join(out)


def shell_tokens_typed(raw_cmd: Any) -> List[tuple[str, bool]] | None:
    """Tokens of a shell command paired with whether each one is a CONTROL OPERATOR —
    real syntax — rather than a literal argument that merely spells like one.

    THE tokenizer of this module; ``shell_tokens`` and ``canonical_command_text`` are
    its two views. The flag is the piece ``shlex`` cannot give back: it strips quotes
    before yielding tokens, so ``echo '&&' x`` and ``echo && x`` arrive identical.
    ``_normalize_shell_source`` marks quoted/escaped punctuation on the way IN, this
    reads the mark, and the mark never leaves (tokens come back unmarked).

    A list is already tokenized, so every element is a literal argument: a caller
    passing argv cannot have glued an operator, and ``["a", "&&", "b"]`` really does
    run ``a`` with two arguments. Returns ``None`` when the text cannot be lexed
    (unbalanced quotes) so each caller picks its own fallback rather than inheriting a
    silent one.
    """
    if isinstance(raw_cmd, (list, tuple)):
        return [(str(x), False) for x in raw_cmd]
    text = _normalize_shell_source(str(raw_cmd or ""))
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=_PUNCTUATION_CHARS)
        lexer.whitespace_split = True
        raw = [t for t in lexer if t]
    except ValueError:
        return None
    return [
        (_unmark(t), _LITERAL_MARK not in t and all(ch in _PUNCTUATION_CHARS for ch in t))
        for t in raw
    ]


def shell_tokens(raw_cmd: Any) -> List[str] | None:
    """Operator-aware tokens of a shell command, control operators KEPT as their own
    tokens — the TEXT view of ``shell_tokens_typed``, shared by ``shell_segments``
    (which then drops the separators) and, indirectly, by every guard built on it.

    Robust against operators glued to adjacent words (``a;b``, ``a&&b``, ``$(cmd)``)
    and unquoted newlines — the cases plain ``shlex.split`` fuses into a single token.
    Quotes are respected, so whitespace INSIDE a quoted argument stays inside its token.
    A token that merely SPELLS like an operator is indistinguishable here by design:
    the guards that split on this view treat a quoted ``&&`` as a separator and so
    inspect MORE segments, which is the fail-safe direction. Callers that need the
    distinction (identity, not guarding) read ``shell_tokens_typed``.
    """
    typed = shell_tokens_typed(raw_cmd)
    return None if typed is None else [token for token, _ in typed]


def canonical_command_text(raw_cmd: Any) -> str:
    """The COMPARISON-STABLE form of a shell command: the same command written two
    cosmetically different ways yields the same text, and two DIFFERENT commands never
    do.

    Structural, not textual: the command is tokenized by ``shell_tokens_typed`` and
    rebuilt with exactly one space between tokens, so only the whitespace BETWEEN tokens
    is collapsed. Whitespace INSIDE a token is part of the argument and survives verbatim
    (re-quoted through ``shlex.quote``) — a flat ``" ".join(text.split())`` instead
    rewrites ``python -c "assert v == 'a  b'"`` into a command that asserts something
    else, and that mattered: this text is a verification's IDENTITY in
    ``_outcome_receipts``, so collapsing it let a green close an unrelated red.

    Nothing is dropped and nothing is re-classified. A token is rendered bare only when
    the lexer saw it as SYNTAX, so ``a && b`` and ``a '&&' b`` (which runs ``a`` with two
    arguments) canonicalize apart — round-5: the old form stripped leading/trailing
    separator-looking tokens AFTER ``shlex`` had discarded quoting, so
    ``shlex.join([..., "&&"])`` canonicalized to the same text as the argv WITHOUT that
    final argument, and a passing run of one could clear a failing run of the other. A
    trailing ``;`` or newline is likewise kept: it is a no-op to the shell, but proving
    that requires knowing it was syntax, and failing to equate two spellings of one
    command only ever leaves a red standing.

    Falls back to the merely STRIPPED raw text when the command cannot be lexed
    (unbalanced quotes): that can only fail to equate two spellings of one command,
    never equate two different ones.
    """
    typed = shell_tokens_typed(raw_cmd)
    if typed is None:
        return str(raw_cmd or "").strip()
    return " ".join(
        token if is_operator else shlex.quote(token) for token, is_operator in typed
    )


def shell_segments(raw_cmd: Any) -> List[List[str]]:
    """Split a shell command into per-command argv segments on control operators.

    Robust against operators glued to adjacent words (``a;b``, ``a&&b``,
    ``$(cmd)``) and unquoted newlines — the cases plain ``shlex.split`` fuses
    into a single token, which previously let ``cd ws;git -C <runtime> reset``
    masquerade as one ``cd`` segment with the ``-C`` selector never inspected.

    Lists are assumed already tokenized (a caller passing an argv list cannot
    glue operators) and are split on standalone separator tokens only.
    """
    tokens = shell_tokens(raw_cmd)
    if tokens is None:
        tokens = [t for t in str(raw_cmd or "").split() if t]
    segments: List[List[str]] = []
    current: List[str] = []
    for token in tokens:
        if token in _SEGMENT_SEPARATORS:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


_HEREDOC_START_RE = re.compile(
    r"<<-?\s*(?P<quote>['\"]?)(?P<delimiter>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)"
)


def shell_segment_rows(raw_cmd: Any) -> List[tuple[List[str], str, tuple[str, ...]]]:
    """Segments with their leading operator and any visible heredoc program body.

    Literal/static heredoc bodies are removed from shell tokenization and attached
    to the command that owns the redirection. This lets interpreter guards inspect
    stdin code without mistaking its lines for independent shell commands.
    """
    if not isinstance(raw_cmd, str) or "<<" not in raw_cmd:
        scrubbed = raw_cmd
        body_owners: list[tuple[int, str]] = []
    else:
        lines = raw_cmd.splitlines()
        scrubbed_lines = list(lines)
        pending: list[tuple[int, str]] = []
        index = 0
        while index < len(lines):
            match = _HEREDOC_START_RE.search(lines[index])
            delimiter = str(match.group("delimiter") or "") if match else ""
            if not delimiter:
                index += 1
                continue
            end = index + 1
            strip_tabs = match.group(0).startswith("<<-")
            while end < len(lines):
                candidate = lines[end].lstrip("\t") if strip_tabs else lines[end]
                if candidate == delimiter:
                    break
                end += 1
            if end >= len(lines):
                index += 1
                continue
            body = "\n".join(lines[index + 1:end])
            for body_index in range(index + 1, end + 1):
                scrubbed_lines[body_index] = ""
            owner_lines = [*scrubbed_lines[:index], lines[index][:match.end()]]
            owner_count = len(shell_segments("\n".join(owner_lines)))
            if owner_count:
                pending.append((owner_count - 1, body))
            index = end + 1
        scrubbed = "\n".join(scrubbed_lines)
        body_owners = pending

    tokens = shell_tokens(scrubbed)
    if tokens is None:
        tokens = [t for t in str(scrubbed or "").split() if t]
    rows: List[tuple[List[str], str, tuple[str, ...]]] = []
    current: List[str] = []
    leading = ""
    for token in tokens:
        if token in _SEGMENT_SEPARATORS:
            if current:
                rows.append((current, leading, ()))
                current = []
            leading = token
            continue
        current.append(token)
    if current:
        rows.append((current, leading, ()))
    for owner, body in body_owners:
        if 0 <= owner < len(rows):
            argv, operator, bodies = rows[owner]
            rows[owner] = (argv, operator, (*bodies, body))
    return rows


# The ONE redirection grammar of this module. A redirection operator is only an
# operator at the START of a token (after an optional file-descriptor prefix), so
# `s/>/x/` and `--pretty=<x>` never match, while both the glued (`2>/dev/null`)
# and the split (`2` `>` `/dev/null`) spellings the two lexers emit are covered.
_REDIRECT_OPERATOR_RE = re.compile(
    r"^(?P<fd>[0-9]+)?(?P<op><<<|<<|<&|<|&>>|&>|>>|>\||>&|>)(?P<operand>.*)$"
)


def split_redirections(tokens: List[str], *, redirect_tokens: List[str] | None = None) -> tuple[List[str], List[str]]:
    """Split redirections off a command segment.

    Returns ``(argv_without_redirections, output_targets)``. Input redirections
    (``<``, ``<<``, ``<<<``, ``<&``) and descriptor duplication/closure
    (``2>&1``, ``>&2``, ``>&-``) yield no target; ``/dev/null`` is exempted on
    the clean operand. Without this split the writer-target lane counted the
    redirect OPERAND as the command's own operand — `cp x y >> log.txt` reported
    `log.txt` and LOST the destination `y`, and a glued `2>/dev/null;` was forged
    into the path `/dev/null;`. ``redirect_tokens`` optionally receives the
    exact removed tokens, including input redirects, for local-effect views.
    """
    items = [str(token) for token in (tokens or [])]
    argv: List[str] = []
    targets: List[str] = []
    index = 0
    while index < len(items):
        start = index
        token = items[index]
        match = _REDIRECT_OPERATOR_RE.match(token)
        if match is None and token.isdigit() and index + 1 < len(items):
            # A bare descriptor token belongs to the operator that follows it.
            following = _REDIRECT_OPERATOR_RE.match(items[index + 1])
            if following is not None and not following.group("fd"):
                match = following
                index += 1
        if match is None:
            argv.append(token)
            index += 1
            continue
        operator = match.group("op")
        operand = match.group("operand")
        if not operand and index + 1 < len(items):
            index += 1
            operand = items[index]
        index += 1
        if redirect_tokens is not None:
            redirect_tokens.extend(items[start:index])
        if operator.startswith("<"):
            continue
        if operator == ">&" and (operand == "-" or operand.isdigit()):
            continue
        if not operand or operand == "/dev/null":
            continue
        targets.append(operand)
    return argv, targets


def local_shell_subject(raw_cmd: Any, _depth: int = 0) -> Any:
    """Local-effect view of SSH argv; execution always keeps the original input.

    OpenSSH sends command/arguments after destination to the remote host
    (https://man.openbsd.org/ssh). Its options, -E log sink, and every outer
    redirection remain local. Unknown option syntax retains the original view.
    Shell wrappers reuse the existing segment/redirection grammar; no remote
    command is parsed as a local program or assigned a local filesystem path.
    """
    if _depth >= 3:
        return raw_cmd
    changed = False
    result: List[str] = []
    for segment, leading, bodies in shell_segment_rows(raw_cmd):
        if bodies:
            return raw_cmd  # an unmodelled stdin program keeps its prior guard
        if result:
            result.append(leading or ";")
        argv = shell_argv(segment)
        head = pathlib.PurePath(argv[0]).name.lower().removesuffix(".exe") if argv else ""
        if head in _SHELLS:
            body = shell_command_string(argv)
            local = local_shell_subject(body, _depth + 1) if body else body
            if local != body:
                redirects: List[str] = []
                split_redirections(argv, redirect_tokens=redirects)
                result.extend([*shell_argv(local), *redirects])
                changed = True
                continue
        if head != "ssh":
            result.extend(argv)
            continue
        redirects = []
        program, _targets = split_redirections(argv, redirect_tokens=redirects)
        index = 1
        logs: List[str] = []
        valid = True
        while index < len(program) and program[index].startswith("-"):
            token = program[index]
            if token == "--":
                index += 1
                break
            letters = token[1:]
            if not letters or letters.startswith("-"):
                valid = False
                break
            for position, letter in enumerate(letters):
                if letter in "BbcDEeFIiJLlmOoPpQRSWw":
                    value = letters[position + 1:]
                    if not value:
                        index += 1
                        value = program[index] if index < len(program) else ""
                    if not value:
                        valid = False
                    if letter == "E" and value:
                        logs.extend([">", value])
                    break
                if letter not in "46AaCfGgKkMNnqsTtVvXxYyZ":
                    valid = False
                    break
            index += 1
        if not valid:
            return raw_cmd
        result.extend([*program[:index + 1], *logs, *redirects])
        changed = changed or index + 1 < len(program) or bool(logs)
    return result if changed else raw_cmd


def collect_leading_env(argv: List[str]) -> tuple[dict, List[str]]:
    """Peel leading environment assignments off a command segment.

    Handles both the bare ``VAR=val cmd`` form and the ``env VAR=val cmd``
    wrapper, returning ``(assignments, remaining_argv)``. git honours
    ``GIT_DIR`` / ``GIT_WORK_TREE`` from the environment over cwd/``-C``, so
    guards must inspect these rather than discard them.
    """
    assignments: dict = {}
    rest = unwrap_env_argv(list(argv))
    # unwrap_env_argv drops the ``env`` wrapper but also its inline VAR=val
    # tokens; recover those from the original argv when an env wrapper was used.
    if argv and pathlib.PurePath(argv[0]).name.lower() == "env":
        for token in argv[1:]:
            if token == "--":
                break
            if token.startswith("-"):
                continue
            if "=" in token and not token.startswith("="):
                key, _, value = token.partition("=")
                assignments[key] = value
            else:
                break
    idx = 0
    while idx < len(rest) and "=" in rest[idx] and not rest[idx].startswith("="):
        key, _, value = rest[idx].partition("=")
        assignments[key] = value
        idx += 1
    return assignments, rest[idx:]


# Heads that only forward to the command that follows them: sudo behind one of
# these is still an invocation. A wrapper flag that consumes a VALUE token
# (``nice -n 10 sudo ...``) hides the wrapped head — a disclosed residual that
# can only miss a hang, never refuse work (this is a hang guard, not a
# privilege boundary).
_SUDO_FORWARDING_WRAPPERS = frozenset({
    "command", "builtin", "exec", "nohup", "time", "nice", "ionice", "stdbuf",
    "setsid", "timeout",
})


def sudo_noninteractive_violation(raw_cmd: Any) -> bool:
    """True when the command actually INVOKES interactive sudo/sudoedit.

    Judged by COMMAND POSITION per segment (the head after env/wrapper peeling),
    never by token mention: ``rg sudo README.md`` or ``ls /usr/bin/sudo`` name
    sudo as DATA and invoke nothing (issue #447 A3). Interactive sudo hangs
    forever on a password prompt in the headless runtime; ``sudo -n`` is fine,
    ``-S`` (password on stdin) is refused outright as before.
    """
    for segment in shell_segments(raw_cmd):
        _env, command = collect_leading_env(segment)
        while command:
            head = pathlib.PurePath(str(command[0])).name.lower()
            if head in _SHELLS:
                inline = shell_command_string(command)
                if inline and sudo_noninteractive_violation(inline):
                    return True
                break
            if head == "sudoedit":
                return True
            if head == "sudo":
                has_noninteractive = False
                for option in _sudo_option_tokens(command[1:]):
                    if option == "-S" or (option.startswith("-") and not option.startswith("--") and "S" in option[1:]):
                        return True
                    if option == "-n" or (option.startswith("-") and not option.startswith("--") and "n" in option[1:]):
                        has_noninteractive = True
                    if option.startswith("--non-interactive"):
                        has_noninteractive = True
                if not has_noninteractive:
                    return True
                # `sudo -n sh -c "sudo ..."` — keep walking the wrapped command.
                command = _sudo_wrapped_command(command[1:])
                continue
            if head in _SUDO_FORWARDING_WRAPPERS:
                command = command[1:]
                while command and str(command[0]).startswith("-"):
                    command = command[1:]
                continue
            break
    return False


def shell_command_string(argv: List[str]) -> str:
    for idx, arg in enumerate(argv[1:], start=1):
        if arg == "-c" or (arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]):
            return argv[idx + 1] if idx + 1 < len(argv) else ""
    return ""


def shell_argv_with_inline(raw_cmd: Any) -> List[str]:
    argv = shell_argv(raw_cmd)
    if argv and pathlib.PurePath(argv[0]).name.lower() in _SHELLS:
        inline = shell_command_string(argv)
        if inline:
            return argv + shell_argv(inline)
    return argv


def slash_normalize_path_text(text: Any) -> str:
    value = str(text or "").replace("\\", "/")
    while "//" in value:
        value = value.replace("//", "/")
    return value


def is_absolute_path_text(text: Any) -> bool:
    value = str(text or "")
    return (
        value.startswith("/")
        or bool(re.match(r"^[A-Za-z]:[\\/]", value))
        or value.startswith("\\\\")
    )


def path_text_is_inside(candidate: Any, root: Any) -> bool:
    candidate_text = slash_normalize_path_text(candidate).rstrip("/")
    root_text = slash_normalize_path_text(root).rstrip("/")
    if not candidate_text or not root_text:
        return False
    candidate_key = candidate_text.casefold()
    root_key = root_text.casefold()
    return candidate_key == root_key or candidate_key.startswith(root_key + "/")


def shell_argv_with_path_tokens(raw_cmd: Any) -> List[str]:
    tokens = list(shell_argv_with_inline(raw_cmd))
    raw_texts = [" ".join(str(x) for x in raw_cmd)] if isinstance(raw_cmd, list) else [str(raw_cmd or "")]
    seen = {str(token) for token in tokens}

    def add_token(value: str) -> None:
        if value and value not in seen:
            tokens.append(value)
            seen.add(value)

    for text in [*raw_texts, *[str(token) for token in tokens]]:
        for match in embedded_absolute_path_tokens(text):
            add_token(match)
        for match in EMBEDDED_WINDOWS_ABSOLUTE_PATH_RE.findall(text):
            add_token(match)
    return tokens


def embedded_absolute_path_tokens(text: Any) -> List[str]:
    """Extract POSIX absolute paths while ignoring HTML closing-tag fragments."""

    raw = str(text or "")
    tokens: List[str] = []
    for match in EMBEDDED_ABSOLUTE_PATH_RE.finditer(raw):
        value = match.group(0)
        if match.start() > 0 and raw[match.start() - 1] == "<" and _HTML_CLOSING_TAG_PATH_RE.fullmatch(value):
            continue
        tokens.append(value)
    return tokens


# -A/--askpass and -b/--background are FLAGS (no argument): listing them here
# made the walker eat the wrapped command's head as an "option value".
_SUDO_OPTIONS_WITH_ARG = frozenset({
    "-a", "-C", "-c", "-D", "-g", "-h", "-p", "-R", "-r", "-T", "-t", "-U", "-u",
    "--auth-type", "--chdir", "--close-from", "--command-timeout",
    "--context", "--group", "--host", "--login-class", "--prompt", "--role", "--type", "--user",
    "--other-user",
})


def _sudo_option_tokens(rest: List[str]) -> List[str]:
    options: List[str] = []
    idx = 0
    while idx < len(rest):
        token = rest[idx]
        if token == "--":
            break
        if not token.startswith("-") or token == "-":
            break
        options.append(token)
        idx += 2 if token in _SUDO_OPTIONS_WITH_ARG else 1
    return options


def _sudo_wrapped_command(rest: List[str]) -> List[str]:
    """The command sudo runs: its own options and leading VAR=val tokens peeled."""
    idx = 0
    while idx < len(rest):
        token = rest[idx]
        if token == "--":
            idx += 1
            break
        if not token.startswith("-") or token == "-":
            break
        idx += 2 if token in _SUDO_OPTIONS_WITH_ARG else 1
    return strip_leading_env_assignments(rest[idx:])


# --- run_command argv validation (moved from ouroboros/tools/shell.py, --------
# ibl-978e5cd9258f — that module was over the size-ratchet GIANT_PATHS cap).
# Pure argv/text checks, no ToolContext dependency, so this is a plain
# top-level move with no circular-import concern; ouroboros/tools/shell.py
# re-exports both functions and all seven constants below.

_SHELL_BUILTINS = frozenset([
    "cd", "source", ".", "export", "alias", "eval",
    "set", "unset", "pushd", "popd", "read", "ulimit",
])

_SHELL_OPERATORS = frozenset(["&&", "||", "|", ";", ">", ">>", "<", "<<"])
# A redirect GLUED into a single argv element ("2>/dev/null", "2>&1", ">out.log",
# "&>x") — the standalone-operator set above misses these. Anchored at the element
# START so a '>' inside a sed/awk/grep expression ("s/a>b/c/g") is NOT flagged.
# Output redirects keep a permissive glued tail. Input redirects are restricted to
# UNAMBIGUOUS shapes — heredoc/herestring ("<<EOF", "<<<s"), an fd-prefixed input
# ("0<f", "2<&1"), or a bare standalone "<" — because a plain "<word" element is
# indistinguishable from a legitimate literal angle-bracket arg (grep "<div>",
# "<stdin>"), and false-flagging those is worse than missing a rare glued "<file
# input redirect. Pipes/control operators are deliberately NOT matched (a glued
# '|' is valid regex alternation, grep "a|b").
_GLUED_REDIRECT_RE = re.compile(
    r'^(?:(?:\d+>>?|>>?&?\d*|\d*>&\d*|&>>?)(?:\S.*)?|\d+<\S*|<<\S*|<)$'
)
# Detect shell pipelines STUFFED into a single argv element (e.g.
# `["curl && -s && https://api..."]`). Signature: whitespace-bracketed `&&` or
# `||` inside one element, with non-whitespace on both sides. The whole string
# reaches subprocess as the executable name and dies with a silent
# `[Errno 2] No such file or directory`. Narrow to `&&`/`||` only because (a)
# a bare `|` element is already caught by `_SHELL_OPERATORS.intersection` and
# (b) `|` inside a regex like `grep "a|b"` is legitimate alternation. The
# check is gated by `_SHELL_INTERPRETERS` so `["sh", "-c", "a && b"]` scripts
# pass through (env-ref check at the same cascade location carries the same
# exemption boundary).
_EMBEDDED_SHELL_OP_RE = re.compile(r'\s(?:&&|\|\|)\s')
# A cmd list of length 1 has no "other arguments" for an operator-looking
# substring to be legitimate content of (the false-positive concern that
# keeps `|` and the >=1 threshold OUT of the multi-arg check above: a value
# argument like a commit message or grep pattern can legitimately contain
# "&&"/"|" text when it sits ALONGSIDE other argv elements). When the WHOLE
# cmd is one string, that string IS what subprocess treats as the executable
# name — so ANY shell metacharacter inside it (single "&&"/"||"/"|", or ";")
# means the caller passed a full pipeline as one un-split argv element
# instead of splitting it or wrapping ["sh", "-c", ...]. This is the
# single-most-recurring production failure shape (11 backlog nominations,
# ibl-5aa29f06571d through ibl-4ecff817c661, 2026-08-11..08-15): the >=2
# stuffed-pipeline check below only fires on TWO OR MORE operators glued into
# one element, so a lone "&&"/"|" (arguably the more common typo) passed
# through silently as a raw [Errno 2] with no actionable guidance.
_SINGLE_ARG_SHELL_META_RE = re.compile(r'\s(?:&&|\|\|)\s|(?<!\|)\|(?!\|)|;')
_SHELL_INTERPRETERS = frozenset({"sh", "bash", "zsh", "fish", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"})
_ENV_REF_PATTERN = re.compile(r'\$(?:\{[A-Z][A-Z0-9_]*\}|[A-Z][A-Z0-9_]*)')


def normalize_run_command_argv(cmd: Any) -> tuple[List[str] | None, str]:
    """Normalize a ``run_command`` ``cmd`` argument into an argv list.

    Extracted from ``ouroboros.tools.shell._run_shell`` (ibl-978e5cd9258f —
    that function was over the size-ratchet 300-line FUNCTION_DEBT cap).
    Handles the stringified-argv recovery path, the autocorrect chain
    (grep backslash-pipe, single-element `&&` chains, single-element
    pipelines), and validation — everything up to but not including the
    ctx-dependent cwd/binding resolution that stays in ``_run_shell``.

    Returns ``(argv, accumulated_note)`` on success (``argv`` is the
    normalized list, ``note`` carries autocorrect/literal-argv disclosures
    to prepend to the eventual result), or ``(None, error_text)`` where
    ``error_text`` is a ready-to-return ``⚠️``-prefixed message.
    """
    # Deferred imports: the autocorrect helpers live in ouroboros.tools.*,
    # which import THIS module (shell_parse) at their own top level — a
    # module-level import here would cycle.
    from ouroboros.tools.shell_and_chain import (
        _maybe_split_single_element_and_chain,
        _maybe_wrap_single_element_pipeline,
    )
    from ouroboros.tools.shell_grep_argv import _maybe_autocorrect_grep_backslash_pipe

    if isinstance(cmd, str):
        # Shared recovery keeps run_command and verify argv semantics aligned.
        recovered = recover_stringified_argv(cmd)
        # Malformed structured literals are not shell commands; refuse explicitly.
        if recovered is None:
            stripped = cmd.lstrip()
            is_posix_test_cmd = stripped.startswith("[ ") and stripped.rstrip().endswith(" ]")
            # A `{ ...; }` brace group is valid shell, not malformed JSON.
            is_brace_group = stripped.startswith("{ ") and stripped.rstrip().endswith("}")
            if is_brace_group:
                return None, (
                    '⚠️ SHELL_CMD_ERROR: `{ ...; }` is a shell brace group, which run_command '
                    'cannot execute directly (it runs argv without a shell). Wrap it in a shell:\n'
                    '  run_command(cmd=["sh", "-c", "{ cmd1; cmd2; }"])'
                )
            if stripped[:1] in ("[", "{") and not is_posix_test_cmd:
                return None, (
                    '⚠️ SHELL_ARG_ERROR: `cmd` looks like a JSON/Python list literal '
                    'but failed to parse cleanly (likely an escape or quote-mismatch '
                    'issue). Pass cmd as an actual array, not a stringified array.\n\n'
                    'Correct usage:\n'
                    '  run_command(cmd=["git", "log", "--oneline", "-10"])\n\n'
                    'Wrong usage (the failure that brought you here):\n'
                    '  run_command(cmd=\'["git", "log", "--oneline", "-10"]\')\n\n'
                    'For reading files, prefer `read_file`.\n'
                    'For searching code, prefer `search_code`.'
                )
            try:
                parts = shlex.split(cmd)
                if parts:
                    recovered = parts
            except ValueError:
                pass
        if recovered is not None:
            cmd = recovered
        else:
            return None, (
                '⚠️ SHELL_ARG_ERROR: `cmd` must be a JSON array of strings, not a plain string.\n\n'
                'Correct usage:\n'
                '  run_command(cmd=["grep", "-r", "pattern", "path/"])\n'
                '  run_command(cmd=["python", "-c", "print(1+1)"])\n\n'
                'Wrong usage:\n'
                '  run_command(cmd="grep -r pattern path/")\n\n'
                'For reading files, prefer `read_file`.\n'
                'For searching code, prefer `search_code`.'
            )

    if not isinstance(cmd, list):
        return None, "⚠️ SHELL_ARG_ERROR: cmd must be a list of strings."
    cmd = [str(x) for x in cmd]

    cmd, autocorrect_note = _maybe_autocorrect_grep_backslash_pipe(cmd)
    cmd, and_chain_note = _maybe_split_single_element_and_chain(cmd)
    if and_chain_note:
        autocorrect_note = (autocorrect_note + and_chain_note) if autocorrect_note else and_chain_note
    cmd, pipeline_note = _maybe_wrap_single_element_pipeline(cmd)
    if pipeline_note:
        autocorrect_note = (autocorrect_note + pipeline_note) if autocorrect_note else pipeline_note
    err = _validate_shell_argv(cmd)
    if err:
        return None, err
    # #447 A5: env-ref / glued-redirect shapes that used to be refused are now
    # disclosed as literal pass-throughs — the command still runs.
    autocorrect_note += _literal_argv_notes(cmd)
    return cmd, autocorrect_note


def _validate_shell_argv(cmd: List[str]) -> str:
    """Cascade validation of a shell argv after autocorrect.

    Returns an empty string when valid, or the SHELL_*_ERROR message that
    should replace the run. Pulled out of _run_shell (cycle #3 cleanup) to
    keep that function under the 300-line test gate while keeping every
    check at the same cascade location and gating the _SHELL_INTERPRETERS
    exemption boundary. Autocorrect (grep backslash-pipe) is intentionally
    NOT in here — callers run it first so this helper validates what
    actually gets executed.

    Cascade order (the still-refused subset; env-ref and glued-redirect
    shapes are now DISCLOSED by ``_literal_argv_notes`` rather than refused —
    #447 A5 — so those two steps were removed here):
      2. shell-builtin check (cd, source, ., etc. — refuse with cwd hint
         for cd and a generic sh -c hint for the rest)
      3. standalone shell-operator check (`_SHELL_OPERATORS.intersection`)
      5. single-element metacharacter check (v6.101.0 fix: a lone `&&`/`||`/
         `|`/`;` when cmd has EXACTLY ONE element — the whole cmd is then
         necessarily one full pipeline mistakenly passed as an un-split
         argv string, so the false-positive concern that keeps this out of
         the multi-arg checks (a value argument legitimately containing
         operator-looking text) cannot apply).
      6. embedded-op check (the cycle #1 fix: 2+ whitespace-bracketed
         `&&`/`||` inside one argv element among possibly several — the
         multi-arg production failure shape).
    """
    executable_name = pathlib.Path(cmd[0]).name.lower() if cmd else ""

    if cmd and cmd[0] in _SHELL_BUILTINS:
        if cmd[0] == "cd":
            return (
                '⚠️ SHELL_CMD_ERROR: "cd" is a shell builtin, not an executable. '
                'Use the "cwd" parameter instead: '
                'run_command(cmd=["git", "log"], cwd="/target/dir")'
            )
        return (
            f'⚠️ SHELL_CMD_ERROR: "{cmd[0]}" is a shell builtin and cannot '
            'be executed directly via subprocess. '
            'Use ["sh", "-c", "your command"] if you need shell builtins.'
        )

    found_ops = _SHELL_OPERATORS.intersection(cmd)
    if found_ops:
        op = sorted(found_ops)[0]
        return (
            f'⚠️ SHELL_CMD_ERROR: Shell operator "{op}" found in cmd array. '
            'Subprocess does not interpret shell syntax. '
            'Options: (1) Split into separate run_command calls. '
            '(2) For pipes/chaining: ["sh", "-c", "cmd1 && cmd2"]'
        )

    # (Glued-redirect refusal removed — #447 A5: a "2>/dev/null" element is
    # literal data to subprocess and is now DISCLOSED by _literal_argv_notes,
    # so the command runs and the [sh,-c,...] hint rides along in the result.)

    # cmd has exactly ONE element: that element is not "one argument among
    # several" (where operator-looking text can be legitimate content, e.g.
    # a commit message `["git", "commit", "-m", "step1 && step2 done"]`) —
    # it IS the entire cmd, and subprocess treats it as the executable NAME.
    # A real single-token executable name never legitimately contains a
    # whitespace-bracketed "&&"/"||", a bare "|", or a ";" — so any of those
    # here means the caller passed a whole pipeline as one un-split argv
    # string instead of splitting it or wrapping ["sh", "-c", ...]. Gated by
    # _SHELL_INTERPRETERS so a bare `["bash"]` (interactive, no -c) is not
    # flagged. This is scoped tighter than the >=2 check below specifically
    # so it can safely use a >=1 threshold and include the bare "|" that the
    # multi-arg check below deliberately excludes (see its comment).
    if len(cmd) == 1 and executable_name not in _SHELL_INTERPRETERS:
        single = cmd[0]
        if _SINGLE_ARG_SHELL_META_RE.search(single):
            preview = single if len(single) <= 80 else single[:77] + "..."
            return (
                f'⚠️ SHELL_CMD_ERROR: Shell syntax found in a single-element cmd: "{preview}". '
                'A one-element cmd is treated as a literal executable NAME by subprocess, '
                'not as a shell pipeline, so this fails with [Errno 2] No such file or '
                'directory. Fix: (1) Split into separate argv elements, one per '
                'run_command call if the operator was meant to chain commands; '
                '(2) Wrap the pipeline: ["sh", "-c", "your command here"].'
            )

    # A shell pipeline STUFFED into a single argv element (e.g.
    # `["curl && -s && https://api.example.com/x"]`) — the standalone-operator
    # check above only catches `"&&"` as its own element, and the glued-redirect
    # check only matches redirect-shaped prefixes. The whole string is then
    # passed to subprocess as the executable name and dies with a silent
    # `[Errno 2] No such file or directory`. Gated by `_SHELL_INTERPRETERS` so
    # `["sh", "-c", "echo a && echo b"]` (a legitimate shell script) passes
    # through. Narrowed to whitespace-bracketed `&&`/`||` (no `|`) so
    # `grep "a|b"` regex alternation is not over-flagged.
    if executable_name not in _SHELL_INTERPRETERS:
        for idx, arg in enumerate(cmd):
            op_matches = _EMBEDDED_SHELL_OP_RE.findall(arg)
            if len(op_matches) >= 2:
                preview = arg if len(arg) <= 80 else arg[:77] + "..."
                return (
                    f'⚠️ SHELL_CMD_ERROR: Shell pipeline stuffed into cmd[{idx}]: "{preview}". '
                    'Two or more `&&`/`||` operators inside one argv element mean '
                    'the OS treats the WHOLE string as the executable name, producing '
                    'silent `[Errno 2] No such file or directory` failures. '
                    'Fix: (1) Split into separate run_command calls; '
                    '(2) Wrap the pipeline: ["sh", "-c", "cmd1 && cmd2"].'
                )

    return ""


def _literal_argv_notes(cmd: List[str]) -> str:
    """Disclosure notes for shell-syntax-looking bytes in direct argv (#447 A5).

    A commit message naming ``$HOME``, an awk ``|`` field separator, a
    ``2>/dev/null`` element — no shell runs for direct argv, so these are
    LITERAL DATA carrying no authority question. They used to be REFUSED as
    errors, blocking commands that would have worked; the in-file autocorrect
    precedent applies instead: run the command and DISCLOSE what was passed
    literally, so a genuinely mistaken spelling still explains its own cryptic
    program error.
    """
    notes: list[str] = []
    executable_name = pathlib.Path(cmd[0]).name.lower() if cmd else ""
    if executable_name not in _SHELL_INTERPRETERS:
        for arg in cmd:
            match = _ENV_REF_PATTERN.search(arg)
            if match:
                notes.append(
                    f'⚠️ SHELL_LITERAL_ARGV_NOTE: literal env reference "{match.group(0)}" in the cmd '
                    "array reached the program UNEXPANDED (run_command executes argv directly). "
                    'Use ["sh", "-c", "..."] if you intended shell expansion.\n'
                )
                break
    if found_ops := _SHELL_OPERATORS.intersection(cmd):
        notes.append(
            f'⚠️ SHELL_LITERAL_ARGV_NOTE: shell operator "{sorted(found_ops)[0]}" in the cmd array was '
            "passed to the program as a LITERAL argument (subprocess interprets no shell syntax). "
            'Use ["sh", "-c", "cmd1 && cmd2"] for pipes/chaining.\n'
        )
    # Glued redirects bypass the standalone-operator set but remain shell-looking.
    for arg in cmd:
        if _GLUED_REDIRECT_RE.match(arg):
            notes.append(
                f'⚠️ SHELL_LITERAL_ARGV_NOTE: redirect-looking argument "{arg}" in the cmd array was '
                "passed to the program as a LITERAL argument (subprocess interprets no shell "
                'syntax). Use ["sh", "-c", "..."] for real redirection.\n'
            )
            break
    return "".join(notes)
