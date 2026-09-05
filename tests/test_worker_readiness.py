"""The worker readiness contract: a spawned or respawned slot is unassignable until its child confirms ready.

What is pinned, on fake process objects (no child is ever forked here):

* spawn installs every slot ``reaping`` and hands the whole set to the ONE readiness seam;
* respawn installs its fresh slot the same way, through the same seam, carrying its attempt count;
* the seam opens a slot only on the child's OWN ``worker_ready`` row (matched by pid), verifying the
  booted SHA in the same step; a foreign pid's row does not open it;
* no ``worker_ready`` inside the window -> the child is torn down (process tree), the slot is
  replaced through ``respawn_worker`` and a typed ``worker_ready_timeout`` row names slot, pid,
  wait and reason;
* the replacement loop is bounded: at ``WORKER_READY_MAX_ATTEMPTS`` the slot is parked (kept
  ``reaping``, no further respawn) and the owner is told;
* a child that DIED during boot is released to the crash detector, which already owns death;
* the assignment path is unchanged for a ready slot: ``assign_tasks`` skips a booting slot and
  dispatches to the open one, and a slot the seam opened is dispatched to like any other.

Readiness is deliberately NOT process liveness (``proc.is_alive``) and NOT the task idle rail: a
child deadlocked on a lock inherited across fork is alive and holds no task.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ouroboros.utils import append_jsonl


class _FakeProc:
    def __init__(self, pid: int, *, alive: bool = True, exitcode=None):
        self.pid = pid
        self._alive = alive
        self.exitcode = exitcode
        self.joined = False
        self.daemon = False

    def start(self):
        pass

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self.joined = True


def _rows(path, event_type: str) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("type") == event_type:
            out.append(row)
    return out


@pytest.fixture
def pool(monkeypatch, tmp_path):
    """The pool facade rebound to a throwaway root with no live workers."""
    from supervisor import worker_pool_lifecycle as lifecycle
    from supervisor import workers

    monkeypatch.setattr(workers, "DRIVE_ROOT", tmp_path)
    monkeypatch.setattr(workers, "REPO_DIR", tmp_path)
    monkeypatch.setattr(workers, "WORKERS", {})
    monkeypatch.setattr(workers, "load_state", lambda: {"current_sha": "abc123", "owner_chat_id": 0})
    monkeypatch.setattr(workers, "_record_worker_pids", lambda: None)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(workers=workers, lifecycle=lifecycle, root=tmp_path)


def _wait_for(predicate, timeout: float = 5.0):
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.01)
    return predicate()


# ---------------------------------------------------------------------------
# (a) spawn and (b) respawn both install booting slots through the one seam
# ---------------------------------------------------------------------------

def test_spawn_installs_every_slot_booting_and_hands_the_set_to_the_readiness_seam(pool, monkeypatch):
    workers = pool.workers
    fake_ctx = MagicMock()
    fake_ctx.Queue.return_value = object()
    created = []

    def make_process(*_args, **_kwargs):
        proc = _FakeProc(1000 + len(created))
        created.append(proc)
        return proc

    fake_ctx.Process.side_effect = make_process
    monkeypatch.setattr(workers, "_CTX", fake_ctx)
    monkeypatch.setattr(workers.mp, "get_context", lambda _method: fake_ctx)
    monkeypatch.setattr(workers, "_EVENT_Q", object())
    monkeypatch.setattr(workers, "_EVENT_Q_GENERATION", "test-generation")
    monkeypatch.setattr(workers, "reap_orphaned_workers", lambda: 0)
    handed = []
    monkeypatch.setattr(
        workers, "_verify_worker_sha_after_spawn",
        lambda slots, cursor, *rest: handed.append((dict(slots), cursor, rest)),
    )

    workers.spawn_workers(2)

    assert sorted(workers.WORKERS) == [0, 1]
    assert all(slot.reaping for slot in workers.WORKERS.values()), "a fresh slot must not be assignable"
    assert _wait_for(lambda: bool(handed))
    slots, cursor, rest = handed[0]
    assert slots == {0: workers.WORKERS[0], 1: workers.WORKERS[1]}
    assert isinstance(cursor, tuple) and len(cursor) == 3
    attempt, spawned_at = rest
    assert attempt == 1 and 0 < spawned_at <= time.time()  # the window counts from the child's birth


def test_respawn_installs_the_fresh_slot_booting_through_the_same_seam_with_its_attempt(pool, monkeypatch):
    workers = pool.workers
    old = workers.Worker(wid=3, proc=_FakeProc(111, alive=False), in_q=MagicMock(), busy_task_id=None, reaping=True)
    workers.WORKERS[3] = old
    fake_ctx = MagicMock()
    fake_ctx.Queue.return_value = MagicMock()
    fake_ctx.Process.side_effect = lambda *_a, **_k: _FakeProc(222)
    monkeypatch.setattr(workers, "_get_ctx", lambda: fake_ctx)
    monkeypatch.setattr(workers, "get_event_q", lambda: object())
    handed = []
    monkeypatch.setattr(
        workers, "_verify_worker_sha_after_spawn",
        lambda slots, cursor, *rest: handed.append((dict(slots), cursor, rest)),
    )

    assert workers.respawn_worker(3) is True
    fresh = workers.WORKERS[3]
    assert fresh is not old and fresh.reaping is True and fresh.busy_task_id is None
    assert _wait_for(lambda: len(handed) == 1)
    assert handed[0][0] == {3: fresh} and handed[0][2][0] == 1 and 0 < handed[0][2][1] <= time.time()

    assert workers.respawn_worker(3, ready_attempt=2) is True
    assert _wait_for(lambda: len(handed) == 2)
    assert handed[1][0] == {3: workers.WORKERS[3]} and handed[1][2][0] == 2


# ---------------------------------------------------------------------------
# The seam itself, run synchronously on fake slots
# ---------------------------------------------------------------------------

@pytest.fixture
def seam(pool, monkeypatch):
    """A short window, a recorded teardown and a recorded respawn around the real seam."""
    lifecycle = pool.lifecycle
    import ouroboros.platform_layer as platform_layer

    killed, respawned, sent = [], [], []
    monkeypatch.setattr(lifecycle, "WORKER_READY_WINDOW_SEC", 0.3)
    monkeypatch.setattr(platform_layer, "kill_pid_tree", lambda pid, **_k: killed.append(pid))
    monkeypatch.setattr(lifecycle, "respawn_worker", lambda wid, **kw: respawned.append((wid, kw)))
    monkeypatch.setattr(pool.workers, "send_with_budget", lambda chat_id, text, **_k: sent.append((chat_id, text)))
    events = pool.root / "logs" / "events.jsonl"
    append_jsonl(events, {"type": "noise"})
    cursor = lifecycle.events_log_cursor()
    return SimpleNamespace(
        run=lifecycle._verify_worker_sha_after_spawn, cursor=cursor, events=events,
        supervisor=pool.root / "logs" / "supervisor.jsonl",
        killed=killed, respawned=respawned, sent=sent,
    )


def _booting_slot(pool, wid: int, pid: int, **proc_kwargs):
    slot = pool.workers.Worker(wid=wid, proc=_FakeProc(pid, **proc_kwargs), in_q=MagicMock(), busy_task_id=None, reaping=True)
    pool.workers.WORKERS[wid] = slot
    return slot


def test_the_slot_opens_only_on_its_own_worker_ready_row_and_verifies_the_sha(pool, seam):
    slot = _booting_slot(pool, 0, 5001)
    append_jsonl(seam.events, {"type": "worker_ready", "worker_id": 0, "pid": 5001, "git_sha": "abc123"})

    seam.run({0: slot}, seam.cursor, 1)

    assert slot.reaping is False, "the child confirmed ready: the slot is assignable"
    assert seam.killed == [] and seam.respawned == []
    verify = _rows(seam.supervisor, "worker_sha_verify")
    assert len(verify) == 1
    assert verify[0]["ok"] is True and verify[0]["worker_id"] == 0 and verify[0]["worker_pid"] == 5001
    assert verify[0]["slot_opened"] is True and verify[0]["attempt"] == 1
    assert _rows(seam.supervisor, "worker_ready_timeout") == []


def test_a_foreign_pid_row_does_not_open_the_slot(pool, seam):
    slot = _booting_slot(pool, 0, 5001)
    append_jsonl(seam.events, {"type": "worker_ready", "worker_id": 0, "pid": 4999, "git_sha": "abc123"})

    seam.run({0: slot}, seam.cursor, 1)

    assert slot.reaping is True
    assert seam.killed == [5001] and seam.respawned == [(0, {"ready_attempt": 2})]


def test_sha_mismatch_on_the_ready_row_opens_the_slot_and_tells_the_owner(pool, seam, monkeypatch):
    monkeypatch.setattr(pool.workers, "load_state", lambda: {"current_sha": "abc123", "owner_chat_id": 7})
    slot = _booting_slot(pool, 0, 5001)
    append_jsonl(seam.events, {"type": "worker_ready", "worker_id": 0, "pid": 5001, "git_sha": "other"})

    seam.run({0: slot}, seam.cursor, 1)

    assert slot.reaping is False
    assert _rows(seam.supervisor, "worker_sha_verify")[0]["ok"] is False
    assert seam.sent and "SHA mismatch" in seam.sent[0][1]


def test_no_worker_ready_inside_the_window_tears_down_replaces_and_types_the_row(pool, seam):
    slot = _booting_slot(pool, 2, 5002)

    started = time.time()
    seam.run({2: slot}, seam.cursor, 1)

    assert time.time() - started >= 0.3
    assert seam.killed == [5002], "the process tree is torn down the way the pool already does"
    assert slot.proc.joined is True
    assert seam.respawned == [(2, {"ready_attempt": 2})], "replaced through the same respawn path"
    assert slot.reaping is True, "the torn-down slot stays owned until respawn swaps it"
    rows = _rows(seam.supervisor, "worker_ready_timeout")
    assert len(rows) == 1
    row = rows[0]
    assert row["worker_id"] == 2 and row["pid"] == 5002 and row["reason"] == "no_worker_ready"
    assert row["attempt"] == 1 and row["action"] == "respawn" and row["window_sec"] == 0.3
    assert row["waited_sec"] >= 0.3 and row["max_attempts"] >= 2
    assert _rows(seam.supervisor, "worker_sha_verify") == []


def test_the_window_and_the_reported_wait_count_from_the_spawn_instant(pool, seam):
    slot = _booting_slot(pool, 4, 5040)

    started = time.time()
    seam.run({4: slot}, seam.cursor, 1, started - 10.0)

    assert time.time() - started < 0.25, "a window already spent at hand-off does not wait again"
    row = _rows(seam.supervisor, "worker_ready_timeout")[0]
    assert row["waited_sec"] >= 10.0 and seam.killed == [5040]


def test_the_replacement_loop_is_bounded_then_parked_and_reported(pool, seam, monkeypatch):
    monkeypatch.setattr(pool.lifecycle, "WORKER_READY_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(pool.workers, "load_state", lambda: {"current_sha": "abc123", "owner_chat_id": 7})
    first = _booting_slot(pool, 1, 5011)
    seam.run({1: first}, seam.cursor, 1)
    assert seam.respawned == [(1, {"ready_attempt": 2})]

    last = _booting_slot(pool, 1, 5012)
    seam.run({1: last}, seam.cursor, 2)

    assert seam.respawned == [(1, {"ready_attempt": 2})], "no respawn at the bound"
    assert seam.killed == [5011, 5012]
    assert last.reaping is True, "parked: never assignable, never respawned again"
    rows = _rows(seam.supervisor, "worker_ready_timeout")
    assert [row["action"] for row in rows] == ["respawn", "parked"]
    assert rows[1]["attempt"] == 2 and rows[1]["max_attempts"] == 2
    assert seam.sent and seam.sent[0][0] == 7 and "slot 1" in seam.sent[0][1] and "parked" in seam.sent[0][1]


def test_a_child_that_died_during_boot_is_released_to_the_crash_detector(pool, seam):
    slot = _booting_slot(pool, 0, 5003, alive=False, exitcode=1)

    seam.run({0: slot}, seam.cursor, 1)

    assert slot.reaping is False, "released: the crash detector owns process death"
    assert seam.killed == [] and seam.respawned == []
    rows = _rows(seam.supervisor, "worker_ready_released")
    assert len(rows) == 1 and rows[0]["reason"] == "died_during_boot"
    assert rows[0]["worker_id"] == 0 and rows[0]["pid"] == 5003 and rows[0]["exitcode"] == 1


def test_a_slot_the_pool_already_replaced_is_left_alone(pool, seam):
    stale = pool.workers.Worker(wid=0, proc=_FakeProc(5004), in_q=MagicMock(), busy_task_id=None, reaping=True)
    _booting_slot(pool, 0, 5005)  # the pool restarted under the seam: slot 0 is a different object now

    seam.run({0: stale}, seam.cursor, 1)

    assert seam.killed == [] and seam.respawned == []
    assert _rows(seam.supervisor, "worker_ready_timeout") == []


def test_the_first_event_reader_keeps_its_contract_over_the_list_reader(pool, seam):
    lifecycle = pool.lifecycle
    append_jsonl(seam.events, {"type": "worker_ready", "pid": 1})
    append_jsonl(seam.events, {"type": "worker_boot", "pid": 2})
    append_jsonl(seam.events, {"type": "worker_ready", "pid": 3})
    assert [row["pid"] for row in lifecycle._worker_events_since(seam.cursor, "worker_ready")] == [1, 3]
    assert lifecycle._first_worker_event_since(seam.cursor, "worker_ready")["pid"] == 1
    assert lifecycle._first_worker_event_since(seam.cursor)["pid"] == 2
    assert lifecycle._first_worker_event_since(seam.cursor, "absent") is None


# ---------------------------------------------------------------------------
# (e) the assignment path: a booting slot is skipped, a ready one is unchanged
# ---------------------------------------------------------------------------

def test_assignment_skips_a_booting_slot_and_dispatches_to_an_open_one_unchanged(tmp_path, monkeypatch):
    from supervisor import queue, state, workers

    state.init(tmp_path, total_budget_limit=10.0)
    queue.init(tmp_path)
    monkeypatch.setattr(workers, "DRIVE_ROOT", tmp_path)
    monkeypatch.setattr(queue, "DRIVE_ROOT", tmp_path)
    workers.PENDING[:] = []
    workers.RUNNING.clear()
    workers.WORKERS.clear()
    queue.BUDGET_ROOT_FENCES.clear()
    queue.init_queue_refs(workers.PENDING, workers.RUNNING, workers.QUEUE_SEQ_COUNTER_REF)
    monkeypatch.setattr(workers, "load_state", lambda: {"owner_chat_id": 0})
    monkeypatch.setattr(state, "budget_remaining", lambda _st, **_kwargs: 10.0)

    sent = {0: [], 1: []}
    booting = SimpleNamespace(wid=0, busy_task_id=None, reaping=True, in_q=SimpleNamespace(put=lambda t: sent[0].append(dict(t))))
    ready = SimpleNamespace(wid=1, busy_task_id=None, reaping=False, in_q=SimpleNamespace(put=lambda t: sent[1].append(dict(t))))
    workers.WORKERS[0] = booting
    workers.WORKERS[1] = ready
    workers.PENDING.append({"id": "first", "type": "task", "chat_id": 0, "priority": 1})

    workers.assign_tasks()

    assert [t["id"] for t in sent[1]] == ["first"] and sent[0] == []
    assert workers.RUNNING["first"]["worker_id"] == 1 and ready.busy_task_id == "first"
    assert booting.busy_task_id is None

    # The seam opened the slot: it is dispatched to like any other.
    booting.reaping = False
    workers.PENDING.append({"id": "second", "type": "task", "chat_id": 0, "priority": 1})
    workers.assign_tasks()
    assert [t["id"] for t in sent[0]] == ["second"]
    assert workers.RUNNING["second"]["worker_id"] == 0
    workers.PENDING[:] = []
    workers.RUNNING.clear()
    workers.WORKERS.clear()
