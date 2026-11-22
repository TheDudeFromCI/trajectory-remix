/* Duplicate trajectory: the copy keeps the saved source but gets a fresh identity. */
import assert from 'node:assert/strict';
const {duplicateSource, duplicateTrajectory} = await import('../trajectory_remix/static/duplicate.js');

const source = {format: 'kidra-trajectory-graph/1', id: 'original', windows: {reasoning: {purpose: 'reasoning', agent_writable: true}},
  events: {a: {kind: 'agent_text', window: 'reasoning', after: [], content: 'hi'}}, metadata: {stage: 2, split: 'Train'}};

// Everything but the ID is copied, and the copy is deep (editing it leaves the original alone).
const copy = duplicateSource(source, 'copy');
assert.deepEqual(copy, {...source, id: 'copy'});
copy.events.a.content = 'changed'; copy.metadata.stage = 0;
assert.equal(source.events.a.content, 'hi'); assert.equal(source.metadata.stage, 2);

// Without an explicit ID a new random one is used.
const fresh = duplicateSource(source);
assert.notEqual(fresh.id, source.id); assert.match(fresh.id, /^[0-9a-f-]{36}$/);
assert.throws(() => duplicateSource(null), /Select a trajectory/);

// The copy is created with a POST to the trajectories collection.
const calls = [];
const record = await duplicateTrajectory(async (path, method, body) => { calls.push({path, method, body}); return {source: body}; }, source);
assert.equal(calls.length, 1); assert.equal(calls[0].path, '/trajectories'); assert.equal(calls[0].method, 'POST');
assert.notEqual(record.source.id, 'original'); assert.deepEqual({...record.source, id: 'original'}, source);
console.log('duplicate frontend ok');
