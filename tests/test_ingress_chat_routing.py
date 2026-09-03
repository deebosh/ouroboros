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


def test_a_registered_project_run_has_exactly_one_destination(tmp_path):
    row = create_project(tmp_path, "proj_reg", name="Registered")
    assert ingress_chat_id(None, tmp_path, "proj_reg") == row["chat_id"]
    assert ingress_chat_id(row["chat_id"], tmp_path, "proj_reg") == row["chat_id"]
    # Anywhere else is refused — the hidden partition included. A run addressed
    # away from its room is the shape that puts a card in Main whose project
    # holds none of its work, which is the defect this sprint exists to remove.
    for elsewhere in (1, HIDDEN_CHAT_ID, 999):
        with pytest.raises(ProjectThreadConflict):
            ingress_chat_id(elsewhere, tmp_path, "proj_reg")


def test_without_a_room_the_explicit_address_is_still_the_callers(tmp_path):
    """A disclosed residual, not an oversight.

    The task API has always accepted a chat id, no first-party client sends one,
    and nothing about an unscoped task creates the empty-room artifact. Narrowing
    it would remove a contract capability with no demonstrated harm, so it stays
    and is disclosed instead.
    """
    assert ingress_chat_id("7", tmp_path, "") == 7
    assert ingress_chat_id(1, tmp_path, "proj_never_registered") == 1
    assert ingress_chat_id(None, tmp_path, "proj_never_registered") == HIDDEN_CHAT_ID


def test_a_chat_id_that_is_not_a_whole_number_is_refused(tmp_path):
    # int(True) is 1 and int(1.9) is 1; neither is a chat the caller named.
    for bad in (True, False, 1.9, "nope"):
        with pytest.raises((TypeError, ValueError)):
            ingress_chat_id(bad, tmp_path, "")


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
    # …but a project-scoped run cannot be addressed anywhere but its room.
    for elsewhere in (0, 1):
        conflict = client.post(
            "/api/tasks",
            json={"description": "x", "project_id": "proj_room2", "chat_id": elsewhere},
        )
        assert conflict.status_code == 400, elsewhere
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


def test_a_run_that_becomes_a_project_mid_flight_still_gets_its_one_main_row(tmp_path, monkeypatch):
    """Main owes exactly two lifecycle rows, so it must not owe only one.

    A run admitted with no project is hidden. If the agent then calls into
    ensure_project_scope, the project is created, the task is BOUND to it and
    Main is told the project started — so Main must also be told when it
    finishes. Gating that row on the admission address instead of the room would
    leave the start row hanging with no completion, which is worse than the
    artifact it was meant to prevent.
    """
    from ouroboros import project_dialogue
    from ouroboros.projects_registry import begin_project_deletion, create_project

    enqueued = []
    monkeypatch.setattr(
        "supervisor.terminal_delivery.enqueue_terminal_delivery",
        lambda drive_root, event: enqueued.append(event) or True,
    )
    row = create_project(tmp_path, "proj_midflight", name="Mid flight")
    # Admitted hidden, re-homed mid-run: the durable record still carries 0.
    task = {"id": "m1", "project_id": "proj_midflight", "description": "run", "chat_id": row["chat_id"]}
    result = {"status": "completed", "project_id": "proj_midflight", "chat_id": 0, "result": "done"}
    assert project_dialogue.enqueue_project_completion_summary(
        tmp_path, {}, "m1", task, result, {"status": "completed"},
    ) is True
    assert len(enqueued) == 1 and enqueued[0]["chat_id"] == 1

    # A project on its way out has no room to open, so Main stays silent.
    begin_project_deletion(tmp_path, "proj_midflight")
    assert project_dialogue.enqueue_project_completion_summary(
        tmp_path, {}, "m2", task, result, {"status": "completed"},
    ) is False
    assert len(enqueued) == 1
