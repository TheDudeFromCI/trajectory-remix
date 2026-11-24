/* Window renderers and the Flow YAML writer: pure functions plus small DOM checks. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
globalThis.document = window.document;
const {collapseNoops, segmentWindow, splitSpeaker, formatJson, headerFields, windowBody} = await import('../trajectory_remix/static/window-render.js');
const text = s => [...s].map(ch => ({text: ch}));
const ctl = control => ({control});

// NOOP runs fold into one entry with a count; other tokens are untouched.
const folded = collapseNoops([ctl('noop'), ctl('noop'), {text: 'a'}, ctl('noop')]);
assert.deepEqual(folded.map(t => t.count ?? t.text), [2, 'a', 1]);

// Segments: header, framed blocks (incl. unclosed/streaming), text and controls.
const parts = segmentWindow([ctl('ctx_begin'), ...text('{"slot":1}'), ctl('ctx_end'), ctl('msg_begin'), ...text('"Maya":Hi'), ctl('msg_end'),
  ...text('note'), ctl('tool_begin'), ...text('{"q":'), ctl('eos')]);
assert.deepEqual(parts.map(p => p.type), ['header', 'msg', 'text', 'tool']);
assert.equal(parts[3].open, true);
assert.equal(parts[0].text, '{"slot":1}');
assert.deepEqual(headerFields('{"slot":1,"purpose":"tool","reasoning_effort":null}'), [['slot', '1'], ['purpose', 'tool']]);
assert.deepEqual(headerFields('chatroom'), [['', 'chatroom']]);

// Speakers from tokenized ("Name":) and graph (Name: ) forms.
assert.deepEqual(splitSpeaker('"Maya":Can you help?'), {speaker: 'Maya', text: 'Can you help?'});
assert.deepEqual(splitSpeaker('agent: Done.'), {speaker: 'agent', text: 'Done.'});
assert.deepEqual(splitSpeaker('no speaker here'), {speaker: '', text: 'no speaker here'});

// JSON formatting keeps every character of the values, tolerates partial input, and skips prose.
const joined = spans => spans.map(s => s.text).join('');
assert.equal(joined(formatJson('{"a":1,"b":[true,null],"c":{}}')), '{\n  "a": 1,\n  "b": [\n    true,\n    null\n  ],\n  "c": {}\n}');
assert.deepEqual(JSON.parse(joined(formatJson('{"query":"refund policy","limit":3}'))), {query: 'refund policy', limit: 3});
assert.ok(joined(formatJson('{"query": "refu')).includes('"refu'));
assert.equal(formatJson('Plain text'), null);
assert.equal(formatJson('{"k":"v"}').find(s => s.text.trim() === '"k"').cls, 'j-key');

// Chat bubbles: agent on the right; raw mode shows the exact stream with a NOOP count.
const chat = [ctl('ctx_begin'), ...text('chatroom'), ctl('ctx_end'),
  {control: 'msg_begin', event: 'ask'}, {text: 'Maya: Hello', event: 'ask'}, {control: 'msg_end', event: 'ask'},
  {control: 'msg_begin', event: 'reply:echo'}, {text: 'agent: Hi Maya', event: 'reply:echo'}, {control: 'msg_end', event: 'reply:echo'}];
const isAgent = event => event === 'reply:echo';
const bubbles = windowBody(chat, {raw: false, purpose: 'chatroom', isAgent}).querySelectorAll('.wt-bubble');
assert.equal(bubbles.length, 2);
assert.equal(bubbles[0].classList.contains('agent'), false); assert.equal(bubbles[1].classList.contains('agent'), true);
assert.equal(bubbles[1].querySelector('.wt-text').textContent, 'Hi Maya');
// Not a chatroom: messages render as framed blocks instead of bubbles.
assert.equal(windowBody(chat, {raw: false, purpose: 'reasoning', isAgent}).querySelectorAll('.wt-bubble').length, 0);
const raw = windowBody([{text: 'think'}, ctl('noop'), ctl('noop'), ctl('noop'), ctl('eos')], {raw: true, purpose: 'reasoning', isAgent});
assert.equal(raw.textContent, 'thinkNOOP ×3EOS');
const formatted = windowBody([{text: 'think'}, ctl('noop'), ctl('noop'), ctl('eos')], {raw: false, purpose: 'reasoning', isAgent});
assert.equal(formatted.querySelectorAll('.noop').length, 1); assert.match(formatted.textContent, /NOOP ×2/);

// Tools windows list tool names (never the raw schema); other purposes and Raw mode are unchanged.
const listing = '{"name":"search","description":"Find docs","parameters":{"type":"object"}}\n{"name":"fetch","description":"fetch","parameters":{}}';
const toolTokens = [ctl('ctx_begin'), ...text('tools'), ctl('ctx_end'), ...text(listing)];
const toolItems = windowBody(toolTokens, {raw: false, purpose: 'tools', isAgent}).querySelectorAll('.wt-tool');
assert.deepEqual([...toolItems].map(i => i.querySelector('.wt-tool-name').textContent), ['search', 'fetch']);
assert.equal(toolItems[0].querySelector('.wt-tool-desc').textContent, 'Find docs');
assert.equal(toolItems[1].querySelector('.wt-tool-desc'), null);
assert.equal(windowBody(toolTokens, {raw: false, purpose: 'tools', isAgent}).textContent.includes('parameters'), false);
assert.equal(windowBody(toolTokens, {raw: false, purpose: 'reasoning', isAgent}).querySelectorAll('.wt-tool').length, 0);
assert.ok(windowBody(toolTokens, {raw: true, purpose: 'tools', isAgent}).textContent.includes('"parameters"'));
// Random extras (tool_schema extra_tools) are grayed out, or left out when hidden in Preferences.
const withExtras = {raw: false, purpose: 'tools', isAgent, toolExtras: () => ['fetch']};
const items = windowBody(toolTokens, withExtras).querySelectorAll('.wt-tool');
assert.deepEqual([...items].map(i => i.classList.contains('random')), [false, true]);
assert.deepEqual([...windowBody(toolTokens, {...withExtras, hideExtras: true}).querySelectorAll('.wt-tool-name')].map(n => n.textContent), ['search']);
assert.equal(windowBody([...text('{"name":"a"}\nnot json')], {raw: false, purpose: 'tools', isAgent}).querySelectorAll('.wt-tool').length, 0);

console.log('Window renderers and Flow YAML: NOOP folding, segments, speakers, JSON formatting, bubbles, raw mode and lossless folding passed.');
window.happyDOM.abort();

// A tool call shows its tool ID in both views, as compiled: "tool":arguments.
const call = [ctl('ctx_begin'), ...text('{"slot":1,"purpose":"tool","reasoning_effort":null}'), ctl('ctx_end'),
  ctl('tool_begin'), ...text('"chat":{"content":"Ack"}'), ctl('tool_end'), ctl('eos')];
const shown = windowBody(call, {raw: false, purpose: 'tool', isAgent});
assert.equal(shown.querySelector('.wt-tool-id').textContent, 'chat');
assert.ok(shown.querySelector('.wt-json').textContent.includes('"content"'));
assert.equal(shown.querySelector('.wt-json').textContent.includes('"chat"'), false);
assert.ok(windowBody(call, {raw: true, purpose: 'tool', isAgent}).textContent.includes('"chat":{"content":"Ack"}'));
// Pretty-printed arguments without a tool ID are never mistaken for one.
const bare = [ctl('tool_begin'), ...text('{"a": 1}'), ctl('tool_end')];
assert.equal(windowBody(bare, {raw: false, purpose: 'tool', isAgent}).querySelector('.wt-tool-id'), null);
