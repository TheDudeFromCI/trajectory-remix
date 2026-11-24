/* Auto-send comments: the Send button greys out with an explanatory tooltip, and new comments are sent at once. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'Event', 'MutationObserver', 'KeyboardEvent', 'CSS']) globalThis[key] = window[key];
const {CommentsView} = await import('../trajectory_remix/static/comments-view.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

const tags = {target: 'select', body: 'textarea', add: 'button', list: 'div', count: 'span', send: 'button'};
const els = Object.fromEntries(Object.entries(tags).map(([k, t]) => [k, document.createElement(t)]));
els.send.dataset.tip = 'Send open comments';
document.body.append(...Object.values(els));
let rows = [], auto = false, sent = 0;
const api = async (path, method = 'GET', body) => {
  if (method === 'GET') return {items: structuredClone(rows)};
  rows.push({id: 'c' + rows.length, target_kind: body.target.kind, target_id: null, body: body.body, author: 'user', replies: [], status: 'open', source_revision: 1, created: '2026-11-22', target_exists: true});
  return {};
};
const view = new CommentsView({api, report: e => { throw e; }, els, autoSend: () => auto, onOpened: async () => { sent += 1; }});
view.setContext({events: {}, windows: {}}, null);

// Disabled by default: the button works as before and nothing is sent automatically.
await view.load('t1'); assert.equal(els.send.disabled, true);
els.body.value = 'First.'; els.add.click(); await settle(); await settle();
assert.equal(sent, 0); assert.equal(els.send.disabled, false); assert.equal(els.send.dataset.tip, 'Send open comments');

// Auto mode: button greyed out with an explanatory tooltip, and adding a comment sends it.
auto = true; view.render();
assert.equal(els.send.disabled, true); assert.match(els.send.dataset.tip, /Auto-send is enabled/);
els.body.value = 'Second.'; els.add.click(); await settle(); await settle();
assert.equal(sent, 1);

// Turning it off restores the button and its original tooltip.
auto = false; view.render();
assert.equal(els.send.disabled, false); assert.equal(els.send.dataset.tip, 'Send open comments');
console.log('Comments auto-send: default off, greyed button with tooltip, send on add and restore passed.');
window.happyDOM.abort();
