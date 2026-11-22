/* Job names follow what the job does, and Results precedes Usage on review jobs. Fake API only. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'MutationObserver', 'CustomEvent', 'Event']) globalThis[key] = window[key];
const {kindName} = await import('../trajectory_remix/static/ui-kit.js');
const {JobsView} = await import('../trajectory_remix/static/jobs-view.js');

assert.equal(kindName('run'), 'Generation');
assert.equal(kindName('bulk', 'review_repair'), 'Review');
assert.equal(kindName('bulk', 'delete'), 'Deletion');
assert.ok(!kindName('bulk', 'review_repair').includes('/'));

for (const id of ['jobs-list', 'jobs-prev', 'jobs-next', 'jobs-refresh', 'jobs-page-label', 'job-detail']) {
  const el = document.createElement(id.startsWith('jobs-p') || id === 'jobs-next' || id === 'jobs-refresh' ? 'button' : 'div'); el.id = id; document.body.append(el);
}
const job = {id: 'job-1', kind: 'bulk', action: 'review_repair', state: 'completed', progress: 1, total: 1, artifact: null, paused_endpoints: [], attempt_states: [], result_states: [], requests: []};
const api = async path => {
  if (path.startsWith('/job-summaries')) return [job];
  if (path.includes('/logs?')) return {items: [], last: 0, truncated: false};
  if (path.includes('/results')) return [];
  if (path.endsWith('/usage')) return {};
  if (path.startsWith('/jobs/job-1')) return structuredClone(job);
  throw new Error('Unexpected request ' + path);
};
const view = new JobsView({api, report: e => { throw e; }, download: async () => {}, openTrajectory: async () => {}, afterDetach: async () => {}, isVisible: () => true});
await view.renderList(); await view.select('job-1');
const v = view.panels.get('job-1');
assert.equal(v.title.textContent, 'Review job');
assert.equal(document.querySelector('.job-row-name').textContent, 'Review');
const order = [...document.querySelectorAll('.job-tabs [role=tab]')].filter(t => !t.hidden).map(t => t.textContent);
assert.deepEqual(order, ['Logs', 'Results', 'Usage']);
console.log('job names and tab order ok');
