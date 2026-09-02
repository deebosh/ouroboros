"""``GET /api/widgets`` and the module endpoint read the live loader only.

Both are hot Widgets-page paths (DEVELOPMENT.md "Passive GET"): they must not
re-discover skills, reconcile review jobs, sync schedules, or hash payloads.
"""
from __future__ import annotations

import importlib
import json
import pathlib
import subprocess
import sys

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from ouroboros import extension_loader
from ouroboros.gateway.router import collect_routes
from ouroboros.gateway.widgets import WidgetTab
from tests._shared import clean_extension_runtime_state
from tests.test_extension_loader import _prepare_extension


@pytest.fixture(autouse=True)
def _clean_loader(monkeypatch):
    monkeypatch.setenv("OUROBOROS_RUNTIME_MODE", "advanced")
    clean_extension_runtime_state()
    yield
    clean_extension_runtime_state()


# Every seam a discovery/reconcile/sync/hash could enter the read path through.
_PASSIVE_SEAMS = (
    ("ouroboros.skill_loader", "discover_skills"),
    ("ouroboros.skill_loader", "find_skill"),
    ("ouroboros.skill_loader", "compute_content_hash"),
    ("ouroboros.extension_loader", "discover_skills"),
    ("ouroboros.extension_loader", "find_skill"),
    ("ouroboros.extension_loader", "compute_content_hash"),
    ("ouroboros.gateway.extensions", "discover_skills"),
    ("ouroboros.gateway.extensions", "find_skill"),
    ("ouroboros.skill_review_runner", "reconcile_stale_review_jobs"),
    ("supervisor.queue", "sync_skill_schedules"),
)


def _arm_counters(monkeypatch) -> dict[str, int]:
    """Wrap every seam in a counting delegate (the real call still runs).

    Arm AFTER the app is built: a module first-imported while a sibling seam
    is wrapped captures the wrapper as its own original, and monkeypatch then
    faithfully "restores" that capture. Delegating wrappers keep even such a
    capture behaviour-preserving; building the app first avoids it entirely.
    """
    modules = {name: importlib.import_module(name) for name, _attr in _PASSIVE_SEAMS}
    calls: dict[str, int] = {}
    for module_name, attr in _PASSIVE_SEAMS:
        label = f"{module_name}.{attr}"
        calls[label] = 0
        original = getattr(modules[module_name], attr)

        def _counted(*args, _label=label, _original=original, **kwargs):
            calls[_label] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(modules[module_name], attr, _counted)
    return calls


def _client(tmp_path) -> TestClient:
    return TestClient(Starlette(routes=collect_routes(data_dir=tmp_path)))


def test_api_widgets_projects_live_tabs_without_discovery(tmp_path, monkeypatch):
    loaded, _, drive_root = _prepare_extension(
        tmp_path,
        "ext_widget",
        "def register(api):\n"
        "    api.register_ui_tab('weather', 'Weather', icon='cloud', render={'kind': 'declarative', "
        "'schema_version': 1, 'components': [{'type': 'markdown', 'text': 'ok'}]})\n",
        permissions=["widget"],
    )
    err = extension_loader.load_extension(loaded, lambda: {}, drive_root=drive_root)
    assert err is None, err
    live_tab = extension_loader.snapshot()["ui_tabs"][0]
    client = _client(tmp_path)  # imports every gateway module before the seams are wrapped
    calls = _arm_counters(monkeypatch)

    with client:
        response = client.get("/api/widgets")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == {"ui_tabs"}
        assert len(payload["ui_tabs"]) == 1
        tab = payload["ui_tabs"][0]
        # Exact contract shape: the TypedDict keys, nothing else (the dead
        # two-phase flags are gone; framed geometry is covered below).
        assert set(tab) == set(WidgetTab.__annotations__)
        assert tab == {
            "key": "ext_widget:weather",
            "skill": "ext_widget",
            "tab_id": "weather",
            "title": "Weather",
            "icon": "cloud",
            "ws_prefix": extension_loader.extension_name_prefix("ext_widget"),
            "render": live_tab["render"],
            "span": 1,
            "grid_span": 1,
            "revision": loaded.content_hash,
        }
        assert response.headers["cache-control"] == "no-store"
        assert tab["revision"] and tab["revision"] == extension_loader.live_widget_projection()[0]["revision"]

        extension_loader.unload_extension("ext_widget")
        assert client.get("/api/widgets").json() == {"ui_tabs": []}
    assert all(count == 0 for count in calls.values()), calls


