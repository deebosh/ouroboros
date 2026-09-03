"""Ingress addressing for headless/API tasks (owner decisions 2A/3A, sprint CLI display).

A task admitted through ``POST /api/tasks`` used to default to chat 0 — the
hidden partition no browser surface reads — even when the caller scoped it to a
project the owner already has open. These pin the one ingress rule: an explicit
id (0 included) is the caller's, a REGISTERED project homes the run into its
thread, and everything else stays hidden.
"""

from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from ouroboros.contracts.chat_id_policy import HIDDEN_CHAT_ID, project_chat_id
from ouroboros.gateway.tasks import api_tasks_create
from ouroboros.projects_registry import create_project
from supervisor.log_addressing import ProjectThreadConflict, ingress_chat_id


def test_an_explicit_chat_id_is_the_callers_but_may_not_leave_the_project_room(tmp_path):
    row = create_project(tmp_path, "proj_reg", name="Registered")
    # 0 is a real destination, not "missing": asking for the hidden partition is
    # asking to run quietly, and a project-scoped run may do that.
    assert ingress_chat_id(0, tmp_path, "proj_reg") == HIDDEN_CHAT_ID
    assert ingress_chat_id(row["chat_id"], tmp_path, "proj_reg") == row["chat_id"]
    # Any OTHER conversation is refused: that is the shape that puts a card in
    # Main whose project room holds none of its work — the reported defect.
    with pytest.raises(ProjectThreadConflict):
        ingress_chat_id(1, tmp_path, "proj_reg")
    # Without a project there is no room to contradict.
    assert ingress_chat_id("7", tmp_path, "") == 7
    assert ingress_chat_id(1, tmp_path, "proj_never_registered") == 1


def test_registered_project_homes_the_run_into_its_thread(tmp_path):
    row = create_project(tmp_path, "proj_reg", name="Registered")
    assert ingress_chat_id(None, tmp_path, "proj_reg") == row["chat_id"] == project_chat_id("proj_reg")


def test_unregistered_or_derived_project_stays_in_the_hidden_partition(tmp_path):
    # A workspace-derived proj_<hash> has no registry row and therefore no
    # thread; stamping its derived chat id would leak the run into Main, where
    # an unknown positive id is accepted as ordinary conversation.
    assert ingress_chat_id(None, tmp_path, "proj_deadbeef1234") == HIDDEN_CHAT_ID
    assert ingress_chat_id(None, tmp_path, "") == HIDDEN_CHAT_ID


def test_malformed_explicit_chat_id_still_raises_for_the_caller_s_400(tmp_path):
    with pytest.raises((TypeError, ValueError)):
        ingress_chat_id("not-an-int", tmp_path, "")


def _app(data, repo):
    app = Starlette(routes=[Route("/api/tasks", endpoint=api_tasks_create, methods=["POST"])])
    app.state.drive_root = data
    app.state.repo_dir = repo
    return app


