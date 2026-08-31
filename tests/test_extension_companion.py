import hmac
import json
import logging
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

import ouroboros.gateway.host_service as host_service

from ouroboros.contracts.plugin_api import ExtensionRegistrationError
from ouroboros.extension_companion import (
    CompanionDescriptor,
    CompanionSupervisor,
    init_server_process_pid,
)
from ouroboros.extension_loader import PluginAPIImpl, _PluginAPIConfig
from ouroboros.extension_plugin_api import ExtensionStaleRecoveryError
import ouroboros.extension_loader as extension_loader
import ouroboros.extension_plugin_api as extension_plugin_api
from ouroboros import extension_health
from ouroboros.skill_loader import SkillReviewState, find_skill, save_enabled, save_review_state


def test_companion_supervisor_starts_and_stops_process(tmp_path: pathlib.Path) -> None:
    init_server_process_pid()
    supervisor = CompanionSupervisor(tmp_path)
    descriptor = CompanionDescriptor(
        skill_name="demo",
        name="sleepy",
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        env={},
    )

    assert supervisor.start(descriptor)
    snapshot = supervisor.snapshot()
    assert "demo:sleepy" in snapshot
    pid = int(snapshot["demo:sleepy"]["pid"])
    assert pid > 0

    supervisor.stop("demo", "sleepy", timeout_sec=1)
    assert supervisor.snapshot() == {}


def test_panic_kill_all_clears_runtime_table(tmp_path: pathlib.Path) -> None:
    init_server_process_pid()
    supervisor = CompanionSupervisor(tmp_path)
    descriptor = CompanionDescriptor(
        skill_name="demo",
        name="panic",
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        env={},
    )

    assert supervisor.start(descriptor)
    assert supervisor.snapshot()
    supervisor.panic_kill_all()
    assert supervisor.snapshot() == {}


