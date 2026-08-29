"""Tests for ibl-consciousness-context-overflow structural fix.

The fix has three observable surfaces:

1. ``_ConsciousnessOverflow`` is a typed ``OverflowError`` subclass carrying
   per-section attribution (total_chars / max_chars / mode / sections list /
   pre-sorted top_contributors). It IS an OverflowError so existing callers
   that catch ``OverflowError`` still match.

2. ``BackgroundConsciousness._build_context`` tracks every part it appends,
   stashes the breakdown on ``self._last_context_sections`` for any
   post-mortem consumer, and raises the typed overflow (not a bare
   ``OverflowError``) on over-budget.

3. ``BackgroundConsciousness._think_scoped`` emits a structured
   ``consciousness_context_overflow`` event with ``total_chars``,
   ``max_chars``, ``mode``, full ``sections`` list and a pre-sorted
   ``top_contributors`` field — so the owner can see WHICH sections crossed
   the limit without re-running the wakeup.

4. Mode-aware assembly honours ``OUROBOROS_CONTEXT_MODE`` in BG: in ``low``
   the improvement backlog digest and ephemeral observations are skipped
   (not P1 cognitive artifacts), the ARCHITECTURE navigation-map vs full
   split is delegated to ``build_governance_sections``.

Run: ``pytest tests/test_consciousness_section_diagnostics.py -v``
"""

from __future__ import annotations

import json
import queue
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_repo_and_drive(tmp_path: Path) -> tuple[Path, Path]:
    """Build a minimal fake repo + drive tree for BackgroundConsciousness."""
    repo_dir = tmp_path / "repo"
    (repo_dir / "docs").mkdir(parents=True)
    (repo_dir / "BIBLE.md").write_text(
        "# BIBLE\n\n" + ("x" * 100_000), encoding="utf-8"
    )
    (repo_dir / "docs" / "ARCHITECTURE.md").write_text(
        "# ARCH\n\n" + ("y" * 300_000), encoding="utf-8"
    )
    (repo_dir / "prompts").mkdir(parents=True)
    (repo_dir / "prompts" / "CONSCIOUSNESS.md").write_text(
        "# CONSCIOUSNESS\n\nthink", encoding="utf-8"
    )
    drive_root = tmp_path / "data"
    (drive_root / "logs").mkdir(parents=True)
    (drive_root / "state").mkdir(parents=True)
    (drive_root / "state" / "state.json").write_text("{}", encoding="utf-8")
    (drive_root / "memory").mkdir(parents=True)
    return repo_dir, drive_root


def _make_bc(tmp_path: Path, *, mode: str | None = None, monkeypatch: pytest.MonkeyPatch | None = None):
    """Build a BackgroundConsciousness with mocked registry.

    Pass ``monkeypatch`` so mode mutation auto-restores across tests; we use
    ``monkeypatch.setenv`` instead of bare ``os.environ[...] = ...`` to avoid
    leaking the env var into adjacent test files (pytest monkeypatch undoes
    at function teardown; bare assignment does NOT).
    """
    if mode is not None:
        if monkeypatch is None:
            pytest.skip("_make_bc requires monkeypatch when mode is set")
        monkeypatch.setenv("OUROBOROS_CONTEXT_MODE", mode)
    repo_dir, drive_root = _seed_repo_and_drive(tmp_path)
    eq: queue.Queue = queue.Queue()
    from ouroboros.consciousness import BackgroundConsciousness

    bc = BackgroundConsciousness(
        drive_root=drive_root,
        repo_dir=repo_dir,
        event_queue=eq,
        owner_chat_id_fn=lambda: 1,
    )
    bc._build_registry = lambda: MagicMock()  # type: ignore[method-assign]
    return bc, eq, drive_root


def _read_events(drive_root: Path) -> List[Dict[str, Any]]:
    """Load every consciousness_context_overflow row from the events log."""
    events_file = drive_root / "logs" / "events.jsonl"
    if not events_file.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in events_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") == "consciousness_context_overflow":
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# 1. Overflow event shape
# ---------------------------------------------------------------------------


