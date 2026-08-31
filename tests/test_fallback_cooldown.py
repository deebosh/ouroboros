"""Unit coverage for `ouroboros.fallback_cooldown` — the process-local 429 cooldown map
that bounds re-hits of a just-rate-limited model within a single worker.

The module is intentionally tiny (80 lines, pure functions + a process-local dict guarded
by a lock). Tests pin every public and module-private helper so future refactors keep the
documented contract: default-on, fail-soft, passive heal-back, per-(model, use_local) key.
"""

from __future__ import annotations

import pytest

import ouroboros.fallback_cooldown as fallback_cooldown


@pytest.fixture(autouse=True)
def _isolate_cooldown_map(monkeypatch):
    """Reset the module-global `_cooldown` map BEFORE every test and clear the env vars
    that the helpers read. We use autouse so individual tests stay focused on their
    surface and never leak state across the suite."""
    fallback_cooldown.reset_for_tests()
    for var in (
        "OUROBOROS_FALLBACK_COOLDOWN_ENABLED",
        "OUROBOROS_FALLBACK_COOLDOWN_SEC",
        "OUROBOROS_FALLBACK_ATTEMPTS_PER_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


# ---------------------------------------------------------------------------
# cooldown_enabled: default-on, only explicit falsey disables.
# ---------------------------------------------------------------------------


def test_cooldown_enabled_default_on_when_unset(monkeypatch):
    monkeypatch.delenv("OUROBOROS_FALLBACK_COOLDOWN_ENABLED", raising=False)
    assert fallback_cooldown.cooldown_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_cooldown_enabled_disabled_on_explicit_falsey(monkeypatch, value):
    monkeypatch.setenv("OUROBOROS_FALLBACK_COOLDOWN_ENABLED", value)
    assert fallback_cooldown.cooldown_enabled() is False


@pytest.mark.parametrize("value", ["0", "FALSE", "No", "OFF", "  False  "])
def test_cooldown_enabled_disabled_on_explicit_falsey_any_case(monkeypatch, value):
    """Case-insensitive, whitespace-trimmed — the helper lowercases after strip()."""
    monkeypatch.setenv("OUROBOROS_FALLBACK_COOLDOWN_ENABLED", value)
    assert fallback_cooldown.cooldown_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "anything", "random", " "])
def test_cooldown_enabled_stays_on_for_truthy_or_garbage(monkeypatch, value):
    """Anything outside the explicit falsey set is treated as default-on."""
    monkeypatch.setenv("OUROBOROS_FALLBACK_COOLDOWN_ENABLED", value)
    assert fallback_cooldown.cooldown_enabled() is True


def test_cooldown_enabled_treats_blank_as_default_on(monkeypatch):
    """Empty string after strip() is also default-on — only explicit falsey opts out."""
    monkeypatch.setenv("OUROBOROS_FALLBACK_COOLDOWN_ENABLED", "")
    assert fallback_cooldown.cooldown_enabled() is True


# ---------------------------------------------------------------------------
# _cooldown_sec: env-driven, non-negative, fail-soft on garbage.
# ---------------------------------------------------------------------------


def test_cooldown_sec_default_when_unset(monkeypatch):
    monkeypatch.delenv("OUROBOROS_FALLBACK_COOLDOWN_SEC", raising=False)
    assert fallback_cooldown._cooldown_sec() == 120.0


def test_cooldown_sec_uses_set_value(monkeypatch):
    monkeypatch.setenv("OUROBOROS_FALLBACK_COOLDOWN_SEC", "45")
    assert fallback_cooldown._cooldown_sec() == 45.0


def test_cooldown_sec_clamps_negative_to_zero(monkeypatch):
    """max(0.0, ...) — negative durations make no sense and become zero."""
    monkeypatch.setenv("OUROBOROS_FALLBACK_COOLDOWN_SEC", "-5")
    assert fallback_cooldown._cooldown_sec() == 0.0


def test_cooldown_sec_falls_back_on_garbage(monkeypatch):
    """A non-numeric value triggers the except branch → 120.0 default."""
    monkeypatch.setenv("OUROBOROS_FALLBACK_COOLDOWN_SEC", "garbage")
    assert fallback_cooldown._cooldown_sec() == 120.0


def test_cooldown_sec_handles_float_string(monkeypatch):
    """Floats are accepted — only non-numeric strings fall through."""
    monkeypatch.setenv("OUROBOROS_FALLBACK_COOLDOWN_SEC", "7.5")
    assert fallback_cooldown._cooldown_sec() == 7.5


