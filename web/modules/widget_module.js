/* Framed widget mounts: the extension-route iframe and the reviewed module srcdoc iframe
   (CSP/sandbox constants, the parent side of the one streaming bridge — fetch relay, abort,
   skill WebSocket event forwarding — plus the resize bridge and source-load timeout).
   widgets.js keeps the page host, the mountTab dispatcher, the card registry and the
   declarative renderer; every mount here returns its disposer to that dispatcher. */

import { apiFetch, extensionRoutePath, extensionRoutePrefix } from './api_client.js';
import { escapeHtmlAttr as escapeHtml } from './utils.js';
import { bridgeChunkBuffer, moduleBridgeScript, moduleResizeScript } from './widget_frame.js';
import { boundedNumber, WIDGET_DISPOSE_ACK_TIMEOUT_MS, WIDGET_REQUEST_TIMEOUT_MS } from './widget_job.js';

export const WIDGET_FRAME_DEFAULT_HEIGHT = 320;
export const WIDGET_FRAME_MAX_HEIGHT = 8192;
export const WIDGET_FRAME_BORDER_RESERVE = 2;

export function frameHeight(render, fallback = WIDGET_FRAME_DEFAULT_HEIGHT) {
    return boundedNumber(render?.height, fallback, WIDGET_FRAME_DEFAULT_HEIGHT, WIDGET_FRAME_MAX_HEIGHT);
}

function frameMaxHeight(render) {
    return boundedNumber(render?.max_height, WIDGET_FRAME_MAX_HEIGHT, WIDGET_FRAME_DEFAULT_HEIGHT, WIDGET_FRAME_MAX_HEIGHT);
}

// The declared frame height is the one measured value the frame (and the
// stopped card's facade standing in for it) carries as a custom property.
export function setFrameHeight(node, height) {
    if (!node) return;
    node.style.setProperty('--widget-frame-height', `${Math.ceil(height)}px`);
}

export function mountRouteIframeWidget(mount, tab, render) {
    const src = extensionRoutePath(tab.skill, render.route);
    if (!src) throw new Error('invalid widget iframe route');
    mount.innerHTML = `<iframe class="widgets-frame" sandbox="" src="${src}"></iframe>`;
    const iframe = mount.querySelector('iframe');
    setFrameHeight(iframe, frameHeight(render));
    let disposed = false;
    return () => {
        if (disposed) return;
        disposed = true;
        if (iframe?.parentNode === mount) iframe.remove();
    };
}

