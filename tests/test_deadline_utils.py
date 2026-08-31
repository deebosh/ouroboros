"""Unit tests for ouroboros/deadline_utils.py.

Covers the 6 pure helpers without touching production code:

- parse_deadline_ts: Z suffix, explicit offset, naive (gets UTC attached), and the
  full set of None/blank/unparseable/non-string edge cases that must return None.
- utc_now: tz-aware UTC.
- seconds_until: past is clamped to 0.0 (non-negative), future is the positive
  delta, and unparsable input returns None.
- deadline_remaining_sec / has_deadline / window_within_deadline: read from
  ``ctx.task_metadata["deadline_at"]`` (NOT a ``ctx.deadline_at`` attribute —
  the contract is the metadata dict). Document the existence-vs-sign split:
  deadline_remaining_sec returns the raw subtraction (negative for past),
  has_deadline answers the separate existence question, and window_within_deadline
  narrows the requested wait so it cannot outlive the deadline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ouroboros import deadline_utils


# ---------------------------------------------------------------------------
# parse_deadline_ts
# ---------------------------------------------------------------------------

class TestParseDeadlineTs:
    def test_iso_with_z_suffix_is_utc(self):
        result = deadline_utils.parse_deadline_ts("2026-08-31T12:00:00Z")
        assert result == datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == 0

    def test_iso_with_explicit_positive_offset_normalizes_to_utc(self):
        # 14:00 +02:00 == 12:00 UTC
        result = deadline_utils.parse_deadline_ts("2026-08-31T14:00:00+02:00")
        assert result == datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)

    def test_iso_with_explicit_negative_offset_normalizes_to_utc(self):
        # 07:00 -05:00 == 12:00 UTC
        result = deadline_utils.parse_deadline_ts("2026-08-31T07:00:00-05:00")
        assert result == datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)

    def test_naive_iso_gets_utc_attached(self):
        result = deadline_utils.parse_deadline_ts("2026-08-31T12:00:00")
        assert result == datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == 0

    def test_leading_trailing_whitespace_is_stripped(self):
        result = deadline_utils.parse_deadline_ts("  2026-08-31T12:00:00Z  ")
        assert result == datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)

    def test_blank_string_returns_none(self):
        assert deadline_utils.parse_deadline_ts("") is None

    def test_whitespace_only_returns_none(self):
        assert deadline_utils.parse_deadline_ts("   ") is None

    def test_none_returns_none(self):
        assert deadline_utils.parse_deadline_ts(None) is None

    def test_unparseable_garbage_returns_none(self):
        assert deadline_utils.parse_deadline_ts("not a date") is None

    def test_non_string_int_returns_none(self):
        # The helper does ``str(value or "")`` so 0 → "0" → unparseable.
        assert deadline_utils.parse_deadline_ts(12345) is None

    def test_non_string_zero_returns_none(self):
        assert deadline_utils.parse_deadline_ts(0) is None


# ---------------------------------------------------------------------------
# utc_now
# ---------------------------------------------------------------------------

class TestUtcNow:
    def test_returns_tz_aware_utc_datetime(self):
        result = deadline_utils.utc_now()
        assert isinstance(result, datetime)
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------------
# seconds_until
# ---------------------------------------------------------------------------

class TestSecondsUntil:
    def test_past_instant_clamped_to_zero(self, monkeypatch):
        # Freeze utc_now so the "past" is unambiguous.
        fixed = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(deadline_utils, "utc_now", lambda: fixed)
        # 10 seconds before frozen now.
        result = deadline_utils.seconds_until("2026-08-31T11:59:50Z")
        assert result == 0.0

    def test_far_past_still_clamped_to_zero(self, monkeypatch):
        fixed = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(deadline_utils, "utc_now", lambda: fixed)
        # An hour before frozen now.
        result = deadline_utils.seconds_until("2026-08-31T11:00:00Z")
        assert result == 0.0

    def test_future_instant_returns_positive_seconds(self, monkeypatch):
        fixed = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(deadline_utils, "utc_now", lambda: fixed)
        # 30 seconds after frozen now.
        result = deadline_utils.seconds_until("2026-08-31T12:00:30Z")
        assert result is not None
        assert result == pytest.approx(30.0, abs=0.01)
        assert result > 0

    def test_unparsable_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            deadline_utils, "utc_now", lambda: datetime.now(timezone.utc)
        )
        assert deadline_utils.seconds_until("not a date") is None

    def test_none_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            deadline_utils, "utc_now", lambda: datetime.now(timezone.utc)
        )
        assert deadline_utils.seconds_until(None) is None

    def test_blank_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            deadline_utils, "utc_now", lambda: datetime.now(timezone.utc)
        )
        assert deadline_utils.seconds_until("") is None


# ---------------------------------------------------------------------------
# deadline_remaining_sec
#
# NOTE: this function returns the RAW (deadline - now) subtraction; it is
# intentionally NOT clamped to 0 so callers can distinguish "deadline spent
# (negative)" from "no deadline set (0.0)" via has_deadline's separate answer.
# ---------------------------------------------------------------------------

class TestDeadlineRemainingSec:
    def test_ctx_without_task_metadata_returns_zero(self, monkeypatch):
        monkeypatch.setattr(
            deadline_utils, "utc_now", lambda: datetime.now(timezone.utc)
        )
        ctx = SimpleNamespace()
        assert deadline_utils.deadline_remaining_sec(ctx) == 0.0

    def test_ctx_with_non_dict_task_metadata_returns_zero(self, monkeypatch):
        monkeypatch.setattr(
            deadline_utils, "utc_now", lambda: datetime.now(timezone.utc)
        )
        ctx = SimpleNamespace(task_metadata="not a dict")  # type: ignore[arg-type]
        assert deadline_utils.deadline_remaining_sec(ctx) == 0.0

    def test_ctx_with_none_task_metadata_returns_zero(self, monkeypatch):
        # getattr default kicks in only when the attribute is missing; an explicit
        # None still hits the isinstance check.
        monkeypatch.setattr(
            deadline_utils, "utc_now", lambda: datetime.now(timezone.utc)
        )
        ctx = SimpleNamespace(task_metadata=None)
        assert deadline_utils.deadline_remaining_sec(ctx) == 0.0

    def test_ctx_with_empty_task_metadata_returns_zero(self, monkeypatch):
        monkeypatch.setattr(
            deadline_utils, "utc_now", lambda: datetime.now(timezone.utc)
        )
        ctx = SimpleNamespace(task_metadata={})
        assert deadline_utils.deadline_remaining_sec(ctx) == 0.0

    def test_ctx_with_dict_without_deadline_at_returns_zero(self, monkeypatch):
        monkeypatch.setattr(
            deadline_utils, "utc_now", lambda: datetime.now(timezone.utc)
        )
        ctx = SimpleNamespace(task_metadata={"other_key": "x"})
        assert deadline_utils.deadline_remaining_sec(ctx) == 0.0

    def test_ctx_with_unparseable_deadline_at_returns_zero(self, monkeypatch):
        monkeypatch.setattr(
            deadline_utils, "utc_now", lambda: datetime.now(timezone.utc)
        )
        ctx = SimpleNamespace(task_metadata={"deadline_at": "not a date"})
        assert deadline_utils.deadline_remaining_sec(ctx) == 0.0

    def test_ctx_with_future_deadline_returns_positive(self, monkeypatch):
        fixed = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(deadline_utils, "utc_now", lambda: fixed)
        ctx = SimpleNamespace(task_metadata={"deadline_at": "2026-08-31T12:00:30Z"})
        result = deadline_utils.deadline_remaining_sec(ctx)
        assert result == pytest.approx(30.0, abs=0.01)
        assert result > 0

    def test_ctx_with_past_deadline_returns_negative(self, monkeypatch):
        # Documented behavior: has_deadline is the existence question; remaining
        # returns the raw (negative) subtraction so callers can detect a spent
        # deadline via the sign.
        fixed = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(deadline_utils, "utc_now", lambda: fixed)
        ctx = SimpleNamespace(task_metadata={"deadline_at": "2026-08-31T11:59:30Z"})
        result = deadline_utils.deadline_remaining_sec(ctx)
        assert result == pytest.approx(-30.0, abs=0.01)
        assert result < 0

    def test_ctx_with_explicit_offset_deadline_at(self, monkeypatch):
        fixed = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(deadline_utils, "utc_now", lambda: fixed)
        # 14:00 +02:00 == 12:00 UTC == 0s remaining.
        ctx = SimpleNamespace(task_metadata={"deadline_at": "2026-08-31T14:00:00+02:00"})
        result = deadline_utils.deadline_remaining_sec(ctx)
        assert result == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# has_deadline — existence question, distinct from remaining-seconds sign.
# ---------------------------------------------------------------------------

class TestHasDeadline:
    def test_no_task_metadata_is_false(self):
        assert deadline_utils.has_deadline(SimpleNamespace()) is False

    def test_task_metadata_non_dict_is_false(self):
        assert deadline_utils.has_deadline(SimpleNamespace(task_metadata="x")) is False

    def test_task_metadata_none_is_false(self):
        assert deadline_utils.has_deadline(SimpleNamespace(task_metadata=None)) is False

    def test_task_metadata_empty_dict_is_false(self):
        assert deadline_utils.has_deadline(SimpleNamespace(task_metadata={})) is False

    def test_task_metadata_without_deadline_at_is_false(self):
        ctx = SimpleNamespace(task_metadata={"other_key": 1})
        assert deadline_utils.has_deadline(ctx) is False

    def test_task_metadata_with_blank_deadline_at_is_false(self):
        ctx = SimpleNamespace(task_metadata={"deadline_at": ""})
        assert deadline_utils.has_deadline(ctx) is False

    def test_task_metadata_with_unparseable_deadline_at_is_false(self):
        ctx = SimpleNamespace(task_metadata={"deadline_at": "not a date"})
        assert deadline_utils.has_deadline(ctx) is False

    def test_task_metadata_with_future_deadline_is_true(self):
        ctx = SimpleNamespace(task_metadata={"deadline_at": "2099-01-01T00:00:00Z"})
        assert deadline_utils.has_deadline(ctx) is True

    def test_task_metadata_with_past_deadline_is_still_true(self):
        # Existence, not sign — this is the whole reason has_deadline exists.
        ctx = SimpleNamespace(task_metadata={"deadline_at": "2000-01-01T00:00:00Z"})
        assert deadline_utils.has_deadline(ctx) is True


# ---------------------------------------------------------------------------
# window_within_deadline
#
# narrows a requested wait so it cannot outlive the deadline; subtracts the
# finalization grace window before clamping; floors to 1 (an int<1 wait would
# round to zero and lose all time).
# ---------------------------------------------------------------------------

class TestWindowWithinDeadline:
    def test_no_deadline_returns_requested(self):
        ctx = SimpleNamespace()
        assert deadline_utils.window_within_deadline(ctx, 60) == 60
        assert deadline_utils.window_within_deadline(ctx, 5) == 5

    def test_no_deadline_floors_zero_to_one(self):
        # ``max(1, int(requested))`` — the floor prevents a 0-second wait from
        # being asked for when there is no deadline at all.
        ctx = SimpleNamespace()
        assert deadline_utils.window_within_deadline(ctx, 0) == 1

    def test_no_deadline_floors_negative_to_one(self):
        ctx = SimpleNamespace()
        assert deadline_utils.window_within_deadline(ctx, -5) == 1

    def test_far_deadline_requested_smaller_than_remaining_returns_requested(
        self, monkeypatch
    ):
        fixed = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(deadline_utils, "utc_now", lambda: fixed)
        monkeypatch.setattr(
            "ouroboros.task_pacing.effective_finalization_reserve_sec",
            lambda ctx: 5.0,
        )
        # 100s out → remaining=100, grace=5 → effective 95s available.
        ctx = SimpleNamespace(task_metadata={"deadline_at": "2026-08-31T12:01:40Z"})
        assert deadline_utils.window_within_deadline(ctx, 30) == 30

    def test_far_deadline_requested_larger_clamps_to_remaining_minus_grace(
        self, monkeypatch
    ):
        fixed = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(deadline_utils, "utc_now", lambda: fixed)
        monkeypatch.setattr(
            "ouroboros.task_pacing.effective_finalization_reserve_sec",
            lambda ctx: 5.0,
        )
        # 100s out → effective 95s available; requested 200 → clamped to 95.
        ctx = SimpleNamespace(task_metadata={"deadline_at": "2026-08-31T12:01:40Z"})
        assert deadline_utils.window_within_deadline(ctx, 200) == 95

    def test_near_deadline_returns_remaining_minus_grace(self, monkeypatch):
        fixed = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(deadline_utils, "utc_now", lambda: fixed)
        monkeypatch.setattr(
            "ouroboros.task_pacing.effective_finalization_reserve_sec",
            lambda ctx: 5.0,
        )
        # 20s out → effective 15s available.
        ctx = SimpleNamespace(task_metadata={"deadline_at": "2026-08-31T12:00:20Z"})
        assert deadline_utils.window_within_deadline(ctx, 60) == 15

    def test_remaining_less_than_grace_floors_to_one(self, monkeypatch):
        fixed = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(deadline_utils, "utc_now", lambda: fixed)
        monkeypatch.setattr(
            "ouroboros.task_pacing.effective_finalization_reserve_sec",
            lambda ctx: 5.0,
        )
        # 3s remaining, grace 5 → min(60, -2) = -2 → max(1, int(-2)) = 1.
        ctx = SimpleNamespace(task_metadata={"deadline_at": "2026-08-31T12:00:03Z"})
        assert deadline_utils.window_within_deadline(ctx, 60) == 1

    def test_past_deadline_floors_to_one(self, monkeypatch):
        # has_deadline is True; remaining is negative; floor at 1.
        fixed = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(deadline_utils, "utc_now", lambda: fixed)
        monkeypatch.setattr(
            "ouroboros.task_pacing.effective_finalization_reserve_sec",
            lambda ctx: 5.0,
        )
        ctx = SimpleNamespace(task_metadata={"deadline_at": "2026-08-31T11:59:00Z"})
        assert deadline_utils.window_within_deadline(ctx, 60) == 1

    def test_unparseable_deadline_treated_as_no_deadline(self, monkeypatch):
        # has_deadline returns False for an unparseable deadline_at; therefore
        # window_within_deadline returns max(1, int(requested)) — full ask.
        monkeypatch.setattr(
            deadline_utils, "utc_now", lambda: datetime.now(timezone.utc)
        )
        ctx = SimpleNamespace(task_metadata={"deadline_at": "garbage"})
        assert deadline_utils.window_within_deadline(ctx, 60) == 60
        assert deadline_utils.window_within_deadline(ctx, 0) == 1
