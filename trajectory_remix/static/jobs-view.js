/* Jobs view: a job list beside one job's progress, controls, logs and files.
   Everything updates in place so polling never flashes or resets scroll. */
import {renderUsage} from './usage-view.js';
import {kindName, KIND_TONES, JOB_TONES, makeTabs, node, progressBar, reconcile, setAttr, setText, shortId, stateWord, tag, toggleClass} from './ui-kit.js';

const $ = id => document.getElementById(id);
const PAGE = 50;
const ACTIVE = new Set(['running', 'paused', 'queued']);
const LOG_LEVELS = new Set(['info', 'success', 'warning', 'error']);
const MAX_LOG_LINES = 2000;

/** One log line, coloured by its status level (older servers send none: plain info). */
export function logLine(text, level) {
  const el = node('span', text + '\n', `log-line level-${LOG_LEVELS.has(level) ? level : 'info'}`);
  el.dataset.level = LOG_LEVELS.has(level) ? level : 'info';
  return el;
}

export class JobsView {
  /** deps: {api, report, download, openTrajectory, afterDetach, isVisible} */
  constructor(deps) {
    this.deps = deps; this.panels = new Map(); this.offset = 0; this.selected = null;
    if (!$('jobs-list')) return;
    $('jobs-prev').onclick = () => this.page(-PAGE);
    $('jobs-next').onclick = () => this.page(PAGE);
    $('jobs-refresh').onclick = () => this.guard(async () => { await this.renderList(); if (this.selected) await this.refresh(this.selected); });
  }
  guard(run) { return Promise.resolve().then(run).catch(error => this.deps.report(error)); }
  page(delta) { this.offset = Math.max(0, this.offset + delta); return this.guard(() => this.renderList()); }
  isOpen(id) { return this.selected === id && this.deps.isVisible(); }

  async renderList() {
    const jobs = await this.deps.api(`/job-summaries?limit=${PAGE}&offset=${this.offset}`);
    const list = $('jobs-list'); this.renderedFirst = jobs[0]?.id;
    if (!jobs.length) list.replaceChildren(node('div', this.offset ? 'No jobs on this page.' : 'No jobs yet. Generation, review, import and export jobs appear here.', 'empty'));
    else reconcile(list, jobs, job => job.id, job => this.row(job), (el, job) => el.update(job));
    setText($('jobs-page-label'), jobs.length ? `${this.offset + 1}–${this.offset + jobs.length}` : '');
    setAttr($('jobs-prev'), 'disabled', this.offset === 0); setAttr($('jobs-next'), 'disabled', jobs.length < PAGE);
  }

  row(job) {
    const el = node('button', undefined, 'job-row'); el.type = 'button'; el.setAttribute('role', 'option');
    const dot = node('span', undefined, 'dot'), name = node('span', '', 'job-row-name truncate'), state = tag('', 'muted');
    const id = node('span', shortId(job.id), 'mono small faint'), bar = progressBar('');
    const top = node('span', undefined, 'job-row-top'); top.append(dot, name, state);
    const bottom = node('span', undefined, 'job-row-bottom'); bottom.append(id, bar);
    el.append(top, bottom); el.onclick = () => this.guard(() => this.select(job.id));
    el.dataset.tip = job.id;
    el.update = j => {
      setAttr(dot, 'class', `dot tone-${KIND_TONES[j.kind] || 'accent'}`);
      setText(name, kindName(j.kind)); setText(state, stateWord(j.state)); setAttr(state, 'class', `tag tone-${JOB_TONES[j.state] || 'muted'}`);
      bar.update({value: j.progress, total: j.total, state: j.state});
      setAttr(el, 'aria-selected', String(j.id === this.selected)); toggleClass(el, 'selected', j.id === this.selected);
    };
    return el;
  }

