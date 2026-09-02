"""``GET /api/widgets`` and the module endpoint read the live loader only.

Both are hot Widgets-page paths (DEVELOPMENT.md "Passive GET"): they must not
re-discover skills, reconcile review jobs, sync schedules, or hash payloads.
"""
from __future__ import annotations

import importlib

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
        assert tab["revision"] and tab["revision"] == extension_loader.live_bundle_facts("ext_widget")[0]

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
        # Exact-entry authorization stays; an unloaded skill is "not live".
        assert client.get("/api/extensions/ext_module/module/other.js").status_code == 404
        assert client.get("/api/extensions/ext_module/module/plugin.py").status_code == 404
        assert client.get("/api/extensions/nope/module/widget.js").status_code == 409
        extension_loader.unload_extension("ext_module")
        assert client.get("/api/extensions/ext_module/module/widget.js").status_code == 409
    assert all(count == 0 for count in calls.values()), calls


def test_live_bundle_facts_reports_loaded_bundle_only(tmp_path):
    assert extension_loader.live_bundle_facts("absent") is None
    loaded, _, drive_root = _prepare_extension(
        tmp_path,
        "ext_facts",
        "def register(api):\n    pass\n",
        permissions=[],
    )
    assert extension_loader.load_extension(loaded, lambda: {}, drive_root=drive_root) is None
    content_hash, skill_dir = extension_loader.live_bundle_facts("ext_facts")
    assert content_hash == loaded.content_hash
    assert skill_dir == str(loaded.skill_dir.resolve())
    extension_loader.unload_extension("ext_facts")
    assert extension_loader.live_bundle_facts("ext_facts") is None