@pytest.fixture()
def admission(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    data = tmp_path / "data"
    (data / "memory").mkdir(parents=True)
    (data / "memory" / "identity.md").write_text("seed identity", encoding="utf-8")
    from supervisor import workers

    monkeypatch.setattr(workers, "WORKERS", {0: SimpleNamespace()})
    monkeypatch.setattr(workers, "_WORKER_POOL_DISABLED_REASON", "")
    captured = []
    monkeypatch.setattr("supervisor.queue.enqueue_task", lambda task: captured.append(dict(task)) or task)
    monkeypatch.setattr("supervisor.queue.persist_queue_snapshot", lambda reason="": True)
    monkeypatch.setattr("ouroboros.workspace_admission.bootstrap_process_path", lambda: [])
    return data, repo, captured


def test_api_task_for_a_registered_project_is_admitted_into_the_project_thread(admission):
    data, repo, captured = admission
    row = create_project(data, "proj_room", name="Room")
    response = TestClient(_app(data, repo)).post(
        "/api/tasks", json={"description": "audit the report", "project_id": "proj_room"},
    )
    assert response.status_code == 200, response.text
    assert captured and captured[0]["chat_id"] == row["chat_id"]
    # The durable scheduled record carries the address too, so a later reader
    # (detail endpoint, reaper, crash insurance) never has to re-derive it.
    from ouroboros.task_results import load_task_result

    stored = load_task_result(data, response.json()["task_id"]) or {}
    assert stored.get("chat_id") == row["chat_id"]


def test_api_task_without_a_project_stays_hidden_and_explicit_zero_is_honoured(admission):
    data, repo, captured = admission
    client = TestClient(_app(data, repo))
    assert client.post("/api/tasks", json={"description": "script run"}).status_code == 200
    assert captured[-1]["chat_id"] == HIDDEN_CHAT_ID
    row = create_project(data, "proj_room2", name="Room")
    assert row["chat_id"] > 0
    assert client.post(
        "/api/tasks", json={"description": "bench run", "project_id": "proj_room2", "chat_id": 0},
    ).status_code == 200
    assert captured[-1]["chat_id"] == HIDDEN_CHAT_ID
    # …but not into a different conversation.
    conflict = client.post(
        "/api/tasks", json={"description": "x", "project_id": "proj_room2", "chat_id": 1},
    )
    assert conflict.status_code == 400
    assert "project thread" in conflict.text


def test_api_task_with_a_malformed_chat_id_is_still_a_typed_400(admission):
    data, repo, _ = admission
    response = TestClient(_app(data, repo)).post(
        "/api/tasks", json={"description": "x", "chat_id": "nope"},
    )
    assert response.status_code == 400
    assert "integers" in response.text


def test_derived_project_id_is_scoped_but_never_announced_in_main(tmp_path, monkeypatch):
    """Owner decision 3A: no room, no Main row.

    A ``--workspace`` run derives ``proj_<hash>``; that id is project-SCOPED for
    lease and memory, but it has no registry row and therefore no thread. The
    Main completion line offers "Open Project", so announcing a derived id would
    hand the owner a door into an empty duplicate of Main.

    The assertion is the GATE, not the delivery: the outbox needs a live
    supervisor event bus, which is process-global and not this test's subject.
    """
    from ouroboros import project_dialogue
    from ouroboros.projects_registry import create_project, task_presentation_snapshot

    enqueued = []
    monkeypatch.setattr(
        "supervisor.terminal_delivery.enqueue_terminal_delivery",
        lambda drive_root, event: enqueued.append(event) or True,
    )

    task = {"id": "t1", "project_id": "proj_deadbeef1234", "description": "run"}
    result = {"status": "completed", "project_id": "proj_deadbeef1234", "result": "done"}
    assert task_presentation_snapshot(tmp_path, "t1", task=task, result=result)["project_routable"] is False
    assert project_dialogue.enqueue_project_completion_summary(
        tmp_path, {}, "t1", task, result, {"status": "completed"},
    ) is False
    assert enqueued == [], "a derived project id must not reach the Main outbox at all"

    row = create_project(tmp_path, "proj_real", name="Real")
    assert row["chat_id"] > 0
    task2 = {"id": "t2", "project_id": "proj_real", "description": "run"}
    result2 = {"status": "completed", "project_id": "proj_real", "result": "done"}
    assert task_presentation_snapshot(tmp_path, "t2", task=task2, result=result2)["project_routable"] is True
    assert project_dialogue.enqueue_project_completion_summary(
        tmp_path, {}, "t2", task2, result2, {"status": "completed"},
    ) is True
    # Main gets exactly one row, and it points at the room that exists.
    assert len(enqueued) == 1
    assert enqueued[0]["chat_id"] == 1
    assert enqueued[0]["system_type"] == "project_completion_summary"


def test_an_explicitly_hidden_run_is_not_announced_into_a_room_it_left(tmp_path, monkeypatch):
    """The room exists, but this run was addressed away from it.

    A caller may scope a task to an active project AND ask for the hidden
    partition. Announcing "Open the Project" then points at a room holding none
    of that task's rows — the exact artifact this sprint removed — so Main stays
    silent while the project itself keeps its lifecycle rows for tasks that do
    live there.
    """
    from ouroboros import project_dialogue
    from ouroboros.projects_registry import begin_project_deletion, create_project

    enqueued = []
    monkeypatch.setattr(
        "supervisor.terminal_delivery.enqueue_terminal_delivery",
        lambda drive_root, event: enqueued.append(event) or True,
    )
    row = create_project(tmp_path, "proj_room3", name="Room")
    common = {"id": "hidden1", "project_id": "proj_room3", "description": "bench run"}
    result = {"status": "completed", "project_id": "proj_room3", "result": "done"}

    hidden = {**common, "chat_id": HIDDEN_CHAT_ID}
    assert project_dialogue.enqueue_project_completion_summary(
        tmp_path, {}, "hidden1", hidden, result, {"status": "completed"},
    ) is False
    assert enqueued == []

    homed = {**common, "chat_id": row["chat_id"]}
    assert project_dialogue.enqueue_project_completion_summary(
        tmp_path, {}, "hidden1", homed, result, {"status": "completed"},
    ) is True
    assert len(enqueued) == 1

    # A project on its way out has no room to open either.
    begin_project_deletion(tmp_path, "proj_room3")
    assert project_dialogue.enqueue_project_completion_summary(
        tmp_path, {}, "hidden2", homed, result, {"status": "completed"},
    ) is False
    assert len(enqueued) == 1
