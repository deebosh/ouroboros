"""Cross-phase contracts of the two transport rails.

The free wait episode (a released pre-dispatch failure, $0, ``transport_unavailable``)
and the paid repeat rail (a dispatched typed transport death, ``provider_outcome_unknown``)
never overlap, and while the round holds a repeat record that record outranks the
wait terminal:

- a dispatched death on an interactive turn takes the repeat rail and never opens a
  wait episode (unknown is not the episode's kind);
- inside an episode the chain's local-only pass exists for ``transport_unavailable``
  alone, and a round record blocks even that pass;
- an episode exhausted on a round that still holds a repeat record ends on the
  record's source, worded as both the wait and the unresolved attempt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import ouroboros.loop as loop_mod
import ouroboros.loop_llm_call as call_mod
import ouroboros.loop_transport as loop_transport
from ouroboros.loop import run_llm_loop
from ouroboros.loop_llm_call import TRANSPORT_DEATHS_KEY
from tests.test_loop_transport_wait import _FakeClock
from tests.test_transport_death_retry import (
    _ScriptedLLM,
    _death,
    _events,
    _loop_kwargs,
    _no_chain,
    _released_connect,
)


@pytest.fixture
def no_sleep(monkeypatch):
    """The repeat rail's backoff sleeps, recorded instead of slept (as in the
    transport-death suite; a fixture is defined where it is used)."""
    sleeps = []
    monkeypatch.setattr(call_mod, "_sleep_within_deadline", lambda sec, _dl: (sleeps.append(sec), True)[1])
    return sleeps


@pytest.mark.parametrize("turn_flag", ["is_direct_chat", "is_ephemeral_turn"])
@pytest.mark.parametrize("deaths", [2, 3])
def test_interactive_turn_death_takes_the_repeat_rail_and_never_enters_a_wait_episode(
    tmp_path, monkeypatch, no_sleep, turn_flag, deaths,
):
    """A direct-chat or ephemeral turn whose DISPATCHED request died with a typed
    transport death is on the paid repeat rail (its round dispatch is primary),
    never in the free wait episode: `provider_outcome_unknown` is not the
    episode's `transport_unavailable`, so no `network_wait` event exists, the
    interactive idle bound never starts, and an exhausted round ends on the
    unknown no-resend terminal, not on the wait window's."""
    monkeypatch.setattr(loop_mod, "_run_cross_model_fallback_chain", _no_chain)
    monkeypatch.setattr(
        loop_transport, "interruptible_wait_sleep",
        lambda _sec, _wake: pytest.fail("a dispatched death must never open a wait episode"),
    )
    monkeypatch.setenv("OUROBOROS_TASK_REVIEW_MODE", "off")
    monkeypatch.setenv("OUROBOROS_MODEL_FALLBACKS", "other/model")
    monkeypatch.delenv("USE_LOCAL_FALLBACK", raising=False)
    llm = _ScriptedLLM(*([_death] * deaths))
    notes = []
    kwargs = _loop_kwargs(tmp_path, llm, notes)
    setattr(kwargs["tools"]._ctx, turn_flag, True)
    result, usage, trace = run_llm_loop(**kwargs)

    assert llm.calls == 3  # the primary send plus two paid repeats, whatever the turn kind
    assert no_sleep == [4.0, 8.0]  # the repeat rail's backoffs, never the episode's wait
    assert _events(tmp_path, "network_wait") == []
    assert not any("provider connection" in note.lower() for note in notes)
    if deaths == 2:
        assert result == "done"
        assert usage.get("reason_code") is None
        assert TRANSPORT_DEATHS_KEY not in usage
    else:
        assert usage.get("execution_status") == "infra_failed"
        assert trace.get("forced_finalization", {}).get("source") == "provider_outcome_unknown_no_resend"
        assert usage[TRANSPORT_DEATHS_KEY]["count"] == 2
        assert "2 earlier physical attempt(s) of the last dispatched round" in result
        assert "waited and redialed" not in result and "no wait window" not in result


