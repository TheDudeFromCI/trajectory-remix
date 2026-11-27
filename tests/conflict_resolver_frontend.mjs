/* Save conflict dialog: labels, the saved-vs-mine diff, per-value and bulk choices, cancel. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'Event', 'MutationObserver', 'KeyboardEvent', 'CSS']) globalThis[key] = window[key];
const {ConflictResolver, conflictDiff, conflictLabel} = await import('../trajectory_remix/static/conflict-resolver.js');

assert.equal(conflictLabel(['events', 'first', 'content']), 'Event first › content');
assert.equal(conflictLabel(['windows', 'w']), 'Window w');
assert.equal(conflictLabel(['metadata', 'stage']), 'Metadata › stage');
assert.equal(conflictLabel(['format']), 'format');
assert.equal(conflictLabel([]), 'Whole trajectory');

// The diff runs from the saved version (−) to mine (+), so only what blocks the merge is marked.
const content = {path: ['events', 'first', 'content'], base: 'one\ntwo', ours: 'one\nmine', theirs: 'one\ntheirs'};
const marked = [...conflictDiff(content).children].map(n => [n.className, n.textContent.trim()]);
assert.deepEqual(marked, [['diff-same', 'one'], ['diff-del', '- theirs'], ['diff-add', '+ mine']]);
const removed = [...conflictDiff({path: ['events', 'x'], base: {a: 1}, theirs: {a: 2}}).children].map(n => n.textContent.trim());
assert.deepEqual(removed, ['- a: 2', '+ (removed)'], 'a side that deleted the value says so');

document.body.innerHTML = `<dialog id="conflict-dialog"><p id="conflict-summary"></p><div id="conflict-list"></div>
  <button id="conflict-all-mine"></button><button id="conflict-all-theirs"></button><button id="conflict-save"></button></dialog>`;
const dialog = document.getElementById('conflict-dialog');
dialog.showModal ??= function () { this.open = true; };
dialog.close ??= function () { this.open = false; this.dispatchEvent(new Event('close')); };
const resolver = new ConflictResolver(dialog);
const conflicts = [content, {path: ['metadata', 'stage'], base: 1, ours: 2, theirs: 3}];

// Mine is the default; one value can switch to theirs.
let pending = resolver.open(conflicts);
assert.equal(dialog.open, true);
assert.match(document.getElementById('conflict-summary').textContent, /^2 values changed/);
assert.deepEqual([...dialog.querySelectorAll('.conflict-path')].map(n => n.textContent), ['Event first › content', 'Metadata › stage']);
const theirs = dialog.querySelectorAll('input[value=theirs]')[1]; theirs.checked = true; theirs.dispatchEvent(new Event('change'));
document.getElementById('conflict-save').click();
assert.deepEqual(await pending, [{path: content.path, keep: 'ours'}, {path: ['metadata', 'stage'], keep: 'theirs'}]);
assert.equal(dialog.open, false);

// Keep all theirs sets every choice.
pending = resolver.open(conflicts);
document.getElementById('conflict-all-theirs').click();
assert.deepEqual([...dialog.querySelectorAll('input:checked')].map(n => n.value), ['theirs', 'theirs']);
document.getElementById('conflict-save').click();
assert.deepEqual((await pending).map(r => r.keep), ['theirs', 'theirs']);

// Closing (Cancel, Escape) resolves to null and saves nothing.
pending = resolver.open(conflicts); dialog.close();
assert.equal(await pending, null);
console.log('Conflict resolver: labels, saved-vs-mine diff, per-value and bulk choices, cancel passed.');
