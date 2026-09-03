"""Focused regressions for the first P3 acceptance-packet fix round."""

import json
from pathlib import Path
from types import SimpleNamespace


def test_prompt_projection_keeps_panels_ahead_of_an_oversized_lens():
    from ouroboros.review_evidence import format_review_evidence_for_prompt

    rendered = format_review_evidence_for_prompt(
        {"has_evidence": True, "oversized_lens": "L" * 30_000},
        max_chars=4_000,
        acceptance_panels=[{
            "panel_id": "panel-must-survive",
            "surface": "task_acceptance",
            "aggregate_signal": "PASS",
            "transport_status": "success",
            "parse_status": "valid",
            "reason": "deciding finding " + "R" * 1_000,
            "actors": [{
                "slot_id": "slot_1",
                "response_ref": {"call_id": "response-must-survive"},
            }],
        }],
    )

    assert rendered.startswith("TASK ACCEPTANCE PANELS:")
    assert "panel-must-survive" in rendered
    assert '"aggregate_signal": "PASS"' in rendered
    assert "deciding finding" in rendered
    assert '"reason_omitted_chars"' in rendered
    assert "response-must-survive" in rendered
    assert "OMISSION NOTE" in rendered


def test_partial_trajectory_is_non_resolving_but_complete_trajectory_resolves():
    from ouroboros.review_dispatch import task_acceptance_zero_physical_refusal
    from ouroboros.review_evidence import annotate_criteria_evidence_resolution
    from ouroboros.review_evidence_refs import acceptance_evidence_ref_vocabulary
    from ouroboros.review_substrate import task_acceptance_is_clean

    def _actor():
        return {
            "signal": "PASS",
            "parsed": {
                "outcome_tier": "solved",
                "criteria_used": [{
                    "criterion": "tool outcome",
                    "status": "supported",
                    "evidence_refs": ["tool_trajectory"],
                }],
            },
        }

    def _result(actor):
        return SimpleNamespace(
            aggregate_signal="PASS", degraded=False, actors=[actor],
        )

    partial = {
        "tool_trajectory": [{"tool": "read_file", "result_complete": False}],
        "__provenance__": {"tool_trajectory": "tool_result"},
    }
    partial_actor = _actor()
    annotate_criteria_evidence_resolution([partial_actor], partial)
    assert task_acceptance_zero_physical_refusal(partial) == {}
    assert acceptance_evidence_ref_vocabulary(partial)["tool_trajectory"] == "partial"
    assert task_acceptance_is_clean(_result(partial_actor)) is False

    complete = {
        "tool_trajectory": [{"tool": "read_file", "result_complete": True}],
        "__provenance__": {"tool_trajectory": "tool_result"},
    }
    complete_actor = _actor()
    annotate_criteria_evidence_resolution([complete_actor], complete)
    assert acceptance_evidence_ref_vocabulary(complete)["tool_trajectory"] == "packet_section"
    assert task_acceptance_is_clean(_result(complete_actor)) is True


def test_skill_history_root_task_projection_avoids_whole_history_reads(monkeypatch, tmp_path):
    from ouroboros.skill_readiness import _skill_names_from_review_history

    skill_dir = tmp_path / "state" / "skills" / "large-skill"
    skill_dir.mkdir(parents=True)
    history = skill_dir / "review_history.jsonl"
    history.write_text(
        (json.dumps({"root_task_id": "some-other-root", "padding": "x" * 200}) + "\n")
        * 20_000,
        encoding="utf-8",
    )
    (tmp_path / "state" / "skill_review_root_tasks.jsonl").write_text(
        json.dumps({"root_task_id": "root-wanted", "skill": "large-skill"}) + "\n",
        encoding="utf-8",
    )
    original = Path.read_text

    def _guarded_read(path, *args, **kwargs):
        if path.name == "review_history.jsonl":
            raise AssertionError("acceptance rebuilt the full skill review history")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _guarded_read)
    assert _skill_names_from_review_history(tmp_path, "root-wanted") == ["large-skill"]


def test_terminal_skill_review_updates_the_root_task_projection(tmp_path):
    from ouroboros.skill_review_runner import _append_terminal_history

    assert _append_terminal_history(
        tmp_path,
        "projected-skill",
        {"job_id": "job-1", "root_task_id": "root-1"},
        status="pass",
        terminal_reason="review_complete",
        ts="2026-09-03T00:00:00+00:00",
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / "state" / "skill_review_root_tasks.jsonl")
        .read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [{
        "ts": "2026-09-03T00:00:00+00:00",
        "root_task_id": "root-1",
        "skill": "projected-skill",
        "job_id": "job-1",
    }]


def test_skill_review_projection_is_enrolled_as_a_hot_store():
    from ouroboros.agent_startup_checks import _hot_store_thresholds

    assert "state/skill_review_root_tasks.jsonl" in {
        relative for relative, _threshold, _remediation in _hot_store_thresholds()
    }


def test_acceptance_slot_fit_uses_packet_density():
    from ouroboros.review_dispatch import acceptance_slot_fit

    chars = 330_001

    cap, estimated = acceptance_slot_fit(
        SimpleNamespace(model="reviewer", max_tokens=16_384),
        SimpleNamespace(prompt_chars=lambda: chars),
        slot_input_caps={"reviewer": 200_000},
    )

    assert cap == 200_000
    assert estimated == 100_001
    assert estimated > chars // 4


