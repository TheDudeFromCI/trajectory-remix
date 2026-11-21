/* Tool checkboxes in the event editor: ticking edits the tools window's listing text. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'Event']) globalThis[key] = window[key];
const {listedNames, withTool, renderToolPicker} = await import('../trajectory_remix/static/tool-picker.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const line = name => `{"name":"${name}","description":"d","parameters":{}}`;

assert.deepEqual(listedNames(`${line('a')}\nnot json\n${line('b')}`), ['a', 'b']);
assert.equal(withTool(`${line('a')}\n${line('b')}`, 'a', false, ''), line('b'), 'unticking removes only that line');
assert.equal(withTool(line('a'), 'b', true, line('b')), `${line('a')}\n${line('b')}`, 'ticking appends the rendered line');
assert.equal(withTool('free text\n' + line('a'), 'a', false, ''), 'free text', 'other lines are kept');

const container = document.createElement('div'); document.body.append(container);
const tools = {a: {}, b: {}}; let result = null; const reports = []; const asked = [];
const draw = (text, extra = {}) => renderToolPicker(container, {text, tools, lineFor: async n => { asked.push(n); return line(n); }, onChange: t => { result = t; }, report: m => reports.push(m), ...extra});
draw(`${line('a')}\n${line('gone')}`);
const box = name => container.querySelector(`[aria-label="List tool ${name}"]`);
assert.equal(box('a').checked, true); assert.equal(box('b').checked, false);
assert.equal(box('gone').checked, true, 'listed tools missing from the project stay visible');
assert.match(container.textContent, /not in project tools/);
box('b').checked = true; box('b').dispatchEvent(new window.Event('change')); await settle();
assert.deepEqual(asked, ['b']); assert.equal(result, `${line('a')}\n${line('gone')}\n${line('b')}`);
box('a').checked = false; box('a').dispatchEvent(new window.Event('change')); await settle();
assert.equal(result, line('gone'));
assert.deepEqual(asked, ['b'], 'unticking needs no request');

// A failed request reverts the checkbox and reports why.
draw(line('a'), {lineFor: async () => { throw new Error('offline'); }});
box('b').checked = true; box('b').dispatchEvent(new window.Event('change')); await settle();
assert.equal(box('b').checked, false); assert.deepEqual(reports, ['offline']);

// Locked trajectories show the boxes disabled.
draw(line('a'), {disabled: true}); assert.equal(box('a').disabled, true);

console.log('Tool picker: listed names, add/remove lines, missing tools, failed requests and locked state passed.');
window.happyDOM.abort();
