/* Manual Show tools: the dialog asks the server for the listing and adds it to the draft. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'Event']) globalThis[key] = window[key];
const {openShowTools, addListingEvent, rootEvents} = await import('../trajectory_remix/static/show-tools.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const tools = {chat: {name: 'Chat', input_schema: {description: 'Talk to the user', type: 'object'}}, read: {name: 'Read', input_schema: {type: 'object'}}, web: {name: 'Web', input_schema: {type: 'object'}}};

// Pure helper: a new tools window, and the first events now follow the listing.
{
  const draft = {id: 't', windows: {room: {purpose: 'chatroom', agent_writable: false}}, events: {opening: {kind: 'chat_message', window: 'room', after: []}, reply: {kind: 'agent_text', window: 'r', after: ['opening']}}};
  assert.deepEqual(rootEvents(draft), ['opening']);
  const result = addListingEvent(draft, {eventId: 'tools', window: 'tools', content: 'x', beforeRoots: true});
  assert.deepEqual(result.followers, ['opening']);
  assert.deepEqual(draft.windows.tools, {purpose: 'tools', agent_writable: false});
  assert.equal(draft.events.tools.supervision.masked, true); assert.deepEqual(draft.events.tools.after, []);
  assert.deepEqual(draft.events.opening.after, ['tools']); assert.deepEqual(draft.events.reply.after, ['opening']);
}

// The dialog: choose tools, random additions, add; the request carries the trajectory and event IDs.
const calls = [];
const api = async (path, method, body) => { calls.push({path, body}); return {chosen: body.tools, added: ['web'], shown: ['chat', 'read', 'web'], content: '{"name":"chat"}\n{"name":"read"}\n{"name":"web"}'}; };
const draft = {id: 'traj-9', windows: {room: {purpose: 'chatroom', agent_writable: false}}, events: {opening: {kind: 'chat_message', window: 'room', after: []}}};
let added = null; const reports = [];
const dialog = openShowTools({api, draft, tools, onAdd: value => { added = value; }, report: text => reports.push(text)});
const byLabel = name => dialog.querySelector(`[aria-label="${name}"]`);
assert.match(dialog.textContent, /Talk to the user/, 'tool descriptions are shown');
const addButton = [...dialog.querySelectorAll('button')].find(b => b.textContent === 'Add listing to draft');
addButton.click(); await settle();
assert.match(dialog.textContent, /Choose at least one tool/);
byLabel('List tool chat').checked = true; byLabel('List tool read').checked = true;
byLabel('Randomly add additional tools').checked = true;
addButton.click(); await settle(); await settle();
assert.deepEqual(calls.at(-1), {path: '/tools/listing', body: {trajectory_id: 'traj-9', event_id: 'tools', tools: ['chat', 'read'], extra_tools: 0.5}});
assert.equal(added.eventId, 'tools'); assert.deepEqual(added.followers, ['opening']);
assert.equal(draft.events.tools.window, 'tools'); assert.match(draft.events.tools.content, /"name":"web"/);
assert.match(reports.at(-1), /Listed 3 tools in tools \(1 added at random\)/);
// Without random additions the chance sent is 0; a second listing gets a new ID.
const again = openShowTools({api, draft, tools, onAdd: () => {}});
assert.equal(again.querySelector('[aria-label="Event ID"]').value, 'tools_2');
again.querySelector('[aria-label="List tool web"]').checked = true;
[...again.querySelectorAll('button')].find(b => b.textContent === 'Add listing to draft').click(); await settle(); await settle();
assert.equal(calls.at(-1).body.extra_tools, 0);
assert.equal(draft.events.tools_2.window, 'tools', 'reuses the existing tools window');
console.log('Show tools (manual): listing request, draft event, window reuse and placement passed.');
window.happyDOM.abort();
