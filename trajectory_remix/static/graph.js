/* Graph studio transport and controller. Project operations use REST exclusively. */
const $ = id => document.getElementById(id);
const copy = value => structuredClone(value);
const json = value => JSON.stringify(value, null, 2);
const stable = value => Array.isArray(value) ? value.map(stable) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(k => [k, stable(value[k])])) : value;
const message = error => { $('status').textContent = error instanceof Error ? error.message : String(error); };
const on = (id, event, handler) => $(id).addEventListener(event, async e => {
  try { await handler(e); } catch (error) { message(error); }
});
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const options = (id, entries, selected) => { const el = $(id); el.replaceChildren(...entries.map(([value, label]) => { const n = node('option', label); n.value = value; return n; })); el.value = selected ?? entries[0]?.[0] ?? ''; };
const button = (text, action, cls) => { const n = node('button', text, cls); n.onclick = async e => { try { await action(e); } catch (error) { message(error); } }; return n; };

export async function api(path, method = 'GET', body) {
  const response = await fetch(path.startsWith('/api/') ? path : '/api/v1' + path, {
    method, headers: {'X-Remix-Token': document.querySelector('meta[name=remix-token]').content, 'Content-Type': 'application/json'},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || data.detail && JSON.stringify(data.detail) || data.errors && JSON.stringify(data.errors) || data.code || response.statusText);
  return data;
}

// Trailing timer plus generation fencing covers edits, mode/profile and selection changes.
export class PreviewController {
  constructor(request, publish, report = message, timers = globalThis) {
    this.request = request; this.publish = publish; this.report = report; this.timers = timers; this.generation = 0; this.cache = new Map();
  }
  invalidate() { ++this.generation; this.timers.clearTimeout(this.timer); }
  schedule(key, payload, delay) {
    this.invalidate(); const generation = this.generation;
    this.timer = this.timers.setTimeout(async () => {
      try {
        const value = this.cache.get(key) || await this.request(payload);
        if (generation !== this.generation) return;
        if (value.client_key !== payload.sourceHash || value.profile !== payload.profile || (payload.settingsRevision !== undefined && value.settings_revision !== payload.settingsRevision)) return;
        this.cache.set(key, value); while (this.cache.size > 4) this.cache.delete(this.cache.keys().next().value);
        this.publish(value);
      } catch (error) { if (generation === this.generation) this.report(error); }
    }, delay);
  }
}

export function graphOrder(source) {
  const pending = new Set(Object.keys(source.events)), order = [];
  while (pending.size) {
    let ready = [...pending].filter(id => !source.events[id].after.some(dep => pending.has(dep))).sort();
    if (!ready.length) ready = [...pending].sort();
    order.push(...ready); ready.forEach(id => pending.delete(id));
  }
  return order;
}

// Match canonical intrinsic hashes, including typed pieces/default-resolved DTO fields.
async function intrinsicHash(source) {
  const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])])) : value;
  const intrinsic = copy(source); delete intrinsic.metadata;
  const data = new TextEncoder().encode(JSON.stringify(canonical(intrinsic)));
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', data))].map(v => v.toString(16).padStart(2, '0')).join('');
}

