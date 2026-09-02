import assert from 'node:assert/strict';
import test from 'node:test';

import {
    COLLAPSED_ACTIVITY_MAX,
    boundActivityPreview,
    clearStickyCardState,
    projectCollapsedActivity,
} from '../modules/chat.js';
import { summarizeChatLiveEvent } from '../modules/log_events.js';
import { bindContentButton, plainActivityText, selectionInside, subagentIdentityTitle, subagentTwin } from '../modules/chat_activity.js';

test('named root card shows the latest activity headline under the coined title', () => {
    assert.equal(projectCollapsedActivity({
        suggestedName: 'Data Analysis',
        headline: 'Analyzing the dataset',
    }), 'Analyzing the dataset');
});

test('unnamed root card suppresses the line (title already shows the activity)', () => {
    assert.equal(projectCollapsedActivity({
        suggestedName: '',
        headline: 'Analyzing the dataset',
    }), '');
    // Suppressed even when a previous activity was remembered.
    assert.equal(projectCollapsedActivity({
        suggestedName: '',
        headline: '',
        previous: 'Earlier step',
    }), '');
});

test('subagent card always feeds the line from the routed progress body', () => {
    assert.equal(projectCollapsedActivity({
        isSubagent: true,
        body: 'Running the migration script',
    }), 'Running the migration script');
    // No coined name is required — the subagent title keeps role·model·id.
    assert.equal(projectCollapsedActivity({
        isSubagent: true,
        suggestedName: '',
        body: 'Collecting evidence',
    }), 'Collecting evidence');
});

test('a frame without new activity keeps the previous text (Done never blanks it)', () => {
    assert.equal(projectCollapsedActivity({
        suggestedName: 'Data Analysis',
        headline: '',
        previous: 'Analyzing the dataset',
    }), 'Analyzing the dataset');
    assert.equal(projectCollapsedActivity({
        isSubagent: true,
        body: '',
        previous: 'Running the migration script',
    }), 'Running the migration script');
});

test('whitespace-only frames fall back to the previous activity', () => {
    assert.equal(projectCollapsedActivity({
        suggestedName: 'X',
        headline: '   ',
        previous: 'Real step',
    }), 'Real step');
});

test('clearStickyCardState resets the recycled record activity + cost (reusable slots)', () => {
    const record = {
        collapsedActivity: 'Old cycle activity',
        costMeta: { meta: ['$1.00'], ts: 1, final: true },
        executorChip: { harness: 'codex', icon: '◇', label: 'codex · no run yet' },
        // Models the real element closely enough for attribute handling.
        activityEl: {
            textContent: 'Old cycle activity',
            title: '',
            removeAttribute(name) { if (name === 'title') this.title = ''; },
        },
    };
    record.latestActivityTs = '12:00:00';
    record.activityEl.title = 'Old cycle activity';
    clearStickyCardState(record);
    assert.equal(record.collapsedActivity, '');
    assert.equal(record.costMeta, null);
    // The sticky executor chip is cycle state: a recycled slot must not claim
    // the previous cycle's delegated route as its own.
    assert.equal(record.executorChip, null);
    assert.equal(record.activityEl.textContent, '');
    // The activity clock is cycle state too.
    assert.equal(record.latestActivityTs, '');
    assert.equal(record.activityEl.title, '');
});

test('a terminal subagent keeps its last narration as collapsed activity (replay)', () => {
    // On history replay terminal children are pre-marked before pass 1, so the
    // card is never re-driven through a working frame; the projection must
    // still return the remembered narration for the collapsed line.
    assert.equal(projectCollapsedActivity({
        isSubagent: true, body: 'Collecting evidence', previous: 'Collecting evidence',
    }), 'Collecting evidence');
    // A later empty frame does not blank it.
    assert.equal(projectCollapsedActivity({
        isSubagent: true, body: '', previous: 'Collecting evidence',
    }), 'Collecting evidence');
});

