/* Large tokenizer definitions stay out of the settings JSON draft but survive a save. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window(); globalThis.document = window.document;
const {STUB_KEY, isStub, parkSettings, restoreSettings, resolveDefinition} = await import('../trajectory_remix/static/tokenizer-store.js');
const {renderProjectSettings} = await import('../trajectory_remix/static/project-settings.js');

const names = ['bos', 'eos', 'noop', 'ctx_begin', 'ctx_end', 'tool_begin', 'tool_end', 'response_begin', 'response_end', 'msg_begin', 'msg_end', 'window_ref'];
const merges = Array.from({length: 50000}, (_, i) => [`a${i}`, `b${i}`]);
const definition = {model: {type: 'BPE', vocab: {x: 0}, merges}, added_tokens: names.map((name, i) => ({id: 100 + i, content: `<|${name}|>`, special: true}))};
const settings = {endpoints: {}, tokenizer_profiles: {qwen: {name: 'qwen', definition, special_tokens: {bos: 100}}}};

// The parked draft is small, leaves the input untouched and restores the same object.
const parked = parkSettings(settings), text = JSON.stringify(parked, null, 2);
assert.ok(isStub(parked.tokenizer_profiles.qwen.definition));
assert.ok(text.length < 1000, `parked draft is ${text.length} chars`);
assert.equal(settings.tokenizer_profiles.qwen.definition, definition);
assert.equal(restoreSettings(JSON.parse(text)).tokenizer_profiles.qwen.definition, definition);
assert.deepEqual(parkSettings(parked), parked);

// Equal content gets the same stub, so unchanged drafts are not reported dirty.
assert.deepEqual(parkSettings(structuredClone(settings)), parked);
const edited = structuredClone(settings); edited.tokenizer_profiles.qwen.definition.added_tokens[0].content = '<|other|>';
assert.notDeepEqual(parkSettings(edited), parked);

// A definition pasted into Advanced JSON passes through; an unknown stub is an error.
assert.deepEqual(restoreSettings({tokenizer_profiles: {t: {definition: {model: {}}}}}).tokenizer_profiles.t.definition, {model: {}});
assert.throws(() => restoreSettings({tokenizer_profiles: {t: {definition: {[STUB_KEY]: 'missing'}}}}), /Re-import its JSON file/);
assert.deepEqual(restoreSettings({endpoints: {}}), {endpoints: {}});

// The control dropdowns list dedicated tokens from a parked definition.
const root = document.createElement('div'); document.body.append(root);
renderProjectSettings(root, parked, {getSettings: () => parked, onChange: () => {}});
const bos = [...root.querySelectorAll('select')].find(n => n.getAttribute('aria-label') === 'Tokenizer qwen bos');
assert.equal(bos.options.length, names.length + 1);
assert.equal(bos.value, '100');
assert.deepEqual(resolveDefinition(parked.tokenizer_profiles.qwen.definition), definition);
console.log('tokenizer store frontend: ok');