def test_overflow_event_carries_per_section_attribution(tmp_path, monkeypatch):
    """When _build_context overflows, the event must name top contributors."""
    bc, _, drive_root = _make_bc(tmp_path)
    # Force an overflow by lowering the max threshold to a value the seeded
    # ARCHITECTURE (300k) + BIBLE (100k) + memory + governance assembly will exceed.
    monkeypatch.setattr("ouroboros.consciousness.BG_CONTEXT_MAX_CHARS", 50_000)

    with patch.object(bc, "_build_registry", return_value=MagicMock()):
        result = bc._think_scoped()

    assert result is False
    events = _read_events(drive_root)
    assert events, "no overflow event recorded"
    evt = events[-1]

    # New diagnostic fields are present.
    assert "sections" in evt and isinstance(evt["sections"], list)
    assert "top_contributors" in evt and isinstance(evt["top_contributors"], list)
    assert "total_chars" in evt and "max_chars" in evt and "mode" in evt
    assert evt["max_chars"] == 50_000
    assert evt["total_chars"] > evt["max_chars"]

    # sections is a list of {name, chars} dicts.
    assert all(
        isinstance(s, dict) and isinstance(s.get("name"), str) and isinstance(s.get("chars"), int)
        for s in evt["sections"]
    )
    # top_contributors is sorted desc by chars.
    chars_seq = [c["chars"] for c in evt["top_contributors"]]
    assert chars_seq == sorted(chars_seq, reverse=True)
    # At most 5 top contributors (the contract: top 5).
    assert len(evt["top_contributors"]) <= 5


# ---------------------------------------------------------------------------
# 2. Typed exception class
# ---------------------------------------------------------------------------


def test_consciousness_overflow_subclass_attributes():
    """_ConsciousnessOverflow IS an OverflowError; carries the breakdown."""
    from ouroboros.consciousness import _ConsciousnessOverflow

    exc = _ConsciousnessOverflow(
        total_chars=1_500_000,
        max_chars=1_200_000,
        mode="max",
        sections=[("bible", 900_000), ("architecture", 300_000), ("identity", 200_000)],
    )
    assert isinstance(exc, OverflowError)
    assert exc.total_chars == 1_500_000
    assert exc.max_chars == 1_200_000
    assert exc.mode == "max"
    assert len(exc.top_contributors) == 3
    # First contributor is the largest (bible).
    assert exc.top_contributors[0][0] == "bible"
    # The message names top contributors.
    assert "bible" in str(exc) and "Top contributors" in str(exc)


def test_consciousness_overflow_defensive_coercion():
    """Malformed section entries are dropped, not crashed on."""
    from ouroboros.consciousness import _ConsciousnessOverflow

    exc = _ConsciousnessOverflow(
        total_chars=100,
        max_chars=50,
        mode="max",
        # Mix valid tuples with bogus entries — only valid ones survive.
        sections=[("ok", 10), None, ("also_ok", 20), "bogus", (None, 5), ("final", 1)],
    )
    names = [n for n, _ in exc.sections]
    assert "ok" in names and "also_ok" in names and "final" in names


# ---------------------------------------------------------------------------
# 3. Section tracking on success
# ---------------------------------------------------------------------------


def test_section_size_tracking_is_deterministic_on_success(tmp_path):
    """Success path: every appended part is tracked by name and char count."""
    bc, _, _ = _make_bc(tmp_path)
    bc._build_context()  # default max fits the seeded text in low mode only

    sections = bc._last_context_sections
    assert isinstance(sections, list)
    # Each entry is (name, char_count, drop_priority) — the drop-priority field
    # was added by the graceful-degradation fix (ibl-local-31f19191be34).
    assert all(isinstance(n, str) and isinstance(c, int) for n, c, *_ in sections)
    assert all(c >= 0 for _n, c, *_ in sections)
    # bg_prompt is always first.
    assert sections[0][0] == "bg_prompt"
    # ARCHITECTURE is now inlined as a navigation map in BOTH modes (the BG loop
    # reads the full body on demand via read_file), so its section is present
    # and non-empty but bounded far below the raw doc size.
    arch_entry = next(
        ((n, c) for n, c, *_ in sections if n.startswith("architecture")), None
    )
    assert arch_entry is not None
    assert 0 < arch_entry[1] < 100_000
    # Stashed mode + total are consistent with the joined full_text:
    # full_text = "\n\n".join(parts), so total >= sum(section chars). The exact
    # arithmetic depends on whether every part is tracked (it is) and on the
    # number of separator chars (2 per gap), so we verify the inequality that
    # must hold without coupling to the exact count.
    assert bc._last_context_mode in {"low", "max"}
    sum_chars = sum(c for _n, c, *_ in sections)
    assert bc._last_context_total >= sum_chars
    # And the gap between sum and total is bounded by 2 per non-empty section.
    assert bc._last_context_total <= sum_chars + 2 * len(sections)


# ---------------------------------------------------------------------------
# 4. Mode-aware BG assembly
# ---------------------------------------------------------------------------


