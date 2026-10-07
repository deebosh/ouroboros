/* System notifications through the desktop app (DESIGN §9; owner decisions 1A, 2A, 3A).

   The bridge is a recording double of launcher_background.DesktopApi: nothing here
   reaches an operating system, plays a sound or shows a banner. */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    DEFAULT_NOTIFY_PREFS,
    NOTIFY_PREFS_KEY,
    createNotifier,
    getNotifier,
    nativeStatusText,
    resetNotifier,
} from '../modules/notifications.js';

const ON = { ...DEFAULT_NOTIFY_PREFS, enabled: true };
const FINISHED = { role: 'system', system_type: 'task_summary', task_id: 't1', chat_id: 7, content: 'Report ready' };
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

function storage(prefs = ON) {
    const map = new Map([[NOTIFY_PREFS_KEY, JSON.stringify(prefs)]]);
    return { map, getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => { map.set(k, String(v)); } };
}

function nodes(selectors = {}) {
    return {
        addEventListener() {},
        removeEventListener() {},
        querySelectorAll: (selector) => selectors[selector] || [],
    };
}

/** A notifier whose desktop app answers `answer` (a value or a function of the call). */
function fixture({ answer, prefs = ON, granted = false, extra = {}, documentRef = nodes() } = {}) {
    const calls = [];
    const banners = [];
    const toasts = [];
    const activated = [];
    let tones = 0;
    let browserAsks = 0;
    class Banner {
        static permission = granted ? 'granted' : 'default';
        static async requestPermission() { browserAsks += 1; return 'granted'; }
        constructor(title, options) { banners.push({ title, options }); }
        close() {}
    }
    class Audio {
        constructor() { this.currentTime = 0; this.destination = {}; }
        resume() {}
        close() {}
        createOscillator() { tones += 1; return { connect() {}, start() {}, stop() {} }; }
        createGain() { return { gain: { value: 0 }, connect() {} }; }
    }
    const hostApi = {
        show_native_notification: (...args) => {
            calls.push(['show_native_notification', ...args]);
            return typeof answer === 'function' ? answer(...args) : answer;
        },
        request_attention: (...args) => { calls.push(['request_attention', ...args]); return { ok: true, status: 'native_sound', sound_played: true }; },
        ...extra,
    };
    const notifier = createNotifier({
        storage: storage(prefs),
        notificationCtor: Banner,
        audioContextCtor: Audio,
        showToast: (line) => { toasts.push(line); return null; },
        onActivate: (target) => activated.push(target),
        documentRef,
        hostApi,
    });
    return { notifier, calls, banners, toasts, activated, tones: () => tones, browserAsks: () => browserAsks };
}

test('a delivered system notification is the one surface: no browser banner, no toast, no page tone', async () => {
    const fx = fixture({ answer: { ok: true, status: 'delivered', banner: true, sound: 'os' }, granted: true });
    const out = fx.notifier.handleFrame(FINISHED, { kind: 'chat', isMain: true });
    await tick();
    assert.equal(out.surface, 'native');
    assert.equal(fx.calls.length, 1);
    const [name, title, body, sound, token] = fx.calls[0];
    assert.equal(name, 'show_native_notification');
    assert.equal(title, 'Task finished');
    assert.equal(body, '', 'message text stays private unless the owner turned it on');
    assert.equal(sound, true, 'the OS plays its own sound (1A)');
    assert.match(token, /^[A-Za-z0-9_.:-]{1,96}$/, 'the token is the launcher\'s accepted shape');
    assert.equal(fx.banners.length, 0);
    assert.deepEqual(fx.toasts, []);
    assert.equal(fx.tones(), 0);
    // The same event again is still one notification.
    assert.equal(fx.notifier.handleFrame(FINISHED, { kind: 'chat', isMain: true }), null);
    fx.notifier.destroy();
});

test('Sound off asks the system for a silent notification', async () => {
    const fx = fixture({ answer: { ok: true, status: 'delivered', sound: 'off' }, prefs: { ...ON, sound: false, show_text: true } });
    fx.notifier.handleFrame(FINISHED, { kind: 'chat', isMain: true });
    await tick();
    assert.equal(fx.calls[0][2], 'Report ready', 'text shown only when the owner turned it on');
    assert.equal(fx.calls[0][3], false);
    assert.equal(fx.tones(), 0);
    fx.notifier.destroy();
});

test('a click on the system notification opens the same source a banner would', async () => {
    const fx = fixture({ answer: { ok: true, status: 'delivered', sound: 'os' } });
    fx.notifier.handleFrame(
        { task_id: 't2', chat_id: 9, quiz: { quiz_id: 'q1', state: 'open', wait_for_answer: true } },
        { kind: 'quiz' },
    );
    await tick();
    const token = fx.calls[0][4];
    assert.equal(fx.notifier.activateNative(token), true);
    assert.deepEqual(fx.activated, [{ chatId: 9, taskId: 't2', quizId: 'q1' }]);
    assert.equal(fx.notifier.activateNative(token), false, 'one click, one navigation');
    assert.equal(fx.notifier.activateNative('n99-unknown'), false, 'an unknown token (after a reload) only opens the window');
    fx.notifier.destroy();
});

