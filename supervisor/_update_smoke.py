"""Leaf module: the pre-restart smoke-test helpers for a managed self-update.

Split out of ``supervisor/update_merge.py`` (ibl-978e5cd9258f — one of the
GIANT_PATHS files in the size-ratchet coordination campaign) purely to
shrink that module under the size-ratchet cap — no behavior change.
``update_merge.py`` re-exports both names so every historical import site
(``from supervisor.update_merge import update_restart_smoke``, etc.) keeps
resolving.

``update_restart_smoke`` reaches back into ``supervisor.update_merge`` for
``managed_update_constitution_present`` via a FUNCTION-LOCAL import (not a
module-level one) — ``update_merge.py`` imports this module's two names at
module level, so a module-level import back would cycle. Neither module
needs the other at load time, only at call time, so this is safe the same
way ``ouroboros.outcomes`` / ``ouroboros._outcome_axes`` is.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Dict, List

from supervisor import git_ops as _g


def _run_update_smoke(cmd: List[str], timeout_sec: float = 120.0) -> Dict[str, Any]:
    from ouroboros.platform_layer import kill_process_tree, subprocess_new_group_kwargs
    from ouroboros.tools.shell import _active_subprocesses, _subprocess_lock

    proc = subprocess.Popen(
        cmd,
        cwd=str(_g.REPO_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **subprocess_new_group_kwargs(),
    )
    with _subprocess_lock:
        _active_subprocesses.add(proc)
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            kill_process_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except Exception:
                stdout, stderr = "", ""
            return {
                "ok": False,
                "stdout": stdout or "",
                "stderr": f"update smoke exceeded {timeout_sec:.0f}s and was terminated",
                "returncode": 124,
            }
        return {
            "ok": proc.returncode == 0,
            "stdout": stdout or "",
            "stderr": stderr or "",
            "returncode": proc.returncode,
        }
    finally:
        with _subprocess_lock:
            _active_subprocesses.discard(proc)


def update_restart_smoke() -> Dict[str, Any]:
    """Stronger pre-restart smoke than ``import_test`` for gating an update apply: no
    unmerged index, ``py_compile server.py``, and an import of the core boot surface.
    pytest is intentionally NOT in this blocking gate (bloat/risk in a live self-updater)."""
    from supervisor.update_merge import managed_update_constitution_present

    if not managed_update_constitution_present("HEAD"):
        return {
            "ok": False,
            "stderr": "BIBLE.md is absent, empty, or not a regular file",
            "returncode": 1,
        }
    if getattr(sys, "frozen", False):
        return {"ok": True, "skipped": "frozen"}
    rc_u, unmerged, _ue = _g.git_capture(["git", "diff", "--name-only", "--diff-filter=U"])
    if rc_u != 0:
        return {"ok": False, "stderr": "could not inspect unmerged paths", "returncode": rc_u}
    if unmerged.strip():
        return {"ok": False, "stderr": f"unmerged paths remain: {unmerged}", "returncode": 1}
    deps_ok, deps_message = _g.sync_runtime_dependencies(reason="managed_update_pre_restart")
    if not deps_ok:
        return {"ok": False, "stderr": f"dependency sync failed: {deps_message}", "returncode": 1}
    compiled = _run_update_smoke([sys.executable, "-m", "py_compile", "server.py"])
    if not compiled["ok"]:
        return compiled
    return _run_update_smoke(
        [sys.executable, "-c",
         "import server, ouroboros.gateway.router, supervisor.queue, "
         "supervisor.events, ouroboros.tools.registry; print('smoke_ok')"]
    )
