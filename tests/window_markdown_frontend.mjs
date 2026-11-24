/* Markdown in the readable window view: safe DOM output, plain text untouched, streaming tolerance. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
globalThis.document = window.document;
const {markdownBlock} = await import('../trajectory_remix/static/markdown-view.js');
const {windowBody} = await import('../trajectory_remix/static/window-render.js');
const html = text => markdownBlock(text).innerHTML;
const chars = s => [...s].map(ch => ({text: ch}));
const ctl = control => ({control});

// Plain text keeps the original pre-wrapped node: no Markdown wrapper, same text.
const plain = markdownBlock('Hello there.\n  indented  line', 'wt-text');
assert.equal(plain.className, 'wt-text'); assert.equal(plain.textContent, 'Hello there.\n  indented  line');
assert.equal(markdownBlock('snake_case_name and 2 * 3 * 4').querySelector?.('em'), null);
assert.equal(markdownBlock('use my_tool_name here').className, 'wt-text');

// Inline: emphasis, code, strike, links.
assert.match(html('**bold** and *it* and ~~gone~~ and `x_y`'), /<strong>bold<\/strong> and <em>it<\/em> and <del>gone<\/del> and <code>x_y<\/code>/);
assert.match(html('see [docs](https://example.com/a)'), /<a href="https:\/\/example.com\/a" target="_blank" rel="noopener noreferrer">docs<\/a>/);
assert.match(html('a \\*literal\\* star'), /a \*literal\* star/);

// Safety: raw HTML is text, unsafe link schemes are not links, scripts never become elements.
const evil = markdownBlock('<img src=x onerror=alert(1)> **b** [x](javascript:alert(1)) <script>1</script>');
assert.equal(evil.querySelector('img'), null); assert.equal(evil.querySelector('script'), null); assert.equal(evil.querySelector('a'), null);
assert.ok(evil.textContent.includes('<img src=x onerror=alert(1)>')); assert.ok(evil.textContent.includes('[x](javascript:alert(1))'));

// Blocks.
const doc = markdownBlock('# Title\n\nPara one\nstill one\n\n- a\n- b\n  - nested\n\n1. x\n2. y\n\n> quoted\n\n---\n\n| h1 | h2 |\n| --- | ---: |\n| 1 | 2 |\n\n```py\nprint("**no**")\n```');
assert.equal(doc.querySelector('[role=heading]').textContent, 'Title');
assert.equal(doc.querySelector('.wt-md-p').textContent, 'Para one\nstill one');
assert.deepEqual([...doc.querySelectorAll('ul > li')].map(li => li.firstChild.textContent), ['a', 'b', 'nested']);
assert.equal(doc.querySelectorAll('ul ul').length, 1);
assert.equal(doc.querySelectorAll('ol > li').length, 2);
assert.equal(doc.querySelector('blockquote').textContent.trim(), 'quoted');
assert.equal(doc.querySelectorAll('hr').length, 1);
assert.deepEqual([...doc.querySelectorAll('th')].map(c => c.textContent), ['h1', 'h2']);
assert.equal(doc.querySelector('td:last-child').style.textAlign, 'right');
assert.equal(doc.querySelector('pre code').textContent, 'print("**no**")'); assert.equal(doc.querySelector('pre code strong'), null);

// Streaming: an unterminated fence renders as an open code block; unterminated emphasis stays literal.
const open = markdownBlock('```json\n{"a":');
assert.ok(open.querySelector('pre.open')); assert.equal(open.querySelector('code').textContent, '{"a":');
assert.equal(markdownBlock('start **bold').querySelector('strong'), null);

// In windows: chat bubbles, framed blocks and text runs render Markdown; JSON and Raw mode do not.
const isAgent = event => event === 'reply';
const chat = [ctl('ctx_begin'), ...chars('chatroom'), ctl('ctx_end'), {control: 'msg_begin', event: 'ask'}, ...chars('Maya: **Hi** `agent`').map(t => ({...t, event: 'ask'})), {control: 'msg_end', event: 'ask'}];
const bubble = windowBody(chat, {raw: false, purpose: 'chatroom', isAgent}).querySelector('.wt-bubble .wt-text');
assert.equal(bubble.querySelector('strong').textContent, 'Hi'); assert.equal(bubble.querySelector('code').textContent, 'agent');
assert.equal(windowBody(chars('# Plan\n- one'), {raw: false, purpose: 'reasoning', isAgent}).querySelectorAll('li').length, 1);
assert.equal(windowBody(chars('{"a":"**x**"}'), {raw: false, purpose: 'reasoning', isAgent}).querySelector('strong'), null);
const raw = windowBody(chat, {raw: true, purpose: 'chatroom', isAgent});
assert.equal(raw.querySelector('strong'), null); assert.ok(raw.textContent.includes('Maya: **Hi** `agent`'));

console.log('Window Markdown: inline, blocks, safety, plain-text passthrough, streaming and Raw mode passed.');
window.happyDOM.abort();