// `messageHandlers` is the page's Set of WebSocket message handlers (fanned from
// `ctx.ws.on('message')`); the frame's event subscription registers into it.
export async function mountModuleWidget(mount, tab, render, mountSignal = null, messageHandlers = null) {
    // Reviewed JS runs in an opaque iframe; the parent bridge is its only I/O
    // path and only reaches this skill's extension route prefix and this
    // skill's namespaced WebSocket events, preserving route IO without cookies.
    const entryName = String(render.entry).replace(/[^A-Za-z0-9._-]/g, '');
    const entryUrl = `${extensionRoutePrefix(tab.skill)}module/${encodeURIComponent(entryName)}`;
    const sourceController = new AbortController();
    const relayAbort = () => sourceController.abort();
    if (mountSignal?.aborted) sourceController.abort();
    else mountSignal?.addEventListener('abort', relayAbort, { once: true });
    let sourceTimedOut = false;
    const sourceTimeout = setTimeout(() => {
        sourceTimedOut = true;
        sourceController.abort();
    }, WIDGET_REQUEST_TIMEOUT_MS);
    let resp;
    let moduleSource;
    try {
        resp = await apiFetch(entryUrl, { cache: 'no-store', signal: sourceController.signal });
        moduleSource = await resp.text();
    } catch (error) {
        if (sourceTimedOut) {
            const timeoutError = new Error('widget module request timed out');
            timeoutError.code = 'WIDGET_REQUEST_TIMEOUT';
            timeoutError.retryable = true;
            throw timeoutError;
        }
        throw error;
    } finally {
        clearTimeout(sourceTimeout);
        mountSignal?.removeEventListener('abort', relayAbort);
    }
    if (mountSignal?.aborted) return;
    if (!resp.ok) {
        mount.innerHTML = `<div class="skills-load-error">module load failed: ${escapeHtml(moduleSource || `HTTP ${resp.status}`)}</div>`;
        return;
    }
    const expectedPrefix = extensionRoutePrefix(tab.skill);
    const wsPrefix = String(tab.ws_prefix || '').trim();
    const nonce = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const csp = [
        "default-src 'none'",
        "script-src 'unsafe-inline'",
        "style-src 'unsafe-inline'",
        "img-src data:",
    ].join('; ');
    const escapeScript = (value) => String(value || '')
        .replace(/<\/script/gi, '<\\/script')
        .replace(/<!--/g, '<\\!--');
    const autoHeight = render.height === undefined || render.height === null;
    const maxHeight = frameMaxHeight(render);
    const bridge = moduleBridgeScript(nonce);
    const resizeBridge = autoHeight
        ? moduleResizeScript(
            nonce, WIDGET_FRAME_DEFAULT_HEIGHT, maxHeight, WIDGET_FRAME_BORDER_RESERVE,
        )
        : '';
    const srcdoc = `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="${csp}"></head><body><div id="root"></div><script>${bridge}</script><script>${resizeBridge}</script><script>${escapeScript(moduleSource)}</script></body></html>`;
    // The document goes in through the `srcdoc` property (no attribute
    // escaping round-trip of a module-sized payload); the sandbox stays the
    // one token `allow-scripts` — nothing that would re-expose the SPA origin.
    const iframe = document.createElement('iframe');
    iframe.className = 'widgets-frame';
    iframe.setAttribute('sandbox', 'allow-scripts');
    iframe.srcdoc = srcdoc;
    mount.replaceChildren(iframe);
    let appliedHeight = frameHeight(render);
    setFrameHeight(iframe, appliedHeight);
    const pendingRequests = new Map();
    let disposed = false;
    let disposing = null;
    let onDisposed = null;
    const post = (message, transfer = []) => {
        if (!disposed) iframe.contentWindow?.postMessage({ ...message, nonce }, '*', transfer);
    };
    // The skill's namespaced WebSocket events — the same `ws_prefix` filter the
    // declarative `subscription` uses — forwarded only while the child subscribes.
    const onWsMessage = (msg) => {
        const type = String(msg?.type || '');
        if (!wsPrefix || !type.startsWith(wsPrefix)) return;
        post({ type: 'ouro-widget-event', event: type.slice(wsPrefix.length), data: msg.data ?? {} });
    };
    // One bridged fetch: the owning-prefix check, then the response streamed to
    // the child as frames — `headers` (status, status text, every header) first,
    // one `data` frame per body chunk as a transferred ArrayBuffer, then `end`;
    // `error` on any failure. HEAD and 204/205/304 answers have no body to pump.
    // No default timeout: `init.timeoutMs` is the author's opt-in bound;
    // `-fetch-abort`, dispose (after the ack window) and a vanished frame abort
    // through the same controller.
    const relayFetch = async (msg) => {
        const id = msg.id;
        const init = msg.init || {};
        const controller = new AbortController();
        pendingRequests.set(id, controller);
        let timedOut = false;
        const timeoutMs = Number(init.timeoutMs);
        const timer = Number.isFinite(timeoutMs) && timeoutMs > 0
            ? setTimeout(() => {
                timedOut = true;
                controller.abort();
            }, timeoutMs)
            : 0;
        const frame = (fields, transfer) => post({ type: 'ouro-widget-fetch-chunk', id, ...fields }, transfer);
        try {
            const parsed = new URL(String(msg.url || ''), window.location.origin);
            if (parsed.origin !== window.location.origin || !parsed.pathname.startsWith(expectedPrefix)) {
                throw new Error('module widget fetch outside extension route prefix');
            }
            const r = await apiFetch(parsed.pathname + parsed.search, {
                method: String(init.method || 'GET').toUpperCase(),
                headers: init.headers || {},
                body: init.body ?? undefined,
                credentials: 'same-origin',
                signal: controller.signal,
            });
            frame({ phase: 'headers', status: r.status, statusText: r.statusText, headers: Array.from(r.headers) });
            const reader = r.body?.getReader();
            while (reader) {
                if (!iframe.isConnected) controller.abort();
                const { done, value } = await reader.read();
                if (done) break;
                const chunk = bridgeChunkBuffer(value);
                frame({ phase: 'data', chunk }, [chunk]);
            }
            frame({ phase: 'end' });
        } catch (err) {
            frame({ phase: 'error', error: timedOut ? 'widget request timed out' : (err?.message || String(err)) });
        } finally {
            clearTimeout(timer);
            pendingRequests.delete(id);
        }
    };
    const onMessage = (event) => {
        if (disposed || !iframe || event.source !== iframe.contentWindow) return;
        const msg = event.data || {};
        if (msg.nonce !== nonce) return;
        if (msg.type === 'ouro-widget-disposed') {
            onDisposed?.();
            return;
        }
        if (msg.type === 'ouro-widget-resize') {
            if (!autoHeight) return;
            const measured = Number(msg.height);
            if (!Number.isFinite(measured) || measured <= 0) return;
            const nextHeight = Math.min(maxHeight, Math.max(WIDGET_FRAME_DEFAULT_HEIGHT, measured + WIDGET_FRAME_BORDER_RESERVE));
            if (nextHeight === appliedHeight) return;
            appliedHeight = nextHeight;
            setFrameHeight(iframe, appliedHeight);
            return;
        }
        if (msg.type === 'ouro-widget-events') {
            if (msg.op === 'subscribe') messageHandlers?.add(onWsMessage);
            else if (msg.op === 'unsubscribe') messageHandlers?.delete(onWsMessage);
            return;
        }
        if (msg.type === 'ouro-widget-fetch-abort') {
            pendingRequests.get(msg.id)?.abort();
            return;
        }
        if (msg.type === 'ouro-widget-fetch') relayFetch(msg);
    };
    window.addEventListener('message', onMessage);
    // Ordered stop with acknowledgement: post the dispose message, keep
    // answering bridged fetches the child's hooks issue meanwhile, and tear down
    // on `ouro-widget-disposed` or after WIDGET_DISPOSE_ACK_TIMEOUT_MS —
    // whichever comes first. The frame stays in its card until then; nothing
    // here blocks a page switch. Returns the settle promise; repeat calls share it.
    return () => {
        if (disposing) return disposing;
        disposing = new Promise((resolve) => {
            let ackTimer = 0;
            const finish = () => {
                if (disposed) return;
                disposed = true;
                clearTimeout(ackTimer);
                pendingRequests.forEach((controller) => controller.abort());
                pendingRequests.clear();
                messageHandlers?.delete(onWsMessage);
                window.removeEventListener('message', onMessage);
                if (iframe?.parentNode === mount) iframe.remove();
                resolve();
            };
            onDisposed = finish;
            ackTimer = setTimeout(finish, WIDGET_DISPOSE_ACK_TIMEOUT_MS);
            const child = iframe?.contentWindow;
            if (child) child.postMessage({ type: 'ouro-widget-dispose', nonce }, '*');
            else finish();
        });
        return disposing;
    };
}
