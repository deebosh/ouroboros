"""Leaf module: dict-projection helpers for review/advisory status payloads.

Split out of ``ouroboros/review_evidence.py`` (ibl-978e5cd9258f — one of the
GIANT_PATHS files in the size-ratchet coordination campaign) purely to
shrink that module under the size-ratchet cap — no behavior change. These
functions serialize typed advisory-run / commit-attempt / obligation / debt
/ continuation records into plain dicts for API/tool responses; they don't
reach back into ``review_evidence.py``'s own logic, only into shared
utilities, so this is a normal top-level import in both directions (no
circular-dependency concern the way ``ouroboros.outcomes`` /
``ouroboros._outcome_axes`` had).
"""

from __future__ import annotations

from typing import Any, Dict

from ouroboros.utils import truncate_review_artifact


def _run_failure_reason(run: Any) -> str | None:
    """Typed cause for a non-parseable advisory run. Diagnostics only.

    Never consumed by the commit gate, freshness, or debt: it exists so a
    repeated deterministic failure is visible after the FIRST attempt instead of
    reading as a generic ``parse_failure`` for hours.
    """
    if str(getattr(run, "status", "") or "") != "parse_failure":
        return None
    from ouroboros.triad_review import empty_array_is_verified_clean

    raw = str(getattr(run, "raw_result", "") or "").strip()
    if not raw:
        return "empty_response"
    if empty_array_is_verified_clean(raw):
        # A contract-compliant clean verdict was still rejected: that is a
        # regression of the sentinel contract, not a model failure. Asking the
        # shared predicate — not a second substring test — is what keeps this
        # diagnostic honest when the contract changes.
        return "clean_sentinel_rejected"
    if raw.startswith("[") or raw.startswith("```"):
        return "malformed_array"
    return "non_json_prose"


def _review_status_run_to_dict(run: Any) -> Dict[str, Any]:
    findings = [
        item for item in (getattr(run, "items", []) or [])
        if isinstance(item, dict) and str(item.get("verdict", "")).upper() == "FAIL"
    ]
    data = {
        "snapshot_hash": str(getattr(run, "snapshot_hash", ""))[:12],
        "critical_findings": sum(1 for item in findings if str(item.get("severity", "")).lower() == "critical"),
        "total_findings": len(findings),
        "attempt": int(getattr(run, "attempt", 0) or 0) or None,
    }
    for key in ("commit_message", "status", "ts", "snapshot_summary"):
        data[key] = str(getattr(run, key, "") or "")
    for key in ("bypass_reason", "repo_key", "tool_name", "task_id"):
        data[key] = str(getattr(run, key, "") or "") or None
    # Already persisted per run, previously dropped from the projection: without
    # these the owner sees repeated identical statuses with no usable cause.
    data["failure_reason"] = _run_failure_reason(run)
    data["model_used"] = str(getattr(run, "model_used", "") or "") or None
    duration = getattr(run, "duration_sec", None)
    data["duration_sec"] = round(float(duration), 2) if duration else None
    prompt_chars = getattr(run, "prompt_chars", None)
    data["prompt_chars"] = int(prompt_chars) or None if prompt_chars else None
    # Deliberately NO raw excerpt here: raw_result is untrusted reviewer output
    # that can echo secret-bearing diff content, and this projection is returned
    # to the active model. The typed reason above is derived, not raw, and the
    # complete text stays in the durable advisory run record addressed by the
    # snapshot_hash/ts already on this row.
    return data


def _review_status_attempt_payload(ca: Any) -> Dict[str, Any] | None:
    if ca is None:
        return None
    data = {
        key: getattr(ca, key) or None
        for key in ("block_reason", "repo_key", "tool_name", "task_id", "phase", "fingerprint_status")
    }
    data.update({
        "status": ca.status,
        "commit_message": ca.commit_message,
        "ts": ca.ts,
        "duration_sec": round(ca.duration_sec, 1),
        "block_details_preview": truncate_review_artifact(ca.block_details, limit=300) if ca.block_details else None,
        "attempt": int(ca.attempt or 0) or None,
        "blocked": bool(ca.blocked),
        "late_result_pending": bool(ca.late_result_pending),
        "critical_findings": len(ca.critical_findings or []),
        "advisory_findings": len(ca.advisory_findings or []),
        "obligation_ids": list(ca.obligation_ids or []),
        "readiness_warnings": list(ca.readiness_warnings or []),
        "pre_review_fingerprint": ca.pre_review_fingerprint[:12] or None,
        "post_review_fingerprint": ca.post_review_fingerprint[:12] or None,
        "degraded_reasons": list(ca.degraded_reasons or []),
        # Max-Review-Cycles accounting facts (Q16 auditability): the typed
        # block class, the dispatch-paid fact, and the identities the free
        # refusal/replay decisions key on.
        "block_class": str(getattr(ca, "block_class", "") or "") or None,
        "paid": bool(getattr(ca, "paid", False)),
        "rebuttal_sha256": str(getattr(ca, "rebuttal_sha256", "") or "")[:12] or None,
        "review_contract_fingerprint": str(getattr(ca, "review_contract_fingerprint", "") or "")[:12] or None,
        "root_task_id": str(getattr(ca, "root_task_id", "") or "") or None,
        **_review_status_actor_summary(ca),
    })
    return data


