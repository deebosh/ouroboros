"""Host-owned pre-dispatch guards: capability/resource, ephemeral, managed-update and skill-payload constraints.

Every span is extracted VERBATIM from the parent's tip bytes by
scripts/v7next_transplant.py (D18/D33 module-handle split, proof-checked);
the parent re-exports every moved name, so historical imports and
monkeypatch targets keep working unchanged.
"""

from __future__ import annotations

import logging
import os
import pathlib

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation-only imports (inert at runtime)
    from ouroboros.contracts.task_constraint import TaskConstraint
    from typing import Any
    from typing import Dict
    from typing import List
    from typing import Optional

# The logger name is pinned to the parent's literal namespace so the
# extraction does not silently rename the log stream.
log = logging.getLogger("ouroboros.tools.registry")


def _registry():
    """The parent module, read at call time.

    The parent owns the rebindable module state and the members tests
    monkeypatch there; reading them through the module at each call keeps
    one binding, where a from-import would freeze the value this leaf saw
    at import time (the owner-approved D18/D33 mechanical exception).
    """
    from ouroboros.tools import registry

    return registry


def _executor_backend_candidate_allowed(ctx: Any, candidate: str, allowed_roots: List[pathlib.Path]) -> bool:
    try:
        from ouroboros.workspace_executor import executor_ref_from_ctx as _executor_ref_from_ctx
        from ouroboros.workspace_executor import map_backend_path as _executor_map_backend_path

        executor_ref = _executor_ref_from_ctx(ctx)
        if executor_ref is None:
            return False
        resolved = _executor_map_backend_path(executor_ref, candidate)
        return any(
            resolved.is_relative_to(root) or _registry()._path_is_relative_to_casefold(resolved, root)
            for root in allowed_roots
        )
    except Exception:
        return False


def _command_mentions_protected_root(cmd_path_lower: str, root_text: str) -> bool:
    """Boundary-aware path containment for the workspace shell guard.

    True only when ``root_text`` (a normalised, lower-cased protected root path)
    appears in the command as a whole path or a parent prefix at a real path
    boundary — NOT as an incidental substring of an unrelated path that merely
    shares the prefix (e.g. protected ``/x/data`` must not match ``/x/database``).
    Used as a coarse catch-all for runtime paths embedded in non-tokenised text
    (e.g. inside a ``python -c`` string); the precise per-token containment loop
    still does the authoritative active/protected classification.
    """
    if not root_text:
        return False
    norm = root_text.rstrip("/")
    if not norm:
        return False
    span = len(norm)
    limit = len(cmd_path_lower)
    start = 0
    while True:
        idx = cmd_path_lower.find(norm, start)
        if idx < 0:
            return False
        end = idx + span
        nxt = cmd_path_lower[end] if end < limit else ""
        # Boundary = end-of-string, a path separator (child path), or a shell
        # token delimiter (the exact path). A trailing path char (letter/digit/
        # ``.``/``-``/``_``) means a DIFFERENT sibling path → keep scanning.
        if nxt == "" or nxt == "/" or nxt in " \t\"')(;:,&|<>":
            return True
        start = end


def _stray_skill_payload_failsoft(root_arg: str, workspace_mode: bool, task_constraint: Any) -> bool:
    """Whether stray bucket/skill_name on a write tool should be DROPPED rather than
    surfaced as SKILL_PAYLOAD_ARG_ERROR. Fail-soft ONLY for a WORKSPACE edit that is
    NOT skill-authoring: there bucket/skill_name are model noise (the B2 footgun —
    reflexive bucket="external" on an /app edit). In light/advanced non-workspace
    skill-authoring (or an explicit root=skill_payload / skill_repair) the specific
    error is the intended helpful signal."""
    skill_payload_intent = root_arg == "skill_payload" or bool(
        task_constraint and getattr(task_constraint, "mode", "") == "skill_repair"
    )
    return bool(workspace_mode and not skill_payload_intent)


