"""The PluginAPI object handed to one extension's ``register(api)``.

One instance is bound to one skill, its manifest permission set, its state dir
and its owner grants, and it is the only way an extension reaches the host: it
registers surfaces, subscribes to host events, spawns manifest-declared
companions, pushes WebSocket messages and reads the settings it was allowed to
see. Registration closes when ``register()`` returns and runtime access closes
at unload, so a late call is refused rather than served.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
import os
import pathlib
import secrets
import sys
import threading
import urllib.request
import uuid
from typing import Any, Callable, Dict, Optional, Sequence

from ouroboros.contracts.plugin_api import (
    ExecutionMode,
    ExtensionRegistrationError,
    FORBIDDEN_SKILL_SETTINGS,
    LEGACY_PLUGIN_API_GENERATION,
    RuntimeInfo,
    VALID_EXTENSION_PERMISSIONS,
    VALID_EXTENSION_ROUTE_METHODS,
    available_capabilities,
    capability_available,
)
from ouroboros.event_bus import VALID_TOPICS as VALID_EVENT_TOPICS, get_global_event_bus
from ouroboros.extension_companion import CompanionDescriptor, get_global_supervisor, is_server_process
from ouroboros.extension_child_catalog import materialize_companion_env
from ouroboros.extension_isolated_deps import (
    async_isolated_site_dirs_scope,
    isolated_site_dirs_scope,
)
from ouroboros.extension_registry_state import (
    _PluginAPIConfig,
    _ExtensionRegistrations,
    _StagedCompanionSpawn,
    _StagedEventSubscription,
    _StagedRegistrations,
    _StagedSupervisedTask,
    _extensions,
    _load_failures,
    _lock,
    _record_companion_name,
    _routes,
    _settings_sections,
    _tools,
    _ui_tabs,
    _unloading,
    _ws_handlers,
)
from ouroboros.extension_surface_names import (
    _assert_namespace_path,
    _assert_tool_name,
    _widget_geometry_from_render,
    _widget_span_from_render,
    extension_name_prefix,
    extension_surface_name,
)
from ouroboros.extension_ui_validation import (
    _assert_ws_message_type,
    validate_settings_schema as _validate_settings_schema,
    validate_ui_render as _validate_ui_render,
)
from ouroboros.gateway.host_service import AUTH_TOKEN_FILENAME
from ouroboros.provider_models import MODEL_PROVIDER_CREDENTIAL_KEYS
from ouroboros.skill_loader import compute_content_hash, requested_core_setting_keys
from ouroboros.skill_token import SkillToken
from ouroboros.utils import atomic_write_json, read_json_dict, utc_now_iso

log = logging.getLogger(__name__)


class ExtensionStaleRecoveryError(ExtensionRegistrationError):
    """Typed refusal of a generation-bound recovery publication (ABI-9).

    Raised when ``require_live_generation`` names a publication that is no
    longer live (the bundle vanished or was reloaded). The refusal has no
    effects on authorization, registries, bundles, or the companion env: no
    bundle is created, nothing is staged, ``auth_token.json`` is neither
    created nor rotated, no companion env is materialized, and nothing may be
    disposed by the caller. Infrastructure reads on the way to the fence (the
    settings lock inside ``load_settings``, a state-dir mkdir via the
    liveness/grant projection) may occur — the same settings/grant read idiom
    used everywhere in the runtime (fix-round-7 scope)."""


def current_execution_mode() -> ExecutionMode:
    """Execution context of the running PluginAPI, derived from the child env flag."""
    if os.environ.get("OUROBOROS_EXTENSION_PROCESS_CHILD") == "1":
        return ExecutionMode.OUT_OF_PROCESS
    return ExecutionMode.IN_PROCESS


def _reject_extension_child_side_effect(capability: str) -> None:
    """Enforce the contract capability matrix for the current execution mode.

    Every side-effect registration method calls this; the matrix in
    ``contracts.plugin_api`` is the single source of truth for what an
    out-of-process (isolated-dep) child may use. on_unload, send_ws_message, and
    register_companion_process are supported out-of-process; subscribe_event and
    register_supervised_task are not (use a companion_process instead).
    """

    mode = current_execution_mode()
    if not capability_available(capability, mode):
        available = ", ".join(sorted(available_capabilities(mode)))
        raise ExtensionRegistrationError(
            f"{capability} is not available to out-of-process (isolated-dep) extensions "
            f"in the per-call child; declare a companion_process for long-running work "
            f"and host-event subscription. Available capabilities here: {available}."
        )


class SkillTokenHashUnavailableError(ExtensionRegistrationError):
    """Fail-closed mint refusal (fix-round-7): the content hash could not be
    computed and no previously issued token exists to reuse."""


def mint_skill_token(state_dir: pathlib.Path, skill_name: str, skill_dir: Optional[pathlib.Path]) -> str:
    """Read or rotate the per-skill Host Service token, bound to the content hash.

    Shared by the in-process PluginAPI (``get_skill_token``), the out-of-process
    child env builder, and the post-fence companion-env materialization inside
    ``_publish_registrations``. This WRITES ``auth_token.json`` when the token
    is missing or hash-stale: a generation-fenced path calls it only post-fence.
    A transient hash failure never rotates: reuse or fail closed (fix-round-7).
    """
    token_path = pathlib.Path(state_dir) / AUTH_TOKEN_FILENAME
    payload = read_json_dict(token_path) or {}
    token = str(payload.get("token") or "")
    content_hash = ""
    if skill_dir is not None:
        try:
            content_hash = compute_content_hash(pathlib.Path(skill_dir))
        except Exception as exc:
            # Fix-round-7: "could not read/compute" is DISTINCT from "read fine
            # and mismatched" (legit rotation) and from a missing/corrupt token
            # file (legit mint); rotating here would de-authorize a running
            # companion whose spawn-env token is still valid.
            if token:
                log.warning("skill %s content hash unavailable; reusing the existing token unrotated", skill_name, exc_info=True)
                return token
            raise SkillTokenHashUnavailableError(
                f"skill {skill_name!r}: content hash could not be computed and "
                f"no previously issued token exists; refusing to mint"
            ) from exc
    if not token or str(payload.get("content_hash") or "") != content_hash:
        token = secrets.token_urlsafe(32)
        atomic_write_json(
            token_path,
            {
                "token": token,
                "issued_at": utc_now_iso(),
                "skill": skill_name,
                "content_hash": content_hash,
            },
        )
        try:
            token_path.chmod(0o600)
        except OSError:
            log.debug("Failed to chmod skill token file %s", token_path, exc_info=True)
    return token


_ws_broadcaster: Optional[Callable[[dict], None]] = None


def set_ws_broadcaster(broadcaster: Callable[[dict], None] | None) -> None:
    """Install the host WebSocket broadcaster used by PluginAPI.send_ws_message."""
    global _ws_broadcaster
    with _lock:
        _ws_broadcaster = broadcaster


class PluginAPIImpl:
    """PluginAPI bound to one skill, permission set, and state dir."""

    def __init__(self, config: _PluginAPIConfig) -> None:
        # ABI-1 (7.0): the legacy keyword construction path is gone; every
        # caller builds an explicit _PluginAPIConfig.
        self._skill = config.skill_name
        self._permissions = frozenset(str(p).strip() for p in (config.permissions or []))
        self._env_allow = frozenset(str(k).strip() for k in (config.env_allowlist or []))
        self._env_allow_upper = frozenset(k.upper() for k in self._env_allow)
        self._state_dir = pathlib.Path(config.state_dir)
        self._drive_root = (
            pathlib.Path(config.drive_root)
            if config.drive_root is not None
            else self._state_dir
        )
        self._subscribe_events = frozenset(str(t).strip() for t in (config.subscribe_events or []) if str(t).strip())
        self._companion_specs = {
            str(item.get("name") or "").strip(): dict(item)
            for item in (config.companion_processes or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        # Keep runtime_info cheap and tied to the loaded payload.
        self._skill_dir = pathlib.Path(config.skill_dir) if config.skill_dir is not None else None
        self._runtime_skill_dir = pathlib.Path(config.runtime_skill_dir) if config.runtime_skill_dir is not None else self._skill_dir
        self._dependency_site_dirs_enabled = bool(config.dependency_site_dirs_enabled)
        self._settings_reader = config.settings_reader
        self._registration_closed = False
        self._runtime_closing = False
        self._runtime_closed = False
        self._plugin_api_generation = (
            str(config.plugin_api_generation or "") or LEGACY_PLUGIN_API_GENERATION
        )
        # ABI-9 staging area: register() accumulates surfaces and side-effect
        # requests here; the loader publishes them as one atomic snapshot.
        self._staged = _StagedRegistrations()
        # The generation digest this instance's own publication minted
        # ("" until _publish_registrations swaps the snapshot in).
        self._published_generation = ""
        self._api_lock = threading.RLock()
        # Core settings are exposed only when a content-hash-bound owner grant
        # was already verified; otherwise the denylist silently drops them.
        self._granted_upper = frozenset(
            str(k).strip().upper() for k in (config.granted_keys or []) if str(k).strip()
        )

    # --- internal helpers ---

    def _require(self, perm: str) -> None:
        with _lock:
            self._require_open_locked()
        if perm not in VALID_EXTENSION_PERMISSIONS:
            raise ExtensionRegistrationError(
                f"unknown extension permission {perm!r}"
            )
        if perm not in self._permissions:
            raise ExtensionRegistrationError(
                f"skill {self._skill!r} cannot {perm!r} "
                f"— manifest permissions={sorted(self._permissions)}"
            )

    def _require_open_locked(self) -> None:
        if self._registration_closed or self._runtime_closing or self._runtime_closed or self._skill in _unloading:
            raise ExtensionRegistrationError(
                f"skill {self._skill!r} cannot register after unload has started"
            )

    def _model_credential_available(self) -> bool:
        """Whether this live in-process extension can read a funded model key."""
        if "read_settings" not in self._permissions:
            return False
        candidates = (
            self._env_allow_upper
            & self._granted_upper
            & MODEL_PROVIDER_CREDENTIAL_KEYS
        )
        if not candidates:
            return False
        settings = self._settings_reader() or {}
        return any(str(settings.get(key) or "").strip() for key in candidates)

    def _disclose_model_capable_dispatch(self, surface_kind: str, surface: str) -> str:
        """Mark one opaque in-process extension callback before it can spend."""
        if not self._model_credential_available():
            return ""
        from ouroboros.usage_accounting import record_unmetered_external_dispatch

        system_task = f"extension:{self._skill}"
        return record_unmetered_external_dispatch(
            f"extension:{surface_kind}:{uuid.uuid4().hex}",
            drive_root=self._drive_root,
            provider="external-extension",
            task_id=system_task,
            root_task_id=system_task,
            category="external_skill",
            source=f"extension_{surface_kind}:{self._skill}:{surface}",
        )

    def _wrap_runtime_handler(
        self,
        handler: Callable[..., Any],
        *,
        opaque_surface: tuple[str, str] | None = None,
    ) -> Callable[..., Any]:
        if self._skill_dir is None and opaque_surface is None:
            return handler

        if inspect.iscoroutinefunction(handler):
            @functools.wraps(handler)
            async def _async_wrapped(*args: Any, **kwargs: Any) -> Any:
                if opaque_surface is not None:
                    self._disclose_model_capable_dispatch(*opaque_surface)
                if self._skill_dir is None:
                    return await handler(*args, **kwargs)
                async with async_isolated_site_dirs_scope(
                    self._skill_dir,
                    enabled=self._dependency_site_dirs_enabled,
                ):
                    return await handler(*args, **kwargs)

            return _async_wrapped

        @functools.wraps(handler)
        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            if opaque_surface is not None:
                self._disclose_model_capable_dispatch(*opaque_surface)
            if self._skill_dir is None:
                return handler(*args, **kwargs)
            with isolated_site_dirs_scope(self._skill_dir, enabled=self._dependency_site_dirs_enabled):
                result = handler(*args, **kwargs)
                return result

        return _wrapped

    def _stage_surface_locked(
        self,
        live_registry: Dict[str, Any],
        staged_registry: Dict[str, Any],
        key: str,
        value: Dict[str, Any],
        label: str,
    ) -> None:
        """Stage one validated surface; publication swaps the whole snapshot."""
        self._require_open_locked()
        if key in live_registry or key in staged_registry:
            raise ExtensionRegistrationError(f"{label} {key!r} already registered")
        staged_registry[key] = value

    # --- registration ---

    def register_tool(
        self,
        name: str,
        handler: Callable[..., str],
        *,
        description: str,
        schema: Dict[str, Any],
        timeout_sec: int = 60,
    ) -> None:
        self._require("tool")
        short = _assert_tool_name(name)
        full = extension_surface_name(self._skill, short)
        # Decide the ctx calling-convention on the RAW handler at register time:
        # the runtime wrapper is (*args, **kwargs), so inspecting it later always
        # reports VAR_POSITIONAL and forces a ctx-first call (TypeError for
        # keyword-only / zero-arg handlers). Dispatch reads this stored flag.
        from ouroboros.extension_process_runner import _handler_wants_ctx
        wants_ctx = _handler_wants_ctx(handler)
        with _lock:
            self._stage_surface_locked(_tools, self._staged.tools, full, {
                "name": full,
                "handler": self._wrap_runtime_handler(handler),
                "wants_ctx": wants_ctx,
                "description": str(description or ""),
                "schema": dict(schema or {}),
                "timeout_sec": max(1, int(timeout_sec)),
                "skill": self._skill,
                **({"_model_credential_probe": self._model_credential_available}
                   if current_execution_mode() is ExecutionMode.IN_PROCESS else {}),
            }, "tool")

    def register_route(
        self,
        path: str,
        handler: Callable[..., Any],
        *,
        methods: Sequence[str] = ("GET",),
    ) -> None:
        self._require("route")
        rel = _assert_namespace_path(path)
        methods_iter = (methods,) if isinstance(methods, str) else (methods or ())
        norm_methods = tuple(
            dict.fromkeys(
                str(m).strip().upper()
                for m in methods_iter
                if str(m).strip()
            )
        )
        if not norm_methods:
            raise ExtensionRegistrationError("route methods must be non-empty")
        invalid_methods = [m for m in norm_methods if m not in VALID_EXTENSION_ROUTE_METHODS]
        if invalid_methods:
            raise ExtensionRegistrationError(
                f"route methods {invalid_methods!r} are unsupported; "
                f"expected subset of {sorted(VALID_EXTENSION_ROUTE_METHODS)}"
            )
        mount = f"/api/extensions/{self._skill}/{rel}"
        with _lock:
            self._stage_surface_locked(_routes, self._staged.routes, mount, {
                "path": mount,
                "handler": self._wrap_runtime_handler(handler),
                "methods": norm_methods,
                "skill": self._skill,
                **({"_model_credential_probe": self._model_credential_available}
                   if current_execution_mode() is ExecutionMode.IN_PROCESS else {}),
            }, "route")

    def register_ws_handler(
        self,
        message_type: str,
        handler: Callable[..., Any],
    ) -> None:
        self._require("ws_handler")
        short = _assert_ws_message_type(message_type)
        full = extension_surface_name(self._skill, short)
        with _lock:
            self._stage_surface_locked(_ws_handlers, self._staged.ws_handlers, full, {
                "type": full,
                "handler": self._wrap_runtime_handler(handler),
                "skill": self._skill,
                **({"_model_credential_probe": self._model_credential_available}
                   if current_execution_mode() is ExecutionMode.IN_PROCESS else {}),
            }, "ws handler")

    def register_ui_tab(
        self,
        tab_id: str,
        title: str,
        *,
        icon: str = "extension",
        render: Dict[str, Any] | None = None,
    ) -> None:
        self._require("widget")
        clean_tab = _assert_tool_name(tab_id)  # same syntax rules
        key = f"{self._skill}:{clean_tab}"
        validated_render = _validate_ui_render({} if render is None else render)
        span = _widget_span_from_render(validated_render)
        with _lock:
            self._stage_surface_locked(_ui_tabs, self._staged.ui_tabs, key, {
                "skill": self._skill,
                "tab_id": clean_tab,
                "title": str(title or clean_tab),
                "icon": str(icon or "extension"),
                "ws_prefix": extension_name_prefix(self._skill),
                "render": validated_render,
                "span": span,
                "grid_span": span,
                **_widget_geometry_from_render(validated_render),
                "ui_host_pending": True,
            }, "ui tab")

    def register_settings_section(
        self,
        section_id: str,
        title: str,
        *,
        schema: Dict[str, Any],
    ) -> None:
        """Validate and register a declarative Settings UI section."""
        # Settings sections share the widget permission and host-rendered schema.
        self._require("widget")
        clean_id = _assert_tool_name(section_id)
        key = f"{self._skill}:{clean_id}"
        # Settings stay declarative-only and narrower than widgets while using
        # the same recursive component validator as every other UI surface.
        validated = _validate_settings_schema(schema)
        with _lock:
            self._stage_surface_locked(_settings_sections, self._staged.settings_sections, key, {
                "skill": self._skill,
                "section_id": clean_id,
                "title": str(title or clean_id),
                "render": validated,
            }, "settings section")

    def register_supervised_task(
        self,
        name: str,
        factory: Callable[[], Any],
        *,
        restart_policy: str = "on_failure",
        max_restarts: int = 5,
        backoff_seconds: float = 2.0,
    ) -> None:
        """Declare a server-owned supervised task; workers only record it.

        ABI-9: the asyncio runner is NOT created here. The request is staged
        and the runner starts only at publication, after the whole
        registration validated — a refused registration can therefore never
        leak a running task outside a bundle's cancellation reach.
        """
        _reject_extension_child_side_effect("register_supervised_task")
        self._require("supervised_task")
        clean_name = _assert_tool_name(name)
        with _lock:
            self._require_open_locked()
            marker = f"task:{clean_name}"
            if marker not in self._staged.companion_names:
                self._staged.companion_names.append(marker)
            self._staged.supervised_tasks.append(_StagedSupervisedTask(
                name=clean_name,
                factory=factory,
                restart_policy=str(restart_policy or "on_failure"),
                max_restarts=int(max_restarts),
                backoff_seconds=float(backoff_seconds),
            ))

    def _start_supervised_task(self, spec: _StagedSupervisedTask) -> Optional[Any]:
        """Start one published supervised runner; returns the future or None."""
        if not is_server_process():
            return None
        loop = getattr(get_global_event_bus(), "_loop", None)
        if loop is None or not loop.is_running():
            return None
        import asyncio

        async def _runner() -> None:
            restarts = 0
            while True:
                try:
                    self._disclose_model_capable_dispatch("supervised_task", spec.name)
                    result = spec.factory()
                    if inspect.isawaitable(result):
                        await result
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    restarts += 1
                    if spec.restart_policy != "on_failure" or restarts > spec.max_restarts:
                        log.warning("supervised task %s/%s stopped after failure", self._skill, spec.name, exc_info=True)
                        return
                    await asyncio.sleep(max(0.1, float(spec.backoff_seconds)))

        return asyncio.run_coroutine_threadsafe(_runner(), loop)

    def register_companion_process(
        self,
        name: str,
    ) -> None:
        _reject_extension_child_side_effect("register_companion_process")
        self._require("companion_process")
        clean_name = _assert_tool_name(name)
        spec = self._companion_specs.get(clean_name)
        if spec is None:
            raise ExtensionRegistrationError(
                f"companion {clean_name!r} is not declared in manifest.companion_processes"
            )
        if current_execution_mode() is ExecutionMode.OUT_OF_PROCESS:
            # Catalog child: only record the manifest-declared name. The host spawns
            # and supervises the real companion after the catalog returns (it owns the
            # supervisor), reusing the in-process descriptor build below.
            with _lock:
                self._require_open_locked()
                self._stage_companion_name_locked(clean_name)
            return
        expected_cmd = [str(part) for part in (spec.get("command") or []) if str(part)]
        expected_runtime = str(spec.get("runtime") or "").strip()
        cmd = list(expected_cmd)
        if not cmd:
            raise ExtensionRegistrationError("companion command must be declared in manifest")
        if expected_runtime in {"python", "python3"} and cmd[0] in {"python", "python3"}:
            cmd = [sys.executable, *cmd[1:]]
        manifest_path_override = any(
            str(key).upper() == "PATH" for key in (spec.get("env") or {})
        )
        if expected_runtime in {"node", "npm"} and not manifest_path_override:
            # T14: an explicit PATH in the companion manifest means the author
            # owns the runtime lookup — the host-side probe cannot see that
            # child-only PATH, so neither the bundled rewrite nor the emergency
            # prepend may shadow it.
            # Node symmetry with the python->sys.executable rewrite above: the
            # skill-family runtime precedence (bundled-first + health rollback
            # to a working PATH node) is owned by
            # platform_layer.select_skill_node_runtime. npm itself is NOT
            # bundled and is never rewritten; only in the emergency state
            # (PATH node missing/execution-probed broken, healthy bundled
            # selected) does the companion child PATH gain the bundled-node
            # dir so npm's `#!/usr/bin/env node` shebang finds a working
            # runtime (the PATH prepend itself happens in the post-fence env
            # materialization, ouroboros/extension_child_catalog.py). On a
            # healthy PATH the child env stays byte-identical. Disclosed
            # residual: an npm launcher rewritten to an ABSOLUTE node shebang
            # ignores PATH and keeps failing honestly.
            from ouroboros.platform_layer import select_skill_node_runtime

            selected_node, _node_provenance = select_skill_node_runtime()
            if selected_node and cmd[0] == "node":
                cmd = [selected_node, *cmd[1:]]
        if not is_server_process():
            with _lock:
                self._require_open_locked()
                self._stage_companion_name_locked(f"worker-skip:{clean_name}")
            return
        supervisor = get_global_supervisor()
        if supervisor is None:
            raise ExtensionRegistrationError("companion supervisor is not initialized")
        # Fix-round-6: the descriptor build is purely computational — the env
        # stays EMPTY here. The settings-derived values (``load_settings``
        # takes the settings lock and may persist a migration), the manifest
        # env overlay, the host bridge URL and the auth token all materialize
        # only in the post-fence attach of ``_publish_registrations``.
        workdir = self._runtime_skill_dir or self._skill_dir or self._state_dir
        descriptor = CompanionDescriptor(
            skill_name=self._skill,
            name=clean_name,
            command=cmd,
            cwd=workdir,
            env={},
            ports=[int(port) for port in (spec.get("ports") or []) if str(port).isdigit()],
            restart_policy=str(spec.get("restart_policy") or "on_failure"),
            max_restarts=max(0, int(spec.get("max_restarts") or 5)),
        )
        # ABI-9: the spawn is deferred to publication; a refused/aborted
        # registration must never leave a running companion process behind.
        with _lock:
            self._require_open_locked()
            self._stage_companion_name_locked(clean_name)
            self._staged.companion_spawns.append(
                _StagedCompanionSpawn(name=clean_name, descriptor=descriptor, spec=dict(spec))
            )

    def _stage_companion_name_locked(self, name: str) -> None:
        if name not in self._staged.companion_names:
            self._staged.companion_names.append(name)

    def subscribe_event(self, topic: str, handler: Callable[[Dict[str, Any]], Any]) -> str:
        _reject_extension_child_side_effect("subscribe_event")
        self._require("subscribe_event")
        topic = str(topic or "").strip()
        if topic not in self._subscribe_events:
            raise ExtensionRegistrationError(
                f"skill {self._skill!r} cannot subscribe to undeclared topic {topic!r}"
            )
        if topic not in VALID_EVENT_TOPICS:
            raise ExtensionRegistrationError(
                f"skill {self._skill!r} declared unsupported event topic {topic!r}"
            )
        # ABI-9: the bus subscription is STAGED, not created — topic validation
        # and the returned sub_id belong to the call, the bus attach belongs to
        # publication. An event published before the snapshot swap therefore
        # never invokes the handler (pre-publication invisibility), and a
        # refused registration leaves nothing on the bus to clean up.
        sub_id = uuid.uuid4().hex
        wrapped = self._wrap_runtime_handler(handler, opaque_surface=("event", topic))
        with _lock:
            self._require_open_locked()
            self._staged.event_subscriptions.append(
                _StagedEventSubscription(sub_id=sub_id, topic=topic, handler=wrapped)
            )
        return sub_id

    def send_ws_message(self, message_type: str, data: Dict[str, Any]) -> None:
        _reject_extension_child_side_effect("send_ws_message")
        if "ws_handler" not in self._permissions:
            raise ExtensionRegistrationError(
                f"skill {self._skill!r} cannot 'ws_handler' "
                f"— manifest permissions={sorted(self._permissions)}"
            )
        short = _assert_ws_message_type(message_type)
        with _lock:
            if self._runtime_closing or self._runtime_closed or self._skill in _unloading:
                return
        if current_execution_mode() is ExecutionMode.OUT_OF_PROCESS:
            # Out-of-process: relay through the Host Service loopback bridge (identity
            # re-derived from the token, host-side re-namespacing). The relay touches
            # no shared host state, so it runs OUTSIDE _api_lock — a slow/unreachable
            # host must not block the lock on the loopback HTTP call.
            self._send_ws_message_via_host(short, dict(data or {}))
            return
        full = extension_surface_name(self._skill, short)
        payload = {"type": full, "data": dict(data or {}), "skill": self._skill}
        with self._api_lock:
            broadcaster = _ws_broadcaster
            if broadcaster is None:
                log.debug("extension %s dropped WS message %s: no broadcaster", self._skill, full)
                return
            try:
                broadcaster(payload)
            except Exception:
                log.warning("extension %s WS broadcast failed for %s", self._skill, full, exc_info=True)

    def _send_ws_message_via_host(self, short: str, data: Dict[str, Any]) -> None:
        """Best-effort WS push from an out-of-process child/companion via Host Service."""
        base_url = (os.environ.get("HOST_SERVICE_URL") or "").strip()
        token = (os.environ.get("HOST_SERVICE_TOKEN") or "").strip()
        if not base_url or not token:
            log.debug("extension %s dropped WS message %s: no host bridge env", self._skill, short)
            return
        body = json.dumps({"message_type": short, "data": data}).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/ui/ws-message",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "x-skill-token": token},
        )
        try:
            with urllib.request.urlopen(request, timeout=2):  # noqa: S310 - loopback Host Service
                return
        except Exception:
            log.debug("extension %s host WS relay failed for %s", self._skill, short, exc_info=True)

    def on_unload(self, callback: Callable[[], Any]) -> None:
        _reject_extension_child_side_effect("on_unload")
        if not callable(callback):
            raise ExtensionRegistrationError("on_unload callback must be callable")
        with _lock:
            if self._registration_closed or self._runtime_closing or self._runtime_closed or self._skill in _unloading:
                raise ExtensionRegistrationError(
                    f"skill {self._skill!r} cannot register unload callbacks after unload has started"
                )
            # Wrap so an out-of-process isolated-dep extension's cleanup runs with its
            # isolated deps on sys.path at child teardown (true OOP on_unload parity);
            # in-process no-dep extensions get the callback unchanged.
            self._staged.unload_callbacks.append(
                self._wrap_runtime_handler(callback, opaque_surface=("unload", "on_unload"))
            )

    def _close_registration(self) -> None:
        with _lock:
            self._registration_closed = True

    # --- ABI-9 atomic publication (stage -> validate -> swap) ---

    def _run_staged_disposers(self, extra: Sequence[Callable[[], Any]] = ()) -> None:
        for dispose in [*reversed(list(extra)), *reversed(self._staged.disposers)]:
            try:
                dispose()
            except Exception:
                log.warning("extension %s staged-registration disposer failed", self._skill, exc_info=True)
        self._staged = _StagedRegistrations()

    def _abort_registration(self) -> None:
        """Discard the staged snapshot and undo its live side effects."""
        self._close_registration()
        self._run_staged_disposers()

    def _staged_surface_conflicts_locked(self) -> list[str]:
        return [
            key
            for live, staged in (
                (_tools, self._staged.tools),
                (_routes, self._staged.routes),
                (_ws_handlers, self._staged.ws_handlers),
                (_ui_tabs, self._staged.ui_tabs),
                (_settings_sections, self._staged.settings_sections),
            )
            for key in staged
            if key in live
        ]

    def _publish_registrations(
        self,
        *,
        content_hash: Optional[str] = None,
        skill_dir: Optional[str] = None,
        import_root: Optional[str] = None,
        module: Any = None,
        require_live_generation: Optional[str] = None,
    ) -> None:
        """Atomically publish the staged registration snapshot (ABI-9).

        validate -> SWAP -> attach under ONE registry-lock hold: the
        definitive unload/conflict validation runs FIRST, so a refused
        publication has produced no externally visible effect at all — no
        supervised runner, no companion process, no bus subscription. The
        validated snapshot then swaps into the process-wide registries as
        the authoritative bundle, stamped with a fresh generation digest,
        and only AFTER the swap do the deferred side effects attach — the
        event-bus subscriptions, the supervised runners, the companion
        spawns — still inside the same critical section. A handler is
        therefore visible to the bus only for an already-published
        extension: a concurrent ``EventBus.publish()`` (which takes only
        the bus's own lock) landing between the validation and the attach
        cannot invoke a handler of a not-yet-published extension. A bundle
        may publish MORE than once (the OOP companion recovery path): a
        later publication mints a fresh digest and RE-STAMPS every
        already-published descriptor the bundle owns, so per-surface
        provenance never diverges from the bundle digest. Such a recovery
        publication is GENERATION-BOUND: it passes
        ``require_live_generation`` and is admitted only while the bundle it
        observed is STILL the live publication — a vanished bundle or a
        different generation raises a typed ``ExtensionStaleRecoveryError``
        before any mutation (the whole companion env included — state dir,
        auth token and settings-derived values materialize into the staged
        descriptors only in the post-swap attach below, never during the
        purely computational descriptor build), and recovery never creates a
        bundle, so a completed unload/reload cannot be resurrected. Every
        attachable effect is recorded on the published bundle at the swap
        (futures as they are created), so a failure while attaching leaves
        nothing orphaned: it is disclosed and raised into the caller's
        STANDARD dispose+unload path (``unload_extension`` reaps the
        bundle's surfaces, subscriptions, futures and companions). No
        concurrent unload or conflicting publication can interleave
        anywhere inside (both mutate only under this lock).
        """
        with _lock:
            try:
                self._require_open_locked()
                if require_live_generation is not None:
                    live_bundle = _extensions.get(self._skill)
                    live_generation = (
                        str(live_bundle.generation_digest or "") if live_bundle is not None else ""
                    )
                    if live_bundle is None or live_generation != str(require_live_generation):
                        raise ExtensionStaleRecoveryError(
                            f"skill {self._skill!r} recovery publication refused: "
                            f"observed generation {str(require_live_generation)!r} is not "
                            f"the live publication (live={live_generation!r}); "
                            "nothing was published"
                        )
                conflicts = self._staged_surface_conflicts_locked()
                if conflicts:
                    raise ExtensionRegistrationError(
                        f"surfaces {sorted(conflicts)!r} were registered concurrently; "
                        "publication refused"
                    )
            except Exception:
                self._run_staged_disposers()
                raise
            staged = self._staged
            bundle = _extensions.get(self._skill)
            if bundle is None:
                bundle = _ExtensionRegistrations()
                _extensions[self._skill] = bundle
            if content_hash is not None:
                bundle.content_hash = content_hash
            if skill_dir is not None:
                bundle.skill_dir = skill_dir
            if import_root is not None or content_hash is not None:
                bundle.import_root = import_root
            bundle.plugin_api_generation = self._plugin_api_generation
            digest = uuid.uuid4().hex
            bundle.generation_digest = digest
            self._published_generation = digest
            for live, staged_map, bundle_keys, stamp in (
                (_tools, staged.tools, bundle.tools, True),
                (_routes, staged.routes, bundle.routes, True),
                (_ws_handlers, staged.ws_handlers, bundle.ws_handlers, True),
                (_ui_tabs, staged.ui_tabs, bundle.ui_tabs, False),
                (_settings_sections, staged.settings_sections, bundle.settings_sections, False),
            ):
                if stamp:
                    # Staged-protocol restamp (ABI-9): a bundle may publish
                    # MORE than once (the server-side companion recovery path
                    # re-publishes onto live surfaces). Each publication mints
                    # ONE digest, so every ALREADY-published descriptor this
                    # bundle owns is re-stamped in the same lock hold — the
                    # per-surface provenance stamp and
                    # ``bundle.generation_digest`` can never diverge.
                    for key in bundle_keys:
                        published = live.get(key)
                        if isinstance(published, dict):
                            published["extension_generation"] = digest
                            published["plugin_api_generation"] = self._plugin_api_generation
                for key, value in staged_map.items():
                    if stamp:
                        value["extension_generation"] = digest
                        value["plugin_api_generation"] = self._plugin_api_generation
                    live[key] = value
                    bundle_keys.append(key)
            bundle.unload_callbacks.extend(staged.unload_callbacks)
            # Recorded on the bundle BEFORE the attach below so a mid-attach
            # failure's unload reaps every effect (unsubscribing a
            # never-attached sub_id / stopping a never-spawned companion is a
            # no-op) — nothing can end up outside the bundle's reach.
            bundle.event_subscriptions.extend(sub.sub_id for sub in staged.event_subscriptions)
            for name in staged.companion_names:
                _record_companion_name(bundle, name)
            if self not in bundle.api_instances:
                bundle.api_instances.append(self)
            _load_failures.pop(self._skill, None)
            self._registration_closed = True
            self._staged = _StagedRegistrations()
            # ATTACH (post-swap, same lock hold): deferred side effects start
            # only against the already-published bundle.
            try:
                if staged.companion_spawns:
                    # Fix-round-5/6: the companion env materializes only HERE,
                    # after the generation fence admitted this publication —
                    # the state dir, the auth token (a mint during descriptor
                    # build would let a stale recovery rotate auth_token.json
                    # before its typed refusal, de-authorizing the LIVE
                    # publication's companions) and the settings-derived
                    # values (``load_settings`` takes the settings lock and
                    # may persist a migration). The pre-fence build is pure.
                    self._state_dir.mkdir(parents=True, exist_ok=True)
                    token = mint_skill_token(self._state_dir, self._skill, self._skill_dir)
                    for spawn in staged.companion_spawns:
                        materialize_companion_env(self, spawn.descriptor, spawn.spec, token)
                bus = get_global_event_bus()
                for sub in staged.event_subscriptions:
                    bus.subscribe(self._skill, sub.topic, sub.handler, sub_id=sub.sub_id)
                for spec in staged.supervised_tasks:
                    future = self._start_supervised_task(spec)
                    if future is not None:
                        bundle.supervised_futures.append(future)
                for spawn in staged.companion_spawns:
                    supervisor = get_global_supervisor()
                    if supervisor is None:
                        raise ExtensionRegistrationError("companion supervisor is not initialized")
                    supervisor.start(spawn.descriptor)
            except Exception:
                # Disclosure: the snapshot IS published; the raise routes the
                # caller into the standard dispose+unload path, which reaps
                # everything the bundle recorded above.
                log.warning(
                    "extension %s post-swap side effect failed; the published "
                    "bundle is disposed through the standard unload path",
                    self._skill, exc_info=True,
                )
                raise

    def _close_runtime_access(self) -> None:
        with _lock:
            self._registration_closed = True
            self._runtime_closing = True
        with self._api_lock:
            with _lock:
                self._runtime_closed = True

    # --- runtime access ---

    def log(self, level: str, message: str, **fields: Any) -> None:
        lvl = str(level or "info").lower()
        levels = {"debug": 10, "info": 20, "warning": 30, "error": 40}
        log.log(
            levels.get(lvl, 20),
            "[ext %s] %s %s",
            self._skill,
            message,
            fields if fields else "",
        )

    def get_settings(self, keys: Sequence[str]) -> Dict[str, Any]:
        with self._api_lock:
            with _lock:
                if self._runtime_closing or self._runtime_closed or self._skill in _unloading:
                    return {}
            if "read_settings" not in self._permissions:
                # Missing permission fails closed without leaking key presence.
                return {}
            settings = self._settings_reader() or {}
            with _lock:
                if self._runtime_closing or self._runtime_closed or self._skill in _unloading:
                    return {}
            out: Dict[str, Any] = {}
            protected_upper = {k.upper() for k in FORBIDDEN_SKILL_SETTINGS}
            protected_upper.update(requested_core_setting_keys(list(self._env_allow)))
            for raw_key in keys or ():
                key = str(raw_key).strip()
                canonical = key.upper()
                if not key:
                    continue
                if canonical in protected_upper and canonical not in self._granted_upper:
                    # Do not reveal forbidden/core key presence without a grant.
                    continue
                if key not in self._env_allow and canonical not in self._env_allow_upper:
                    continue
                settings_key = canonical if canonical in protected_upper else key
                if settings_key in settings:
                    out[settings_key] = settings[settings_key]
            return out

    def get_state_dir(self) -> str:
        return str(self._state_dir)

    def skill_job_dir(self, job_id: str) -> pathlib.Path:
        raw = str(job_id or "").strip()
        safe = "".join(
            ch if ch.isalnum() or ch in "-_." else "_"
            for ch in raw
        ).strip("._")
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        prefix = (safe or "_job")[:55].rstrip("._-") or "_job"
        safe = f"{prefix}-{digest}"
        root = self._state_dir / "jobs" / safe
        for child in ("assets", "output", "tmp"):
            (root / child).mkdir(parents=True, exist_ok=True)
        return root

    def get_skill_token(self) -> SkillToken:
        return SkillToken(mint_skill_token(self._state_dir, self._skill, self._skill_dir))

    def get_runtime_info(self) -> RuntimeInfo:
        """Return the PluginAPI runtime-info snapshot without manifest I/O."""
        try:
            from ouroboros.config import (
                get_runtime_mode as _get_runtime_mode,
                DATA_DIR as _DATA_DIR,
            )
            runtime_mode = _get_runtime_mode()
            data_dir = str(_DATA_DIR)
        except Exception:
            runtime_mode = "advanced"
            data_dir = ""
        try:
            from ouroboros import get_version as _get_version
            app_version = str(_get_version())
        except Exception:
            app_version = ""
        try:
            from ouroboros.config import AGENT_SERVER_PORT as _agent_port, PORT_FILE as _PORT_FILE
            server_port = 0
            try:
                port_text = pathlib.Path(_PORT_FILE).read_text(encoding="utf-8").strip()
                if port_text:
                    server_port = int(port_text)
            except Exception:
                server_port = 0
            if server_port <= 0:
                server_port = int(_agent_port)
        except Exception:
            server_port = 0
        skill_dir = str(getattr(self, "_skill_dir", "") or "")
        mode = current_execution_mode()
        return {
            "runtime_mode": runtime_mode,
            "app_version": app_version,
            "data_dir": data_dir,
            "skill_dir": skill_dir,
            "state_dir": str(self._state_dir),
            "server_port": server_port,
            # Capability negotiation: an extension can branch on its execution mode
            # instead of calling an unavailable capability and aborting register().
            "execution_mode": mode.value,
            "capabilities": sorted(available_capabilities(mode)),
            # ABI-1: the generation NEGOTIATED for this extension (LEGACY for
            # grandfathered field-less payloads), not the host's own version.
            "plugin_api_version": self._plugin_api_generation,
        }
