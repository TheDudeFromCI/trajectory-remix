import {renderProjectSettings, renderGenerationSettings} from './project-settings.js';
import {renderValidationSettings} from './validation-settings.js';
import {JobsView} from './jobs-view.js';
import {renderMetrics} from './metrics-view.js';
import {windowBody} from './window-render.js';
import {CommentsView} from './comments-view.js';
import {ProposalView} from './assistant-proposal.js';
import {MetricsEditor} from './metrics-editor.js';
import {renderComplexity} from './complexity-select.js';
import {studioWarnings} from './studio-warnings.js';
import {AssistantStatus, isAssistantOwner} from './assistant-indicator.js';
import {AssistantQueue} from './assistant-queue.js';
import {AutoSave} from './auto-save.js';
import {loadBarriers, loadRunInputs, persistInput, resolveSeed, saveBarriers} from './run-inputs.js';
import {duplicateTrajectory} from './duplicate.js';
import {renameEvent, renameProblem} from './event-rename.js';
import {describeMerge, mergeTools, toolImportControls} from './tool-import.js';
import {renderToolPicker} from './tool-picker.js';
import {parseArguments, renderArgumentForm} from './argument-form.js';
import {markInvalid} from './field-invalid.js';
import {ContentModes} from './content-mode.js';
import {WindowsPanel} from './windows-panel.js';
import {bindViewTabs, currentView, showView} from './views.js';
import {byId} from './panel-popout.js';
import {ConflictResolver} from './conflict-resolver.js';
import {icon, jobProgress, KIND_TONES, kindName, pastel, progressBar, reconcile, setAttr, setText, shortId, stageInfo, tag, toggleClass} from './ui-kit.js';
import {parkSettings, restoreSettings} from './tokenizer-store.js';
/* Graph studio transport and controller. Project operations use REST exclusively. */
const $ = byId; // Also finds elements in a popped-out panel.
const copy = value => structuredClone(value);
const json = value => JSON.stringify(value, null, 2);
const stable = value => Array.isArray(value) ? value.map(stable) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(k => [k, stable(value[k])])) : value;
let statusTimer;
const message = error => {
  const status = $('status'), failed = error instanceof Error;
  status.textContent = failed ? error.message : String(error); status.classList.toggle('error', failed);
  clearTimeout(statusTimer); if (!failed) statusTimer = setTimeout(() => { status.textContent = ''; }, 10000);
  // A modal hides the footer, so mirror the message into the open dialog's status line.
  const local = document.querySelector('dialog[open] [data-status]');
  if (local) { local.textContent = status.textContent; local.classList.toggle('error', failed); }
};
const ACTIVE_JOBS = new Set(['running', 'paused', 'queued']);
const GUTTER = 120; // Timeline lane labels.
const on = (id, event, handler) => $(id).addEventListener(event, async e => {
  try { await handler(e); } catch (error) { message(error); }
});
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const options = (id, entries, selected) => { const el = $(id); el.replaceChildren(...entries.map(([value, label]) => { const n = node('option', label); n.value = value; return n; })); el.value = selected ?? entries[0]?.[0] ?? ''; };
const button = (text, action, cls) => { const n = node('button', text, cls); n.onclick = async e => { try { await action(e); } catch (error) { message(error); } }; return n; };

/** Deep equality that ignores key order: the server may echo a source with its keys reordered. */
const same = (a, b) => JSON.stringify(stable(a)) === JSON.stringify(stable(b));
/** Refresh a raw JSON box unless the user is editing it. */
const keepTyping = (id, text) => { const el = document.getElementById(id); if (document.activeElement !== el) el.value = text; };
const isTyping = el => el?.tagName === 'TEXTAREA' || (el?.tagName === 'INPUT' && !['checkbox', 'radio', 'range', 'file'].includes(el.type));

export async function api(path, method = 'GET', body) {
  const response = await fetch(path.startsWith('/api/') ? path : '/api/v1' + path, {
    method, headers: {'X-Remix-Token': document.querySelector('meta[name=remix-token]').content, 'Content-Type': 'application/json'},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})
  });
  const data = await response.json();
  // `data` stays on the error so callers can read details such as a conflict's values.
  if (!response.ok) throw Object.assign(new Error(data.message || data.detail && JSON.stringify(data.detail) || data.errors && JSON.stringify(data.errors) || data.code || response.statusText), {data});
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
  for (const w of Object.values(intrinsic.windows || {})) if (!w.intentionally_left_open) delete w.intentionally_left_open;
  const data = new TextEncoder().encode(JSON.stringify(canonical(intrinsic)));
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', data))].map(v => v.toString(16).padStart(2, '0')).join('');
}

