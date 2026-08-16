"""The single classifier answers exactly what the retired loop pair answered,
except on a table of deltas the owner approved.

The oracle is a golden snapshot of the OLD pair's answers, captured from the tree
named in ``GOLDEN_SOURCE_SHA``, not a copy of the old parser: a copy is dead code
that invites cleanup, a data file cannot drift silently.

Both directions fail. An unapproved divergence fails because the cutover would then
be changing behaviour nobody signed off. An APPROVED delta that no longer fires ALSO
fails, so the table cannot rot into a permanent excuse list — once a delta is gone,
its row goes with it.
"""

from __future__ import annotations

import json
import pathlib
from types import MappingProxyType
from typing import Mapping, NamedTuple

import pytest

from ouroboros.loop_tool_execution import _typed_execution_failure, _typed_result_metadata
from ouroboros.tools.tool_result import TOOL_CODE_SPECS, LegacyTextResultAdapter
from tests.tool_classification_corpus import (
    GOLDEN_SOURCE_SHA,
    build_corpus,
    harvested_identifiers,
    typed_result,
)

GOLDEN_PATH = pathlib.Path(__file__).resolve().parent / "fixtures" / "legacy_tool_classification_306f8827.json"


class Delta(NamedTuple):
    old_is_error: bool
    old_status: str
    new_is_error: bool
    new_status: str
    owner_item: str
    reason: str


