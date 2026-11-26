/* Windows tab: a selectable list of the draft's windows with New, Modify, Rename and
   Delete. Deleting warns first and lists the events that are deleted with the window. */
import {ChoiceField} from './choice-field.js';
import {node, setAttr, tag} from './ui-kit.js';
import {affectedEvents, createWindow, deleteWindow, eventCounts, renameWindow} from './window-edits.js';

const PURPOSES = ['reasoning', 'tool', 'chatroom'];
const TITLES = {create: 'New window', edit: 'Modify window', rename: 'Rename window'};
const SUBMIT = {create: 'Create window', edit: 'Save changes', rename: 'Rename'};

export class WindowsPanel {
  /**
   * @param {object} options
   * @param {(id: string) => HTMLElement} options.$ element lookup
   * @param {() => object | null} options.draft the editable draft
   * @param {(deletedEvents?: string[]) => void} options.onChange called after the draft changes
   * @param {(error: Error) => void} options.report shows an error
   */
  constructor({$, draft, onChange, report}) {
    this.$ = $; this.draft = draft; this.onChange = onChange; this.report = report;
    this.selected = null; this.mode = null; this.locked = false;
    this.purpose = new ChoiceField($('window-purpose'), $('window-purpose-custom'));
    this.effort = new ChoiceField($('window-effort'), $('window-effort-custom'), {empty: 'Not set'});
    this.purpose.setChoices(PURPOSES);
    const act = (id, fn) => $(id).addEventListener('click', () => { try { fn(); } catch (error) { report(error); } });
    act('window-new', () => this.open('create'));
    act('window-edit', () => this.open('edit'));
    act('window-rename', () => this.open('rename'));
    act('window-delete', () => this.confirmDelete());
    act('window-cancel', () => this.close());
    act('window-delete-confirm', () => this.deleteSelected());
    $('window-form').addEventListener('submit', e => { e.preventDefault(); try { this.submit(); } catch (error) { this.problem(error.message); } });
    $('window-form').addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); this.close(); } });
    $('window-id').addEventListener('input', () => this.problem(''));
    $('window-list').addEventListener('keydown', e => this.navigate(e));
  }
  setReasoningLevels(levels) { this.effort.setChoices(levels); }
  /** Select a window (for example from a comment) and show it. */
  select(id) {
    if (!this.draft()?.windows[id]) return;
    if (this.selected !== id) this.close();
    this.selected = id; this.render();
    this.row(id)?.scrollIntoView?.({block: 'nearest'});
  }
  /** Start over for a newly opened trajectory, selecting `id` (the selected event's window). */
  reset(id) { this.mode = null; this.selected = id ?? null; this.problem(''); }
  setLocked(locked) { this.locked = locked; if (locked) this.close(); else this.render(); }
  render() {
    const draft = this.draft(), $ = this.$, ids = Object.keys(draft?.windows || {});
    if (!ids.includes(this.selected)) { this.selected = ids[0] ?? null; if (this.mode !== 'create') this.mode = null; }
    const counts = eventCounts(draft);
    const purposes = [...new Set([...PURPOSES, ...Object.values(draft?.windows || {}).map(w => w.purpose)])];
    if (purposes.join('\n') !== this.purpose.choices.join('\n')) this.purpose.setChoices(purposes);
    const list = $('window-list');
    list.replaceChildren(...ids.map(id => this.listRow(id, draft.windows[id], counts.get(id) || 0)));
    if (!ids.length) list.append(node('div', draft ? 'No windows yet. Create one to start writing events.' : 'No trajectory selected.', 'empty'));
    setAttr(list, 'aria-activedescendant', this.selected ? this.rowId(this.selected) : null);
    $('window-new').disabled = this.locked || !draft;
    for (const id of ['window-edit', 'window-rename', 'window-delete']) $(id).disabled = this.locked || !this.selected;
    $('window-form').hidden = !this.mode;
  }
  rowId(id) { return 'window-row-' + encodeURIComponent(id).replace(/[^\w-]/g, '_'); }
  row(id) { return this.$(this.rowId(id)); }
  listRow(id, w, count) {
    const row = node('div', undefined, 'window-row' + (id === this.selected ? ' selected' : ''));
    row.id = this.rowId(id); row.setAttribute('role', 'option'); row.setAttribute('aria-selected', String(id === this.selected));
    row.append(node('span', id, 'window-row-id truncate'), tag(w.purpose, w.purpose === 'chatroom' ? 'sky' : 'accent'));
    if (w.reasoning_effort) row.append(tag('effort ' + w.reasoning_effort, 'muted'));
    if (!w.agent_writable) row.append(tag('read-only', 'muted'));
    if (w.intentionally_left_open) row.append(tag('left open', 'warning'));
    row.append(node('span', `${count} event${count === 1 ? '' : 's'}`, 'window-row-count small muted'));
    row.addEventListener('click', () => { if (this.mode && this.mode !== 'create' && id !== this.selected) this.close(); this.selected = id; this.render(); this.$('window-list').focus(); });
    row.addEventListener('dblclick', () => { if (!this.locked) this.open('edit'); });
    return row;
  }
  navigate(e) {
    const ids = Object.keys(this.draft()?.windows || {}), at = ids.indexOf(this.selected);
    const next = {ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: ids.length - 1}[e.key];
    if (next !== undefined && ids.length) { e.preventDefault(); this.select(ids[Math.max(0, Math.min(ids.length - 1, next))]); }
    else if (e.key === 'Enter' && this.selected && !this.locked) { e.preventDefault(); this.open('edit'); }
    else if (e.key === 'Delete' && this.selected && !this.locked) { e.preventDefault(); this.confirmDelete(); }
  }
  open(mode) {
    const draft = this.draft(), $ = this.$; if (!draft || this.locked) return;
    const w = mode === 'create' ? {purpose: 'reasoning', agent_writable: true} : draft.windows[this.selected];
    if (!w) throw new Error('Select a window first.');
    this.mode = mode;
    $('window-form-title').textContent = mode === 'create' ? TITLES.create : `${TITLES[mode]} “${this.selected}”`;
    $('window-submit').textContent = SUBMIT[mode];
    $('window-id-field').hidden = mode === 'edit'; $('window-props').hidden = mode === 'rename';
    $('window-id').value = mode === 'rename' ? this.selected : '';
    this.purpose.value = w.purpose; this.effort.value = w.reasoning_effort || '';
    $('window-writable').checked = w.agent_writable; $('window-left-open').checked = w.intentionally_left_open ?? false;
    this.problem(''); this.render();
    (mode === 'edit' ? $('window-purpose') : $('window-id')).focus();
    if (mode === 'rename') $('window-id').select?.();
  }
  close() { if (!this.mode) return; this.mode = null; this.problem(''); this.render(); }
  problem(text) { const n = this.$('window-form-problem'); n.textContent = text; n.hidden = !text; setAttr(this.$('window-id'), 'aria-invalid', text ? 'true' : null); }
  props() {
    const purpose = this.purpose.value;
    if (!purpose) throw new Error('Choose a purpose or enter a custom one.');
    return {purpose, agent_writable: this.$('window-writable').checked, reasoning_effort: this.effort.value || null, intentionally_left_open: this.$('window-left-open').checked};
  }
  submit() {
    const draft = this.draft(), id = this.$('window-id').value;
    if (!draft || this.locked) return this.close();
    if (this.mode === 'create') { createWindow(draft, id, this.props()); this.selected = id; }
    else if (this.mode === 'rename') { renameWindow(draft, this.selected, id); this.selected = id; }
    else if (this.mode === 'edit') draft.windows[this.selected] = {...draft.windows[this.selected], ...this.props()};
    this.mode = null; this.render(); this.onChange();
    this.$('window-list').focus();
  }
  confirmDelete() {
    const draft = this.draft(), $ = this.$, id = this.selected; if (!draft || !id || this.locked) return;
    this.close();
    const affected = affectedEvents(draft, id);
    $('window-delete-name').textContent = id;
    $('window-delete-summary').textContent = affected.length
      ? `These ${affected.length} event${affected.length === 1 ? '' : 's'} will also be deleted. Events that ran after them lose that dependency.`
      : 'No events use this window, so only the window is deleted.';
    $('window-delete-events').replaceChildren(...affected.map(e => { const li = node('li'); li.append(node('code', e.id), node('span', ` ${e.kind.replaceAll('_', ' ')} · ${e.reason}`, 'small muted')); return li; }));
    $('window-delete-events').hidden = !affected.length;
    $('window-delete-confirm').textContent = affected.length ? `Delete window and ${affected.length} event${affected.length === 1 ? '' : 's'}` : 'Delete window';
    $('window-delete-dialog').showModal();
  }
  deleteSelected() {
    const draft = this.draft(), id = this.selected; this.$('window-delete-dialog').close();
    if (!draft || this.locked || !Object.hasOwn(draft.windows, id)) return;
    const removed = deleteWindow(draft, id); this.selected = null;
    this.render(); this.onChange(removed); this.$('window-list').focus();
  }
}