def _managed_update_code_tool_block(ctx: Any, name: str) -> str:
    """Block a repo-mutating code tool while a managed-update assisted merge is staged for
    ANOTHER task (P2/SC2). Returns a block message, or "" when allowed (this is the authorized
    resolution task, or no managed tx is active). A corrupt tx marker fails closed."""
    try:
        from supervisor.update_merge import managed_assisted_tx_for

        if managed_assisted_tx_for(
            getattr(ctx, "task_id", ""),
            getattr(ctx, "task_metadata", None),
        )[1]:
            return (
                f"⚠️ MANAGED_UPDATE_IN_PROGRESS: {name!r} is blocked while a managed update merge "
                "is being resolved (only its authorized resolution task may write the repo). "
                "Retry after the update lands or is rolled back."
            )
    except Exception:
        return (
            f"⚠️ MANAGED_UPDATE_STATE_UNAVAILABLE: {name!r} is blocked because the managed "
            "update transaction state could not be verified. Retry after the update state is "
            "available or repaired."
        )
    return ""


def _authorized_managed_update_resolver(ctx: Any) -> bool:
    """Whether this task is the durable tx-authorized assisted resolver.

    Fail-closed bool for every authority consumer (False = no extra powers).
    The AUTHORITY-READ failure is additionally distinguished from an honest
    "not the resolver" via a typed ctx marker (``_managed_authority_read_error``:
    set on an unreadable read AND on a corrupt tx marker, cleared on every
    healthy evaluation), so the review-subject builder can fail LOUDLY instead
    of silently reviewing a possibly-managed candidate as an ordinary full
    staged capture."""
    try:
        from supervisor.update_merge import authorized_assisted_task_strict

        marker_status, tx = authorized_assisted_task_strict(
            getattr(ctx, "task_id", ""),
            getattr(ctx, "task_metadata", None),
        )
        try:
            if marker_status == "corrupt":
                # A tx marker EXISTS but cannot be parsed: authority stays
                # False (fail-closed) for every bool consumer, but the loud
                # A4 channel must fire — clearing the marker here would let
                # the review subject silently treat a possibly-managed
                # candidate as an ordinary full staged diff.
                setattr(
                    ctx, "_managed_authority_read_error",
                    "update_tx_corrupt: the managed update transaction marker "
                    "exists but could not be parsed",
                )
            else:
                setattr(ctx, "_managed_authority_read_error", "")
        except Exception:
            pass
        return bool(tx)
    except Exception as exc:
        try:
            setattr(ctx, "_managed_authority_read_error", repr(exc))
        except Exception:
            pass
        return False


def _task_constraint_path_allowed(path_text: str, constraint: Optional[TaskConstraint], drive_root: pathlib.Path) -> bool:
    return _registry().is_skill_payload_path(
        drive_root,
        path_text or "",
        constraint=constraint,
        allow_short_relative=True,
        allow_control_plane=True,
    )


def _light_mode_payload_mutation_allowed(
    *,
    ctx: Any,
    tool_name: str,
    args: Dict[str, Any],
    runtime_mode: str,
    effective_constraint: Optional[TaskConstraint],
    implicit_skill_cwd_allowed: bool,
    allow_short_relative: bool,
) -> bool:
    """Return True for light-mode data skill payload edits that do not touch repo files."""

    # apply_patch/edit_batch are DELIBERATELY absent: they refuse data-plane roots
    # entirely (repo lanes only), so they can never be a payload edit — in light
    # mode they stay under the generic repo-mutation block like any repo write.
    if runtime_mode != "light" or tool_name not in {"edit_text", "write_file"}:
        return False
    requested_root = str(args.get("root", "") or "active_workspace")
    try:
        requested_root = _registry().normalize_root(requested_root)
    except Exception:
        requested_root = str(args.get("root", "") or "active_workspace")
    if requested_root in {"task_drive", "artifact_store", "user_files"}:
        return True
    legacy_data_skill_edit = False
    if tool_name == "edit_text" and requested_root == "active_workspace":
        try:
            legacy_target = _registry().resolve_skill_payload_target(
                pathlib.Path(ctx.drive_root),
                str(args.get("path", "") or ""),
            )
            legacy_data_skill_edit = legacy_target.target_path.exists() and not legacy_target.control_plane
        except Exception:
            legacy_data_skill_edit = False
    if requested_root not in {"runtime_data", "skill_payload"} and not legacy_data_skill_edit:
        return False
    return _registry().is_skill_payload_path(
        pathlib.Path(ctx.drive_root),
        str(args.get("path", "") or ""),
        constraint=effective_constraint,
        allow_short_relative=allow_short_relative,
        allow_control_plane=False,
    )


