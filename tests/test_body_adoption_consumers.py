"""The existing owners around a body adoption: server restart/bootstrap/exit tail,
evolution, publication and the Runtime fact (#1539). Real Git throughout; the
supervisor context and the stop owners are the same doubles the neighbouring
restart suites use."""
from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

from ouroboros import body_adoption, body_candidate, body_switch
from tests.body_candidate_support import (
    candidate_commit, git, isolate, make_ctx, make_serving, restart_receipt, rich_commit,
)

EXITED = {"doomed": [4001], "dead": [4001], "unconfirmed": [], "cleanup_ok": True, "snapshot_ok": True}


@pytest.fixture
def scene(tmp_path, monkeypatch):
    serving = make_serving(tmp_path)
    data = isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OUROBOROS_RUNTIME_MODE", "pro")
    ctx = make_ctx(serving, data, "root-consumer")
    body_candidate.prepare(ctx)
    cand = rich_commit(ctx.repo_dir)
    body_candidate.record_reviewed_commit(ctx, cand)
    return SimpleNamespace(serving=serving, data=data, ctx=ctx, cand=cand,
                           old=git(serving, "rev-parse", "HEAD"), candidate=pathlib.Path(ctx.repo_dir))


class _GitOps:
    def __init__(self):
        self.safe_restart_calls, self.local_sync_calls = [], []

    def init(self, **_kwargs):
        return None

    def ensure_repo_present(self):
        return None

    def safe_restart(self, **kwargs):
        self.safe_restart_calls.append(kwargs)
        return True, "reset"

    def sync_runtime_dependencies(self, **kwargs):
        self.local_sync_calls.append(kwargs)
        return True, "ok"

    def import_test(self):
        return {"ok": True}


def _managed_server(monkeypatch, scene):
    import server

    monkeypatch.setattr(server, "REPO_DIR", scene.serving)
    monkeypatch.setattr(server, "DATA_DIR", scene.data)
    monkeypatch.setattr(server, "_LAUNCHER_MANAGED", True)
    monkeypatch.setattr(server, "_LAUNCHER_MANAGED_REPO_DIR", str(scene.serving.resolve()))
    monkeypatch.setattr(server, "setup_remote_if_configured", lambda *args: None)
    monkeypatch.setattr(server, "_has_active_evolution_transaction", lambda: False)
    monkeypatch.setattr(server, "_safe_restart_serialized", lambda fn, **kwargs: fn(**kwargs))
    return server


def _switch(scene, reason="adopt it"):
    """Authorize, bind, arm and let the REAL helper switch the tree in this process."""
    body_adoption.authorize(scene.ctx, scene.cand, reason=reason)
    restart_receipt(scene.data, scene.cand, reason)
    assert body_adoption.bind_restart(lambda **_kw: (True, "ok"), scene.data, reason)()[0]
    assert body_adoption.arm(scene.data, worker_exits=EXITED, live_children=[], owner_restart=False,
                             owned_stop={"state": "completed", "unconfirmed": []}) == "armed"
    handoff = body_adoption.read(scene.data)
    helper = str(body_adoption.helper_dir(scene.data))
    body_switch.record(helper, handoff, "switching", "test")
    root, rows = str(scene.serving), handoff["switch"]
    tracked = body_switch._tracks_filemode(root)
    body_switch._converge(root, rows, "new", body_switch._states(root, rows, tracked), tracked)
    body_switch._set_index(root, rows, "new", body_switch._index_states(root, rows))
    git(scene.serving, "update-ref", "refs/heads/ouroboros", scene.cand, scene.old)
    body_switch.record(helper, handoff, "switched", "test")
    # The fresh process the helper hands the tree to attests what it imports (the hook's
    # ``switched`` branch); here that process is this one.
    body_switch._attest_loaded(root, body_adoption.read(scene.data))