def test_exhausted_companion_remains_in_durable_runtime_health(tmp_path: pathlib.Path) -> None:
    init_server_process_pid()
    drive_root = tmp_path / "drive"
    repo_root = tmp_path / "skills"
    skill_dir = repo_root / "connector"
    drive_root.mkdir()
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: connector\ndescription: connector\nversion: 0.1.0\n"
        "type: extension\nentry: plugin.py\npermissions: []\n---\nbody\n",
        encoding="utf-8",
    )
    (skill_dir / "plugin.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    loaded = find_skill(drive_root, "connector", repo_path=str(repo_root))
    assert loaded is not None
    save_enabled(drive_root, loaded.name, True)
    save_review_state(
        drive_root,
        loaded.name,
        SkillReviewState(status="pass", content_hash=loaded.content_hash),
    )
    loaded = find_skill(drive_root, loaded.name, repo_path=str(repo_root))
    assert loaded is not None
    try:
        assert extension_loader.load_extension(loaded, lambda: {}, drive_root=drive_root) is None
        supervisor = CompanionSupervisor(drive_root)
        descriptor = CompanionDescriptor(
            skill_name=loaded.name,
            name="bridge",
            command=[sys.executable, "-c", "raise SystemExit(7)"],
            cwd=tmp_path,
            env={},
            max_restarts=0,
        )

        assert supervisor.start(descriptor)
        deadline = time.monotonic() + 3
        while supervisor.snapshot() and time.monotonic() < deadline:
            time.sleep(0.01)

        assert supervisor.snapshot() == {}
        health = extension_health.read_extension_health(drive_root, loaded.name) or {}
        observed = health.get("last_observed") or {}
        assert observed["status"] == extension_health.BROKEN
        assert observed["reason"] == "companion_restart_exhausted:bridge"
        state = extension_loader.runtime_state_for_loaded_skill(
            loaded, drive_root, skills=[loaded],
        )
        assert state["live_loaded"] is True
        assert state["companion_failed"] is True
        assert state["reason"] == "companion_restart_exhausted:bridge"
        assert "exhausting its restart budget" in state["load_error"]
        assert extension_health.status_for_runtime_state(state) == extension_health.BROKEN
    finally:
        extension_loader.unload_extension(loaded.name)


def test_plugin_api_companion_registration_uses_reviewed_manifest_descriptor(tmp_path: pathlib.Path) -> None:
    init_server_process_pid(999999)
    state_dir = tmp_path / "state"
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    api = PluginAPIImpl(_PluginAPIConfig(
        skill_name="demo",
        permissions=["companion_process"],
        env_allowlist=[],
        state_dir=state_dir,
        settings_reader=lambda: {},
        companion_processes=[{
            "name": "daemon",
            "command": ["python3", "scripts/daemon.py"],
            "runtime": "python3",
        }],
        skill_dir=skill_dir,
    ))

    api.register_companion_process("daemon")

    api._close_registration()
    init_server_process_pid()


def test_plugin_api_companion_uses_staged_skill_root_as_cwd(tmp_path: pathlib.Path, monkeypatch) -> None:
    init_server_process_pid()
    staged_skill = tmp_path / "import" / "skill"
    (staged_skill / "scripts").mkdir(parents=True)
    (staged_skill / "scripts" / "daemon.py").write_text("print('ok')\n", encoding="utf-8")
    captured = {}

    class FakeSupervisor:
        def start(self, descriptor):
            captured["descriptor"] = descriptor
            return True

        def stop(self, skill_name, name):
            return True

    # The companion supervisor is read by PluginAPIImpl (its owner) at register
    # time and by the loader's spawn/ensure paths; patch both readers.
    monkeypatch.setattr(extension_plugin_api, "get_global_supervisor", lambda: FakeSupervisor())
    monkeypatch.setattr(extension_loader, "get_global_supervisor", lambda: FakeSupervisor())
    api = PluginAPIImpl(_PluginAPIConfig(
        skill_name="demo",
        permissions=["companion_process"],
        env_allowlist=[],
        state_dir=tmp_path / "state",
        settings_reader=lambda: {},
        companion_processes=[{
            "name": "daemon",
            "command": ["python3", "scripts/daemon.py"],
            "runtime": "python3",
            "env": {"HOST_SERVICE_URL": "https://evil.example", "HOST_SERVICE_TOKEN": "evil"},
        }],
        skill_dir=tmp_path / "mutable",
        runtime_skill_dir=staged_skill,
    ))

    api.register_companion_process("daemon")
    # ABI-9: the spawn is a deferred side effect; publication starts it.
    api._publish_registrations()

    descriptor = captured["descriptor"]
    assert descriptor.cwd == staged_skill
    assert (descriptor.cwd / "scripts" / "daemon.py").is_file()
    assert descriptor.env["HOST_SERVICE_URL"].startswith("http://127.0.0.1:")
    assert descriptor.env["HOST_SERVICE_TOKEN"] != "evil"


_COMPANION_FRONTMATTER = (
    "companion_processes:\n"
    "  - name: daemon\n"
    "    runtime: python3\n"
    "    command: [\"python3\", \"scripts/daemon.py\"]\n"
)


class _RecordingSupervisor:
    """Fake supervisor: records spawns/stops; reports no running companions."""

    def __init__(self) -> None:
        self.started: list = []
        self.stopped: list = []

    def start(self, descriptor):
        self.started.append(descriptor)
        return True

    def snapshot(self):
        return {}

    def stop(self, *args, **kwargs):
        self.stopped.append(args)

    def stop_skill(self, skill_name):
        self.stopped.append((skill_name,))


def _reviewed_companion_skill(tmp_path: pathlib.Path, name: str = "compskill"):
    """A reviewed+enabled companion extension the recovery tests can load."""
    repo_root = tmp_path / "skills"
    drive_root = tmp_path / "drive"
    drive_root.mkdir(exist_ok=True)
    skill_dir = repo_root / name
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "daemon.py").write_text("print('ok')\n", encoding="utf-8")
    (skill_dir / "plugin.py").write_text(
        "def register(api):\n    api.register_companion_process('daemon')\n", encoding="utf-8"
    )
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: companion skill\n"
        "version: 0.1.0\n"
        "type: extension\n"
        "entry: plugin.py\n"
        "permissions: [companion_process]\n"
        f"{_COMPANION_FRONTMATTER}"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    loaded = find_skill(drive_root, name, repo_path=str(repo_root))
    assert loaded is not None
    save_enabled(drive_root, loaded.name, True)
    save_review_state(
        drive_root, loaded.name,
        SkillReviewState(status="pass", content_hash=loaded.content_hash),
    )
    loaded = find_skill(drive_root, loaded.name, repo_path=str(repo_root))
    assert loaded is not None
    return loaded, repo_root, drive_root


