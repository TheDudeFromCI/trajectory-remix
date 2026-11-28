/* Draft tab in the Editor: the trajectory's prose draft (metadata.draft).
   The text edits and saves like any other field. New draft samples unset metrics from the
   matrix, assigns them, and runs the standard Draft pass; Assemble runs the standard Assembly
   pass on the draft, which edits the existing graph in place. Both run as jobs that lock the
   trajectory until they settle. */
import {setAttr, setText} from './ui-kit.js';

export const draftText = source => typeof source?.metadata?.draft === 'string' ? source.metadata.draft : '';

/** Why the buttons are unavailable, or '' when they can run. */
export function blocker({source, locked, roles}) {
  if (!source) return 'Select a trajectory to see its draft.';
  if (locked) return 'A job is working on this trajectory. The draft unlocks when it settles.';
  if (!roles?.draft) return 'Assign the Draft endpoint role in Project Settings to generate or assemble.';
  return '';
}

export class DraftTab {
  /** els: {text, generate, assemble, note}. source() is the editor's draft source, locked() whether a
     job owns it, roles() the endpoint roles; edit(text) writes the text into the draft source;
     ready() saves pending edits (throwing if they can't save); start(action) asks the server to run
     'draft' or 'assemble'. */
  constructor({els, source, locked, roles, edit, ready, start, report, confirm = globalThis.confirm}) {
    Object.assign(this, {els, source, locked, roles, edit, ready, start, report, confirm});
    this.busy = false;
    els.text.addEventListener('input', () => { this.edit(els.text.value); this.render(); });
    els.generate.addEventListener('click', () => this.run('draft').catch(report));
    els.assemble.addEventListener('click', () => this.run('assemble').catch(report));
  }
  render() {
    const {els} = this, source = this.source(), locked = !!this.locked(), text = draftText(source);
    if (globalThis.document?.activeElement !== els.text || !source) els.text.value = text;
    els.text.disabled = !source || locked;
    const blocked = blocker({source, locked, roles: this.roles()});
    els.generate.disabled = !!blocked || this.busy;
    els.assemble.disabled = !!blocked || this.busy || !text.trim();
    const role = this.roles()?.assembly || this.roles()?.draft;
    setText(els.note, blocked || (text.trim() ? `Edits save as you type. Assemble updates the existing graph in place (${role}).`
      : 'No draft yet. Write one here or click New draft.'));
    els.note.className = 'small grow ' + (locked ? 'tone-warning' : 'muted');
    setAttr(els.assemble, 'data-tip', text.trim() ? 'Run the Assembly pass on this draft. It edits the current graph rather than replacing it.' : 'Write or generate a draft first');
  }
  async run(action) {
    const source = this.source();
    if (!source || this.busy) return;
    if (action === 'draft' && draftText(source).trim() && !this.confirm('Replace this draft with a new one? Undo brings the old one back.')) return;
    this.busy = true; this.render();
    try { await this.ready(); await this.start(action); } finally { this.busy = false; this.render(); }
  }
}
