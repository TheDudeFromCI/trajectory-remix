/* Metrics view: population coverage and the selected trajectory's metric status.
   Renders the saved-record metrics payloads; never computes metrics itself. */
import {node, shortId, tag} from './ui-kit.js';

const $ = id => document.getElementById(id);
const pct = value => value === null || value === undefined ? '—' : (value * 100).toFixed(1) + '%';
const label = value => String(value).replaceAll('_', ' ');

function widget(title, body, actions) {
  const root = node('section', undefined, 'widget'), head = node('div', undefined, 'widget-head');
  head.append(node('h2', title)); if (actions) head.append(actions);
  const content = node('div', undefined, 'widget-body'); content.append(...[body].flat());
  root.append(head, content); return root;
}

function tile(value, text, tip) {
  const root = node('div', undefined, 'stat-tile');
  root.append(node('span', String(value), 'stat-value num'), node('span', text, 'stat-label'));
  if (tip) { const info = node('span', undefined, 'info'); info.tabIndex = 0; info.dataset.tip = tip; root.querySelector('.stat-label').append(' ', info); }
  return root;
}

function shareBar(value, tone) {
  const bar = node('span', undefined, 'share-bar'), fill = node('span', undefined, 'share-fill');
  fill.style.width = Math.max(0, Math.min(1, value || 0)) * 100 + '%'; fill.classList.add('tone-' + tone);
  bar.append(fill); return bar;
}

function targetsTable(dimension) {
  const table = node('table', undefined, 'data metrics-targets'), head = node('tr'), body = node('tbody');
  for (const [text, cls] of [['Choice'], ['Target', 'num'], [''], ['Observed', 'num'], [''], ['Δ points', 'num']]) head.append(node('th', text, cls));
  const thead = node('thead'); thead.append(head); table.append(thead, body);
  for (const [choice, row] of Object.entries(dimension)) {
    const tr = node('tr'), delta = row.delta_percentage_points;
    const deltaCell = node('td', delta === null || delta === undefined ? '—' : (delta > 0 ? '+' : '') + delta.toFixed(1), 'num');
    if (Math.abs(delta) >= 10) deltaCell.classList.add(delta > 0 ? 'over' : 'under');
    const targetBar = node('td'), observedBar = node('td');
    targetBar.append(shareBar(row.configured_target_share, 'muted')); observedBar.append(shareBar(row.observed_share, 'accent'));
    tr.append(node('td', label(choice)), node('td', pct(row.configured_target_share), 'num'), targetBar, node('td', pct(row.observed_share), 'num'), observedBar, deltaCell);
    body.append(tr);
  }
  return table;
}

function targetsWidget(coverage) {
  const comparison = coverage?.configured_target_comparison || {};
  const dims = Object.keys(comparison);
  if (!dims.length) return widget('Targets vs observed', node('div', 'No accepted proposals in this search yet. Coverage counts Candidate or higher.', 'empty'));
  const picker = node('select'); picker.setAttribute('aria-label', 'Dimension');
  for (const d of dims) { const o = node('option', label(d)); o.value = d; picker.append(o); }
  const area = node('div', undefined, 'table-wrap');
  const draw = () => area.replaceChildren(targetsTable(comparison[picker.value]));
  picker.onchange = draw; draw();
  const parts = [area];
  if (coverage.mixed_matrix_versions_warning) parts.unshift(node('p', 'This search mixes proposals from different matrix versions; shares compare against the current targets.', 'note tone-warning'));
  return widget('Targets vs observed', parts, picker);
}

function availabilityWidget(population) {
  const table = node('table', undefined, 'data'), head = node('tr'), body = node('tbody');
  for (const [text, cls] of [['Metric'], ['Kind'], ['Unavailable', 'num'], ['Stale', 'num']]) head.append(node('th', text, cls));
  const thead = node('thead'); thead.append(head); table.append(thead, body);
  for (const [key, definition] of Object.entries(population.definitions || {})) {
    const tr = node('tr'), unavailable = population.unavailable?.[key] ?? 0, stale = population.stale?.[key] ?? 0;
    const u = node('td', String(unavailable), 'num'), s = node('td', String(stale), 'num');
    if (population.total && unavailable === population.total) u.classList.add('faint');
    if (stale) s.classList.add('warn');
    tr.append(node('td', label(key)), node('td', definition.kind, 'muted'), u, s); body.append(tr);
  }
  const wrap = node('div', undefined, 'table-wrap'); wrap.append(table);
  return widget('Metric availability', wrap);
}

