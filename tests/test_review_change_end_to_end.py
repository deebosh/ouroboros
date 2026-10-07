"""One subject, two doors, the same brief (seam 4 of the review operation).

``review_change(root="system_repo", subject="index")`` and the commit gate's
review-only cycle review the same staged index through the same seats, the same
body-layer rules and the same prompt text. The ONLY difference the two doors
may leave in a brief is the narrative line that names the wave: the gate hands
the reviewers the intended commit message, the operation its own wave label
(``review_change: staged index of <root> ...``), both inside the
``## Informational context — commit message`` block the goal section renders.
This test pins that difference to exactly that block and nothing else.
"""

import hashlib
import json
from pathlib import Path

import pytest

import ouroboros.review_substrate as substrate
from ouroboros import review_ledger
from ouroboros.tools import git as git_mod
from ouroboros.tools import review_change
from ouroboros.tools.git_review_cycle import _run_non_committing_review_cycle
from ouroboros.tools.registry import ToolContext
from ouroboros.tools.review_change import run_review_change
from scripts import run_external_review as runner
from tests import _contributor_packet_shared as shared

GOAL = "Make the installed body's helper return the proposal's constant."
SCOPE = "ouroboros/helper.py only; the checklist and tests stay as they are."
COMMIT_MESSAGE = "fix: return the proposal's constant\n\nThe narrative body of the intended commit."


def _brief_text(brief: dict) -> str:
    """Every byte a seat was given: the message texts (plain or block-structured)
    and, for a retrieving seat, its session task."""
    parts = []
    for message in brief["messages"]:
        content = message.get("content") or ""
        if isinstance(content, list):
            parts.extend(str(block.get("text") or json.dumps(block, sort_keys=True)) for block in content)
        else:
            parts.append(str(content))
    return "\n".join(parts) + "\n" + brief["session_task"]


