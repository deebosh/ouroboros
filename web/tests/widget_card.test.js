import test from 'node:test';
import assert from 'node:assert/strict';

import {
    confirmWidgetsRestart,
    effectiveStartMode,
    isFramedWidget,
    isRetainedWidget,
    renderWidgetCardControls,
    syncWidgetCardControls,
    WIDGET_START_MODES,
    withWidgetStartMode,
} from '../modules/widget_card.js';

function tab(render, overrides = {}) {
    return { key: 'demo:main', skill: 'demo', tab_id: 'main', title: 'Demo', render, ...overrides };
}

test('effective launch policy: owner override > author render.start > kind default', () => {
    const module = tab({ kind: 'module', entry: 'widget.js', start: 'auto' });
    // Author's validated value.
    assert.equal(effectiveStartMode(module, { widget_start_mode: {} }), 'auto');
    assert.equal(effectiveStartMode(module, null), 'auto');
    // Owner override wins, for every mode the validator allows.
    for (const mode of WIDGET_START_MODES) {
        assert.equal(effectiveStartMode(module, { widget_start_mode: { 'demo:main': mode } }), mode);
    }
    // An override for another card never leaks; a garbage override is ignored.
    assert.equal(effectiveStartMode(module, { widget_start_mode: { 'other:main': 'manual' } }), 'auto');
    assert.equal(effectiveStartMode(module, { widget_start_mode: { 'demo:main': 'always' } }), 'auto');
    // The server key is the lookup key; skill:tab_id is the fallback.
    const unkeyed = tab({ kind: 'module', entry: 'widget.js', start: 'auto' }, { key: undefined });
    assert.equal(effectiveStartMode(unkeyed, { widget_start_mode: { 'demo:main': 'manual' } }), 'manual');
});

test('payloads registered before the validator filled `start` fall back to the kind default', () => {
    assert.equal(effectiveStartMode(tab({ kind: 'module', entry: 'widget.js' }), {}), 'manual');
    assert.equal(effectiveStartMode(tab({ kind: 'iframe', route: 'view' }), {}), 'manual');
    assert.equal(effectiveStartMode(tab({ kind: 'declarative', schema_version: 1, components: [] }), {}), 'auto');
    assert.equal(effectiveStartMode(tab({ kind: 'module', entry: 'widget.js', start: 'bogus' }), {}), 'manual');
    assert.equal(effectiveStartMode({ skill: 's', tab_id: 't' }, {}), 'auto');
});

test('retain is the third policy: it starts like auto and keeps only a FRAMED card running while hidden', () => {
    assert.equal(effectiveStartMode(tab({ kind: 'module', entry: 'widget.js', start: 'retain' }), {}), 'retain');
    assert.deepEqual(WIDGET_START_MODES, ['auto', 'manual', 'retain']);
    const kept = tab({ kind: 'module', entry: 'widget.js', start: 'retain' });
    assert.equal(isRetainedWidget(kept, {}), true);
    assert.equal(isRetainedWidget(tab({ kind: 'iframe', route: 'view' }), { widget_start_mode: { 'demo:main': 'retain' } }), true);
    // The owner's override wins in both directions.
    assert.equal(isRetainedWidget(kept, { widget_start_mode: { 'demo:main': 'auto' } }), false);
    assert.equal(isRetainedWidget(tab({ kind: 'module', entry: 'widget.js' }), { widget_start_mode: { 'demo:main': 'retain' } }), true);
    // A declarative card is host-drawn: it always disposes on leave, whatever the override says.
    assert.equal(isRetainedWidget(tab({ kind: 'declarative', schema_version: 1, components: [] }), { widget_start_mode: { 'demo:main': 'retain' } }), false);
    assert.equal(isRetainedWidget(null, {}), false);
    assert.equal(isRetainedWidget(undefined, null), false);
});

function fakeCard() {
    const power = { textContent: '', disabled: false };
    const status = { hidden: false, dataset: {}, textContent: '' };
    const items = WIDGET_START_MODES.map((mode) => {
        const attrs = {};
        return { dataset: { widgetStartMode: mode }, attrs, setAttribute: (name, value) => { attrs[name] = value; } };
    });
    return {
        power,
        status,
        items,
        querySelector: (selector) => (selector === '[data-widget-power]' ? power : selector === '[data-widget-status]' ? status : null),
        querySelectorAll: (selector) => (selector === '[data-widget-start-mode]' ? items : []),
    };
}

