"""Pure requested/permitted/attempted/achieved depth evidence projections."""

from __future__ import annotations

from typing import Any, Dict, Iterable

from ouroboros.contracts.task_contract import normalize_depth_provenance


class TaskDepthError(ValueError):
    """Typed failure for a task-depth value that cannot cross an ingress."""

    def __init__(self, message: str, *, code: str) -> None:
        self.code = str(code or "invalid_task_depth")
        super().__init__(message)


def parse_task_depth(value: Any, *, default: int = 0) -> int:
    """Parse task lineage depth while preserving legacy integer coercion."""
    if value is None or (isinstance(value, str) and not value.strip()):
        try:
            fallback = int(default)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TaskDepthError(
                "task depth default must be a non-negative integer",
                code="negative_task_depth",
            ) from exc
        if fallback < 0:
            raise TaskDepthError(
                "task depth default must be a non-negative integer",
                code="negative_task_depth",
            )
        return fallback
    try:
        # ``int(-0.5)`` is zero, but the source value is still a negative depth
        # request and must not cross an ingress that promises non-negative data.
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
            raise TaskDepthError(
                "task depth must be a non-negative integer",
                code="negative_task_depth",
            )
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, TaskDepthError):
            raise
        raise TaskDepthError("task depth must be an integer", code="invalid_task_depth") from exc
    if parsed < 0:
        raise TaskDepthError(
            "task depth must be a non-negative integer",
            code="negative_task_depth",
        )
    return parsed


def task_depth_provenance(row: Any) -> Dict[str, Any]:
    """Read one task row's normalized depth facts from either preserved projection."""

    if not isinstance(row, dict):
        return {}
    direct = normalize_depth_provenance(row.get("depth_provenance"))
    if direct:
        return direct
    contract = row.get("task_contract") if isinstance(row.get("task_contract"), dict) else {}
    budget = contract.get("delegation_budget") if isinstance(contract.get("delegation_budget"), dict) else {}
    return normalize_depth_provenance(budget.get("depth_provenance"))


def build_depth_summary(
    root_contract: Any, subtree_statuses: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Summarize host-visible depth without forcing a topology or parsing prose."""

    contract = root_contract if isinstance(root_contract, dict) else {}
    budget = contract.get("delegation_budget") if isinstance(contract.get("delegation_budget"), dict) else {}
    root_provenance = normalize_depth_provenance(budget.get("depth_provenance"))
    requested = root_provenance.get("requested_depth")
    if requested is None and "depth_remaining" in budget:
        try:
            requested = max(0, int(budget.get("depth_remaining")))
        except (TypeError, ValueError):
            requested = None
    statuses = [row for row in subtree_statuses if isinstance(row, dict)]
    provenances = [task_depth_provenance(row) for row in statuses]
    provenances = [row for row in provenances if row]
    requested_values = [
        value for value in [requested, *(row.get("requested_depth") for row in provenances)]
        if value is not None
    ]
    requested = max(requested_values) if requested_values else None
    branches = sorted(
        ({"request": row.get("requested_depth"),
          "permitted": row.get("permitted_depth"),
          "achieved": row.get("achieved_depth")} for row in provenances),
        key=lambda row: tuple(
            (row[key] is None, row[key] or 0)
            for key in ("request", "permitted", "achieved")
        ),
    )
    permitted_values = [
        value
        for value in [
            root_provenance.get("permitted_depth"),
            *(row.get("permitted_depth") for row in provenances),
        ]
        if value is not None
    ]
    permitted = min(permitted_values) if permitted_values else None

    def _maximum(key: str) -> Any:
        values = [row.get(key) for row in provenances if row.get(key) is not None]
        if values:
            return max(values)
        return 0 if not statuses else None

    attempted = _maximum("attempted_depth")
    achieved = _maximum("achieved_depth")

    def _reduction_key(row: Dict[str, Any]) -> Any:
        """Most reduced request; ties prefer the strongest, least-achieved ask."""
        ask, cap = row.get("requested_depth"), row.get("permitted_depth")
        reached = row.get("achieved_depth")
        tried = row.get("attempted_depth")
        known = ask is not None and cap is not None
        return (
            known, ask - cap if known else float("-inf"), ask if ask is not None else -1,
            -(reached if reached is not None else -1),
            -(tried if tried is not None else -1),
        )

    status_source = max(provenances, key=_reduction_key) if provenances else {}
    source_requested = status_source.get("requested_depth") if status_source else requested
    source_permitted = status_source.get("permitted_depth") if status_source else permitted
    source_attempted = status_source.get("attempted_depth") if status_source else attempted
    source_achieved = status_source.get("achieved_depth") if status_source else achieved
    if source_requested is None:
        status = "request_unknown"
    elif source_permitted is None or source_attempted is None or source_achieved is None:
        status = "evidence_unknown"
    elif source_permitted < source_requested:
        status = "capability_reduced"
    elif source_achieved >= source_requested:
        status = "achieved"
    else:
        status = "chosen_shallower"
    return {
        "requested_depth": requested,
        "permitted_depth": permitted,
        "attempted_depth": attempted,
        "achieved_depth": achieved,
        "status": status,
        "host_visible_only": True,
        "branches": branches,
    }
