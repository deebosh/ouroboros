"""The physical chat-log generation chain and immutable memory-source retention.

``logs/chat.jsonl`` rotates verbatim into ``archive/chat_<ts>.jsonl``; the
ordered archives plus the live file are the whole conversation. These readers
are shared by ``chat_history``, the generation-aware chat reader in ``memory``,
the owner-message source lens in ``project_dialogue``, reflection, the
consciousness wake and the legacy dialogue writer in ``consolidator``. Callers
use ``chat_chain.retain_memory_source`` as a module attribute, so one
substitution reaches every caller.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List, Tuple

from ouroboros.contracts.chat_id_policy import is_a2a_chat_id
from ouroboros.utils import jsonl_generation_signature as _chat_log_signature


def retain_memory_source(context: Any, source_id: str, data: bytes, extension: str = "md") -> Dict[str, Any]:
    """Use existing immutable source storage with a reader valid after this task."""
    from ouroboros.artifacts import store_actor_source_bytes, task_artifact_dir_path
    root, task_id = pathlib.Path(context.drive_root).resolve(), str(context.task_id or "consolidation")
    ref = store_actor_source_bytes(root, task_id, category="context_checkpoints",
                                  source_id=source_id, data=data, extension=extension)
    path = task_artifact_dir_path(root, task_id, create=False) / ref["path"]
    return {**ref, "task_id": task_id, "canonical_root": str(root), "read": {"tool": "read_file",
            "arguments": {"root": "runtime_data", "path": path.relative_to(root).as_posix(), "start_line": 1}}}


def _ordered_chat_generation_paths(source_path: pathlib.Path) -> List[pathlib.Path]:
    """Return the physical chat chain, oldest archive to live."""
    archive_dir = source_path.parent.parent / "archive"
    try:
        archives = sorted(archive_dir.glob("chat_*.jsonl"), key=lambda p: p.name)
    except OSError:
        archives = []
    return [*archives, source_path]


def _resolve_generation_segments(
    meta: Dict[str, Any], source_path: pathlib.Path,
) -> Tuple[List[pathlib.Path], int, bool]:
    """Generation-aware consolidation cursor (v6.73.0).

    The cursor (``last_consolidated_offset`` + ``chat_log_signature``) points into
    ONE log generation. Rotation moves that generation to ``archive/chat_<ts>.jsonl``
    verbatim, so the stored first-line hash locates it in the ordered archive chain
    and consolidation continues over ``archives[i:] + live`` — the pre-rotation
    tail (and any number of intervening rotations) is consolidated, never dropped.
    Returns ``(ordered segments, offset into their concatenation, gap_detected)``;
    ``gap_detected`` is True only when the stored generation no longer exists
    anywhere (manual deletion/corruption — archives are never auto-pruned).
    """
    last_offset = int(meta.get("last_consolidated_offset", 0) or 0)
    stored_sig = meta.get("chat_log_signature") or {}
    stored_first = str(stored_sig.get("first_line_sha256") or "") if isinstance(stored_sig, dict) else ""
    live_sig = _chat_log_signature(source_path)
    archives = _ordered_chat_generation_paths(source_path)[:-1]
    if not stored_first:
        # Uninitialized cursor. Any archives that already exist rotated BEFORE
        # the first consolidation ever ran — they are unconsolidated by
        # definition, so the whole ordered chain is the window (offset 0).
        # A nonzero offset WITHOUT a signature is an ambiguous pre-signature
        # legacy shape: keep the historical live-only behavior for it.
        if last_offset == 0 and archives:
            return [*archives, source_path], 0, False
        return [source_path], last_offset, False
    if stored_first == str(live_sig.get("first_line_sha256") or ""):
        return [source_path], last_offset, False
    for index, archive_path in enumerate(archives):
        sig = _chat_log_signature(archive_path)
        if str(sig.get("first_line_sha256") or "") == stored_first:
            return [*archives[index:], source_path], last_offset, False
    return [source_path], 0, True


def _read_chat_entries(path: pathlib.Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    # Full project awareness (v6.32.0): the one identity's consolidated dialogue
    # (dialogue_blocks.json) is its WHOLE conversation — main + project threads —
    # because Ouroboros is one awareness/biography across direct chat, project
    # rooms, and background consciousness (BIBLE P1). Only A2A virtual-transport
    # ids are excluded (machine-to-machine traffic, not the human dialogue). This
    # MUST match memory.read_jsonl_tail_after_offset so the shared consolidation
    # offset indexes the same stream.
    entries = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not is_a2a_chat_id(entry.get("chat_id", 1)):
                entries.append(entry)
    return entries
