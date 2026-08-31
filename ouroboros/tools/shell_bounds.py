"""run_script input bounds (leaf SSOT; extracted at shell.py's module-size gate).

The schema advertises these caps to the model and the handler enforces them
(the registry enforces no JSON-schema bounds; in light/off safety mode no LLM
check runs, so the handler checks are the only bound there). Larger payloads
stage via write_file. These are PER-FIELD caps, complementary to safety.py's
TOTAL serialized-subject budget (_SAFETY_SUBJECT_CHAR_BUDGET, 250k), which a
field-maxed call can still exceed and is then refused typed at the LLM-check
seam.
"""

from __future__ import annotations

from typing import List, Optional

RUN_SCRIPT_MAX_SCRIPT_CHARS = 200_000
RUN_SCRIPT_MAX_ARG_CHARS = 10_000
RUN_SCRIPT_MAX_ARGS_COUNT = 100


def oversized_run_script_input(body: str, args: Optional[List[str]]) -> str:
    """Typed refusal text for an over-cap run_script input, or '' when in bounds."""
    problem = ""
    if len(body) > RUN_SCRIPT_MAX_SCRIPT_CHARS:
        problem = (f"script is {len(body):,} characters, above the "
                   f"{RUN_SCRIPT_MAX_SCRIPT_CHARS:,}-character limit")
    elif len(args or []) > RUN_SCRIPT_MAX_ARGS_COUNT:
        problem = f"{len(args or [])} args, above the {RUN_SCRIPT_MAX_ARGS_COUNT}-argument limit"
    else:
        for idx, arg in enumerate(args or []):
            if len(str(arg)) > RUN_SCRIPT_MAX_ARG_CHARS:
                problem = (f"args[{idx}] is {len(str(arg)):,} characters, above the "
                           f"{RUN_SCRIPT_MAX_ARG_CHARS:,}-character per-argument limit")
                break
    if not problem:
        return ""
    return (f"⚠️ TOOL_ARG_ERROR (run_script): {problem}. Stage bulk content with "
            "write_file and run the staged file instead.")
