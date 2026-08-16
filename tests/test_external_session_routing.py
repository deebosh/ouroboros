"""Session isolation by external identity (v6.102.0).

Each distinct (source, external chat id) pair from an external transport
(Telegram bridge, or any other skill using inject_chat) threads into its own
auto-provisioned Project instead of collapsing into the single shared main
chat. See ``ouroboros.projects_registry.resolve_external_session_chat_id``.
"""

from __future__ import annotations

from ouroboros.contracts.chat_id_policy import is_a2a_chat_id, project_chat_id
from ouroboros.projects_registry import (
    PROJECT_ACTIVE,
    begin_project_deletion,
    complete_project_deletion,
    get_project,
    list_projects,
    projects_summary,
    resolve_external_session_chat_id,
)


def test_falsy_chat_id_passes_through_unchanged(tmp_path):
    """The 0 sentinel means "unidentified sender" elsewhere in the pipeline
    (enqueue_local_message) — must never be translated into a real project."""
    data = tmp_path / "data"
    assert resolve_external_session_chat_id(data, source="skill:telegram-bridge", external_chat_id=0) == 0


def test_empty_source_passes_through_unchanged(tmp_path):
    data = tmp_path / "data"
    assert resolve_external_session_chat_id(data, source="", external_chat_id=42) == 42


def test_first_contact_provisions_a_project(tmp_path):
    data = tmp_path / "data"
    resolved = resolve_external_session_chat_id(
        data, source="skill:telegram-bridge", external_chat_id=344067595, sender_label="Alex"
    )
    assert resolved != 344067595
    assert resolved > 0
    projects = list_projects(data)
    assert len(projects) == 1
    assert projects[0]["chat_id"] == resolved
    assert projects[0]["origin"] == "external_session"
    assert projects[0]["lifecycle"] == PROJECT_ACTIVE
    assert "Alex" in projects[0]["name"]


def test_same_source_and_chat_id_is_idempotent(tmp_path):
    """Repeated messages from the SAME Telegram chat must land in the SAME
    session every time, not mint a new project per message."""
    data = tmp_path / "data"
    first = resolve_external_session_chat_id(data, source="skill:telegram-bridge", external_chat_id=111)
    second = resolve_external_session_chat_id(data, source="skill:telegram-bridge", external_chat_id=111)
    third = resolve_external_session_chat_id(data, source="skill:telegram-bridge", external_chat_id=111)
    assert first == second == third
    assert len(list_projects(data)) == 1


def test_different_chat_ids_get_isolated_sessions(tmp_path):
    """The actual feature: two different Telegram chats must NOT collapse
    into one session."""
    data = tmp_path / "data"
    chat_a = resolve_external_session_chat_id(data, source="skill:telegram-bridge", external_chat_id=111)
    chat_b = resolve_external_session_chat_id(data, source="skill:telegram-bridge", external_chat_id=222)
    assert chat_a != chat_b
    assert len(list_projects(data)) == 2


def test_different_senders_through_same_chat_id_space_but_different_source_are_isolated(tmp_path):
    """Two different skills reporting the SAME numeric chat_id must not be
    merged — the key is (source, external_chat_id), not chat_id alone."""
    data = tmp_path / "data"
    a = resolve_external_session_chat_id(data, source="skill:telegram-bridge", external_chat_id=555)
    b = resolve_external_session_chat_id(data, source="skill:some-other-bridge", external_chat_id=555)
    assert a != b
    assert len(list_projects(data)) == 2


def test_negative_telegram_group_id_never_becomes_the_project_chat_id(tmp_path):
    """The core safety property: Telegram group/supergroup ids are negative,
    and this system reserves every negative chat_id for synthetic internal
    A2A traffic. A real human group conversation must never be assigned a
    negative internal chat_id or it would be silently hidden from history."""
    data = tmp_path / "data"
    group_id = -1003942901945
    resolved = resolve_external_session_chat_id(data, source="skill:telegram-bridge", external_chat_id=group_id)
    assert resolved > 0
    assert not is_a2a_chat_id(resolved)
    # And it must match the project's own normally-derived chat_id scheme.
    projects = list_projects(data)
    assert projects[0]["chat_id"] == project_chat_id(projects[0]["id"])


def test_deleted_session_falls_back_to_raw_id_never_resurrected(tmp_path):
    """If the owner deletes an auto-session, a later message from the same
    chat must not resurrect it under the same project id, and must not be
    lost either — it falls back to the shared main chat."""
    data = tmp_path / "data"
    resolved = resolve_external_session_chat_id(data, source="skill:telegram-bridge", external_chat_id=333)
    projects = list_projects(data)
    project_id = projects[0]["id"]
    begin_project_deletion(data, project_id)
    complete_project_deletion(data, project_id)
    assert get_project(data, project_id) is None  # active-only lookup no longer sees it

    fallback = resolve_external_session_chat_id(data, source="skill:telegram-bridge", external_chat_id=333)
    assert fallback == 333
    assert len(list_projects(data)) == 0


def test_sender_label_used_when_present(tmp_path):
    data = tmp_path / "data"
    resolve_external_session_chat_id(
        data, source="skill:telegram-bridge", external_chat_id=1, sender_label="Owner (Telegram)"
    )
    assert "Owner (Telegram)" in list_projects(data)[0]["name"]


def test_no_sender_label_still_names_the_session(tmp_path):
    data = tmp_path / "data"
    resolve_external_session_chat_id(data, source="skill:telegram-bridge", external_chat_id=2)
    name = list_projects(data)[0]["name"]
    assert name and "telegram-bridge" in name


def test_skill_prefix_stripped_from_display_name(tmp_path):
    data = tmp_path / "data"
    resolve_external_session_chat_id(data, source="skill:telegram-bridge", external_chat_id=3)
    name = list_projects(data)[0]["name"]
    assert not name.startswith("skill:")


def test_projects_summary_exposes_origin_for_frontend_marker(tmp_path):
    """The web sidebar (app.js paintProjectsNav) reads project.origin to mark
    an auto-provisioned session with a lightweight-conversation icon, distinct
    from an owner-created folder-backed Project."""
    data = tmp_path / "data"
    resolve_external_session_chat_id(data, source="skill:telegram-bridge", external_chat_id=9)
    summary = projects_summary(data)
    assert len(summary) == 1
    assert summary[0]["origin"] == "external_session"
