/* Assistant status: "Working..." by default, "Running `tool`" for ten seconds after each tool call. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'Event', 'MutationObserver']) globalThis[key] = window[key];
const {AssistantStatus, formatElapsed, isAssistantOwner, lastToolCall} = await import('../trajectory_remix/static/assistant-indicator.js');

const jobs = new Map([['a', {action: 'assistant'}], ['r', {action: 'review_repair'}]]);
assert.equal(isAssistantOwner('a', jobs), true);
assert.equal(isAssistantOwner('r', jobs), false);
assert.equal(isAssistantOwner(null, jobs), false);
assert.equal(lastToolCall([{text: '[t] Assistant tool: view'}, {text: 'other'}, {text: '[t] Assistant tool: patch'}, {text: 'x'}]), 'patch');
assert.equal(lastToolCall([{text: 'nothing'}]), null);

const logs = [{sequence: 1, text: 'Assistant tool: old'}];
const api = async path => {
  const after = Number(/after=(\d+)/.exec(path)[1]), limit = Number(/limit=(\d+)/.exec(path)[1]);
  return {last: logs.at(-1).sequence, items: logs.filter(l => l.sequence > after).slice(0, limit)};
};
let pending = null, cleared = 0, holdMs = 0;
const setTimer = (fn, ms) => { pending = fn; holdMs = ms; return 1; }, clearTimer = () => { cleared++; pending = null; };
let clock = 1000, tick = null;
const setTick = fn => { tick = fn; return 2; }, clearTick = () => { tick = null; };
const host = document.createElement('div'); document.body.append(host);
const status = new AssistantStatus({host, api, now: () => clock, setTimer, clearTimer, setTick, clearTick});
assert.deepEqual([0, 43, 60, 73, 3725].map(formatElapsed), ['0s', '43s', '1m 0s', '1m 13s', '1h 2m 5s']);
const text = () => host.textContent;

assert.equal(host.hidden, false === true);
await status.update(null);
assert.equal(host.hidden, true);
await status.update('job', 73);
assert.equal(host.hidden, false);
assert.equal(text(), 'Working... (1m 13s)', 'tool calls from before we started watching are ignored');
assert.equal(host.querySelectorAll('.working-dots span').length, 3);

logs.push({sequence: 2, text: 'Assistant tool: view'});
await status.update('job');
assert.equal(text(), 'Running view... (1m 13s)');
clock += 30000; tick();
assert.equal(text(), 'Running view... (1m 43s)', 'the counter ticks and keeps the job total');
assert.equal(host.querySelector('code').textContent, 'view');
assert.equal(holdMs, 10000, 'the tool name holds for ten seconds');
const first = pending;
logs.push({sequence: 3, text: 'Assistant tool: patch'}, {sequence: 4, text: 'unrelated'});
await status.update('job');
assert.equal(text(), 'Running patch... (1m 43s)', 'a newer call overwrites the text');
assert.ok(cleared >= 1 && pending !== first, 'and restarts the hold');
await status.update('job');
assert.equal(text(), 'Running patch... (1m 43s)', 'no new calls: still holding');
pending();
assert.equal(text(), 'Working... (1m 43s)', 'falls back after the hold');

const toggle = host.querySelector('.working-toggle'), items = () => [...host.querySelectorAll('.working-tools li')].map(li => li.textContent);
assert.deepEqual(items(), [], 'collapsed by default');
toggle.click();
assert.deepEqual(items(), ['old', 'view', 'patch'], 'expanding lists every tool run so far, including earlier ones');
logs.push({sequence: 5, text: 'Assistant tool: grep'});
await status.update('job');
assert.deepEqual(items(), ['old', 'view', 'patch', 'grep']);
assert.equal(toggle.getAttribute('aria-expanded'), 'true');
assert.equal(host.querySelector('.working-tools').tagName, 'UL', 'a bulleted list, not numbered');

await status.update(null);
assert.equal(host.hidden, true);
await status.update('job2', 0);
assert.deepEqual(items(), [], 'the list is discarded when the job ends');
await status.update(null);
assert.equal(tick, null, 'the ticker stops with the job');
console.log('assistant indicator ok');