def _patch_supervisor(monkeypatch) -> _RecordingSupervisor:
    # The companion supervisor is read by PluginAPIImpl (its owner) at register
    # time and by the loader's spawn/ensure/unload paths; patch both readers.
    fake = _RecordingSupervisor()
    monkeypatch.setattr(extension_plugin_api, "get_global_supervisor", lambda: fake)
    monkeypatch.setattr(extension_loader, "get_global_supervisor", lambda: fake)
    return fake


def test_publish_out_of_process_registration_host_spawns_declared_name(tmp_path: pathlib.Path, monkeypatch) -> None:
    """Out-of-process catalog -> host spawns the manifest-declared companion; an
    undeclared cataloged name is rejected at the host trust boundary, and an
    ambiguous publication form (neither initial load nor generation-bound
    recovery) is a typed refusal (Ф3.1 fix-round-4)."""
    init_server_process_pid()
    loaded, _repo_root, drive_root = _reviewed_companion_skill(tmp_path)
    fake = _patch_supervisor(monkeypatch)
    # Production-shape per-skill state dir (fix-round-5): the same directory
    # extension_loader resolves via skill_state_dir, where auth_token.json lives.
    state_dir = extension_loader.skill_state_dir(drive_root, loaded.name)
    try:
        with pytest.raises(ExtensionRegistrationError, match="exactly one of"):
            extension_loader._publish_out_of_process_registration(
                loaded,
                catalog={"companions": ["daemon"]},
                state_dir=state_dir,
                settings_reader=lambda: {},
                granted_keys=[],
                dependency_site_dirs_enabled=False,
            )
        with pytest.raises(ExtensionRegistrationError, match="escaped manifest"):
            extension_loader._publish_out_of_process_registration(
                loaded,
                catalog={"companions": ["evil"]},
                state_dir=state_dir,
                settings_reader=lambda: {},
                granted_keys=[],
                dependency_site_dirs_enabled=False,
                current_hash=loaded.content_hash,
            )
        assert fake.started == []
        assert not (state_dir / host_service.AUTH_TOKEN_FILENAME).exists(), (
            "a refused publication must not have minted a companion token"
        )

        extension_loader._publish_out_of_process_registration(
            loaded,
            catalog={"companions": ["daemon"]},
            state_dir=state_dir,
            settings_reader=lambda: {},
            granted_keys=[],
            dependency_site_dirs_enabled=False,
            current_hash=loaded.content_hash,
        )
        assert len(fake.started) == 1
        assert fake.started[0].name == "daemon"
        # Initial load legitimately mints the token — at publication, into the
        # spawned descriptor's env.
        token_payload = json.loads(
            (state_dir / host_service.AUTH_TOKEN_FILENAME).read_text(encoding="utf-8")
        )
        assert fake.started[0].env["HOST_SERVICE_TOKEN"] == token_payload["token"]
    finally:
        extension_loader.unload_extension("compskill")
        init_server_process_pid()


def test_recovery_publication_requires_a_pre_existing_live_bundle(tmp_path: pathlib.Path, monkeypatch) -> None:
    """Ф3.1 fix-round-4 REPLACEMENT (disclosed): the clause this test replaces
    pinned the OPPOSITE contract — the recovery-form publication helper
    accepted no pre-existing live bundle and spawned the companion anyway,
    which pinned the resurrection bug itself (a stale recovery re-created an
    empty companion-only bundle after disable/unload). The recovery form now
    REQUIRES a still-live bundle at the observed generation: with no live
    bundle it refuses with zero effects — filesystem included (fix-round-5:
    no auth_token.json is minted) — and creates nothing."""
    init_server_process_pid()
    loaded, _repo_root, drive_root = _reviewed_companion_skill(tmp_path)
    fake = _patch_supervisor(monkeypatch)
    state_dir = extension_loader.skill_state_dir(drive_root, loaded.name)
    try:
        with pytest.raises(ExtensionStaleRecoveryError):
            extension_loader._publish_out_of_process_registration(
                loaded,
                catalog={"companions": ["daemon"]},
                state_dir=state_dir,
                settings_reader=lambda: {},
                granted_keys=[],
                dependency_site_dirs_enabled=False,
                expected_generation="0" * 32,
            )
        assert fake.started == []
        with extension_loader._lock:
            assert "compskill" not in extension_loader._extensions
        assert "compskill" not in extension_loader.snapshot()["extensions"]
        assert not (state_dir / host_service.AUTH_TOKEN_FILENAME).exists(), (
            "stale recovery minted auth_token.json before its refusal"
        )
    finally:
        extension_loader.unload_extension("compskill")
        init_server_process_pid()


