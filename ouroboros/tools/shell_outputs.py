"""Declared process outputs: resolution, fingerprints, and artifact registration.

Owns the protected and credential-like refusals that keep a control-plane or
secret path out of the deliverable store, the bounded file/directory
fingerprints that decide whether a declared output actually changed, and the
copy into the task artifact store. The allowed artifact roots and the lexical
audit that notices an undeclared user_files deliverable live with the upstream
extraction in ``tools/shell_audit.py``; the handlers that call this surface
stay with ``tools/shell.py``.
"""

from __future__ import annotations

import hashlib
from hashlib import sha256
import os
import pathlib
import stat
from typing import Dict, List

from ouroboros.artifacts import copy_directory_to_task_artifacts, copy_file_to_task_artifacts
from ouroboros.runtime_mode_policy import (
    is_protected_runtime_path,
)
from ouroboros.shell_parse import is_absolute_path_text
from ouroboros.tools.registry import (
    ToolContext,
)
from ouroboros.tools.deliverables_shell import lexical_user_files_block_reason
from ouroboros.tools.shell_audit import (
    _allowed_output_roots,
    _presence_allows_user_output,
)
from ouroboros.tool_access import (
    ResolvedResourceBinding,
    _deliverables_root_lexical,
    _deliverables_root_lexical_alias,
    _lexical_path_is_relative_to_casefold,
    _path_is_relative_to_casefold,
    path_is_relative_to,
    resource_root_path,
    user_files_path_block_reason,
)
from ouroboros.utils import safe_relpath
from ouroboros.workspace_executor import executor_ref_from_ctx
from ouroboros.workspace_executor import map_backend_path as executor_map_backend_path
from ouroboros.workspace_executor import map_backend_path_lexical as executor_map_backend_path_lexical


def _shell():
    """The parent module, read at call time.

    The parent owns the rebindable module state and the members tests
    monkeypatch there; reading them through the module at each call keeps
    one binding, where a from-import would freeze the value this leaf saw
    at import time (the owner-approved D18/D33 mechanical exception).
    """
    from ouroboros.tools import shell

    return shell


_OUTPUT_DIR_MAX_FILES = 1000


_OUTPUT_DIR_MAX_BYTES = 50 * 1024 * 1024


def _protected_output_source_reason(
    ctx: ToolContext,
    source: pathlib.Path,
    label: str,
    changed_paths: set[str],
    binding: ResolvedResourceBinding | None = None,
) -> str:
    """Return a block reason for protected/control-plane output sources."""

    try:
        from ouroboros.protected_artifacts import block_reason_for_path

        protected_artifact_reason = block_reason_for_path(
            ctx, source, "copy", binding,
        )
        if protected_artifact_reason:
            return protected_artifact_reason
    except Exception:
        pass

    name_lower = source.name.lower()
    if (
        source.name.startswith(".")
        or name_lower in _SENSITIVE_OUTPUT_NAMES
        or name_lower.endswith(_SENSITIVE_OUTPUT_SUFFIXES)
        or any(marker in name_lower for marker in _SENSITIVE_OUTPUT_MARKERS)
    ):
        return f"credential-like output {source.name} is not a deliverable artifact"

    try:
        system_repo = pathlib.Path(getattr(ctx, "system_repo_dir", None) or getattr(ctx, "repo_dir")).resolve(strict=False)
    except Exception:
        system_repo = pathlib.Path(getattr(ctx, "repo_dir")).resolve(strict=False)
    if path_is_relative_to(source, system_repo):
        try:
            rel = source.relative_to(system_repo).as_posix()
        except ValueError:
            rel = source.name
        if is_protected_runtime_path(rel):
            return f"protected repo output {rel} is not a deliverable artifact"
        if label in {"active_workspace", "system_repo"} and not _changed_path_covers(rel, changed_paths):
            return f"unchanged repo output {rel} is not a generated deliverable"

    try:
        drive = pathlib.Path(getattr(ctx, "drive_root")).resolve(strict=False)
        if path_is_relative_to(source, drive):
            if (
                binding is not None
                and binding.root == "skill_payload"
                and path_is_relative_to(source, binding.base_path)
            ):
                return ""
            task_drive = resource_root_path(ctx, "task_drive")
            artifact_store = resource_root_path(ctx, "artifact_store")
            if not (path_is_relative_to(source, task_drive) or path_is_relative_to(source, artifact_store)):
                return "runtime data output is not a user deliverable; use task_drive or artifact_store"
    except Exception:
        pass

    return ""


def _changed_path_covers(rel: str, changed_paths: set[str]) -> bool:
    clean = str(rel or "").strip().strip("/")
    if not clean:
        return False
    for item in changed_paths or set():
        path = str(item or "").strip().strip("/")
        if path == clean or path.startswith(clean + "/") or clean.startswith(path + "/"):
            return True
    return False


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


