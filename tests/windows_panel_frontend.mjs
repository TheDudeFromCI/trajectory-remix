/* Windows tab: list and select windows, create, modify, rename, and delete with a warning
   that lists (and then deletes) the affected events. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'Event', 'KeyboardEvent', 'HTMLElement']) globalThis[key] = window[key];
const html = await fs.readFile(new URL('../trajectory_remix/static/graph.html', import.meta.url), 'utf8');
const pick = (start, end) => html.slice(html.indexOf(start), html.indexOf(end, html.indexOf(start)) + end.length);
document.body.innerHTML = pick('<div role="tabpanel" id="panel-windows"', '</form>\n      </div>') + pick('<dialog id="window-delete-dialog"', '</dialog>');
const dialog = document.getElementById('window-delete-dialog');
dialog.showModal = () => { dialog.open = true; }; dialog.close = () => { dialog.open = false; };

const edits = await import('../trajectory_remix/static/window-edits.js');
const {WindowsPanel} = await import('../trajectory_remix/static/windows-panel.js');
const $ = id => document.getElementById(id);
const click = id => $(id).click();
const sample = () => ({windows: {reasoning: {purpose: 'reasoning', agent_writable: true}, tool: {purpose: 'tool', agent_writable: true}},
  events: {a: {kind: 'agent_text', window: 'reasoning', after: [], content: 'hi'},
    b: {kind: 'tool_call', window: 'tool', after: ['a'], content: 'x'},
    c: {kind: 'agent_text', window: 'reasoning', after: ['b'], content: [{kind: 'window_ref', window: 'tool'}]},
    d: {kind: 'agent_text', window: 'reasoning', after: ['a', 'c'], content: 'end'}}});

// Pure edits.
let d = sample();
assert.deepEqual(edits.affectedEvents(d, 'tool').map(e => [e.id, e.reason]), [['b', 'writes to this window'], ['c', 'references this window']]);
edits.renameWindow(d, 'tool', 'shell');
assert.deepEqual(Object.keys(d.windows), ['reasoning', 'shell'], 'rename keeps the order');
assert.equal(d.events.b.window, 'shell'); assert.equal(d.events.c.content[0].window, 'shell');
assert.throws(() => edits.renameWindow(d, 'shell', 'reasoning'), /already exists/);
assert.throws(() => edits.createWindow(d, '', {}), /Enter a window ID/);
assert.deepEqual(edits.deleteWindow(d, 'shell'), ['b', 'c']);
assert.deepEqual(Object.keys(d.events), ['a', 'd']); assert.deepEqual(d.events.d.after, ['a'], 'dependencies on deleted events go');

// Panel.
let draft = sample(), changes = [], errors = [];
const panel = new WindowsPanel({$, draft: () => draft, onChange: removed => changes.push(removed || []), report: e => errors.push(e)});
panel.setReasoningLevels(['low', 'high']);
panel.reset('tool'); panel.render();
const rows = () => [...$('window-list').querySelectorAll('[role=option]')];
assert.deepEqual(rows().map(r => r.querySelector('.window-row-id').textContent), ['reasoning', 'tool']);
assert.equal(rows()[1].getAttribute('aria-selected'), 'true'); assert.match(rows()[0].textContent, /3 events/);
assert.equal($('window-form').hidden, true);
rows()[0].click(); assert.equal(panel.selected, 'reasoning');
$('window-list').dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown'})); assert.equal(panel.selected, 'tool');

// Modify: properties only, with the effort dropdown keeping every level.
click('window-edit');
assert.equal($('window-form').hidden, false); assert.equal($('window-id-field').hidden, true);
assert.match($('window-form-title').textContent, /Modify window “tool”/);
assert.deepEqual([...$('window-effort').options].map(o => o.textContent), ['Not set', 'low', 'high', 'Custom…']);
$('window-effort').value = 'high'; $('window-effort').dispatchEvent(new Event('change'));
$('window-writable').checked = false; $('window-left-open').checked = true;
$('window-form').dispatchEvent(new Event('submit', {cancelable: true}));
assert.deepEqual(draft.windows.tool, {purpose: 'tool', agent_writable: false, reasoning_effort: 'high', intentionally_left_open: true});
assert.equal($('window-form').hidden, true); assert.equal(changes.length, 1);
assert.match(rows()[1].textContent, /read-only/);

// Custom purpose.
click('window-edit'); const purposes = [...$('window-purpose').options];
$('window-purpose').value = purposes.at(-1).value; $('window-purpose').dispatchEvent(new Event('change'));
assert.equal($('window-purpose-custom').hidden, false);
$('window-purpose-custom').value = 'scratch'; $('window-form').dispatchEvent(new Event('submit', {cancelable: true}));
assert.equal(draft.windows.tool.purpose, 'scratch');
assert.ok([...$('window-purpose').options].some(o => o.textContent === 'scratch'), 'purposes in use become choices');

// Create: duplicate IDs are refused inline; Cancel and Escape close the form.
click('window-new'); assert.equal($('window-id-field').hidden, false); assert.equal($('window-props').hidden, false);
$('window-id').value = 'tool'; $('window-form').dispatchEvent(new Event('submit', {cancelable: true}));
assert.match($('window-form-problem').textContent, /already exists/); assert.equal($('window-form-problem').hidden, false);
$('window-form').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); assert.equal($('window-form').hidden, true);
click('window-new'); $('window-id').value = 'chat'; $('window-purpose').value = 'chatroom';
$('window-form').dispatchEvent(new Event('submit', {cancelable: true}));
assert.deepEqual(draft.windows.chat, {purpose: 'chatroom', agent_writable: true, reasoning_effort: null, intentionally_left_open: false});
assert.equal(panel.selected, 'chat');

// Rename updates events.
panel.select('tool'); click('window-rename'); assert.equal($('window-props').hidden, true); assert.equal($('window-id').value, 'tool');
$('window-id').value = 'shell'; $('window-form').dispatchEvent(new Event('submit', {cancelable: true}));
assert.ok(draft.windows.shell && !draft.windows.tool); assert.equal(draft.events.b.window, 'shell'); assert.equal(panel.selected, 'shell');

// Delete warns with the affected events; Cancel keeps everything.
click('window-delete'); assert.equal(dialog.open, true);
assert.equal($('window-delete-name').textContent, 'shell');
assert.deepEqual([...$('window-delete-events').querySelectorAll('code')].map(n => n.textContent), ['b', 'c']);
assert.equal($('window-delete-confirm').textContent, 'Delete window and 2 events');
dialog.close(); assert.ok(draft.windows.shell);
click('window-delete'); click('window-delete-confirm');
assert.equal(dialog.open, false); assert.ok(!draft.windows.shell); assert.deepEqual(changes.at(-1), ['b', 'c']);
assert.deepEqual(Object.keys(draft.events), ['a', 'd']);
panel.select('chat'); click('window-delete'); assert.match($('window-delete-summary').textContent, /No events use this window/);
assert.equal($('window-delete-events').hidden, true); click('window-delete-confirm');

// Locking disables every action and closes an open form.
click('window-edit'); panel.setLocked(true);
assert.equal($('window-form').hidden, true);
for (const id of ['window-new', 'window-edit', 'window-rename', 'window-delete']) assert.equal($(id).disabled, true, id);
panel.setLocked(false); assert.equal($('window-new').disabled, false);
draft = null; panel.render(); assert.match($('window-list').textContent, /No trajectory selected/); assert.equal($('window-edit').disabled, true);
assert.deepEqual(errors, []);
console.log('Windows panel: list, select, modify, custom purpose, create, rename, delete warning and locking passed.');
window.happyDOM.abort();
