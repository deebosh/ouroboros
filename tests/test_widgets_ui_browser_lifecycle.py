"""Widgets lifecycle phase 2 browser smoke: launch policy (auto / manual /
owner override), the ordered dispose → acknowledgement handshake, session-local
Stop suppression, and force-stop on skill disable. Kept apart from
``test_widgets_ui_browser.py`` (geometry / job retry) so neither file grows
past the size-ratchet band."""

from __future__ import annotations

import os
import pathlib
import textwrap

import pytest

from tests.test_ui_smoke_playwright import direct_server_with_data as _direct_server_with_data

direct_server_with_data = _direct_server_with_data


def _write_lifecycle_widget_extension(data_dir: pathlib.Path) -> str:
    """Install the launch-policy / ordered-stop fixture: a `manual` program (kind
    default), an `auto` instrument whose async dispose hook flushes through the
    bridge, an `auto` card whose hook never resolves, and a declarative gauge."""
    from ouroboros.skill_loader import SkillReviewState, compute_content_hash, save_review_state

    name = "lifecycle_widget_smoke"
    skill_dir = data_dir / "skills" / "external" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            f"""\
            ---
            name: {name}
            description: Isolated widget launch-policy and ordered-stop fixture.
            version: 0.1.0
            type: extension
            entry: plugin.py
            permissions: ["route", "widget"]
            ---
            # Widget lifecycle fixture
            """
        ),
        encoding="utf-8",
    )
    (skill_dir / "plugin.py").write_text(
        textwrap.dedent(
            """\
            async def ping(_request):
                return {"ok": True}


            async def flush(_request):
                return {"ok": True}


            def register(api):
                api.register_route("ping", ping, methods=("GET",))
                api.register_route("flush", flush, methods=("POST",))
                program = {"kind": "module", "entry": "widget.js", "height": 360}
                api.register_ui_tab("manual", "Manual program", render=program)
                api.register_ui_tab("auto", "Auto instrument", render={**program, "start": "auto"})
                api.register_ui_tab("hang", "Hanging hook", render={"kind": "module", "entry": "hang.js", "height": 360, "start": "auto"})
                api.register_ui_tab(
                    "gauge",
                    "Gauge",
                    render={"kind": "declarative", "schema_version": 1, "components": [{"type": "markdown", "text": "gauge"}]},
                )
            """
        ),
        encoding="utf-8",
    )
    (skill_dir / "widget.js").write_text(
        textwrap.dedent(
            f"""\
            (() => {{
                document.getElementById('root').textContent = 'Program running';
                // Async dispose hook: flush through the bridge, then prove the
                // answer arrived by issuing a second request that carries it.
                window.__ouroWidgetOnDispose(async () => {{
                    const saved = await fetch('/api/extensions/{name}/flush', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{state: 'saved'}}),
                    }});
                    await fetch('/api/extensions/{name}/ping?flushed=' + saved.status);
                }});
            }})();
            """
        ),
        encoding="utf-8",
    )
    (skill_dir / "hang.js").write_text(
        textwrap.dedent(
            """\
            (() => {
                document.getElementById('root').textContent = 'Never acknowledges';
                window.__ouroWidgetOnDispose(() => new Promise(() => {}));
            })();
            """
        ),
        encoding="utf-8",
    )
    content_hash = compute_content_hash(skill_dir, manifest_entry="plugin.py")
    save_review_state(data_dir, name, SkillReviewState(status="pass", content_hash=content_hash))
    return name


# Parent-side probe: every host fetch to the fixture skill is logged with the
# number of the skill's frames still attached and whether Widgets is the active
# page at that moment. The bridged flush a dispose hook issues goes through the
# parent's `fetch`, so this observes the ordered stop from the host's side.
_HOST_FETCH_PROBE_SCRIPT = r"""
(() => {
    const log = [];
    window.__hostFetchLog = log;
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => {
        const url = typeof input === 'string' ? input : String(input && input.url || input);
        if (url.includes('/api/extensions/lifecycle_widget_smoke/')) {
            log.push({
                url,
                t: performance.now(),
                frames: document.querySelectorAll('[data-widget-key^="lifecycle_widget_smoke:"] iframe').length,
                widgetsActive: Boolean(document.getElementById('page-widgets')?.classList.contains('active')),
            });
        }
        return original(input, init);
    };
})();
"""


def _click_nav(page, target: str) -> None:
    page.evaluate(
        """(target) => {
            const button = [...document.querySelectorAll(`[data-nav-page="${target}"]`)]
                .find((item) => getComputedStyle(item).display !== 'none');
            button?.click();
        }""",
        target,
    )


