"""The resident memory view of the acting mind: one render for every role (memory spec P3 §2).

The view replaces the old dialogue history and chat tail in a request. Block B carries my
story (``## My story``); block C the live part (``## Marks I keep in view``,
``## Live rooms`` and ``## This room (<label>) — head <n>``). Which parts a role sees is
one ``ViewSpec``: ``ROLE_DEFAULTS`` by role (Main, a root and a Project task are the
integrator; consciousness; Presence; a delegated child; a nanny), or an explicit spec a
task carries under ``memory_view``. The current room is the task's own room
(``dialogue_evidence.own_room_chat``); outside a Project and Main it is Main, except a
Presence turn, which sees its own conversation.

``capture_memory_view`` reads the facts once per request into a ``MemoryViewSnapshot``
(texts included), so rendering for another window or mode reads nothing again. It
activates the chronicle exactly once (the one legacy import, no model call). Until that
import has completed the view says so in one visible line, reads no chain and never
fails the task. What of memory is open and what is folded comes from
``memory_inventory`` only.

Nothing here publishes a record or calls a model.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import pathlib
from types import MappingProxyType, SimpleNamespace
from typing import Any, Dict, Mapping, Optional, Tuple

from ouroboros.chronicle_import import legacy_frontier
from ouroboros.chronicle_store import ChronicleStore
from ouroboros.contracts.chat_id_policy import WEB_UI_CHAT_ID
from ouroboros.dialogue_provenance import is_presence_task

log = logging.getLogger(__name__)

MAIN_ROOM = str(WEB_UI_CHAT_ID)
ROLES = ("integrator", "consciousness", "presence", "child", "nanny")
LIVE_ROOMS = ("none", "lines", "lines_with_words")
MARKS = ("all", "room_and_global", "none")
LEGACY_FILE = "memory/dialogue_blocks.json"
# The delegated child's role text (memory spec P2 §5, Opus R3), carried verbatim.
CHILD_ROLE_TEXT = ("Work from this assignment first — it is written to be enough; read memory or sources only to "
                   "fill a gap it leaves, and name what you read in your report.")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


# --- what a role sees ------------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class ViewSpec:
    """What one request's memory view holds (P3 §2.10; helpers' composition is P5's)."""
    role: str
    story: bool = True  # block B: the top level of my story
    room_id: Optional[str] = None  # the current room; None: no ``## This room``
    room_page: bool = True  # the room's retold records not yet folded, pages under my parts, my open notes
    room_lanes: bool = True  # the open conversation (lane 1) and one line per task (lane 2)
    origin_words: bool = True  # the owner's words that started a Project, when its rows are not open
    live_rooms: str = "lines"  # none | lines | lines_with_words
    marks: str = "all"  # all | room_and_global | none
    knowledge: bool = True  # overview, knowledge index, patterns, project journal (rendered by the context)
    owner_words: bool = False  # the owner's words that caused a helper's work (owner_words.py)


ROLE_DEFAULTS: Mapping[str, ViewSpec] = MappingProxyType({
    "integrator": ViewSpec("integrator"),
    "consciousness": ViewSpec("consciousness", room_page=False, room_lanes=False, origin_words=False),
    "presence": ViewSpec("presence", origin_words=False, live_rooms="none", marks="room_and_global"),
    "child": ViewSpec("child", room_lanes=False, live_rooms="none", marks="room_and_global", knowledge=False,
                      owner_words=True),
    "nanny": ViewSpec("nanny", story=False, room_lanes=False, live_rooms="none", marks="room_and_global",
                      knowledge=False, owner_words=True),
})
_ROOMLESS = frozenset({"consciousness"})
_FIELDS = {field.name: field.type for field in dataclasses.fields(ViewSpec)}


def view_role(task: Mapping[str, Any]) -> str:
    """The view role, first match wins, in ``knowledge.focus_signature``'s order.

    A nanny is the one dispatch fact (``_nanny_route_dispatched_for``), then a delegated
    child, a wake's own ledger category, a Presence turn; everything else integrates.
    The facts are read at the task's top level or under ``metadata``.
    """
    from ouroboros.consciousness_authority import CONSCIOUSNESS_CATEGORY
    from ouroboros.subagent_dispatch_notes import _nanny_route_dispatched_for  # D03->D07 is lazy-only

    meta = dict(_mapping(task.get("metadata")))
    if _nanny_route_dispatched_for(dict(task), None) or _nanny_route_dispatched_for(meta, None):
        return "nanny"
    if str(task.get("delegation_role") or meta.get("delegation_role") or "").strip().lower() == "subagent":
        return "child"
    if (task.get("usage_category") or meta.get("usage_category")) == CONSCIOUSNESS_CATEGORY:
        return "consciousness"
    if is_presence_task(task):
        return "presence"
    return "integrator"


