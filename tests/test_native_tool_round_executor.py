"""The bounded native tool-round review executor (configured-subagent api rows).

One episode = ONE logical review attempt of ``chat(tools=…)`` calls against a
fresh instance-local inspection registry until the reviewer answers: no round
cap; the bounds are the window-derived transcript bound (with a once-only
landing notice), the owner deadline and the ledger. Exhaustion is a typed
refusal for verdict shapes and a disclosed incomplete product for the report
shape; the second actor attempt repairs format locally over the collected
answer; every read is host-observed disclosure, never a full-coverage claim.
"""

import copy
import dataclasses
import json

import pytest

from ouroboros.review_execution import (
    ReviewAssignment,
    ReviewRouteKind,
    ReviewRouteUnavailable,
    _review_route_executor,
)
from ouroboros.review_native_episode import NativeToolRoundReviewExecutor
from ouroboros.review_substrate import ReviewRequest, ReviewSlot

_VERDICT = '[{"severity": "advisory", "item": "x", "evidence": "e", "recommendation": "r"}]'


class _ScriptedLLM:
    """chat() replays a script; captures every messages payload it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, **kwargs):
        # Snapshot: the executor mutates ONE messages list across rounds, so a
        # live reference would show every later message on every earlier call.
        self.calls.append({**kwargs, "messages": copy.deepcopy(kwargs.get("messages"))})
        if not self.script:
            raise AssertionError("script exhausted — executor made an extra call")
        entry = self.script.pop(0)
        return entry, {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0}


def _tool_call(name, args, call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


@pytest.fixture()
def subject_repo(tmp_path):
    repo = tmp_path / "subject"
    repo.mkdir()
    (repo / "greeting.txt").write_text("hello native reviewer\n", encoding="utf-8")
    return repo


def _assignment(repo, llm, session_task="Review the staged change; cite files."):
    request = ReviewRequest(
        surface="multi_model_review",
        goal="review",
        task_id="t-native",
        session_root=str(repo),
        session_task=session_task,
        policy={"output_contract": "JSON array of findings"},
        no_proxy=True,
    )
    slot = ReviewSlot(
        slot_id="t1",
        model="openai/fake-reviewer",
        effort="low",
        route=ReviewRouteKind.API_CHAT,
        subagent_id="api-critic",
    )
    return ReviewAssignment(request=request, slot=slot, call_id="op-1")


def test_route_seam_selects_native_executor(subject_repo):
    executor = _review_route_executor(_assignment(subject_repo, None))
    assert isinstance(executor, NativeToolRoundReviewExecutor)


def test_episode_reads_then_answers(subject_repo):
    llm = _ScriptedLLM([
        {"tool_calls": [_tool_call("read_file", {"path": "greeting.txt"})]},
        {"content": _VERDICT},
    ])
    executor = NativeToolRoundReviewExecutor(_assignment(subject_repo, llm), llm=llm)
    result = executor.execute()
    assert result.raw_text == _VERDICT
    assert result.message["native_transcript"] == _VERDICT
    usage = result.usage
    assert usage["native_rounds"] == 2
    assert usage["host_file_read_attestation"] == "host_observed"
    assert usage["native_tool_receipts"][0]["tool"] == "read_file"
    assert usage["native_tool_receipts"][0]["path"] == "greeting.txt"
    # The REAL inspection tool ran against the pinned root: its output (with
    # the file body) went back to the model as a role=tool message.
    round2_messages = llm.calls[1]["messages"]
    tool_msgs = [m for m in round2_messages if m.get("role") == "tool"]
    assert tool_msgs and "hello native reviewer" in tool_msgs[0]["content"]
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    # tools were offered on every round, from the curated inspection set only
    offered = {t["function"]["name"] for t in llm.calls[0]["tools"]}
    assert "read_file" in offered and "search_code" in offered
    assert "schedule_subagent" not in offered and "write_file" not in offered


def test_second_execute_repairs_locally_without_new_episode(subject_repo):
    llm = _ScriptedLLM([
        {"content": _VERDICT},
    ])
    executor = NativeToolRoundReviewExecutor(_assignment(subject_repo, llm), llm=llm)
    first = executor.execute()
    calls_after_first = len(llm.calls)
    second = executor.execute()
    # No new provider round: format repair reuses the collected answer.
    assert len(llm.calls) == calls_after_first
    assert second.raw_text == first.raw_text


def test_no_round_cap_many_rounds_then_answer(subject_repo, monkeypatch):
    """P13: the floor is hardcoded, never the ceiling — 40 inspection rounds
    (well past the retired 16-round cap) end in the reviewer's own answer."""
    monkeypatch.delenv("OUROBOROS_REVIEW_NATIVE_MAX_ROUNDS", raising=False)
    script = [
        {"tool_calls": [_tool_call("read_file", {"path": "greeting.txt"}, f"c{i}")]}
        for i in range(40)
    ] + [{"content": _VERDICT}]
    llm = _ScriptedLLM(script)
    executor = NativeToolRoundReviewExecutor(_assignment(subject_repo, llm), llm=llm)
    result = executor.execute()
    assert result.raw_text == _VERDICT
    assert result.usage["native_rounds"] == 41
    assert result.usage["native_end_reason"] == "final_answer"
    assert result.usage["native_landing_notified"] is False
    assert not llm.script


