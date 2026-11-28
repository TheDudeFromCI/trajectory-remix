/* Trajectory-bar complexity picker: choices, target hint, current value and locking. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
globalThis.document = window.document;
const {renderComplexity} = await import('../trajectory_remix/static/complexity-select.js');

const select = document.createElement('select'); document.body.append(select);
const choices = ['trivial', 'easy', 'medium', 'hard', 'expert'];
const view = (status, value) => ({targets: {sampling: {complexity: 'hard'}},
  values: {complexity: {status, observation: value === undefined ? undefined : {value}, definition: {choices}}}});

renderComplexity(select, view('unavailable'), false);
assert.deepEqual([...select.options].map(o => o.textContent), ['Not set (target Hard)', 'Trivial', 'Easy', 'Medium', 'Hard', 'Expert']);
assert.equal(select.value, ''); assert.equal(select.disabled, false);

renderComplexity(select, view('available', 'expert'), false); assert.equal(select.value, 'expert');
const first = select.options[1]; renderComplexity(select, view('available', 'easy'), false);
assert.equal(select.options[1], first, 'same choices reuse the options'); assert.equal(select.value, 'easy');

renderComplexity(select, view('available', 'easy'), true); assert.equal(select.disabled, true);
renderComplexity(select, null, false); assert.equal(select.disabled, true);
const sizes = {targets: {sampling: {task_length: 'L'}}, values: {task_length: {status: 'available', observation: {value: 'XL'}, definition: {choices: ['S', 'L', 'XL']}}}};
const target = document.createElement('select'); renderComplexity(target, sizes, false, 'task_length');
assert.deepEqual([...target.options].map(o => o.textContent), ['Not set (target L)', 'S', 'L', 'XL']); assert.equal(target.value, 'XL');
console.log('Complexity and Target size pickers: choices, target hint, value, option reuse and locking passed.');
window.happyDOM.abort();
