"""Real delivery -> chat file card -> document reader, live and after a history reload.

The mock model calls the real ``send_file`` tool, so every file reaches Chat through
the supervisor document handler exactly as the owner receives it: inline bytes in the
live frame, the immutable task-artifact route after a reload. Main and a Project room
are both driven, in Chromium and WebKit at the desktop client size (1473x978) and a
phone (390x844), in light and dark appearance. The 409 and 503 answers are Playwright
route stubs (labelled where used); every other answer is the real server's."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from devtools.benchmarks.common.server_runner import _api
from tests.test_ui_smoke_playwright import direct_server_with_data  # noqa: F401 - pytest fixture import
from tests.ui_chat_viewport_smoke import _CAPTURE_TEST_SOCKET

LIMIT = 1024 * 1024
WIDE, NARROW = {"width": 1473, "height": 978}, {"width": 390, "height": 844}
TABLE = ("| " + " | ".join(f"Measured column {index}" for index in range(12)) + " |\n"
         + "|" + "---|" * 12 + "\n" + "| " + " | ".join(f"value-{index}-with-length" for index in range(12)) + " |\n")
BRIEF = (
    "# Delivery brief\n\n"
    "The first line of a paragraph\ncontinues on the next line in the same paragraph.\n"
    "An explicit break ends here  \nand this line follows it.\n\n"
    '<script>window.__readerXss = 1</script><img src="/reader-probe/raw.png" onerror="window.__readerXss = 2">\n\n'
    "![Remote chart](https://example.invalid/reader-probe/remote.png) and ![Local chart](./reader-probe/local.png)\n\n"
    "[Sibling note](./sibling.md) and [Project site](https://example.com/)\n\n"
    '<svg onload="window.__readerXss = 3"><circle r="4"></circle></svg>\n\n'
    + TABLE + "\n```python\nprint('" + "a very long line of code " * 12 + "')\n```\n\n"
    + "".join(f"## Section {index}\n\nParagraph {index} keeps the reader long enough to scroll.\n\n" for index in range(30))
)
NOTES = "Plain notes\nline two\twith a tab\nПривет, мир\n" + "long-line " * 40 + "\n"
# 1 ASCII byte, then two-byte characters: the 1 MiB cap lands inside a character.
BIG = "x" + "й" * 800_000
FILES = {
    "brief.md": BRIEF.encode(), "notes.txt": NOTES.encode(), "big.md": BIG.encode(),
    "broken.txt": b"valid start \xff\xfe invalid middle\n", "binary.txt": b"text\x00\x01\x02 binary",
    "page.html": b"<h1>not read here</h1>", "tampered.md": b"# Tampered\n\nOriginal delivered bytes.\n",
}

READER_STATE = """() => {
    const dialog = document.querySelector('dialog.document-reader[open]');
    if (!dialog) return null;
    const body = dialog.querySelector('.document-reader-body');
    const md = body.querySelector('.document-reader-markdown');
    const rect = dialog.getBoundingClientRect();
    return {
        title: dialog.querySelector('.document-reader-title').textContent,
        meta: dialog.querySelector('.document-reader-meta').textContent,
        notice: dialog.querySelector('.document-reader-notice').hidden ? '' : dialog.querySelector('.document-reader-notice').textContent,
        status: body.querySelector('.document-reader-status')?.textContent || '',
        retry: Boolean(body.querySelector('[data-reader-action="retry"]')),
        views: !dialog.querySelector('.document-reader-views').hidden,
        markdown: md ? md.innerText : null,
        source: body.querySelector('.document-reader-source')?.textContent ?? null,
        paragraphs: md ? [...md.querySelectorAll('p')].map((p) => ({ text: p.textContent, breaks: p.querySelectorAll('br').length })) : [],
        active: document.activeElement === body,
        activeInside: dialog.contains(document.activeElement),
        active_elements: md ? md.querySelectorAll('script, img, iframe, object, embed, svg, video, audio').length : 0,
        links: md ? [...md.querySelectorAll('a')].map((a) => [a.textContent, a.getAttribute('href')]) : [],
        tables: md ? [...md.querySelectorAll('.md-table-wrap')].map((w) => w.scrollWidth > w.clientWidth + 1) : [],
        bodyOverflowX: body.scrollWidth - body.clientWidth,
        scroll: [body.scrollTop, body.scrollHeight, body.clientHeight],
        rect: [rect.left, rect.top, rect.width, rect.height],
        viewport: [innerWidth, innerHeight],
        xss: window.__readerXss ?? null,
    };
}"""


def _send_files_mock(paths, calls):
    state = {"sent": False}

    def handler(request_handler):
        request = json.loads(request_handler.rfile.read(int(request_handler.headers.get("content-length") or 0)))
        names = {tool.get("function", {}).get("name") for tool in request.get("tools") or []}
        message = {"role": "assistant", "content": "The files are delivered."}
        if not state["sent"] and "send_file" in names:
            state["sent"] = True
            message = {"role": "assistant", "content": "", "tool_calls": [{
                "id": f"reader-file-{index}", "type": "function", "function": {
                    "name": "send_file", "arguments": json.dumps({"file_path": str(path), "caption": ""}),
                },
            } for index, path in enumerate(paths)]}
            calls.extend(path.name for path in paths)
        finish = "tool_calls" if message.get("tool_calls") else "stop"
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        common = {"id": "mock-reader", "model": request.get("model") or "mock-model"}
        if request.get("stream"):
            delta = dict(message)
            if delta.get("tool_calls"):
                delta["tool_calls"] = [dict(call, index=index) for index, call in enumerate(delta["tool_calls"])]
            frames = [{**common, "object": "chat.completion.chunk",
                       "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]},
                      {**common, "object": "chat.completion.chunk", "choices": [], "usage": usage}]
            data = ("".join("data: " + json.dumps(frame) + "\n\n" for frame in frames) + "data: [DONE]\n\n").encode()
            content_type = "text/event-stream"
        else:
            data = json.dumps({**common, "object": "chat.completion", "usage": usage,
                               "choices": [{"index": 0, "message": message, "finish_reason": finish}]}).encode()
            content_type = "application/json"
        request_handler.send_response(200)
        request_handler.send_header("Content-Type", content_type)
        request_handler.send_header("Content-Length", str(len(data)))
        request_handler.end_headers()
        request_handler.wfile.write(data)

    return handler


def _reader(page):
    return page.evaluate(READER_STATE)


def _open(page, name, *, ready=".document-reader-markdown, .document-reader-source, .document-reader-status:not(.is-loading)",
          feed="#chat-messages"):
    page.locator(f"{feed} .chat-file-card").filter(has_text=name).click()
    page.locator("dialog.document-reader[open]").wait_for(state="visible")
    page.locator(f"dialog.document-reader[open] :is({ready})").first.wait_for(state="attached")
    return _reader(page)


def _close_with_escape(page):
    page.keyboard.press("Escape")
    page.locator("dialog.document-reader").wait_for(state="detached")


def _feed_scroll(page, feed="#chat-messages"):
    """The conversation's scroll offset once layout has settled (two equal reads)."""
    read = "feed => document.querySelector(feed).scrollTop"
    value = page.evaluate(read, feed)
    for _ in range(20):
        page.wait_for_timeout(150)
        latest = page.evaluate(read, feed)
        if latest == value:
            return value
        value = latest
    return value