export class StudioUI {
  constructor() {
    let saved = {}; try { saved = JSON.parse(localStorage.getItem('graph-preferences') || '{}'); } catch {}
    this.prefs = {mode: 'graph', profile: '', debounce: 500, zoom: 1, showRandomTools: true, autoSendComments: false, hideClosedWindows: false, ...saved};
    // A draft that failed to save is not retried until it changes again.
    this.autoSave = new AutoSave({pending: () => !this.restoring && this.hasUnsavedChanges() && this.draftKey() !== this.failedKey,
      save: () => this.save(), typing: () => isTyping(document.activeElement),
      report: error => { this.failedKey = this.sentKey; message(error.data?.conflicts?.length ? 'Not saved: your change clashes with a newer save. Click “Failed to Save” to choose which to keep.' : 'Not saved: ' + error.message); this.renderDirty(); }});
    if (!Number.isFinite(this.prefs.zoom)) this.prefs.zoom = 1;
    this.prefs.zoom = Math.min(3, Math.max(0.25, this.prefs.zoom));
    if (!['graph', 'ticks'].includes(this.prefs.mode)) this.prefs.mode = 'graph';
    if (!Number.isFinite(this.prefs.debounce) || this.prefs.debounce < 0) this.prefs.debounce = 500;
    this.offset = 0; this.position = 0; this.drafts = new Map(); this.contentModes = new ContentModes(); this.jobs = new Map(); this.dialogs = new Map(); this.recorded = new Set(); this.ownedBulk = new Set();
    this.undo = []; this.redo = []; this.selectionGeneration = 0;
    this.jobsView = new JobsView({api, report: message, download: (job, name) => this.download(job, name),
      openTrajectory: async id => { showView('workspace'); await this.select(id); },
      afterDetach: () => this.refreshSelected(), isVisible: () => currentView() === 'jobs-view'});
    this.dialogs = this.jobsView.panels; // One retained detail panel per inspected job.
    this.preview = new PreviewController(async p => api(`/trajectories/${p.id}/preview`, 'POST', {
      expected_revision: p.revision, profile: p.profile, source: p.source, client_key: p.sourceHash
    }), value => { this.frames = value.frames; this.previewToolExtras = value.tool_extras || {}; if (this.followPreviewEnd) this.position = Math.max(0, value.frames.length - 1); this.followPreviewEnd = false; this.previewHash = value.source_hash; $('preview-status').textContent = value.truncated ? 'Showing first 1000 frames; preview truncated' : 'Tick history'; this.renderTimeline(); });
  }
  query() {
    const q = {}, size = $('sample-search').value.match(/(?:^|\s)@size:(\S+)/i); // `@size:m` or `@size:<=xs`
    const text = $('sample-search').value.replace(/(?:^|\s)@size:\S+/gi, ' ').trim();
    if (text) q.text = text;
    if (size) q.size = size[1];
    if ($('search-stage').value !== '') q.stage = Number($('search-stage').value);
    if ($('search-split').value) q.split = $('search-split').value;
    if ($('search-tool').value) q.tool = $('search-tool').value;
    if ($('search-comments').value) q.comments = $('search-comments').value;
    return q;
  }
  async loadSettings() {
    this.settingsRecord = await api('/settings'); this.settings = this.settingsRecord.settings;
    const profiles = [['', 'Select profile'], ...Object.entries(this.settings.tokenizer_profiles).map(([id, p]) => [id, p.name])];
    const exportSelection = $('export-profile').value;
    options('preview-profile', profiles, this.prefs.profile); options('export-profile', profiles, exportSelection);
    options('tool-id', Object.entries(this.settings.tools).map(([id, t]) => [id, t.name]), $('tool-id').value);
    this.windowsPanel?.setReasoningLevels(this.settings.reasoning_levels);
  }
  async listing() {
    const key = JSON.stringify([this.query(), this.offset]);
    this.listKey = key;
    const q = new URLSearchParams({...this.query(), limit: 50, offset: this.offset});
    const [data, counts] = await Promise.all([api('/trajectories?' + q), api('/comments/counts')]);
    if (key !== this.listKey) return;
    data.items = data.items.map(item => {
      const c = counts.counts[item.id] || {open: 0, addressed: 0};
      return {...item, comments: c.open + c.addressed, review: c.addressed};
    });
    this.lastItems = data.items; setText($('sample-count'), String(data.total));
    setText($('sample-page-label'), data.total ? `${Math.min(this.offset + 1, data.total)}–${this.offset + data.items.length} of ${data.total}` : '');
    $('sample-page-prev').disabled = !this.offset; $('sample-page-next').disabled = this.offset + 50 >= data.total;
    const list = $('sample-list');
    if (!data.items.length) {
      const empty = node('div', undefined, 'empty');
      empty.append(node('span', Object.keys(this.query()).length ? 'No trajectories match these filters.' : 'No trajectories yet.'));
      if (!Object.keys(this.query()).length) empty.append(button('Create trajectory', () => $('new-sample').click(), 'sm'), button('Import…', () => $('import-button').click(), 'sm ghost'));
      list.replaceChildren(empty); return;
    }
    reconcile(list, data.items, item => item.id, item => this.sampleRow(item), (row, item) => row.update(item));
  }
  sampleRow(item) {
    const row = button('', () => this.select(row.dataset.key), 'sample-row'); row.setAttribute('role', 'option');
    const id = node('span', shortId(item.id), 'mono sample-id'), stage = tag('', 'muted'), split = node('span', '', 'sample-split small'), lock = node('span', undefined, 'sample-lock');
    const notes = node('span', undefined, 'sample-comments'), noteCount = node('span', '', 'num');
    lock.append(icon('lock')); notes.append(icon('comment'), noteCount); row.append(id, stage, split, notes, lock);
    row.update = value => {
      const [name, tone] = stageInfo(value.stage);
      setText(stage, name); setAttr(stage, 'class', `tag tone-${tone}`);
      setText(split, value.split ?? 'No split'); toggleClass(split, 'warn', !value.split);
      setText(noteCount, String(value.comments || '')); setAttr(notes, 'hidden', !value.comments); toggleClass(notes, 'review', !!value.review);
      const open = value.comments - value.review;
      setAttr(notes, 'data-tip', value.comments ? [open && `${open} open`, value.review && `${value.review} awaiting your review`].filter(Boolean).join(' · ') : null);
      setAttr(lock, 'hidden', !value.owner); setAttr(lock, 'data-tip', value.owner ? 'Locked by a running job' : null);
      setAttr(row, 'data-tip', value.id); toggleClass(row, 'selected', value.id === this.id); setAttr(row, 'aria-selected', String(value.id === this.id));
      setAttr(row, 'aria-label', `${value.id} · ${name} · ${value.split ?? 'Missing split'}${value.comments ? ` · ${value.comments} unresolved comments` : ''}${value.owner ? ' · Locked' : ''}`);
    };
    return row;
  }
  markSelectedRow() {
    for (const row of $('sample-list').children) { toggleClass(row, 'selected', row.dataset.key === this.id); if (row.dataset.key) setAttr(row, 'aria-selected', String(row.dataset.key === this.id)); }
  }
  renderDirty() {
    const indicator = $('dirty-indicator'), locked = !!this.record?.owner, dirty = !locked && this.hasUnsavedChanges();
    const failed = dirty && !!this.autoSave.error && this.failedKey === this.draftKey();
    const resolvable = failed && !!this.saveConflicts();
    setText(indicator, !this.record ? '' : locked ? 'Locked' : failed ? 'Failed to Save' : dirty ? 'Saving...' : 'Saved');
    setAttr(indicator, 'data-tip', !failed ? null : resolvable ? 'Your change clashes with a newer save. Click to compare and choose which to keep.'
      : 'The last change could not be saved: ' + this.autoSave.error.message + ' It saves with your next change.');
    // A conflict can be resolved from the marker itself.
    setAttr(indicator, 'role', resolvable ? 'button' : 'status'); setAttr(indicator, 'tabindex', resolvable ? '0' : null);
    indicator.className = `tag tone-${locked || failed ? 'warning' : dirty ? 'accent' : 'muted'}${resolvable ? ' is-actionable' : ''}`;
    indicator.hidden = !this.record;
    this.metricsEditor?.sync({id: this.record ? this.id : null, revision: this.record?.revision, locked});
    setText($('trajectory-title'), this.id || 'No trajectory selected'); setAttr($('trajectory-title'), 'data-tip', null);
    this.autoSave.schedule();
  }
  editorState() {
    const ids = ['event-content', 'content-mode', 'event-kind', 'event-window', 'call-id', 'tool-id', 'chat-speaker', 'delivery', 'weight'];
    const flags = ['chain', 'replace-body', 'close-window', 'masked', 'begin', 'end', 'echo-chat'];
    return Object.fromEntries([...ids.map(id => [id, $(id).value]), ...flags.map(id => [id, $(id).checked])]);
  }
  /** Changes the auto-saver should store: the draft and applied event edits, not raw JSON being typed. */
  hasUnsavedChanges() {
    if (!this.record || !this.draft || this.record.owner) return false;
    return JSON.stringify(this.draft) !== JSON.stringify(this.record.source) || this.editors.size > 0 ||
      !!(this.event && this.editorBaseline && JSON.stringify(this.editorState()) !== JSON.stringify(this.editorBaseline));
  }
  draftKey() { return JSON.stringify([this.id, this.draft, this.event && this.editorState()]); }
  isDirty() {
    if (!this.record || !this.draft) return false;
    return JSON.stringify(this.draft) !== JSON.stringify(this.record.source) || this.editors.size > 0 ||
      (this.event && this.editorBaseline && JSON.stringify(this.editorState()) !== JSON.stringify(this.editorBaseline)) ||
      $('source-json').value !== json(this.draft) || $('metadata-json').value !== json(this.draft.metadata || {});
  }
  stashEditor() {
    if (!this.event || !this.editors) return;
    const state = this.editorState();
    if (JSON.stringify(state) !== JSON.stringify(this.editorBaseline)) this.editors.set(this.event, state);
    else this.editors.delete(this.event);
  }
  async select(id, force = false) {
    if (this.id !== id) await this.autoSave.flush();
    const generation = ++this.selectionGeneration;
    this.followPreviewEnd = false;
    this.preview.invalidate(); this.frames = null; clearTimeout(this.validationTimer); this.validationGeneration = (this.validationGeneration || 0) + 1;
    if (this.id && this.draft) {
      this.stashEditor();
      if (this.isDirty()) this.drafts.set(this.id, {record: this.record, draft: this.draft, event: this.event, editors: this.editors,
        sourceText: $('source-json').value, metadataText: $('metadata-json').value});
      else this.drafts.delete(this.id);
    }
    if (force) this.drafts.delete(id);
    const cached = !force && this.drafts.get(id);
    const latest = await api(`/trajectories/${id}`);
    if (generation !== this.selectionGeneration) return;
    this.id = id; this.record = cached ? {...cached.record, owner: latest.owner} : latest;
    this.diagnostics = cached ? cached.record.diagnostics : latest.diagnostics;
    this.draft = cached?.draft || copy(latest.source); this.editors = cached?.editors || new Map();
    this.event = cached?.event || Object.keys(this.draft.events)[0]; this.position = 0;
    this.windowsPanel.reset(this.draft.events[this.event]?.window);
    this.renderDraft(); this.renderEvent(); this.markSelectedRow();
    if (cached) { $('source-json').value = cached.sourceText; $('metadata-json').value = cached.metadataText;
      if (latest.revision !== cached.record.revision) message('Saved source changed. Your edits are retained; reload to reconcile.'); }
    this.listing(); this.schedulePreview(); this.comments?.load(id).catch(message); this.proposal?.load(id).catch(message);
  }
  async refreshSelected() {
    if (!this.id) return;
    const id = this.id, generation = this.selectionGeneration;
    const latest = await api(`/trajectories/${id}`);
    if (id !== this.id || generation !== this.selectionGeneration) return;
    // A save that landed (or is landing) while this was fetched is newer; the next poll catches up.
    if (this.autoSave.running || latest.revision < this.record.revision) return;
    const settingsChanged = latest.diagnostics_settings_revision !== this.record.diagnostics_settings_revision;
    if (latest.revision === this.record.revision && latest.owner === this.record.owner && !settingsChanged) return;
    if (this.isDirty()) {
      this.record.owner = latest.owner;
      if (settingsChanged) {
        this.record.diagnostics_settings_revision = latest.diagnostics_settings_revision;
        this.scheduleDiagnostics();
      }
      this.applyLock();
      if (latest.revision !== this.record.revision) message('Saved source changed. Your edits are retained; reload to reconcile.');
      return;
    }
    const followEnd = this.position >= (this.maxPosition || 0);
    this.followPreviewEnd = this.prefs.mode === 'ticks' && followEnd;
    this.preview.invalidate(); this.frames = null;
    this.record = latest; this.draft = copy(latest.source); this.diagnostics = latest.diagnostics;
    if (followEnd && this.prefs.mode !== 'ticks') this.position = Math.max(0, graphOrder(this.draft).length - 1);
    this.editors.clear(); this.drafts.delete(id);
    if (!this.draft.events[this.event]) this.event = Object.keys(this.draft.events)[0];
    this.renderDraft(); this.renderEvent(); this.schedulePreview();
  }
  applyLock() {
    const locked = !!this.record?.owner;
    for (const id of ['delete-sample', 'new-event', 'delete-event', 'event-apply', 'dependency-add', 'metadata-apply', 'source-apply']) $(id).disabled = locked || !this.draft;
    for (const root of ['graph-editor-fields', 'metadata-panel']) {
      const region = $(root); if (region) for (const input of region.querySelectorAll('input,select,textarea')) if (!input.closest('[data-lock-exempt]')) input.disabled = locked;
    }
    // Existing markup uses these stable controls even before the shell update.
    for (const id of ['event-content','event-name','event-kind','event-window','call-id','tool-id','chat-speaker','delivery','weight','chain','replace-body','close-window','masked','begin','end','echo-chat','metadata-json','source-json','stage','split']) $(id).disabled = locked;
    let ownerLink = $('open-selected-job');
    if (!ownerLink) {
      ownerLink = button('Inspect owning job', () => this.inspectJob(this.record.owner), 'sm'); ownerLink.id = 'open-selected-job';
      const note = node('span', undefined, 'lock-note'); note.append(icon('lock'), node('span', 'A job is working on this trajectory. Editing unlocks when it settles or is detached.', 'truncate'));
      $('lock-slot').append(note, ownerLink);
    }
    $('lock-slot').hidden = !locked;
    if (locked) setAttr(ownerLink, 'data-tip', 'Job ' + this.record.owner);
    $('workspace').classList.toggle('is-locked', locked);
    this.windowsPanel?.setLocked(locked);
    this.renderDirty();
  }
  changed() {
    this.frames = null; this.preview.invalidate();
    $('source-json').value = json(this.draft); this.renderDirty();
    this.renderTimeline(); this.schedulePreview(); this.scheduleDiagnostics();
  }
  renderDraft() {
    if (!this.draft) return;
    options('event-picker', Object.keys(this.draft.events).map(id => [id, id]), this.event);
    options('event-window', Object.keys(this.draft.windows).map(id => [id, id]), $('event-window').value || this.draft.events[this.event]?.window);
    this.windowsPanel.render();
    keepTyping('metadata-json', json(this.draft.metadata || {}));
    $('stage').value = this.draft.metadata?.stage ?? -3; $('split').value = this.draft.metadata?.split ?? '';
    const size = this.record.size; $('size-tag').textContent = size?.label || '';
    setAttr($('size-tag'), 'data-tip', size ? `Saved source: about ${size.total_tokens_estimate.toLocaleString()} tokens total, about ${size.agent_tokens_estimate.toLocaleString()} agent-generated.` : null);
    keepTyping('source-json', json(this.draft));
    this.renderDiagnostics();
    this.applyLock();
    this.renderTimeline();
  }
  renderDiagnostics() {
    const studio = studioWarnings({draft: this.draft, record: this.record, metrics: this.metricsView});
    const list = [...(this.diagnostics || []), ...studio], errors = list.filter(d => d.severity === 'error').length, warnings = list.length - errors;
    $('diagnostics').replaceChildren(...list.map(d => {
      const n = node('div', undefined, 'diagnostic ' + d.severity), head = node('div', undefined, 'row');
      const origin = tag(d.source === 'studio' ? 'Studio' : 'Compiler', 'muted', 'diagnostic-source');
      setAttr(origin, 'data-tip', d.source === 'studio' ? 'Editor check: never blocks export and is not shown to agents' : 'Structural check from the compiler');
      head.append(tag(d.severity === 'error' ? 'Error' : 'Warning', d.severity === 'error' ? 'danger' : 'warning'), origin, node('code', d.code, 'small grow truncate'));
      if (d.event_id) head.append(button('Inspect event', () => { this.selectEvent(d.event_id); $('tab-content')?.click(); }, 'sm ghost'));
      if (d.action === 'split') head.append(button('Set split', () => { $('tab-metrics').click(); $('split').focus(); }, 'sm ghost'));
      if (d.action === 'metrics') head.append(button('Show metrics', () => $('tab-metrics').click(), 'sm ghost'));
      n.append(head, node('p', d.message)); return n;
    }));
    if (!list.length) $('diagnostics').append(node('div', 'No problems. Errors block compiled export; warnings do not.', 'empty'));
    setText($('problems-count'), list.length ? String(list.length) : '');
    $('problems-count').className = 'badge' + (errors ? ' danger' : warnings ? ' warning' : '');
    const summary = $('diagnostics-summary'); summary.hidden = !list.length;
    summary.replaceChildren(...(errors ? [tag(`${errors} error${errors === 1 ? '' : 's'}`, 'danger')] : []), ...(warnings ? [tag(`${warnings} warning${warnings === 1 ? '' : 's'}`, 'warning')] : []));
    summary.setAttribute('aria-label', `${errors} errors, ${warnings} warnings. Show problems`);
  }
  /** After the Windows tab edits the draft; deleting a window also deletes its events. */
  windowsChanged(removed = []) {
    for (const id of removed) this.editors.delete(id);
    if (!this.draft.events[this.event]) this.event = Object.keys(this.draft.events)[0];
    this.renderDraft(); this.renderEvent(); this.changed();
    if (removed.length) message(`Deleted ${removed.length} event${removed.length === 1 ? '' : 's'} with the window.`);
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
    const candidates = Object.keys(this.draft?.events || {}).filter(id => id !== this.event);
    options('dependency-picker', [['', candidates.length ? 'Choose an event' : 'No other events'], ...candidates.map(id => [id, id])], candidates.includes($('dependency-picker').value) ? $('dependency-picker').value : '');
    toggleClass($('event-editor-panel'), 'no-event', !e);
    if (!e) { $('event-content').value = ''; $('dependencies').replaceChildren(); setText($('links-count'), ''); this.editorBaseline = null; if (this.record) this.renderDirty(); return; }
    $('event-name').value = this.event; $('event-kind').value = e.kind; $('event-window').value = e.window; $('call-id').value = e.call_id || '';
    $('tool-id').value = e.tool || ''; $('chat-speaker').value = e.chat?.speaker || 'User'; $('delivery').value = e.delivery?.mode || 'instant';
    $('chain').checked = e.delivery?.chain_immediately ?? true; $('replace-body').checked = e.replace_body ?? false;
    $('close-window').checked = e.close_window; $('masked').checked = e.supervision.masked; $('weight').value = e.supervision.weight;
    $('begin').checked = e.insert_tool_begin ?? e.insert_response_begin ?? true; $('end').checked = e.insert_tool_end ?? e.insert_response_end ?? true;
    $('echo-chat').checked = e.echo_chat ?? true;
    $('content-mode').value = this.contentModes.pick(this.id, this.event, e.content); $('event-content').value = typeof e.content === 'string' ? e.content : json(e.content);
    this.editorBaseline = this.editorState();
    const stashed = this.editors.get(this.event);
    if (stashed) for (const [id, value] of Object.entries(stashed)) { if (typeof value === 'boolean') $(id).checked = value; else $(id).value = value; }
    this.kindControls(); this.renderArguments(); this.renderToolPicker(); this.renderDependencies();
    // renderDraft() runs before the baseline above is reset, so refresh the badge now.
    if (this.record) this.renderDirty();
  }
  /** A tool_schema event swaps its text box for tool checkboxes; every other kind keeps the text box. */
  renderToolPicker() {
    const holder = $('tool-picker'), head = document.querySelector('#panel-content .content-head');
    const isSchema = $('event-kind').value === 'tool_schema' && $('content-mode').value === 'text';
    this.toolPickerShown = isSchema; holder.hidden = !isSchema; $('event-content').hidden = isSchema || !!this.argumentForm; if (head) head.hidden = isSchema;
    if (!isSchema) { holder.replaceChildren(); return; }
    renderToolPicker(holder, {text: $('event-content').value, tools: this.settings?.tools || {}, disabled: !!this.record?.owner, report: message,
      relist: async chosen => {
        const listing = await api('/tools/listing', 'POST', {trajectory_id: this.draft.id, event_id: this.event, tools: chosen});
        $('event-content').value = listing.content;
        this.stashEditor(); this.applyEvent(); this.renderToolPicker();
      }});
  }
  kindControls() {
    const kind = $('event-kind').value, tool = kind === 'tool_call', response = kind === 'tool_response', chat = kind === 'chat_message';
    const external = response || kind === 'external_text' || kind === 'tool_schema';
    for (const [id, show] of Object.entries({'call-id': tool || response || chat, 'tool-id': tool, 'chat-speaker': chat, delivery: external, chain: external || chat, 'replace-body': external, begin: tool || response, end: tool || response, 'echo-chat': tool})) $(id).closest('label').hidden = !show;
    this.markRequiredIds();
  }
  /** Red outline on an empty Call ID (tool call or response) or Tool (tool call). The event still
     saves; compiling reports missing_call_id / missing_tool. */
  markRequiredIds() {
    const kind = $('event-kind').value;
    markInvalid($('call-id'), ['tool_call', 'tool_response'].includes(kind) && !$('call-id').value.trim());
    markInvalid($('tool-id'), kind === 'tool_call' && !$('tool-id').value);
  }
  /** Form mode swaps the text box for one typed field per tool argument; unparseable content keeps the text box. */
  renderArguments() {
    const holder = $('argument-form'), form = $('content-mode').value === 'form';
    const args = form ? parseArguments($('event-content').value) : null;
    this.argumentForm = !!args; $('event-content').hidden = this.argumentForm || this.toolPickerShown;
    if (!args) {
      holder.replaceChildren();
      if (form) message('Form mode needs a complete JSON argument object. Raw content retained.');
      return;
    }
    const schema = $('event-kind').value === 'tool_call' ? this.settings.tools[$('tool-id').value]?.input_schema : undefined;
    renderArgumentForm(holder, {args, schema, disabled: !!this.record?.owner, report: message,
      onChange: value => { $('event-content').value = json(value); this.stashEditor(); this.applyEvent(); }});
  }
  composeEvent() {
    const kind = $('event-kind').value, external = ['external_text', 'tool_schema', 'tool_response', 'chat_message'].includes(kind);
    let content = $('event-content').value;
    if ($('content-mode').value === 'json' && !['tool_call', 'tool_response'].includes(kind)) content = JSON.parse(content);
    const e = {kind, window: $('event-window').value, after: this.draft.events[this.event]?.after || [], content,
      close_window: $('close-window').checked, supervision: {masked: $('masked').checked, weight: Number($('weight').value)}};
    if (external) e.delivery = {mode: kind === 'chat_message' ? 'instant' : $('delivery').value, chain_immediately: $('chain').checked};
    if (kind === 'external_text' || kind === 'tool_schema' || kind === 'tool_response') e.replace_body = $('replace-body').checked;
    if (kind === 'tool_call' || kind === 'tool_response') e.call_id = $('call-id').value;
    if (kind === 'tool_call') Object.assign(e, {tool: $('tool-id').value, insert_tool_begin: $('begin').checked, insert_tool_end: $('end').checked, echo_chat: $('echo-chat').checked});
    if (kind === 'tool_response') Object.assign(e, {insert_response_begin: $('begin').checked, insert_response_end: $('end').checked});
    if (kind === 'chat_message') { e.chat = {speaker: $('chat-speaker').value}; if ($('call-id').value) e.call_id = $('call-id').value; }
    return e;
  }
  renameCurrentEvent() {
    const current = this.event, next = $('event-name').value.trim();
    if (!current || !this.draft?.events[current]) return;
    const problem = renameProblem(this.draft.events, current, next);
    if (problem) { $('event-name').value = current; if (next !== current) message(problem); return; }
    if (next === current) return;
    this.stashEditor();
    renameEvent(this.draft, current, next);
    if (this.editors.has(current)) { this.editors.set(next, this.editors.get(current)); this.editors.delete(current); }
    this.contentModes.rename(this.id, current, next);
    this.event = next; this.renderDraft(); this.renderEvent(); this.changed();
  }
  applyEvent() { if (!this.event || !this.draft.events[this.event]) return; this.draft.events[this.event] = this.composeEvent(); this.editors.delete(this.event); this.editorBaseline = this.editorState(); this.changed(); }
  renderDependencies() {
    const after = this.draft.events[this.event]?.after || [];
    $('dependencies').replaceChildren(...after.map(dep => {
      const chip = button(`${this.draft.events[dep] ? '' : 'Broken: '}${dep} ×`, () => {
        if (this.record?.owner) return message('This trajectory is locked. Detach it before editing.');
        this.draft.events[this.event].after = this.draft.events[this.event].after.filter(x => x !== dep); this.renderDependencies(); this.changed();
      }, 'chip' + (this.draft.events[dep] ? '' : ' broken'));
      chip.setAttribute('aria-label', `Remove dependency ${dep}`); return chip;
    }));
    if (!after.length) $('dependencies').append(node('span', 'Nothing — this is a root event.', 'muted small'));
    setText($('links-count'), after.length ? String(after.length) : '');
  }
  addDependency(dep) {
    if (this.record?.owner) return message('This trajectory is locked. Detach it before editing.');
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
  windowTone(id) {
    const ids = Object.keys(this.draft?.windows || {}), index = ids.indexOf(id);
    return pastel(index < 0 ? ids.length + [...String(id)].reduce((n, c) => n + c.charCodeAt(0), 0) : index);
  }
  // Horizontal pixel position of the scrub line for a timeline position (and its inverse).
  cursorX(position) {
    const zoom = this.prefs.zoom;
    return this.prefs.mode === 'ticks' ? GUTTER + 12 + position * 7 * zoom : GUTTER + 12 + position * 185 * zoom + 175 * zoom;
  }
  positionAt(x) {
    const zoom = this.prefs.zoom, ticks = this.prefs.mode === 'ticks';
    const raw = ticks ? (x - GUTTER - 12) / (7 * zoom) : (x - GUTTER - 12 - 175 * zoom) / (185 * zoom);
    return Math.min(this.maxPosition || 0, Math.max(0, Math.round(raw)));
  }
  startScrub(down) {
    down.preventDefault();
    const move = e => {
      const chart = $('timeline').querySelector('.timeline-lanes'); if (!chart) return;
      const position = this.positionAt(e.clientX - chart.getBoundingClientRect().left);
      if (position !== this.position) { this.followPreviewEnd = false; this.position = position; this.renderTimeline(); }
    };
    const stop = () => { removeEventListener('pointermove', move); removeEventListener('pointerup', stop); removeEventListener('pointercancel', stop); };
    addEventListener('pointermove', move); addEventListener('pointerup', stop); addEventListener('pointercancel', stop);
    move(down);
  }
  renderTimeline() {
    if (!this.draft) {
      $('timeline').replaceChildren(node('div', 'Select a trajectory to see its timeline.', 'empty'));
      $('window-grid').replaceChildren(node('div', 'Select a trajectory from the dataset, or create one with +.', 'empty'));
      return;
    }
    this.comments?.setContext(this.draft, this.event);
    const order = graphOrder(this.draft), ticks = this.prefs.mode === 'ticks', zoom = this.prefs.zoom;
    const frames = this.frames || [];
    const depth = {};
    for (const id of order) depth[id] = Math.max(0, ...this.draft.events[id].after.map(dep => (depth[dep] ?? -1) + 1));
    // Graph mode steps one dependency level at a time, so parallel events appear together.
    const max = ticks ? Math.max(0, frames.length - 1) : Math.max(0, ...order.map(id => depth[id]));
    this.maxPosition = max;
    this.position = Math.min(max, Math.max(0, this.position));
    $('tick-number').value = this.position; $('tick-number').max = max; $('max-tick').textContent = '/ ' + max;
    const visible = ticks ? new Set(frames.slice(0, this.position + 1).flatMap(f => [...f.inputs, ...f.outputs].map(e => e.event))) : new Set(order.filter(id => depth[id] <= this.position));
    const lanes = [...new Set(order.map(id => this.draft.events[id].window))], positions = new Map();
    const spans = new Map();
    if (ticks) frames.forEach((f, index) => [...f.inputs, ...f.outputs].forEach(e => {
      if (!e.event) return; const span = spans.get(e.event) || [index, index]; span[1] = index; spans.set(e.event, span);
    }));
    for (const id of order) {
      const event = this.draft.events[id];
      const span = spans.get(id);
      positions.set(id, {x: GUTTER + (ticks ? 12 + (span?.[0] || 0) * 7 * zoom : 12 + depth[id] * 185 * zoom),
        y: 10 + lanes.indexOf(event.window) * 44, width: ticks ? Math.max(28, ((span?.[1] || 0) - (span?.[0] || 0) + 1) * 7 * zoom) : Math.max(60, 165 * zoom)});
    }
    const chart = node('div', undefined, 'timeline-lanes');
    const width = Math.max(450, ...[...positions.values()].map(p => p.x + p.width + 20));
    const height = Math.max(64, lanes.length * 44 + 16); chart.style.width = width + 'px'; chart.style.height = height + 'px';
    lanes.forEach((lane, index) => {
      const label = node('div', lane, 'lane-label truncate'); label.style.top = (10 + index * 44) + 'px'; label.style.setProperty('--tone', `var(--pastel-${this.windowTone(lane)})`);
      const band = node('div', undefined, 'lane-band'); band.style.top = (6 + index * 44) + 'px';
      chart.append(band, label);
    });
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('width', width); svg.setAttribute('height', height); svg.classList.add('dependency-edges');
    for (const id of order) {
      const target = positions.get(id);
      for (const dep of this.draft.events[id].after) {
        const origin = positions.get(dep); if (!origin) continue;
        const edge = document.createElementNS(svg.namespaceURI, 'path');
        const start = origin.x + origin.width, end = target.x, middle = (start + end) / 2;
        edge.setAttribute('d', `M${start},${origin.y + 14} C${middle},${origin.y + 14} ${middle},${target.y + 14} ${end},${target.y + 14}`);
        if (id === this.event || dep === this.event) edge.classList.add('active');
        svg.append(edge);
      }
    }
    chart.append(svg);
    for (const id of order) {
      const e = this.draft.events[id], p = positions.get(id);
      const n = button('', click => click.shiftKey ? this.addDependency(id) : this.selectEvent(id), 'event-node' + (id === this.event ? ' selected' : '') + (visible.has(id) ? '' : ' future'));
      n.append(node('span', id, 'truncate'), node('span', ` → ${e.window}`, 'event-node-window'));
      const notes = this.comments?.count('event', id) || 0;
      if (notes) { const badge = node('span', undefined, 'event-node-comments'); badge.append(icon('comment'), node('span', String(notes))); n.append(badge); }
      n.title = `Depends on: ${e.after.join(', ') || 'root'}${notes ? ` · ${notes} open comment${notes === 1 ? '' : 's'}` : ''}`;
      n.style.setProperty('--tone', `var(--pastel-${this.windowTone(e.window)})`);
      Object.assign(n.style, {left: p.x + 'px', top: p.y + 'px', width: p.width + 'px'}); chart.append(n);
    }
    if (order.length) {
      const line = node('div', undefined, 'timeline-cursor'); line.title = 'Drag to scrub the timeline';
      line.style.left = this.cursorX(this.position) + 'px'; chart.append(line);
      chart.addEventListener('pointerdown', e => { if (!e.target.closest('.event-node')) this.startScrub(e); });
    }
    if (!order.length) chart.append(node('div', 'No events yet. Add one with + in the event editor.', 'timeline-empty'));
    $('timeline').replaceChildren(chart);
    const windows = new Map();
    const ensure = id => { if (!windows.has(id)) windows.set(id, {tokens: [], closed: false}); return windows.get(id); };
    if (ticks) {
      for (const f of frames.slice(0, this.position + 1)) for (const emission of [...f.inputs, ...f.outputs]) {
        if (emission.window === '@available') continue;
        const w = ensure(emission.window); if (emission.replace) w.tokens = w.tokens.filter(t => t.header);
        for (const t of emission.tokens) { w.tokens.push({...t, event: emission.event}); if (t.control === 'eos') w.closed = true; }
      }
    } else for (const id of order.filter(id => visible.has(id))) {
      const e = this.draft.events[id], w = ensure(e.window), add = token => w.tokens.push({...token, event: id});
      if (e.replace_body) w.tokens = [];
      if (!w.tokens.length) {
        // Same JSON shape as the compiled header; the slot is only known after scheduling.
        const win = this.draft.windows[e.window] || {};
        w.tokens.push({control: 'ctx_begin'}, {text: JSON.stringify({purpose: win.purpose || e.window, reasoning_effort: win.reasoning_effort ?? null})}, {control: 'ctx_end'});
      }
      const tool = e.kind === 'tool_call', response = e.kind === 'tool_response', chat = e.kind === 'chat_message';
      if (tool && e.insert_tool_begin || response && e.insert_response_begin || chat) add({control: chat ? 'msg_begin' : tool ? 'tool_begin' : 'response_begin'});
      if (chat) add({text: e.chat.speaker + ': '});
      if (tool && e.insert_tool_begin) add({text: JSON.stringify(e.tool) + ':'});  // as compiled: "tool":args
      add({text: typeof e.content === 'string' ? e.content : e.content.map(p => p.kind === 'text' ? p.text : `[${p.window}]`).join('')});
      if (tool && e.insert_tool_end || response && e.insert_response_end || chat) add({control: chat ? 'msg_end' : tool ? 'tool_end' : 'response_end'});
      if (e.close_window) { add({control: 'eos'}); w.closed = true; }
    }
    // Windows touched by the step that led to this position get a faint glow.
    this.changedWindows = new Set(ticks
      ? [...(frames[this.position]?.inputs || []), ...(frames[this.position]?.outputs || [])].map(e => e.window)
      : order.filter(id => depth[id] === this.position).map(id => this.draft.events[id].window));
    this.windowModels = windows;
    const hideClosed = this.prefs.hideClosedWindows === true;
    const cards = [...windows].filter(([, w]) => !(hideClosed && w.closed)).map(([id, w]) => this.windowCard(id, w));
    $('window-grid').replaceChildren(...(cards.length ? cards : [node('div', windows.size ? 'All windows at this position are closed (hidden in Preferences).' : order.length ? 'No window has content at this position. Move the timeline forward.' : 'This trajectory has no events yet.', 'empty')]));
    if (!ticks) $('preview-status').textContent = '';
    this.renderPopout();
  }
  isAgentMessage(eventId, speaker) {
    const e = this.draft?.events[eventId];
    if (String(eventId || '').endsWith(':echo') || speaker === 'agent') return true;
    return !!(e?.call_id && Object.values(this.draft.events).some(o => o.kind === 'tool_call' && o.call_id === e.call_id));
  }
  windowCard(id, w, popout = false) {
    this.rawWindows ||= new Set();
    const raw = this.rawWindows.has(id), card = node('article', undefined, 'window-card' + (popout ? ' popout-card' : '') + (this.changedWindows?.has(id) ? ' changed' : ''));
    const head = node('header', undefined, 'window-card-head'), purpose = this.draft.windows[id]?.purpose || '';
    card.style.setProperty('--tone', `var(--pastel-${this.windowTone(id)})`);
    head.append(node('span', undefined, 'dot'), node(popout ? 'h2' : 'h3', id, 'truncate'), node('span', purpose, 'small muted truncate'));
    if (w.closed) head.append(tag('Closed', 'muted'));
    const toggle = button('Raw', () => { if (raw) this.rawWindows.delete(id); else this.rawWindows.add(id); this.renderTimeline(); }, 'ghost sm window-raw');
    toggle.setAttribute('aria-pressed', String(raw)); toggle.setAttribute('aria-label', `Show raw tokens for window ${id}`);
    toggle.dataset.tip = raw ? 'Showing the exact token stream. Click for the readable view.' : 'Show the exact token stream instead of the readable view.';
    const size = popout
      ? button('', () => this.closePopout(), 'ghost icon sm window-shrink')
      : button('', () => this.openPopout(id), 'ghost icon sm window-expand');
    size.append(icon(popout ? 'shrink' : 'expand')); size.setAttribute('aria-label', `${popout ? 'Shrink' : 'Expand'} window ${id}`);
    size.dataset.tip = popout ? 'Shrink back to the window grid (Esc)' : 'Expand this window into a large panel';
    const notes = this.comments?.count('window', id) || 0, comment = button('', () => this.commentOn('window', id), 'ghost sm window-comment' + (notes ? ' has-comments' : ''));
    comment.append(icon('comment')); if (notes) comment.append(node('span', String(notes), 'num'));
    comment.setAttribute('aria-label', `Comment on window ${id}${notes ? ` (${notes} open)` : ''}`);
    comment.dataset.tip = notes ? `${notes} open comment${notes === 1 ? '' : 's'}. Click to add another.` : 'Comment on this window, for example a problem an agent should fix';
    head.append(comment, toggle, size);
    head.classList.add('clickable'); head.title = 'Jump to the latest event that edits this window';
    head.addEventListener('click', click => { if (!click.target.closest('button')) this.selectLatestWindowEvent(w); });
    card.append(head, windowBody(w.tokens, {raw, purpose, isAgent: (event, speaker) => this.isAgentMessage(event, speaker), toolExtras: event => this.previewToolExtras?.[event] || [], hideExtras: !this.prefs.showRandomTools}));
    return card;
  }
  /** Select the last event (at or before the current position) that wrote tokens into this window. */
  selectLatestWindowEvent(w) {
    for (let i = w.tokens.length - 1; i >= 0; i--) {
      const raw = w.tokens[i].event; if (!raw) continue;
      const id = this.draft.events[raw] ? raw : String(raw).replace(/:echo$/, '');
      if (this.draft.events[id]) { this.selectEvent(id); return; }
    }
  }
  commentOn(kind, id) {
    if (!this.draft) return;
    $('tab-comments').click(); this.comments.compose(kind, id);
  }
  inspectTarget(kind, id) {
    if (kind === 'event') { this.selectEvent(id); $('tab-content').click(); }
    if (kind === 'window') {
      this.windowsPanel.select(id); $('tab-windows').click();
      [...$('window-grid').querySelectorAll('.window-comment')].find(b => b.getAttribute('aria-label').startsWith(`Comment on window ${id}`))?.closest('.window-card')?.scrollIntoView?.({block: 'nearest'});
    }
  }
  openPopout(id) { this.popoutWindow = id; $('window-popout').hidden = false; this.renderPopout(); $('window-popout').querySelector('.window-shrink')?.focus(); }
  closePopout() {
    const id = this.popoutWindow; this.popoutWindow = null; $('window-popout').hidden = true;
    [...$('window-grid').querySelectorAll('.window-expand')].find(b => b.getAttribute('aria-label') === `Expand window ${id}`)?.focus();
  }
  renderPopout() {
    const panel = $('window-popout'); if (!panel || !this.popoutWindow) return;
    if (!this.draft) { this.closePopout(); return; }
    const w = this.windowModels?.get(this.popoutWindow) || {tokens: [], closed: false};
    const body = $('window-popout-body'), scroll = body.querySelector('.window-formatted, .window-tokens')?.scrollTop;
    body.replaceChildren(this.windowCard(this.popoutWindow, w, true));
    const inner = body.querySelector('.window-formatted, .window-tokens'); if (inner && scroll) inner.scrollTop = scroll;
    setText($('popout-position'), `${this.position} / ${this.maxPosition || 0}`);
    if (!w.tokens.length) body.querySelector('.window-card').append(node('div', 'This window has no content at this position.', 'empty'));
  }
  history(deltas) { if (!deltas.length) return; this.undo.push(deltas); if (this.undo.length > 50) this.undo.shift(); this.redo = []; this.historyButtons(); }
  historyButtons() { $('undo-edit').disabled = !this.undo.length; $('redo-edit').disabled = !this.redo.length; }
  async restore(undo) {
    // Save what was just typed first, so Undo steps back from the latest state.
    await this.autoSave.flush();
    if (this.hasUnsavedChanges()) throw new Error('The latest change could not be saved yet, so Undo/Redo is paused. Fix it or reload the saved source.');
    this.restoring = true;
    try { await this.restoreSteps(undo); } finally { this.restoring = false; }
  }
  async restoreSteps(undo) {
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
  /** The values the last failed save clashed on, when it can be resolved by choosing. */
  saveConflicts() { const c = this.autoSave.error?.data?.conflicts; return Array.isArray(c) && c.length ? c : null; }
  /** Failed to Save → compare each clashing value and save again keeping mine or theirs. */
  async resolveConflicts() {
    const conflicts = this.saveConflicts();
    if (!conflicts || this.record?.owner) return;
    const resolutions = await this.conflictResolver.open(conflicts);
    if (!resolutions) return;
    this.resolutions = resolutions; this.failedKey = null;
    try { await this.autoSave.flush(); } finally { this.resolutions = null; this.renderDirty(); }
    if (!this.autoSave.error) message('Conflict resolved and saved. Undo reverts it.');
  }
  async save() {
    if (!this.draft) return;
    this.applyEvent(); const before = copy(this.record.source), sent = copy(this.draft), id = this.id, generation = this.selectionGeneration;
    this.sentKey = this.draftKey(); // A failure is charged to this draft, not to later edits.
    const base = this.record.revision, resolutions = this.resolutions;
    const value = await api(`/trajectories/${id}`, 'PUT', {expected_revision: base, source: sent, ...(resolutions ? {resolutions} : {})});
    // The server merges a save made from an older revision onto newer saves (a metric value, a job);
    // Undo then steps back to the revision just before ours, keeping those changes.
    const prior = value.revision > base + 1 ? (await api(`/trajectories/${id}?revision=${value.revision - 1}`)).source : before;
    this.history([{id, before: prior, after: value.source, expected_revision: value.revision, expected_commit: value.commit_id}]);
    // A reload (switching trajectory, Undo/Redo) or a later save already shows newer state.
    if (id !== this.id || generation !== this.selectionGeneration || value.revision < this.record.revision) return;
    // A save response has no `owner`; keep the known one so the next poll doesn't see a change and reload.
    this.record = {...value, owner: this.record.owner}; this.diagnostics = value.diagnostics;
    // Edits made while the save was in flight stay in the draft and are saved next.
    const unchanged = same(this.draft, sent), normalized = !same(value.source, sent);
    if (unchanged) this.draft = copy(value.source);
    if (unchanged && normalized) { this.editors.clear(); this.renderDraft(); this.renderEvent(); } else this.renderDraft();
    this.schedulePreview(); await this.listing();
  }
  async duplicate() {
    if (!this.record) return message('Select a trajectory to duplicate.');
    $('trajectory-menu')?.removeAttribute('open');
    const unsaved = this.isDirty(), original = this.id, r = await duplicateTrajectory(api, this.record.source);
    await this.select(r.source.id);
    message(`Duplicated ${shortId(original)}${unsaved ? ' from its saved version; unsaved edits stay on the original' : ''}.`);
  }
  exportSource() {
    if (!this.draft || !this.id) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(this.draft, null, 2) + '\n'], {type: 'application/json'})), a = node('a');
    a.href = url; a.download = `${this.id}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async exportMatches() {
    const response = await fetch('/api/v1/trajectories/export?' + new URLSearchParams(this.query()), {headers: {'X-Remix-Token': document.querySelector('meta[name=remix-token]').content}});
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).message || response.statusText);
    const url = URL.createObjectURL(await response.blob()), a = node('a'); a.href = url; a.download = 'trajectories.jsonl'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async download(job, name) {
    const response = await fetch(`/api/v1/jobs/${job}/artifacts/${encodeURIComponent(name)}`, {headers: {'X-Remix-Token': document.querySelector('meta[name=remix-token]').content}});
    if (!response.ok) throw new Error('Artifact unavailable or expired');
    const url = URL.createObjectURL(await response.blob()), a = node('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  renderSettingsForms() {
    const settings = JSON.parse($('settings-json').value);
    const opts = {getSettings: () => JSON.parse($('settings-json').value), onChange: draft => { $('settings-json').value = json(parkSettings(draft)); }};
    if ($('validation-settings-editor')) renderValidationSettings($('validation-settings-editor'), this.settingsRecord.validation_warnings || [], opts);
    if ($('endpoint-settings-editor')) renderProjectSettings($('endpoint-settings-editor'), settings, opts);
    if ($('review-settings-editor')) renderGenerationSettings($('review-settings-editor'), settings, {...opts, loadDefaultChecklists: async () => (await api('/settings/default-review-checklists')).review_checklists, loadDefaultPrompts: async () => api('/generation/prompts/default'), footer: this.reviewFoot ||= $('review-settings-foot')});
  }
  async openSettings() {
    await this.loadSettings(); this.settingsDraftRevision = this.settingsRecord.revision; $('settings-json').value = json(parkSettings(this.settings)); this.renderToolSettings(); this.renderSettingsForms();
    await this.renderLogUsage(); $('settings-dialog').showModal();
  }
  async renderLogUsage() {
    const usage = await api('/logs/usage'), stat = (value, label) => { const s = node('div', undefined, 'stat-tile'); s.append(node('span', value, 'stat-value num'), node('span', label, 'stat-label')); return s; };
    const mib = value => (value / 1048576).toFixed(value >= 1048576 ? 1 : 2) + ' MiB';
    $('log-usage').replaceChildren(stat(String(usage.entries), 'Retained log lines'), stat(mib(usage.bytes || 0), 'Used'), stat(usage.cap_mib === null || usage.cap_mib === undefined ? 'Unlimited' : usage.cap_mib + ' MiB', 'Cache limit'));
  }
  async saveSettings(dialog) {
    await api('/settings', 'PUT', {settings: restoreSettings(JSON.parse($('settings-json').value)), expected_revision: this.settingsDraftRevision ?? this.settingsRecord.revision});
    await this.loadSettings(); await this.refreshSelected(); this.preview.invalidate(); this.schedulePreview(); if (dialog) $(dialog).close(); message('Project settings saved. New runs use these settings; existing runs retain their frozen configuration.');
  }
  async openReview() {
    // Keep unsaved review-settings edits when returning to the tab; otherwise show the saved settings.
    const dirty = $('settings-json').value && JSON.stringify(stable(JSON.parse($('settings-json').value))) !== JSON.stringify(stable(parkSettings(this.settings)));
    if (dirty) return;
    await this.loadSettings(); this.settingsDraftRevision = this.settingsRecord.revision; $('settings-json').value = json(parkSettings(this.settings)); this.renderSettingsForms();
  }
  async startReview({bulk = false} = {}) {
    if (!bulk && (!this.id || this.isDirty())) throw new Error('Save or reload the selected draft before review.');
    $('trajectory-menu')?.removeAttribute('open');
    const r = await api('/reviews', 'POST', {mode: 'standard', ...(bulk ? {query: this.query()} : {trajectory: this.id, expected_revision: this.record.revision})});
    this.reviewJob = r.job_id; this.ownedBulk.add(r.job_id);
    message('Review running… Progress also appears in Jobs.');
  }
  async sendToAssistant() {
    if (!this.id) return;
    if (!this.settings.endpoint_roles?.assistant) throw new Error('Assign the Assistant endpoint role in Project Settings first.');
    if (this.record?.owner) throw new Error('A job is working on this trajectory. Wait for it to settle or detach it.');
    if (this.isDirty()) throw new Error('Save or reload your draft first, so the agent edits what you see.');
    this.assistantQueue.clear(this.id); // Before the call, so a failed send isn't retried on every poll.
    const r = await api(`/trajectories/${this.id}/assistant`, 'POST', {expected_revision: this.record.revision});
    this.ownedBulk.add(r.job_id);
    message(`Sent to ${this.settings.endpoint_roles.assistant}. Follow it in Jobs; its answers and proposed changes appear here.`);
  }
  /** Apply builds on the saved trajectory, so save what was just typed first. */
  async saveBeforeProposal() {
    await this.autoSave.flush();
    if (this.hasUnsavedChanges()) throw new Error('Your latest edit could not be saved yet. Fix it or reload the saved source, then apply.');
  }
  async proposalApplied(result) {
    this.history([result.delta]);
    await this.refreshSelected(); await this.comments.load(this.id); await this.listing();
    message(`Applied the proposed changes${result.resolved ? ` and resolved ${result.resolved} comment${result.resolved === 1 ? '' : 's'}` : ''}. Undo reverts them.`);
  }
  /** "Request changes" on a proposal: a trajectory comment, sent to the assistant at once. */
  async requestProposalChanges(text) {
    await api(`/trajectories/${this.id}/comments`, 'POST', {target: {kind: 'trajectory'}, body: text});
    await this.comments.load(this.id);
    await this.sendToAssistant();
  }
  /** Auto-send: while a job owns the trajectory, queue the send instead of refusing it. */
  async autoSendToAssistant() {
    if (this.id && this.record?.owner) {
      this.assistantQueue.hold(this.id);
      return message('A job is working on this trajectory. Your comment is queued and goes to the assistant when it settles.');
    }
    await this.sendToAssistant();
  }
  /** Send a queued trajectory once its owner is gone and the draft is saved. */
  async flushAssistantQueue() {
    const id = this.id;
    if (!id || !this.assistantQueue.ready(id, {owner: this.record?.owner, dirty: this.isDirty(), comments: this.comments.items, auto: this.prefs.autoSendComments === true})) return;
    await this.sendToAssistant();
  }
  async openGenerationSettings() {
    await this.loadSettings(); this.settingsDraftRevision = this.settingsRecord.revision; $('settings-json').value = json(parkSettings(this.settings)); this.renderSettingsForms();
    const sampler = globalThis.samplerWorkbench; if (sampler) await sampler.open();
    const box = $('generation-run-controls'); box.replaceChildren(node('h3', 'New run'));
    const roles = this.settings.endpoint_roles || {}, roleList = node('div', undefined, 'role-list');
    for (const [role, name] of [['draft', 'Draft'], ['assembly', 'Assembly'], ['fast', 'Fast review'], ['critical', 'Critical review']]) {
      const row = node('div', undefined, 'role-row'); row.append(node('span', name, 'muted'), roles[role] ? tag(roles[role], 'accent', 'mono') : tag('Not configured', role === 'draft' ? 'danger' : 'faint')); roleList.append(row);
    }
    box.append(roleList);
    $('generation-endpoints-button').onclick = () => { if (JSON.stringify(stable(JSON.parse($('settings-json').value))) !== JSON.stringify(stable(parkSettings(this.settings))) && !confirm('Discard unsaved settings and open Project Settings?')) return; $('generation-settings-dialog').close(); return this.openSettings(); };
    const grid = node('div', undefined, 'form-grid two'); box.append(grid);
    const field = (text, id, value) => { const label = node('label', text), input = node('input'); input.type = 'number'; input.id = id; input.value = value; label.append(input); grid.append(label); return input; };
    const saved = loadRunInputs();
    const count = field('Attempts', 'generation-attempts', saved.attempts); count.min = '1'; count.max = '1000'; count.step = '1';
    const seed = field('Starting seed', 'generation-seed', saved.seed); seed.step = '1'; seed.title = '0 picks a random seed';
    persistInput(count, 'attempts'); persistInput(seed, 'seed');
    const pins = sampler?.pins || {}, pinBox = node('div', undefined, 'pin-list');
    pinBox.append(node('span', 'Pins', 'muted small'));
    if (Object.keys(pins).length) for (const [k, v] of Object.entries(pins)) pinBox.append(tag(`${k}: ${v}`, 'lilac'));
    else pinBox.append(node('span', 'None — the sampler uses saved matrix weights', 'small faint'));
    const barrierBox = node('fieldset', undefined, 'barrier-list'); barrierBox.append(node('legend', 'Phase barriers', 'muted small'));
    const barriers = {};
    for (const [gate, text, tip] of [
      ['assembly', 'Pause before Assembly', 'Every trajectory finishes Draft first; the job then pauses so you can switch models. Resume starts Assembly.'],
      ['fast', 'Pause before Fast review', 'Every trajectory is assembled first; the job then pauses. Resume starts Fast review.'],
      ['critical', 'Pause before Critical review', 'Every trajectory passes Fast review first; the job then pauses. Resume starts Critical review.'],
    ]) {
      const label = node('label'), box_ = node('input'); box_.type = 'checkbox'; box_.id = `generation-barrier-${gate}`; box_.checked = loadBarriers()[gate];
      box_.onchange = () => saveBarriers({[gate]: box_.checked}); label.dataset.tip = tip; label.append(box_, ` ${text}`);
      barriers[gate] = box_; barrierBox.append(label);
    }
    box.append(pinBox, barrierBox, node('p', 'Each run freezes its settings, prompts, matrix and pins when it starts.', 'note'));
    const status = node('p', '', 'status-line'); status.setAttribute('role', 'status'); status.dataset.status = '';
    const start = button('Start generation', async () => {
      if (JSON.stringify(stable(JSON.parse($('settings-json').value))) !== JSON.stringify(stable(parkSettings(this.settings)))) throw new Error('Save review settings before starting a run.');
      if (sampler?.dirty()) throw new Error('Save or discard the Workbench matrix draft before generating.');
      const n = Number(count.value); let first = Number(seed.value);
      if (!count.value || !Number.isInteger(n) || n < 1 || n > 1000 || !seed.value || !Number.isSafeInteger(first) || !Number.isSafeInteger(first + n - 1)) throw new Error('Supply 1–1000 attempts and a safe integer seed.');
      first = resolveSeed(first);
      start.disabled = true;
      try {
        const requests = Array.from({length:n}, (_, i) => ({seed:first+i, overrides:{...(sampler?.pins || {})}}));
        const run = await api('/generation/runs', 'POST', {requests, instructions: {}, barriers: Object.fromEntries(Object.entries(barriers).map(([gate, input]) => [gate, input.checked]))});
        status.textContent = `Started ${n} attempts. Job ${run.job_id}`;
        $('generation-settings-dialog').close(); await this.inspectJob(run.job_id); await this.listing();
      } finally { start.disabled = false; }
    }, 'primary'); start.id = 'generation-start'; box.append(start, status);
    $('generation-settings-dialog').showModal();
  }
  async openJobs() {
    await this.jobsView.renderList();
    if (!this.jobsView.selected && this.jobsView.renderedFirst) await this.jobsView.select(this.jobsView.renderedFirst);
  }
  renderJobBar(jobs) {
    const active = jobs.filter(job => ACTIVE_JOBS.has(job.state));
    reconcile($('job-status-bar'), active, job => job.id, job => {
      const chip = button('', () => this.inspectJob(chip.dataset.key), 'job-chip ghost sm');
      const dot = node('span', undefined, 'dot'), name = node('span', ''), bar = progressBar('');
      chip.append(dot, name, bar); chip.parts = {dot, name, bar}; return chip;
    }, (chip, job) => {
      setAttr(chip.parts.dot, 'class', `dot tone-${KIND_TONES[job.kind] || 'accent'}`);
      const progress = jobProgress(job), {value, total, unit} = progress;
      const name = kindName(job.kind, job.action);
      setText(chip.parts.name, name); chip.parts.bar.update(progress);
      setAttr(chip, 'aria-label', `${name} job ${job.state} ${value} of ${total}${unit ? ' ' + unit : ''}. Open in Jobs`);
      toggleClass(chip, 'paused', job.state === 'paused');
    });
    const badge = $('jobs-running-badge');
    setText(badge, active.length ? String(active.length) : ''); setAttr(badge, 'hidden', !active.length);
    setAttr(badge, 'class', 'badge ' + (active.some(j => j.state === 'paused') ? 'warning' : 'accent'));
  }
  async pollJobs() {
    const jobs = await api('/job-summaries');
    this.renderJobBar(jobs);
    const visible = new Set(jobs.map(j => j.id));
    const tracked = new Set([...this.ownedBulk].filter(id => !this.recorded.has(id)));
    if (this.reviewJob) tracked.add(this.reviewJob);
    for (const id of this.dialogs.keys()) if (this.jobsView.isOpen(id)) tracked.add(id);
    for (const id of tracked) if (!visible.has(id)) jobs.push(await api(`/jobs/${id}?include_config=false`));
    for (const job of jobs) {
      this.jobs.set(job.id, job);
      if (this.reviewJob === job.id) {
        const fullJob = await api(`/jobs/${job.id}?include_config=false`);
        const skipped = (fullJob.result_states || []).find(r => r.state === 'skipped')?.count || 0;
        if (['completed', 'cancelled'].includes(job.state)) { this.reviewJob = null; message(`Review ${job.state} · ${job.progress}/${job.total} complete · ${skipped} skipped`); }
        else if (job.state === 'paused') message('Review paused. Open it from the Jobs view to resume.');
      }
      if (this.ownedBulk.has(job.id) && job.kind === 'bulk' && ['completed', 'cancelled'].includes(job.state) && !this.recorded.has(job.id)) {
        const deltas = []; for (let offset = 0;; offset += 1000) { const part = await api(`/jobs/${job.id}/history?limit=1000&offset=${offset}`); deltas.push(...part); if (part.length < 1000) break; }
        this.recorded.add(job.id); this.history(deltas); await this.listing();
      }
      if (this.jobsView.isOpen(job.id)) await this.jobsView.refresh(job.id);
    }
  }
  /** Run after the selected record is refreshed, so its owner is current. */
  async updateAssistantStatus() {
    this.assistantStatus ||= new AssistantStatus({host: $('assistant-status'), api});
    const owner = isAssistantOwner(this.record?.owner, this.jobs) ? this.record.owner : null;
    await this.assistantStatus.update(owner, owner ? this.jobs.get(owner).age : 0);
  }
  async inspectJob(id) {
    showView('jobs-view');
    await this.jobsView.select(id);
    await this.jobsView.renderList();
  }
  async refreshJob(id) { return this.jobsView.refresh(id); }
  renderToolSettings(selected = '') {
    const box = $('tool-schema-editor'); box.replaceChildren();
    box.className = 'settings-section';
    box.append(node('h3', 'Synthetic tools'), node('p', 'Tools an agent can call in authored and generated trajectories. They are contracts only: the Draft and Assembly models write each call and its result, and nothing runs. The selector and writers read each schema, including its description. Forms edit the settings draft; Save settings validates it.', 'settings-help'));
    const data = JSON.parse($('settings-json').value); data.tools ||= {};
    const write = () => { const current = JSON.parse($('settings-json').value); current.tools = data.tools; $('settings-json').value = json(current); };
    const control = (label, value = '') => { const wrap = node('label', label), n = node('input'); n.value = value; n.setAttribute('aria-label', label); wrap.append(n); return [wrap, n]; };
    const top = node('div', undefined, 'inline-form');
    const select = node('select'); select.setAttribute('aria-label', 'Project tool');
    for (const [id, tool] of Object.entries(data.tools)) { const opt = node('option', tool.name + ' · ' + id); opt.value = id; select.append(opt); }
    if (selected && data.tools[selected]) select.value = selected;
    select.onchange = () => this.renderToolSettings(select.value);
    const pick = node('label', 'Tool'); pick.append(select);
    const [idLabel, ident] = control('New tool ID'); ident.placeholder = 'search_docs';
    top.append(...(Object.keys(data.tools).length ? [pick] : []), idLabel, button('Add tool to settings draft', () => {
      const id = ident.value.trim(); if (!/^[a-z][a-z0-9_-]*$/.test(id) || data.tools[id]) throw new Error('Supply a unique tool ID with letters, digits, underscores or hyphens.');
      data.tools[id] = {name: id, input_schema: {type:'object',properties:{}}}; write(); this.renderToolSettings(id);
    }, 'sm'));
    box.append(top, ...toolImportControls((tools, replace) => {
      const summary = describeMerge(mergeTools(data.tools, tools, {replace})); write();
      this.renderToolSettings(select.value || Object.keys(data.tools)[0]); $('tool-schema-editor').querySelector('.tool-import')?.nextElementSibling?.replaceChildren(summary);
      return summary;
    }));
    const tool = data.tools[select.value];
    if (!tool) { box.append(node('div', 'No tools defined yet. Add one by ID above.', 'empty')); return; }
    const card = node('section', undefined, 'settings-card'); box.append(card);
    const cardHead = node('div', undefined, 'row'); cardHead.append(node('h4', select.value, 'mono grow'));
    cardHead.append(button('Remove tool from settings draft', () => {
      if (!confirm('Remove this tool definition? Existing source calls are retained and will show diagnostics.')) return;
      delete data.tools[select.value]; write(); this.renderToolSettings();
    }, 'sm danger'));
    card.append(cardHead);
    const [nameLabel, name] = control('Tool display name', tool.name); name.oninput = () => { tool.name = name.value; write(); }; card.append(nameLabel);
    const schema = tool.input_schema;
    if (schema.type !== 'object') { card.append(node('p', 'This input schema uses advanced JSON. Object schemas support the field editor.', 'note')); return; }
    schema.properties ||= {};
    const table = node('table', undefined, 'data'), head = node('tr'), body = node('tbody');
    for (const text of ['Field', 'Type', 'Required', '']) head.append(node('th', text));
    const thead = node('thead'); thead.append(head); table.append(thead, body);
    for (const [key, spec] of Object.entries(schema.properties)) {
      const row = node('tr'), required = node('input'); required.type = 'checkbox'; required.checked = (schema.required || []).includes(key); required.setAttribute('aria-label', 'Required tool field ' + key);
      required.onchange = () => { schema.required = (schema.required || []).filter(x => x !== key); if (required.checked) schema.required.push(key); if (!schema.required.length) delete schema.required; write(); };
      const req = node('td'), remove = node('td'); req.append(required);
      const removeButton = button('Remove', () => {
        delete schema.properties[key]; if (schema.required) schema.required = schema.required.filter(x => x !== key); write(); this.renderToolSettings(select.value);
      }, 'sm ghost'); removeButton.setAttribute('aria-label', 'Remove tool field ' + key); remove.append(removeButton);
      row.append(node('td', key, 'mono'), node('td', spec.type || 'advanced schema', 'muted'), req, remove); body.append(row);
    }
    if (Object.keys(schema.properties).length) card.append(table); else card.append(node('p', 'No input fields yet.', 'muted small'));
    const [fieldLabel, fieldId] = control('New tool field ID'), type = node('select'); type.setAttribute('aria-label', 'New tool field type');
    for (const value of ['string','number','integer','boolean','object','array']) { const opt = node('option', value); opt.value = value; type.append(opt); }
    const typeLabel = node('label', 'Type'); typeLabel.append(type);
    const addRow = node('div', undefined, 'inline-form');
    addRow.append(fieldLabel, typeLabel, button('Add tool field', () => {
      const key = fieldId.value.trim(); if (!key || Object.hasOwn(schema.properties, key)) throw new Error('Supply a unique field ID.');
      schema.properties[key] = type.value === 'array' ? {type:'array',items:{type:'string'}} : {type:type.value};
      write(); this.renderToolSettings(select.value);
    }, 'sm'));
    card.append(addRow, node('p', 'Nested constraints, custom schema keywords and non-object schemas stay editable in Advanced JSON.', 'settings-help'));
  }
  async init() {
    window.addEventListener?.('beforeunload', e => { if (this.isDirty()) { e.preventDefault(); e.returnValue = ''; } });
    window.addEventListener?.('pagehide', () => { this.stopped = true; clearTimeout(this.pollTimer); clearTimeout(this.validationTimer); this.preview.invalidate(); if (this.playing) clearInterval(this.playing); });
    // Started elsewhere (e.g. the Sampler): track it without leaving the current view.
    document.addEventListener('graph-job-started', e => this.jobsView.select(e.detail).then(() => message('Job started. Follow it in Jobs or from the status bar.')).catch(message));
    bindViewTabs();
    this.assistantQueue = new AssistantQueue();
    this.windowsPanel = new WindowsPanel({$, draft: () => this.draft, report: message,
      onChange: removed => this.windowsChanged(removed)});
    this.comments = new CommentsView({api, report: message, inspect: (kind, id) => this.inspectTarget(kind, id),
      autoSend: () => this.prefs.autoSendComments === true, onOpened: () => this.autoSendToAssistant(),
      proposed: () => this.proposal?.pending || new Set(),
      els: {target: $('comment-target'), body: $('comment-body'), add: $('comment-add'), list: $('comment-list'), count: $('comments-count'), send: $('comment-send')},
      onChange: () => {
        if (this.draft) this.renderTimeline();
        const items = this.comments.items, row = this.lastItems?.find(i => i.id === this.id);
        const unresolved = items.filter(c => c.status !== 'resolved').length, review = items.filter(c => c.status === 'addressed').length;
        if (row && (row.comments !== unresolved || row.review !== review)) this.listing().catch(message);
      }});
    this.proposal = new ProposalView({api, report: message, host: $('assistant-proposal'),
      revision: () => this.record.revision, locked: () => !!this.record?.owner,
      ready: () => this.saveBeforeProposal(), onChange: () => this.comments.render(),
      onApplied: result => this.proposalApplied(result), requestChanges: text => this.requestProposalChanges(text)});
    this.metricsEditor = new MetricsEditor({api, report: message, beforeSave: () => this.autoSave.flush(),
      onSaved: view => { if (view.delta) this.history([view.delta]); return this.refreshSelected(); },
      onView: (view, state) => {
        renderComplexity($('complexity'), view, state.locked);
        if (view !== this.metricsView) { this.metricsView = view; this.renderDiagnostics(); }
      },
      shownElsewhere: ['complexity'],
      els: {list: $('metrics-editor-list'), note: $('metrics-editor-note'), unset: $('metrics-only-unset')}});
    on('complexity', 'change', () => this.metricsEditor.save('complexity', $('complexity').value || undefined));
    $('tab-metrics').addEventListener('click', () => this.metricsEditor.load().catch(message));
    document.addEventListener('studio-view', e => {
      if (e.detail === 'jobs-view') this.openJobs().catch(message);
      if (e.detail === 'metrics-view') this.loadMetrics().catch(message);
      if (e.detail === 'review-view') this.openReview().catch(message);
    });
    await this.loadSettings(); $('view-mode').value = this.prefs.mode; $('timeline-zoom').value = this.prefs.zoom; $('preview-debounce').value = this.prefs.debounce; $('show-random-tools').checked = this.prefs.showRandomTools; $('auto-send-comments').checked = this.prefs.autoSendComments === true; $('hide-closed-windows').checked = this.prefs.hideClosedWindows === true;
    on('new-sample', 'click', async () => { const r = await api('/trajectories', 'POST', {format: 'kidra-trajectory-graph/1', id: crypto.randomUUID(), windows: {reasoning: {purpose: 'reasoning', agent_writable: true}}, events: {}, metadata: {stage: -3}}); await this.select(r.source.id); });
    on('duplicate-sample', 'click', () => this.duplicate());
    on('reload-source', 'click', () => this.id && this.select(this.id, true));
    on('delete-sample', 'click', async () => { if (!this.id || !confirm('Delete this trajectory? Undo can restore it during this session.')) return; const delta = await api(`/trajectories/${this.id}`, 'DELETE', {expected_revision: this.record.revision}); this.history([delta]); this.drafts.delete(this.id); this.id = null; this.draft = null; this.record = null; this.event = null; this.editors.clear(); this.comments.load(null); this.proposal.load(null); this.windowsPanel.render(); this.renderDirty(); this.renderEvent(); this.renderTimeline(); await this.listing(); });
    on('event-picker', 'change', e => this.selectEvent(e.target.value));
    on('event-apply', 'click', () => this.applyEvent());
    on('event-name', 'change', () => this.renameCurrentEvent());
    on('new-event', 'click', () => {
      if (!this.draft) return; const id = $('new-event-id').value || 'event-' + crypto.randomUUID().slice(0, 8); if (this.draft.events[id]) throw new Error('Event ID exists');
      this.stashEditor(); this.draft.events[id] = {kind: 'agent_text', window: Object.keys(this.draft.windows)[0] || 'reasoning', after: [], content: '', close_window: false, supervision: {masked: false, weight: 1}};
      this.event = id; this.renderDraft(); this.renderEvent(); this.changed();
    });
    on('delete-event', 'click', () => { if (!this.event) return; delete this.draft.events[this.event]; this.editors.delete(this.event); this.event = Object.keys(this.draft.events)[0]; this.renderDraft(); this.renderEvent(); this.changed(); });
    for (const id of ['call-id', 'tool-id']) for (const type of ['input', 'change']) on(id, type, () => this.markRequiredIds());
    on('event-kind', 'change', () => { $('masked').checked = ['external_text', 'tool_schema', 'tool_response', 'chat_message'].includes($('event-kind').value); this.kindControls(); this.renderArguments(); this.renderToolPicker(); });
    on('content-mode', 'change', () => { this.contentModes.remember(this.id, this.event, $('content-mode').value); this.stashEditor(); this.renderArguments(); this.renderToolPicker(); });
    on('event-content', 'input', () => { this.stashEditor(); this.applyEvent(); this.renderToolPicker(); });
    for (const id of ['event-window', 'call-id', 'tool-id', 'chat-speaker', 'delivery', 'weight', 'chain', 'replace-body', 'close-window', 'masked', 'begin', 'end', 'echo-chat']) on(id, 'change', () => { this.stashEditor(); this.applyEvent(); this.renderToolPicker(); });
    on('tool-id', 'change', () => this.renderArguments());
    on('dependency-add', 'click', () => this.addDependency($('dependency-picker').value));
    on('metadata-apply', 'click', () => { const m = JSON.parse($('metadata-json').value); if (!m || Array.isArray(m) || typeof m !== 'object') throw new Error('Metadata must be an object'); this.draft.metadata = m; this.renderDraft(); this.changed(); });
    for (const id of ['stage', 'split']) on(id, 'change', () => { this.draft.metadata ||= {}; if (id === 'split' && !$(id).value) delete this.draft.metadata.split; else this.draft.metadata[id] = id === 'stage' ? Number($(id).value) : $(id).value; this.renderDraft(); this.changed(); });
    on('source-apply', 'click', async () => { const draft = JSON.parse($('source-json').value); if (draft.id !== this.id || !draft.events || !draft.windows) throw new Error('Keep source UUID and graph maps'); const validated = await api('/diagnostics', 'POST', draft); this.draft = validated.source; this.diagnostics = validated.diagnostics; this.editors.clear(); this.event = Object.keys(draft.events)[0]; this.renderDraft(); this.renderEvent(); this.changed(); });
    for (const id of ['sample-search', 'search-stage', 'search-split', 'search-tool', 'search-comments']) on(id, id === 'sample-search' || id === 'search-tool' ? 'input' : 'change', () => { this.offset = 0; return this.listing(); });
    on('sample-page-prev', 'click', () => { this.offset = Math.max(0, this.offset - 50); return this.listing(); }); on('sample-page-next', 'click', () => { this.offset += 50; return this.listing(); });
    on('preferences-button', 'click', () => $('preferences-dialog').showModal());
    on('preferences-save', 'click', () => { const delay = Number($('preview-debounce').value); if (markInvalid($('preview-debounce'), $('preview-debounce').value === '' || !Number.isFinite(delay) || delay < 0 || delay > 10000)) throw new Error('Debounce must be 0–10000 ms'); this.prefs.profile = $('preview-profile').value; this.prefs.debounce = delay; this.prefs.showRandomTools = $('show-random-tools').checked; this.prefs.autoSendComments = $('auto-send-comments').checked; this.prefs.hideClosedWindows = $('hide-closed-windows').checked; this.comments.render(); this.savePreferences(); this.renderEvent(); this.renderTimeline(); this.preview.invalidate(); this.frames = null; this.schedulePreview(); $('preferences-dialog').close(); });
    on('timeline-zoom', 'input', () => { this.prefs.zoom = Number($('timeline-zoom').value); this.savePreferences(); this.renderTimeline(); });
    on('view-mode', 'change', () => { this.prefs.mode = $('view-mode').value; this.savePreferences(); this.preview.invalidate(); this.frames = null; this.position = 0; this.renderTimeline(); this.schedulePreview(); });
    const scrub = value => { this.followPreviewEnd = false; this.position = Number(value); this.renderTimeline(); };
    on('tick-number', 'change', e => scrub(e.target.value));
    on('first-tick', 'click', () => scrub(0)); on('prev-tick', 'click', () => scrub(this.position - 1)); on('next-tick', 'click', () => scrub(this.position + 1)); on('last-tick', 'click', () => scrub(this.maxPosition));
    const lastTick = () => this.maxPosition || 0;
    const pausePlayback = () => { clearInterval(this.playing); this.playing = null; $('play').textContent = 'Play'; };
    on('play', 'click', () => {
      if (this.playing) return pausePlayback();
      if (this.position >= lastTick()) scrub(0); // Pressing Play at the end restarts from the beginning.
      $('play').textContent = 'Pause';
      // Playback does not loop: it pauses on reaching the last tick.
      this.playing = setInterval(() => { scrub(Math.min(this.position + 1, lastTick())); if (this.position >= lastTick()) pausePlayback(); }, 500);
    });
    on('settings-button', 'click', () => this.openSettings());
    if ($('generation-settings-button')) on('generation-settings-button', 'click', () => this.openGenerationSettings());
    if ($('generation-sampler-button')) on('generation-sampler-button', 'click', () => { $('generation-settings-dialog').close(); $('sampler-tab').click(); });
    on('review-settings-save', 'click', () => this.saveSettings());
    on('settings-json', 'change', () => { this.renderToolSettings(); this.renderSettingsForms(); });
    on('settings-save', 'click', () => this.saveSettings('settings-dialog'));
    on('logs-clear', 'click', async () => { await api('/logs', 'DELETE'); await this.renderLogUsage(); message('Log cache cleared.'); });
    on('diagnostics-summary', 'click', () => $('tab-problems').click());
    on('import-button', 'click', () => $('file-input').click()); on('file-input', 'change', async () => { const file = $('file-input').files[0]; if (!file) return; const r = await api('/imports', 'POST', {text: await file.text()}); await this.inspectJob(r.job_id); $('file-input').value = ''; });
    on('export-button', 'click', () => $('export-dialog').showModal());
    on('export-start', 'click', async () => { const r = await api('/exports', 'POST', {profile: $('export-profile').value || null, options: {compiled: $('export-format').value === 'compiled', trajectories_per_file: Number($('export-shard').value), minimum_stage: $('export-stage').value === '' ? null : Number($('export-stage').value), include_stale: $('export-stale').checked, exclude_missing_split: $('export-exclude').checked, metadata: $('export-metadata').value}}); $('export-dialog').close(); await this.inspectJob(r.job_id); });
    on('bulk-action', 'change', () => { $('bulk-value-label').hidden = !['stage', 'split'].includes($('bulk-action').value); });
    on('bulk-start', 'click', async () => { const action = $('bulk-action').value; if (action === 'export') return this.exportMatches(); if (action === 'review') return this.startReview({bulk: true}); const config = action === 'delete' ? {} : {value: action === 'stage' ? Number($('bulk-value').value) : $('bulk-value').value}; const r = await api('/bulk', 'POST', {query: this.query(), action, config}); this.ownedBulk.add(r.job_id); await this.inspectJob(r.job_id); });
    this.conflictResolver = new ConflictResolver($('conflict-dialog'));
    on('dirty-indicator', 'click', () => this.resolveConflicts());
    on('dirty-indicator', 'keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && this.saveConflicts()) { e.preventDefault(); return this.resolveConflicts(); } });
    on('undo-edit', 'click', () => this.restore(true)); on('redo-edit', 'click', () => this.restore(false));
    on('review-source', 'click', () => this.startReview());
    on('comment-send', 'click', () => this.sendToAssistant());
    on('export-source', 'click', () => this.exportSource());
    on('metrics-refresh', 'click', () => this.loadMetrics());
    on('metrics-calculate', 'click', async () => {
      const r = await api('/metrics/calculate-missing', 'POST', {query: this.query()});
      this.ownedBulk.add(r.job_id); await this.inspectJob(r.job_id);
    });
    document.querySelectorAll('[data-close]').forEach(n => n.onclick = () => n.closest('dialog').close());
    this.renderTimeline(); this.renderDirty(); $('lock-slot').hidden = true;
    await this.listing(); if (this.lastItems?.length) await this.select(this.lastItems[0].id); await this.pollJobs(); await this.updateAssistantStatus();
    const poll = async () => { if (this.stopped) return; try { await this.pollChanges(); } catch (e) { message(e); } if (!this.stopped) this.pollTimer = setTimeout(poll, 1500); }; this.pollTimer = setTimeout(poll, 1500);
  }
  async pollChanges() {
    await this.pollJobs();
    const changes = await api(`/changes?after=${this.cursor || 0}`);
    if (changes.items.length || changes.resync) {
      this.cursor = changes.items.at(-1)?.cursor || changes.latest;
      if (changes.resync || changes.items.some(x => x.kind === 'settings')) { await this.loadSettings(); this.preview.invalidate(); this.frames = null; this.schedulePreview(); }
      for (const change of changes.items) if (change.entity_id !== this.id) {
        const cached = this.drafts.get(change.entity_id);
        if (cached && JSON.stringify(cached.draft) === JSON.stringify(cached.record.source) && !cached.editors.size) this.drafts.delete(change.entity_id);
      }
      await this.listing();
    }
    await this.refreshSelected();
    await this.updateAssistantStatus();
    if (this.id) await this.comments.load(this.id);
    if (this.id) await this.proposal.load(this.id);
    await this.flushAssistantQueue();
    if (currentView() === 'jobs-view') await this.jobsView.renderList();
  }
  async loadMetrics() {
    const query = this.query(), population = await api('/metrics/population', 'POST', {query});
    const selected = this.id ? await api(`/metrics/trajectories/${this.id}`) : null;
    const filters = Object.keys(query).length;
    renderMetrics(population, selected, filters ? `Current search · ${filters} filter${filters === 1 ? '' : 's'}` : 'Whole dataset');
  }
  savePreferences() { localStorage.setItem('graph-preferences', JSON.stringify(this.prefs)); }
}

if (typeof document !== 'undefined' && $('workspace')) { const ui = new StudioUI(); globalThis.graphStudio = ui; ui.init().catch(message); }
