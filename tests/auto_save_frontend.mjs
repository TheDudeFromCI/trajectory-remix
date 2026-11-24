/* Auto-save: saves after a quiet period when enabled, restarts on each edit, and reports failures. */
import assert from 'node:assert/strict';
import {AutoSave} from '../trajectory_remix/static/auto-save.js';
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

let on = true, dirty = true, saves = 0, errors = [], fail = false;
const auto = new AutoSave({delay: 20, enabled: () => on, pending: () => dirty,
  save: async () => { saves += 1; if (fail) throw new Error('boom'); dirty = false; }, report: e => errors.push(e.message)});

// Each edit restarts the countdown, so a burst of edits saves once.
auto.schedule(); await wait(10); auto.schedule(); await wait(10); assert.equal(saves, 0);
await wait(40); assert.equal(saves, 1);

// Disabled: nothing is saved.
on = false; dirty = true; auto.schedule(); await wait(40); assert.equal(saves, 1);

// Nothing pending: nothing is saved.
on = true; dirty = false; auto.schedule(); await wait(40); assert.equal(saves, 1);

// Turning it off before the timer fires cancels the save.
dirty = true; auto.schedule(); on = false; await wait(40); assert.equal(saves, 1);

// A failed save is reported and not retried until the next edit.
on = true; fail = true; auto.schedule(); await wait(40);
assert.deepEqual(errors, ['boom']); assert.equal(saves, 2); await wait(40); assert.equal(saves, 2);
console.log('Auto-save: debounce, preference off, nothing pending, cancel and error reporting passed.');
