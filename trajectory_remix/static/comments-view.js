/* Reviewer comments on the selected trajectory, its events and its windows.
   Comments save straight away and sit outside the source: they never make the draft
   dirty, and they stay writable while a job holds the trajectory. */
import {node, setText, tag} from './ui-kit.js';

export const targetKey = (kind, id) => kind === 'trajectory' ? 'trajectory' : `${kind}:${id}`;
const parseKey = key => { const at = key.indexOf(':'); return at < 0 ? {kind: 'trajectory'} : {kind: key.slice(0, at), id: key.slice(at + 1)}; };
const KIND_NAMES = {trajectory: 'Trajectory', event: 'Event', window: 'Window'};
export const targetLabel = (kind, id) => kind === 'trajectory' ? 'Whole trajectory' : `${KIND_NAMES[kind]} ${id}`;

/** Open comments per target key, for badges on window cards and timeline events. */
export function openCounts(items) {
  const counts = new Map();
  for (const c of items) if (c.status === 'open') { const key = targetKey(c.target_kind, c.target_id); counts.set(key, (counts.get(key) || 0) + 1); }
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
  async sendReply(comment) {
    const body = (this.replies.get(comment.id) || '').trim();
    if (!body) throw new Error('Write the reply first.');
    await this.act(comment, 'POST', {body}, '/replies');
    this.replies.delete(comment.id); this.render();
  }
  render() {
    this.counts = openCounts(this.items);
    const open = this.items.filter(c => c.status === 'open'), resolved = this.items.filter(c => c.status !== 'open');
    setText(this.els.count, open.length ? String(open.length) : '');
    if (this.els.send) this.els.send.disabled = !open.length;
    const list = this.els.list, wasOpen = list.querySelector('details')?.open ?? false;
    const focused = document.activeElement?.closest?.('.comment-reply-form') && document.activeElement.dataset.comment;
    if (!this.trajectory) { list.replaceChildren(); return; }
    const parts = open.length ? open.map(c => this.card(c)) : [node('div', 'No open comments. Point out a problem above and an agent can pick it up.', 'empty')];
    if (resolved.length) {
      const more = node('details', undefined, 'comment-resolved'); more.open = wasOpen;
      more.append(node('summary', `Resolved (${resolved.length})`), ...resolved.map(c => this.card(c)));
      parts.push(more);
    }
    list.replaceChildren(...parts);
    if (focused) list.querySelector(`textarea[data-comment="${CSS.escape(focused)}"]`)?.focus();
  }
  card(c) {
    const card = node('article', undefined, `comment-card${c.status === 'open' ? '' : ' resolved'}`);
    const head = node('header', undefined, 'comment-head');
    const jump = node('button', targetLabel(c.target_kind, c.target_id), 'comment-target ghost sm');
    jump.type = 'button'; jump.disabled = !c.target_exists;
    jump.dataset.tip = c.target_exists ? 'Show this in the editor' : `This ${c.target_kind} is no longer in the trajectory`;
    jump.onclick = () => this.inspect(c.target_kind, c.target_id);
    head.append(jump);
    if (!c.target_exists) head.append(tag('Removed', 'warning'));
    head.append(tag(c.author === 'agent' ? 'Agent' : 'You', c.author === 'agent' ? 'accent' : 'muted'));
    const when = node('span', `rev ${c.source_revision ?? '?'} · ${String(c.created).slice(0, 10)}`, 'small muted grow');
    when.dataset.tip = `Written against source revision ${c.source_revision ?? 'unknown'} at ${c.created}`;
    head.append(when);
    card.append(head, node('p', c.body, 'comment-body'));
    if (c.resolution) card.append(node('p', c.resolution, 'comment-resolution'));
    if (c.replies?.length) card.append(this.thread(c));
    const replying = this.replies.has(c.id);
    if (replying) card.append(this.replyForm(c));
    const actions = node('div', undefined, 'comment-actions');
    if (!replying) {
      const reply = node('button', 'Reply', 'sm ghost');
      reply.onclick = () => { this.replies.set(c.id, ''); this.render(); this.els.list.querySelector(`textarea[data-comment="${CSS.escape(c.id)}"]`)?.focus(); };
      actions.append(reply);
    }
    const toggle = node('button', c.status === 'open' ? 'Resolve' : 'Reopen', 'sm ghost');
    toggle.onclick = () => this.act(c, 'PATCH', {status: c.status === 'open' ? 'resolved' : 'open'}).catch(this.report);
    const remove = node('button', 'Delete', 'sm ghost danger');
    remove.onclick = () => { if (globalThis.confirm?.('Delete this comment?') === false) return; this.act(c, 'DELETE').catch(this.report); };
    actions.append(toggle, remove); card.append(actions);
    return card;
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
    const form = node('div', undefined, 'comment-reply-form'), box = node('textarea');
    box.rows = 2; box.dataset.comment = c.id; box.value = this.replies.get(c.id) || '';
    box.setAttribute('aria-label', `Reply to comment on ${targetLabel(c.target_kind, c.target_id)}`); box.placeholder = 'Reply…';
    box.oninput = () => this.replies.set(c.id, box.value);
    box.onkeydown = e => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this.sendReply(c).catch(this.report); }
      if (e.key === 'Escape') { this.replies.delete(c.id); this.render(); }
    };
    const send = node('button', 'Send reply', 'sm'), cancel = node('button', 'Cancel', 'sm ghost');
    send.onclick = () => this.sendReply(c).catch(this.report);
    cancel.onclick = () => { this.replies.delete(c.id); this.render(); };
    const row = node('div', undefined, 'comment-actions'); row.append(cancel, send);
    form.append(box, row); return form;
  }
}
