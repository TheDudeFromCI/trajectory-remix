/* Readable line diffs of the Assistant's proposed changes.
   textLines turns an event, window or metadata value into "key: value" lines, with multi-line
   text (event content) split into its own lines, so a one-word edit shows as one changed line.
   lineDiff pairs two line lists with a longest-common-subsequence diff. */

const MAX_CELLS = 250_000; // Past this, show the old lines then the new ones.

function scalar(value) { return typeof value === 'string' ? value : JSON.stringify(value); }

/** Lines describing a JSON value; nested objects indent, multi-line strings open a block. */
export function textLines(value, indent = '') {
  if (value === null || value === undefined) return [];
  if (typeof value !== 'object') return String(scalar(value)).split('\n').map(line => indent + line);
  const lines = [], array = Array.isArray(value);
  if (array && value.every(v => v === null || typeof v !== 'object')) return [indent + JSON.stringify(value)];
  for (const [key, item] of Object.entries(value)) {
    const name = array ? '-' : `${key}:`;
    if (item !== null && typeof item === 'object' && !(Array.isArray(item) && item.every(v => v === null || typeof v !== 'object'))) {
      lines.push(indent + name, ...textLines(item, indent + '  '));
    } else if (typeof item === 'string' && item.includes('\n')) {
      lines.push(`${indent}${name} |`, ...item.split('\n').map(line => `${indent}  ${line}`));
    } else lines.push(`${indent}${name} ${scalar(item)}`);
  }
  return lines;
}

/** [{op: 'same'|'del'|'add', text}] turning `before` into `after`. */
export function lineDiff(before, after) {
  const n = before.length, m = after.length;
  if (n * m > MAX_CELLS) return [...before.map(text => ({op: 'del', text})), ...after.map(text => ({op: 'add', text}))];
  const table = Array.from({length: n + 1}, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) {
    table[i][j] = before[i] === after[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  }
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (before[i] === after[j]) { out.push({op: 'same', text: before[i]}); i++; j++; }
    else if (table[i + 1][j] >= table[i][j + 1]) out.push({op: 'del', text: before[i++]});
    else out.push({op: 'add', text: after[j++]});
  }
  while (i < n) out.push({op: 'del', text: before[i++]});
  while (j < m) out.push({op: 'add', text: after[j++]});
  return out;
}

/** Collapse long unchanged runs to `context` lines around each change. */
export function withContext(diff, context = 3) {
  const keep = diff.map(() => false);
  diff.forEach((line, index) => {
    if (line.op === 'same') return;
    for (let k = Math.max(0, index - context); k <= Math.min(diff.length - 1, index + context); k++) keep[k] = true;
  });
  const out = [];
  let skipped = 0;
  diff.forEach((line, index) => {
    if (keep[index]) { if (skipped) out.push({op: 'skip', text: `… ${skipped} unchanged line${skipped === 1 ? '' : 's'}`}); skipped = 0; out.push(line); }
    else skipped++;
  });
  if (skipped) out.push({op: 'skip', text: `… ${skipped} unchanged line${skipped === 1 ? '' : 's'}`});
  return out;
}
