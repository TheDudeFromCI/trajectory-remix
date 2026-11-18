/* Job log lines are coloured by status level and appended in place. Fake API only. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'MutationObserver', 'CustomEvent', 'Event']) globalThis[key] = window[key];
const {JobsView, logLine} = await import('../trajectory_remix/static/jobs-view.js');

for (const id of ['jobs-list', 'jobs-prev', 'jobs-next', 'jobs-refresh', 'jobs-page-label', 'job-detail']) {
  const el = document.createElement(id.startsWith('jobs-p') || id === 'jobs-next' || id === 'jobs-refresh' ? 'button' : 'div'); el.id = id; document.body.append(el);
}
const job = {id: 'job-1', kind: 'run', state: 'running', progress: 0, total: 1, artifact: null, paused_endpoints: [], attempt_states: [], result_states: [], requests: []};
const logs = {
  0: {items: [{sequence: 1, text: 'queued', level: 'info'}, {sequence: 2, text: 'retrying', level: 'warning'}, {sequence: 3, text: 'exhausted', level: 'error'}], last: 3, truncated: false},
  3: {items: [{sequence: 4, text: 'done', level: 'success'}, {sequence: 5, text: 'from an older server'}, {sequence: 6, text: 'odd', level: 'bogus'}], last: 6, truncated: false}
};
const api = async path => {
  const match = path.match(/^\/jobs\/job-1\/logs\?after=(\d+)&limit=100$/);
  if (match) return structuredClone(logs[match[1]] || {items: [], last: 6, truncated: false});
  if (path.startsWith('/job-summaries')) return [job];
  if (path.startsWith('/jobs/job-1/attempts')) return [];
  if (path.endsWith('/phase')) return {enabled: false};
  if (path.endsWith('/usage')) throw new Error('No usage route'); // the Usage tab simply hides
  if (path.startsWith('/jobs/job-1')) return structuredClone(job);
  throw new Error('Unexpected request ' + path);
};
const view = new JobsView({api, report: e => { throw e; }, download: async () => {}, openTrajectory: async () => {}, afterDetach: async () => {}, isVisible: () => true});
await view.renderList(); await view.select('job-1');
const box = view.panels.get('job-1').logs, levels = () => [...box.children].map(el => el.dataset.level);
assert.deepEqual(levels(), ['info', 'warning', 'error']);
assert.deepEqual([...box.children].map(el => el.className), ['log-line level-info', 'log-line level-warning', 'log-line level-error']);
assert.equal(box.textContent, 'queued\nretrying\nexhausted\n', 'copying the log still gives one line per entry');

// New lines are appended in place; an older server's missing level and an unknown level are plain info.
const first = box.firstElementChild;
await view.refresh('job-1');
assert.equal(box.firstElementChild, first);
assert.deepEqual(levels(), ['info', 'warning', 'error', 'success', 'info', 'info']);
assert.equal(view.panels.get('job-1').logCount.textContent, '6 lines');
await view.refresh('job-1');
assert.equal(box.childElementCount, 6, 'a refresh with nothing new changes nothing');
assert.equal(logLine('x', 'error').className, 'log-line level-error');
console.log('Job log levels: coloured lines, plain fallback and in-place appends passed.');
window.happyDOM.abort();
