/* Framed widget mounts: the extension-route iframe and the reviewed module srcdoc iframe
   (CSP/sandbox constants, parent fetch/resize bridge, source-load timeout). widgets.js keeps
   the page host, the mountTab dispatcher, the card registry and the declarative renderer;
   every mount here returns its disposer to that dispatcher. */

import { apiFetch, extensionRoutePath, extensionRoutePrefix } from './api_client.js';
import { escapeHtmlAttr as escapeHtml } from './utils.js';
import { moduleBridgeScript, moduleResizeScript } from './widget_frame.js';
import { boundedNumber, WIDGET_REQUEST_TIMEOUT_MS } from './widget_job.js';

export const WIDGET_FRAME_DEFAULT_HEIGHT = 320;
export const WIDGET_FRAME_MAX_HEIGHT = 8192;
export const WIDGET_FRAME_BORDER_RESERVE = 2;

function frameHeight(render, fallback = WIDGET_FRAME_DEFAULT_HEIGHT) {
    return boundedNumber(render?.height, fallback, WIDGET_FRAME_DEFAULT_HEIGHT, WIDGET_FRAME_MAX_HEIGHT);
}

function frameMaxHeight(render) {
    return boundedNumber(render?.max_height, WIDGET_FRAME_MAX_HEIGHT, WIDGET_FRAME_DEFAULT_HEIGHT, WIDGET_FRAME_MAX_HEIGHT);
}

function setFrameHeight(iframe, height) {
    if (!iframe) return;
    iframe.style.setProperty('--widget-frame-height', `${Math.ceil(height)}px`);
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

export async function mountModuleWidget(mount, tab, render, mountSignal = null) {
    // Reviewed JS runs in an opaque iframe; parent fetch bridge only allows
    // this skill's extension route prefix, preserving route IO without cookies.
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
    mount.innerHTML = `<iframe class="widgets-frame" sandbox="allow-scripts" srcdoc="${escapeHtml(srcdoc)}"></iframe>`;
    const iframe = mount.querySelector('iframe');
    let appliedHeight = frameHeight(render);
    setFrameHeight(iframe, appliedHeight);
    const pendingRequests = new Map();
    let disposed = false;
    const onMessage = async (event) => {
        if (disposed || !iframe || event.source !== iframe.contentWindow) return;
        const msg = event.data || {};
        if (msg.nonce !== nonce) return;
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
        if (msg.type !== 'ouro-widget-fetch') return;
        const controller = new AbortController();
        pendingRequests.set(msg.id, controller);
        try {
            const parsed = new URL(String(msg.url || ''), window.location.origin);
            if (parsed.origin !== window.location.origin || !parsed.pathname.startsWith(expectedPrefix)) {
                throw new Error('module widget fetch outside extension route prefix');
            }
            const r = await apiFetch(parsed.pathname + parsed.search, {
                method: String(msg.init?.method || 'GET').toUpperCase(),
                headers: msg.init?.headers || {},
                body: msg.init?.body || undefined,
                credentials: 'same-origin',
                signal: controller.signal,
            });
            const body = await r.text();
            if (disposed) return;
            iframe.contentWindow?.postMessage({
                type: 'ouro-widget-fetch-result',
                nonce,
                id: msg.id,
                status: r.status,
                headers: { 'content-type': r.headers.get('content-type') || '' },
                body,
            }, '*');
        } catch (err) {
            if (disposed) return;
            iframe.contentWindow?.postMessage({
                type: 'ouro-widget-fetch-result',
                nonce,
                id: msg.id,
                error: err.message || String(err),
            }, '*');
        } finally {
            pendingRequests.delete(msg.id);
        }
    };
    window.addEventListener('message', onMessage);
    return () => {
        if (disposed) return;
        disposed = true;
        pendingRequests.forEach((controller) => controller.abort());
        pendingRequests.clear();
        iframe?.contentWindow?.postMessage({ type: 'ouro-widget-dispose', nonce }, '*');
        window.removeEventListener('message', onMessage);
        if (iframe?.parentNode === mount) iframe.remove();
    };
}
