/* Content mode memory: the chosen mode survives re-renders, renames and is per trajectory. */
import assert from 'node:assert/strict';
const {ContentModes} = await import('../trajectory_remix/static/content-mode.js');

const modes = new ContentModes();
assert.equal(modes.pick('t1', 'call', '{"a":1}'), 'text', 'text content defaults to Text');
assert.equal(modes.pick('t1', 'call', {a: 1}), 'json', 'structured content is Raw JSON');
modes.remember('t1', 'call', 'form');
assert.equal(modes.pick('t1', 'call', '{"a":2}'), 'form', 'the chosen mode survives a re-render');
assert.equal(modes.pick('t2', 'call', '{"a":2}'), 'text', 'modes are kept per trajectory');
assert.equal(modes.pick('t1', 'call', [1]), 'json', 'structured content still needs Raw JSON');
modes.rename('t1', 'call', 'renamed');
assert.equal(modes.pick('t1', 'renamed', ''), 'form'); assert.equal(modes.pick('t1', 'call', ''), 'text');
modes.remember('t1', null, 'json'); assert.equal(modes.modes.size, 1, 'no event, nothing remembered');
console.log('Content mode: defaults, memory across re-renders, renames and trajectories passed.');
