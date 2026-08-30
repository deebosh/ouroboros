"""Unit tests for ``supervisor/schedule_time.py`` — pure schedule helpers.

These helpers are deliberately pure: the caller supplies the clock, so the
selection logic can be unit-tested without touching the wall clock. This file
closes ``ibl-3945f4a638ee`` by giving the module its first dedicated test
coverage (advisory readiness has been flagging
``PYTHON_FILES_MODIFIED_NO_TESTS`` for ``supervisor/`` across multiple runs).

Coverage map (every public function in the module):
* ``parse_schedule_time`` — ISO with ``Z``, ISO with offset, naive datetime
  gets the supplied ``tz`` attached, blank / None / unparseable → ``None``.
* ``once_due`` — past → ``(True, "")``, future → ``(False, "")``,
  missing / invalid ``run_at`` → ``(False, "invalid or missing run_at ..."``.
* ``timezone_for_schedule`` — valid IANA name → that ``ZoneInfo``; invalid
  name falls back without raising; blank → local zone.
* ``record_last_error`` — first set returns ``True`` (changed); identical
  repeat returns ``False`` (write-churn guard); different message rewrites.
* ``prune_consumed_once_records`` — consumed one-shot records older than the
  cutoff are dropped and the count is returned; live / cron records stay.
* ``schedule_next_run`` / ``next_cron_time`` — a simple cron expr
  (``*/5 * * * *``) from a fixed base advances to the expected next instant;
  non-cron / blank-expr short-circuits to ``""``.

The clock is always passed explicitly — no monkey-patching of
``datetime.datetime.now`` needed.
"""

from __future__ import annotations

import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from supervisor.schedule_time import (
    next_cron_time,
    once_due,
    parse_schedule_time,
    prune_consumed_once_records,
    record_last_error,
    schedule_next_run,
    timezone_for_schedule,
)

UTC = datetime.timezone.utc
EASTERN = ZoneInfo("America/New_York")


# --------------------------------------------------------------------------- #
# parse_schedule_time                                                         #
# --------------------------------------------------------------------------- #


class TestParseScheduleTime:
    def test_iso_with_z_suffix_is_utc(self):
        result = parse_schedule_time("2026-01-01T00:00:00Z", UTC)
        assert result is not None
        assert result.utcoffset() == datetime.timedelta(0)
        assert (result.year, result.month, result.day) == (2026, 1, 1)

    def test_iso_with_positive_offset_is_normalised_to_supplied_tz(self):
        # 05:00 in +05:00 == 00:00 UTC; parse_schedule_time must convert into
        # the caller's tz so callers compare apples to apples.
        result = parse_schedule_time("2026-01-01T05:00:00+05:00", UTC)
        assert result is not None
        assert result.utcoffset() == datetime.timedelta(0)
        assert result.hour == 0
        assert result.minute == 0

    def test_naive_datetime_gets_supplied_tz_attached(self):
        result = parse_schedule_time("2026-06-15T12:00:00", EASTERN)
        assert result is not None
        assert result.tzinfo == EASTERN
        # Wall-clock time is preserved — only the tzinfo slot is filled in.
        assert result.hour == 12
        assert result.minute == 0

    def test_blank_returns_none(self):
        assert parse_schedule_time("", UTC) is None
        assert parse_schedule_time("   ", UTC) is None
        assert parse_schedule_time(None, UTC) is None

    def test_unparseable_returns_none(self):
        assert parse_schedule_time("not-a-date", UTC) is None
        assert parse_schedule_time("2026/01/01 00:00", UTC) is None
        assert parse_schedule_time("01-01-2026", UTC) is None


# --------------------------------------------------------------------------- #
# once_due                                                                    #
# --------------------------------------------------------------------------- #