def test_unload_completing_between_snapshot_and_publication_refuses_recovery(
    tmp_path: pathlib.Path, monkeypatch,
) -> None:
    """Ф3.1 fix-round-4 pin (ABI-9а, TOCTOU): an unload that COMPLETES between
    recovery's liveness/bundle snapshot and its publication turns the
    publication into a typed zero-effect refusal — registries and supervisor
    stay empty and NO new bundle is created. (Before the fix the stale
    recovery re-created an empty companion-only bundle and started its
    companion, resurrecting the extension after disable/unload.)"""
    init_server_process_pid()
    loaded, repo_root, drive_root = _reviewed_companion_skill(tmp_path)
    fake = _patch_supervisor(monkeypatch)
    try:
        assert extension_loader.load_extension(
            loaded, lambda: {}, drive_root=drive_root, skills=[loaded],
        ) is None
        assert len(fake.started) == 1  # the initial publication's spawn
        # Fix-round-5: the token file the initial publication minted must
        # survive the stale refusal byte-for-byte (unload keeps it; only a
        # post-fence publication may touch it).
        token_path = (
            extension_loader.skill_state_dir(drive_root, loaded.name)
            / host_service.AUTH_TOKEN_FILENAME
        )
        token_bytes = token_path.read_bytes()

        real_state_dir = extension_loader.skill_state_dir

        def _unload_wins_the_race(drive_root_arg, skill_name_arg):
            # Deterministic interleave: the concurrent unload COMPLETES after
            # recovery snapshotted liveness/bundle and before it publishes.
            extension_loader.unload_extension(skill_name_arg)
            return real_state_dir(drive_root_arg, skill_name_arg)

        monkeypatch.setattr(extension_loader, "skill_state_dir", _unload_wins_the_race)
        result = extension_loader.ensure_companions_running(
            loaded.name, drive_root, lambda: {}, repo_path=str(repo_root),
        )

        assert result["action"] == "stale_recovery_refused"
        assert result["started"] == []
        assert "recovery publication refused" in result["reason"]
        assert len(fake.started) == 1, "stale recovery must not start a companion"
        with extension_loader._lock:
            assert loaded.name not in extension_loader._extensions
            assert not any(
                entry.get("skill") == loaded.name
                for entry in extension_loader._tools.values()
            )
        assert loaded.name not in extension_loader.snapshot()["extensions"]
        assert token_path.read_bytes() == token_bytes, (
            "stale recovery touched auth_token.json"
        )
    finally:
        extension_loader.unload_extension(loaded.name)
        init_server_process_pid()


def test_recovery_publication_refuses_on_generation_mismatch_without_effects(
    tmp_path: pathlib.Path, monkeypatch,
) -> None:
    """Ф3.1 fix-round-4 pin (ABI-9б), rebuilt through the REAL recovery entry
    in fix-round-5: an unload/reload that COMPLETES between recovery's
    generation snapshot and its publication leaves a live bundle under a NEW
    generation — the recovery publication naming the old generation is a
    typed zero-effect refusal: the reloaded bundle keeps its generation, no
    companion is started, and auth_token.json is byte-untouched."""
    init_server_process_pid()
    loaded, repo_root, drive_root = _reviewed_companion_skill(tmp_path)
    fake = _patch_supervisor(monkeypatch)
    try:
        assert extension_loader.load_extension(
            loaded, lambda: {}, drive_root=drive_root, skills=[loaded],
        ) is None
        first_generation = extension_loader.extension_generation_digest(loaded.name)
        assert first_generation
        token_path = (
            extension_loader.skill_state_dir(drive_root, loaded.name)
            / host_service.AUTH_TOKEN_FILENAME
        )
        token_bytes = token_path.read_bytes()

        real_state_dir = extension_loader.skill_state_dir

        def _reload_wins_the_race(drive_root_arg, skill_name_arg):
            # Deterministic interleave: the unload/reload COMPLETES after
            # recovery snapshotted the generation and before it publishes;
            # the bundle EXISTS again, under a fresh generation. Restore the
            # real resolver first — the reload itself resolves state dirs.
            monkeypatch.setattr(extension_loader, "skill_state_dir", real_state_dir)
            extension_loader.unload_extension(skill_name_arg)
            assert extension_loader.load_extension(
                loaded, lambda: {}, drive_root=drive_root_arg, skills=[loaded],
            ) is None
            return real_state_dir(drive_root_arg, skill_name_arg)

        monkeypatch.setattr(extension_loader, "skill_state_dir", _reload_wins_the_race)
        result = extension_loader.ensure_companions_running(
            loaded.name, drive_root, lambda: {}, repo_path=str(repo_root),
        )

        assert result["action"] == "stale_recovery_refused"
        assert result["started"] == []
        second_generation = extension_loader.extension_generation_digest(loaded.name)
        assert second_generation and second_generation != first_generation
        with extension_loader._lock:
            assert loaded.name in extension_loader._extensions
        assert len(fake.started) == 2, (
            "only the initial load and the in-window reload may spawn"
        )
        assert token_path.read_bytes() == token_bytes
    finally:
        extension_loader.unload_extension(loaded.name)
        init_server_process_pid()


