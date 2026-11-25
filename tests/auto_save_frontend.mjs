/* Auto-save: discrete changes save at once, typing waits for a pause, flush saves what's left. */
import assert from 'node:assert/strict';
import {AutoSave} from '../trajectory_remix/static/auto-save.js';
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

// The draft has a version; a save stores the version it started with.
let version = 1, stored = 0, typing = false, saves = 0, errors = [], fail = false;
const pending = () => version !== stored;
const edit = () => { version += 1; auto.schedule(); };
const auto = new AutoSave({delay: 20, pending, typing: () => typing,
  save: async () => { const sent = version; saves += 1; await wait(5); if (fail) throw new Error('boom'); stored = sent; }, report: e => errors.push(e.message)});

// A discrete change (select, checkbox, button) saves straight away.
auto.schedule(); await wait(10); assert.equal(saves, 1); assert.equal(pending(), false);

// Typing: each keystroke restarts the pause, so a burst saves once.
typing = true;
edit(); await wait(10); edit(); await wait(10); assert.equal(saves, 1);
await wait(30); assert.equal(saves, 2);

// Nothing pending: nothing is saved.
typing = false; auto.schedule(); await wait(10); assert.equal(saves, 2);

// Flush saves a pending typing burst immediately (for example before switching trajectory).
typing = true; edit(); await auto.flush(); assert.equal(saves, 3); assert.equal(pending(), false);

// A change during a running save is saved after it, not lost.
typing = false; edit(); await wait(1); edit();
await wait(30); assert.equal(saves, 5); assert.equal(pending(), false);

// A failed save is reported and not retried until the next change.
fail = true; edit(); await wait(20);
assert.deepEqual(errors, ['boom']); assert.equal(saves, 6); await wait(30); assert.equal(saves, 6);
console.log('Auto-save: instant saves, typing pause, flush, overlapping changes and error reporting passed.');
