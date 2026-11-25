/* Block Markdown for the readable window view: headings, lists, quotes, code fences, tables, rules.
   Visual only: the token stream is never changed and Raw mode shows it as is. Text that does not
   look like Markdown is returned exactly as the plain view always showed it. */
import {node} from './ui-kit.js';
import {inlineMarkdown} from './markdown-inline.js';

const MAX_CHARS = 20000; // beyond this, skip Markdown rather than risk a slow render
const MAX_DEPTH = 6; // nested quotes and lists
const HINT = /[`*_~#>|[]|^\s*(?:[-+]|\d+[.)])\s/m;
const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)[^\n]*$/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/;
const RULE = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^ {0,3}>\s?/;
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)*\|?\s*$/;

const startsBlock = line => FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || ITEM.test(line);
const isTableStart = (line, next) => line.includes('|') && next !== undefined && next.includes('-') && TABLE_SEP.test(next);
const cells = row => row.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
const alignOf = cell => (cell.startsWith(':') ? (cell.endsWith(':') ? 'center' : 'left') : cell.endsWith(':') ? 'right' : '');

function codeBlock(lines, i, lang) {
  const fence = FENCE.exec(lines[i])[1], body = [];
  let j = i + 1;
  while (j < lines.length && !(lines[j].trim().startsWith(fence[0].repeat(fence.length)) && /^[`~]+\s*$/.test(lines[j].trim()))) body.push(lines[j++]);
  const open = j >= lines.length; // no closing fence yet: still streaming
  const pre = node('pre', undefined, 'wt-md-code' + (open ? ' open' : '')), code = node('code', body.join('\n'));
  if (lang) pre.dataset.lang = lang;
  pre.append(code);
  return [pre, open ? j : j + 1];
}

function table(lines, i) {
  const head = cells(lines[i]), aligns = cells(lines[i + 1]).map(alignOf);
  const el = node('table', undefined, 'wt-md-table'), thead = node('thead'), tbody = node('tbody');
  const row = (values, name) => {
    const tr = node('tr');
    head.forEach((_, c) => { const cell = node(name); if (aligns[c]) cell.style.textAlign = aligns[c]; inlineMarkdown(values[c] ?? '', cell); tr.append(cell); });
    return tr;
  };
  thead.append(row(head, 'th'));
  let j = i + 2;
  while (j < lines.length && lines[j].trim() && lines[j].includes('|')) tbody.append(row(cells(lines[j++]), 'td'));
  el.append(thead, tbody);
  return [el, j];
}

function list(lines, i, depth) {
  const first = ITEM.exec(lines[i]), base = first[1].length, ordered = /\d/.test(first[2][0]);
  const el = node(ordered ? 'ol' : 'ul', undefined, 'wt-md-list');
  if (ordered && Number(first[2].slice(0, -1)) !== 1) el.start = Number(first[2].slice(0, -1));
  let j = i;
  while (j < lines.length) {
    const m = ITEM.exec(lines[j]);
    if (!m || m[1].length !== base || /\d/.test(m[2][0]) !== ordered) break;
    const body = [m[3]], offset = base + m[2].length + 1;
    j += 1;
    for (; j < lines.length && lines[j].trim(); j += 1) {
      const indent = lines[j].length - lines[j].trimStart().length;
      if (indent <= base) break;
      body.push(lines[j].slice(Math.min(offset, indent)));
    }
    const li = node('li');
    renderBlocks(body, li, depth + 1);
    el.append(li);
    if (j < lines.length && !lines[j].trim() && ITEM.test(lines[j + 1] ?? '')) j += 1; // loose list
  }
  return [el, j];
}

function quote(lines, i, depth) {
  const body = [];
  let j = i;
  while (j < lines.length && QUOTE.test(lines[j])) body.push(lines[j++].replace(QUOTE, ''));
  const el = node('blockquote', undefined, 'wt-md-quote');
  renderBlocks(body, el, depth + 1);
  return [el, j];
}

function paragraph(lines, i) {
  const body = [lines[i]];
  let j = i + 1;
  while (j < lines.length && lines[j].trim() && !startsBlock(lines[j]) && !isTableStart(lines[j], lines[j + 1])) body.push(lines[j++]);
  const p = node('p', undefined, 'wt-md-p');
  inlineMarkdown(body.join('\n'), p);
  return [p, j];
}

function renderBlocks(lines, parent, depth = 0) {
  if (depth >= MAX_DEPTH) { parent.append(node('p', lines.join('\n'), 'wt-md-p')); return; }
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    let block, heading, fence;
    if (!line.trim()) { i += 1; continue; }
    if ((fence = FENCE.exec(line))) [block, i] = codeBlock(lines, i, fence[2]);
    else if ((heading = HEADING.exec(line))) {
      block = node('div', undefined, `wt-md-h wt-md-h${heading[1].length}`);
      block.setAttribute('role', 'heading'); block.setAttribute('aria-level', String(heading[1].length));
      inlineMarkdown(heading[2], block); i += 1;
    } else if (RULE.test(line)) { block = node('hr', undefined, 'wt-md-rule'); i += 1; }
    else if (QUOTE.test(line)) [block, i] = quote(lines, i, depth);
    else if (ITEM.test(line)) [block, i] = list(lines, i, depth);
    else if (isTableStart(line, lines[i + 1])) [block, i] = table(lines, i);
    else [block, i] = paragraph(lines, i);
    parent.append(block);
  }
}

/**
 * Markdown rendering of `text` as a block element carrying `cls`.
 * Plain text (no Markdown syntax at all) keeps the original pre-wrapped text node.
 */
export function markdownBlock(text, cls = 'wt-text', tagName = 'div') {
  if (text.length > MAX_CHARS || !HINT.test(text)) return node(tagName, text, cls);
  const el = node('div', undefined, `${cls} wt-md`);
  renderBlocks(text.replace(/\r\n?/g, '\n').split('\n'), el);
  const [only] = el.children;
  const unformatted = el.children.length === 1 && only.className === 'wt-md-p' && !only.children.length && only.textContent === text;
  return unformatted || !el.children.length ? node(tagName, text, cls) : el;
}
