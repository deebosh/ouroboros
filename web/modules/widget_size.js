/* The owner's card widths on the Widgets board (docs/DESIGN.md "Widgets board").
   The masonry (web/modules/masonry.js) packs the cards in the owner's order;
   a card spans as many of its columns as the owner chose
   (`ui_preferences.widget_size`, `{w, h}`) or, until the owner picks one, the
   author's `span` (1 or 2). A width is a column count; WIDGET_FULL_SPAN means
   every column the board has. Nothing here measures or writes the DOM. The
   bounds mirror ouroboros/gateway/ui_preferences.py. */

import { widgetKey } from './widget_list.js';

export const WIDGET_FULL_SPAN = 12;
export const WIDGET_SIZE_MAX_ITEMS = 200;
const WIDGET_KEY_MAX_LENGTH = 200;
// The owner's width steps, in columns of the board; the masonry decides how
// many columns fit, and a step wider than that fills the row.
export const WIDGET_WIDTH_STEPS = Object.freeze([
    { w: 1, label: '1 column' },
    { w: 2, label: '2 columns' },
    { w: 3, label: '3 columns' },
    { w: WIDGET_FULL_SPAN, label: 'Full width' },
]);

/** The saved `widget_size` map, bounded the way the server stores it. */
export function normalizeWidgetSize(value) {
    const sizes = {};
    if (!value || typeof value !== 'object' || Array.isArray(value)) return sizes;
    for (const [rawKey, size] of Object.entries(value).slice(0, WIDGET_SIZE_MAX_ITEMS)) {
        const key = String(rawKey || '').trim();
        if (!key || key.length > WIDGET_KEY_MAX_LENGTH || !Number.isInteger(size?.w)) continue;
        sizes[key] = { w: Math.max(1, Math.min(WIDGET_FULL_SPAN, size.w)), h: 0 };
    }
    return sizes;
}

/** The author's default width: two columns for `span: 2`, else one. */
export function defaultWidgetWidth(tab) {
    return Number(tab?.span || tab?.grid_span || 1) >= 2 ? 2 : 1;
}

/** A card's width setting in columns: the owner's over the author's default. */
export function widgetWidth(tab, sizes) {
    return sizes?.[widgetKey(tab)]?.w || defaultWidgetWidth(tab);
}

/** The owner's widths by card key, the `spans` the masonry takes. */
export function ownerSpans(sizes) {
    return Object.fromEntries(Object.entries(sizes || {}).filter(([, size]) => size?.w).map(([key, size]) => [key, size.w]));
}

/** How many of `count` columns a width takes: Full width all of them. */
export function widthColumns(w, count) {
    return w >= WIDGET_FULL_SPAN ? count : Math.min(w, count);
}

/**
 * The step a drag of the right edge lands on: the nearest to `columns`
 * (fractional) on a board of `count` columns, a tie keeping the narrower. The
 * whole row is Full width, never a numbered step that happens to fill it.
 */
export function nearestWidgetWidth(columns, count) {
    const steps = WIDGET_WIDTH_STEPS.filter(({ w }) => w >= WIDGET_FULL_SPAN || w < count);
    return steps.reduce((best, { w }) => (
        Math.abs(widthColumns(w, count) - columns) < Math.abs(widthColumns(best, count) - columns) ? w : best
    ), steps[0].w);
}

/** The next step wider (`delta` > 0) or narrower than `w`, held at the first and last step. */
export function stepWidgetWidth(w, delta) {
    const steps = WIDGET_WIDTH_STEPS.map((step) => step.w);
    if (delta > 0) return steps.find((step) => step > w) ?? steps[steps.length - 1];
    return steps.slice().reverse().find((step) => step < w) ?? steps[0];
}
