/* Scenario-first projection of the shared sampler. No local sampling or generation. */
const clone = v => structuredClone(v);
const json = v => JSON.stringify(v, null, 2);
const human = v => String(v ?? 'Unavailable').replaceAll('_', ' ');
const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const at = (root, path) => path.reduce((v, k) => v?.[k], root);
const pathText = path => path.join(' / ');

// Include empty containers and every unknown leaf; array indices remain stable in a draft.
export function settingsIndex(value, path = []) {
  if (!value || typeof value !== 'object' || !Object.keys(value).length) return [{path, value}];
  return Object.entries(value).flatMap(([k, v]) => settingsIndex(v, [...path, k]));
}

export function controlPath(matrix, key, value) {
  let path;
  if (key === 'focus.family') path = ['competencies'];
  else if (key === 'focus.behavior') {
    const family = matrix.competencies.findIndex(f => f.behaviors.some(b => b.id === value));
    if (family >= 0) path = ['competencies', family, 'behaviors'];
  } else if (key === 'decision.axis') return ['decision_axes', value];
  else if (key === 'decision.choice') {
    const axis = Object.keys(matrix.decision_axes).find(a => matrix.decision_axes[a].variants.some(v => v.id === value));
    if (axis) path = ['decision_axes', axis, 'variants'];
  } else if (key === 'domain') path = ['domains'];
  else if (matrix.dimensions[key]) path = ['dimensions', key];
  if (!path) return null;
  const i = at(matrix, path).findIndex(v => v.id === value);
  return i < 0 ? null : [...path, i];
}