def test_managed_boot_right_after_a_switch_does_not_reset_or_clean_the_checkout(scene, monkeypatch):
    server = _managed_server(monkeypatch, scene)
    ordinary = _GitOps()
    assert server._bootstrap_supervisor_repo({}, ordinary)[0] is True
    assert [call["unsynced_policy"] for call in ordinary.safe_restart_calls] == ["rescue_and_reset"]

    (scene.serving / "owner-notes.txt").write_text("kept through the switch\n")
    _switch(scene)
    adoption_boot = _GitOps()

    ok, message = server._bootstrap_supervisor_repo({}, adoption_boot)

    assert ok is True and "local-dev" in message
    assert adoption_boot.safe_restart_calls == []  # no reset --hard / clean -fd on the tree that just landed
    assert adoption_boot.local_sync_calls == [{"reason": "bootstrap_local_dev"}]  # dependencies for the NEW tree
    assert (scene.serving / "owner-notes.txt").read_text() == "kept through the switch\n"

    assert body_adoption.finalize_on_boot(scene.data, scene.serving, supervisor_ready=True)["outcome"] == "adopted"
    later = _GitOps()
    server._bootstrap_supervisor_repo({}, later)
    assert [call["unsynced_policy"] for call in later.safe_restart_calls] == ["rescue_and_reset"]  # unchanged since


def test_exit_tail_arms_only_with_the_stop_owners_confirmation(scene, monkeypatch):
    import server
    from supervisor import workers

    monkeypatch.setattr(server, "DATA_DIR", scene.data)
    census = {"value": EXITED}
    monkeypatch.setattr(workers, "kill_workers", lambda **kw: True)  # the bool is about cleanup, not readers
    monkeypatch.setattr(workers, "last_worker_exit_census", lambda: census["value"])
    monkeypatch.setattr("ouroboros.platform_layer.kill_process_on_port", lambda _port: None)
    monkeypatch.setattr("ouroboros.extension_companion.panic_kill_all", lambda: None)
    monkeypatch.setattr("ouroboros.gateway.host_service.host_service_port", lambda: 8767)
    monkeypatch.setattr(server, "_stop_owned_daemon_for_new_pin", lambda: None)
    monkeypatch.setattr("multiprocessing.active_children", lambda: [])
    body_adoption.authorize(scene.ctx, scene.cand, reason="adopt it")
    restart_receipt(scene.data, scene.cand, "adopt it")
    assert body_adoption.bind_restart(lambda **_kw: (True, "ok"), scene.data, "adopt it")()[0]
    pointer = pathlib.Path(body_switch.pointer_path(str(scene.serving)))

    # An ordinary shutdown (no restart requested) never arms.
    monkeypatch.setattr(server, "stop_owned_work", lambda _root: {"state": "completed", "unconfirmed": []})
    server._restart_requested.clear()
    server._emergency_process_cleanup(port_sweep=False)
    assert body_adoption.read(scene.data)["phase"] == "authorized" and not pointer.exists()

    server._restart_requested.set()
    try:
        # A custodied survivor of the owned stop defers arming: the next generation boots the old tree.
        monkeypatch.setattr(server, "stop_owned_work",
                            lambda _root: {"state": "unconfirmed", "unconfirmed": [{"pid": 77}]})
        server._emergency_process_cleanup(port_sweep=False)
        handoff = body_adoption.read(scene.data)
        assert handoff["phase"] == "authorized" and "owned_work_unconfirmed" in handoff["events"][-1]["detail"]
        assert not pointer.exists()

        handoff["restart_bound"] = True
        body_switch.write_handoff(str(body_adoption.helper_dir(scene.data)), handoff)
        monkeypatch.setattr(server, "stop_owned_work", lambda _root: {"state": "completed", "unconfirmed": []})
        # A kill whose join could not confirm one worker's exit: the census, not kill_workers' bool, decides.
        census["value"] = {**EXITED, "unconfirmed": [4001]}
        server._emergency_process_cleanup(port_sweep=False)
        handoff = body_adoption.read(scene.data)
        assert handoff["phase"] == "authorized" and "worker_exits_unconfirmed" in handoff["events"][-1]["detail"]
        census["value"] = EXITED

        handoff["restart_bound"] = True  # the same adoption, carried by its restart again
        body_switch.write_handoff(str(body_adoption.helper_dir(scene.data)), handoff)
        server._owner_restart_requested.set()  # the owner pressed Restart meanwhile: newer control governs
        try:
            server._emergency_process_cleanup(port_sweep=False)
        finally:
            server._owner_restart_requested.clear()
        assert body_adoption.read(scene.data)["phase"] == "authorized" and not pointer.exists()

        handoff = body_adoption.read(scene.data)
        handoff["restart_bound"] = True
        body_switch.write_handoff(str(body_adoption.helper_dir(scene.data)), handoff)
        server._emergency_process_cleanup(port_sweep=False)
    finally:
        server._restart_requested.clear()
    assert body_adoption.read(scene.data)["phase"] == "armed"
    assert pointer.read_text().strip() == str(body_adoption.helper_dir(scene.data))
    assert git(scene.serving, "rev-parse", "HEAD") == scene.old  # the old generation never moved the tree