def test_api_extension_module_serves_live_entry_without_discovery(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "ext_module"
    skill_dir.mkdir(parents=True)
    # Written BEFORE the payload hash is taken so the reviewed hash covers it.
    (skill_dir / "widget.js").write_text("window.__ok = true;\n", encoding="utf-8")
    loaded, _, drive_root = _prepare_extension(
        tmp_path,
        "ext_module",
        "def register(api):\n"
        "    api.register_ui_tab('module', 'Module', render={'kind': 'module', 'entry': 'widget.js', 'height': 480})\n",
        permissions=["widget"],
    )
    err = extension_loader.load_extension(loaded, lambda: {}, drive_root=drive_root)
    assert err is None, err
    client = _client(tmp_path)  # imports every gateway module before the seams are wrapped
    calls = _arm_counters(monkeypatch)

    with client:
        # Framed geometry rides inside ``render`` (where the page reads it), never
        # as a promoted top-level card key.
        card = client.get("/api/widgets").json()["ui_tabs"][0]
        assert card["render"]["height"] == 480 and "height" not in card
        ok = client.get("/api/extensions/ext_module/module/widget.js")
        assert ok.status_code == 200, ok.text
        assert "window.__ok" in ok.text
        assert ok.headers["content-type"].startswith("application/javascript")
        assert ok.headers["cache-control"] == "no-store"
        assert ok.headers["access-control-allow-origin"] == "*"
        # Reviewed bytes = served bytes by construction: the source was captured
        # when the bundle loaded, so an edit on disk afterwards is NOT served
        # until the skill reloads (which review freshness requires anyway).
        (skill_dir / "widget.js").write_text("window.__edited_after_load = true;\n", encoding="utf-8")
        again = client.get("/api/extensions/ext_module/module/widget.js")
        assert again.status_code == 200 and again.text == "window.__ok = true;\n"
        # The source lives on the loader bundle, never in the browser-facing snapshot.
        assert "window.__ok" not in json.dumps(extension_loader.snapshot())
        # Exact-entry authorization stays; an unloaded skill is "not live".
        assert client.get("/api/extensions/ext_module/module/other.js").status_code == 404
        assert client.get("/api/extensions/ext_module/module/plugin.py").status_code == 404
        assert client.get("/api/extensions/nope/module/widget.js").status_code == 409
        extension_loader.unload_extension("ext_module")
        assert client.get("/api/extensions/ext_module/module/widget.js").status_code == 409
    assert all(count == 0 for count in calls.values()), calls


def test_live_widget_projection_joins_tabs_with_owner_revision_and_source(tmp_path):
    """One accessor under one lock: tab, owner revision and module source per row."""
    assert extension_loader.live_widget_projection("absent") is None
    assert extension_loader.live_widget_projection() == []
    skill_dir = tmp_path / "skills" / "ext_proj"
    skill_dir.mkdir(parents=True)
    (skill_dir / "widget.js").write_text("export const x = 1;\n", encoding="utf-8")
    loaded, _, drive_root = _prepare_extension(
        tmp_path,
        "ext_proj",
        "def register(api):\n"
        "    api.register_ui_tab('module', 'Module', render={'kind': 'module', 'entry': 'widget.js'})\n"
        "    api.register_ui_tab('plain', 'Plain', render={'kind': 'declarative', 'schema_version': 1, "
        "'components': [{'type': 'markdown', 'text': 'ok'}]})\n",
        permissions=["widget"],
    )
    assert extension_loader.load_extension(loaded, lambda: {}, drive_root=drive_root) is None
    rows = extension_loader.live_widget_projection("ext_proj")
    assert [row["tab"]["key"] for row in rows] == ["ext_proj:module", "ext_proj:plain"]
    assert {row["revision"] for row in rows} == {loaded.content_hash}
    assert rows[0]["module_source"] == "export const x = 1;\n"
    assert rows[1]["module_source"] is None
    assert extension_loader.live_widget_projection() == rows
    # A live bundle declaring no tabs is [] (the module endpoint's 404), not None (its 409).
    other, _, _ = _prepare_extension(tmp_path, "ext_notabs", "def register(api):\n    pass\n", permissions=[])
    assert extension_loader.load_extension(other, lambda: {}, drive_root=drive_root) is None
    assert extension_loader.live_widget_projection("ext_notabs") == []
    extension_loader.unload_extension("ext_proj")
    assert extension_loader.live_widget_projection("ext_proj") is None


@pytest.mark.parametrize(
    "payload,expected",
    [(None, "missing from the skill directory"), (b"\xff\xfe\x00bad", "not UTF-8")],
    ids=["missing-entry", "non-utf8-entry"],
)
def test_module_widget_without_readable_source_is_not_live(tmp_path, payload, expected):
    """The entry is read ONCE at load; without it the tab (and the skill) is not live."""
    skill_dir = tmp_path / "skills" / "ext_broken"
    skill_dir.mkdir(parents=True)
    if payload is not None:
        (skill_dir / "widget.js").write_bytes(payload)
    loaded, _, drive_root = _prepare_extension(
        tmp_path,
        "ext_broken",
        "def register(api):\n"
        "    api.register_ui_tab('module', 'Module', render={'kind': 'module', 'entry': 'widget.js'})\n",
        permissions=["widget"],
    )
    err = extension_loader.load_extension(loaded, lambda: {}, drive_root=drive_root)
    assert err is not None and "widget.js" in err and expected in err, err
    assert extension_loader.snapshot()["ui_tabs"] == []
    assert extension_loader.live_widget_projection("ext_broken") is None


def test_out_of_process_catalog_captures_module_source_at_load(tmp_path):
    """The host-side catalog path stores the same reviewed source as register_ui_tab."""
    from types import SimpleNamespace

    from ouroboros.contracts.plugin_api import ExtensionRegistrationError

    skill_dir = tmp_path / "skills" / "oop"
    skill_dir.mkdir(parents=True)
    (skill_dir / "widget.js").write_text("export const oop = 1;\n", encoding="utf-8")
    skill = SimpleNamespace(name="oop", skill_dir=skill_dir)
    catalog = {"ui_tabs": [{"key": "oop:m", "skill": "oop", "tab_id": "m", "title": "M",
                            "render": {"kind": "module", "entry": "widget.js"}}]}
    extension_loader._register_out_of_process_surfaces(skill, current_hash="h1", catalog=catalog)
    rows = extension_loader.live_widget_projection("oop")
    assert [row["tab"]["key"] for row in rows] == ["oop:m"]
    assert rows[0]["revision"] == "h1" and rows[0]["module_source"] == "export const oop = 1;\n"
    extension_loader.unload_extension("oop")
    # A catalog declaring an entry the payload lacks is not installed at all.
    (skill_dir / "widget.js").unlink()
    with pytest.raises(ExtensionRegistrationError, match="'widget.js' is missing"):
        extension_loader._register_out_of_process_surfaces(skill, current_hash="h2", catalog=catalog)
    assert extension_loader.live_widget_projection("oop") is None


def test_contracts_import_stays_transport_free():
    """``gateway/contracts.py`` re-exports the Widgets TypedDicts homed in
    ``gateway/widgets.py``; importing the contracts must not load Starlette."""
    code = "import sys, ouroboros.contracts.api_v1; sys.exit(1 if 'starlette' in sys.modules else 0)"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=pathlib.Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr or "starlette was imported by ouroboros.contracts.api_v1"
