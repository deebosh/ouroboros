"""Skill-widget surface regressions: preflight entry truth, reconcile receipts, catalogue liveness.

Single home for this stream's Python pins so no existing oversized test module
grows. No network, no real LLM calls; every case runs against an isolated
tmp_path drive root.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from ouroboros.tools.registry import ToolContext


def _make_ctx(tmp_path: pathlib.Path) -> ToolContext:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    return ToolContext(repo_dir=repo_dir, drive_root=drive_root)


def _extension_manifest(name: str = "alpha", *, ui_tab: str = "") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        "description: widget surface test\n"
        "version: 0.1.0\n"
        "type: extension\n"
        "entry: plugin.py\n"
        "permissions: [widget]\n"
        f"{ui_tab}"
        "---\n"
        "body\n"
    )


def _module_ui_tab(entry: str = "widget.js") -> str:
    return (
        "ui_tab:\n"
        "  id: main\n"
        "  title: Main\n"
        "  render:\n"
        "    kind: module\n"
        f"    entry: {entry}\n"
        "    height: 480\n"
    )


def _make_skill(tmp_path: pathlib.Path, monkeypatch, manifest: str, plugin: str) -> pathlib.Path:
    skills_root = tmp_path / "skills"
    skills_root.mkdir(exist_ok=True)
    monkeypatch.setenv("OUROBOROS_SKILLS_REPO_PATH", str(skills_root))
    skill_dir = skills_root / "alpha"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(manifest, encoding="utf-8")
    (skill_dir / "plugin.py").write_text(plugin, encoding="utf-8")
    return skill_dir


_TRIVIAL_PLUGIN = "def register(api):\n    pass\n"


# --------------------------------------------------------------------------- F12


def test_skill_preflight_flags_missing_module_widget_entry(tmp_path, monkeypatch):
    """A declared module entry that is not on disk must not read as verified ok."""
    ctx = _make_ctx(tmp_path)
    skill_dir = _make_skill(
        tmp_path,
        monkeypatch,
        _extension_manifest(ui_tab=_module_ui_tab()),
        _TRIVIAL_PLUGIN,
    )

    from ouroboros.tools import skill_preflight as sp

    result = json.loads(sp._handle_skill_preflight(ctx, skill="alpha"))
    entry_rows = [row for row in result["widgets"] if row["item"] == "widget_entry_exists"]
    assert entry_rows, result["widgets"]
    assert result["ok"] is False
    assert entry_rows[0]["ok"] is False
    assert "widget.js" in entry_rows[0]["detail"]
    assert entry_rows[0]["source"] == "manifest.ui_tab.render"

    # Same declaration, file now present: the row flips and names the entry.
    (skill_dir / "widget.js").write_text("const a = 1;\n", encoding="utf-8")
    result = json.loads(sp._handle_skill_preflight(ctx, skill="alpha"))
    entry_rows = [row for row in result["widgets"] if row["item"] == "widget_entry_exists"]
    assert entry_rows[0]["ok"] is True
    assert entry_rows[0]["detail"] == "widget.js"


def test_skill_preflight_checks_plugin_registered_module_entry(tmp_path, monkeypatch):
    """The plugin.py register_ui_tab path is covered without touching the AST walker."""
    ctx = _make_ctx(tmp_path)
    _make_skill(
        tmp_path,
        monkeypatch,
        _extension_manifest(),
        "_UI_RENDER = {'kind': 'module', 'entry': 'missing.js', 'height': 400}\n"
        "def register(api):\n"
        "    api.register_ui_tab('main', 'Main', render=_UI_RENDER)\n",
    )

    from ouroboros.tools import skill_preflight as sp

    result = json.loads(sp._handle_skill_preflight(ctx, skill="alpha"))
    entry_rows = [row for row in result["widgets"] if row["item"] == "widget_entry_exists"]
    assert entry_rows, result["widgets"]
    assert result["ok"] is False
    assert entry_rows[0]["ok"] is False
    assert "missing.js" in entry_rows[0]["detail"]
    assert entry_rows[0]["source"].startswith("plugin.py:")


# --------------------------------------------------------------------------- F13


@pytest.mark.parametrize(
    ("source", "expect_block"),
    [("export const a = 1;\n", True), ("const a = 1;\n", False)],
)
def test_skill_preflight_parses_module_entry_as_classic_script(
    tmp_path, monkeypatch, source, expect_block
):
    """The declared entry is checked in the grammar the frame actually runs it in."""
    ctx = _make_ctx(tmp_path)
    skill_dir = _make_skill(
        tmp_path,
        monkeypatch,
        _extension_manifest(ui_tab=_module_ui_tab()),
        _TRIVIAL_PLUGIN,
    )
    (skill_dir / "widget.js").write_text(source, encoding="utf-8")

    from ouroboros.tools import skill_preflight as sp

    result = json.loads(sp._handle_skill_preflight(ctx, skill="alpha"))
    rows = [row for row in result["files"] if row["path"] == "widget.js"]
    assert rows, result["files"]
    row = rows[0]
    assert row["grammar"] == "classic_script"
    if row.get("skipped"):
        # No usable node runtime: the honest skip branch, never a verdict.
        assert row["skip_reason"] in {"runtime_unavailable", "validator_killed", "validator_timeout"}
        assert result["degraded"] is True
        return
    if expect_block:
        assert row["ok"] is False
        assert result["ok"] is False
    else:
        assert row["ok"] is True
