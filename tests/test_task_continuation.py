"""Focused regression tests for review-continuation retirement."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ouroboros.task_continuation import (
    ReviewContinuation,
    archived_continuation_dir,
    continuation_path,
    retire_settled_continuations,
    save_review_continuation,
    sweep_stale_continuations,
)


def _save_aged_continuation(tmp_path, task_id: str, *, days_old: float, obligation_ids=()):
    save_review_continuation(
        tmp_path,
        ReviewContinuation(
            task_id=task_id,
            source="commit_blocked",
            stage="review",
            obligation_ids=list(obligation_ids),
        ),
    )
    path = continuation_path(tmp_path, task_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    aged = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    data["created_ts"] = aged
    data["updated_ts"] = aged
    path.write_text(json.dumps(data), encoding="utf-8")


def test_missing_result_retires_after_three_age_windows(tmp_path):
    _save_aged_continuation(tmp_path, "abandoned", days_old=30)

    retired = retire_settled_continuations(
        tmp_path,
        is_settled=lambda _task_id: False,
        result_missing=lambda _task_id: True,
    )

    assert retired == ["abandoned"]
    assert not continuation_path(tmp_path, "abandoned").exists()
    assert (archived_continuation_dir(tmp_path) / "abandoned.json").exists()


def test_young_missing_result_stays_live(tmp_path):
    _save_aged_continuation(tmp_path, "young", days_old=10)

    retired = retire_settled_continuations(
        tmp_path,
        is_settled=lambda _task_id: False,
        result_missing=lambda _task_id: True,
    )

    assert retired == []
    assert continuation_path(tmp_path, "young").exists()


def test_context_caller_retires_missing_result(tmp_path):
    from ouroboros.review_state import AdvisoryReviewState, save_state
    from ouroboros.task_continuation import retire_settled_continuations_for_context

    _save_aged_continuation(tmp_path, "missing-context", days_old=30)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    state = AdvisoryReviewState()
    save_state(tmp_path, state)

    retired = retire_settled_continuations_for_context(
        tmp_path,
        state,
        lambda _task_id: None,
    )

    assert retired == ["missing-context"]
    assert not continuation_path(tmp_path, "missing-context").exists()


def test_settled_open_work_still_survives_retirement(tmp_path):
    _save_aged_continuation(tmp_path, "open", days_old=30)

    retired = retire_settled_continuations(
        tmp_path,
        is_settled=lambda _task_id: True,
        has_open_work=lambda _item: True,
        result_missing=lambda _task_id: False,
    )

    assert retired == []
    assert continuation_path(tmp_path, "open").exists()


def test_settled_result_missing_false_uses_existing_retirement_path(tmp_path):
    _save_aged_continuation(tmp_path, "closed", days_old=30)

    retired = retire_settled_continuations(
        tmp_path,
        is_settled=lambda _task_id: True,
        result_missing=lambda _task_id: False,
    )

    assert retired == ["closed"]
    assert not continuation_path(tmp_path, "closed").exists()
    assert (archived_continuation_dir(tmp_path) / "closed.json").exists()


def test_result_missing_exception_leaves_that_record_and_processes_others(tmp_path):
    _save_aged_continuation(tmp_path, "bad-probe", days_old=30)
    _save_aged_continuation(tmp_path, "old", days_old=30)

    def _result_missing(task_id: str) -> bool:
        if task_id == "bad-probe":
            raise RuntimeError("result lookup unavailable")
        return True

    retired = retire_settled_continuations(
        tmp_path,
        is_settled=lambda _task_id: False,
        result_missing=_result_missing,
    )

    assert retired == ["old"]
    assert continuation_path(tmp_path, "bad-probe").exists()
    assert not continuation_path(tmp_path, "old").exists()


def test_sweep_retires_missing_result_with_empty_open_ledger(tmp_path):
    from ouroboros.review_state import AdvisoryReviewState, save_state

    _save_aged_continuation(tmp_path, "missing-context", days_old=30)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    save_state(tmp_path, AdvisoryReviewState())

    retired = sweep_stale_continuations(tmp_path)

    assert retired == ["missing-context"]
    assert not continuation_path(tmp_path, "missing-context").exists()


def test_gitignore_contains_runtime_scratch():
    from pathlib import Path

    ignore = (Path(__file__).parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert ".ouroboros/" in ignore.splitlines()


def test_sweep_is_throttled_by_periodic_marker(monkeypatch, caplog):
    import time

    import server
    import ouroboros.task_continuation as continuation_module

    caplog.set_level("INFO", logger="server")

    monkeypatch.setattr(server, "_LAST_CANCEL_INTENT_SWEEP", [time.time()])
    monkeypatch.setattr(server, "_LAST_STORE_GC", [time.time()])
    monkeypatch.setattr(server, "_periodic_store_gc", lambda: None)
    monkeypatch.setattr(server, "_LAST_REVIEW_CONTINUATION_SWEEP", [0.0])

    seen = []
    monkeypatch.setattr(
        continuation_module,
        "sweep_stale_continuations",
        lambda root: seen.append(root) or ["old"],
    )

    server._periodic_supervisor_maintenance([time.time()], [time.time()])
    server._periodic_supervisor_maintenance([time.time()], [time.time()])

    assert seen == [server.DATA_DIR]
    assert "old" in caplog.text
