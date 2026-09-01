import assert from 'node:assert/strict';
import test from 'node:test';

import {
    BoundedDetailMap,
    CONCLUDED_ACTIVITY_LEDGER_MAX,
    LIVE_CARD_RECORDS_CAP,
    RETIRED_TASK_IDS_CAP,
    SKILL_REVIEW_DETAIL_CAP,
    createChatLedgers,
} from '../modules/chat_ledgers.js';
import { loadSkillReviewDetail } from '../modules/skill_review_card.js';

function build() {
    const maps = {
        taskKey: (value) => String(value || '').trim(),
        liveCardRecords: new Map(),
        taskUiStates: new Map(),
        retiredTaskIds: new Set(),
        explicitCardExpansion: new Map(),
        reviewDisclosureByTask: new Map(),
        skillReviewDetailStore: new Map(),
        pendingSuggestedNames: new Map(),
        cancelableTaskIds: new Set(),
        concludedDirectActivities: new Map(),
        activeDirectActivities: new Map(),
        missingManagedTaskIds: new Set(),
        subagentChildParents: new Map(),
    };
    return { maps, ledgers: createChatLedgers(maps) };
}

const record = (finished) => ({
    finished,
    root: { removed: false, remove() { this.removed = true; } },
});

test('no eviction at or under the cap', () => {
    const { maps, ledgers } = build();
    for (let i = 0; i < LIVE_CARD_RECORDS_CAP; i += 1) maps.liveCardRecords.set(`t${i}`, record(true));
    assert.deepEqual(ledgers.evictFinishedCardsOverCap(), []);
    assert.equal(maps.liveCardRecords.size, LIVE_CARD_RECORDS_CAP);
});

test('over cap: the oldest FINISHED record goes, with its DOM and satellites', () => {
    const { maps, ledgers } = build();
    maps.liveCardRecords.set('unfinished-oldest', record(false));
    for (let i = 0; i < LIVE_CARD_RECORDS_CAP; i += 1) maps.liveCardRecords.set(`t${i}`, record(true));
    const victim = maps.liveCardRecords.get('t0');
    maps.explicitCardExpansion.set('t0', true);
    maps.reviewDisclosureByTask.set('t0', {});
    maps.pendingSuggestedNames.set('t0', 'name');
    maps.cancelableTaskIds.add('t0');
    const timer = setTimeout(() => {}, 60000);
    maps.taskUiStates.set('t0', { taskId: 't0', cleanupTimer: timer });

    const evicted = ledgers.evictFinishedCardsOverCap();

    assert.deepEqual(evicted, ['t0'], 'oldest finished, never the older unfinished one');
    assert.equal(victim.root.removed, true, 'the DOM card leaves with the record');
    assert.equal(maps.liveCardRecords.has('t0'), false);
    assert.equal(maps.liveCardRecords.has('unfinished-oldest'), true);
    assert.equal(maps.retiredTaskIds.has('t0'), true);
    assert.equal(maps.explicitCardExpansion.has('t0'), false);
    assert.equal(maps.reviewDisclosureByTask.has('t0'), false);
    assert.equal(maps.pendingSuggestedNames.has('t0'), false);
    assert.equal(maps.cancelableTaskIds.has('t0'), false);
    assert.equal(maps.taskUiStates.has('t0'), false);
    assert.equal(maps.liveCardRecords.size, LIVE_CARD_RECORDS_CAP);
});

test('all-unfinished over cap evicts nothing', () => {
    const { maps, ledgers } = build();
    for (let i = 0; i < LIVE_CARD_RECORDS_CAP + 5; i += 1) maps.liveCardRecords.set(`t${i}`, record(false));
    assert.deepEqual(ledgers.evictFinishedCardsOverCap(), []);
    assert.equal(maps.liveCardRecords.size, LIVE_CARD_RECORDS_CAP + 5);
});

test('a reusable logical slot id is never retired on eviction', () => {
    const { maps, ledgers } = build();
    maps.liveCardRecords.set('active', record(true));
    for (let i = 0; i < LIVE_CARD_RECORDS_CAP; i += 1) maps.liveCardRecords.set(`t${i}`, record(true));
    const evicted = ledgers.evictFinishedCardsOverCap();
    assert.deepEqual(evicted, ['active']);
    assert.equal(maps.retiredTaskIds.has('active'), false);
});

test('the job-keyed review-detail store is a bounded Map on its own keys', () => {
    const store = new BoundedDetailMap();
    for (let i = 0; i < SKILL_REVIEW_DETAIL_CAP + 3; i += 1) {
        store.set(`skill:job-${i}`, { heavy: i });
    }
    assert.ok(store instanceof Map, 'the consumer type-gates on instanceof Map');
    assert.equal(store.size, SKILL_REVIEW_DETAIL_CAP);
    assert.equal(store.has('skill:job-0'), false);
    assert.equal(store.has(`skill:job-${SKILL_REVIEW_DETAIL_CAP + 2}`), true);
});

