import test from 'node:test';
import assert from 'node:assert/strict';

import {
    effectiveStartMode,
    isFramedWidget,
    renderWidgetCardControls,
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

test('retain is accepted by the policy function (the host treats it as auto until keep-alive lands)', () => {
    assert.equal(effectiveStartMode(tab({ kind: 'module', entry: 'widget.js', start: 'retain' }), {}), 'retain');
    assert.deepEqual(WIDGET_START_MODES, ['auto', 'manual', 'retain']);
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