def test_stale_recovery_does_not_break_live_publication_authorization(
    tmp_path: pathlib.Path, monkeypatch,
) -> None:
    """Ф3.1 fix-round-5 pin (HIGH): a recovery holding a STALE payload snapshot
    (an old skill root that still exists with the pre-update content) loses
    the race to an unload/reload with CHANGED content; its typed refusal must
    leave auth_token.json byte-untouched. Pre-fix, the recovery minted the
    token DURING descriptor build — the stale root's content hash mismatched
    the G2-bound token file, so the mint ROTATED it before the generation
    fence refused the publication — and the live G2 companion, spawned with
    the current token in its env while the Host Service rereads the file on
    every request, was left permanently unauthorized."""
    init_server_process_pid()
    loaded_v1, repo_root, drive_root = _reviewed_companion_skill(tmp_path)
    fake = _patch_supervisor(monkeypatch)
    try:
        assert extension_loader.load_extension(
            loaded_v1, lambda: {}, drive_root=drive_root, skills=[loaded_v1],
        ) is None
        token_path = (
            extension_loader.skill_state_dir(drive_root, loaded_v1.name)
            / host_service.AUTH_TOKEN_FILENAME
        )
        # The update installs v2 under a NEW payload root; the G1 root
        # (repo_root) survives on disk with the v1 content — the recovery's
        # stale snapshot keeps pointing there.
        repo_root_v2 = tmp_path / "skills_v2"
        shutil.copytree(repo_root, repo_root_v2)
        (repo_root_v2 / loaded_v1.name / "scripts" / "daemon.py").write_text(
            "print('v2')\n", encoding="utf-8"
        )
        real_state_dir = extension_loader.skill_state_dir
        g2_token_bytes: dict = {}

        def _reload_v2_wins_the_race(drive_root_arg, skill_name_arg):
            # Deterministic interleave: the unload + v2 reload COMPLETES after
            # recovery snapshotted generation/payload and before it publishes;
            # the G2 token is bound to the NEW root's content hash while the
            # stale recovery still holds the v1 snapshot at the old root.
            monkeypatch.setattr(extension_loader, "skill_state_dir", real_state_dir)
            extension_loader.unload_extension(skill_name_arg)
            loaded_v2 = find_skill(
                drive_root, skill_name_arg, repo_path=str(repo_root_v2),
            )
            assert loaded_v2 is not None
            assert loaded_v2.content_hash != loaded_v1.content_hash
            save_review_state(
                drive_root, skill_name_arg,
                SkillReviewState(status="pass", content_hash=loaded_v2.content_hash),
            )
            loaded_v2 = find_skill(
                drive_root, skill_name_arg, repo_path=str(repo_root_v2),
            )
            assert extension_loader.load_extension(
                loaded_v2, lambda: {}, drive_root=drive_root_arg, skills=[loaded_v2],
            ) is None
            g2_token_bytes["value"] = token_path.read_bytes()
            return real_state_dir(drive_root_arg, skill_name_arg)

        monkeypatch.setattr(extension_loader, "skill_state_dir", _reload_v2_wins_the_race)
        result = extension_loader.ensure_companions_running(
            loaded_v1.name, drive_root, lambda: {}, repo_path=str(repo_root),
            selected_skill=loaded_v1,  # the stale v1 snapshot the recovery holds
        )

        assert result["action"] == "stale_recovery_refused"
        assert token_path.read_bytes() == g2_token_bytes["value"], (
            "stale recovery rotated auth_token.json; the live publication's "
            "companion is now permanently unauthorized"
        )
        # The live G2 companion's spawn-env token still matches the file the
        # Host Service rereads per request...
        g2_env_token = fake.started[-1].env["HOST_SERVICE_TOKEN"]
        file_token = json.loads(token_path.read_text(encoding="utf-8"))["token"]
        assert hmac.compare_digest(file_token, g2_env_token)
        # ...and end-to-end host authorization for G2 still succeeds.
        # (find_skill is patched only because HostServiceContext resolves the
        # default skills repo path, not this test's temporary v2 root.)
        monkeypatch.setattr(
            host_service, "find_skill",
            lambda data_dir, name: find_skill(
                data_dir, name, repo_path=str(repo_root_v2),
            ),
        )
        ctx = host_service.HostServiceContext(data_dir=drive_root)
        authed_name, payload = ctx.authenticate_token_payload(g2_env_token)
        assert authed_name == loaded_v1.name
        assert payload["token"] == g2_env_token
    finally:
        extension_loader.unload_extension(loaded_v1.name)
        init_server_process_pid()


