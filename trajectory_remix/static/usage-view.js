/* Job usage tab: token counts and provider-reported cost from GET /jobs/{id}/usage.
   Re-renders only when the numbers change, so polling causes no DOM churn. */
import {node} from './ui-kit.js';

const COLUMNS = [
  ['requests', 'Requests'], ['prompt_tokens', 'Prompt tokens'], ['cached_tokens', 'Cached'],
  ['completion_tokens', 'Output tokens'], ['reasoning_tokens', 'Reasoning'], ['cost', 'Cost'],
  ['seconds', 'Model time']
];
/** Seconds as a short duration: 42s, 3m 05s, 2h 07m. */
export function duration(seconds) {
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor(s % 3600 / 60)).padStart(2, '0')}m`;
}
const OPERATIONS = {flow_select: 'Selections', event: 'Event writing', review: 'Reviews', repair: 'Repairs'};

export function formatValue(key, value, row) {
  if (value === null || value === undefined) return '—';
  if (key === 'cost') return '$' + (value >= 1 ? value.toFixed(2) : value.toPrecision(3));
  if (key === 'seconds') return duration(value);
  const whole = Math.round(value).toLocaleString('en-US');
  if (key === 'cached_tokens' && row?.prompt_tokens) return `${whole} (${Math.round(100 * value / row.prompt_tokens)}%)`;
  return whole;
}

function table(rows) {
  const t = node('table', undefined, 'data usage-table'), head = node('tr');
  head.append(node('th', ''), ...COLUMNS.map(([, title]) => node('th', title)));
  t.append(head);
  for (const [title, row, note] of rows) {
    const tr = node('tr'); tr.append(node('th', title));
    for (const [key] of COLUMNS) {
      const cell = node('td', formatValue(key, row[key], row), 'num');
      if ((row[key] === null || row[key] === undefined) && key !== 'requests') cell.dataset.tip = note || 'Not reported by every response, so no total is shown.';
      tr.append(cell);
    }
    t.append(tr);
  }
  return t;
}

/** Render a usage report into box; returns the signature it rendered. */
export function renderUsage(box, report, previous = '') {
  const signature = JSON.stringify(report);
  if (signature === previous) return previous;
  if (!report?.total) {
    box.replaceChildren(node('p', 'No model responses yet. Usage appears here as responses arrive.', 'empty'));
    return signature;
  }
  const rows = [['Total', report.total]];
  if (report.per_trajectory && report.trajectories) rows.push([`Per trajectory (${report.trajectories})`, report.per_trajectory, 'Average over trajectories with at least one response.']);
  for (const [op, row] of Object.entries(report.operations)) rows.push([OPERATIONS[op] || op, row]);
  const notes = node('p', 'Counts and cost as reported by the provider for each response, including replies that were rejected and retried. Cost is shown only when every response reported it. Model time adds up each request\'s wait, so it exceeds wall time when requests run in parallel.', 'small muted');
  const wall = typeof report.wall_seconds === 'number' ? node('p', `Wall time, first to last response: ${duration(report.wall_seconds)}`, 'small') : null;
  const models = report.models?.length ? node('p', 'Models: ' + report.models.join(', '), 'small muted mono') : null;
  box.replaceChildren(table(rows), ...(wall ? [wall] : []), notes, ...(models ? [models] : []));
  return signature;
}