test('the status badge is honest about keep-alive: "Keeps running" only for a running retain card', () => {
    const card = fakeCard();
    syncWidgetCardControls(card, 'running', 'retain');
    assert.deepEqual(card.status, { hidden: false, dataset: { tone: 'ok' }, textContent: 'Keeps running' });
    assert.equal(card.power.textContent, 'Stop');
    assert.equal(card.power.disabled, false);
    assert.deepEqual(card.items.map((item) => item.attrs['aria-checked']), ['false', 'false', 'true']);
    syncWidgetCardControls(card, 'running', 'auto');
    assert.equal(card.status.textContent, 'Running');
    // Without a mode the caller does not know it: the plain running sentence.
    syncWidgetCardControls(card, 'running');
    assert.equal(card.status.textContent, 'Running');
    syncWidgetCardControls(card, 'stopping', 'retain');
    assert.equal(card.status.textContent, 'Stopping…');
    assert.equal(card.status.dataset.tone, 'neutral');
    assert.equal(card.power.disabled, true);
    syncWidgetCardControls(card, 'stopped', 'retain');
    assert.equal(card.status.hidden, true);
    assert.equal(card.power.textContent, 'Start');
});

test('Refresh asks only while a kept-running card would be stopped, and only a strict true restarts', async () => {
    const calls = [];
    const dialogImpl = async (options) => { calls.push(options); return true; };
    // Nothing kept running: no dialog, straight to the hard reset.
    assert.equal(await confirmWidgetsRestart(0, { dialogImpl }), true);
    assert.equal(await confirmWidgetsRestart(undefined, { dialogImpl }), true);
    assert.equal(await confirmWidgetsRestart(-3, { dialogImpl }), true);
    assert.equal(calls.length, 0);
    assert.equal(await confirmWidgetsRestart(1, { dialogImpl }), true);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].title, 'Restart all widgets?');
    assert.equal(calls[0].body, '1 program kept running in the background will be stopped.');
    assert.equal(calls[0].confirmLabel, 'Restart');
    assert.equal(calls[0].input, undefined, 'confirm mode: a strict boolean comes back');
    await confirmWidgetsRestart(2, { dialogImpl });
    assert.equal(calls[1].body, '2 programs kept running in the background will be stopped.');
    // Cancel, Close, Escape, backdrop and supersession all resolve false → nothing happens.
    assert.equal(await confirmWidgetsRestart(1, { dialogImpl: async () => false }), false);
    // Anything that is not the strict boolean true keeps every frame as it is.
    assert.equal(await confirmWidgetsRestart(1, { dialogImpl: async () => ({ confirmed: true, value: '' }) }), false);
    assert.equal(await confirmWidgetsRestart(1, { dialogImpl: async () => 'yes' }), false);
});

test('only framed cards carry Start/Stop and the launch-policy menu', () => {
    assert.equal(isFramedWidget(tab({ kind: 'module', entry: 'widget.js' })), true);
    assert.equal(isFramedWidget(tab({ kind: 'iframe', route: 'view' })), true);
    assert.equal(isFramedWidget(tab({ kind: 'declarative', schema_version: 1, components: [] })), false);
    assert.equal(renderWidgetCardControls(tab({ kind: 'declarative', schema_version: 1, components: [] })), '');
    const controls = renderWidgetCardControls(tab({ kind: 'module', entry: 'widget.js' }));
    // Exactly one primary control; the policy is a secondary menu of radio items.
    assert.equal((controls.match(/btn-primary/g) || []).length, 1);
    assert.match(controls, /data-widget-power>Start</);
    assert.match(controls, /class="ui-status" data-tone="neutral" data-widget-status hidden/);
    assert.match(controls, /<dialog class="skills-card-menu-dialog" role="menu"/);
    for (const mode of WIDGET_START_MODES) {
        assert.match(controls, new RegExp(`role="menuitemradio"[^>]*data-widget-start-mode="${mode}"`));
    }
    assert.doesNotMatch(controls, /aria-checked="true"/);
});

test('start-mode payload is a whole-map replace that keeps every other card', () => {
    const current = { 'game:main': 'retain', 'gone:old': 'manual' };
    assert.deepEqual(
        withWidgetStartMode(current, 'demo:main', 'auto'),
        { 'game:main': 'retain', 'gone:old': 'manual', 'demo:main': 'auto' },
    );
    assert.deepEqual(current, { 'game:main': 'retain', 'gone:old': 'manual' });
    assert.deepEqual(withWidgetStartMode(null, 'demo:main', 'manual'), { 'demo:main': 'manual' });
    assert.deepEqual(withWidgetStartMode(['x'], 'demo:main', 'manual'), { 'demo:main': 'manual' });
});
