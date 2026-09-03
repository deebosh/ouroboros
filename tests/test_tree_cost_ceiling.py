"""One deciding money number per task tree, and the wrap-up affordability rail.

Covers the graceful in-task cost stop that borrows the ledger fence's own
per-attempt reservation, the cache-aware shape of that reservation, the single
root-resolved ceiling every tree member shares, and the global-budget default.
"""
from __future__ import annotations

import queue
from unittest.mock import MagicMock

import pytest

from ouroboros import task_pacing, usage_accounting
from ouroboros.contracts.task_contract import normalize_budget_profile
from ouroboros.loop import _check_budget_limits, _RoundLimitContext


@pytest.fixture(autouse=True)
def _priced_anthropic_route(monkeypatch):
    """An isolated catalog, so the money numbers below are exact and offline."""
    from ouroboros import pricing

    monkeypatch.setattr(pricing, "_cached_pricing", {})
    monkeypatch.setattr(pricing, "_pricing_fetched_at", {})
    monkeypatch.setattr(pricing, "_pricing_retry_after", {})
    monkeypatch.setattr(pricing, "_pricing_fetch_in_progress", set())
    monkeypatch.setattr(
        "ouroboros.llm.fetch_openrouter_pricing",
        # (input, cached_read, cache_write(5m), output) per 1M tokens.
        lambda **_kwargs: {"anthropic/claude-test": (3.0, 0.3, 3.75, 15.0)},
    )
    usage_accounting._reset_task_cache_splits()
    yield
    usage_accounting._reset_task_cache_splits()


def _scoped(task_id, root_id, root_limit=None):
    from ouroboros.usage_accounting import UsageScope, usage_scope

    return usage_scope(UsageScope(
        drive_root=None, task_id=task_id, root_task_id=root_id, root_limit_usd=root_limit,
    ))


def _ctx(**overrides):
    llm = MagicMock()
    values = dict(
        messages=[],
        llm=llm,
        active_model="anthropic/claude-test",
        active_effort="high",
        max_retries=1,
        drive_logs=None,
        task_id="task1",
        round_idx=3,
        event_queue=queue.Queue(),
        accumulated_usage={"cost": 1.0, "_context_prompt_estimate": 400_000},
        task_type="task",
        active_use_local=False,
        max_rounds=100,
        llm_trace={},
    )
    values.update(overrides)
    return _RoundLimitContext(**values)


def _request(**overrides):
    values = dict(
        model="anthropic/claude-test",
        provider="openrouter",
        prompt_tokens_estimate=100_000,
        max_completion_tokens=1_000,
    )
    values.update(overrides)
    return usage_accounting.AttemptRequest(**values)


class TestCacheAwareReservation:
    """The fence prices what the task's own last send actually read from cache."""

    def test_full_write_without_an_observed_split(self):
        cold = usage_accounting._reservation_cost(_request(task_id="t1"))
        assert cold is not None and cold > 0

    def test_observed_split_lowers_the_reservation(self):
        cold = usage_accounting._reservation_cost(_request(task_id="t1"))
        usage_accounting.stash_task_cache_split(
            "t1", "anthropic/claude-test", 95_000, ttl_seconds=300.0,
        )
        warm = usage_accounting._reservation_cost(_request(task_id="t1"))
        assert warm is not None and warm < cold

    def test_a_split_of_another_model_is_never_inherited(self):
        cold = usage_accounting._reservation_cost(_request(task_id="t1"))
        usage_accounting.stash_task_cache_split(
            "t1", "anthropic/other-model", 95_000, ttl_seconds=300.0,
        )
        assert usage_accounting._reservation_cost(_request(task_id="t1")) == cold

    def test_a_lapsed_split_is_never_inherited(self):
        cold = usage_accounting._reservation_cost(_request(task_id="t1"))
        usage_accounting.stash_task_cache_split(
            "t1", "anthropic/claude-test", 95_000, ttl_seconds=-1.0,
        )
        assert usage_accounting._reservation_cost(_request(task_id="t1")) == cold

    def test_a_request_without_a_task_id_still_resolves_its_split(self, tmp_path):
        """Regression: the main-loop request carries no task id of its own.

        The bound scope's id has to reach the request before the reservation is
        priced, otherwise the split is never found and the cache-aware
        reservation silently degrades to a full write on every live round.
        """
        usage_accounting.stash_task_cache_split(
            "live-task", "anthropic/claude-test", 95_000, ttl_seconds=300.0,
        )
        cold = usage_accounting._reservation_cost(_request(task_id="unknown-task"))
        with _scoped("live-task", "live-task"):
            merged, _scope = usage_accounting._merge_scope(_request())
            assert merged.task_id == "live-task"
            assert usage_accounting._reservation_cost(merged) < cold

    def test_the_reservation_still_takes_one_positional_argument(self):
        assert usage_accounting._reservation_cost(_request(task_id="t1", reservation_usd=2.0)) == 2.0