def _review_status_attempt_to_dict(item: Any) -> Dict[str, Any]:
    data = _review_status_attempt_payload(item) or {}
    data.pop("commit_message", None)
    data.pop("block_details_preview", None)
    data["ts"] = item.ts
    return data


def _review_status_actor_summary(attempt: Any) -> Dict[str, Any]:
    scope_raw = getattr(attempt, "scope_raw_result", None) or {}
    return {
        "triad_actors": [
            {"model_id": r.get("model_id", "?"), "status": r.get("status", "?")}
            for r in (getattr(attempt, "triad_raw_results", None) or [])
        ],
        "scope_actor": (
            {"model_id": scope_raw.get("model_id", "?"), "status": scope_raw.get("status", "?")}
            if scope_raw.get("status") else None
        ),
    }


def _review_status_obligation_to_dict(item: Any) -> Dict[str, Any]:
    return {
        **{key: getattr(item, key, "") for key in ("obligation_id", "fingerprint", "item", "severity", "status")},
        "reason": truncate_review_artifact(item.reason, limit=200),
        "source_ts": item.source_attempt_ts,
        "source_commit": item.source_attempt_msg,
    }


def _review_status_debt_to_dict(item: Any) -> Dict[str, Any]:
    return {
        "debt_id": item.debt_id,
        "category": item.category,
        "title": item.title,
        "summary": truncate_review_artifact(item.summary, limit=220),
        "status": item.status,
        "severity": item.severity,
        "source": item.source,
        "repo_key": item.repo_key or None,
        "source_obligation_ids": list(item.source_obligation_ids or []),
        "evidence": list(item.evidence or []),
        "updated_at": item.updated_at,
    }


def _review_status_message(projection: Dict[str, Any]) -> str:
    ca = projection.get("selected_attempt")
    current = f"Current advisory: {projection['effective_status']}"
    if ca and ca.status in ("blocked", "failed"):
        reason_map = {
            "no_advisory": "No fresh advisory review found. Run preflight_review first.",
            "critical_findings": "Reviewers found critical issues. Fix all issues listed, then re-run advisory.",
            "review_quorum": "Not enough review models responded. Retry — usually transient.",
            "parse_failure": "Review models could not produce parseable output. Retry the commit.",
            "infra_failure": "Infrastructure failure. Check block_details.",
            "scope_blocked": "Scope reviewer blocked the commit. Address scope review findings.",
            "preflight": "Preflight check failed. Stage all related files.",
            "revalidation_failed": "The staged diff changed after review. Re-run advisory and review.",
            "fingerprint_unavailable": "The staged diff could not be fingerprinted. Fix git diff and retry.",
            "overlap_guard": "Another reviewed attempt is still active. Wait or expire it before retrying.",
            "attempt_cap_reached": "The same staged diff was review-blocked repeatedly. Change the diff or rebut via review_rebuttal.",
            "identical_diff_refused": "This exact staged diff was already review-blocked. Change the diff or supply a NEW review_rebuttal (identical bytes are never re-reviewed for pay).",
            "review_cycles_exhausted": "This task tree spent its paid review cycles (OUROBOROS_REVIEW_MAX_CYCLES). Finalize honestly or ask the owner to raise the cap.",
            "review_subject_binding_mismatch": "The reviewed managed subject is not the tree this commit would write. Re-stage the intended candidate and retry so review and commit describe the same tree.",
        }
        label = "BLOCKED" if ca.status == "blocked" else "FAILED"
        current = (
            f"Last commit {label} ({ca.block_reason or 'unclassified'}): "
            f"{reason_map.get(ca.block_reason, ca.block_reason or 'unknown')}"
            f"  |  {current}"
        )
    if projection.get("open_debts"):
        current = f"{current}  |  Commit-readiness debt: {len(projection['open_debts'])}"
    return current


