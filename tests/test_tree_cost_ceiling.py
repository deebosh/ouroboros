"""One deciding money number per task tree, and the wrap-up affordability rail.

Covers the graceful in-task cost stop that borrows the ledger fence's own
per-attempt reservation, the cache-aware shape of that reservation, the root
ceiling no tree member may exceed, and the global-budget default.
"""
from __future__ import annotations

import queue
from types import SimpleNamespace
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
            "t1", "anthropic/claude-test", 95_000, provider="openrouter", ttl_seconds=300.0,
        )
        warm = usage_accounting._reservation_cost(_request(task_id="t1"))
        assert warm is not None and warm < cold

    def test_direct_route_and_ledger_identity_share_a_split(self):
        usage_accounting.stash_task_cache_split(
            "t1", "anthropic/claude-test", 95_000, provider="anthropic", ttl_seconds=300.0,
        )
        assert usage_accounting.last_task_cache_split(
            "t1", "anthropic::claude-test", provider="anthropic",
        ) == 95_000

    def test_direct_split_is_cold_after_openrouter_fallback(self):
        direct = _request(
            task_id="t1", provider="anthropic", model="anthropic::claude-test",
        )
        fallback = _request(
            task_id="t1", provider="openrouter", model="anthropic/claude-test",
        )
        cold = usage_accounting._reservation_cost(fallback)
        usage_accounting.stash_task_cache_split(
            "t1", direct.model, 95_000, provider=direct.provider, ttl_seconds=300.0,
        )

        assert usage_accounting.last_task_cache_split(
            "t1", direct.model, provider=direct.provider,
        ) == 95_000
        assert usage_accounting.last_task_cache_split(
            "t1", fallback.model, provider=fallback.provider,
        ) is None
        assert usage_accounting._reservation_cost(fallback) == cold

    def test_a_split_of_another_model_is_never_inherited(self):
        cold = usage_accounting._reservation_cost(_request(task_id="t1"))
        usage_accounting.stash_task_cache_split(
            "t1", "anthropic/other-model", 95_000, provider="openrouter", ttl_seconds=300.0,
        )
        assert usage_accounting._reservation_cost(_request(task_id="t1")) == cold

    def test_a_lapsed_split_is_never_inherited(self):
        cold = usage_accounting._reservation_cost(_request(task_id="t1"))
        usage_accounting.stash_task_cache_split(
            "t1", "anthropic/claude-test", 95_000, provider="openrouter", ttl_seconds=-1.0,
        )
        assert usage_accounting._reservation_cost(_request(task_id="t1")) == cold

    def test_a_request_without_a_task_id_still_resolves_its_split(self, tmp_path):
        """Regression: the main-loop request carries no task id of its own.

        The bound scope's id has to reach the request before the reservation is
        priced, otherwise the split is never found and the cache-aware
        reservation silently degrades to a full write on every live round.
        """
        usage_accounting.stash_task_cache_split(
            "live-task", "anthropic/claude-test", 95_000, provider="openrouter", ttl_seconds=300.0,
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
        answers = iter((True, False))
        monkeypatch.setattr(task_pacing, "wrapup_reservation_fits", lambda **_k: next(answers))
        monkeypatch.setattr(
            "ouroboros.loop._forced_final_answer",
            lambda ctx_, **kwargs: ("wrapped up", ctx_.accumulated_usage, {"kwargs": kwargs}),
        )

        result = _check_budget_limits(ctx, None, self._ceiling(50.0))

        assert result is not None
        assert ctx.accumulated_usage["cost_stop_rail"] == "wrapup_reservation_last_fit"
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
        text = task_pacing.wrapup_last_fit_text(49.9, self._ceiling(50.0))

        assert "$49.900" in text and "$50.00" in text
        assert "wrap-up call" in text


class TestOneCeilingPerTree:
    """Every member stays at or below its root's deciding money number."""

    def _profile(self):
        return normalize_budget_profile(None)

    def test_a_non_root_member_never_exceeds_the_root_deciding_number(self):
        root = task_pacing.resolve_cost_ceiling(40.0, self._profile(), root_cap_usd=50.0)
        member = task_pacing.resolve_cost_ceiling(
            40.0, self._profile(), root_cap_usd=50.0, non_root_member=True,
        )

        assert "global_pct" in root.basis
        assert "global_pct" in member.basis
        assert "non_root_member" in member.basis
        assert member.ceiling_usd <= root.ceiling_usd == 20.0

    def test_a_descendant_tightens_when_global_remaining_falls(self):
        early = task_pacing.resolve_cost_ceiling(
            40.0, self._profile(), root_cap_usd=50.0, non_root_member=True,
        )
        late = task_pacing.resolve_cost_ceiling(
            4.0, self._profile(), root_cap_usd=50.0, non_root_member=True,
        )

        assert late.ceiling_usd < early.ceiling_usd

    def test_without_a_root_cap_the_global_component_still_binds(self):
        member = task_pacing.resolve_cost_ceiling(
            20.0, self._profile(), root_cap_usd=None, non_root_member=True,
        )

        assert "global_pct" in member.basis
        assert member.ceiling_usd == 10.0

    def test_the_default_keeps_the_historical_root_semantics(self):
        positional = task_pacing.resolve_cost_ceiling(20.0, self._profile(), root_cap_usd=50.0)

        assert positional.basis == "min(global_pct, root_cap_minus_margin)"

    def test_a_tree_member_resolves_the_cap_minus_margin_from_its_scope(self):
        with _scoped("child", "root", 50.0):
            ceiling = task_pacing.resolve_task_cost_ceiling(SimpleNamespace(), 40.0)

        assert "non_root_member" in ceiling.basis
        assert ceiling.ceiling_usd == 20.0

    def test_the_root_of_the_tree_keeps_both_components(self):
        with _scoped("root", "root", 50.0):
            ceiling = task_pacing.resolve_task_cost_ceiling(SimpleNamespace(), 40.0)

        assert "global_pct" in ceiling.basis

    def test_the_disclosed_ceiling_is_the_object_the_loop_decides_on(self):
        from ouroboros.loop import _resolve_task_cost_ceiling

        ctx = SimpleNamespace()
        with _scoped("root", "root", 50.0):
            disclosure = task_pacing.in_task_cost_ceiling_disclosure(ctx, 40.0)
            deciding = _resolve_task_cost_ceiling(ctx, 4.0)

        assert deciding is ctx._cost_ceiling
        assert disclosure["ceiling_usd"] == deciding.ceiling_usd
        assert disclosure["state"] == deciding.state

    def test_a_context_that_cannot_be_stashed_still_discloses(self):
        disclosure = task_pacing.in_task_cost_ceiling_disclosure(object(), 40.0)

        assert "state" in disclosure and "rule" in disclosure

    def test_the_checkpoint_and_the_pacing_note_share_one_formatter(self):
        active = task_pacing.resolve_cost_ceiling(
            None, normalize_budget_profile(None), root_cap_usd=50.0,
        )
        line = task_pacing.tree_spend_line(
            {"accounted_usd": 12.0, "root_limit_usd": 50.0}, active,
        )

        assert line.startswith("Task tree spend: ~$12.00")
        assert "in-task cost ceiling" in line and "$50.00 hard tree cap" in line
        assert task_pacing.tree_spend_line({"accounted_usd": None}, active) == ""

    def test_the_rails_line_names_the_binding_bound(self):
        ceiling_binds = task_pacing._headroom_phrase(40.0, 10.0, 2.0)
        wallet_binds = task_pacing._headroom_phrase(3.0, 40.0, 2.0)

        assert ceiling_binds == "$8.00 budget left (in-task cost ceiling binds)"
        assert wallet_binds == "$3.00 budget left (wallet binds)"
        assert task_pacing._headroom_phrase(None, None, None) == "budget left unknown"

    def test_acceptance_rails_use_tree_spend_for_ceiling_headroom(self, monkeypatch):
        monkeypatch.setattr(
            usage_accounting, "usage_projection",
            lambda *_a, **_k: {"accounted_usd": 42.0, "remaining_known_usd": 58.0},
        )
        with _scoped("child", "root", 47.0):
            line = task_pacing._acceptance_rails_line_inner(
                SimpleNamespace(has_deadline=False), self._profile(), 0,
                {"task_cost_usd": 2.0, "cost_ceiling_usd": 47.0},
                required_blocking=False,
            )

        assert "$2.00 spent this task" in line
        assert "$5.00 budget left (in-task cost ceiling binds)" in line


class TestGlobalBudgetDefault:
    """One number for the global budget, whatever the reader."""

    def test_an_absent_setting_resolves_the_product_default(self, monkeypatch):
        from ouroboros.config import SETTINGS_DEFAULTS
        from ouroboros.settings_setup_contract import resolve_total_budget_usd

        monkeypatch.delenv("TOTAL_BUDGET", raising=False)

        assert resolve_total_budget_usd() == float(SETTINGS_DEFAULTS["TOTAL_BUDGET"])

    def test_an_explicit_zero_stays_no_finite_global_budget(self, monkeypatch):
        from ouroboros.settings_setup_contract import resolve_total_budget_usd

        monkeypatch.setenv("TOTAL_BUDGET", "0")

        assert resolve_total_budget_usd() is None

    def test_junk_falls_back_to_the_product_default(self, monkeypatch):
        from ouroboros.config import SETTINGS_DEFAULTS
        from ouroboros.settings_setup_contract import resolve_total_budget_usd

        monkeypatch.setenv("TOTAL_BUDGET", "not-a-number")

        assert resolve_total_budget_usd() == float(SETTINGS_DEFAULTS["TOTAL_BUDGET"])

    def test_every_reader_agrees_on_the_absent_setting(self, monkeypatch):
        """Regression: an env-less harness install used to see $1 on the loop's
        money axis, no limit at all on the bound scope, and $200 at the ledger
        fence -- so one round of work could reject every later task."""
        from ouroboros.settings_setup_contract import resolve_total_budget_usd
        from ouroboros.usage_accounting import _global_limit

        monkeypatch.delenv("TOTAL_BUDGET", raising=False)
        expected = resolve_total_budget_usd()

        assert expected is not None and expected > 1.0
        assert _global_limit(_request()) == expected

    def test_an_unset_budget_no_longer_rejects_a_task_at_round_one(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TOTAL_BUDGET", raising=False)
        from ouroboros.settings_setup_contract import resolve_total_budget_usd

        assert resolve_total_budget_usd() is not None
        ctx = _ctx(round_idx=1, accumulated_usage={"cost": 1.5})
        disabled = task_pacing.resolve_cost_ceiling(None, normalize_budget_profile(None))

        assert _check_budget_limits(ctx, None, disabled) is None