def _explicit_problem(explicit: Any) -> str:
    if not isinstance(explicit, Mapping):
        return "memory_view is an object of ViewSpec fields"
    unknown = sorted(set(explicit) - set(_FIELDS))
    if unknown:
        return "unknown ViewSpec field(s): " + ", ".join(map(str, unknown))
    for name, value in explicit.items():
        if name == "role" and value not in ROLES:
            return f"role is one of {', '.join(ROLES)}"
        if name == "live_rooms" and value not in LIVE_ROOMS:
            return f"live_rooms is one of {', '.join(LIVE_ROOMS)}"
        if name == "marks" and value not in MARKS:
            return f"marks is one of {', '.join(MARKS)}"
        if name == "room_id" and not (value is None or isinstance(value, (str, int)) and not isinstance(value, bool)):
            return "room_id is a room id or null"
        if _FIELDS[name] == "bool" and not isinstance(value, bool):
            return f"{name} is true or false"
    return ""


def _task_ctx(task: Mapping[str, Any]) -> SimpleNamespace:
    """The namespace ``own_room_chat`` reads, built from a queue task record."""
    meta = dict(_mapping(task.get("metadata")))
    for key in ("parent_task_id", "root_task_id", "delegation_role"):
        if task.get(key) not in (None, ""):
            meta.setdefault(key, task[key])
    return SimpleNamespace(task_id=str(task.get("id") or task.get("task_id") or ""), task_metadata=meta,
                           current_chat_id=task.get("chat_id"))


def view_room_id(task: Mapping[str, Any], ctx: Any, drive_root: Any, *, project_chat_ids: Any,
                 presence: bool = False) -> str:
    """The current room of a view: ``own_room_chat`` (binding, then chat, then ancestors).

    A result outside the Projects and Main, or none (the hidden partition, another
    transport chat, a task without a chat), is Main, as the old focused view was; a
    Presence turn keeps its own conversation.
    """
    from ouroboros.dialogue_evidence import own_room_chat  # D03->D06 is lazy-only

    try:
        own = own_room_chat(ctx if ctx is not None else _task_ctx(task), drive_root)
    except (TypeError, ValueError, OSError):
        own = None
    if own is not None and (presence or str(own) == MAIN_ROOM or int(own) in set(project_chat_ids or ())):
        return str(own)
    return MAIN_ROOM


def view_spec_for_task(task: Mapping[str, Any], drive_root: Any, *, ctx: Any = None) -> ViewSpec:
    """The role's default spec, or the task's explicit ``memory_view`` after its fields check.

    An explicit spec with an unknown field or value is not used: the role default is,
    and the refusal is logged. Every role but consciousness gets its current room.
    """
    role = view_role(task)
    spec = ROLE_DEFAULTS[role]
    explicit = task.get("memory_view")
    if explicit is not None:
        problem = _explicit_problem(explicit)
        if problem:
            log.warning("memory_view of task %s refused, role default used: %s", task.get("id"), problem)
        else:
            spec = dataclasses.replace(spec, **dict(explicit))
    if spec.room_id is not None:
        return dataclasses.replace(spec, room_id=str(spec.room_id))
    if spec.role in _ROOMLESS:
        return spec
    from ouroboros.memory_inventory import membership_facts

    projects = membership_facts(drive_root).project_chat_ids
    return dataclasses.replace(spec, room_id=view_room_id(task, ctx, drive_root, project_chat_ids=projects,
                                                          presence=spec.role == "presence"))


# --- the captured facts ----------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class MemoryViewSnapshot:
    """Everything a render needs, read once per request; ``snapshot_json`` is its canonical form."""
    spec: ViewSpec
    store_status: Dict[str, Any]  # {"state": "active" | an import kind, "reason"?}
    frontier: Dict[str, Any]  # {"status", "pos"} of the legacy frontier
    story: Tuple[Dict[str, Any], ...] = ()  # legacy pointers, then pages and parts, in story order
    room: Optional[Dict[str, Any]] = None  # the current room's facts and texts
    live_rooms: Tuple[Dict[str, Any], ...] = ()
    marks: Tuple[Dict[str, Any], ...] = ()
    legacy_blocks: Dict[str, Any] = dataclasses.field(default_factory=dict)  # the story status facts
    fallback_refusals: Tuple[Dict[str, Any], ...] = ()
    owner_words: str = ""  # the rendered block of the owner's words that caused a helper's work

    @property
    def active(self) -> bool:
        return self.store_status.get("state") == "active"


_TUPLES = ("story", "live_rooms", "marks", "fallback_refusals")


