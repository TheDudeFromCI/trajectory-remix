/* Flow view: a flowchart editor for the project flow plus the step-by-step walkthrough.
   Edits stay in a draft until Save; saving stores the flow in the project database. */
import {node, tag} from './ui-kit.js';
import {FlowCanvas} from './flow-canvas.js';
import {renderFlowForm, renderNodeForm, renderProblems} from './flow-inspector.js';
import {KINDS, NODE_H, addNode, compactFlow, removeNode, renameNode, setKind} from './flow-model.js';
import {layout} from './flow-layout.js';
import {WalkthroughPanel} from './flow-walkthrough.js';

const LAYOUT_KEY = 'flow-layout';
const TABS = [['node', 'Node'], ['flow', 'Flow settings'], ['yaml', 'YAML'], ['problems', 'Problems']];

async function request(path, method = 'GET', body) {
  const response = await fetch('/api/v1' + path, {
    method, headers: {'Content-Type': 'application/json', 'X-Remix-Token': document.querySelector('meta[name=remix-token]').content},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || JSON.stringify(data.errors || data.detail || data));
  return data;
}

export class FlowView {
  constructor(root, {api = request, report = () => {}, storage = globalThis.localStorage, delay = 250} = {}) {
    Object.assign(this, {root, api, report, storage, delay});
    this.mode = 'edit'; this.tab = 'node'; this.generation = 0; this.diagnostics = [];
  }

  async open() {
    if (this.doc) return;
    this.mount();
    try { this.adopt(await this.api('/generation/flow')); } catch (error) { this.status(error.message, true); }
  }

  mount() {
    this.root.innerHTML = `<header class="flow-toolbar">
        <h1>Flow <span class="info" tabindex="0" data-tip="How each trajectory is built: Select nodes ask the Generator what happens next, Write event nodes ask it to write that event, Branch nodes route on the selection, End finishes. Saved in the project database; runs freeze the flow when they start."></span></h1>
        <span id="flow-origin"></span><span id="flow-hash"></span><span id="flow-dirty" class="tag"></span>
        <div class="seg" role="tablist" aria-label="Flow mode"><button type="button" role="tab" data-mode="edit">Edit</button><button type="button" role="tab" data-mode="walk">Walkthrough</button></div>
        <span class="grow"></span>
        <details class="menu" id="flow-add-menu"><summary>Add node</summary><div class="menu-list end"></div></details>
        <div class="btn-group"><button id="flow-zoom-out" class="ghost icon sm" type="button" aria-label="Zoom out">−</button><button id="flow-zoom-reset" class="ghost sm num" type="button" aria-label="Reset zoom">100%</button><button id="flow-zoom-in" class="ghost icon sm" type="button" aria-label="Zoom in">+</button><button id="flow-zoom-fit" class="ghost sm" type="button" data-tip="Zoom to show the whole chart">Fit</button></div>
        <button id="flow-tidy" class="ghost sm" type="button" data-tip="Arrange nodes by distance from the start node">Tidy layout</button>
        <button id="flow-reload" class="ghost sm" type="button">Reload</button>
        <button id="flow-discard" class="sm" type="button">Discard</button>
        <button id="flow-save" class="primary sm" type="button">Save flow</button>
      </header>
      <p id="flow-status" class="status-line" role="status" aria-live="polite"></p>
      <div class="flow-body">
        <div class="flow-stage"><div id="flow-canvas" class="flow-canvas" aria-label="Flow chart"></div><nav id="flow-steps" class="flow-steps" aria-label="Walkthrough steps" hidden></nav></div>
        <aside class="widget flow-side">
          <div id="flow-edit-panel"><div class="tab-list" role="tablist" aria-label="Flow editor sections"></div><div id="flow-tab-body" class="panel-body"></div></div>
          <div id="flow-walk-panel" class="panel-body" hidden></div>
        </aside>
      </div>`;
    const $ = s => this.root.querySelector(s);
    this.el = {origin: $('#flow-origin'), hash: $('#flow-hash'), dirty: $('#flow-dirty'), status: $('#flow-status'), save: $('#flow-save'), discard: $('#flow-discard'), tabs: $('#flow-edit-panel .tab-list'), body: $('#flow-tab-body'), edit: $('#flow-edit-panel'), walk: $('#flow-walk-panel'), zoom: $('#flow-zoom-reset')};
    this.canvas = new FlowCanvas($('#flow-canvas'), {
      onSelect: id => { this.selected = id; if (this.mode === 'edit') this.tab = 'node'; this.render(); },
      onMove: () => this.persistLayout(),
      onConnect: (from, port, to) => this.change(f => { f.nodes[from][port] = to; })
    });
    for (const button of this.root.querySelectorAll('[data-mode]')) button.onclick = () => this.setMode(button.dataset.mode);
    const menu = $('#flow-add-menu .menu-list');
    for (const [kind, info] of Object.entries(KINDS)) {
      const item = node('button', info.label); item.type = 'button'; item.dataset.kind = kind;
      item.onclick = () => { $('#flow-add-menu').open = false; this.add(kind); };
      menu.append(item);
    }
    for (const [id, text] of TABS) {
      const tab = node('button', text); tab.type = 'button'; tab.setAttribute('role', 'tab'); tab.dataset.tab = id;
      tab.onclick = () => { this.tab = id; this.renderSide(); };
      this.el.tabs.append(tab);
    }
    $('#flow-zoom-in').onclick = () => this.zoom(this.canvas.scale + 0.1);
    $('#flow-zoom-out').onclick = () => this.zoom(this.canvas.scale - 0.1);
    this.el.zoom.onclick = () => this.zoom(1);
    $('#flow-zoom-fit').onclick = () => this.fit();
    $('#flow-tidy').onclick = () => { this.positions = layout(this.draft); this.persistLayout(); this.render(); };
    $('#flow-reload').onclick = () => this.reload();
    this.el.discard.onclick = () => { if (this.dirty() && confirm('Discard your unsaved flow changes?')) { this.adopt(this.doc); this.status('Draft discarded.'); } };
    this.el.save.onclick = () => this.save().catch(error => this.status(error.message, true));
    this.walk = new WalkthroughPanel(this.el.walk, $('#flow-steps'), {
      api: this.api, flow: () => this.draft, pins: () => globalThis.samplerWorkbench?.pins || {},
      report: error => this.status(error instanceof Error ? error.message : String(error), error instanceof Error),
      onTrail: trail => { this.trail = trail; if (this.mode === 'walk') this.renderCanvas(); }
    });
  }

  status(text, error = false) { this.el.status.textContent = text; this.el.status.classList.toggle('error', error); }

  adopt(doc) {
    this.doc = doc; this.draft = compactFlow(doc.flow); this.saved = JSON.stringify(this.draft);
    this.rawText = doc.text; this.yamlText = doc.text; this.diagnostics = doc.diagnostics || []; this.hash = doc.flow_hash;
    let stored = {};
    try { stored = JSON.parse(this.storage?.getItem(LAYOUT_KEY) || '{}'); } catch { stored = {}; }
    const auto = layout(this.draft);
    this.positions = Object.fromEntries(Object.keys(this.draft.nodes).map(id => [id, stored[id] || auto[id]]));
    if (!(this.selected in this.draft.nodes)) this.selected = this.draft.start;
    this.render();
    if (!this.fitted) { this.fitted = true; this.fit(); }
  }

  dirty() { return !!this.draft && JSON.stringify(this.draft) !== this.saved; }

  /** Apply one edit. Typing passes rerender=false so the form keeps focus. */
  change(mutate, rerender = true) {
    mutate(this.draft);
    this.rawText = null; this.yamlText = null;
    if (rerender) this.render(); else { this.renderCanvas(); this.renderHeader(); }
    this.scheduleCheck();
  }

  scheduleCheck() {
    clearTimeout(this.timer); const generation = ++this.generation;
    this.timer = setTimeout(async () => {
      try {
        const result = await this.api('/generation/flow/check', 'POST', {flow: this.draft});
        if (generation !== this.generation) return;
        this.diagnostics = result.diagnostics; this.hash = result.flow_hash;
        if (this.rawText === null) this.yamlText = result.yaml;
        this.renderCanvas(); this.renderHeader();
        if (this.tab !== 'node' && this.tab !== 'flow') this.renderSide();
        else this.refreshTabs();
      } catch (error) { if (generation === this.generation) this.status(error.message, true); }
    }, this.delay);
  }

  add(kind) {
    let id;
    this.change(f => { id = addNode(f, kind); });
    const anchor = this.positions[this.selected], lowest = Math.max(0, ...Object.values(this.positions).map(p => p.y));
    this.positions[id] = anchor ? {x: anchor.x + 40, y: anchor.y + NODE_H + 60} : {x: 40, y: lowest + NODE_H + 60};
    this.selected = id; this.tab = 'node'; this.persistLayout(); this.render();
  }

  hooks() {
    return {
      change: (mutate, rerender) => this.change(mutate, rerender),
      rename: value => {
        try {
          const from = this.selected; let to;
          this.change(f => { to = renameNode(f, from, value); });
          this.positions[to] = this.positions[from]; if (to !== from) delete this.positions[from];
          this.selected = to; this.persistLayout(); this.render();
        } catch (error) { this.status(error.message, true); this.render(); }
      },
      kind: value => this.change(f => setKind(f, this.selected, value)),
      start: () => this.change(f => { f.start = this.selected; }),
      remove: () => {
        if (!confirm(`Delete node ${this.selected}? Links into it become unconnected.`)) return;
        const id = this.selected; this.change(f => removeNode(f, id));
        delete this.positions[id]; this.selected = this.draft.start; this.persistLayout(); this.render();
      }
    };
  }

  persistLayout() {
    try {
      const stored = JSON.parse(this.storage?.getItem(LAYOUT_KEY) || '{}');
      for (const id of Object.keys(stored)) if (!(id in this.positions)) delete stored[id];
      this.storage?.setItem(LAYOUT_KEY, JSON.stringify({...stored, ...this.positions}));
    } catch { /* Storage unavailable: layout lasts for this page only. */ }
  }

  zoom(scale) { this.el.zoom.textContent = Math.round(this.canvas.setZoom(scale) * 100) + '%'; }
  fit() { this.el.zoom.textContent = Math.round(this.canvas.fit() * 100) + '%'; }

  setMode(mode) {
    this.mode = mode;
    for (const b of this.root.querySelectorAll('[data-mode]')) b.setAttribute('aria-selected', String(b.dataset.mode === mode));
    this.el.edit.hidden = mode !== 'edit'; this.el.walk.hidden = mode !== 'walk';
    this.root.querySelector('#flow-steps').hidden = mode !== 'walk' || !this.walk.entries.length;
    if (mode === 'walk') this.walk.renderPins();
    this.renderCanvas();
  }

  async reload() {
    if (this.dirty() && !confirm('Discard your unsaved flow changes and load the saved flow?')) return;
    try { this.adopt(await this.api('/generation/flow')); this.status('Loaded the saved flow.'); } catch (error) { this.status(error.message, true); }
  }

  async save() {
    if (!this.dirty() && this.rawText === this.doc.text) return;
    const body = this.rawText !== null ? {yaml: this.rawText} : {flow: this.draft};
    try {
      const doc = await this.api('/generation/flow', 'PUT', {...body, expected_revision: this.doc.revision});
      this.adopt(doc); this.status('Saved the project flow. New runs and walkthroughs use it; running jobs keep the flow they started with.');
    } catch (error) {
      throw new Error(/changed since/.test(error.message) ? 'The project flow was changed elsewhere. Your draft is kept; Reload to see the saved version.' : error.message);
    }
  }

  problems() {
    const map = new Map();
    for (const d of this.diagnostics) if (d.node && map.get(d.node) !== 'error') map.set(d.node, d.severity);
    return map;
  }

  render() { this.renderHeader(); this.renderCanvas(); this.renderSide(); this.setMode(this.mode); }

  renderHeader() {
    if (!this.doc) return;
    const origin = this.doc.origin === 'project' ? tag('project flow', 'accent') : tag('packaged default', 'muted');
    origin.dataset.tip = this.doc.origin === 'project' ? 'Saved in the project database.' : 'This project has no saved flow yet, so it uses the default that ships with Remix. Saving stores your version in the project database.';
    this.el.origin.replaceChildren(origin);
    this.el.hash.replaceChildren(...(this.hash ? [Object.assign(tag(this.hash.slice(0, 10), 'faint', 'mono'), {title: 'Flow hash ' + this.hash})] : []));
    const dirty = this.dirty() || (this.rawText !== null && this.rawText !== this.doc.text);
    const errors = this.diagnostics.filter(d => d.severity === 'error').length;
    this.el.dirty.textContent = dirty ? 'Unsaved changes' : 'Saved';
    this.el.dirty.className = 'tag ' + (dirty ? 'tone-warning' : 'tone-faint');
    this.el.save.disabled = !dirty || errors > 0;
    this.el.save.title = errors ? 'Fix the errors listed under Problems first' : '';
    this.el.discard.disabled = !dirty;
  }

  renderCanvas() {
    if (!this.draft) return;
    this.canvas.render(this.draft, {positions: this.positions, selected: this.selected, problems: this.problems(), trail: this.mode === 'walk' ? this.trail : null});
    this.canvas.root.classList.toggle('is-walking', this.mode === 'walk');
  }

  refreshTabs() {
    const errors = this.diagnostics.filter(d => d.severity === 'error').length, warnings = this.diagnostics.length - errors;
    for (const tab of this.el.tabs.children) {
      const on = tab.dataset.tab === this.tab; tab.setAttribute('aria-selected', String(on)); tab.tabIndex = on ? 0 : -1;
      if (tab.dataset.tab === 'problems') tab.textContent = `Problems${errors + warnings ? ` (${errors + warnings})` : ''}`;
      tab.classList.toggle('has-errors', tab.dataset.tab === 'problems' && errors > 0);
    }
  }

  renderSide() {
    if (!this.draft) return;
    this.refreshTabs();
    const body = this.el.body;
    if (this.tab === 'node') renderNodeForm(body, this.draft, this.selected, this.hooks(), this.diagnostics);
    else if (this.tab === 'flow') renderFlowForm(body, this.draft, this.hooks(), this.diagnostics);
    else if (this.tab === 'problems') renderProblems(body, this.diagnostics, d => { if (d.node) { this.selected = d.node; this.tab = 'node'; } else this.tab = 'flow'; this.render(); });
    else this.renderYaml(body);
  }

  renderYaml(body) {
    const area = node('textarea', undefined, 'flow-yaml mono'); area.spellcheck = false; area.setAttribute('aria-label', 'Flow YAML');
    area.value = this.yamlText ?? 'Checking…';
    const apply = node('button', 'Apply YAML to chart', 'sm'); apply.type = 'button'; apply.disabled = true;
    const note = node('p', this.rawText !== null ? 'This is the file text, comments included. Saving without chart edits keeps it exactly.' : 'Generated from the chart. Comments in the old file are not kept once you edit the chart.', 'small muted');
    area.oninput = () => { apply.disabled = false; };
    apply.onclick = async () => {
      const result = await this.api('/generation/flow/check', 'POST', {yaml: area.value}).catch(error => ({valid: false, diagnostics: [{severity: 'error', message: error.message}]}));
      this.diagnostics = result.diagnostics;
      if (!result.valid) { this.status('YAML not applied: ' + (result.diagnostics.find(d => d.severity === 'error')?.message || 'invalid'), true); this.refreshTabs(); return; }
      this.draft = compactFlow(result.flow); this.rawText = area.value; this.yamlText = area.value; this.hash = result.flow_hash;
      const auto = layout(this.draft);
      this.positions = Object.fromEntries(Object.keys(this.draft.nodes).map(id => [id, this.positions[id] || auto[id]]));
      if (!(this.selected in this.draft.nodes)) this.selected = this.draft.start;
      this.status('YAML applied to the chart. Save to store it in the project.'); this.render();
    };
    const actions = node('div', undefined, 'panel-actions'); actions.append(apply);
    body.replaceChildren(note, area, actions);
  }
}

if (typeof document !== 'undefined' && document.getElementById('flow-view')) {
  const view = globalThis.flowView = new FlowView(document.getElementById('flow-view'));
  document.addEventListener('studio-view', e => { if (e.detail === 'flow-view') view.open(); });
  window.addEventListener('beforeunload', e => { if (view.dirty()) { e.preventDefault(); e.returnValue = ''; } });
}
