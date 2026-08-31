"""One-element ``&&``-chain argv autocorrect — split out of shell.py.

Pure argv/string logic, no ToolContext dependency. Mirrors the
``shell_grep_argv`` module's split-out shape (v6.109.x) so the body of
``shell.py`` stays under the module-size gate.

The bug this closes (ibl-21e0a036155d / ibl-51106385d9f1 / ibl-571ce5f8eec6 /
ibl-78f783d0ac6a — one class):

    run_command(cmd=["git && status && --porcelain"])

The whole string is treated as ONE executable name by subprocess, and the
single-element guard (``SHELL_CMD_ERROR``) refuses the call. Today the model
loops on that refusal because the surface error message points at "argv"
but not at the specific ``&&``-between-single-tokens typo this catches.

The autocorrect is intentionally narrow: it only fires when the single
string is a clear argv-boundary mistake (one-token segments, no other
shell metacharacter). Anything else — segments with spaces (``cd foo &&
make``), other metachars (``echo $HOME && ls``) — falls through to the
existing ``SHELL_CMD_ERROR`` path untouched.
"""

from __future__ import annotations

import re
from typing import List, Tuple

# Whitespace-bracketed ``&&``; matches ``a && b``, ``a  &&  b`` etc.
_AND_CHAIN_PATTERN = re.compile(r"\s+&&\s+")
_OTHER_METACHAR_RE = re.compile(r"""[|;<>&`"'$()]""")

# Whitespace-bracketed pipe that is NOT part of ``||`` — matches ``a | b``.
# Left/right ``(?<!\\|)`` / ``(?!\\|)`` guards exclude the ``||`` operator, and
# the literal surrounding spaces mean glued forms like ``grep "a|b"`` (regex
# alternation) are NOT matched. The leading-interpreter check below
# separately skips ``[\"sh -c 'a | b'\"]`` style argv where wrapping is
# wrong.
_PIPE_PATTERN = re.compile(r'(?<!\|) \| (?!\|)')
# First whitespace token of a one-element cmd is treated as the executable
# name. A leading ``sh`` / ``bash`` / ``zsh`` / ``fish`` / ``cmd`` /
# ``powershell`` / ``pwsh`` means the argv is a one-string form of the
# legitimate shell-script path — wrap would still work but the existing
# interpreter path already handles it and we should not duplicate the
# disclosure.
_SHELL_INTERPRETERS = frozenset(("sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh"))


def _maybe_split_single_element_and_chain(cmd: List[str]) -> Tuple[List[str], str]:
    """Rewrap a single-element ``&&`` argv mistake into real argv tokens.

    Returns ``(cmd, "")`` when nothing to do (multi-element, no ``&&``,
    segments aren't single tokens, or other shell metachar is present),
    and ``(rewritten, disclosure_note)`` otherwise.

    The autocorrect only fires when ALL of:
      * ``len(cmd) == 1`` and ``cmd[0]`` is a ``str``,
      * the literal substring ``" && "`` appears in ``cmd[0]``,
      * splitting on ``\\s+&&\\s+`` yields >= 2 non-empty segments,
      * every segment is a single whitespace-free token
        (``len(seg.split()) == 1``),
      * the string carries NO OTHER shell metacharacter
        (``[|;<>&`"'$()]``) once the ``&&`` occurrences are stripped.

    A disclosure note of the form
    ``⚠️ SHELL_CMD_AUTO_SPLIT: one-element cmd cannot encode a pipeline ...``
    is appended so the operator sees exactly what was rewritten.
    """
    if not cmd or len(cmd) != 1 or not isinstance(cmd[0], str):
        return cmd, ""
    raw = cmd[0]
    if " && " not in raw:
        return cmd, ""
    segments = _AND_CHAIN_PATTERN.split(raw)
    if len(segments) < 2:
        return cmd, ""
    stripped = [seg.strip() for seg in segments]
    if any(not seg for seg in stripped):
        return cmd, ""
    if any(len(seg.split()) != 1 for seg in stripped):
        return cmd, ""
    remainder = _AND_CHAIN_PATTERN.sub("", raw)
    if _OTHER_METACHAR_RE.search(remainder):
        return cmd, ""
    return stripped, (
        "⚠️ SHELL_CMD_AUTO_SPLIT: one-element cmd cannot encode a pipeline and `&&` "
        f"between single tokens is an argv-boundary mistake — split <{raw}> into "
        f"argv {stripped!r}. For a real chain use separate run_command calls or "
        f"[\"sh\",\"-c\",\"a && b\"].\n"
    )


def _maybe_wrap_single_element_pipeline(cmd: List[str]) -> Tuple[List[str], str]:
    """Rewrap a single-element piped argv into ``[\"sh\", \"-c\", <cmd>]``.

    Returns ``(cmd, \"\")`` when nothing to do, and
    ``([\"sh\", \"-c\", cmd[0]], disclosure_note)`` when the autocorrect fires.

    The autocorrect only fires when ALL of:
      * ``len(cmd) == 1`` and ``cmd[0]`` is a ``str`` (multi-element
        argv is left for the existing ``_SHELL_OPERATORS`` /
        ``_EMBEDDED_SHELL_OP`` checks; the ``&&`` chain split helper
        runs first and a multi-element post-split still falls through
        below),
      * ``_PIPE_PATTERN.search(cmd[0])`` finds at least one
        whitespace-bracketed ``|`` that is not part of ``||``
        (``grep \"a|b\"`` has no surrounding whitespace and does NOT
        match; ``a||b`` is excluded by the lookbehind/lookahead),
      * the first whitespace token of ``cmd[0]`` is NOT one of
        ``sh`` / ``bash`` / ``zsh`` / ``fish`` / ``cmd`` /
        ``powershell`` / ``pwsh`` (a ``[\"sh -c 'a | b'\"]`` argv is a
        one-string form of the legitimate shell-script path — leave
        it for the interpreter branch).

    The disclosure note formats as
    ``⚠️ SHELL_CMD_AUTO_WRAP: a one-element cmd with a shell pipe was wrapped as [\"sh\",\"-c\",<cmd>] ...``
    so the operator sees exactly what was rewritten.
    """
    if not cmd or len(cmd) != 1 or not isinstance(cmd[0], str):
        return cmd, ""
    raw = cmd[0]
    if not _PIPE_PATTERN.search(raw):
        return cmd, ""
    first_token = raw.split(maxsplit=1)[0] if raw.split() else ""
    # Strip an optional leading path so ``/bin/sh -c 'a | b'`` reads as ``sh``.
    first_token = first_token.rsplit("/", 1)[-1].lower() if first_token else ""
    if first_token in _SHELL_INTERPRETERS:
        return cmd, ""
    return ["sh", "-c", raw], (
        "⚠️ SHELL_CMD_AUTO_WRAP: a one-element cmd with a shell pipe was wrapped as "
        f"[\"sh\",\"-c\",<{raw}>] — subprocess does not interpret \"|\". Pass "
        "[\"sh\",\"-c\",\"a | b\"] yourself, or split into sequential run_command calls, "
        "to avoid this rewrite.\n"
    )
