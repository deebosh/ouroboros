"""Managed-update status, official tags and update preparation, split out of
``supervisor/git_ops.py`` (module-size discipline, v7 G1 split).

Owns the read side of the managed-update surface — version/commit listings, the
official update remote, the UI Update panel divergence status — and the explicit
hard-reset preparation that arms the update intent against an exact disclosure.
The parent keeps the rebindable module state (``init`` REBINDS REPO_DIR/BRANCH_*
and friends), the capture plumbing, the marker/meta probes and the update_source
bindings, and re-exports every name here, so ``supervisor.git_ops`` stays the
one public surface. Parent members and rebindable globals are read through the
call-time handle ``_go()`` — never a from-import, which would freeze the binding
this module saw at import time.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple



def _go():
    """The parent module, read at call time.

    ``supervisor.git_ops`` owns the rebindable module state (``init`` REBINDS
    REPO_DIR, DRIVE_ROOT and BRANCH_*) and the helpers tests monkeypatch on the
    parent (``git_capture``, ``_managed_update_target``, ``git_fetch_bounded``,
    the sibling re-exports). Reading them through the module keeps one binding:
    a from-import here would freeze the value this module saw at import time.
    """
    from supervisor import git_ops

    return git_ops


# The parent's logger name is pinned so moved log records keep their `%(name)s`
# in server.log/stdout — the same logger object the parent binds.
log = logging.getLogger("supervisor.git_ops")


def list_versions(max_count: int = 50) -> List[Dict[str, Any]]:
    """Return list of annotated git tags sorted newest-first."""
    rc, raw, _ = _go().git_capture([
        "git", "tag", "-l", "--sort=-creatordate",
        "--format=%(refname:short)\t%(creatordate:iso-strict)\t%(subject)",
    ])
    if rc != 0 or not raw.strip():
        return []
    versions: List[Dict[str, Any]] = []
    for line in raw.splitlines()[:max_count]:
        parts = line.split("\t", 2)
        if len(parts) >= 1:
            versions.append({
                "tag": parts[0],
                "date": parts[1] if len(parts) > 1 else "",
                "message": parts[2] if len(parts) > 2 else "",
            })
    return versions


def list_commits(max_count: int = 30) -> List[Dict[str, Any]]:
    """Return recent commits on current branch."""
    rc, raw, _ = _go().git_capture([
        "git", "log", f"--max-count={max_count}",
        "--format=%H\t%h\t%ai\t%s",
    ])
    if rc != 0 or not raw.strip():
        return []
    commits: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        parts = line.split("\t", 3)
        if len(parts) >= 4:
            commits.append({
                "sha": parts[0], "short_sha": parts[1],
                "date": parts[2], "message": parts[3],
            })
    return commits


def ensure_official_update_remote() -> Tuple[bool, str]:
    """Ensure the managed update remote points at the official Ouroboros repository."""
    # Honor the manifest-selected managed remote name (default "managed") so the
    # repaired/added remote matches the one _managed_update_target fetches from.
    remote_name = _go()._managed_remote_name()
    remotes = _go()._list_remotes()
    if remote_name in remotes:
        rc, _out, err = _go().git_capture(["git", "remote", "set-url", remote_name, _go().OFFICIAL_UPDATE_REMOTE_URL])
    else:
        rc, _out, err = _go().git_capture(["git", "remote", "add", remote_name, _go().OFFICIAL_UPDATE_REMOTE_URL])
    return rc == 0, err


def list_official_update_tags(max_count: int = 30) -> List[Dict[str, Any]]:
    """Return official tags from the official managed remote, separate from local/user tags."""
    remote_name = _go()._managed_remote_name()
    if not _go()._has_remote(remote_name):
        return []
    rc, raw, _err = _go()._git_network_bounded([
        "ls-remote", "--tags", "--refs", "--sort=-version:refname",
        remote_name, "refs/tags/v*",
    ])
    if rc != 0:
        return []
    tags: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        tags.append({
            "tag": parts[1].rsplit("/", 1)[-1],
            "sha": parts[0],
            "source": "official",
        })
        if len(tags) >= max_count:
            break
    return tags


def compute_managed_update_status(fetch: bool = False) -> Dict[str, Any]:
    """Return current managed-remote divergence for the UI Update panel."""
    branch_dev, _branch_stable = _go().managed_branch_defaults()
    remote_name, remote_branch, branch_ref = _go()._managed_update_target()
    from ouroboros.update_channels import get_update_channel

    update_channel = get_update_channel()
    official_remote_ok = True
    official_remote_err = ""
    if fetch and remote_name:
        official_remote_ok, official_remote_err = _go().ensure_official_update_remote()
    state: Dict[str, Any] = {
        "managed": bool(_go()._read_managed_repo_meta()),
        "remote": remote_name,
        "remote_branch": remote_branch,
        "target_ref": branch_ref,
        "update_channel": update_channel,
        "current_branch": "unknown",
        "current_sha": "",
        "current_short_sha": "",
        "latest_sha": "",
        "latest_short_sha": "",
        "latest_message": "",
        "ahead": 0,
        "behind": 0,
        "dirty": False,
        "dirty_count": 0,
        "dirty_preview": [],
        "warnings": [],
        "check_ok": None if not fetch else False,
        "available": False,
        "safe_to_apply": False,
    }
    if not official_remote_ok:
        state["warnings"].append(f"remote_config_error:{official_remote_err or 'unknown error'}")
        state["managed"] = False
        state["available"] = False
        state["safe_to_apply"] = False
        return state

    # Fetch before recording the local base: a long network call gives a live
    # writer time to advance HEAD, and the returned SHA becomes the apply pin.
    fetch_failed = False
    if fetch and remote_name:
        rc, _out, err = _go().git_fetch_bounded(remote_name)
        if rc != 0:
            fetch_failed = True
            state["warnings"].append(f"fetch_error:{err or 'unknown error'}")

    rc, branch, err = _go().git_capture(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if rc == 0:
        state["current_branch"] = branch
    elif err:
        state["warnings"].append(f"branch_error:{err}")

    rc, sha, err = _go().git_capture(["git", "rev-parse", "HEAD"])
    if rc == 0:
        state["current_sha"] = sha
        state["current_short_sha"] = sha[:8]
    elif err:
        state["warnings"].append(f"head_error:{err}")

    rc, dirty, err = _go().git_capture(["git", "status", "--porcelain"])
    if rc == 0:
        dirty_lines = [line for line in dirty.splitlines() if line.strip()]
        state["dirty"] = bool(dirty_lines)
        state["dirty_count"] = len(dirty_lines)
        state["dirty_preview"] = dirty_lines[:20]
    else:
        state["warnings"].append(f"status_error:{err or 'unknown error'}")
        return state

    if fetch_failed:
        return state
    if not branch_ref:
        state["warnings"].append("managed_updates_unavailable")
        return state
    if state["current_branch"] != branch_dev:
        state["warnings"].append(f"managed_update_requires_branch:{branch_dev}")
        return state
    if not fetch:
        cached_target_ref, _cached_target_sha, _cached_target_error = (
            _go()._resolve_managed_update_target(
                remote_name, remote_branch, branch_ref, update_channel
            )
        )
        if cached_target_ref:
            state["target_ref"] = cached_target_ref
        state["warnings"].append("official_status_requires_check")
        try:
            cache = (_go().load_state() or {}).get("managed_update_cache") or {}
            identity_matches = all(
                str(cache.get(key) or "") == str(state.get(key) or "")
                for key in ("remote", "remote_branch", "target_ref", "update_channel")
            )
            cached_sha = str(cache.get("latest_sha") or "")
            consumed = bool(cached_sha and cached_sha == state["current_sha"])
            if cached_sha and state["current_sha"] and not consumed:
                consumed = _go().git_capture(
                    ["git", "merge-base", "--is-ancestor", cached_sha, state["current_sha"]]
                )[0] == 0
            counts_rc, cached_counts, _counts_error = _go().git_capture(
                ["git", "rev-list", "--left-right", "--count", f"HEAD...{cached_sha}"]
            ) if cached_sha else (1, "", "")
            try:
                cached_ahead, cached_behind = (
                    (int(part) for part in cached_counts.split()) if counts_rc == 0 else (0, 0)
                )
            except Exception:
                counts_rc, cached_ahead, cached_behind = 1, 0, 0
            if (
                identity_matches
                and cache.get("available")
                and cached_sha
                and not consumed
                and counts_rc == 0
                and cached_behind > 0
            ):
                state.update({
                    "available": True,
                    "safe_to_apply": cached_ahead == 0 and not state["dirty"],
                    "latest_sha": cached_sha,
                    "latest_short_sha": str(cache.get("latest_short_sha") or ""),
                    "latest_message": str(cache.get("latest_message") or ""),
                    "behind": cached_behind,
                    "ahead": cached_ahead,
                    "checked_at": str(cache.get("checked_at") or ""),
                    "from_cache": True,
                })
        except Exception:
            log.debug("managed update status cache overlay failed", exc_info=True)
        return state
    if not _go()._has_remote(remote_name):
        state["warnings"].append(f"missing_remote:{remote_name}")
        return state

    target_ref, latest_sha, target_error = _go()._resolve_managed_update_target(
        remote_name, remote_branch, branch_ref, update_channel
    )
    if not target_ref or not latest_sha:
        state["warnings"].append(f"target_ref_error:{target_error or branch_ref}")
        return state
    state["target_ref"] = target_ref
    state["latest_sha"] = latest_sha
    state["latest_short_sha"] = latest_sha[:8]

    rc, latest_msg, _err = _go().git_capture(["git", "log", "-1", "--format=%s", latest_sha])
    if rc == 0:
        state["latest_message"] = latest_msg

    rc, counts, err = _go().git_capture(["git", "rev-list", "--left-right", "--count", f"HEAD...{latest_sha}"])
    if rc == 0:
        try:
            ahead, behind = (int(part) for part in counts.split())
        except Exception:
            ahead, behind = 0, 0
            state["warnings"].append(f"divergence_parse_error:{counts}")
        else:
            state["check_ok"] = True
        state["ahead"] = ahead
        state["behind"] = behind
        state["available"] = behind > 0
        state["safe_to_apply"] = behind > 0 and ahead == 0 and not state["dirty"]
    elif err:
        state["warnings"].append(f"divergence_error:{err}")
    try:
        from supervisor.state import update_state
        snapshot = {
            key: state.get(key)
            for key in (
                "remote", "remote_branch", "target_ref", "update_channel", "available",
                "safe_to_apply", "latest_sha", "latest_short_sha", "latest_message",
                "behind", "ahead",
            )
        }
        snapshot["checked_at"] = _go().utc_now_iso()
        update_state(lambda saved: saved.__setitem__("managed_update_cache", snapshot))
    except Exception:
        log.debug("managed update status cache save failed", exc_info=True)
    return state
