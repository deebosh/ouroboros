"""ABI 7.0 Q10 removal pins (owner decisions Q10=A x3, F3.0 opening train).

Per-surface removal tests in the F11 style: each pin asserts the ABSENCE of a
removed surface, so a later change cannot quietly resurrect a retired knob or
guard. One section per Q10 item:

- OUROBOROS_SCOPE_REVIEW_FLOOR: key, owner endpoint, gateway contract, web
  client, shell/browser guards — removed; a stored value is stripped on load.
- fail_tasks: the budget-drain batch terminalizer is removed; pausing before
  dispatch (the E13 scenario) is the one live semantics for a budget-exhausted
  queued task, and the pre-assignment pending drop is the one secondary settle
  site left.
"""

from __future__ import annotations

import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]


# --- Q10 item 1: OUROBOROS_SCOPE_REVIEW_FLOOR ---------------------------------

def test_scope_review_floor_key_is_retired_not_defaulted():
    from ouroboros.settings_defaults import RETIRED_SETTING_KEYS, SETTINGS_DEFAULTS

    assert "OUROBOROS_SCOPE_REVIEW_FLOOR" not in SETTINGS_DEFAULTS
    assert "OUROBOROS_SCOPE_REVIEW_FLOOR" in RETIRED_SETTING_KEYS


def test_stored_scope_review_floor_value_is_stripped_on_load(monkeypatch, tmp_path):
    """A pre-7.0 settings document carrying the retired key still loads cleanly,
    and the stale value never reaches the effective settings (owner history is
    not an error, just inert bytes)."""
    import ouroboros.config as cfg

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "OUROBOROS_SCOPE_REVIEW_FLOOR": "advisory",  # retired in 7.0
        "TOTAL_BUDGET": "10",
    }), encoding="utf-8")
    monkeypatch.setattr(cfg, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.delenv("OUROBOROS_SCOPE_REVIEW_FLOOR", raising=False)

    loaded = cfg.load_settings()

    assert "OUROBOROS_SCOPE_REVIEW_FLOOR" not in loaded
    assert not hasattr(cfg, "get_scope_review_floor"), "no consumer may read the floor"


def test_scope_review_floor_gateway_surface_is_gone():
    from ouroboros.gateway import contracts, settings as gw_settings

    assert not hasattr(contracts, "OwnerScopeReviewFloorResponse")
    assert "POST /api/owner/scope-review-floor" not in contracts.HTTP_ENDPOINTS
    assert not hasattr(gw_settings, "api_owner_scope_review_floor")
    assert not hasattr(gw_settings, "_api_owner_scope_review_floor_sync")


def test_scope_review_floor_guards_are_gone_but_family_read_carve_survives():
    from ouroboros.tools import browser, registry, registry_guard_process

    for mod in (registry, registry_guard_process):
        assert not hasattr(mod, "_detect_scope_review_floor_self_lowering")
    for name in (
        "_blocks_scope_review_floor_self_lowering_js",
        "_is_scope_review_floor_owner_post",
        "_block_scope_review_floor_owner_post",
    ):
        assert not hasattr(browser, name)
    # The shared read-carve the floor guard adjudicated stays, family-wide.
    assert callable(registry_guard_process._is_pure_read_inspection)
    assert callable(registry._detect_safety_mode_self_lowering)


def test_scope_review_floor_left_no_source_remnants():
    """Grep-level absence over the runtime tree: no code path may still spell
    the retired key or endpoint (docs/history and the retirement entry itself
    are the only legitimate spellings)."""
    allowed = {
        pathlib.Path("ouroboros/settings_defaults.py"),  # RETIRED_SETTING_KEYS entry
        pathlib.Path("ouroboros/tools/registry_guard_process.py"),  # historical incident comment
    }
    offenders = []
    for root in ("ouroboros", "supervisor", "web", "prompts"):
        for path in sorted((REPO / root).rglob("*")):
            if not path.is_file() or path.suffix not in {".py", ".js", ".md", ".html"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "SCOPE_REVIEW_FLOOR" in text.upper() or "scope-review-floor" in text:
                rel = path.relative_to(REPO)
                if rel not in allowed:
                    offenders.append(str(rel))
    assert not offenders, f"retired floor surface respelled in: {offenders}"


# --- Q10 item 2: fail_tasks (budget-drain terminalizer) -----------------------

def test_fail_tasks_is_gone_and_pause_scenario_supersedes_it():
    import ouroboros.task_results as task_results

    assert not hasattr(task_results, "fail_tasks")
    # The E-suite records the supersession explicitly: E8 (budget-drain
    # fail_tasks) retired deliberately, E13 (pause before dispatch) lives.
    from tests.fixtures_e2e_cancellation import SCENARIOS

    assert "E8" not in SCENARIOS
    assert "E13" in SCENARIOS


def test_fail_tasks_left_no_source_remnants():
    offenders = []
    for root in ("ouroboros", "supervisor", "web", "prompts"):
        for path in sorted((REPO / root).rglob("*.py")):
            if "fail_tasks" in path.read_text(encoding="utf-8", errors="replace"):
                offenders.append(str(path.relative_to(REPO)))
    assert not offenders, f"removed fail_tasks respelled in: {offenders}"
