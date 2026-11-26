/* Assistant proposals on the Comments tab: readable diffs, apply/discard/request changes, quiet polling. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'Event', 'MutationObserver', 'KeyboardEvent', 'CSS']) globalThis[key] = window[key];
const {lineDiff, textLines, withContext} = await import('../trajectory_remix/static/line-diff.js');
const {ProposalView, changeLabel} = await import('../trajectory_remix/static/assistant-proposal.js');
const {CommentsView} = await import('../trajectory_remix/static/comments-view.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

// Lines: multi-line content splits, so a one-line edit is one removed and one added line.
const before = {kind: 'agent_text', content: 'one\ntwo\nthree', after: ['a']};
const after = {kind: 'agent_text', content: 'one\n2\nthree', after: ['a']};
assert.deepEqual(textLines(before), ['kind: agent_text', 'content: |', '  one', '  two', '  three', 'after: ["a"]']);
const diff = lineDiff(textLines(before), textLines(after));
assert.deepEqual(diff.filter(l => l.op !== 'same'), [{op: 'del', text: '  two'}, {op: 'add', text: '  2'}]);
assert.deepEqual(textLines(null), [], 'an added entity has no before lines');
assert.equal(textLines({stage: 2, nested: {a: 1}}).join('|'), 'stage: 2|nested:|  a: 1');
const long = Array.from({length: 20}, (_, i) => `line ${i}`), edited = long.map((l, i) => i === 10 ? 'changed' : l);
const shown = withContext(lineDiff(long, edited), 2);
assert.deepEqual(shown.map(l => l.op), ['skip', 'same', 'same', 'del', 'add', 'same', 'same', 'skip']);
assert.equal(shown[0].text, '… 8 unchanged lines');
assert.equal(changeLabel({kind: 'event', id: 'x'}), 'Event x');
assert.equal(changeLabel({kind: 'defaults', id: null}), 'Defaults');

// The panel: hidden without a proposal, then a card per change with its diff.
const host = document.createElement('section'); document.body.append(host);
let proposal = null, calls = [], revision = 4, owner = null, changes = 0, applied = null, requested = [];
const api = async (path, method = 'GET', body) => {
  calls.push([method, path, body]);
  if (method === 'GET') return {proposal: structuredClone(proposal)};
  if (method === 'POST') { proposal = null; return {delta: {id: 't1'}, resolved: 1}; }
  if (method === 'DELETE') { proposal = null; return {discarded: true, reopened: 1}; }
};
const reports = [];
const view = new ProposalView({api, report: e => reports.push(e.message), host, revision: () => revision,
  locked: () => !!owner, onChange: () => { changes += 1; }, onApplied: r => { applied = r; },
  requestChanges: async text => { requested.push(text); }});
await view.load('t1');
assert.equal(host.hidden, true); assert.equal(view.pending.size, 0);

proposal = {base_revision: 3, updated: '2026-11-26T10:00:00+00:00', comment_ids: ['c1'], conflicts: [],
  changes: [{kind: 'event', id: 'x', change: 'changed', before, after}, {kind: 'metadata', id: 'note', change: 'added', before: null, after: 'hi'}]};
await view.load('t1');
assert.equal(host.hidden, false);
assert.deepEqual([...view.pending], ['c1']);
assert.match(host.querySelector('.proposal-head').textContent, /Proposed changes2 changesfrom rev 3/);
const cards = [...host.querySelectorAll('.proposal-change')];
assert.deepEqual(cards.map(c => c.querySelector('summary').textContent), ['ChangedEvent x', 'AddedMetadata note']);
assert.equal(cards[0].querySelector('.diff-del').textContent, '-   two\n');
assert.equal(cards[0].querySelector('.diff-add').textContent, '+   2\n');
assert.equal(cards[1].querySelector('.diff-add').textContent, '+ hi\n');
const button = name => [...host.querySelectorAll('button')].find(b => b.textContent === name);

// Polling with the same proposal does no DOM work; a collapsed card stays collapsed.
cards[0].open = false;
const seen = []; const observer = new MutationObserver(list => seen.push(...list)); observer.observe(host, {subtree: true, childList: true, characterData: true});
const before_ = changes; await view.load('t1'); assert.equal(seen.length, 0); assert.equal(changes, before_); observer.disconnect();
proposal.updated = '2026-11-26T10:05:00+00:00'; await view.load('t1');
assert.equal(host.querySelector('.proposal-change').open, false, 'a re-render keeps what the person collapsed');

// A job owning the trajectory, or a conflicting edit, blocks Apply.
owner = 'job1'; await view.load('t1'); assert.equal(button('Apply changes').disabled, true); assert.equal(button('Discard').disabled, true);
owner = null; proposal.conflicts = [{kind: 'event', id: 'x'}]; await view.load('t1');
assert.equal(button('Apply changes').disabled, true);
assert.match(host.querySelector('.note').textContent, /Also changed in the trajectory since: Event x/);
assert.ok(host.querySelector('.proposal-change summary').textContent.includes('Conflict'));
proposal.conflicts = []; await view.load('t1'); assert.equal(button('Apply changes').disabled, false);

// The comment list points addressed comments in the proposal at Apply instead of Accept.
const els = Object.fromEntries(['target', 'body', 'add', 'list', 'count'].map(k => [k, document.createElement({target: 'select', body: 'textarea', add: 'button', list: 'div', count: 'span'}[k])]));
const rows = [{id: 'c1', target_kind: 'trajectory', target_id: null, body: 'Fix.', author: 'user', replies: [], status: 'addressed', source_revision: 3, created: '2026-11-26', target_exists: true},
  {id: 'c2', target_kind: 'trajectory', target_id: null, body: 'Other.', author: 'user', replies: [], status: 'addressed', source_revision: 3, created: '2026-11-26', target_exists: true}];
const comments = new CommentsView({api: async () => ({items: rows}), report: () => {}, els, proposed: () => view.pending});
await comments.load('t1');
const [inProposal, other] = els.list.querySelectorAll('.comment-card');
assert.ok(!inProposal.textContent.includes('Accept') && inProposal.textContent.includes('Fix is in the proposed changes'));
assert.ok(other.textContent.includes('Accept'));

// Request changes: a note goes to the agent; empty notes are refused.
button('Request changes').click(); await settle();
const box = host.querySelector('textarea'); assert.ok(box);
button('Send to agent').click(); await settle(); assert.deepEqual(reports, ['Say what should change in the proposal.']);
box.value = 'Keep the original wording in line one.'; box.dispatchEvent(new window.Event('input'));
button('Send to agent').click(); await settle(); await settle();
assert.deepEqual(requested, ['Keep the original wording in line one.']); assert.equal(host.querySelector('textarea'), null);

// Apply sends the editor's revision and hands the result (with its Undo delta) back.
button('Apply changes').click(); await settle(); await settle();
assert.deepEqual(calls.at(-1), ['POST', '/trajectories/t1/assistant/proposal/apply', {expected_revision: 4}]);
assert.deepEqual(applied, {delta: {id: 't1'}, resolved: 1}); assert.equal(host.hidden, true); assert.equal(view.pending.size, 0);

// Discard asks first, then drops it.
proposal = {base_revision: 4, updated: 'x', comment_ids: [], conflicts: [], changes: [{kind: 'defaults', id: null, change: 'changed', before: {}, after: {a: 1}}]};
await view.load('t1');
globalThis.confirm = () => false; button('Discard').click(); await settle(); assert.notEqual(calls.at(-1)[0], 'DELETE');
globalThis.confirm = () => true; button('Discard').click(); await settle(); await settle();
assert.deepEqual(calls.at(-1), ['DELETE', '/trajectories/t1/assistant/proposal', undefined]); assert.equal(host.hidden, true);

// Switching trajectory clears the panel at once.
proposal = {base_revision: 4, updated: 'x', comment_ids: [], conflicts: [], changes: []};
await view.load('t1'); await view.load(null); assert.equal(host.hidden, true);
console.log('assistant proposal frontend ok');
