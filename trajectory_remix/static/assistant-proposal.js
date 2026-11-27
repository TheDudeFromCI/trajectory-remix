/* The Assistant's proposed changes to the selected trajectory, at the top of the Comments tab.
   The Assistant never edits the trajectory itself: its edits wait here, like a pull request,
   until you apply them (one edit, so Undo reverts it), discard them, or request changes, which
   sends a new comment and lets the agent keep working on the same proposal. Each change has a
   checkbox: Apply takes only the ticked ones and drops the rest. */
import {node, setText, tag} from './ui-kit.js';
import {lineDiff, textLines, withContext} from './line-diff.js';

const KIND_NAMES = {defaults: 'Defaults', window: 'Window', event: 'Event', metadata: 'Metadata'};
const CHANGE_TONES = {added: 'success', removed: 'danger', changed: 'warning'};
const CHANGE_WORDS = {added: 'Added', removed: 'Removed', changed: 'Changed'};
const MARKS = {same: ' ', del: '-', add: '+', skip: ' '};
const OPEN_LIMIT = 5; // Up to this many changes start expanded.

export const changeLabel = c => c.kind === 'defaults' ? KIND_NAMES.defaults : `${KIND_NAMES[c.kind]} ${c.id}`;
const sameTarget = (a, b) => a.kind === b.kind && (a.id ?? null) === (b.id ?? null);

/** The diff of one change as a <pre> of marked lines. */
export function diffBlock(change) {
  const pre = node('pre', undefined, 'proposal-diff');
  for (const line of withContext(lineDiff(textLines(change.before), textLines(change.after)))) {
    pre.append(node('span', `${MARKS[line.op]} ${line.text}\n`, `diff-${line.op}`));
  }
  return pre;
}