class TestOnceDue:
    def test_run_at_in_past_returns_due_true(self):
        trigger = {"type": "once", "run_at": "2026-01-01T00:00:00Z"}
        now = datetime.datetime(2026, 6, 1, tzinfo=UTC)
        due, err = once_due(trigger, UTC, now)
        assert due is True
        assert err == ""

    def test_run_at_in_future_returns_due_false(self):
        trigger = {"type": "once", "run_at": "2030-01-01T00:00:00Z"}
        now = datetime.datetime(2026, 6, 1, tzinfo=UTC)
        due, err = once_due(trigger, UTC, now)
        assert due is False
        assert err == ""

    def test_run_at_exactly_now_is_due_at_or_after_semantics(self):
        trigger = {"type": "once", "run_at": "2026-06-01T12:00:00Z"}
        now = datetime.datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        due, err = once_due(trigger, UTC, now)
        assert due is True
        assert err == ""

    def test_missing_run_at_returns_typed_error(self):
        trigger = {"type": "once"}
        now = datetime.datetime(2026, 6, 1, tzinfo=UTC)
        due, err = once_due(trigger, UTC, now)
        assert due is False
        assert err == "invalid or missing run_at for one-shot schedule"

    def test_unparseable_run_at_returns_typed_error(self):
        trigger = {"type": "once", "run_at": "not-a-date"}
        now = datetime.datetime(2026, 6, 1, tzinfo=UTC)
        due, err = once_due(trigger, UTC, now)
        assert due is False
        assert err == "invalid or missing run_at for one-shot schedule"

    def test_none_trigger_returns_typed_error(self):
        now = datetime.datetime(2026, 6, 1, tzinfo=UTC)
        due, err = once_due(None, UTC, now)
        assert due is False
        assert err == "invalid or missing run_at for one-shot schedule"


# --------------------------------------------------------------------------- #
# timezone_for_schedule                                                       #
# --------------------------------------------------------------------------- #


class TestTimezoneForSchedule:
    def test_valid_iana_name_returns_that_zoneinfo(self):
        tz = timezone_for_schedule({"timezone": "America/New_York"})
        assert isinstance(tz, ZoneInfo)
        assert str(tz) == "America/New_York"

    def test_invalid_name_falls_back_without_raising(self):
        with mock.patch(
            "ouroboros.platform_layer.local_zoneinfo",
            return_value=ZoneInfo("UTC"),
        ):
            tz = timezone_for_schedule({"timezone": "Not/A_Real_Zone"})
        assert tz == ZoneInfo("UTC")

    def test_blank_timezone_falls_back_to_local_zone(self):
        sentinel = ZoneInfo("America/Los_Angeles")
        with mock.patch(
            "ouroboros.platform_layer.local_zoneinfo",
            return_value=sentinel,
        ):
            tz_blank = timezone_for_schedule({"timezone": ""})
            tz_missing = timezone_for_schedule({})
        assert tz_blank == sentinel
        assert tz_missing == sentinel


# --------------------------------------------------------------------------- #
# record_last_error                                                           #
# --------------------------------------------------------------------------- #


class TestRecordLastError:
    def test_first_set_returns_true_and_assigns(self):
        record: dict = {}
        changed = record_last_error(record, "boom")
        assert changed is True
        assert record["last_error"] == "boom"

    def test_identical_repeat_returns_false_no_churn(self):
        record: dict = {"last_error": "boom"}
        # The write-churn guard must NOT rewrite the slot with identical text —
        # otherwise a permanently invalid record rewrites the table every tick.
        changed = record_last_error(record, "boom")
        assert changed is False
        assert record["last_error"] == "boom"

    def test_different_message_returns_true_and_overwrites(self):
        record: dict = {"last_error": "boom"}
        changed = record_last_error(record, "different boom")
        assert changed is True
        assert record["last_error"] == "different boom"


# --------------------------------------------------------------------------- #
# prune_consumed_once_records                                                 #
# --------------------------------------------------------------------------- #


