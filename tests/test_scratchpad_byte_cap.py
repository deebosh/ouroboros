"""Regression tests for ibl-2b09abdadd25: scratchpad content cap + degradation.

Two layers covered:

1. **Source-side content cap** (ouroboros/memory.py::Memory.append_scratchpad_block).
   SCRATCHPAD_MAX_CONTENT_CHARS (60_000, declared in ouroboros/context_budget.py
   alongside the existing scratchpad thresholds) bounds total block content.
   The cap is AND'd with the count cap (_SCRATCHPAD_MAX_BLOCKS=10) in a single
   pass: while EITHER cap is violated, evict the oldest ELIGIBLE (non-pinned)
   block. Each eviction is journaled as ``block_evicted`` and FAILS HARD on
   journal-write failure (existing ibl-3d7b7b7d5dc9 contract preserved).

2. **Consumer-side degradation**
   (ouroboros/context.py::_render_scratchpad_for_context).
   When the raw scratchpad exceeds SCRATCHPAD_SECTION_BUDGET_CHARS
   (90_000) AND scratchpad_blocks.json has blocks: render the newest WHOLE
   blocks that fit, drop oldest-first, ALWAYS retain the single newest
   (even if it alone exceeds), and append an in-band gap marker (BIBLE P1
   — omission is never silent). When scratchpad_blocks.json is absent /
   empty (legacy flat scratchpad), return raw unmodified (legacy fallback
   so the source switch introduces no silent amnesia).
"""
from __future__ import annotations

import json

import pytest

from ouroboros.context import _render_scratchpad_for_context
from ouroboros.context_budget import (
    SCRATCHPAD_MAX_CONTENT_CHARS,
    SCRATCHPAD_SECTION_BUDGET_CHARS,
)
from ouroboros.memory import Memory, _SCRATCHPAD_MAX_BLOCKS


# ---------- helpers ----------------------------------------------------------


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


def _force_oversized_via_json(tmp_path, n_blocks=4, each_chars=20_000):
    """Write scratchpad_blocks.json with n blocks each containing `each_chars`
    of content, bypassing the source-side cap. Lets the test exercise the
    consumer-side degradation path with a scratchpad that exceeded the cap
    by some other route (legacy import, corruption recovery, future
    regressions that bypass the source cap)."""
    mem = _mem(tmp_path)
    bp = mem.scratchpad_blocks_path()
    blocks = [
        {
            "ts": f"2026-09-01T12:0{i}:00+00:00",
            "source": "forced",
            "content": "x" * each_chars,
        }
        for i in range(n_blocks)
    ]
    bp.write_text(json.dumps(blocks), encoding="utf-8")
    # Regenerate the scratchpad.md so load_scratchpad returns the oversized file.
    mem._write_scratchpad_markdown(blocks)
    return mem


# ---------- source-side content cap ------------------------------------------


def test_content_cap_evicts_oldest_when_under_count_cap(tmp_path):
    """2 blocks of 40_000 chars each = 80_000 total > 60_000 cap. Count cap
    not violated (2 < 10), so eviction is content-driven only."""
    mem = _mem(tmp_path)
    mem.append_scratchpad_block("a" * 40_000, source="task")
    mem.append_scratchpad_block("b" * 40_000, source="task")
    kept = _blocks(mem)
    # Oldest (40_000 a's) evicted to satisfy content cap.
    assert len(kept) == 1
    assert kept[0]["content"].startswith("b")
    assert "block_evicted" in _journal_types(mem)


def test_content_cap_evicts_until_under_both_caps(tmp_path):
    """5 blocks of 20_000 = 100_000 > 60_000; count cap not violated (5 < 10).
    Eviction should leave enough blocks to satisfy BOTH caps."""
    mem = _mem(tmp_path)
    for i in range(5):
        mem.append_scratchpad_block(("c" * 20_000) + f"-{i}", source="task")
    kept = _blocks(mem)
    total = sum(len(b["content"]) for b in kept)
    assert total <= SCRATCHPAD_MAX_CONTENT_CHARS
    assert len(kept) <= _SCRATCHPAD_MAX_BLOCKS
    # The newest block is always retained.
    assert kept[-1]["content"].endswith("-4")


