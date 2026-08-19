"""Supervisor git/reset/rescue/dependency operations."""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple

from supervisor.state import (
    append_jsonl, atomic_write_text, load_state, save_state,
)
from ouroboros import config as _config
from ouroboros.utils import utc_now_iso

log = logging.getLogger(__name__)


# Pre-``init`` defaults follow the same environment-aware roots as the rest of the
# runtime (``ouroboros.config``), so a process that never calls ``init`` — an
# isolated test or smoke — cannot write supervisor rows into the live data drive.
REPO_DIR: pathlib.Path = pathlib.Path(_config.REPO_DIR)
DRIVE_ROOT: pathlib.Path = pathlib.Path(_config.DATA_DIR)
REMOTE_URL: str = ""
BRANCH_DEV: str = "ouroboros"
BRANCH_STABLE: str = "ouroboros-stable"
MANAGED_REPO_META_NAME = "ouroboros-managed.json"
BOOTSTRAP_PIN_MARKER_NAME = "ouroboros-bootstrap-pending"
UPDATE_INTENT_MARKER_NAME = "ouroboros-update-intent.json"
OFFICIAL_UPDATE_REMOTE_URL = "https://github.com/razzant/ouroboros"


def _guard_live_repo_destructive_git(cmd: List[str]) -> None:
    if os.environ.get("OUROBOROS_ALLOW_LIVE_REPO_TESTS") == "1":
        return
    try:
        live_repo = REPO_DIR.resolve(strict=False) == (
            pathlib.Path.home() / "Ouroboros" / "repo"
        ).resolve(strict=False)
    except OSError:
        live_repo = False
    if not (("PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules) and live_repo):
        return
    normalized = [str(part) for part in cmd]
    is_reset_hard = normalized[:3] == ["git", "reset", "--hard"]
    is_clean = normalized[:2] == ["git", "clean"]
    if is_reset_hard or is_clean:
        raise RuntimeError(
            "Refusing to run destructive git reset/clean on the live Ouroboros repo from pytest. "
            "Use an isolated repo fixture, or OUROBOROS_ALLOW_LIVE_REPO_TESTS=1 for an explicit live-repo test."
        )


def init(repo_dir: pathlib.Path, drive_root: pathlib.Path, remote_url: str,
         branch_dev: str = "ouroboros", branch_stable: str = "ouroboros-stable") -> None:
    global REPO_DIR, DRIVE_ROOT, REMOTE_URL, BRANCH_DEV, BRANCH_STABLE
    REPO_DIR = repo_dir
    DRIVE_ROOT = drive_root
    REMOTE_URL = remote_url
    BRANCH_DEV = branch_dev
    BRANCH_STABLE = branch_stable


def _git_dir() -> pathlib.Path:
    return REPO_DIR / ".git"


def _managed_repo_meta_path() -> pathlib.Path:
    return _git_dir() / MANAGED_REPO_META_NAME


def _bootstrap_pin_marker_path() -> pathlib.Path:
    return _git_dir() / BOOTSTRAP_PIN_MARKER_NAME


def _update_intent_marker_path() -> pathlib.Path:
    return _git_dir() / UPDATE_INTENT_MARKER_NAME


def _read_managed_repo_meta() -> Dict[str, Any]:
    path = _managed_repo_meta_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def managed_branch_defaults(repo_dir: Optional[pathlib.Path] = None) -> Tuple[str, str]:
    repo = repo_dir or REPO_DIR
    meta_path = repo / ".git" / MANAGED_REPO_META_NAME
    if not meta_path.is_file():
        return BRANCH_DEV, BRANCH_STABLE
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return BRANCH_DEV, BRANCH_STABLE
    if not isinstance(raw, dict):
        return BRANCH_DEV, BRANCH_STABLE
    branch_dev = str(raw.get("managed_local_branch") or BRANCH_DEV).strip() or BRANCH_DEV
    branch_stable = str(raw.get("managed_local_stable_branch") or BRANCH_STABLE).strip() or BRANCH_STABLE
    return branch_dev, branch_stable


def _is_launcher_managed_repo() -> bool:
    if str(os.environ.get("OUROBOROS_MANAGED_BY_LAUNCHER", "") or "").strip() == "1":
        return True
    return bool(_read_managed_repo_meta())


def _list_remotes(*, capture=None, warnings: Optional[List[str]] = None) -> List[str]:
    capture_fn = capture or git_capture
    rc, remotes, error = capture_fn(["git", "remote"])
    if rc != 0:
        if warnings is not None:
            warnings.append(
                f"remotes_error:{error or f'git remote exited {rc} without stderr'}"
            )
        return []
    return [line.strip() for line in remotes.splitlines() if line.strip()]


def _has_remote(name: Optional[str] = None) -> bool:
    remotes = _list_remotes()
    if name is None:
        return bool(remotes)
    return name in remotes


def _managed_remote_name(meta: Optional[Dict[str, Any]] = None) -> str:
    info = meta if meta is not None else _read_managed_repo_meta()
    return str(info.get("managed_remote_name") or "managed").strip() or "managed"


def _managed_remote_branch_for(branch: str, meta: Optional[Dict[str, Any]] = None) -> str:
    info = meta if meta is not None else _read_managed_repo_meta()
    if branch == BRANCH_DEV:
        return str(info.get("managed_remote_branch") or branch).strip()
    if branch == BRANCH_STABLE:
        return str(info.get("managed_remote_stable_branch") or branch).strip()
    return branch


def _pin_to_bundle_sha_on_bootstrap(reason: str, managed_meta: Optional[Dict[str, Any]] = None) -> bool:
    if str(reason or "").strip().lower() != "bootstrap":
        return False
    if not _bootstrap_pin_marker_path().exists():
        return False
    info = managed_meta if managed_meta is not None else _read_managed_repo_meta()
    source_sha = str(info.get("source_sha") or "").strip()
    if not source_sha:
        return False
    rc, head_sha, _ = git_capture(["git", "rev-parse", "HEAD"])
    if rc != 0 or str(head_sha or "").strip() != source_sha:
        return False
    return True


def _clear_bootstrap_pin_marker() -> None:
    try:
        _bootstrap_pin_marker_path().unlink()
    except FileNotFoundError:
        return
    except Exception:
        log.warning("Failed to clear bootstrap pin marker", exc_info=True)


def _read_update_intent() -> Dict[str, Any]:
    path = _update_intent_marker_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_update_intent(payload: Dict[str, Any]) -> None:
    # Atomic: a torn marker would make the next restart silently skip the
    # prepared managed update (reader fails closed on parse errors).
    from ouroboros.utils import atomic_write_json

    path = _update_intent_marker_path()
    atomic_write_json(path, payload, trailing_newline=True)


def _clear_update_intent() -> bool:
    try:
        _update_intent_marker_path().unlink()
    except FileNotFoundError:
        return True
    except Exception:
        log.warning("Failed to clear update intent marker", exc_info=True)
        return False
    return True


def _run_git_process_bounded(
    cmd: List[str], *, timeout: float, cwd: Optional[pathlib.Path] = None,
    env: Optional[Dict[str, str]] = None, text: bool = True,
) -> Tuple[int, Any, Any]:
    """Run one short-lived Git process and terminate its tree on timeout."""
    from ouroboros.platform_layer import kill_process_tree, subprocess_new_group_kwargs
    from ouroboros.tools.shell import _active_subprocesses, _subprocess_lock

    limit = float(timeout)
    empty = "" if text else b""
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd or REPO_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            env=env,
            **subprocess_new_group_kwargs(),
        )
    except OSError as exc:
        detail = str(exc)
        return 127, empty, detail if text else detail.encode("utf-8", "replace")

    with _subprocess_lock:
        _active_subprocesses.add(proc)
    try:
        try:
            stdout, stderr = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired as exc:
            partial_error = getattr(exc, "stderr", None)
            kill_process_tree(proc)
            try:
                _tail_out, tail_error = proc.communicate(timeout=10)
            except Exception:
                tail_error = None
            raw_detail = tail_error or partial_error or empty
            if isinstance(raw_detail, bytes):
                detail = raw_detail.decode("utf-8", "replace").strip()
            else:
                detail = str(raw_detail or "").strip()
            message = (
                f"git process timed out after {limit:g}s and was terminated: "
                f"{' '.join(cmd)}"
            )
            if detail:
                message += f" ({detail})"
            return (
                FETCH_TIMEOUT_RC,
                empty,
                message if text else message.encode("utf-8", "replace"),
            )
        return (
            int(proc.returncode if proc.returncode is not None else 1),
            stdout if stdout is not None else empty,
            stderr if stderr is not None else empty,
        )
    finally:
        with _subprocess_lock:
            _active_subprocesses.discard(proc)


def git_capture(cmd: List[str], *, timeout: Optional[float] = None) -> Tuple[int, str, str]:
    # Same reason as utils.run_cmd: this stderr is PARSED (`_maybe_repair_git_index`
    # matches English git diagnostics), so the operator's locale must not decide
    # whether a repairable index error is recognised.
    # ``timeout`` is None for existing non-rescue call sites: a bound here is a
    # behavior change, so it stays opt-in rather than silently retiming them.
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    for _attempt in range(2):
        if timeout is None:
            result = subprocess.run(
                cmd, cwd=str(REPO_DIR), capture_output=True, text=True, env=env,
            )
            returncode, stdout, raw_stderr = (
                result.returncode, result.stdout or "", result.stderr or "",
            )
        else:
            returncode, stdout, raw_stderr = _run_git_process_bounded(
                cmd, timeout=timeout, cwd=REPO_DIR, env=env, text=True,
            )
        stderr = str(raw_stderr or "").strip()
        if returncode == 0:
            return returncode, str(stdout or "").strip(), stderr
        if _maybe_repair_git_index(stderr, timeout=timeout):
            continue
        return returncode, str(stdout or "").strip(), stderr
    return returncode, str(stdout or "").strip(), stderr


from supervisor import update_source as _update_source

# One name per assignment: the module-handle pins (tests/test_module_handle_extraction.py)
# and the G1 leaves resolve these as parent-owned bindings, and a tuple target would
# hide them from the lexical binding scan.
FETCH_TIMEOUT_RC = _update_source.FETCH_TIMEOUT_RC
_git_network_bounded = _update_source._git_network_bounded
_managed_update_target = _update_source._managed_update_target
git_fetch_bounded = _update_source.git_fetch_bounded

def rescue_git_capture(cmd: List[str]) -> Tuple[int, str, str]:
    """Run ``git_capture`` under the configured rescue-only wall-clock bound.

    A timeout returns through the normal nonzero-rc shape, which every caller
    in the rescue graph already treats as a warning rather than a hard stop:
    fail-open, never a stall.
    """
    from ouroboros.update_channels import get_rescue_git_timeout_sec

    return git_capture(cmd, timeout=get_rescue_git_timeout_sec())


def _resolve_managed_update_target(
    remote_name: str, remote_branch: str, branch_ref: str, update_channel: str
) -> Tuple[str, str, str]:
    return _update_source.resolve_managed_update_target(
        remote_name,
        remote_branch,
        branch_ref,
        update_channel=update_channel,
        capture=git_capture,
    )


def _stale_git_lock_paths(max_age_sec: float = 15.0) -> List[pathlib.Path]:
    git_dir = REPO_DIR / ".git"
    if not git_dir.exists():
        return []
    candidates = [git_dir / "index.lock"]
    stale_paths: List[pathlib.Path] = []
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    for path in candidates:
        try:
            age = now - path.stat().st_mtime
        except FileNotFoundError:
            continue
        except Exception:
            continue
        if age >= max_age_sec:
            stale_paths.append(path)
    return stale_paths


def _maybe_repair_git_index(stderr: str, *, timeout: Optional[float] = None) -> bool:
    error_text = str(stderr or "")
    error_lower = error_text.lower()
    repaired = False

    if "index.lock" in error_lower:
        for lock_path in _stale_git_lock_paths():
            try:
                lock_path.unlink()
                repaired = True
                log.warning("Removed stale git lock: %s", lock_path)
            except Exception:
                log.warning("Failed to remove stale git lock: %s", lock_path, exc_info=True)

    corrupt_markers = (
        "index file smaller than expected",
        "index file corrupt",
        "fatal: .git/index:",
    )
    if not any(marker in error_lower for marker in corrupt_markers):
        return repaired

    git_dir = REPO_DIR / ".git"
    if not git_dir.exists():
        return repaired

    index_path = git_dir / "index"
    if index_path.exists():
        backup_path = git_dir / f"index.corrupt.{uuid.uuid4().hex[:8]}.bak"
        try:
            index_path.replace(backup_path)
            repaired = True
            log.warning("Backed up corrupt git index to %s", backup_path)
        except Exception:
            log.warning("Failed to back up corrupt git index %s", index_path, exc_info=True)
            return repaired

    rebuild_cmd = ["git", "reset", "--mixed", "HEAD"]
    rebuild_env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    if timeout is None:
        rebuild = subprocess.run(
            rebuild_cmd,
            cwd=str(REPO_DIR),
            capture_output=True,
            text=True,
            env=rebuild_env,
        )
        rebuild_rc = rebuild.returncode
        rebuild_error = (rebuild.stderr or "").strip() or (rebuild.stdout or "").strip()
    else:
        rebuild_rc, rebuild_stdout, rebuild_stderr = _run_git_process_bounded(
            rebuild_cmd, timeout=timeout, cwd=REPO_DIR, env=rebuild_env, text=True,
        )
        rebuild_error = str(rebuild_stderr or "").strip() or str(rebuild_stdout or "").strip()
    if rebuild_rc == 0:
        log.warning("Rebuilt git index after corruption in %s", REPO_DIR)
        return True

    log.warning(
        "Failed to rebuild git index after corruption: %s",
        rebuild_error,
    )
    return repaired


_REPO_GITIGNORE = """\
# Secrets
.env
.env.*
*.key
*.pem

# IDE
.cursor/
.vscode/
.idea/

# Python bytecode
__pycache__/
*.pyc
*.pyo
*.egg-info/

# Build artifacts
dist/
build/
.pytest_cache/
.mypy_cache/

# Native / binary artifacts (PyInstaller, compiled extensions)
*.so
*.dylib
*.dll
*.dist-info/
base_library.zip

# OS
.DS_Store
Thumbs.db

# Release artifacts
.create_release.py
.release_notes.md
repo.bundle
repo_bundle_manifest.json
python-standalone/
"""


def _ensure_repo_gitignore(repo_dir: pathlib.Path = None) -> None:
    """Write .gitignore if missing before any git add -A."""
    target = repo_dir or REPO_DIR
    gi = target / ".gitignore"
    if not gi.exists():
        gi.write_text(_REPO_GITIGNORE, encoding="utf-8")


def _ensure_git_identity() -> None:
    """Ensure repo-local git identity exists for local commits/tags."""
    git_capture(["git", "config", "user.name", "Ouroboros"])
    git_capture(["git", "config", "user.email", "ouroboros@local.mac"])


def _ensure_local_version_tag() -> None:
    """Create the current VERSION tag locally when a local-only repo has none."""
    version_path = REPO_DIR / "VERSION"
    if not version_path.exists():
        return

    version = version_path.read_text(encoding="utf-8").strip().lstrip("v")
    if not re.match(r"^\d+\.\d+\.\d+(?:-?(?:rc|alpha|beta|a|b)\.?\d+)?$", version, re.IGNORECASE):
        return

    tag_name = f"v{version}"
    rc, tag_match, err = git_capture(["git", "tag", "-l", tag_name])
    if rc != 0:
        log.warning("Failed to check local tag %s: %s", tag_name, err)
        return
    if tag_match.strip():
        return

    rc, all_tags, err = git_capture(["git", "tag", "-l"])
    if rc != 0:
        log.warning("Failed to list local tags: %s", err)
        return
    if any(t.strip() for t in all_tags.splitlines()):
        return

    rc, head_sha, err = git_capture(["git", "rev-parse", "HEAD"])
    if rc != 0 or not head_sha:
        log.warning("Cannot create local version tag %s without HEAD: %s", tag_name, err)
        return

    _ensure_git_identity()
    rc, _, err = git_capture(["git", "tag", "-a", tag_name, "-m", f"Release {tag_name}"])
    if rc != 0:
        log.warning("Failed to create local version tag %s: %s", tag_name, err)
        return

    log.info("Created local-only version tag %s at %s", tag_name, head_sha[:8])


def ensure_repo_present() -> None:
    if not (REPO_DIR / ".git").exists():
        if _is_launcher_managed_repo():
            raise RuntimeError(
                "Launcher-managed repo is missing .git metadata. "
                "The launcher bootstrap must recreate REPO_DIR from the embedded repo bundle."
            )
        # REPO_DIR is live code: initialize in place, never remove it.
        REPO_DIR.mkdir(parents=True, exist_ok=True)
        _ensure_repo_gitignore()
        import dulwich.repo
        dulwich.repo.Repo.init(str(REPO_DIR))

        _ensure_git_identity()

        rc, _, _ = git_capture(["git", "status", "--porcelain"])
        if rc == 0:
            subprocess.run(["git", "add", "-A"], cwd=str(REPO_DIR), check=True)
            subprocess.run(["git", "commit", "-m", "Initial commit from bundle"], cwd=str(REPO_DIR), check=False)

        subprocess.run(["git", "branch", "-M", BRANCH_DEV], cwd=str(REPO_DIR), check=False)
        subprocess.run(["git", "branch", BRANCH_STABLE], cwd=str(REPO_DIR), check=False)

    if not _is_launcher_managed_repo():
        _ensure_local_version_tag()

def _collect_repo_sync_state() -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "current_branch": "unknown",
        "dirty_lines": [],
        "unpushed_lines": [],
        "warnings": [],
    }

    rc, branch, err = rescue_git_capture(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if rc == 0 and branch:
        state["current_branch"] = branch
    elif err:
        state["warnings"].append(f"branch_error:{err}")

    rc, dirty, err = rescue_git_capture(["git", "status", "--porcelain"])
    if rc == 0 and dirty:
        state["dirty_lines"] = [ln for ln in dirty.splitlines() if ln.strip()]
    elif rc != 0:
        detail = err or f"git status exited {rc} without stderr"
        state["warnings"].append(f"status_error:{detail}")

    remotes = set(_list_remotes(
        capture=rescue_git_capture,
        warnings=state["warnings"],
    ))
    upstream = ""
    current_branch = str(state.get("current_branch") or "")
    managed_meta = _read_managed_repo_meta()
    if managed_meta and current_branch not in ("", "HEAD", "unknown"):
        managed_remote = _managed_remote_name(managed_meta)
        managed_branch = _managed_remote_branch_for(current_branch, managed_meta)
        if managed_branch and managed_remote in remotes:
            upstream = f"{managed_remote}/{managed_branch}"

    if not upstream and "origin" in remotes:
        rc, up, err = rescue_git_capture(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
        if rc == 0 and up:
            upstream = up
        else:
            if current_branch not in ("", "HEAD", "unknown"):
                upstream = f"origin/{current_branch}"
            elif err:
                state["warnings"].append(f"upstream_error:{err}")

    if upstream:
        rc, unpushed, err = rescue_git_capture(["git", "log", "--oneline", f"{upstream}..HEAD"])
        if rc == 0 and unpushed:
            state["unpushed_lines"] = [ln for ln in unpushed.splitlines() if ln.strip()]
        elif rc != 0 and err:
            state["warnings"].append(f"unpushed_error:{err}")

    return state


def _copy_untracked_for_rescue(dst_root: pathlib.Path, max_files: int = 200,
                                max_total_bytes: int = 12_000_000) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "copied_files": 0, "skipped_files": 0, "copied_bytes": 0, "truncated": False,
    }
    rc, txt, err = rescue_git_capture(["git", "ls-files", "--others", "--exclude-standard"])
    if rc != 0:
        out["error"] = err or "git ls-files failed"
        return out

    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    if not lines:
        return out

    dst_root.mkdir(parents=True, exist_ok=True)
    for rel in lines:
        if out["copied_files"] >= max_files:
            out["truncated"] = True
            break
        src = (REPO_DIR / rel).resolve()
        try:
            src.relative_to(REPO_DIR.resolve())
        except Exception:
            out["skipped_files"] += 1
            continue
        if not src.exists() or not src.is_file():
            out["skipped_files"] += 1
            continue
        try:
            size = int(src.stat().st_size)
        except Exception:
            out["skipped_files"] += 1
            continue
        if (out["copied_bytes"] + size) > max_total_bytes:
            out["truncated"] = True
            break
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
            out["copied_files"] += 1
            out["copied_bytes"] += size
        except Exception:
            out["skipped_files"] += 1
    return out


def _atomic_write_bytes(path: pathlib.Path, data: bytes) -> None:
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_bytes(data)
    tmp.replace(path)


def _create_rescue_snapshot(branch: str, reason: str,
                             repo_state: Dict[str, Any], *,
                             link_evolution: bool = True) -> Dict[str, Any]:
    now = datetime.datetime.now(datetime.timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")
    rescue_dir = DRIVE_ROOT / "archive" / "rescue" / f"{ts}_{uuid.uuid4().hex[:8]}"
    rescue_dir.mkdir(parents=True, exist_ok=True)

    info: Dict[str, Any] = {
        "ts": now.isoformat(),
        "target_branch": branch,
        "reason": reason,
        "current_branch": repo_state.get("current_branch"),
        "dirty_count": len(repo_state.get("dirty_lines") or []),
        "unpushed_count": len(repo_state.get("unpushed_lines") or []),
        "warnings": list(repo_state.get("warnings") or []),
        "path": str(rescue_dir),
    }

    rc_status, status_txt, status_error = rescue_git_capture(
        ["git", "status", "--porcelain"]
    )
    if rc_status == 0:
        atomic_write_text(rescue_dir / "status.porcelain.txt",
                          status_txt + ("\n" if status_txt else ""))
    else:
        info["warnings"].append(
            f"snapshot_status_error:{status_error or f'git status exited {rc_status} without stderr'}"
        )

    # changes.diff must survive BYTES end-to-end: on an unmerged index it is the
    # ONLY carrier of in-progress resolutions, and text-mode capture would corrupt
    # non-UTF-8 content into U+FFFD. The flag tail pins away operator config that
    # reshapes diff output into something `git apply` cannot re-apply: external
    # diff drivers (--no-ext-diff), textconv filters (--no-textconv), colour
    # escapes (--no-color) and prefix rewrites (--src-prefix/--dst-prefix beat
    # diff.noprefix). GIT_DIFF_OPTS is dropped from the environment because it
    # can carry a context-width override that beats the flags.
    try:
        from ouroboros.update_channels import get_rescue_git_timeout_sec

        capture_env = {k: v for k, v in os.environ.items() if k != "GIT_DIFF_OPTS"}
        capture_env.update({"LC_ALL": "C", "LANG": "C"})
        diff_rc, diff_stdout, diff_stderr = _run_git_process_bounded(
            ["git", "diff", "--binary", "--no-ext-diff", "--no-textconv", "--no-color",
             "--src-prefix=a/", "--dst-prefix=b/", "HEAD"],
            cwd=REPO_DIR,
            env=capture_env,
            text=False,
            timeout=get_rescue_git_timeout_sec(),
        )
        if diff_rc == 0:
            _atomic_write_bytes(rescue_dir / "changes.diff", diff_stdout or b"")
        else:
            raw_error = diff_stderr or b""
            info["diff_error"] = (
                raw_error.decode("utf-8", "replace").strip()
                if isinstance(raw_error, bytes)
                else str(raw_error).strip()
            ) or "git diff failed"
    except Exception as diff_exc:
        log.warning("Rescue diff capture failed", exc_info=True)
        info["diff_error"] = repr(diff_exc)

    # Also capture tracked changes as a real, recoverable git object so recovery
    # is `git stash apply <sha>` / `git checkout <ref> -- .` rather than only a
    # loose diff file. `git stash create` snapshots staged+unstaged tracked
    # changes (it omits untracked files, which the copy below preserves). Purely
    # additive: failure here never blocks the reset and the diff/untracked copy
    # remain the primary recovery artifacts.
    rc_stash, stash_sha, stash_err = rescue_git_capture(["git", "stash", "create", f"rescue:{reason}"])
    stash_sha = stash_sha.strip()
    if rc_stash != 0:
        # rc==0 with an empty sha is LEGITIMATE (nothing to stash / untracked-only
        # dirt); a nonzero rc — e.g. "needs merge" on an unmerged index — is
        # disclosed instead of silently omitting rescue_ref.
        info["rescue_stash_error"] = stash_err or "git stash create failed"
    elif stash_sha:
        ref_name = f"refs/rescue/{rescue_dir.name}"
        rc_ref, _, ref_err = rescue_git_capture(["git", "update-ref", ref_name, stash_sha])
        if rc_ref == 0:
            info["rescue_ref"] = ref_name
            info["rescue_commit"] = stash_sha
        else:
            info["rescue_ref_error"] = ref_err or "git update-ref failed"

    # Merge topology (best-effort): an in-progress merge cannot be stash-captured,
    # so record MERGE_HEAD, the unmerged index entries, and the merge message —
    # together with changes.diff (a plain worktree-vs-HEAD diff that DOES carry
    # in-progress resolutions) they make the merge state operator-recoverable.
    try:
        rc_mh, merge_head, mh_error = rescue_git_capture(
            ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"]
        )
        if rc_mh == 0 and merge_head.strip():
            info["merge_head"] = merge_head.strip()
            rc_u, unmerged_txt, unmerged_error = rescue_git_capture(
                ["git", "ls-files", "-u"]
            )
            if rc_u == 0 and unmerged_txt:
                atomic_write_text(rescue_dir / "unmerged.txt", unmerged_txt + "\n")
                # Unique conflicted PATHS (stage 1/2/3 rows collapse to one path).
                info["unmerged_count"] = len({
                    ln.split("\t", 1)[-1] for ln in unmerged_txt.splitlines() if ln.strip()
                })
            elif rc_u != 0:
                info["warnings"].append(
                    f"unmerged_index_error:{unmerged_error or f'git ls-files exited {rc_u} without stderr'}"
                )
            # --git-path: in a linked worktree .git is a FILE, so a naive
            # .git/MERGE_MSG probe would silently drop the message.
            rc_p, msg_rel, msg_path_error = rescue_git_capture(
                ["git", "rev-parse", "--git-path", "MERGE_MSG"]
            )
            if rc_p != 0:
                info["warnings"].append(
                    f"merge_msg_path_error:{msg_path_error or f'git rev-parse exited {rc_p} without stderr'}"
                )
            merge_msg_path = (REPO_DIR / msg_rel) if rc_p == 0 and msg_rel else (
                _git_dir() / "MERGE_MSG"
            )
            if merge_msg_path.is_file():
                atomic_write_text(rescue_dir / "merge_msg.txt",
                                  merge_msg_path.read_text(encoding="utf-8", errors="replace"))
        elif rc_mh != 1 or bool(mh_error.strip()):
            info["warnings"].append(
                f"merge_head_error:{mh_error or f'git rev-parse exited {rc_mh} without stderr'}"
            )
    except Exception as exc:
        log.warning("Failed to capture merge topology into rescue snapshot", exc_info=True)
        info["warnings"].append(f"merge_topology_error:{exc!r}")

    untracked_meta = _copy_untracked_for_rescue(rescue_dir / "untracked")
    info["untracked"] = untracked_meta

    unpushed_lines = [ln for ln in (repo_state.get("unpushed_lines") or []) if str(ln).strip()]
    if unpushed_lines:
        atomic_write_text(rescue_dir / "unpushed_commits.txt",
                          "\n".join(unpushed_lines) + "\n")

    atomic_write_text(rescue_dir / "rescue_meta.json",
                      json.dumps(info, ensure_ascii=False, indent=2))
    if link_evolution:
        _link_rescue_to_evolution_transaction(info, reason)
    return info


def _link_rescue_to_evolution_transaction(rescue_info: Dict[str, Any], reason: str) -> None:
    """Attach rescue recovery pointers to the active evolution transaction."""
    try:
        from supervisor.evolution_lifecycle import link_evolution_rescue

        linked = link_evolution_rescue(pathlib.Path(DRIVE_ROOT), rescue_info)
        if not linked:
            return
        append_jsonl(
            pathlib.Path(DRIVE_ROOT) / "logs" / "supervisor.jsonl",
            {
                "ts": utc_now_iso(),
                "type": "evolution_transaction_rescue_linked",
                "reason": reason,
                "transaction_id": linked.get("transaction_id"),
                "task_id": linked.get("task_id"),
                "rescue_ref": linked.get("rescue_ref"),
                "rescue_path": linked.get("rescue_path"),
            },
        )
    except Exception:
        log.debug("Failed to link rescue snapshot to evolution transaction", exc_info=True)


def _rescue_untracked_incomplete(rescue_info: Dict[str, Any]) -> str:
    """Return a human-readable reason when untracked rescue capture is incomplete."""
    meta = rescue_info.get("untracked")
    if not isinstance(meta, dict):
        return ""
    if meta.get("error"):
        return str(meta.get("error"))
    if meta.get("truncated"):
        return "untracked rescue copy was truncated"
    if int(meta.get("skipped_files") or 0) > 0:
        return f"{int(meta.get('skipped_files') or 0)} untracked file(s) were skipped"
    return ""


def rescue_before_destructive_rollback(reason: str, *, context: str = "rollback") -> Dict[str, Any]:
    """Best-effort rescue snapshot before a destructive managed-update step.

    Returns a pointer ``{path, ref, ts}`` on capture, ``{}`` when the tree is
    clean and no merge is in progress — nothing to rescue, so a replayed
    ``rolling_back`` boot stays idempotent — and ``{"error": ...}`` on failure.
    A git-status failure counts as a DIRTY tree: an unreadable tree is rescued,
    not skipped. ``context`` only labels the durable reason (``rollback`` →
    ``managed_update_rollback:*``, anything else → ``managed_update_rescue:*``,
    e.g. the boot re-materialization path). FAIL-OPEN by owner decision
    (2026-08-10, 4=A): failures never block the rollback — they are logged and
    returned as the typed ``error`` marker. One durable supervisor.jsonl line
    records the capture (or its failure) before the destructive step; that
    write itself never branches the flow. The snapshot is NOT linked to the
    active evolution transaction — it documents a managed-update rollback, and
    the link would flip a live evolution cycle to "abandoned". Transaction
    bookkeeping stays with the caller (update_merge); this helper only talks to
    git and the supervisor log."""
    try:
        rc_status, dirty, status_error = rescue_git_capture(
            ["git", "status", "--porcelain"]
        )
        rc_mh, merge_head, merge_head_error = rescue_git_capture(
            ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"]
        )
        merge_in_progress = rc_mh == 0 and bool(merge_head.strip())
        merge_absent = rc_mh == 1 and not merge_head_error.strip()
        if rc_status == 0 and not dirty.strip() and merge_absent:
            return {}
        repo_state = _collect_repo_sync_state()
        warnings = repo_state.setdefault("warnings", [])
        if rc_status != 0:
            warnings.append(
                f"rollback_status_error:{status_error or f'git status exited {rc_status} without stderr'}"
            )
        if not merge_in_progress and not merge_absent:
            warnings.append(
                f"merge_head_error:{merge_head_error or f'git rev-parse exited {rc_mh} without a merge head'}"
            )
        branch = str(repo_state.get("current_branch") or BRANCH_DEV)
        prefix = "managed_update_rollback" if context == "rollback" else "managed_update_rescue"
        info = _create_rescue_snapshot(
            branch, f"{prefix}:{reason}", repo_state, link_evolution=False,
        )
        result: Dict[str, Any] = {
            "path": str(info.get("path") or ""),
            "ref": str(info.get("rescue_ref") or ""),
            "ts": str(info.get("ts") or ""),
        }
        event = {
            "ts": utc_now_iso(), "type": "managed_update_rescue_captured",
            "reason": reason, "rescue_path": result["path"],
            **({"rescue_ref": result["ref"]} if result["ref"] else {}),
            **({"warnings": list(info.get("warnings") or [])}
               if info.get("warnings") else {}),
        }
    except Exception as exc:
        log.warning(
            "rescue before destructive rollback failed (rollback continues)", exc_info=True
        )
        result = {"error": repr(exc)}
        event = {"ts": utc_now_iso(), "type": "managed_update_rescue_failed",
                 "reason": reason, "error": repr(exc)}
    try:
        if not append_jsonl(DRIVE_ROOT / "logs" / "supervisor.jsonl", event):
            log.warning(
                "rescue disclosure could not be written to supervisor.jsonl "
                "(rescue itself is at %s)", result.get("path") or "<none>",
            )
    except Exception:
        log.warning("rescue disclosure raised (continuing)", exc_info=True)
    return result


def rescue_into_tx(tx: Dict[str, Any], *, key: str, reason: str, context: str,
                   writer) -> Dict[str, Any]:
    """Take a pre-destructive rescue and record its outcome in the update tx.

    A captured pointer lands under *key* as ``{path, ref?, ts, reason, count}``
    and is persisted via *writer* (``update_merge.write_update_tx``) BEFORE the
    caller's destructive step — the persisted pointer doubles as the replay
    guard against duplicate rescues. ``count`` increments when a previous
    pointer is overwritten (each re-materialization takes a fresh rescue), so
    the objective renderer can honestly say "latest of N". A capture failure is
    recorded in-memory under ``<key>_error`` for the caller's terminal event and
    is NOT persisted, so a retried rollback re-attempts the rescue. Fail-open
    throughout: a failed tx write is logged and never blocks the caller."""
    rescue_info = rescue_before_destructive_rollback(reason, context=context)
    if rescue_info.get("path"):
        prior = tx.get(key)
        count = (int(prior.get("count") or 1) + 1) if isinstance(prior, dict) else 1
        pointer = {"path": rescue_info["path"], "ts": rescue_info.get("ts") or "",
                   "reason": reason, "count": count}
        if rescue_info.get("ref"):
            pointer["ref"] = rescue_info["ref"]
        tx[key] = pointer
        try:
            writer(tx)
        except Exception:
            log.warning("could not persist the %s rescue pointer into the update tx",
                        key, exc_info=True)
    elif rescue_info.get("error"):
        tx[f"{key}_error"] = str(rescue_info["error"])
    return rescue_info


def _compute_ref_ahead_count(ref: str, target_ref: str) -> Tuple[bool, int, str]:
    """Return whether *ref* is ahead of *target_ref*, failing closed on errors."""
    if not ref or not target_ref:
        return False, 0, "missing ref for ahead comparison"
    rc, counts, err = git_capture([
        "git", "rev-list", "--left-right", "--count", f"{ref}...{target_ref}",
    ])
    if rc != 0:
        return False, 0, err or f"git rev-list failed for {ref}...{target_ref}"
    try:
        ahead, _behind = (int(part) for part in counts.split())
    except Exception:
        return False, 0, f"could not parse ahead/behind counts: {counts!r}"
    return True, ahead, ""


def _ref_points_at_ref(left_ref: str, right_ref: str) -> bool:
    left_ref = str(left_ref or "").strip()
    right_ref = str(right_ref or "").strip()
    if not left_ref or not right_ref:
        return False
    rc_left, left_sha, _ = git_capture(["git", "rev-parse", "--verify", left_ref])
    if rc_left != 0 or not left_sha:
        return False
    rc_right, right_sha, _ = git_capture(["git", "rev-parse", "--verify", right_ref])
    return rc_right == 0 and bool(right_sha) and left_sha.strip() == right_sha.strip()


def preserve_local_ref_branch(ref: str = "HEAD", prefix: str = "local-keep") -> Tuple[bool, str]:
    """Create a local branch pointing at *ref* before replacing it."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    branch_name = f"{prefix}-{now}-{uuid.uuid4().hex[:6]}"
    rc, _out, err = git_capture(["git", "branch", branch_name, ref])
    if rc != 0:
        return False, err or f"failed to create {branch_name}"
    return True, branch_name


def _preserve_branch_for_official_reset(
    branch: str,
    target_ref: str,
    update_intent: Dict[str, Any],
) -> Tuple[bool, str]:
    """Ensure local commits survive an explicit official update reset."""
    count_ok, ahead, count_error = _compute_ref_ahead_count(branch, target_ref)
    if not count_ok:
        return False, f"Could not compare {branch} with update target {target_ref}: {count_error}"
    if ahead <= 0:
        return True, ""
    existing = str(update_intent.get("keep_branch") or "").strip()
    if existing and _ref_points_at_ref(existing, branch):
        return True, existing
    ok, branch_or_error = preserve_local_ref_branch(branch)
    if not ok:
        return False, branch_or_error
    return True, branch_or_error

def _run_git_resilient(cmd, **kwargs):
    """Run a destructive-checkout git command with index-repair retries."""
    import time
    check = bool(kwargs.pop("check", False))
    _guard_live_repo_destructive_git(list(cmd))
    for attempt in range(5):
        run_kwargs = dict(kwargs)
        run_kwargs.setdefault("capture_output", True)
        run_kwargs.setdefault("text", True)
        result = subprocess.run(cmd, **run_kwargs)
        if result.returncode == 0:
            return result
        if _maybe_repair_git_index(result.stderr):
            time.sleep(0.2)
            continue
        if not check:
            return result
        if attempt == 4:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, output=result.stdout, stderr=result.stderr,
            )
        time.sleep(1)
    return subprocess.run(cmd, check=check, **kwargs)


def _admission_gate_for_unsynced_tree(
    branch: str, reason: str, policy: str, update_intent_target: str,
) -> Optional[Tuple[bool, str]]:
    """Apply unsynced_policy's block/rescue rules for checkout_and_reset.

    Returns ``(False, msg)`` when the reset must stop here, or ``None`` to proceed.
    """
    repo_state = _collect_repo_sync_state()
    dirty_lines = list(repo_state.get("dirty_lines") or [])
    unpushed_lines = list(repo_state.get("unpushed_lines") or [])
    unpushed_needs_rescue = bool(update_intent_target and unpushed_lines)

    # A failed status read or an unconsulted MERGE_HEAD used to read as a clean
    # tree; force the same rescue/block branch a dirty tree takes, matching the
    # fail-closed read already used for the managed-update rollback path.
    status_unreadable = any(
        str(w).startswith("status_error:") for w in (repo_state.get("warnings") or [])
    )
    merge_in_progress = False
    merge_head_unreadable = False
    # Keep the process-free path for normal clones.  Linked worktrees use a
    # .git pointer file, so ask Git for the worktree-specific admin path there.
    git_dir = _git_dir()
    merge_head_path = git_dir / "MERGE_HEAD"
    if git_dir.is_file():
        rc_path, merge_head_rel, _path_err = rescue_git_capture(
            ["git", "rev-parse", "--git-path", "MERGE_HEAD"]
        )
        if rc_path == 0 and merge_head_rel:
            merge_head_path = REPO_DIR / merge_head_rel
        else:
            merge_head_unreadable = True

    # A present file whose content is not a SHA is unreadable, not absent, per
    # the issue's fix direction.
    if merge_head_path.is_file():
        try:
            merge_head_content = merge_head_path.read_text(encoding="utf-8").strip()
        except Exception:
            merge_head_content = ""
        if re.fullmatch(r"[0-9a-fA-F]{7,64}", merge_head_content):
            merge_in_progress = True
        else:
            merge_head_unreadable = True

    if dirty_lines or unpushed_needs_rescue or status_unreadable or merge_in_progress \
            or merge_head_unreadable:
        bits: List[str] = []
        if unpushed_lines and (dirty_lines or unpushed_needs_rescue):
            bits.append(f"unpushed={len(unpushed_lines)}")
        if dirty_lines:
            bits.append(f"dirty={len(dirty_lines)}")
        if status_unreadable:
            bits.append("status_unreadable")
        if merge_in_progress:
            bits.append("merge_in_progress")
        if merge_head_unreadable:
            bits.append("merge_head_unreadable")
        detail = ", ".join(bits) if bits else "unsynced"
        rescue_info: Dict[str, Any] = {}
        if policy in {"rescue_and_block", "rescue_and_reset"}:
            try:
                rescue_info = _create_rescue_snapshot(
                    branch=branch, reason=reason, repo_state=repo_state)
            except Exception as e:
                rescue_info = {"error": repr(e)}
            if policy == "rescue_and_reset" and rescue_info.get("error"):
                msg = (
                    f"Reset blocked ({detail}) because rescue snapshot failed: "
                    f"{rescue_info.get('error')}. Local changes were left untouched."
                )
                append_jsonl(
                    DRIVE_ROOT / "logs" / "supervisor.jsonl",
                    {
                        "ts": utc_now_iso(),
                        "type": "reset_blocked_rescue_failed",
                        "target_branch": branch, "reason": reason, "policy": policy,
                        "current_branch": repo_state.get("current_branch"),
                        "dirty_count": len(dirty_lines),
                        "unpushed_count": len(unpushed_lines),
                        "dirty_preview": dirty_lines[:20],
                        "unpushed_preview": unpushed_lines[:20],
                        "warnings": list(repo_state.get("warnings") or []),
                        "rescue": rescue_info,
                        "incomplete_reason": "snapshot_error",
                    },
                )
                return False, msg
            if policy == "rescue_and_reset" and rescue_info.get("diff_error"):
                msg = (
                    f"Reset blocked ({detail}) because rescue diff capture failed: "
                    f"{rescue_info.get('diff_error')}. Local changes were left untouched."
                )
                append_jsonl(
                    DRIVE_ROOT / "logs" / "supervisor.jsonl",
                    {
                        "ts": utc_now_iso(),
                        "type": "reset_blocked_rescue_incomplete",
                        "target_branch": branch, "reason": reason, "policy": policy,
                        "current_branch": repo_state.get("current_branch"),
                        "dirty_count": len(dirty_lines),
                        "unpushed_count": len(unpushed_lines),
                        "dirty_preview": dirty_lines[:20],
                        "unpushed_preview": unpushed_lines[:20],
                        "warnings": list(repo_state.get("warnings") or []),
                        "rescue": rescue_info,
                        "incomplete_reason": "diff_error",
                    },
                )
                return False, msg
            untracked_rescue_error = _rescue_untracked_incomplete(rescue_info)
            if policy == "rescue_and_reset" and untracked_rescue_error:
                msg = (
                    f"Reset blocked ({detail}) because untracked-file rescue was incomplete: "
                    f"{untracked_rescue_error}. Local changes were left untouched."
                )
                append_jsonl(
                    DRIVE_ROOT / "logs" / "supervisor.jsonl",
                    {
                        "ts": utc_now_iso(),
                        "type": "reset_blocked_rescue_incomplete",
                        "target_branch": branch, "reason": reason, "policy": policy,
                        "current_branch": repo_state.get("current_branch"),
                        "dirty_count": len(dirty_lines),
                        "unpushed_count": len(unpushed_lines),
                        "dirty_preview": dirty_lines[:20],
                        "unpushed_preview": unpushed_lines[:20],
                        "warnings": list(repo_state.get("warnings") or []),
                        "rescue": rescue_info,
                        "incomplete_reason": "untracked_rescue",
                        "incomplete_detail": untracked_rescue_error,
                    },
                )
                return False, msg
        rescue_suffix = ""
        rescue_path = str(rescue_info.get("path") or "").strip()
        if rescue_path:
            rescue_suffix = f" Rescue saved to {rescue_path}."
        elif policy in {"rescue_and_block", "rescue_and_reset"} and rescue_info.get("error"):
            rescue_suffix = f" Rescue failed: {rescue_info.get('error')}."

        if policy in {"block", "rescue_and_block"}:
            msg = f"Reset blocked ({detail}) to protect local changes.{rescue_suffix}"
            append_jsonl(
                DRIVE_ROOT / "logs" / "supervisor.jsonl",
                {
                    "ts": utc_now_iso(),
                    "type": "reset_blocked_unsynced_state",
                    "target_branch": branch, "reason": reason, "policy": policy,
                    "current_branch": repo_state.get("current_branch"),
                    "dirty_count": len(dirty_lines),
                    "unpushed_count": len(unpushed_lines),
                    "dirty_preview": dirty_lines[:20],
                    "unpushed_preview": unpushed_lines[:20],
                    "warnings": list(repo_state.get("warnings") or []),
                    "rescue": rescue_info,
                },
            )
            return False, msg

        append_jsonl(
            DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": utc_now_iso(),
                "type": "reset_unsynced_rescued_then_reset",
                "target_branch": branch, "reason": reason, "policy": policy,
                "current_branch": repo_state.get("current_branch"),
                "dirty_count": len(dirty_lines),
                "unpushed_count": len(unpushed_lines),
                "dirty_preview": dirty_lines[:20],
                "unpushed_preview": unpushed_lines[:20],
                "warnings": list(repo_state.get("warnings") or []),
                "rescue": rescue_info,
            },
        )
    return None


def checkout_and_reset(branch: str, reason: str = "unspecified",
                       unsynced_policy: str = "ignore") -> Tuple[bool, str]:
    managed_meta = _read_managed_repo_meta()
    fetch_remote = ""
    target_ref = ""
    pin_bundle_sha = _pin_to_bundle_sha_on_bootstrap(reason, managed_meta)
    update_intent = _read_update_intent()
    update_intent_target = ""
    intent_keep_branch = ""
    if managed_meta and not pin_bundle_sha and update_intent:
        intent_branch = str(update_intent.get("branch") or BRANCH_DEV)
        intent_sha = str(update_intent.get("target_sha") or "").strip()
        if intent_branch == branch:
            from supervisor.update_merge import read_update_tx_strict

            tx_status, update_tx = read_update_tx_strict()
            tx_phase = str(update_tx.get("phase") or "")
            tx_matches = bool(
                tx_status == "valid"
                and tx_phase in {"applying_replace", "pending_boot_smoke"}
                and str(update_tx.get("target_sha") or "").strip() == intent_sha
                and str(update_tx.get("pre_update_branch") or BRANCH_DEV) == branch
            )
            rc_intent = -1
            if intent_sha:
                rc_intent, _sha_out, _sha_err = git_capture(
                    ["git", "rev-parse", "--verify", f"{intent_sha}^{{commit}}"]
                )
            constitution_ok = bool(
                tx_matches
                and intent_sha
                and rc_intent == 0
                and _update_source.official_ref_has_constitution(
                    intent_sha, repo_dir=REPO_DIR
                )
            )
            if constitution_ok:
                update_intent_target = intent_sha
                target_ref = intent_sha
                intent_keep_branch = str(update_intent.get("keep_branch") or "").strip()
            else:
                cleared = _clear_update_intent()
                append_jsonl(
                    DRIVE_ROOT / "logs" / "supervisor.jsonl",
                    {
                        "ts": utc_now_iso(),
                        "type": "managed_update_intent_invalid",
                        "target_branch": branch,
                        "target_sha": intent_sha,
                        "tx_status": tx_status,
                        "tx_phase": tx_phase,
                        "tx_target_sha": str(update_tx.get("target_sha") or ""),
                        "cleared": cleared,
                    },
                )
                detail = intent_sha[:12] if intent_sha else "missing SHA"
                return False, (
                    f"Managed update intent is invalid ({detail}); checkout was left unchanged. "
                    + ("The marker was cleared." if cleared else "The marker could not be cleared.")
                )
    if not managed_meta and not pin_bundle_sha and _has_remote("origin"):
        fetch_remote = "origin"

    if fetch_remote:
        rc, _, err = git_capture(["git", "fetch", fetch_remote])
        if rc != 0:
            msg = f"git fetch {fetch_remote} failed: {err or 'unknown error'}"
            append_jsonl(
                DRIVE_ROOT / "logs" / "supervisor.jsonl",
                {
                    "ts": utc_now_iso(),
                    "type": "reset_fetch_failed",
                    "target_branch": branch, "reason": reason, "error": msg,
                    "remote": fetch_remote,
                    "continuing_local_reset": True,
                },
            )
            log.warning("%s; continuing with local reset for branch %s", msg, branch)

    policy = str(unsynced_policy or "ignore").strip().lower()
    if policy not in {"ignore", "block", "rescue_and_block", "rescue_and_reset"}:
        policy = "ignore"

    if policy != "ignore":
        admission_result = _admission_gate_for_unsynced_tree(
            branch, reason, policy, update_intent_target)
        if admission_result is not None:
            return admission_result

    remote_ref_exists = False
    if target_ref:
        remote_ref_exists = subprocess.run(
            ["git", "rev-parse", "--verify", target_ref],
            cwd=str(REPO_DIR),
            capture_output=True,
        ).returncode == 0

    if remote_ref_exists:
        if update_intent_target:
            preserve_ok, preserve_msg = _preserve_branch_for_official_reset(
                branch, target_ref, update_intent,
            )
            if not preserve_ok:
                return False, f"Could not preserve local branch before official update: {preserve_msg}"
            if preserve_msg and preserve_msg != intent_keep_branch:
                append_jsonl(
                    DRIVE_ROOT / "logs" / "supervisor.jsonl",
                    {
                        "ts": utc_now_iso(),
                        "type": "ui_update_preserved_late_head",
                        "target_branch": branch,
                        "reason": reason,
                        "target_ref": target_ref,
                        "keep_branch": preserve_msg,
                    },
                )
            _run_git_resilient(["git", "reset", "--hard", "HEAD"], cwd=str(REPO_DIR), check=True)
            _run_git_resilient(["git", "clean", "-fd"], cwd=str(REPO_DIR), check=True)
        _run_git_resilient(["git", "checkout", "-B", branch, target_ref], cwd=str(REPO_DIR), check=True)
        if update_intent_target:
            _run_git_resilient(["git", "reset", "--hard", target_ref], cwd=str(REPO_DIR), check=True)
        _run_git_resilient(["git", "clean", "-fd"], cwd=str(REPO_DIR), check=True)
    else:
        rc_local = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            cwd=str(REPO_DIR), capture_output=True,
        ).returncode

        if rc_local != 0:
            _run_git_resilient(["git", "reset", "--hard", "HEAD"], cwd=str(REPO_DIR), check=True)
            _run_git_resilient(["git", "clean", "-fd"], cwd=str(REPO_DIR), check=True)
            # §6 (same detached-HEAD class as BUG1): `-b` with check=False silently swallowed a
            # "branch already exists" error and proceeded with HEAD possibly detached/wrong;
            # `-B` force-creates the branch at HEAD and check=True raises a real failure.
            _run_git_resilient(["git", "checkout", "-B", branch], cwd=str(REPO_DIR), check=True)
        else:
            if policy == "rescue_and_reset":
                _run_git_resilient(["git", "reset", "--hard", "HEAD"], cwd=str(REPO_DIR), check=True)
                _run_git_resilient(["git", "clean", "-fd"], cwd=str(REPO_DIR), check=True)
            _run_git_resilient(["git", "checkout", branch], cwd=str(REPO_DIR), check=True)
            _run_git_resilient(["git", "reset", "--hard", "HEAD"], cwd=str(REPO_DIR), check=True)
            if policy == "rescue_and_reset":
                _run_git_resilient(["git", "clean", "-fd"], cwd=str(REPO_DIR), check=True)

    # Checkout may not update mtimes; remove stale bytecode.
    for p in REPO_DIR.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    st = load_state()
    st["current_branch"] = branch
    st["current_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(REPO_DIR),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    save_state(st)
    if update_intent_target and st["current_sha"] != update_intent_target:
        return False, f"Update intent checkout landed on {st['current_sha']} but expected {update_intent_target}"
    if pin_bundle_sha:
        _clear_bootstrap_pin_marker()
    if update_intent_target and str(reason or "") != "ui_update_apply":
        _clear_update_intent()
    return True, "ok"

def sync_runtime_dependencies(reason: str) -> Tuple[bool, str]:
    if getattr(sys, 'frozen', False):
        log.info("Skipping pip install in frozen (PyInstaller) mode — deps are bundled.")
        return True, "frozen:bundled"

    from ouroboros.platform_layer import pip_install_target_args

    req_path = REPO_DIR / "requirements-runtime.lock"
    if not req_path.exists():
        # Preserve upgrades from managed repositories created before uv locks.
        req_path = REPO_DIR / "requirements.txt"
    # The sixth and last pip call site. On a packaged install `sys.executable` IS the
    # bundled interpreter, so an unflagged install wrote into the signed bundle.
    cmd: List[str] = [sys.executable, "-m", "pip", "install", "-q",
                      *pip_install_target_args(sys.executable)]
    source = ""
    if req_path.exists():
        cmd += ["-r", str(req_path)]
        source = f"requirements:{req_path}"
    else:
        cmd += ["openai>=1.0.0", "requests"]
        source = "fallback:minimal"
    try:
        from ouroboros.platform_layer import kill_process_tree, subprocess_new_group_kwargs
        from ouroboros.tools.shell import _active_subprocesses, _subprocess_lock

        proc = subprocess.Popen(
            cmd, cwd=str(REPO_DIR), **subprocess_new_group_kwargs()
        )
        with _subprocess_lock:
            _active_subprocesses.add(proc)
        try:
            returncode = proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            kill_process_tree(proc)
            proc.wait(timeout=10)
            raise
        finally:
            with _subprocess_lock:
                _active_subprocesses.discard(proc)
        if returncode != 0:
            raise subprocess.CalledProcessError(returncode, cmd)
        append_jsonl(
            DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": utc_now_iso(),
                "type": "deps_sync_ok", "reason": reason, "source": source,
            },
        )
        return True, source
    except Exception as e:
        msg = repr(e)
        append_jsonl(
            DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": utc_now_iso(),
                "type": "deps_sync_error", "reason": reason, "source": source, "error": msg,
            },
        )
        return False, msg


def import_test() -> Dict[str, Any]:
    if getattr(sys, 'frozen', False):
        log.info("Skipping import_test in frozen (PyInstaller) mode — modules are bundled.")
        return {"ok": True, "skipped": "frozen"}

    r = subprocess.run(
        [sys.executable, "-c", "import ouroboros, ouroboros.agent; print('import_ok')"],
        cwd=str(REPO_DIR),
        capture_output=True, text=True,
    )
    return {"ok": (r.returncode == 0), "stdout": r.stdout, "stderr": r.stderr,
            "returncode": r.returncode}

def safe_restart(
    reason: str,
    unsynced_policy: str = "rescue_and_reset",
) -> Tuple[bool, str]:
    """Checkout dev, sync deps, import-test, then fall back to stable if needed.

    ``OUROBOROS_DISABLE_MANAGED_UPDATES=1`` is the stand lever: it keeps the deps
    sync and the import test but skips the checkout, so a stand pinned to one sha
    stays on it. This is the choke point EVERY unrequested tree move goes through
    (bootstrap, owner restart, agent restart) — the local-dev bootstrap branch in
    server.py only covered the first of the three. An explicit owner version
    change (Update / Rollback) calls ``checkout_and_reset`` directly and is
    deliberately still honoured: that one the operator asked for.
    """
    if str(os.environ.get("OUROBOROS_DISABLE_MANAGED_UPDATES", "") or "").strip() == "1":
        append_jsonl(
            DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {"ts": utc_now_iso(), "type": "managed_checkout_disabled",
             "reason": reason, "target_branch": BRANCH_DEV},
        )
        deps_ok, deps_msg = sync_runtime_dependencies(reason=reason)
        if not deps_ok:
            return False, f"Failed deps with managed checkout disabled: {deps_msg}"
        t = import_test()
        if t["ok"]:
            return True, "OK: managed checkout disabled — staying on the current checkout"
        return False, f"Import test failed with managed checkout disabled (rc={t.get('returncode', -1)})"

    ok, err = checkout_and_reset(BRANCH_DEV, reason=reason, unsynced_policy=unsynced_policy)
    if not ok:
        return False, f"Failed checkout {BRANCH_DEV}: {err}"

    deps_ok, deps_msg = sync_runtime_dependencies(reason=reason)
    if not deps_ok:
        return False, f"Failed deps for {BRANCH_DEV}: {deps_msg}"

    t = import_test()
    if t["ok"]:
        return True, f"OK: {BRANCH_DEV}"

    append_jsonl(
        DRIVE_ROOT / "logs" / "supervisor.jsonl",
        {
            "ts": utc_now_iso(),
            "type": "safe_restart_dev_import_failed",
            "reason": reason,
            "branch": BRANCH_DEV,
            "stdout": t.get("stdout", ""),
            "stderr": t.get("stderr", ""),
            "returncode": t.get("returncode", -1),
        },
    )

    ok_s, err_s = checkout_and_reset(
        BRANCH_STABLE,
        reason=f"{reason}_fallback_stable",
        unsynced_policy="rescue_and_reset",
    )
    if not ok_s:
        return False, f"Failed checkout {BRANCH_STABLE}: {err_s}"

    deps_ok_s, deps_msg_s = sync_runtime_dependencies(reason=f"{reason}_fallback_stable")
    if not deps_ok_s:
        return False, f"Failed deps for {BRANCH_STABLE}: {deps_msg_s}"

    t2 = import_test()
    if t2["ok"]:
        return True, f"OK: fell back to {BRANCH_STABLE}"

    return False, "Both branches failed import (dev and stable)"


# The managed-update status/preparation surface lives in
# supervisor/git_ops_updates.py (G1 split); re-exported because callers/tests
# address it through the git_ops facade (cycle-free: the leaf imports git_ops
# only at call time through its _go() handle).
from supervisor.git_ops_updates import (  # noqa: E402,F401
    compute_managed_update_status,
    ensure_official_update_remote,
    list_commits,
    list_official_update_tags,
    list_versions,
    prepare_managed_update,
)


# Owner recovery surface lives in supervisor/update_recovery.py; re-exported because
# callers/tests address it through the git_ops facade (cycle-free: update_recovery
# imports git_ops only inside functions).
from supervisor.update_recovery import promote_branch_exact, rollback_to_version  # noqa: E402,F401


# The personal persistence remote (`origin`) surface lives in
# supervisor/git_ops_remotes.py (G1 split); re-exported because callers/tests
# address it through the git_ops facade (cycle-free: the leaf imports git_ops
# only at call time through its _go() handle).
from supervisor.git_ops_remotes import (  # noqa: E402,F401
    _configure_credential_helper,
    configure_personal_remote,
    configure_remote,
    push_to_remote,
)
