/* Reviewer comments panel: target choice, adding, resolve/reopen, removed targets and quiet polling. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'Event', 'MutationObserver', 'KeyboardEvent']) globalThis[key] = window[key];
const {CommentsView, openCounts, targetKey} = await import('../trajectory_remix/static/comments-view.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

const els = Object.fromEntries(['target', 'body', 'add', 'list', 'count'].map(k => [k, document.createElement({target: 'select', body: 'textarea', add: 'button', list: 'div', count: 'span'}[k])]));
document.body.append(...Object.values(els));
let rows = [], posted = [], patched = [], inspected = [], changes = 0;
const api = async (path, method = 'GET', body) => {
  if (method === 'GET') return {items: structuredClone(rows)};
  if (method === 'POST') { posted.push(body); rows.push({id: 'c' + rows.length, target_kind: body.target.kind, target_id: body.target.id ?? null, body: body.body, author: 'user', status: 'open', resolution: null, source_revision: 1, created: '2026-11-22T00:00:00+00:00', target_exists: true}); return {}; }
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

// A comment whose event was deleted is flagged and cannot jump.
rows.push({...rows[0], id: 'c9', target_kind: 'event', target_id: 'gone', target_exists: false});
await view.load('t1');
const gone = [...els.list.querySelectorAll('.comment-card')].at(-1);
assert.match(gone.textContent, /Removed/); assert.equal(gone.querySelector('.comment-target').disabled, true);

assert.deepEqual([...openCounts(rows)], [[targetKey('window', 'doc'), 1], ['event:gone', 1]]);
await view.load(null); assert.equal(els.list.children.length, 0); assert.equal(els.count.textContent, '');
console.log('Comments view: targets, composing from a window, adding, quiet polling, resolve/reopen and removed targets passed.');
window.happyDOM.abort();
