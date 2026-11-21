/* Polling re-renders must not rebuild unchanged DOM: no server, fake API only. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'MutationObserver', 'CustomEvent', 'Event']) globalThis[key] = window[key];
const {reconcile, progressBar, node} = await import('../trajectory_remix/static/ui-kit.js');
const {JobsView} = await import('../trajectory_remix/static/jobs-view.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
async function mutations(target, run) {
  const seen = []; const observer = new MutationObserver(list => seen.push(...list));
  observer.observe(target, {childList: true, subtree: true, characterData: true, attributes: true});
  await run(); await settle(); observer.disconnect(); return seen;
}

// Keyed reconciliation reuses rows, updates in place and only touches changed structure.
const list = document.createElement('div'); document.body.append(list);
const draw = items => reconcile(list, items, i => i.id, i => node('div', i.text), (el, i) => { if (el.textContent !== i.text) el.textContent = i.text; });
draw([{id: 'a', text: 'A'}, {id: 'b', text: 'B'}]);
const [first, second] = list.children;
assert.equal((await mutations(list, () => draw([{id: 'a', text: 'A'}, {id: 'b', text: 'B'}]))).length, 0);
draw([{id: 'b', text: 'B2'}, {id: 'a', text: 'A'}, {id: 'c', text: 'C'}]);
assert.equal(list.children[0], second); assert.equal(list.children[1], first);
assert.deepEqual([...list.children].map(n => n.textContent), ['B2', 'A', 'C']);
draw([{id: 'c', text: 'C'}]); assert.equal(list.children.length, 1);

// A progress bar given the same values twice does not touch the DOM.
const bar = progressBar('Run'); document.body.append(bar);
bar.update({value: 3, total: 6, state: 'running'});
assert.equal((await mutations(bar, () => bar.update({value: 3, total: 6, state: 'running'}))).length, 0);
assert.match(bar.textContent, /3\/6/);

// The job detail panel keeps its nodes across identical refreshes (the old dialog flickered).
for (const id of ['jobs-list', 'jobs-prev', 'jobs-next', 'jobs-refresh', 'jobs-page-label', 'job-detail']) {
  const el = document.createElement(id.startsWith('jobs-p') || id === 'jobs-next' || id === 'jobs-refresh' ? 'button' : 'div'); el.id = id; document.body.append(el);
}
const job = {id: 'job-1', kind: 'run', state: 'running', progress: 2, total: 5, artifact: null, paused_endpoints: [], attempt_states: [{state: 'active', count: 3}], result_states: [], requests: []};
const responses = {
  '/job-summaries?limit=50&offset=0': [job],
  '/jobs/job-1?include_config=false': job,
  '/jobs/job-1/logs?after=0&limit=100': {items: [{sequence: 1, text: 'started'}], last: 1, truncated: false},
  '/jobs/job-1/logs?after=1&limit=100': {items: [], last: 1, truncated: false},
  '/jobs/job-1/attempts?limit=100&offset=0': [{id: 'a1', ordinal: 0, state: 'active', trajectory_id: 'traj-0001'}],
  '/jobs/job-1/phase': {enabled: false, phase: 'generation'}
};
const api = async path => { if (!(path in responses)) throw new Error('Unexpected request ' + path); return structuredClone(responses[path]); };
const view = new JobsView({api, report: e => { throw e; }, download: async () => {}, openTrajectory: async () => {}, afterDetach: async () => {}, isVisible: () => true});
await view.renderList(); await view.select('job-1');
const panel = view.panels.get('job-1').root, row = document.getElementById('jobs-list').firstElementChild;
assert.equal(panel.dataset.state, 'run · running');
assert.match(panel.textContent, /Pause/); assert.match(panel.textContent, /traj-000/);
assert.equal((await mutations(document.body, async () => { await view.refresh('job-1'); await view.renderList(); })).length, 0);
assert.equal(document.getElementById('jobs-list').firstElementChild, row);
responses['/jobs/job-1?include_config=false'] = {...job, state: 'paused', progress: 3};
await view.refresh('job-1');
assert.equal(view.panels.get('job-1').root, panel); assert.equal(panel.dataset.state, 'run · paused');
assert.equal(view.panels.get('job-1').buttons.resume.disabled, false);
// Skip barriers shows only for a run with a phase barrier that is still generating.
assert.equal(view.panels.get('job-1').reviews.hidden, true);
responses['/jobs/job-1/phase'] = {enabled: true, phase: 'generation'};
await view.refresh('job-1');
assert.equal(view.panels.get('job-1').reviews.hidden, false);
responses['/jobs/job-1/phase'] = {enabled: true, phase: 'review'};
await view.refresh('job-1');
assert.equal(view.panels.get('job-1').reviews.hidden, true);
console.log('Studio UI: keyed rows, idempotent progress and flicker-free job refresh passed.');
window.happyDOM.abort();
