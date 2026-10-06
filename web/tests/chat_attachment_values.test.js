import assert from 'node:assert/strict';
import test from 'node:test';
import { attachmentCaption, attachmentTail, attachmentViews } from '../modules/chat_attachments.js';
import { uploadView } from './helpers/attachment_views.js';

test('the caption hides only the exact generated tail and text the row marks as the host\'s', () => {
    const views = [uploadView('one.png', 'image'), uploadView('two.pdf', 'file')];
    const tail = attachmentTail(views.map((view) => view.name));
    assert.equal(tail, '[Attached file: one.png]\n[Attached file: two.pdf]');
    assert.equal(attachmentCaption(`Look\n\n${tail}`, views), 'Look');
    assert.equal(attachmentCaption(tail, views), '');
    // Host text is hidden only when the row marks it as the host's (`text_placeholder`).
    assert.equal(attachmentCaption('(image attached)', views, { placeholder: true }), '');
    assert.equal(attachmentCaption('[user attachment: one.png]', views, { placeholder: true }), '',
        'the mark covers the whole text, whichever host label it is');
    assert.equal(attachmentCaption('(image attached)', views), '(image attached)', 'typed by the owner, kept');
    // The owner's own words that merely look like a tail stay exactly as written.
    assert.equal(attachmentCaption('Look [Attached file: one.png]', views), 'Look [Attached file: one.png]');
    assert.equal(attachmentCaption(`Look\n\n${tail}`, [views[1], views[0]]), `Look\n\n${tail}`);
    assert.equal(attachmentCaption(`Look\n\n${tail}`, []), `Look\n\n${tail}`);
    const many = Array.from({ length: 27 }, (_, i) => `f${i}.txt`);
    assert.ok(attachmentTail(many).endsWith('[Attached file: f24.txt]\n[2 more attached files]'));
});

test('every attachment renders past the text tail, and its exact tail still hides', () => {
    const views = Array.from({ length: 30 }, (_, i) => uploadView(`shot-${i}.png`, 'image'));
    assert.equal(attachmentViews(views).length, 30, 'no silent cap: the tail bound is text-only');
    const tail = attachmentTail(views.map((view) => view.name));
    assert.ok(tail.endsWith('[5 more attached files]'));
    assert.equal(attachmentCaption(`Тридцать\n\n${tail}`, attachmentViews(views)), 'Тридцать');
});

test('attachment views keep their closed shape and refuse a foreign URL', () => {
    const [ok, foreign, weird] = attachmentViews([
        uploadView('a.png', 'image'),
        uploadView('b.png', 'image', { url: 'https://evil.example/a.png' }),
        { name: 'c\nd', kind: 'script', available: true, url: `/api/files/download?upload=${'b'.repeat(32)}_c` },
    ]);
    assert.equal(ok.available && ok.kind === 'image' && ok.url.startsWith('/api/files/download?upload='), true);
    assert.deepEqual([foreign.available, foreign.kind, foreign.url], [false, 'file', '']);
    assert.deepEqual([weird.name, weird.kind], ['c d', 'file']);
});
