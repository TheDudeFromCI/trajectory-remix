/* Metrics tab in the Editor: view and set the selected trajectory's metric values.
   Values save straight to the stored trajectory (with source and definition provenance),
   after any pending trajectory edits have saved, and each change is one Undo step.
   Measured metrics (calculated from the source or from an export) are shown read-only. */
import {node, setAttr, setText, tag} from './ui-kit.js';

const GROUPS = [
  ['category', 'Categories', 'One choice from the sampling matrix.'],
  ['labels', 'Labels', 'Any number of choices.'],
  ['annotation', 'Outcomes and notes', 'Free values: text, a number, true/false or JSON.'],
  ['measured', 'Measured', 'Filled by “Calculate missing metrics” (Metrics view) or by an export. Read-only here.'],
];
const TONES = {available: 'success', stale: 'warning', unavailable: 'faint'};
const STATUS = {available: 'Set', stale: 'Stale', unavailable: 'Not set'};
const words = value => String(value).replaceAll('_', ' ');

/** Group a metric definition: editable kinds by kind, everything measured together. */
export const groupOf = spec => spec.scope === 'annotation' ? spec.kind : 'measured';

/** Turn typed text into a value: JSON when it parses, otherwise the text itself. Empty means clear. */
export function parseNote(text) {
  const trimmed = text.trim();
  if (!trimmed) return undefined;
  try { const value = JSON.parse(trimmed); return value === null ? undefined : value; } catch { return trimmed; }
}
export const showNote = value => value === undefined || value === null ? '' : typeof value === 'string' ? value : JSON.stringify(value);

