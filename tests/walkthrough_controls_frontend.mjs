/* Walkthrough panel controls: stop a waiting step, follow saved settings, read cut-off replies. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'CustomEvent', 'Event']) globalThis[key] = window[key];
const {WalkthroughPanel} = await import('../trajectory_remix/static/flow-walkthrough.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

const knows = {node: 'choose', steps: 0, max_steps: 9, events: 0, max_events: 9, last_selection: null, pending_task: null, correction: '', format_errors: 0, format_retries: 2, chat_echo_pending: null, windows: [], diagnostics: []};
const request = max => ({operation: 'flow_select', schema: {type: 'object'}, messages: [{role: 'user', content: 'x'}], endpoint: 'local', wire: {model: 'm', max_tokens: max, messages: [{role: 'user', content: 'x'}]}, budget: {input_bytes: 1, input_ceiling: 9}});
const state = rev => ({version: 'flow-walkthrough/1', settings_revision: rev, plan: {proposal: {}}, source: {id: 'abcdef12-0000-4000-8000-000000000000', events: {}}, checkpoint: {}, done: false});
const calls = []; let release;
const api = async (path, method, body) => {
  calls.push({path, body: structuredClone(body)});
  if (path === '/walkthrough/start') return {state: state(1), next: {action: 'model', node: 'choose', summary: 'Ask', request: request(4096), knows, settings_revision: 1}};
  if (path === '/walkthrough/describe?latest_settings=true') return {action: 'model', node: 'choose', summary: 'Ask', request: request(8192), knows, settings_revision: 2};
  if (path === '/walkthrough/cancel') { release({state: body.state ?? state(2), next: {action: 'model', node: 'choose', summary: 'Ask', request: request(8192), knows, settings_revision: 2}, trace: {node: 'choose', kind: 'select', action: 'model', accepted: false, error: 'Stopped before a reply arrived; nothing changed', to: 'choose', knows, added_events: [], settings_revision: 2}}); return {cancelled: true}; }
  if (path === '/walkthrough/step') {
    if (calls.filter(c => c.path === '/walkthrough/step').length === 1) return new Promise(resolve => { release = resolve; });
    return {state: state(2), next: {action: 'model', node: 'choose', summary: 'Ask', request: request(8192), knows, settings_revision: 2},
      trace: {node: 'choose', kind: 'select', action: 'model', accepted: false, error: 'cut off', to: 'choose', knows, added_events: [], settings_revision: 2,
        request: request(8192), response: {invalid_output: true, finish_reason: 'length', raw_reasoning: 'Okay, let me think about this episode...', usage: {completion_tokens: 8192}}}};
  }
  throw new Error('Unexpected ' + path);
};
const root = document.createElement('div'), strip = document.createElement('nav'); document.body.append(root, strip);
const panel = new WalkthroughPanel(root, strip, {api, flow: () => ({}), report: () => {}});
await panel.start();
const $ = id => root.querySelector('#' + id);
assert.equal($('walk-latest').checked, true, 'follows saved settings by default');

// Applying saved settings refreshes the next request and records the new revision.
$('walk-apply').click(); await settle();
assert.equal(panel.latest.next.request.wire.max_tokens, 8192);
assert.equal(panel.latest.state.settings_revision, 2);

// A waiting step can be stopped; the step body carries the option and a call id.
const pending = panel.advance(); await settle();
assert.equal($('walk-stop').hidden, false, 'Stop shows while a step waits');
const sent = calls.findLast(c => c.path === '/walkthrough/step').body;
assert.equal(sent.latest_settings, true); assert.match(sent.call_id, /^c\w+/);
$('walk-stop').click(); await settle();
assert.deepEqual(calls.findLast(c => c.path === '/walkthrough/cancel').body, {call_id: sent.call_id});
await pending;
assert.match(root.querySelector('#walk-summary').textContent, /Stopped before a reply arrived/);
assert.equal($('walk-stop').hidden, true);

// Turning the option off sends frozen settings.
$('walk-latest').checked = false;
await panel.advance();
assert.equal(calls.findLast(c => c.path === '/walkthrough/step').body.latest_settings, false);
// A cut-off reply shows what the model was writing.
root.querySelector('[data-tab="reply"]').click();
const reply = root.querySelector('#walk-detail').textContent;
assert.match(reply, /cut off at Max output tokens/); assert.match(reply, /let me think about this episode/);
assert.match(root.querySelector('#walk-summary').textContent, /settings r2/);
console.log('Walkthrough controls: stop a waiting step, apply saved settings and cut-off reply preview passed.');

// Copy buttons: each tab copies something useful, and each message copies itself exactly.
{
  const {messagesText} = await import('../trajectory_remix/static/flow-walkthrough.js');
  const copied = [];
  Object.defineProperty(globalThis, 'navigator', {value: {clipboard: {writeText: async text => { copied.push(text); }}}, configurable: true});
  assert.equal(messagesText([{role: 'system', content: 'rules'}, {role: 'user', content: 'data'}]), '### system\nrules\n\n### user\ndata');
  root.querySelector('[data-tab="prompt"]').click();
  const tabCopy = [...root.querySelectorAll('#walk-detail button')].find(b => b.textContent === 'Copy' && b.dataset.tip === 'Copy this tab');
  tabCopy.click(); await settle();
  assert.equal(copied.at(-1), '### user\nx', 'the prompt tab copies the messages sent');
  const json = [...root.querySelectorAll('#walk-detail button')].find(b => b.textContent === 'Copy JSON');
  json.click(); await settle();
  assert.equal(JSON.parse(copied.at(-1)).max_tokens, 8192, 'Copy JSON copies the request body');
  const perMessage = root.querySelector('.flow-message .flow-copy'); perMessage.click(); await settle();
  assert.equal(copied.at(-1), 'x');
  assert.equal(perMessage.textContent, 'Copied');
  root.querySelector('[data-tab="reply"]').click();
  [...root.querySelectorAll('#walk-detail button')].find(b => b.textContent === 'Copy').click(); await settle();
  assert.equal(JSON.parse(copied.at(-1)).response.finish_reason, 'length');
  console.log('Walkthrough copy buttons: tab, JSON body and per-message copies passed.');
}
window.happyDOM.abort();
