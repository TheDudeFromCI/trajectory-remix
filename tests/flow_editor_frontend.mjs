/* Flow editor and walkthrough panel against a fake API: no server, no model. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';

const window = new Window();
for (const key of ['document', 'CustomEvent', 'Event', 'MutationObserver']) globalThis[key] = window[key];
globalThis.confirm = () => true;
const settle = () => new Promise(resolve => setTimeout(resolve, 5));
const model = await import('../trajectory_remix/static/flow-model.js');
const {FlowView} = await import('../trajectory_remix/static/flow-view.js');
const {WalkthroughPanel, replyTemplate} = await import('../trajectory_remix/static/flow-walkthrough.js');

const base = () => ({
  version: 'adaptive-flow/1', start: 'choose', max_steps: 600, max_events: 128, actor_instruction: 'Act.',
  nodes: {
    choose: {kind: 'select', instruction: 'Pick', next: 'finished', path: 'finished', equals: true, then: null, otherwise: null},
    finished: {kind: 'branch', instruction: '', next: null, path: 'finished', equals: true, then: 'done', otherwise: 'write'},
    write: {kind: 'event', instruction: 'Write it.', next: 'choose', path: 'finished', equals: true, then: null, otherwise: null},
    done: {kind: 'end', instruction: '', next: null, path: 'finished', equals: true, then: null, otherwise: null}
  }
});

// ---------- Pure model ----------
{
  const flow = model.compactFlow(base());
  assert.deepEqual(flow.nodes.done, {kind: 'end'}, 'defaults are dropped');
  assert.deepEqual(Object.keys(flow.nodes.finished), ['kind', 'path', 'equals', 'then', 'otherwise']);
  const at = model.layout(flow);
  assert.ok(at.choose.y < at.finished.y && at.finished.y < at.done.y, 'layers follow distance from start');
  assert.ok(at.done.x < at.write.x, 'then is placed left of otherwise so links do not cross');
  assert.equal(new Set(Object.values(at).map(p => `${p.x},${p.y}`)).size, 4, 'no overlapping cards');

  model.renameNode(flow, 'choose', 'pick');
  assert.equal(flow.start, 'pick'); assert.equal(flow.nodes.write.next, 'pick');
  assert.deepEqual(Object.keys(flow.nodes), ['pick', 'finished', 'write', 'done'], 'rename keeps order');
  assert.throws(() => model.renameNode(flow, 'pick', 'write'), /already exists/);

  model.setKind(flow, 'write', 'branch');
  assert.equal(flow.nodes.write.then, 'pick', 'changing kind keeps the outgoing link');
  model.removeNode(flow, 'pick');
  assert.equal(flow.nodes.write.then, null); assert.equal(flow.start, 'finished');
  assert.deepEqual(model.edges(flow).filter(e => e.to === null).map(e => e.port), ['then', 'otherwise'], 'dangling links are reported');
  assert.equal(model.addNode(flow, 'select'), 'select'); assert.equal(model.addNode(flow, 'select'), 'select_2');
}

// ---------- Editor ----------
const calls = [];
let doc = {flow: base(), flow_hash: 'a'.repeat(64), text: '# note\nversion: adaptive-flow/1\n', origin: 'packaged', revision: 'packaged', diagnostics: [], path: '/p/flow.yaml'};
const api = async (path, method = 'GET', body) => {
  calls.push({path, method, body: structuredClone(body)});
  if (path === '/generation/flow' && method === 'GET') return structuredClone(doc);
  if (path === '/generation/flow/check') {
    const flow = body.flow || base();
    const broken = Object.entries(flow.nodes).filter(([, n]) => ['next', 'then', 'otherwise'].some(k => k in n && n[k] === null && model.KINDS[n.kind].ports.includes(k)));
    const diagnostics = broken.map(([id]) => ({severity: 'error', code: 'missing_target', message: 'not connected', node: id, field: 'next'}));
    return {valid: !broken.length, diagnostics, flow: broken.length ? null : flow, flow_hash: 'b'.repeat(64), yaml: 'rendered: yes\n'};
  }
  if (path === '/generation/flow' && method === 'PUT') { doc = {...doc, flow: body.flow || base(), origin: 'project', revision: 'r2', text: body.yaml || 'saved\n'}; return structuredClone(doc); }
  throw new Error('Unexpected request ' + method + ' ' + path);
};
const storage = new Map();
const root = document.createElement('section'); document.body.append(root);
const view = new FlowView(root, {api, delay: 0, storage: {getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v)}});
await view.open();
assert.equal(root.querySelectorAll('.flow-node').length, 4);
assert.equal(root.querySelectorAll('.flow-edge').length, 4, 'one link per port');
assert.equal(root.querySelector('#flow-save').disabled, true, 'nothing to save yet');
assert.match(root.querySelector('[data-node="choose"]').getAttribute('aria-label'), /start/);

// Selecting by keyboard opens the node form.
root.querySelector('[data-node="write"]').dispatchEvent(new window.KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
assert.equal(root.querySelector('#flow-tab-body input').value, 'write');
const instruction = root.querySelector('#flow-tab-body textarea');
instruction.value = 'Write carefully.'; instruction.dispatchEvent(new Event('input'));
assert.equal(root.querySelector('#flow-tab-body textarea'), instruction, 'typing keeps the same field (and focus)');
assert.equal(view.draft.nodes.write.instruction, 'Write carefully.');
assert.equal(root.querySelector('#flow-dirty').textContent, 'Unsaved changes');
await settle();
assert.ok(calls.some(c => c.path === '/generation/flow/check' && c.body.flow.nodes.write.instruction === 'Write carefully.'));

// Disconnecting a link marks the node, blocks Save, and shows the problem count.
const next = root.querySelector('[aria-label="Next node"]'); next.value = ''; next.dispatchEvent(new Event('change'));
await settle();
assert.ok(root.querySelector('[data-node="write"]').classList.contains('has-error'));
assert.equal(root.querySelector('#flow-save').disabled, true);
assert.match(root.querySelector('[data-tab="problems"]').textContent, /Problems \(1\)/);
assert.ok(root.querySelector('.flow-edge.is-dangling'));
// Connecting via the canvas (as a port drag does) fixes it.
view.canvas.handlers.onConnect('write', 'next', 'choose'); await settle();
assert.equal(root.querySelector('#flow-save').disabled, false);

// Save sends the chart with the loaded revision.
root.querySelector('#flow-save').click(); await settle();
const save = calls.findLast(c => c.method === 'PUT');
assert.equal(save.body.expected_revision, 'packaged');
assert.equal(save.body.flow.nodes.write.instruction, 'Write carefully.');
assert.ok(!('yaml' in save.body));
assert.equal(root.querySelector('#flow-dirty').textContent, 'Saved');

// Applying YAML text saves that text verbatim, so comments survive.
root.querySelector('[data-tab="yaml"]').click();
const area = root.querySelector('.flow-yaml');
area.value = '# keep me\nversion: adaptive-flow/1\n'; area.dispatchEvent(new Event('input'));
root.querySelector('#flow-tab-body .panel-actions button').click(); await settle();
root.querySelector('#flow-save').click(); await settle();
assert.equal(calls.findLast(c => c.method === 'PUT').body.yaml, '# keep me\nversion: adaptive-flow/1\n');

// Moving a card persists the layout for this browser.
view.canvas.handlers.onMove('done', {x: 900, y: 40});
assert.equal(JSON.parse(storage.get('flow-layout')).done.x, view.positions.done.x);

// ---------- Walkthrough panel ----------
const knows = {node: 'choose', steps: 0, max_steps: 600, events: 0, max_events: 128, last_selection: null, pending_task: null, correction: '', format_errors: 0, format_retries: 2, chat_echo_pending: null, windows: [], diagnostics: []};
const request = {operation: 'flow_select', schema: {type: 'object'}, messages: [{role: 'system', content: 'Pick'}, {role: 'user', content: '{"proposal":{"a":1}}'}], endpoint: 'g', wire: {model: 'm', messages: [{role: 'system', content: 'Pick'}, {role: 'user', content: '{"proposal":{"a":1}}'}, {role: 'user', content: 'Return JSON'}]}, budget: {input_bytes: 10, input_ceiling: 99}};
const walkCalls = [];
const state = n => ({version: 'flow-walkthrough/1', settings_revision: 1, plan: {proposal: {focus: 'x'}}, source: {id: 'abcdef12-0000-4000-8000-000000000000', events: n ? {opening: {kind: 'chat_message', window: 'room', after: [], content: 'Hi'}} : {}}, checkpoint: {}, done: false});
const walkApi = async (path, method, body) => {
  walkCalls.push({path, body: structuredClone(body)});
  if (path === '/walkthrough/start') return {state: state(0), next: {action: 'model', node: 'choose', kind: 'select', summary: 'Ask', request, knows}};
  if (path === '/walkthrough/step') {
    if (body.mode === 'manual') return {state: body.state, next: {action: 'model', node: 'choose', summary: 'Ask', request, knows: {...knows, correction: 'bad'}}, trace: {node: 'choose', kind: 'select', action: 'model', accepted: false, error: 'bad', to: 'choose', request, response: {output: body.response, model: 'manual'}, knows, added_events: []}};
    return {state: state(1), next: {action: 'branch', node: 'finished', summary: 'If finished', knows}, trace: {node: 'choose', kind: 'select', action: 'model', accepted: true, error: null, to: 'finished', request, response: {output: {finished: false}, model: 'm', usage: {prompt_tokens: 5}}, knows, added_events: ['opening'], summary: 'Chose opening'}};
  }
  if (path === '/trajectories') return {};
  throw new Error('Unexpected ' + path);
};
const panelRoot = document.createElement('div'), strip = document.createElement('nav'); document.body.append(panelRoot, strip);
let trail = null; const reports = [];
const panel = new WalkthroughPanel(panelRoot, strip, {api: walkApi, flow: () => ({start: 'choose'}), pins: () => ({domain: 'law'}), onTrail: t => { trail = t; }, report: r => reports.push(r)});
assert.match(panelRoot.querySelector('#walk-pins').textContent, /domain = law/);
panelRoot.querySelector('#walk-seed').value = '7';
await panel.start();
assert.deepEqual(walkCalls[0].body, {seed: 7, overrides: {domain: 'law'}, flow: {start: 'choose'}}, 'sends seed, pins and the draft chart');
assert.match(panelRoot.querySelector('#walk-detail').textContent, /Return JSON/, 'the prompt tab shows the exact wire messages');
assert.match(panelRoot.querySelector('#walk-detail').textContent, /added for JSON mode/);
await panel.advance();
assert.equal(strip.children.length, 2);
assert.deepEqual(trail, {current: 'finished', visits: {choose: 1}, edge: {from: 'choose', port: 'next'}});
assert.match(panelRoot.querySelector('#walk-detail').textContent, /accepted/, 'a model step opens its reply');
panelRoot.querySelector('[data-tab="trajectory"]').click();
assert.ok(panelRoot.querySelector('.flow-events li.is-new'), 'new events are marked');
// History: step back, rewind, and typed replies.
panelRoot.querySelector('#walk-prev').click();
assert.equal(panelRoot.querySelector('#walk-step').disabled, true, 'only the newest position can advance');
panelRoot.querySelector('#walk-rewind').click();
assert.equal(panel.entries.length, 1);
panelRoot.querySelector('#walk-mode').value = 'manual';
panelRoot.querySelector('#walk-mode').dispatchEvent(new Event('change'));
panelRoot.querySelector('[data-tab="reply"]').click();
const reply = panelRoot.querySelector('#walk-reply');
assert.deepEqual(JSON.parse(reply.value), JSON.parse(replyTemplate(request)));
reply.value = '{nope'; reply.dispatchEvent(new Event('input'));
await assert.rejects(panel.advance(), /not valid JSON/);
reply.value = '{"finished": true}'; reply.dispatchEvent(new Event('input'));
await panel.advance();
assert.deepEqual(walkCalls.at(-1).body.response, {finished: true});
assert.equal(walkCalls.at(-1).body.mode, 'manual');
assert.ok(strip.lastElementChild.classList.contains('is-rejected'));
assert.equal(trail.edge, null, 'rejected steps do not light a link');
await assert.rejects(panel.runToEnd(), /switch from typed replies/);

console.log('Flow editor: model edits, layout, draft/save/YAML, problems and walkthrough history passed.');
window.happyDOM.abort();