def _place(page, name, feed="#chat-messages"):
    """The settled feed offset with the card in view, as a click leaves it (a click scrolls it in)."""
    page.locator(f"{feed} .chat-file-card").filter(has_text=name).scroll_into_view_if_needed()
    return _feed_scroll(page, feed)


def _check_brief(state, title="brief.md"):
    assert state["title"] == title and state["meta"].startswith("MD · ")
    joined = [paragraph for paragraph in state["paragraphs"] if paragraph["text"].startswith("The first line")]
    # Document semantics: one paragraph, soft newlines joined, only the explicit hard break kept.
    assert len(joined) == 1 and joined[0]["breaks"] == 1, state["paragraphs"][:3]
    assert "continues on the next line in the same paragraph" in joined[0]["text"]
    # Raw HTML stays literal text, images stay references, nothing active is mounted.
    assert "<script>window.__readerXss = 1</script>" in state["markdown"]
    assert "Image: Remote chart" in state["markdown"] and "Image: Local chart" in state["markdown"]
    assert state["active_elements"] == 0 and state["xss"] is None
    links = dict(state["links"])
    assert links.get("Sibling note") is None, "a relative link is not resolved against any folder"
    assert links.get("Project site") == "https://example.com/"
    assert state["views"] is True and state["bodyOverflowX"] <= 1