class TestWrapupAffordability:
    """The graceful stop uses the fence's own reservation, and fails open."""

    def test_no_bound_scope_fails_open(self):
        assert task_pacing.wrapup_reservation_fits(
            model="anthropic/claude-test", prompt_tokens=400_000,
            root_cap_usd=50.0, deciding_usd=49.0,
        ) is None

    def test_no_root_cap_fails_open(self):
        with _scoped("t1", "t1"):
            assert task_pacing.wrapup_reservation_fits(
                model="anthropic/claude-test", prompt_tokens=400_000,
                root_cap_usd=None, deciding_usd=1.0,
            ) is None

    def test_unknown_price_fails_open(self):
        with _scoped("t1", "t1"):
            assert task_pacing.wrapup_reservation_fits(
                model="~no-such-model/never-priced", prompt_tokens=400_000,
                root_cap_usd=50.0, deciding_usd=49.0,
            ) is None

    def test_a_wrap_up_that_no_longer_fits_reports_false(self):
        with _scoped("t1", "t1"):
            assert task_pacing.wrapup_reservation_fits(
                model="anthropic/claude-test", prompt_tokens=400_000,
                root_cap_usd=50.0, deciding_usd=49.99,
            ) is False

    def test_a_wrap_up_that_still_fits_reports_true(self):
        with _scoped("t1", "t1"):
            assert task_pacing.wrapup_reservation_fits(
                model="anthropic/claude-test", prompt_tokens=1_000,
                root_cap_usd=50.0, deciding_usd=0.0,
            ) is True

    def test_the_predicate_equals_the_fence_reservation(self):
        with _scoped("t1", "t1"):
            from ouroboros.loop_llm_call import MAIN_LOOP_MAX_TOKENS

            bound = usage_accounting._reservation_cost(_request(
                task_id="t1", prompt_tokens_estimate=400_000,
                max_completion_tokens=MAIN_LOOP_MAX_TOKENS,
            ))
            assert task_pacing.wrapup_reservation_fits(
                model="anthropic/claude-test", prompt_tokens=400_000,
                root_cap_usd=bound + 1.0, deciding_usd=1.0 + 1e-6,
            ) is False

    def test_the_predicate_never_reads_the_usage_projection(self, monkeypatch):
        monkeypatch.setattr(
            usage_accounting, "usage_projection",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("per-round ledger scan")),
        )
        with _scoped("t1", "t1"):
            assert task_pacing.wrapup_reservation_fits(
                model="anthropic/claude-test", prompt_tokens=400_000,
                root_cap_usd=50.0, deciding_usd=49.99,
            ) is False


class TestWrapupAffordabilityRail:
    """The loop soft-lands on the rail, and stays silent when it cannot know."""

    def _ceiling(self, root_cap):
        return task_pacing.resolve_cost_ceiling(
            None, normalize_budget_profile(None), root_cap_usd=root_cap,
        )

    def test_the_rail_soft_lands_with_a_typed_stamp(self, monkeypatch):
        ctx = _ctx()
        monkeypatch.setattr(
            "ouroboros.loop._loop_tree_accounting", lambda **_k: {"accounted_usd": 20.0},
        )
        monkeypatch.setattr(task_pacing, "wrapup_reservation_fits", lambda **_k: False)
        monkeypatch.setattr(
            "ouroboros.loop._forced_final_answer",
            lambda ctx_, **kwargs: ("wrapped up", ctx_.accumulated_usage, {"kwargs": kwargs}),
        )

        result = _check_budget_limits(ctx, None, self._ceiling(50.0))

        assert result is not None
        assert ctx.accumulated_usage["cost_stop_rail"] == "wrapup_reservation_unaffordable"
        assert result[2]["kwargs"]["reason_code"] == "budget_exhausted"

    def test_a_missing_prompt_estimate_keeps_the_rail_silent(self, monkeypatch):
        ctx = _ctx(accumulated_usage={"cost": 1.0})
        monkeypatch.setattr(
            "ouroboros.loop._loop_tree_accounting", lambda **_k: {"accounted_usd": 20.0},
        )
        monkeypatch.setattr(
            task_pacing, "wrapup_reservation_fits",
            lambda **_k: (_ for _ in ()).throw(AssertionError("armed without a prompt size")),
        )

        assert _check_budget_limits(ctx, None, self._ceiling(50.0)) is None

    def test_a_disabled_ceiling_never_arms_the_rail(self, monkeypatch):
        ctx = _ctx()
        monkeypatch.setattr(
            task_pacing, "wrapup_reservation_fits",
            lambda **_k: (_ for _ in ()).throw(AssertionError("armed on a disabled ceiling")),
        )
        disabled = task_pacing.resolve_cost_ceiling(
            None, normalize_budget_profile({"cost_hard_stop_pct": 0}), root_cap_usd=50.0,
        )

        assert _check_budget_limits(ctx, None, disabled) is None

    def test_the_stop_text_names_the_cap_and_the_reason(self):
        text = task_pacing.wrapup_unaffordable_text(49.9, self._ceiling(50.0))

        assert "$49.900" in text and "$50.00" in text
        assert "wrap-up call" in text
