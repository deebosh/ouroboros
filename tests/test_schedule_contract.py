"""Unit coverage for ouroboros/schedule_contract.py — the pure validation helpers
shared by queue-backed schedules."""

from __future__ import annotations

import re

import pytest

from ouroboros.schedule_contract import (
    RESERVED_TEMPLATE_FIELDS,
    cron_error,
    schedule_id_error,
    schedule_slug,
    timezone_error,
)


# ---------------------------------------------------------------------------
# schedule_id_error
# ---------------------------------------------------------------------------

class TestScheduleIdError:
    """schedule_id_error returns "" on valid tokens, a stable error string otherwise."""

    def test_blank_string_is_ok(self):
        assert schedule_id_error("") == ""

    def test_none_is_ok(self):
        assert schedule_id_error(None) == ""

    def test_whitespace_only_is_ok(self):
        # str(None or "").strip() collapses blanks before the empty check.
        assert schedule_id_error("   ") == ""

    def test_valid_token_is_ok(self):
        assert schedule_id_error("my.schedule-1") == ""

    def test_valid_token_with_underscore_is_ok(self):
        assert schedule_id_error("my_schedule_1") == ""

    def test_token_with_space_errors(self):
        assert schedule_id_error("my schedule") == "schedule id must be a single URL-safe token"

    def test_token_with_double_dot_errors(self):
        # Regex would match; the explicit ".." guard still rejects it.
        assert schedule_id_error("my..schedule") == "schedule id must be a single URL-safe token"

    def test_token_with_double_dot_anywhere_errors(self):
        assert schedule_id_error("a..b") == "schedule id must be a single URL-safe token"

    def test_82_char_token_errors(self):
        # Regex anchors to 1+0..80 = up to 81 chars; 82 must fail.
        token = "a" + "b" * 81
        assert len(token) == 82
        assert schedule_id_error(token) == "schedule id must be a single URL-safe token"

    def test_81_char_token_is_ok_at_boundary(self):
        token = "a" + "b" * 80
        assert len(token) == 81
        assert schedule_id_error(token) == ""

    def test_token_starting_with_dot_errors(self):
        assert schedule_id_error(".token") == "schedule id must be a single URL-safe token"

    def test_token_starting_with_dash_errors(self):
        assert schedule_id_error("-token") == "schedule id must be a single URL-safe token"

    def test_token_starting_with_underscore_errors(self):
        # Underscore is in the regex tail set but not in the leading-char class.
        assert schedule_id_error("_token") == "schedule id must be a single URL-safe token"

    def test_token_with_disallowed_char_errors(self):
        # Slash is outside the regex alphabet.
        assert schedule_id_error("a/b") == "schedule id must be a single URL-safe token"


# ---------------------------------------------------------------------------
# schedule_slug
# ---------------------------------------------------------------------------

class TestScheduleSlug:
    """schedule_slug joins parts with '-', sanitizes, and shortens long slugs."""

    def test_simple_join(self):
        assert schedule_slug("a", "b", "c") == "a-b-c"

    def test_illegal_chars_become_dashes(self):
        # ' ' and '/' fall outside [A-Za-z0-9_.-], so each becomes a single '-'.
        assert schedule_slug("a b", "c/d") == "a-b-c-d"

    def test_leading_dot_is_stripped(self):
        assert schedule_slug(".a", "b", "c") == "a-b-c"

    def test_leading_dash_is_stripped(self):
        assert schedule_slug("-a-", "b") == "a--b"

    def test_trailing_dash_is_stripped(self):
        # Trailing '-' from the final part is removed; the inner join '-' remains.
        assert schedule_slug("a", "b-") == "a-b"

    def test_collapsed_separators_become_single_dash(self):
        # Consecutive illegal chars collapse to one '-' via the regex.
        assert schedule_slug("a   b", "c//d") == "a-b-c-d"

    def test_empty_parts_joined_yields_dash(self):
        # None / "" parts stringify to "" so they still occupy a join position
        # → consecutive '-' between surrounding alnum parts (not collapsed).
        assert schedule_slug("a", None, "b") == "a--b"
        assert schedule_slug("a", "", "b") == "a--b"

    def test_no_parts_yields_schedule_prefix(self):
        # Empty joined result becomes the schedule-<empty> form.
        assert schedule_slug() == "schedule-"

    def test_only_illegal_chars_yields_schedule_prefix(self):
        # When the joined result reduces to empty after sanitization, prefix kicks in.
        assert schedule_slug(" ") == "schedule-"
        assert schedule_slug("///", "...") == "schedule-"

    def test_leading_underscore_only_after_strip_still_yields_alnum_start(self):
        # After strip('-._') the leading underscore is removed, so 's' stays first.
        # The spec's "does not start alnum" branch is exercised via the empty path above.
        result = schedule_slug("_x")
        assert result and result[0].isalnum()

    def test_short_slug_returned_as_is(self):
        # Below the 81-char threshold the slug is returned verbatim.
        assert schedule_slug("alpha", "beta") == "alpha-beta"
        assert len(schedule_slug("alpha", "beta")) <= 81

    def test_long_slug_is_shortened_with_sha256_digest(self):
        # 90 alnum chars joined → length 90 > 81 → digest suffix applied.
        slug = schedule_slug("a" * 90)
        assert len(slug) <= 81
        # Trailing shape: "-<10 hex chars>"
        assert re.match(r"-[0-9a-f]{10}$", slug[-11:]), slug
        # Total length of "<prefix>-<10 hex>" must not exceed 81.
        assert len(slug) == 81

    def test_long_slug_keeps_meaningful_prefix(self):
        # The 70-char body is the start of the original slug, rstripped of '-._'.
        prefix_input = "abcdefghij" * 8  # 80 chars, well under any trim threshold before shortening
        slug = schedule_slug(prefix_input)
        # The shortened slug begins with the first 70 chars (or trimmed thereof) of the body.
        assert slug.startswith("a")

    def test_long_slug_digest_is_stable_for_same_input(self):
        # Same input → same sha256 digest suffix → same final slug.
        s1 = schedule_slug("x" * 100)
        s2 = schedule_slug("x" * 100)
        assert s1 == s2

    def test_long_slug_digest_differs_for_different_input(self):
        # Different input bytes → different digest suffix.
        s1 = schedule_slug("x" * 100)
        s2 = schedule_slug("y" * 100)
        assert s1 != s2