def test_supervisor_restart_binds_the_adoption_and_a_refused_restart_abandons_it(scene, monkeypatch):
    import server

    monkeypatch.setattr(server, "_safe_restart_serialized", lambda fn, **kwargs: fn(**kwargs))
    exited, messages = [], []
    monkeypatch.setattr(server, "_request_restart_exit", lambda: exited.append(True))

    def ctx(safe_restart):
        return SimpleNamespace(
            DRIVE_ROOT=scene.data, REPO_DIR=scene.serving, RUNNING={}, load_state=lambda: {"owner_chat_id": 1},
            safe_restart=safe_restart, kill_workers=lambda **k: None, update_state=lambda fn: None,
            persist_queue_snapshot=lambda **k: None, send_with_budget=lambda *a, **kw: messages.append(a[1]))

    body_adoption.authorize(scene.ctx, scene.cand, reason="adopt reviewed fix")
    restart_receipt(scene.data, scene.cand, "adopt reviewed fix")
    server._perform_supervisor_restart(ctx(lambda **k: (False, "Unsynced state rescued; restart blocked.")),
                                       restart_reason="adopt reviewed fix")
    assert exited == [] and body_adoption.read(scene.data) == {}
    assert any("was not armed" in text for text in messages)

    body_adoption.authorize(scene.ctx, scene.cand, reason="adopt reviewed fix")
    restart_receipt(scene.data, scene.cand, "adopt reviewed fix")
    server._perform_supervisor_restart(ctx(lambda **k: (True, "ok")), restart_reason="adopt reviewed fix")
    assert exited == [True]
    assert body_adoption.read(scene.data)["restart_bound"] is True
    assert git(scene.serving, "rev-parse", "HEAD") == scene.old


def test_evolution_restart_accepts_a_candidate_claim_at_the_adoption_base(scene, monkeypatch, tmp_path):
    import server

    from supervisor import evolution_lifecycle
    from tests._evolution_state_shared import _active_transaction

    campaign, tx = _active_transaction(scene.data, task_id="root-consumer")
    claim = {"campaign_id": campaign["id"], "transaction_id": tx["transaction_id"], "task_id": tx["task_id"]}
    assert evolution_lifecycle.record_evolution_commit(**claim, commit_sha=scene.cand)["ok"] is True
    claim["commit_sha"] = scene.cand
    marker = scene.data / "state" / "pending_restart_verify.json"
    marker.write_text(json.dumps({"reason": "evolution restart", "expected_sha": scene.cand,
                                  "evolution_claim": claim}))
    monkeypatch.setattr(server, "_safe_restart_serialized", lambda fn, **kwargs: fn(**kwargs))
    exited, messages, restarted = [], [], []
    monkeypatch.setattr(server, "_request_restart_exit", lambda: exited.append(True))
    ctx = SimpleNamespace(
        DRIVE_ROOT=scene.data, REPO_DIR=scene.serving, RUNNING={}, load_state=lambda: {"owner_chat_id": 1},
        safe_restart=lambda **k: restarted.append(k) or (True, "ok"), kill_workers=lambda **k: None,
        update_state=lambda fn: None, persist_queue_snapshot=lambda **k: None,
        send_with_budget=lambda *a, **kw: messages.append(a[1]))

    # The claimed commit lives on the candidate; with no authorized adoption the checkout "does not match".
    server._perform_supervisor_restart(ctx, restart_reason="evolution restart", evolution_restart=True)
    assert restarted == [] and "no longer matches" in messages[-1]

    # The supervisor's own evolution restart authorizes exactly that commit, then the check passes at the base.
    assert body_adoption.authorize_for_task(scene.data, "root-consumer", scene.cand, "evolution restart") is True
    server._perform_supervisor_restart(ctx, restart_reason="evolution restart", evolution_restart=True)
    assert restarted and exited == [True] and body_adoption.read(scene.data)["restart_bound"] is True

    # Owner dirt in the serving tree still stops an evolution restart (unchanged rule).
    exited.clear(), restarted.clear()
    (scene.serving / "owner-notes.txt").write_text("dirt\n")
    server._perform_supervisor_restart(ctx, restart_reason="evolution restart", evolution_restart=True)
    assert restarted == [] and exited == []