def snapshot_json(snapshot: MemoryViewSnapshot) -> str:
    """The canonical JSON of a snapshot (sorted keys): the context core keeps it and hashes it."""
    return json.dumps(dataclasses.asdict(snapshot), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def snapshot_from_json(text: str) -> MemoryViewSnapshot:
    data = json.loads(text)
    data["spec"] = ViewSpec(**data["spec"])
    for name in _TUPLES:
        data[name] = tuple(data.get(name) or ())
    return MemoryViewSnapshot(**data)


def _owner_words_block(task: Mapping[str, Any]) -> str:
    from ouroboros.owner_words import render_owner_words, task_governing_words  # D03->D17 is lazy-only

    meta = _mapping(task.get("metadata"))
    root = str(task.get("root_task_id") or meta.get("root_task_id") or "")
    rows, absent = task_governing_words(task)
    return render_owner_words(rows, absent, audience="child", root_task_id=root)


def _activation(store: ChronicleStore) -> Dict[str, Any]:
    """``{"state": "active"}`` after the one legacy import, else the import's kind and reason."""
    try:
        receipt = store.ensure_activated()
    except Exception as exc:  # the store's own refusal to open its journal; the view must still render
        log.warning("memory view: the chronicle cannot be activated", exc_info=True)
        return {"state": "journal_unreadable", "reason": f"{type(exc).__name__}: {exc}"}
    if isinstance(receipt, dict) and receipt.get("kind") == "activation":
        return {"state": "active"}
    receipt = receipt if isinstance(receipt, dict) else {}
    return {"state": str(receipt.get("kind") or "import_failed"),
            "reason": str(receipt.get("reason") or receipt.get("kind") or "no activation receipt")}


def capture_memory_view(drive_root: Any, task: Mapping[str, Any], spec: ViewSpec) -> MemoryViewSnapshot:
    """The facts of one request's view, read once; the chronicle is activated exactly once here.

    Before the import has completed (another importer holds the legacy lock, the
    import was refused, the journal cannot be opened) the snapshot carries only the
    reason and the room id: no story, lanes or live rooms, and no chain read.
    """
    root = pathlib.Path(drive_root)
    owner_words = _owner_words_block(task) if spec.owner_words else ""
    status = _activation(ChronicleStore(root))
    room = {"room_id": spec.room_id} if spec.room_id is not None else None
    if status["state"] != "active":
        return MemoryViewSnapshot(spec=spec, store_status=status, frontier={}, room=room, owner_words=owner_words)
    frontier = legacy_frontier(ChronicleStore(root))
    return MemoryViewSnapshot(spec=spec, store_status=status,
                              frontier={"status": frontier.get("status"), "pos": frontier.get("pos")},
                              room=room, owner_words=owner_words)


# --- the role line ----------------------------------------------------------------------------------

def working_sources_line(spec: ViewSpec, snapshot: Optional[MemoryViewSnapshot] = None) -> str:
    """A helper's ``## Working sources`` section, listed from the same spec that drew its view.

    The list cannot disagree with the view: every loaded item is a field of ``spec``,
    and what a field leaves out is named as not loaded, with the readers that reach it.
    """
    room = snapshot.room if snapshot is not None and isinstance(snapshot.room, dict) else {}
    where = str(room.get("label") or (f"chat {spec.room_id}" if spec.room_id is not None else ""))
    loaded = ["the Constitution and system prompt", "the navigation of both books", "your identity"]
    missing = []
    (loaded if spec.story else missing).append("the top level of your story")
    if spec.room_id is not None and spec.room_page:
        loaded.append(f"the page of your parent's room {where}")
    if spec.room_id is not None and spec.room_lanes:
        loaded.append(f"the open conversation of {where}")
    else:
        missing.append("raw conversations")
    if spec.room_id is not None and spec.origin_words:
        loaded.append("the words that started that work")
    if spec.marks != "none":
        loaded.append("the memory marks of that room and global ones" if spec.marks == "room_and_global"
                      else "all memory marks")
    if spec.live_rooms != "none":
        loaded.append("one line per other live room")
    else:
        missing.append("other rooms' pages")
    (loaded if spec.knowledge else missing).append("knowledge (overview, index, patterns)")
    if spec.owner_words:
        loaded.append("the words of my human that caused this work")
    missing += ["the global scratchpad", "earlier task reports"]
    return ("## Working sources\n\n"
            f"Loaded above: {', '.join(loaded)}; your own recent process is loaded below. "
            f"Not loaded: {', '.join(missing)}; memory_read, chat_history, knowledge_read and get_task_result "
            "reach them, as they reach your parent.\n" + CHILD_ROLE_TEXT)
