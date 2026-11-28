/* Readable window rendering: chat bubbles, formatted JSON, Markdown text and collapsed NOOP runs.
   Purely visual. The token stream is never changed; "Raw" mode shows it as is. */
import {node, tag} from './ui-kit.js';
import {toolListBlock} from './tool-list-view.js';
import {markdownBlock} from './markdown-view.js';

const SPEAKER_TONES = ['sky', 'peach', 'lilac', 'butter', 'rose', 'mint'];
const PAIRS = {msg_begin: 'msg_end', tool_begin: 'tool_end', response_begin: 'response_end'};
const LABELS = {msg_begin: 'Message', tool_begin: 'Tool call', response_begin: 'Tool result'};

export const controlLabel = control => control.replaceAll('_', ' ').toUpperCase();
const textOf = tokens => tokens.map(t => t.reference ? `[${t.reference[0]}]` : t.text || '').join('');

/** Fold consecutive NOOP tokens into one {control: 'noop', count} entry. */
export function collapseNoops(tokens) {
  const out = [];
  for (const t of tokens) {
    const last = out.at(-1);
    if (t.control === 'noop' && last?.control === 'noop') last.count += 1;
    else out.push(t.control === 'noop' ? {...t, count: 1} : t);
  }
  return out;
}

/**
 * Split a window's tokens into header, framed blocks (message, tool call, tool result),
 * plain text runs and standalone controls. Unclosed blocks are marked open (streaming).
 */
export function segmentWindow(tokens) {
  const parts = [];
  let i = 0;
  if (tokens[0]?.control === 'ctx_begin') {
    const end = tokens.findIndex(t => t.control === 'ctx_end');
    const stop = end < 0 ? tokens.length : end + 1;
    parts.push({type: 'header', text: textOf(tokens.slice(1, end < 0 ? stop : end))});
    i = stop;
  }
  let text = [];
  const flush = () => { if (text.length) { parts.push({type: 'text', text: textOf(text), event: text[0].event}); text = []; } };
  while (i < tokens.length) {
    const t = tokens[i];
    if (PAIRS[t.control]) {
      flush();
      const close = tokens.findIndex((x, j) => j > i && x.control === PAIRS[t.control]);
      const body = tokens.slice(i + 1, close < 0 ? tokens.length : close);
      const inner = body.filter(x => !x.control || x.reference);
      parts.push({type: t.control.replace('_begin', ''), text: textOf(inner), open: close < 0,
        event: t.event ?? inner[0]?.event, noops: body.filter(x => x.control === 'noop').reduce((n, x) => n + (x.count || 1), 0)});
      i = close < 0 ? tokens.length : close + 1;
    } else if (t.control && !t.reference) {
      flush(); parts.push({type: 'control', control: t.control, count: t.count || 1}); i += 1;
    } else { text.push(t); i += 1; }
  }
  flush();
  return parts;
}

/** "Speaker: text" (graph preview) or "\"Speaker\":text" (tokenized) → {speaker, text}. */
export function splitSpeaker(text) {
  const quoted = /^\s*"((?:[^"\\]|\\.)*)"\s*:\s?/.exec(text);
  if (quoted) { try { return {speaker: JSON.parse(`"${quoted[1]}"`), text: text.slice(quoted[0].length)}; } catch { /* fall through */ } }
  const plain = /^([^:\n]{1,40}):\s/.exec(text);
  return plain ? {speaker: plain[1], text: text.slice(plain[0].length)} : {speaker: '', text};
}

/**
 * Tolerant JSON pretty-printer for complete or partial (streaming) text.
 * Returns spans [{text, cls}] or null when the text does not look like JSON.
 */
export function formatJson(source) {
  const text = source.trim();
  if (!/^[[{]/.test(text)) return null;
  const spans = [], lex = /\s+|"(?:[^"\\]|\\.)*"?|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[{}[\]:,]|./gy;
  let depth = 0, match, lineStart = true, previous = '';
  const indent = () => '  '.repeat(Math.max(0, depth));
  const push = (value, cls) => { spans.push({text: (lineStart ? indent() : '') + value, cls}); lineStart = false; };
  const newline = () => { spans.push({text: '\n'}); lineStart = true; };
  while ((match = lex.exec(text))) {
    const token = match[0];
    if (/^\s+$/.test(token)) continue;
    if (token === '{' || token === '[') { push(token, 'j-punct'); depth += 1; newline(); }
    else if (token === '}' || token === ']') {
      depth -= 1;
      if (previous === '{' || previous === '[') { spans.pop(); spans.push({text: token, cls: 'j-punct'}); lineStart = false; }
      else { if (!lineStart) newline(); push(token, 'j-punct'); }
    } else if (token === ',') { push(',', 'j-punct'); newline(); }
    else if (token === ':') push(': ', 'j-punct');
    else if (token[0] === '"') {
      const rest = text.slice(lex.lastIndex).trimStart();
      push(token, rest.startsWith(':') ? 'j-key' : 'j-string');
    } else if (/^-?\d/.test(token)) push(token, 'j-number');
    else if (token === 'true' || token === 'false') push(token, 'j-bool');
    else if (token === 'null') push(token, 'j-null');
    else push(token, 'j-other');
    previous = token;
  }
  return spans;
}

