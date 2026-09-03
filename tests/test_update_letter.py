"""The update letter: range material, the accounted LIGHT call, storage, and projection."""

from __future__ import annotations

import json
import subprocess

import pytest

from ouroboros import update_letter as ul


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def _capture_for(repo):
    def capture(cmd):
        proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

    return capture


_HEADER = "# Demo\n\n## Version History\n\n| Version | Date | Description |\n|---|---|---|\n"


def _write_readme(repo, rows):
    (repo / "README.md").write_text(_HEADER + "".join(f"| {v} | {d} | {t} |\n" for v, d, t in rows), encoding="utf-8")


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def history_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _write_readme(repo, [("1.0.0", "2026-01-01", "first")])
    (repo / "VERSION").write_text("1.0.0\n")
    base = _commit(repo, "release: 1.0.0")
    _write_readme(repo, [("1.1.0", "2026-01-02", "second with an escaped \\| pipe"), ("1.0.0", "2026-01-01", "first")])
    (repo / "VERSION").write_text("1.1.0\n")
    c1 = _commit(repo, "release: 1.1.0\n\nBody of the second release.")
    # Two rows in one commit plus one malformed row (two cells only).
    (repo / "README.md").write_text(
        _HEADER
        + "| 1.2.0 | 2026-01-03 | third |\n"
        + "| 1.1.1 | 2026-01-03 | third-fix |\n"
        + "| 1.3.0 | only two cells |\n"
        + "| 1.1.0 | 2026-01-02 | second with an escaped \\| pipe |\n"
        + "| 1.0.0 | 2026-01-01 | first |\n",
        encoding="utf-8",
    )
    (repo / "VERSION").write_text("1.2.0\n")
    c2 = _commit(repo, "release: 1.2.0")
    # Roll-off: the 1.0.0 row leaves the table (capped), a new row arrives, a tag lands.
    _write_readme(repo, [
        ("1.4.0", "2026-01-04", "fourth"),
        ("1.2.0", "2026-01-03", "third"),
        ("1.1.1", "2026-01-03", "third-fix"),
        ("1.1.0", "2026-01-02", "second with an escaped \\| pipe"),
    ])
    (repo / "VERSION").write_text("1.4.0\n")
    c3 = _commit(repo, "release: 1.4.0")
    _git(repo, "tag", "-a", "v1.4.0", "-m", "v1.4.0")
    (repo / "notes.txt").write_text("tail\n")
    c4 = _commit(repo, "tail work\n\nDetails of the untagged tail.")
    return {"repo": repo, "base": base, "c1": c1, "c2": c2, "c3": c3, "c4": c4}


# ---------------------------------------------------------------------------
# material
# ---------------------------------------------------------------------------

def test_material_recovers_rows_from_commit_diffs_and_lists_first_parent_commits(history_repo):
    repo = history_repo["repo"]
    material = ul.collect_range_material(history_repo["base"], history_repo["c4"], git=_capture_for(repo))

    assert [row["version"] for row in material["releases"]] == ["1.4.0", "1.2.0", "1.1.1", "1.1.0"]
    assert material["omitted_rows"] == 1  # the two-cell 1.3.0 row is disclosed, not silently dropped
    by_version = {row["version"]: row for row in material["releases"]}
    assert by_version["1.1.0"]["text"] == "second with an escaped | pipe"
    assert by_version["1.4.0"]["commit"] == history_repo["c3"]
    assert [c["sha"] for c in material["commits"]] == [
        history_repo["c4"], history_repo["c3"], history_repo["c2"], history_repo["c1"],
    ]
    assert material["commits"][0]["body"] == "Details of the untagged tail."
    assert material["omitted_commits"] == 0
    assert material["versions"] == {"base": "1.0.0", "target": "1.4.0"}
    assert set(material) == {"base_sha", "target_sha", "commits", "omitted_commits", "releases",
                             "omitted_rows", "omitted_older_rows", "versions"}


def test_material_caps_commits_but_keeps_every_release_row(history_repo):
    material = ul.collect_range_material(
        history_repo["base"], history_repo["c4"], git=_capture_for(history_repo["repo"]), max_commits=2,
    )
    assert len(material["commits"]) == 2 and material["omitted_commits"] == 2
    assert len(material["releases"]) == 4
    capped = ul.collect_range_material(
        history_repo["base"], history_repo["c4"], git=_capture_for(history_repo["repo"]), max_rows=3,
    )
    assert [row["version"] for row in capped["releases"]] == ["1.4.0", "1.2.0", "1.1.1"]
    assert capped["omitted_older_rows"] == 1 and "1 older release row(s) omitted" in ul.material_text(capped)
    rendered = ul.material_text(material)
    assert "2 older commit(s) omitted" in rendered and "1 malformed history row(s) omitted" in rendered