_HEAL_MODE_ALLOWED_TOOLS = frozenset({
    "read_file",
    "list_files",
    "write_file",
    "edit_text",
    "list_skills",
    "skill_review", "skill_preflight",
})


def _heal_protected_payload_sidecar(path_text: str) -> bool:
    return _registry().is_skill_payload_control_filename(path_text)


_WEB_TOOLS = frozenset({"web_search", "browse_page", "browser_action", "youtube_transcript"})


def _resource_allowed(ctx: Any, key: str) -> bool:
    metadata = getattr(ctx, "task_metadata", {}) if isinstance(getattr(ctx, "task_metadata", {}), dict) else {}
    contract = metadata.get("task_contract") if isinstance(metadata.get("task_contract"), dict) else {}
    if not contract and isinstance(getattr(ctx, "task_contract", None), dict):
        contract = getattr(ctx, "task_contract")
    resources = {}
    for source in (metadata, contract):
        raw = source.get("allowed_resources") if isinstance(source, dict) else None
        if isinstance(raw, dict):
            resources.update(raw)
    if not resources:
        return True
    for name in (key, f"allow_{key}"):
        value = resources.get(name)
        if isinstance(value, bool):
            return value
    if key == "web":
        for name in ("network", "allow_network", "internet", "external_network"):
            value = resources.get(name)
            if isinstance(value, bool) and not value:
                return False
    if key == "network":
        for name in ("web", "allow_web", "internet", "external_network"):
            value = resources.get(name)
            if isinstance(value, bool) and not value:
                return False
    return True


def _disabled_tools(ctx: Any) -> frozenset:
    """Tool names the task contract withholds (declarative tool policy).

    Independent of ``allowed_resources``: a caller can disable specific tools
    (e.g. the agent's web_search/browser/VLM tools for a faithful benchmark)
    WITHOUT setting web/network=false — so shell network egress (git/pip) stays
    available and the web<->network cross-implication in ``_resource_allowed``
    never fires.
    """
    metadata = getattr(ctx, "task_metadata", {}) if isinstance(getattr(ctx, "task_metadata", {}), dict) else {}
    contract = metadata.get("task_contract") if isinstance(metadata.get("task_contract"), dict) else {}
    if not contract and isinstance(getattr(ctx, "task_contract", None), dict):
        contract = getattr(ctx, "task_contract")
    names: set = set()
    for source in (metadata, contract):
        raw = source.get("disabled_tools") if isinstance(source, dict) else None
        if isinstance(raw, (list, tuple)):
            names.update(str(n).strip() for n in raw if str(n).strip())
    # D10 compatibility: `claude_code_edit` was retired; saved contracts that
    # withheld the external coding gateway keep withholding its SUCCESSOR — the
    # delegated coding session's start verb. The dead name stays in the set
    # too (harmless: nothing registers it), so old contracts round-trip as-is.
    if "claude_code_edit" in names:
        names.add("delegate_start")
    # Q1 rename compatibility: contracts that withheld `advisory_review` keep
    # withholding the SAME organ under its new name, and vice versa (a new
    # contract naming only the new spelling must also silence the alias).
    if "advisory_review" in names:
        names.add("preflight_review")
    if "preflight_review" in names:
        names.add("advisory_review")
    return frozenset(names)


_GITHUB_TOKEN_TOOLS = frozenset({
    "list_github_prs",
    "get_github_pr",
    "comment_on_pr",
    "list_github_issues",
    "get_github_issue",
    "comment_on_issue",
    "close_github_issue",
    "create_github_issue",
    "run_ci_tests",
    "submit_skill_to_hub",
    "generate_evolution_stats",
})