def _attempt_to_dict(item: Any) -> Dict[str, Any]:
    data = {
        key: str(getattr(item, key, "") or "")
        for key in (
            "ts", "tool_name", "status", "phase", "block_reason", "scope_model",
            # Max-Review-Cycles accounting facts (Q16 auditability).
            "block_class", "rebuttal_sha256", "review_contract_fingerprint",
            "root_task_id",
        )
    }
    data.update({
        "paid": bool(getattr(item, "paid", False)),
        "raw_stripped": bool(getattr(item, "raw_stripped", False)),
        "attempt": int(getattr(item, "attempt", 0) or 0),
        "late_result_pending": bool(getattr(item, "late_result_pending", False)),
        "duration_sec": float(getattr(item, "duration_sec", 0.0) or 0.0),
        "critical_findings": list(getattr(item, "critical_findings", []) or []),
        "advisory_findings": list(getattr(item, "advisory_findings", []) or []),
        "triad_raw_results": list(getattr(item, "triad_raw_results", []) or []),
        "scope_raw_result": dict(getattr(item, "scope_raw_result", {}) or {}),
    })
    for key in ("readiness_warnings", "obligation_ids", "degraded_reasons", "triad_models"):
        data[key] = [str(x) for x in (getattr(item, key, []) or [])]
    return data


_RESPONDED_STATUSES = frozenset({"fresh", "stale"})


def _run_to_dict(item: Any) -> Dict[str, Any]:
    """Serialise AdvisoryRunRecord with responded/skipped/error status summary."""
    valid_items = [entry for entry in list(getattr(item, "items", []) or []) if isinstance(entry, dict)]
    fail_items = [
        {
            "severity": str(entry.get("severity", "") or "advisory"),
            "item": str(entry.get("item", "") or ""),
            "reason": str(entry.get("reason", "") or ""),
        }
        for entry in valid_items
        if str(entry.get("verdict", "")).upper() == "FAIL"
    ]
    total_items = len(valid_items)

    status = str(getattr(item, "status", "") or "")
    bypass_reason = str(getattr(item, "bypass_reason", "") or "")
    raw_result_text = str(getattr(item, "raw_result", "") or "")

    status_summary = status if status in {"bypassed", "skipped", "parse_failure", "error"} else status or "unknown"
    if status in _RESPONDED_STATUSES:
        status_summary = (
            "responded_with_findings" if fail_items
            else "responded_clean" if total_items > 0
            else "responded_empty"
        )

    return {
        "ts": str(getattr(item, "ts", "") or ""),
        "status": status,
        "status_summary": status_summary,
        "repo_key": str(getattr(item, "repo_key", "") or ""),
        "bypass_reason": bypass_reason,
        "snapshot_summary": str(getattr(item, "snapshot_summary", "") or ""),
        "findings": fail_items,
        "total_items": total_items,
        "raw_result_present": bool(raw_result_text),
        "readiness_warnings": [str(x) for x in (getattr(item, "readiness_warnings", []) or [])],
        "prompt_chars": int(getattr(item, "prompt_chars", 0) or 0),
        "model_used": str(getattr(item, "model_used", "") or ""),
        "duration_sec": float(getattr(item, "duration_sec", 0.0) or 0.0),
    }


def _obligation_to_dict(item: Any) -> Dict[str, Any]:
    return {
        "obligation_id": str(getattr(item, "obligation_id", "") or ""),
        "fingerprint": str(getattr(item, "fingerprint", "") or ""),
        "item": str(getattr(item, "item", "") or ""),
        "severity": str(getattr(item, "severity", "") or ""),
        "reason": str(getattr(item, "reason", "") or ""),
        "status": str(getattr(item, "status", "") or ""),
        "created_ts": str(getattr(item, "created_ts", "") or ""),
        "updated_ts": str(getattr(item, "updated_ts", "") or ""),
    }


def _continuation_to_dict(item: Any) -> Dict[str, Any]:
    data = {
        key: str(getattr(item, key, "") or "")
        for key in ("task_id", "source", "stage", "tool_name", "block_reason", "updated_ts")
    }
    data.update({
        "attempt": int(getattr(item, "attempt", 0) or 0),
        "critical_findings": list(getattr(item, "critical_findings", []) or []),
        "advisory_findings": list(getattr(item, "advisory_findings", []) or []),
        "readiness_warnings": [str(x) for x in (getattr(item, "readiness_warnings", []) or [])],
    })
    return data


def _debt_to_dict(item: Any) -> Dict[str, Any]:
    data = {
        key: str(getattr(item, key, "") or "")
        for key in ("debt_id", "category", "title", "summary", "status", "severity", "source", "repo_key", "updated_at")
    }
    data["source_obligation_ids"] = [str(x) for x in (getattr(item, "source_obligation_ids", []) or [])]
    data["evidence"] = [str(x) for x in (getattr(item, "evidence", []) or [])]
    return data