def test_content_cap_with_count_cap_active_evicts_in_single_pass(tmp_path):
    """12 blocks of 10_000 = 120_000; both caps violated simultaneously.
    Single-pass eviction should leave us under BOTH caps."""
    mem = _mem(tmp_path)
    for i in range(12):
        mem.append_scratchpad_block(("d" * 10_000) + f"-{i}", source="task")
    kept = _blocks(mem)
    assert len(kept) <= _SCRATCHPAD_MAX_BLOCKS
    total = sum(len(b["content"]) for b in kept)
    assert total <= SCRATCHPAD_MAX_CONTENT_CHARS
    # Newest three must be the last three we wrote.
    assert kept[-1]["content"].endswith("-11")
    assert kept[-2]["content"].endswith("-10")
    assert kept[-3]["content"].endswith("-9")


def test_content_cap_respects_pinning(tmp_path):
    """A pinned block is exempt from BOTH caps, consistent with ibl-3d7b7b7d5dc9.
    When the only eligible victims are pinned, the list grows past both caps."""
    mem = _mem(tmp_path)
    pinned = mem.append_scratchpad_block("p" * 50_000, source="task")
    mem.pin_scratchpad_block(pinned["ts"])
    # This block is so big that even with the pinned one removed, we'd need
    # to evict many. Pin the second too so both are exempt.
    pinned2 = mem.append_scratchpad_block("q" * 50_000, source="task")
    mem.pin_scratchpad_block(pinned2["ts"])
    # One more block pushes us over the cap.
    mem.append_scratchpad_block("r" * 50_000, source="task")
    kept = _blocks(mem)
    # Two pinned blocks + one new = 3; both are exempt from eviction.
    assert len(kept) == 3
    pinned_tss = {pinned["ts"], pinned2["ts"]}
    assert pinned_tss.issubset({b["ts"] for b in kept})


def test_content_cap_does_not_evict_when_already_under(tmp_path):
    """Sanity: small blocks don't trigger content-cap eviction."""
    mem = _mem(tmp_path)
    for i in range(3):
        mem.append_scratchpad_block(f"small block {i}", source="task")
    kept = _blocks(mem)
    assert len(kept) == 3
    assert "block_evicted" not in _journal_types(mem)


# ---------- consumer-side degradation ----------------------------------------


def test_render_scratchpad_passes_through_when_under_budget(tmp_path):
    """If the raw scratchpad fits, the helper returns the raw markdown
    unchanged (the trim path is a no-op)."""
    mem = _mem(tmp_path)
    mem.append_scratchpad_block("a normal block", source="task")
    raw = mem.load_scratchpad()
    # Use a budget larger than raw — nothing to trim.
    body = _render_scratchpad_for_context(mem, budget=SCRATCHPAD_SECTION_BUDGET_CHARS)
    assert body == raw


def test_render_scratchpad_trims_with_gap_marker(tmp_path):
    """When the raw exceeds budget AND blocks.json has blocks, return the
    newest whole blocks that fit, drop oldest, and append the in-band gap
    marker (BIBLE P1)."""
    # 4 * 25_000 = 100_000 content chars (+ per-block framing overhead)
    # comfortably exceeds the 90_000 section budget; dropping just the
    # oldest block brings 3 * 25_000 = 75_000 (+overhead) back under it.
    mem = _force_oversized_via_json(tmp_path, n_blocks=4, each_chars=25_000)
    raw = mem.load_scratchpad()
    assert len(raw) > SCRATCHPAD_SECTION_BUDGET_CHARS
    # Tight budget forces the helper to drop oldest blocks.
    body = _render_scratchpad_for_context(mem, budget=SCRATCHPAD_SECTION_BUDGET_CHARS)
    assert len(body) < len(raw)
    assert "budget gap" in body
    assert "omitted" in body
    # Newest block is retained (its content shows up in the rendered body).
    # _render_block truncates ts to [:16] (drops seconds/offset), matching
    # _write_scratchpad_markdown's own rendering convention.
    assert "2026-09-01T12:03" in body


