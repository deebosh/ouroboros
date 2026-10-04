"""Real Chat replay and controls over isolated durable logs, with a controlled census."""
from __future__ import annotations

import json
import urllib.request

import pytest

from tests.test_ui_smoke_playwright import direct_server_with_data as direct_server_with_data

pytestmark = [pytest.mark.ui_browser, pytest.mark.serial]


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_speechless_history_and_direct_resume(direct_server_with_data, engine):
    from playwright.sync_api import sync_playwright

    fixture = direct_server_with_data
    root, task_id = fixture["data_dir"], "tool-only-pause"
    fixture["stop_server"]()
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "chat.jsonl").write_text(json.dumps({"direction": "in", "chat_id": 1,
        "task_id": task_id, "text": "Inspect the saved sources", "ts": "2026-10-04T09:00:00Z"}) + "\n", encoding="utf-8")
    calls = [{"type": kind, "task_id": task_id, "invocation_id": f"call-{index}",
              "tool": "read_file", "is_error": False, "ts": "2026-10-04T09:00:01Z"}
             for index in range(24) for kind in ("tool_call_started", "tool_call")]
    (logs / "tools.jsonl").write_text("".join(json.dumps(row) + "\n" for row in calls), encoding="utf-8")
    results = root / "task_results"
    results.mkdir(exist_ok=True)
    (results / f"{task_id}.json").write_text(json.dumps({"_schema_version": 1, "task_id": task_id,
        "status": "completed", "suggested_name": "Read source records", "_is_direct_chat": True,
        "root_phase_checkpoint": {"post_task_synthesis": "paused"}}), encoding="utf-8")
    fixture["start_server"]()
    census = {"phase": "budget_pausing", "pause_cause": "owner"}
    with urllib.request.urlopen(fixture["url"] + "/api/state", timeout=10) as response:
        state = json.load(response)

    def state_route(route):
        data = dict(state)
        data.update(active_chat_activities=[{"activity_id": task_id, "chat_id": 1,
                    "kind": "direct_chat", **census}], active_chat_activities_complete=True,
                    supervisor_ready=True)
        route.fulfill(json=data)

    with sync_playwright() as pw:
        browser = getattr(pw, engine).launch(headless=True)
        try:
            for width in (1440, 390):
                context = browser.new_context(viewport={"width": width, "height": 900},
                    is_mobile=width == 390, has_touch=width == 390)
                page = context.new_page()
                page.route("**/api/state", state_route)
                census["phase"] = "budget_pausing"
                page.goto(fixture["url"], wait_until="domcontentloaded")
                card = page.locator(f'.chat-live-card[data-task-id="{task_id}"]')
                card.wait_for(state="visible")
                page.wait_for_function("() => document.querySelector('[data-live-phase-secondary]')?.textContent.includes('Pausing')")
                assert card.locator("[data-resume-run]").count() == 0
                assert card.locator("[data-cancel-run]").is_visible()
                assert card.locator("[data-live-phase]").inner_text() == "Done"
                assert page.locator(".chat-bubble.assistant:not(.typing-bubble)").count() == 0
                assert "24 tool calls" in card.inner_text()
                page.screenshot(path=str(root.parent / f"task-evidence-{engine}-{width}-pausing.png"), full_page=True)

                census["phase"] = "budget_paused"
                page.reload(wait_until="domcontentloaded")
                resume = card.locator("[data-resume-run]")
                resume.wait_for(state="visible")
                assert resume.inner_text() == "Resume"
                assert card.locator("[data-live-phase-secondary]").inner_text() == "Paused · owner pause"
                assert card.locator("[data-live-title]").inner_text() == "Read source records"
                assert page.locator("#chat-messages").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
                assert resume.evaluate("el => { const r=el.getBoundingClientRect(); return r.left>=0 && r.right<=innerWidth; }")
                card.locator("[data-cancel-run]").click()
                assert page.locator('[role="menuitem"]', has_text="Resume").count() == 0
                assert page.locator('[role="menuitem"]', has_text="Stop now").is_visible()
                page.keyboard.press("Escape")
                page.screenshot(path=str(root.parent / f"task-evidence-{engine}-{width}-paused.png"), full_page=True)
                card.locator(":scope > [data-live-summary-button]").click()
                assert card.locator(".chat-live-line").count() == 1
                assert "24 tool calls" in card.locator(".chat-live-line").inner_text()
                page.screenshot(path=str(root.parent / f"task-evidence-{engine}-{width}-expanded.png"), full_page=True)

                # The controlled census offers the door; the real server still
                # refuses a root with no resumable queue/fence authority.
                resume.click()
                page.locator(".toast", has_text="Resume refused").wait_for(state="visible")
                assert card.locator("[data-live-phase-secondary]").inner_text() == "Paused · owner pause"
                assert card.locator("[data-live-phase]").inner_text() == "Done"
                context.close()
        finally:
            browser.close()
