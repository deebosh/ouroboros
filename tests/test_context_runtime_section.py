"""The runtime section and the user content the context builder emits.

Split verbatim out of ``tests/test_context.py`` by theme. This module owns the
force-plan notice that must not rewrite the user's text, the ephemeral force plan that
only routes, the light-mode rule and filesystem affordances the runtime section states,
the workspace rules that preserve system review/commit authority, the host routing
manifest and manual contract, the improvement backlog digest, and the runtime_env
block.
"""

from __future__ import annotations

import inspect
import json

import pytest

from ouroboros.context import build_runtime_section, build_user_content

from tests._context_shared import _make_health_env

def test_build_llm_messages_has_no_recorder_only_soft_cap_chain():
    from ouroboros import context as context_module
    from ouroboros.context import build_llm_messages

    assert "soft_cap_tokens" not in inspect.signature(build_llm_messages).parameters
    assert not hasattr(context_module, "apply_message_token_soft_cap")
    source = inspect.getsource(build_llm_messages)
    assert "estimated_tokens_before" not in source
    assert "trimmed_sections" not in source
    assert "context_fit" in source


@pytest.mark.parametrize("enforcement", ["blocking", "advisory"])
def test_force_plan_metadata_adds_structured_notice_without_rewriting_user_text(
    monkeypatch, enforcement,
):
    monkeypatch.setenv("OUROBOROS_REVIEW_ENFORCEMENT", enforcement)
    content = build_user_content(
        {
            "text": "Fix the marketplace retry flow.",
            "metadata": {"force_plan": True, "force_plan_source": "swarm"},
        }
    )

    assert content.startswith("[SWARM_INITIATIVE]")
    assert "Source: swarm." in content
    assert f"Resolved review enforcement: {enforcement}." in content
    assert "Under blocking" in content
    assert "non-mutating preparation" in content
    assert "begin implementation only after review closes" in content
    # Fan-out integration mechanics (owner-approved, 2026-08-05): parallel
    # children cannot see each other's edits, so a plan gives them disjoint
    # write regions or plans the parent synthesis for the expected overlap.
    assert "cannot see each other's edits" in content
    assert "disjoint write regions" in content
    assert content.rstrip().endswith("Fix the marketplace retry flow.")


def test_ephemeral_force_plan_is_routing_only_and_transfers_work():
    content = build_user_content({
        "text": "Fix the marketplace retry flow.",
        "_ephemeral_turn": True,
        "metadata": {"force_plan": True, "force_plan_source": "swarm"},
    })

    assert content.startswith("[SWARM_ROUTING_INTENT]")
    assert "exactly one NEW managed root" in content
    assert "do not execute it" in content
    assert content.rstrip().endswith("Fix the marketplace retry flow.")


def test_runtime_section_includes_light_runtime_mode_rule(tmp_path, monkeypatch):
    env = _make_health_env(tmp_path)
    monkeypatch.setattr("ouroboros.config.get_runtime_mode", lambda: "light")
    section = build_runtime_section(env, {"id": "task-1", "type": "task"})
    payload = json.loads(section.split("\n\n", 1)[1])

    assert payload["runtime_mode"] == "light"
    assert "forbids Ouroboros repo mutation" in payload["runtime_mode_rule"]
    assert "user_files" in payload["runtime_mode_rule"]
    assert "artifact_store" in payload["runtime_mode_rule"]
    assert "explicit scoped skill-payload work/repair" in payload["runtime_mode_rule"]
    assert "runtime_data/uploads" in payload["runtime_mode_rule"]


def test_runtime_section_includes_filesystem_affordances_with_ctx(tmp_path, monkeypatch):
    from ouroboros.tools.registry import ToolContext

    env = _make_health_env(tmp_path)
    monkeypatch.setattr("ouroboros.config.get_runtime_mode", lambda: "light")
    ctx = ToolContext(repo_dir=tmp_path / "repo", drive_root=tmp_path)

    section = build_runtime_section(env, {"id": "task-1", "type": "task"}, ctx=ctx)
    payload = json.loads(section.split("\n\n", 1)[1])
    fs = payload["capabilities"]["filesystem"]

    assert fs["profile"] == "self_modification"
    assert "runtime_data" in fs["searchable_roots"]
    assert "task_drive" not in fs["searchable_roots"]
    assert "task_drive" in fs["allowed_shell_cwd_roots"]
    assert "status" in fs["git_readonly_subcommands"]
    assert "active_workspace" in fs["light_gated_roots"]


