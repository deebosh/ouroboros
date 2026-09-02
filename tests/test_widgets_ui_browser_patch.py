"""Widgets keyed-patch and reconnect browser smoke (cycle-A fix round), on
chromium and webkit. A CHANGED card whose module frame is running (its skill's
``revision`` moved) is stopped in order first: the old card stands, marked and
"Stopping…", while its async dispose hook flushes through the bridge, and only
then is it removed and the fresh card mounted — never two frames for one key,
never a frame torn down in the same turn as its dispose message. The same
reconcile runs on every WebSocket (re)connect (``ws.on('open')``) and removes /
re-adds cards accordingly while untouched cards keep their nodes and frames.
Two quick launch-policy changes are written one after the other (never two
read-modify-writes of the stored map in flight), and closing a policy menu with
Escape hands focus back to its trigger on both engines. Kept apart from
``test_widgets_ui_browser_lifecycle.py`` so that file stays under the
size-ratchet band; the fixtures are shared by import."""

from __future__ import annotations

import json
import time

import pytest

from tests.test_ui_smoke_playwright import direct_server_with_data as _direct_server_with_data
from tests.test_widgets_ui_browser_lifecycle import _click_nav, _write_lifecycle_widget_extension

direct_server_with_data = _direct_server_with_data

# Host-side probe: every host fetch to an extension route is logged with the
# number of widget frames attached and whether the frame the test marked as
# `window.__oldFrame` is still connected at that moment. A dispose hook's
# bridged flush goes through the parent's `fetch`, so this observes from the
# host's side whether the old frame was still alive when its hook ran.
_PATCH_PROBE_SCRIPT = r"""
(() => {
    const log = [];
    window.__hostFetchLog = log;
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => {
        const url = typeof input === 'string' ? input : String(input && input.url || input);
        if (url.includes('/api/extensions/')) {
            log.push({
                url,
                frames: document.querySelectorAll('#widgets-list iframe').length,
                oldFrameConnected: window.__oldFrame ? window.__oldFrame.isConnected : null,
            });
        }
        return original(input, init);
    };
})();
"""


