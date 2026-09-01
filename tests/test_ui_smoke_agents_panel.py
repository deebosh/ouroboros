"""Settings → Agents acceptance (docs/DESIGN.md "List editors"): real UI, real browser.

Sibling of ``test_ui_smoke_playwright.py`` (which carries the shared server fixture and
sits at its byte gate); marker-gated the same way, runs in the same CI job.
"""

from __future__ import annotations

import json

import pytest

pytest_plugins = ("tests.test_ui_smoke_playwright",)


_AGENTS_PANEL_ROSTER = {
    "enabled": True,
    "items": [
        {"subagent_id": "claude_builder", "recommended_use": "Main workhorse for code and design.",
         "route": {"kind": "agent_session", "target_id": "claude=claude-opus-5"}, "effort": "medium"},
        {"subagent_id": "codex_reviewer", "recommended_use": "Deep code review of diffs.",
         "route": {"kind": "agent_session", "target_id": "codex=gpt-5.6-sol-high"}},
        {"subagent_id": "api_scout", "recommended_use": "Fast independent research.",
         "route": {"kind": "api_model", "target_id": "openai/gpt-5.6-luna"}, "effort": "high"},
    ],
}

_AGENTS_PANEL_VISIBLE_ROWS_JS = """
    (selector) => {
        // One pixel of slack: the list's own border sits on the scroll edge.
        const box = document.querySelector('.settings-scroll').getBoundingClientRect();
        return [...document.querySelectorAll(selector)]
            .map((row) => row.getBoundingClientRect())
            .filter((r) => r.top >= box.top - 1 && r.bottom <= box.bottom + 1).length;
    }
"""