test('scheduleTaskUiCleanup clears state and retires the id (moved verbatim)', async () => {
    const { maps, ledgers } = build();
    const state = { taskId: 't-done', cleanupTimer: null };
    maps.taskUiStates.set('t-done', state);
    ledgers.scheduleTaskUiCleanup(state, 1);
    await new Promise((resolve) => setTimeout(resolve, 15));
    assert.equal(maps.taskUiStates.has('t-done'), false);
    assert.equal(maps.retiredTaskIds.has('t-done'), true);
});

test('recordTerminalActivity: normal ids conclude, reusable slots reset', () => {
    const { maps, ledgers } = build();
    maps.activeDirectActivities.set('t1', {});
    maps.missingManagedTaskIds.add('t1');
    ledgers.recordTerminalActivity('t1');
    assert.equal(maps.activeDirectActivities.has('t1'), false);
    assert.equal(maps.missingManagedTaskIds.has('t1'), false);
    assert.equal(maps.concludedDirectActivities.has('t1'), true);

    maps.concludedDirectActivities.set('active', Date.now());
    ledgers.recordTerminalActivity('active');
    assert.equal(maps.concludedDirectActivities.has('active'), false);
});

test('concluded-activity ledger keeps its 200-entry cap', () => {
    const { maps, ledgers } = build();
    for (let i = 0; i < CONCLUDED_ACTIVITY_LEDGER_MAX + 4; i += 1) {
        ledgers.recordConcludedActivity(`a${i}`);
    }
    assert.equal(maps.concludedDirectActivities.size, CONCLUDED_ACTIVITY_LEDGER_MAX);
    assert.equal(maps.concludedDirectActivities.has('a0'), false);
});


test('the real detail-load consumer accepts and writes the bounded store', async () => {
    const store = new BoundedDetailMap();
    const full = { dataset: {}, innerHTML: '', textContent: '', replaceChildren() {} };
    const state = await loadSkillReviewDetail(
        full,
        { skill: 'alpha', jobId: 'job-1' },
        {
            store,
            fetchImpl: async () => ({ ok: true, json: async () => ({ markdown: '# detail' }) }),
            render: () => '<h1>detail</h1>',
            onDomWrite: (fn) => fn(),
        },
    );
    assert.equal(state, 'loaded');
    assert.equal(store.size, 1, 'the consumer must not silently discard the bounded store');
    assert.equal([...store.values()][0].state, 'loaded');
});

test('eviction spares converted project chips and unfinished-child parents', () => {
    const { maps, ledgers } = build();
    const converted = record(true);
    converted.root.dataset = { projectCreated: '1' };
    maps.liveCardRecords.set('converted', converted);
    maps.liveCardRecords.set('parent', record(true));
    maps.liveCardRecords.set('child-live', record(false));
    maps.subagentChildParents.set('child-live', { parentId: 'parent' });
    for (let i = 0; i < LIVE_CARD_RECORDS_CAP; i += 1) maps.liveCardRecords.set(`t${i}`, record(true));

    const evicted = ledgers.evictFinishedCardsOverCap();

    assert.ok(!evicted.includes('converted'), 'a project chip is never evicted');
    assert.ok(!evicted.includes('parent'), 'a parent with unfinished children is composite in-flight work');
    assert.deepEqual(evicted, ['t0', 't1', 't2']);
});

test('evicting a finished parent cascades its finished child records', () => {
    const { maps, ledgers } = build();
    maps.liveCardRecords.set('parent', record(true));
    maps.liveCardRecords.set('child-a', record(true));
    maps.subagentChildParents.set('child-a', { parentId: 'parent' });
    for (let i = 0; i < LIVE_CARD_RECORDS_CAP; i += 1) maps.liveCardRecords.set(`t${i}`, record(true));

    const evicted = ledgers.evictFinishedCardsOverCap();

    assert.deepEqual(evicted.slice(0, 2), ['parent', 'child-a'],
        'the parent subtree carried the child DOM; the child record goes with it');
    assert.equal(maps.liveCardRecords.has('child-a'), false);
});

test('the retired-id memory is bounded FIFO', () => {
    const { maps, ledgers } = build();
    for (let i = 0; i < RETIRED_TASK_IDS_CAP + LIVE_CARD_RECORDS_CAP + 10; i += 1) {
        maps.liveCardRecords.set(`t${i}`, record(true));
    }
    ledgers.evictFinishedCardsOverCap();
    assert.ok(maps.retiredTaskIds.size <= RETIRED_TASK_IDS_CAP);
});