def test_low_mode_skips_backlog_and_observations(tmp_path, monkeypatch):
    """In low mode the BG context skips the backlog digest and observations."""
    bc, _, _ = _make_bc(tmp_path, mode="low", monkeypatch=monkeypatch)
    # Stub format_backlog_digest so we can detect the call without needing
    # the real backlog file machinery.
    called = {"backlog": 0}

    def _fake_format_backlog_digest(*args, **kwargs):
        called["backlog"] += 1
        # Use a unique sentinel so we can prove the digest was or was not appended
        # without colliding with CONSCIOUSNESS.md content (which already contains
        # the word "fake" naturally).
        return "## Improvement Backlog Digest\n\nSENTINEL_BACKLOG_DIGEST_PAYLOAD_42"

    monkeypatch.setattr(
        "ouroboros.improvement_backlog.format_backlog_digest",
        _fake_format_backlog_digest,
        raising=False,
    )

    # Inject one observation so we can prove the queue is drained but not appended.
    bc.inject_observation("hello from owner")

    with patch.object(bc, "_build_registry", return_value=MagicMock()):
        ctx = bc._build_context()

    # Backlog digest was NOT called (low mode skips it).
    assert called["backlog"] == 0
    # The fake digest content is NOT in the assembled context.
    assert "SENTINEL_BACKLOG_DIGEST_PAYLOAD_42" not in ctx
    # The section tracker recorded the skip.
    sections = bc._last_context_sections
    section_names = {s[0] for s in sections}
    assert "backlog_digest_skipped_low_mode" in section_names
    # Observations were drained (queue cleared) but not appended under low mode.
    assert "observations_skipped_low_mode" in section_names
    # The queue is empty after the drain (proving the drain happened).
    assert bc._observations.empty()
    # Stamped mode is low.
    assert bc._last_context_mode == "low"


def test_max_mode_keeps_backlog_and_observations(tmp_path, monkeypatch):
    """In max mode the BG context includes the backlog digest and observations."""
    bc, _, _ = _make_bc(tmp_path, mode="max", monkeypatch=monkeypatch)

    called = {"backlog": 0}

    def _fake_format_backlog_digest(*args, **kwargs):
        called["backlog"] += 1
        return "## Improvement Backlog Digest\n\nSENTINEL_BACKLOG_DIGEST_PAYLOAD_42"

    monkeypatch.setattr(
        "ouroboros.improvement_backlog.format_backlog_digest",
        _fake_format_backlog_digest,
        raising=False,
    )

    bc.inject_observation("hello from owner")

    with patch.object(bc, "_build_registry", return_value=MagicMock()):
        ctx = bc._build_context()

    # Backlog digest WAS called in max mode and the sentinel IS in context.
    assert called["backlog"] == 1
    assert "SENTINEL_BACKLOG_DIGEST_PAYLOAD_42" in ctx
    # Observations section IS appended.
    sections = bc._last_context_sections
    section_names = {s[0] for s in sections}
    assert "observations" in section_names
    obs_entry = {s[0]: s[1] for s in sections}["observations"]
    assert obs_entry > 0
    # Stamped mode is max.
    assert bc._last_context_mode == "max"


def test_mode_change_round_trip(tmp_path, monkeypatch):
    """Switching mode at runtime changes the assembly on the next call."""
    # Start in max; switch to mid-test via monkeypatch.setenv (no bare os.environ).
    monkeypatch.setenv("OUROBOROS_CONTEXT_MODE", "max")
    bc, _, _ = _make_bc(tmp_path, mode="max", monkeypatch=monkeypatch)
    monkeypatch.setattr(
        "ouroboros.improvement_backlog.format_backlog_digest",
        lambda *a, **kw: "## Improvement Backlog Digest\n\nSENTINEL_BACKLOG_DIGEST_PAYLOAD_42",
        raising=False,
    )

    with patch.object(bc, "_build_registry", return_value=MagicMock()):
        ctx_max = bc._build_context()
    assert "SENTINEL_BACKLOG_DIGEST_PAYLOAD_42" in ctx_max
    assert bc._last_context_mode == "max"

    # Flip to low and rebuild — monkeypatch.setenv again (auto-restored at teardown).
    monkeypatch.setenv("OUROBOROS_CONTEXT_MODE", "low")
    with patch.object(bc, "_build_registry", return_value=MagicMock()):
        ctx_low = bc._build_context()
    assert "SENTINEL_BACKLOG_DIGEST_PAYLOAD_42" not in ctx_low
    assert bc._last_context_mode == "low"


# ---------------------------------------------------------------------------
# 5. _label_section helper
# ---------------------------------------------------------------------------


def test_label_section_parses_heading_and_falls_back():
    """_label_section extracts '## Header' labels and falls back on missing."""
    from ouroboros.consciousness import _label_section

    assert _label_section("## BIBLE.md\n\nbody", "fallback") == "bible.md"
    assert _label_section("## Recent chat\n\nbody", "x") == "recent_chat"
    # No leading heading -> fallback.
    assert _label_section("no heading here", "memory[0]") == "memory[0]"
    # Empty content -> fallback.
    assert _label_section("", "fallback") == "fallback"
    # Parens and slashes are normalised away.
    assert _label_section("## Knowledge base\n\nbody", "x") == "knowledge_base"