# ---------------------------------------------------------------------------
# attempts_per_model: 1..2 clamp, fail-soft on garbage.
# ---------------------------------------------------------------------------


def test_attempts_per_model_default_when_unset(monkeypatch):
    monkeypatch.delenv("OUROBOROS_FALLBACK_ATTEMPTS_PER_MODEL", raising=False)
    assert fallback_cooldown.attempts_per_model() == 1


def test_attempts_per_model_uses_value_within_range(monkeypatch):
    monkeypatch.setenv("OUROBOROS_FALLBACK_ATTEMPTS_PER_MODEL", "2")
    assert fallback_cooldown.attempts_per_model() == 2


def test_attempts_per_model_clamps_above_max(monkeypatch):
    """max(1, min(2, ...)) — anything > 2 is clamped to 2."""
    monkeypatch.setenv("OUROBOROS_FALLBACK_ATTEMPTS_PER_MODEL", "5")
    assert fallback_cooldown.attempts_per_model() == 2


def test_attempts_per_model_clamps_below_min(monkeypatch):
    """Anything < 1 is clamped to 1 — zero attempts would skip the candidate entirely."""
    monkeypatch.setenv("OUROBOROS_FALLBACK_ATTEMPTS_PER_MODEL", "0")
    assert fallback_cooldown.attempts_per_model() == 1


def test_attempts_per_model_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("OUROBOROS_FALLBACK_ATTEMPTS_PER_MODEL", "garbage")
    assert fallback_cooldown.attempts_per_model() == 1


# ---------------------------------------------------------------------------
# mark_cooldown + is_cooling_down: round-trip, passive heal-back.
# ---------------------------------------------------------------------------


def test_is_cooling_down_initially_false():
    fallback_cooldown.reset_for_tests()
    assert fallback_cooldown.is_cooling_down("model-a") is False


def test_mark_cooldown_then_is_cooling_down_round_trip():
    fallback_cooldown.reset_for_tests()
    fallback_cooldown.mark_cooldown("model-a")
    assert fallback_cooldown.is_cooling_down("model-a") is True


def test_passive_heal_back_drops_entry_after_window(monkeypatch):
    """When the window has elapsed, is_cooling_down returns False AND drops the
    internal dict entry — the second call must also read False, with no orphan key."""
    fallback_cooldown.reset_for_tests()
    monkeypatch.setattr(fallback_cooldown.time, "time", lambda: 1_000_000.0)

    fallback_cooldown.mark_cooldown("model-a")
    assert fallback_cooldown.is_cooling_down("model-a") is True
    assert ("model-a", False) in fallback_cooldown._cooldown

    # Jump past the cooldown window (default 120s).
    monkeypatch.setattr(fallback_cooldown.time, "time", lambda: 1_000_500.0)

    # First call after window: returns False and pops the entry (heal-back).
    assert fallback_cooldown.is_cooling_down("model-a") is False
    # Second call: still False (and no longer reads the now-expired entry).
    assert fallback_cooldown.is_cooling_down("model-a") is False
    # Passive heal-back: the internal dict is now empty.
    assert ("model-a", False) not in fallback_cooldown._cooldown
    assert fallback_cooldown._cooldown == {}


def test_mark_cooldown_respects_custom_duration(monkeypatch):
    """A short OUROBOROS_FALLBACK_COOLDOWN_SEC is honored."""
    monkeypatch.setenv("OUROBOROS_FALLBACK_COOLDOWN_SEC", "5")
    fallback_cooldown.reset_for_tests()
    monkeypatch.setattr(fallback_cooldown.time, "time", lambda: 2_000_000.0)

    fallback_cooldown.mark_cooldown("model-a")
    # Still inside the 5s window.
    monkeypatch.setattr(fallback_cooldown.time, "time", lambda: 2_000_003.0)
    assert fallback_cooldown.is_cooling_down("model-a") is True
    # Past the 5s window.
    monkeypatch.setattr(fallback_cooldown.time, "time", lambda: 2_000_010.0)
    assert fallback_cooldown.is_cooling_down("model-a") is False


