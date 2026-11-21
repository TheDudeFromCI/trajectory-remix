/* Small DOM helpers shared by the studio views. No network access. */

export const node = (tag, text, cls) => {
  const n = document.createElement(tag);
  if (text !== undefined && text !== null) n.textContent = text;
  if (cls) n.className = cls;
  return n;
};

/** Write text only when it changed, so polling never re-renders identical content. */
export const setText = (el, text) => { if (el.textContent !== text) el.textContent = text; };
export const toggleClass = (el, name, on) => { if (el.classList.contains(name) !== on) el.classList.toggle(name, on); };
export const setAttr = (el, name, value) => {
  if (value === null || value === undefined || value === false) { if (el.hasAttribute(name)) el.removeAttribute(name); }
  else if (el.getAttribute(name) !== String(value)) el.setAttribute(name, String(value));
};

export const icon = name => {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'), use = document.createElementNS(svg.namespaceURI, 'use');
  svg.setAttribute('class', 'ico'); svg.setAttribute('aria-hidden', 'true'); use.setAttribute('href', '#i-' + name); svg.append(use);
  return svg;
};

export const tag = (text, tone = 'muted', cls = '') => node('span', text, `tag tone-${tone}${cls ? ' ' + cls : ''}`);
export const shortId = id => String(id || '').slice(0, 8);

/**
 * Keyed list reconciliation. Existing children are reused and updated in place;
 * only added, removed or moved items touch the DOM structure.
 */
export function reconcile(container, items, key, create, update) {
  const existing = new Map([...container.children].filter(c => c.dataset.key !== undefined).map(c => [c.dataset.key, c]));
  const wanted = items.map(item => {
    const k = String(key(item));
    let el = existing.get(k);
    if (!el) { el = create(item); el.dataset.key = k; }
    existing.delete(k);
    update?.(el, item);
    return el;
  });
  for (const stale of existing.values()) stale.remove();
  for (const child of [...container.children]) if (child.dataset.key === undefined) child.remove();
  wanted.forEach((el, index) => { if (container.children[index] !== el) container.insertBefore(el, container.children[index] || null); });
}

export function jobProgress(job) {
  const stages = job.stage_progress;
  return stages
    ? {value: stages.completed, total: stages.total, state: job.state, unit: 'stages'}
    : {value: job.progress, total: job.total, state: job.state};
}

/** Progress bar: create once, update with value/total and a state word. */
export function progressBar(label = '') {
  const root = node('div', undefined, 'progress'), row = node('div', undefined, 'progress-row');
  const name = node('span', label, 'progress-label'), status = node('span', '', 'progress-status'), pct = node('span', '', 'progress-pct');
  const track = node('div', undefined, 'progress-track'), fill = node('div', undefined, 'progress-fill');
  track.setAttribute('role', 'progressbar'); track.setAttribute('aria-valuemin', '0'); track.append(fill);
  row.append(name, status, pct); root.append(row, track);
  root.update = ({value = 0, total = 0, state = '', status: text = '', label: title, unit}) => {
    if (title !== undefined) setText(name, title);
    const tone = JOB_TONES[state] || 'accent', fraction = total > 0 ? Math.min(1, value / total) : 0;
    const indeterminate = total <= 0 && state === 'running';
    if (fill.classList.contains('indeterminate') !== indeterminate) fill.classList.toggle('indeterminate', indeterminate);
    const color = `var(--${TONE_VARS[tone] || tone})`;
    if (fill.style.getPropertyValue('--tone') !== color) fill.style.setProperty('--tone', color);
    const width = indeterminate ? '' : (state === 'completed' && !unit ? 100 : fraction * 100) + '%';
    if (fill.style.width !== width) fill.style.width = width;
    setText(status, text);
    setText(pct, total > 0 ? `${value}/${total}${unit ? ' ' + unit : ''}` : state ? stateWord(state) : '');
    setAttr(track, 'aria-valuemax', total || null); setAttr(track, 'aria-valuenow', total ? value : null);
    setAttr(track, 'aria-label', `${title ?? name.textContent} ${stateWord(state)} ${total ? value + ' of ' + total + (unit ? ' ' + unit : '') : ''}`.trim());
  };
  return root;
}

const TONE_VARS = {accent: 'accent', success: 'success', warning: 'warning', danger: 'danger', muted: 'ink-muted', faint: 'ink-faint'};
export const JOB_TONES = {running: 'accent', queued: 'muted', paused: 'warning', completed: 'success', failed: 'danger', cancelled: 'faint'};
export const KIND_TONES = {run: 'mint', bulk: 'lilac', export: 'sky', import: 'peach', log_export: 'butter', sampler: 'rose'};
export const KIND_NAMES = {run: 'Generation', bulk: 'Bulk / review', export: 'Export', import: 'Import', log_export: 'Log export', sampler: 'Sampler run'};
export const stateWord = state => state ? state[0].toUpperCase() + state.slice(1) : '';
export const kindName = kind => KIND_NAMES[kind] || stateWord(String(kind || 'job').replaceAll('_', ' '));

export const STAGES = {
  '-3': ['Unfinished', 'faint'], '-2': ['Rejected', 'danger'], '-1': ['Unsuccessful', 'warning'],
  0: ['Pending', 'sky'], 1: ['Candidate', 'lilac'], 2: ['Validated', 'mint'], 3: ['Ready', 'success']
};
export const stageInfo = stage => STAGES[stage ?? -3] || [String(stage), 'muted'];

/** Build a tab list with panels for dynamically created content. */
export function makeTabs(defs, label) {
  const root = node('div', undefined, 'tabs'), list = node('div', undefined, 'tab-list'), tabs = new Map();
  list.setAttribute('role', 'tablist'); list.setAttribute('aria-label', label); root.append(list);
  const select = id => {
    for (const [key, {tab, panel}] of tabs) {
      const on = key === id; tab.setAttribute('aria-selected', String(on)); tab.tabIndex = on ? 0 : -1; panel.hidden = !on;
    }
  };
  for (const {id, text, panel} of defs) {
    const tab = node('button', text); tab.type = 'button'; tab.setAttribute('role', 'tab');
    const uid = 'dyn-' + Math.random().toString(36).slice(2, 9);
    tab.id = uid + '-tab'; panel.id = uid + '-panel'; panel.setAttribute('role', 'tabpanel');
    tab.setAttribute('aria-controls', panel.id); panel.setAttribute('aria-labelledby', tab.id);
    tab.onclick = () => select(id);
    tab.onkeydown = event => {
      const step = {ArrowRight: 1, ArrowLeft: -1}[event.key]; if (!step) return;
      const visible = [...tabs.entries()].filter(([, t]) => !t.tab.hidden);
      const next = visible[(visible.findIndex(([key]) => key === id) + step + visible.length) % visible.length];
      select(next[0]); next[1].tab.focus(); event.preventDefault();
    };
    tabs.set(id, {tab, panel}); list.append(tab); root.append(panel);
  }
  select(defs[0].id);
  return {root, select, tab: id => tabs.get(id).tab, selected: () => [...tabs].find(([, t]) => !t.panel.hidden)?.[0]};
}

/** One pastel per window, stable for the window's position in the draft. */
export const PASTELS = ['mint', 'sky', 'peach', 'lilac', 'butter', 'rose'];
export const pastel = index => PASTELS[((index % PASTELS.length) + PASTELS.length) % PASTELS.length];