export class SamplerUI {
  constructor(root, request) { this.root = root; this.api = request; this.pins = {}; this.page = 0; }
  $(id) { return this.root.querySelector('#' + id); }
  button(text, action, cls) {
    const b = el('button', text, cls); b.type = 'button';
    b.onclick = () => this.act(action); return b;
  }
  async act(action) {
    if (this.busy) return;
    try { await action(); } catch (error) { this.$('sampler-message').textContent = error.message; }
  }
  lock(value) { this.busy = value; this.root.inert = value; this.root.setAttribute('aria-busy', String(value)); }
  async init() {
    this.root.innerHTML = `
      <div class="sampler-heading"><div><span class="eyebrow">CONFIGURATION → PROPOSAL → TRAJECTORY</span><h1>What will this sampler ask Kidra to do?</h1><p>Explore one concrete scenario, then follow its attributes back to the matrix.</p></div><span class="sampler-label">Design C · Scenario first</span></div>
      <div class="sampler-scope"><span><b>Project matrix</b> · shared weights & constraints</span><span>Applies to future runs. Saved targets and active-run snapshots stay frozen.</span></div>
      <div class="sampler-actions"><button id="sampler-save">Save matrix & compare</button><button id="sampler-reload">Reload saved matrix</button><span id="sampler-dirty"></span></div>
      <p id="sampler-message" role="status" aria-live="polite"></p>
      <div class="sampler-columns"><div class="sampler-story">
        <div class="sampler-preview-controls"><label>Preview seed <input id="sampler-seed" type="number" step="1" value="137"></label><button id="sampler-preview">Preview saved matrix</button><button id="sampler-vary">Next seed</button><span id="sampler-preview-label">One sampled outcome · no generated text</span></div>
        <details class="sampler-pins"><summary>Preview pins <span id="sampler-pin-count">· none</span></summary><p>Supported per-run overrides, tried here on one proposal. Pins do not edit project weights or start a run.</p><div id="sampler-pin-fields"></div></details>
        <div id="sampler-brief"></div><div id="sampler-comparison"></div>
        <details><summary>Full structured proposal & frozen provenance</summary><pre id="sampler-raw"></pre></details>
      </div><aside class="sampler-settings" aria-label="Matrix settings">
        <div class="eyebrow">FOLLOW AN ATTRIBUTE OR SEARCH EVERYTHING</div><h2>Controlling settings</h2>
        <section id="sampler-inspector"></section>
        <div class="sampler-index"><h3>Complete settings index</h3><label>Search paths, IDs & values <input id="sampler-search" type="search" placeholder="e.g. weight, hard_implications, tolerance"></label><div id="sampler-index-count"></div><div id="sampler-index-results"></div><div class="sampler-pagination"><button id="sampler-prev">Previous</button><button id="sampler-next">Next</button></div></div>
      </aside></div>`;
    const bind = (id, fn) => this.$(id).onclick = () => this.act(fn);
    bind('sampler-save', () => this.save()); bind('sampler-reload', () => this.load());
    bind('sampler-preview', () => this.preview());
    bind('sampler-vary', () => { this.$('sampler-seed').stepUp(); return this.preview(); });
    bind('sampler-prev', () => { this.page--; this.renderIndex(); });
    bind('sampler-next', () => { this.page++; this.renderIndex(); });
    this.$('sampler-search').oninput = () => { this.page = 0; this.renderIndex(); };
    this.$('sampler-seed').oninput = () => this.markPreview();
    await this.load();
    await this.preview();
  }
  async load() {
    const record = await this.api('/sampler');
    this.record = record; this.draft = {matrix: clone(record.configuration.matrix), metric_options: clone(record.configuration.metric_options)};
    this.editPath = null; this.baseline = null; this.comparison = null;
    this.$('sampler-message').textContent = 'Loaded saved matrix. Reload replaces local matrix edits.';
    this.renderPins(); this.renderIndex(); this.inspect(); this.dirty(); this.renderBrief();
  }
  dirty() {
    this.isDirty = json(this.draft) !== json({matrix: this.record.configuration.matrix, metric_options: this.record.configuration.metric_options});
    this.$('sampler-dirty').textContent = this.isDirty ? 'Unsaved project edits · preview still uses saved matrix' : `Saved · revision ${this.record.settings_revision}`;
    this.$('sampler-preview').disabled = this.isDirty;
    this.$('sampler-vary').disabled = this.isDirty;
    this.markPreview();
  }
  seed() {
    const value = this.$('sampler-seed').value;
    if (!value.trim() || !Number.isSafeInteger(Number(value))) throw new Error('Preview seed must be a safe integer.');
    return Number(value);
  }
  markPreview() {
    if (!this.snapshot) return;
    const p = this.snapshot.proposal.sampling_provenance;
    const stale = p.seed !== Number(this.$('sampler-seed').value) || json(p.requested_overrides) !== json(this.pins) || this.snapshot.frozen.configuration.matrix_digest !== this.record.configuration.matrix_digest;
    this.$('sampler-preview-label').textContent = stale ? 'Previous outcome shown · preview again for current seed / pins / matrix' : 'One sampled outcome · no generated text';
  }
  async preview() {
    if (this.isDirty) throw new Error('Save the matrix to compare this seed against the edited configuration.');
    const seed = this.seed(), overrides = clone(this.pins);
    this.lock(true);
    try {
      const snapshot = await this.api('/sampler/proposals', 'POST', {seed, overrides});
      if (snapshot.frozen.settings_revision !== this.record.settings_revision) throw new Error('Project settings changed elsewhere. Reload the saved matrix before previewing; local edits are retained.');
      this.snapshot = snapshot; this.$('sampler-comparison').replaceChildren(); this.renderBrief(); this.markPreview();
      this.$('sampler-message').textContent = `Proposal saved as ${snapshot.id}. Preview creates a proposal record only; no run or model call.`;
    } finally { this.lock(false); }
  }
  async save() {
    if (this.editorChanged) throw new Error('Apply or discard the field edit before saving the matrix.');
    this.seed();
    // Capture a baseline only when its seed and pins match; never imply unrelated draws are comparable.
    const prior = this.snapshot;
    this.baseline = prior && prior.proposal.sampling_provenance.seed === this.seed() && json(prior.proposal.sampling_provenance.requested_overrides) === json(this.pins) ? prior : null;
    this.lock(true);
    try {
      this.record = await this.api('/sampler', 'PUT', {expected_revision: this.record.settings_revision, ...clone(this.draft)});
      this.draft = {matrix: clone(this.record.configuration.matrix), metric_options: clone(this.record.configuration.metric_options)};
      this.renderPins(); this.renderIndex(); this.dirty(); this.inspect(this.editPath);
    } finally { this.lock(false); }
    try { await this.preview(); this.renderComparison(); }
    catch (error) { throw new Error(`Matrix saved at revision ${this.record.settings_revision}; proposal preview failed: ${error.message}`); }
  }
  renderPins() {
    const fields = this.$('sampler-pin-fields'); fields.replaceChildren();
    for (const [key, values] of Object.entries(this.record.overrides)) {
      const label = el('label', human(key)); const select = el('select'); select.setAttribute('aria-label', 'Preview pin ' + key);
      const missing = this.pins[key] && !values.includes(this.pins[key]) ? [[this.pins[key], 'Unavailable pin: ' + human(this.pins[key])]] : [];
      for (const [value, text] of [['', 'Sample freely'], ...missing, ...values.map(v => [v, human(v)])]) { const o = el('option', text); o.value = value; select.append(o); }
      select.value = this.pins[key] || '';
      select.onchange = () => { if (select.value) this.pins[key] = select.value; else delete this.pins[key]; this.markPreview(); this.pinCount(); };
      label.append(select); fields.append(label);
    }
    this.pinCount();
  }
  pinCount() { this.$('sampler-pin-count').textContent = `· ${Object.keys(this.pins).length} pinned`; }
  link(key, value, title) {
    return this.button(title || human(value), () => {
      const path = controlPath(this.draft.matrix, key, value);
      if (path) this.inspect(['matrix', ...path], key);
      else { this.$('sampler-search').value = key; this.page = 0; this.renderIndex(); }
      this.$('sampler-inspector').scrollIntoView({block: 'nearest', behavior: 'smooth'});
    }, 'sampler-attribute');
  }
  renderBrief() {
    const area = this.$('sampler-brief'); area.replaceChildren();
    if (!this.snapshot) return;
    const p = this.snapshot.proposal, s = p.scenario, l = p.learning_objective;
    const card = el('article', undefined, 'sampler-hero');
    card.append(el('div', `SAMPLED SCENARIO · SEED ${p.sampling_provenance.seed}`, 'eyebrow'), el('h2', human(s.domain.suggested_project || s.domain.description)));
    const context = el('p'); context.append('In ', this.link('domain', s.domain.id), ', the project is ', this.link('lifecycle', s.project_state.lifecycle), '. ', this.link('complexity', s.project_state.complexity), ` complexity; ${human(s.project_state.information_state)} information; ${human(s.project_state.incident)} incident.`);
    card.append(context, el('p', `Episode goal: ${human(s.project_state.episode_goal)}. Entry: ${human(s.project_state.entry_state)}. Timescale: ${human(s.project_state.project_timescale)}.`));
    const learning = el('section', undefined, 'sampler-learning');
    const focus = el('div'); focus.append(el('span', '01 · COMPETENCY ', 'eyebrow'), this.link('focus.family', l.primary_family), ' → ', this.link('focus.behavior', l.primary_behavior));
    const decision = el('p'); decision.append('Decision: ', this.link('decision.axis', l.decision_axis), ' → ', this.link('decision.choice', l.target_decision));
    learning.append(focus, el('h3', l.description), decision, el('div', 'WHEN THIS IS THE RIGHT DECISION', 'eyebrow'), el('p', l.condition_making_decision_valid), el('div', 'TRAJECTORY SHOULD DEMONSTRATE', 'eyebrow'), el('p', l.correct_coordination_behavior));
    if (l.secondary?.length) learning.append(el('p', 'Secondary objectives: ' + l.secondary.map(x => x.behavior || x.family || json(x)).join(', ')));
    card.append(learning); area.append(card);
    const grid = el('div', undefined, 'sampler-attributes');
    const groups = [
      ['02 · User & collaboration', Object.entries(s.user).map(([k,v]) => ['user_' + k,v])],
      ['03 · Constraints & resources', [['resource_posture',s.resources.posture], ['tool_access',s.resources.tool_access], ['agent_reliability',s.resources.agent_reliability], ['dependency_pattern',s.project_state.dependency_pattern], ['information_state',s.project_state.information_state], ['incident',s.project_state.incident]]],
      ['04 · Execution shape', [['execution_pattern',s.execution.pattern], ['observation_source',s.execution.observation_source], ['window_role_profile',s.execution.window_role_profile], ['episode_horizon',s.execution.episode_horizon], ['episode_goal',s.project_state.episode_goal]]]
    ];
    for (const [title, pairs] of groups) {
      const c = el('article', undefined, 'sampler-card'); c.append(el('h3', title));
      for (const [key,value] of pairs) { const row = el('div', undefined, 'sampler-pair'); row.append(el('span', human(key)), this.link(key,value)); c.append(row); }
      grid.append(c);
    }
    area.append(grid);
    const targets = el('article', undefined, 'sampler-targets'); targets.append(el('h3','Suggested trajectory budget'), el('p','Sampled targets, not measured outcomes. Constraints can change which combinations are feasible.'));
    const budgets = {
      suggested_coordination_events: ['Coordination events', 'episode_horizon', s.execution.episode_horizon],
      suggested_task_count: ['Tasks', 'complexity', s.project_state.complexity],
      suggested_dependency_depth: ['Dependency depth (edges)', 'complexity', s.project_state.complexity],
      available_agent_count: ['Available specialist agents', 'team_posture', p.metrics.sampling.team_posture],
      target_active_window_capacity: ['Active window capacity (windows)', 'complexity', s.project_state.complexity],
      suggested_tool_observations_or_calls: ['Tool observations / calls', 'complexity', s.project_state.complexity]
    };
    for (const [key, value] of Object.entries(p.metrics.numeric_targets)) {
      const [label, axis, choice] = budgets[key] || [human(key), 'complexity', s.project_state.complexity];
      const b = this.button(`${value} · ${label}`, () => {
        const path = controlPath(this.draft.matrix, axis, choice);
        this.inspect(['matrix', ...path], axis);
      }, 'sampler-target'); targets.append(b);
    }
    targets.append(el('p','Budget controls: episode horizon → events; complexity → task count, depth, window and tool ranges; team posture → agents. Dependency pattern, execution minimums, tool access and observation source also constrain these targets.'));
    const tags = el('p', 'Event evidence: ');
    for (const tag of s.additional_event_tags) tags.append(this.button(human(tag.id), () => this.inspect(['matrix','event_tags',tag.id]), 'sampler-attribute'), ' ');
    targets.append(tags); area.append(targets);
    this.$('sampler-raw').textContent = json(this.snapshot);
  }
  renderComparison() {
    const box = this.$('sampler-comparison'); box.replaceChildren();
    if (!this.baseline) { box.append(el('p','No matching baseline. Preview with the same seed and pins before an edit to compare.')); return; }
    const before = this.baseline.proposal.metrics, after = this.snapshot.proposal.metrics;
    box.append(el('h3', `Before → after · fixed seed ${this.seed()} & identical pins`), el('p','One paired proposal illustrates a consequence; it does not estimate frequency.'));
    box.append(el('p', `Scenario: ${this.baseline.proposal.scenario.domain.suggested_project} → ${this.snapshot.proposal.scenario.domain.suggested_project}`));
    const paths = new Map([...settingsIndex(before), ...settingsIndex(after)].filter(x => x.path[0] !== 'actual').map(x => [json(x.path),x.path]));
    const a = [...paths.values()].map(path => ({path,value:at(before,path)}));
    const changed = a.filter(x => json(x.value) !== json(at(after, x.path)));
    for (const entry of changed) box.append(el('p', `${pathText(entry.path)}: ${human(entry.value)} → ${human(at(after, entry.path))}`));
    if (!changed.length) box.append(el('p','No target changes for this seed. Other seeds may produce different outcomes.'));
  }
  inspect(path = null, key = null) {
    if (this.editorChanged && path) throw new Error('Apply or discard the current field edit before opening a setting.');
    const box = this.$('sampler-inspector'); box.replaceChildren(); this.editPath = path; this.editorChanged = false;
    if (!path) { box.append(el('p','Click an underlined scenario attribute to inspect its options, weights and constraints. Or search any field below.')); return; }
    const value = at(this.draft, path);
    box.append(el('div','PROJECT EDIT · FUTURE RUNS', 'eyebrow'), el('h3', value?.id ? human(value.id) : pathText(path)), el('small',pathText(path)), el('p','Weights are relative and nonnegative, not observed percentages. Conditions, hard implications and compatibility filters also affect the draw.'));
    for (const field of ['objective','description','scenario_condition','target_behavior']) if (value?.[field]) box.append(el('p',human(field) + ': ' + value[field]));
    if (value?.allowed_lifecycle) box.append(el('p','Eligible lifecycle: ' + value.allowed_lifecycle.map(human).join(', ')));
    if (value?.min_complexity !== undefined || value?.max_complexity !== undefined) box.append(el('p',`Complexity level: ${value.min_complexity ?? 'unbounded'}–${value.max_complexity ?? 'unbounded'} (matrix levels).`));
    if (value?.required_event_tags?.length) box.append(el('p','Required evidence tags: ' + value.required_event_tags.map(human).join(', ')));
    if (key) {
      const optionsPath = key === 'decision.axis' ? [...path,'variants'] : path.slice(0,-1);
      const opts = at(this.draft, optionsPath);
      if (Array.isArray(opts)) {
        const choices = el('div', undefined,'sampler-option-list');
        for (let i=0; i<opts.length; i++) if (opts[i]?.id) choices.append(this.button(`${human(opts[i].id)}${opts[i].weight !== undefined ? ' · weight ' + opts[i].weight : ''}`, () => this.inspect([...optionsPath,i],key)));
        box.append(el('h4','Options & relative weights'), choices);
      }
      const pin = this.$('sampler-pin-fields').querySelector(`[aria-label="Preview pin ${key}"]`);
      box.append(el('p', pin ? `Per-run pin supported for ${key}. Set it under Preview pins; it will not change project weights.` : 'This attribute has no supported per-run pin. Edit its matrix settings for future draws.'));
    }
    const label = el('label', 'Value / option JSON (retains custom fields)');
    const editor = el('textarea'); editor.id = 'sampler-field'; editor.value = json(value); editor.spellcheck = false; editor.setAttribute('aria-label','Setting JSON');
    editor.oninput = () => { this.editorChanged = true; this.$('sampler-message').textContent = 'Field text changed. Apply field edit to include it in the project draft.'; };
    const numericFields = [];
    for (const [field, title] of [['weight','Relative project weight'],['p','Event inclusion probability (0–1)']]) {
      if (typeof value?.[field] !== 'number') continue;
      const numericLabel = el('label',title), input = el('input'); input.type = 'number'; input.step = 'any'; input.min = '0'; if (field === 'p') input.max = '1';
      input.value = value[field]; input.setAttribute('aria-label',title); numericFields.push(input);
      input.oninput = () => {
        this.editorChanged = true;
        if (!input.value || !input.checkValidity()) return;
        try { const option = JSON.parse(editor.value); option[field] = Number(input.value); editor.value = json(option); }
        catch { this.$('sampler-message').textContent = 'Correct the JSON before changing a numeric field.'; }
      };
      numericLabel.append(input); box.append(numericLabel);
    }
    label.append(editor); box.append(label, this.button('Apply field edit', () => {
      if (numericFields.some(input => !input.value || !input.checkValidity())) throw new Error('Use a finite, nonnegative weight or a probability from 0 to 1.');
      const replacement = JSON.parse(editor.value);
      const parent = at(this.draft,path.slice(0,-1)); parent[path.at(-1)] = replacement;
      this.editorChanged = false; this.dirty(); this.renderIndex(); this.inspect(path,key); this.$('sampler-message').textContent = 'Field edit applied to the local draft. Save matrix & compare to use it in future runs.';
    }), this.button('Discard field edit', () => { this.editorChanged = false; this.inspect(path,key); }));
    const id = value?.id || (typeof value === 'string' ? value : key);
    if (id) {
      const related = ['conditional_weights','hard_implications'].flatMap(section => (this.draft.matrix[section] || []).flatMap((rule,i) => json(rule).includes(id) ? [{section,i,rule}] : []));
      if (related.length) { box.append(el('h4','Related weight rules & hard implications'));
        for (const r of related) { const d = el('details'); d.append(el('summary',r.section + ' / ' + r.i),el('pre',json(r.rule)),this.button('Edit rule', () => this.inspect(['matrix',r.section,r.i]))); box.append(d); }
      }
    }
  }
  renderIndex() {
    const query = this.$('sampler-search').value.toLowerCase();
    const entries = settingsIndex(this.draft).map(e => ({...e, ids: e.path.map((_,i) => at(this.draft,e.path.slice(0,i))?.id).filter(Boolean).join(' / ')})).filter(e => (pathText(e.path) + ' ' + e.ids + ' ' + json(e.value)).toLowerCase().includes(query));
    this.page = Math.max(0, Math.min(this.page, Math.ceil(entries.length/40)-1));
    const start = this.page*40, rows = entries.slice(start,start+40);
    this.$('sampler-index-count').textContent = `${entries.length ? start+1 : 0}–${start+rows.length} of ${entries.length} fields · includes custom settings & metric options`;
    this.$('sampler-index-results').replaceChildren(...rows.map(e => this.button(`${pathText(e.path)}${e.ids ? ' [' + e.ids + ']' : ''} = ${json(e.value)}`, () => this.inspect(e.path), 'sampler-index-row')));
    this.$('sampler-prev').disabled = !this.page; this.$('sampler-next').disabled = start+40 >= entries.length;
    if (!entries.length) this.$('sampler-index-results').append(el('p','No matching settings. Try a shorter path or ID.'));
  }
}
