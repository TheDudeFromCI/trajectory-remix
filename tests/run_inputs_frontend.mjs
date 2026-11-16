/* Unit checks for the saved attempts/seed helper (no browser needed). */
import assert from 'node:assert/strict';

const store = new Map();
globalThis.localStorage = {getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v))};
const {loadRunInputs, saveRunInputs, persistInput, resolveSeed} = await import('../trajectory_remix/static/run-inputs.js');

assert.deepEqual(loadRunInputs(), {attempts: 100, seed: 0}, 'defaults use seed 0');
saveRunInputs({seed: 42});
assert.deepEqual(loadRunInputs(), {attempts: 100, seed: 42}, 'partial save keeps other field');
store.set('generation-run-inputs', '{not json');
assert.deepEqual(loadRunInputs(), {attempts: 100, seed: 0}, 'corrupt storage falls back');

const listeners = {};
const input = {value: '', addEventListener: (e, fn) => { listeners[e] = fn; }};
persistInput(input, 'attempts');
input.value = '7'; listeners.input(); assert.equal(loadRunInputs().attempts, 7);
input.value = ''; listeners.input(); assert.equal(loadRunInputs().attempts, 7, 'blank is not stored');

assert.equal(resolveSeed(5), 5);
for (let i = 0; i < 20; i++) { const s = resolveSeed(0); assert.ok(Number.isSafeInteger(s) && s > 0); }
console.log('run inputs ok');
