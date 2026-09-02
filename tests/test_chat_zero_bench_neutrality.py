"""Closing the chat-id truthiness class must not move a benchmark's answer.

Terminal-Bench reads a run's final answer straight out of ``chat.jsonl``: the
LAST untyped row with ``direction == "out"`` (``atif._final_answer``). Headless
benchmark roots run in the hidden partition (chat 0), which is exactly the
partition the class fix stops dropping — so every notice that now REACHES chat 0
has to land somewhere ATIF ignores, or a crashed run's incident line would be
recorded as the model's answer.
"""

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from devtools.benchmarks.terminal_bench.atif import _final_answer  # noqa: E402
from ouroboros.contracts.chat_id_policy import HIDDEN_CHAT_ID  # noqa: E402


def _agent_dir(tmp_path, chat_rows):
    data = tmp_path / "ouroboros-data"
    (data / "logs").mkdir(parents=True)
    (data / "logs" / "chat.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in chat_rows),
        encoding="utf-8",
    )
    return tmp_path


def test_progress_notices_never_enter_the_answer_stream(tmp_path):
    """The reaper/scheduler toasts the class fix un-drops are progress rows.

    ``send_with_budget(is_progress=True)`` appends to progress.jsonl, so a
    grace toast for a hidden-partition root cannot be mistaken for its answer.
    """
    from types import SimpleNamespace

    from supervisor import message_bus

    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    sent = []
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(message_bus, "DATA_DIR", data)
        monkey.setattr(
            message_bus, "_BRIDGE",
            SimpleNamespace(send_message=lambda *a, **k: sent.append((a, k))),
        )
        message_bus.send_with_budget(
            HIDDEN_CHAT_ID, "⏱️ Task t1 has been running 600s", is_progress=True, task_id="t1",
        )
    finally:
        monkey.undo()
    assert sent, "the notice still reaches the live bridge"
    assert (data / "logs" / "progress.jsonl").exists()
    assert not (data / "logs" / "chat.jsonl").exists()


def test_a_system_incident_row_is_not_read_as_the_answer(tmp_path):
    """A provider-outage notice now reaches chat 0 instead of vanishing (P1).

    It is persisted as a SYSTEM row, so the trajectory still reports the task's
    own last answer rather than the incident sentence.
    """
    agent_dir = _agent_dir(tmp_path, [
        {"direction": "in", "chat_id": HIDDEN_CHAT_ID, "text": "solve it"},
        {"direction": "out", "chat_id": HIDDEN_CHAT_ID, "text": "the real answer"},
        {"direction": "system", "chat_id": HIDDEN_CHAT_ID, "type": "terminal_incident",
         "text": "🔌 Task t1 was stopped by a model-provider outage"},
    ])
    assert _final_answer(agent_dir) == "the real answer"


def test_typed_delivery_rows_after_the_answer_are_still_skipped(tmp_path):
    agent_dir = _agent_dir(tmp_path, [
        {"direction": "out", "chat_id": HIDDEN_CHAT_ID, "text": "the real answer"},
        {"direction": "out", "chat_id": HIDDEN_CHAT_ID, "type": "task_summary",
         "text": "Done with warnings"},
    ])
    assert _final_answer(agent_dir) == "the real answer"


def test_an_untyped_out_row_would_hijack_the_answer(tmp_path):
    """The hazard this suite exists to prevent, stated as an executable fact.

    Any future notice that reaches the hidden partition as a plain outbound chat
    row REPLACES the benchmark's recorded answer. Route such a notice through
    progress or a typed/system row instead.
    """
    agent_dir = _agent_dir(tmp_path, [
        {"direction": "out", "chat_id": HIDDEN_CHAT_ID, "text": "the real answer"},
        {"direction": "out", "chat_id": HIDDEN_CHAT_ID, "text": "⚠️ some later notice"},
    ])
    assert _final_answer(agent_dir) == "⚠️ some later notice"