def test_runtime_section_external_workspace_includes_user_files_shell_affordance(tmp_path, monkeypatch):
    from ouroboros.tools.registry import ToolContext

    env = _make_health_env(tmp_path)
    monkeypatch.setattr("ouroboros.config.get_runtime_mode", lambda: "advanced")
    drive = tmp_path / "data"
    repo = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    drive.mkdir()
    repo.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    ctx = ToolContext(
        repo_dir=repo,
        drive_root=drive,
        workspace_root=workspace,
        workspace_mode="external",
    )

    section = build_runtime_section(env, {"id": "task-1", "type": "task"}, ctx=ctx)
    payload = json.loads(section.split("\n\n", 1)[1])
    fs = payload["capabilities"]["filesystem"]

    assert fs["profile"] == "external_workspace_task"
    assert "user_files" in fs["allowed_shell_cwd_roots"]


def test_runtime_section_workspace_rule_preserves_system_review_commit_authority(tmp_path, monkeypatch):
    env = _make_health_env(tmp_path)
    monkeypatch.setattr("ouroboros.config.get_runtime_mode", lambda: "advanced")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    section = build_runtime_section(
        env,
        {
            "id": "task-1",
            "type": "task",
            "workspace_root": str(workspace),
            "workspace_mode": "external",
            "memory_mode": "forked",
        },
    )
    rule = json.loads(section.split("\n\n", 1)[1])["active_workspace"]["rule"]

    assert "default to the active workspace" in rule
    assert "explicit typed root/cwd" in rule
    assert "self-review/commit tools remain available" in rule
    assert "self-review/commit tools are unavailable" not in rule


def test_runtime_section_omits_light_rule_for_advanced(tmp_path, monkeypatch):
    env = _make_health_env(tmp_path)
    monkeypatch.setattr("ouroboros.config.get_runtime_mode", lambda: "advanced")
    section = build_runtime_section(env, {"id": "task-1", "type": "task"})
    payload = json.loads(section.split("\n\n", 1)[1])

    assert payload["runtime_mode"] == "advanced"
    assert "runtime_mode_rule" not in payload


def test_runtime_section_includes_non_workspace_memory_boundary(tmp_path, monkeypatch):
    env = _make_health_env(tmp_path)
    monkeypatch.setattr("ouroboros.config.get_runtime_mode", lambda: "advanced")
    section = build_runtime_section(
        env,
        {
            "id": "task-1",
            "type": "task",
            "memory_mode": "forked",
            "drive_root": str(tmp_path / "child"),
            "child_drive_root": str(tmp_path / "child"),
            "budget_drive_root": str(tmp_path / "data"),
        },
    )
    payload = json.loads(section.split("\n\n", 1)[1])
    assert payload["task"]["memory_mode"] == "forked"
    assert payload["task"]["child_drive_root"].endswith("child")
    assert payload["task"]["budget_drive_root"].endswith("data")


def test_runtime_section_exposes_host_routing_manifest_and_manual_contract(tmp_path, monkeypatch):
    env = _make_health_env(tmp_path)
    monkeypatch.setattr("ouroboros.config.get_runtime_mode", lambda: "advanced")
    task = {
        "id": "decision-1",
        "type": "task",
        "metadata": {
            "current_chat": {
                "chat_id": 1,
                "running_tasks": [],
                "addressable_root_tasks": [{"task_id": "pending-1", "status": "pending"}],
            },
            "main_routing_manifest": {
                "projects": [{"project_id": "racer", "name": "Racer"}],
                "root_tasks": [{"task_id": "pending-1", "status": "pending"}],
            },
            "routing_contract": {
                "source_lane": "main",
                "on_uncertain_or_invalid_target": "needs_manual_target",
                "manual_options": [{"task_id": "pending-1"}],
            },
        },
    }

    payload = json.loads(build_runtime_section(env, task).split("\n\n", 1)[1])

    assert payload["current_chat"]["addressable_root_tasks"][0]["task_id"] == "pending-1"
    assert payload["main_routing_manifest"]["projects"][0]["project_id"] == "racer"
    assert payload["routing_contract"]["on_uncertain_or_invalid_target"] == "needs_manual_target"