def _builtin_tool_availability(name: str, ctx: Any = None) -> tuple[bool, str, str]:
    """Return ``(available, reason, detail)`` for built-in tool credential gates.

    Predicates are lazy to avoid registry import cycles and discovery-time side effects.
    """
    # A bare registry (unit tests, static policy inventory, import-time introspection)
    # is a structural surface, not a running task capability envelope.
    if not str(getattr(ctx, "task_id", "") or "").strip():
        metadata = getattr(ctx, "task_metadata", {}) if ctx is not None else {}
        contract = getattr(ctx, "task_contract", {}) if ctx is not None else {}
        if not metadata and not contract:
            return True, "", ""
    tool = str(name or "").strip()
    if tool == "web_search":
        try:
            from ouroboros.tools.search import _available_web_search_backends

            if not _available_web_search_backends():
                return False, "missing_credential", "web_search_backend"
        except ImportError:
            return True, "", ""
        except Exception:
            return True, "", ""
    if tool in _GITHUB_TOKEN_TOOLS and not os.environ.get("GITHUB_TOKEN", "").strip():
        return False, "missing_credential", "GITHUB_TOKEN"
    return True, "", ""


def _payload_dispatch_constraint(
    ctx: Any,
    *,
    name: str,
    args: dict[str, Any],
    task_constraint: Optional[TaskConstraint],
    workspace_mode: bool,
) -> tuple[Optional[TaskConstraint], str]:
    """Preserve repair selectors without letting stray selectors retarget work."""

    raw_bucket = str(args.get("bucket", "") or "")
    raw_skill_name = str(args.get("skill_name", "") or "")
    explicit_skill_root = str(args.get("root", "") or "").strip().lower() == "skill_payload"
    short_form_decision = None if explicit_skill_root else _registry().decide_payload_short_form(
        bucket=raw_bucket,
        skill_name=raw_skill_name,
        path_text=str(args.get("path", "") or "."),
        repo_dir=pathlib.Path(ctx.repo_dir),
        drive_root=pathlib.Path(ctx.drive_root),
    )
    if explicit_skill_root:
        # Binding selection already handled the explicit target. This legacy
        # constraint exists only for the light-mode data-payload carve-out.
        synthesized = _registry().synthesize_payload_constraint(raw_bucket, raw_skill_name)
    else:
        synthesized = (
            short_form_decision.constraint
            if short_form_decision is not None
            and task_constraint
            and task_constraint.mode == "skill_repair"
            else None
        )

    if (
        (raw_bucket or raw_skill_name)
        and short_form_decision is not None
        and short_form_decision.error
        and name in {"write_file", "edit_text"}
    ):
        root_arg = str(args.get("root", "") or "").strip().lower()
        if _stray_skill_payload_failsoft(root_arg, workspace_mode, task_constraint):
            log.info(
                "Ignoring stray bucket/skill_name on %s (workspace edit, root=%s): %s",
                name,
                root_arg or "active_workspace",
                short_form_decision.error[:80],
            )
            args.pop("bucket", None)
            args.pop("skill_name", None)
            synthesized = None
        else:
            return None, f"⚠️ SKILL_PAYLOAD_ARG_ERROR: {short_form_decision.error}"

    redirect_err = _registry().cross_skill_redirect_error(task_constraint, synthesized)
    if redirect_err and name in {"write_file", "edit_text"}:
        return None, f"⚠️ SKILL_REDIRECT_BLOCKED: {redirect_err}"
    if task_constraint and task_constraint.mode == "skill_repair":
        return task_constraint, ""
    return synthesized or task_constraint, ""


_EPHEMERAL_ALLOWED_TOOLS = frozenset({
    # read / inspect
    "read_file", "query_code", "search_code", "list_files", "web_search", "browse_page",
    "chat_history", "recent_tasks", "get_task_result", "vcs_diff", "vcs_status",
    "analyze_screenshot", "vlm_query",
    # decide / route / spawn-owner-task / reply
    "route_to_project", "promote_chat_to_task", "steer_task", "list_projects", "send_photo",
})
