"""``_store_task_result`` persistence semantics.

Split out of ``tests/test_agent_task_pipeline.py`` when that module was divided
by theme; every moved block is verbatim. Covers review-evidence persistence,
the compact review projection (no raw model text), failed-status preservation,
and the unresolved-vs-recovered tool-failure outcome axes.
"""

import json
from types import SimpleNamespace

import ouroboros.agent_task_pipeline as pipeline


def test_store_task_result_persists_review_evidence(tmp_path):
    env = SimpleNamespace(drive_root=tmp_path)

    pipeline._store_task_result(
        env=env,
        task={"id": "task-store", "type": "task", "text": "hi"},
        text="done",
        usage={"rounds": 2, "cost": 0.1},
        llm_trace={"tool_calls": [], "reasoning_notes": []},
        review_evidence={"has_evidence": True, "open_obligations": [{"item": "tests_affected"}]},
    )

    payload = json.loads((tmp_path / "task_results" / "task-store.json").read_text(encoding="utf-8"))
    assert payload["review_evidence"]["has_evidence"] is True
    assert payload["review_evidence"]["open_obligations"][0]["item"] == "tests_affected"


def test_store_task_result_persists_only_compact_review_projection(tmp_path):
    env = SimpleNamespace(drive_root=tmp_path)
    trace = {
        "tool_calls": [],
        "review_runs": [{
            "request": {"surface": "task_acceptance", "policy": {"min_successful_slots": 1}},
            "authority": "host_root",
            "aggregate_signal": "DEGRADED",
            "actors": [{
                "slot_id": "slot_1", "model": "openai/gpt-5.6-sol", "status": "ok",
                "parsed": {"verdict": "DEGRADED", "summary": "not enough evidence"},
                "signal": "DEGRADED", "raw_text": "PRIVATE RAW MODEL RESPONSE",
            }],
        }],
    }
    pipeline._store_task_result(
        env=env,
        task={"id": "task-review-projection", "type": "task", "text": "hi"},
        text="done",
        usage={"rounds": 1, "cost": 0.0},
        llm_trace=trace,
        review_evidence={},
    )

    payload = json.loads(
        (tmp_path / "task_results" / "task-review-projection.json").read_text(encoding="utf-8")
    )
    actor = payload["review_projection"]["panels"][0]["actors"][0]
    assert actor["model"] == "openai/gpt-5.6-sol"
    assert actor["parse_status"] == "valid"
    assert actor["semantic_verdict"] == "DEGRADED"
    assert "raw_text" not in actor
    assert "PRIVATE RAW MODEL RESPONSE" not in json.dumps(payload)


def test_store_task_result_preserves_failed_status(tmp_path):
    from ouroboros.task_results import STATUS_FAILED, write_task_result

    env = SimpleNamespace(drive_root=tmp_path)
    write_task_result(tmp_path, "task-failed", STATUS_FAILED, result="initial failure")

    pipeline._store_task_result(
        env=env,
        task={"id": "task-failed", "type": "task", "text": "hi"},
        text="final failure reply",
        usage={"rounds": 1, "cost": 0.0},
        llm_trace={"tool_calls": [], "reasoning_notes": []},
        review_evidence={},
    )

    payload = json.loads((tmp_path / "task_results" / "task-failed.json").read_text(encoding="utf-8"))
    assert payload["status"] == STATUS_FAILED
    assert payload["result"] == "final failure reply"


def test_store_task_result_marks_unresolved_tool_failure_failed(tmp_path):
    from ouroboros.task_results import STATUS_COMPLETED

    env = SimpleNamespace(drive_root=tmp_path)

    pipeline._store_task_result(
        env=env,
        task={"id": "task-tool-failed", "type": "task", "text": "make file"},
        text="Created the file.",
        usage={"rounds": 2, "cost": 0.0},
        llm_trace={
            "tool_calls": [{
                "tool": "run_command",
                "args": {"cmd": "python3 -c ..."},
                "result": "⚠️ ARTIFACT_OUTPUT_ERROR: command succeeded but declared output registration failed.",
                "is_error": True,
                "status": "artifact_output_error",
            }],
            "reasoning_notes": [],
        },
        review_evidence={},
    )

    payload = json.loads((tmp_path / "task_results" / "task-tool-failed.json").read_text(encoding="utf-8"))
    assert payload["status"] == STATUS_COMPLETED
    assert payload["outcome_axes"]["execution"]["status"] == "degraded"
    assert payload["outcome_axes"]["objective"]["status"] == "not_evaluated"
    assert payload["reason_code"] == "tool_failure"
    assert payload["loop_outcome"]["failure"]["tool_errors"][0]["status"] == "artifact_output_error"


