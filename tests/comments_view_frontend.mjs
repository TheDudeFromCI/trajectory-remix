/* Reviewer comments panel: target choice, adding, resolve/reopen, removed targets and quiet polling. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'Event', 'MutationObserver', 'KeyboardEvent', 'CSS']) globalThis[key] = window[key];
const {CommentsView, unresolvedCounts, targetKey} = await import('../trajectory_remix/static/comments-view.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

const els = Object.fromEntries(['target', 'body', 'add', 'list', 'count'].map(k => [k, document.createElement({target: 'select', body: 'textarea', add: 'button', list: 'div', count: 'span'}[k])]));
document.body.append(...Object.values(els));
let rows = [], replies = [], posted = [], patched = [], inspected = [], changes = 0;
const api = async (path, method = 'GET', body) => {
  if (method === 'GET') return {items: structuredClone(rows)};
  if (method === 'POST' && !path.endsWith('/replies')) { posted.push(body); rows.push({id: 'c' + rows.length, target_kind: body.target.kind, target_id: body.target.id ?? null, body: body.body, author: 'user', replies: [], status: 'open', source_revision: 1, created: '2026-11-22T00:00:00+00:00', target_exists: true}); return {}; }
  if (method === 'POST' && path.endsWith('/replies')) { const r = rows.find(x => path.includes(x.id)); replies.push(body); r.replies.push({id: 'r' + r.replies.length, author: 'user', body: body.body, created: '2026-11-22'}); if (body.reopen) r.status = 'open'; return r; }
  if (method === 'PATCH') { patched.push([path, body]); const r = rows.find(x => path.endsWith(x.id)); r.status = body.status; return r; }
  if (method === 'DELETE') { rows = rows.filter(x => !path.endsWith(x.id)); return {}; }
};
const reports = [];
const view = new CommentsView({api, report: e => reports.push(e.message), els, inspect: (k, id) => inspected.push([k, id]), onChange: () => { changes += 1; }});
const draft = {events: {a: {}, b: {}}, windows: {doc: {}}};

// Targets: trajectory, each event, each window; the composer follows the selected event.
view.setContext(draft, 'a');
assert.deepEqual([...els.target.options].map(o => o.value), ['trajectory', 'event:a', 'event:b', 'window:doc']);
assert.equal(els.target.value, 'event:a');
view.setContext(draft, 'b'); assert.equal(els.target.value, 'event:b');

// A window card points the composer at its window, and that choice sticks while typing.
view.compose('window', 'doc'); els.body.value = 'Not from any document.';
view.setContext(draft, 'a'); assert.equal(els.target.value, 'window:doc');

await view.load('t1'); els.body.value = 'Not from any document.'; els.add.click(); await settle(); await settle();
assert.deepEqual(posted, [{target: {kind: 'window', id: 'doc'}, body: 'Not from any document.'}]);
assert.equal(els.body.value, ''); assert.equal(els.count.textContent, '1'); assert.equal(view.count('window', 'doc'), 1);
view.setContext(draft, 'b'); assert.equal(els.target.value, 'event:b', 'after posting, the composer follows the selection again');

// Empty comments are refused without a request.
els.add.click(); await settle(); assert.deepEqual(reports, ['Write the comment first.']); assert.equal(posted.length, 1);

// Polling with unchanged data does not touch the DOM.
const seen = []; const observer = new MutationObserver(list => seen.push(...list)); observer.observe(els.list, {subtree: true, childList: true, characterData: true, attributes: true});
const before = changes; await view.load('t1'); await settle();
assert.equal(seen.length, 0); assert.equal(changes, before); observer.disconnect();

// Resolve moves it under "Resolved"; reopen brings it back. The target button jumps to it.
els.list.querySelector('.comment-target').click(); assert.deepEqual(inspected, [['window', 'doc']]);
[...els.list.querySelectorAll('button')].find(b => b.textContent === 'Resolve').click(); await settle(); await settle();
assert.deepEqual(patched.at(-1), ['/comments/c0', {status: 'resolved'}]);
assert.equal(els.count.textContent, ''); assert.match(els.list.querySelector('details summary').textContent, /Resolved \(1\)/);
[...els.list.querySelectorAll('button')].find(b => b.textContent === 'Reopen').click(); await settle(); await settle();
assert.equal(els.count.textContent, '1');

// Replies: a draft reply survives a re-render from polling, then posts and shows in the thread.
const buttonNamed = name => [...els.list.querySelectorAll('button')].find(b => b.textContent === name);
buttonNamed('Reply').click();
const draftBox = () => els.list.querySelector('.comment-reply-form textarea');
draftBox().value = 'Event c reads it later.'; draftBox().dispatchEvent(new window.Event('input'));
rows[0].body = 'Not from any document (edited).'; await view.load('t1');
assert.equal(draftBox().value, 'Event c reads it later.', 'polling keeps the reply being typed');
buttonNamed('Send reply').click(); await settle(); await settle();
assert.equal(draftBox(), null); assert.match(els.list.querySelector('.comment-thread').textContent, /Event c reads it later\./);

// An agent says it's done: it waits for review. Request changes reopens it with a reply; Accept resolves.
rows[0].status = 'addressed'; rows[0].replies.push({id: 'ra', author: 'agent', body: 'Added a read of the listing.', created: '2026-11-22'});
await view.load('t1');
assert.match(els.list.textContent, /Awaiting your review \(1\)/); assert.match(els.list.textContent, /Awaiting review/);
assert.equal(els.count.textContent, '1', 'addressed comments still count as unresolved');
assert.equal(buttonNamed('Resolve'), undefined, 'addressed comments are accepted, not resolved directly');
buttonNamed('Request changes').click();
assert.equal(buttonNamed('Send and reopen') !== undefined, true);
draftBox().value = 'The listing is never shown to the model.'; draftBox().dispatchEvent(new window.Event('input'));
buttonNamed('Send and reopen').click(); await settle(); await settle();
assert.deepEqual(replies.at(-1), {body: 'The listing is never shown to the model.', reopen: true});
assert.equal(rows[0].status, 'open'); assert.doesNotMatch(els.list.textContent, /Awaiting your review/);
rows[0].status = 'addressed'; await view.load('t1');
buttonNamed('Accept').click(); await settle(); await settle();
assert.deepEqual(patched.at(-1), ['/comments/c0', {status: 'resolved'}]); assert.equal(els.count.textContent, '');
buttonNamed('Reopen').click(); await settle(); await settle();

// A comment whose event was deleted is flagged and cannot jump.
rows.push({...rows[0], id: 'c9', target_kind: 'event', target_id: 'gone', target_exists: false});
await view.load('t1');
const gone = [...els.list.querySelectorAll('.comment-card')].at(-1);
assert.match(gone.textContent, /Removed/); assert.equal(gone.querySelector('.comment-target').disabled, true);

assert.deepEqual([...unresolvedCounts(rows)], [[targetKey('window', 'doc'), 1], ['event:gone', 1]]);
await view.load(null); assert.equal(els.list.children.length, 0); assert.equal(els.count.textContent, '');
console.log('Comments view: targets, composing from a window, adding, quiet polling, resolve/reopen, replies, accept/request changes and removed targets passed.');
window.happyDOM.abort();