  async select(id) {
    this.selected = id;
    for (const row of $('jobs-list')?.children || []) { toggleClass(row, 'selected', row.dataset.key === id); setAttr(row, 'aria-selected', String(row.dataset.key === id)); }
    if (!this.panels.has(id)) this.panels.set(id, this.buildPanel(id));
    const panel = this.panels.get(id), host = $('job-detail');
    if (host && host.firstElementChild !== panel.root) host.replaceChildren(panel.root);
    await this.refresh(id);
  }

  buildPanel(id) {
    const v = {id, cursor: 0, attemptOffset: 0, signatures: {}};
    v.root = node('div', undefined, 'job-panel');
    const head = node('div', undefined, 'widget-head');
    v.kindDot = node('span', undefined, 'dot'); v.title = node('h2', 'Job');
    const idTag = node('span', shortId(id), 'mono small muted'); idTag.dataset.tip = id; idTag.tabIndex = 0;
    v.stateTag = tag('', 'muted');
    v.controls = node('div', undefined, 'widget-actions');
    v.buttons = {};
    for (const [action, text, cls] of [['pause', 'Pause', ''], ['resume', 'Resume', ''], ['cancel', 'Cancel', 'danger']]) {
      const b = node('button', text, `sm ${cls}`); b.type = 'button';
      b.onclick = () => this.guard(async () => { await this.deps.api(`/jobs/${id}/${action}`, 'POST'); await this.refresh(id); });
      v.buttons[action] = b; v.controls.append(b);
    }
    v.reviews = node('button', 'Start reviews now', 'sm'); v.reviews.type = 'button'; v.reviews.hidden = true;
    v.reviews.dataset.tip = 'Phase barrier: assembled trajectories wait here for the review models. Start reviews now instead of waiting for every trajectory; unfinished ones keep generating when their endpoint is back.';
    v.reviews.onclick = () => this.guard(async () => { await this.deps.api(`/jobs/${id}/phase/review`, 'POST'); await this.refresh(id); });
    v.controls.append(v.reviews);
    v.clear = node('button', 'Clear job', 'sm danger'); v.clear.type = 'button'; v.clear.hidden = true;
    v.clear.dataset.tip = 'Remove this finished job, its logs and its files from the list.';
    v.clear.onclick = () => this.guard(async () => {
      if (!confirm('Clear this job? Its record, logs and downloadable files will be removed.')) return;
      await this.deps.api(`/jobs/${id}`, 'DELETE'); this.panels.delete(id);
      if (this.selected === id) { this.selected = null; $('job-detail')?.replaceChildren(); }
      await this.renderList();
    });
    v.started = node('span', '', 'small faint truncate');
    head.append(v.kindDot, v.title, idTag, v.stateTag, v.started, v.controls, v.clear);

    const summary = node('div', undefined, 'job-summary');
    v.progress = progressBar('Progress');
    v.stats = node('div', undefined, 'job-stats');
    v.endpoints = node('div', undefined, 'job-endpoints');
    summary.append(v.progress, v.stats, v.endpoints);

    v.logs = node('pre', '', 'job-logs'); v.logs.setAttribute('aria-label', 'Job log'); v.logs.tabIndex = 0;
    const logPanel = node('div', undefined, 'job-tab'), logBar = node('div', undefined, 'job-toolbar');
    const more = node('button', 'Load more', 'sm ghost'); more.type = 'button'; more.onclick = () => this.guard(() => this.refresh(id));
    const exportLogs = node('button', 'Export retained logs', 'sm'); exportLogs.type = 'button';
    exportLogs.onclick = () => this.guard(async () => { const r = await this.deps.api(`/logs/${id}/export`, 'POST'); await this.renderList(); await this.select(r.job_id); });
    const clearLogs = node('button', 'Clear these logs', 'sm danger'); clearLogs.type = 'button';
    clearLogs.onclick = () => this.guard(async () => { await this.deps.api(`/logs?job=${id}`, 'DELETE'); v.logs.textContent = ''; v.cursor = 0; await this.refresh(id); });
    v.logCount = node('span', '', 'grow small muted');
    logBar.append(v.logCount, more, exportLogs, clearLogs); logPanel.append(logBar, v.logs);

    v.attempts = node('div', undefined, 'job-tab');
    v.results = node('div', undefined, 'job-tab');
    v.files = node('div', undefined, 'job-tab');
    v.usage = node('div', undefined, 'job-tab');
    v.tabs = makeTabs([
      {id: 'logs', text: 'Logs', panel: logPanel}, {id: 'attempts', text: 'Attempts', panel: v.attempts},
      {id: 'usage', text: 'Usage', panel: v.usage},
      {id: 'results', text: 'Results', panel: v.results}, {id: 'files', text: 'Files', panel: v.files}
    ], 'Job sections');
    v.tabs.root.classList.add('job-tabs');
    v.root.append(head, summary, v.tabs.root);
    return v;
  }

