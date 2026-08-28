"""ibl-3d7b7b7d5dc9: the scratchpad is no longer append-only.

``Memory`` gains explicit ``prune_scratchpad_block`` (by index or ts) plus
``pin_scratchpad_block`` / ``unpin_scratchpad_block``; pinned blocks are exempt
from the FIFO eviction at ``_SCRATCHPAD_MAX_BLOCKS`` but NOT from an explicit
prune. Every mutation is journaled (``block_pruned`` / ``block_pinned`` /
``block_unpinned``) so the audit trail distinguishes deliberate removal from
automatic eviction.
"""

from __future__ import annotations

import json

import pytest

from ouroboros.memory import Memory, _SCRATCHPAD_MAX_BLOCKS


def _mem(tmp_path):
    drive = tmp_path / "data"
    (drive / "memory").mkdir(parents=True)
    (drive / "logs").mkdir(parents=True)
    mem = Memory(drive_root=drive)
    mem.ensure_files()
    return mem


def _blocks(mem):
    bp = mem.scratchpad_blocks_path()
    return json.loads(bp.read_text(encoding="utf-8")) if bp.exists() else []


def _journal_types(mem):
    jp = mem.journal_path()
    if not jp.exists():
        return []
    out = []
    for line in jp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line).get("type"))
    return out


def test_prune_by_index(tmp_path):
    mem = _mem(tmp_path)
    for i in range(3):
        mem.append_scratchpad_block(f"block number {i}", source="task")
    mem.prune_scratchpad_block(index=1)
    remaining = [b["content"] for b in _blocks(mem)]
    assert remaining == ["block number 0", "block number 2"]
    assert "block_pruned" in _journal_types(mem)


def test_prune_by_ts(tmp_path):
    mem = _mem(tmp_path)
    blocks = [mem.append_scratchpad_block(f"content block {i}", source="task") for i in range(3)]
    target_ts = blocks[0]["ts"]
    mem.prune_scratchpad_block(ts=target_ts)
    assert target_ts not in {b["ts"] for b in _blocks(mem)}
    assert len(_blocks(mem)) == 2


def test_prune_requires_exactly_one_selector(tmp_path):
    mem = _mem(tmp_path)
    mem.append_scratchpad_block("a meaningful block", source="task")
    with pytest.raises(ValueError):
        mem.prune_scratchpad_block()
    with pytest.raises(ValueError):
        mem.prune_scratchpad_block(index=0, ts="2020-01-01T00:00:00+00:00")


def test_prune_index_out_of_range(tmp_path):
    mem = _mem(tmp_path)
    mem.append_scratchpad_block("the only block here", source="task")
    with pytest.raises(IndexError):
        mem.prune_scratchpad_block(index=5)


def test_prune_unknown_ts(tmp_path):
    mem = _mem(tmp_path)
    mem.append_scratchpad_block("the only block here", source="task")
    with pytest.raises(KeyError):
        mem.prune_scratchpad_block(ts="1999-01-01T00:00:00+00:00")


def test_pinned_block_is_exempt_from_fifo_eviction(tmp_path):
    mem = _mem(tmp_path)
    first = mem.append_scratchpad_block("pin me, I am important", source="task")
    mem.pin_scratchpad_block(first["ts"])
    # Overflow the cap: without the pin, `first` (oldest) would be evicted first.
    for i in range(_SCRATCHPAD_MAX_BLOCKS + 3):
        mem.append_scratchpad_block(f"filler block {i}", source="task")
    kept = _blocks(mem)
    assert first["ts"] in {b["ts"] for b in kept}
    assert len(kept) == _SCRATCHPAD_MAX_BLOCKS
    assert "block_pinned" in _journal_types(mem)
    assert "block_evicted" in _journal_types(mem)


def test_all_pinned_grows_past_cap(tmp_path):
    mem = _mem(tmp_path)
    for i in range(_SCRATCHPAD_MAX_BLOCKS):
        b = mem.append_scratchpad_block(f"pinned filler {i}", source="task")
        mem.pin_scratchpad_block(b["ts"])
    # Every block pinned -> nothing eligible to evict -> the list is allowed to grow.
    mem.append_scratchpad_block("one more over the cap", source="task")
    assert len(_blocks(mem)) == _SCRATCHPAD_MAX_BLOCKS + 1


def test_unpin_restores_eviction_candidacy(tmp_path):
    mem = _mem(tmp_path)
    first = mem.append_scratchpad_block("temporarily pinned block", source="task")
    mem.pin_scratchpad_block(first["ts"])
    mem.unpin_scratchpad_block(first["ts"])
    for i in range(_SCRATCHPAD_MAX_BLOCKS + 2):
        mem.append_scratchpad_block(f"filler block {i}", source="task")
    assert first["ts"] not in {b["ts"] for b in _blocks(mem)}
    assert "block_unpinned" in _journal_types(mem)


def test_pin_is_idempotency_checked(tmp_path):
    mem = _mem(tmp_path)
    b = mem.append_scratchpad_block("a block to pin twice", source="task")
    mem.pin_scratchpad_block(b["ts"])
    with pytest.raises(ValueError):
        mem.pin_scratchpad_block(b["ts"])


def test_unpin_unpinned_block_raises(tmp_path):
    mem = _mem(tmp_path)
    b = mem.append_scratchpad_block("a never-pinned block", source="task")
    with pytest.raises(ValueError):
        mem.unpin_scratchpad_block(b["ts"])


def test_pin_unknown_ts_raises(tmp_path):
    mem = _mem(tmp_path)
    mem.append_scratchpad_block("the only block here", source="task")
    with pytest.raises(KeyError):
        mem.pin_scratchpad_block("1999-01-01T00:00:00+00:00")


def test_explicit_prune_ignores_pin(tmp_path):
    mem = _mem(tmp_path)
    b = mem.append_scratchpad_block("pinned but prunable", source="task")
    mem.pin_scratchpad_block(b["ts"])
    # Pin protects from FIFO eviction only; an explicit prune still removes it.
    mem.prune_scratchpad_block(ts=b["ts"])
    assert _blocks(mem) == []
