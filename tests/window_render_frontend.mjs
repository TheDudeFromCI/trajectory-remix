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

console.log('Window renderers and Flow YAML: NOOP folding, segments, speakers, JSON formatting, bubbles, raw mode and lossless folding passed.');
window.happyDOM.abort();
