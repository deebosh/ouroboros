import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    createWidgetWidths, mergeWidgetOrder, moveWidgetKey, normalizeWidgetOrder, sortTabsByWidgetOrder,
} from '../modules/widget_reorder.js';

// Widgets lifecycle phase 3: a reorder is a pure move in the KEY order; the
// handles never move an <article> (a moved <iframe> reloads). The card widths
// (`createWidgetWidths`) change custom properties and `ui_preferences.widget_size` only.

test('moveWidgetKey moves a key to a clamped index and returns the same array when nothing changes', () => {
    const order = ['a', 'b', 'c', 'd'];
    assert.deepEqual(moveWidgetKey(order, 'b', 2), ['a', 'c', 'b', 'd']);
    assert.deepEqual(moveWidgetKey(order, 'b', 0), ['b', 'a', 'c', 'd']);
    assert.deepEqual(moveWidgetKey(order, 'c', 0), ['c', 'a', 'b', 'd']);
    assert.deepEqual(moveWidgetKey(order, 'a', Number.MAX_SAFE_INTEGER), ['b', 'c', 'd', 'a']);
    assert.deepEqual(moveWidgetKey(order, 'd', -5), ['d', 'a', 'b', 'c']);
    // Identity means "not moved": same slot, first card moved up, unknown key.
    assert.equal(moveWidgetKey(order, 'b', 1), order);
    assert.equal(moveWidgetKey(order, 'a', -1), order);
    assert.equal(moveWidgetKey(order, 'zzz', 0), order);
    assert.equal(moveWidgetKey([], 'a', 0).length, 0);
    // The input is never mutated.
    assert.deepEqual(order, ['a', 'b', 'c', 'd']);
});

test('a drop onto a target lands after a target the key was before, before a target it was after', () => {
    const order = ['a', 'b', 'c', 'd'];
    // Drag a onto c: a was before c → a lands after c.
    assert.deepEqual(moveWidgetKey(order, 'a', order.indexOf('c')), ['b', 'c', 'a', 'd']);
    // Drag d onto b: d was after b → d lands before b.
    assert.deepEqual(moveWidgetKey(order, 'd', order.indexOf('b')), ['a', 'd', 'b', 'c']);
});

test('normalizeWidgetOrder and sortTabsByWidgetOrder keep the phase-2 contract', () => {
    assert.deepEqual(normalizeWidgetOrder([' a ', '', 'b', 'a', null]), ['a', 'b']);
    assert.deepEqual(normalizeWidgetOrder('nope'), []);
    const tabs = [{ key: 'x' }, { key: 'y' }, { key: 'z' }];
    assert.deepEqual(sortTabsByWidgetOrder(tabs, ['z']).map((tab) => tab.key), ['z', 'x', 'y']);
});

const keysOf = (tabs) => tabs.map((tab) => tab.key);
const tabsOf = (...keys) => keys.map((key) => ({ key }));

test('a reorder of the shown cards keeps every stored key that is not on screen in its slot', () => {
    // Stored [A, H, B]; H's skill is off; the owner moves B before A.
    assert.deepEqual(mergeWidgetOrder(['a', 'h', 'b'], ['b', 'a']), ['b', 'h', 'a']);
    // Stored [A, B, C]; B is off; C moves up: B keeps the middle slot, never the end.
    assert.deepEqual(mergeWidgetOrder(['a', 'b', 'c'], ['c', 'a']), ['c', 'b', 'a']);
    // A shown card the stored order lacks takes a new slot at its end first.
    assert.deepEqual(mergeWidgetOrder(['a', 'h', 'b'], ['n', 'a', 'b']), ['n', 'h', 'a', 'b']);
    // Nothing stored yet: the shown order is the order.
    assert.deepEqual(mergeWidgetOrder([], ['c', 'a']), ['c', 'a']);
    assert.deepEqual(mergeWidgetOrder(null, ['c', 'a']), ['c', 'a']);
    // Both inputs are normalised, neither is mutated.
    const stored = [' a ', 'h', 'a', 'b'];
    assert.deepEqual(mergeWidgetOrder(stored, ['b', 'a', 'b']), ['b', 'h', 'a']);
    assert.deepEqual(stored, [' a ', 'h', 'a', 'b']);
});

test('disable a widget, reorder the others, enable it again: it is back in its old slot', () => {
    const stored = ['s:A', 's:B', 's:C', 's:D'];
    // B's skill is off: the board shows A, C, D, and the owner moves D before C.
    const shown = keysOf(sortTabsByWidgetOrder(tabsOf('s:A', 's:C', 's:D'), stored));
    const written = mergeWidgetOrder(stored, moveWidgetKey(shown, 's:D', shown.indexOf('s:C')));
    assert.deepEqual(written, ['s:A', 's:B', 's:D', 's:C']);
    // B's skill is on again: it stands between A and D, where it stood.
    assert.deepEqual(keysOf(sortTabsByWidgetOrder(tabsOf('s:A', 's:B', 's:C', 's:D'), written)), written);
});

