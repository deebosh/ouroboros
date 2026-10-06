"""Named ingress acceptance: one canonical inbound row per message id, landed before any dispatch.

The owner of a named owner/skill message's acceptance (ARCHITECTURE 04 "WebSocket chat", 12
"Operation correlation"): a same-id redelivery rejoins the accepted row when its words and ordered
attachment content match and is refused otherwise, ``_INGRESS_LOCK`` serializes check → canonical
row → dispatch, and only THIS process knows what happened to a row's dispatch: ``_UNDISPATCHED``
(its write raised before dispatch, so one retry hands it over) and the row's ``ingress_process``
stamp (which process accepted it — ``accepted_here``). ``_DISPATCH_ENTERED`` records actual
call entry separately; acceptance cannot prove it, even in this process. These facts do not
survive the process, and nothing durable records dispatch. ``supervisor.message_bus`` re-exports
the public names; its ``DATA_DIR`` and ``log_chat`` are read at call time.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional, Tuple

from ouroboros.chat_uploads import attachment_views, same_message, stored_refs
from ouroboros.utils import utc_now_iso

log = logging.getLogger(__name__)

_INGRESS_LOCK = threading.Lock()
# Named acceptances (web or skill) whose canonical write raised in THIS process: the row may have
# landed and dispatch was never entered — positive proof, which no row from before a restart has.
# Operation reads say ``lost`` and history marks the row ``ingress_undispatched``; the first explicit
# same-id retry of the same message takes the proof and dispatches the row once, and a later landed
# write of the id clears it. The process's end retires the rest: then nothing is known, nothing replays.
_UNDISPATCHED: "set[Tuple[int, str]]" = set()
# Positive call-entry evidence, separate from acceptance and never persisted.
_DISPATCH_ENTERED: set[tuple] = set()


def _dispatch_key(row: dict) -> tuple:
    return tuple(row.get(key) for key in ("ingress_process", "chat_id", "client_message_id", "ts"))


def dispatch_entered(row: dict) -> bool:
    return accepted_here(row) and _dispatch_key(row) in _DISPATCH_ENTERED


def _enter_dispatch(row: dict) -> None:
    _DISPATCH_ENTERED.add(_dispatch_key(row))


def acceptance_undispatched(chat_id: int, client_message_id: str) -> bool:
    return (int(chat_id), str(client_message_id)) in _UNDISPATCHED


def accepted_here(row: dict) -> bool:
    """True when THIS host process wrote that named acceptance row (``log_chat`` stamps the process
    generation as ``ingress_process``). This proves acceptance only, never dispatch entry;
    a row from an ended process (a restart came between) or before the stamp is unknown."""
    from ouroboros.process_custody import current_custody_session_id

    stamp = str((row or {}).get("ingress_process") or "")
    return bool(stamp) and stamp == current_custody_session_id()


def _write_marked(key: Tuple[int, str], write) -> dict:
    """A named acceptance's canonical write, under ``_INGRESS_LOCK`` before any dispatch: a raise
    marks the id undispatched (positive proof); a write that lands clears the mark."""
    try:
        row = write()
    except BaseException:
        if key[1]:
            _UNDISPATCHED.add(key)
        raise
    _UNDISPATCHED.discard(key)
    return row


def _take_undispatched(key: Tuple[int, str]) -> bool:
    """Under ``_INGRESS_LOCK``: consume that proof — True for one retry, which hands the row over."""
    found = key in _UNDISPATCHED
    _UNDISPATCHED.discard(key)
    return found


def accepted_chat_message(drive_root, chat_id: int, client_message_id: str) -> Optional[dict]:
    """Read a named canonical source across its retained generation chain.

    Every owner web message asks this once, so a segment that cannot hold the id
    is skipped unparsed: a plain printable-ASCII id is written verbatim by any JSON
    encoder, and a segment without those bytes has no row naming it.
    """
    from pathlib import Path
    from ouroboros.utils import iter_jsonl_objects, jsonl_chain_handles

    plain = all(" " <= char <= "~" and char not in '"\\' for char in client_message_id)
    needle = client_message_id.encode("ascii") if plain and client_message_id else b""
    with jsonl_chain_handles(Path(drive_root) / "logs" / "chat.jsonl", strict=True) as handles:
        for path, handle in reversed(handles):
            if needle:
                if needle not in handle.read():
                    continue
                handle.seek(0)
            for row in iter_jsonl_objects(path, _handle=handle):
                if (row.get("direction") == "in" and row.get("chat_id") == chat_id
                        and row.get("client_message_id") == client_message_id):
                    return row
    return None


def _accepted_web_message(chat_id: int, client_message_id: str) -> Optional[dict]:
    """The accepted row a web frame's own id already names (call under ``_INGRESS_LOCK``).

    Absent history is a new message; a chain that cannot be READ is unknown: the error
    refuses the frame before any claim, write or dispatch (never a second row).
    """
    from supervisor import message_bus

    if not client_message_id or message_bus.DATA_DIR is None:
        return None
    try:
        return accepted_chat_message(message_bus.DATA_DIR, chat_id, client_message_id)
    except OSError:
        log.warning("Web ingress refused id %r: retained chat is unreadable", client_message_id, exc_info=True)
        raise


def accept_local_message(bridge, drive_root, text: str, *, retain_inputs=None, dispatch=None, **message) -> tuple[dict, bool]:
    """Accept a named skill delivery once, then schedule/queue its exact source: ``(row, rejoined)``.
    The ingress lock serializes receipt-before-dispatch. A same-id retry rejoins without dispatch unless
    it takes the proof that its row never entered dispatch (``_UNDISPATCHED``): it dispatches, not
    ``rejoined``. After a crash, reads disclose a lost host session; nothing redispatches.
    """
    from ouroboros.project_dialogue import build_owner_message_ref
    from supervisor import message_bus

    chat_id = int(message["chat_id"])
    message_id = str(message["client_message_id"])
    source = str(message["source"])
    logged = text.strip() or str(message.get("image_caption") or "").strip() or (
        "(image attached)" if message.get("image_base64")
        else "(file attached)" if (message.get("task_metadata") or {}).get("chat_attachment_uploads") else ""
    )
    if not logged:
        raise ValueError("message is empty")
    refs = stored_refs((message.get("task_metadata") or {}).get("chat_attachments"))
    placeholder = not text.strip() and not str(message.get("image_caption") or "").strip()
    with _INGRESS_LOCK:
        row = accepted_chat_message(drive_root, chat_id, message_id)
        if row is not None:
            if row.get("source") != source:
                raise ValueError("client_message_id is already bound to another source")
            if not same_message(row, logged, refs):  # text AND ordered attachment content
                raise ValueError("client_message_id was already used for a different message")
            if not _take_undispatched((chat_id, message_id)):
                return row, True
            # Proven never dispatched: this retry's identical copies feed it; the echo shows the row's refs.
            if (message.get("task_metadata") or {}).get("chat_attachments"):
                message["task_metadata"] = {**message["task_metadata"], "chat_attachments": row.get("attachments")}
        ts = str(row.get("ts") or "") if row else utc_now_iso()
        try:
            row = row or _write_marked((chat_id, message_id), lambda: message_bus.log_chat(
                "in", chat_id, int(message.get("user_id") or 0), logged, ts=ts,
                source=source, client_message_id=message_id,
                sender_label=str(message.get("sender_label") or ""),
                transport=message.get("transport"), drive_root=drive_root, require_write=True,
                ensure_record_boundary=True,  # parseable acceptance record
                message_meta={"attachments": refs, "text_placeholder": placeholder},
            ))
        finally:
            # Once this write is attempted (or a proven-undispatched row is handed over), failure can
            # leave canonical bytes. Transfer input custody without claiming acceptance or queue
            # success; a replay/pre-write refusal never adopts this request's fresh copies.
            if retain_inputs is not None:
                retain_inputs()
        ref = build_owner_message_ref(chat_id=chat_id, client_message_id=message_id, ts=ts, text=logged)
        # record_inbound_message gets the row witness and receipt time.
        # Dispatch only schedules/queues; slow work must not hold the ingress lock.
        _enter_dispatch(row)
        (dispatch or bridge.enqueue_local_message)(text, **message, accepted_source_ref=ref, accepted_source_row=row, received_at=ts)
        return row, False


def record_inbound_message(bridge, message: dict, *, chat_id: int, user_id: int,
                           client_message_id: str, text: str, ts: str) -> Optional[dict]:
    """Keep one canonical ingress writer for dequeued and preaccepted messages."""
    from ouroboros.project_dialogue import (
        _text_sha256, build_owner_message_ref, entry_matches_source_ref, owner_message_ref_is_valid,
    )
    from supervisor import message_bus

    source = str(message.get("source") or "web")
    ref = message.get("accepted_source_ref")
    if ref:
        # A web acceptance (socket or late quiz answer) wrote this row in-process
        # before enqueue; its returned row is the item's exact witness. A skill
        # source, or a web item without a witness (the queue's empty default is
        # absent, not a row to match), re-reads the retained disk row.
        accepted_row = message.get("accepted_source_row")
        row = (accepted_row if source == "web" and isinstance(accepted_row, dict) and accepted_row
               else accepted_chat_message(message_bus.DATA_DIR, chat_id, client_message_id)) if owner_message_ref_is_valid(ref) else None
        if (not row or row.get("source") != source or row.get("chat_id") != chat_id
                or row.get("client_message_id") != client_message_id or not entry_matches_source_ref(row, [ref])
                or ref["text_sha256"] != _text_sha256(text)):
            raise ValueError("accepted source does not match the queued message")
        ref = dict(ref)
        ts = ref["ts"]
        placeholder = row.get("text_placeholder") is True
    elif message.get("suppress_chat_log"):
        return None
    else:
        metadata = message.get("task_metadata") or {}
        # no words or caption from the sender: TEXT is the host's placeholder
        placeholder = not str(message.get("text") or "").strip() and not str(message.get("image_caption") or "").strip()
        message_bus.log_chat(
            "in", chat_id, user_id, text, ts=ts, source=source,
            sender_label=str(message.get("sender_label") or ""),
            sender_session_id=str(message.get("sender_session_id") or ""),
            client_message_id=client_message_id, transport=message.get("transport"),
            client_surface=(metadata.get("client_surface") if isinstance(metadata, dict)
                            and isinstance(metadata.get("client_surface"), dict) else None),
            message_meta={"attachments": metadata.get("chat_attachments") if isinstance(metadata, dict) else None,
                          "text_placeholder": placeholder},
        )
        ref = build_owner_message_ref(chat_id=chat_id, client_message_id=client_message_id, ts=ts, text=text)
    if source != "web":
        # A parked inline photo is one of the views: one bubble, never a second photo frame;
        # its text is the row's, placeholder mark included, as history replays it.
        views = attachment_views((message.get("task_metadata") or {}).get("chat_attachments"))
        bridge.broadcast({
            "type": "photo" if message.get("image_base64") and not views else "chat", "role": "user",
            "content": text if views and placeholder else str(message.get("text") or ""),
            "caption": str(message.get("image_caption") or ""), **({"text_placeholder": True} if views and placeholder else {}),
            "image_base64": "" if views else str(message.get("image_base64") or ""), **({"attachments": views} if views else {}),
            "mime": str(message.get("image_mime") or "image/jpeg"), "ts": ts, "source": source,
            "sender_label": str(message.get("sender_label") or ""),
            "sender_session_id": str(message.get("sender_session_id") or ""),
            "client_message_id": client_message_id, "transport": message.get("transport") or {},
            "chat_id": chat_id,
        })
    return ref
