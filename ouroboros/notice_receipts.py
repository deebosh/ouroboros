"""Upgrade-notice receipts addressed by notice id, written by chat publication.

The lifecycle import reads historical chat once. New publication stores the
receipt immediately after the canonical append, before any state-marker write.
An append/receipt crash remains at-least-once (an extra notice, never a loss).
"""
from ouroboros import obligations as o

UPGRADE_TYPES = frozenset({"reviewer_default_notice", "optional_bounds_notice", "legacy_memory_notice"})


def notice_id(chat_id, notice_type):
    return f"{int(chat_id)}:{notice_type}"


def recorded(root, chat_id, notice_type):
    return o.members(root, "upgrade_notices").get(notice_id(chat_id, notice_type), {}).get("recorded") is True


def record(root, row):
    kind = row.get("type")
    if row.get("direction") == "system" and kind in UPGRADE_TYPES:
        o.add(root, "upgrade_notices", notice_id(row["chat_id"], kind),
              {"chat_id": row["chat_id"], "type": kind, "ts": row.get("ts"), "recorded": True})


def import_upgrade_receipts(root):
    from pathlib import Path
    from ouroboros.utils import jsonl_chain_handles, iter_jsonl_objects
    records = {}
    with jsonl_chain_handles(Path(root) / "logs/chat.jsonl", strict=True) as handles:
        rows = [row for path, handle in handles for row in iter_jsonl_objects(path, _handle=handle)]
    for row in rows:
        kind = row.get("type")
        if row.get("direction") == "system" and kind in UPGRADE_TYPES:
            records[notice_id(row["chat_id"], kind)] = {"chat_id": row["chat_id"], "type": kind,
                                                      "ts": row.get("ts"), "recorded": True}
    return records
