/* Job usage tab: formatting, missing values and no DOM churn on identical polls. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'MutationObserver']) globalThis[key] = window[key];
const {renderUsage, formatValue, duration} = await import('../trajectory_remix/static/usage-view.js');
const row = (n, cost) => ({requests: n, prompt_tokens: 1000 * n, cached_tokens: 700 * n, completion_tokens: 50 * n, reasoning_tokens: 0, cost, seconds: n});
const report = {job_id: 'j', trajectories: 2, wall_seconds: 125, models: ['vendor/model'], total: row(10, 0.0123), per_trajectory: row(5, 0.00615),
  operations: {draft: row(6, 0.009), review: row(4, null)}};
const box = document.createElement('div'); document.body.append(box);
const signature = renderUsage(box, report);
const text = box.textContent;
assert.match(text, /Per trajectory \(2\)/); assert.match(text, /Drafts/); assert.match(text, /Reviews/);
assert.match(text, /\$0\.0123/); assert.match(text, /7,000 \(70%\)/); assert.match(text, /vendor\/model/);
const missing = [...box.querySelectorAll('td')].find(td => td.textContent === '—');
assert.ok(missing?.dataset.tip, 'missing values explain themselves');
// An identical poll changes nothing.
const seen = []; const observer = new MutationObserver(list => seen.push(...list));
observer.observe(box, {childList: true, subtree: true, characterData: true, attributes: true});
assert.equal(renderUsage(box, structuredClone(report), signature), signature);
await new Promise(resolve => setTimeout(resolve, 0)); observer.disconnect();
assert.equal(seen.length, 0);
assert.match(text, /Wall time, first to last response: 2m 05s/);
assert.equal(duration(42), '42s'); assert.equal(duration(7620), '2h 07m'); assert.equal(formatValue('seconds', 65), '1m 05s');
assert.equal(formatValue('cost', 1.5), '$1.50'); assert.equal(formatValue('cost', null), '—');
renderUsage(box, {total: null}); assert.match(box.textContent, /No model responses yet/);
console.log('Usage view: totals, per-trajectory averages, missing values and idempotent polling passed.');
window.happyDOM.abort();
