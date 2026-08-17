"""The control tool producers publish their own result code, with unchanged text.

Same two things are pinned per site as in ``tests/test_core_native_results.py``,
because either one alone would let the cutover change what the loop records:

* the EXACT text the producer returned before it published anything — the string
  ABI the model sees is unchanged;
* what the published code says about the call, computed rather than restated. For
  the argument and access refusals that is equality with the single adapter's
  answer for the same bytes, so nativisation carries no owner semantics; for the
  owner-approved A.21 rows it is the OPPOSITE — the divergence has to be real, so
  an approved exception cannot rot into a silent one.
"""

from __future__ import annotations

import pathlib

import pytest

from ouroboros.tools import control_routing, control_runtime, control_scheduling, control_task_results
from ouroboros.tools.registry import ToolContext
from ouroboros.tools.tool_result import (
    LegacyTextResultAdapter,
    ToolResult,
    _install_tool_result_sidecar,
    _published_tool_result,
    _restore_tool_result_sidecar,
)


def _published(ctx, tool: str, call, *, owner_delta: str = "") -> ToolResult:
    """Run one producer under the registry's own result-consumption rule.

    ``registry_core`` installs a per-invocation sentinel and accepts the published
    result only when its text is exactly the string the handler returned; a helper
    called outside a dispatch must therefore still return that same text.
    """
    sentinel = object()
    token = _install_tool_result_sidecar(ctx, sentinel)
    try:
        text = call()
        published = _published_tool_result(ctx, sentinel)
    finally:
        _restore_tool_result_sidecar(token)
    assert isinstance(published, ToolResult), f"{tool}: producer published no typed result"
    assert published.text == text, f"{tool}: published text is not the returned text"
    adapter_code = LegacyTextResultAdapter.from_text(tool, text).code
    if owner_delta:
        assert published.code != adapter_code, (
            f"{tool}: {owner_delta} claims a divergence from the adapter that is not there"
        )
    else:
        assert published.code == adapter_code, (
            f"{tool}: published code diverges from the adapter answer for the same text"
        )
    return published


def _ctx(tmp_path: pathlib.Path) -> ToolContext:
    repo = tmp_path / "repo"
    drive = tmp_path / "drive"
    repo.mkdir()
    (drive / "logs").mkdir(parents=True)
    return ToolContext(repo_dir=repo, drive_root=drive, task_metadata={})


# --- Table 1: the adapter's own answer, published by the branch that made it ---


@pytest.mark.parametrize(
    ("label", "tool", "text"),
    [
        (
            "promote_no_objective",
            "promote_chat_to_task",
            "⚠️ TOOL_ARG_ERROR (promote_chat_to_task): objective is required",
        ),
        (
            "promote_bad_project_id",
            "promote_chat_to_task",
            "⚠️ TOOL_ARG_ERROR (promote_chat_to_task): project_id 'Not/Clean!' is not "
            "filesystem-clean; use lowercase alphanumeric/_/-/. (<=64 chars)",
        ),
        (
            "route_no_message",
            "route_to_project",
            "⚠️ TOOL_ARG_ERROR (route_to_project): message is required",
        ),
        (
            "steer_no_task_id",
            "steer_task",
            "⚠️ TOOL_ARG_ERROR (steer_task): task_id is required — pick one from "
            "current_chat.running_tasks (or promote_chat_to_task to start new work).",
        ),
        (
            "steer_no_message",
            "steer_task",
            "⚠️ TOOL_ARG_ERROR (steer_task): message is required.",
        ),
    ],
)
def test_routing_argument_refusals_publish_their_adapter_code(tmp_path, label, tool, text):
    ctx = _ctx(tmp_path)
    calls = {
        "promote_no_objective": lambda: control_routing._promote_chat_to_task(ctx, ""),
        "promote_bad_project_id": lambda: control_routing._promote_chat_to_task(
            ctx, "do the work", project_id="Not/Clean!"),
        "route_no_message": lambda: control_routing._route_to_project(ctx, project_id="p"),
        "steer_no_task_id": lambda: control_routing._steer_task(ctx, "", "hello"),
        "steer_no_message": lambda: control_routing._steer_task(ctx, "abc123", ""),
    }

    published = _published(ctx, tool, calls[label])

    assert published.code == "TOOL_ARG_ERROR"
    assert published.status == "error"
    assert published.text == text