# Every entry traces to a numbered item of the delta list the owner approved
# (batch #3 answer 1=A, batch #4 answers 1-3). Nothing else may differ.
APPROVED_DELTAS: Mapping[str, Delta] = MappingProxyType({
    "ACCESS_DENIED": Delta(False, "ok", True, "blocked", "A.4", "an access denial recorded as success is the worst under-reporting"),
    "ACTING_SUBAGENT_TOOL_NOT_GRANTED": Delta(False, "ok", True, "blocked", "A.4", "an ungranted tool for the acting subagent is a denial"),
    "CANCEL_INTENT_PROJECTION_CORRUPT": Delta(False, "ok", True, "error", "A.5", "a corrupted cancel projection is an error, not a success"),
    "CAPABILITY_UNAVAILABLE": Delta(True, "error", True, "unavailable", "A.18", "unavailability gets its own status name; the report bucket is unchanged"),
    "CHILD_RESULT_LINEAGE_FORBIDDEN": Delta(False, "ok", True, "blocked", "A.4", "a refused child-result lineage is a denial"),
    "CI_UNAVAILABLE": Delta(True, "error", True, "unavailable", "A.18", "unavailability gets its own status name; the report bucket is unchanged"),
    "COGNITIVE_TOOL_REQUIRED": Delta(True, "cognitive_tool_required", False, "ok", "A.11", "owner batch #4: the cognitive redirect is a hint, the error flag is removed"),
    "EXECUTOR_UNAVAILABLE": Delta(True, "error", True, "unavailable", "A.18", "unavailability gets its own status name; the report bucket is unchanged"),
    "EXTRACT_VIDEO_FRAMES_UNAVAILABLE": Delta(True, "error", True, "unavailable", "A.18", "unavailability gets its own status name; the report bucket is unchanged"),
    "GH_TIMEOUT": Delta(False, "ok", True, "timeout", "A.2", "an expired GitHub operation is a timeout, not a success"),
    "GIT_ERROR": Delta(False, "error", False, "git_error", "A.17", "the version-control refusal gets its own bucket; is_error is unchanged"),
    "INVALID_ARG": Delta(False, "ok", True, "argument_error", "A.6", "a bad pull-request argument is an error, not a success"),
    "MANAGED_UPDATE_STATE_UNAVAILABLE": Delta(True, "error", True, "unavailable", "A.18", "unavailability gets its own status name; the report bucket is unchanged"),
    "MCP_DISABLED": Delta(False, "ok", True, "unavailable", "A.3", "an MCP provider that is off is unavailable, not a success"),
    "MCP_TOOL_DISALLOWED": Delta(False, "ok", True, "blocked", "A.3", "an MCP tool refused by policy is a denial, not a success"),
    "MCP_TOOL_ERROR": Delta(True, "error", True, "mcp_error", "A.17", "the MCP error gets its own bucket, homed to the blocking partition"),
    "MCP_TOOL_NOT_FOUND": Delta(False, "ok", True, "unavailable", "A.3", "a missing MCP tool is unavailable, not a success"),
    "MCP_TOOL_TIMEOUT": Delta(False, "ok", True, "timeout", "A.3", "an expired MCP call is a timeout, not a success"),
    "MUTATIVE_SUBAGENTS_DISABLED": Delta(False, "ok", True, "blocked", "A.4", "a disabled mutative subagent is a denial"),
    "OCR_PDF_SCANNED_UNAVAILABLE": Delta(True, "error", True, "unavailable", "A.18", "unavailability gets its own status name; the report bucket is unchanged"),
    "OCR_PDF_UNAVAILABLE": Delta(True, "error", True, "unavailable", "A.18", "unavailability gets its own status name; the report bucket is unchanged"),
    "PYTHON_INTERPRETER_UNAVAILABLE": Delta(True, "error", True, "unavailable", "A.18", "unavailability gets its own status name; the report bucket is unchanged"),
    "REVIEW_BLOCKED": Delta(False, "blocked", False, "review_blocked", "A.17", "the review refusal gets its own bucket; is_error is unchanged"),
    "SKILLS_UNAVAILABLE": Delta(True, "error", True, "unavailable", "A.18", "unavailability gets its own status name; the report bucket is unchanged"),
    "SKILL_EXEC_TIMEOUT": Delta(False, "ok", True, "timeout", "A.2", "an expired skill run is a timeout, not a success"),
    "TASK_FORBIDDEN": Delta(False, "ok", True, "blocked", "A.4", "a forbidden task surface is a denial"),
    "TOOL_ARG_ERROR": Delta(True, "error", True, "argument_error", "A.17", "the argument error gets its own bucket, homed to the blocking partition"),
    "VIEW_IMAGE_UNAVAILABLE": Delta(True, "error", True, "unavailable", "A.18", "unavailability gets its own status name; the report bucket is unchanged"),
    "YOUTUBE_TRANSCRIPT_UNAVAILABLE": Delta(True, "error", True, "unavailable", "A.18", "unavailability gets its own status name; the report bucket is unchanged"),
    "body:empty": Delta(False, "ok", False, "untyped", "A.17", "a dynamic provider body is untyped rather than assumed ok; is_error is unchanged"),
    "body:list": Delta(False, "ok", False, "untyped", "A.17", "a dynamic provider body is untyped rather than assumed ok; is_error is unchanged"),
    "body:nested_only": Delta(False, "ok", False, "untyped", "A.17", "a dynamic provider body is untyped rather than assumed ok; is_error is unchanged"),
    "body:prose": Delta(False, "ok", False, "untyped", "A.17", "a dynamic provider body is untyped rather than assumed ok; is_error is unchanged"),
    "body:string_false": Delta(False, "ok", False, "untyped", "A.17", "a dynamic provider body is untyped rather than assumed ok; is_error is unchanged"),
    "body:true": Delta(False, "ok", False, "untyped", "A.17", "a dynamic provider body is untyped rather than assumed ok; is_error is unchanged"),
    "compose:exit:route+safety": Delta(False, "ok", True, "non_zero_exit", "A.7", "the safety wrapper no longer masks what it wraps"),
    "compose:exit:safety": Delta(False, "ok", True, "non_zero_exit", "A.7", "the safety wrapper no longer masks what it wraps"),
    "compose:integrate:route+safety": Delta(False, "ok", True, "integration_blocked", "A.7", "the safety wrapper no longer masks what it wraps"),
    "compose:integrate:safety": Delta(False, "ok", True, "integration_blocked", "A.7", "the safety wrapper no longer masks what it wraps"),
    "compose:protected:route+safety": Delta(False, "ok", True, "protected_blocked", "A.7", "the safety wrapper no longer masks what it wraps"),
    "compose:protected:safety": Delta(False, "ok", True, "protected_blocked", "A.7", "the safety wrapper no longer masks what it wraps"),
    "compose:reported:safety": Delta(False, "ok", True, "tool_reported_failure", "A.7", "the safety wrapper no longer masks what it wraps"),
    "compose:timeout:route+safety": Delta(False, "ok", True, "timeout", "A.7", "the safety wrapper no longer masks what it wraps"),
    "compose:timeout:safety": Delta(False, "ok", True, "timeout", "A.7", "the safety wrapper no longer masks what it wraps"),
    "compose:violation:route+safety": Delta(False, "ok", True, "safety_violation", "A.7", "the safety wrapper no longer masks what it wraps"),
    "compose:violation:safety": Delta(False, "ok", True, "safety_violation", "A.7", "the safety wrapper no longer masks what it wraps"),
    "edge:autocorrect_line2": Delta(True, "shell_error", True, "non_zero_exit", "A.13", "the wrapper body is classified by its own first line, so the exit error is named precisely"),
    "edge:autocorrect_line3": Delta(True, "shell_error", False, "ok_autocorrected", "A.13", "the loop's whole-remainder scan matched a marker three lines down; the body's first line governs"),
    "edge:safety_inner_block": Delta(False, "ok", True, "resource_policy_blocked", "A.7", "the safety wrapper no longer masks what it wraps"),
    "edge:unknown_tool": Delta(False, "ok", True, "unknown_tool", "A.1", "a call to a tool that does not exist was never a success"),
    "envelope:empty": Delta(False, "ok", False, "untyped", "A.17", "a dynamic provider body is untyped rather than assumed ok; is_error is unchanged"),
    "envelope:false": Delta(False, "ok", False, "untyped", "A.17", "a dynamic provider body is untyped rather than assumed ok; is_error is unchanged"),
    "envelope:false_indented": Delta(False, "ok", False, "untyped", "A.17", "a dynamic provider body is untyped rather than assumed ok; is_error is unchanged"),
    "envelope:list": Delta(False, "ok", False, "untyped", "A.17", "a dynamic provider body is untyped rather than assumed ok; is_error is unchanged"),
    "envelope:nested_only": Delta(False, "ok", False, "untyped", "A.17", "a dynamic provider body is untyped rather than assumed ok; is_error is unchanged"),
    "envelope:prose": Delta(False, "ok", False, "untyped", "A.17", "a dynamic provider body is untyped rather than assumed ok; is_error is unchanged"),
    "envelope:string_false": Delta(False, "ok", False, "untyped", "A.17", "a dynamic provider body is untyped rather than assumed ok; is_error is unchanged"),
    "envelope:true": Delta(False, "ok", False, "untyped", "A.17", "a dynamic provider body is untyped rather than assumed ok; is_error is unchanged"),
    "native:ACCESS_BLOCKED:ACTING_SUBAGENT_TOOL_NOT_GRANTED": Delta(False, "ok", True, "blocked", "A.4", "same denial through its native code"),
    "native:ACCESS_BLOCKED:MANAGED_UPDATE_IN_PROGRESS": Delta(False, "ok", True, "blocked", "A.4", "the managed-update denial is an access block its text never marked"),
    "native:CAPABILITY_UNAVAILABLE:CAPABILITY_UNAVAILABLE": Delta(True, "error", True, "unavailable", "A.18", "unavailability gets its own status name; the report bucket is unchanged"),
    "native:CAPABILITY_UNAVAILABLE:MANAGED_UPDATE_STATE_UNAVAILABLE": Delta(True, "error", True, "unavailable", "A.18", "unavailability gets its own status name; the report bucket is unchanged"),
    "native:CAPABILITY_UNAVAILABLE:PYTHON_INTERPRETER_UNAVAILABLE": Delta(True, "error", True, "unavailable", "A.18", "unavailability gets its own status name; the report bucket is unchanged"),
    "native:HEAL_MODE_BLOCKED:SKILL_REDIRECT_BLOCKED": Delta(True, "skill_payload_blocked", True, "heal_mode_blocked", "A.18", "the publisher's code wins over its text; both statuses are policy denials"),
    "shape:cognitive_redirect": Delta(True, "cognitive_tool_required", False, "ok", "A.11", "owner batch #4, through the native producer"),
    "shape:executor_crash": Delta(True, "error", True, "executor_error", "A.17", "the executor crash gets its own bucket, homed to the blocking partition"),
    "shape:git_error_untyped_text": Delta(False, "error", False, "git_error", "A.17", "same bucket rename through the native code, with no marker in the text"),
    "shape:review_blocked_untyped_text": Delta(False, "blocked", False, "review_blocked", "A.17", "same bucket rename through the native code, with no marker in the text"),
})