def test_generation_bound_disposal_skips_a_newer_publication(
    tmp_path: pathlib.Path, monkeypatch, caplog,
) -> None:
    """Ф3.1 fix-round-4 pin (ABI-9в): disposal bound to an OLD generation is a
    disclosed no-op when the live publication is newer — recovery cleanup can
    never unload a publication it did not make. The same call naming the live
    generation unloads it."""
    init_server_process_pid()
    loaded, _repo_root, drive_root = _reviewed_companion_skill(tmp_path)
    _patch_supervisor(monkeypatch)
    try:
        assert extension_loader.load_extension(
            loaded, lambda: {}, drive_root=drive_root, skills=[loaded],
        ) is None
        live_generation = extension_loader.extension_generation_digest(loaded.name)
        assert live_generation

        with caplog.at_level(logging.WARNING, logger="ouroboros.extension_loader"):
            assert extension_loader.unload_extension(
                loaded.name, expected_generation="f" * 32,
            ) is False
        assert "generation-bound disposal skipped" in caplog.text
        with extension_loader._lock:
            assert loaded.name in extension_loader._extensions

        assert extension_loader.unload_extension(
            loaded.name, expected_generation=live_generation,
        ) is True
        with extension_loader._lock:
            assert loaded.name not in extension_loader._extensions
    finally:
        extension_loader.unload_extension(loaded.name)
        init_server_process_pid()


def test_windows_companion_start_does_not_request_console_process_group(tmp_path: pathlib.Path, monkeypatch) -> None:
    init_server_process_pid()
    captured = {}
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-should-not-leak")
    monkeypatch.setenv("windir", "C:\\Windows")
    monkeypatch.setenv("ComSpec", "C:\\Windows\\System32\\cmd.exe")

    class FakeProcess:
        pid = 12345
        stdout = None
        stderr = None

        def poll(self):
            return None

        def wait(self):
            return 0

    monkeypatch.setattr("ouroboros.extension_companion.IS_WINDOWS", True)
    monkeypatch.setattr("ouroboros.extension_companion.create_kill_on_close_job", lambda: object())
    monkeypatch.setattr("ouroboros.extension_companion.assign_pid_to_job", lambda _job, _pid: True)
    monkeypatch.setattr("ouroboros.extension_companion.close_job", lambda _job: None)

    def fake_popen(command, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr("ouroboros.extension_companion.subprocess.Popen", fake_popen)
    supervisor = CompanionSupervisor(tmp_path)
    descriptor = CompanionDescriptor(
        skill_name="skill",
        name="daemon",
        command=["python", "-c", "print('ok')"],
        cwd=tmp_path,
        env={},
    )

    assert supervisor.start(descriptor) is True
    assert captured.get("creationflags", 0) & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) == 0
    captured_upper = {key.upper(): value for key, value in captured["env"].items()}
    assert "PATH" in captured_upper
    assert captured_upper["WINDIR"] == "C:\\Windows"
    assert captured_upper["COMSPEC"].endswith("cmd.exe")
    assert "OPENROUTER_API_KEY" not in captured["env"]