def _open_agents_tab(page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("#page-chat", timeout=30_000)
    page.click('[data-nav-page="settings"]')
    page.wait_for_selector(".settings-shell", timeout=15_000)
    page.click('[data-settings-tab="agents"]')
    page.wait_for_selector("#available-subagents-editor .available-subagent-row", timeout=20_000)
    # The list at the top of the scroll body: "three cards fit a laptop-height body" is a
    # claim about the cards, not about the section's heading and copy above them.
    page.evaluate("() => document.querySelector('.available-subagents-list').scrollIntoView({block: 'start'})")


def _agents_panel_add_reveals_the_new_card(page) -> None:
    """The shared add-and-reveal contract, run on whichever engine the caller launched."""
    page.click("[data-subagent-add]")
    page.wait_for_function(
        "() => document.querySelectorAll('.available-subagent-row').length === 4", timeout=5_000)
    # The appended card is fully inside the scroll body and its Description holds the caret;
    # the section-level error line stays hidden and no card is tinted before a save attempt.
    page.wait_for_function(
        """() => {
            const rows = [...document.querySelectorAll('.available-subagent-row')];
            const last = rows[rows.length - 1];
            const box = document.querySelector('.settings-scroll').getBoundingClientRect();
            const r = last.getBoundingClientRect();
            return r.top >= box.top - 1 && r.bottom <= box.bottom + 1
                && document.activeElement === last.querySelector('[data-subagent-field="recommended_use"]');
        }""",
        timeout=5_000,
    )
    assert page.evaluate("() => document.querySelector('[data-subagents-validation]').hidden") is True
    assert page.locator(".available-subagent-row[data-invalid]").count() == 0
    hint = page.locator(".available-subagent-row").last.locator("[data-subagent-meta]")
    assert "Choose how this subagent runs" in hint.inner_text()


def _agents_panel_typing_reads_draft(page) -> None:
    """A keystroke into a SAVED card (no structural repaint) turns every head status to
    Draft at once, patched in place — the caret stays in the field being typed into."""
    field = page.locator('.available-subagent-row [data-subagent-field="recommended_use"]').first
    field.click()
    page.keyboard.type(" ")
    page.wait_for_function(
        """() => [...document.querySelectorAll('[data-subagent-status]')]
            .every((el) => el.textContent.startsWith('Draft · '))
            && document.activeElement === document.querySelector(
                '.available-subagent-row [data-subagent-field="recommended_use"]')""",
        timeout=5_000,
    )


@pytest.mark.ui_browser
def test_ui_smoke_agents_panel_list_editor(direct_server_with_data):
    """Settings → Agents: three compact subagent cards fit a laptop-height body; typing turns
    the head status to Draft in place; Add reveals the appended card with the caret in it and
    no error; only a Save attempt (whichever validation aborts it) turns the empty route into
    a section-level line plus a tinted, self-naming card, and the fix typed into the card
    clears line, tint and footer together; a later Add is an invitation again; Review lanes'
    Add lives in the group head and reveals its new row (docs/DESIGN.md "List editors")."""
    pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    settings_path = direct_server_with_data["data_dir"] / "settings.json"
    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    saved["OUROBOROS_SUBAGENTS"] = json.dumps(_AGENTS_PANEL_ROSTER)
    settings_path.write_text(json.dumps(saved), encoding="utf-8")
    direct_server_with_data["restart_server"]()
    url = direct_server_with_data["url"]

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                _open_agents_tab(page, url)
                assert page.evaluate(_AGENTS_PANEL_VISIBLE_ROWS_JS, ".available-subagent-row") >= 3
                _agents_panel_typing_reads_draft(page)
                _agents_panel_add_reveals_the_new_card(page)

                # Every Save click is an attempt, even one that another field's validation
                # aborts: with a malformed Every-N cadence the roster is still judged (line +
                # tint) while the footer names the cadence error. The segmented control's
                # hidden inputs are poked directly — the cadence row is not the point here.
                mode_before = page.evaluate("() => document.getElementById('s-post-task-evolution-mode').value")
                page.evaluate("() => { document.getElementById('s-post-task-evolution-mode').value = 'every_n';"
                              " document.getElementById('s-evo-cadence-n').value = 'x'; }")
                page.click("#btn-save-settings")
                page.wait_for_function(
                    "() => !document.querySelector('[data-subagents-validation]').hidden", timeout=5_000)
                assert page.locator(".available-subagent-row[data-invalid]").count() == 1
                assert "Every-N cadence" in page.locator("#settings-status").inner_text()
                page.evaluate("(mode) => { document.getElementById('s-post-task-evolution-mode').value = mode;"
                              " document.getElementById('s-evo-cadence-n').value = ''; }", mode_before)
                # With the cadence valid again, Save names the roster error in the footer too.
                page.click("#btn-save-settings")
                page.wait_for_function(
                    "() => document.getElementById('settings-status').textContent.startsWith('Available subagents:')",
                    timeout=5_000)
                line = page.locator("[data-subagents-validation]").inner_text()
                assert line.startswith("Subagent 4 needs a model or agent-session route.")
                tinted = page.locator(".available-subagent-row[data-invalid]")
                assert tinted.count() == 1
                assert tinted.locator('[data-subagent-meta][data-tone="error"]').inner_text().startswith(
                    "Subagent 4 needs")

                # A fix typed into the field clears the section line and the tint TOGETHER …
                tinted.locator('[data-subagent-field="model"]').fill("openai/gpt-5.6-luna")
                page.wait_for_function(
                    "() => document.querySelector('[data-subagents-validation]').hidden"
                    " && !document.querySelector('.available-subagent-row[data-invalid]')",
                    timeout=5_000,
                )
                assert "Subagent 4" not in page.locator("#settings-status").inner_text()
                # … and the NEXT added entry is still an invitation, not an error: the
                # attempt judged the rows that existed then, not every row forever.
                page.click("[data-subagent-add]")
                page.wait_for_function(
                    "() => document.querySelectorAll('.available-subagent-row').length === 5", timeout=5_000)
                assert page.evaluate("() => document.querySelector('[data-subagents-validation]').hidden") is True
                assert page.locator(".available-subagent-row[data-invalid]").count() == 0
                assert "Choose how this subagent runs" in page.locator(
                    ".available-subagent-row").last.locator("[data-subagent-meta]").inner_text()

                # Review lanes: the group's Add sits in its head and reveals the appended row.
                assert page.evaluate(
                    "() => Boolean(document.getElementById('btn-add-triad-slot').closest('.reviewer-slots-head'))")
                before = page.locator("#reviewer-triad-rows .reviewer-slot-row").count()
                page.click("#btn-add-triad-slot")
                page.wait_for_function(
                    """(before) => {
                        const rows = document.querySelectorAll('#reviewer-triad-rows .reviewer-slot-row');
                        if (rows.length !== before + 1) return false;
                        const last = rows[rows.length - 1];
                        const box = document.querySelector('.settings-scroll').getBoundingClientRect();
                        const r = last.getBoundingClientRect();
                        return r.top >= box.top - 1 && r.bottom <= box.bottom + 1
                            && document.activeElement === last.querySelector('[data-slot-route]');
                    }""",
                    arg=before,
                    timeout=5_000,
                )
            finally:
                browser.close()

            # The desktop shell is WebKit: the add-and-reveal contract must hold there too.
            try:
                webkit = pw.webkit.launch()
            except PlaywrightError as exc:
                if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc).lower():
                    webkit = None
                else:
                    raise
            if webkit is not None:
                try:
                    page = webkit.new_page(viewport={"width": 1440, "height": 900})
                    _open_agents_tab(page, url)
                    _agents_panel_add_reveals_the_new_card(page)
                finally:
                    webkit.close()
    except PlaywrightError as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc).lower():
            pytest.skip(str(exc))
        raise