def _first_send_chars(subject_repo):
    """What the first send carries (prompt + instructions + schemas), measured
    from a one-shot episode's own counter rather than re-deriving it here."""
    llm = _ScriptedLLM([{"content": _VERDICT}])
    executor = NativeToolRoundReviewExecutor(_assignment(subject_repo, llm), llm=llm)
    usage = executor.execute().usage
    return usage["native_transcript_chars"] - len(_VERDICT)


def test_landing_notice_is_posted_once_at_the_landing_fraction(subject_repo, monkeypatch):
    """The host's budget fact: when the transcript crosses 80% of its bound,
    ONE typed user message tells the reviewer to answer on the next send —
    never a silent cut, never repeated on later rounds."""
    import ouroboros.review_native_episode as native_episode

    (subject_repo / "chunk.txt").write_text("y" * 4_000, encoding="utf-8")
    first_send = _first_send_chars(subject_repo)
    assert first_send >= 6_000
    # Below 80% after the first send alone, above it once one 4K read lands,
    # and still under the bound after that read (see the module's arithmetic).
    bound = int(first_send * 1.25) + 2_500
    monkeypatch.setattr(native_episode, "review_native_transcript_bound",
                        lambda *a, **k: bound)
    llm = _ScriptedLLM([
        {"tool_calls": [_tool_call("read_file", {"path": "chunk.txt"}, "c1")]},
        {"tool_calls": [_tool_call("read_file", {"path": "greeting.txt"}, "c2")]},
        {"content": _VERDICT},
    ])
    executor = NativeToolRoundReviewExecutor(_assignment(subject_repo, llm), llm=llm)
    result = executor.execute()
    assert result.raw_text == _VERDICT
    notices = [
        [m for m in call["messages"] if m.get("role") == "user" and "[EPISODE_BUDGET]" in str(m.get("content"))]
        for call in llm.calls
    ]
    assert [len(n) for n in notices] == [0, 1, 1]  # posted before send 2, carried (not repeated) on send 3
    notice = notices[1][0]["content"]
    assert f"of {bound} chars" in notice and "no tool calls" in notice
    # The notice follows the tool results of the round that crossed the line.
    round2 = llm.calls[1]["messages"]
    assert round2[-1] == notices[1][0] and round2[-2]["role"] == "tool"
    assert result.usage["native_landing_notified"] is True
    assert result.usage["native_transcript_bound"] == bound


def test_transcript_bound_is_the_window_capacity_never_above_the_ceiling(monkeypatch):
    """A 1M reviewer lands on the owner ceiling; a 200K route gets the bound
    its own window can carry (density-calibrated, in chars)."""
    import ouroboros.review_native_episode as native_episode
    from ouroboros.reviewer_window import REVIEWER_FULL_WINDOW

    monkeypatch.setenv("OUROBOROS_REVIEW_NATIVE_MAX_TRANSCRIPT_CHARS", "900000")
    monkeypatch.setattr(native_episode, "_CHARS_PER_ESTIMATED_TOKEN", 4)
    import ouroboros.reviewer_window as reviewer_window
    windows = {"openai/big": REVIEWER_FULL_WINDOW, "openai/small": 200_000}
    monkeypatch.setattr(reviewer_window, "reviewer_context_window",
                        lambda model_id, **_: windows[model_id])
    big = native_episode.review_native_transcript_bound("openai/big", output_reserve=16_000)
    small = native_episode.review_native_transcript_bound("openai/small", output_reserve=16_000)
    assert big == 900_000
    assert 0 < small < big
    # (200K − 16K) / 1.65 cold density ≈ 111K tokens ≈ 446K chars, below the
    # absolute-margin form and far below the ceiling.
    assert 400_000 <= small <= 460_000


