/* Widgets card order: the owner's `widget_order` preference applied to the card
   list, and the drag / keyboard reorder handles on the cards. Moved out of
   widgets.js unchanged (phase 2 of the widgets lifecycle sprint); widgets.js
   still owns persisting the order through `/api/ui/preferences`. */

import { applyMasonry } from './masonry.js';
import { widgetKey } from './widget_list.js';

export function normalizeWidgetOrder(value) {
    if (!Array.isArray(value)) return [];
    const seen = new Set();
    return value
        .map((item) => String(item || '').trim())
        .filter((item) => {
            if (!item || seen.has(item)) return false;
            seen.add(item);
            return true;
        });
}

export function sortTabsByWidgetOrder(tabs, order) {
    const rank = new Map(normalizeWidgetOrder(order).map((key, idx) => [key, idx]));
    return tabs.map((tab, originalIndex) => ({ tab, originalIndex })).sort((a, b) => {
        const aRank = rank.has(widgetKey(a.tab)) ? rank.get(widgetKey(a.tab)) : Number.MAX_SAFE_INTEGER;
        const bRank = rank.has(widgetKey(b.tab)) ? rank.get(widgetKey(b.tab)) : Number.MAX_SAFE_INTEGER;
        if (aRank !== bRank) return aRank - bRank;
        return a.originalIndex - b.originalIndex;
    }).map((item) => item.tab);
}

export function currentWidgetOrderFromDom(list) {
    return Array.from(list.querySelectorAll('[data-widget-key]'))
        .map((card) => card.dataset.widgetKey || '')
        .filter(Boolean);
}

// Cards keep their DOM node across list patches, so binding is per card, once;
// the drag source is shared by every binding pass over the one Widgets list.
const reorderBoundCards = new WeakSet();
let draggedKey = '';

export function bindWidgetCardReorder(list, onOrderChange) {
    if (!list) return;
    const clearDragState = () => {
        list.querySelectorAll('.widgets-card.dragging, .widgets-card.drag-over').forEach((card) => {
            card.classList.remove('dragging', 'drag-over');
        });
        draggedKey = '';
    };
    const finishReorder = () => {
        applyMasonry(list);
        onOrderChange(currentWidgetOrderFromDom(list));
    };
    list.querySelectorAll('[data-widget-reorder-handle]').forEach((handle) => {
        const card = handle.closest('[data-widget-key]');
        if (!card || reorderBoundCards.has(card)) return;
        handle.setAttribute('draggable', 'true');
        handle.addEventListener('dragstart', (event) => {
            draggedKey = card.dataset.widgetKey || '';
            if (!draggedKey) return;
            card.classList.add('dragging');
            if (event.dataTransfer) {
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', draggedKey);
            }
        });
        handle.addEventListener('dragend', clearDragState);
        handle.addEventListener('keydown', (event) => {
            let moved = false;
            if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
                const previous = card.previousElementSibling;
                if (previous?.classList.contains('widgets-card')) {
                    previous.before(card);
                    moved = true;
                }
            } else if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
                const next = card.nextElementSibling;
                if (next?.classList.contains('widgets-card')) {
                    next.after(card);
                    moved = true;
                }
            } else if (event.key === 'Home') {
                const first = list.querySelector('.widgets-card');
                if (first && first !== card) {
                    first.before(card);
                    moved = true;
                }
            } else if (event.key === 'End') {
                const cards = list.querySelectorAll('.widgets-card');
                const last = cards[cards.length - 1];
                if (last && last !== card) {
                    last.after(card);
                    moved = true;
                }
            }
            if (!moved) return;
            event.preventDefault();
            clearDragState();
            finishReorder();
            handle.focus();
        });
    });
    list.querySelectorAll('.widgets-card').forEach((card) => {
        if (reorderBoundCards.has(card)) return;
        reorderBoundCards.add(card);
        card.addEventListener('dragover', (event) => {
            if (!draggedKey || card.dataset.widgetKey === draggedKey) return;
            event.preventDefault();
            card.classList.add('drag-over');
            if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
        });
        card.addEventListener('dragleave', () => card.classList.remove('drag-over'));
        card.addEventListener('drop', (event) => {
            if (!draggedKey || card.dataset.widgetKey === draggedKey) return;
            event.preventDefault();
            const dragged = list.querySelector(`[data-widget-key="${CSS.escape(draggedKey)}"]`);
            if (!dragged) return;
            const cards = Array.from(list.querySelectorAll('.widgets-card'));
            const draggedIdx = cards.indexOf(dragged);
            const targetIdx = cards.indexOf(card);
            if (draggedIdx < 0 || targetIdx < 0) return;
            if (draggedIdx < targetIdx) card.after(dragged);
            else card.before(dragged);
            clearDragState();
            finishReorder();
        });
    });
}
