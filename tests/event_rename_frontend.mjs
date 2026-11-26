/* Renaming an event rewrites every dependency that points at it. */
import assert from 'node:assert/strict';
const {renameEvent, renameProblem} = await import('../trajectory_remix/static/event-rename.js');

const ev = after => ({kind: 'agent_text', window: 'w', after, content: ''});
const draft = {events: {a: ev([]), b: ev(['a']), c: ev(['a', 'b'])}};

assert.equal(renameProblem(draft.events, 'a', ''), 'Event name cannot be empty.');
assert.equal(renameProblem(draft.events, 'a', '  '), 'Event name cannot be empty.');
assert.match(renameProblem(draft.events, 'a', 'b'), /already exists/);
assert.equal(renameProblem(draft.events, 'a', 'a'), '');
assert.equal(renameProblem(draft.events, 'a', 'z'), '');

assert.equal(renameEvent(draft, 'a', 'a'), false);
assert.equal(renameEvent(draft, 'a', 'z'), true);
assert.deepEqual(Object.keys(draft.events), ['z', 'b', 'c']); // order is kept
assert.deepEqual(draft.events.b.after, ['z']);
assert.deepEqual(draft.events.c.after, ['z', 'b']);
console.log('event rename frontend passed');
