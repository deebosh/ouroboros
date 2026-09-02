"""Process tools: run_command and run_script."""

from __future__ import annotations

from hashlib import sha256
import json
import logging
import os
import pathlib
import re
import shlex
import stat
import subprocess
import threading
import time
import uuid
from typing import Dict, List

from ouroboros.artifacts import copy_directory_to_task_artifacts, copy_file_to_task_artifacts, record_task_scratch
from ouroboros.platform_layer import bootstrap_process_path, kill_process_tree, scrub_repo_from_pythonpath, subprocess_new_group_kwargs
from ouroboros.process_interpreters import (
    active_node_resolution,
    apply_env_path_prepend,
    interpreter_path_overlay,
)
from ouroboros.config import SETTINGS_DEFAULTS, load_settings
from ouroboros.runtime_mode_policy import (
    is_protected_runtime_path,
)
from ouroboros.tools.commit_gate import _invalidate_advisory
# Export-eligibility policy (extracted module; re-exported names keep call sites
# and tests importing from here working).
from ouroboros.tools.output_export_policy import (  # noqa: F401 — re-exported for call sites/tests
    _changed_path_covers,
    _protected_output_source_reason,
    _scan_directory_output_members,
    _sensitive_output_component_reason,
)
from ouroboros.tools.result_envelope import annotate as _annotate_result
from ouroboros.shell_parse import is_absolute_path_text, recover_stringified_argv
from ouroboros.tools.registry import (
    ToolContext,
    ToolEntry,
)
from ouroboros.tools import shell_audit as _shell_audit
from ouroboros.tools.deliverables_shell import lexical_user_files_block_reason
from ouroboros.tools.shell_audit import (
    _UNDECLARED_OUTPUTS_MARKER,
    _mentioned_user_file_outputs_without_declaration,
    _presence_allows_user_output,
    _allowed_output_roots,
)

# Preserve private module attributes used by existing callers/tests while the
# implementation lives in the extracted audit module.
_EMBEDDED_OUTPUT_PATH_RE = _shell_audit._EMBEDDED_OUTPUT_PATH_RE
_OUTPUT_CALL_PATH_RE = _shell_audit._OUTPUT_CALL_PATH_RE
_OUTPUT_REDIRECT_PATH_RE = _shell_audit._OUTPUT_REDIRECT_PATH_RE
_OUTPUT_STAT_SLACK_SEC = _shell_audit._OUTPUT_STAT_SLACK_SEC
_USER_FILE_OPEN_WRITE_CALL_RE = _shell_audit._USER_FILE_OPEN_WRITE_CALL_RE
_USER_FILE_REDIRECT_RE = _shell_audit._USER_FILE_REDIRECT_RE
_USER_FILE_WRITE_CALL_RE = _shell_audit._USER_FILE_WRITE_CALL_RE
from ouroboros.tool_access import (
    ResolvedResourceBinding,
    _deliverables_root_lexical,
    _deliverables_root_lexical_alias,
    _lexical_path_is_relative_to_casefold,
    _path_is_relative_to_casefold,
    build_resolved_resource_binding,
    path_is_relative_to,
    resource_root_path,
    shell_cwd_block_message,
    user_files_path_block_reason,
)
from ouroboros.utils import safe_relpath
from ouroboros.tools.shell_grep_argv import (  # noqa: F401 - moved to its own module; compatibility re-export
    _GREP_BACKSLASH_PIPE_PATTERN,
    _GREP_REGEX_MODE_FLAGS,
    _GREP_TOOLS,
    _NO_MATCH_EXIT_TOOLS,
    _grep_has_explicit_regex_mode,
    _is_search_no_match,
    _maybe_autocorrect_grep_backslash_pipe,
)
from ouroboros.tools.shell_and_chain import _maybe_split_single_element_and_chain, _maybe_wrap_single_element_pipeline
from ouroboros.tools.shell_output_fingerprint import (  # noqa: F401 - moved to its own module; compatibility re-export
    _OUTPUT_DIR_MAX_BYTES,
    _OUTPUT_DIR_MAX_FILES,
    _bounded_directory_fingerprint,
    _directory_fingerprint_from_entries,
    _fingerprint_output,
)
from ouroboros.deadline_utils import deadline_remaining_sec
from ouroboros.workspace_executor import execute as executor_execute
from ouroboros.workspace_executor import executor_ref_from_ctx
from ouroboros.workspace_executor import map_backend_path as executor_map_backend_path
from ouroboros.workspace_executor import map_backend_path_lexical as executor_map_backend_path_lexical
from ouroboros.workspace_executor import map_host_path as executor_map_host_path

log = logging.getLogger(__name__)
# Tracked process groups let panic kill descendant trees too.
_active_subprocesses: set = set()
_subprocess_lock = threading.Lock()
_RUN_SHELL_DEFAULT_TIMEOUT_SEC = 360
_CONTROL_DIR_BACKUP_MAX_BYTES = 5 * 1024 * 1024
from ouroboros.tools.output_export_policy import (  # noqa: E402 — constants SSOT moved with the policy
    _OUTPUT_DIR_MAX_BYTES,
    _OUTPUT_DIR_MAX_FILES,
)