@pytest.fixture
def staged_body(tmp_path, monkeypatch):
    fixture = shared.init_installed_body(tmp_path)
    repo = Path(fixture["repo"])
    # A real install carries a .gitignore; without one the gate's `git add -A`
    # door would stage the one it writes and review a different tree.
    (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    shared.git(repo, "add", ".gitignore")
    shared.git(repo, "commit", "-q", "-m", "ignore caches")
    shared.git(repo, "cherry-pick", "--no-commit", fixture["head_sha"])
    fixture["staged_tree_sha"] = shared.git(repo, "write-tree")
    assert fixture["staged_tree_sha"] != fixture["head_tree_sha"]
    monkeypatch.setenv("OUROBOROS_REVIEWER_SLOTS", json.dumps(runner._slot_plan_payload(shared.GOLDEN_CONFIG)))
    monkeypatch.setenv("OUROBOROS_REVIEW_ENFORCEMENT", "blocking")
    monkeypatch.setenv("OUROBOROS_PRE_PUSH_TESTS", "1")
    monkeypatch.setenv("OUROBOROS_RUNTIME_MODE", "pro")  # the proposal touches a protected surface
    monkeypatch.setattr(git_mod, "_run_review_preflight_tests", shared.passing_test_runner)
    return fixture


def test_review_change_on_the_system_index_is_the_commit_gates_brief(staged_body, tmp_path, monkeypatch):
    repo = Path(staged_body["repo"])
    operation_briefs: list[dict] = []
    monkeypatch.setattr(substrate, "run_review_request", shared.golden_substrate(operation_briefs))
    operation_ctx = ToolContext(repo_dir=repo, drive_root=tmp_path / "operation-drive")
    result = run_review_change(operation_ctx, root="system_repo", surface="change", goal=GOAL, scope=SCOPE,
                               subject="index")
    assert result["aggregate"] == "PASS" and result["state"] == "settled", result
    operation_record = review_ledger.load_record(operation_ctx.drive_root, result["record_id"])
    # The operation only reads: the staged index it reviewed is still staged.
    assert shared.git(repo, "write-tree") == staged_body["staged_tree_sha"]

    # The gate's review-only cycle is the second door onto the same index (it
    # unstages the index when it is done, which is why it goes second here).
    gate_briefs: list[dict] = []
    monkeypatch.setattr(substrate, "run_review_request", shared.golden_substrate(gate_briefs))
    gate_ctx = ToolContext(repo_dir=repo, drive_root=tmp_path / "gate-drive")
    outcome = _run_non_committing_review_cycle(gate_ctx, COMMIT_MESSAGE, skip_advisory_review=True,
                                               goal=GOAL, scope=SCOPE)
    assert outcome["status"] == "passed", outcome
    gate_record = review_ledger.load_record(gate_ctx.drive_root, outcome["review_record_id"])

    # Same subject, same seats, same rules, same verdict.
    assert operation_record["subject"]["tree_sha"] == gate_record["subject"]["tree_sha"] == staged_body["staged_tree_sha"]
    assert operation_record["subject"]["diff_sha"] == gate_record["subject"]["diff_sha"]
    operation_checklist = dict(operation_record["brief"]["checklist"])
    assert operation_checklist.pop("treat_as_body") is False  # the operation's own argument, recorded
    assert operation_checklist == gate_record["brief"]["checklist"]
    assert (operation_checklist["layer"], operation_checklist["body_fact"], operation_checklist["how"]) == (
        "body", "true", "dir")
    assert operation_checklist["rules_source"]["sha"] and operation_checklist["checklist_hash"]
    assert (operation_record["brief"]["goal"], operation_record["brief"]["scope"]) == (GOAL, SCOPE) == (
        gate_record["brief"]["goal"], gate_record["brief"]["scope"])
    seats = lambda record: [(row["seat_id"], row["requested"]["model"], row["effective"]["model"], row["parts"])  # noqa: E731
                            for row in record["rows"]]
    assert seats(operation_record) == seats(gate_record)
    shared_panel = ("seats", "distinct_models", "observed_unknown_seats", "distinct_engines", "single_model_panel",
                    "composition", "chosen_by", "assigned", "additional")
    assert {key: operation_record["panel"][key] for key in shared_panel} == {
        key: gate_record["panel"][key] for key in shared_panel}
    assert operation_record["panel"]["composition"] == "configured"
    # Disclosed divergence: the gate's configured panel is loud about its unrecorded
    # reason; the operation is loud only when an author NARROWED the panel without one.
    assert (gate_record["panel"]["reason_missing"], operation_record["panel"]["reason_missing"]) == (True, False)
    assert operation_record["verdict"]["aggregate"] == gate_record["verdict"]["aggregate"] == "PASS"
    assert (operation_record["surface"], gate_record["surface"]) == ("change", "commit_gate")

    # Same brief per seat: the texts differ in the narrative wave line alone.
    by_seat = lambda briefs: {brief["slot_id"]: brief for brief in briefs}  # noqa: E731
    gate, operation = by_seat(gate_briefs), by_seat(operation_briefs)
    assert sorted(gate) == sorted(operation) == ["s1", "t1", "t2"]
    label = review_change._wave_label(review_change.parse_request(
        {"root": "system_repo", "surface": "change", "goal": GOAL, "scope": SCOPE, "subject": "index"}), repo)
    for slot_id in ("t1", "t2", "s1"):
        assert gate[slot_id]["model"] == operation[slot_id]["model"]
        gate_text, operation_text = _brief_text(gate[slot_id]), _brief_text(operation[slot_id])
        assert gate_text != operation_text, slot_id
        assert operation_text.count(label) == gate_text.count(COMMIT_MESSAGE) == 1, slot_id
        aligned = operation_text.replace(label, COMMIT_MESSAGE)
        assert hashlib.sha256(aligned.encode()).hexdigest() == hashlib.sha256(gate_text.encode()).hexdigest(), slot_id
        assert "## Informational context — commit message" in gate_text, slot_id