@pytest.mark.parametrize("turn", ["managed", "is_direct_chat", "is_ephemeral_turn"])
@pytest.mark.parametrize("with_record", [True, False])
def test_wait_episode_exhausted_on_a_round_holding_a_repeat_record_takes_the_unknown_source(
    tmp_path, monkeypatch, no_sleep, turn, with_record,
):
    """death → granted repeat RELEASED (`transport_unavailable`) → wait episode →
    window exhausted (a managed task's deadline, an interactive turn's idle
    bound): the round still holds an unresolved paid attempt, so the record
    fence outranks the wait terminal — durable source
    `provider_outcome_unknown_no_resend`, owner text saying both that it waited
    and that an earlier attempt stays unresolved; execution status and reason
    code stay the wait terminal's. Control: the same episode without a record
    keeps `transport_unavailable_no_resend` and the byte-identical base wording."""
    from ouroboros.config import get_finalization_grace_sec

    clock = _FakeClock(monkeypatch)
    monkeypatch.setattr(loop_transport, "get_task_idle_timeout_sec", lambda: 60)
    monkeypatch.setattr(loop_mod, "_run_cross_model_fallback_chain", _no_chain)
    monkeypatch.setenv("OUROBOROS_TASK_REVIEW_MODE", "off")
    monkeypatch.setenv("OUROBOROS_MODEL_FALLBACKS", "other/model")
    monkeypatch.delenv("USE_LOCAL_FALLBACK", raising=False)
    script = ([_death] if with_record else []) + [_released_connect] * 12
    llm = _ScriptedLLM(*script)
    notes = []
    kwargs = _loop_kwargs(tmp_path, llm, notes)
    if turn == "managed":
        deadline = datetime.now(timezone.utc) + timedelta(seconds=get_finalization_grace_sec() + 8)
        kwargs["tools"]._ctx.task_metadata = {"deadline_at": deadline.isoformat()}
    else:
        setattr(kwargs["tools"]._ctx, turn, True)
    result, usage, trace = run_llm_loop(**kwargs)

    assert (3 if with_record else 2) <= llm.calls <= len(script)  # the episode ended; the turn never recovered
    assert no_sleep == ([4.0] if with_record else [])  # one repeat backoff; released redials never re-arm it
    ended = [row["detail"] for row in _events(tmp_path, "network_wait") if row["phase"] == "ended"]
    assert ended == ["deadline_after_final_redial" if turn == "managed" else "interactive_wait_window_exhausted"]
    assert usage["execution_status"] == "infra_failed"
    assert usage["reason_code"] == "provider_unavailable"
    base = loop_transport.provider_terminal_fallback_text(
        {}, is_context_overflow=False, is_transport_wait=True, waited_sec=sum(clock.sleeps),
        interactive=turn != "managed", is_deadline_exhausted=False,
    )
    assert "waited and redialed" in base
    if with_record:
        assert trace["forced_finalization"]["source"] == "provider_outcome_unknown_no_resend"
        assert usage[TRANSPORT_DEATHS_KEY]["count"] == 1
        assert "1 earlier physical attempt(s) of the last dispatched round" in result
        assert "unresolved at their upper bound" in result
        assert result == base + loop_transport.provider_recovery_hint(usage)
    else:
        assert trace["forced_finalization"]["source"] == "transport_unavailable_no_resend"
        assert TRANSPORT_DEATHS_KEY not in usage
        assert result == base  # byte-identical base wording


def test_fallback_chain_fence_holds_inside_a_wait_episode_too(monkeypatch):
    """`fallback_chain_allowed` on the combined tree: inside a wait episode the
    one local-only chain pass exists for `transport_unavailable` alone (never
    for the unknown kind), and a round record — an unresolved attempt of this
    round — blocks even that pass whatever the kind says, because no candidate
    may dial over a request that may still be live."""
    monkeypatch.setenv("USE_LOCAL_FALLBACK", "1")
    routable = SimpleNamespace(exact_model_route=False)
    record = {"round_id": "r", "count": 1, "backoff_sec": 4.0}
    episode = loop_transport.TransportWaitEpisode(started_monotonic=1.0, interactive=True, wait_bound_sec=900.0)

    assert loop_transport.fallback_chain_allowed(routable, "provider_outcome_unknown", episode) is False
    assert loop_transport.fallback_chain_allowed(
        routable, "transport_unavailable", episode, {TRANSPORT_DEATHS_KEY: record},
    ) is False
    assert episode.local_pass_used is False  # neither refusal spent the episode's single local pass
    assert loop_transport.fallback_chain_allowed(routable, "transport_unavailable", episode) is True
    assert episode.local_pass_used is True
    assert loop_transport.fallback_chain_allowed(routable, "transport_unavailable", episode) is False  # once per episode
    assert loop_transport.fallback_chain_allowed(routable, "provider_outcome_unknown", None) is False
