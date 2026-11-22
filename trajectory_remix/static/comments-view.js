/* Reviewer comments on the selected trajectory, its events and its windows.
   Comments save straight away and sit outside the source: they never make the draft
   dirty, and they stay writable while a job holds the trajectory.
   Lifecycle: open → addressed (an agent says it's fixed) → resolved. Only the person
   resolves: Accept closes an addressed comment, Request changes reopens it with a reply. */
import {node, setText, tag} from './ui-kit.js';

export const targetKey = (kind, id) => kind === 'trajectory' ? 'trajectory' : `${kind}:${id}`;
const parseKey = key => { const at = key.indexOf(':'); return at < 0 ? {kind: 'trajectory'} : {kind: key.slice(0, at), id: key.slice(at + 1)}; };
const KIND_NAMES = {trajectory: 'Trajectory', event: 'Event', window: 'Window'};
export const targetLabel = (kind, id) => kind === 'trajectory' ? 'Whole trajectory' : `${KIND_NAMES[kind]} ${id}`;

const UNRESOLVED = new Set(['open', 'addressed']);

/** Unresolved (open or addressed) comments per target key, for badges on cards and events. */
export function unresolvedCounts(items) {
  const counts = new Map();
  for (const c of items) if (UNRESOLVED.has(c.status)) { const key = targetKey(c.target_kind, c.target_id); counts.set(key, (counts.get(key) || 0) + 1); }
  return counts;
}

