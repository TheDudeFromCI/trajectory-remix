/* Auto-send comments: new comments and replies on open comments are sent at once; the Send button is always enabled. */
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
  const reply = path.match(/^\/comments\/(\w+)\/replies$/);
  if (reply) {
    const row = rows.find(r => r.id === reply[1]);
    row.replies.push({author: 'user', body: body.body});
    if (body.reopen) row.status = 'open';
    return {};
  }
  rows.push({id: 'c' + rows.length, target_kind: body.target.kind, target_id: null, body: body.body, author: 'user', replies: [], status: 'open', source_revision: 1, created: '2026-11-22', target_exists: true});
  return {};
};
const view = new CommentsView({api, report: e => { throw e; }, els, autoSend: () => auto, onOpened: async () => { sent += 1; }});
view.setContext({events: {}, windows: {}}, null);

// The button is enabled even with no comments, and nothing is sent automatically when auto is off.
await view.load('t1'); assert.equal(els.send.disabled, false);
els.body.value = 'First.'; els.add.click(); await settle(); await settle();
assert.equal(sent, 0); assert.equal(els.send.disabled, false); assert.equal(els.send.dataset.tip, 'Send open comments');

// Auto mode: the button stays enabled for manual resends, and adding a comment sends it.
auto = true; view.render();
assert.equal(els.send.disabled, false); assert.equal(els.send.dataset.tip, 'Send open comments');
els.body.value = 'Second.'; els.add.click(); await settle(); await settle();
assert.equal(sent, 1);

// A reply on a still-open comment starts a job; a plain reply on an addressed one does not.
rows[0].replies.length = 0;
view.replies.set('c0', {text: 'Answer.', reopen: false});
await view.sendReply(rows[0]); assert.equal(sent, 2);
rows[1].status = 'addressed'; await view.load('t1');
view.replies.set('c1', {text: 'Thanks.', reopen: false});
await view.sendReply(rows[1]); assert.equal(sent, 2);
view.replies.set('c1', {text: 'Still wrong.', reopen: true});
await view.sendReply(rows[1]); assert.equal(sent, 3);
console.log('Comments auto-send: always-enabled button, send on add, reply and request changes passed.');
window.happyDOM.abort();
