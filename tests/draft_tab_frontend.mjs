/* Editor Draft tab: shows and edits metadata.draft, New draft and Assemble buttons and locks. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'Event']) globalThis[key] = window[key];
const {DraftTab, blocker, draftText} = await import('../trajectory_remix/static/draft-tab.js');

assert.equal(draftText({metadata: {draft: 'notes'}}), 'notes');
assert.equal(draftText({metadata: {draft: 3}}), '');
assert.match(blocker({source: null}), /Select a trajectory/);
assert.match(blocker({source: {}, locked: true, roles: {draft: 'g'}}), /job is working/);
assert.match(blocker({source: {}, roles: {}}), /Draft endpoint role/);
assert.equal(blocker({source: {}, roles: {draft: 'g'}}), '');

const els = {text: document.createElement('textarea'), generate: document.createElement('button'), assemble: document.createElement('button'), note: document.createElement('span')};
document.body.append(...Object.values(els));
let source = {metadata: {}}, locked = false, roles = {draft: 'g'}, confirmed = true;
const started = [], order = [];
const tab = new DraftTab({els, source: () => source, locked: () => locked, roles: () => roles,
  edit: text => { source.metadata.draft = text; }, ready: async () => order.push('ready'),
  start: async action => { order.push(action); started.push(action); }, report: e => { throw e; }, confirm: () => confirmed});

tab.render();
assert.equal(els.text.value, ''); assert.equal(els.generate.disabled, false);
assert.equal(els.assemble.disabled, true, 'nothing to assemble without a draft');

els.text.value = 'My plan'; els.text.dispatchEvent(new Event('input'));
assert.equal(source.metadata.draft, 'My plan'); assert.equal(els.assemble.disabled, false);

await tab.run('assemble');
assert.deepEqual(order, ['ready', 'assemble'], 'pending edits save before the pass');
confirmed = false; await tab.run('draft');
assert.deepEqual(started, ['assemble'], 'declining keeps the current draft');
confirmed = true; await tab.run('draft');
assert.deepEqual(started, ['assemble', 'draft']);

locked = true; tab.render();
assert.equal(els.text.disabled, true); assert.equal(els.generate.disabled, true); assert.equal(els.assemble.disabled, true);
locked = false; roles = {}; tab.render();
assert.equal(els.generate.disabled, true); assert.match(els.note.textContent, /Draft endpoint role/);
console.log('draft tab frontend ok');
