"""Context-assembly helpers for the background consciousness loop.

Extracted from ``ouroboros.consciousness`` so that module stays under the
size-ratchet giant threshold after the v6.110.0 merge. These are the pure
pieces of the graceful-degradation context builder (``ibl-local-31f19191be34``):
the typed overflow, the section labeller, the drop-priority assembler, and the
low-mode drive-state / identity bounds.
"""

from __future__ import annotations

import logging
from typing import Any, List, Tuple

from ouroboros.context_budget import BG_STATE_JSON_WARN_CHARS

log = logging.getLogger(__name__)


class _ConsciousnessOverflow(OverflowError):
    """OverflowError carrying per-section diagnostics for consciousness context.

    The bare ``OverflowError`` raised from ``_build_context`` cannot name which
    sections crossed the ``BG_CONTEXT_MAX_CHARS`` limit; this subclass carries
    the breakdown so the overflow event in ``logs/events.jsonl`` can surface
    top contributors (structural fix for ``ibl-consciousness-context-overflow``).
    It is only raised when even the P1 core alone overflows — non-P1 sections
    are dropped first by graceful degradation (``ibl-local-31f19191be34``).
    """

    def __init__(self, *, total_chars: int, max_chars: int, mode: str,
                 sections: List[Any]) -> None:
        self.total_chars = int(total_chars)
        self.max_chars = int(max_chars)
        self.mode = str(mode or "")
        # sections is List[Tuple[str, int, int]] (name, chars, priority) —
        # coerce defensively to (name, chars).
        norm: List[tuple] = []
        for entry in sections or []:
            try:
                # sections may be (name, chars) or (name, chars, priority);
                # anything else (None, a bare string, a bad char count) is
                # dropped rather than crashed on.
                name, chars = entry[0], int(entry[1])
            except (TypeError, IndexError, ValueError):
                continue
            norm.append((str(name), chars))
        self.sections = norm
        self.top_contributors = sorted(norm, key=lambda x: -x[1])[:5]
        over = max(0, self.total_chars - self.max_chars)
        super().__init__(
            f"Background consciousness context too large "
            f"({self.total_chars:,} chars, {over:,} over the {self.max_chars:,} limit, "
            f"mode={self.mode}). Top contributors: "
            f"{[(n, c) for n, c in self.top_contributors]}"
        )


def _label_section(content: str, fallback: str) -> str:
    """Best-effort label from a section's leading '## Header' marker.

    Falls back to the caller-supplied position-based label when the section is
    missing a recognised heading (the low-mode ARCHITECTURE navigation map, for
    example, is built without a leading ``## ARCHITECTURE.md`` header)."""
    head = str(content or "")[:200]
    if head.startswith("## "):
        first_line = head.split("\n", 1)[0]
        label = first_line[3:].strip()
        if label:
            return label[:80].lower().replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_")
    return fallback


def graceful_assemble(
    parts: List[str],
    sections: List[Any],
    max_chars: int,
) -> Tuple[str, int, int]:
    """Assemble context with graceful overflow degradation.

    Iteratively drops the largest non-P1 section until the joined context fits
    ``max_chars``. P1 sections (drop_priority=0) are NEVER dropped — if even the
    P1 core alone overflows, the caller raises ``_ConsciousnessOverflow``.

    Returns ``(text, total_chars, dropped_count)``.
    """
    keep_parts: List[str] = list(parts)
    keep_sections: List[Any] = list(sections)
    dropped = 0
    while True:
        full_text = "\n\n".join(keep_parts)
        total_chars = len(full_text)
        if total_chars <= max_chars:
            break
        largest_idx = -1
        largest_chars = -1
        for idx, s in enumerate(keep_sections):
            if s[2] == 0:  # P1 — never dropped
                continue
            if s[1] > largest_chars:
                largest_chars = s[1]
                largest_idx = idx
        if largest_idx < 0:
            break  # all remaining sections are P1; caller raises overflow
        log.info(
            "consciousness: dropping %s (%d chars, priority=%d) — overflow "
            "graceful degradation",
            keep_sections[largest_idx][0], keep_sections[largest_idx][1],
            keep_sections[largest_idx][2],
        )
        keep_parts.pop(largest_idx)
        keep_sections.pop(largest_idx)
        dropped += 1
    return full_text, total_chars, dropped


def slim_drive_state(repo_dir: Any, drive_root: Any, *, context_mode: str) -> str:
    """Return a slim projection of state/state.json for the consciousness loop.

    Strips ``usage_accounting.by_root`` (the 453K-char mostly-zero per-root map)
    in BOTH modes; in ``low`` mode further bounds the section to ~30K chars.
    Mirrors ``ouroboros.context._drive_state_section``.
    """
    from ouroboros.agent import Env
    from ouroboros.context import _drive_state_section

    env = Env(repo_dir=repo_dir, drive_root=drive_root)
    section = _drive_state_section(env)
    if not section:
        return ""
    if '"by_root"' in section:
        try:
            import json as _json

            from ouroboros.context_health import read_json_dict as _rjd

            raw = _rjd(env.drive_path("state/state.json")) or {}
            ua = dict(raw.get("usage_accounting") or {})
            ua.pop("by_root", None)
            raw["usage_accounting"] = ua
            keys = (
                "session_id", "current_branch", "current_sha",
                "evolution_mode_enabled", "evolution_owner_stopped",
                "evolution_cycle", "evolution_consecutive_failures",
                "last_evolution_task_at", "bg_consciousness_enabled",
                "post_task_autostop", "budget_drift_pct",
                "budget_drift_alert", "last_owner_message_at",
            )
            projected = {k: raw[k] for k in keys if k in raw}
            omitted = sorted(set(raw) - set(projected))
            note = (
                "Projection of state/state.json (spend/budget facts live "
                "in the Runtime section, from the usage-accounting authority)."
                + ((" Omitted keys: " + ", ".join(omitted) + ". Full file: "
                    "read_file(root='runtime_data', path='state/state.json').")
                   if omitted else "")
            )
            section = ("## Drive state\n\n"
                       + _json.dumps(projected, ensure_ascii=False, indent=1,
                                     sort_keys=True, default=str)
                       + "\n\n" + note)
        except Exception:
            pass
    if context_mode == "low" and len(section) > 30_000:
        section = section[:30_000] + "\n\n[truncated — drive state bounded in low mode]\n"
    if len(section) > BG_STATE_JSON_WARN_CHARS:
        log.warning(
            "consciousness: drive state JSON is large (%d chars, mode=%s)",
            len(section), context_mode,
        )
    return section


def bounded_identity_for_low_mode(identity_section: str) -> str:
    """Return a bounded identity section for low-mode consciousness.

    Keeps the preamble + the most recent §-numbered section and trims everything
    between. Full identity stays available on demand via
    ``read_file(root='runtime_data', path='memory/identity.md')``.
    """
    if "## §" not in identity_section:
        return identity_section
    last_section_idx = identity_section.rfind("\n\n## §")
    if last_section_idx < 0:
        return identity_section
    first_body_idx = identity_section.find("\n\n## §")
    if first_body_idx < 0 or first_body_idx >= last_section_idx:
        return identity_section
    preamble = identity_section[:first_body_idx]
    last_section = identity_section[last_section_idx:].lstrip("\n")
    return (
        preamble
        + "\n\n[Earlier §-sections trimmed in low mode for context budget — "
          "full identity available via "
          "read_file(root='runtime_data', path='memory/identity.md').]\n\n"
        + last_section
    )
