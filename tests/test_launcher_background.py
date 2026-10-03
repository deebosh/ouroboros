"""Desktop background mode: close vs quit, the one consent question, quiet start, second launch.

Contract scenarios for ``ouroboros/launcher_background.py`` with a stand-in window and
indicator; the platform adapters' native halves (WinForms tray, AppKit menu-bar item) are
exercised only on their own OS. Nothing here starts a GUI, the server or the live install.
"""
from __future__ import annotations

import os
import subprocess
import sys

from ouroboros import platform_layer


def test_a_losing_launcher_leaves_the_holders_pid_readable(tmp_path):
    """A manual second launch signals the PID in the lock file, so the loser must not erase it."""
    lock = tmp_path / "ouroboros.pid"
    assert platform_layer.pid_lock_acquire(str(lock))
    try:
        assert lock.read_text(encoding="utf-8") == str(os.getpid())
        loser = subprocess.run(
            [sys.executable, "-c", "import sys; from ouroboros import platform_layer as p; "
             "sys.exit(0 if p.pid_lock_acquire(sys.argv[1]) else 7)", str(lock)],
            capture_output=True, text=True, timeout=60, cwd=str(os.getcwd()),
            env={**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [os.getcwd(), os.environ.get("PYTHONPATH")]))},
        )
        assert loser.returncode == 7, loser.stderr
        assert lock.read_text(encoding="utf-8") == str(os.getpid())
    finally:
        platform_layer.pid_lock_release(str(lock))
    assert not lock.exists()
