import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    applyWidgetGrid,
    defaultWidgetWidth,
    nearestWidgetWidth,
    normalizeWidgetSize,
    setWidgetCardWidth,
    stepWidgetWidth,
    WIDGET_GRID_COLUMNS,
    WIDGET_GRID_STACK_BELOW_PX,
    WIDGET_SIZE_MAX_ITEMS,
    WIDGET_WIDTH_STEPS,
    widgetGridMode,
    widgetWidth,
} from '../modules/widget_grid.js';

// Widgets board (docs/DESIGN.md "Widgets board"): rows on a 12-column grid in
// the owner's order, each card as wide as the owner's step over the author's
// `span`, as tall as its content. Nothing measured is an input, and the board
// reaches the DOM only as custom properties: no node is moved.

const tab = (key, span) => ({ key, skill: key.split(':')[0], tab_id: key.split(':')[1], span });

test('a saved width clamps to the board, h stays 0, anything but an integer w is no size', () => {
    assert.deepEqual(normalizeWidgetSize({
        'a:main': { w: 6, h: 0 },
        ' b:main ': { w: 99, h: 640 },
        'c:main': { w: -3 },
        'd:main': { w: '4' },
        'e:main': { w: 4.5 },
        'f:main': null,
        '': { w: 4 },
        [`${'x'.repeat(201)}`]: { w: 4 },
    }), { 'a:main': { w: 6, h: 0 }, 'b:main': { w: WIDGET_GRID_COLUMNS, h: 0 }, 'c:main': { w: 1, h: 0 } });
    for (const bad of [null, undefined, [], 'wide', 4]) assert.deepEqual(normalizeWidgetSize(bad), {});
    const many = Object.fromEntries(Array.from({ length: 250 }, (_, i) => [`s:${i}`, { w: 4 }]));
    assert.equal(Object.keys(normalizeWidgetSize(many)).length, WIDGET_SIZE_MAX_ITEMS);
});

test('the author span is the default width and the owner width wins over it', () => {
    assert.equal(defaultWidgetWidth(tab('a:main', 1)), 4);
    assert.equal(defaultWidgetWidth(tab('a:main', 2)), 8);
    assert.equal(defaultWidgetWidth({ grid_span: 2 }), 8);
    assert.equal(defaultWidgetWidth({}), 4);
    assert.equal(defaultWidgetWidth(null), 4);
    const sizes = { 'a:main': { w: 12, h: 0 } };
    assert.equal(widgetWidth(tab('a:main', 1), sizes), 12);
    assert.equal(widgetWidth(tab('b:main', 2), sizes), 8, 'a size for another card never leaks');
    assert.equal(widgetWidth({ skill: 'a', tab_id: 'main' }, sizes), 12, 'skill:tab_id is the key fallback');
});

test('width steps: a third, a half, two thirds, full; a drag lands on the nearest, keys walk them', () => {
    assert.deepEqual(WIDGET_WIDTH_STEPS.map((step) => step.w), [4, 6, 8, 12]);
    assert.deepEqual(WIDGET_WIDTH_STEPS.map((step) => step.label), ['One third', 'Half', 'Two thirds', 'Full width']);
    assert.equal(nearestWidgetWidth(-3), 4);
    assert.equal(nearestWidgetWidth(4.9), 4);
    assert.equal(nearestWidgetWidth(5.2), 6);
    assert.equal(nearestWidgetWidth(7), 6, 'a tie keeps the narrower step');
    assert.equal(nearestWidgetWidth(10.1), 12);
    assert.equal(nearestWidgetWidth(99), 12);
    assert.equal(stepWidgetWidth(4, 1), 6);
    assert.equal(stepWidgetWidth(8, 1), 12);
    assert.equal(stepWidgetWidth(12, 1), 12, 'held at the last step');
    assert.equal(stepWidgetWidth(8, -1), 6);
    assert.equal(stepWidgetWidth(4, -1), 4, 'held at the first step');
    // A width between steps (a hand-edited file) steps to its neighbours.
    assert.equal(stepWidgetWidth(5, 1), 6);
    assert.equal(stepWidgetWidth(5, -1), 4);
});

