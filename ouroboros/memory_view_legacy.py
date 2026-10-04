"""How my story names the old memory a helper retold before the update (memory spec P3 §2.2, §2.6 F3).

A retold record is one line that says what its address holds: the room, the period, how
many of that room's chat rows it retells (where none is established, the old writer's own
message count) and the length of the retelling, then the ``memory_read`` call that reads
it. A journal gap is named with its detail. Under the physical floor (F3) a room's records
are one line: the room, its whole period, their total length and every id.

Only facts of the record, never a reason to read it. Nothing here reads a file.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def retold_size(entry: Mapping[str, Any]) -> str:
    """``4 rows retold in 1234 chars``; without rows of its room, the old writer's message count if it kept one."""
    rows, said, chars = entry.get("rows"), entry.get("messages"), entry.get("chars")
    if rows:
        held = _count(rows, "row") + " retold"
    elif type(said) is int and said > 0:
        held = _count(said, "message") + " retold (the old writer's count)"
    else:
        held = ("no row of this room in that period; " if rows == 0 else "") + "retold"
    return held + (f" in {chars} chars" if chars else "")


def pointer_line(entry: Mapping[str, Any]) -> str:
    read = f"memory_read(node_id='{entry['id']}')"
    if entry.get("gap"):
        return f"- memory gap: {entry['label']}; {entry['period']}; {entry['gap']}; {read}"
    return f"- {entry['label']}; {entry['period']}; {retold_size(entry)}; {read}"


def pointer_rooms(story: Any) -> Dict[str, List[Dict[str, Any]]]:
    rooms: Dict[str, List[Dict[str, Any]]] = {}
    for entry in story:
        if entry.get("kind") == "legacy" and not entry.get("gap"):
            rooms.setdefault(str(entry["room_id"]), []).append(entry)
    return rooms


def room_pointer(entries: List[Dict[str, Any]]) -> str:
    """A room's retold records as one line: the room, its whole period, their length, every id (F3)."""
    spans = [entry["span"] for entry in entries if entry.get("span")]
    period = "; ".join(([f"{min(s[0] for s in spans)} → {max(s[1] for s in spans)}"] if spans else [])
                       + sorted({entry["period"] for entry in entries if not entry.get("span")}))
    chars = sum(entry.get("chars") or 0 for entry in entries)
    return (f"- {entries[0]['label']}; {period}; {_count(len(entries), 'retold record')}"
            + (f" in {chars} chars" if chars else "") + ": "
            + ", ".join(entry["id"] for entry in entries) + "; memory_read(node_id=<id>) reads each")


def pointer_lines(pointers: List[Dict[str, Any]], rooms: Any) -> List[str]:
    """The pointers in story order; a room the floor took is one line where its first pointer stood."""
    grouped, lines = pointer_rooms(pointers), []
    for entry in pointers:
        room = str(entry["room_id"])
        if entry.get("gap") or room not in rooms:
            lines.append(pointer_line(entry))
        elif grouped[room][0] is entry:
            lines.append(room_pointer(grouped[room]))
    return lines