def _directory_fingerprint_from_entries(root: pathlib.Path, entries: list[tuple[str, os.stat_result, pathlib.Path]]) -> str:
    digest = hashlib.sha256()
    for rel, st, child in sorted(entries, key=lambda item: item[0]):
        digest.update(rel.encode("utf-8", errors="replace"))
        digest.update(str(st.st_mode).encode())
        digest.update(str(st.st_size).encode())
        digest.update(str(st.st_mtime_ns).encode())
        if stat.S_ISLNK(st.st_mode):
            try:
                digest.update(os.readlink(child).encode("utf-8", errors="replace"))
            except OSError:
                pass
    return digest.hexdigest()


def _bounded_directory_fingerprint(path: pathlib.Path) -> tuple[bool, int, str]:
    root = pathlib.Path(path).resolve(strict=False)
    total = 0
    entries: list[tuple[str, os.stat_result, pathlib.Path]] = []
    try:
        for child in root.rglob("*"):
            try:
                st = child.lstat()
            except OSError:
                continue
            try:
                rel = child.resolve(strict=False).relative_to(root).as_posix()
            except ValueError:
                rel = safe_relpath(str(child))
            entries.append((rel, st, child))
            if child.is_file() and not child.is_symlink():
                total += st.st_size
            if len(entries) > _OUTPUT_DIR_MAX_FILES:
                return True, total, f"too_many_entries:{_OUTPUT_DIR_MAX_FILES}"
            if total > _OUTPUT_DIR_MAX_BYTES:
                return True, total, f"too_many_bytes:{_OUTPUT_DIR_MAX_BYTES}"
        return True, total, _directory_fingerprint_from_entries(root, entries)
    except OSError:
        return False, -1, ""


def _fingerprint_output(path: pathlib.Path) -> tuple[bool, int, str]:
    try:
        if path.is_dir():
            return _bounded_directory_fingerprint(path)
        if not path.is_file():
            return False, -1, ""
        raw = path.read_bytes()
        return True, len(raw), sha256(raw).hexdigest()
    except OSError:
        return False, -1, ""


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


def _scan_directory_output_members(
    ctx: ToolContext,
    source: pathlib.Path,
    *,
    label: str,
    changed_paths: set[str],
    binding: ResolvedResourceBinding | None = None,
) -> tuple[list[pathlib.Path], int, str]:
    root = pathlib.Path(source).resolve(strict=False)
    members: list[pathlib.Path] = []
    dir_size = 0
    try:
        for child in root.rglob("*"):
            if child.is_symlink():
                continue
            if not child.is_file():
                continue
            members.append(child)
            try:
                dir_size += child.stat().st_size
            except OSError:
                pass
            try:
                rel_parts = child.resolve(strict=False).relative_to(root).parts
            except ValueError:
                rel_parts = child.parts
            component_reason = _sensitive_output_component_reason(rel_parts)
            if component_reason:
                return [], dir_size, f"{child}: {component_reason}"
            reason = _protected_output_source_reason(
                ctx, child.resolve(strict=False), label, changed_paths, binding,
            )
            if reason:
                return [], dir_size, f"{child}: {reason}"
            if len(members) > _OUTPUT_DIR_MAX_FILES:
                return [], dir_size, f"{source}: directory output has more than {_OUTPUT_DIR_MAX_FILES} files"
            if dir_size > _OUTPUT_DIR_MAX_BYTES:
                return [], dir_size, f"{source}: directory output exceeds {_OUTPUT_DIR_MAX_BYTES} bytes"
    except OSError as exc:
        return [], dir_size, f"{source}: {type(exc).__name__}: {exc}"
    return sorted(members, key=lambda item: item.as_posix()), dir_size, ""


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
            dir_members, _dir_size, blocked_member = _scan_directory_output_members(
                ctx,
                source,
                label=str(cwd_root or "cwd"),
                changed_paths=changed_paths or set(),
                binding=binding,
            )
            if blocked_member:
                notes.append(f"blocked directory output: {blocked_member}")
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


_SENSITIVE_OUTPUT_NAMES = frozenset({".env", ".env.local", "credentials.json", "secrets.json", "token.json"})


_SENSITIVE_OUTPUT_SUFFIXES = (".key", ".pem", ".p12", ".pfx")


_SENSITIVE_OUTPUT_MARKERS = ("api_key", "apikey", "access_token", "bearer_token", "credential", "password", "refresh_token", "secret")


_SENSITIVE_OUTPUT_COMPONENT_NAMES = _SENSITIVE_OUTPUT_NAMES | frozenset({"secret", "secrets", "credential", "credentials", "token", "tokens"})


def _sensitive_output_component_reason(parts: tuple[str, ...]) -> str:
    for part in parts:
        text = str(part or "")
        if not text:
            continue
        low = text.lower()
        if text.startswith("."):
            return f"hidden/control output path component {text} is not a deliverable artifact"
        if low in _SENSITIVE_OUTPUT_COMPONENT_NAMES or low.endswith(_SENSITIVE_OUTPUT_SUFFIXES) or any(marker in low for marker in _SENSITIVE_OUTPUT_MARKERS):
            return f"credential-like output path component {text} is not a deliverable artifact"
    return ""