test('a widget that appears joins the end, even when its key sorts before the cards on screen', () => {
    // The server lists by key; nothing is arranged yet and this window shows m, z.
    const listed = tabsOf('a:main', 'm:main', 'z:main');
    assert.deepEqual(keysOf(sortTabsByWidgetOrder(listed, [], ['m:main', 'z:main'])), ['m:main', 'z:main', 'a:main']);
    // The stored order still wins; the shown order places only the keys it lacks.
    assert.deepEqual(keysOf(sortTabsByWidgetOrder(listed, ['z:main'], ['m:main', 'z:main'])), ['z:main', 'm:main', 'a:main']);
    // A window's first list (nothing shown yet) keeps the listing order.
    assert.deepEqual(keysOf(sortTabsByWidgetOrder(listed, [])), ['a:main', 'm:main', 'z:main']);
});

test('the reorder module moves keys only: no node insertion or move API, no masonry import', () => {
    const source = readFileSync(new URL('../modules/widget_reorder.js', import.meta.url), 'utf8');
    for (const forbidden of ['.before(', '.after(', '.prepend(', '.append(', 'insertBefore', 'appendChild', 'replaceWith', "from './masonry.js'"]) {
        assert.equal(source.includes(forbidden), false, `widget_reorder.js must not use ${forbidden}`);
    }
    assert.match(source, /export function bindWidgetCardReorder\(list, currentOrder, onOrderChange\)/);
});

// --- card widths -------------------------------------------------------------

function listener() {
    const handlers = new Map();
    return {
        handlers,
        addEventListener(type, fn) { handlers.set(type, [...(handlers.get(type) || []), fn]); },
        removeEventListener(type, fn) { handlers.set(type, (handlers.get(type) || []).filter((item) => item !== fn)); },
        fire(type, event) { (handlers.get(type) || []).forEach((fn) => fn(event)); },
    };
}

function classes() {
    const set = new Set();
    return { set, add: (name) => set.add(name), remove: (name) => set.delete(name), contains: (name) => set.has(name) };
}

