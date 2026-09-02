// The job-keyed skill-review detail cache is bounded (issue #135) and remains
// a Map subclass, because its only consumer type-gates on `instanceof Map`.
import assert from 'node:assert/strict';
import test from 'node:test';

import {
    BoundedDetailMap,
    SKILL_REVIEW_DETAIL_CAP,
    loadSkillReviewDetail,
} from '../modules/skill_review_card.js';

test('the detail store trims FIFO past the cap on its own keys', () => {
    const store = new BoundedDetailMap();
    for (let i = 0; i < SKILL_REVIEW_DETAIL_CAP + 3; i += 1) store.set(`skill:job-${i}`, { heavy: i });
    assert.ok(store instanceof Map);
    assert.equal(store.size, SKILL_REVIEW_DETAIL_CAP);
    assert.equal(store.has('skill:job-0'), false, 'the oldest entries left first');
    assert.equal(store.has(`skill:job-${SKILL_REVIEW_DETAIL_CAP + 2}`), true);
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
