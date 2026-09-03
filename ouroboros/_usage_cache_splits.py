"""Process-local record of each task's last observed prompt-cache split.

Extracted from ``ouroboros.usage_accounting`` (at its module size ceiling) as a
seam beside ``_usage_rows_memo`` and re-exported from there. Nothing here is
durable and nothing is locked: a lost, evicted or stale entry only makes the
money reservation price the whole prompt as a fresh cache write again, which is
the conservative direction, so a torn read can never under-reserve.
"""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

# (task_id, model) -> (observed cached prompt tokens, monotonic stamp, horizon)
_SPLITS: Dict[Tuple[str, str], Tuple[int, float, float]] = {}
_SPLITS_CAP = 64


def stash_task_cache_split(
    task_id: str, model: str, cached_tokens: int, *, ttl_seconds: float
) -> None:
    """Remember what one task+model send actually read from the provider cache."""
    key = (str(task_id or "").strip(), str(model or "").strip())
    if not key[0] or not key[1]:
        return
    if key not in _SPLITS and len(_SPLITS) >= _SPLITS_CAP:
        _SPLITS.clear()
    _SPLITS[key] = (max(0, int(cached_tokens or 0)), time.monotonic(), float(ttl_seconds))


def last_task_cache_split(task_id: str, model: str) -> Optional[int]:
    """The task's own last observed cached-token count, or None once it lapsed.

    None also covers a different model or route: the key carries the model, so a
    route change never inherits another route's cache split.
    """
    split = _SPLITS.get((str(task_id or "").strip(), str(model or "").strip()))
    if split is None or time.monotonic() - split[1] > split[2]:
        return None
    return split[0]


def reset_task_cache_splits() -> None:
    """Test seam: forget every observed split (process-local, no durable state)."""
    _SPLITS.clear()