  async refresh(id) {
    const v = this.panels.get(id); if (!v) return;
    const j = await this.deps.api(`/jobs/${id}?include_config=false`);
    setAttr(v.kindDot, 'class', `dot tone-${KIND_TONES[j.kind] || 'accent'}`);
    setText(v.title, `${kindName(j.kind)} job`);
    setText(v.stateTag, stateWord(j.state)); setAttr(v.stateTag, 'class', `tag tone-${JOB_TONES[j.state] || 'muted'}`);
    setAttr(v.root, 'data-state', `${j.kind} · ${j.state}`);
    setAttr(v.buttons.pause, 'disabled', j.state !== 'running'); setAttr(v.buttons.resume, 'disabled', j.state !== 'paused');
    setAttr(v.buttons.cancel, 'disabled', !['running', 'paused'].includes(j.state));
    setAttr(v.controls, 'hidden', !ACTIVE.has(j.state));
    const phase = j.kind === 'run' && ACTIVE.has(j.state) ? await this.deps.api(`/jobs/${id}/phase`) : null;
    setAttr(v.reviews, 'hidden', !(phase?.enabled && phase.phase === 'generation'));
    setAttr(v.clear, 'hidden', ACTIVE.has(j.state));
    if (j.created) setText(v.started, 'Started ' + new Date(j.created * 1000).toLocaleString());
    v.progress.update({value: j.progress, total: j.total, state: j.state, status: stateWord(j.state)});
    this.renderStats(v, j);
    setAttr(v.tabs.tab('attempts'), 'hidden', j.kind !== 'run');
    setAttr(v.tabs.tab('results'), 'hidden', j.kind !== 'bulk');
    setAttr(v.tabs.tab('files'), 'hidden', !(j.state === 'completed' && j.artifact));
    if (v.tabs.tab(v.tabs.selected()).hidden) v.tabs.select('logs');
    await this.appendLogs(v);
    if (j.kind === 'run') await this.renderAttempts(v);
    if (j.kind === 'bulk') await this.renderResults(v);
    if (j.state === 'completed' && j.artifact && !v.signatures.files) await this.renderFiles(v, j);
    await this.renderUsage(v, j);
    return j;
  }

  /** Model jobs only. A server without the usage route simply hides the tab. */
  async renderUsage(v, j) {
    const wanted = j.kind === 'run' || j.kind === 'bulk';
    let report = null;
    if (wanted) { try { report = await this.deps.api(`/jobs/${j.id}/usage`); } catch { report = null; } }
    setAttr(v.tabs.tab('usage'), 'hidden', !report);
    if (report) v.signatures.usage = renderUsage(v.usage, report, v.signatures.usage);
  }