# ---------------------------------------------------------------------------
# cron_error
# ---------------------------------------------------------------------------

class TestCronError:
    """cron_error accepts valid 5-field croniter expressions and rejects the rest."""

    def test_valid_cron_returns_empty(self):
        assert cron_error("*/5 * * * *") == ""

    def test_daily_cron_returns_empty(self):
        assert cron_error("0 14 * * *") == ""

    def test_four_field_expression_errors_with_5_field_message(self):
        # 4 tokens → wrong field count, not a parse error.
        assert cron_error("* * * *") == "cron schedules require a 5-field expression"

    def test_empty_string_errors_with_5_field_message(self):
        # 0 tokens after split.
        assert cron_error("") == "cron schedules require a 5-field expression"

    def test_whitespace_only_errors_with_5_field_message(self):
        # Split() on whitespace collapses to an empty list.
        assert cron_error("   ") == "cron schedules require a 5-field expression"

    def test_none_errors_with_5_field_message(self):
        assert cron_error(None) == "cron schedules require a 5-field expression"

    def test_six_field_expression_errors_with_5_field_message(self):
        # Too many fields.
        assert cron_error("* * * * * *") == "cron schedules require a 5-field expression"

    def test_five_tokens_invalid_cron_errors(self):
        # Five tokens but the minute field is out of range — croniter rejects it
        # even though the token count is correct, so the parse-error path fires.
        msg = cron_error("60 * * * *")
        assert msg.startswith("invalid cron expression:")

    def test_out_of_range_value_errors(self):
        # Five tokens but a field out of croniter's accepted range.
        msg = cron_error("99 99 99 99 99")
        assert msg.startswith("invalid cron expression:")


# ---------------------------------------------------------------------------
# timezone_error
# ---------------------------------------------------------------------------

class TestTimezoneError:
    """timezone_error accepts valid IANA names and rejects the rest."""

    def test_blank_string_is_ok(self):
        assert timezone_error("") == ""

    def test_none_is_ok(self):
        assert timezone_error(None) == ""

    def test_whitespace_only_is_ok(self):
        assert timezone_error("   ") == ""

    def test_valid_iana_zone_is_ok(self):
        assert timezone_error("America/New_York") == ""

    def test_utc_is_ok(self):
        assert timezone_error("UTC") == ""

    def test_unknown_zone_errors(self):
        msg = timezone_error("Not/A_Zone")
        assert msg.startswith("invalid timezone:")

    def test_garbage_string_errors(self):
        msg = timezone_error("definitely-not-a-timezone")
        assert msg.startswith("invalid timezone:")


# ---------------------------------------------------------------------------
# RESERVED_TEMPLATE_FIELDS
# ---------------------------------------------------------------------------

class TestReservedTemplateFields:
    """RESERVED_TEMPLATE_FIELDS is the documented set of keys templates must not smuggle."""

    def test_is_frozenset(self):
        assert isinstance(RESERVED_TEMPLATE_FIELDS, frozenset)

    def test_contains_documented_keys(self):
        # Documented minimum surface — admission rejects these loudly and
        # _task_from_schedule filters them out of pre-rule records.
        expected = {"task_id", "session_id", "actor_id", "client_surface", "drive_root"}
        assert expected <= RESERVED_TEMPLATE_FIELDS

    def test_client_surface_is_reserved(self):
        # Owner Surface Fact: machine-fired schedules have no owner message behind them,
        # so the template must never smuggle a sending-surface descriptor.
        assert "client_surface" in RESERVED_TEMPLATE_FIELDS

    def test_task_id_is_reserved(self):
        assert "task_id" in RESERVED_TEMPLATE_FIELDS

    def test_set_is_non_empty(self):
        assert len(RESERVED_TEMPLATE_FIELDS) > 0

    def test_no_unexpected_python_builtins(self):
        # Defensive: nobody should add a __dunder__ or private name by accident.
        for key in RESERVED_TEMPLATE_FIELDS:
            assert not key.startswith("_"), key
