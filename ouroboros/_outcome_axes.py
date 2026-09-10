"""Leaf module for ``ouroboros.outcomes.derive_loop_outcome``'s two phases:
classifying the execution-health axis, then finalizing the review/objective
axes and assembling the outcome dict. Split out of ``outcomes.py`` (ibl-500e3d3b40f2 /
ibl-6710dd209229) purely to shrink that module and ``derive_loop_outcome``
itself under the size-ratchet caps — no behavior change.

Both functions reach back into ``ouroboros.outcomes`` for its constants and
helpers via a FUNCTION-LOCAL import (not a module-level one): ``outcomes.py``
imports this module's two entry points the same way, function-local, inside
``derive_loop_outcome`` itself. Neither module imports the other at module
load time, so there is no import cycle even though the two modules are
mutually dependent at call time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class LoopExecutionClassification:
    """Everything ``_finalize_objective_axes`` needs from the execution-status
    classification phase, carried across the module boundary as one value
    instead of a long, easy-to-typo argument list."""

    text: str
    execution_status: str
    reason_code: str
    failure: Optional[Dict[str, Any]]
    resource_limit: Dict[str, Any]
    recovered_tool_errors: List[Dict[str, Any]]
    cosmetic_tool_errors: List[Dict[str, Any]]
    ignored_tool_errors: List[Dict[str, Any]]
    policy_denials: List[Dict[str, Any]]
    mutation_attribution: Dict[str, Any]
    delivery_candidate: Dict[str, Any]
    deferred_child_count: int
    deferred_child_suffix: bool
    forced_best_effort_with_deferred_child: bool
    acceptance_review_skipped_eligible: bool
    acceptance_reason: str = field(default="")


def _classify_loop_execution_status(
    final_text: str,
    usage: Dict[str, Any],
    llm_trace: Dict[str, Any],
    *,
    repo_dir: Optional[Any] = None,
    attributed_paths: Optional[Iterable[str]] = None,
) -> LoopExecutionClassification:
    """Phase 1 of ``derive_loop_outcome``: determine the execution-health axis
    (``execution_status`` / ``reason_code`` / ``failure``) plus the auxiliary
    facts the objective/review phase needs. See ``derive_loop_outcome``'s
    docstring for the ``repo_dir`` / ``attributed_paths`` contract — unchanged
    by this split."""
    from ouroboros.outcomes import (
        ACCEPTANCE_BYPASS_REASONS,
        ACCEPTANCE_FINALIZED_UNACCEPTED,
        BEST_EFFORT_REASON_CODES,
        EXECUTION_BEST_EFFORT,
        EXECUTION_DEGRADED,
        EXECUTION_FAILED,
        EXECUTION_INFRA_FAILED,
        EXECUTION_OK,
        REASON_ACCEPTANCE_REVIEW_SKIPPED_DEADLINE_RESERVE,
        REASON_DELIVERY_CONTROL_DEGRADED,
        REASON_CHILD_RESULTS_DEFERRED,
        REASON_EMPTY_FINAL_TEXT,
        REASON_FINAL_MESSAGE,
        REASON_PROVIDER_FAILURE,
        REASON_TOOL_FAILURE,
        REASON_WORK_UNCOMMITTED,
        RESULT_FAILED,
        RESULT_INFRA_FAILED,
        _classify_tool_errors,
        _failure_block_for_work_uncommitted,
        _INFRA_TEXT_PREFIXES,
        _trace_mapping,
        detect_work_uncommitted,
        filter_work_uncommitted_to_attributed,
    )

    usage_status = str(usage.get("execution_status") or usage.get("result_status") or "").strip()
    usage_reason = str(usage.get("reason_code") or "").strip()
    resource_limit = dict(usage.get("resource_limit") or {}) if isinstance(usage.get("resource_limit"), dict) else {}
    text = str(final_text or "")
    failure: Dict[str, Any] | None = None
    execution_status = EXECUTION_OK
    reason_code = REASON_FINAL_MESSAGE
    tool_error_state = _classify_tool_errors(llm_trace)
    tool_errors = tool_error_state.get("unresolved") or []
    recovered_tool_errors = tool_error_state.get("recovered") or []
    cosmetic_tool_errors = tool_error_state.get("cosmetic") or []
    # A2: read-only access-policy blocks — recorded for forensics, never degrading
    # and (unlike cosmetic) never a residual-warning trigger.
    ignored_tool_errors = tool_error_state.get("ignored") or []
    # v6.57.0: unrecovered POLICY refusals (write/shell/integration `*_blocked`) —
    # telemetry only, never degrading and never a `tool_failure` headline.
    policy_denials = tool_error_state.get("policy_denials") or []
    delivery_candidate = _trace_mapping(llm_trace, "delivery_candidate")
    acceptance_decision = _trace_mapping(llm_trace, "acceptance_decision")
    review_decision = _trace_mapping(llm_trace, "review_decision")
    mutation_attribution = _trace_mapping(llm_trace, "mutation_attribution")
    # v6.78.0: keyed on the CANONICAL status plus the typed reason (before the
    # three-state collapse the reason literal WAS the status). Missing this pairing
    # would silently stop degrading an eligible-but-skipped panel — a false green;
    # keying on the status alone would degrade honest capsule_spent finalizations.
    # The forced-rail bypass reasons (ACCEPTANCE_BYPASS_REASONS) ride the same key.
    _acceptance_reason = str(acceptance_decision.get("reason") or "")
    acceptance_review_skipped_eligible = (
        str(acceptance_decision.get("status") or "") == ACCEPTANCE_FINALIZED_UNACCEPTED
        and (
            _acceptance_reason == REASON_ACCEPTANCE_REVIEW_SKIPPED_DEADLINE_RESERVE
            or _acceptance_reason in ACCEPTANCE_BYPASS_REASONS
        )
        and str(review_decision.get("eligibility") or "") == "eligible"
    )
    disposition_projection = _trace_mapping(llm_trace, "child_result_dispositions")
    deferred_child_count = int(disposition_projection.get("deferred_count") or 0)
    deferred_child_suffix = bool(
        deferred_child_count
        and str(delivery_candidate.get("degraded_reason") or "")
        == "host_child_status_suffix"
    )
    forced_best_effort_with_deferred_child = bool(
        deferred_child_count
        and (
            str(delivery_candidate.get("degraded_reason") or "")
            in BEST_EFFORT_REASON_CODES
            # provider_unavailable left the best-effort set (2026-08-10 saga:
            # a provider-killed task is failed, not best-effort), but a forced
            # provider rail must still not erase the more specific
            # deferred-child objective below.
            or str(delivery_candidate.get("degraded_reason") or "")
            == "provider_unavailable"
        )
    )
    verification_failures: List[Dict[str, Any]] = []
    for event in llm_trace.get("verification_events") or []:
        if not isinstance(event, dict):
            continue
        for service in event.get("services") or []:
            if not isinstance(service, dict):
                continue
            artifact_text = str(service.get("artifact_outputs") or "")
            if bool(service.get("artifact_output_failed")) or artifact_text.startswith("⚠️ ARTIFACT_OUTPUT_ERROR"):
                verification_failures.append({
                    "kind": str(event.get("kind") or "runtime_event"),
                    "service": service.get("name"),
                    "status": "artifact_output_error",
                    "reason": artifact_text[:500],
                })

    if usage_status == RESULT_INFRA_FAILED:
        execution_status = EXECUTION_INFRA_FAILED
        reason_code = usage_reason or REASON_PROVIDER_FAILURE
        failure = {"kind": "provider", "reason_code": reason_code}
        # Only the raw provider API-error terminal carries the overflow sub-kind.
        # When a latched wait cause outranks the pass (reason_code is
        # provider_unavailable / a wait terminal), the published projection must
        # NOT contradict it with an overflow kind (test_transport_wait_repeat_interaction).
        if reason_code == "llm_api_error" and str(usage.get("_last_llm_error_kind") or "") == "context_overflow":
            failure["error_kind"] = "context_overflow"
    elif (
        usage_status == RESULT_FAILED
        and usage_reason in BEST_EFFORT_REASON_CODES
        and bool(usage.get("_best_effort_extracted"))
        and text.strip()
        and not text.lstrip().startswith(("⚠️", "❌"))
    ):
        # Forced finalization (deadline grace / budget / round limit) that
        # actually EXTRACTED a model answer: honest best-effort, not failure.
        # Deterministic structural gate: forced reason code + the loop's typed
        # "model answer extracted" fact + non-empty non-error text. Host
        # fallback strings (e.g. budget rejection notices) never set the
        # extraction fact and stay failed — no text-shape whitewashing.
        execution_status = EXECUTION_BEST_EFFORT
        reason_code = usage_reason
        failure = None
    elif usage_status == RESULT_FAILED:
        execution_status = EXECUTION_FAILED
        reason_code = usage_reason or REASON_EMPTY_FINAL_TEXT
        failure = {"kind": "agent", "reason_code": reason_code}
    elif not text.strip():
        execution_status = EXECUTION_FAILED
        reason_code = REASON_EMPTY_FINAL_TEXT
        failure = {"kind": "agent", "reason_code": reason_code}
    elif (_infra := next(
        (row for row in _INFRA_TEXT_PREFIXES if text.lstrip().startswith(row[0])), None,
    )) is not None:
        execution_status = EXECUTION_INFRA_FAILED
        reason_code = usage_reason or _infra[2]
        failure = {"kind": _infra[1], "reason_code": reason_code}
    elif delivery_candidate.get("degraded") and not deferred_child_suffix:
        execution_status = EXECUTION_DEGRADED
        # The candidate's OWN typed cause survives; the generic code is reserved
        # for a degradation that reports no reason.
        reason_code = (
            usage_reason
            or str(delivery_candidate.get("degraded_reason") or "")
            or REASON_DELIVERY_CONTROL_DEGRADED
        )
        failure = {"kind": "finalization_control", "reason_code": reason_code}
    elif deferred_child_count:
        execution_status = EXECUTION_DEGRADED
        reason_code = usage_reason or REASON_CHILD_RESULTS_DEFERRED
        failure = {
            "kind": "child_result_disposition",
            "reason_code": reason_code,
            "deferred_count": deferred_child_count,
        }
    elif verification_failures:
        execution_status = EXECUTION_DEGRADED
        reason_code = usage_reason or REASON_TOOL_FAILURE
        failure = {
            "kind": "verification",
            "reason_code": reason_code,
            "verification_failures": verification_failures[:20],
        }
    elif tool_errors:
        execution_status = EXECUTION_DEGRADED
        reason_code = usage_reason or REASON_TOOL_FAILURE
        failure = {
            "kind": "tool",
            "reason_code": reason_code,
            "tool_errors": tool_errors[:20],
        }
    elif repo_dir is not None and execution_status == EXECUTION_OK:
        # ibl-local-27745117e0e1: a task that ran cleanly but left tracked files
        # dirty without a commit. The git-status probe is cheap and read-only;
        # narrower than a provider/tool/deferred failure but distinct from a clean
        # no-op (the agent DID change tracked files). The narrow eligibility gate
        # preserves stronger classifications above; this branch only fires when
        # the existing chain left execution OK with REASON_FINAL_MESSAGE — the
        # honest observation, not a verdict.
        work_uncommitted_files = detect_work_uncommitted(repo_dir)
        if attributed_paths is not None:
            # Shared-tree regime: a concurrent task's dirty state must not bleed
            # into THIS task's verdict. Narrow to the per-task attributed set.
            # An empty attributed set after filtering means concurrent dirt only —
            # no degradation, no false-positive probe.
            work_uncommitted_files = filter_work_uncommitted_to_attributed(
                work_uncommitted_files, attributed_paths,
            )
        if work_uncommitted_files:
            execution_status = EXECUTION_DEGRADED
            reason_code = REASON_WORK_UNCOMMITTED
            failure = _failure_block_for_work_uncommitted(work_uncommitted_files)

    # A skipped-or-bypassed eligible panel is not a verdict, but cannot remain clean;
    # preserve stronger classifications and degrade only the false-green remainder.
    # Honest reachability (measured, not asserted): the FORCED-rail bypass reasons
    # cannot arrive here on an OK execution — a bypass is stamped only when the rail
    # already wrote `usage.reason_code`, and every writer of that key also writes
    # `execution_status='failed'` (the provider rail upgrades it to 'infra_failed'),
    # so those runs land on the STRONGER failed/infra_failed /
    # best_effort branches above and the owner-visible bypass rides the review axis
    # (see test_forced_rail_axes_are_the_production_shape). What this branch actually
    # decides is the pacing skip (REASON_ACCEPTANCE_REVIEW_SKIPPED_DEADLINE_RESERVE).
    # The bypass keys stay in the condition as a BACKSTOP, not as a live behaviour
    # claim: a future rail that bypasses an owed panel without failing the usage must
    # not be able to come back as a clean green.
    if acceptance_review_skipped_eligible and execution_status == EXECUTION_OK:
        execution_status = EXECUTION_DEGRADED
        reason_code = _acceptance_reason
        failure = {
            "kind": "task_acceptance",
            "reason_code": reason_code,
        }

    return LoopExecutionClassification(
        text=text,
        execution_status=execution_status,
        reason_code=reason_code,
        failure=failure,
        resource_limit=resource_limit,
        recovered_tool_errors=recovered_tool_errors,
        cosmetic_tool_errors=cosmetic_tool_errors,
        ignored_tool_errors=ignored_tool_errors,
        policy_denials=policy_denials,
        mutation_attribution=mutation_attribution,
        delivery_candidate=delivery_candidate,
        deferred_child_count=deferred_child_count,
        deferred_child_suffix=deferred_child_suffix,
        forced_best_effort_with_deferred_child=forced_best_effort_with_deferred_child,
        acceptance_review_skipped_eligible=acceptance_review_skipped_eligible,
        acceptance_reason=_acceptance_reason,
    )


def _finalize_objective_axes(
    classification: LoopExecutionClassification,
    llm_trace: Dict[str, Any],
    usage: Dict[str, Any],
) -> Dict[str, Any]:
    """Phase 2 of ``derive_loop_outcome``: compute the review/objective axes
    from ``classification`` plus ``llm_trace``, extract the final answer, and
    assemble the outcome dict. The caller (``outcomes.derive_loop_outcome``)
    still applies ``_apply_actor_first_terminal_projection`` afterward — that
    step stays in ``outcomes.py`` since it is a thin, outcomes-native overlay,
    not part of this phase's logic."""
    from ouroboros.outcomes import (
        OBJECTIVE_BEST_EFFORT,
        OBJECTIVE_DEGRADED,
        OBJECTIVE_FAIL,
        OBJECTIVE_NOT_EVALUATED,
        OUTCOME_TIER_BLOCKED,
        REASON_FINAL_MESSAGE,
        REASON_REVIEW_CYCLES_EXHAUSTED,
        REASON_REVIEW_QUORUM_UNREACHABLE,
        REASON_TOOL_FAILURE,
        REASON_ACCEPTANCE_REVIEW_SKIPPED_DEADLINE_RESERVE,
        WARN_RESIDUAL_TOOL_ERRORS_WITHOUT_REVIEW,
        EXECUTION_DEGRADED,
        _merge_objective_warning,
        _objective_axis,
        _review_axis,
        _trace_mapping,
        collect_trace_refs,
        extract_final_answer,
    )

    text = classification.text
    execution_status = classification.execution_status
    reason_code = classification.reason_code
    failure = classification.failure
    delivery_candidate = classification.delivery_candidate
    deferred_child_count = classification.deferred_child_count
    deferred_child_suffix = classification.deferred_child_suffix
    forced_best_effort_with_deferred_child = classification.forced_best_effort_with_deferred_child
    acceptance_review_skipped_eligible = classification.acceptance_review_skipped_eligible
    _acceptance_reason = classification.acceptance_reason

    review = _review_axis(llm_trace)
    objective = _objective_axis(review)
    plan_gate = _trace_mapping(llm_trace, "force_plan_decision")
    _plan_gate_status = str(plan_gate.get("status") or "")
    if str(plan_gate.get("enforcement") or "") == "blocking" and (
        _plan_gate_status == "cycles_exhausted"
        or (_plan_gate_status == "open" and plan_gate.get("quorum_unreachable"))
    ):
        # D27: a blocking plan review whose cycle cap is spent never closed — the
        # task terminalizes BLOCKED, never best_effort. B2b extends the same honest
        # terminal to a structurally unreachable reviewer quorum (the agent CHOSE to
        # finalize; the review itself stays open and implementation stayed held).
        _quorum_case = _plan_gate_status != "cycles_exhausted"
        objective.update({
            "status": OBJECTIVE_FAIL,
            "source": ("plan_review_quorum_unreachable" if _quorum_case
                       else "plan_review_cycles_exhausted"),
            "outcome_tier": OUTCOME_TIER_BLOCKED,
            "reason": (REASON_REVIEW_QUORUM_UNREACHABLE if _quorum_case
                       else REASON_REVIEW_CYCLES_EXHAUSTED),
        })
    if deferred_child_count and objective.get("status") != OBJECTIVE_FAIL:
        objective.update({
            "status": OBJECTIVE_BEST_EFFORT,
            "source": "child_result_disposition",
            "deferred_count": deferred_child_count,
        })
    # A forced rail already owns the execution reason; its generic candidate
    # degradation must not erase the more specific deferred-child objective.
    # Invalid finalization controls remain degraded through the branch below.
    if (
        delivery_candidate.get("degraded")
        and not deferred_child_suffix
        and not forced_best_effort_with_deferred_child
        and objective.get("status") != OBJECTIVE_FAIL
    ):
        objective.update({
            "status": OBJECTIVE_DEGRADED,
            "source": "delivery_finalization_control",
        })
    if (
        acceptance_review_skipped_eligible
        and objective.get("status") == OBJECTIVE_NOT_EVALUATED
    ):
        objective.update({
            "status": OBJECTIVE_DEGRADED,
            "source": (
                "task_acceptance_deadline_reserve"
                if _acceptance_reason == REASON_ACCEPTANCE_REVIEW_SKIPPED_DEADLINE_RESERVE
                else "task_acceptance_forced_bypass"
            ),
        })
    # Mutation attribution is evidence for the reviewing panels (attached to the
    # failure-evidence projection below), deliberately never a structural veto.
    # T4 honest residual: cosmetic shell errors no longer degrade execution, so
    # when the objective was never judged (default "auto" with no self-call ->
    # objective not_evaluated) a real overclaim could read as clean. Surface a
    # structural warning (not a failure) so the UI escalates it. Gating on the
    # objective being genuinely unjudged is the honest condition: a review that
    # ran (any verdict) already judged it. No review is auto-run, no env knob, no
    # content inference (Bible P5).
    if classification.cosmetic_tool_errors and objective.get("status") == OBJECTIVE_NOT_EVALUATED:
        _merge_objective_warning(objective, WARN_RESIDUAL_TOOL_ERRORS_WITHOUT_REVIEW)
    final_answer_payload = (
        extract_final_answer(text)
        or (
            str(llm_trace.get("best_valid_final_answer") or "")
            if len(llm_trace.get("tool_calls") or []) <= int(llm_trace.get("best_valid_final_answer_tools") or 0)
            else ""
        )
    )
    headline_reason = reason_code
    headline_failure = failure
    if (
        final_answer_payload
        and execution_status == EXECUTION_DEGRADED
        and reason_code == REASON_TOOL_FAILURE
        and text.strip()
        and not text.lstrip().startswith(("⚠️", "❌"))
    ):
        # Keep execution-health honest in outcome_axes.execution, but do not
        # headline a completed answer-bearing task as a top-level tool failure.
        headline_reason = REASON_FINAL_MESSAGE
        headline_failure = None

    outcome_axes = {
        "schema_version": 1,
        "lifecycle": {"status": "completed"},
        "execution": {
            "status": execution_status,
            "reason_code": reason_code,
            "failure": failure,
            **({"resource_limit": classification.resource_limit} if classification.resource_limit else {}),
            "recoveries": classification.recovered_tool_errors[:20],
            "cosmetic_tool_errors": classification.cosmetic_tool_errors[:20],
            "ignored_tool_errors": classification.ignored_tool_errors[:20],
            "policy_denials": classification.policy_denials[:20],
            **({"mutation_attribution": classification.mutation_attribution} if classification.mutation_attribution else {}),
        },
        "artifacts": {"status": "not_applicable"},
        "objective": objective,
        "review": review,
    }
    outcome = {
        "schema_version": 3,
        "outcome_axes": outcome_axes,
        "review_eligibility": str(review.get("eligibility") or "not_eligible"),
        "review_trigger": str(review.get("trigger") or "not_evaluated"),
        "finish_reason": headline_reason,
        "reason_code": headline_reason,
        "final_text": text,
        # Answer precedence: the final text's explicit FINAL ANSWER marker > the latched
        # answer from an earlier round. The latch recovers a produced answer whenever the
        # final text LACKS a marker (whether empty OR marker-less prose — both lose the
        # structured deliverable a downstream extractor needs) AND no NEW tool work
        # happened since it was stamped. The tool-count guard is the key invariant: with
        # no new grounding, a later marker-less round is the model second-guessing its OWN
        # answer under review PRESSURE, which BIBLE Q7 says review must not let DOWNGRADE a
        # produced answer; new grounding (a higher tool count) instead invalidates the latch.
        "final_answer": final_answer_payload,
        # v6.60.0: keyed on the TYPED final_answer payload (extracted OR latch-recovered),
        # not a re-scan of the final text — a task whose earlier-round answer was latched
        # is not "missing" one; marker-free tasks (no answer_protocol) simply read True,
        # which downstream consumers must interpret via the contract, not as a failure.
        "final_answer_missing_sentinel": not final_answer_payload,
        "failure": headline_failure,
        # The degradation FACT reaches the record itself — benchmark run-summary
        # and result-index readers look for it here.
        "degraded": bool(delivery_candidate.get("degraded")),
        "degraded_reason": str(delivery_candidate.get("degraded_reason") or ""),
        "recoveries": classification.recovered_tool_errors[:20],
        "usage": {
            # ABI-3: the loop's own accounted cost rides the honest name; the
            # projection boundary (cost_projection.with_cost_aliases) still
            # resolves legacy rows deprecated-wins.
            "accounted_upper_bound_usd": (
                round(float(usage["cost"]), 6)
                if usage.get("cost") is not None else None
            ),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_rounds": int(usage.get("rounds") or 0),
            **({"resource_limit": classification.resource_limit} if classification.resource_limit else {}),
        },
        "trace_refs": collect_trace_refs(usage, llm_trace),
    }
    return outcome