function sizeWidget(population) {
  const labels = population.size_labels || [], counts = population.size_distribution || {};
  if (!labels.length) return widget('Trajectory sizes', node('div', 'No size labels are configured. Add them in Project settings.', 'empty'));
  const table = node('table', undefined, 'data'), head = node('tr'), body = node('tbody');
  for (const [text, cls] of [['Size'], ['Agent tokens', 'num'], ['Total tokens', 'num'], ['Trajectories', 'num']]) head.append(node('th', text, cls));
  const thead = node('thead'); thead.append(head); table.append(thead, body);
  for (const l of labels) {
    const tr = node('tr'); tr.append(node('td', l.name), node('td', l.agent_tokens.toLocaleString(), 'num'), node('td', l.total_tokens.toLocaleString(), 'num'), node('td', String(counts[l.name] ?? 0), 'num')); body.append(tr);
  }
  const wrap = node('div', undefined, 'table-wrap'); wrap.append(table);
  return widget('Trajectory sizes', [node('p', 'Nearest size by estimated total tokens (about 3.5 characters per token, log2 distance).', 'note'), wrap]);
}

function selectedWidget(selected) {
  if (!selected) return widget('Selected trajectory', node('div', 'Select a trajectory in the Editor to see its metric status.', 'empty'));
  const table = node('table', undefined, 'data'), head = node('tr'), body = node('tbody');
  for (const text of ['Metric', 'Status', 'Value']) head.append(node('th', text));
  const thead = node('thead'); thead.append(head); table.append(thead, body);
  const tones = {measured: 'success', available: 'success', stale: 'warning', unavailable: 'faint'};
  for (const [key, value] of Object.entries(selected.values || {})) {
    const tr = node('tr'), status = node('td'), obs = value.observation;
    status.append(tag(label(value.status), tones[value.status] || 'muted'));
    const shown = obs === null || obs === undefined ? '—' : typeof obs === 'object' ? JSON.stringify(obs.value ?? obs) : String(obs);
    tr.append(node('td', label(key)), status, node('td', shown, 'mono small truncate')); body.append(tr);
  }
  const wrap = node('div', undefined, 'table-wrap'); wrap.append(table);
  const title = node('span', shortId(selected.id), 'mono small muted');
  return widget('Selected trajectory', wrap, title);
}

/** Render population metrics and, if given, the selected trajectory's values. */
export function renderMetrics(population, selected, scopeText) {
  const coverage = population.proposal_coverage || {};
  const unavailableTotal = Object.values(population.unavailable || {}).reduce((a, b) => a + b, 0);
  const staleTotal = Object.values(population.stale || {}).reduce((a, b) => a + b, 0);
  const tiles = node('div', undefined, 'stat-tiles');
  tiles.append(
    tile(population.total ?? 0, 'Saved records in search'),
    tile(coverage.included_records ?? 0, 'Accepted for coverage', population.acceptance ? 'Coverage counts records with ' + population.acceptance + '.' : undefined),
    tile(unavailableTotal, 'Unavailable values', 'Metric values not yet measured or annotated. Use “Calculate missing metrics” to fill measurable ones.'),
    tile(staleTotal, 'Stale values', 'Values recorded against an older source or metric definition.'),
    tile((population.total_characters ?? 0).toLocaleString(), 'Total characters', 'Characters of text in every event of the saved records in this search.'),
    tile((population.agent_characters ?? 0).toLocaleString(), 'Agent-generated characters', 'Characters in agent text and tool call events.'));
  const grid = node('div', undefined, 'metrics-grid');
  grid.append(targetsWidget(coverage), availabilityWidget(population), sizeWidget(population), selectedWidget(selected));
  $('metrics-content').replaceChildren(tiles, grid);
  $('metrics-scope').textContent = scopeText;
  $('metrics-values').textContent = JSON.stringify({population, selected, note: 'Saved records only; unsaved drafts excluded.'}, null, 2);
}
