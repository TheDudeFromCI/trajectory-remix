/* Editor Metrics tab: grouping, value parsing, read-only measured metrics, locking and saves. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'Event']) globalThis[key] = window[key];
const {MetricsEditor, parseNote, showNote, groupOf} = await import('../trajectory_remix/static/metrics-editor.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

assert.equal(parseNote('  '), undefined); assert.equal(parseNote('null'), undefined);
assert.equal(parseNote('true'), true); assert.equal(parseNote('3'), 3); assert.deepEqual(parseNote('["a"]'), ['a']);
assert.equal(parseNote('looks fine'), 'looks fine'); assert.equal(showNote({a: 1}), '{"a":1}'); assert.equal(showNote('x'), 'x');
assert.equal(groupOf({kind: 'number', scope: 'source'}), 'measured'); assert.equal(groupOf({kind: 'labels', scope: 'annotation'}), 'labels');

const def = (kind, scope, choices) => ({kind, scope, ...(choices ? {choices} : {}), revision: 'r'});
let revision = 1;
const values = () => ({
  lifecycle: {status: 'unavailable', observation: undefined, definition: def('category', 'annotation', ['open_ended', 'closed'])},
  event_tags: {status: 'available', observation: {value: ['x']}, definition: def('labels', 'annotation', ['x', 'y'])},
  actual_outcome: {status: 'stale', observation: {value: 'ok'}, definition: def('annotation', 'annotation')},
  authored_windows: {status: 'available', observation: {value: 2}, definition: {...def('number', 'source'), unit: 'count'}},
});
const calls = [];
const api = async (path, method = 'GET', body) => {
  if (method !== 'GET') { calls.push([method, path, body]); revision += 1; }
  return {id: 't1', revision, values: values()};
};
const els = {list: document.createElement('div'), note: document.createElement('span'), unset: document.createElement('input')};
els.unset.type = 'checkbox'; document.body.append(...Object.values(els));
let saved = 0;
let flushed = 0;
const editor = new MetricsEditor({api, report: e => { throw e; }, els, beforeSave: async () => { flushed += 1; }, onSaved: view => { saved += 1; assert.ok(view); }});

editor.sync({id: 't1', revision: 1, locked: false}); await settle();
const text = els.list.textContent;
for (const heading of ['Categories', 'Labels', 'Outcomes and notes', 'Measured']) assert.match(text, new RegExp(heading));
assert.equal(els.list.querySelectorAll('select').length, 1);
assert.equal(els.list.querySelectorAll('.metric-label-grid input').length, 2);
const measured = [...els.list.querySelectorAll('.metric-row')].find(r => r.textContent.includes('authored windows'));
assert.equal(measured.querySelector('[data-metric-input]'), null, 'measured metrics are read-only');
assert.match(text, /Confirm/, 'stale values can be confirmed');

// Choosing a category stores it against the current revision.
const select = els.list.querySelector('select'); select.value = 'closed'; select.dispatchEvent(new Event('change'));
await editor.busy;
assert.deepEqual(calls.at(-1), ['PUT', '/metrics/trajectories/t1/lifecycle', {expected_revision: 1, value: 'closed', origin: 'manual_annotation'}]);
assert.equal(saved, 1); assert.equal(flushed, 1, 'pending trajectory edits save first');

// Clearing uses DELETE on the latest revision.
editor.sync({id: 't1', revision: 2, locked: false});
const clear = [...els.list.querySelectorAll('button')].find(b => b.textContent === 'Clear');
clear.click(); await editor.busy;
assert.equal(calls.at(-1)[0], 'DELETE'); assert.deepEqual(calls.at(-1)[2], {expected_revision: 2});

// A lock disables every control and explains why.
editor.sync({id: 't1', revision: 3, locked: true});
assert.ok([...els.list.querySelectorAll('[data-metric-input]')].every(i => i.disabled));
assert.match(els.note.textContent, /job holds/);

// "Only show unset" hides set values.
editor.sync({id: 't1', revision: 3, locked: false}); await settle();
els.unset.checked = true; els.unset.dispatchEvent(new Event('change'));
assert.doesNotMatch(els.list.textContent, /authored windows/); assert.match(els.list.textContent, /lifecycle/);
console.log('Metrics editor: grouping, parsing, read-only measured values, saves, clears and locking, flush-first and Undo deltas passed.');
window.happyDOM.abort();
