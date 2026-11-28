/* The project's agent name labels agent chat bubbles; the stored speaker stays "agent". */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
globalThis.document = window.document;
const {windowBody} = await import('../trajectory_remix/static/window-render.js');
const ctl = control => ({control});
const text = (s, event) => [...s].map(ch => ({text: ch, event}));
const chat = [ctl('ctx_begin'), ...text('chatroom'), ctl('ctx_end'),
  {control: 'msg_begin', event: 'ask'}, ...text('Maya: Hello', 'ask'), {control: 'msg_end', event: 'ask'},
  {control: 'msg_begin', event: 'reply:echo'}, ...text('agent: Hi Maya', 'reply:echo'), {control: 'msg_end', event: 'reply:echo'}];
const isAgent = event => event === 'reply:echo';
const speakers = options => [...windowBody(chat, {raw: false, purpose: 'chatroom', isAgent, ...options}).querySelectorAll('.wt-speaker')].map(n => n.textContent);
assert.deepEqual(speakers({}), ['Maya', 'agent']);
assert.deepEqual(speakers({agentName: 'Kidra'}), ['Maya', 'Kidra']);
console.log('agent name frontend ok');
