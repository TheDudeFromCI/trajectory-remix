/* Tool checkboxes for tool_schema events: ticking re-renders the listing; extras are grayed or hidden. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'Event']) globalThis[key] = window[key];
const {listedNames, chosenNames, renderToolPicker} = await import('../trajectory_remix/static/tool-picker.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const line = name => `{"name":"${name}","description":"d","parameters":{}}`;

const text = [line('a'), 'not json', line('b'), line('c')].join('\n');
assert.deepEqual(listedNames(text), ['a', 'b', 'c']);
assert.deepEqual(chosenNames(text, ['c']), ['a', 'b'], 'random extras are not chosen tools');

const container = document.createElement('div'); document.body.append(container);
const tools = {a: {}, b: {}, c: {}, d: {}}; let relisted = null; const reports = [];
const draw = (listing, extra = {}) => renderToolPicker(container, {text: listing, tools, relist: async chosen => { relisted = chosen; }, report: m => reports.push(m), ...extra});
const box = name => container.querySelector(`[aria-label="List tool ${name}"]`);
const tick = async (name, checked) => { box(name).checked = checked; box(name).dispatchEvent(new window.Event('change')); await settle(); };

// Chosen tools are ticked; random extras are grayed out, ticked and not editable.
draw([line('a'), line('c')].join('\n'), {extras: ['c']});
assert.equal(box('a').checked, true); assert.equal(box('b').checked, false); assert.equal(box('d').checked, false);
assert.equal(box('c').checked, true); assert.equal(box('c').disabled, true);
assert.equal(box('c').closest('label').classList.contains('random'), true);
assert.equal(box('a').closest('label').classList.contains('random'), false);

// Ticking sends the new chosen set (extras are drawn again by the server); unticking removes one.
await tick('b', true); assert.deepEqual(relisted, ['a', 'b']);
await tick('a', false); assert.deepEqual(relisted, []);

// Hidden extras leave no row; chosen tools the project no longer has stay until unticked.
draw([line('a'), line('c'), line('gone')].join('\n'), {extras: ['c'], hideExtras: true});
assert.equal(box('c'), null); assert.match(container.textContent, /not in project tools/);
await tick('gone', false); assert.deepEqual(relisted, ['a']);

// A failed request reverts the checkbox and reports why.
draw(line('a'), {relist: async () => { throw new Error('offline'); }});
await tick('b', true); assert.equal(box('b').checked, false); assert.deepEqual(reports, ['offline']);

// Locked trajectories show the boxes disabled.
draw(line('a'), {disabled: true}); assert.equal(box('a').disabled, true);

console.log('Tool picker: chosen vs random tools, relisting, hidden extras, missing tools, failed requests and locked state passed.');
window.happyDOM.abort();