def test_mark_cooldown_records_until_timestamp(monkeypatch):
    """The dict value is `now + cooldown_sec`, not a relative offset."""
    monkeypatch.setenv("OUROBOROS_FALLBACK_COOLDOWN_SEC", "30")
    fallback_cooldown.reset_for_tests()
    monkeypatch.setattr(fallback_cooldown.time, "time", lambda: 5_000.0)
    fallback_cooldown.mark_cooldown("model-a")
    assert fallback_cooldown._cooldown[("model-a", False)] == pytest.approx(5_030.0)


def test_mark_cooldown_then_reset_for_tests_clears_state():
    fallback_cooldown.mark_cooldown("model-a")
    assert fallback_cooldown.is_cooling_down("model-a") is True
    fallback_cooldown.reset_for_tests()
    assert fallback_cooldown.is_cooling_down("model-a") is False
    assert fallback_cooldown._cooldown == {}


# ---------------------------------------------------------------------------
# use_local flag keys separately.
# ---------------------------------------------------------------------------


def test_use_local_flag_keys_separately():
    """The key is (model, use_local) — local and remote variants do not collide."""
    fallback_cooldown.reset_for_tests()
    fallback_cooldown.mark_cooldown("model-a", use_local=True)
    # The (model-a, False) bucket is unaffected.
    assert fallback_cooldown.is_cooling_down("model-a", use_local=False) is False
    # The (model-a, True) bucket is on cooldown.
    assert fallback_cooldown.is_cooling_down("model-a", use_local=True) is True


def test_use_local_flag_default_is_false():
    """`mark_cooldown(m)` defaults to use_local=False; explicit and default queries
    address the same key."""
    fallback_cooldown.reset_for_tests()
    fallback_cooldown.mark_cooldown("model-a")
    assert fallback_cooldown.is_cooling_down("model-a") is True
    assert fallback_cooldown.is_cooling_down("model-a", use_local=False) is True


def test_distinct_models_do_not_collide():
    fallback_cooldown.reset_for_tests()
    fallback_cooldown.mark_cooldown("model-a")
    assert fallback_cooldown.is_cooling_down("model-b") is False


def test_empty_model_string_is_normalized_to_string():
    """The helper coerces `model` via `str(model or "")` — None becomes empty key."""
    fallback_cooldown.reset_for_tests()
    fallback_cooldown.mark_cooldown(None)
    assert fallback_cooldown.is_cooling_down(None) is True
    assert fallback_cooldown.is_cooling_down("") is True


# ---------------------------------------------------------------------------
# Disabled short-circuit: when OUROBOROS_FALLBACK_COOLDOWN_ENABLED="0", both
# mark_cooldown (no-op) and is_cooling_down (always False) honor the gate, even if
# stale entries were left in the dict from a previous test or earlier code path.
# ---------------------------------------------------------------------------


def test_disabled_short_circuit_marks_no_op(monkeypatch):
    monkeypatch.setenv("OUROBOROS_FALLBACK_COOLDOWN_ENABLED", "0")
    fallback_cooldown.reset_for_tests()
    fallback_cooldown.mark_cooldown("model-a")
    # mark_cooldown was a no-op — the dict stays empty.
    assert fallback_cooldown._cooldown == {}


def test_disabled_short_circuit_always_reads_false(monkeypatch):
    monkeypatch.setenv("OUROBOROS_FALLBACK_COOLDOWN_ENABLED", "0")
    # Plant a stale entry directly — bypassing the disabled gate.
    fallback_cooldown._cooldown[("stale-model", False)] = 9_999_999_999.0
    assert fallback_cooldown.is_cooling_down("stale-model") is False
    # Stale entry is untouched by disabled reads (we never reach the pop branch).
    assert ("stale-model", False) in fallback_cooldown._cooldown


def test_re_enabling_after_disabled_does_not_resurrect_state(monkeypatch):
    """Toggle behavior is per-call: once disabled, no mark ever persists; once
    re-enabled, is_cooling_down sees only entries written under the enabled gate."""
    monkeypatch.setenv("OUROBOROS_FALLBACK_COOLDOWN_ENABLED", "0")
    fallback_cooldown.mark_cooldown("model-a")  # no-op
    assert fallback_cooldown._cooldown == {}

    monkeypatch.delenv("OUROBOROS_FALLBACK_COOLDOWN_ENABLED", raising=False)
    # Still empty — the disabled-phase mark did not record anything.
    assert fallback_cooldown.is_cooling_down("model-a") is False

    fallback_cooldown.mark_cooldown("model-a")
    assert fallback_cooldown.is_cooling_down("model-a") is True
