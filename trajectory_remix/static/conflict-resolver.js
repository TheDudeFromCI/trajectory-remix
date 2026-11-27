/* Resolve a save that clashed with a newer one. Each conflict is a value both saves changed;
   the dialog shows what blocks the merge as a diff (− the saved version, + yours) and asks which
   to keep. Everything else from both saves is merged as usual. */
import {node, setText} from './ui-kit.js';
import {lineDiff, textLines, withContext} from './line-diff.js';

const MARKS = {same: ' ', del: '-', add: '+', skip: ' '};
const KINDS = {events: 'Event', windows: 'Window', metadata: 'Metadata', defaults: 'Defaults'};

/** "Event first › content" for ["events", "first", "content"]. */
export function conflictLabel(path) {
  if (!path.length) return 'Whole trajectory';
  const [head, ...rest] = path, kind = KINDS[head];
  if (!kind) return path.join(' › ');
  if (head === 'metadata' || head === 'defaults') return [kind, ...rest].join(' › ');
  return [`${kind} ${rest[0] ?? ''}`.trim(), ...rest.slice(1)].join(' › ');
}

/** A side's value as lines, or a note when that save removed it. */
const sideLines = (conflict, side) => side in conflict ? textLines(conflict[side]) : ['(removed)'];

/** Diff from the saved version (theirs) to yours, as marked lines. */
export function conflictDiff(conflict) {
  const pre = node('pre', undefined, 'proposal-diff conflict-diff');
  for (const line of withContext(lineDiff(sideLines(conflict, 'theirs'), sideLines(conflict, 'ours')))) {
    pre.append(node('span', `${MARKS[line.op]} ${line.text}\n`, `diff-${line.op}`));
  }
  return pre;
}

export class ConflictResolver {
  /** dialog: the <dialog> holding #conflict-list, #conflict-summary and the footer buttons. */
  constructor(dialog) {
    this.dialog = dialog; this.list = dialog.querySelector('#conflict-list');
    this.summary = dialog.querySelector('#conflict-summary'); this.choices = [];
    dialog.querySelector('#conflict-all-mine').onclick = () => this.setAll('ours');
    dialog.querySelector('#conflict-all-theirs').onclick = () => this.setAll('theirs');
    dialog.querySelector('#conflict-save').onclick = () => this.finish(this.resolutions());
    dialog.addEventListener('close', () => this.finish(null));
  }
  /** Show `conflicts`; resolves to [{path, keep}] or null when cancelled. */
  open(conflicts) {
    this.finish(null);
    this.choices = conflicts.map(() => 'ours');
    this.conflicts = conflicts;
    setText(this.summary, `${conflicts.length} value${conflicts.length === 1 ? '' : 's'} changed in both your edit and a newer save. Choose which to keep; everything else from both is kept.`);
    this.list.replaceChildren(...conflicts.map((c, index) => this.card(c, index)));
    if (!this.dialog.open) this.dialog.showModal();
    return new Promise(resolve => { this.resolve = resolve; });
  }
  card(conflict, index) {
    const card = node('section', undefined, 'conflict-card'), head = node('div', undefined, 'conflict-head');
    const choices = node('div', undefined, 'conflict-choices'); choices.setAttribute('role', 'radiogroup');
    choices.setAttribute('aria-label', `Keep for ${conflictLabel(conflict.path)}`);
    for (const [keep, text] of [['ours', 'Keep mine'], ['theirs', 'Keep theirs']]) {
      const label = node('label', undefined, 'check conflict-choice'), input = node('input');
      input.type = 'radio'; input.name = `conflict-${index}`; input.value = keep; input.checked = this.choices[index] === keep;
      input.onchange = () => { this.choices[index] = keep; };
      label.append(input, node('span', text)); choices.append(label);
    }
    head.append(node('h3', conflictLabel(conflict.path), 'conflict-path'), choices);
    card.append(head, conflictDiff(conflict));
    return card;
  }
  setAll(keep) {
    this.choices = this.choices.map(() => keep);
    for (const input of this.list.querySelectorAll('input[type=radio]')) input.checked = input.value === keep;
  }
  resolutions() { return this.conflicts.map((c, index) => ({path: c.path, keep: this.choices[index]})); }
  finish(value) {
    const resolve = this.resolve; this.resolve = null;
    if (value && this.dialog.open) this.dialog.close();
    resolve?.(value);
  }
}