export class MetricsEditor {
  /** els: {list, note, unset}; beforeSave() saves pending trajectory edits first;
     onSaved(view) runs after each stored change with its Undo delta in view.delta;
     onView(view, state) runs whenever the shown values change. */
  /** `shownElsewhere` lists metric keys with their own control (left out of the list). */
  constructor({api, report, els, beforeSave = async () => {}, onSaved = async () => {}, onView = () => {}, shownElsewhere = []}) {
    Object.assign(this, {api, report, els, beforeSave, onSaved, onView, shownElsewhere: new Set(shownElsewhere)});
    this.view = null; this.key = ''; this.busy = Promise.resolve(); this.state = {id: null};
    els.unset.addEventListener('change', () => this.render());
  }
  /** Follow the selected trajectory; reloads only when its id or revision changed. */
  sync({id, revision, locked}) {
    this.state = {id, revision, locked};
    const key = id ? `${id}@${revision}` : '';
    if (key !== this.key) { this.key = key; this.load().catch(this.report); }
    this.renderState();
  }
  async load() {
    const {id} = this.state;
    if (!id) { this.view = null; return this.render(); }
    const view = await this.api(`/metrics/trajectories/${id}`);
    if (id !== this.state.id) return;
    this.view = view; this.render();
  }
  get editable() { return !!this.view && !this.state.locked; }
  renderState() {
    this.onView(this.view, this.state);
    const {locked} = this.state, note = this.els.note;
    const text = !this.state.id ? '' : locked ? 'A job holds this trajectory. Metrics unlock when it settles.'
      : 'Changes save at once. Undo reverts them like any other edit.';
    setText(note, text); note.className = 'small ' + (locked ? 'tone-warning metrics-note' : 'muted metrics-note');
    for (const input of this.els.list.querySelectorAll('[data-metric-input]')) input.disabled = !this.editable;
  }
  render() {
    this.onView(this.view, this.state);
    const list = this.els.list;
    if (!this.view) { list.replaceChildren(node('div', this.state.id ? 'Loading metrics…' : 'Select a trajectory to edit its metrics.', 'empty')); return; }
    const onlyUnset = this.els.unset.checked, sections = [];
    for (const [group, title, hint] of GROUPS) {
      const rows = Object.entries(this.view.values).filter(([key, v]) => !this.shownElsewhere.has(key) && groupOf(v.definition) === group && (!onlyUnset || v.status !== 'available'));
      if (rows.length) sections.push(this.section(title, hint, rows.map(([key, value]) => this.row(key, value))));
    }
    list.replaceChildren(...(sections.length ? sections : [node('div', 'Every metric has a value.', 'empty')]));
    this.renderState();
  }
  section(title, hint, rows) {
    const root = node('section', undefined, 'option-group metrics-group'), head = node('h3', title);
    head.append(' ', node('span', String(rows.length), 'badge'));
    root.append(head, node('p', hint, 'small muted'), ...rows); return root;
  }
  row(key, value) {
    const spec = value.definition, root = node('div', undefined, 'metric-row'), head = node('div', undefined, 'metric-head');
    const name = node('span', spec.label || words(key), 'metric-name truncate'); setAttr(name, 'data-tip', key);
    const status = tag(STATUS[value.status] || words(value.status), TONES[value.status] || 'muted');
    if (value.status === 'stale') setAttr(status, 'data-tip', 'Recorded against an older source or metric definition. Save it again to confirm it.');
    head.append(name, status);
    const current = value.observation?.value, group = groupOf(spec);
    if (group !== 'measured' && value.status === 'stale') head.append(this.action('Confirm', () => this.save(key, current), 'Record this value against the current source'));
    if (group !== 'measured' && value.status !== 'unavailable') head.append(this.action('Clear', () => this.save(key, undefined), 'Remove this value'));
    root.append(head, this.control(key, spec, group, current));
    return root;
  }
  action(text, run, tip) {
    const b = node('button', text, 'ghost sm'); b.type = 'button'; b.dataset.metricInput = ''; setAttr(b, 'data-tip', tip);
    b.addEventListener('click', run); return b;
  }
  control(key, spec, group, current) {
    if (group === 'category') return this.select(key, spec, current);
    if (group === 'labels') return this.labels(key, spec, Array.isArray(current) ? current : []);
    if (group === 'annotation') return this.note(key, current);
    const shown = node('span', current === undefined ? '—' : String(current), 'num metric-value');
    if (spec.unit) shown.append(node('span', ' ' + spec.unit, 'muted small'));
    return shown;
  }
  select(key, spec, current) {
    const select = node('select'); select.dataset.metricInput = ''; setAttr(select, 'aria-label', spec.label || words(key));
    const none = node('option', '— Not set —'); none.value = ''; select.append(none);
    for (const choice of spec.choices || []) { const o = node('option', words(choice)); o.value = choice; select.append(o); }
    select.value = current ?? '';
    select.addEventListener('change', () => this.save(key, select.value || undefined));
    return select;
  }
  labels(key, spec, current) {
    const box = node('details', undefined, 'metric-labels'), summary = node('summary', current.length ? current.map(words).join(', ') : 'None selected', 'truncate');
    const grid = node('div', undefined, 'metric-label-grid'); box.append(summary, grid);
    for (const choice of spec.choices || []) {
      const label = node('label', undefined, 'small'), input = node('input'); input.type = 'checkbox'; input.dataset.metricInput = '';
      input.checked = current.includes(choice); input.value = choice;
      input.addEventListener('change', () => {
        const chosen = [...grid.querySelectorAll('input:checked')].map(i => i.value);
        this.save(key, chosen.length ? chosen : undefined);
      });
      label.append(input, ' ' + words(choice)); grid.append(label);
    }
    box.open = this.openLabels === key;
    box.addEventListener('toggle', () => { if (box.open) this.openLabels = key; else if (this.openLabels === key) this.openLabels = null; });
    return box;
  }
  note(key, current) {
    const input = node('input'); input.dataset.metricInput = ''; input.value = showNote(current);
    input.placeholder = 'Not set'; setAttr(input, 'aria-label', words(key));
    input.addEventListener('change', () => this.save(key, parseNote(input.value)));
    input.addEventListener('keydown', e => { if (e.key === 'Enter') input.blur(); });
    return input;
  }
  /** Store one value (undefined clears it). Saves run one after another on the latest revision. */
  save(key, value) {
    this.busy = this.busy.then(() => this.store(key, value)).catch(error => { this.report(error); return this.load().catch(this.report); });
    return this.busy;
  }
  async store(key, value) {
    const id = this.state.id;
    if (!this.editable || !id) return;
    await this.beforeSave();
    if (id !== this.state.id) return;
    const path = `/metrics/trajectories/${id}/${encodeURIComponent(key)}`, expected_revision = this.state.revision;
    const view = value === undefined ? await this.api(path, 'DELETE', {expected_revision})
      : await this.api(path, 'PUT', {expected_revision, value, origin: 'manual_annotation'});
    if (id !== this.state.id) return;
    this.view = view; this.key = `${id}@${view.revision}`; this.state.revision = view.revision;
    this.render(); await this.onSaved(view);
  }
}
