"""Widgets board browser smoke: ordered rows with the owner's width steps.

On chromium and webkit, against a real server with real module frames and real
declarative cards of very different heights (a 720px game-like frame, a short
metric, a long route-backed table, an auto-height module): the cards stand in
rows of the 12-column board in the owner's order at the author's default
widths; content growth makes only its own card taller and moves no card
sideways; the card menu, the right-edge drag and its arrow keys set width steps
stored in ``ui_preferences.widget_size`` (Reset size deletes one) that a window
reload restores; a narrow window stacks the cards in one column in the same
order, hides the edge handle, and its menu says widths apply when the list is
wide. Through all of it every card keeps its DOM node and every frame its
window (a moved or re-inserted <iframe> would reload). Screenshots of the
board, the open menu and the stacked column go to the evidence directory. Kept
apart from the lifecycle / geometry suites so none of them grows past the
size-ratchet band (docs/DESIGN.md "Widgets board")."""

from __future__ import annotations

import os
import pathlib
import textwrap

import pytest

from tests.test_ui_smoke_playwright import direct_server_with_data as _direct_server_with_data

direct_server_with_data = _direct_server_with_data

GAP_PX = 14


def _write_board_widget_extension(data_dir: pathlib.Path) -> str:
    """Install five cards: a 720px game-like module frame (span 2), a short
    metric, a long route-backed table, an auto-height module with a Grow
    button and a short note."""
    from ouroboros.skill_loader import SkillReviewState, compute_content_hash, save_review_state

    name = "board_widget_smoke"
    skill_dir = data_dir / "skills" / "external" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            f"""\
            ---
            name: {name}
            description: Isolated widgets board fixture.
            version: 0.1.0
            type: extension
            entry: plugin.py
            permissions: ["route", "widget"]
            ---
            # Widgets board fixture
            """
        ),
        encoding="utf-8",
    )
    (skill_dir / "plugin.py").write_text(
        textwrap.dedent(
            """\
            _ROWS = [
                {"issue": f"#{1300 + i}", "title": f"Board fixture row {i}", "state": "open" if i % 3 else "closed"}
                for i in range(30)
            ]


            async def rows(_request):
                return {"rows": _ROWS}


            def register(api):
                api.register_route("rows", handler=rows, methods=("GET",))
                api.register_ui_tab("game", "Game", render={
                    "kind": "module", "entry": "game.js", "height": 720, "start": "auto", "span": 2,
                })
                api.register_ui_tab("gauge", "Gauge", render={
                    "kind": "declarative", "schema_version": 1,
                    "components": [{"type": "metric", "label": "Used", "value": "62", "unit": "%"}],
                })
                api.register_ui_tab("grow", "Grow", render={"kind": "module", "entry": "grow.js", "start": "auto"})
                api.register_ui_tab("issues", "Issues", render={
                    "kind": "declarative", "schema_version": 1, "components": [
                        {"type": "poll", "label": "Load", "route": "rows", "auto_start": True, "max_ticks": 1},
                        {"type": "table", "path": "rows", "columns": [
                            {"label": "Issue", "path": "issue"}, {"label": "Title", "path": "title"},
                            {"label": "State", "path": "state"},
                        ]},
                    ],
                })
                api.register_ui_tab("notes", "Notes", render={
                    "kind": "declarative", "schema_version": 1,
                    "components": [{"type": "callout", "text": "A short note."}],
                })
            """
        ),
        encoding="utf-8",
    )
    (skill_dir / "game.js").write_text(
        "(() => { const root = document.getElementById('root'); root.style.height = '700px';"
        " root.style.background = 'linear-gradient(135deg, #1b5e3a, #0d2a3a)'; root.textContent = 'Game'; })();\n",
        encoding="utf-8",
    )
    (skill_dir / "grow.js").write_text(
        textwrap.dedent(
            """\
            (() => {
                const root = document.getElementById('root');
                root.innerHTML = '<button id="grow" type="button">Grow</button><div id="rows"></div>';
                document.getElementById('grow').addEventListener('click', () => {
                    const rows = document.getElementById('rows');
                    for (let i = 0; i < 20; i += 1) {
                        const row = document.createElement('div');
                        row.style.height = '40px';
                        row.textContent = `Row ${i + 1}`;
                        rows.appendChild(row);
                    }
                });
            })();
            """
        ),
        encoding="utf-8",
    )
    content_hash = compute_content_hash(skill_dir, manifest_entry="plugin.py")
    save_review_state(data_dir, name, SkillReviewState(status="pass", content_hash=content_hash))
    return name