def _golden() -> dict[str, dict]:
    payload = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert payload["source_sha"] == GOLDEN_SOURCE_SHA, "golden was captured from another tree"
    return payload["entries"]


def _live_answer(case) -> tuple[bool, str]:
    typed = typed_result(case)
    is_error = _typed_execution_failure(True, typed)
    return is_error, _typed_result_metadata(case.tool, case.text, is_error, typed)["status"]


def test_single_classifier_matches_the_retired_pair_except_approved_deltas() -> None:
    golden = _golden()
    corpus = build_corpus()
    assert len(corpus) >= 600, "the corpus collapsed; a harvest regression would hide every delta"

    unexpected: list[tuple[str, dict, tuple[bool, str]]] = []
    unfired = set(APPROVED_DELTAS)
    for case in corpus:
        assert case.key in golden, f"no golden answer for {case.key}: regenerate before trusting this run"
        old = golden[case.key]
        live = _live_answer(case)
        if live == (old["is_error"], old["status"]):
            continue
        delta = APPROVED_DELTAS.get(case.subject)
        expected = None if delta is None else (
            (delta.old_is_error, delta.old_status), (delta.new_is_error, delta.new_status)
        )
        if expected != ((old["is_error"], old["status"]), live):
            unexpected.append((case.key, old, live))
        else:
            unfired.discard(case.subject)

    assert not unexpected, f"unapproved classification changes: {unexpected[:12]}"
    assert not unfired, f"approved deltas that no longer fire (delete the rows): {sorted(unfired)}"