  renderStats(v, j) {
    const items = [...(j.attempt_states || []).map(r => [`${stateWord(r.state)}${r.outcome ? ' · ' + r.outcome : ''}`, r.count]),
      ...(j.result_states || []).map(r => [stateWord(r.state), r.count]),
      ...(j.requests || []).map(r => [`Requests ${r.state}`, r.count])];
    const signature = JSON.stringify(items);
    if (v.signatures.stats !== signature) {
      v.signatures.stats = signature;
      v.stats.replaceChildren(...items.map(([label, count]) => { const s = node('span', undefined, 'job-stat'); s.append(node('span', String(count), 'num'), node('span', label, 'muted')); return s; }));
    }
    const paused = j.paused_endpoints || [], pausedSig = paused.join(',');
    if (v.signatures.endpoints !== pausedSig) {
      v.signatures.endpoints = pausedSig;
      v.endpoints.replaceChildren(...paused.map(endpoint => {
        const row = node('div', undefined, 'note tone-warning row'), b = node('button', 'Resume endpoint ' + endpoint, 'sm'); b.type = 'button';
        b.onclick = () => this.guard(async () => { await this.deps.api(`/endpoints/${endpoint}/resume`, 'POST'); await this.refresh(v.id); });
        row.append(node('span', `Endpoint ${endpoint} paused after repeated failures.`, 'grow'), b); return row;
      }));
    }
  }

  async appendLogs(v) {
    const log = await this.deps.api(`/jobs/${v.id}/logs?after=${v.cursor}&limit=100`);
    const atBottom = v.logs.scrollHeight - v.logs.scrollTop - v.logs.clientHeight < 24;
    const added = [];
    if (log.truncated) added.push(logLine('[Earlier retained logs were cleared or evicted]', 'info'));
    for (const line of log.items) { added.push(logLine(line.text, line.level)); v.cursor = line.sequence; }
    if (!log.items.length) v.cursor = Math.max(v.cursor, log.last);
    if (added.length) {
      if (log.truncated) v.logs.prepend(added.shift());
      v.logs.append(...added);
      while (v.logs.childElementCount > MAX_LOG_LINES) v.logs.firstElementChild.remove();
      if (atBottom) v.logs.scrollTop = v.logs.scrollHeight;
    }
    const lines = v.logs.childElementCount;
    setText(v.logCount, lines ? `${lines} line${lines === 1 ? '' : 's'}` : 'No log lines retained for this job.');
  }

  async renderAttempts(v) {
    const attempts = await this.deps.api(`/jobs/${v.id}/attempts?limit=100&offset=${v.attemptOffset}`);
    const signature = JSON.stringify([v.attemptOffset, attempts.map(a => [a.id, a.state, a.trajectory_id])]);
    if (v.signatures.attempts === signature) return;
    v.signatures.attempts = signature;
    const table = node('table', undefined, 'data'), head = node('tr'), body = node('tbody');
    for (const text of ['#', 'Trajectory', 'State', '']) head.append(node('th', text));
    const thead = node('thead'); thead.append(head); table.append(thead, body);
    for (const attempt of attempts) {
      const row = node('tr'), open = node('button', shortId(attempt.trajectory_id), 'sm ghost mono'); open.type = 'button';
      open.dataset.tip = 'Open in the editor · ' + attempt.trajectory_id;
      open.onclick = () => this.guard(() => this.deps.openTrajectory(attempt.trajectory_id));
      const action = node('td');
      if (attempt.state === 'active') {
        const detach = node('button', 'Detach', 'sm'); detach.type = 'button'; detach.setAttribute('aria-label', 'Detach trajectory ' + shortId(attempt.trajectory_id));
        detach.dataset.tip = 'Stop all automatic work on this trajectory permanently, so you can edit it by hand.';
        detach.onclick = () => this.guard(async () => {
          if (!confirm('Detach this trajectory permanently and stop all automatic work on it?')) return;
          await this.deps.api(`/jobs/${v.id}/detach/${attempt.trajectory_id}`, 'POST'); v.signatures.attempts = null; await this.refresh(v.id); await this.deps.afterDetach();
        });
        action.append(detach);
      }
      const cell = node('td'); cell.append(open);
      row.append(node('td', String(attempt.ordinal ?? ''), 'num'), cell, (() => { const td = node('td'); td.append(tag(stateWord(attempt.state), attempt.state === 'active' ? 'accent' : 'muted')); return td; })(), action);
      body.append(row);
    }
    const nav = node('div', undefined, 'job-toolbar');
    nav.append(node('span', attempts.length ? `Attempts ${v.attemptOffset + 1}–${v.attemptOffset + attempts.length}` : 'No attempts yet.', 'grow small muted'));
    if (v.attemptOffset) { const b = node('button', 'Previous attempts', 'sm ghost'); b.type = 'button'; b.onclick = () => this.guard(() => { v.attemptOffset = Math.max(0, v.attemptOffset - 100); return this.refresh(v.id); }); nav.append(b); }
    if (attempts.length === 100) { const b = node('button', 'Next attempts', 'sm ghost'); b.type = 'button'; b.onclick = () => this.guard(() => { v.attemptOffset += 100; return this.refresh(v.id); }); nav.append(b); }
    const wrap = node('div', undefined, 'table-wrap'); wrap.append(table);
    v.attempts.replaceChildren(nav, wrap);
  }

