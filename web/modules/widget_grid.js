/* Widgets board (docs/DESIGN.md "Widgets board"): the cards stand in rows on a
   12-column grid in the owner's `widget_order`, each as wide as the owner's
   width (`ui_preferences.widget_size`) or, until the owner picks one, the
   author's `span` (1: a third of a row, 2: two thirds), and as tall as its own
   content; a row is as tall as its tallest card. A list narrower than
   WIDGET_GRID_STACK_BELOW_PX stacks the cards in one column and ignores widths.
   Nothing here measures a card or moves a node: `applyWidgetGrid` writes ONLY
   custom properties — `--widget-w` and `--widget-order` on each card — and the
   list's `data-widget-layout` mode, which static rules in web/style.css turn
   into grid placement, so a reorder, a resize or a mode switch never reloads a
   running <iframe>. The bounds mirror ouroboros/gateway/ui_preferences.py. */

import { widgetKey } from './widget_list.js';

export const WIDGET_GRID_COLUMNS = 12;
export const WIDGET_SIZE_MAX_ITEMS = 200;
const WIDGET_KEY_MAX_LENGTH = 200;
// The owner's width steps. Rows close as 4 + 8, 6 + 6, 4 + 4 + 4 or 12; a
// third beside a half leaves the two-column gap the owner chose.
export const WIDGET_WIDTH_STEPS = Object.freeze([
    { w: 4, label: 'One third' },
    { w: 6, label: 'Half' },
    { w: 8, label: 'Two thirds' },
    { w: 12, label: 'Full width' },
]);
// Below this list width the board falls back to one stacked column. The band
// around it keeps a scrollbar that appears or leaves with the other mode's
// height from flipping the mode straight back.
export const WIDGET_GRID_STACK_BELOW_PX = 720;
const WIDGET_GRID_MODE_BAND_PX = 24;

const bound = new WeakMap();

/** The saved `widget_size` map, bounded the way the server stores it. */
export function normalizeWidgetSize(value) {
    const sizes = {};
    if (!value || typeof value !== 'object' || Array.isArray(value)) return sizes;
    for (const [rawKey, size] of Object.entries(value).slice(0, WIDGET_SIZE_MAX_ITEMS)) {
        const key = String(rawKey || '').trim();
        if (!key || key.length > WIDGET_KEY_MAX_LENGTH || !Number.isInteger(size?.w)) continue;
        sizes[key] = { w: Math.max(1, Math.min(WIDGET_GRID_COLUMNS, size.w)), h: 0 };
    }
    return sizes;
}

/** The author's default width: a third of a row, two thirds for `span: 2`. */
export function defaultWidgetWidth(tab) {
    return Number(tab?.span || tab?.grid_span || 1) >= 2 ? 8 : 4;
}

/** A card's width in board columns: the owner's width over the author's default. */
export function widgetWidth(tab, sizes) {
    return sizes?.[widgetKey(tab)]?.w || defaultWidgetWidth(tab);
}

/** The width step nearest to a column count (a drag ends between steps). */
export function nearestWidgetWidth(columns) {
    return WIDGET_WIDTH_STEPS.reduce((best, { w }) => (
        Math.abs(w - columns) < Math.abs(best - columns) ? w : best
    ), WIDGET_WIDTH_STEPS[0].w);
}

/** The next step wider (`delta` > 0) or narrower than `w`, held at the first and last step. */
export function stepWidgetWidth(w, delta) {
    const steps = WIDGET_WIDTH_STEPS.map((step) => step.w);
    if (delta > 0) return steps.find((step) => step > w) ?? steps[steps.length - 1];
    return steps.slice().reverse().find((step) => step < w) ?? steps[0];
}

/** `grid` or `stack` for a list this wide; a zero width (a hidden page) keeps the mode. */
export function widgetGridMode(width, previous = 'grid') {
    if (!(width > 0)) return previous;
    const half = WIDGET_GRID_MODE_BAND_PX / 2;
    if (previous === 'stack') return width >= WIDGET_GRID_STACK_BELOW_PX + half ? 'grid' : 'stack';
    return width < WIDGET_GRID_STACK_BELOW_PX - half ? 'stack' : 'grid';
}

/** One card's width, written only when it changed (a drag preview uses it too). */
export function setWidgetCardWidth(card, w) {
    if (card.style.getPropertyValue('--widget-w') !== String(w)) card.style.setProperty('--widget-w', String(w));
}

/**
 * Bind (once per list) and write the board: each card's width and its rank in
 * `tabs` (already in the owner's order). A card marked `data-widget-removed`
 * (its frame still stopping in order) keeps what it had. The list's one
 * ResizeObserver only switches `data-widget-layout` between `grid` and
 * `stack` by the list's own width — no card size feeds back into it — one
 * frame later, because the switch changes the observed list's height and a
 * change inside the callback would be an observer loop. Every call returns the
 * list's one idempotent disposer.
 */
export function applyWidgetGrid(list, { tabs = [], sizes = {} } = {}) {
    if (!list) return () => {};
    let entry = bound.get(list);
    if (!entry) {
        let frame = 0;
        const syncMode = () => {
            const mode = widgetGridMode(list.clientWidth, list.dataset.widgetLayout || 'grid');
            if (list.dataset.widgetLayout !== mode) list.dataset.widgetLayout = mode;
        };
        const observer = new ResizeObserver(() => {
            if (!frame) frame = requestAnimationFrame(() => {
                frame = 0;
                syncMode();
            });
        });
        entry = {
            syncMode,
            dispose() {
                if (bound.get(list) !== entry) return;
                bound.delete(list);
                observer.disconnect();
                if (frame) cancelAnimationFrame(frame);
                frame = 0;
            },
        };
        bound.set(list, entry);
        observer.observe(list);
    }
    // Every write re-reads the width too: a page shown again may have been
    // resized while hidden, and its first paint must not use the old mode.
    entry.syncMode();
    const byKey = new Map(tabs.map((tab, rank) => [widgetKey(tab), { tab, rank }]));
    list.querySelectorAll('[data-widget-key]').forEach((card) => {
        const placed = byKey.get(card.dataset.widgetKey || '');
        if (!placed || card.hasAttribute('data-widget-removed')) return;
        setWidgetCardWidth(card, widgetWidth(placed.tab, sizes));
        const rank = String(placed.rank);
        if (card.style.getPropertyValue('--widget-order') !== rank) card.style.setProperty('--widget-order', rank);
    });
    return entry.dispose;
}