def _tracked_subprocess_run(cmd, **kwargs):
    """subprocess.run replacement with process-tree tracking. When capturing TEXT
    output, decode tolerantly (errors='replace') so binary stdout/stderr (a MIPS
    interpreter, a DOOM framebuffer, raw bytes) surfaces as readable text instead
    of raising UnicodeDecodeError and collapsing the whole call into a
    shell_error."""
    timeout = kwargs.pop("timeout", None)
    if kwargs.get("text") or kwargs.get("universal_newlines"):
        kwargs.setdefault("errors", "replace")
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    kwargs.update(subprocess_new_group_kwargs())
    proc = subprocess.Popen(cmd, **kwargs)
    with _subprocess_lock:
        _active_subprocesses.add(proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        proc.wait(timeout=5)
        raise
    finally:
        with _subprocess_lock:
            _active_subprocesses.discard(proc)


def _kill_process_group(proc):
    """Kill a subprocess tree."""
    kill_process_tree(proc)


def kill_all_tracked_subprocesses():
    """Kill all tracked subprocess trees on panic."""
    with _subprocess_lock:
        procs = list(_active_subprocesses)
    for proc in procs:
        _kill_process_group(proc)
    with _subprocess_lock:
        _active_subprocesses.clear()


def _shell_env_for_cwd(ctx: ToolContext, work_dir: pathlib.Path) -> "dict | None":
    """For a command whose cwd is OUTSIDE the Ouroboros system repo (an external
    workspace / target project, e.g. SWE-bench dig-direct ``/app``), return an
    env copy with the repo dir scrubbed from ``PYTHONPATH`` so the target cannot
    shadow-import Ouroboros's own modules (R2). ``ctx.repo_dir`` stays pinned to
    the Ouroboros repo even in workspace mode, so this is the authoritative
    in-repo test. Returns ``None`` for commands inside the system repo (Ouroboros
    tooling legitimately imports itself) so they inherit ``os.environ``."""
    try:
        system_repo = pathlib.Path(getattr(ctx, "repo_dir")).resolve(strict=False)
        wd = pathlib.Path(work_dir).resolve(strict=False)
    except Exception:
        return None
    try:
        in_repo = wd == system_repo or wd.is_relative_to(system_repo)
    except AttributeError:  # pragma: no cover - py<3.9
        in_repo = str(wd) == str(system_repo) or str(wd).startswith(str(system_repo) + os.sep)
    if in_repo:
        return None
    return scrub_repo_from_pythonpath(dict(os.environ), system_repo)


def _resolve_effective_timeout(
    default_timeout_sec: int,
    ctx: ToolContext | None = None,
    override_sec: int | None = None,
) -> int:
    """Resolve the effective per-command timeout as ONE normalized pipeline:
    resolve the REQUESTED value from a single precedence chain (per-call
    ``override_sec`` > env ``OUROBOROS_TOOL_TIMEOUT_SEC`` > settings.json > config
    ``SETTINGS_DEFAULTS`` > the in-code last-resort ``default_timeout_sec``), then
    apply the per-call ceiling, then clamp toward the remaining task-deadline budget
    (60s floor when a deadline exists), then floor at 1s. The outer budget loop
    remains the hard deadline enforcer.

    Hygiene fix (SSOT): the prior code skipped an env/settings value EQUAL to the
    config default (``!= default_setting``), so ``OUROBOROS_TOOL_TIMEOUT_SEC=600``
    (= the SETTINGS_DEFAULTS value) silently fell through to the in-code 360s default.
    The configured value is now honored regardless of equality, and env/settings
    values no longer BYPASS the ceiling/deadline clamp. RELEASE NOTE: installs that
    relied on the buggy effective 360s now get the configured 600s — a foreground
    command may hold the task longer (still bounded by ceiling + task deadline).
    """
    from ouroboros.config import get_per_call_timeout_ceiling_sec

    # 1. Resolve the REQUESTED timeout from a single precedence chain.
    requested: int | None = None
    if override_sec is not None:
        try:
            ov = int(override_sec)
        except (TypeError, ValueError):
            ov = 0
        if ov > 0:
            requested = ov
    if requested is None:
        raw = str(os.environ.get("OUROBOROS_TOOL_TIMEOUT_SEC", "") or "").strip()
        if raw:
            try:
                v = int(raw)
                if v > 0:
                    requested = v
            except ValueError:
                pass
    if requested is None:
        try:
            settings_val = int(load_settings().get("OUROBOROS_TOOL_TIMEOUT_SEC") or 0)
            if settings_val > 0:
                requested = settings_val
        except Exception:
            pass
    if requested is None:
        cfg_default = int(SETTINGS_DEFAULTS.get("OUROBOROS_TOOL_TIMEOUT_SEC") or 0)
        requested = cfg_default if cfg_default > 0 else int(default_timeout_sec)

    # 2. Per-call ceiling.
    effective = min(requested, get_per_call_timeout_ceiling_sec())

    # 3. Clamp toward the remaining task-deadline budget (60s floor when a deadline exists).
    if ctx is not None:
        remaining = deadline_remaining_sec(ctx)
        if remaining > 0:
            effective = int(max(60, min(effective, remaining * 0.5)))

    # 4. Floor at 1s.
    return max(1, int(effective))


# Typed process-facts channel (R5) seam: ouroboros/tools/process_facts.py.
# Historical private spellings stay as aliases for call sites and tests.
from ouroboros.tools.process_facts import (  # noqa: E402
    active_resolved_runtime as _active_resolved_runtime,
    describe_returncode as _describe_returncode,
    publish_process_facts as _publish_process_facts,
)

def _format_process_output(stdout: str, stderr: str, *, limit: int = 50_000) -> str:
    """Render bounded stdout/stderr sections."""
    stdout_text = str(stdout or "")
    stderr_text = str(stderr or "")
    parts: List[str] = []
    if stdout_text.strip():
        parts.append(f"STDOUT:\n{stdout_text}")
    if stderr_text.strip():
        parts.append(f"STDERR:\n{stderr_text}")
    rendered = "\n\n".join(parts) if parts else "STDOUT:\n(empty)"
    if len(rendered) > limit:
        rendered = rendered[: limit // 2] + "\n...(truncated)...\n" + rendered[-limit // 2 :]
    return rendered


def _resolve_declared_output(
    ctx: ToolContext,
    raw_item: str,
    work_dir: pathlib.Path,
    cwd_root: str = "",
    changed_paths: set[str] | None = None,
    binding: ResolvedResourceBinding | None = None,
) -> tuple[pathlib.Path | None, str]:
    text = str(raw_item or "").strip()
    if not text:
        return None, "empty output path"
    raw = pathlib.Path(text).expanduser()
    executor_ref = executor_ref_from_ctx(ctx)
    if executor_ref is not None and is_absolute_path_text(text) and not text.startswith("~"):
        try:
            lexical_source = executor_map_backend_path_lexical(executor_ref, text)
        except ValueError:
            lexical_source = raw
    elif is_absolute_path_text(text) or text.startswith("~"):
        lexical_source = raw
    else:
        lexical_source = pathlib.Path(work_dir) / safe_relpath(text)
    # is_absolute_path_text (not Path.is_absolute) so a backend output path like
    # "/workspace/out.txt" maps through the executor on Windows too, where
    # Path.is_absolute() is False for drive-less roots.
    if executor_ref is not None and is_absolute_path_text(text) and not text.startswith("~"):
        try:
            source = executor_map_backend_path(executor_ref, text)
        except ValueError:
            source = raw.resolve(strict=False)
    elif is_absolute_path_text(text) or text.startswith("~"):
        source = raw.resolve(strict=False)
    else:
        source = (pathlib.Path(work_dir) / safe_relpath(text)).resolve(strict=False)
    changed = changed_paths or set()

    # Deliverables may be configured inside the active workspace.  Check that
    # physical target before the generic cwd root, otherwise a declared
    # ``workspace/Deliverables/.env`` is labelled active_workspace and skips
    # the user-files hidden/credential and Presence policy.
    try:
        deliverables_root = resource_root_path(ctx, "deliverables")
        deliverables_root_lexical = _deliverables_root_lexical()
        deliverables_root_lexical_alias = _deliverables_root_lexical_alias()
    except Exception:
        deliverables_root = None
        deliverables_root_lexical = None
        deliverables_root_lexical_alias = None
    if deliverables_root is not None:
        try:
            in_deliverables = (
                deliverables_root_lexical is not None
                and (
                    path_is_relative_to(lexical_source, deliverables_root_lexical)
                    or _lexical_path_is_relative_to_casefold(lexical_source, deliverables_root_lexical)
                    or _lexical_path_is_relative_to_casefold(
                        lexical_source, deliverables_root_lexical_alias,
                    )
                    or _lexical_path_is_relative_to_casefold(
                        lexical_source, deliverables_root,
                    )
                )
            )
        except (OSError, TypeError, ValueError):
            in_deliverables = False
        if in_deliverables:
            physical_deliverables = pathlib.Path(deliverables_root).resolve(strict=False)
            if not (
                path_is_relative_to(source, physical_deliverables)
                or _path_is_relative_to_casefold(source, physical_deliverables)
            ):
                return None, (
                    f"Deliverables output escapes its resolved configured root: {text}"
                )
            lexical_reason = lexical_user_files_block_reason(lexical_source)
            if lexical_reason:
                return None, f"protected user_files output {text}: {lexical_reason}"
            reason = user_files_path_block_reason(ctx, source)
            if reason:
                return None, f"protected user_files output {text}: {reason}"
            if not _presence_allows_user_output(ctx, source):
                return None, (
                    "user_files output is outside this presence task's positive "
                    "resource ceiling"
                )
            protected_reason = _protected_output_source_reason(
                ctx, source, "user_files", changed, binding,
            )
            if protected_reason:
                return None, protected_reason
            return source, ""

    for label, root in _allowed_output_roots(ctx, work_dir, cwd_root, binding):
        if not (
            path_is_relative_to(source, root)
            or _path_is_relative_to_casefold(source, root)
        ):
            continue
        if label == "user_files":
            reason = user_files_path_block_reason(ctx, source)
            if reason:
                return None, f"protected user_files output {text}: {reason}"
            if not _presence_allows_user_output(ctx, source):
                return None, (
                    "user_files output is outside this presence task's positive "
                    "resource ceiling"
                )
        protected_reason = _protected_output_source_reason(
            ctx, source, label, changed, binding,
        )
        if protected_reason:
            return None, protected_reason
        return source, ""
    allowed = ", ".join(
        f"{label}={root}"
        for label, root in _allowed_output_roots(ctx, work_dir, cwd_root, binding)
    )
    return None, f"output escapes allowed artifact roots: {text}; allowed_roots: {allowed}"


def _snapshot_declared_outputs(
    ctx: ToolContext,
    outputs: List[str] | None,
    work_dir: pathlib.Path,
    cwd_root: str = "",
    changed_paths: set[str] | None = None,
    binding: ResolvedResourceBinding | None = None,
) -> Dict[str, tuple[bool, int, str]]:
    snapshots: Dict[str, tuple[bool, int, str]] = {}
    for raw_item in outputs or []:
        source, block_reason = _resolve_declared_output(
            ctx,
            str(raw_item or ""),
            work_dir,
            cwd_root=cwd_root,
            changed_paths=changed_paths,
            binding=binding,
        )
        if source is not None and not block_reason:
            snapshots[str(source)] = _fingerprint_output(source)
    return snapshots


def _register_process_outputs(
    ctx: ToolContext,
    outputs: List[str] | None,
    work_dir: pathlib.Path,
    cwd_root: str = "",
    changed_paths: set[str] | None = None,
    before_outputs: Dict[str, tuple[bool, int, str]] | None = None,
    binding: ResolvedResourceBinding | None = None,
) -> tuple[str, bool]:
    """Copy declared command outputs into the task artifact store."""

    if not outputs:
        return "", False
    notes: list[str] = []
    failed = False
    registered = False  # at least one canonical artifact record was actually created
    for raw_item in outputs:
        text = str(raw_item or "").strip()
        source, block_reason = _resolve_declared_output(
            ctx,
            text,
            work_dir,
            cwd_root=cwd_root,
            changed_paths=changed_paths,
            binding=binding,
        )
        if block_reason:
            notes.append(block_reason)
            failed = True
            continue
        if source is None:
            notes.append(f"invalid output: {text}")
            failed = True
            continue
        if not source.exists():
            notes.append(f"missing output: {text}")
            failed = True
            continue
        before = (before_outputs or {}).get(str(source), (False, -1, ""))
        after = _fingerprint_output(source)
        if before[0] and before == after:
            # Present-but-unchanged is NOT a failure (a deterministic re-run, or a
            # command that re-verifies an existing artifact): note it cosmetically
            # and skip re-registration. "Did it actually work?" lives on the
            # objective/review axis, not the tool-execution axis (Bible P5). A
            # genuinely MISSING declared output above stays a blocking failure.
            notes.append(f"unchanged output (cosmetic): {text}")
            continue
        if source.is_file():
            try:
                record = copy_file_to_task_artifacts(ctx, source, kind="process_output")
            except OSError as exc:
                notes.append(f"failed output copy {text}: {type(exc).__name__}: {exc}")
                failed = True
                continue
            if record:
                registered = True
                notes.append(
                    f"registered output {source} -> artifact_store:{record.get('name')} "
                    f"sha256={str(record.get('sha256') or '')[:12]}"
                )
            else:
                notes.append(f"failed output copy {text}: source is not a regular file")
                failed = True
        elif source.is_dir():
            dir_members, _dir_size, blocked_member, skipped_members = _scan_directory_output_members(
                ctx,
                source,
                label=str(cwd_root or "cwd"),
                changed_paths=changed_paths or set(),
                binding=binding,
            )
            if skipped_members:
                # Per-member receipt (D4): the export is PARTIAL, never silently
                # so. The rendered note is bounded; the COMPLETE list goes to
                # the task log so the omission stays resolvable (#447 P1).
                shown = "; ".join(skipped_members[:5])
                more = (
                    f" (+{len(skipped_members) - 5} more; full list in server.log,"
                    f" task {getattr(ctx, 'task_id', '') or '?'})"
                    if len(skipped_members) > 5 else ""
                )
                if len(skipped_members) > 5:
                    log.info(
                        "task %s: directory output %s: full export skip list (%d): %s",
                        getattr(ctx, "task_id", "") or "?",
                        text, len(skipped_members), "; ".join(skipped_members),
                    )
                notes.append(
                    f"skipped {len(skipped_members)} member(s) of directory output {text}: {shown}{more}"
                )
            if blocked_member:
                notes.append(f"blocked directory output: {blocked_member}")
                failed = True
                continue
            if not dir_members:
                notes.append(f"failed directory output copy {text}: no exportable members")
                failed = True
                continue
            try:
                records = copy_directory_to_task_artifacts(
                    ctx,
                    source,
                    kind="process_output_directory",
                    member_paths=dir_members,
                )
            except OSError as exc:
                notes.append(f"failed directory output copy {text}: {type(exc).__name__}: {exc}")
                failed = True
                continue
            if records:
                registered = True
                names = ", ".join(str(record.get("name") or "") for record in records)
                notes.append(f"registered directory output {source} -> artifact_store:{names}")
            else:
                notes.append(f"failed directory output copy {text}: no artifact records")
                failed = True
        else:
            notes.append(f"skipped non-file output: {text}")
            failed = True
    if not notes:
        return "", False
    # Distinguish a CANONICAL artifact registration from a cosmetic-only note (e.g.
    # an unchanged declared output): the downstream artifact_registered detector
    # (outcomes.py / loop_tool_execution.py) keys on the exact "ARTIFACT_OUTPUTS"
    # marker, so a cosmetic note must NOT borrow it — else an unchanged output reads
    # as a real registration / false recovery signal. "ARTIFACT_OUTPUT_NOTE" does
    # not contain the "ARTIFACT_OUTPUTS" substring, so it is correctly ignored.
    if failed:
        prefix = "⚠️ ARTIFACT_OUTPUT_ERROR"
    elif registered:
        prefix = "ARTIFACT_OUTPUTS"
    else:
        prefix = "ARTIFACT_OUTPUT_NOTE"
    return "\n\n" + prefix + ":\n" + "\n".join(f"- {note}" for note in notes), failed


# v6.90.x (submarine unwind) — the DECLARATION-NUDGE marker, deliberately typed
# APART from the real ``ARTIFACT_OUTPUT_ERROR`` registration failure above. The
# command SUCCEEDED (exit_code=0) and this only asks for ``outputs=[...]`` to be
# declared, so its status lands in the v6.57.0 POLICY-DENIAL partition
# (``_outcome_tool_errors._POLICY_DENIAL_STATUSES``) instead of degrading execution
# to ``tool_failure``. The submarine wave-3 incident was exactly this: a moot nudge
# on an already-registered artifact fed the failure record. SSOT for both
# ``run_command`` and ``run_script`` so the two nudges cannot drift apart.
def _executor_can_run_cwd(ctx: ToolContext, work_dir: pathlib.Path) -> bool:
    executor_ref = executor_ref_from_ctx(ctx)
    if executor_ref is None:
        return False
    try:
        executor_map_host_path(executor_ref, pathlib.Path(work_dir).resolve(strict=False))
        return True
    except Exception:
        return False


def _resolve_git_root(path: pathlib.Path) -> pathlib.Path | None:
    try:
        from ouroboros.review_state import discover_repo_root
        root = discover_repo_root(path)
        if not (root / ".git").exists():
            return None
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return root if probe.returncode == 0 and probe.stdout.strip() == "true" else None
    except Exception:
        return None


def _status_snapshot(repo_dir: pathlib.Path | None) -> list[str]:
    if repo_dir is None:
        return []
    return sorted(_get_changed_files(repo_dir))


def _shallow_listing(work_dir: pathlib.Path, cap: int = 5000) -> dict:
    """Bounded immediate-children {name: (mtime_ns, size)} snapshot of a cwd. One
    directory level, capped — NOT a recursive filesystem monitor (R5). Used to
    detect a non-git user_files cwd actually producing a top-level deliverable."""
    out: dict = {}
    try:
        with os.scandir(work_dir) as it:
            for entry in it:
                if len(out) >= cap:
                    break
                try:
                    st = entry.stat(follow_symlinks=False)
                    out[entry.name] = (int(st.st_mtime_ns), int(st.st_size))
                except OSError:
                    continue
    except OSError:
        return {}
    return out


def _user_files_run_had_effect(
    before_changed: list[str],
    after_changed: list[str],
    before_listing: dict | None,
    work_dir: pathlib.Path,
) -> bool:
    """Effect-based gate for the ARTIFACT_AUDIT_GAP nudge (R5): warn only when the
    command produced an OBSERVABLE filesystem change in the cwd, not merely
    because it ran in a user_files cwd. Git-tracked cwd (e.g. dig-direct /app) →
    a status delta (modified or new untracked file). Non-git cwd → a bounded
    shallow immediate-children snapshot delta. A read-only command (ls/cat/grep)
    changes neither and is no longer falsely flagged."""
    if after_changed != before_changed:
        return True
    if before_listing is not None:
        return _shallow_listing(work_dir) != before_listing
    return False


def _protected_runtime_dirty_paths(repo_dir: pathlib.Path) -> list[str]:
    dirty: set[str] = set()
    for cmd in (["git", "diff", "--name-only"], ["git", "diff", "--cached", "--name-only"]):
        try:
            res = subprocess.run(
                cmd,
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0:
                dirty.update(rel for rel in res.stdout.splitlines() if is_protected_runtime_path(rel))
        except Exception:
            pass
    return sorted(dirty)


def _restore_protected_runtime_paths(repo_dir: pathlib.Path, paths: list[str]) -> list[str]:
    restored: list[str] = []
    for rel in sorted(set(paths)):
        try:
            subprocess.run(
                ["git", "reset", "HEAD", "--", rel],
                cwd=str(repo_dir),
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                ["git", "checkout", "--", rel],
                cwd=str(repo_dir),
                capture_output=True,
                timeout=5,
            )
            restored.append(rel)
        except Exception:
            pass
    return restored


# Size-ratchet manifest path. Hardcoded as a module-local constant so this
# helper does not depend on a top-level import of ouroboros.review (which
# pulls a wider module graph: review_helpers, git_status_parse,
# release_sync). The path matches SIZE_RATCHET_MANIFEST_PATH in
# ouroboros/review.py:28; kept inline so shell.py's auto-restore branch
# is self-contained.
_SIZE_RATCHET_MANIFEST_PATH = "ouroboros/size_ratchet_manifest.py"


def _maybe_preserve_size_ratchet_update(
    repo_dir: pathlib.Path, dirty: list[str]
) -> tuple[list[str], str]:
    """Sanctioned size-ratchet manifest update path (ibl-local-29ee0d0e37bc).

    The manifest sits on ``FROZEN_CONTRACT_PATHS`` and the shell auto-restore
    reverts it after every shell command. But the OFFICIAL CI ``size_ratchet``
    lane ALSO blocks on size-ratchet drift, so an agent that gets the
    manifest right should be able to KEEP its edit instead of losing it to a
    reverter — the manifest still has to pass ``commit_reviewed``'s own
    review gates, but it must be allowed to survive the shell-restore step.

    Returns:
        ``([], note)`` — the dirty set is exactly the size-ratchet manifest
            AND the in-tree manifest validates cleanly against HEAD's
            committed authority. The caller suppresses the revert and emits
            ``note`` so the agent sees the preservation.
        ``(dirty, "")`` — any other case: another protected path is also
            dirty, the manifest fails validation, the validator raised, or
            the manifest isn't dirty at all. The caller reverts exactly as
            before. A validator exception is treated as a validation failure
            (fail-soft: the manifest gets reverted, the agent retries through
            the normal reviewed path).
    """
    if dirty != [_SIZE_RATCHET_MANIFEST_PATH]:
        return dirty, ""
    try:
        # Lazy import: ouroboros.review pulls a wider module graph
        # (review_helpers, git_status_parse, release_sync). The cost is
        # paid only when an agent has actually dirtied the manifest, which
        # is the rare path.
        from ouroboros.review import validate_size_ratchet

        violations = validate_size_ratchet(repo_dir)
    except Exception:
        return dirty, ""
    if violations:
        return dirty, ""
    note = (
        "\n\nℹ️ SIZE_RATCHET_MANIFEST_UPDATE_PRESERVED: "
        "'ouroboros/size_ratchet_manifest.py' was modified by a shell command, "
        "but the in-tree change is a valid size-ratchet update against HEAD's "
        "committed authority (no transition violations, exactness holds). "
        "The change was PRESERVED instead of auto-reverted; it must still "
        "pass commit_reviewed's own review gates to land."
    )
    return [], note


_SHELL_BUILTINS = frozenset([
    "cd", "source", ".", "export", "alias", "eval",
    "set", "unset", "pushd", "popd", "read", "ulimit",
])

_SHELL_OPERATORS = frozenset(["&&", "||", "|", ";", ">", ">>", "<", "<<"])
# A redirect GLUED into a single argv element ("2>/dev/null", "2>&1", ">out.log",
# "&>x") — the standalone-operator set above misses these. Anchored at the element
# START so a '>' inside a sed/awk/grep expression ("s/a>b/c/g") is NOT flagged.
# Output redirects keep a permissive glued tail. Input redirects are restricted to
# UNAMBIGUOUS shapes — heredoc/herestring ("<<EOF", "<<<s"), an fd-prefixed input
# ("0<f", "2<&1"), or a bare standalone "<" — because a plain "<word" element is
# indistinguishable from a legitimate literal angle-bracket arg (grep "<div>",
# "<stdin>"), and false-flagging those is worse than missing a rare glued "<file"
# input redirect. Pipes/control operators are deliberately NOT matched (a glued
# '|' is valid regex alternation, grep "a|b").
_GLUED_REDIRECT_RE = re.compile(
    r'^(?:(?:\d+>>?|>>?&?\d*|\d*>&\d*|&>>?)(?:\S.*)?|\d+<\S*|<<\S*|<)$'
)
# Detect shell pipelines STUFFED into a single argv element (e.g.
# `["curl && -s && https://api..."]`). Signature: whitespace-bracketed `&&` or
# `||` inside one element, with non-whitespace on both sides. The whole string
# reaches subprocess as the executable name and dies with a silent
# `[Errno 2] No such file or directory`. Narrow to `&&`/`||` only because (a)
# a bare `|` element is already caught by `_SHELL_OPERATORS.intersection` and
# (b) `|` inside a regex like `grep "a|b"` is legitimate alternation. The
# check is gated by `_SHELL_INTERPRETERS` so `["sh", "-c", "a && b"]` scripts
# pass through (env-ref check at the same cascade location carries the same
# exemption boundary).
_EMBEDDED_SHELL_OP_RE = re.compile(r'\s(?:&&|\|\|)\s')
# A cmd list of length 1 has no "other arguments" for an operator-looking
# substring to be legitimate content of (the false-positive concern that
# keeps `|` and the >=1 threshold OUT of the multi-arg check above: a value
# argument like a commit message or grep pattern can legitimately contain
# "&&"/"|" text when it sits ALONGSIDE other argv elements). When the WHOLE
# cmd is one string, that string IS what subprocess treats as the executable
# name — so ANY shell metacharacter inside it (single "&&"/"||"/"|", or ";")
# means the caller passed a full pipeline as one un-split argv element
# instead of splitting it or wrapping ["sh", "-c", ...]. This is the
# single-most-recurring production failure shape (11 backlog nominations,
# ibl-5aa29f06571d through ibl-4ecff817c661, 2026-08-11..08-15): the >=2
# stuffed-pipeline check below only fires on TWO OR MORE operators glued into
# one element, so a lone "&&"/"|" (arguably the more common typo) passed
# through silently as a raw [Errno 2] with no actionable guidance.
_SINGLE_ARG_SHELL_META_RE = re.compile(r'\s(?:&&|\|\|)\s|(?<!\|)\|(?!\|)|;')
_SHELL_INTERPRETERS = frozenset({"sh", "bash", "zsh", "fish", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"})
_ENV_REF_PATTERN = re.compile(r'\$(?:\{[A-Z][A-Z0-9_]*\}|[A-Z][A-Z0-9_]*)')
def _validate_shell_argv(cmd: List[str]) -> str:
    """Cascade validation of a shell argv after autocorrect.

    Returns an empty string when valid, or the SHELL_*_ERROR message that
    should replace the run. Pulled out of _run_shell (cycle #3 cleanup) to
    keep that function under the 300-line test gate while keeping every
    check at the same cascade location and gating the _SHELL_INTERPRETERS
    exemption boundary. Autocorrect (grep backslash-pipe) is intentionally
    NOT in here — callers run it first so this helper validates what
    actually gets executed.

    Cascade order (the still-refused subset; env-ref and glued-redirect
    shapes are now DISCLOSED by ``_literal_argv_notes`` rather than refused —
    #447 A5 — so those two steps were removed here):
      2. shell-builtin check (cd, source, ., etc. — refuse with cwd hint
         for cd and a generic sh -c hint for the rest)
      3. standalone shell-operator check (`_SHELL_OPERATORS.intersection`)
      5. single-element metacharacter check (v6.101.0 fix: a lone `&&`/`||`/
         `|`/`;` when cmd has EXACTLY ONE element — the whole cmd is then
         necessarily one full pipeline mistakenly passed as an un-split
         argv string, so the false-positive concern that keeps this out of
         the multi-arg checks (a value argument legitimately containing
         operator-looking text) cannot apply).
      6. embedded-op check (the cycle #1 fix: 2+ whitespace-bracketed
         `&&`/`||` inside one argv element among possibly several — the
         multi-arg production failure shape).
    """
    executable_name = pathlib.Path(cmd[0]).name.lower() if cmd else ""

    if cmd and cmd[0] in _SHELL_BUILTINS:
        if cmd[0] == "cd":
            return (
                '⚠️ SHELL_CMD_ERROR: "cd" is a shell builtin, not an executable. '
                'Use the "cwd" parameter instead: '
                'run_command(cmd=["git", "log"], cwd="/target/dir")'
            )
        return (
            f'⚠️ SHELL_CMD_ERROR: "{cmd[0]}" is a shell builtin and cannot '
            'be executed directly via subprocess. '
            'Use ["sh", "-c", "your command"] if you need shell builtins.'
        )

    found_ops = _SHELL_OPERATORS.intersection(cmd)
    if found_ops:
        op = sorted(found_ops)[0]
        return (
            f'⚠️ SHELL_CMD_ERROR: Shell operator "{op}" found in cmd array. '
            'Subprocess does not interpret shell syntax. '
            'Options: (1) Split into separate run_command calls. '
            '(2) For pipes/chaining: ["sh", "-c", "cmd1 && cmd2"]'
        )

    # (Glued-redirect refusal removed — #447 A5: a "2>/dev/null" element is
    # literal data to subprocess and is now DISCLOSED by _literal_argv_notes,
    # so the command runs and the [sh,-c,...] hint rides along in the result.)

    # cmd has exactly ONE element: that element is not "one argument among
    # several" (where operator-looking text can be legitimate content, e.g.
    # a commit message `["git", "commit", "-m", "step1 && step2 done"]`) —
    # it IS the entire cmd, and subprocess treats it as the executable NAME.
    # A real single-token executable name never legitimately contains a
    # whitespace-bracketed "&&"/"||", a bare "|", or a ";" — so any of those
    # here means the caller passed a whole pipeline as one un-split argv
    # string instead of splitting it or wrapping ["sh", "-c", ...]. Gated by
    # _SHELL_INTERPRETERS so a bare `["bash"]` (interactive, no -c) is not
    # flagged. This is scoped tighter than the >=2 check below specifically
    # so it can safely use a >=1 threshold and include the bare "|" that the
    # multi-arg check below deliberately excludes (see its comment).
    if len(cmd) == 1 and executable_name not in _SHELL_INTERPRETERS:
        single = cmd[0]
        if _SINGLE_ARG_SHELL_META_RE.search(single):
            preview = single if len(single) <= 80 else single[:77] + "..."
            return (
                f'⚠️ SHELL_CMD_ERROR: Shell syntax found in a single-element cmd: "{preview}". '
                'A one-element cmd is treated as a literal executable NAME by subprocess, '
                'not as a shell pipeline, so this fails with [Errno 2] No such file or '
                'directory. Fix: (1) Split into separate argv elements, one per '
                'run_command call if the operator was meant to chain commands; '
                '(2) Wrap the pipeline: ["sh", "-c", "your command here"].'
            )

    # A shell pipeline STUFFED into a single argv element (e.g.
    # `["curl && -s && https://api.example.com/x"]`) — the standalone-operator
    # check above only catches `"&&"` as its own element, and the glued-redirect
    # check only matches redirect-shaped prefixes. The whole string is then
    # passed to subprocess as the executable name and dies with a silent
    # `[Errno 2] No such file or directory`. Gated by `_SHELL_INTERPRETERS` so
    # `["sh", "-c", "echo a && echo b"]` (a legitimate shell script) passes
    # through. Narrowed to whitespace-bracketed `&&`/`||` (no `|`) so
    # `grep "a|b"` regex alternation is not over-flagged.
    if executable_name not in _SHELL_INTERPRETERS:
        for idx, arg in enumerate(cmd):
            op_matches = _EMBEDDED_SHELL_OP_RE.findall(arg)
            if len(op_matches) >= 2:
                preview = arg if len(arg) <= 80 else arg[:77] + "..."
                return (
                    f'⚠️ SHELL_CMD_ERROR: Shell pipeline stuffed into cmd[{idx}]: "{preview}". '
                    'Two or more `&&`/`||` operators inside one argv element mean '
                    'the OS treats the WHOLE string as the executable name, producing '
                    'silent `[Errno 2] No such file or directory` failures. '
                    'Fix: (1) Split into separate run_command calls; '
                    '(2) Wrap the pipeline: ["sh", "-c", "cmd1 && cmd2"].'
                )

    return ""


def _literal_argv_notes(cmd: List[str]) -> str:
    """Disclosure notes for shell-syntax-looking bytes in direct argv (#447 A5).

    A commit message naming ``$HOME``, an awk ``|`` field separator, a
    ``2>/dev/null`` element — no shell runs for direct argv, so these are
    LITERAL DATA carrying no authority question. They used to be REFUSED as
    errors, blocking commands that would have worked; the in-file autocorrect
    precedent applies instead: run the command and DISCLOSE what was passed
    literally, so a genuinely mistaken spelling still explains its own cryptic
    program error.
    """
    notes: list[str] = []
    executable_name = pathlib.Path(cmd[0]).name.lower() if cmd else ""
    if executable_name not in _SHELL_INTERPRETERS:
        for arg in cmd:
            match = _ENV_REF_PATTERN.search(arg)
            if match:
                notes.append(
                    f'⚠️ SHELL_LITERAL_ARGV_NOTE: literal env reference "{match.group(0)}" in the cmd '
                    "array reached the program UNEXPANDED (run_command executes argv directly). "
                    'Use ["sh", "-c", "..."] if you intended shell expansion.\n'
                )
                break
    if found_ops := _SHELL_OPERATORS.intersection(cmd):
        notes.append(
            f'⚠️ SHELL_LITERAL_ARGV_NOTE: shell operator "{sorted(found_ops)[0]}" in the cmd array was '
            "passed to the program as a LITERAL argument (subprocess interprets no shell syntax). "
            'Use ["sh", "-c", "cmd1 && cmd2"] for pipes/chaining.\n'
        )
    # Glued redirects bypass the standalone-operator set but remain shell-looking.
    for arg in cmd:
        if _GLUED_REDIRECT_RE.match(arg):
            notes.append(
                f'⚠️ SHELL_LITERAL_ARGV_NOTE: redirect-looking argument "{arg}" in the cmd array was '
                "passed to the program as a LITERAL argument (subprocess interprets no shell "
                'syntax). Use ["sh", "-c", "..."] for real redirection.\n'
            )
            break
    return "".join(notes)


def _resolve_scratch_abs(scratch: List[str] | None, work_dir) -> list[pathlib.Path]:
    """Resolve declared ephemeral `scratch=[...]` paths to absolute host paths (relative ones
    against the command cwd). Blank entries dropped. (v6.52.2)"""
    base = pathlib.Path(work_dir).resolve(strict=False) if work_dir else None
    out: list[pathlib.Path] = []
    for raw in (scratch or []):
        text = str(raw or "").strip()
        if not text:
            continue
        p = pathlib.Path(text).expanduser()
        out.append((p if p.is_absolute() else ((base / p) if base is not None else p)).resolve(strict=False))
    return out


def _scratch_safety_reason(ctx: ToolContext, scratch_abs: list[pathlib.Path], work_dir, repo_root) -> str:
    """Pre-exec gate for declared scratch (v6.52.2; v6.56.0 adoptable): the cwd must be inside a git
    worktree (so the git-untracked proof is meaningful and the patch-exclusion contract applies), and
    each path must be CONFINED to the command cwd and git-UNTRACKED — so an ephemeral verification
    file can never mask a real TRACKED edit. Returns a refusal reason or ''.

    v6.56.0: a path is no longer blocked merely because it already EXISTS. Re-declaring the same
    throwaway across commands, or adopting an untracked file created earlier in THIS task (e.g. via
    write_file, or a prior command), is a normal verification loop — the git-tracked check still
    blocks masking a real edit, and headless patch exclusion stays sha-gated (a later real rewrite
    diverges the sha and is NOT dropped). On adoption we record the current sha through the SSOT
    writer so the manifest reflects the adopted state at declaration time."""
    if not scratch_abs:
        return ""
    if repo_root is None:
        # No git worktree at the cwd: we cannot prove a path is git-untracked, and there is no
        # workspace patch to exclude it from — so scratch is not meaningful here.
        return "scratch requires a git-worktree cwd (it is for in-repo verification); use outputs= for a deliverable"
    base = pathlib.Path(work_dir).resolve(strict=False) if work_dir else None
    tracked: set[str] = set()
    try:
        res = subprocess.run(["git", "ls-files"], cwd=str(repo_root), capture_output=True, text=True, timeout=20)
        if res.returncode == 0:
            root = pathlib.Path(repo_root).resolve(strict=False)
            tracked = {str((root / line.strip()).resolve(strict=False)) for line in (res.stdout or "").splitlines() if line.strip()}
    except Exception:
        tracked = set()
    adopt: dict = {}
    for cand in scratch_abs:
        if base is not None and not (cand == base or path_is_relative_to(cand, base)):
            return f"scratch path escapes the command cwd ({base}): {cand}"
        if str(cand) in tracked:
            return f"scratch path is git-tracked — not a throwaway (use outputs=, or edit it as a real change): {cand}"
        # A directory can neither be sha-fingerprinted nor excluded from the patch
        # file-by-file — silently adopting one would let its contents leak into the
        # deliverable while SCRATCH_REMAINS nags forever. Refuse explicitly.
        try:
            if cand.is_dir():
                return f"scratch path is a directory — declare the throwaway FILES, not their parent dir: {cand}"
        except OSError:
            pass
        # Adoptable: an existing untracked+confined file — record its current sha now so a
        # re-declaration is idempotent and the adopted state is captured at declaration.
        try:
            if cand.is_file():
                adopt[str(cand)] = sha256(cand.read_bytes()).hexdigest()
        except OSError:
            continue
    if adopt:
        record_task_scratch(ctx, adopt)
    return ""


def _record_scratch_fingerprints(ctx: ToolContext, scratch_abs: list[pathlib.Path]) -> None:
    """Record sha256 of declared scratch files that exist NOW (post-exec) so workspace patch
    capture can exclude them while they still match. Called on EVERY exit path — normal, nonzero,
    timeout, and exception — so a file created by a command that then times out is still managed
    (v6.52.2). Fail-soft; only records files that currently exist."""
    if not scratch_abs:
        return
    fingerprints: dict = {}
    for sp in scratch_abs:
        try:
            if sp.is_file():
                fingerprints[str(sp)] = sha256(sp.read_bytes()).hexdigest()
        except OSError:
            continue
    if fingerprints:
        record_task_scratch(ctx, fingerprints)


def _run_shell(
    ctx: ToolContext,
    cmd,
    cwd: str = "",
    outputs: List[str] | None = None,
    scratch: List[str] | None = None,
    _resolved_binding: ResolvedResourceBinding | None = None,
    **kwargs,
) -> str:
    # Per-call timeout override (canonical timeout_sec; timeout accepted as alias).
    timeout_sec = kwargs.get("timeout_sec")
    timeout = kwargs.get("timeout")
    _timeout_override = timeout_sec if timeout_sec is not None else timeout
    bucket = str(kwargs.get("bucket") or "")
    skill_name = str(kwargs.get("skill_name") or "")
    if isinstance(cmd, str):
        # Shared recovery keeps run_command and verify argv semantics aligned.
        recovered = recover_stringified_argv(cmd)
        # Malformed structured literals are not shell commands; refuse explicitly.
        if recovered is None:
            stripped = cmd.lstrip()
            is_posix_test_cmd = stripped.startswith("[ ") and stripped.rstrip().endswith(" ]")
            # A `{ ...; }` brace group is valid shell, not malformed JSON.
            is_brace_group = stripped.startswith("{ ") and stripped.rstrip().endswith("}")
            if is_brace_group:
                return (
                    '⚠️ SHELL_CMD_ERROR: `{ ...; }` is a shell brace group, which run_command '
                    'cannot execute directly (it runs argv without a shell). Wrap it in a shell:\n'
                    '  run_command(cmd=["sh", "-c", "{ cmd1; cmd2; }"])'
                )
            if stripped[:1] in ("[", "{") and not is_posix_test_cmd:
                return (
                    '⚠️ SHELL_ARG_ERROR: `cmd` looks like a JSON/Python list literal '
                    'but failed to parse cleanly (likely an escape or quote-mismatch '
                    'issue). Pass cmd as an actual array, not a stringified array.\n\n'
                    'Correct usage:\n'
                    '  run_command(cmd=["git", "log", "--oneline", "-10"])\n\n'
                    'Wrong usage (the failure that brought you here):\n'
                    '  run_command(cmd=\'["git", "log", "--oneline", "-10"]\')\n\n'
                    'For reading files, prefer `read_file`.\n'
                    'For searching code, prefer `search_code`.'
                )
            try:
                parts = shlex.split(cmd)
                if parts:
                    recovered = parts
            except ValueError:
                pass
        if recovered is not None:
            cmd = recovered
        else:
            return (
                '⚠️ SHELL_ARG_ERROR: `cmd` must be a JSON array of strings, not a plain string.\n\n'
                'Correct usage:\n'
                '  run_command(cmd=["grep", "-r", "pattern", "path/"])\n'
                '  run_command(cmd=["python", "-c", "print(1+1)"])\n\n'
                'Wrong usage:\n'
                '  run_command(cmd="grep -r pattern path/")\n\n'
                'For reading files, prefer `read_file`.\n'
                'For searching code, prefer `search_code`.'
            )

    if not isinstance(cmd, list):
        return "⚠️ SHELL_ARG_ERROR: cmd must be a list of strings."
    cmd = [str(x) for x in cmd]

    cmd, autocorrect_note = _maybe_autocorrect_grep_backslash_pipe(cmd)
    cmd, and_chain_note = _maybe_split_single_element_and_chain(cmd)
    if and_chain_note:
        autocorrect_note = (autocorrect_note + and_chain_note) if autocorrect_note else and_chain_note
    cmd, pipeline_note = _maybe_wrap_single_element_pipeline(cmd)
    if pipeline_note:
        autocorrect_note = (autocorrect_note + pipeline_note) if autocorrect_note else pipeline_note
    err = _validate_shell_argv(cmd)
    if err:
        return err
    # #447 A5: env-ref / glued-redirect shapes that used to be refused are now
    # disclosed as literal pass-throughs — the command still runs.
    autocorrect_note += _literal_argv_notes(cmd)

    try:
        binding = _resolved_binding or build_resolved_resource_binding(
            ctx, operation="shell", process_cwd=cwd, bucket=bucket, skill_name=skill_name,
        )
        work_dir = pathlib.Path(binding.target_path)
        cwd_root = binding.root
    except (OSError, ValueError) as exc:
        return shell_cwd_block_message(ctx, cwd, operation="shell", error=exc)
    if not work_dir.exists() or not work_dir.is_dir():
        return (
            f"⚠️ SHELL_CWD_BLOCKED: cwd is not a directory: {work_dir}. "
            f"root={binding.root}, source={binding.source}."
        )
    # Disclose the room-lens default once; explicit cwd is already caller-visible.
    if not str(cwd or "").strip() and not getattr(ctx, "_room_cwd_noted", False):
        try:
            from ouroboros.tool_access import project_room_lens_dir

            _room = project_room_lens_dir(ctx)
        except Exception:
            _room = None
        if _room is not None and pathlib.Path(work_dir).resolve(strict=False) == _room:
            ctx._room_cwd_noted = True
            autocorrect_note += (
                f"[project-room cwd: this command ran in {_room} (the room's folder). "
                'The Ouroboros system repo needs an explicit cwd.]\n\n'
            )
    repo_root = _resolve_git_root(pathlib.Path(work_dir))
    before_changed = _status_snapshot(repo_root)
    # Bounded snapshot makes the user_files artifact nudge effect-based.
    before_listing = (
        _shallow_listing(pathlib.Path(work_dir))
        if (cwd_root == "user_files" and repo_root is None and not outputs)
        else None
    )
    before_outputs = _snapshot_declared_outputs(
        ctx,
        outputs,
        pathlib.Path(work_dir),
        cwd_root=cwd_root,
        changed_paths=set(before_changed or []),
        binding=binding,
    )

    # Scratch is confined/new/untracked, patch-excluded, and never an artifact.
    scratch_abs = _resolve_scratch_abs(scratch, work_dir)
    if scratch_abs:
        _scratch_reason = _scratch_safety_reason(ctx, scratch_abs, pathlib.Path(work_dir), repo_root)
        if _scratch_reason:
            return f"⚠️ SCRATCH_BLOCKED: {_scratch_reason}."
    timeout_sec = _resolve_effective_timeout(_RUN_SHELL_DEFAULT_TIMEOUT_SEC, ctx, override_sec=_timeout_override)
    bootstrap_process_path()
    # Emergency bundled-node PATH prepend; None on every healthy path (env stays byte-identical).
    node_resolution = active_node_resolution(ctx)
    # Two clocks (D2-1): EPOCH feeds the st_mtime audit; MONOTONIC feeds durations.
    _command_start_epoch = time.time()
    _command_start_ts = time.monotonic()
    try:
        if _executor_can_run_cwd(ctx, pathlib.Path(work_dir)):
            res = executor_execute(ctx, cmd, pathlib.Path(work_dir), timeout_sec,
                                   env_overlay=interpreter_path_overlay(node_resolution))
        else:
            run_env = apply_env_path_prepend(
                _shell_env_for_cwd(ctx, pathlib.Path(work_dir)), node_resolution)
            res = _tracked_subprocess_run(
                cmd, cwd=str(work_dir),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=timeout_sec,
                **({"env": run_env} if run_env is not None else {}),
            )
        # Typed process facts (R5): measured HERE, structurally — never re-derived from prose.
        _lived_ms = max(0, int((time.monotonic() - _command_start_ts) * 1000))
        _publish_process_facts(
            returncode=getattr(res, "returncode", None),
            started_ts=_command_start_ts,
            resolved_runtime=_active_resolved_runtime(ctx),
        )
        # Post-run hashes exclude scratch only while its exact bytes still match.
        _record_scratch_fingerprints(ctx, scratch_abs)
        if res.returncode != 0:
            executor_note = ""
            if getattr(res, "backend_trace", None):
                executor_note = "\n\nEXECUTOR_TRACE:\n" + json.dumps(res.backend_trace, ensure_ascii=False, indent=2)
            if _is_search_no_match(res):
                return autocorrect_note + (
                    f"{_describe_returncode(res.returncode, cwd=work_dir, binding=binding)} (no matches)\n"
                    f"{_format_process_output(res.stdout or '', '')}"
                    f"{executor_note}"
                )
            return _annotate_result(autocorrect_note + f"⚠️ SHELL_EXIT_ERROR: command exited with {_describe_returncode(res.returncode, cwd=work_dir, binding=binding, lived_ms=_lived_ms, resolved_runtime=_active_resolved_runtime(ctx))}.\n\n{_format_process_output(res.stdout or '', res.stderr or '')}{executor_note}", status="non_zero_exit", is_failure=True)
        after_changed = _status_snapshot(repo_root)
        if after_changed != before_changed:
            # This resolved cwd may be outside the live-repo dispatcher snapshot.
            _invalidate_advisory(
                ctx,
                changed_paths=after_changed or before_changed,
                mutation_root=repo_root,
                source_tool="run_command",
            )
        # A shell command has no file-write guard the way write_file/edit_text do —
        # revert any protected runtime path (BIBLE.md, docs/CHECKLISTS.md, etc.) it
        # happened to dirty in the system repo, the same way an unauthorized edit_text
        # write is refused. Scoped to the SYSTEM repo only (never an unrelated cwd's
        # git tree that coincidentally shares a protected filename).
        protected_restore_note = ""
        if repo_root is not None:
            try:
                system_repo = pathlib.Path(
                    getattr(ctx, "system_repo_dir", None) or getattr(ctx, "repo_dir")
                ).resolve(strict=False)
                if pathlib.Path(repo_root).resolve(strict=False) == system_repo:
                    protected_dirty = _protected_runtime_dirty_paths(repo_root)
                    if protected_dirty:
                        # ibl-local-29ee0d0e37bc: sanctioned size-ratchet update path.
                        # If the ONLY dirty protected path is the size-ratchet manifest,
                        # validate the in-tree manifest against HEAD's committed
                        # authority and preserve a clean update instead of reverting it.
                        # Otherwise fall through to the existing revert-everything flow.
                        protected_dirty, preserved_note = _maybe_preserve_size_ratchet_update(
                            repo_root, protected_dirty
                        )
                        if preserved_note:
                            protected_restore_note = preserved_note
                        if protected_dirty:
                            restored = _restore_protected_runtime_paths(repo_root, protected_dirty)
                            if restored:
                                protected_restore_note = (
                                    "\n\n⚠️ PROTECTED_PATH_AUTO_RESTORED: this command changed protected "
                                    "runtime file(s), which shell commands cannot modify (use edit_text "
                                    "through the normal review path instead); reverted: "
                                    + ", ".join(restored[:5])
                                )
            except Exception:
                log.debug("protected-runtime-path restore check failed", exc_info=True)
        undeclared_user_outputs = _mentioned_user_file_outputs_without_declaration(
            ctx,
            cmd,
            outputs,
            scratch_abs=scratch_abs,
            command_start_ts=_command_start_epoch,
            cwd=work_dir,
        )
        if undeclared_user_outputs:
            # Declaration NUDGE, not a failure — see _UNDECLARED_OUTPUTS_MARKER.
            return (
                autocorrect_note
                + f"{_UNDECLARED_OUTPUTS_MARKER}: command appears to write user_files outputs "
                "without declaring outputs=[...]. Declare generated user-visible files so "
                "they are copied into the task artifact store before claiming completion. "
                f"Paths: {', '.join(undeclared_user_outputs[:5])}.\n\n"
                + f"{_describe_returncode(0, cwd=work_dir, binding=binding)}\n"
                + _format_process_output(res.stdout or "", res.stderr or "")
                + protected_restore_note
            )
        artifact_note, artifact_failed = _register_process_outputs(
            ctx,
            outputs,
            pathlib.Path(work_dir),
            cwd_root=cwd_root,
            changed_paths=set(after_changed or []),
            before_outputs=before_outputs,
            binding=binding,
        )
        audit_note = ""
        if cwd_root == "user_files" and not outputs:
            # Remove scratch effects without hiding a simultaneous real deliverable.
            _after_for_audit = after_changed
            if scratch_abs and repo_root is not None:
                _repo = pathlib.Path(repo_root).resolve(strict=False)
                _scratch_rel: set[str] = set()
                for _sp in scratch_abs:
                    try:
                        _scratch_rel.add(_sp.resolve(strict=False).relative_to(_repo).as_posix())
                    except ValueError:
                        continue
                if _scratch_rel:
                    _after_for_audit = [p for p in (after_changed or []) if p not in _scratch_rel]
            if _user_files_run_had_effect(before_changed, _after_for_audit, before_listing, pathlib.Path(work_dir)):
                audit_note = (
                    "\n\n⚠️ ARTIFACT_AUDIT_GAP: command modified files in a user_files cwd without "
                    "outputs=[...]. If it created a deliverable, rerun/register the file "
                    "with outputs or write_file(root=artifact_store) before claiming it."
                )
        scratch_note = ""
        _scratch_remaining = [str(p) for p in scratch_abs if p.exists()]
        if _scratch_remaining:
            scratch_note = (
                "\n\n⚠️ SCRATCH_REMAINS: declared scratch still on disk after the command: "
                + ", ".join(_scratch_remaining[:5])
                + ". It is excluded from the workspace patch, but delete it before finishing so it does not linger."
            )
        if artifact_failed:
            return (
                autocorrect_note
                + "⚠️ ARTIFACT_OUTPUT_ERROR: command succeeded but declared output registration failed. "
                + f"{_describe_returncode(0, cwd=work_dir, binding=binding)}\n"
                + f"{_format_process_output(res.stdout or '', res.stderr or '')}"
                + artifact_note
                + protected_restore_note
            )
        executor_note = ""
        if getattr(res, "backend_trace", None):
            executor_note = "\n\nEXECUTOR_TRACE:\n" + json.dumps(res.backend_trace, ensure_ascii=False, indent=2)
        return _annotate_result(autocorrect_note + f"{_describe_returncode(0, cwd=work_dir, binding=binding)}\n{_format_process_output(res.stdout or '', res.stderr or '')}{artifact_note}{audit_note}{scratch_note}{executor_note}{protected_restore_note}", status="ok_autocorrected" if autocorrect_note else "ok", is_failure=False)
    except subprocess.TimeoutExpired:
        # A timed-out child has no returncode (unlike signal death): duration only.
        _publish_process_facts(started_ts=_command_start_ts,
                               resolved_runtime=_active_resolved_runtime(ctx))
        # Timeout-created scratch still needs its exclusion fingerprint.
        _record_scratch_fingerprints(ctx, scratch_abs)
        return (
            f"⚠️ TOOL_TIMEOUT (run_command): command exceeded the per-command timeout of {timeout_sec}s "
            f"and its subprocess tree was terminated (root={binding.root}, cwd={work_dir}). NOTE: this is the per-command "
            f"FOREGROUND timeout, NOT the task deadline. For genuinely long-running compute (training, "
            f"sampling, large builds/downloads), start it with start_service and poll "
            f"service_status/service_logs while you do other work, or pass an explicit timeout_sec=<seconds> "
            f"(up to the per-call ceiling) — and preserve a best-effort deliverable before the task deadline."
        )
    except Exception as e:
        # e.g. FileNotFoundError before/at exec: no returncode, duration only.
        _publish_process_facts(started_ts=_command_start_ts,
                               resolved_runtime=_active_resolved_runtime(ctx))
        _record_scratch_fingerprints(ctx, scratch_abs)
        return f"⚠️ SHELL_ERROR: {e}. root={binding.root}, cwd={work_dir}"


def _load_project_context(repo_dir: pathlib.Path) -> str:
    """Load governance docs for Claude Code system_prompt injection."""
    docs = [
        ("BIBLE.md", "CONSTITUTION"),
        ("docs/DEVELOPMENT.md", "DEVELOPMENT GUIDE"),
        ("docs/CHECKLISTS.md", "REVIEW CHECKLISTS"),
        ("docs/ARCHITECTURE.md", "ARCHITECTURE"),
    ]
    parts: list = []
    for relpath, label in docs:
        fpath = repo_dir / relpath
        if fpath.is_file():
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
                parts.append(f"## {label}\n\n{content}")
            except Exception:
                pass
    return "\n\n---\n\n".join(parts)


def _get_changed_files(repo_dir: pathlib.Path) -> list:
    """Return changed files after an edit."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return [line[3:].strip() for line in res.stdout.splitlines() if len(line) > 3 and line.strip()]
    except Exception:
        pass
    return []


def _get_diff_stat(repo_dir: pathlib.Path) -> str:
    """Return git diff --stat output."""
    try:
        res = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=5,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return ""


# The run_script interpreter VALIDATOR (SSOT; the schema enum below is the
# advertised subset — Windows launcher spellings are accepted, not advertised).
RUN_SCRIPT_INTERPRETER_ALLOWLIST = frozenset({
    "python", "python3", "python.exe", "python3.exe",
    "bash", "sh", "node", "node.exe", "ruby",
})


def _run_script(
    ctx: ToolContext,
    script: str,
    interpreter: str = "python3",
    args: List[str] | None = None,
    cwd: str = "",
    outputs: List[str] | None = None,
    _resolved_binding: ResolvedResourceBinding | None = None,
    **kwargs,
) -> str:
    """Stage a temporary script and run it with one resolved process binding.

    Optional public fields ride in ``kwargs`` to keep the handler within the
    DEVELOPMENT parameter limit; dispatch validates them against the schema.
    """
    timeout_sec = kwargs.get("timeout_sec")
    timeout = kwargs.get("timeout")
    scratch = kwargs.get("scratch")
    bucket = str(kwargs.get("bucket") or "")
    skill_name = str(kwargs.get("skill_name") or "")
    interp = str(interpreter or "python3").strip()
    allowed = RUN_SCRIPT_INTERPRETER_ALLOWLIST
    resolver_attested = False
    try:
        from ouroboros.process_interpreters import InterpreterResolutionTrace

        resolution = getattr(ctx, "_active_interpreter_resolution", None)
        resolver_attested = bool(
            isinstance(resolution, InterpreterResolutionTrace)
            and resolution.verified
            and resolution.tool == "run_script"
            and (
                resolution.requested_interpreter in {"python", "python3"}
                if resolution.family == "python"
                # A node attestation admits only an actual SUBSTITUTION (emergency
                # rewrite); healthy paths have changed=False, so bare spellings
                # still hit the allowlist (A-F1).
                else (resolution.family == "node" and resolution.changed)
            )
            and resolution.resolved_interpreter == interp
        )
    except Exception:
        resolver_attested = False
    if pathlib.PurePath(interp).name not in allowed and not resolver_attested:
        return f"⚠️ RUN_SCRIPT_BLOCKED: interpreter must be one of {sorted(allowed)}."
    body = str(script or "")
    if not body.strip():
        return "⚠️ TOOL_ARG_ERROR (run_script): script is required."
    try:
        binding = _resolved_binding or build_resolved_resource_binding(
            ctx, operation="shell", process_cwd=cwd, bucket=bucket, skill_name=skill_name,
        )
    except (OSError, ValueError) as exc:
        return shell_cwd_block_message(ctx, cwd, operation="shell", error=exc)
    # The undeclared-output audit of the script BODY (argv only carries the temp script path, so
    # _run_shell cannot see the body) is POST-exec (v6.56.0): the stat filter needs the files to
    # exist, and a pre-exec scan on not-yet-written paths would either be a no-op or false-flag
    # import strings. We resolve the body-audit scratch against the SAME effective cwd the script
    # executes in so a relatively-declared scratch path matches a user_files write in the body.
    resolved_workdir = pathlib.Path(binding.target_path)
    _scratch_abs_body = _resolve_scratch_abs(scratch, resolved_workdir)
    _body_start_epoch = time.time()  # st_mtime audit; monotonic below is for durations
    _body_start_ts = time.monotonic()
    executor_active = _executor_can_run_cwd(ctx, resolved_workdir)
    active_workspace_script = binding.root == "active_workspace"
    if active_workspace_script:
        root = resolved_workdir / ".ouroboros" / "tmp_scripts"
    else:
        try:
            root = pathlib.Path(ctx.task_drive_root()) / "tmp_scripts"
        except Exception:
            root = pathlib.Path(ctx.drive_root) / "tmp_scripts"
    root.mkdir(parents=True, exist_ok=True)
    suffix = ".py" if "python" in pathlib.PurePath(interp).name else ".sh"
    script_path = root / f"script_{uuid.uuid4().hex}{suffix}"
    script_path.write_text(body, encoding="utf-8")
    try:
        os.chmod(script_path, 0o600)
    except OSError:
        pass
    script_arg = str(script_path)
    if executor_active:
        executor = executor_ref_from_ctx(ctx)
        if executor is not None and executor.kind != "local":
            try:
                script_arg = executor_map_host_path(executor, script_path)
            except Exception as exc:
                script_path.unlink(missing_ok=True)
                return f"⚠️ RUN_SCRIPT_BLOCKED: executor-backed run_script could not map temp script path: {type(exc).__name__}: {exc}"
    argv = [interp, script_arg, *[str(item) for item in (args or [])]]
    try:
        result = _run_shell(
            ctx, argv, cwd=cwd, outputs=outputs, scratch=scratch,
            _resolved_binding=binding, timeout_sec=timeout_sec, timeout=timeout,
        )
    finally:
        try:
            script_path.unlink(missing_ok=True)
            script_path.parent.rmdir()
            if active_workspace_script:
                script_path.parent.parent.rmdir()
        except OSError:
            pass
    # POST-exec body audit: stat-confirmed user_files writes performed by the script
    # body itself. Runs on EVERY exit path (parity with _record_scratch_fingerprints):
    # a script that writes an undeclared deliverable and then FAILS (raise/SystemExit/
    # timeout) still leaves that file on disk, so a `⚠️` result does NOT mean "no
    # deliverable to declare" — surface both the error and the output-guard note.
    undeclared_user_outputs = _mentioned_user_file_outputs_without_declaration(
        ctx,
        [interp, "-c", body],
        outputs,
        scratch_abs=_scratch_abs_body,
        command_start_ts=_body_start_epoch,
        cwd=resolved_workdir,
    )
    audit_note = ""
    if undeclared_user_outputs:
        # Same declaration NUDGE class as run_command's — see _UNDECLARED_OUTPUTS_MARKER.
        audit_note = (
            f"{_UNDECLARED_OUTPUTS_MARKER}: run_script wrote user_files without declaring outputs: "
            + ", ".join(undeclared_user_outputs)
            + ". Re-run with outputs=[...] or write the canonical deliverable via root=artifact_store."
        )
    if str(result).lstrip().startswith("⚠️"):
        # The result already owns line 1 with its own typed marker, which is what
        # the failure classifier reads — the nudge appends after it.
        tail = f"\n{audit_note}" if audit_note else ""
        return f"{result}{tail}\n# script_path={script_path}"
    if audit_note:
        # The nudge used to REPLACE the whole _run_shell payload (a successful
        # script's answer was gone; re-running was the sole recovery). Marker
        # first — ARTIFACT_OUTPUT_UNDECLARED is a typed policy-denial surface the
        # classifier reads off line 1 — payload appended, as in run_command.
        return f"{audit_note}\n\n# script_path={script_path}\n{result}"
    return f"# script_path={script_path}\n{result}"


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("run_command", {
            "name": "run_command",
            "description": (
                "Run a foreground bounded command in an allowed resource-root cwd. Returns stdout+stderr. "
                "Every result header echoes the resolved cwd. "
                "cmd MUST be an array of strings, never a single shell-style "
                "string. Use cwd= for working directory; cd is rejected. "
                "For pipes/chaining use [\"sh\", \"-c\", \"cmd1 && cmd2\"]. "
                "Prefer the dedicated tools where one fits: read_file (not cat/head/sed-as-reader), "
                "search_code/query_code (not grep/find-as-search), write_file/edit_text (not sed/echo-redirect)."
            ),
            "parameters": {"type": "object", "properties": {
                "cmd": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Argv as a JSON array of strings. Example: "
                        "[\"git\", \"log\", \"--oneline\", \"-10\"]. NEVER "
                        "pass a single string like \"git log\" or a "
                        "stringified array like '[\"git\", \"log\"]'."
                    ),
                },
	                "cwd": {"type": "string", "default": "", "description": "Omit for active_workspace; use system_repo[/subdir] for Ouroboros or skill_payload[/subdir] with bucket+skill_name for a skill. Existing task_drive, artifact_store, user_files and authorized absolute cwd forms remain available; use cwd instead of the rejected cd builtin."},
	                "bucket": {"type": "string", "enum": ["external", "clawhub", "ouroboroshub", "user_repo"], "description": "Physical skill location for cwd=skill_payload[/subdir]."},
	                "skill_name": {"type": "string", "description": "Exact skill identity for cwd=skill_payload[/subdir]."},
	                "outputs": {
	                    "type": "array",
	                    "items": {"type": "string"},
	                    "default": [],
	                    "description": "Generated file paths to copy/register into the task artifact store after success.",
	                },
	                "scratch": {
	                    "type": "array",
	                    "items": {"type": "string"},
	                    "default": [],
	                    "description": (
	                        "Transient in-repo verification files (e.g. a throwaway test you write, run, and "
	                        "delete to check your own work) — throwaway verification ONLY, never part of the "
	                        "solution. Each must be untracked and confined to the cwd: a NEW file, or an existing "
	                        "untracked file created earlier in THIS task (adopted by sha, so re-declaring is "
	                        "idempotent); tracked files and directories stay blocked. They are exempt "
	                        "from the deliverable-output guard, never registered as artifacts, and EXCLUDED "
	                        "from the workspace patch. Use outputs=[...] for real deliverables."
	                    ),
	                },
	                "timeout_sec": {
	                    "type": "integer",
	                    "description": (
	                        "Optional per-call timeout in seconds for long builds/tests (alias: timeout). "
	                        "Clamped to the remaining task-deadline budget. Omit for the default (deadline-capped)."
	                    ),
	                },
	                "timeout": {
	                    "type": "integer",
	                    "description": "Alias for timeout_sec (per-call timeout in seconds).",
	                },
	            }, "required": ["cmd"]},
        }, _run_shell, is_code_tool=True, timeout_sec=_RUN_SHELL_DEFAULT_TIMEOUT_SEC, mutates_worktree=True),
        ToolEntry("run_script", {
            "name": "run_script",
            "description": (
                "Run a short task-scoped temporary script with a declared interpreter. "
                "Use for multi-line diagnostics or harness helpers; generated script files live under the task drive. "
                "The underlying command result echoes the resolved cwd."
            ),
            "parameters": {"type": "object", "properties": {
                "script": {"type": "string"},
	                "interpreter": {"type": "string", "enum": ["python", "python3", "bash", "sh", "node", "ruby"], "default": "python3"},
	                "args": {"type": "array", "items": {"type": "string"}, "default": []},
	                "cwd": {"type": "string", "default": "", "description": "Omit for active_workspace; use system_repo[/subdir] for Ouroboros or skill_payload[/subdir] with bucket+skill_name for a skill."},
	                "bucket": {"type": "string", "enum": ["external", "clawhub", "ouroboroshub", "user_repo"], "description": "Physical skill location for cwd=skill_payload[/subdir]."},
	                "skill_name": {"type": "string", "description": "Exact skill identity for cwd=skill_payload[/subdir]."},
	                "outputs": {
	                    "type": "array",
	                    "items": {"type": "string"},
	                    "default": [],
	                    "description": "Generated file paths to copy/register into the task artifact store after success.",
	                },
	                "scratch": {
	                    "type": "array",
	                    "items": {"type": "string"},
	                    "default": [],
	                    "description": (
	                        "Transient in-repo verification files (e.g. a throwaway test you write, run, and "
	                        "delete to check your own work) — throwaway verification ONLY, never part of the "
	                        "solution. Each must be untracked and confined to the cwd: a NEW file, or an existing "
	                        "untracked file created earlier in THIS task (adopted by sha, so re-declaring is "
	                        "idempotent); tracked files and directories stay blocked. They are exempt "
	                        "from the deliverable-output guard, never registered as artifacts, and EXCLUDED "
	                        "from the workspace patch. Use outputs=[...] for real deliverables."
	                    ),
	                },
	                "timeout_sec": {
	                    "type": "integer",
	                    "description": (
	                        "Optional per-call timeout in seconds for long scripts (alias: timeout). "
	                        "Clamped to the remaining task-deadline budget. Omit for the default (deadline-capped)."
	                    ),
	                },
	                "timeout": {
	                    "type": "integer",
	                    "description": "Alias for timeout_sec (per-call timeout in seconds).",
	                },
	            }, "required": ["script"]},
        }, _run_script, is_code_tool=True, timeout_sec=_RUN_SHELL_DEFAULT_TIMEOUT_SEC, mutates_worktree=True),
    ]
