/* Tool checkboxes for tool_schema events: ticking re-renders the listing; only chosen tools are stored. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'Event']) globalThis[key] = window[key];
const {listedNames, renderToolPicker} = await import('../trajectory_remix/static/tool-picker.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const line = name => `{"name":"${name}","description":"d","parameters":{}}`;

const text = [line('a'), 'not json', line('b'), line('c')].join('\n');
assert.deepEqual(listedNames(text), ['a', 'b', 'c']);

const container = document.createElement('div'); document.body.append(container);
const tools = {a: {}, b: {}, c: {}, d: {}}; let relisted = null; const reports = [];
const draw = (listing, extra = {}) => renderToolPicker(container, {text: listing, tools, relist: async chosen => { relisted = chosen; }, report: m => reports.push(m), ...extra});
const box = name => container.querySelector(`[aria-label="List tool ${name}"]`);
const tick = async (name, checked) => { box(name).checked = checked; box(name).dispatchEvent(new window.Event('change')); await settle(); };

// Chosen tools are ticked and every tool stays selectable.
draw([line('a'), line('c')].join('\n'));
assert.equal(box('a').checked, true); assert.equal(box('b').checked, false); assert.equal(box('c').checked, true);
assert.equal(box('c').disabled, false); assert.equal(box('d').disabled, false);

// Ticking sends the new chosen set; unticking removes one.
await tick('b', true); assert.deepEqual(relisted, ["a", "c", "b"]);
await tick('a', false); assert.deepEqual(relisted, ['c']);

// Chosen tools the project no longer has stay until unticked.
draw([line('a'), line('gone')].join('\n'));
assert.match(container.textContent, /not in project tools/);
await tick('gone', false); assert.deepEqual(relisted, ['a']);

// A failed request reverts the checkbox and reports why.
draw(line('a'), {relist: async () => { throw new Error('offline'); }});
await tick('b', true); assert.equal(box('b').checked, false); assert.deepEqual(reports, ['offline']);

// Locked trajectories show the boxes disabled.
draw(line('a'), {disabled: true}); assert.equal(box('a').disabled, true);

console.log('Tool picker: chosen tools, relisting, missing tools, failed requests and locked state passed.');
window.happyDOM.abort();