export class CommentsView {
  /** els: {target, body, add, list, count, send?}; inspect(kind, id) jumps to a comment's target. */
  constructor({api, report, els, inspect = () => {}, onChange = () => {}}) {
    Object.assign(this, {api, report, els, inspect, onChange});
    this.items = []; this.counts = new Map(); this.trajectory = null; this.loaded = ''; this.replies = new Map();
    els.add.addEventListener('click', () => this.add().catch(report));
    els.body.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this.add().catch(report); } });
    els.target.addEventListener('change', () => { this.pinned = true; });
  }
  count(kind, id) { return this.counts.get(targetKey(kind, id)) || 0; }
  /** Offer every event and window as a target; follow the selected event until the user picks one. */
  setContext(draft, event) {
    const entries = [['trajectory', targetLabel('trajectory')],
      ...Object.keys(draft?.events || {}).map(id => [targetKey('event', id), targetLabel('event', id)]),
      ...Object.keys(draft?.windows || {}).map(id => [targetKey('window', id), targetLabel('window', id)])];
    const select = this.els.target, keys = entries.map(([key]) => key);
    if (JSON.stringify(keys) !== JSON.stringify([...select.options].map(o => o.value))) {
      select.replaceChildren(...entries.map(([value, label]) => { const o = node('option', label); o.value = value; return o; }));
    }
    const follow = event && draft?.events[event] ? targetKey('event', event) : 'trajectory';
    if (event !== this.event && !this.els.body.value.trim()) this.pinned = false;
    this.event = event;
    if (!this.pinned || !keys.includes(select.value)) select.value = follow;
  }
  /** Point the composer at a target (from a window card) and focus it. */
  compose(kind, id) { this.els.target.value = targetKey(kind, id); this.pinned = true; this.els.body.focus(); }
  async load(trajectory) {
    if (trajectory !== this.trajectory) { this.trajectory = trajectory; this.items = []; this.loaded = ''; this.replies.clear(); this.render(); }
    if (!trajectory) return;
    const data = await this.api(`/trajectories/${trajectory}/comments`);
    if (trajectory !== this.trajectory) return;
    const key = JSON.stringify(data.items);
    if (key === this.loaded) return; // Polling: no DOM work when nothing changed.
    this.loaded = key; this.items = data.items; this.render(); this.onChange();
  }
  async add() {
    const body = this.els.body.value.trim();
    if (!this.trajectory) throw new Error('Select a trajectory first.');
    if (!body) throw new Error('Write the comment first.');
    await this.api(`/trajectories/${this.trajectory}/comments`, 'POST', {target: parseKey(this.els.target.value), body});
    this.els.body.value = ''; this.pinned = false;
    await this.load(this.trajectory);
  }
  async act(comment, method, body, path = '') {
    await this.api(`/comments/${comment.id}${path}`, method, body);
    await this.load(this.trajectory);
  }
  /** Open a reply box; reopen makes it "Request changes". */
  startReply(comment, reopen = false) {
    this.replies.set(comment.id, {text: this.replies.get(comment.id)?.text || '', reopen});
    this.render(); this.els.list.querySelector(`textarea[data-comment="${CSS.escape(comment.id)}"]`)?.focus();
  }
  async sendReply(comment) {
    const draft = this.replies.get(comment.id) || {text: ''}, body = draft.text.trim();
    if (!body) throw new Error(draft.reopen ? 'Say what still needs to change.' : 'Write the reply first.');
    await this.act(comment, 'POST', {body, reopen: !!draft.reopen}, '/replies');
    this.replies.delete(comment.id); this.render();
  }
  render() {
    this.counts = unresolvedCounts(this.items);
    const by = status => this.items.filter(c => c.status === status);
    const open = by('open'), addressed = by('addressed'), resolved = by('resolved');
    const unresolved = open.length + addressed.length;
    setText(this.els.count, unresolved ? String(unresolved) : '');
    if (this.els.send) this.els.send.disabled = !open.length;
    const list = this.els.list, wasOpen = list.querySelector('details')?.open ?? false;
    const focused = document.activeElement?.closest?.('.comment-reply-form') && document.activeElement.dataset.comment;
    if (!this.trajectory) { list.replaceChildren(); return; }
    const parts = [];
    if (addressed.length) parts.push(node('h3', `Awaiting your review (${addressed.length})`, 'comment-section'), ...addressed.map(c => this.card(c)));
    if (open.length) parts.push(...(addressed.length ? [node('h3', `Open (${open.length})`, 'comment-section')] : []), ...open.map(c => this.card(c)));
    if (!unresolved) parts.push(node('div', 'No open comments. Point out a problem above and an agent can pick it up.', 'empty'));
    if (resolved.length) {
      const more = node('details', undefined, 'comment-resolved'); more.open = wasOpen;
      more.append(node('summary', `Resolved (${resolved.length})`), ...resolved.map(c => this.card(c)));
      parts.push(more);
    }
    list.replaceChildren(...parts);
    if (focused) list.querySelector(`textarea[data-comment="${CSS.escape(focused)}"]`)?.focus();
  }
  card(c) {
    const card = node('article', undefined, `comment-card ${c.status}`);
    const head = node('header', undefined, 'comment-head');
    const jump = node('button', targetLabel(c.target_kind, c.target_id), 'comment-target ghost sm');
    jump.type = 'button'; jump.disabled = !c.target_exists;
    jump.dataset.tip = c.target_exists ? 'Show this in the editor' : `This ${c.target_kind} is no longer in the trajectory`;
    jump.onclick = () => this.inspect(c.target_kind, c.target_id);
    head.append(jump);
    if (!c.target_exists) head.append(tag('Removed', 'warning'));
    head.append(tag(c.author === 'agent' ? 'Agent' : 'You', c.author === 'agent' ? 'accent' : 'muted'));
    if (c.status === 'addressed') { const t = tag('Awaiting review', 'warning'); t.dataset.tip = 'An agent says this is fixed. Accept it, or request changes.'; head.append(t); }
    const when = node('span', `rev ${c.source_revision ?? '?'} · ${String(c.created).slice(0, 10)}`, 'small muted grow');
    when.dataset.tip = `Written against source revision ${c.source_revision ?? 'unknown'} at ${c.created}`;
    head.append(when);
    card.append(head, node('p', c.body, 'comment-body'));
    if (c.replies?.length) card.append(this.thread(c));
    const replying = this.replies.has(c.id);
    if (replying) card.append(this.replyForm(c));
    card.append(this.actions(c, replying));
    return card;
  }
  actions(c, replying) {
    const row = node('div', undefined, 'comment-actions');
    const act = (text, cls, run) => { const b = node('button', text, `sm ${cls}`); b.onclick = () => Promise.resolve(run()).catch(this.report); row.append(b); return b; };
    if (!replying) act('Reply', 'ghost', () => this.startReply(c));
    if (c.status === 'addressed') {
      if (!replying) act('Request changes', 'ghost', () => this.startReply(c, true)).dataset.tip = 'Reopen it with a reply saying what is still wrong';
      act('Accept', 'primary', () => this.act(c, 'PATCH', {status: 'resolved'})).dataset.tip = 'The fix is right: resolve the comment';
    } else if (c.status === 'open') act('Resolve', 'ghost', () => this.act(c, 'PATCH', {status: 'resolved'}));
    else act('Reopen', 'ghost', () => this.act(c, 'PATCH', {status: 'open'}));
    act('Delete', 'ghost danger', () => { if (globalThis.confirm?.('Delete this comment?') === false) return; return this.act(c, 'DELETE'); });
    return row;
  }
  thread(c) {
    const list = node('ol', undefined, 'comment-thread');
    for (const r of c.replies) {
      const item = node('li', undefined, 'comment-reply'), head = node('div', undefined, 'comment-head');
      head.append(tag(r.author === 'agent' ? 'Agent' : 'You', r.author === 'agent' ? 'accent' : 'muted'), node('span', String(r.created).slice(0, 10), 'small muted grow'));
      const remove = node('button', '×', 'ghost sm comment-reply-delete'); remove.setAttribute('aria-label', 'Delete reply');
      remove.onclick = () => this.act(c, 'DELETE', undefined, `/replies/${r.id}`).catch(this.report);
      head.append(remove); item.append(head, node('p', r.body, 'comment-body')); list.append(item);
    }
    return list;
  }
  replyForm(c) {
    const draft = this.replies.get(c.id), form = node('div', undefined, 'comment-reply-form'), box = node('textarea');
    box.rows = 2; box.dataset.comment = c.id; box.value = draft.text;
    const what = draft.reopen ? 'Request changes on' : 'Reply to';
    box.setAttribute('aria-label', `${what} comment on ${targetLabel(c.target_kind, c.target_id)}`);
    box.placeholder = draft.reopen ? 'What is still wrong? The comment reopens for the agent.' : 'Reply…';
    box.oninput = () => { draft.text = box.value; };
    box.onkeydown = e => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this.sendReply(c).catch(this.report); }
      if (e.key === 'Escape') { this.replies.delete(c.id); this.render(); }
    };
    const send = node('button', draft.reopen ? 'Send and reopen' : 'Send reply', 'sm'), cancel = node('button', 'Cancel', 'sm ghost');
    send.onclick = () => this.sendReply(c).catch(this.report);
    cancel.onclick = () => { this.replies.delete(c.id); this.render(); };
    const row = node('div', undefined, 'comment-actions'); row.append(cancel, send);
    form.append(box, row); return form;
  }
}