  async renderResults(v) {
    const rows = await this.deps.api(`/jobs/${v.id}/results?limit=100`);
    const signature = JSON.stringify(rows.map(r => [r.trajectory_id, r.state, r.warning]));
    if (v.signatures.results === signature) return;
    v.signatures.results = signature;
    if (!rows.length) { v.results.replaceChildren(node('div', 'No per-trajectory results yet.', 'empty')); return; }
    const table = node('table', undefined, 'data'), head = node('tr'), body = node('tbody');
    for (const text of ['Trajectory', 'Result', 'Warning']) head.append(node('th', text));
    const thead = node('thead'); thead.append(head); table.append(thead, body);
    for (const r of rows) {
      const row = node('tr'), open = node('button', shortId(r.trajectory_id), 'sm ghost mono'); open.type = 'button';
      open.dataset.tip = r.trajectory_id; open.onclick = () => this.guard(() => this.deps.openTrajectory(r.trajectory_id));
      const cell = node('td'), state = node('td'); cell.append(open);
      state.append(tag(stateWord(r.state), r.state === 'skipped' ? 'warning' : r.state === 'failed' ? 'danger' : 'success'));
      row.append(cell, state, node('td', r.warning || '', 'small muted')); body.append(row);
    }
    const wrap = node('div', undefined, 'table-wrap'); wrap.append(table); v.results.replaceChildren(wrap);
  }

  async renderFiles(v, j) {
    v.signatures.files = 'loaded';
    try {
      const manifest = await this.deps.api(`/jobs/${v.id}/manifest`);
      const names = new Set(['download.zip', 'manifest.json', ...(manifest.files || []).map(f => typeof f === 'string' ? f : f.name), ...(manifest.assets || [])]);
      if (j.kind === 'log_export') names.add('logs.txt');
      const list = node('div', undefined, 'file-list');
      for (const name of names) {
        const b = node('button', 'Download ' + name, name === 'download.zip' ? 'primary sm' : 'sm'); b.type = 'button';
        b.onclick = () => this.guard(() => this.deps.download(v.id, name)); list.append(b);
      }
      const parts = [list];
      if (manifest.warnings?.length) { const warn = node('div', undefined, 'note tone-warning'); warn.append(node('strong', 'Warnings'), node('pre', JSON.stringify(manifest.warnings, null, 2))); parts.push(warn); }
      if (manifest.expires_at) parts.push(node('p', 'Files expire ' + new Date(manifest.expires_at * 1000).toLocaleString() + '.', 'small muted'));
      v.files.replaceChildren(...parts);
      if (!ACTIVE.has(j.state) && v.tabs.selected() === 'logs' && !v.logs.textContent) v.tabs.select('files');
    } catch { v.files.replaceChildren(node('p', 'Artifacts expired or removed. The job record is retained.', 'muted')); }
  }
}
