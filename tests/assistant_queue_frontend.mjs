/* Auto-send queue: comments added while a job owns the trajectory wait, then go out once as one send. */
import assert from 'node:assert/strict';
import {AssistantQueue} from '../trajectory_remix/static/assistant-queue.js';

const open = [{id: 'c1', status: 'open'}], done = [{id: 'c1', status: 'addressed'}];
const state = (over = {}) => ({owner: null, dirty: false, comments: open, auto: true, ...over});
const q = new AssistantQueue();

// Nothing is held until a comment arrives during a job.
assert.equal(q.ready('t1', state()), false);

// Several comments during one job still make a single entry; it waits while the job owns the trajectory.
q.hold('t1'); q.hold('t1'); q.hold('t1');
assert.equal(q.waiting.size, 1);
assert.equal(q.ready('t1', state({owner: 'job-1'})), false); assert.ok(q.has('t1'));

// An unsaved draft also holds it back.
assert.equal(q.ready('t1', state({dirty: true})), false); assert.ok(q.has('t1'));

// Other trajectories are unaffected.
assert.equal(q.ready('t2', state()), false);

// Once the owner is gone and the draft is saved, it is ready exactly until cleared.
assert.equal(q.ready('t1', state()), true);
q.clear('t1');
assert.equal(q.ready('t1', state()), false);

// Nothing open (all addressed or resolved) drops the entry instead of sending.
q.hold('t1'); assert.equal(q.ready('t1', state({comments: done})), false); assert.equal(q.has('t1'), false);

// Turning auto-send off drops the entry too.
q.hold('t1'); assert.equal(q.ready('t1', state({auto: false})), false); assert.equal(q.has('t1'), false);

console.log('Assistant queue: one entry per trajectory, waits for owner and draft, drops stale entries passed.');
