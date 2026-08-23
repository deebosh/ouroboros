"""Owner-configurable LLM sampling temperature — split out of config.py (module-size
gate) alongside review_cycles.py's precedent: the setting's env keys stay registered
in ``config.SETTINGS_DEFAULTS`` (the SSOT settings registry), while the resolver
logic lives here.

None is the "no override" sentinel throughout: the LLM layer omits the ``temperature``
key from the wire payload (see llm.py) and the provider default applies. Any
out-of-range value, NaN, inf, or non-numeric string resolves to None (NOT a clamped
fallback — temperature has no "unknown is medium" analog, invalid is invalid).
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, Optional

# Provider ranges vary (OpenAI/OpenRouter accept 0-2, Anthropic 0-1, and values
# >1.5 is exploratory-only); we expose the FULL provider-supported range and let the
# owner choose. The resolver treats any value OUTSIDE the range as invalid -> None
# (no override sent on the wire); the provider default applies. This is the same
# shape as EFFORT_SCALE's "unknown -> default" semantic, but with a numeric closed
# interval rather than an enum.
TEMPERATURE_SCALE_MIN: float = 0.0
TEMPERATURE_SCALE_MAX: float = 2.0


def _coerce_temperature_value(raw: Any) -> Optional[float]:
    """Parse a temperature string from env/settings; return the float or None.

    None means "do NOT send a temperature override on the wire" — the provider
    default applies. Any out-of-range value, NaN, inf, or non-numeric string
    resolves to None (NOT a clamped fallback; temperature has no "unknown is
    medium" analog, invalid is invalid). Empty string -> None (clear action).
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        val = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(val) or math.isinf(val):
        return None
    # -0.0 is the sign-bit zero; it compares equal to 0.0 but is valid.
    if val < TEMPERATURE_SCALE_MIN or val > TEMPERATURE_SCALE_MAX:
        return None
    return val


# Mapping from canonical task_type -> (env key, default). Mirrors resolve_effort's
# branch table: an unknown task_type falls back to the "task" lane (the ordinary
# default), consistent with resolve_effort's else-branch.
_TEMPERATURE_KEY_FOR_TASK = {
    "consciousness": ("OUROBOROS_TEMPERATURE_CONSCIOUSNESS", None),
}


def resolve_temperature(task_type: str) -> Optional[float]:
    """Return the configured sampling temperature for the given task type, or None.

    The closed contract: a float in [TEMPERATURE_SCALE_MIN, TEMPERATURE_SCALE_MAX]
    (inclusive) is returned; unset / empty / out-of-range / non-numeric / NaN / inf
    all resolve to None. None is the "no override" sentinel — the LLM layer omits the
    `temperature` key from the wire payload (see llm.py), so the provider default
    applies. Mirrors ``resolve_effort``'s shape: same task_type normalization, same
    env-only read path, same fallback semantics for unknown task_type.
    """
    t = (task_type or "").lower().strip()
    env_key, default = _TEMPERATURE_KEY_FOR_TASK.get(
        t, ("OUROBOROS_TEMPERATURE_TASK", None)
    )
    raw = os.environ.get(env_key, default)
    return _coerce_temperature_value(raw)


def get_temperatures() -> Dict[str, Optional[float]]:
    """The current per-task-type temperature projection, for GET /api/state.

    Returns a freshly built dict on every call (same shape as ``get_context_mode``
    returning a fresh string): a caller mutating the dict does not leak into
    subsequent reads. Keys are the two documented task_type values; an unknown
    task_type is not enumerated (callers should consume ``resolve_temperature``
    directly for non-canonical task_types).
    """
    return {
        "task": resolve_temperature("task"),
        "consciousness": resolve_temperature("consciousness"),
    }
