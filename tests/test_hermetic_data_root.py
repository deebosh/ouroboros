"""Hermetic data-root CLASS pins (issue #455).

Two structural guarantees, pinned independently of any single writer:

1. The shared jsonl writer helper fails CLOSED under pytest when a path
   resolves into the live data tree — the guard half that was missing while
   ``state.atomic_write_text`` already had it, which is why the supervisor.jsonl
   leak landed silently.
2. Batch recipe: a real run of the update-merge suites under a throwaway HOME
   plus full OUROBOROS_* isolation leaves the live-shaped data root untouched
   (pre/post inventory delta must be empty) — the AGENTS hermeticity etalon
   (``find <live-root> -newermt <start>`` empty) as a regression test.
"""

import os
import pathlib
import subprocess
import sys

import pytest

from ouroboros.utils import append_jsonl

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_append_jsonl_fails_closed_on_live_root_write(monkeypatch, tmp_path):
    fake_live = tmp_path / "fake-live"
    monkeypatch.setenv("OUROBOROS_PYTEST_ACTIVE", "1")
    monkeypatch.delenv("OUROBOROS_ALLOW_LIVE_DATA_TESTS", raising=False)
    monkeypatch.setenv("OUROBOROS_TEST_LIVE_DATA_ROOT", str(fake_live))
    with pytest.raises(RuntimeError, match="PYTEST_LIVE_DATA_WRITE_BLOCKED"):
        append_jsonl(fake_live / "logs" / "supervisor.jsonl", {"type": "probe"})
    assert not (fake_live / "logs" / "supervisor.jsonl").exists()
    # A non-live path keeps working.
    assert append_jsonl(tmp_path / "ok" / "log.jsonl", {"type": "probe"}) is True


@pytest.mark.serial
def test_update_merge_suites_leave_live_shaped_root_untouched(tmp_path):
    """The E2E class pin: the exact suites that leaked in the live repro run
    under a throwaway HOME + full env isolation, and the live-shaped root
    (``$HOME/Ouroboros/data``) shows an EMPTY pre/post inventory delta."""
    fake_home = tmp_path / "home"
    iso = tmp_path / "iso"
    fake_home.mkdir()
    # The suites commit in throwaway repos with repo-local identity, but a
    # hermetic HOME must still offer a global one for any stray `git commit`.
    (fake_home / ".gitconfig").write_text(
        "[user]\n\tname = hermetic-pin\n\temail = hermetic-pin@example.invalid\n",
        encoding="utf-8",
    )
    env = {**os.environ,
           "HOME": str(fake_home),
           "OUROBOROS_APP_ROOT": str(iso),
           "OUROBOROS_REPO_DIR": str(iso / "repo"),
           "OUROBOROS_DATA_DIR": str(iso / "data"),
           "OUROBOROS_SETTINGS_PATH": str(iso / "data" / "settings.json")}
    for stale in ("OUROBOROS_PYTEST_ACTIVE", "OUROBOROS_TEST_LIVE_DATA_ROOT",
                  "OUROBOROS_BENCH_RUNS_ROOT", "PYTEST_CURRENT_TEST", "PYTEST_XDIST_WORKER"):
        env.pop(stale, None)

    live_shaped = fake_home / "Ouroboros" / "data"
    pre_inventory = sorted(str(p) for p in live_shaped.rglob("*")) if live_shaped.exists() else []
    assert pre_inventory == []

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
         "tests/test_update_dirty_stash.py", "tests/test_update_merge_plan.py"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == 0, (proc.stdout or "")[-4000:] + (proc.stderr or "")[-2000:]

    post_inventory = sorted(str(p) for p in live_shaped.rglob("*")) if live_shaped.exists() else []
    assert post_inventory == [], (
        f"update-merge suites leaked into the live-shaped data root: {post_inventory}"
    )