@pytest.mark.ui_browser
@pytest.mark.parametrize("browser_name", ("chromium", "webkit"))
def test_ui_smoke_widget_board_rows_and_owner_widths(direct_server_with_data, browser_name):
    pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    url = direct_server_with_data["url"]
    data_dir = direct_server_with_data["data_dir"]
    skill = _write_board_widget_extension(data_dir)
    key = {tab: f"{skill}:{tab}" for tab in ("game", "gauge", "grow", "issues", "notes")}
    framed = [key["game"], key["grow"]]
    evidence_dir = pathlib.Path(os.environ.get("OUROBOROS_UI_EVIDENCE_DIR", str(data_dir.parent)))
    evidence_dir.mkdir(parents=True, exist_ok=True)

    def card(tab: str) -> str:
        return f'[data-widget-key="{key[tab]}"]'

    def rects(page) -> dict:
        # Relative to the list, so a scroll (a click into a frame scrolls it into
        # view) never reads as a card moving.
        return page.evaluate(
            """(keys) => {
                const list = document.getElementById('widgets-list').getBoundingClientRect();
                return Object.fromEntries(Object.entries(keys).map(([tab, key]) => {
                    const box = document.querySelector(`[data-widget-key="${key}"]`).getBoundingClientRect();
                    return [tab, {x: box.x - list.x, y: box.y - list.y, width: box.width, height: box.height}];
                }));
            }""",
            key,
        )

    def widths(page) -> dict:
        return page.evaluate(
            """(keys) => Object.fromEntries(Object.entries(keys).map(([tab, key]) => [
                tab, document.querySelector(`[data-widget-key="${key}"]`).style.getPropertyValue('--widget-w'),
            ]))""",
            key,
        )

    def list_width(page) -> float:
        return page.evaluate("document.getElementById('widgets-list').getBoundingClientRect().width")

    def columns(page, count: int) -> float:
        pitch = (list_width(page) + GAP_PX) / 12
        return count * pitch - GAP_PX

    def saved_sizes(page) -> dict:
        return page.evaluate("async () => (await (await fetch('/api/ui/preferences')).json()).widget_size || {}")

    def wait_saved(page, tab: str, size) -> None:
        page.wait_for_function(
            """async ([key, size]) => {
                const saved = ((await (await fetch('/api/ui/preferences')).json()).widget_size || {})[key];
                return size === null ? saved === undefined : (!!saved && saved.w === size.w && saved.h === 0);
            }""",
            arg=[key[tab], size],
            timeout=5_000,
        )

    def mark_frames(page) -> None:
        for frame_key in framed:
            frame = page.locator(f'[data-widget-key="{frame_key}"] iframe')
            frame.evaluate("frame => { frame.__boardMark = true; }")
            frame.element_handle().content_frame().evaluate("() => { window.__boardMark = 'same-window'; }")

    def frames_kept(page) -> bool:
        for frame_key in framed:
            frame = page.locator(f'[data-widget-key="{frame_key}"] iframe')
            if frame.count() != 1 or not frame.evaluate("frame => frame.__boardMark === true"):
                return False
            if frame.element_handle().content_frame().evaluate("() => window.__boardMark ?? null") != "same-window":
                return False
        return True

    def node_order(page) -> list:
        return page.evaluate("() => [...document.querySelectorAll('#widgets-list [data-widget-key]')].map((node) => node.dataset.widgetKey)")

    def open_widgets(page) -> None:
        page.click('[data-nav-page="widgets"]')
        for frame_key in framed:
            page.locator(f'[data-widget-key="{frame_key}"] iframe').wait_for(state="attached", timeout=30_000)
        page.locator(f"{card('issues')} table tbody tr").nth(29).wait_for(state="attached", timeout=15_000)

    def choose(page, tab: str, item: str) -> None:
        page.locator(f"{card(tab)} [data-widget-menu-trigger]").click()
        page.locator(f'body > .skills-card-menu-dialog[open] [data-widget-size="{item}"]').click()

    def same(a: float, b: float) -> bool:
        return abs(a - b) <= 1.5

    try:
        with sync_playwright() as pw:
            browser = getattr(pw, browser_name).launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                toggled = page.evaluate(
                    """async (skill) => (await fetch(`/api/skills/${encodeURIComponent(skill)}/toggle`, {
                        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: true}),
                    })).status""",
                    skill,
                )
                assert toggled == 200
                open_widgets(page)
                assert page.evaluate("document.getElementById('widgets-list').dataset.widgetLayout") == "grid"
                assert widths(page) == {"game": "8", "gauge": "4", "grow": "4", "issues": "4", "notes": "4"}
                mark_frames(page)
                dom_before = node_order(page)

                # Rows in the owner's order (no order saved yet: the list's order): the
                # 8-column game and the gauge share row one, the other three row two.
                box = rects(page)
                assert same(box["game"]["width"], columns(page, 8)) and same(box["gauge"]["width"], columns(page, 4))
                assert same(box["game"]["y"], box["gauge"]["y"]) and box["gauge"]["x"] > box["game"]["x"]
                row_two = [box[tab] for tab in ("grow", "issues", "notes")]
                assert all(same(item["y"], row_two[0]["y"]) for item in row_two)
                assert [item["x"] for item in row_two] == sorted(item["x"] for item in row_two)
                assert same(row_two[0]["y"], max(box["game"]["y"] + box["game"]["height"], box["gauge"]["y"] + box["gauge"]["height"]) + GAP_PX)
                # Each card keeps its own content height: the long table is much
                # taller than the note beside it, the gauge much shorter than the game.
                assert box["issues"]["height"] > box["notes"]["height"] + 400
                assert box["gauge"]["height"] < box["game"]["height"] - 400
                page.screenshot(path=str(evidence_dir / f"widget-board-{browser_name}.png"), full_page=True)

                # Content growth: only the grown card gets taller; nothing moves sideways
                # or changes width, and its row neighbours keep their tops.
                frame_before = page.locator(f"{card('grow')} iframe").evaluate("frame => frame.getBoundingClientRect().height")
                page.frame_locator(f"{card('grow')} iframe").locator("#grow").click()
                page.wait_for_function(
                    "([selector, before]) => document.querySelector(`${selector} iframe`).getBoundingClientRect().height > before + 300",
                    arg=[card("grow"), frame_before],
                    timeout=10_000,
                )
                grown = rects(page)
                for tab, item in grown.items():
                    assert same(item["x"], box[tab]["x"]) and same(item["width"], box[tab]["width"]), tab
                    assert same(item["y"], box[tab]["y"]), tab
                assert grown["grow"]["height"] > box["grow"]["height"] + 300
                assert frames_kept(page)

                # The card menu: the gauge goes full width, on its own row under the game.
                page.locator(f"{card('gauge')} [data-widget-menu-trigger]").click()
                menu = page.locator("body > .skills-card-menu-dialog[open]")
                menu.wait_for()
                assert menu.locator('[data-widget-size="4"]').get_attribute("aria-checked") == "true"
                assert menu.locator("[data-widget-size-note]").is_hidden()
                page.screenshot(path=str(evidence_dir / f"widget-board-menu-{browser_name}.png"))
                menu.locator('[data-widget-size="12"]').click()
                wait_saved(page, "gauge", {"w": 12})
                after = rects(page)
                assert widths(page)["gauge"] == "12" and same(after["gauge"]["width"], list_width(page))
                assert after["gauge"]["y"] > after["game"]["y"] + after["game"]["height"]

                # The right edge drags between steps: two columns wider lands on Half.
                page.evaluate("(selector) => document.querySelector(selector).scrollIntoView({block: 'start'})", card("issues"))
                handle = page.locator(f"{card('issues')} [data-widget-resize-handle]").bounding_box()
                x, y = handle["x"] + handle["width"] / 2, handle["y"] + 60
                pitch = (list_width(page) + GAP_PX) / 12
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + 2 * pitch, y, steps=8)
                page.mouse.up()
                wait_saved(page, "issues", {"w": 6})
                assert same(rects(page)["issues"]["width"], columns(page, 6))

                # Its arrow keys step the width and the live region names the step.
                page.locator(f"{card('notes')} [data-widget-resize-handle]").focus()
                page.keyboard.press("ArrowRight")
                wait_saved(page, "notes", {"w": 6})
                page.keyboard.press("End")
                wait_saved(page, "notes", {"w": 12})
                page.keyboard.press("Home")
                wait_saved(page, "notes", {"w": 4})
                assert page.locator("[data-widget-arrange-status]").text_content() == "Width: one third"

                # Reset size returns the gauge to its author default.
                choose(page, "gauge", "reset")
                wait_saved(page, "gauge", None)
                assert widths(page)["gauge"] == "4"
                assert frames_kept(page)
                assert node_order(page) == dom_before, "arranging never moves a card node"
                arranged = widths(page)
                pixels = rects(page)

                # A window reload reads the same widths back from the server.
                page.reload(wait_until="domcontentloaded", timeout=30_000)
                open_widgets(page)
                assert widths(page) == arranged == {"game": "8", "gauge": "4", "grow": "4", "issues": "6", "notes": "4"}
                assert saved_sizes(page) == {key["issues"]: {"w": 6, "h": 0}, key["notes"]: {"w": 4, "h": 0}}
                for tab, item in rects(page).items():
                    assert same(item["width"], pixels[tab]["width"]), tab

                # Narrow: one stacked column in the same order, no edge handle, the menu
                # says widths apply when wide; a width chosen there is stored, not shown.
                mark_frames(page)
                page.set_viewport_size({"width": 600, "height": 900})
                page.wait_for_function("document.getElementById('widgets-list').dataset.widgetLayout === 'stack'", timeout=5_000)
                stacked = rects(page)
                tops = [stacked[tab]["y"] for tab in ("game", "gauge", "grow", "issues", "notes")]
                assert tops == sorted(tops), tops
                assert all(same(item["width"], list_width(page)) for item in stacked.values())
                assert page.locator(f"{card('notes')} [data-widget-resize-handle]").is_hidden()
                page.locator(f"{card('gauge')} [data-widget-menu-trigger]").click()
                page.locator("body > .skills-card-menu-dialog[open] [data-widget-size-note]").wait_for(state="visible")
                page.screenshot(path=str(evidence_dir / f"widget-board-stack-menu-{browser_name}.png"))
                page.locator('body > .skills-card-menu-dialog[open] [data-widget-size="6"]').click()
                wait_saved(page, "gauge", {"w": 6})
                assert same(rects(page)["gauge"]["width"], list_width(page))
                assert frames_kept(page)

                page.set_viewport_size({"width": 1280, "height": 800})
                page.wait_for_function("document.getElementById('widgets-list').dataset.widgetLayout === 'grid'", timeout=5_000)
                assert widths(page)["gauge"] == "6"
                assert same(rects(page)["gauge"]["width"], columns(page, 6))
                assert frames_kept(page)
            finally:
                browser.close()
    except PlaywrightError as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc).lower():
            pytest.skip(str(exc))
        raise