test('the collapsed projection is whitespace-normalized, bounded and explicit', () => {
    const long = 'x'.repeat(COLLAPSED_ACTIVITY_MAX + 250);
    const out = boundActivityPreview(long);
    assert.equal(out.length, COLLAPSED_ACTIVITY_MAX);
    assert.ok(out.endsWith('…'), 'the cut is visible, never silent');
    assert.equal(boundActivityPreview('  Reading\n  the   ledger  '), 'Reading the ledger');
    assert.equal(projectCollapsedActivity({ suggestedName: 'X', headline: long }), out);
});

test('root progress keeps a bounded activity preview and a complete timeline companion', () => {
    const full = `Inspecting evidence\n${'long detail '.repeat(60)}UNIQUE_ROOT_TAIL`;
    const summary = summarizeChatLiveEvent({
        type: 'send_message', is_progress: true, task_id: 'root-1', content: full,
    });
    assert.ok(summary.activityPreview.length <= COLLAPSED_ACTIVITY_MAX);
    assert.match(summary.activityPreview, /^Inspecting evidence/);
    assert.equal(summary.fullHeadline, full);
    assert.match(summary.fullHeadline, /UNIQUE_ROOT_TAIL$/);
});

test('subagent projection keeps identity, compact facts and complete disclosure', () => {
    const full = `${'Collecting evidence '.repeat(30)}UNIQUE_CHILD_TAIL`;
    const summary = summarizeChatLiveEvent({
        type: 'send_message',
        is_progress: true,
        delegation_role: 'subagent',
        subagent_task_id: 'child123456',
        parent_task_id: 'parent1',
        root_task_id: 'root1',
        subagent_role: 'researcher',
        model: 'anthropic/claude-fable-5',
        subagent_event: 'running',
        content: full,
        write_surface: 'workspace',
        status: 'running',
    });
    // Identity only: the status lives in the chip and the id shows only for twins.
    assert.equal(summary.headline, 'researcher · claude-fable-5');
    // A roleless child is `Subagent · model`: the id is a render-time twin tag, never identity.
    assert.equal(summarizeChatLiveEvent({
        type: 'send_message', is_progress: true, delegation_role: 'subagent',
        subagent_task_id: 'child987654', parent_task_id: 'parent1', model: 'openai/gpt-5.6-sol',
        subagent_event: 'running', content: 'x', status: 'running',
    }).headline, 'Subagent · gpt-5.6-sol');
    // chat.js writes the child title from the lineage map; it must read like the reducer's headline.
    assert.equal(subagentIdentityTitle({ role: 'researcher', model: 'anthropic/claude-fable-5' }), 'researcher · claude-fable-5');
    assert.equal(subagentIdentityTitle({ role: '', model: 'openai::gpt-5.6-sol' }), 'Subagent · gpt-5.6-sol');
    assert.equal(subagentIdentityTitle({ role: 'planner', model: '' }), 'planner');
    assert.ok(summary.activityPreview.length <= COLLAPSED_ACTIVITY_MAX);
    assert.match(summary.fullBody, /UNIQUE_CHILD_TAIL$/);
    assert.deepEqual(summary.meta, ['write=workspace', 'status=running']);
    assert.doesNotMatch(summary.meta.join(' '), /subagent|role=|parent=|root=/);
});

test('the collapsed activity line is plain text: the renderer\'s markdown inventory', () => {
    // The expanded timeline renders the same headline through renderMarkdown, so
    // the compact line strips that marker inventory (line by line, over-strip
    // preferred to a leaked marker).
    assert.equal(plainActivityText('**Planning a network update** I need `git fetch`'),
        'Planning a network update I need git fetch');
    assert.equal(plainActivityText('### Title\n- one\n- two [link](http://x)'), 'Title\none\ntwo link');
    assert.equal(plainActivityText('~~old~~ *new*'), 'old new');
    assert.equal(plainActivityText('#### Deep\n``x → y`` tail'), 'Deep\nx → y tail');
    assert.equal(plainActivityText('```js\nlet a = 1;\n```'), 'let a = 1;');
    assert.equal(boundActivityPreview('| a | b |\n|---|---|\n| 1 | 2 |'), 'a b 1 2');
    // Markers-only text keeps its source: an empty projection would flip the
    // reserved activity band's :empty rules on the card.
    assert.equal(plainActivityText('---'), '---');
    // Whitespace-only narration projects to nothing: the band's :empty rules
    // (reserve while running, fold when finished) need a truly empty node.
    assert.equal(boundActivityPreview(' \n\t '), '');
    assert.equal(plainActivityText(''), '');
    // Composition: the bound preview is built on the plain projection.
    assert.equal(boundActivityPreview('  **Reading**\n  the   ledger  '), 'Reading the ledger');
});