def test_acceptance_slot_fit_reuses_packet_budget_caps(monkeypatch):
    from ouroboros.review_dispatch import acceptance_slot_fit
    from ouroboros.review_evidence import acceptance_packet_budget_chars
    from ouroboros.review_substrate import ReviewSlot
    from ouroboros.tools import review_synthesis

    calls = []
    caps = {"wide": 800_000, "narrow": 20_000}

    def _caps(models, **_kwargs):
        calls.append(list(models))
        return {model: caps[model] for model in models}

    monkeypatch.setattr(review_synthesis, "per_slot_input_token_limits", _caps)
    slots = [
        ReviewSlot(slot_id="slot_1", model="wide"),
        ReviewSlot(slot_id="slot_2", model="narrow"),
    ]
    budget = acceptance_packet_budget_chars(slots)
    monkeypatch.setattr(
        review_synthesis,
        "per_slot_input_token_limits",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dispatch recalibrated cached packet caps")
        ),
    )

    cap, estimated = acceptance_slot_fit(
        slots[1], SimpleNamespace(prompt_chars=lambda: 99_000),
        slot_input_caps=budget.slot_input_caps,
    )

    assert calls == [["wide", "narrow"]]
    assert cap == 20_000
    assert estimated == 30_000


def test_budget_ladder_stops_shedding_after_predecessor_fits():
    from ouroboros.review_evidence import _accept_enforce_budget

    trajectory = [{"tool": f"tool-{index}", "result": "ok"} for index in range(21)]
    evidence = {
        "task_contract": {
            "requirements": "keep the trajectory",
            "predecessor_authority": {
                "previous_task_id": "prior-task",
                "envelope": "x" * 20_000,
            },
        },
        "tool_trajectory": trajectory,
    }
    compact_without_envelope = {
        **evidence,
        "task_contract": {
            "requirements": "keep the trajectory",
            "predecessor_authority": {
                "kind": "predecessor_authority_omitted_for_budget",
                "previous_task_id": "prior-task",
                "omitted_chars": len(json.dumps(
                    evidence["task_contract"]["predecessor_authority"]
                )),
            },
        },
        "omissions_manifest": [{
            "section": "task_contract.predecessor_authority",
            "omitted": len(json.dumps(
                evidence["task_contract"]["predecessor_authority"]
            )),
            "reason": "evidence_budget",
        }],
    }
    budget = len(json.dumps(compact_without_envelope)) + 10

    result = _accept_enforce_budget(evidence, budget=budget)

    assert result["tool_trajectory"] == trajectory
    assert not any(
        row.get("section") == "tool_trajectory"
        for row in result["omissions_manifest"]
    )


def test_acceptance_docs_have_complete_sentence_boundaries():
    development = Path("docs/DEVELOPMENT.md").read_text(encoding="utf-8")
    architecture = Path("docs/ARCHITECTURE.md").read_text(encoding="utf-8")

    assert "silent false green. The\n  Every forced rail" not in development
    assert "never certify success; An OPEN plan wave" not in architecture
    assert "never had claims. an unresolved reference" not in architecture


def test_refused_slot_reuses_the_already_persisted_prompt(monkeypatch, tmp_path):
    import ouroboros.review_substrate as substrate

    calls = []

    def _persist(*_args, **kwargs):
        calls.append(kwargs["call_type"])
        return {"manifest_ref": kwargs["call_id"]}

    monkeypatch.setattr(substrate, "persist_call", _persist)
    request = substrate.ReviewRequest(
        surface="task_acceptance",
        goal="review",
        task_id="prompt-reuse",
        evidence={"__unresolved_partial_artifacts__": [{
            "status": "source_unavailable",
        }]},
    )
    actor = substrate.ReviewCoordinator(drive_root=tmp_path)._run_slot(
        request,
        substrate.ReviewSlot(slot_id="slot_1", model="reviewer"),
        operation_id="operation-1",
    )

    assert actor.status == "not_dispatched"
    assert actor.prompt_ref == {"manifest_ref": "operation-1_prompt"}
    assert len([call for call in calls if call.endswith("_prompt")]) == 1
    assert len(calls) == 2


def test_broken_reflection_panel_keeps_commit_advisory_lens(monkeypatch):
    from ouroboros.reflection import generate_reflection
    from ouroboros import review_substrate

    captured = {}

    class _Llm:
        def chat(self, **kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return {"content": "Reflection completed."}, {}

    monkeypatch.setattr(
        review_substrate,
        "compact_review_projection",
        lambda _runs: (_ for _ in ()).throw(ValueError("broken panel")),
    )

    generate_reflection(
        task={"id": "reflection-task", "text": "reflect"},
        llm_trace={"tool_calls": [], "review_runs": [{"broken": True}]},
        trace_summary="completed",
        llm_client=_Llm(),
        usage_dict={"rounds": 2, "cost": 0.1},
        review_evidence={"has_evidence": True, "lens_marker": "lens-survives"},
    )

    assert "lens-survives" in captured["prompt"]
    assert "review evidence unavailable" not in captured["prompt"]
