/* Dropdown with a Custom choice: all choices stay listed, Custom reveals a text box. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
globalThis.document = window.document; globalThis.Event = window.Event;
const {ChoiceField} = await import('../trajectory_remix/static/choice-field.js');

const select = document.createElement('select'), input = document.createElement('input');
document.body.append(select, input);
const seen = [];
const field = new ChoiceField(select, input, {empty: 'Model default', onChange: v => seen.push(v)});
field.setChoices(['low', 'medium', 'high']);
const labels = () => [...select.options].map(o => o.textContent);
assert.deepEqual(labels(), ['Model default', 'low', 'medium', 'high', 'Custom…']);
assert.equal(field.value, ''); assert.equal(input.hidden, true);

field.value = 'high'; assert.equal(select.value, 'high'); assert.equal(input.hidden, true);
field.value = 'turbo';
assert.equal(field.custom, true); assert.equal(input.hidden, false); assert.equal(input.value, 'turbo'); assert.equal(field.value, 'turbo');
assert.deepEqual(labels(), ['Model default', 'low', 'medium', 'high', 'Custom…'], 'a custom value keeps every choice listed');

// Picking Custom by hand reveals the box; typing reports the trimmed value.
field.value = 'low'; select.value = [...select.options].at(-1).value; select.dispatchEvent(new Event('change'));
assert.equal(input.hidden, false); input.value = ' ultra '; input.dispatchEvent(new Event('input'));
assert.equal(seen.at(-1), 'ultra');
select.value = 'medium'; select.dispatchEvent(new Event('change'));
assert.equal(input.hidden, true); assert.equal(seen.at(-1), 'medium');

// New choices keep the current value; a value that becomes listed leaves Custom.
field.value = 'xhigh'; field.setChoices(['low', 'xhigh']);
assert.equal(field.custom, false); assert.equal(field.value, 'xhigh');
field.setChoices(['low']); assert.equal(field.custom, true); assert.equal(field.value, 'xhigh');

// Without a blank choice, blank falls back to the first choice.
const plain = new ChoiceField(document.createElement('select'), document.createElement('input'));
plain.setChoices(['reasoning', 'tool']); plain.value = ''; assert.equal(plain.value, 'reasoning');
plain.disabled = true; assert.equal(plain.select.disabled, true); assert.equal(plain.input.disabled, true);
console.log('Choice field: full list, custom box, trimming, choice refresh and disabling passed.');
window.happyDOM.abort();
