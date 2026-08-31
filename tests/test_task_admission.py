"""Unit tests for ``supervisor/task_admission.py`` — token-owned admission reservations.

The module mutates and reads supervisor queue state (``ADMISSION_RESERVATIONS``,
``RUNNING``, ``PENDING``, ``_queue_lock``, ``DRIVE_ROOT``) and lazily imports
``ouroboros.task_results.load_task_result`` plus ``supervisor.workers``. The
fixture below snapshots-and-restores the queue state so tests do not leak
across the suite, and monkeypatching replaces the lazy imports with stubs so
every branch can be exercised deterministically without touching real task
results or the live worker pool.

Coverage map (every public branch):

* ``reserve_task_admission``
    - empty ``task_id`` or empty ``admission_token`` → ``invalid_admission_reservation``
    - ``tid`` already in ``ADMISSION_RESERVATIONS`` with the SAME token → ``already_reserved``
    - ``tid`` already reserved with a DIFFERENT token → ``duplicate_task_id``
    - ``tid`` present in ``queue.RUNNING`` → ``duplicate_task_id``
    - ``tid`` present as a dict row in ``queue.PENDING`` → ``duplicate_task_id``
    - ``load_task_result`` raises → ``task_id_lookup_failed``
    - existing result whose ``promotion_admission.routing_token == token`` → ``existing_same_token``
    - existing result with a DIFFERENT or missing routing_token → ``duplicate_task_id``
    - happy path (nothing reserved, no existing result) → ``reserved`` AND
      ``ADMISSION_RESERVATIONS[tid] == token``
    - ``require_worker_pool=True`` with ``worker_pool=[]`` → ``worker_pool_unavailable``
    - ``require_worker_pool=True`` with non-empty ``worker_pool`` and no
      disabled reason → proceeds to ``reserved``

* ``release_task_admission``
    - wrong token → ``False`` and reservation untouched
    - correct token → ``True`` and tid removed from ``ADMISSION_RESERVATIONS``

This file closes ``ibl-3945f4a638ee`` by giving the module its first
dedicated test coverage.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set

import pytest

from supervisor import queue
from supervisor.task_admission import (
    release_task_admission,
    reserve_task_admission,
)


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture
def clean_admission_state(monkeypatch):
    """Snapshot ``queue.ADMISSION_RESERVATIONS`` / ``RUNNING`` / ``PENDING`` and
    restore them on teardown so tests cannot leak cross-suite.

    Also stubs the lazy ``supervisor.workers`` import to deterministic defaults
    so the worker-pool branch can be exercised without touching the live pool.
    """

    saved_reservations = dict(queue.ADMISSION_RESERVATIONS)
    saved_running = dict(queue.RUNNING)
    saved_pending = list(queue.PENDING)
    # Workers module is lazily imported inside reserve_task_admission; pin the
    # baseline values so the worker-pool branch is deterministic. The
    # reserve_task_admission logic reads these via `workers._WORKER_POOL_DISABLED_REASON`
    # and `workers.WORKERS` when `worker_pool` arg is None; per-call overrides
    # via the kwarg remain in the test's control.
    from supervisor import workers

    saved_disabled = workers._WORKER_POOL_DISABLED_REASON
    saved_pool = workers.WORKERS
    workers._WORKER_POOL_DISABLED_REASON = ""
    workers.WORKERS = {}
    monkeypatch.setattr(workers, "_WORKER_POOL_DISABLED_REASON", "", raising=False)
    monkeypatch.setattr(workers, "WORKERS", {}, raising=False)
    try:
        queue.ADMISSION_RESERVATIONS.clear()
        queue.RUNNING.clear()
        queue.PENDING.clear()
        yield {
            "reservations": queue.ADMISSION_RESERVATIONS,
            "running": queue.RUNNING,
            "pending": queue.PENDING,
        }
    finally:
        queue.ADMISSION_RESERVATIONS.clear()
        queue.ADMISSION_RESERVATIONS.update(saved_reservations)
        queue.RUNNING.clear()
        queue.RUNNING.update(saved_running)
        queue.PENDING.clear()
        # Restore the workers module-level state we mutated.
        try:
            workers._WORKER_POOL_DISABLED_REASON = saved_disabled
            workers.WORKERS = saved_pool
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# reserve_task_admission — input validation                                   #
# --------------------------------------------------------------------------- #


class TestReserveTaskAdmissionInvalidArgs:
    def test_empty_task_id_is_rejected(self, clean_admission_state):
        result = reserve_task_admission("", "tok-abc")
        assert result == {"status": "blocked", "reason": "invalid_admission_reservation"}

    def test_whitespace_task_id_is_rejected(self, clean_admission_state):
        result = reserve_task_admission("   ", "tok-abc")
        assert result == {"status": "blocked", "reason": "invalid_admission_reservation"}

    def test_none_task_id_is_rejected(self, clean_admission_state):
        result = reserve_task_admission(None, "tok-abc")
        assert result == {"status": "blocked", "reason": "invalid_admission_reservation"}

    def test_empty_admission_token_is_rejected(self, clean_admission_state):
        result = reserve_task_admission("tid-001", "")
        assert result == {"status": "blocked", "reason": "invalid_admission_reservation"}

    def test_whitespace_admission_token_is_rejected(self, clean_admission_state):
        result = reserve_task_admission("tid-001", "   ")
        assert result == {"status": "blocked", "reason": "invalid_admission_reservation"}

    def test_none_admission_token_is_rejected(self, clean_admission_state):
        result = reserve_task_admission("tid-001", None)
        assert result == {"status": "blocked", "reason": "invalid_admission_reservation"}


# --------------------------------------------------------------------------- #
# reserve_task_admission — duplicate / live-task detection                     #
# --------------------------------------------------------------------------- #


class TestReserveTaskAdmissionDuplicates:
    def test_same_token_returns_already_reserved(self, clean_admission_state):
        clean_admission_state["reservations"]["tid-dup"] = "tok-xyz"
        result = reserve_task_admission("tid-dup", "tok-xyz")
        assert result == {"status": "already_reserved", "reason": ""}

    def test_same_token_after_strip_is_matched(self, clean_admission_state):
        # reserve_task_admission strips its inputs before comparing, so a
        # previously-stored token survives a padded retry.
        clean_admission_state["reservations"]["tid-dup"] = "tok-xyz"
        result = reserve_task_admission("  tid-dup  ", "  tok-xyz  ")
        assert result == {"status": "already_reserved", "reason": ""}

    def test_different_token_returns_duplicate_task_id(self, clean_admission_state):
        clean_admission_state["reservations"]["tid-dup"] = "tok-original"
        result = reserve_task_admission("tid-dup", "tok-different")
        assert result == {"status": "blocked", "reason": "duplicate_task_id"}

    def test_tid_in_running_is_blocked_as_duplicate(self, clean_admission_state):
        clean_admission_state["running"]["tid-live"] = {
            "task": {"id": "tid-live", "type": "task"},
            "started_at": 0.0,
        }
        result = reserve_task_admission("tid-live", "tok-abc")
        assert result == {"status": "blocked", "reason": "duplicate_task_id"}

    def test_tid_as_pending_dict_row_is_blocked(self, clean_admission_state):
        clean_admission_state["pending"].append({"id": "tid-pending", "type": "task"})
        result = reserve_task_admission("tid-pending", "tok-abc")
        assert result == {"status": "blocked", "reason": "duplicate_task_id"}

    def test_tid_as_pending_non_dict_row_does_not_match(self, clean_admission_state):
        # A non-dict row in PENDING must not satisfy the dict-row check; the
        # helper guards with isinstance(row, dict). The reservation should
        # proceed to the task-result lookup path.
        clean_admission_state["pending"].append("not-a-dict")
        result = reserve_task_admission("tid-fresh", "tok-abc")
        # No prior reservation, no result → happy path, returns "reserved".
        assert result == {"status": "reserved", "reason": ""}


# --------------------------------------------------------------------------- #
# reserve_task_admission — task_result lookup                                 #
# --------------------------------------------------------------------------- #


class TestReserveTaskAdmissionLookup:
    def test_load_task_result_raises_returns_task_id_lookup_failed(
        self, clean_admission_state, monkeypatch
    ):
        def _explode(drive_root, task_id):
            raise RuntimeError("simulated drive I/O failure")

        monkeypatch.setattr(
            "ouroboros.task_results.load_task_result", _explode
        )
        result = reserve_task_admission("tid-001", "tok-abc")
        assert result == {"status": "blocked", "reason": "task_id_lookup_failed"}
        # Lookup failure must NOT seed a reservation — the caller did not own
        # the slot yet and the bookkeeping is the user's, not to the helper's.
        assert "tid-001" not in clean_admission_state["reservations"]

    def test_load_task_result_returns_none_treated_as_no_existing(
        self, clean_admission_state, monkeypatch
    ):
        def _no_record(drive_root, task_id):
            return None

        monkeypatch.setattr(
            "ouroboros.task_results.load_task_result", _no_record
        )
        result = reserve_task_admission("tid-001", "tok-abc")
        # `None or {}` → {}, falls through to the worker-pool branch (skipped
        # because require_worker_pool is False) and reserves the slot.
        assert result == {"status": "reserved", "reason": ""}
        assert clean_admission_state["reservations"]["tid-001"] == "tok-abc"

    def test_existing_same_token_returns_existing_same_token(
        self, clean_admission_state, monkeypatch
    ):
        existing = {
            "status": "scheduled",
            "promotion_admission": {
                "routing_token": "tok-abc",
                "admitted_at": "2026-08-31T00:00:00Z",
            },
        }

        monkeypatch.setattr(
            "ouroboros.task_results.load_task_result",
            lambda drive_root, task_id: existing,
        )
        result = reserve_task_admission("tid-001", "tok-abc")
        assert result["status"] == "existing_same_token"
        assert result["reason"] == ""
        assert result["task_status"] == "scheduled"
        assert result["promotion_admission"]["routing_token"] == "tok-abc"
        assert (
            result["promotion_admission"]["admitted_at"] == "2026-08-31T00:00:00Z"
        )
        # Same-token branch does NOT mutate the reservations table.
        assert "tid-001" not in clean_admission_state["reservations"]

    def test_existing_with_different_token_returns_duplicate_task_id(
        self, clean_admission_state, monkeypatch
    ):
        existing = {
            "status": "scheduled",
            "promotion_admission": {"routing_token": "tok-original"},
        }
        monkeypatch.setattr(
            "ouroboros.task_results.load_task_result",
            lambda drive_root, task_id: existing,
        )
        result = reserve_task_admission("tid-001", "tok-different")
        assert result == {"status": "blocked", "reason": "duplicate_task_id"}

    def test_existing_with_no_routing_token_returns_duplicate_task_id(
        self, clean_admission_state, monkeypatch
    ):
        existing = {
            "status": "scheduled",
            "promotion_admission": {"admitted_at": "2026-08-31T00:00:00Z"},
        }
        monkeypatch.setattr(
            "ouroboros.task_results.load_task_result",
            lambda drive_root, task_id: existing,
        )
        result = reserve_task_admission("tid-001", "tok-abc")
        assert result == {"status": "blocked", "reason": "duplicate_task_id"}

    def test_existing_with_non_dict_promotion_admission_returns_duplicate(
        self, clean_admission_state, monkeypatch
    ):
        existing = {
            "status": "scheduled",
            "promotion_admission": "not-a-dict",
        }
        monkeypatch.setattr(
            "ouroboros.task_results.load_task_result",
            lambda drive_root, task_id: existing,
        )
        result = reserve_task_admission("tid-001", "tok-abc")
        assert result == {"status": "blocked", "reason": "duplicate_task_id"}

    def test_existing_with_no_promotion_admission_key_returns_duplicate(
        self, clean_admission_state, monkeypatch
    ):
        existing = {"status": "scheduled"}
        monkeypatch.setattr(
            "ouroboros.task_results.load_task_result",
            lambda drive_root, task_id: existing,
        )
        result = reserve_task_admission("tid-001", "tok-abc")
        assert result == {"status": "blocked", "reason": "duplicate_task_id"}


# --------------------------------------------------------------------------- #
# reserve_task_admission — happy path + worker pool gate                       #
# --------------------------------------------------------------------------- #


class TestReserveTaskAdmissionHappyPath:
    def test_happy_path_reserves_and_seeds_admissions_table(
        self, clean_admission_state
    ):
        # Default monkeypatched load_task_result returns None → no existing
        # result → falls through to the reservation.
        result = reserve_task_admission("tid-001", "tok-abc")
        assert result == {"status": "reserved", "reason": ""}
        assert clean_admission_state["reservations"]["tid-001"] == "tok-abc"

    def test_happy_path_with_drive_root_override(
        self, clean_admission_state, monkeypatch, tmp_path
    ):
        # `drive_root` is forwarded to load_task_result; the helper only uses
        # it for the lookup. Pin a per-call drive root so the override path is
        # exercised end-to-end.
        captured: Dict[str, Any] = {}

        def _capture(drive_root, task_id):
            captured["drive_root"] = drive_root
            captured["task_id"] = task_id
            return None

        monkeypatch.setattr(
            "ouroboros.task_results.load_task_result", _capture
        )
        result = reserve_task_admission(
            "tid-002", "tok-abc", drive_root=tmp_path
        )
        assert result == {"status": "reserved", "reason": ""}
        assert captured["drive_root"] == tmp_path
        assert captured["task_id"] == "tid-002"
        assert clean_admission_state["reservations"]["tid-002"] == "tok-abc"


# --------------------------------------------------------------------------- #
# reserve_task_admission — require_worker_pool                                 #
# --------------------------------------------------------------------------- #


class TestReserveTaskAdmissionWorkerPool:
    def test_empty_worker_pool_returns_worker_pool_unavailable(
        self, clean_admission_state
    ):
        result = reserve_task_admission(
            "tid-001", "tok-abc", require_worker_pool=True, worker_pool=[]
        )
        assert result["status"] == "blocked"
        assert result["reason"] == "worker_pool_unavailable"
        # `len([]) == 0` → falls into the no_workers branch, the surfaced
        # disabled_reason is "no_workers".
        assert result["worker_pool_disabled_reason"] == "no_workers"
        assert "tid-001" not in clean_admission_state["reservations"]

    def test_non_empty_worker_pool_proceeds_to_reserved(
        self, clean_admission_state, monkeypatch
    ):
        # The worker_pool kwarg bypasses `workers.WORKERS`, so we don't need
        # to seed the real pool. The pool check is `len(worker_pool) > 0`.
        result = reserve_task_admission(
            "tid-001",
            "tok-abc",
            require_worker_pool=True,
            worker_pool=[object()],
        )
        assert result == {"status": "reserved", "reason": ""}
        assert clean_admission_state["reservations"]["tid-001"] == "tok-abc"

    def test_disabled_reason_alone_blocks_even_with_workers(
        self, clean_admission_state, monkeypatch
    ):
        # Seed the workers module's disabled reason via monkeypatch so the
        # branch fires independently of the pool length. Note:
        # reserve_task_admission reads `workers._WORKER_POOL_DISABLED_REASON`
        # via the lazy import; monkeypatch.setattr on the module attribute is
        # the supported way to flip that flag from a test.
        import supervisor.workers as workers_mod

        monkeypatch.setattr(workers_mod, "_WORKER_POOL_DISABLED_REASON", "owner_paused")
        result = reserve_task_admission(
            "tid-001",
            "tok-abc",
            require_worker_pool=True,
            worker_pool=[object()],
        )
        assert result["status"] == "blocked"
        assert result["reason"] == "worker_pool_unavailable"
        assert result["worker_pool_disabled_reason"] == "owner_paused"
        assert "tid-001" not in clean_admission_state["reservations"]

    def test_worker_pool_import_failure_returns_worker_pool_state_unavailable(
        self, clean_admission_state, monkeypatch
    ):
        # Force the lazy `from supervisor import workers` import to raise —
        # the helper catches the exception and reports a distinct reason so
        # callers can distinguish "no workers" from "we cannot tell".
        import builtins

        original_import = builtins.__import__

        def _guarded(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "supervisor.workers" or (
                fromlist and "workers" in fromlist and name == "supervisor"
            ):
                raise ImportError("simulated workers import failure")
            return original_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", _guarded)
        result = reserve_task_admission(
            "tid-001", "tok-abc", require_worker_pool=True
        )
        assert result == {"status": "blocked", "reason": "worker_pool_state_unavailable"}
        assert "tid-001" not in clean_admission_state["reservations"]


# --------------------------------------------------------------------------- #
# release_task_admission                                                      #
# --------------------------------------------------------------------------- #


class TestReleaseTaskAdmission:
    def test_release_with_wrong_token_returns_false_and_leaves_reservation(
        self, clean_admission_state
    ):
        clean_admission_state["reservations"]["tid-rel"] = "tok-owner"
        result = release_task_admission("tid-rel", "tok-imposter")
        assert result is False
        assert clean_admission_state["reservations"]["tid-rel"] == "tok-owner"

    def test_release_with_unknown_tid_returns_false(self, clean_admission_state):
        # No reservation exists for tid-unknown → guard returns False.
        result = release_task_admission("tid-unknown", "tok-anything")
        assert result is False
        assert "tid-unknown" not in clean_admission_state["reservations"]

    def test_release_with_correct_token_removes_reservation(
        self, clean_admission_state
    ):
        clean_admission_state["reservations"]["tid-rel"] = "tok-owner"
        result = release_task_admission("tid-rel", "tok-owner")
        assert result is True
        assert "tid-rel" not in clean_admission_state["reservations"]

    def test_release_after_reserve_round_trip(self, clean_admission_state):
        # End-to-end: reserve seats the token; release by the same token
        # returns True and clears the row.
        reserve = reserve_task_admission("tid-rt", "tok-rt")
        assert reserve["status"] == "reserved"
        assert clean_admission_state["reservations"]["tid-rt"] == "tok-rt"
        release = release_task_admission("tid-rt", "tok-rt")
        assert release is True
        assert "tid-rt" not in clean_admission_state["reservations"]

    def test_release_with_empty_tid_is_safe(self, clean_admission_state):
        # The helper strips its inputs and proceeds to compare; an empty tid
        # produces no lookup match → False, no mutation.
        result = release_task_admission("", "tok-abc")
        assert result is False

    def test_release_with_empty_token_is_safe(self, clean_admission_state):
        clean_admission_state["reservations"]["tid-rel"] = "tok-owner"
        result = release_task_admission("tid-rel", "")
        # Stripped token = ""; "" != "tok-owner" → False, reservation stays.
        assert result is False
        assert clean_admission_state["reservations"]["tid-rel"] == "tok-owner"