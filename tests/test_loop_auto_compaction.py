"""ibl-long-task-context-growth-latency: the main loop auto-schedules ONE
tool-history compaction once the measured Main-fit prompt estimate crosses
``OUROBOROS_AUTO_COMPACT_PROMPT_TOKENS``. The manual ``compact_context`` tool
still wins, a 0 threshold disables the automatic path, and a still-large
estimate cannot re-fire before ``_AUTO_COMPACT_MIN_ROUND_GAP`` rounds.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _receipt():
    from ouroboros.context_budget import ContextReclaimReceipt

    return ContextReclaimReceipt(
        status="applied",
        before_transcript_sha256="a" * 64,
        after_transcript_sha256="b" * 64,
        selection_fingerprint="c" * 64,
        selected_unit_ids=("unit",),
        reclaimed_tokens=1234,
        goal_reached=False,
        checkpoint_ref={"path": "checkpoint"},
        capsule_refs=(),
    )


def _ctx(tmp_path, *, pending=None, estimate=0, round_idx=40, last_auto=None):
    from ouroboros import loop

    inner = SimpleNamespace(_pending_compaction=pending)
    if last_auto is not None:
        inner._last_auto_compact_round = last_auto
    return loop._CompactionRoundContext(
        tools=SimpleNamespace(_ctx=inner),
        drive_root=tmp_path,
        drive_logs=tmp_path / "logs",
        task_id="task",
        round_idx=round_idx,
        event_queue=None,
        emit_progress=lambda _text: None,
        prompt_token_estimate=estimate,
    )


@pytest.fixture
def spy(monkeypatch):
    from ouroboros import loop

    seen = {}

    def fake(messages, **kwargs):
        seen["called"] = True
        seen.update(kwargs)
        return [{"role": "assistant", "content": "summary"}], _receipt(), {"prompt_tokens": 5}

    monkeypatch.setattr(loop, "compact_tool_history_llm", fake)
    monkeypatch.delenv("OUROBOROS_AUTO_COMPACT_PROMPT_TOKENS", raising=False)
    return seen


def test_auto_compaction_fires_above_threshold(tmp_path, spy):
    from ouroboros import loop

    threshold = loop._auto_compact_prompt_token_threshold()
    assert threshold > 0
    ctx = _ctx(tmp_path, estimate=threshold + 1, round_idx=52)

    result, usage = loop._run_round_compaction([{"role": "user", "content": "go"}], ctx)

    assert spy.get("called") is True
    assert spy["keep_recent"] == loop._AUTO_COMPACT_KEEP_RECENT
    assert usage == {"prompt_tokens": 5}
    assert result == [{"role": "assistant", "content": "summary"}]
    # the auto round is recorded so it cannot re-fire immediately
    assert ctx.tools._ctx._last_auto_compact_round == 52
    # nothing manual was pending, so no manual attr is left behind
    assert getattr(ctx.tools._ctx, "_pending_compaction", None) is None


def test_no_auto_compaction_below_threshold(tmp_path, spy):
    from ouroboros import loop

    threshold = loop._auto_compact_prompt_token_threshold()
    ctx = _ctx(tmp_path, estimate=threshold - 1)

    result, usage = loop._run_round_compaction([{"role": "user", "content": "x"}], ctx)

    assert spy.get("called") is None
    assert usage is None
    assert result == [{"role": "user", "content": "x"}]
    assert getattr(ctx.tools._ctx, "_last_auto_compact_round", None) is None


def test_threshold_zero_disables_auto(tmp_path, spy, monkeypatch):
    from ouroboros import loop

    monkeypatch.setenv("OUROBOROS_AUTO_COMPACT_PROMPT_TOKENS", "0")
    ctx = _ctx(tmp_path, estimate=10_000_000)

    result, usage = loop._run_round_compaction([{"role": "user", "content": "x"}], ctx)

    assert spy.get("called") is None
    assert usage is None
    assert result == [{"role": "user", "content": "x"}]


def test_manual_request_takes_precedence(tmp_path, spy):
    from ouroboros import loop

    threshold = loop._auto_compact_prompt_token_threshold()
    # estimate is over threshold too, but a manual keep_last_n=4 is pending
    ctx = _ctx(tmp_path, pending=4, estimate=threshold + 5000, round_idx=60)

    loop._run_round_compaction([{"role": "user", "content": "go"}], ctx)

    assert spy["keep_recent"] == 4  # manual value, not _AUTO_COMPACT_KEEP_RECENT
    assert ctx.tools._ctx._pending_compaction is None  # manual request consumed
    # manual path does not stamp the auto round
    assert getattr(ctx.tools._ctx, "_last_auto_compact_round", None) is None


def test_auto_compaction_respects_min_round_gap(tmp_path, spy):
    from ouroboros import loop

    threshold = loop._auto_compact_prompt_token_threshold()
    # last auto compaction was 2 rounds ago; gap requires >= _AUTO_COMPACT_MIN_ROUND_GAP (3)
    ctx = _ctx(tmp_path, estimate=threshold + 1, round_idx=50, last_auto=48)

    result, usage = loop._run_round_compaction([{"role": "user", "content": "x"}], ctx)

    assert spy.get("called") is None
    assert usage is None
    assert ctx.tools._ctx._last_auto_compact_round == 48  # unchanged

    # far enough past the gap -> fires again
    ctx2 = _ctx(tmp_path, estimate=threshold + 1, round_idx=51, last_auto=48)
    loop._run_round_compaction([{"role": "user", "content": "x"}], ctx2)
    assert spy.get("called") is True
    assert ctx2.tools._ctx._last_auto_compact_round == 51