@pytest.mark.ui_browser
@pytest.mark.serial
def test_delivered_documents_open_in_the_reader_live_and_after_reload(direct_server_with_data, monkeypatch, tmp_path):  # noqa: F811
    from playwright.sync_api import sync_playwright
    from ouroboros.artifacts import task_artifact_dir_path
    from tests import fixtures_mock_llm

    root = direct_server_with_data["data_dir"]
    evidence = Path(os.environ.get("OUROBOROS_UI_EVIDENCE_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    source = root / "reader-src"
    source.mkdir()
    paths = []
    for name, data in FILES.items():
        (source / name).write_bytes(data)
        paths.append(source / name)
    calls = []
    monkeypatch.setattr(fixtures_mock_llm._Handler, "do_POST", _send_files_mock(paths, calls))
    url = direct_server_with_data["url"]
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        try:
            page = chromium.new_page(viewport=WIDE, color_scheme="dark")
            page.add_init_script(f"({_CAPTURE_TEST_SOCKET})()")
            frames, requests, failed = {}, [], []

            def capture(payload):
                value = json.loads(payload) if isinstance(payload, str) and '"document"' in payload else {}
                if value.get("type") == "document":
                    frames.setdefault(value["filename"], value)

            page.on("websocket", lambda socket: socket.on("framereceived", capture))
            page.on("request", lambda request: requests.append((request.url, request.headers.get("range"))))
            page.on("requestfailed", lambda request: failed.append(request.url))
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#chat-input").wait_for(state="visible")
            page.wait_for_function("() => window.__testSockets?.some(socket => socket.readyState === WebSocket.OPEN)")
            page.locator("#chat-input").fill("Send me the documents.")
            page.locator("#chat-send").click()
            for name in FILES:
                page.locator(".chat-file-card").filter(has_text=name).wait_for(state="visible", timeout=60_000)
            assert sorted(calls) == sorted(FILES) and sorted(frames) == sorted(FILES)
            assert all(frame["file_base64"] and frame["download_url"].startswith("/api/tasks/") for frame in frames.values())
            artifact = {name: url + frame["download_url"] for name, frame in frames.items()}
            readable = {name: page.locator(".chat-file-card").filter(has_text=name).locator(".chat-file-more").inner_text()
                        for name in FILES}
            assert readable == {name: ("•••" if name == "page.html" else "Read") for name in FILES}

            # Live: the inline delivered bytes, with no request to any address of the file.
            before = _place(page, "brief.md")
            requests.clear()
            state = _open(page, "brief.md", ready=".document-reader-markdown")
            _check_brief(state)
            assert state["active"], "the reading region takes focus so keys scroll it at once"
            live_markdown = state["markdown"]
            page.wait_for_timeout(300)
            assert not [entry for entry in requests if "/artifacts/" in entry[0] or "/api/files/" in entry[0]
                        or "reader-probe" in entry[0] or "example.com" in entry[0]], requests
            page.screenshot(path=str(evidence / "chromium-wide-dark-brief.png"))
            ring = "() => getComputedStyle(document.querySelector('.document-reader-body')).outlineStyle"
            assert page.evaluate(ring) == "none", "a pointer open shows no ring on the reading region"
            page.keyboard.press("PageDown")
            page.wait_for_function("() => document.querySelector('.document-reader-body').scrollTop > 0")
            assert page.evaluate(ring) == "solid", "the first key shows the one focus ring"
            for _ in range(8):
                page.keyboard.press("Tab")
                assert _reader(page)["activeInside"], "Tab stays inside the reader"
            page.locator('[data-reader-view="source"]').click()
            source_state = _reader(page)
            assert source_state["source"] == BRIEF and source_state["markdown"] is None
            assert page.locator(".document-reader-source").get_attribute("contenteditable") is None
            page.locator('[data-reader-view="formatted"]').click()
            _close_with_escape(page)
            assert page.evaluate("() => document.activeElement?.closest('.chat-file-card')?.textContent.includes('brief.md')")
            assert abs(_feed_scroll(page) - before) <= 1, "closing returns to the same place in the conversation"

            state = _open(page, "notes.txt", ready=".document-reader-source")
            assert state["source"] == NOTES and not state["views"] and state["meta"].startswith("TXT · ")
            page.locator('dialog.document-reader [data-reader-action="close"]').click()
            page.locator("dialog.document-reader").wait_for(state="detached")

            state = _open(page, "big.md", ready=".document-reader-markdown")
            assert "Showing the first 1.0 MB of 1.5 MB" in state["notice"] and "UTF-8" not in state["notice"]
            assert "�" not in state["markdown"]
            _close_with_escape(page)
            state = _open(page, "broken.txt", ready=".document-reader-source")
            assert "not valid UTF-8" in state["notice"] and "�" in state["source"]
            _close_with_escape(page)
            state = _open(page, "binary.txt", ready=".document-reader-status:not(.is-loading)")
            assert "does not look like text" in state["status"] and state["source"] is None
            _close_with_escape(page)
            page.locator(".chat-file-card").filter(has_text="page.html").click()
            page.locator(".chat-file-dialog[open]").wait_for(state="visible")
            assert page.locator("dialog.document-reader").count() == 0, "HTML keeps the file dialog"
            page.locator('.chat-file-dialog[open] [data-file-action="close"]').click()

            # The stored copy of one delivery is damaged on disk after it was sent.
            tampered = frames["tampered.md"]
            stored = task_artifact_dir_path(root, tampered["task_id"]) / tampered["file_ref"]["path"]
            stored.chmod(0o644)
            stored.write_bytes(b"# Tampered\n\nChanged after delivery!!!\n")

            # Replay: the same documents now come from the immutable artifact route.
            page.reload(wait_until="domcontentloaded")
            page.locator(".chat-file-card").filter(has_text="brief.md").wait_for(state="visible", timeout=30_000)
            requests.clear()
            state = _open(page, "brief.md", ready=".document-reader-markdown")
            _check_brief(state)
            assert state["markdown"] == live_markdown, "replay shows the same delivered document"
            assert (artifact["brief.md"], None) in requests, "a known small file is read whole"
            _close_with_escape(page)
            requests.clear()
            state = _open(page, "big.md", ready=".document-reader-markdown")
            assert (artifact["big.md"], f"bytes=0-{LIMIT - 1}") in requests
            assert "Showing the first 1.0 MB of 1.5 MB" in state["notice"] and "UTF-8" not in state["notice"]
            _close_with_escape(page)
            requests.clear()
            state = _open(page, "tampered.md", ready=".document-reader-status:not(.is-loading)")
            assert "did not pass its integrity check" in state["status"] and not state["retry"]
            assert not [entry for entry in requests if "/api/files/" in entry[0]], "no fallback to a current file path"
            _close_with_escape(page)

            # Platform refusal (#1297) offers Retry; a changed row does not. Both answers are
            # Playwright route stubs: the real 404 is the tampered copy above.
            page.route(artifact["notes.txt"], lambda route: route.fulfill(status=503, json={
                "error": "artifact could not be read", "reason_code": "artifact_unavailable"}))
            state = _open(page, "notes.txt", ready=".document-reader-status:not(.is-loading)")
            assert "cannot read the delivered copy right now" in state["status"] and state["retry"]
            page.unroute(artifact["notes.txt"])
            page.locator('[data-reader-action="retry"]').click()
            page.locator("dialog.document-reader .document-reader-source").wait_for(state="attached")
            assert _reader(page)["source"] == NOTES
            _close_with_escape(page)
            page.route(artifact["notes.txt"], lambda route: route.fulfill(status=409, json={
                "error": "changed", "reason_code": "artifact_identity_changed"}))
            state = _open(page, "notes.txt", ready=".document-reader-status:not(.is-loading)")
            assert "changed after it was delivered" in state["status"] and not state["retry"]
            page.unroute(artifact["notes.txt"])
            _close_with_escape(page)

            # Rapid close: the held read is aborted and its late answer paints nothing.
            held = []
            page.route("**/artifacts/**", lambda route: held.append(route))
            page.locator(".chat-file-card").filter(has_text="brief.md").click()
            page.locator("dialog.document-reader .document-reader-status.is-loading").wait_for(state="attached")
            _close_with_escape(page)
            page.locator(".chat-file-card").filter(has_text="notes.txt").click()
            page.wait_for_function("() => document.querySelector('dialog.document-reader .document-reader-status.is-loading')")
            for _ in range(40):
                if artifact["brief.md"] in failed:
                    break
                page.wait_for_timeout(50)
            assert artifact["brief.md"] in failed, "closing aborted the pending read"
            assert _reader(page)["title"] == "notes.txt"
            for route in held:
                try:
                    route.continue_()
                except Exception:  # the aborted request has no route left to continue
                    pass
            page.unroute("**/artifacts/**")
            page.locator("dialog.document-reader .document-reader-source").wait_for(state="attached")
            state = _reader(page)
            assert state["title"] == "notes.txt" and state["source"] == NOTES and state["markdown"] is None
            _close_with_escape(page)

            # Phone: a full sheet in light appearance; Close returns to the same place.
            page.set_viewport_size(NARROW)
            page.emulate_media(color_scheme="light")
            before = _place(page, "brief.md")
            state = _open(page, "brief.md", ready=".document-reader-markdown")
            assert [round(value) for value in state["rect"]] == [0, 0, NARROW["width"], NARROW["height"]], state["rect"]
            assert any(state["tables"]) and state["bodyOverflowX"] <= 1, "only the table scrolls sideways"
            close = page.locator('dialog.document-reader [data-reader-action="close"]').bounding_box()
            download = page.locator('dialog.document-reader [data-reader-action="download"]').bounding_box()
            assert close and close["x"] + close["width"] <= NARROW["width"] and close["y"] >= 0
            # Close stays beside the title; the other actions take one row below it.
            assert download["x"] + download["width"] <= NARROW["width"] and close["y"] < download["y"]
            page.screenshot(path=str(evidence / "chromium-narrow-light-brief.png"))
            page.locator('dialog.document-reader [data-reader-action="close"]').click()
            page.locator("dialog.document-reader").wait_for(state="detached")
            assert abs(_feed_scroll(page) - before) <= 1
        finally:
            chromium.close()

        webkit = playwright.webkit.launch()
        try:
            page = webkit.new_page(viewport=WIDE, color_scheme="light")
            requests = []
            page.on("request", lambda request: requests.append((request.url, request.headers.get("range"))))
            page.goto(url, wait_until="domcontentloaded")
            page.locator(".chat-file-card").filter(has_text="brief.md").wait_for(state="visible", timeout=30_000)
            before = _place(page, "brief.md")
            state = _open(page, "brief.md", ready=".document-reader-markdown")
            _check_brief(state)
            assert state["active"]
            assert page.evaluate("() => getComputedStyle(document.querySelector('.document-reader-body')).outlineStyle") == "none"
            page.screenshot(path=str(evidence / "webkit-wide-light-brief.png"))
            _close_with_escape(page)
            assert page.evaluate("() => document.activeElement?.closest('.chat-file-card')?.textContent.includes('brief.md')")
            assert abs(_feed_scroll(page) - before) <= 1
            state = _open(page, "big.md", ready=".document-reader-markdown")
            assert "Showing the first 1.0 MB of 1.5 MB" in state["notice"] and "UTF-8" not in state["notice"]
            _close_with_escape(page)
            page.set_viewport_size(NARROW)
            page.emulate_media(color_scheme="dark")
            state = _open(page, "notes.txt", ready=".document-reader-source")
            assert state["source"] == NOTES
            assert [round(value) for value in state["rect"]] == [0, 0, NARROW["width"], NARROW["height"]], state["rect"]
            page.screenshot(path=str(evidence / "webkit-narrow-dark-notes.png"))
            _close_with_escape(page)
            state = _open(page, "brief.md", ready=".document-reader-markdown")
            assert any(state["tables"]) and state["bodyOverflowX"] <= 1
            page.screenshot(path=str(evidence / "webkit-narrow-dark-brief.png"))
            _close_with_escape(page)
        finally:
            webkit.close()


PROJECT_FILES = {"room-brief.md": BRIEF.encode(), "room-notes.txt": NOTES.encode()}

LEFT_BEHIND = """room => ({
    readers: document.querySelectorAll('dialog.document-reader').length,
    open_dialogs: document.querySelectorAll('dialog[open]').length,
    room: Boolean(document.querySelector(room)),
    focus_connected: !document.activeElement || document.activeElement.isConnected,
})"""

OPEN_ROOM = """project => window.dispatchEvent(new CustomEvent('ouro:open-project', {detail: {project}}))"""


def _enter_room(page, project):
    """Open a Project room from the navigation column, as the owner does."""
    row = page.locator(f'.nav-project-row[data-project-id="{project["id"]}"]')
    row.wait_for(state="attached", timeout=30_000)
    toggle = page.locator("#page-chat [data-mobile-nav-toggle]")
    if toggle.is_visible() and not page.locator("#primary-sidebar").evaluate("node => node.classList.contains('open')"):
        toggle.click()
    row.click()
    feed = f'#pchat-{project["id"]}-messages'
    page.locator(feed).wait_for(state="visible", timeout=30_000)
    return feed


def _await_abort(page, failed, url):
    for _ in range(60):
        if url in failed:
            return True
        page.wait_for_timeout(50)
    return False


def _release(page, held):
    for route in held:
        try:
            route.continue_()
        except Exception:  # an aborted request has no route left to continue
            pass
    held.clear()
    page.unroute("**/artifacts/**")


@pytest.mark.ui_browser
@pytest.mark.serial
def test_project_room_reader_survives_reload_and_closes_with_its_room(direct_server_with_data, monkeypatch, tmp_path):  # noqa: F811
    """A Project room's delivered documents read live and after a reload; leaving the room
    destroys its chat, which aborts a pending read and removes an open reader, and the room
    reopens to a working reader. Leaving goes through the app's own `ouro:open-project`
    navigation (what a Project reference does): a modal reader makes the panel's own Close inert."""
    from playwright.sync_api import sync_playwright
    from tests import fixtures_mock_llm

    root = direct_server_with_data["data_dir"]
    url = direct_server_with_data["url"]
    evidence = Path(os.environ.get("OUROBOROS_UI_EVIDENCE_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    source = root / "room-src"
    source.mkdir()
    paths = []
    for name, data in PROJECT_FILES.items():
        (source / name).write_bytes(data)
        paths.append(source / name)
    calls = []
    monkeypatch.setattr(fixtures_mock_llm._Handler, "do_POST", _send_files_mock(paths, calls))
    room = _api(url, "POST", "/api/projects", {"name": "Reader room"})["project"]
    other = _api(url, "POST", "/api/projects", {"name": "Other room"})["project"]
    assert int(room["chat_id"]) not in (1, int(other["chat_id"])), (room, other)
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        try:
            page = chromium.new_page(viewport=WIDE, color_scheme="dark")
            page.add_init_script(f"({_CAPTURE_TEST_SOCKET})()")
            frames, requests, failed = {}, [], []

            def capture(payload):
                value = json.loads(payload) if isinstance(payload, str) and '"document"' in payload else {}
                if value.get("type") == "document":
                    frames.setdefault(value["filename"], value)

            page.on("websocket", lambda socket: socket.on("framereceived", capture))
            page.on("request", lambda request: requests.append((request.url, request.headers.get("range"))))
            page.on("requestfailed", lambda request: failed.append(request.url))
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_function("() => window.__testSockets?.some(socket => socket.readyState === WebSocket.OPEN)")
            feed = _enter_room(page, room)
            page.locator(f'[id="pchat-{room["id"]}-input"]').fill("Send me the room documents.")
            page.locator(f'[id="pchat-{room["id"]}-send"]').click()
            for name in PROJECT_FILES:
                page.locator(f"{feed} .chat-file-card").filter(has_text=name).wait_for(state="visible", timeout=60_000)
            assert sorted(calls) == sorted(PROJECT_FILES) and sorted(frames) == sorted(PROJECT_FILES)
            assert {int(frame["chat_id"]) for frame in frames.values()} == {int(room["chat_id"])}, frames
            assert page.locator("#chat-messages .chat-file-card").count() == 0, "the room's files stay in the room"
            artifact = {name: url + frame["download_url"] for name, frame in frames.items()}

            # Live, in the room: the inline delivered bytes.
            before = _place(page, "room-brief.md", feed)
            requests.clear()
            state = _open(page, "room-brief.md", ready=".document-reader-markdown", feed=feed)
            _check_brief(state, "room-brief.md")
            assert state["active"]
            live_markdown = state["markdown"]
            assert not [entry for entry in requests if "/artifacts/" in entry[0] or "/api/files/" in entry[0]], requests
            page.screenshot(path=str(evidence / "chromium-wide-dark-project-brief.png"))
            _close_with_escape(page)
            assert page.evaluate(f"() => document.activeElement?.closest('{feed} .chat-file-card')?.textContent.includes('room-brief.md')")
            assert abs(_feed_scroll(page, feed) - before) <= 1, "closing returns to the same place in the room"

            # Reload: the room rebuilds from history and reads the immutable artifact route.
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("() => window.__testSockets?.some(socket => socket.readyState === WebSocket.OPEN)")
            feed = _enter_room(page, room)
            page.locator(f"{feed} .chat-file-card").filter(has_text="room-brief.md").wait_for(state="visible", timeout=30_000)
            requests.clear()
            state = _open(page, "room-brief.md", ready=".document-reader-markdown", feed=feed)
            _check_brief(state, "room-brief.md")
            assert state["markdown"] == live_markdown, "replay shows the same delivered document"
            assert (artifact["room-brief.md"], None) in requests, requests
            _close_with_escape(page)

            # Leaving the room while a read is pending aborts it and leaves nothing behind.
            held = []
            page.route("**/artifacts/**", lambda route: held.append(route))
            page.locator(f"{feed} .chat-file-card").filter(has_text="room-notes.txt").click()
            page.locator("dialog.document-reader .document-reader-status.is-loading").wait_for(state="attached")
            failed.clear()
            page.evaluate(OPEN_ROOM, other)
            page.locator(f'#pchat-{other["id"]}-messages').wait_for(state="visible", timeout=30_000)
            assert _await_abort(page, failed, artifact["room-notes.txt"]), "leaving the room aborted the pending read"
            assert page.evaluate(LEFT_BEHIND, feed) == {
                "readers": 0, "open_dialogs": 0, "room": False, "focus_connected": True}
            _release(page, held)
            # Nothing modal is left over the app: the room now shown takes a click at once.
            page.locator(f'[id="pchat-{other["id"]}-input"]').click(timeout=5_000)

            # The room reopens to a working reader (now from history, so the artifact route).
            feed = _enter_room(page, room)
            requests.clear()
            state = _open(page, "room-notes.txt", ready=".document-reader-source", feed=feed)
            assert state["source"] == NOTES and state["title"] == "room-notes.txt"
            assert (artifact["room-notes.txt"], None) in requests, requests

            # Leaving with the document open closes it the same way.
            page.evaluate(OPEN_ROOM, other)
            page.locator(f'#pchat-{other["id"]}-messages').wait_for(state="visible", timeout=30_000)
            assert page.evaluate(LEFT_BEHIND, feed) == {
                "readers": 0, "open_dialogs": 0, "room": False, "focus_connected": True}
            page.locator(f'[id="pchat-{other["id"]}-input"]').click(timeout=5_000)
        finally:
            chromium.close()

        webkit = playwright.webkit.launch()
        try:
            page = webkit.new_page(viewport=NARROW, color_scheme="light")
            failed = []
            page.on("requestfailed", lambda request: failed.append(request.url))
            page.goto(url, wait_until="domcontentloaded")
            feed = _enter_room(page, room)
            page.locator(f"{feed} .chat-file-card").filter(has_text="room-brief.md").wait_for(state="visible", timeout=30_000)
            before = _place(page, "room-brief.md", feed)
            state = _open(page, "room-brief.md", ready=".document-reader-markdown", feed=feed)
            _check_brief(state, "room-brief.md")
            assert [round(value) for value in state["rect"]] == [0, 0, NARROW["width"], NARROW["height"]], state["rect"]
            page.screenshot(path=str(evidence / "webkit-narrow-light-project-brief.png"))
            _close_with_escape(page)
            assert page.evaluate(f"() => document.activeElement?.closest('{feed} .chat-file-card')?.textContent.includes('room-brief.md')")
            assert abs(_feed_scroll(page, feed) - before) <= 1

            held = []
            page.route("**/artifacts/**", lambda route: held.append(route))
            page.locator(f"{feed} .chat-file-card").filter(has_text="room-notes.txt").click()
            page.locator("dialog.document-reader .document-reader-status.is-loading").wait_for(state="attached")
            page.evaluate(OPEN_ROOM, other)
            page.locator(f'#pchat-{other["id"]}-messages').wait_for(state="visible", timeout=30_000)
            assert _await_abort(page, failed, artifact["room-notes.txt"])
            assert page.evaluate(LEFT_BEHIND, feed) == {
                "readers": 0, "open_dialogs": 0, "room": False, "focus_connected": True}
            _release(page, held)
            # On a phone the other room covers Main's menu: close it, then reopen the room.
            page.locator("#project-panel-close").click()
            feed = _enter_room(page, room)
            state = _open(page, "room-notes.txt", ready=".document-reader-source", feed=feed)
            assert state["source"] == NOTES
            _close_with_escape(page)
        finally:
            webkit.close()
