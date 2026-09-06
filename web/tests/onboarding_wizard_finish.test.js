import assert from 'node:assert/strict';
import test from 'node:test';

test('wizard renders and submits the owner draft on Finish', async (t) => {
    const old = { window: globalThis.window, document: globalThis.document, fetch: globalThis.fetch };
    t.after(() => Object.assign(globalThis, old));
    let finish;
    let submitted;
    let completed;
    const root = { innerHTML: '', querySelectorAll: () => [] };
    const button = { addEventListener: (event, callback) => { if (event === 'click') finish = callback; } };
    globalThis.document = {
        getElementById: (id) => id === 'root' ? root : id === 'next-btn' ? button : null,
        addEventListener() {},
    };
    globalThis.window = {
        addEventListener() {},
        location: { origin: 'http://localhost', replace: (url) => { completed = url; } },
        __OURO_ONBOARDING_BOOTSTRAP__: {
            hostMode: 'web',
            modelDefaults: { openrouter: { main: 'test/model' } },
            contract: {
                steps: [{ id: 'summary', title: 'Review before launch', copy: '', footer: '' }],
                providerFields: [{ id: 'openrouter-key', stateKey: 'openrouterKey', settingKey: 'OPENROUTER_API_KEY' }],
                modelSlots: [{ stateKey: 'mainModel', settingKey: 'OUROBOROS_MODEL' }],
            },
            initialState: {
                openrouterKey: 'test-key-not-a-secret', mainModel: 'test/model',
                reviewEnforcement: 'advisory', runtimeMode: 'advanced',
                totalBudget: 5, perTaskCostUsd: 1,
            },
        },
    };
    globalThis.window.parent = globalThis.window;
    globalThis.fetch = async (url, options) => {
        submitted = { url, method: options.method, body: JSON.parse(options.body) };
        return { ok: true, status: 200, json: async () => ({ ok: true, runtime_mode: 'advanced', restart_required: false }) };
    };
    await import('../modules/onboarding_wizard.js');
    assert.match(root.innerHTML, /Start Ouroboros/);
    assert.equal(typeof finish, 'function');
    finish();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(submitted?.url, '/api/onboarding/complete', root.innerHTML);
    assert.equal(submitted.method, 'POST');
    assert.equal(submitted.body.OPENROUTER_API_KEY, 'test-key-not-a-secret');
    assert.equal(submitted.body.OUROBOROS_MODEL, 'test/model');
    assert.equal(completed, '/');
});
