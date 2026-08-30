"""The immutable first-party tool descriptor (ToolEntry).

Every span is extracted VERBATIM from the parent's tip bytes by
scripts/v7next_transplant.py (D18/D33 module-handle split, proof-checked);
the parent re-exports every moved name, so historical imports and
monkeypatch targets keep working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation-only imports (inert at runtime)
    from typing import Any
    from typing import Callable
    from typing import Dict


def _registry():
    """The parent module, read at call time.

    The parent owns the rebindable module state and the members tests
    monkeypatch there; reading them through the module at each call keeps
    one binding, where a from-import would freeze the value this leaf saw
    at import time (the owner-approved D18/D33 mechanical exception).
    """
    from ouroboros.tools import registry

    return registry


@dataclass
class ToolEntry:
    """Single tool descriptor."""

    name: str
    schema: Dict[str, Any]
    handler: Callable  # fn(ctx: ToolContext, **args) -> str
    is_code_tool: bool = False
    timeout_sec: int = 360
    # Capability flag: tool can mutate the live repo worktree. The dispatcher
    # snapshots `git status --porcelain` around flagged tools and invalidates
    # advisory freshness when the worktree ACTUALLY changed — covering error
    # and timeout paths uniformly, and never invalidating for read-only runs.
    mutates_worktree: bool = False
    # Compatibility alias: a renamed tool's old public name. An alias entry is
    # CALLABLE (execute dispatches it like any entry) but never advertised —
    # schemas()/available_tools() skip it, so the public surface carries only
    # the canonical name while saved prompts, memories, and configs that still
    # use the old spelling keep working.
    alias_for: str = ""
