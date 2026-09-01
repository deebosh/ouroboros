"""Host-side validation of surface descriptors returned by a child catalog run.

An isolated-dep extension registers its surfaces in a short-lived child process
and reports them back as plain descriptors. The child is outside the host trust
boundary, so every descriptor is re-validated here — namespace, provider-safe
name, method vocabulary, render schema — and staged onto the loader's ABI-9
publication snapshot before anything is installed.
"""

from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING, Any, Dict

from ouroboros.contracts.plugin_api import ExtensionRegistrationError, VALID_EXTENSION_ROUTE_METHODS
from ouroboros.extension_registry_state import (
    _lock,
    _routes,
    _settings_sections,
    _tools,
    _ui_tabs,
    _ws_handlers,
)
from ouroboros.extension_surface_names import (
    _EXTENSION_NAME_RE,
    _widget_geometry_from_render,
    _widget_span_from_render,
    extension_name_prefix,
)
from ouroboros.extension_ui_validation import (
    validate_settings_schema as _validate_settings_schema,
    validate_ui_render as _validate_ui_render,
)

if TYPE_CHECKING:  # pragma: no cover - annotation-only imports
    from ouroboros.extension_plugin_api import PluginAPIImpl
    from ouroboros.skill_loader import LoadedSkill


def _out_of_process_handler_proxy(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("extension surface is configured for out-of-process dispatch")


def _stage_out_of_process_surfaces(
    api: "PluginAPIImpl",
    skill: "LoadedSkill",
    catalog: Dict[str, Any],
) -> None:
    """Validate child-catalog descriptors and stage them on *api*'s snapshot.

    ABI-9: nothing is installed here — every descriptor is validated and
    staged through the same ``_stage_surface_locked`` seam the in-process
    ``register()`` window uses, so the whole out-of-process registration
    (surfaces AND companions) publishes as ONE validate -> swap -> attach
    transaction; a bad catalog publishes NOTHING rather than a prefix.
    """

    def _proxy(item: Dict[str, Any]) -> Dict[str, Any]:
        item["handler"] = _out_of_process_handler_proxy
        item["skill"] = skill.name
        item["out_of_process"] = True
        item["skills_repo_path"] = str(skill.skill_dir.parent)
        return item

    kinds = (
        ("tools", _validate_child_tool_descriptor, "name", True, _tools, "tool"),
        ("routes", _validate_child_route_descriptor, "path", True, _routes, "route"),
        ("ws_handlers", _validate_child_ws_descriptor, "type", True, _ws_handlers, "ws handler"),
        ("ui_tabs", _validate_child_ui_descriptor, None, False, _ui_tabs, "ui tab"),
        ("settings_sections", _validate_child_settings_descriptor, None, False, _settings_sections, "settings section"),
    )
    with _lock:
        for kind, validate, key_field, proxied, live, label in kinds:
            staged = getattr(api._staged, kind)
            for raw in catalog.get(kind) or []:
                item = validate(skill.name, dict(raw or {}))
                key = (
                    str(item.get(key_field) or "") if key_field
                    else str(item.pop("key", "") or "")
                )
                if not key:
                    continue
                api._stage_surface_locked(
                    live, staged, key, _proxy(item) if proxied else item, label,
                )


def _validate_child_catalog_namespace(skill_name: str, surface_kind: str, value: str) -> None:
    """Re-check child catalog namespaces at the host trust boundary."""

    if surface_kind in {"tool", "ws handler"}:
        expected = extension_name_prefix(skill_name)
    elif surface_kind == "route":
        expected = f"/api/extensions/{skill_name}/"
    elif surface_kind in {"ui tab", "settings section"}:
        expected = f"{skill_name}:"
    else:
        expected = ""
    if expected and not value.startswith(expected):
        raise ExtensionRegistrationError(
            f"out-of-process {surface_kind} {value!r} escaped extension namespace {expected!r}"
        )


def _validate_child_tool_descriptor(skill_name: str, item: Dict[str, Any]) -> Dict[str, Any]:
    name = str(item.get("name") or "")
    _validate_child_catalog_namespace(skill_name, "tool", name)
    if not _EXTENSION_NAME_RE.match(name):
        raise ExtensionRegistrationError(f"out-of-process tool {name!r} is not provider-safe")
    if not isinstance(item.get("schema", {}), dict):
        raise ExtensionRegistrationError(f"out-of-process tool {name!r} schema must be an object")
    item["schema"] = dict(item.get("schema") or {})
    item["description"] = str(item.get("description") or "")
    try:
        item["timeout_sec"] = max(1, int(item.get("timeout_sec") or 60))
    except (TypeError, ValueError) as exc:
        raise ExtensionRegistrationError(f"out-of-process tool {name!r} timeout_sec must be an integer") from exc
    return item


def _validate_child_route_descriptor(skill_name: str, item: Dict[str, Any]) -> Dict[str, Any]:
    path = str(item.get("path") or "")
    _validate_child_catalog_namespace(skill_name, "route", path)
    methods_iter = item.get("methods") or ("GET",)
    if isinstance(methods_iter, str):
        methods_iter = (methods_iter,)
    methods = tuple(dict.fromkeys(str(method).strip().upper() for method in methods_iter if str(method).strip()))
    if not methods:
        raise ExtensionRegistrationError(f"out-of-process route {path!r} methods must be non-empty")
    invalid = [method for method in methods if method not in VALID_EXTENSION_ROUTE_METHODS]
    if invalid:
        raise ExtensionRegistrationError(
            f"out-of-process route {path!r} methods {invalid!r} are unsupported; "
            f"expected subset of {sorted(VALID_EXTENSION_ROUTE_METHODS)}"
        )
    item["methods"] = methods
    return item


def _validate_child_ws_descriptor(skill_name: str, item: Dict[str, Any]) -> Dict[str, Any]:
    msg_type = str(item.get("type") or "")
    _validate_child_catalog_namespace(skill_name, "ws handler", msg_type)
    if not _EXTENSION_NAME_RE.match(msg_type):
        raise ExtensionRegistrationError(f"out-of-process ws handler {msg_type!r} is not provider-safe")
    return item


def _validate_child_ui_descriptor(skill_name: str, item: Dict[str, Any]) -> Dict[str, Any]:
    key = str(item.get("key") or "")
    _validate_child_catalog_namespace(skill_name, "ui tab", key)
    if not isinstance(item.get("render", {}), dict):
        raise ExtensionRegistrationError(f"out-of-process ui tab {key!r} render must be an object")
    render = _validate_ui_render(dict(item.get("render") or {}))
    item["render"] = render
    span = _widget_span_from_render(render)
    item["span"] = span
    item["grid_span"] = span
    item.update(_widget_geometry_from_render(render))
    return item


def _validate_child_settings_descriptor(skill_name: str, item: Dict[str, Any]) -> Dict[str, Any]:
    key = str(item.get("key") or "")
    _validate_child_catalog_namespace(skill_name, "settings section", key)
    if not isinstance(item.get("render", {}), dict):
        raise ExtensionRegistrationError(f"out-of-process settings section {key!r} render must be an object")
    item["render"] = _validate_settings_schema(dict(item.get("render") or {}))
    return item


def skill_state_path(drive_root: pathlib.Path, name: str) -> pathlib.Path:
    """The per-skill state-dir PATH without creating the directory.

    The generation-fenced companion recovery resolves this path BEFORE its
    fence (fix-round-6); creation belongs to the post-fence attach in
    ``_publish_registrations``, so a stale refusal creates no directories.
    ``skill_state_dir`` remains the creating variant for load paths.
    """
    from ouroboros.skill_loader import _sanitize_skill_name, _skills_state_root

    return _skills_state_root(pathlib.Path(drive_root)) / _sanitize_skill_name(name)


def materialize_companion_env(api: "PluginAPIImpl", descriptor: Any, spec: Dict[str, Any], token: str) -> None:
    """Fill one staged companion descriptor's env at publication (fix-round-6).

    Runs only inside ``_publish_registrations``'s post-swap attach, after the
    generation fence admitted the publication: the settings-derived values
    (``_scrub_env`` -> ``load_settings`` takes the settings lock and may
    persist a settings migration), the manifest env overlay, the Host Service
    bridge URL/token and the isolated-dep PYTHONPATH are all filled in HERE,
    keeping the pre-fence descriptor build purely computational.
    """
    from ouroboros.contracts.plugin_api import FORBIDDEN_SKILL_SETTINGS
    from ouroboros.extension_isolated_deps import _isolated_python_site_dirs
    from ouroboros.gateway.host_service import DEFAULT_HOST_SERVICE_HOST, host_service_port
    from ouroboros.tools.skill_exec import _scrub_env

    env = _scrub_env(
        list(api._env_allow),
        api._state_dir,
        api._skill,
        granted_keys=list(api._granted_upper),
    )
    reserved_env = {"HOST_SERVICE_TOKEN", "HOST_SERVICE_URL"}
    manifest_env = {
        str(key): str(value)
        for key, value in (spec.get("env") or {}).items()
        if str(key).upper() not in FORBIDDEN_SKILL_SETTINGS
        and str(key).upper() not in reserved_env
    }
    # Case-aware merge (delta finding D2-8): a manifest "Path" must REPLACE
    # the allowlisted "PATH" on Windows, never sit next to it — duplicate
    # case-variant env keys make CreateProcess-era spawns fail or pick an
    # undefined winner. Same contract as the executor-local service lane.
    from ouroboros.workspace_executor import overlay_env

    env = overlay_env(env, manifest_env)
    env["HOST_SERVICE_URL"] = f"http://{DEFAULT_HOST_SERVICE_HOST}:{host_service_port()}"
    env["HOST_SERVICE_TOKEN"] = token
    if api._skill_dir is not None:
        site_dirs = [str(path) for path in _isolated_python_site_dirs(api._skill_dir)]
        if site_dirs:
            existing_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = os.pathsep.join(
                [*site_dirs, existing_pythonpath] if existing_pythonpath else site_dirs
            )
    expected_runtime = str(spec.get("runtime") or "").strip()
    manifest_path_override = any(
        str(key).upper() == "PATH" for key in (spec.get("env") or {})
    )
    if expected_runtime in {"node", "npm"} and not manifest_path_override:
        # T14 emergency-only PATH prepend (see register_companion_process):
        # descriptor env keys win over the supervisor's `_companion_base_env`
        # merge, so the prepended PATH reaches the child and survives
        # supervisor restarts.
        from ouroboros.platform_layer import skill_node_emergency_path_dir

        node_prepend_dir = skill_node_emergency_path_dir()
        if node_prepend_dir:
            existing_path = env.get("PATH") or os.environ.get("PATH", "")
            env["PATH"] = (
                os.pathsep.join([node_prepend_dir, existing_path])
                if existing_path
                else node_prepend_dir
            )
    descriptor.env.clear()
    descriptor.env.update(env)
