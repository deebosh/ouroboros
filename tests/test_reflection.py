"""Tests for the read-side reflection CLI helper and ``__main__`` block.

Covers:
- ``build_reflection_from_logs``: latest-entry-wins, missing/corrupt fallthrough, blank task_id.
- ``python3 -m ouroboros.reflection`` CLI: success path prints the canonical
  trailing-JSON markers reconstructed from the stored structured fields; missing
  task_id exits non-zero with a typed stderr message.

No network, no LLM calls, no supervisor state. All paths are tmp_path-scoped.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ouroboros.reflection import (
    REFLECTIONS_FILENAME,
    build_reflection_from_logs,
)


def _write_entry(drive_root: Path, entry: dict) -> Path:
    """Append a single reflection entry to task_reflections.jsonl under drive_root."""
    logs_dir = drive_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / REFLECTIONS_FILENAME
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def _base_entry(task_id: str, **overrides) -> dict:
    entry = {
        "ts": "2026-09-02T20:43:09.518136+00:00",
        "task_id": task_id,
        "task_type": "evolution",
        "goal": "Test goal",
        "rounds": 5,
        "cost_usd": 0.0,
        "error_count": 0,
        "key_markers": [],
        "review_evidence": {},
        "reflection": "Some narrative from a real reflection.",
        "backlog_candidates": [],
        "memory_actions": [],
    }
    entry.update(overrides)
    return entry


def test_build_reflection_from_logs_returns_latest_matching_entry(tmp_path):
    drive_root = tmp_path
    _write_entry(drive_root, _base_entry("task-1", reflection="first", rounds=1))
    _write_entry(drive_root, _base_entry("task-2", reflection="second", rounds=2))
    _write_entry(
        drive_root,
        _base_entry(
            "task-1",
            reflection="second-for-task-1",
            rounds=11,
            memory_actions=[{"type": "scratchpad_append", "content": "x", "task_id": "task-1"}],
        ),
    )

    result = build_reflection_from_logs(drive_root, "task-1")
    assert result is not None
    # Latest wins: the second task-1 entry, not the first.
    assert result["reflection"] == "second-for-task-1"
    assert result["rounds"] == 11
    assert result["memory_actions"] == [
        {"type": "scratchpad_append", "content": "x", "task_id": "task-1"}
    ]


def test_build_reflection_from_logs_returns_none_for_blank_task_id(tmp_path):
    drive_root = tmp_path
    _write_entry(drive_root, _base_entry("task-x"))
    assert build_reflection_from_logs(drive_root, "") is None
    assert build_reflection_from_logs(drive_root, None) is None


def test_build_reflection_skips_corrupt_lines_keeps_clean(tmp_path):
    drive_root = tmp_path
    logs_dir = drive_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / REFLECTIONS_FILENAME
    # Mix of: clean line for task-A, corrupt line, clean line for task-B.
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(_base_entry("task-A", reflection="clean-A")) + "\n")
        handle.write("{this is not valid json\n")
        handle.write(json.dumps(_base_entry("task-B", reflection="clean-B")) + "\n")

    a = build_reflection_from_logs(drive_root, "task-A")
    b = build_reflection_from_logs(drive_root, "task-B")
    assert a is not None and a["reflection"] == "clean-A"
    assert b is not None and b["reflection"] == "clean-B"


@pytest.mark.parametrize(
    "drive_root, task_id, scenario",
    [
        ("/nonexistent/path/that/does/not/exist", "task-x", "missing_drive_root"),
        ("__DRIVE_ROOT__", "task-x", "missing_file"),
        ("__DRIVE_ROOT__", "no-such-task", "missing_task_id"),
    ],
)
def test_build_reflection_handles_missing_and_corrupt(tmp_path, drive_root, task_id, scenario):
    # Resolve the placeholder to a real tmp_path.
    if drive_root == "__DRIVE_ROOT__":
        drive_root = tmp_path
        # Intentionally do NOT create logs/ — file missing case.
    result = build_reflection_from_logs(drive_root, task_id)
    assert result is None, f"scenario={scenario} must return None"


def test_build_reflection_returns_none_for_corrupt_only_file(tmp_path):
    drive_root = tmp_path
    logs_dir = drive_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / REFLECTIONS_FILENAME).write_text("not-json-at-all\n", encoding="utf-8")
    assert build_reflection_from_logs(drive_root, "any") is None


def _run_cli(args, *, cwd, env=None, drive_root_override=None):
    """Run ``python3 -m ouroboros.reflection <args>`` from the system repo.

    ``env`` is merged on top of ``os.environ`` (only present keys override).
    If ``drive_root_override`` is given, ``OUROBOROS_DRIVE_ROOT`` is set to it.
    Returns a CompletedProcess-like with .returncode, .stdout, .stderr.
    """
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    if drive_root_override is not None:
        full_env["OUROBOROS_DRIVE_ROOT"] = str(drive_root_override)
    return subprocess.run(
        [sys.executable, "-m", "ouroboros.reflection", *args],
        cwd=cwd,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_main_emits_trailing_json_for_stored_entry(tmp_path):
    drive_root = tmp_path
    memory_actions = [
        {
            "type": "scratchpad_append",
            "content": "Note from cycle 40 investigation",
            "task_id": "task-cli-1",
        }
    ]
    backlog_candidates = [
        {
            "fingerprint": "abc123",
            "summary": "Fix reflection CLI",
            "category": "process",
            "source": "execution_reflection",
            "evidence": "Bug required manual JSON reproduction",
            "context": "",
            "proposed_next_step": "Add __main__ block",
            "task_id": "task-cli-1",
            "requires_plan_review": False,
            "priority": "med",
            "kind": "improvement",
        }
    ]
    _write_entry(
        drive_root,
        _base_entry(
            "task-cli-1",
            reflection="Reflection narrative for the CLI test.",
            memory_actions=memory_actions,
            backlog_candidates=backlog_candidates,
        ),
    )

    result = _run_cli(
        ["--task-id", "task-cli-1", "--drive-root", str(drive_root)],
        cwd="/opt/ouroboros",
    )
    assert result.returncode == 0, f"stderr: {result.stderr!r}, stdout: {result.stdout!r}"
    # The narrative precedes the trailing-JSON markers.
    assert "Reflection narrative for the CLI test." in result.stdout
    # Both JSON markers are present and reconstruct from the stored structured fields.
    assert "MEMORY_ACTIONS_JSON: " in result.stdout
    assert "BACKLOG_CANDIDATES_JSON: " in result.stdout
    # Round-trip parse the JSON lines to verify they match the stored fields.
    mem_line = next(
        line for line in result.stdout.splitlines() if line.startswith("MEMORY_ACTIONS_JSON: ")
    )
    bcl_line = next(
        line for line in result.stdout.splitlines() if line.startswith("BACKLOG_CANDIDATES_JSON: ")
    )
    assert json.loads(mem_line.split("MEMORY_ACTIONS_JSON: ", 1)[1]) == memory_actions
    assert json.loads(bcl_line.split("BACKLOG_CANDIDATES_JSON: ", 1)[1]) == backlog_candidates


def test_main_exits_nonzero_on_missing_task(tmp_path):
    drive_root = tmp_path
    # No entries written.
    result = _run_cli(
        ["--task-id", "no-such-task", "--drive-root", str(drive_root)],
        cwd="/opt/ouroboros",
    )
    assert result.returncode == 1
    # Typed stderr message — no traceback, mentions the missing task_id.
    assert "no reflection entry" in result.stderr
    assert "no-such-task" in result.stderr


def test_main_uses_drive_root_env_var(tmp_path):
    drive_root = tmp_path
    _write_entry(
        drive_root,
        _base_entry("task-env-1", reflection="env-var driven"),
    )
    # No --drive-root flag; env must be honored.
    result = _run_cli(
        ["--task-id", "task-env-1"],
        cwd="/opt/ouroboros",
        drive_root_override=str(drive_root),
    )
    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    assert "env-var driven" in result.stdout


def test_main_exits_2_when_no_task_id():
    result = _run_cli(
        [],
        cwd="/opt/ouroboros",
    )
    assert result.returncode == 2
    assert "no task_id given" in result.stderr
