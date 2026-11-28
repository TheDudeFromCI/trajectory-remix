/* UI B: project draft editor over the shared sampler/registry REST API. */
import {loadRunInputs, persistInput, resolveSeed} from './run-inputs.js';
const $ = id => document.getElementById(id);
const clone = value => structuredClone(value);
const pretty = value => JSON.stringify(value, null, 2);
const label = value => value.replaceAll('_', ' ').replaceAll('.', ' / ');
const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const action = (text, fn, cls) => { const n = el('button', text, cls); n.type = 'button'; n.onclick = fn; return n; };
const input = (name, value, change, type = 'text') => {
  const n = el('input'); n.type = type; n.value = value; n.setAttribute('aria-label', name);
  n.addEventListener('input', () => change(n.value, n)); return n;
};
const field = (name, control) => { const n = el('label', name); n.append(control); return n; };
async function request(path, method = 'GET', body) {
  const r = await fetch('/api/v1' + path, {method, headers: {'Content-Type': 'application/json', 'X-Remix-Token': document.querySelector('meta[name=remix-token]').content}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const data = await r.json(); if (!r.ok) throw new Error(data.message || pretty(data.detail || data)); return data;
}
export function groups(matrix, definitions) {
  return [
    {id: 'focus.family', section: 'Focus', title: 'Competency families', items: matrix.competencies},
    ...matrix.competencies.map(f => ({id: 'behavior:' + f.id, metric: 'focus.behavior', section: 'Behaviors', title: label(f.id), items: f.behaviors})),
    ...Object.entries(matrix.decision_axes).map(([id, a]) => ({id: 'axis:' + id, metric: 'decision.choice', section: 'Decisions', title: label(id), items: a.variants})),
    {id: 'domain', section: 'Dimensions', title: 'Domain', items: matrix.domains},
    ...Object.entries(matrix.dimensions).map(([id, items]) => ({id, section: 'Dimensions', title: label(id), items})),
    {id: 'event_tags', section: 'Events', title: 'Event tags', items: Object.entries(matrix.event_tags).map(([id, v]) => ({...v, id})), probability: true},
    {id: 'conditional_weights', section: 'Rules', title: 'Conditional multipliers', items: matrix.conditional_weights.map((v, i) => ({...v, id: 'Rule ' + (i + 1)})), raw: true},
    {id: 'hard_implications', section: 'Rules', title: 'Hard implications', items: matrix.hard_implications.map((v, i) => ({...v, id: 'Rule ' + (i + 1)})), raw: true},
    ...Object.entries(definitions).map(([id]) => ({id: 'metric:' + id, metric: id, section: 'Metrics', title: label(id), items: [], metricOnly: true})),
    {id: 'sampler', section: 'Advanced', title: 'Sampler settings', items: [], raw: true}
  ];
}
/** The field a group's choices pin: behaviors and decision choices are grouped per family and axis. */
export const pinKey = g => g.id.startsWith('behavior:') || g.id.startsWith('axis:') ? g.metric : g.id;
export class SamplerWorkbench {
  constructor(root, transport = request) { this.root = root; this.api = transport; this.pins = {}; this.selectedRows = new Set(); this.selected = 'focus.family'; this.query = ''; this.filter = ''; this.choiceQuery = ''; this.invalid = new Map(); this.generation = 0; }
  async open() {
    if (this.record) return;
    try { const record = await this.api('/sampler'); this.adopt(record); this.mount(); } catch (e) { this.root.replaceChildren(el('p', e.message), action('Retry loading matrix', () => this.open())); }
  }
  adopt(record) { this.record = record; this.config = clone(record.configuration); this.saved = pretty(this.config); this.selectedRows.clear(); this.invalid.clear(); }
  dirty() { return this.invalid.size > 0 || pretty(this.config) !== this.saved; }
  allGroups() { return groups(this.config.matrix, this.config.definitions); }
  group() { return this.allGroups().find(g => g.id === this.selected) || this.allGroups()[0]; }
  report(text, error = false) { $('sampler-message').textContent = text; $('sampler-message').classList.toggle('error', error); }
  mount() {
    this.root.innerHTML = `<header class="sw-heading"><h1>Sampler matrix <span class="info" tabindex="0" data-tip="The weights and rules that decide what each generated trajectory is about. Changes apply to future runs; existing runs keep their frozen matrix, targets and instructions."></span></h1><div class="sw-save"><span id="sampler-dirty"></span><button id="sampler-reload" class="ghost sm">Reload saved</button><button id="sampler-reset" class="sm">Discard draft</button><button id="sampler-save" class="primary sm">Save project changes</button></div></header>
    <div id="sampler-message" role="status" aria-live="polite"></div>
    <div class="sw-layout"><aside class="sw-navigation"><div class="sw-nav-tools"><input id="sampler-search" type="search" aria-label="Find a dimension or entry" placeholder="Find a dimension or entry"><select id="sampler-filter" aria-label="Show groups"><option value="">All groups</option><option>Focus</option><option>Behaviors</option><option>Decisions</option><option>Dimensions</option><option>Events</option><option>Rules</option><option>Metrics</option><option>Advanced</option><option value="changed">Changed groups</option></select><span id="sampler-count"></span></div><nav id="sampler-nav" aria-label="Matrix groups"></nav></aside>
    <div class="sw-main"><section id="sampler-detail"></section><details class="sw-preview"><summary>Proposal preview &amp; generation <span>Fixed seed · saved project · per-run pins</span></summary><p>Sample one proposal from the saved matrix to see what a run would ask for. Pins force a choice for previews and generation without editing weights. Previews make no model or compiler calls.</p><div class="sw-preview-controls"><label>Seed<input id="sampler-seed" type="number" step="1" value="0" title="0 picks a random seed"></label><button id="sampler-preview">Sample one proposal</button></div><div id="sampler-pins" class="sw-pins"></div><p id="sampler-preview-status" role="status"></p><pre id="sampler-proposal"></pre><hr><h3>Generate with these pins</h3><div class="sw-generate"><label>Attempts<input id="sampler-attempts" type="number" min="1" max="1000" step="1" value="1"></label><button id="sampler-generate" class="primary">Start generation</button></div><details class="collapsible"><summary>Advanced Flow instructions</summary><div class="collapsible-body"><label>Run instructions JSON<textarea id="sampler-instructions">{}</textarea></label></div></details><p id="sampler-run-status" role="status" class="small muted"></p></details></div></div>`;
    $('sampler-search').oninput = e => { this.query = e.target.value.toLowerCase().trim(); this.renderNav(); };
    $('sampler-filter').onchange = e => { this.filter = e.target.value; this.renderNav(); };
    $('sampler-save').onclick = () => this.save();
    $('sampler-reload').onclick = async () => { if (this.dirty() && !confirm('Discard your draft and load the latest saved configuration?')) return; try { this.adopt(await this.api('/sampler')); this.renderPins(); this.renderNav(); this.renderDetail(); this.update(); ++this.generation; this.report('Latest saved configuration loaded.'); } catch (e) { this.report(e.message, true); } };
    $('sampler-reset').onclick = () => { if (!this.dirty() || confirm('Discard all unsaved matrix and metric changes?')) { this.adopt(this.record); this.renderPins(); this.renderNav(); this.renderDetail(); this.update(); this.report('Draft discarded.'); } };
    $('sampler-preview').onclick = () => this.preview();
    $('sampler-generate').onclick = () => this.generate();
    const saved = loadRunInputs({attempts: 1});
    $('sampler-seed').value = saved.seed; $('sampler-attempts').value = saved.attempts;
    persistInput($('sampler-seed'), 'seed'); persistInput($('sampler-attempts'), 'attempts');
    this.renderPins();
    this.renderNav(); this.renderDetail(); this.update();
  }
  renderPins() {
    $('sampler-pins').replaceChildren();
    for (const [key, values] of Object.entries(this.record.overrides)) {
      const select = el('select'); select.dataset.pin = key; select.setAttribute('aria-label', 'Pin ' + key);
      for (const value of ['', ...values]) { const option = el('option', value || 'Unpinned — sample'); option.value = value; select.append(option); }
      // Retain a removed choice explicitly until the author unpins or changes it.
      if (this.pins[key] && !values.includes(this.pins[key])) { const option = el('option', 'Unavailable — ' + label(this.pins[key])); option.value = this.pins[key]; select.append(option); }
      select.value = this.pins[key] || '';
      select.onchange = () => this.setPin(key, select.value);
      $('sampler-pins').append(field(label(key), select));
    }
    this.refreshPins();
  }
  setPin(key, value) {
    if (value) this.pins[key] = value; else delete this.pins[key];
    this.refreshPins(); ++this.generation;
    this.report(value ? `${label(key)} pinned to ${label(value)} for previews and generation. Project weights are unchanged.` : `${label(key)} unpinned; the sampler will choose it. Project weights are unchanged.`);
    if ($('sampler-proposal').textContent) $('sampler-preview-status').textContent = 'Pins changed; sample again to update the previous preview.';
  }
  refreshPins() {
    for (const select of $('sampler-pins').querySelectorAll('select')) select.value = this.pins[select.dataset.pin] || '';
    for (const button of this.root.querySelectorAll('.sw-pin')) {
      const pinned = this.pins[button.dataset.pinKey] === button.dataset.pinValue;
      button.setAttribute('aria-pressed', String(pinned));
      button.title = `${pinned ? 'Unpin' : 'Pin'} ${label(button.dataset.pinValue)} for ${label(button.dataset.pinKey)} · previews and generation`;
    }
  }
  renderNav() {
    const nav = $('sampler-nav'), scroll = nav.scrollTop;
    const groups = this.allGroups().filter(g => (!this.filter || (this.filter === 'changed' ? this.groupChanged(g) : g.section === this.filter)) && (!this.query || (g.title + g.id + pretty(g.items)).toLowerCase().includes(this.query)));
    nav.replaceChildren(); let section;
    for (const g of groups) {
      if (section !== g.section) { section = g.section; nav.append(el('h3', section)); }
      const b = action('', () => { this.selected = g.id; this.choiceQuery = ''; this.selectedRows.clear(); this.renderNav(); this.renderDetail(); });
      b.className = 'sw-nav-row' + (g.id === this.selected ? ' selected' : ''); b.setAttribute('aria-current', String(g.id === this.selected));
      b.append(el('span', g.title), el('small', `${g.items.length || '—'}${this.groupChanged(g) ? ' · edited' : ''}`)); nav.append(b);
    }
    if (!groups.length) nav.append(el('p', 'No matching groups. Clear the search or filter.'));
    $('sampler-count').textContent = `${groups.length} of ${this.allGroups().length} groups`; nav.scrollTop = scroll;
  }
  groupChanged(g) {
    const before = groups(this.record.configuration.matrix, this.record.configuration.definitions).find(x => x.id === g.id);
    return [...this.invalid.keys()].some(k => k.startsWith(g.id + ':') || k === 'tolerance:' + (g.metric || g.id)) || pretty(g.items) !== pretty(before?.items) || pretty(this.config.metric_options[g.metric || g.id]) !== pretty(this.record.configuration.metric_options[g.metric || g.id]) || (g.id === 'sampler' && pretty(this.config.matrix.sampler) !== pretty(this.record.configuration.matrix.sampler));
  }
  update() {
    const dirty = this.dirty(); $('sampler-dirty').textContent = dirty ? 'Unsaved project draft' : `Saved · revision ${this.record.settings_revision}`;
    $('sampler-save').disabled = !dirty || this.saving; $('sampler-reset').disabled = !dirty || this.saving;
    $('sampler-preview').disabled = dirty || this.saving;
    $('sampler-generate').disabled = dirty || this.saving || this.starting;
    $('sampler-preview-status').textContent = dirty ? 'Save project changes to preview this matrix.' : 'Uses the saved project configuration at request time.';
  }
  mutate() { this.update(); this.renderNav(); const raw = this.root.querySelector('textarea[aria-label="Group JSON"]'); if (raw && !raw.dataset.edited) { const g = this.group(); raw.value = pretty(g.raw || ['event_tags', 'sampler'].includes(g.id) ? this.config.matrix[g.id] : g.items); } ++this.generation; if ($('sampler-proposal').textContent) $('sampler-preview-status').textContent = 'Previous preview retained below; configuration has changed.'; }
  renderDetail() {
    const g = this.group(), detail = $('sampler-detail'); const previousScroll = detail.querySelector('.sw-table-wrap')?.scrollTop || 0; this.selected = g.id;
    detail.replaceChildren(el('div', g.section.toUpperCase() + ' / ' + g.id, 'sw-eyebrow'), el('h2', g.title));
    detail.append(el('p', g.probability ? 'Per-tag base probability before eligibility, forced tags and the extra-tag cap.' : g.raw ? 'Shared sampler rules. Edit the full group below; validation happens on save.' : 'Compare base weights within this group. Constraints and conditional multipliers can change sampled outcomes.', 'sw-help'));
    if (!g.raw && !g.metricOnly) {
      const choices = input('Filter choices in group', this.choiceQuery, v => { this.choiceQuery = v; tableArea.replaceChildren(); this.renderTable(tableArea, this.group()); }, 'search');
      choices.placeholder = 'Filter choices, descriptions or constraints…';
      const tableArea = el('div'); detail.append(field('Filter choices in this group', choices), tableArea); this.renderTable(tableArea, g);
    }
    if (g.raw && g.items.length) {
      const rules = el('div', undefined, 'sw-rule-cards');
      for (const item of g.items) rules.append(action(`${item.id} · When ${Object.entries(item.when || {}).map(([k,v]) => `${label(k)} = ${Array.isArray(v) ? v.join(' or ') : v}`).join('; ')} → ${item.target ? `${item.target} × ${item.factor}` : 'force ' + Object.entries(item.force || {}).map(([k,v]) => `${label(k)} = ${v}`).join('; ')}`, () => this.inspect(item, g)));
      detail.append(rules);
    }
    if (!g.metricOnly && g.id !== 'sampler') this.renderStructure(detail, g);
    this.renderMetric(detail, g.metric || g.id);
    const advanced = el('details', undefined, 'sw-advanced'); advanced.append(el('summary', 'Advanced JSON · rules, constraints & custom fields'));
    advanced.append(el('p', 'Apply replaces only this group. Other groups and unknown fields are preserved. Invalid configurations are rejected by the shared backend.'));
    const value = g.id === 'sampler' ? this.config.matrix.sampler : g.id === 'event_tags' ? this.config.matrix.event_tags : g.raw ? this.config.matrix[g.id] : g.items;
    if (!g.metricOnly) {
      const raw = el('textarea'); raw.value = pretty(value); raw.spellcheck = false; raw.setAttribute('aria-label', 'Group JSON'); raw.oninput = () => { raw.dataset.edited = 'true'; };
      advanced.append(raw, action('Apply JSON to project draft', () => { try { const v = JSON.parse(raw.value); this.replaceGroup(g, v); this.mutate(); this.renderDetail(); this.report('Group JSON applied to draft. Save to validate.'); } catch (e) { this.report(e.message, true); } })); detail.append(advanced);
    }
    const inspect = el('section', undefined, 'sw-inspector'); inspect.id = 'sampler-inspector'; detail.append(inspect);
    if (g.items.length) this.inspect(g.items[0], g);
    const table = detail.querySelector('.sw-table-wrap'); if (table) table.scrollTop = previousScroll;
  }
  renderStructure(detail, g) {
    const panel = el('details', undefined, 'sw-structure');
    panel.append(el('summary', g.raw ? 'Add or remove rules' : 'Add or remove entries'));
    panel.append(el('p', 'Changes stay in this project draft until Save. Removal does not rewrite references; inspect warnings and fix referring constraints explicitly.'));
    const choices = el('select'); choices.setAttribute('aria-label', 'Entry to copy or remove');
    for (const item of g.items) { const opt = el('option', item.id); opt.value = item.id; choices.append(opt); }
    const ident = input('New entry ID', '', () => {});
    panel.append(field('Entry to copy or remove', choices));
    if (!g.raw) panel.append(field('New entry ID', ident));
    if (g.raw) {
      const conditions = {'focus.family': this.config.matrix.competencies.map(x => x.id), 'focus.id': this.config.matrix.competencies.flatMap(x => x.behaviors.map(b => b.id)), ...Object.fromEntries(Object.entries(this.config.matrix.dimensions).map(([key, items]) => [key, items.map(x => x.id)]))};
      const select = (name, values) => { const n = el('select'); n.setAttribute('aria-label', name); for (const v of values) { const o = el('option', v); o.value = v; n.append(o); } return n; };
      const condition = select('Condition field', Object.keys(conditions)), value = select('Condition value', conditions[condition.value]);
      condition.onchange = () => { value.replaceChildren(...conditions[condition.value].map(v => { const o = el('option', v); o.value = v; return o; })); };
      const targets = Object.entries(this.config.matrix.dimensions).flatMap(([key, items]) => items.map(x => key + '.' + x.id));
      const target = select('Rule target', targets), factor = input('Rule multiplier', '1', () => {}, 'number'); factor.min = '0'; factor.step = 'any';
      panel.append(field('When field', condition), field('Equals', value), field(g.id === 'conditional_weights' ? 'Multiply option' : 'Force option', target));
      if (g.id === 'conditional_weights') panel.append(field('Multiplier', factor));
      panel.append(action('Add rule to draft', () => {
        if (g.id === 'conditional_weights' && (factor.value === '' || !factor.checkValidity())) return this.report('Enter a finite nonnegative multiplier.', true);
        const rule = {when: {[condition.value]: [value.value]}};
        if (g.id === 'conditional_weights') Object.assign(rule, {target: target.value, factor: Number(factor.value)});
        else { const dot = target.value.indexOf('.'); rule.force = {[target.value.slice(0, dot)]: target.value.slice(dot + 1)}; }
        this.config.matrix[g.id].push(rule); this.mutate(); this.renderDetail(); this.report('Rule added to project draft. Save to validate.');
      }));
    }
    // Copying keeps required behavior/domain fields and custom constraints intact.
    panel.append(action(g.raw ? 'Copy rule to draft' : 'Copy entry to draft', () => {
      try {
        const index = g.items.findIndex(x => x.id === choices.value);
        if (index < 0) throw new Error('Select an existing entry to copy. Use Advanced JSON for the first rule.');
        const id = ident.value.trim();
        if (!g.raw && (!/^[a-z][a-z0-9_]*$/.test(id) || g.items.some(x => x.id === id))) throw new Error('Use a unique lowercase ID with letters, digits and underscores.');
        if (g.raw) this.config.matrix[g.id].push(clone(this.config.matrix[g.id][index]));
        else if (g.probability) this.config.matrix.event_tags[id] = clone(this.config.matrix.event_tags[choices.value]);
        else {
          const entry = {...clone(g.items[index]), id};
          if (g.id === 'focus.family') entry.behaviors = entry.behaviors.map(b => ({...b, id: id + '_' + b.id}));
          g.items.push(entry);
        }
        this.mutate(); this.renderDetail(); this.report('Copy added to draft. Inspect inherited constraints before saving.');
      } catch (e) { this.report(e.message, true); }
    }));
    panel.append(action(g.raw ? 'Remove rule from draft' : 'Remove entry from draft', () => {
      const index = g.items.findIndex(x => x.id === choices.value); if (index < 0) return;
      if (!g.raw && !g.probability && g.items.length === 1) return this.report('Keep at least one entry in this group.', true);
      if (!confirm(`Remove ${choices.value} from this draft? References and pins are retained for explicit correction.`)) return;
      if (g.probability) delete this.config.matrix.event_tags[choices.value];
      else if (g.raw) this.config.matrix[g.id].splice(index, 1);
      else g.items.splice(index, 1);
      this.invalid.delete(g.id + ':' + choices.value); this.selectedRows.delete(choices.value);
      this.mutate(); this.renderDetail(); this.report('Removed from draft. Inspect referring rules before Save; invalid saves retain your draft.');
    }));
    detail.append(panel);
  }
  replaceGroup(g, value) {
    if (['sampler', 'event_tags'].includes(g.id) || g.raw) {
      if (!value || typeof value !== 'object' || Array.isArray(value) !== Array.isArray(this.config.matrix[g.id])) throw new Error('Keep the original group object/array structure.');
      this.config.matrix[g.id] = value;
    } else {
      if (!Array.isArray(value) || !value.length || value.some(x => !x || typeof x !== 'object' || typeof x.id !== 'string')) throw new Error('Expected a nonempty array of entries with string IDs.');
      if (g.id === 'focus.family') this.config.matrix.competencies = value;
      else if (g.id === 'domain') this.config.matrix.domains = value;
      else if (g.id.startsWith('behavior:')) this.config.matrix.competencies.find(f => 'behavior:' + f.id === g.id).behaviors = value;
      else if (g.id.startsWith('axis:')) this.config.matrix.decision_axes[g.id.slice(5)].variants = value;
      else this.config.matrix.dimensions[g.id] = value;
    }
  }
  renderTable(detail, g) {
    const visible = g.items.filter(i => !this.choiceQuery || pretty(i).toLowerCase().includes(this.choiceQuery.toLowerCase()));
    const tools = el('div', undefined, 'sw-bulk'); const count = el('span', '0 selected');
    const amount = input('Repeated edit value', '1', () => {}, 'number'); amount.min = '0'; amount.step = 'any'; if (g.probability) amount.max = '1';
    const set = action('Set selected', () => {
      if (!this.selectedRows.size) return this.report('Select entries before applying a repeated edit.', true);
      if (!amount.checkValidity() || amount.value === '') return this.report('Enter a finite nonnegative value' + (g.probability ? ' from 0 to 1.' : '.'), true);
      for (const item of g.items) if (this.selectedRows.has(item.id)) this.writeWeight(g, item, Number(amount.value));
      this.mutate(); this.renderDetail(); this.report('Selected entries updated in draft.');
    });
    tools.append(count, amount, set); detail.append(tools);
    const wrap = el('div', undefined, 'sw-table-wrap'), table = el('table', undefined, 'sw-table');
    const head = el('thead'), hr = el('tr'), allCell = el('th'), all = el('input'); all.type = 'checkbox'; all.setAttribute('aria-label', 'Select all choices');
    all.checked = visible.length > 0 && visible.every(i => this.selectedRows.has(i.id));
    all.onchange = () => { for (const i of visible) all.checked ? this.selectedRows.add(i.id) : this.selectedRows.delete(i.id); this.renderDetail(); }; allCell.append(all); hr.append(allCell);
    for (const text of ['Choice / description', 'Pin', g.probability ? 'Base p' : 'Base weight', g.probability ? 'Probability' : 'Base share¹', 'Rules']) hr.append(el('th', text)); head.append(hr); table.append(head);
    const body = el('tbody'); this.weightCells = [];
    for (const item of visible) {
      const row = el('tr'), checkCell = el('td'), check = el('input'); check.type = 'checkbox'; check.checked = this.selectedRows.has(item.id); check.setAttribute('aria-label', 'Select ' + item.id);
      check.onchange = () => { check.checked ? this.selectedRows.add(item.id) : this.selectedRows.delete(item.id); count.textContent = `${this.selectedRows.size} selected`; all.checked = visible.length > 0 && visible.every(i => this.selectedRows.has(i.id)); }; checkCell.append(check);
      const name = el('td'); name.append(action(label(item.id), () => this.inspect(item, g), 'sw-choice'), ...((item.description || item.objective || item.scenario_condition) ? [el('small', item.description || item.objective || item.scenario_condition)] : []));
      const pinCell = el('td', undefined, 'sw-pin-cell');
      const key = pinKey(g);
      if (this.record.overrides[key]?.includes(item.id)) {
        const pin = action('', () => this.setPin(key, this.pins[key] === item.id ? '' : item.id), 'sw-pin');
        pin.dataset.pinKey = key; pin.dataset.pinValue = item.id;
        pin.setAttribute('aria-label', `Pin ${label(item.id)} for ${label(key)}`);
        pin.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M16 3l5 5-4 1-4 4-1 4-5-5 4-1 4-4z"/><path d="M7 17l-4 4"/></svg>';
        pinCell.append(pin);
      } else { pinCell.textContent = '—'; pinCell.title = 'This field is not supported as a sampler pin.'; }
      const numeric = el('td'), weight = input((g.probability ? 'Probability ' : 'Weight ') + item.id, item[g.probability ? 'p' : 'weight'] ?? 0, (v, control) => {
        if (v === '' || !control.checkValidity()) { control.setAttribute('aria-invalid', 'true'); this.invalid.set(g.id + ':' + item.id, v); this.update(); this.renderNav(); this.report('Enter a finite nonnegative value' + (g.probability ? ' from 0 to 1.' : '.'), true); return; }
        control.removeAttribute('aria-invalid'); this.invalid.delete(g.id + ':' + item.id); this.writeWeight(g, item, Number(v)); this.refreshShares(g); this.mutate(); this.report('Weight updated in project draft.');
      }, 'number'); weight.min = '0'; weight.step = 'any'; if (g.probability) weight.max = '1'; if (this.invalid.has(g.id + ':' + item.id)) { weight.value = this.invalid.get(g.id + ':' + item.id); weight.setAttribute('aria-invalid', 'true'); } numeric.append(weight);
      const share = el('td', '', 'sw-share'); this.weightCells.push([item, share]);
      const rules = Object.keys(item).filter(k => !['id', 'weight', 'p', 'description', 'objective', 'behaviors', 'scenario_condition', 'target_behavior'].includes(k));
      row.append(checkCell, name, pinCell, numeric, share, el('td', rules.length ? `${rules.length} fields` : '—')); body.append(row);
    }
    count.textContent = `${this.selectedRows.size} selected`; table.append(body); wrap.append(table); detail.append(wrap);
    detail.append(el('p', g.probability ? 'Base p is an independent input, not a normalized share or an observed frequency.' : '¹ Base share = weight ÷ group total. This comparison is arithmetic only; it is not an output frequency or an exact joint probability.', 'sw-footnote'));
    this.refreshShares(g); this.refreshPins();
  }
  writeWeight(g, item, value) { const key = g.probability ? 'p' : 'weight'; this.invalid.delete(g.id + ':' + item.id); item[key] = value; if (g.probability) this.config.matrix.event_tags[item.id][key] = value; }
  refreshShares(g) {
    const total = g.items.reduce((n, i) => n + Number(i.weight || 0), 0);
    for (const [i, cell] of this.weightCells) { const fraction = g.probability ? i.p : total > 0 ? i.weight / total : null; cell.textContent = fraction === null ? 'Unavailable' : (fraction * 100).toFixed(2) + '%'; cell.style.setProperty('--share', (fraction || 0) * 100 + '%'); }
  }
  inspect(item, g) {
    const box = $('sampler-inspector'); box.replaceChildren(el('h3', label(item.id)), el('p', item.description || item.objective || item.target_behavior || 'Entry configuration'));
    const constraints = el('dl', undefined, 'sw-properties');
    for (const [key, value] of Object.entries(item)) {
      if (['id', 'weight', 'p', 'description', 'objective', 'target_behavior', 'behaviors'].includes(key)) continue;
      constraints.append(el('dt', label(key)), el('dd', typeof value === 'object' ? pretty(value) : String(value)));
    }
    if (constraints.children.length) box.append(constraints); else box.append(el('p', 'No entry-specific constraints. Global sampler rules still apply.'));
    const rules = [...this.config.matrix.conditional_weights, ...this.config.matrix.hard_implications].filter(r => pretty(r).includes(item.id));
    if (rules.length) {
      const list = el('ul', undefined, 'sw-rule-list');
      for (const rule of rules) list.append(el('li', `When ${Object.entries(rule.when || {}).map(([k, v]) => `${label(k)} = ${Array.isArray(v) ? v.join(' or ') : v}`).join('; ')} → ${rule.target ? `${rule.target} × ${rule.factor}` : 'force ' + Object.entries(rule.force || {}).map(([k,v]) => `${label(k)} = ${v}`).join('; ')}`));
      box.append(el('h4', 'Referencing conditions / implications'), list);
    }
    box.append(el('small', g.raw ? 'Shared sampler rule. Eligibility and forced propagation are evaluated by the shared sampler.' : `Linked definition: ${g.metric || g.id}. Eligibility and forced propagation are evaluated by the shared sampler.`));
  }
  renderMetric(detail, key) {
    const definition = this.config.definitions[key]; if (!definition) return;
    const panel = el('details', undefined, 'sw-metric'); panel.append(el('summary', `Linked metric · ${key} · ${definition.scope}`));
    panel.append(el('p', `Kind: ${definition.kind}. Actual values remain in Dataset Metrics. Unmeasured values stay unavailable. Tolerances are specific to this definition.`));
    const values = this.config.metric_options[key] || {};
    const edit = (k, value) => { this.config.metric_options[key] ||= {}; if (value === '') delete this.config.metric_options[key][k]; else this.config.metric_options[key][k] = value; if (!Object.keys(this.config.metric_options[key]).length) delete this.config.metric_options[key]; this.mutate(); };
    for (const k of ['label', 'unit', 'target']) panel.append(field(k, input(`${key} ${k}`, values[k] ?? '', v => edit(k, v))));
    const tolerance = values.tolerance; const pendingTolerance = this.invalid.get('tolerance:' + key);
    const absolute = input(`${key} absolute tolerance`, pendingTolerance?.absolute ?? tolerance?.absolute ?? '', () => {}, 'number'), relative = input(`${key} relative tolerance`, pendingTolerance?.relative ?? tolerance?.relative ?? '', () => {}, 'number');
    for (const control of [absolute, relative]) { control.min = '0'; control.step = 'any'; control.oninput = () => {
      if (absolute.value === '' && relative.value === '') { absolute.removeAttribute('aria-invalid'); relative.removeAttribute('aria-invalid'); this.invalid.delete('tolerance:' + key); edit('tolerance', ''); return; }
      for (const c of [absolute, relative]) c.removeAttribute('aria-invalid');
      if ([absolute, relative].some(c => c.value === '' || !c.checkValidity())) { this.invalid.set('tolerance:' + key, {absolute:absolute.value, relative:relative.value}); this.update(); absolute.setAttribute('aria-invalid', 'true'); relative.setAttribute('aria-invalid', 'true'); return this.report('Set both nonnegative tolerance bands, or clear both to leave tolerance unspecified.', true); }
      this.invalid.delete('tolerance:' + key); edit('tolerance', {absolute: Number(absolute.value), relative: Number(relative.value)});
    }; }
    if (pendingTolerance) for (const control of [absolute, relative]) control.setAttribute('aria-invalid', 'true');
    panel.append(field('Absolute band (metric units)', absolute), field('Relative band (fraction; 0.1 = 10%)', relative)); detail.append(panel);
  }
  async save() {
    if (this.saving) return;
    if (this.invalid.size) return this.report('Resolve invalid numeric fields before saving: ' + [...this.invalid.keys()].join(', '), true);
    this.saving = true; this.update();
    // Freeze the submitted draft; edits made while the request is pending are retained.
    const submitted = clone(this.config);
    try {
      const result = await this.api('/sampler', 'PUT', {matrix: submitted.matrix, metric_options: submitted.metric_options, expected_revision: this.record.settings_revision});
      const newer = this.invalid.size > 0 || pretty(this.config) !== pretty(submitted), retained = this.config, retainedInvalid = new Map(this.invalid);
      const rowSelection = new Set(this.selectedRows); this.adopt(result); this.selectedRows = rowSelection; if (newer) { this.config.matrix = retained.matrix; this.config.metric_options = retained.metric_options; this.invalid = retainedInvalid; }
      this.renderPins(); this.renderNav(); if (!newer) this.renderDetail(); this.report(newer ? 'Submitted changes saved. Newer edits remain in your draft.' : 'Project configuration saved. Existing runs are unchanged.'); ++this.generation;
    } catch (e) { this.report('Save rejected; your draft is retained. ' + e.message + ' If another editor changed settings, copy your group JSON before discarding and reloading.', true); }
    finally { this.saving = false; this.update(); }
  }
  async generate() {
    if (this.dirty()) return this.report('Save project changes before generating.', true);
    const seed = resolveSeed(Number($('sampler-seed').value)), count = Number($('sampler-attempts').value);
    if ($('sampler-seed').value === '' || !Number.isSafeInteger(seed) || !Number.isInteger(count) || count < 1 || count > 1000 || !Number.isSafeInteger(seed + count - 1)) return this.report('Supply a safe integer seed and 1–1000 attempts.', true);
    this.starting = true; this.update();
    try {
      const instructions = JSON.parse($('sampler-instructions').value);
      const requests = Array.from({length:count}, (_, i) => ({seed:seed+i, overrides:{...this.pins}}));
      const run = await this.api('/generation/runs', 'POST', {requests, instructions});
      $('sampler-run-status').textContent = `Started ${count} attempts · job ${run.job_id}. Inputs and pins are frozen for this run.`;
      document.dispatchEvent(new CustomEvent('graph-job-started', {detail:run.job_id}));
    } catch (e) { this.report('Generation rejected: ' + e.message, true); }
    finally { this.starting = false; this.update(); }
  }
  async preview() {
    if (this.dirty()) return this.report('Save project changes before previewing.', true);
    const seed = resolveSeed(Number($('sampler-seed').value)); if ($('sampler-seed').value === '' || !Number.isSafeInteger(seed)) return this.report('Seed must be a safe integer.', true);
    const overrides = {...this.pins};
    const generation = ++this.generation; $('sampler-preview').disabled = true;
    try {
      const result = await this.api('/sampler/proposals', 'POST', {seed, overrides});
      if (generation !== this.generation) return;
      $('sampler-proposal').textContent = pretty({proposal: result.proposal, payload_digest: result.payload_digest, matrix_digest: result.frozen.configuration.matrix_digest, settings_revision: result.frozen.settings_revision});
      this.report('Saved proposal snapshot created. Pins apply only to this request.');
    } catch (e) { this.report('Preview rejected: ' + e.message, true); }
    finally { this.update(); }
  }
}
if (typeof document !== 'undefined' && $('sampler-workbench')) {
  const ui = globalThis.samplerWorkbench = new SamplerWorkbench($('sampler-workbench'));
  // The shell switches views; open the matrix the first time this view is shown.
  document.addEventListener('studio-view', e => { if (e.detail === 'sampler-workbench') ui.open(); });
  window.addEventListener('beforeunload', e => { if (ui.record && ui.dirty()) { e.preventDefault(); e.returnValue = ''; } });
}
