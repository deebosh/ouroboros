// The onboarding wizard boots and renders under a bare DOM stand-in.
//
// The wizard is an IIFE: importing the module runs `render()` once, and the
// save path runs it again before the completion POST. The dead call that
// stranded every fresh desktop install on "Saving..." (issues #557/#607)
// lived at the END of `render()` — the first paint succeeded because the DOM
// was already written, so nothing short of executing the function saw the
// ReferenceError. This test executes it: a Proxy stands in for `document` and
// `window` (every element exists, every method is a no-op, every value is
// inert), so the only way the import can throw is a real defect in the
// module's own code — an undeclared name, a bad destructure, a null
// dereference on state the wizard itself owns.
//
// This is the runtime half of the class gate; `no_undef.test.js` is the static
// half (it sees every module and every path, this sees the wizard's boot path
// with real control flow).

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

// The SAME bootstrap the server injects into the page (`build_setup_bootstrap`
// on an empty settings document); tests/test_onboarding_wizard.py pins the
// fixture byte-for-byte against the live Python contract, so the wizard here
// walks the real step order with the real field lists.
const BOOTSTRAP = JSON.parse(readFileSync(
    new URL('./fixtures/onboarding_bootstrap.json', import.meta.url), 'utf8',
));

function inertElement() {
    const listeners = new Map();
    const target = {
        innerHTML: '', textContent: '', value: '', hidden: false, disabled: false, checked: false,
        dataset: {}, style: {}, classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
        children: [], childNodes: [], attributes: [],
        addEventListener(type, fn) { listeners.set(type, fn); },
        removeEventListener() {},
        dispatchEvent() { return true; },
        setAttribute() {}, removeAttribute() {}, getAttribute: () => null, hasAttribute: () => false,
        appendChild: (c) => c, removeChild: (c) => c, replaceChildren() {}, insertBefore: (c) => c,
        querySelector: () => inertElement(), querySelectorAll: () => [], closest: () => null,
        contains: () => false, focus() {}, blur() {}, click() {}, scrollIntoView() {},
        getBoundingClientRect: () => ({ top: 0, left: 0, width: 0, height: 0, right: 0, bottom: 0 }),
        matches: () => false, remove() {},
    };
    return new Proxy(target, {
        get(obj, prop) {
            if (prop in obj) return obj[prop];
            if (typeof prop === 'symbol') return undefined;
            // Unknown property: a callable that also behaves like an inert element.
            return Object.assign(() => inertElement(), { then: undefined });
        },
        set(obj, prop, value) { obj[prop] = value; return true; },
    });
}

function inertDocument() {
    const doc = inertElement();
    doc.body = inertElement();
    doc.documentElement = inertElement();
    doc.head = inertElement();
    doc.getElementById = () => inertElement();
    doc.createElement = () => inertElement();
    doc.createTextNode = (text) => ({ textContent: String(text) });
    doc.createDocumentFragment = () => inertElement();
    doc.activeElement = null;
    doc.readyState = 'complete';
    doc.cookie = '';
    doc.title = '';
    return doc;
}

// One import per step: the wizard renders `stepOrder[0]` at boot, so the
// bootstrap is rotated to put each step first (cache-busting query on the
// import URL — an ES module is evaluated once per URL) and every step's
// renderer executes, the summary/save re-render included — the surface
// #557/#607 actually broke on.
for (const [index, step] of BOOTSTRAP.stepOrder.entries()) {
test(`importing the onboarding wizard renders the '${step}' step without throwing`, async () => {
    const rotated = { ...BOOTSTRAP, stepOrder: [...BOOTSTRAP.stepOrder.slice(index), ...BOOTSTRAP.stepOrder.slice(0, index)] };
    const doc = inertDocument();
    const win = new Proxy({
        document: doc,
        location: { origin: 'http://127.0.0.1:8765', href: 'http://127.0.0.1:8765/onboarding', search: '', hash: '', pathname: '/onboarding' },
        navigator: { userAgent: 'node', platform: 'node', clipboard: { writeText: async () => {} } },
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        __OURO_ONBOARDING_BOOTSTRAP__: rotated,
        addEventListener() {}, removeEventListener() {},
        setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
        requestAnimationFrame: (fn) => setTimeout(fn, 0), getComputedStyle: () => ({}),
        matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
        fetch: async () => ({ ok: true, status: 200, json: async () => ({}), text: async () => '' }),
        open() {}, scrollTo() {}, parent: null, pywebview: undefined,
    }, {
        get(obj, prop) { return prop in obj ? obj[prop] : undefined; },
        set(obj, prop, value) { obj[prop] = value; return true; },
    });
    // Node 22+ exposes some Web IDL globals (`navigator`) as getter-only
    // properties: a plain assignment throws before the wizard is imported.
    // Install every stand-in through defineProperty and restore the exact
    // prior descriptor afterwards, so the smoke runs the same on every Node
    // CI pins.
    const installed = {};
    const install = (name, value) => {
        installed[name] = Object.getOwnPropertyDescriptor(globalThis, name);
        Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
    };
    install('document', doc);
    install('window', win);
    install('navigator', win.navigator);
    install('localStorage', win.localStorage);
    install('location', win.location);
    install('fetch', win.fetch);
    install('requestAnimationFrame', win.requestAnimationFrame);
    install('setTimeout', win.setTimeout);
    install('setInterval', win.setInterval);
    install('clearTimeout', win.clearTimeout);
    install('clearInterval', win.clearInterval);
    try {
        // A ReferenceError here is the exact failure that shipped in 6.113.3–6.114.0.
        await import(`../modules/onboarding_wizard.js?step=${index}`);
    } finally {
        for (const [name, descriptor] of Object.entries(installed)) {
            if (descriptor) Object.defineProperty(globalThis, name, descriptor);
            else delete globalThis[name];
        }
    }
    assert.ok(true);
});
}