test('grid or stack by the list width, with a band that keeps a scrollbar from flapping it', () => {
    assert.equal(WIDGET_GRID_STACK_BELOW_PX, 720);
    assert.equal(widgetGridMode(1200), 'grid');
    assert.equal(widgetGridMode(708, 'grid'), 'grid');
    assert.equal(widgetGridMode(707, 'grid'), 'stack');
    assert.equal(widgetGridMode(731, 'stack'), 'stack');
    assert.equal(widgetGridMode(732, 'stack'), 'grid');
    assert.equal(widgetGridMode(0, 'stack'), 'stack', 'a hidden list keeps its mode');
    assert.equal(widgetGridMode(Number.NaN, 'grid'), 'grid');
});

// --- DOM writer ------------------------------------------------------------

function fakeCard(key, { removed = false } = {}) {
    const props = new Map();
    const writes = [];
    const node = {
        dataset: { widgetKey: key },
        props,
        writes,
        style: {
            getPropertyValue: (name) => props.get(name) || '',
            setProperty: (name, value) => { writes.push(name); props.set(name, value); },
            removeProperty: () => { throw new Error('the board never removes a property'); },
        },
        hasAttribute: (name) => removed && name === 'data-widget-removed',
        get offsetHeight() { throw new Error('the board must never measure a card'); },
        getBoundingClientRect() { throw new Error('the board must never measure a card'); },
    };
    for (const name of ['before', 'after', 'append', 'prepend', 'remove', 'replaceWith', 'insertBefore', 'appendChild']) {
        node[name] = () => { throw new Error(`the board must not call ${name}`); };
    }
    return node;
}

function fakeList(cards, width = 1200) {
    return {
        cards,
        clientWidth: width,
        dataset: {},
        querySelectorAll(selector) {
            assert.equal(selector, '[data-widget-key]');
            return this.cards.slice();
        },
    };
}

function installObserver() {
    const observers = [];
    const frames = new Map();
    let nextFrame = 1;
    globalThis.requestAnimationFrame = (callback) => {
        frames.set(nextFrame, callback);
        return nextFrame++;
    };
    globalThis.cancelAnimationFrame = (id) => frames.delete(id);
    observers.flush = () => {
        const callbacks = [...frames.values()];
        frames.clear();
        callbacks.forEach((callback) => callback());
    };
    observers.pending = () => frames.size;
    globalThis.ResizeObserver = class {
        constructor(callback) {
            this.callback = callback;
            this.disconnected = false;
            observers.push(this);
        }
        observe(target) { this.target = target; }
        disconnect() { this.disconnected = true; }
    };
    return observers;
}

test('applyWidgetGrid writes width and order only as custom properties and never measures or moves a card', () => {
    installObserver();
    const a = fakeCard('demo:a');
    const b = fakeCard('demo:b');
    const retiring = fakeCard('demo:gone', { removed: true });
    // DOM order differs from the owner's order: the order is a property, not a node move.
    const list = fakeList([b, retiring, a]);
    const tabs = [tab('demo:a', 2), tab('demo:b', 1)];
    const dispose = applyWidgetGrid(list, { tabs, sizes: { 'demo:b': { w: 12, h: 0 } } });
    assert.deepEqual(Object.fromEntries(a.props), { '--widget-w': '8', '--widget-order': '0' });
    assert.deepEqual(Object.fromEntries(b.props), { '--widget-w': '12', '--widget-order': '1' });
    assert.equal(retiring.props.size, 0, 'a card whose frame is still stopping keeps what it had');
    assert.equal(list.dataset.widgetLayout, 'grid');
    // An unchanged board writes nothing again; a changed width writes that card only.
    const before = a.writes.length + b.writes.length;
    applyWidgetGrid(list, { tabs, sizes: { 'demo:b': { w: 12, h: 0 } } });
    assert.equal(a.writes.length + b.writes.length, before);
    applyWidgetGrid(list, { tabs, sizes: {} });
    assert.deepEqual(b.writes.slice(-1), ['--widget-w']);
    assert.equal(b.props.get('--widget-w'), '4', 'Reset: back to the author span');
    assert.equal(a.writes.length, 2);
    setWidgetCardWidth(a, 6);
    assert.equal(a.props.get('--widget-w'), '6');
    dispose();
});