function board(keys = ['demo:a', 'demo:b'], { width = 1200 } = {}) {
    globalThis.requestAnimationFrame = () => 1;
    globalThis.cancelAnimationFrame = () => {};
    globalThis.ResizeObserver = class { observe() {} disconnect() {} };
    globalThis.getComputedStyle = () => ({ columnGap: '14px' });
    const doc = listener();
    const cards = keys.map((key) => {
        const props = new Map();
        const card = {
            dataset: { widgetKey: key },
            classList: classes(),
            isConnected: true,
            props,
            style: { getPropertyValue: (name) => props.get(name) || '', setProperty: (name, value) => props.set(name, value) },
            hasAttribute: () => false,
        };
        const handle = {
            ...listener(),
            captured: null,
            closest: (selector) => (selector === '[data-widget-key]' ? card : null),
            setPointerCapture(id) { this.captured = id; },
            hasPointerCapture(id) { return this.captured === id; },
            releasePointerCapture() { this.captured = null; },
        };
        card.handle = handle;
        return card;
    });
    const list = {
        dataset: {},
        classList: classes(),
        clientWidth: width,
        ownerDocument: doc,
        querySelectorAll: (selector) => (selector === '[data-widget-resize-handle]' ? cards.map((card) => card.handle) : cards),
    };
    const saves = [];
    const status = { textContent: '', dataset: { tone: 'neutral' } };
    let prefs = { widget_size: {} };
    const tabs = keys.map((key) => ({ key, span: 1 }));
    const widths = createWidgetWidths(list, {
        tabs: () => tabs,
        prefs: () => prefs,
        adopt(sizes) { prefs = { ...prefs, widget_size: sizes }; },
        save(payload) {
            let settle;
            const done = new Promise((resolve, reject) => { settle = { resolve, reject }; });
            saves.push({ payload, ...settle });
            return done;
        },
        status,
    });
    widths.relayout();
    return { list, cards, doc, saves, status, widths, prefs: () => prefs };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const key = (name, extra = {}) => ({ key: name, preventDefault() { this.prevented = true; }, ...extra });

test('a width from the menu shows at once, saves one write at a time and merges what lands meanwhile', async () => {
    const { cards, saves, widths, prefs } = board();
    assert.equal(cards[0].props.get('--widget-w'), '4', 'the author span is the starting width');
    widths.setWidth('demo:a', 12);
    assert.equal(cards[0].props.get('--widget-w'), '12');
    assert.deepEqual(prefs().widget_size, { 'demo:a': { w: 12, h: 0 } });
    await settle();
    assert.deepEqual(saves.map((save) => save.payload), [{ widget_size: { 'demo:a': { w: 12, h: 0 } } }]);
    // Three changes while the first write is out: one merged write follows it.
    widths.setWidth('demo:b', 6);
    widths.setWidth('demo:a', 8);
    widths.setWidth('demo:b', null);
    await settle();
    assert.equal(saves.length, 1, 'never two writes in flight');
    // A list read that began before these changes cannot undo them.
    assert.deepEqual(widths.readSizes({ 'demo:a': { w: 4 }, 'demo:b': { w: 12 }, 'other:c': { w: 6 } }), {
        'demo:a': { w: 8, h: 0 }, 'other:c': { w: 6, h: 0 },
    });
    saves[0].resolve({ ok: true });
    await settle();
    assert.deepEqual(saves[1].payload, { widget_size: { 'demo:b': null, 'demo:a': { w: 8, h: 0 } } });
    assert.equal(cards[1].props.get('--widget-w'), '4', 'Reset: back to the author span');
    saves[1].resolve({ ok: true });
    await settle();
    assert.deepEqual(widths.readSizes({ 'demo:a': { w: 8, h: 0 } }), { 'demo:a': { w: 8, h: 0 } });
});

test('a failed save stays visible, rides along with the next change and clears once saved', async () => {
    const { saves, status, widths } = board();
    widths.setWidth('demo:a', 6);
    await settle();
    saves[0].reject(new Error('HTTP 500'));
    await settle();
    assert.deepEqual([status.textContent, status.dataset.tone], ['Size not saved: HTTP 500', 'error']);
    assert.equal(saves.length, 1, 'nothing retries on its own');
    // A list read still shows the width that failed to save.
    assert.deepEqual(widths.readSizes({}), { 'demo:a': { w: 6, h: 0 } });
    widths.setWidth('demo:b', 8);
    await settle();
    assert.deepEqual(saves[1].payload, { widget_size: { 'demo:a': { w: 6, h: 0 }, 'demo:b': { w: 8, h: 0 } } });
    saves[1].resolve({ ok: true });
    await settle();
    assert.deepEqual([status.textContent, status.dataset.tone], ['', 'neutral']);
    assert.deepEqual(widths.readSizes({}), {});
});

test('the edge handle keys step the width and announce it; other keys and modifiers pass through', async () => {
    const { cards, saves, status } = board();
    const handle = cards[0].handle;
    const right = key('ArrowRight');
    handle.fire('keydown', right);
    assert.equal(right.prevented, true);
    assert.equal(cards[0].props.get('--widget-w'), '6');
    assert.equal(status.textContent, 'Width: half');
    handle.fire('keydown', key('End'));
    assert.equal(cards[0].props.get('--widget-w'), '12');
    assert.equal(status.textContent, 'Width: full width');
    handle.fire('keydown', key('Home'));
    assert.equal(cards[0].props.get('--widget-w'), '4');
    const left = key('ArrowLeft');
    handle.fire('keydown', left);
    assert.equal(left.prevented, true, 'held at the first step, still answered');
    for (const ignored of [key('ArrowDown'), key('Enter'), key('ArrowRight', { altKey: true }), key('ArrowRight', { metaKey: true })]) {
        handle.fire('keydown', ignored);
        assert.equal(ignored.prevented, undefined, ignored.key);
    }
    assert.equal(cards[0].props.get('--widget-w'), '4');
    await settle();
    assert.equal(saves.length, 1, 'the keys share the one-at-a-time write');
});

test('dragging the edge previews width steps on that card only; drop saves, Escape cancels', async () => {
    const { list, cards, doc, saves, status } = board();
    const handle = cards[0].handle;
    const pitch = (1200 + 14) / 12;
    const down = { button: 0, pointerId: 7, clientX: 400, currentTarget: handle, preventDefault() {} };
    handle.fire('pointerdown', down);
    assert.equal(list.classList.contains('resizing'), true);
    assert.equal(handle.captured, 7);
    handle.fire('pointermove', { pointerId: 7, clientX: 400 + 1.6 * pitch });
    assert.equal(cards[0].props.get('--widget-w'), '6', 'the nearest step to 5.6 columns');
    handle.fire('pointermove', { pointerId: 7, clientX: 400 + 9 * pitch });
    assert.equal(cards[0].props.get('--widget-w'), '12');
    assert.equal(cards[1].props.get('--widget-w'), '4', 'no other card is touched');
    await settle();
    assert.equal(saves.length, 0, 'a preview is not a save');
    handle.fire('pointerup', { pointerId: 7 });
    handle.fire('lostpointercapture', { pointerId: 7 });
    assert.equal(list.classList.contains('resizing'), false);
    assert.equal(status.textContent, 'Width: full width');
    await settle();
    assert.deepEqual(saves[0].payload, { widget_size: { 'demo:a': { w: 12, h: 0 } } });

    handle.fire('pointerdown', { ...down, pointerId: 8 });
    handle.fire('pointermove', { pointerId: 8, clientX: 400 - 7 * pitch });
    assert.equal(cards[0].props.get('--widget-w'), '4');
    const escape = { key: 'Escape', preventDefault() {}, stopPropagation() {} };
    doc.fire('keydown', escape);
    assert.equal(cards[0].props.get('--widget-w'), '12', 'Escape restores the saved width');
    assert.equal(doc.handlers.get('keydown').length, 0, 'the drag releases its document listener');
    handle.fire('pointerup', { pointerId: 8 });
    saves[0].resolve({ ok: true });
    await settle();
    assert.equal(saves.length, 1);

    // The stacked column has no widths to drag.
    list.dataset.widgetLayout = 'stack';
    handle.fire('pointerdown', { ...down, pointerId: 9 });
    assert.equal(list.classList.contains('resizing'), false);
});
