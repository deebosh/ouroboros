"""Guard-condition tests for the periodic check-and-notify watcher (v6.103.0).

Never calls apply_managed_merge_update or any writer-fencing/apply code — this
module only ever plans (read-only, isolated temp worktree) and, at most, sends
a chat notification. Auto-apply from this path is impossible by construction.
"""

from __future__ import annotations

import supervisor.update_watch as update_watch


def test_disabled_autocheck_makes_no_git_or_network_call(monkeypatch):
    import ouroboros.update_channels as update_channels

    monkeypatch.setattr(update_channels, "get_update_autocheck_enabled", lambda: False)

    def _boom(*_a, **_k):
        raise AssertionError("must not touch update state while autocheck is disabled")

    monkeypatch.setattr(update_watch, "_read_watch_marker", _boom)

    update_watch._maybe_run_watch_cycle()


def test_never_proceeds_while_an_update_tx_is_active(monkeypatch):
    import ouroboros.update_channels as update_channels
    import supervisor.update_merge as update_merge

    monkeypatch.setattr(update_channels, "get_update_autocheck_enabled", lambda: True)
    monkeypatch.setattr(update_channels, "get_update_autocheck_interval_sec", lambda: 1800)
    monkeypatch.setattr(update_watch, "_read_watch_marker", lambda: {})
    monkeypatch.setattr(update_merge, "active_update_tx", lambda: {"phase": "assisted_resolution"})

    def _boom(*_a, **_k):
        raise AssertionError("must not acquire the update lock while a tx is active")

    monkeypatch.setattr(update_merge, "acquire_update_lock", _boom)

    update_watch._maybe_run_watch_cycle()


def test_skips_quietly_when_lock_is_held_elsewhere(monkeypatch):
    import ouroboros.update_channels as update_channels
    import supervisor.update_merge as update_merge

    monkeypatch.setattr(update_channels, "get_update_autocheck_enabled", lambda: True)
    monkeypatch.setattr(update_channels, "get_update_autocheck_interval_sec", lambda: 1800)
    monkeypatch.setattr(update_watch, "_read_watch_marker", lambda: {})
    monkeypatch.setattr(update_merge, "active_update_tx", lambda: {})
    monkeypatch.setattr(
        update_merge, "acquire_update_lock",
        lambda: (_ for _ in ()).throw(RuntimeError("held")),
    )

    def _boom(*_a, **_k):
        raise AssertionError("must not plan while the lock is held elsewhere")

    monkeypatch.setattr(update_watch, "_run_watch_cycle", _boom)

    update_watch._maybe_run_watch_cycle()  # must not raise


def test_interval_gating_skips_before_the_next_due_time(monkeypatch):
    import time

    import ouroboros.update_channels as update_channels

    monkeypatch.setattr(update_channels, "get_update_autocheck_enabled", lambda: True)
    monkeypatch.setattr(update_channels, "get_update_autocheck_interval_sec", lambda: 1800)
    monkeypatch.setattr(update_watch, "_read_watch_marker", lambda: {"last_run_at": time.time()})

    def _boom(*_a, **_k):
        raise AssertionError("must not touch the update lock before the interval elapses")

    import supervisor.update_merge as update_merge
    monkeypatch.setattr(update_merge, "active_update_tx", _boom)

    update_watch._maybe_run_watch_cycle()


def test_current_plan_persists_last_run_and_sends_no_notification(monkeypatch):
    sent = []
    monkeypatch.setattr(update_watch, "_notify_owner_new_update", lambda *_a, **_k: sent.append(True))

    marker_writes = []
    monkeypatch.setattr(update_watch, "_write_watch_marker", lambda data: marker_writes.append(data))

    import supervisor.update_merge as update_merge
    monkeypatch.setattr(update_merge, "plan_managed_update_merge", lambda **_k: {"available": True, "kind": "current"})

    update_watch._run_watch_cycle({})

    assert sent == []
    assert marker_writes and "last_run_at" in marker_writes[-1]


def test_dedupes_on_unchanged_target_and_flags(monkeypatch):
    sent = []
    monkeypatch.setattr(update_watch, "_notify_owner_new_update", lambda *_a, **_k: sent.append(True))
    monkeypatch.setattr(update_watch, "_write_watch_marker", lambda _data: None)

    import supervisor.update_merge as update_merge
    target = "b" * 40
    monkeypatch.setattr(
        update_merge, "plan_managed_update_merge",
        lambda **_k: {"available": True, "kind": "clean", "base_sha": "a" * 40, "target_sha": target, "overlap_candidates": {}},
    )

    marker = {"last_notified_fingerprint": f"{target}:0"}
    update_watch._run_watch_cycle(marker)

    assert sent == []


def test_notifies_once_for_a_new_target_sha(monkeypatch):
    sent = []
    monkeypatch.setattr(update_watch, "_notify_owner_new_update", lambda plan, flags: sent.append((plan["target_sha"], flags)))
    monkeypatch.setattr(update_watch, "_write_watch_marker", lambda _data: None)

    import supervisor.update_merge as update_merge
    target = "b" * 40
    monkeypatch.setattr(
        update_merge, "plan_managed_update_merge",
        lambda **_k: {"available": True, "kind": "clean", "base_sha": "a" * 40, "target_sha": target, "overlap_candidates": {}},
    )

    update_watch._run_watch_cycle({})

    assert sent == [(target, [])]