def test_store_task_result_allows_recovered_tool_failure_success(tmp_path):
    from ouroboros.task_results import STATUS_COMPLETED

    env = SimpleNamespace(drive_root=tmp_path)

    pipeline._store_task_result(
        env=env,
        task={"id": "task-tool-recovered", "type": "task", "text": "make file"},
        text="Created the file.",
        usage={"rounds": 3, "cost": 0.0},
        llm_trace={
            "tool_calls": [
                {
                    "tool": "edit_text",
                    "args": {"path": "Desktop/report.html"},
                    "result": "⚠️ EDIT_TEXT_ERROR: old_str matched 0 times",
                    "is_error": True,
                    "status": "edit_text_blocked",
                },
                {
                    "tool": "write_file",
                    "args": {"root": "user_files", "path": "Desktop/report.html"},
                    "result": "OK: wrote user_files:Desktop/report.html\nARTIFACT_OUTPUTS: registered user file -> artifact_store:report.html",
                    "is_error": False,
                    "status": "ok",
                    "artifact_registered": True,
                },
            ],
            "reasoning_notes": [],
        },
        review_evidence={},
    )

    payload = json.loads((tmp_path / "task_results" / "task-tool-recovered.json").read_text(encoding="utf-8"))
    assert payload["status"] == STATUS_COMPLETED
    assert payload["outcome_axes"]["execution"]["status"] == "ok"
    assert payload["outcome_axes"]["objective"]["status"] == "not_evaluated"
    assert payload["loop_outcome"]["failure"] is None


# ---------------------------------------------------------------------------
# Fork ibl-local-27745117e0e1: host-bound work_uncommitted enforcement.
# Kept in this file (not test_agent_task_pipeline.py) after the v7 theme split;
# the upstream split did not carry these fork-only cases.
# ---------------------------------------------------------------------------


def _work_uncommitted_loop_outcome(tmp_path):
    """A real ``derive_loop_outcome`` result flagged REASON_WORK_UNCOMMITTED: an
    otherwise-clean run against a repo with an uncommitted tracked change."""
    import subprocess

    from ouroboros.outcomes import REASON_WORK_UNCOMMITTED, derive_loop_outcome

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (("init", "-q"), ("config", "user.email", "t@t"), ("config", "user.name", "t")):
        subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=str(repo), check=True, capture_output=True)
    (repo / "a.py").write_text("x = 2  # changed, never committed\n")

    loop_outcome = derive_loop_outcome(
        "I implemented the change to a.py; the tests pass.",
        {"rounds": 3, "cost": 0.0},
        {"tool_calls": [], "reasoning_notes": []},
        repo_dir=repo,
    )
    assert loop_outcome["reason_code"] == REASON_WORK_UNCOMMITTED
    return loop_outcome


def test_store_task_result_fails_host_bound_task_with_uncommitted_diff(tmp_path):
    """ibl-local-27745117e0e1 enforcement: a host-bound task that left its own
    tracked changes uncommitted finalizes STATUS_FAILED, not STATUS_COMPLETED —
    the uncommitted diff IS the failure. The axes still carry the typed reason
    and the file list."""
    from ouroboros.task_results import STATUS_FAILED

    env = SimpleNamespace(drive_root=tmp_path)
    loop_outcome = _work_uncommitted_loop_outcome(tmp_path)

    pipeline._store_task_result(
        env=env,
        task={"id": "task-uncommitted", "type": "task", "text": "implement it"},
        text="I implemented the change to a.py; the tests pass.",
        usage={"rounds": 3, "cost": 0.0},
        llm_trace={"tool_calls": [], "reasoning_notes": []},
        review_evidence={},
        loop_outcome=loop_outcome,
    )

    payload = json.loads((tmp_path / "task_results" / "task-uncommitted.json").read_text(encoding="utf-8"))
    assert payload["status"] == STATUS_FAILED
    assert payload["reason_code"] == "work_uncommitted"
    assert payload["outcome_axes"]["execution"]["status"] == "degraded"
    assert any(
        "a.py" in line
        for line in payload["loop_outcome"]["failure"].get("files", [])
    )


def test_store_task_result_does_not_fail_subagent_with_uncommitted_diff(tmp_path):
    """A subagent's uncommitted worktree is the parent's concern, not a task
    failure — the enforcement is scoped to host-bound (non-subagent) tasks."""
    from ouroboros.task_results import STATUS_COMPLETED

    env = SimpleNamespace(drive_root=tmp_path)
    loop_outcome = _work_uncommitted_loop_outcome(tmp_path)

    pipeline._store_task_result(
        env=env,
        task={
            "id": "task-sub-uncommitted", "type": "task", "text": "implement it",
            "delegation_role": "subagent",
        },
        text="Implemented; parent will absorb the patch.",
        usage={"rounds": 3, "cost": 0.0},
        llm_trace={"tool_calls": [], "reasoning_notes": []},
        review_evidence={},
        loop_outcome=loop_outcome,
    )

    payload = json.loads(
        (tmp_path / "task_results" / "task-sub-uncommitted.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == STATUS_COMPLETED
    # The typed reason is still recorded — observation preserved, verdict withheld.
    assert payload["reason_code"] == "work_uncommitted"