def test_material_reworded_row_is_first_wins_newest_first(history_repo):
    repo = history_repo["repo"]
    _write_readme(repo, [("1.4.0", "2026-01-04", "fourth, reworded"), ("1.2.0", "2026-01-03", "third")])
    c5 = _commit(repo, "reword 1.4.0 row")
    material = ul.collect_range_material(history_repo["base"], c5, git=_capture_for(repo))
    assert material["releases"][0]["text"] == "fourth, reworded"
    assert [row["version"] for row in material["releases"]] == ["1.4.0", "1.2.0", "1.1.1", "1.1.0"]


def test_material_empty_range_and_non_ancestor_base(history_repo):
    repo = history_repo["repo"]
    empty = ul.collect_range_material(history_repo["c4"], history_repo["c4"], git=_capture_for(repo))
    assert empty["commits"] == [] and empty["releases"] == []
    reverse = ul.collect_range_material(history_repo["c4"], history_repo["base"], git=_capture_for(repo))
    assert reverse["commits"] == [] and reverse["releases"] == []


def test_split_row_keeps_three_cells_only():
    assert ul._split_row("+| 1.2.3 | 2026-01-01 | a \\| b |") == ("1.2.3", "2026-01-01", "a | b")
    assert ul._split_row("+| 1.2.3 | only two |") is None


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------

def _status(**over):
    base = {
        "check_ok": True, "available": True, "current_sha": "a" * 40, "latest_sha": "b" * 40,
        "update_channel": "stable", "target_ref": "managed/main", "behind": 3, "ahead": 0,
        "checked_at": "2026-09-03T18:00:00+00:00",
    }
    base.update(over)
    return base


def _material():
    return {
        "commits": [{"sha": "b" * 40, "date": "2026-09-03", "subject": "s", "body": ""}],
        "releases": [{"version": "6.114.0", "date": "2026-09-01", "text": "row", "commit": "b" * 40}],
        "omitted_commits": 0, "omitted_rows": 0,
        "versions": {"base": "6.113.5", "target": "6.114.0"},
    }


@pytest.fixture
def letter_env(tmp_path, monkeypatch):
    drive = tmp_path / "data"
    repo = tmp_path / "repo"
    (drive / "state").mkdir(parents=True)
    (drive / "memory").mkdir()
    repo.mkdir()
    monkeypatch.setenv("OUROBOROS_MODEL_LIGHT", "test/light")
    monkeypatch.delenv("USE_LOCAL_LIGHT", raising=False)
    monkeypatch.setattr("ouroboros.provider_models.model_has_credentials", lambda model: True)
    calls = []

    def fake_context(env, memory, task):
        calls.append(("context", task))
        return [{"role": "system", "content": "identity"}, {"role": "user", "content": task["text"]}]

    monkeypatch.setattr(ul, "_context_messages", fake_context)
    return {"drive": drive, "repo": repo, "calls": calls}


def test_write_letter_ready_record_carries_attempt_and_versions(letter_env, monkeypatch):
    seen = {}

    def fake_chat(client, *, drive_root, **kwargs):
        seen.update(kwargs)
        return {"content": "  One short paragraph.  "}, {"ledger_attempt_ids": ["att-1", "att-2"]}

    monkeypatch.setattr(ul, "_chat", fake_chat)
    record = ul.write_letter(_status(), _material(), drive_root=letter_env["drive"])

    assert record["state"] == "ready" and record["text"] == "One short paragraph."
    assert record["attempt_id"] == "att-2" and record["attempt_ids"] == ["att-1", "att-2"]
    assert record["model"] == "test/light"
    assert record["key"]["base_sha"] == "a" * 40 and record["checked_head_sha"] == "a" * 40
    assert record["target_version"] == "6.114.0" and record["error_kind"] == ""
    assert seen["model"] == "test/light" and seen["max_tokens"] == ul.UPDATE_LETTER_MAX_TOKENS
    assert seen["reasoning_effort"] == "low" and seen["tools"] is None
    task = letter_env["calls"][0][1]
    assert task["id"] == ul.SYSTEM_TASK_ID and task["model"] == "test/light"
    assert "[UPDATE LETTER REQUEST]" in task["text"] and "6.114.0" in task["text"]
    assert "ONE short paragraph" in seen["messages"][-1]["content"]


