/* Inline Markdown for the readable window view: code, emphasis, strikethrough, links.
   Builds DOM nodes with textContent only (never innerHTML), so window text cannot inject markup.
   Unterminated syntax (a streaming "**bold") simply stays literal until its closer arrives. */
import {node} from './ui-kit.js';

const INLINE = new RegExp([
  '(`+)(?!`)([\\s\\S]*?[^`])\\1(?!`)', // 1,2: code span
  '\\[((?:[^\\[\\]]|\\[[^\\]]*\\])*)\\]\\(\\s*<?([^\\s)>]+)>?(?:\\s+"[^"]*")?\\s*\\)', // 3,4: link
  '<((?:https?:\\/\\/|mailto:)[^\\s<>]+)>', // 5: autolink
  '\\*\\*(?=\\S)([\\s\\S]*?\\S)\\*\\*', // 6: bold
  '\\b__(?=\\S)([\\s\\S]*?\\S)__\\b', // 7: bold
  '~~(?=\\S)([\\s\\S]*?\\S)~~', // 8: strikethrough
  '\\*(?=[^\\s*])([^*\\n]*?[^\\s*])\\*', // 9: italic
  '\\b_(?=[^\\s_])([^_\\n]*?[^\\s_])_\\b', // 10: italic (never inside snake_case)
  '\\\\([\\\\`*_{}\\[\\]()#+\\-.!~|>])', // 11: escaped punctuation
].join('|'), 'g');

/** Only web and mail links are clickable; anything else (javascript:, data:, ...) stays text. */
export const safeUrl = url => (/^(?:https?:\/\/|mailto:)/i.test(url) ? url : null);

function link(parent, label, url) {
  const href = safeUrl(url);
  if (!href) { parent.append(`[${label}](${url})`); return; }
  const a = node('a');
  a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
  inlineMarkdown(label, a, 1);
  parent.append(a);
}

/** Append the inline Markdown of `text` to `parent`. */
export function inlineMarkdown(text, parent, depth = 0) {
  let last = 0;
  for (const m of text.matchAll(INLINE)) { // matchAll clones the regex, so nested calls can't disturb lastIndex
    if (m.index > last) parent.append(text.slice(last, m.index));
    last = m.index + m[0].length;
    const wrap = (name, inner) => { const el = node(name); if (depth < 4) inlineMarkdown(inner, el, depth + 1); else el.textContent = inner; parent.append(el); };
    if (m[2] !== undefined) parent.append(node('code', m[2].replace(/^ (.*[^ ].*) $/s, '$1')));
    else if (m[4] !== undefined) link(parent, m[3], m[4]);
    else if (m[5] !== undefined) link(parent, m[5], m[5]);
    else if (m[6] !== undefined || m[7] !== undefined) wrap('strong', m[6] ?? m[7]);
    else if (m[8] !== undefined) wrap('del', m[8]);
    else if (m[9] !== undefined || m[10] !== undefined) wrap('em', m[9] ?? m[10]);
    else parent.append(m[11]);
  }
  if (last < text.length) parent.append(text.slice(last));
}