@pytest.mark.ui_browser
@pytest.mark.parametrize("browser_name", ("chromium", "webkit"))
def test_ui_smoke_widget_changed_card_and_reconnect_reconcile(direct_server_with_data, browser_name):
    """(1) A revision bump of a RUNNING auto card, reconciled through the ws
    `open` trigger: the old card is marked `data-widget-removed`, both cards
    stand side by side while the hook's flush POST and its answer reach the
    host with the old frame still connected, then the old card goes and exactly
    one fresh frame remains; the hang and manual cards (unchanged entries) keep
    their nodes and frames. (2) The `open` reconcile removes a vanished card and
    re-adds it when it is back, touching no running frame. (3) Two quick policy
    changes hold at most one stored-map read in flight and both land. (4) Escape
    closes the menu and focuses its ⋮ trigger."""
    pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    url = direct_server_with_data["url"]
    data_dir = direct_server_with_data["data_dir"]
    skill = _write_lifecycle_widget_extension(data_dir)
    page_errors: list[str] = []

    def card(tab_id: str) -> str:
        return f'[data-widget-key="{skill}:{tab_id}"]'

    def frame_count(page, tab_id: str) -> int:
        return page.locator(f"{card(tab_id)} iframe").count()

    def wait_frame(page, tab_id: str, present: bool, timeout: int = 10_000) -> None:
        page.wait_for_function(
            "([selector, present]) => (document.querySelector(`${selector} iframe`) !== null) === present",
            arg=[card(tab_id), present],
            timeout=timeout,
        )

    def toggle(page, enabled: bool) -> int:
        return page.evaluate(
            """async ([skill, enabled]) => (await fetch(`/api/skills/${encodeURIComponent(skill)}/toggle`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({enabled}),
            })).status""",
            [skill, enabled],
        )

    def emit_open(page) -> None:
        # The loopback debug hook app.js exposes; the same `open` the client
        # emits on every (re)connect, which the Widgets page reconciles on.
        page.evaluate("() => { window.__ouroWs.emit('open', {previouslyConnected: true}); }")

    def hang_frame_kept(page) -> bool:
        return page.evaluate("(selector) => document.querySelector(`${selector} iframe`)?.__ouroHangFrame === true", card("hang"))

    def routed_list(route, mutate) -> None:
        data = route.fetch().json()
        mutate(data)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    try:
        with sync_playwright() as pw:
            browser = getattr(pw, browser_name).launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.add_init_script(_PATCH_PROBE_SCRIPT)
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                assert toggle(page, True) == 200
                page.click('[data-nav-page="widgets"]')
                for tab_id in ("manual", "auto", "hang", "gauge"):
                    page.locator(card(tab_id)).wait_for(state="visible", timeout=30_000)
                wait_frame(page, "auto", True)
                wait_frame(page, "hang", True)
                page.wait_for_function(
                    "(selector) => document.querySelector(`${selector} [data-widget-power]`)?.textContent === 'Stop'",
                    arg=card("auto"),
                    timeout=10_000,
                )
                page.frame_locator(f"{card('auto')} iframe").locator("#root").wait_for(state="visible", timeout=10_000)

                # Mark the running auto frame and its card (the opaque-origin document
                # is unreadable from here, so identity is an expando on the nodes), the
                # hang frame (an unchanged entry), and watch the auto key's cards.
                page.evaluate(
                    """(sel) => {
                        const cardNode = document.querySelector(sel.auto);
                        const frame = cardNode.querySelector('iframe');
                        frame.__ouroOldFrame = true;
                        window.__oldFrame = frame;
                        cardNode.__ouroOldCard = true;
                        document.querySelector(`${sel.hang} iframe`).__ouroHangFrame = true;
                        window.__patchObs = {removedMarked: false, oldStoppingText: '', maxAutoCards: 1, maxAutoFrames: 1};
                        new MutationObserver(() => {
                            if (cardNode.hasAttribute('data-widget-removed')) {
                                window.__patchObs.removedMarked = true;
                                window.__patchObs.oldStoppingText = cardNode.querySelector('[data-widget-status]')?.textContent || '';
                            }
                            const cards = document.querySelectorAll(sel.auto);
                            window.__patchObs.maxAutoCards = Math.max(window.__patchObs.maxAutoCards, cards.length);
                            window.__patchObs.maxAutoFrames = Math.max(
                                window.__patchObs.maxAutoFrames, document.querySelectorAll(`${sel.auto} iframe`).length);
                        }).observe(document.getElementById('widgets-list'), {
                            subtree: true, childList: true, attributes: true, attributeFilter: ['data-widget-removed'],
                        });
                    }""",
                    {"auto": card("auto"), "hang": card("hang")},
                )
                page.evaluate("window.__hostFetchLog.length = 0")

                # (1) The auto card's skill revision moved; the reconnect reconcile
                # fetches the list, patches by key and lets the old frame flush first.
                page.route("**/api/widgets", lambda route: routed_list(route, lambda data: [
                    tab.__setitem__("revision", "f" * 64)
                    for tab in data.get("ui_tabs", []) if tab.get("key") == f"{skill}:auto"
                ]))
                emit_open(page)
                page.wait_for_function(
                    """(selector) => {
                        const cards = document.querySelectorAll(selector);
                        if (cards.length !== 1 || cards[0].__ouroOldCard === true) return false;
                        const frames = cards[0].querySelectorAll('iframe');
                        return frames.length === 1 && frames[0].__ouroOldFrame !== true;
                    }""",
                    arg=card("auto"),
                    timeout=15_000,
                )
                page.frame_locator(f"{card('auto')} iframe").locator("#root").wait_for(state="visible", timeout=10_000)
                page.wait_for_function(
                    "(selector) => document.querySelector(`${selector} [data-widget-status]`)?.textContent === 'Running'",
                    arg=card("auto"),
                    timeout=10_000,
                )
                page.unroute("**/api/widgets")
                observed = page.evaluate("window.__patchObs")
                assert observed["removedMarked"] is True, observed
                assert observed["oldStoppingText"] == "Stopping…", observed
                assert observed["maxAutoCards"] == 2, observed
                assert observed["maxAutoFrames"] == 1, observed
                fetch_log = page.evaluate("window.__hostFetchLog")
                flush = [row for row in fetch_log if row["url"].endswith(f"/api/extensions/{skill}/flush")]
                answered = [row for row in fetch_log if f"/api/extensions/{skill}/ping?flushed=200" in row["url"]]
                assert len(flush) == 1, fetch_log
                assert flush[0]["oldFrameConnected"] is True, fetch_log
                assert len(answered) == 1, fetch_log
                assert answered[0]["oldFrameConnected"] is True, fetch_log
                assert page.evaluate("window.__oldFrame.isConnected") is False
                assert page.evaluate("(selector) => document.querySelectorAll(selector).length", card("auto")) == 1
                assert frame_count(page, "auto") == 1
                assert page.locator("#widgets-list [data-widget-removed]").count() == 0
                assert hang_frame_kept(page), "an unchanged entry must keep its frame across the patch"
                assert frame_count(page, "manual") == 0

                # (2) The reconnect reconcile alone (no lifecycle event) removes a card
                # that left the list and re-adds it once it is back; running frames of
                # other cards are not touched by either pass.
                page.route("**/api/widgets", lambda route: routed_list(route, lambda data: data.__setitem__(
                    "ui_tabs", [tab for tab in data.get("ui_tabs", []) if tab.get("key") != f"{skill}:gauge"],
                )))
                emit_open(page)
                page.locator(card("gauge")).wait_for(state="detached", timeout=10_000)
                page.unroute("**/api/widgets")
                emit_open(page)
                page.locator(card("gauge")).wait_for(state="visible", timeout=10_000)
                page.wait_for_timeout(300)
                assert hang_frame_kept(page)
                assert frame_count(page, "auto") == 1
                assert page.locator("#widgets-list iframe").count() == 2

                # (3) Two quick launch-policy changes: the stored-map reads are held
                # here, so a second read in flight would be visible. With the writes
                # chained there is exactly one at a time, and both choices land.
                held: list = []

                def hold_reads(route):
                    if route.request.method == "GET":
                        held.append(route)
                    else:
                        route.continue_()

                def wait_held(count: int) -> None:
                    deadline = time.monotonic() + 5
                    while len(held) < count and time.monotonic() < deadline:
                        page.wait_for_timeout(50)
                    assert len(held) == count, len(held)

                page.route("**/api/ui/preferences", hold_reads)
                page.locator(f"{card('manual')} [data-widget-menu-trigger]").click()
                page.locator(f"{card('manual')} [data-widget-start-mode=\"auto\"]").click()
                page.locator(f"{card('hang')} [data-widget-menu-trigger]").click()
                page.locator(f"{card('hang')} [data-widget-start-mode=\"manual\"]").click()
                wait_held(1)
                page.wait_for_timeout(400)
                assert len(held) == 1, "the second change must wait for the first write"
                held.pop().continue_()
                wait_held(1)
                held.pop().continue_()
                # Both writes are on their way; stop holding reads before the
                # verification below issues its own GET.
                page.unroute("**/api/ui/preferences")
                page.wait_for_function(
                    """async ([manualKey, hangKey]) => {
                        const prefs = await (await fetch('/api/ui/preferences')).json();
                        const modes = prefs.widget_start_mode || {};
                        return modes[manualKey] === 'auto' && modes[hangKey] === 'manual' && Object.keys(modes).length === 2;
                    }""",
                    arg=[f"{skill}:manual", f"{skill}:hang"],
                    timeout=10_000,
                )
                wait_frame(page, "manual", True)
                assert page.locator(f"{card('manual')} [data-widget-start-mode=\"auto\"]").get_attribute("aria-checked") == "true"
                assert page.locator(f"{card('hang')} [data-widget-start-mode=\"manual\"]").get_attribute("aria-checked") == "true"
                assert hang_frame_kept(page), "Manual changes nothing until Stop"

                # (4) Escape closes the menu and returns focus to the ⋮ trigger.
                page.locator(f"{card('hang')} [data-widget-menu-trigger]").click()
                page.locator(f"{card('hang')} [data-widget-start-mode=\"manual\"]").wait_for(state="visible", timeout=5_000)
                assert page.evaluate("() => document.activeElement?.hasAttribute('data-widget-start-mode')")
                page.keyboard.press("Escape")
                page.locator(f"{card('hang')} [data-widget-start-mode=\"manual\"]").wait_for(state="hidden", timeout=5_000)
                assert page.evaluate(
                    "(selector) => document.activeElement === document.querySelector(`${selector} [data-widget-menu-trigger]`)",
                    card("hang"),
                )
                assert page.locator(f"{card('hang')} [data-widget-menu-trigger]").get_attribute("aria-expanded") == "false"

                _click_nav(page, "dashboard")
                page.wait_for_function("() => document.querySelectorAll('#widgets-list iframe').length === 0", timeout=5_000)
                assert page_errors == [], page_errors
            finally:
                browser.close()
    except PlaywrightError as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc).lower():
            pytest.skip(str(exc))
        raise