export class StudioUI {
  constructor() {
    let saved = {}; try { saved = JSON.parse(localStorage.getItem('graph-preferences') || '{}'); } catch {}
    this.prefs = {mode: 'graph', profile: '', debounce: 500, ...saved};
    if (!['graph', 'ticks'].includes(this.prefs.mode)) this.prefs.mode = 'graph';
    if (!Number.isFinite(this.prefs.debounce) || this.prefs.debounce < 0) this.prefs.debounce = 500;
    this.offset = 0; this.position = 0; this.drafts = new Map(); this.jobs = new Map(); this.dialogs = new Map(); this.recorded = new Set(); this.ownedBulk = new Set();
    this.undo = []; this.redo = []; this.selectionGeneration = 0;
    this.preview = new PreviewController(async p => api(`/trajectories/${p.id}/preview`, 'POST', {
      expected_revision: p.revision, profile: p.profile, source: p.source, client_key: p.sourceHash
    }), value => { this.frames = value.frames; this.previewHash = value.source_hash; $('preview-status').textContent = value.truncated ? 'Showing first 1000 frames; preview truncated' : 'Tick history'; this.renderTimeline(); });
  }
  query() {
    const q = {}; if ($('sample-search').value) q.text = $('sample-search').value;
    if ($('search-stage').value !== '') q.stage = Number($('search-stage').value);
    if ($('search-split').value) q.split = $('search-split').value;
    if ($('search-tool').value) q.tool = $('search-tool').value;
    return q;
  }
  async loadSettings() {
    this.settingsRecord = await api('/settings'); this.settings = this.settingsRecord.settings;
    const profiles = [['', 'Select profile'], ...Object.entries(this.settings.tokenizer_profiles).map(([id, p]) => [id, p.name])];
    const exportSelection = $('export-profile').value;
    options('preview-profile', profiles, this.prefs.profile); options('export-profile', profiles, exportSelection);
    options('tool-id', Object.entries(this.settings.tools).map(([id, t]) => [id, t.name]), $('tool-id').value);
    $('effort-options').replaceChildren(...this.settings.reasoning_levels.map(x => { const n = node('option'); n.value = x; return n; }));
  }
  async listing() {
    const key = JSON.stringify([this.query(), this.offset]);
    this.listKey = key;
    const q = new URLSearchParams({...this.query(), limit: 50, offset: this.offset});
    const data = await api('/trajectories?' + q);
    if (key !== this.listKey) return;
    this.lastItems = data.items; $('sample-count').textContent = data.total; $('sample-page-label').textContent = `${Math.min(this.offset + 1, data.total)}–${this.offset + data.items.length} / ${data.total}`;
    $('sample-page-prev').disabled = !this.offset; $('sample-page-next').disabled = this.offset + 50 >= data.total;
    $('sample-list').replaceChildren(...data.items.map(item => button(`${item.id.slice(0, 8)} · ${item.stage ?? 'Unfinished'} · ${item.split ?? 'Missing split'}${item.owner ? ' · Locked' : ''}`, () => this.select(item.id), 'sample-row' + (item.id === this.id ? ' selected' : ''))));
  }
  async select(id, force = false) {
    const generation = ++this.selectionGeneration;
    this.preview.invalidate(); this.frames = null; clearTimeout(this.validationTimer); this.validationGeneration = (this.validationGeneration || 0) + 1;
    if (this.id && this.draft) { this.stashEditor(); this.drafts.set(this.id, {record: this.record, draft: this.draft, event: this.event, editors: this.editors}); }
    if (force) this.drafts.delete(id);
    const cached = !force && this.drafts.get(id);
    const record = cached?.record || await api(`/trajectories/${id}`);
    if (generation !== this.selectionGeneration) return;
    this.id = id; this.record = record; this.diagnostics = record.diagnostics; this.draft = cached?.draft || copy(record.source); this.editors = cached?.editors || new Map();
    this.event = cached?.event || Object.keys(this.draft.events)[0]; this.position = 0;
    this.renderDraft(); this.renderEvent(); this.listing(); this.schedulePreview();
  }
  isDirty() { return this.record && JSON.stringify(this.draft) !== JSON.stringify(this.record.source); }
  stashEditor() {
    if (!this.event || !this.editors) return;
    const ids = ['event-content', 'content-mode', 'event-kind', 'event-window', 'call-id', 'tool-id', 'chat-speaker', 'delivery', 'weight'];
    const flags = ['chain', 'replace-body', 'close-window', 'masked', 'begin', 'end', 'echo-chat'];
    this.editors.set(this.event, Object.fromEntries([...ids.map(id => [id, $(id).value]), ...flags.map(id => [id, $(id).checked])]));
  }
  changed() {
    this.frames = null; this.preview.invalidate();
    $('source-json').value = json(this.draft); $('dirty-indicator').textContent = this.isDirty() ? 'Unsaved draft' : `Revision ${this.record.revision}`;
    this.renderTimeline(); this.schedulePreview(); this.scheduleDiagnostics();
  }
  renderDraft() {
    if (!this.draft) return;
    options('event-picker', Object.keys(this.draft.events).map(id => [id, id]), this.event);
    options('event-window', Object.keys(this.draft.windows).map(id => [id, id]), $('event-window').value || this.draft.events[this.event]?.window);
    $('metadata-json').value = json(this.draft.metadata || {});
    $('stage').value = this.draft.metadata?.stage ?? -3; $('split').value = this.draft.metadata?.split ?? '';
    $('review-stale').textContent = this.record.review_status?.stale ? 'Review stale: ' + this.record.review_status.reasons.join(', ') + '. Re-review manually.' : '';
    $('split-warning').textContent = this.draft.metadata?.split ? '' : 'Missing split: export assigns automatically and warns.';
    $('source-json').value = json(this.draft);
    this.renderDiagnostics();
    $('dirty-indicator').textContent = this.record.owner ? `Locked by job ${this.record.owner}` : this.isDirty() ? 'Unsaved draft' : `Revision ${this.record.revision}`;
    this.renderTimeline();
  }
  renderDiagnostics() {
    $('diagnostics').replaceChildren(...(this.diagnostics || []).map(d => {
      const n = node('div', `${d.severity}: ${d.code} — ${d.message}`, 'diagnostic');
      if (d.event_id) n.append(button('Inspect event', () => this.selectEvent(d.event_id)));
      return n;
    }));
  }
  scheduleDiagnostics() {
    clearTimeout(this.validationTimer); const generation = this.validationGeneration = (this.validationGeneration || 0) + 1;
    const draft = copy(this.draft);
    this.validationTimer = setTimeout(async () => {
      try {
        const value = await api('/diagnostics', 'POST', draft);
        if (generation !== this.validationGeneration || draft.id !== this.id) return;
        this.diagnostics = value.diagnostics; this.renderDiagnostics();
      } catch (error) { if (generation === this.validationGeneration) message(error); }
    }, 200);
  }
  selectEvent(id) { this.stashEditor(); this.event = id; this.renderEvent(); this.renderTimeline(); }
  renderEvent() {
    const e = this.draft?.events[this.event];
    $('event-picker').value = this.event || '';
    $('dependency-options').replaceChildren(...Object.keys(this.draft?.events || {}).filter(id => id !== this.event).map(id => { const n = node('option'); n.value = id; return n; }));
    if (!e) { $('event-content').value = ''; $('dependencies').replaceChildren(); return; }
    $('event-kind').value = e.kind; $('event-window').value = e.window; $('call-id').value = e.call_id || '';
    $('tool-id').value = e.tool || ''; $('chat-speaker').value = e.chat?.speaker || 'User'; $('delivery').value = e.delivery?.mode || 'instant';
    $('chain').checked = e.delivery?.chain_immediately ?? true; $('replace-body').checked = e.replace_body ?? false;
    $('close-window').checked = e.close_window; $('masked').checked = e.supervision.masked; $('weight').value = e.supervision.weight;
    $('begin').checked = e.insert_tool_begin ?? e.insert_response_begin ?? true; $('end').checked = e.insert_tool_end ?? e.insert_response_end ?? true;
    $('echo-chat').checked = e.echo_chat ?? true;
    $('content-mode').value = typeof e.content === 'string' ? 'text' : 'json'; $('event-content').value = typeof e.content === 'string' ? e.content : json(e.content);
    const stashed = this.editors.get(this.event);
    if (stashed) for (const [id, value] of Object.entries(stashed)) { if (typeof value === 'boolean') $(id).checked = value; else $(id).value = value; }
    this.kindControls(); this.renderArguments(); this.renderDependencies();
  }
  kindControls() {
    const kind = $('event-kind').value, tool = kind === 'tool_call', response = kind === 'tool_response', chat = kind === 'chat_message';
    const external = response || kind === 'external_text';
    for (const [id, show] of Object.entries({'call-id': tool || response || chat, 'tool-id': tool, 'chat-speaker': chat, delivery: external, chain: external || chat, 'replace-body': external, begin: tool || response, end: tool || response, 'echo-chat': tool})) $(id).closest('label').hidden = !show;
  }
  renderArguments() {
    $('argument-form').replaceChildren();
    if ($('content-mode').value !== 'form') return;
    let args; try { args = JSON.parse($('event-content').value); } catch { message('Partial arguments retained. Complete JSON to use Form mode.'); return; }
    if (!args || typeof args !== 'object' || Array.isArray(args)) { message('Form mode needs an argument object. Raw content retained.'); return; }
    const schema = this.settings.tools[$('tool-id').value]?.input_schema;
    const keys = new Set([...Object.keys(schema?.properties || {}), ...Object.keys(args)]);
    for (const key of keys) {
      const label = node('label', key), input = node('input'); input.value = key in args ? JSON.stringify(args[key]) : ''; input.setAttribute('aria-label', 'Argument ' + key);
      input.onchange = () => { try { if (input.value === '') delete args[key]; else args[key] = JSON.parse(input.value); $('event-content').value = json(args); this.stashEditor(); this.applyEvent(); } catch { message('Argument values use JSON; original raw content retained.'); } };
      label.append(input); $('argument-form').append(label);
    }
  }
  composeEvent() {
    const kind = $('event-kind').value, external = ['external_text', 'tool_response', 'chat_message'].includes(kind);
    let content = $('event-content').value;
    if ($('content-mode').value === 'json' && !['tool_call', 'tool_response'].includes(kind)) content = JSON.parse(content);
    const e = {kind, window: $('event-window').value, after: this.draft.events[this.event]?.after || [], content,
      close_window: $('close-window').checked, supervision: {masked: $('masked').checked, weight: Number($('weight').value)}};
    if (external) e.delivery = {mode: kind === 'chat_message' ? 'instant' : $('delivery').value, chain_immediately: $('chain').checked};
    if (kind === 'external_text' || kind === 'tool_response') e.replace_body = $('replace-body').checked;
    if (kind === 'tool_call' || kind === 'tool_response') e.call_id = $('call-id').value;
    if (kind === 'tool_call') Object.assign(e, {tool: $('tool-id').value, insert_tool_begin: $('begin').checked, insert_tool_end: $('end').checked, echo_chat: $('echo-chat').checked});
    if (kind === 'tool_response') Object.assign(e, {insert_response_begin: $('begin').checked, insert_response_end: $('end').checked});
    if (kind === 'chat_message') { e.chat = {speaker: $('chat-speaker').value}; if ($('call-id').value) e.call_id = $('call-id').value; }
    return e;
  }
  applyEvent() { if (!this.event || !this.draft.events[this.event]) return; this.draft.events[this.event] = this.composeEvent(); this.editors.delete(this.event); this.changed(); }
  renderDependencies() {
    $('dependencies').replaceChildren(...(this.draft.events[this.event]?.after || []).map(dep => button(`${this.draft.events[dep] ? '' : 'Broken: '}${dep} ×`, () => {
      this.draft.events[this.event].after = this.draft.events[this.event].after.filter(x => x !== dep); this.renderDependencies(); this.changed();
    })));
  }
  addDependency(dep) {
    if (!this.draft.events[this.event] || dep === this.event || !dep) return;
    const after = this.draft.events[this.event].after; if (!after.includes(dep)) after.push(dep);
    this.renderDependencies(); this.changed();
  }
  async schedulePreview() {
    const generation = this.preview.generation;
    if (this.prefs.mode !== 'ticks' || !this.draft) return;
    if (!this.prefs.profile) { $('preview-status').textContent = 'Select a tokenizer in Preferences'; return; }
    const draft = copy(this.draft), id = this.id, revision = this.record.revision, profile = this.prefs.profile;
    const sourceHash = await intrinsicHash(draft);
    if (generation !== this.preview.generation || id !== this.id || this.prefs.mode !== 'ticks') return;
    $('preview-status').textContent = 'Waiting for Tick history…';
    const key = JSON.stringify([sourceHash, profile, this.settingsRecord.revision]);
    this.preview.schedule(key, {id, revision, profile, source: draft, sourceHash, settingsRevision: this.settingsRecord.revision}, this.prefs.debounce);
  }
  renderTimeline() {
    if (!this.draft) return;
    const order = graphOrder(this.draft), ticks = this.prefs.mode === 'ticks';
    const frames = this.frames || [];
    const max = Math.max(0, (ticks ? frames.length : order.length) - 1);
    this.position = Math.min(max, Math.max(0, this.position));
    $('scrubber').max = max; $('scrubber').value = this.position; $('tick-number').value = this.position; $('max-tick').textContent = '/ ' + max;
    const visible = ticks ? new Set(frames.slice(0, this.position + 1).flatMap(f => [...f.inputs, ...f.outputs].map(e => e.event))) : new Set(order.slice(0, this.position + 1));
    const lanes = [...new Set(order.map(id => this.draft.events[id].window))], depth = {}, positions = new Map();
    const spans = new Map();
    if (ticks) frames.forEach((f, index) => [...f.inputs, ...f.outputs].forEach(e => {
      if (!e.event) return; const span = spans.get(e.event) || [index, index]; span[1] = index; spans.set(e.event, span);
    }));
    for (const id of order) {
      const event = this.draft.events[id]; depth[id] = Math.max(0, ...event.after.map(dep => (depth[dep] ?? -1) + 1));
      const span = spans.get(id);
      positions.set(id, {x: ticks ? 12 + (span?.[0] || 0) * 7 : 12 + depth[id] * 185,
        y: 10 + lanes.indexOf(event.window) * 48, width: ticks ? Math.max(28, ((span?.[1] || 0) - (span?.[0] || 0) + 1) * 7) : 165});
    }
    const chart = node('div', undefined, 'timeline-lanes');
    const width = Math.max(450, ...[...positions.values()].map(p => p.x + p.width + 20));
    const height = Math.max(70, lanes.length * 48 + 20); chart.style.width = width + 'px'; chart.style.height = height + 'px';
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('width', width); svg.setAttribute('height', height); svg.classList.add('dependency-edges');
    for (const id of order) {
      const target = positions.get(id);
      for (const dep of this.draft.events[id].after) {
        const origin = positions.get(dep); if (!origin) continue;
        const edge = document.createElementNS(svg.namespaceURI, 'path');
        const start = origin.x + origin.width, end = target.x, middle = (start + end) / 2;
        edge.setAttribute('d', `M${start},${origin.y + 16} C${middle},${origin.y + 16} ${middle},${target.y + 16} ${end},${target.y + 16}`);
        edge.setAttribute('fill', 'none'); edge.setAttribute('stroke', '#779dc7'); svg.append(edge);
      }
    }
    chart.append(svg);
    for (const id of order) {
      const e = this.draft.events[id], p = positions.get(id);
      const n = button(`${id} → ${e.window}`, click => click.shiftKey ? this.addDependency(id) : this.selectEvent(id), 'event-node' + (id === this.event ? ' selected' : ''));
      n.title = `Depends on: ${e.after.join(', ') || 'root'}`; n.style.opacity = visible.has(id) ? '1' : '.5';
      Object.assign(n.style, {left: p.x + 'px', top: p.y + 'px', width: p.width + 'px'}); chart.append(n);
    }
    if (ticks) { const line = node('div', undefined, 'timeline-cursor'); line.style.left = (12 + this.position * 7) + 'px'; chart.append(line); }
    $('timeline').replaceChildren(chart);
    const windows = new Map();
    const ensure = id => { if (!windows.has(id)) windows.set(id, {tokens: [], closed: false}); return windows.get(id); };
    if (ticks) {
      for (const f of frames.slice(0, this.position + 1)) for (const emission of [...f.inputs, ...f.outputs]) {
        if (emission.window === '@available') continue;
        const w = ensure(emission.window); if (emission.replace) w.tokens = w.tokens.filter(t => t.header);
        for (const t of emission.tokens) { w.tokens.push(t); if (t.control === 'eos') w.closed = true; }
      }
    } else for (const id of order.filter(id => visible.has(id))) {
      const e = this.draft.events[id], w = ensure(e.window);
      if (e.replace_body) w.tokens = [];
      if (!w.tokens.length) w.tokens.push({control: 'ctx_begin'}, {text: this.draft.windows[e.window]?.purpose || e.window}, {control: 'ctx_end'});
      const tool = e.kind === 'tool_call', response = e.kind === 'tool_response', chat = e.kind === 'chat_message';
      if (tool && e.insert_tool_begin || response && e.insert_response_begin || chat) w.tokens.push({control: chat ? 'msg_begin' : tool ? 'tool_begin' : 'response_begin'});
      if (chat) w.tokens.push({text: e.chat.speaker + ': '});
      w.tokens.push({text: typeof e.content === 'string' ? e.content : e.content.map(p => p.kind === 'text' ? p.text : `[${p.window}]`).join('')});
      if (tool && e.insert_tool_end || response && e.insert_response_end || chat) w.tokens.push({control: chat ? 'msg_end' : tool ? 'tool_end' : 'response_end'});
      if (e.close_window) { w.tokens.push({control: 'eos'}); w.closed = true; }
    }
    $('window-grid').replaceChildren(...[...windows].map(([id, w]) => {
      const card = node('article', undefined, 'window-card'); card.append(node('h3', id + (w.closed ? ' · Closed' : '')));
      for (const t of w.tokens) card.append(t.control ? node('span', t.control.replaceAll('_', ' ').toUpperCase(), 'token-block') : node('span', t.reference ? `[${t.reference[0]}]` : t.text || ''));
      return card;
    }));
    if (!ticks) $('preview-status').textContent = 'Dependency graph · no tokenization';
  }
  history(deltas) { if (!deltas.length) return; this.undo.push(deltas); if (this.undo.length > 50) this.undo.shift(); this.redo = []; this.historyButtons(); }
  historyButtons() { $('undo-edit').disabled = !this.undo.length; $('redo-edit').disabled = !this.redo.length; }
  async restore(undo) {
    if (this.isDirty()) throw new Error('Save or reload your draft before Undo/Redo.');
    const stack = undo ? this.undo : this.redo, inverse = undo ? this.redo : this.undo;
    if (!stack.length) return;
    const deltas = stack.at(-1), restored = [];
    for (let offset = 0; offset < deltas.length; offset += 1000) {
      const r = await api('/bulk/history', 'POST', {deltas: deltas.slice(offset, offset + 1000), undo}); restored.push(...r.deltas);
      if (r.warnings.length) message(json(r.warnings));
    }
    stack.pop();
    // Our own guarded restore creates new revisions. Rebase the next local action
    // only when its expected full source equals the successfully restored state.
    for (const changed of restored) {
      const target = undo ? changed.before : changed.after;
      for (let i = stack.length - 1; i >= 0; i--) {
        const prior = stack[i].find(d => d.id === changed.id);
        if (!prior) continue;
        if (JSON.stringify(stable(undo ? prior.after : prior.before)) === JSON.stringify(stable(target))) {
          prior.expected_revision = changed.expected_revision; prior.expected_commit = changed.expected_commit;
        }
        break;
      }
    }
    if (restored.length) inverse.push(restored); this.historyButtons(); this.drafts.clear();
    if (this.id) { try { await this.select(this.id, true); } catch { this.id = null; this.draft = null; } } await this.listing();
  }
  async save() {
    if (!this.draft) return;
    this.applyEvent(); const before = copy(this.record.source);
    const value = await api(`/trajectories/${this.id}`, 'PUT', {expected_revision: this.record.revision, source: this.draft});
    this.history([{id: this.id, before, after: value.source, expected_revision: value.revision, expected_commit: value.commit_id}]);
    this.record = value; this.diagnostics = value.diagnostics; this.draft = copy(value.source); this.editors.clear(); this.renderDraft(); this.renderEvent(); this.schedulePreview(); await this.listing();
  }
  async download(job, name) {
    const response = await fetch(`/api/v1/jobs/${job}/artifacts/${encodeURIComponent(name)}`, {headers: {'X-Remix-Token': document.querySelector('meta[name=remix-token]').content}});
    if (!response.ok) throw new Error('Artifact unavailable or expired');
    const url = URL.createObjectURL(await response.blob()), a = node('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async pollJobs() {
    const jobs = await api('/job-summaries');
    $('job-status-bar').replaceChildren(...jobs.map(job => button(`${job.kind} · ${job.state} · ${job.progress}/${job.total}`, () => this.inspectJob(job.id))));
    for (const job of jobs) {
      this.jobs.set(job.id, job);
      if (this.reviewJob === job.id) {
        const fullJob = await api(`/jobs/${job.id}`);
        const skipped = (fullJob.result_states || []).find(r => r.state === 'skipped')?.count || 0;
        $('review-progress').textContent = `${job.state} · ${job.progress}/${job.total} complete · ${skipped} skipped`;
        if (['completed', 'cancelled'].includes(job.state)) { $('review-dialog').close(); this.reviewJob = null; message($('review-progress').textContent); }
        else if (job.state === 'paused') message('Review paused. Open its job in the bottom status bar to Resume.');
      }
      if (this.ownedBulk.has(job.id) && job.kind === 'bulk' && ['completed', 'cancelled'].includes(job.state) && !this.recorded.has(job.id)) {
        const deltas = []; for (let offset = 0;; offset += 1000) { const part = await api(`/jobs/${job.id}/history?limit=1000&offset=${offset}`); deltas.push(...part); if (part.length < 1000) break; }
        this.recorded.add(job.id); this.history(deltas); await this.listing();
      }
      if (this.dialogs.get(job.id)?.dialog.open) await this.refreshJob(job.id);
    }
  }
  async inspectJob(id) {
    if (!this.dialogs.has(id)) {
      const dialog = node('dialog'), title = node('h2', 'Job ' + id), state = node('pre'), logs = node('pre', '', 'job-logs'), files = node('div'), controls = node('div');
      for (const action of ['pause', 'resume', 'cancel']) controls.append(button(action, async () => { await api(`/jobs/${id}/${action}`, 'POST'); await this.refreshJob(id); }));
      controls.append(button('Export retained logs', async () => { const r = await api(`/logs/${id}/export`, 'POST'); await this.inspectJob(r.job_id); }), button('Clear these logs', async () => { await api(`/logs?job=${id}`, 'DELETE'); logs.textContent = ''; this.dialogs.get(id).cursor = 0; await this.refreshJob(id); }));
      const older = button('Load next log page', () => this.refreshJob(id));
      dialog.append(title, state, controls, logs, older, files, button('Close', () => dialog.close())); document.body.append(dialog);
      this.dialogs.set(id, {dialog, state, logs, files, controls, cursor: 0});
    }
    const view = this.dialogs.get(id); if (!view.dialog.open) view.dialog.showModal(); await this.refreshJob(id);
  }
  async refreshJob(id) {
    const view = this.dialogs.get(id), j = await api(`/jobs/${id}`);
    view.state.textContent = `${j.kind} · ${j.state}\n${j.progress} / ${j.total}\nPaused endpoints: ${(j.paused_endpoints || []).join(', ') || 'none'}\n` + (j.attempt_states || []).map(r => `${r.state} ${r.outcome || ''}: ${r.count}`).join(' · ');
    const controls = view.controls.querySelectorAll('button');
    controls[0].disabled = j.state !== 'running'; controls[1].disabled = j.state !== 'paused'; controls[2].disabled = !['running', 'paused'].includes(j.state);
    const log = await api(`/jobs/${id}/logs?after=${view.cursor}&limit=100`);
    if (log.truncated) view.logs.textContent = '[Earlier retained logs were cleared or evicted]\n' + view.logs.textContent;
    for (const line of log.items) { view.logs.textContent += line.text + '\n'; view.cursor = line.sequence; }
    if (!log.items.length) view.cursor = Math.max(view.cursor, log.last);
    view.logs.textContent = view.logs.textContent.slice(-200000);
    view.files.replaceChildren();
    for (const endpoint of j.paused_endpoints || []) view.files.append(button('Resume endpoint ' + endpoint, () => api(`/endpoints/${endpoint}/resume`, 'POST')));
    if (j.kind === 'bulk') {
      const rows = await api(`/jobs/${id}/results?limit=100`); view.files.append(node('pre', json(rows.map(r => ({id: r.trajectory_id, state: r.state, warning: r.warning})))));
    }
    if (j.state === 'completed' && j.artifact) {
      try {
        const manifest = await api(`/jobs/${id}/manifest`);
        if (manifest.warnings?.length) view.files.append(node('pre', json(manifest.warnings)));
        const names = new Set(['download.zip', 'manifest.json', ...(manifest.files || []).map(f => typeof f === 'string' ? f : f.name), ...(manifest.assets || [])]);
        if (j.kind === 'log_export') names.add('logs.txt');
        for (const name of names) view.files.append(button('Download ' + name, () => this.download(id, name)));
      } catch { view.files.append(node('p', 'Artifacts expired or removed. Job record retained.')); }
    }
  }
  async init() {
    document.addEventListener('graph-job-started', e => this.inspectJob(e.detail));
    await this.loadSettings(); $('view-mode').value = this.prefs.mode; $('preview-debounce').value = this.prefs.debounce;
    on('new-sample', 'click', async () => { const r = await api('/trajectories', 'POST', {format: 'kidra-trajectory-graph/1', id: crypto.randomUUID(), windows: {reasoning: {purpose: 'reasoning', agent_writable: true}}, events: {}, metadata: {stage: -3}}); await this.select(r.source.id); });
    on('save-source', 'click', () => this.save()); on('reload-source', 'click', () => this.id && this.select(this.id, true));
    on('delete-sample', 'click', async () => { if (!this.id) return; const delta = await api(`/trajectories/${this.id}`, 'DELETE', {expected_revision: this.record.revision}); this.history([delta]); this.drafts.delete(this.id); this.id = null; this.draft = null; $('window-grid').replaceChildren(); $('timeline').replaceChildren(); await this.listing(); });
    on('event-picker', 'change', e => this.selectEvent(e.target.value));
    on('event-apply', 'click', () => this.applyEvent());
    on('new-event', 'click', () => {
      if (!this.draft) return; const id = $('new-event-id').value || 'event-' + crypto.randomUUID().slice(0, 8); if (this.draft.events[id]) throw new Error('Event ID exists');
      this.stashEditor(); this.draft.events[id] = {kind: 'agent_text', window: Object.keys(this.draft.windows)[0] || 'reasoning', after: [], content: '', close_window: false, supervision: {masked: false, weight: 1}};
      this.event = id; this.renderDraft(); this.renderEvent(); this.changed();
    });
    on('delete-event', 'click', () => { if (!this.event) return; delete this.draft.events[this.event]; this.editors.delete(this.event); this.event = Object.keys(this.draft.events)[0]; this.renderDraft(); this.renderEvent(); this.changed(); });
    on('event-kind', 'change', () => { $('masked').checked = ['external_text', 'tool_response', 'chat_message'].includes($('event-kind').value); this.kindControls(); });
    on('content-mode', 'change', () => { this.stashEditor(); this.renderArguments(); });
    on('event-content', 'input', () => { this.stashEditor(); this.applyEvent(); });
    for (const id of ['event-window', 'call-id', 'tool-id', 'chat-speaker', 'delivery', 'weight', 'chain', 'replace-body', 'close-window', 'masked', 'begin', 'end', 'echo-chat']) on(id, 'change', () => { this.stashEditor(); this.applyEvent(); });
    on('tool-id', 'change', () => this.renderArguments());
    on('dependency-add', 'click', () => this.addDependency($('dependency-picker').value));
    on('window-save', 'click', () => { const id = $('window-id').value; if (!id) throw new Error('Supply a window ID'); const w = {purpose: $('window-purpose').value, agent_writable: $('window-writable').checked}; if ($('window-effort').value) w.reasoning_effort = $('window-effort').value; this.draft.windows[id] = w; this.renderDraft(); this.renderEvent(); this.changed(); });
    on('window-delete', 'click', () => { delete this.draft.windows[$('window-id').value]; this.renderDraft(); this.renderEvent(); this.changed(); });
    on('metadata-apply', 'click', () => { const m = JSON.parse($('metadata-json').value); if (!m || Array.isArray(m) || typeof m !== 'object') throw new Error('Metadata must be an object'); this.draft.metadata = m; this.renderDraft(); this.changed(); });
    for (const id of ['stage', 'split']) on(id, 'change', () => { this.draft.metadata ||= {}; if (id === 'split' && !$(id).value) delete this.draft.metadata.split; else this.draft.metadata[id] = id === 'stage' ? Number($(id).value) : $(id).value; this.renderDraft(); this.changed(); });
    on('source-apply', 'click', async () => { const draft = JSON.parse($('source-json').value); if (draft.id !== this.id || !draft.events || !draft.windows) throw new Error('Keep source UUID and graph maps'); const validated = await api('/diagnostics', 'POST', draft); this.draft = validated.source; this.diagnostics = validated.diagnostics; this.editors.clear(); this.event = Object.keys(draft.events)[0]; this.renderDraft(); this.renderEvent(); this.changed(); });
    for (const id of ['sample-search', 'search-stage', 'search-split', 'search-tool']) on(id, id === 'sample-search' || id === 'search-tool' ? 'input' : 'change', () => { this.offset = 0; return this.listing(); });
    on('sample-page-prev', 'click', () => { this.offset = Math.max(0, this.offset - 50); return this.listing(); }); on('sample-page-next', 'click', () => { this.offset += 50; return this.listing(); });
    on('preferences-button', 'click', () => $('preferences-dialog').showModal());
    on('preferences-save', 'click', () => { const delay = Number($('preview-debounce').value); if (!Number.isFinite(delay) || delay < 0 || delay > 10000) throw new Error('Debounce must be 0–10000 ms'); this.prefs.profile = $('preview-profile').value; this.prefs.debounce = delay; this.savePreferences(); this.preview.invalidate(); this.frames = null; this.schedulePreview(); $('preferences-dialog').close(); });
    on('view-mode', 'change', () => { this.prefs.mode = $('view-mode').value; this.savePreferences(); this.preview.invalidate(); this.frames = null; this.position = 0; this.renderTimeline(); this.schedulePreview(); });
    const scrub = value => { this.position = Number(value); this.renderTimeline(); };
    on('scrubber', 'input', e => scrub(e.target.value)); on('tick-number', 'change', e => scrub(e.target.value));
    on('first-tick', 'click', () => scrub(0)); on('prev-tick', 'click', () => scrub(this.position - 1)); on('next-tick', 'click', () => scrub(this.position + 1)); on('last-tick', 'click', () => scrub($('scrubber').max));
    on('play', 'click', () => { if (this.playing) { clearInterval(this.playing); this.playing = null; $('play').textContent = 'Play'; } else { $('play').textContent = 'Pause'; this.playing = setInterval(() => scrub(this.position >= Number($('scrubber').max) ? 0 : this.position + 1), 500); } });
    on('settings-button', 'click', async () => { await this.loadSettings(); $('settings-json').value = json(this.settings); const usage = await api('/logs/usage'); $('log-usage').textContent = json(usage); $('settings-dialog').showModal(); });
    on('settings-save', 'click', async () => { await api('/settings', 'PUT', {settings: JSON.parse($('settings-json').value), expected_revision: this.settingsRecord.revision}); await this.loadSettings(); this.preview.invalidate(); this.schedulePreview(); $('settings-dialog').close(); });
    on('logs-clear', 'click', () => api('/logs', 'DELETE'));
    on('import-button', 'click', () => $('file-input').click()); on('file-input', 'change', async () => { const file = $('file-input').files[0]; if (!file) return; const r = await api('/imports', 'POST', {text: await file.text()}); await this.inspectJob(r.job_id); $('file-input').value = ''; });
    on('export-button', 'click', () => $('export-dialog').showModal());
    on('export-start', 'click', async () => { const r = await api('/exports', 'POST', {profile: $('export-profile').value || null, options: {compiled: $('export-format').value === 'compiled', trajectories_per_file: Number($('export-shard').value), minimum_stage: $('export-stage').value === '' ? null : Number($('export-stage').value), include_stale: $('export-stale').checked, exclude_missing_split: $('export-exclude').checked, metadata: $('export-metadata').value}}); $('export-dialog').close(); await this.inspectJob(r.job_id); });
    on('bulk-start', 'click', async () => { const action = $('bulk-action').value; const config = action === 'delete' ? {} : {value: action === 'stage' ? Number($('bulk-value').value) : $('bulk-value').value}; const r = await api('/bulk', 'POST', {query: this.query(), action, config}); this.ownedBulk.add(r.job_id); await this.inspectJob(r.job_id); });
    on('undo-edit', 'click', () => this.restore(true)); on('redo-edit', 'click', () => this.restore(false));
    on('review-button', 'click', () => { $('review-start').disabled = false; $('review-progress').textContent = ''; $('review-dialog').showModal(); });
    on('review-start', 'click', async () => {
      const selected = $('review-scope').value === 'selected';
      if (selected && (!this.id || this.isDirty())) throw new Error('Save or reload the selected draft before review.');
      const r = await api('/reviews', 'POST', {mode:$('review-mode').value, repair:$('review-repair').checked,
        ...(selected ? {trajectory:this.id, expected_revision:this.record.revision} : {query:this.query()})});
      this.reviewJob = r.job_id; this.ownedBulk.add(r.job_id); $('review-start').disabled = true;
      $('review-progress').textContent = 'Review running · ' + r.job_id;
    });
    on('metrics-button', 'click', async () => {
      const population = await api('/metrics/population', 'POST', {query: this.query()});
      const selected = this.id ? await api(`/metrics/trajectories/${this.id}`) : null;
      $('metrics-values').textContent = json({population, selected, note: 'Saved records only; unsaved drafts excluded.'});
      $('metrics-dialog').showModal();
    });
    on('metrics-calculate', 'click', async () => {
      const r = await api('/metrics/calculate-missing', 'POST', {query: this.query()});
      this.ownedBulk.add(r.job_id); $('metrics-dialog').close(); await this.inspectJob(r.job_id);
    });
    document.querySelectorAll('[data-close]').forEach(n => n.onclick = () => n.closest('dialog').close());
    await this.listing(); if (this.lastItems?.length) await this.select(this.lastItems[0].id); await this.pollJobs();
    const poll = async () => { try { await this.pollJobs(); const changes = await api(`/changes?after=${this.cursor || 0}`); if (changes.items.length || changes.resync) { this.cursor = changes.items.at(-1)?.cursor || changes.latest; if (changes.resync || changes.items.some(x => x.kind === 'settings')) { await this.loadSettings(); this.preview.invalidate(); this.frames = null; this.schedulePreview(); } await this.listing(); if (this.id && changes.items.some(x => x.entity_id === this.id)) message('Saved source changed. Your draft is retained; reload to reconcile.'); } } catch (e) { message(e); } this.pollTimer = setTimeout(poll, 1500); }; this.pollTimer = setTimeout(poll, 1500);
  }
  savePreferences() { localStorage.setItem('graph-preferences', JSON.stringify(this.prefs)); }
}

if (typeof document !== 'undefined' && $('workspace')) { const ui = new StudioUI(); globalThis.graphStudio = ui; ui.init().catch(message); }
