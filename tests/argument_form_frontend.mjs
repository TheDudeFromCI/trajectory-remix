/* Tool argument form: typed fields from the input schema, round trips and bad JSON. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
globalThis.document = window.document; globalThis.Event = window.Event;
const {fieldType, parseArguments, renderArgumentForm} = await import('../trajectory_remix/static/argument-form.js');

assert.equal(fieldType({type: 'string'}), 'string');
assert.equal(fieldType({type: ['integer', 'null']}), 'integer');
assert.equal(fieldType({enum: ['a', 'b']}), 'enum');
assert.equal(fieldType({type: 'object'}), 'json');
assert.equal(fieldType({}), 'json');
assert.deepEqual(parseArguments('  '), {});
assert.deepEqual(parseArguments('{"a":1}'), {a: 1});
assert.equal(parseArguments('{"a":'), null);
assert.equal(parseArguments('[1]'), null);

const schema = {type: 'object', required: ['path'], properties: {
  path: {type: 'string', description: 'File to read'}, limit: {type: 'integer'}, mode: {enum: ['fast', 'slow']},
  follow: {type: 'boolean'}, filters: {type: 'array'}}};
const holder = document.createElement('div'); document.body.append(holder);
const changes = [], reports = [];
const args = {path: 'a.txt', mode: 'fast', extra: {x: 1}};
renderArgumentForm(holder, {args, schema, onChange: a => changes.push(structuredClone(a)), report: m => reports.push(m)});
const field = key => holder.querySelector(`[data-argument="${key}"]`);
assert.deepEqual([...holder.querySelectorAll('[data-argument]')].map(n => n.dataset.argument), ['path', 'limit', 'mode', 'follow', 'filters', 'extra']);
assert.equal(field('path').tagName, 'TEXTAREA'); assert.equal(field('path').value, 'a.txt');
assert.ok(holder.textContent.includes('File to read')); assert.ok(holder.textContent.includes('required'));
assert.equal(field('limit').type, 'number'); assert.equal(field('limit').step, '1');
assert.equal(field('mode').tagName, 'SELECT'); assert.equal(field('mode').value, '"fast"');
assert.deepEqual([...field('follow').options].map(o => o.textContent), ['Not set', 'true', 'false']);
assert.ok(holder.textContent.includes('not in schema')); assert.equal(JSON.parse(field('extra').value).x, 1);

const set = (key, value, event) => { field(key).value = value; field(key).dispatchEvent(new Event(event)); };
set('path', 'b.txt', 'input'); assert.equal(changes.at(-1).path, 'b.txt');
set('limit', '5', 'input'); assert.equal(changes.at(-1).limit, 5);
set('limit', '', 'input'); assert.ok(!('limit' in changes.at(-1)));
set('follow', 'true', 'change'); assert.equal(changes.at(-1).follow, true);
set('mode', '', 'change'); assert.ok(!('mode' in changes.at(-1)));
set('filters', '["a"]', 'change'); assert.deepEqual(changes.at(-1).filters, ['a']);
// A required string stays present even when emptied; bad JSON reports and keeps the value.
set('path', '', 'input'); assert.equal(changes.at(-1).path, '');
const before = changes.length; set('filters', '[', 'change');
assert.equal(changes.length, before); assert.equal(reports.length, 1); assert.deepEqual(args.filters, ['a']);

// A saved enum value outside the schema stays selected instead of being replaced.
renderArgumentForm(holder, {args: {mode: 'turbo'}, schema: {properties: {mode: {enum: ['fast']}}, required: ['mode']}, onChange() {}, report() {}});
assert.equal(field('mode').value, '"turbo"');
renderArgumentForm(holder, {args: {}, schema: {properties: {}}, disabled: true, onChange() {}, report() {}});
assert.ok(holder.textContent.includes('no arguments'));
renderArgumentForm(holder, {args: {a: 1}, disabled: true, onChange() {}, report() {}});
assert.equal(field('a').disabled, true);
// Broken fields get aria-invalid (red outline): omitted required arguments and bad JSON.
renderArgumentForm(holder, {args: {}, schema, onChange() {}, report() {}});
assert.equal(field('path').getAttribute('aria-invalid'), 'true'); assert.equal(field('limit').hasAttribute('aria-invalid'), false);
{
  const a = {}; renderArgumentForm(holder, {args: a, schema, onChange() {}, report() {}});
  set('path', 'x', 'input'); assert.equal(field('path').hasAttribute('aria-invalid'), false);
  set('path', '', 'input'); assert.equal(field('path').getAttribute('aria-invalid'), 'true');
  set('filters', '[', 'change'); assert.equal(field('filters').getAttribute('aria-invalid'), 'true');
  set('filters', '[1]', 'change'); assert.equal(field('filters').hasAttribute('aria-invalid'), false);
}
console.log('Argument form: typed fields, round trips, unset values, bad JSON and locking passed.');
window.happyDOM.abort();