def test_evolution_cleanup_never_stashes_or_resets_serving_dirt_of_a_candidate_cycle(scene, monkeypatch):
    from supervisor import evolution_lifecycle, git_ops
    from tests._evolution_state_shared import _active_transaction

    _campaign, tx = _active_transaction(scene.data, task_id="root-consumer")
    monkeypatch.setattr(git_ops, "REPO_DIR", scene.serving)
    (scene.serving / "ouroboros/mod_b.py").write_text("GEN = 'OWNER_EDIT'\n")
    (scene.serving / "owner-notes.txt").write_text("owner untracked\n")
    tx = {**tx, "base_head": scene.old}

    evolution_lifecycle._cleanup_worktree_after_cycle(tx, "root-consumer")

    assert tx["cleanup_status"] == "candidate_retained" and "cleanup_stash" not in tx
    assert (scene.serving / "ouroboros/mod_b.py").read_text() == "GEN = 'OWNER_EDIT'\n"
    assert (scene.serving / "owner-notes.txt").read_text() == "owner untracked\n"
    assert git(scene.serving, "stash", "list") == ""
    assert scene.candidate.is_dir() and git(scene.candidate, "rev-parse", "HEAD") == scene.cand

    # A cycle that ended with an authorization nobody armed leaves no pending adoption behind.
    git(scene.serving, "checkout", "--", "ouroboros/mod_b.py")
    body_adoption.authorize(scene.ctx, scene.cand, reason="evolution restart")
    evolution_lifecycle._cleanup_worktree_after_cycle({**tx}, "root-consumer")
    assert body_adoption.read(scene.data) == {}

    # A cycle that authored the serving checkout itself keeps today's cleanup contract.
    other = {**tx, "task_id": "in-place-task"}
    evolution_lifecycle._cleanup_worktree_after_cycle(other, "in-place-task")
    assert other["cleanup_status"] != "candidate_retained"


def test_serving_push_publishes_reachable_release_tags_and_never_a_candidates(scene, monkeypatch, tmp_path):
    from ouroboros.tools import git as git_tools
    from supervisor import git_ops

    remote = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(scene.serving, "remote", "add", "origin", str(remote))
    git(scene.serving, "tag", "-a", "v1.0.0", "-m", "v1.0.0: base")  # the serving line's own release tag

    # A NUMBERED release committed in the candidate gets its annotated tag at commit time (shared namespace).
    numbered = candidate_commit(scene.candidate, "v1.0.1: numbered release", files={"VERSION": "1.0.1\n"})
    tagged = git_tools._auto_tag_on_version_bump(scene.candidate, "numbered release", expected_commit_sha=numbered,
                                                 expected_tag="v1.0.1")
    assert tagged == " [tagged: v1.0.1]" and git(scene.serving, "rev-parse", "v1.0.1^{commit}") == numbered
    # A version-neutral contribution carries no release tag at all.
    neutral = candidate_commit(scene.candidate, "neutral fix", files={"ouroboros/mod_b.py": "GEN = 'N'\n"})
    assert git_tools._auto_tag_on_version_bump(scene.candidate, "neutral fix", expected_commit_sha=neutral,
                                               expected_tag="") == ""
    assert git(scene.candidate, "tag", "--points-at", neutral) == ""
    git(scene.serving, "update-ref", "refs/ouroboros/candidates/private-pin", neutral)

    monkeypatch.setattr(git_ops, "REPO_DIR", scene.serving)
    monkeypatch.setattr(git_ops, "BRANCH_DEV", "ouroboros")
    pushed, message = git_ops.push_to_remote()

    assert pushed is True and "+ tags" in message
    assert git(remote, "rev-parse", "refs/heads/ouroboros") == scene.old
    assert git(remote, "rev-parse", "refs/tags/v1.0.0^{commit}") == scene.old
    refs = git(remote, "for-each-ref", "--format=%(refname)").splitlines()
    assert sorted(refs) == ["refs/heads/ouroboros", "refs/tags/v1.0.0"]  # no v1.0.1, no candidate branch, no pin

    # Explicit publication of the approved contribution branch stays available and is not forced.
    git(scene.candidate, "push", "-q", "origin", f"{scene.ctx.branch_dev}:refs/heads/contrib/fix")
    assert git(remote, "rev-parse", "refs/heads/contrib/fix") == neutral
    assert "refs/tags/v1.0.1" not in git(remote, "for-each-ref", "--format=%(refname)")

    # Once the numbered release IS the serving line, the same push publishes its tag.
    git(scene.serving, "merge", "-q", "--ff-only", numbered)
    assert git_ops.push_to_remote()[0] is True
    assert git(remote, "rev-parse", "refs/tags/v1.0.1^{commit}") == numbered