@pytest.mark.parametrize(
    ("label", "text"),
    [
        (
            "evolution_block",
            "⚠️ RESTART_BLOCKED: in evolution mode, HEAD changed after the last reviewed local commit.",
        ),
        (
            "receipt_not_persisted",
            "⚠️ RESTART_BLOCKED: the exact evolution restart receipt could not be persisted (boom).",
        ),
    ],
)
def test_restart_denials_publish_their_adapter_code(tmp_path, monkeypatch, label, text):
    ctx = _ctx(tmp_path)
    ctx.current_task_type = "evolution"
    if label == "evolution_block":
        monkeypatch.setattr(
            control_runtime, "_evolution_restart_block_reason",
            lambda _ctx: "HEAD changed after the last reviewed local commit",
        )
    else:
        monkeypatch.setattr(control_runtime, "_evolution_restart_block_reason", lambda _ctx: "")

        def _boom(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(control_runtime, "run_cmd", _boom)

    published = _published(
        ctx, "request_restart", lambda: control_runtime._request_restart(ctx, "why"))

    assert published.code == "LEGACY_BLOCKED"
    assert published.status == "blocked"
    assert published.text == text


def test_subagent_constraint_denials_publish_their_adapter_code(tmp_path, monkeypatch):
    """Both guards that refuse an acting child name the denial themselves.

    The selector's own refusal and the one it delegates to the acting-constraint
    builder are the same policy answer, and the builder receives the invocation so
    the branch that made the decision is the branch that reports it.
    """
    import ouroboros.config as config

    monkeypatch.setattr(config, "get_allow_mutative_subagents", lambda _surface: False)
    ctx = _ctx(tmp_path)

    toggled_off = _published(
        ctx, "schedule_subagent",
        lambda: control_scheduling._build_acting_constraint(
            write_surface="self_worktree", write_root="", protected_paths_grant=False,
            external_tool_grants=None, parent_workspace_root="", ctx=ctx),
    )
    assert toggled_off.code == "ACCESS_BLOCKED"
    assert toggled_off.status == "blocked"
    assert toggled_off.text.startswith(
        "⚠️ MUTATIVE_SUBAGENTS_DISABLED: acting children with "
        "write_surface='self_worktree' are disabled here. "
    )

    readonly_parent = _published(
        ctx, "schedule_subagent",
        lambda: control_scheduling._select_subagent_constraint(
            "self_worktree", "", False, [], "", caller_readonly=True, ctx=ctx),
    )
    assert readonly_parent.code == "ACCESS_BLOCKED"
    assert readonly_parent.status == "blocked"
    assert readonly_parent.text == (
        "⚠️ MUTATIVE_SUBAGENTS_DISABLED: a read-only subagent cannot spawn a mutative (acting) "
        "child. Only the root agent, workspace tasks, or acting subagents may pass write_surface; "
        "schedule a read-only child instead."
    )


def test_a_direct_selector_call_without_an_invocation_still_returns_its_text(tmp_path, monkeypatch):
    """``ctx`` is optional, so a caller outside a dispatch keeps the exact string.

    The publication seam must not become a reason for the selector to require a
    context it does not otherwise need; without an invocation there is simply
    nothing to publish into.
    """
    import ouroboros.config as config

    monkeypatch.setattr(config, "get_allow_mutative_subagents", lambda _surface: False)

    refusal = control_scheduling._select_subagent_constraint("self_worktree", "", False, [], "")

    assert isinstance(refusal, str)
    assert refusal.startswith("⚠️ MUTATIVE_SUBAGENTS_DISABLED: acting children with ")


@pytest.mark.parametrize(
    ("label", "prefix"),
    [
        ("retired_param", "⚠️ TOOL_ARG_ERROR (schedule_subagent): effort was withdrawn: "),
        ("unsupported_param", "⚠️ TOOL_ARG_ERROR (schedule_subagent): unsupported argument(s): bogus."),
        ("validator_refusal", "⚠️ TOOL_ARG_ERROR (schedule_subagent): objective is required."),
        (
            "capability_arg_error",
            "⚠️ TOOL_ARG_ERROR (schedule_subagent): required_capabilities must be a list of strings.",
        ),
    ],
)
def test_schedule_argument_refusals_publish_their_adapter_code(tmp_path, label, prefix):
    ctx = _ctx(tmp_path)
    calls = {
        "retired_param": lambda: control_scheduling._schedule_task(ctx, effort="high"),
        "unsupported_param": lambda: control_scheduling._schedule_task(ctx, bogus=1),
        "validator_refusal": lambda: control_scheduling._schedule_task(ctx, objective=""),
        "capability_arg_error": lambda: control_scheduling._schedule_task(
            ctx, objective="o", expected_output="e", required_capabilities="shell"),
    }

    published = _published(ctx, "schedule_subagent", calls[label])

    assert published.code == "TOOL_ARG_ERROR"
    assert published.status == "error"
    assert published.text.startswith(prefix)


@pytest.mark.parametrize(
    ("label", "tool", "text"),
    [
        (
            "wait_task_bad_id",
            "wait_task",
            "⚠️ TOOL_ARG_ERROR (wait_task): task_id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127}",
        ),
        (
            "wait_tasks_empty",
            "wait_tasks",
            "⚠️ TOOL_ARG_ERROR (wait_tasks): task_ids must be a non-empty list.",
        ),
        (
            "wait_tasks_bad_id",
            "wait_tasks",
            "⚠️ TOOL_ARG_ERROR (wait_tasks): task_id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127}",
        ),
        (
            "wait_tasks_bad_mode",
            "wait_tasks",
            "⚠️ TOOL_ARG_ERROR (wait_tasks): mode must be all_terminal or any_terminal.",
        ),
    ],
)
def test_wait_argument_refusals_publish_their_adapter_code(tmp_path, label, tool, text):
    ctx = _ctx(tmp_path)
    calls = {
        "wait_task_bad_id": lambda: control_task_results._wait_for_task(ctx, "not a task id!"),
        "wait_tasks_empty": lambda: control_task_results._wait_for_tasks(ctx, []),
        "wait_tasks_bad_id": lambda: control_task_results._wait_for_tasks(ctx, ["not a task id!"]),
        "wait_tasks_bad_mode": lambda: control_task_results._wait_for_tasks(
            ctx, ["abc123"], mode="whenever"),
    }

    published = _published(ctx, tool, calls[label])

    assert published.code == "TOOL_ARG_ERROR"
    assert published.status == "error"
    assert published.text == text


def test_the_wait_set_cap_refusal_names_the_configured_cap(tmp_path):
    from ouroboros.config import MAX_ACTIVE_SUBAGENTS_HARD_CAP

    ctx = _ctx(tmp_path)
    oversized = [f"t{index}" for index in range(MAX_ACTIVE_SUBAGENTS_HARD_CAP + 1)]

    published = _published(
        ctx, "wait_tasks", lambda: control_task_results._wait_for_tasks(ctx, oversized))

    assert published.code == "TOOL_ARG_ERROR"
    assert published.text == (
        "⚠️ TOOL_ARG_ERROR (wait_tasks): task_ids is capped at "
        f"{MAX_ACTIVE_SUBAGENTS_HARD_CAP}."
    )
