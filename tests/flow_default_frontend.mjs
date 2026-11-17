/* Flow tab: Load default asks first, loads an unsaved draft, and saves the default's text.
   Also the flow-wide Immediate response checkbox. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';

const window = new Window();
for (const key of ['document', 'CustomEvent', 'Event', 'MutationObserver']) globalThis[key] = window[key];
const settle = () => new Promise(resolve => setTimeout(resolve, 5));
const {FlowView} = await import('../trajectory_remix/static/flow-view.js');

const flow = (instruction, extra = {}) => ({
  version: 'adaptive-flow/1', start: 'choose', max_steps: 600, max_events: 128, actor_instruction: 'Act.', ...extra,
  nodes: {choose: {kind: 'select', instruction, next: 'done'}, done: {kind: 'end'}}
});
const saved = {flow: flow('My edited prompt'), flow_hash: 'a'.repeat(64), text: 'mine\n', origin: 'project', revision: 'r1', diagnostics: []};
const packaged = {flow: flow('Default prompt', {immediate_response: true}), flow_hash: 'c'.repeat(64), text: '# default\n', origin: 'packaged', revision: null, diagnostics: []};
const calls = [];
const api = async (path, method = 'GET', body) => {
  calls.push({path, method, body});
  if (path === '/generation/flow' && method === 'GET') return structuredClone(saved);
  if (path === '/generation/flow/default') return structuredClone(packaged);
  if (path === '/generation/flow/check') return {valid: true, diagnostics: [], flow: body.flow, flow_hash: 'b'.repeat(64), yaml: 'x\n'};
  if (path === '/generation/flow' && method === 'PUT') return {...structuredClone(saved), text: body.yaml, flow: packaged.flow, revision: 'r2'};
  throw new Error('Unexpected request ' + method + ' ' + path);
};
const root = document.createElement('section'); document.body.append(root);
const view = new FlowView(root, {api, delay: 0, storage: {getItem: () => null, setItem: () => {}}});
await view.open();
const button = root.querySelector('#flow-default');
assert.equal(button.textContent, 'Load default');

// Declining the confirmation changes nothing and asks the server nothing.
let asked = '';
globalThis.confirm = text => { asked = text; return false; };
await view.loadDefault();
assert.match(asked, /Replace your project flow with the default flow\?/);
assert.match(asked, /Nothing is saved until you click Save flow/);
assert.ok(!calls.some(c => c.path === '/generation/flow/default'));
assert.equal(view.draft.nodes.choose.instruction, 'My edited prompt');

// Accepting loads the default as an unsaved draft; saving sends the default's own text.
globalThis.confirm = () => true;
await view.loadDefault();
assert.equal(view.draft.nodes.choose.instruction, 'Default prompt');
assert.equal(root.querySelector('#flow-dirty').textContent, 'Unsaved changes');
assert.equal(root.querySelector('#flow-save').disabled, false);
assert.match(root.querySelector('#flow-status').textContent, /default flow as a draft/);
assert.ok(!calls.some(c => c.method === 'PUT'), 'nothing saved yet');
await view.save();
assert.deepEqual(calls.at(-1), {path: '/generation/flow', method: 'PUT', body: {yaml: '# default\n', expected_revision: 'r1'}});

// The Flow settings tab has the Immediate response checkbox.
view.tab = 'flow'; view.renderSide();
const box = root.querySelector('[aria-label="Immediate response"]');
assert.equal(box.checked, true);
box.checked = false; box.dispatchEvent(new Event('change'));
assert.equal(view.draft.immediate_response, false);
await settle();
console.log('Flow default: confirmation, unsaved draft, save and Immediate response passed.');
window.happyDOM.abort();
