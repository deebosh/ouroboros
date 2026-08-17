"""Characterization of the settings READ path, before the normalization seam lands.

`load_settings` does five things to a raw settings document before the shipped
defaults are merged over it: it coerces every known key to its declared type,
folds the deprecated per-subsystem retention keys into the unified one, drops the
keys a release retired, promotes the renamed model slots (and the singular
scope-review pin), and repairs secret placeholders. Every one of those five is a
migration that PRESERVES an owner customization written under a former key.

`_owner_read_settings_raw` — the reader behind every owner endpoint and behind the
context-fit route resolver — merges the defaults over the RAW document instead, so
it sees none of them. On its own that is a wrong read; combined with the
read-modify-write those endpoints perform it is destructive, because the defaults
the merge invented get written back as if the owner had chosen them, and the
migration that would have rescued the legacy value then finds the new key already
present and leaves it alone. Forever.

The tests below pin BOTH sides as they behave today. The ones named
``..._defect_...`` are the defect, kept only until the seam lands so that the fix
is visible as a diff rather than asserted from scratch.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

# One owner-authored document, written entirely under keys a release renamed or
# retired. Every value differs from both its legacy default and its current one,
# so nothing here can be mistaken for "the shipped value".
LEGACY_OWNER_DOCUMENT = {
    "TOTAL_BUDGET": 77.0,
    "OUROBOROS_MODEL_CODE": "owner/heavy-choice",
    "OUROBOROS_VISION_MODEL": "owner/vision-choice",
    "OUROBOROS_MODEL_FALLBACK": "owner/fallback-choice",
    "USE_LOCAL_CODE": True,
    "OUROBOROS_SCOPE_REVIEW_MODEL": "owner/scope-pin",
    "OUROBOROS_SUBAGENT_WORKTREE_RETENTION_DAYS": 30,
    "OUROBOROS_SUBAGENT_CAPABILITY_DEPTH_LIMIT": 1,
}

# key -> the value the READ path must produce for the document above.
MIGRATED_OWNER_VALUES = {
    "OUROBOROS_MODEL_HEAVY": "owner/heavy-choice",
    "OUROBOROS_MODEL_VISION": "owner/vision-choice",
    "OUROBOROS_MODEL_FALLBACKS": "owner/fallback-choice",
    "USE_LOCAL_HEAVY": True,
    "OUROBOROS_SCOPE_REVIEW_MODELS": "owner/scope-pin",
    "OUROBOROS_GC_RETENTION_DAYS": 30,
}

RETIRED_GHOST = "OUROBOROS_SUBAGENT_CAPABILITY_DEPTH_LIMIT"


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """A real settings file nobody else shares, with the ratchet env neutralised."""
    from ouroboros import config as cfg

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings_path = data_dir / "settings.json"
    monkeypatch.setattr(cfg, "DATA_DIR", data_dir, raising=True)
    monkeypatch.setattr(cfg, "SETTINGS_PATH", settings_path, raising=True)
    for key in cfg.SETTINGS_DEFAULTS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("OUROBOROS_MODEL_FALLBACK", raising=False)
    cfg.reset_runtime_mode_baseline_for_tests()
    yield settings_path
    cfg.reset_runtime_mode_baseline_for_tests()


def _seed(settings_path: pathlib.Path, document: dict) -> None:
    settings_path.write_text(json.dumps(document, indent=2), encoding="utf-8")


def _owner_app(handler_name: str, route: str, drive_root: pathlib.Path) -> Starlette:
    from ouroboros.gateway import settings as settings_mod

    app = Starlette(routes=[
        Route(route, endpoint=getattr(settings_mod, handler_name), methods=["POST"])])
    app.state.drive_root = drive_root
    return app


def test_load_settings_migrates_every_renamed_key_and_drops_the_retired_one(isolated_settings):
    """The golden the seam must keep producing: five raw-stage migrations, in the
    order that makes each of them work (the singular scope pin is promoted BEFORE
    the defaults supply the plural that would otherwise win)."""
    from ouroboros import config as cfg

    _seed(isolated_settings, LEGACY_OWNER_DOCUMENT)
    loaded = cfg.load_settings()

    for key, expected in MIGRATED_OWNER_VALUES.items():
        assert loaded[key] == expected, key
    for legacy in ("OUROBOROS_MODEL_CODE", "OUROBOROS_VISION_MODEL", "OUROBOROS_MODEL_FALLBACK",
                   "USE_LOCAL_CODE", "OUROBOROS_SUBAGENT_WORKTREE_RETENTION_DAYS"):
        assert legacy not in loaded, f"{legacy} survived its rename"
    assert RETIRED_GHOST not in loaded, "a retired key is still served to consumers"
    assert loaded["TOTAL_BUDGET"] == 77.0


def test_load_settings_coerces_declared_types_before_the_defaults_merge(isolated_settings):
    """The coercion half of the same stage: strings off disk reach consumers as the
    type the default declares, and a value that cannot be coerced falls back."""
    from ouroboros import config as cfg

    _seed(isolated_settings, {
        "OUROBOROS_MAX_WORKERS": "12",
        "TOTAL_BUDGET": "40.5",
        "MCP_ENABLED": "yes",
        "OUROBOROS_RUNTIME_MODE": "PRO",
        "OUROBOROS_SKILLS_REPO_PATH": "  ",
        "MCP_SERVERS": '[{"name": "one"}]',
        "OUROBOROS_TOOL_TIMEOUT_SEC": "not a number",
    })
    loaded = cfg.load_settings()

    assert loaded["OUROBOROS_MAX_WORKERS"] == 12
    assert loaded["TOTAL_BUDGET"] == 40.5
    assert loaded["MCP_ENABLED"] is True
    assert loaded["OUROBOROS_RUNTIME_MODE"] == "pro"
    assert loaded["OUROBOROS_SKILLS_REPO_PATH"] == ""
    assert loaded["MCP_SERVERS"] == [{"name": "one"}]
    assert loaded["OUROBOROS_TOOL_TIMEOUT_SEC"] == 600


def test_reading_settings_writes_nothing_to_disk(isolated_settings):
    """A read is a read on both readers: same bytes, same mtime, no lock left behind."""
    from ouroboros import config as cfg
    from ouroboros.gateway.owner_settings import _owner_read_settings_raw

    _seed(isolated_settings, LEGACY_OWNER_DOCUMENT)
    before = isolated_settings.read_bytes()
    before_mtime = isolated_settings.stat().st_mtime_ns

    for _ in range(3):
        cfg.load_settings()
        cfg.load_settings_lock_held(_settings_lock_held=False)
        _owner_read_settings_raw()

    assert isolated_settings.read_bytes() == before
    assert isolated_settings.stat().st_mtime_ns == before_mtime
    assert not pathlib.Path(str(isolated_settings) + ".lock").exists()


def test_owner_read_settings_raw_defect_skips_the_read_normalization(isolated_settings):
    """DEFECT (superseded by the normalization seam): the owner reader merges the
    shipped defaults over the RAW document, so every renamed slot answers the
    default while the legacy key it should have been promoted from is still there,
    and a retired key is served as if it were live."""
    from ouroboros import config as cfg
    from ouroboros.gateway.owner_settings import _owner_read_settings_raw

    _seed(isolated_settings, LEGACY_OWNER_DOCUMENT)
    raw = _owner_read_settings_raw()

    assert raw["OUROBOROS_MODEL_HEAVY"] == cfg.SETTINGS_DEFAULTS["OUROBOROS_MODEL_HEAVY"]
    assert raw["OUROBOROS_MODEL_VISION"] == cfg.SETTINGS_DEFAULTS["OUROBOROS_MODEL_VISION"]
    assert raw["OUROBOROS_MODEL_FALLBACKS"] == cfg.SETTINGS_DEFAULTS["OUROBOROS_MODEL_FALLBACKS"]
    assert raw["OUROBOROS_SCOPE_REVIEW_MODELS"] == (
        cfg.SETTINGS_DEFAULTS["OUROBOROS_SCOPE_REVIEW_MODELS"])
    assert raw["USE_LOCAL_HEAVY"] == cfg.SETTINGS_DEFAULTS["USE_LOCAL_HEAVY"]
    assert raw["OUROBOROS_MODEL_CODE"] == "owner/heavy-choice"
    assert raw[RETIRED_GHOST] == 1


def test_one_owner_endpoint_write_defect_erases_six_owner_customizations(isolated_settings):
    """DEFECT (superseded by the normalization seam): the smallest possible owner
    action — turning auto-grant off, which touches ONE unrelated boolean — writes
    the un-normalized read back and destroys the heavy, vision, fallback, local-heavy,
    scope-pin and retention customizations, permanently: the next load finds the
    renamed keys already present at their defaults and no longer migrates them."""
    from ouroboros import config as cfg

    _seed(isolated_settings, LEGACY_OWNER_DOCUMENT)
    before = cfg.load_settings()
    assert before["OUROBOROS_MODEL_HEAVY"] == "owner/heavy-choice"

    app = _owner_app("api_owner_auto_grant", "/api/owner/auto-grant", isolated_settings.parent)
    response = TestClient(app).post("/api/owner/auto-grant", json={"enabled": False})
    assert response.status_code == 200, response.text

    after = cfg.load_settings()
    assert after["OUROBOROS_AUTO_GRANT_REVIEWED_SKILLS"] == "false", "the intended change"
    erased = {
        key: (before[key], after[key])
        for key in MIGRATED_OWNER_VALUES
        if after[key] != before[key]
    }
    assert set(erased) == set(MIGRATED_OWNER_VALUES), erased
    for key, (_owner_value, now) in erased.items():
        assert now == cfg.SETTINGS_DEFAULTS[key], key
    stored = json.loads(isolated_settings.read_text(encoding="utf-8"))
    assert RETIRED_GHOST in stored, "the retired ghost was re-persisted"


def test_every_owner_endpoint_reaches_the_same_unnormalized_read(isolated_settings):
    """The defect is one seam, not five bugs: each single-decision owner endpoint,
    and the generic save, take their document from ``_owner_read_settings_raw``."""
    import ast

    from ouroboros.gateway import settings as settings_mod

    source = pathlib.Path(settings_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    readers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "_owner_read_settings_raw"
            for inner in ast.walk(node)
        )
    }
    assert readers == {
        "api_owner_runtime_mode",
        "api_owner_auto_grant",
        "api_owner_context_mode",
        "api_owner_scope_review_floor",
        "api_owner_safety_mode",
        "api_settings_post",
    }, readers


def test_the_three_settings_writers_are_exactly_these_three():
    """No fourth writer: the persisting surfaces are the config saver, the owner
    endpoint seam, and the packaged CLI bootstrap saver."""
    import ast

    repo = pathlib.Path(__file__).resolve().parents[1]
    writers: set[str] = set()
    for relpath in ("ouroboros/config.py", "ouroboros/gateway/owner_settings.py",
                    "ouroboros/packaged_cli.py", "ouroboros/context_mode_compat.py",
                    "ouroboros/colab_bootstrap.py"):
        source = (repo / relpath).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                text = ast.get_source_segment(source, call) or ""
                targets_settings = "settings" in text.lower() or "SETTINGS_PATH" in text
                writes = any(
                    marker in text
                    for marker in ("atomic_write_json(", "os.replace(", ".write_text(")
                )
                if writes and targets_settings:
                    writers.add(f"{relpath}::{node.name}")
    assert writers == {
        "ouroboros/config.py::save_settings",
        "ouroboros/gateway/owner_settings.py::_owner_write_settings",
        "ouroboros/packaged_cli.py::_save_settings",
        # Not settings documents: the one-window raw context pair migration, written
        # under the load lock, and the Colab bootstrap's own generated file.
        "ouroboros/context_mode_compat.py::normalize_and_persist_context_mode_compat",
        "ouroboros/colab_bootstrap.py::write_colab_settings",
    }, writers
