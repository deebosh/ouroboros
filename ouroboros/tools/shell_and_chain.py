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