def test_boot_settlement_pushes_the_adopted_serving_line_and_tells_the_owner(scene, monkeypatch, tmp_path):
    from supervisor import git_ops, git_ops_reset, message_bus, state

    remote = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(scene.serving, "remote", "add", "origin", str(remote))
    monkeypatch.setattr(git_ops, "REPO_DIR", scene.serving)
    monkeypatch.setattr(git_ops, "BRANCH_DEV", "ouroboros")
    facts, sent = [], []
    monkeypatch.setattr(git_ops_reset, "_record_checkout_facts", lambda value: facts.append(value))
    monkeypatch.setattr(state, "load_state", lambda: {"owner_chat_id": 7})
    monkeypatch.setattr(message_bus, "send_with_budget", lambda chat, text, **kw: sent.append((chat, text, kw)))

    assert body_adoption.settle_on_boot(scene.data, scene.serving, supervisor_ready=True) == {}  # nothing inherited
    assert sent == [] and git(remote, "for-each-ref") == ""

    _switch(scene)
    unready = body_adoption.settle_on_boot(scene.data, scene.serving, supervisor_ready=False)
    assert unready["outcome"] == "unconfirmed" and git(remote, "for-each-ref") == "" and facts == []

    settled = body_adoption.settle_on_boot(scene.data, scene.serving, supervisor_ready=True)

    assert settled["outcome"] == "adopted"
    assert facts == [{"current_branch": "ouroboros", "current_sha": scene.cand}]
    assert git(remote, "rev-parse", "refs/heads/ouroboros") == scene.cand  # the serving line, as after any commit
    assert sent and sent[0][0] == 7 and scene.cand[:12] in sent[0][1]
    assert sent[0][2]["system_type"] == "restart_notice"


def test_runtime_fact_lists_retained_candidates_for_a_deliberate_resume(scene):
    assert body_candidate.context_fact("someone-else")[0] == {
        "id": "body_root-consumer", "owner_task": "root-consumer", "branch": "candidate/root-consumer",
        "path": str(scene.candidate), "base": scene.old[:12], "reviewed_commits": 1, "yours": False}
    assert body_candidate.context_fact("root-consumer")[0]["yours"] is True
    for index in range(3):
        body_candidate.prepare(make_ctx(scene.serving, scene.data, f"extra-{index}"))
    limited = body_candidate.context_fact("root-consumer", limit=2)
    assert len(limited) == 3 and limited[-1] == {"omitted": 2, "source": "state/subagent_worktrees.json (kind=body_candidate rows)"}

    from ouroboros.context import build_runtime_section

    env = SimpleNamespace(repo_dir=scene.serving, drive_root=scene.data, budget_drive_root=None,
                          drive_path=lambda name: scene.data / name)
    section = build_runtime_section(env, {"id": "root-consumer", "type": "task"})
    assert '"body_candidates"' in section and "candidate/root-consumer" in section
    assert f'"repo_dir": "{scene.serving}"' in section  # the Runtime block still names the RUNNING body


def test_adoption_state_uses_canonical_budget_root_for_forked_task(scene, tmp_path):
    """A forked execution drive must not hide restart adoption from the server boot."""
    fork = tmp_path / "forked-drive"
    fork.mkdir()
    scene.ctx.drive_root = fork
    scene.ctx.budget_drive_root = scene.data
    handoff = body_adoption.authorize(scene.ctx, scene.cand, reason="canonical-adoption")
    assert handoff["cand"] == scene.cand
    assert body_adoption.read(scene.data)["cand"] == scene.cand
    assert body_adoption.read(fork) == {}