@pytest.mark.parametrize("surface", ["multi_model_review", "task_acceptance"])
def test_transcript_bound_fails_closed_for_verdict_shapes(subject_repo, monkeypatch, surface):
    monkeypatch.setenv("OUROBOROS_REVIEW_NATIVE_MAX_TRANSCRIPT_CHARS", "50000")
    (subject_repo / "big.txt").write_text("x" * 60_000, encoding="utf-8")
    llm = _ScriptedLLM([
        {"content": "Reading the big file first.",
         "tool_calls": [_tool_call("read_file", {"path": "big.txt"})]},
    ])
    assignment = _assignment(subject_repo, llm)
    assignment.request.surface = surface
    executor = NativeToolRoundReviewExecutor(assignment, llm=llm)
    with pytest.raises(ReviewRouteUnavailable) as exc:
        executor.execute()
    assert exc.value.code == "native_transcript_cap_exceeded"
    # The settled failure replays; no second paid episode.
    with pytest.raises(ReviewRouteUnavailable):
        executor.execute()
    assert len(llm.calls) == 1


def test_report_shape_delivers_the_collected_draft_marked_incomplete(subject_repo, monkeypatch):
    """A report is a product, not a verdict: when the bound lands before the
    final answer, the reviewer's last draft is delivered with a typed
    `native_incomplete` fact and a capability delta — never discarded, never
    compacted."""
    monkeypatch.setenv("OUROBOROS_REVIEW_NATIVE_MAX_TRANSCRIPT_CHARS", "50000")
    (subject_repo / "big.txt").write_text("x" * 60_000, encoding="utf-8")
    draft = "# Deep self-review (draft)\n\nCRITICAL: loop.py finalization race.\n"
    llm = _ScriptedLLM([
        {"content": draft, "tool_calls": [_tool_call("read_file", {"path": "big.txt"})]},
    ])
    assignment = _assignment(subject_repo, llm)
    assignment.request.surface = "deep_self_review"
    executor = NativeToolRoundReviewExecutor(assignment, llm=llm)
    result = executor.execute()
    assert result.raw_text == draft and result.message["content"] == draft
    assert result.usage["verdict_method"] == "report"
    assert result.usage["native_incomplete"] == "transcript_bound"
    assert result.usage["native_end_reason"] == "transcript_bound"
    assert result.usage["capability_delta"][0]["reason"] == "native_transcript_bound_before_final_answer"
    # An exhausted report episode with NOTHING collected still fails closed.
    llm2 = _ScriptedLLM([{"tool_calls": [_tool_call("read_file", {"path": "big.txt"})]}])
    assignment2 = _assignment(subject_repo, llm2)
    assignment2.request.surface = "deep_self_review"
    with pytest.raises(ReviewRouteUnavailable) as exc:
        NativeToolRoundReviewExecutor(assignment2, llm=llm2).execute()
    assert exc.value.code == "native_transcript_cap_exceeded"


def test_data_root_is_opt_in_and_never_removed(subject_repo, tmp_path, monkeypatch):
    """Default: the inspection tools see an EMPTY scratch as their data plane
    (removed after the episode). Opt-in: `policy["native_data_root"]` names
    the caller's real root, readable through the same read-only tools and
    left untouched even when the episode fails."""
    import shutil

    import ouroboros.review_native_episode as native_episode

    removed = []
    real_rmtree = shutil.rmtree
    monkeypatch.setattr(shutil, "rmtree", lambda path, **kw: (removed.append(str(path)), real_rmtree(path, **kw)))
    seen_roots = []
    original = native_episode.NativeToolRoundReviewExecutor._inspection_registry

    def spy(self, root, drive_root):
        seen_roots.append(str(drive_root))
        return original(self, root, drive_root)

    monkeypatch.setattr(native_episode.NativeToolRoundReviewExecutor, "_inspection_registry", spy)

    # Default: scratch.
    llm = _ScriptedLLM([{"content": _VERDICT}])
    NativeToolRoundReviewExecutor(_assignment(subject_repo, llm), llm=llm).execute()
    assert seen_roots[-1] in removed and "ouro-native-review-" in seen_roots[-1]

    # Opt-in: the real root survives a FAILED episode.
    data_root = tmp_path / "data"
    (data_root / "task_results").mkdir(parents=True)
    (data_root / "task_results" / "t.json").write_text('{"ok": true}', encoding="utf-8")
    monkeypatch.setenv("OUROBOROS_REVIEW_NATIVE_MAX_TRANSCRIPT_CHARS", "50000")
    (subject_repo / "big.txt").write_text("x" * 60_000, encoding="utf-8")
    llm = _ScriptedLLM([{"tool_calls": [_tool_call("read_file", {"path": "big.txt"})]}])
    assignment = _assignment(subject_repo, llm)
    assignment.request.policy["native_data_root"] = str(data_root)
    with pytest.raises(ReviewRouteUnavailable):
        NativeToolRoundReviewExecutor(assignment, llm=llm).execute()
    assert seen_roots[-1] == str(data_root)
    assert str(data_root) not in removed
    assert (data_root / "task_results" / "t.json").read_text(encoding="utf-8") == '{"ok": true}'
    assert any("ouro-native-review-" in path for path in removed[1:])  # the scratch still went