def test_runtime_section_includes_improvement_backlog_digest(tmp_path):
    from ouroboros.context import build_llm_messages
    from ouroboros.memory import Memory

    class FakeEnv:
        def drive_path(self, p):
            return tmp_path / p

        def repo_path(self, p):
            return tmp_path / "repo" / p

        @property
        def repo_dir(self):
            return tmp_path / "repo"

        @property
        def drive_root(self):
            return tmp_path

    (tmp_path / "repo" / "prompts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "repo" / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory" / "knowledge").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)

    (tmp_path / "repo" / "prompts" / "SYSTEM.md").write_text("System prompt", encoding="utf-8")
    (tmp_path / "repo" / "BIBLE.md").write_text("Bible", encoding="utf-8")
    (tmp_path / "repo" / "README.md").write_text("README", encoding="utf-8")
    (tmp_path / "repo" / "docs" / "ARCHITECTURE.md").write_text('# Ouroboros v1.2.3', encoding="utf-8")
    (tmp_path / "repo" / "docs" / "DEVELOPMENT.md").write_text('# Dev', encoding="utf-8")
    (tmp_path / "repo" / "docs" / "CHECKLISTS.md").write_text('Checklist', encoding="utf-8")
    (tmp_path / "repo" / "VERSION").write_text("1.2.3", encoding="utf-8")
    (tmp_path / "repo" / "pyproject.toml").write_text('version = "1.2.3"', encoding="utf-8")
    (tmp_path / "state" / "state.json").write_text('{"spent_usd": 0}', encoding="utf-8")
    (tmp_path / "memory" / "identity.md").write_text("I am Ouroboros", encoding="utf-8")
    (tmp_path / "memory" / "scratchpad.md").write_text("scratchpad", encoding="utf-8")
    (tmp_path / "memory" / "knowledge" / "improvement-backlog.md").write_text(
        "# Improvement Backlog\n\n### ibl-1\n- status: open\n- created_at: 2026-04-14T09:00:00+00:00\n- source: execution_reflection\n- category: process\n- task_id: task-1\n- requires_plan_review: yes\n- fingerprint: fp-1\n- summary: Reduce recurring task friction around REVIEW_BLOCKED\n",
        encoding="utf-8",
    )

    messages, _ = build_llm_messages(
        env=FakeEnv(),
        memory=Memory(drive_root=tmp_path),
        task={"id": "task-a", "type": "task", "text": "hello"},
    )
    dynamic_text = messages[0]["content"][2]["text"]
    assert "## Improvement Backlog" in dynamic_text
    assert "Reduce recurring task friction around REVIEW_BLOCKED" in dynamic_text


class TestRuntimeEnvSection:
    """build_runtime_section includes runtime_env with platform and is_desktop."""

    def _make_env(self, tmp_path):
        class FakeEnv:
            repo_dir = tmp_path / "repo"
            drive_root = tmp_path

            def drive_path(self, p):
                return tmp_path / p

        (tmp_path / "state").mkdir(parents=True, exist_ok=True)
        (tmp_path / "state" / "state.json").write_text(
            '{"spent_usd": 0}', encoding="utf-8"
        )
        return FakeEnv()

    def test_runtime_env_present(self, tmp_path, monkeypatch):
        from ouroboros.context import build_runtime_section

        monkeypatch.delenv("OUROBOROS_DESKTOP_MODE", raising=False)
        env = self._make_env(tmp_path)
        section = build_runtime_section(env, {"id": "t1", "type": "task"})
        data = json.loads(section.split("## Runtime context\n\n", 1)[1])
        assert "runtime_env" in data
        assert "platform" in data["runtime_env"]
        assert isinstance(data["runtime_env"]["platform"], str)
        assert data["runtime_env"]["is_desktop"] is False

    def test_runtime_env_desktop_flag(self, tmp_path, monkeypatch):
        from ouroboros.context import build_runtime_section

        monkeypatch.setenv("OUROBOROS_DESKTOP_MODE", "1")
        env = self._make_env(tmp_path)
        section = build_runtime_section(env, {"id": "t2", "type": "task"})
        data = json.loads(section.split("## Runtime context\n\n", 1)[1])
        assert data["runtime_env"]["is_desktop"] is True
