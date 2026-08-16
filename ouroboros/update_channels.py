"""Official managed-update channel mapping and bounded fetch settings."""

from __future__ import annotations

import os
from typing import Any, Mapping


UPDATE_CHANNEL_BRANCHES = {
    "stable": "main",
    "qa": "ouroboros-stable",
    "development": "ouroboros",
}

UPDATE_SETTINGS_DEFAULTS = {
    "OUROBOROS_UPDATE_CHANNEL": "stable",
    "OUROBOROS_MANAGED_UPDATE_FETCH_TIMEOUT_SEC": 300,
    "OUROBOROS_UPDATE_AUTOCHECK_ENABLED": False,
    "OUROBOROS_UPDATE_AUTOCHECK_INTERVAL_SEC": 86400,
    "OUROBOROS_UPDATE_SEMANTIC_TIMEOUT_SEC": 45,
}


def normalize_update_channel(value: Any) -> str:
    """Normalize the owner-facing channel; invalid values stay on Stable."""
    channel = str(value or "").strip().lower()
    return channel if channel in UPDATE_CHANNEL_BRANCHES else "stable"


def get_update_channel(settings: Mapping[str, Any] | None = None) -> str:
    """Return the runtime update channel, independent from launcher metadata."""
    if settings is not None:
        raw = settings.get("OUROBOROS_UPDATE_CHANNEL")
    else:
        raw = os.environ.get("OUROBOROS_UPDATE_CHANNEL")
        if raw is None:
            from ouroboros.config import load_settings

            raw = load_settings().get("OUROBOROS_UPDATE_CHANNEL")
    return normalize_update_channel(raw)


def get_update_branch(settings: Mapping[str, Any] | None = None) -> str:
    """Map the closed channel enum to its official branch."""
    return UPDATE_CHANNEL_BRANCHES[get_update_channel(settings)]


def get_managed_update_fetch_timeout_sec() -> int:
    """Wall-clock ceiling for official git network operations."""
    raw = os.environ.get(
        "OUROBOROS_MANAGED_UPDATE_FETCH_TIMEOUT_SEC",
        UPDATE_SETTINGS_DEFAULTS["OUROBOROS_MANAGED_UPDATE_FETCH_TIMEOUT_SEC"],
    )
    try:
        parsed = int(float(raw))
    except (TypeError, ValueError):
        parsed = int(UPDATE_SETTINGS_DEFAULTS["OUROBOROS_MANAGED_UPDATE_FETCH_TIMEOUT_SEC"])
    return max(30, min(parsed, 1800))


def get_update_autocheck_enabled(settings: Mapping[str, Any] | None = None) -> bool:
    """Owner opt-in for the periodic check-and-notify watcher. Off by default —
    apply always stays owner-gated regardless of this setting (BIBLE P0)."""
    if settings is not None:
        raw = settings.get("OUROBOROS_UPDATE_AUTOCHECK_ENABLED")
    else:
        raw = os.environ.get("OUROBOROS_UPDATE_AUTOCHECK_ENABLED")
        if raw is None:
            from ouroboros.config import load_settings

            raw = load_settings().get("OUROBOROS_UPDATE_AUTOCHECK_ENABLED")
    if raw is None:
        return bool(UPDATE_SETTINGS_DEFAULTS["OUROBOROS_UPDATE_AUTOCHECK_ENABLED"])
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def get_update_autocheck_interval_sec(settings: Mapping[str, Any] | None = None) -> int:
    """Clamped [30min, 24h] — the owner's 24h default sits at the ceiling."""
    if settings is not None:
        raw = settings.get("OUROBOROS_UPDATE_AUTOCHECK_INTERVAL_SEC")
    else:
        raw = os.environ.get("OUROBOROS_UPDATE_AUTOCHECK_INTERVAL_SEC")
        if raw is None:
            from ouroboros.config import load_settings

            raw = load_settings().get("OUROBOROS_UPDATE_AUTOCHECK_INTERVAL_SEC")
    try:
        parsed = int(float(raw if raw is not None else UPDATE_SETTINGS_DEFAULTS["OUROBOROS_UPDATE_AUTOCHECK_INTERVAL_SEC"]))
    except (TypeError, ValueError):
        parsed = int(UPDATE_SETTINGS_DEFAULTS["OUROBOROS_UPDATE_AUTOCHECK_INTERVAL_SEC"])
    return max(1800, min(parsed, 86400))


def get_update_semantic_timeout_sec(settings: Mapping[str, Any] | None = None) -> float:
    """Wall-clock ceiling for the semantic-overlap advisory model call."""
    if settings is not None:
        raw = settings.get("OUROBOROS_UPDATE_SEMANTIC_TIMEOUT_SEC")
    else:
        raw = os.environ.get("OUROBOROS_UPDATE_SEMANTIC_TIMEOUT_SEC")
        if raw is None:
            from ouroboros.config import load_settings

            raw = load_settings().get("OUROBOROS_UPDATE_SEMANTIC_TIMEOUT_SEC")
    try:
        parsed = float(raw if raw is not None else UPDATE_SETTINGS_DEFAULTS["OUROBOROS_UPDATE_SEMANTIC_TIMEOUT_SEC"])
    except (TypeError, ValueError):
        parsed = float(UPDATE_SETTINGS_DEFAULTS["OUROBOROS_UPDATE_SEMANTIC_TIMEOUT_SEC"])
    return max(5.0, min(parsed, 180.0))