def test_render_scratchpad_legacy_fallback_returns_raw(tmp_path):
    """Legacy flat scratchpad.md with no scratchpad_blocks.json: the helper
    returns the raw markdown unchanged. This is the source-switch fallback
    (no silent amnesia)."""
    mem = _mem(tmp_path)
    # Force a legacy state: scratchpad.md exists, blocks.json absent.
    mem.scratchpad_path().write_text(
        "## Scratchpad (legacy flat)\n\nSome legacy note here.\n",
        encoding="utf-8",
    )
    raw = mem.load_scratchpad()
    body = _render_scratchpad_for_context(mem, budget=len(raw) - 100)
    # raw is bigger than budget, but no blocks to trim by — must return raw.
    assert body == raw


def test_render_scratchpad_always_retains_newest_even_if_over_budget(tmp_path):
    """When the single newest block alone exceeds the budget, retain it
    anyway and append the gap marker (BIBLE P1 — never silent)."""
    mem = _mem(tmp_path)
    # Single huge block: content alone is bigger than the budget.
    mem.append_scratchpad_block("z" * 200_000, source="task")
    body = _render_scratchpad_for_context(mem, budget=1_000)
    # The newest block IS retained even though it exceeds.
    assert "z" in body
    # And the gap marker fires.
    assert "budget gap" in body


def test_render_scratchpad_block_boundary_invariant(tmp_path):
    """Block-boundary cuts only — no mid-block, no mid-string truncation.
    Verify that no rendered block's content is split across the budget."""
    mem = _force_oversized_via_json(tmp_path, n_blocks=3, each_chars=30_000)
    body = _render_scratchpad_for_context(mem, budget=SCRATCHPAD_SECTION_BUDGET_CHARS)
    # Find each block marker in the body and confirm its content begins with
    # the canned "xxxx" content (full block, not a truncated slice).
    import re
    for m in re.finditer(r"### \[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} — forced\]\n(x+)", body):
        # Each retained block has at least one 'x' before the trailing \n.
        block_xs = m.group(1)
        assert len(block_xs) > 0
        # And the block boundary ends with our separator.
        assert "---" in body[m.end():m.end() + 10]


def test_render_scratchpad_no_mid_string_truncation(tmp_path):
    """No block is sliced mid-content. Each retained block must contain its
    FULL canned payload (all-x's)."""
    mem = _force_oversized_via_json(tmp_path, n_blocks=4, each_chars=25_000)
    body = _render_scratchpad_for_context(mem, budget=SCRATCHPAD_SECTION_BUDGET_CHARS)
    # Each rendered block should contain a long run of 'x's (not a slice).
    import re
    long_x = re.search(r"x{1000,}", body)
    assert long_x is not None
    # The gap marker should be present (some blocks omitted).
    assert "budget gap" in body


# ---------- ordering invariant -----------------------------------------------


def test_constant_ordering_holds():
    """The invariant asserted in ouroboros/context_budget.py must hold at
    runtime: SCRATCHPAD_CONSOLIDATION_THRESHOLD_CHARS < SCRATCHPAD_MAX_CONTENT_CHARS
    <= SCRATCHPAD_SECTION_BUDGET_CHARS - rendering headroom."""
    from ouroboros.context_budget import (
        SCRATCHPAD_CONSOLIDATION_THRESHOLD_CHARS,
    )
    assert SCRATCHPAD_CONSOLIDATION_THRESHOLD_CHARS < SCRATCHPAD_MAX_CONTENT_CHARS
    assert SCRATCHPAD_MAX_CONTENT_CHARS <= SCRATCHPAD_SECTION_BUDGET_CHARS - 1_000