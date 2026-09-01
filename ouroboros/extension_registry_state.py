"""Process-wide registries of the surfaces live extensions own.

One loaded extension owns one ``_ExtensionRegistrations`` bundle; the per-surface
maps beside it are keyed by the canonical surface name so unload stays
proportional to a single extension. Everything here is mutated in place under
``_lock``, so every reader — the loader, the PluginAPI, the liveness projection —
shares the same objects.
"""

from __future__ import annotations

import pathlib
import threading
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Callable, Dict, List, Optional, Sequence


@dataclass
class _ExtensionRegistrations:
    """Attached surfaces owned by one loaded extension."""

    tools: List[str] = field(default_factory=list)
    routes: List[str] = field(default_factory=list)
    ws_handlers: List[str] = field(default_factory=list)
    ui_tabs: List[str] = field(default_factory=list)
    settings_sections: List[str] = field(default_factory=list)
    unload_callbacks: List[Callable[[], Any]] = field(default_factory=list)
    event_subscriptions: List[str] = field(default_factory=list)
    companion_names: List[str] = field(default_factory=list)
    supervised_futures: List[Any] = field(default_factory=list)
    api_instances: List[Any] = field(default_factory=list)
    content_hash: Optional[str] = None
    skill_dir: Optional[str] = None
    import_root: Optional[str] = None
    # ABI-9: minted at each atomic publication; stamped into the dispatch
    # surfaces (tools/routes/ws) so physical-call provenance can name the
    # exact published generation it dispatched against.
    generation_digest: str = ""
    # ABI-1: the PluginAPI generation this bundle was negotiated under
    # (manifest ``plugin_api`` field, or the legacy generation for
    # grandfathered field-less payloads).
    plugin_api_generation: str = ""


@dataclass
class _StagedSupervisedTask:
    """A supervised-task request captured during the registration window.

    The asyncio runner is deliberately NOT created here: it starts only at
    publication, after the whole registration validated (ABI-9) — a refused
    registration therefore can never leak a running task outside a bundle.
    """

    name: str
    factory: Callable[[], Any]
    restart_policy: str = "on_failure"
    max_restarts: int = 5
    backoff_seconds: float = 2.0


@dataclass
class _StagedCompanionSpawn:
    """A validated companion descriptor whose spawn is deferred to publication.

    ``spec`` carries the manifest companion entry so the post-fence attach can
    materialize the settings-derived env (fix-round-6): the descriptor's env is
    EMPTY until ``_publish_registrations`` fills it after the generation fence.
    """

    name: str
    descriptor: Any
    spec: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _StagedEventSubscription:
    """A validated event subscription whose bus attach is deferred to publication.

    ABI-9: ``subscribe_event`` validates the topic and mints the sub_id during
    the registration window but the bus subscription is created only at
    publication — an event published before the snapshot swap can never invoke
    a staged handler (pre-publication invisibility, not eventual cleanup)."""

    sub_id: str
    topic: str
    handler: Callable[[Dict[str, Any]], Any]


@dataclass
class _StagedRegistrations:
    """Private staging area for one PluginAPI registration window (ABI-9).

    ``register()`` accumulates every surface and side-effect request here;
    nothing reaches the process-wide registries until the loader publishes the
    whole snapshot atomically (validate -> swap -> attach). Every deferred
    side effect — supervised runners, companion spawns, event-bus
    subscriptions — attaches only at publication, after the definitive
    unload/conflict validation AND after the snapshot swap (so a handler is
    visible to the bus only for an already-published extension); an aborted
    registration leaves zero residue, and a post-swap attach failure is
    disposed through the standard unload path. The disposers list is
    loader-internal and never exposed through the PluginAPI ABI.
    """

    tools: Dict[str, Any] = field(default_factory=dict)
    routes: Dict[str, Any] = field(default_factory=dict)
    ws_handlers: Dict[str, Any] = field(default_factory=dict)
    ui_tabs: Dict[str, Any] = field(default_factory=dict)
    settings_sections: Dict[str, Any] = field(default_factory=dict)
    unload_callbacks: List[Callable[[], Any]] = field(default_factory=list)
    event_subscriptions: List["_StagedEventSubscription"] = field(default_factory=list)
    companion_names: List[str] = field(default_factory=list)
    supervised_tasks: List[_StagedSupervisedTask] = field(default_factory=list)
    companion_spawns: List[_StagedCompanionSpawn] = field(default_factory=list)
    disposers: List[Callable[[], Any]] = field(default_factory=list)


@dataclass
class _ExtensionLoadFailure:
    content_hash: str
    skill_dir: str
    error: str


@dataclass
class _PluginAPIConfig:
    skill_name: str
    permissions: Sequence[str]
    env_allowlist: Sequence[str]
    state_dir: pathlib.Path
    settings_reader: Callable[[], Dict[str, Any]]
    drive_root: pathlib.Path | None = None
    granted_keys: Sequence[str] | None = None
    subscribe_events: Sequence[str] | None = None
    companion_processes: Sequence[Dict[str, Any]] | None = None
    skill_dir: pathlib.Path | None = None
    runtime_skill_dir: pathlib.Path | None = None
    dependency_site_dirs_enabled: bool = False
    # ABI-1: negotiated PluginAPI generation served to this extension
    # ("" -> the loader's negotiation default for grandfathered payloads).
    plugin_api_generation: str = ""


# Lock-guarded registries; per-surface maps keep unload proportional to one extension.
_lock = threading.RLock()
_extensions: Dict[str, _ExtensionRegistrations] = {}
_extension_modules: Dict[str, ModuleType] = {}
_load_failures: Dict[str, _ExtensionLoadFailure] = {}
_unloading: set[str] = set()
_lifecycle_locks: Dict[str, threading.RLock] = {}
_tools: Dict[str, Any] = {}            # {"ext_<len>_<token>_<name>": ToolEntry-like}
_routes: Dict[str, Any] = {}           # {"/api/extensions/<skill>/<path>": handler_spec}
_ws_handlers: Dict[str, Any] = {}      # {"ext_<len>_<token>_<message_type>": handler}
_ui_tabs: Dict[str, Any] = {}          # {"<skill>:<tab_id>": tab_spec}
# Declarative settings sections keyed like UI tabs.
_settings_sections: Dict[str, Any] = {}


def _lifecycle_lock_for(skill_name: str) -> threading.RLock:
    with _lock:
        lock = _lifecycle_locks.get(skill_name)
        if lock is None:
            lock = threading.RLock()
            _lifecycle_locks[skill_name] = lock
        return lock


def extension_generation_digest(skill_name: str) -> str:
    """Return the generation digest of the skill's live publication, or ``""``.

    Dispatch-side provenance reads this (or the per-surface stamp) to name the
    exact published registration generation a physical call ran against.
    """
    with _lock:
        bundle = _extensions.get(str(skill_name or ""))
        return str(bundle.generation_digest or "") if bundle is not None else ""


def _record_companion_name(bundle: _ExtensionRegistrations, name: str) -> None:
    if name not in bundle.companion_names:
        bundle.companion_names.append(name)