test('a card carries only its own facts: with 18 cards each holds its width and a distinct rank, nothing shared', () => {
    installObserver();
    const keys = Array.from({ length: 18 }, (_, i) => `skill${i}:main`);
    const cards = keys.map((key) => fakeCard(key));
    const list = fakeList(cards.slice().reverse());
    applyWidgetGrid(list, { tabs: keys.map((key, i) => tab(key, i % 2 ? 2 : 1)), sizes: {} });
    for (const node of cards) assert.deepEqual([...node.props.keys()].sort(), ['--widget-order', '--widget-w']);
    assert.equal(new Set(cards.map((node) => node.props.get('--widget-order'))).size, 18);
    // The one board-wide fact, the layout mode, lives on the list, never on a card.
    assert.equal(list.dataset.widgetLayout, 'grid');
    const lone = fakeCard('skill0:main');
    applyWidgetGrid(fakeList([lone]), { tabs: [tab('skill0:main', 1)], sizes: {} });
    assert.deepEqual(Object.fromEntries(lone.props), Object.fromEntries(cards[0].props));
});

test('the list width alone switches grid and stack; one observer per list, one idempotent disposer', () => {
    const observers = installObserver();
    const a = fakeCard('demo:a');
    const list = fakeList([a], 1200);
    const tabs = [tab('demo:a', 1)];
    const dispose = applyWidgetGrid(list, { tabs });
    assert.equal(applyWidgetGrid(list, { tabs }), dispose);
    assert.equal(observers.length, 1);
    assert.equal(observers[0].target, list);
    list.clientWidth = 480;
    observers[0].callback();
    observers[0].callback();
    assert.equal(list.dataset.widgetLayout, 'grid', 'the switch waits one frame: never inside the observer callback');
    assert.equal(observers.pending(), 1, 'triggers before the frame coalesce');
    observers.flush();
    assert.equal(list.dataset.widgetLayout, 'stack');
    assert.equal(a.props.get('--widget-w'), '4', 'the width stays stored on the card; the stack ignores it in CSS');
    list.clientWidth = 1000;
    applyWidgetGrid(list, { tabs });
    assert.equal(list.dataset.widgetLayout, 'grid', 'a write re-reads the width: a page shown again never paints the old mode');
    list.clientWidth = 0;
    observers[0].callback();
    observers.flush();
    assert.equal(list.dataset.widgetLayout, 'grid', 'a hidden list keeps its mode');
    observers[0].callback();
    dispose();
    dispose();
    assert.equal(observers[0].disconnected, true);
    assert.equal(observers.pending(), 0, 'the disposer cancels a pending switch');
    const next = applyWidgetGrid(list, { tabs });
    assert.notEqual(next, dispose);
    assert.equal(observers.length, 2);
    next();
    assert.equal(typeof applyWidgetGrid(null), 'function');
});

test('the board modules measure no card and have no node insertion or move API', () => {
    for (const file of ['widget_grid.js', 'widget_reorder.js']) {
        const source = readFileSync(new URL(`../modules/${file}`, import.meta.url), 'utf8');
        for (const forbidden of ['.before(', '.after(', '.prepend(', '.append(', 'insertBefore', 'appendChild', 'replaceWith', '.remove()', 'offsetHeight', 'scrollHeight', "from './masonry.js'"]) {
            assert.equal(source.includes(forbidden), false, `${file} must not use ${forbidden}`);
        }
    }
    const grid = readFileSync(new URL('../modules/widget_grid.js', import.meta.url), 'utf8');
    for (const measured of ['getBoundingClientRect', 'getComputedStyle', 'ResizeObserver(run', 'MutationObserver']) {
        assert.equal(grid.includes(measured), false, `the board never uses ${measured}`);
    }
});
