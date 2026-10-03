import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    defaultWidgetWidth,
    nearestWidgetWidth,
    normalizeWidgetSize,
    ownerSpans,
    stepWidgetWidth,
    WIDGET_FULL_SPAN,
    WIDGET_SIZE_MAX_ITEMS,
    WIDGET_WIDTH_STEPS,
    widgetWidth,
    widthColumns,
} from '../modules/widget_size.js';

// The owner's card widths (docs/DESIGN.md "Widgets board"): a width is a column
// count of the masonry board, Full width every column it has; until the owner
// picks one, the author's `span` (1 or 2) decides.

const tab = (key, span) => ({ key, skill: key.split(':')[0], tab_id: key.split(':')[1], span });

test('a saved width clamps to 1..Full width, h stays 0, anything but an integer w is no size', () => {
    assert.deepEqual(normalizeWidgetSize({
        'a:main': { w: 2, h: 0 },
        ' b:main ': { w: 99, h: 640 },
        'c:main': { w: -3 },
        'd:main': { w: '2' },
        'e:main': { w: 1.5 },
        'f:main': null,
        '': { w: 1 },
        [`${'x'.repeat(201)}`]: { w: 1 },
    }), { 'a:main': { w: 2, h: 0 }, 'b:main': { w: WIDGET_FULL_SPAN, h: 0 }, 'c:main': { w: 1, h: 0 } });
    for (const bad of [null, undefined, [], 'wide', 4]) assert.deepEqual(normalizeWidgetSize(bad), {});
    const many = Object.fromEntries(Array.from({ length: 250 }, (_, i) => [`s:${i}`, { w: 1 }]));
    assert.equal(Object.keys(normalizeWidgetSize(many)).length, WIDGET_SIZE_MAX_ITEMS);
});

test("the author span is the default width in columns and the owner's width wins over it", () => {
    assert.equal(defaultWidgetWidth(tab('a:main', 1)), 1);
    assert.equal(defaultWidgetWidth(tab('a:main', 2)), 2);
    assert.equal(defaultWidgetWidth({ grid_span: 2 }), 2);
    assert.equal(defaultWidgetWidth({}), 1);
    assert.equal(defaultWidgetWidth(null), 1);
    const sizes = { 'a:main': { w: WIDGET_FULL_SPAN, h: 0 } };
    assert.equal(widgetWidth(tab('a:main', 1), sizes), WIDGET_FULL_SPAN);
    assert.equal(widgetWidth(tab('b:main', 2), sizes), 2, 'a size for another card never leaks');
    assert.equal(widgetWidth({ skill: 'a', tab_id: 'main' }, sizes), WIDGET_FULL_SPAN, 'skill:tab_id is the key fallback');
    // The masonry takes the owner's widths by key, nothing else.
    assert.deepEqual(ownerSpans({ 'a:main': { w: 3, h: 0 }, 'b:main': null, 'c:main': {} }), { 'a:main': 3 });
    assert.deepEqual(ownerSpans(undefined), {});
});

test('width steps: 1, 2, 3 columns and Full width; keys walk them, held at both ends', () => {
    assert.deepEqual(WIDGET_WIDTH_STEPS.map((step) => step.w), [1, 2, 3, WIDGET_FULL_SPAN]);
    assert.deepEqual(WIDGET_WIDTH_STEPS.map((step) => step.label), ['1 column', '2 columns', '3 columns', 'Full width']);
    assert.equal(WIDGET_FULL_SPAN, 12);
    assert.equal(stepWidgetWidth(1, 1), 2);
    assert.equal(stepWidgetWidth(3, 1), WIDGET_FULL_SPAN);
    assert.equal(stepWidgetWidth(WIDGET_FULL_SPAN, 1), WIDGET_FULL_SPAN, 'held at the last step');
    assert.equal(stepWidgetWidth(WIDGET_FULL_SPAN, -1), 3);
    assert.equal(stepWidgetWidth(1, -1), 1, 'held at the first step');
    // A width between steps (a hand-edited file) steps to its neighbours.
    assert.equal(stepWidgetWidth(5, 1), WIDGET_FULL_SPAN);
    assert.equal(stepWidgetWidth(5, -1), 3);
});

test('a width takes its columns of the board; a drag lands on the nearest step, the whole row is Full width', () => {
    assert.equal(widthColumns(2, 4), 2);
    assert.equal(widthColumns(3, 2), 2, 'clamped to the board');
    assert.equal(widthColumns(WIDGET_FULL_SPAN, 5), 5);
    // Four columns: 1, 2, 3 and the whole row.
    assert.equal(nearestWidgetWidth(0.2, 4), 1);
    assert.equal(nearestWidgetWidth(1.6, 4), 2);
    assert.equal(nearestWidgetWidth(2.5, 4), 2, 'a tie keeps the narrower step');
    assert.equal(nearestWidgetWidth(3.7, 4), WIDGET_FULL_SPAN);
    assert.equal(nearestWidgetWidth(9, 4), WIDGET_FULL_SPAN);
    // Three columns: three of them is the whole row, so a drag there is Full width.
    assert.equal(nearestWidgetWidth(3, 3), WIDGET_FULL_SPAN);
    assert.equal(nearestWidgetWidth(2.2, 3), 2);
    // Five columns: four is between 3 and the row; the tie keeps 3.
    assert.equal(nearestWidgetWidth(4, 5), 3);
    assert.equal(nearestWidgetWidth(4.6, 5), WIDGET_FULL_SPAN);
});

test('the size module measures nothing and writes no DOM', () => {
    const source = readFileSync(new URL('../modules/widget_size.js', import.meta.url), 'utf8');
    for (const forbidden of ['document', 'querySelector', 'style.', 'offsetHeight', 'getBoundingClientRect', 'ResizeObserver']) {
        assert.equal(source.includes(forbidden), false, `widget_size.js must not use ${forbidden}`);
    }
});