test('twins are two children of one parent with the same role and model', () => {
    const children = new Map([
        ['a', { parentId: 'p', role: 'scout', model: 'gemini-3.6-flash' }],
        ['b', { parentId: 'p', role: 'scout', model: 'gemini-3.6-flash' }],
        ['c', { parentId: 'p', role: 'reviewer', model: 'gemini-3.6-flash' }],
        ['d', { parentId: 'q', role: 'scout', model: 'gemini-3.6-flash' }],
    ]);
    assert.equal(subagentTwin(children, 'a'), true);
    assert.equal(subagentTwin(children, 'b'), true);
    assert.equal(subagentTwin(children, 'c'), false);
    assert.equal(subagentTwin(children, 'd'), false);
    assert.equal(subagentTwin(children, 'missing'), false);
    // The collision key is the DISPLAYED identity: equivalent model spellings and the
    // roleless fallback collide exactly when the headlines read the same.
    const spelled = new Map([
        ['e', { parentId: 'p', role: 'scout', model: 'openai/gpt-5.6-sol' }],
        ['f', { parentId: 'p', role: 'scout', model: 'openai::gpt-5.6-sol' }],
        ['g', { parentId: 'p', role: '', model: 'gpt-5.6-sol' }],
        ['h', { parentId: 'p', role: 'Subagent', model: 'gpt-5.6-sol' }],
    ]);
    assert.equal(subagentTwin(spelled, 'e'), true);
    assert.equal(subagentTwin(spelled, 'f'), true);
    assert.equal(subagentTwin(spelled, 'g'), true);
    assert.equal(subagentTwin(spelled, 'h'), true);
});

test('a selection inside the surface means the reader is copying, not clicking', () => {
    const inside = {}; const outside = {};
    const el = { contains: (node) => node === inside };
    assert.equal(selectionInside(el, { isCollapsed: false, anchorNode: inside }), true);
    assert.equal(selectionInside(el, { isCollapsed: true, anchorNode: inside }), false);
    assert.equal(selectionInside(el, { isCollapsed: false, anchorNode: outside }), false);
    assert.equal(selectionInside(el, null), false);
});

test('a content button toggles on click and keyboard, but not on a selecting drag', () => {
    const handlers = {}; let clicks = 0; let activated = 0;
    const inside = {};
    const el = {
        addEventListener: (type, fn) => { handlers[type] = fn; },
        click: () => { clicks += 1; handlers.click({ detail: 0 }); },
        contains: (node) => node === inside,
    };
    bindContentButton(el, () => { activated += 1; });
    const saved = globalThis.getSelection;
    globalThis.getSelection = () => ({ isCollapsed: false, anchorNode: inside });
    handlers.click({ detail: 1 });
    assert.equal(activated, 0, 'a pointer click after a selecting drag is a copy, not a toggle');
    globalThis.getSelection = () => ({ isCollapsed: true, anchorNode: inside });
    handlers.click({ detail: 1 });
    assert.equal(activated, 1);
    let prevented = 0;
    handlers.keydown({ key: 'Enter', preventDefault: () => { prevented += 1; } });
    handlers.keydown({ key: ' ', preventDefault: () => { prevented += 1; } });
    handlers.keydown({ key: 'a', preventDefault: () => { prevented += 1; } });
    assert.deepEqual([clicks, activated, prevented], [2, 3, 2]);
    globalThis.getSelection = saved;
});