@pytest.mark.ui_browser
def test_ui_smoke_widget_launch_policy_and_ordered_stop(direct_server_with_data):
    """Widgets lifecycle phase 2 end to end: manual facade until Start, auto
    mounts on show, ordered dispose with acknowledgement (async hook flushes
    through the bridge before the frame goes; a hook that never resolves is cut
    after ~1 s without delaying the page switch), one frame per key across a
    leave/return race, session-local Stop suppression, owner override over the
    author default (menu and API), and force-stop + removal on skill disable."""
    pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    url = direct_server_with_data["url"]
    data_dir = direct_server_with_data["data_dir"]
    skill = _write_lifecycle_widget_extension(data_dir)
    evidence_dir = pathlib.Path(os.environ.get("OUROBOROS_UI_EVIDENCE_DIR", str(data_dir.parent)))
    evidence_dir.mkdir(parents=True, exist_ok=True)

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

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.add_init_script(_HOST_FETCH_PROBE_SCRIPT)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                toggled = page.evaluate(
                    """async (skill) => {
                        const response = await fetch(`/api/skills/${encodeURIComponent(skill)}/toggle`, {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({enabled: true}),
                        });
                        return {status: response.status, body: await response.json()};
                    }""",
                    skill,
                )
                assert toggled["status"] == 200, toggled
                page.click('[data-nav-page="widgets"]')
                for tab_id in ("manual", "auto", "hang", "gauge"):
                    page.locator(card(tab_id)).wait_for(state="visible", timeout=30_000)

                # auto → mounts on show (the `start:"auto"` survival path); manual → facade,
                # no frame; declarative → host-drawn, no Start/Stop and no policy menu.
                wait_frame(page, "auto", True)
                wait_frame(page, "hang", True)
                page.locator(f"{card('manual')} [data-widget-facade]").wait_for(state="visible", timeout=10_000)
                assert frame_count(page, "manual") == 0
                assert page.locator(f"{card('manual')} [data-widget-power]").inner_text() == "Start"
                assert page.locator(f"{card('gauge')} [data-widget-power]").count() == 0
                assert page.locator(f"{card('gauge')} [data-widget-menu-trigger]").count() == 0
                page.wait_for_function(
                    "(selector) => document.querySelector(`${selector} [data-widget-power]`)?.textContent === 'Stop'",
                    arg=card("auto"),
                    timeout=10_000,
                )
                assert page.locator(f"{card('auto')} [data-widget-status]").inner_text() == "Running"
                facade_height = page.locator(f"{card('manual')} [data-widget-facade]").evaluate(
                    "node => node.getBoundingClientRect().height"
                )
                assert facade_height == 360, facade_height
                page.screenshot(path=str(evidence_dir / "widget-lifecycle-cards.png"), full_page=True)
                page.locator(f"{card('manual')} [data-widget-menu-trigger]").click()
                page.locator(f"{card('manual')} [data-widget-start-mode=\"manual\"]").wait_for(state="visible", timeout=5_000)
                page.screenshot(path=str(evidence_dir / "widget-lifecycle-menu.png"), full_page=True)
                page.keyboard.press("Escape")
                page.locator(f"{card('manual')} [data-widget-start-mode=\"manual\"]").wait_for(state="hidden", timeout=5_000)

                # Leave: the page switches at once while both frames still stand in their
                # cards; the auto card's async hook flushes through the bridge and gets its
                # answer before the frame goes; the hanging hook is cut after ~1 s.
                page.evaluate("window.__hostFetchLog.length = 0")
                left_at = page.evaluate("performance.now()")
                _click_nav(page, "dashboard")
                page.wait_for_function(
                    "() => !document.getElementById('page-widgets').classList.contains('active')",
                    timeout=2_000,
                )
                assert frame_count(page, "hang") == 1, "the page switch must not wait for the acknowledgement"
                wait_frame(page, "auto", False, timeout=5_000)
                wait_frame(page, "hang", False, timeout=5_000)
                hang_gone_at = page.evaluate("performance.now()")
                assert 700 <= hang_gone_at - left_at <= 4_000, hang_gone_at - left_at
                fetch_log = page.evaluate("window.__hostFetchLog")
                flush = [row for row in fetch_log if row["url"].endswith(f"/api/extensions/{skill}/flush")]
                answered = [row for row in fetch_log if f"/api/extensions/{skill}/ping?flushed=200" in row["url"]]
                assert len(flush) == 1, fetch_log
                assert flush[0]["frames"] >= 1, fetch_log
                assert flush[0]["widgetsActive"] is False, fetch_log
                assert len(answered) == 1, fetch_log
                assert answered[0]["frames"] >= 1, fetch_log

                # Return: auto cards remount (one frame each — the hang card's remount waited
                # for its pending stop), the manual card stays a facade.
                _click_nav(page, "widgets")
                wait_frame(page, "auto", True)
                wait_frame(page, "hang", True)
                page.wait_for_timeout(300)
                assert frame_count(page, "auto") == 1
                assert frame_count(page, "hang") == 1
                assert frame_count(page, "manual") == 0

                # Leave and return within the acknowledgement window: the remount waits
                # for the pending stop, so the card never holds two frames and ends with
                # one fresh frame (the opaque-origin document is unreadable from here, so
                # freshness is an expando on the old node and a visible child root).
                page.evaluate(
                    """(selector) => {
                        const cardNode = document.querySelector(selector);
                        cardNode.querySelector('iframe').__ouroOldFrame = true;
                        window.__maxHangFrames = 1;
                        new MutationObserver(() => {
                            window.__maxHangFrames = Math.max(window.__maxHangFrames, cardNode.querySelectorAll('iframe').length);
                        }).observe(cardNode, {subtree: true, childList: true});
                    }""",
                    card("hang"),
                )
                _click_nav(page, "dashboard")
                page.wait_for_timeout(150)
                assert frame_count(page, "hang") == 1
                _click_nav(page, "widgets")
                page.wait_for_function(
                    """(selector) => {
                        const frames = document.querySelectorAll(`${selector} iframe`);
                        return frames.length === 1 && frames[0].__ouroOldFrame !== true;
                    }""",
                    arg=card("hang"),
                    timeout=10_000,
                )
                page.frame_locator(f"{card('hang')} iframe").locator("#root").wait_for(state="visible", timeout=10_000)
                page.wait_for_timeout(300)
                assert frame_count(page, "hang") == 1
                assert page.evaluate("window.__maxHangFrames") == 1

                # Owner Stop → facade; the Stop is remembered across leave/return until Start.
                page.locator(f"{card('auto')} [data-widget-power]").click()
                wait_frame(page, "auto", False)
                page.locator(f"{card('auto')} [data-widget-facade]").wait_for(state="visible", timeout=5_000)
                assert page.locator(f"{card('auto')} [data-widget-power]").inner_text() == "Start"
                _click_nav(page, "dashboard")
                wait_frame(page, "hang", False, timeout=5_000)
                _click_nav(page, "widgets")
                wait_frame(page, "hang", True)
                page.wait_for_timeout(300)
                assert frame_count(page, "auto") == 0, "an owner-stopped auto card must not restart on return"
                page.locator(f"{card('auto')} [data-widget-power]").click()
                wait_frame(page, "auto", True)

                # Owner override from the card menu: Manual program → Auto starts it now and
                # persists the whole map through the preferences API.
                page.locator(f"{card('manual')} [data-widget-menu-trigger]").click()
                page.locator(f"{card('manual')} [data-widget-start-mode=\"auto\"]").click()
                wait_frame(page, "manual", True)
                prefs = page.evaluate("async () => (await fetch('/api/ui/preferences')).json()")
                assert prefs["widget_start_mode"] == {f"{skill}:manual": "auto"}, prefs
                assert page.locator(f"{card('manual')} [data-widget-start-mode=\"auto\"]").get_attribute("aria-checked") == "true"

                # Owner override through the API beats the author default in both directions
                # after the hard reset: the author-auto card waits, the author-manual card runs.
                saved = page.evaluate(
                    """async (payload) => (await fetch('/api/ui/preferences', {
                        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
                    })).status""",
                    {"widget_start_mode": {f"{skill}:manual": "auto", f"{skill}:auto": "manual"}},
                )
                assert saved == 200
                page.click("#widgets-refresh")
                wait_frame(page, "hang", True, timeout=15_000)
                wait_frame(page, "manual", True, timeout=15_000)
                page.locator(f"{card('auto')} [data-widget-facade]").wait_for(state="visible", timeout=10_000)
                assert frame_count(page, "auto") == 0
                assert page.locator(f"{card('auto')} [data-widget-start-mode=\"manual\"]").get_attribute("aria-checked") == "true"

                # Disabling the skill while its cards run force-stops them in order and
                # removes every card of that skill.
                disabled = page.evaluate(
                    """async (skill) => (await fetch(`/api/skills/${encodeURIComponent(skill)}/toggle`, {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({enabled: false}),
                    })).status""",
                    skill,
                )
                assert disabled == 200
                page.wait_for_function(
                    "(prefix) => document.querySelectorAll(`[data-widget-key^=\"${prefix}\"]`).length === 0",
                    arg=f"{skill}:",
                    timeout=20_000,
                )
                assert page.locator("#widgets-list iframe").count() == 0
            finally:
                browser.close()
    except PlaywrightError as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc).lower():
            pytest.skip(str(exc))
        raise