for (const status of ['denied', 'not_determined', 'unavailable', 'failed']) {
    test(`a ${status} answer falls back to the page's own surface, with exactly one sound`, async () => {
        const fx = fixture({ answer: { ok: false, status, reason: 'x' } });
        assert.equal(fx.notifier.handleFrame(FINISHED, { kind: 'chat', isMain: true }).surface, 'native');
        await tick();
        await tick();
        assert.deepEqual(fx.toasts, ['Task finished'], 'the alert still reaches the owner');
        assert.equal(fx.calls.filter(([name]) => name === 'request_attention').length, 1, 'the existing attention cue');
        assert.equal(fx.tones(), 0, 'the launcher played the one system sound');
        fx.notifier.destroy();
    });
}

test('a broken bridge call is a fallback, never a lost alert', async () => {
    const fx = fixture({ answer: () => { throw new Error('bridge gone'); } });
    fx.notifier.handleFrame(FINISHED, { kind: 'chat', isMain: true });
    await tick();
    assert.deepEqual(fx.toasts, ['Task finished']);
    const rejected = fixture({ answer: () => Promise.reject(new Error('closed')), granted: true });
    rejected.notifier.handleFrame(FINISHED, { kind: 'chat', isMain: true });
    await tick();
    await tick();
    assert.equal(rejected.banners.length, 1, 'with browser permission the browser banner takes over');
    fx.notifier.destroy();
    rejected.notifier.destroy();
});

test('an app without the native method keeps every earlier path (3A)', async () => {
    const fx = fixture({ answer: null, extra: { show_native_notification: undefined } });
    assert.equal(fx.notifier.handleFrame(FINISHED, { kind: 'chat', isMain: true }).surface, 'in_app');
    assert.equal(fx.calls.some(([name]) => name === 'show_native_notification'), false);
    fx.notifier.destroy();
});

test('enabling asks the desktop app, not the browser, and the answer reaches Settings', async () => {
    const status = { textContent: '' };
    const attention = { textContent: 'stale' };
    let asked = 0;
    const fx = fixture({
        prefs: DEFAULT_NOTIFY_PREFS,
        answer: { ok: true, status: 'delivered' },
        documentRef: nodes({ '[data-notify-status]': [status], '[data-notify-attention-status]': [attention] }),
        extra: { request_native_notifications: () => { asked += 1; return { available: true, status: 'authorized', platform: 'macos' }; } },
    });
    fx.notifier.configure({ shell: { desktop: true, legacy: false, version: '7.7.0', native: { status: 'not_determined' } } });
    assert.match(status.textContent, /off/);
    await fx.notifier.setPref('enabled', true);
    assert.equal(asked, 1);
    assert.equal(fx.browserAsks(), 0);
    assert.match(status.textContent, /System notifications are on/);
    assert.equal(attention.textContent, '', 'no second line while the system shows the alerts');
    fx.notifier.destroy();
});

test('the status line names what the desktop app can do, and the old app honestly', () => {
    const shell = { desktop: true, legacy: false, version: '7.7.0' };
    assert.equal(nativeStatusText({ enabled: false, shell, capability: { status: 'authorized' } }), '');
    assert.equal(nativeStatusText({ enabled: true, shell: { desktop: false } }), '', 'a browser keeps the browser line');
    assert.match(nativeStatusText({ shell, capability: { status: 'authorized' } }), /in front.*own sound/);
    assert.match(nativeStatusText({ shell, capability: { status: 'not_determined' } }), /asks once/);
    assert.match(nativeStatusText({ shell, capability: { status: 'denied' } }), /denied.*notification settings/);
    assert.match(nativeStatusText({ shell, capability: { status: 'unavailable', reason: 'not_an_app_bundle' } }),
        /unavailable to this desktop app \(7\.7\.0\) here \(not_an_app_bundle\)/);
    const legacy = nativeStatusText({ shell: { desktop: true, legacy: true, version: '6.82.0' } });
    assert.match(legacy, /need the current Ouroboros desktop app; this desktop app \(6\.82\.0\) predates them/);
    assert.match(legacy, /do not replace the app itself/);
});

test('the client-level notifier answers the desktop app\'s click by token', async () => {
    resetNotifier();
    const notifier = getNotifier({ storage: storage(), documentRef: nodes(), notificationCtor: undefined, audioContextCtor: null });
    assert.equal(typeof globalThis.ouroNotifications?.activate, 'function');
    assert.equal(globalThis.ouroNotifications.activate('n1-none'), false);
    assert.equal(notifier, getNotifier());
    resetNotifier();
    assert.equal(globalThis.ouroNotifications, undefined);
});