def test_every_approved_delta_names_an_owner_item() -> None:
    for subject, delta in APPROVED_DELTAS.items():
        assert delta.owner_item.startswith("A."), subject
        assert delta.reason.strip(), subject
        assert (delta.old_is_error, delta.old_status) != (delta.new_is_error, delta.new_status), subject


def test_golden_covers_every_harvested_producer() -> None:
    """A producer added after the cutover has no golden answer, so it fails here
    instead of silently entering the tree with an unverified classification."""
    golden = _golden()
    missing = [
        identifier for identifier in harvested_identifiers()
        if f"ident:{identifier}:plain" not in golden
    ]
    assert not missing, f"new warning identifiers without a golden answer: {missing}"


def test_specific_identifiers_beat_their_family_and_families_beat_generic_markers() -> None:
    """Order, asserted as behaviour rather than as a position in a table."""
    def bucket(text: str) -> str:
        return TOOL_CODE_SPECS[LegacyTextResultAdapter.from_text("fixture_tool", text).code].outcome_bucket

    assert bucket("⚠️ SHELL_CWD_BLOCKED: escapes roots") == "cwd_blocked"
    assert bucket("⚠️ SHELL_EXIT_ERROR: exit_code=1") == "non_zero_exit"
    assert bucket("⚠️ SHELL_ENV_ERROR: bad env") == "shell_error"
    assert bucket("⚠️ RUN_SCRIPT_BLOCKED: interpreter") == "run_script_blocked"
    assert bucket("⚠️ RUN_SCRIPT_LAUNCH_ERROR: boom") == "run_script_error"
    assert bucket("⚠️ LIGHT_MODE_REPO_WRITE_BLOCKED: repo") == "light_mode_blocked"
    assert bucket("⚠️ INTEGRATE_TARGET_ERROR: not git") == "integration_blocked"
    assert bucket("⚠️ INTEGRATE_LOCK_TIMEOUT: busy") == "integration_blocked"
    assert bucket("⚠️ WRITE_FILE_ERROR: boom") == "write_file_blocked"
    assert bucket("⚠️ EDIT_TEXT_ERROR: old_str not found") == "edit_text_blocked"
    assert bucket("⚠️ APPLY_PATCH_ERROR: occurrence miscount") == "edit_ops_blocked"
    assert bucket("⚠️ EDIT_BATCH_ERROR: occurrence miscount") == "edit_ops_blocked"
    assert bucket("⚠️ DATA_WRITE_ERROR: refused") == "data_blocked"
    assert bucket("⚠️ SKILL_PAYLOAD_ARG_ERROR: bad selector") == "skill_payload_blocked"
    assert bucket("⚠️ ROOT_REQUIRED_USER_FILES: retry") == "root_required_user_files"
    assert bucket("⚠️ ROOT_REQUIRED_ACTIVE_WORKSPACE: retry") == "root_required_active_workspace"
    assert bucket("⚠️ RESOURCE_CONSTRAINT_BLOCKED: no network") == "resource_constraint_blocked"
    assert bucket("⚠️ RESOURCE_POLICY_BLOCKED: protected") == "resource_policy_blocked"
    assert bucket("⚠️ UNKNOWN_COARSE_BLOCKED: generic") == "blocked"
    assert bucket("⚠️ UNKNOWN_COARSE_ERROR: generic") == "error"
    # The one negation in the retired chain: an autocorrected command that also
    # exited non-zero must not read as a plain autocorrected success.
    assert bucket("⚠️ SHELL_REGEX_AUTO_CORRECTED: fixed\n⚠️ SHELL_EXIT_ERROR: exit_code=1") == "non_zero_exit"
    assert bucket("⚠️ SHELL_REGEX_AUTO_CORRECTED: fixed\nexit_code=0") == "ok_autocorrected"


@pytest.mark.parametrize("case_key", ["shape:shell_no_match_autocorrected", "shape:shell_ok"])
def test_process_facts_stay_typed_not_parsed(case_key: str) -> None:
    """The three post-rules that are NOT text classification keep working off meta."""
    case = next(item for item in build_corpus() if item.key == case_key)
    typed = typed_result(case)
    meta = _typed_result_metadata(case.tool, case.text, False, typed)

    assert meta["exit_code"] == dict(case.meta)["exit_code"]
    expected = "ok_autocorrected" if dict(case.meta).get("shell_regex_auto_corrected") else "ok"
    assert meta["status"] == expected