export class ProposalView {
  /** host: the panel element. revision() is the saved revision Apply builds on; ready() throws when
     the editor can't apply now (unsaved edits); locked() is true while a job owns the trajectory.
     onApplied(result) gets the saved trajectory with its Undo delta; onChange() runs when the
     pending proposal appears, changes or goes; requestChanges(text) sends a follow-up comment. */
  constructor({api, report, host, revision, ready = async () => {}, locked = () => false, onApplied = async () => {}, onChange = () => {}, requestChanges = async () => {}}) {
    Object.assign(this, {api, report, host, revision, ready, locked, onApplied, onChange, requestChanges});
    this.trajectory = null; this.proposal = null; this.loaded = ''; this.asking = null; this.busy = false;
    this.skipped = new Set(); // Labels of the changes the person unticked.
  }
  /** Comment IDs the pending proposal addresses: they resolve when it is applied. */
  get pending() { return new Set(this.proposal?.comment_ids || []); }
  async load(trajectory) {
    if (trajectory !== this.trajectory) { this.trajectory = trajectory; this.proposal = null; this.loaded = ''; this.asking = null; this.skipped.clear(); this.render(); this.onChange(); }
    if (!trajectory) return;
    const data = await this.api(`/trajectories/${trajectory}/assistant/proposal`);
    if (trajectory !== this.trajectory) return;
    const key = JSON.stringify(data.proposal);
    if (key === this.loaded) { this.renderButtons(); return; } // Polling: no DOM work when nothing changed.
    this.loaded = key; this.proposal = data.proposal; this.forgetGone(); this.render(); this.onChange();
  }
  /** The changes Apply would take: every change not unticked. */
  get selected() { return (this.proposal?.changes || []).filter(c => !this.skipped.has(changeLabel(c))); }
  toggle(change, on) {
    const key = changeLabel(change);
    if (on) this.skipped.delete(key); else this.skipped.add(key);
    this.host.querySelector(`details[data-key="${CSS.escape(key)}"]`)?.classList.toggle('skipped', !on);
    this.renderButtons();
  }
  /** Drop unticks for changes the proposal no longer has, so a later one starts ticked. */
  forgetGone() {
    const present = new Set((this.proposal?.changes || []).map(changeLabel));
    for (const key of this.skipped) if (!present.has(key)) this.skipped.delete(key);
  }
  async run(work) {
    if (this.busy) return;
    this.busy = true; this.renderButtons();
    try { await work(); } finally { this.busy = false; this.renderButtons(); }
  }
  apply() {
    const chosen = this.selected, total = this.proposal.changes.length, partial = chosen.length < total;
    if (!chosen.length) throw new Error('Tick at least one change to apply, or discard the proposal.');
    if (partial && globalThis.confirm?.(`Apply ${chosen.length} of ${total} changes? The ${total - chosen.length} unticked will be dropped.`) === false) return;
    return this.run(async () => {
      await this.ready();
      const body = {expected_revision: this.revision()};
      if (partial) body.changes = chosen.map(c => ({kind: c.kind, id: c.id ?? null}));
      const result = await this.api(`/trajectories/${this.trajectory}/assistant/proposal/apply`, 'POST', body);
      this.skipped.clear();
      this.proposal = null; this.loaded = ''; this.render(); this.onChange();
      await this.onApplied(result);
    });
  }
  discard() {
    if (globalThis.confirm?.('Discard the proposed changes? The comments they addressed reopen.') === false) return;
    return this.run(async () => {
      await this.api(`/trajectories/${this.trajectory}/assistant/proposal`, 'DELETE');
      this.proposal = null; this.loaded = ''; this.render(); this.onChange();
    });
  }
  ask() { this.asking = this.asking ?? ''; this.render(); this.host.querySelector('textarea')?.focus(); }
  sendRequest() {
    const text = (this.asking || '').trim();
    if (!text) throw new Error('Say what should change in the proposal.');
    return this.run(async () => {
      await this.requestChanges(text);
      this.asking = null; this.render();
    });
  }
  render() {
    const p = this.proposal;
    this.host.hidden = !p;
    if (!p) { this.host.replaceChildren(); return; }
    const head = node('header', undefined, 'proposal-head');
    const count = p.changes.length;
    head.append(node('h3', 'Proposed changes'), tag(`${count} change${count === 1 ? '' : 's'}`, 'accent'));
    const when = node('span', `from rev ${p.base_revision} · ${String(p.updated).slice(0, 16).replace('T', ' ')}`, 'small muted grow');
    when.dataset.tip = 'The assistant edits a copy. Nothing changes in the trajectory until you apply it.';
    head.append(when);
    const parts = [head, node('p', 'Inspect what the assistant changed, then apply it, request changes, or discard it. Untick a change to leave it out when applying.', 'small muted')];
    if (p.conflicts.length) {
      const names = p.conflicts.map(changeLabel).join(', ');
      parts.push(node('p', `Also changed in the trajectory since: ${names}. Undo your edit there, untick the change, or discard the proposal before applying.`, 'note tone-warning'));
    }
    const list = node('div', undefined, 'proposal-changes');
    const expand = count <= OPEN_LIMIT;
    for (const change of p.changes) list.append(this.changeCard(change, p.conflicts.some(c => sameTarget(c, change)), expand));
    parts.push(list);
    if (this.asking !== null) parts.push(this.requestForm());
    parts.push(this.actions());
    this.host.replaceChildren(...parts);
    this.renderButtons();
  }
  /** One change; it keeps the open state the person gave it across re-renders. */
  changeCard(change, conflict, expand) {
    const key = changeLabel(change), kept = !this.skipped.has(key);
    const card = node('details', undefined, `proposal-change ${change.change}${kept ? '' : ' skipped'}`);
    const summary = node('summary'), pick = node('input');
    pick.type = 'checkbox'; pick.checked = kept; pick.className = 'proposal-pick';
    pick.setAttribute('aria-label', `Apply ${key}`); pick.dataset.tip = 'Include this change when applying';
    pick.onclick = e => e.stopPropagation();
    pick.onchange = () => this.toggle(change, pick.checked);
    summary.append(pick, tag(CHANGE_WORDS[change.change], CHANGE_TONES[change.change]), node('span', key, 'mono'));
    if (conflict) summary.append(tag('Conflict', 'danger'));
    card.append(summary, diffBlock(change));
    card.dataset.key = key;
    card.open = this.host.querySelector(`details[data-key="${CSS.escape(card.dataset.key)}"]`)?.open ?? expand;
    return card;
  }
  requestForm() {
    const form = node('div', undefined, 'comment-reply-form'), box = node('textarea');
    box.rows = 2; box.value = this.asking; box.setAttribute('aria-label', 'Changes to request on the proposal');
    box.placeholder = 'What should the agent change? This becomes a comment and goes to the assistant.';
    box.oninput = () => { this.asking = box.value; };
    box.onkeydown = e => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); Promise.resolve().then(() => this.sendRequest()).catch(this.report); }
      if (e.key === 'Escape') { this.asking = null; this.render(); }
    };
    const row = node('div', undefined, 'comment-actions');
    const cancel = node('button', 'Cancel', 'sm ghost'), send = node('button', 'Send to agent', 'sm');
    cancel.onclick = () => { this.asking = null; this.render(); };
    send.onclick = () => Promise.resolve().then(() => this.sendRequest()).catch(this.report);
    row.append(cancel, send); form.append(box, row);
    return form;
  }
  actions() {
    const row = node('div', undefined, 'comment-actions proposal-actions');
    const button = (text, cls, run, tip) => { const b = node('button', text, `sm ${cls}`); b.dataset.tip = tip; b.onclick = () => Promise.resolve().then(run).catch(this.report); row.append(b); return b; };
    this.discardButton = button('Discard', 'ghost danger', () => this.discard(), 'Drop these changes; the comments they addressed reopen');
    this.askButton = this.asking === null ? button('Request changes', 'ghost', () => this.ask(), 'Tell the agent what to change; it keeps working on this proposal') : null;
    this.applyButton = button('Apply changes', 'primary', () => this.apply(), 'Apply to the trajectory as one edit (Undo reverts it) and resolve the comments it addressed');
    return row;
  }
  /** Enable the buttons for the current lock and conflict state. */
  renderButtons() {
    if (!this.proposal || !this.applyButton) return;
    const chosen = this.selected, total = this.proposal.changes.length;
    const locked = this.locked(), conflict = chosen.some(c => this.proposal.conflicts.some(x => sameTarget(x, c)));
    this.applyButton.disabled = this.busy || locked || conflict || !chosen.length;
    this.discardButton.disabled = this.busy || locked;
    if (this.askButton) this.askButton.disabled = this.busy || locked;
    setText(this.applyButton, this.busy ? 'Working…' : chosen.length < total ? `Apply ${chosen.length} of ${total}` : 'Apply changes');
  }
}