test('eviction forgets hydration state so a recreated card hydrates fresh', () => {
    const { maps } = build();
    const dropped = [];
    const ledgers = createChatLedgers({ ...maps, reviewHydrator: { drop: (id) => dropped.push(id) } });
    for (let i = 0; i < LIVE_CARD_RECORDS_CAP + 1; i += 1) maps.liveCardRecords.set(`t${i}`, record(true));
    assert.deepEqual(ledgers.evictFinishedCardsOverCap(), ['t0']);
    assert.deepEqual(dropped, ['t0']);
});

test('a dropped hydration cannot apply its in-flight detail afterwards', async () => {
    const { createReviewHydrator } = await import('../modules/review_presentation.js');
    const applied = [];
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const hydrator = createReviewHydrator({
        fetchDetail: async () => { await gate; return { plan_review_state: {} }; },
        applyDetail: (id) => { applied.push(id); return true; },
    });
    const pending = hydrator.hydrate('t-evicted', 7);
    hydrator.drop('t-evicted');
    release();
    await pending;
    assert.deepEqual(applied, [], 'detail resolved after drop() must not apply');
});


test('a dropped hydration cannot flip status to error afterwards either', async () => {
    const { createReviewHydrator } = await import('../modules/review_presentation.js');
    const statuses = [];
    let reject;
    const gate = new Promise((_resolve, rej) => { reject = rej; });
    const hydrator = createReviewHydrator({
        fetchDetail: async () => { await gate; },
        applyDetail: () => true,
        onState: (id, status) => statuses.push(`${id}:${status}`),
    });
    const pending = hydrator.hydrate('t-evicted', 7);
    hydrator.drop('t-evicted');
    reject(new Error('late transport failure'));
    await pending;
    assert.deepEqual(statuses, ['t-evicted:loading'],
        'the rejection path honors the same identity bail as success');
});

test('eviction eligibility and cascade see the WHOLE lineage, not only children', () => {
    const { maps, ledgers } = build();
    // Composite A: finished parent -> finished child -> UNFINISHED grandchild.
    maps.liveCardRecords.set('a-parent', record(true));
    maps.liveCardRecords.set('a-child', record(true));
    maps.liveCardRecords.set('a-grand', record(false));
    maps.subagentChildParents.set('a-child', { parentId: 'a-parent' });
    maps.subagentChildParents.set('a-grand', { parentId: 'a-child' });
    // Composite B: finished parent -> CONVERTED (project chip) child.
    const chip = record(true);
    chip.root.dataset = { projectCreated: '1' };
    maps.liveCardRecords.set('b-parent', record(true));
    maps.liveCardRecords.set('b-chip', chip);
    maps.subagentChildParents.set('b-chip', { parentId: 'b-parent' });
    // Composite C: fully finished two-level tree — the only legal victim.
    maps.liveCardRecords.set('c-parent', record(true));
    maps.liveCardRecords.set('c-child', record(true));
    maps.liveCardRecords.set('c-grand', record(true));
    maps.subagentChildParents.set('c-child', { parentId: 'c-parent' });
    maps.subagentChildParents.set('c-grand', { parentId: 'c-child' });
    for (let i = 0; i < LIVE_CARD_RECORDS_CAP; i += 1) maps.liveCardRecords.set(`t${i}`, record(false));

    const evicted = ledgers.evictFinishedCardsOverCap();

    assert.deepEqual(evicted, ['c-parent', 'c-child', 'c-grand'],
        'whole-lineage cascade of the one evictable composite, in tree order');
    assert.ok(maps.liveCardRecords.has('a-parent'), 'unfinished grandchild protects the composite');
    assert.ok(maps.liveCardRecords.has('a-child'), 'and its intermediate child');
    assert.ok(maps.liveCardRecords.has('b-parent'), 'a converted descendant protects its parent');
    assert.ok(maps.liveCardRecords.has('b-chip'), 'the chip itself is never evicted');
});


test('cyclic lineage metadata terminates instead of hanging the eviction scan', () => {
    const { maps, ledgers } = build();
    maps.liveCardRecords.set('cyc-a', record(true));
    maps.liveCardRecords.set('cyc-b', record(true));
    maps.subagentChildParents.set('cyc-b', { parentId: 'cyc-a' });
    maps.subagentChildParents.set('cyc-a', { parentId: 'cyc-b' });
    for (let i = 0; i < LIVE_CARD_RECORDS_CAP; i += 1) maps.liveCardRecords.set(`t${i}`, record(false));

    const evicted = ledgers.evictFinishedCardsOverCap();

    assert.deepEqual(evicted, ['cyc-a', 'cyc-b'],
        'the cycle is walked once, evicted once, and the scan returns');
    assert.equal(maps.liveCardRecords.size, LIVE_CARD_RECORDS_CAP);
});