def test_episode_fact_is_custodied_even_when_the_episode_refuses(subject_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("OUROBOROS_REVIEW_NATIVE_MAX_TRANSCRIPT_CHARS", "50000")
    (subject_repo / "big.txt").write_text("x" * 60_000, encoding="utf-8")
    llm = _ScriptedLLM([{"tool_calls": [_tool_call("read_file", {"path": "big.txt"})]}])
    assignment = dataclasses.replace(_assignment(subject_repo, llm), custody_root=tmp_path / "custody")
    with pytest.raises(ReviewRouteUnavailable):
        NativeToolRoundReviewExecutor(assignment, llm=llm).execute()
    from ouroboros.delegate_custody import event_log_path

    rows = [json.loads(line) for line in event_log_path(tmp_path / "custody").read_text(encoding="utf-8").splitlines() if line.strip()]
    fact = [r for r in rows if r.get("type") == "review_native_episode"]
    assert len(fact) == 1
    assert fact[0]["native_end_reason"] == "transcript_bound"
    assert fact[0]["native_rounds"] == 1 and fact[0]["slot_id"] == "t1"
    assert fact[0]["native_transcript_chars"] > fact[0]["native_transcript_bound"] == 50_000


def test_transcript_counter_includes_system_schemas_and_args(subject_repo, monkeypatch):
    """The bound is a SEND bound, so it must measure what every send carries:
    the system instructions and tool schemas ride each provider call, and
    tool-call argument objects accumulate in the message list like results.
    A cap sized to admit the bare prompt but not prompt+system+schemas must
    therefore refuse BEFORE the first send (previously it passed — each send
    was understated by the fixed ~9K system/schema cost plus the argument
    tail). Units are chars on both sides.
    """
    llm = _ScriptedLLM([
        {"content": _VERDICT},
    ])
    executor = NativeToolRoundReviewExecutor(_assignment(subject_repo, llm), llm=llm)
    # Comfortably above the episode prompt alone, strictly below what the
    # first send actually carries (prompt + instructions + tool schemas).
    # The env knob clamps at a 50K floor, so the getter is patched directly —
    # the subject is the COUNTER's coverage, not the knob's clamp.
    import ouroboros.review_native_episode as native_episode

    cap = len(executor.episode_prompt) + 100
    monkeypatch.setattr(
        native_episode, "review_native_max_transcript_chars", lambda: cap
    )
    with pytest.raises(ReviewRouteUnavailable) as exc:
        executor.execute()
    assert exc.value.code == "native_transcript_cap_exceeded"
    assert not llm.calls, "the send bound must refuse before paying for a send"


def test_uninspectable_tool_is_refused_in_episode(subject_repo):
    llm = _ScriptedLLM([
        {"tool_calls": [_tool_call("write_file", {"path": "greeting.txt", "content": "hacked"})]},
        {"content": _VERDICT},
    ])
    executor = NativeToolRoundReviewExecutor(_assignment(subject_repo, llm), llm=llm)
    executor.execute()
    round2_messages = llm.calls[1]["messages"]
    tool_msgs = [m for m in round2_messages if m.get("role") == "tool"]
    assert tool_msgs and "not available" in tool_msgs[0]["content"]
    # The subject was NOT mutated.
    assert (subject_repo / "greeting.txt").read_text(encoding="utf-8") == "hello native reviewer\n"


def test_missing_session_task_refuses_typed(subject_repo):
    llm = _ScriptedLLM([])
    with pytest.raises(ReviewRouteUnavailable) as exc:
        NativeToolRoundReviewExecutor(
            _assignment(subject_repo, llm, session_task=""), llm=llm,
        ).execute()
    assert exc.value.code == "session_task_missing"
