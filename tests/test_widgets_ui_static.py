"""Static contract checks for the Widgets page renderer."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _widgets_js() -> str:
    return (REPO_ROOT / "web" / "modules" / "widgets.js").read_text(
        encoding="utf-8"
    )


def _framed_widget_sources() -> str:
    """widgets.js (page host, dispatcher, declarative renderer) plus the framed
    mounts split out of it (widget_module.js), the in-frame bootstrap
    (widget_frame.js), the framed-card chrome (widget_card.js) and the card
    reorder handles (widget_reorder.js). Negative pins run against this union
    so the moved code never leaves their coverage."""
    return (
        _widgets_js()
        + _read("web/modules/widget_module.js")
        + _read("web/modules/widget_frame.js")
        + _read("web/modules/widget_card.js")
        + _read("web/modules/widget_reorder.js")
    )


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_widgets_support_declarative_schema_components():
    """Spot-check that widgets.js exposes the declarative schema entry point
    and a representative set of components. Trimmed in v5.15.x — the full
    type-marker enumeration (15+ entries) was brittle to schema evolution
    and added little signal over a smoke check. Security/lifecycle pins
    moved to the dedicated tests below (escape/sanitize, media source guard,
    download host helper, etc.)."""
    source = _widgets_js()
    assert "render.kind === 'declarative'" in source
    # Sentinel components — proof the declarative router is wired
    assert "type === 'form'" in source
    assert "type === 'action'" in source
    assert "type === 'table'" in source
    assert "type === 'markdown'" in source
    # Lifecycle / cleanup discipline
    assert "disposeMountedWidgets();" in source
    assert "let widgetsMounted = false;" in source
    assert "let renderGeneration = 0;" in source
    page_shown_branch = source.split("window.addEventListener('ouro:page-shown'")[1]
    assert "disposeMountedWidgets();" in page_shown_branch


def test_widgets_page_reads_cheap_list_and_reconciles_by_signature():
    """Widgets lifecycle phase 1: the page reads the passive ``GET /api/widgets``
    projection — never the fat ``/api/extensions`` catalogue, which stays the
    Skills page's read — paints the shell from the last known list before its
    first await, and after every fetch compares an order-independent list
    signature: unchanged → not one ``<article>`` is touched; changed → keyed
    patch (``web/modules/widget_list.js`` holds the pure helpers). The same
    sync runs on a visible ``extension_lifecycle`` event and on every WebSocket
    (re)connect, never on a timer. Refresh keeps the hard-reset semantics
    (dispose everything, refetch, rebuild all) and says so in its tooltip."""
    source = _widgets_js()
    helpers = _read("web/modules/widget_list.js")
    assert "apiClient.widgets()" in source
    assert "apiClient.extensions()" not in source
    assert "live.ui_tabs" not in source
    assert "live?.ui_tabs" not in source
    assert "from './widget_list.js'" in source
    assert "export function widgetTabsSignature" in helpers
    assert "export function planWidgetListPatch" in helpers
    assert "const signature = widgetTabsSignature(tabs);" in source
    assert "if (signature !== lastSignature) patchWidgetCards(list, lastTabs, tabs);" in source
    assert "ctx.ws.on('extension_lifecycle', reconcileWidgetList);" in source
    assert "ctx.ws.on('open', reconcileWidgetList);" in source
    assert "setInterval(" not in source
    # Entry paints the shell before the first await; Refresh is the hard reset:
    # it forgets every owner Stop and lets the ordered stops settle before it
    # rebuilds the cards (destroying a frame mid-flush would skip its ack window).
    assert source.index("paintShell(lastTabs);") < source.index("await syncWidgets(generation);")
    force_branch = source.split("if (force) {", 1)[1].split("}", 1)[0]
    assert "stoppedByOwner.clear();" in force_branch
    assert "await disposeMountedWidgets();" in force_branch
    assert "refreshBtn.addEventListener('click', () => render(true));" in source
    assert 'title="Reload the list and restart all widgets"' in source


def test_widgets_escape_and_sanitize_untrusted_content():
    """Widgets must reach the sanitised markdown helper through the v5.8.3-rc.5
    SSOT (``web/modules/utils.js::renderMarkdownSafe``); the DOMPurify
    allowlist itself moved to that module and is pinned by
    ``tests/test_web_utils_ssot.py::test_render_markdown_safe_strips_dangerous_tags_and_attrs``.
    Widgets-side this test now only verifies the import and the
    escapeHtml-around-untrusted-content discipline that remains local
    (table cells, JSON dumps).
    """
    source = _widgets_js()
    assert "renderMarkdownSafe" in source
    # Widgets must NOT redeclare the SSOT helper locally.
    assert "function renderMarkdownSafe" not in _framed_widget_sources(), (
        "widgets.js must use renderMarkdownSafe from utils.js (SSOT), not a local copy"
    )
    assert "escapeHtml(JSON.stringify(value, null, 2))" in source
    assert "renderTableCell(row, c)" in source
    assert "function safeTableHref" in source


def test_widgets_media_sources_are_constrained_to_extension_routes_or_data_urls():
    source = _widgets_js()
    assert "function safeMediaSrc" in source
    assert "effectiveTarget = ''" in source
    assert "state[effectiveTarget || spec.target || 'result']" in source
    assert "safeMediaSrc(tab, component, state, target)" in source
    assert "const route = spec.route || spec.api_route || '';" in source
    assert "extensionRoutePath(tab.skill, route, params)" in source
    assert "data:(image\\/" in source
    assert "parsed.pathname.startsWith(expectedPrefix)" in source
    assert "parsed.origin === window.location.origin" in source
    assert "javascript:" not in _framed_widget_sources()
    assert "`${treePath}.gallery.${idx}`, passiveTarget" in source


def test_widgets_downloads_use_host_handler_not_navigation():
    source = _widgets_js()
    helper = _read("web/modules/ui_helpers.js")
    assert "data-widget-download-url" in source
    assert "event.preventDefault();" in source
    assert "downloadViaHostBridge(" in source
    assert "download_file_to_downloads" in helper
    assert "URL.createObjectURL" in helper
    framed = _framed_widget_sources()
    assert "window.location.href" not in framed
    assert "window.location.assign" not in framed
    assert '<a class="btn btn-default" href' not in framed


def test_widgets_treat_head_as_no_body_request():
    source = _widgets_js()
    assert "const noBody = method === 'GET' || method === 'HEAD';" in source
    assert "const init = noBody" in source


def test_widgets_keep_iframe_sandbox_locked_down():
    """The legacy ``kind: "iframe"`` widget surface mounts an extension
    route inside a <iframe> with the *empty* sandbox attribute (no
    permissions at all). v5.7.0 added ``kind: "module"``, which mounts
    extension-supplied JS inside a separate <iframe srcdoc> with
    ``sandbox="allow-scripts"`` BUT no ``allow-same-origin`` token —
    so the iframe is still an opaque origin (no SPA cookie / storage
    access) and is further constrained by a strict CSP. We check both
    invariants here:

    1. The legacy iframe path still uses the empty sandbox.
    2. The module iframe path adds ``allow-scripts`` but never adds
       ``allow-same-origin`` (the only token that would re-expose
       parent storage).
    """
    source = _framed_widget_sources()
    assert 'sandbox=""' in source
    # ``allow-scripts`` is now legitimately present, but only inside the
    # ``kind === 'module'`` branch. The dangerous combined sandbox token
    # must never appear in an actual iframe attribute.
    assert 'sandbox="allow-scripts"' in source
    assert 'sandbox="allow-scripts allow-same-origin"' not in source
    assert 'sandbox="allow-scripts allow-forms allow-same-origin"' not in source
    assert "render.kind === 'module'" in source
    # Verify the module iframe carries a CSP that does NOT grant network
    # access directly. The parent injects a postMessage fetch bridge instead,
    # restricted to /api/extensions/<skill>/... from the parent side.
    assert "default-src 'none'" in source
    assert "script-src 'unsafe-inline'" in source
    assert "OuroborosWidget = { fetch: window.fetch }" in source
    assert "module widget fetch outside extension route prefix" in source


def test_widgets_frame_geometry_and_teardown_contract():
    source = _framed_widget_sources()
    style = _read("web/style.css")
    assert "--widget-frame-height" in source
    assert "height: var(--widget-frame-height, 320px);" in style
    assert "type: 'ouro-widget-resize'" in source
    assert "new ResizeObserver(report)" in source
    assert "box.bottom - bodyTop + bodyBottomSpacing" in source
    assert "fixedViewportBody" in source
    assert 'scrolling="no"' not in source
    assert "syncModuleFrameScrolling" not in source
    assert "getPropertyValue('overflow-y')" in source
    assert "getPropertyPriority('overflow-y')" in source
    assert "style.setProperty('overflow-y', 'hidden', 'important')" in source
    assert "style.setProperty('overflow-x'" not in source
    assert source.index("setVerticalOverflowSuppressed(true);") < source.index("const report = () =>")
    assert "setVerticalOverflowSuppressed(outerHeight <" in source
    assert "setVerticalOverflowSuppressed(false);" in source
    assert "nonce, WIDGET_FRAME_DEFAULT_HEIGHT, maxHeight, WIDGET_FRAME_BORDER_RESERVE," in source
    assert source.index("<script>${resizeBridge}</script>") < source.index("<script>${escapeScript(moduleSource)}</script>")
    assert "ouro-widget-dispose" in source
    assert "if (iframe?.parentNode === mount) iframe.remove();" in source
    assert "pendingRequests.forEach((controller) => controller.abort());" in source
    assert "if (!isCurrent())" in source
    assert "WIDGET_FRAME_MAX_HEIGHT = 8192" in source
    assert "widget module request timed out" in source
    assert source.index("moduleSource = await resp.text();") < source.index("clearTimeout(sourceTimeout);")
    assert "widgetMountControllers.forEach((controller) => controller.abort());" in source


def test_widgets_framed_dispose_is_ordered_and_acknowledged():
    """Widgets lifecycle phase 2, both sides of the frame. Child
    (``widget_frame.js``): on ``ouro-widget-dispose`` every registered hook
    runs first — async hooks are awaited, the fetch bridge stays live for
    them — then the bootstrap posts ``ouro-widget-disposed`` and only then
    rejects pending fetches and removes its listener. Parent
    (``widget_module.js``): the disposer posts the dispose message, keeps
    ``onMessage`` answering bridged fetches, and the abort → unlisten →
    ``iframe.remove()`` tail runs from ``finish`` on the acknowledgement or
    after ``WIDGET_DISPOSE_ACK_TIMEOUT_MS`` (1 s, beside the 25 s request
    timeout) — asynchronously, never blocking a page switch. The page keeps
    one settle promise per key so a remount waits for the pending stop."""
    child = _read("web/modules/widget_frame.js")
    parent = _read("web/modules/widget_module.js")
    jobs = _read("web/modules/widget_job.js")
    page = _widgets_js()
    assert "export const WIDGET_DISPOSE_ACK_TIMEOUT_MS = 1000;" in jobs
    assert jobs.index("WIDGET_REQUEST_TIMEOUT_MS = 25000") < jobs.index("WIDGET_DISPOSE_ACK_TIMEOUT_MS = 1000")
    # Child: hooks (awaited) → ack → reject pending → unlisten.
    dispose_body = child.split("const dispose = async () =>", 1)[1].split("const onMessage", 1)[0]
    assert "await Promise.allSettled(hooks.map((fn) => Promise.resolve().then(fn)));" in dispose_body
    assert dispose_body.index("Promise.allSettled") < dispose_body.index("type: 'ouro-widget-disposed'")
    assert dispose_body.index("type: 'ouro-widget-disposed'") < dispose_body.index("reject(new Error('widget disposed'))")
    assert dispose_body.index("reject(new Error('widget disposed'))") < dispose_body.index("window.removeEventListener('message', onMessage);")
    # The bridge answers during the hooks: fetch is refused only once `disposed`.
    assert "if (msg.type !== 'ouro-widget-fetch-result' || disposed) return;" in child
    # Parent: the old synchronous tail is now the post-ack `finish`.
    assert "if (msg.type === 'ouro-widget-disposed') {" in parent
    tail = parent.split("const finish = () =>", 1)[1]
    assert tail.index("pendingRequests.forEach((controller) => controller.abort());") < tail.index("window.removeEventListener('message', onMessage);")
    assert tail.index("window.removeEventListener('message', onMessage);") < tail.index("if (iframe?.parentNode === mount) iframe.remove();")
    assert "onDisposed = finish;" in tail
    assert "setTimeout(finish, WIDGET_DISPOSE_ACK_TIMEOUT_MS)" in tail
    assert "postMessage({ type: 'ouro-widget-dispose', nonce }, '*');" in tail
    assert "if (disposing) return disposing;" in parent
    # Page: one settle promise per key; a remount and the facade wait for it.
    assert "const widgetDisposing = new Map();" in page
    assert "if (settling) await settling;" in page
    assert "await widgetDisposing.get(key);" in page
    assert "return Promise.allSettled(Array.from(widgetDisposing.values()));" in page


def test_widgets_launch_policy_controls_and_stop_suppression():
    """Widgets lifecycle phase 2, host side. Framed (module / route-iframe)
    cards carry exactly one primary control (Start / Stop) plus a secondary
    launch-policy menu built on the Skills card menu primitive; declarative
    cards get neither. The effective policy is owner override > author
    ``render.start`` > kind default (``widget_card.js``, node-tested). A card
    that is not to run shows a facade at the declared frame height through
    the frame's own custom property. Owner Stop is remembered for the page
    session only; Start, a policy change to Auto / Keep running, and Refresh
    forget it. A vanished card is stopped in order and evicts its session
    state. Retain is accepted and behaves as auto until the keep-alive phase."""
    page = _widgets_js()
    card = _read("web/modules/widget_card.js")
    style = _read("web/style.css")
    assert "export function effectiveStartMode(tab, prefs)" in card
    assert "const KIND_DEFAULT_START = { declarative: 'auto', module: 'manual', iframe: 'manual' };" in card
    assert "if (!isFramedWidget(tab)) return '';" in card
    assert card.count("btn btn-primary") == 1
    assert 'role="menuitemradio"' in card
    assert '<dialog class="skills-card-menu-dialog" role="menu"' in card
    assert 'class="skills-card-menu-trigger"' in card
    assert '<span class="ui-status" data-tone="neutral" data-widget-status hidden>' in card
    assert "setFrameHeight(mount.firstElementChild, frameHeight(tab.render || {}));" in card
    assert ".widgets-facade {" in style
    assert "height: var(--widget-frame-height, 320px);" in style.split(".widgets-facade {", 1)[1].split("}", 1)[0]
    assert ".widgets-card-controls .ui-status[data-tone]::before" in style
    # Page: policy gate, suppression, owner controls, whole-map persistence.
    assert "const stoppedByOwner = new Set();" in page
    assert "effectiveStartMode(tab, uiPreferences) !== 'manual'" in page
    assert "&& !stoppedByOwner.has(widgetKey(tab));" in page
    assert "if (isFramedWidget(tab) && !startsOnShow(tab)) await settleStopped(card, tab);" in page
    assert "stoppedByOwner.add(widgetKey(tab));" in page
    assert "stoppedByOwner.delete(widgetKey(tab));" in page
    assert "apiClient.saveUiPreferences({ widget_start_mode: next })" in page
    assert "const next = withWidgetStartMode(current, key, mode);" in page
    assert "bindWidgetCardMenus(list, setWidgetStartMode);" in page
    assert "event.target.closest('[data-widget-power]')" in page
    # Force-stop + eviction on a vanished card; the frame keeps its ack window.
    removed_branch = page.split("for (const key of plan.removed) {", 1)[1].split("let anchor = null;", 1)[0]
    assert "disposeWidgetByKey(key)" in removed_branch
    assert "widgetSessionState.delete(key);" in removed_branch
    assert "stoppedByOwner.delete(key);" in removed_branch
    assert "card.setAttribute('data-widget-removed', '');" in removed_branch
    assert "localStorage" not in _framed_widget_sources()


def test_widgets_job_poll_retries_transient_transport_without_dropping_id():
    source = _widgets_js()
    assert "error.retryable = resp.status === 408" in source
    assert "isRetryableWidgetError(err) && ticks < maxTicks" in source
    assert "classifyWidgetJobStatus" in source
    assert "invalid job status response" in source
    assert "status[target] = 'loading';" in source
    assert "schedule(pollJob, intervalMs);" in source
    assert "delete componentState[`job:${key}`];" in source


def test_widgets_use_design_radius_tokens():
    style = (REPO_ROOT / "web" / "style.css").read_text(encoding="utf-8")
    block_start = style.index(".widget-field input,")
    block_end = style.index("}", block_start)
    block = style[block_start:block_end]
    assert "border-radius: var(--radius-sm);" in block
    assert "border-radius: 9px;" not in block


def test_widgets_refresh_button_shows_loading_state():
    source = _widgets_js()
    css = (REPO_ROOT / "web" / "style.css").read_text(encoding="utf-8")

    assert "refreshBtn.classList.add('is-loading')" in source
    assert "refreshBtn.classList.remove('is-loading')" in source
    assert "refreshBtn.disabled = true" in source
    assert "#widgets-refresh.is-loading::after" in css


def test_widgets_cards_do_not_stretch_to_row_height():
    source = _widgets_js()
    css = (REPO_ROOT / "web" / "style.css").read_text(encoding="utf-8")
    masonry = (REPO_ROOT / "web" / "modules" / "masonry.js").read_text(encoding="utf-8")
    assert "const span = Number(tab.span || tab.grid_span || 1);" in source
    assert "widgets-card-span-2" in source
    assert "applyMasonry(list)" in source
    assert "function layout(container, config)" in masonry
    assert "item.classList.contains(spanClass) ? 2 : 1" in masonry
    assert "Math.min(desiredColumns, availableColumns)" in masonry
    assert "itemResizeObserver" in masonry
    assert "observeItems()" in masonry
    widgets_block = css.split(".widgets-list {", 1)[1].split("}", 1)[0]
    assert "display: grid" not in widgets_block
    assert "position: relative;" in widgets_block
    assert ".widgets-card-span-2" in css


def test_widget_form_label_is_accessible_heading_fallback():
    source = _widgets_js()
    assert "const heading = component.title || component.label || '';" in source
    assert 'aria-label="${escapeHtml(heading)}"' in source
    assert "heading ? `<h4>${escapeHtml(heading)}</h4>` : ''" in source


def test_widget_json_wraps_inside_its_host_card():
    style = _read("web/style.css")
    json_block = style.split(".widget-json pre {", 1)[1].split("}", 1)[0]
    assert "max-width: 100%;" in json_block
    assert "max-height: min(360px, 50vh);" in json_block
    assert "overflow: auto;" in json_block
    assert "white-space: pre-wrap;" in json_block
    assert "overflow-wrap: anywhere;" in json_block


def test_widgets_card_order_is_owner_ui_preference():
    """The reorder handles moved unchanged into ``widget_reorder.js`` (phase 2
    made room in widgets.js); the card markup and the preference write stay
    in the page host."""
    source = _widgets_js()
    reorder = _read("web/modules/widget_reorder.js")
    css = (REPO_ROOT / "web" / "style.css").read_text(encoding="utf-8")
    api_client = (REPO_ROOT / "web" / "modules" / "api_client.js").read_text(encoding="utf-8")

    assert 'data-widget-reorder-handle' in source
    assert "from './widget_reorder.js'" in source
    assert "export function sortTabsByWidgetOrder" in reorder
    assert "originalIndex" in reorder
    assert "return a.originalIndex - b.originalIndex;" in reorder
    assert "Move widget: drag or use arrow keys" in source
    assert "handle.addEventListener('keydown'" in reorder
    assert "event.key === 'ArrowUp'" in reorder
    assert "apiClient.uiPreferences()" in source
    assert "apiClient.saveUiPreferences({ widget_order: normalized })" in source
    assert "currentWidgetOrderFromDom(list)" in reorder
    assert ".widgets-card-drag" in css
    assert ".widgets-card.drag-over" in css
    assert "uiPreferences: () => fetchJson('/api/ui/preferences'" in api_client
    assert "saveUiPreferences: (payload) => jsonPost('/api/ui/preferences', payload)" in api_client


def test_widgets_inline_card_host_path_removed():
    source = _widgets_js()
    framed = _framed_widget_sources()
    assert "render.kind === 'inline_card'" not in framed
    assert "skill-widget-weather" not in framed
    assert "const saved = widgetSessionState.get(persistenceKey) || {};" in source


def test_widgets_v5_7_0_new_components_render():
    """v5.7.0 host-owned declarative components: ``map`` (Leaflet-ready
    fallback list), ``calendar`` (host SVG-style row list), ``kanban``
    (HTML5 drag with on_move POST). All three must be present in the
    declarative renderer so authors can reference them in widgets, and
    none of them may bring skill-supplied JS into the SPA origin."""
    source = _widgets_js()
    assert "type === 'map'" in source
    assert "type === 'calendar'" in source
    assert "type === 'kanban'" in source
    # Module / arbitrary <script> from the skill must NEVER be inserted
    # into the host origin. ``data-widget-map-config`` carries the spec
    # as JSON in a data attribute (host renders); no runtime eval of
    # extension JS is acceptable in any of the new component renderers.
    assert "data-widget-map-config" in source
    assert "widget-kanban-card" in source


def test_widgets_render_subscription_children():
    source = _widgets_js()
    assert "type === 'subscription'" in source
    assert "component.render" in source
    assert "widget-subscription-render" in source
    assert "inheritedTarget = ''" in source
    assert "component.target || inheritedTarget || 'result'" in source
    assert "renderComponent(tab, child, view, `${treePath}.render.${idx}`, target)" in source
    assert "const passiveTarget = inheritedTarget ? target : '';" in source
    assert "value_key" in source
    assert "items_key" in source
    assert "route_prefix" in source
    assert "type === 'key_value'" in source


def test_widgets_schema_v1_composition_uses_stable_tree_keys():
    source = _widgets_js()
    assert "function componentIdentity" in source
    assert "function indexComponentTree" in source
    assert "type === 'group'" in source
    assert "type === 'metric'" in source
    assert "type === 'callout'" in source
    assert "visibleKeys.forEach((key)" in source
    assert "components[Number(" not in _framed_widget_sources()
    assert "data-widget-kanban-key" in source


def test_widgets_forms_charts_and_kanban_keep_host_owned_contracts():
    source = _widgets_js()
    helper = _read("web/modules/ui_helpers.js")
    assert "renderSafeField(" in source
    assert "collectSafeFieldValues(" in source
    assert "includePasswords: false" in source
    assert "pendingActions.has(key)" in source
    assert "spanGaps: false" in source
    assert "finiteChartValue" in source
    assert "aria-label=" in source
    assert "renderChartDataTable" in source
    assert "data-widget-kanban-move" in source
    assert "widget-kanban-empty" in source
    assert "mount.querySelectorAll('[data-widget-kanban-key]')" in source
    assert "{ card_id: cardId, column_id: columnId }" in source
    assert "SAFE_FIELD_TYPES" in helper
    assert "autocomplete=\"new-password\"" in helper


def test_widgets_responsive_design_system_styles_are_host_owned():
    style = _read("web/style.css")
    assert ".widget-group-grid" in style
    assert ".widget-metric" in style
    assert ".widget-callout" in style
    assert ".widget-form-fields.widget-grid-cols-4" in style
    assert "content: attr(data-label);" in style
    assert ".widget-kanban-move" in style
    assert ".widget-kanban-col.is-empty" in style
    # The widget narrow block is found by its CONTENT, not by being the last
    # `@media (max-width: 640px)` in the file. Position was never the fact under
    # test, and any surface that later adds its own 640px block (the Agents tab's
    # account rows did) would silently steal this assertion and fail it.
    blocks = style.split("@media (max-width: 640px) {")[1:]
    narrow = [b for b in blocks if ".widget-kanban-col.is-empty" in b]
    assert narrow, "no @media (max-width: 640px) block carries the widget kanban rules"
    assert any("min-height: 0;" in b for b in narrow)
    assert any("padding-block: 8px;" in b for b in narrow)
    assert ".widget-group-components > * { margin-top: 0; }" in style
    assert "repeat(auto-fit, minmax(min(220px, 100%), 1fr))" in style
    assert ".widget-group-grid > .widget-group-components > :is(" in style


def test_widget_public_tones_share_the_host_normalizer_and_canonical_css():
    source = _widgets_js()
    helper = _read("web/modules/ui_helpers.js")
    style = _read("web/style.css")
    assert "function widgetTone" not in _framed_widget_sources()
    assert "normalizeTone(component.tone)" in source
    assert "normalizeTone(component.tone, 'info')" in source
    assert "success: 'ok'" in helper
    assert "warning: 'warn'" in helper
    assert "neutral: 'muted'" in helper
    assert '.widget-metric[data-tone="ok"], .widget-callout[data-tone="ok"]' in style
    assert '.widget-metric[data-tone="warn"], .widget-callout[data-tone="warn"]' in style


def test_widget_metrics_share_the_standard_empty_value_and_numeric_formatter():
    source = _widgets_js()
    assert "const numericValue = text ? Number(text) : Number.NaN;" in source
    assert "!Number.isNaN(numericValue) && !Number.isFinite(numericValue)" in source
    assert "const structured = raw !== null && typeof raw === 'object';" in source
    assert "nonFiniteText" in source
    assert "typeof raw === 'number' || numericText ? formatNumber" in source