def test_write_letter_without_light_credentials_fails_typed_and_never_calls(letter_env, monkeypatch):
    monkeypatch.setattr("ouroboros.provider_models.model_has_credentials", lambda model: False)
    monkeypatch.setattr(ul, "_chat", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call")))
    record = ul.write_letter(_status(), _material(), drive_root=letter_env["drive"])
    assert record["state"] == "failed" and record["error_kind"] == "no_credentials"
    assert "test/light" in record["error_text"] and record["text"] == ""


@pytest.mark.parametrize("exc, kind", [
    (TimeoutError("slow"), "timeout"),
    (RuntimeError("boom"), "provider_unavailable"),
])
def test_write_letter_failures_are_typed(letter_env, monkeypatch, exc, kind):
    def fake_chat(client, *, drive_root, **kwargs):
        raise exc

    monkeypatch.setattr(ul, "_chat", fake_chat)
    record = ul.write_letter(_status(), _material(), drive_root=letter_env["drive"])
    assert record["state"] == "failed" and record["error_kind"] == kind
    assert "boom" in record["error_text"] or "slow" in record["error_text"]


def test_write_letter_budget_exhausted_is_typed(letter_env, monkeypatch):
    from ouroboros.usage_accounting import BudgetExceeded

    def fake_chat(client, *, drive_root, **kwargs):
        raise BudgetExceeded("global budget exhausted")

    monkeypatch.setattr(ul, "_chat", fake_chat)
    record = ul.write_letter(_status(), _material(), drive_root=letter_env["drive"])
    assert record["error_kind"] == "budget_exhausted"


def test_write_letter_empty_response_is_typed(letter_env, monkeypatch):
    monkeypatch.setattr(ul, "_chat", lambda client, *, drive_root, **kw: ({"content": "   "}, {}))
    record = ul.write_letter(_status(), _material(), drive_root=letter_env["drive"])
    assert record["state"] == "failed" and record["error_kind"] == "empty_response"


# ---------------------------------------------------------------------------
# refresh seam
# ---------------------------------------------------------------------------

def test_refresh_writes_nothing_without_a_successful_check(tmp_path, monkeypatch):
    drive = tmp_path / "data"
    monkeypatch.setattr(ul, "collect_range_material", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no material")))
    assert ul.refresh_after_check(_status(check_ok=False), drive_root=drive) is None
    assert ul.refresh_after_check(_status(check_ok=None), drive_root=drive) is None
    assert not ul.record_path(drive).exists()


def test_refresh_records_the_checked_head_even_without_an_update(tmp_path, monkeypatch):
    drive = tmp_path / "data"
    monkeypatch.setattr(ul, "collect_range_material", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no material")))
    record = ul.refresh_after_check(_status(available=False, latest_sha=""), drive_root=drive)
    assert record["state"] == "none" and record["checked_head_sha"] == "a" * 40
    assert ul.project_letter(record, head_sha="a" * 40, latest_sha="") is None
    fact = ul.official_update_projection("a" * 40, drive_root=drive, state={"managed_update_cache": {
        "latest_sha": "", "available": False, "behind": 0, "ahead": 2, "checked_at": "t0"}})
    assert fact["status"] == "up_to_date" and fact["letter"] is None


def test_refresh_writes_record_and_keeps_last_good_on_failure(tmp_path, monkeypatch):
    drive = tmp_path / "data"
    monkeypatch.setattr(ul, "collect_range_material", lambda base, target, **k: _material())
    ready = {"schema": 1, "key": ul._key_from_status(_status()), "checked_head_sha": "a" * 40,
             "state": "ready", "text": "first letter", "author_version": "6.113.5",
             "target_version": "6.114.0", "model": "m", "written_at": "t1", "attempt_id": "att-1",
             "error_kind": "", "error_text": "", "last_good": None}
    monkeypatch.setattr(ul, "write_letter", lambda status, material, **k: dict(ready))
    record = ul.refresh_after_check(_status(), drive_root=drive)
    assert record["state"] == "ready" and ul.read_record(drive)["text"] == "first letter"

    failed = dict(ready, state="failed", text="", error_kind="timeout", written_at="t2")
    monkeypatch.setattr(ul, "write_letter", lambda status, material, **k: dict(failed))
    record = ul.refresh_after_check(_status(), drive_root=drive)
    assert record["state"] == "failed" and record["last_good"]["text"] == "first letter"
    stored = json.loads(ul.record_path(drive).read_text())
    assert stored["last_good"]["text"] == "first letter"

    # An applied update (available=False) leaves the letter untouched.
    kept = ul.refresh_after_check(_status(available=False), drive_root=drive)
    assert kept["last_good"]["text"] == "first letter"

    # A newer target whose letter fails still carries the older good letter (D-KEEP).
    moved = dict(failed, key=ul._key_from_status(_status(latest_sha="c" * 40)), target_version="6.115.0")
    monkeypatch.setattr(ul, "write_letter", lambda status, material, **k: dict(moved))
    record = ul.refresh_after_check(_status(latest_sha="c" * 40), drive_root=drive)
    assert record["last_good"]["text"] == "first letter"
    view = ul.project_letter(record, head_sha="a" * 40, latest_sha="c" * 40)
    assert view["text"] == "first letter" and view["target_version"] == "6.114.0"
    assert view["state"] == "failed" and view["has_last_good"] is True


def test_refresh_is_single_flight(tmp_path, monkeypatch):
    drive = tmp_path / "data"
    monkeypatch.setattr(ul, "collect_range_material", lambda *a, **k: (_ for _ in ()).throw(AssertionError("busy")))
    assert ul._REFRESH_LOCK.acquire(blocking=False)
    try:
        assert ul.refresh_after_check(_status(), drive_root=drive) is None
    finally:
        ul._REFRESH_LOCK.release()


def test_refresh_never_raises(tmp_path, monkeypatch):
    drive = tmp_path / "data"
    monkeypatch.setattr(ul, "collect_range_material", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("git down")))
    assert ul.refresh_after_check(_status(), drive_root=drive) is None


# ---------------------------------------------------------------------------
# projection
# ---------------------------------------------------------------------------

def _record(**over):
    record = {"schema": 1, "key": {"base_sha": "a" * 40, "target_sha": "b" * 40, "update_channel": "stable",
                                   "target_ref": "managed/main"},
              "checked_head_sha": "a" * 40, "state": "ready", "text": "letter", "author_version": "6.113.5",
              "target_version": "6.114.0", "model": "m", "written_at": "t", "attempt_id": "att",
              "error_kind": "", "error_text": "", "last_good": None}
    record.update(over)
    return record


@pytest.mark.parametrize("head, latest, relation", [
    ("a" * 40, "b" * 40, "pending"),
    ("b" * 40, "b" * 40, "applied"),
    ("b" * 40, "c" * 40, "applied"),
    ("a" * 40, "c" * 40, "superseded"),
    ("d" * 40, "b" * 40, "other"),
])
def test_project_letter_relations(head, latest, relation):
    view = ul.project_letter(_record(), head_sha=head, latest_sha=latest)
    assert view["relation"] == relation and view["state"] == "ready" and view["text"] == "letter"


def test_project_letter_failed_with_last_good_shows_previous_text_and_provenance():
    record = _record(state="failed", text="", error_kind="timeout", error_text="slow", written_at="t-fail",
                     author_version="6.114.0", last_good=_record(text="older letter", written_at="t-good"))
    view = ul.project_letter(record, head_sha="a" * 40, latest_sha="b" * 40)
    assert view["state"] == "failed" and view["text"] == "older letter" and view["has_last_good"] is True
    assert view["error_kind"] == "timeout"
    assert view["written_at"] == "t-good" and view["author_version"] == "6.113.5"
    assert ul.project_letter(None, head_sha="a" * 40, latest_sha="") is None


def test_official_update_projection_states(tmp_path):
    drive = tmp_path / "data"
    (drive / "state").mkdir(parents=True)
    head = "a" * 40
    assert ul.official_update_projection(head, drive_root=drive, state={})["status"] == "unchecked"
    cache = {"managed_update_cache": {"latest_sha": "b" * 40, "available": True, "behind": 3, "ahead": 0,
                                      "checked_at": "t0", "update_channel": "stable"}}
    # A cache from a check this code never recorded (no record at all): never invented.
    assert ul.official_update_projection(head, drive_root=drive, state=cache)["status"] == "moved_since_check"
    ul.record_path(drive).write_text(json.dumps(_record()))
    fact = ul.official_update_projection(head, drive_root=drive, state=cache)
    assert fact["status"] == "update_available" and fact["letter"]["relation"] == "pending"
    assert fact["target"] == {"version": "6.114.0", "sha": "b" * 40} and fact["status_as_of"] == "t0"
    assert fact["running"]["sha"] == head and fact["behind"] == 3
    applied = ul.official_update_projection("b" * 40, drive_root=drive, state=cache)
    assert applied["status"] == "up_to_date" and applied["letter"]["relation"] == "applied"
    moved = ul.official_update_projection("e" * 40, drive_root=drive, state=cache)
    assert moved["status"] == "moved_since_check" and moved["letter"]["relation"] == "other"
    # A newer official target than the letter's: the target version is not the letter's.
    newer = dict(cache); newer["managed_update_cache"] = dict(cache["managed_update_cache"], latest_sha="c" * 40)
    superseded = ul.official_update_projection(head, drive_root=drive, state=newer)
    assert superseded["target"] == {"version": "", "sha": "c" * 40}
    assert superseded["letter"]["relation"] == "superseded"


def test_official_update_projection_never_raises(tmp_path):
    fact = ul.official_update_projection("a" * 40, drive_root=tmp_path / "missing", state={"managed_update_cache": "bad"})
    assert fact["status"] == "unchecked"


class _Projection:
    def __init__(self, fits, label):
        self.fits_known_window = fits
        self.label = label

    def system_message(self):
        return {"role": "system", "content": self.label}


class _Plan:
    def __init__(self, preferred, max_fits, low_fits):
        self.initial_mode = preferred
        self.max_projection = _Projection(max_fits, "max")
        self.low_projection = _Projection(low_fits, "low")

    def projection(self, mode):
        return self.low_projection if mode == "low" else self.max_projection

    def messages_for(self, mode):
        return [self.projection(mode).system_message(), {"role": "user", "content": "req"}]


@pytest.mark.parametrize("preferred, max_fits, low_fits, expected", [
    ("max", True, True, "max"),      # the owner's mode when it fits
    ("max", None, None, "max"),      # unknown window: keep the owner's mode
    ("max", False, True, "low"),     # a small light slot: the projection that fits
    ("max", False, False, "max"),    # nothing fits: send the owner's mode and let it fail typed
    ("low", True, True, "low"),
])
def test_context_messages_use_the_ordinary_plan_and_the_fitting_projection(monkeypatch, preferred, max_fits, low_fits, expected):
    import ouroboros.context as context

    captured = {}

    def fake_plan(env, memory, task, *a, **k):
        captured["task"] = task
        return _Plan(preferred, max_fits, low_fits)

    monkeypatch.setattr(context, "build_context_fit_plan", fake_plan)
    task = {"id": ul.SYSTEM_TASK_ID, "type": "update_letter", "model": "test/light",
            "use_local_model": False, "text": "req", "metadata": {}}
    messages = ul._context_messages(object(), object(), task)
    assert captured["task"] is task and messages[0]["content"] == expected and messages[-1]["content"] == "req"


def test_write_letter_local_light_route_skips_the_credential_gate(letter_env, monkeypatch):
    monkeypatch.setenv("USE_LOCAL_LIGHT", "true")
    monkeypatch.setattr("ouroboros.provider_models.model_has_credentials",
                        lambda model: (_ for _ in ()).throw(AssertionError("local route must not ask")))
    seen = {}

    def fake_chat(client, *, drive_root, **kwargs):
        seen.update(kwargs)
        return {"content": "local paragraph"}, {}

    monkeypatch.setattr(ul, "_chat", fake_chat)
    record = ul.write_letter(_status(), _material(), drive_root=letter_env["drive"])
    assert record["state"] == "ready" and seen["use_local"] is True
    assert letter_env["calls"][0][1]["use_local_model"] is True


def test_write_letter_context_overflow_is_typed(letter_env, monkeypatch):
    from ouroboros.llm import LocalContextTooLargeError

    def fake_chat(client, *, drive_root, **kwargs):
        raise LocalContextTooLargeError("too big")

    monkeypatch.setattr(ul, "_chat", fake_chat)
    record = ul.write_letter(_status(), _material(), drive_root=letter_env["drive"])
    assert record["state"] == "failed" and record["error_kind"] == "context_overflow"


def test_boot_check_writes_the_letter_before_the_readiness_broadcast(monkeypatch):
    import server
    import supervisor.git_ops as git_ops
    import supervisor.update_merge as update_merge

    calls = []
    monkeypatch.setattr(server, "_wait_for_supervisor_update_finalize", lambda: False)
    monkeypatch.setattr(update_merge, "finalize_managed_update_on_boot",
                        lambda supervisor_ready: {"finalized": False, "rolled_back": False})
    monkeypatch.setattr(git_ops, "compute_managed_update_status", lambda fetch: _status())
    monkeypatch.setattr(ul, "refresh_after_check", lambda status, **k: calls.append(("letter", status["latest_sha"])))
    monkeypatch.setattr(server, "broadcast_ws_sync", lambda payload: calls.append((payload["type"], "")))

    server._boot_managed_update_tasks()

    assert calls == [("letter", "b" * 40), ("update_status_ready", "")]