/** Window header as [label, value] pairs: tokenized headers are JSON objects, graph previews plain text. */
export function headerFields(text) {
  try {
    const value = JSON.parse(text);
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return Object.entries(value).filter(([, v]) => v !== null && v !== '').map(([k, v]) => [k.replaceAll('_', ' '), typeof v === 'object' ? JSON.stringify(v) : String(v)]);
    }
  } catch { /* partial or plain header */ }
  return [['', text]];
}

function jsonBlock(text) {
  const spans = formatJson(text);
  if (!spans) return null;
  const pre = node('pre', undefined, 'wt-json');
  for (const s of spans) pre.append(s.cls ? node('span', s.text, s.cls) : document.createTextNode(s.text));
  return pre;
}

function rawBody(tokens) {
  const body = node('div', undefined, 'window-tokens');
  for (const t of collapseNoops(tokens)) {
    if (t.control === 'noop') body.append(noopChip(t.count));
    else body.append(t.control ? node('span', controlLabel(t.control), 'token-block') : node('span', t.reference ? `[${t.reference[0]}]` : t.text || ''));
  }
  return body;
}

function noopChip(count) {
  const chip = node('span', count > 1 ? `NOOP ×${count}` : 'NOOP', 'token-block noop');
  chip.dataset.tip = `${count} idle tick${count === 1 ? '' : 's'}: this writable window predicted NOOP while waiting. NOOP is a training label, not window content, and never enters the context.`;
  chip.tabIndex = 0;
  return chip;
}

/** Formatted view: chat bubbles for chatrooms, tool name lists for tools windows, JSON blocks for tool traffic. */
function formattedBody(tokens, {purpose, isAgent, agentName = 'agent', toolExtras = () => [], hideExtras = false}) {
  const body = node('div', undefined, 'window-formatted');
  const chat = purpose === 'chatroom', speakers = new Map();
  const tone = name => { if (!speakers.has(name)) speakers.set(name, SPEAKER_TONES[speakers.size % SPEAKER_TONES.length]); return speakers.get(name); };
  for (const part of segmentWindow(collapseNoops(tokens))) {
    if (part.type === 'header') {
      const head = node('div', undefined, 'wt-header');
      head.append(node('span', 'Context', 'wt-label'));
      for (const [key, value] of headerFields(part.text)) {
        const field = node('span', undefined, 'wt-field');
        if (key) field.append(node('span', key, 'faint'), ' ');
        field.append(node('span', value, 'mono')); head.append(field);
      }
      body.append(head);
    } else if (part.type === 'msg' && chat) {
      const {speaker, text} = splitSpeaker(part.text), agent = isAgent(part.event, speaker);
      const bubble = node('div', undefined, 'wt-bubble' + (agent ? ' agent' : '') + (part.open ? ' open' : ''));
      bubble.style.setProperty('--tone', `var(--pastel-${agent ? 'mint' : tone(speaker)})`);
      // The stored speaker for the agent is "agent"; show the project's agent name instead.
      const label = agent && (!speaker || speaker === 'agent') ? agentName : speaker;
      bubble.append(node('span', label || 'Message', 'wt-speaker'), markdownBlock(text, 'wt-text', 'span'));
      body.append(bubble);
    } else if (['msg', 'tool', 'response'].includes(part.type)) {
      const block = node('div', undefined, `wt-block ${part.type}` + (part.open ? ' open' : ''));
      const label = node('div', undefined, 'wt-block-head');
      label.append(node('span', LABELS[part.type + '_begin'], 'wt-label'));
      // A call's text is `"tool_id":arguments`; show the tool in the label, arguments below.
      const call = part.type === 'tool' && part.text.trimStart().startsWith('"') ? splitSpeaker(part.text) : {speaker: '', text: part.text};
      if (call.speaker) label.append(node('span', call.speaker, 'wt-tool-id mono'));
      if (part.open) label.append(tag('Streaming', 'accent'));
      if (part.noops) label.append(noopChip(part.noops));
      block.append(label, jsonBlock(call.text) || markdownBlock(call.text));
      body.append(block);
    } else if (part.type === 'text') {
      body.append((purpose === 'tools' && toolListBlock(part.text, {extras: toolExtras(part.event), hideExtras})) || jsonBlock(part.text) || markdownBlock(part.text));
    } else if (part.type === 'control') {
      body.append(part.control === 'noop' ? noopChip(part.count) : node('span', controlLabel(part.control), 'token-block'));
    }
  }
  return body;
}

/** Window body for the chosen mode. */
export function windowBody(tokens, options) {
  return options.raw ? rawBody(tokens) : formattedBody(tokens, options);
}