class TestPruneConsumedOnceRecords:
    def _make_inputs(self):
        cutoff = datetime.datetime(2026, 8, 1, tzinfo=UTC).timestamp()
        old_one_shot = {
            "id": "old-once",
            "enabled": False,
            "completed_at": "2026-01-01T00:00:00Z",  # older than cutoff
            "trigger": {"type": "once"},
        }
        recent_one_shot = {
            "id": "recent-once",
            "enabled": False,
            "completed_at": "2026-08-30T00:00:00Z",  # newer than cutoff
            "trigger": {"type": "once"},
        }
        live_one_shot = {
            "id": "live-once",
            "enabled": True,
            "completed_at": "2026-01-01T00:00:00Z",
            "trigger": {"type": "once"},
        }
        cron_disabled = {
            # Disabled cron rows are standing schedules the owner may re-enable,
            # so they are kept even when they carry a stray completed_at.
            "id": "cron-disabled",
            "enabled": False,
            "completed_at": "2026-01-01T00:00:00Z",
            "trigger": {"type": "cron", "expr": "*/5 * * * *"},
        }
        cron_live = {
            "id": "cron-live",
            "enabled": True,
            "trigger": {"type": "cron", "expr": "*/5 * * * *"},
        }
        return cutoff, [old_one_shot, recent_one_shot, live_one_shot, cron_disabled, cron_live]

    def test_drops_old_consumed_one_shot_and_keeps_the_rest(self):
        cutoff, records = self._make_inputs()
        kept, pruned = prune_consumed_once_records(records, cutoff)
        assert pruned == 1
        kept_ids = [r["id"] for r in kept]
        assert "old-once" not in kept_ids
        assert "recent-once" in kept_ids
        assert "live-once" in kept_ids
        assert "cron-disabled" in kept_ids
        assert "cron-live" in kept_ids

    def test_unparseable_completed_at_is_kept_conservatively(self):
        cutoff = datetime.datetime(2026, 8, 1, tzinfo=UTC).timestamp()
        record = {
            "id": "weird",
            "enabled": False,
            "completed_at": "not-a-date",
            "trigger": {"type": "once"},
        }
        kept, pruned = prune_consumed_once_records([record], cutoff)
        assert pruned == 0
        assert len(kept) == 1

    def test_empty_input_returns_empty_pair(self):
        kept, pruned = prune_consumed_once_records([], 0.0)
        assert kept == []
        assert pruned == 0


# --------------------------------------------------------------------------- #
# next_cron_time                                                              #
# --------------------------------------------------------------------------- #


class TestNextCronTime:
    def test_every_five_minutes_advances_to_next_multiple(self):
        # base = 12:03:00 -> next */5 must be 12:05:00 (croniter is
        # strictly-after-the-base, by design).
        base = datetime.datetime(2026, 1, 1, 12, 3, 0, tzinfo=UTC)
        assert next_cron_time("*/5 * * * *", base) == datetime.datetime(
            2026, 1, 1, 12, 5, 0, tzinfo=UTC
        )

    def test_daily_at_specific_hour(self):
        # base = 09:00 -> next "0 14 * * *" should be 14:00 same day.
        base = datetime.datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
        assert next_cron_time("0 14 * * *", base) == datetime.datetime(
            2026, 1, 1, 14, 0, 0, tzinfo=UTC
        )


# --------------------------------------------------------------------------- #
# schedule_next_run                                                           #
# --------------------------------------------------------------------------- #


class TestScheduleNextRun:
    def test_cron_trigger_advances_to_next_iso_instant(self):
        record = {"trigger": {"type": "cron", "expr": "*/5 * * * *"}}
        base = datetime.datetime(2026, 1, 1, 12, 3, 0, tzinfo=UTC)
        with mock.patch(
            "ouroboros.platform_layer.local_zoneinfo",
            return_value=UTC,
        ):
            assert schedule_next_run(record, base=base) == "2026-01-01T12:05:00+00:00"

    def test_one_shot_trigger_returns_empty_string(self):
        record = {"trigger": {"type": "once", "run_at": "2026-01-01T00:00:00Z"}}
        base = datetime.datetime(2026, 1, 1, tzinfo=UTC)
        with mock.patch(
            "ouroboros.platform_layer.local_zoneinfo",
            return_value=UTC,
        ):
            assert schedule_next_run(record, base=base) == ""

    def test_blank_cron_expr_returns_empty_string(self):
        record = {"trigger": {"type": "cron", "expr": ""}}
        base = datetime.datetime(2026, 1, 1, tzinfo=UTC)
        with mock.patch(
            "ouroboros.platform_layer.local_zoneinfo",
            return_value=UTC,
        ):
            assert schedule_next_run(record, base=base) == ""

    def test_record_level_cron_fallback_when_trigger_has_no_expr(self):
        # Some legacy records carry the cron expression at the top level
        # rather than nested under trigger; the helper must honour that.
        record = {"cron": "*/10 * * * *"}
        base = datetime.datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC)
        with mock.patch(
            "ouroboros.platform_layer.local_zoneinfo",
            return_value=UTC,
        ):
            assert schedule_next_run(record, base=base) == "2026-01-01T12:10:00+00:00"
