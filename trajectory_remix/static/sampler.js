/* UI A: guided editing over the shared sampler REST contract. No sampling logic. */
const $ = id => document.getElementById(id);
const clone = value => structuredClone(value);
export const human = value => String(value).replaceAll('_', ' ').replaceAll('.', ' › ').replace(/^./, c => c.toUpperCase());
const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const action = (text, fn) => { const b = el('button', text); b.type = 'button'; b.onclick = fn; return b; };
async function request(path, method = 'GET', body) {
  const response = await fetch('/api/v1' + path, {method, headers: {'Content-Type': 'application/json', 'X-Remix-Token': document.querySelector('meta[name=remix-token]').content}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) throw new Error(`${response.status === 409 ? 'Project settings changed elsewhere. Your draft is retained. Reconcile with the latest settings before saving. ' : ''}${data.message || JSON.stringify(data)}`);
  return data;
}
const help = {
  weight: 'Relative weight, not a percentage. Higher values favor this choice among eligible candidates; 0 removes its base weight. Joint balancing and conditional factors also apply.',
  p: 'Probability from 0 to 1, only when this event is eligible. Required events and constraints can override this base probability.',
  factor: 'Multiplier applied when the rule matches. 1 leaves the base weight unchanged.',
  min_complexity: 'Minimum complexity level: 0 trivial, 1 small, 2 medium, 3 large.',
  max_complexity: 'Maximum complexity level: 0 trivial, 1 small, 2 medium, 3 large.',
  requires: 'Eligible only when these scenario choices match.',
  allowed_lifecycle: 'This behavior is eligible only in these project phases.',
  required_event_tags: 'These events must be present and compatible with the scenario.',
  absolute: 'Allowed difference in the metric’s own units. No shared default.',
  relative: 'Allowed fractional difference: 0.05 means 5%. No shared default.',
  target: 'Key of the linked sampled numeric target. This is not a measured value.',
};

export class GuidedSampler {
  constructor() { this.pins = {}; this.version = 0; this.busy = false; }
  bind() {
    $('generation-settings-button').onclick = () => this.guard(async () => {
      if (!this.record) await this.load();
      $('sampler-dialog').showModal();
      $('sampler-search').focus();
    });
    $('sampler-close').onclick = () => $('sampler-dialog').close();
    // Closing retains the draft; only the explicitly labeled reload discards it.
    $('sampler-search').oninput = () => this.filter();
    $('sampler-expand').onclick = () => this.sections.filter(s => !s.root.hidden).forEach(s => { s.root.open = true; });
    $('sampler-collapse').onclick = () => this.sections.forEach(s => { s.root.open = false; });
    $('sampler-save').onclick = () => this.guard(() => this.save());
    $('sampler-discard').onclick = () => this.guard(() => this.load());
    $('sampler-preview').onclick = () => this.guard(() => this.preview());
    $('sampler-clear-pins').onclick = () => { this.pins = {}; this.renderPins(); this.changed(false); };
    $('sampler-seed').oninput = () => this.changed(false);
    const tabs = ['matrix', 'pins'];
    const select = name => tabs.forEach(key => {
      const active = key === name, tab = $(`sampler-${key}-tab`);
      tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
      $(`sampler-${key}-panel`).hidden = !active;
    });
    tabs.forEach(name => {
      const tab = $(`sampler-${name}-tab`);
      tab.onclick = () => select(name);
      tab.onkeydown = e => {
        if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) {
          e.preventDefault(); const next = e.key === 'Home' ? 'matrix' : e.key === 'End' ? 'pins' : tabs.find(x => x !== name);
          select(next); $(`sampler-${next}-tab`).focus();
        }
      };
    });
  }
  async guard(fn) {
    if (this.busy) return;
    this.busy = true; this.updateButtons(); $('sampler-error').textContent = '';
    try { await fn(); } catch (e) {
      $('sampler-error').textContent = e.message;
      if ($('sampler-dialog').open) $('sampler-error').focus();
      else $('status').textContent = e.message;
    } finally { this.busy = false; this.updateButtons(); }
  }
  async load() {
    this.record = await request('/sampler');
    this.matrix = clone(this.record.configuration.matrix);
    this.metricOptions = clone(this.record.configuration.metric_options);
    this.dirty = false; this.version++; this.last = null;
    $('sampler-result').replaceChildren(); $('sampler-preview-state').textContent = 'No preview yet.';
    $('sampler-search').value = ''; this.render(); this.renderPins(); this.recipe();
    $('sampler-state').textContent = `Saved project recipe · revision ${this.record.settings_revision}`;
  }
  changed(matrix = true) {
    if (matrix) this.dirty = true;
    this.version++;
    $('sampler-state').textContent = this.dirty ? 'Unsaved matrix edits · future runs only' : `Saved project recipe · revision ${this.record.settings_revision}`;
    if (this.last) $('sampler-preview-state').textContent = 'Previous example · recipe, pins or seed changed. Preview again to compare.';
    this.recipe(); this.updateButtons();
  }
  updateButtons() {
    $('sampler-save').disabled = this.busy || !this.dirty;
    $('sampler-preview').disabled = this.busy || this.dirty || !this.record;
    $('sampler-discard').disabled = this.busy;
  }
  recipe() {
    const m = this.matrix; if (!m) return;
    const families = m.competencies.filter(x => x.weight > 0);
    const leaders = families.slice().sort((a, b) => b.weight - a.weight).slice(0, 3).map(x => `${human(x.id)} (${x.weight})`).join(', ') || 'none';
    const list = el('ul');
    for (const text of [
      `${families.length} competency families with positive base weight; one primary behavior per proposal. Largest base weights: ${leaders}.`,
      'Scenario choices are sampled together with eligibility rules. Base weights are not guaranteed final percentages.',
      `${m.conditional_weights.length} conditional weight rules and ${m.hard_implications.length} hard implications refine the choices.`,
      Object.keys(this.pins).length ? 'Pinned for preview: ' + Object.entries(this.pins).map(([k, v]) => `${human(k)} = ${human(v)}`).join('; ') + '.' : 'No pins: all supported choices are Any.',
      this.dirty ? 'Unsaved changes shown here. Save before previewing this recipe.' : 'Preview uses the saved project recipe.',
    ]) list.append(el('li', text));
    $('sampler-recipe').replaceChildren(list);
    const caption = this.sections?.[0]?.root.querySelector('summary > small');
    if (caption) caption.textContent = `${families.length} families with positive base weight · largest: ${leaders}`;
  }
  field(parent, key, path, label = human(key)) {
    const value = parent[key];
    if (value !== null && typeof value === 'object') return this.tree(value, label, path);
    const wrap = el('label', undefined, 'sampler-field'); wrap.append(el('span', label));
    const input = el(typeof value === 'string' && value.length > 110 ? 'textarea' : 'input');
    input.dataset.path = path; input.setAttribute('aria-label', `${label} · ${path}`);
    if (typeof value === 'boolean') { input.type = 'checkbox'; input.checked = value; }
    else if (typeof value === 'number') {
      input.type = 'number'; input.step = 'any'; input.value = value; input.required = true;
      if (['weight', 'p', 'factor', 'absolute', 'relative', 'min_complexity', 'max_complexity'].includes(key)) input.min = '0';
      if (key === 'p') input.max = '1';
    } else { input.value = value === null ? '' : value; input.readOnly = key === 'id' || key === 'schema_version'; }
    input.oninput = () => {
      if (input.type === 'number' && (!input.validity.valid || !Number.isFinite(input.valueAsNumber))) {
        input.setAttribute('aria-invalid', 'true'); this.changed(); return;
      }
      input.removeAttribute('aria-invalid');
      parent[key] = typeof value === 'boolean' ? input.checked : typeof value === 'number' ? input.valueAsNumber : input.value;
      this.changed();
    };
    wrap.append(input);
    if (help[key]) wrap.append(el('small', help[key]));
    if (input.readOnly) wrap.append(el('small', 'Stable source identity; retained so references stay linked.'));
    return wrap;
  }
  tree(value, label, path) {
    const details = el('details', undefined, 'sampler-tree');
    details.append(el('summary', `${label} · ${Object.keys(value).length} ${Array.isArray(value) ? 'items' : 'fields'}`));
    if (help[path.split('.').at(-1)]) details.append(el('p', help[path.split('.').at(-1)]));
    let built = false;
    details.addEventListener('toggle', () => {
      if (!details.open || built) return; built = true;
      Object.keys(value).forEach(k => details.append(this.field(value, k, `${path}.${k}`, Array.isArray(value) ? (value[k]?.id ? human(value[k].id) : `Item ${Number(k) + 1}`) : human(k))));
    });
    return details;
  }
  card(value, path, title) {
    const card = el('article', undefined, 'sampler-card');
    card.dataset.search = `${title} ${path} ${JSON.stringify(value)}`.toLowerCase().replaceAll('_', ' ');
    card.append(el('h4', human(title)), el('p', value.description || value.objective || value.scenario_condition || value.target_behavior || (Array.isArray(value) ? `${value.length} choices. Read what each means before changing its relative weight.` : value.behaviors ? 'Choose the kind of coordination skill to practice. Each proposal focuses on one eligible behavior.' : 'Explore the choices and rules in this group.')));
    const children = Array.isArray(value) && value.every(v => v && typeof v === 'object' && v.id) ? value : value.behaviors || value.variants;
    if (children) {
      const explore = el('details'); explore.append(el('summary', `Explore ${children.length} ${value.behaviors ? 'behaviors' : 'choices'} & explanations`));
      let expanded = false;
      explore.addEventListener('toggle', () => {
        if (!explore.open || expanded) return; expanded = true;
        children.forEach((v, i) => explore.append(this.card(v, `${path}${value.behaviors ? '.behaviors' : value.variants ? '.variants' : ''}.${i}`, v.id)));
      });
      card.append(explore);
    }
    if (value.allowed_lifecycle || value.min_complexity !== undefined || value.requires) {
      const phases = value.allowed_lifecycle?.map(human).join(', ');
      card.append(el('p', `Eligibility: ${phases ? 'phases ' + phases + '. ' : ''}${value.min_complexity !== undefined ? `complexity level ${value.min_complexity}–${value.max_complexity ?? 3}. ` : ''}${value.requires ? 'Requires ' + Object.entries(value.requires).map(([k, v]) => `${human(k)}: ${[].concat(v).map(human).join(' or ')}`).join('; ') + '.' : ''}`));
    }
    const advanced = el('details'); advanced.append(el('summary', 'Edit weights, choices & eligibility'));
    let built = false;
    advanced.addEventListener('toggle', () => {
      if (!advanced.open || built) return; built = true;
      Object.keys(value).filter(k => !children || (!Array.isArray(value) && !['behaviors', 'variants'].includes(k))).forEach(k => advanced.append(this.field(value, k, `${path}.${k}`)));
      if (Array.isArray(value) && children) advanced.append(el('p', 'Open a choice above to edit its weight and eligibility.'));
    });
    card.append(advanced); return card;
  }
  section(title, description, entries) {
    const root = el('details', undefined, 'sampler-section'), summary = el('summary', title), caption = el('small', description);
    summary.append(caption); root.append(summary);
    const body = el('div', undefined, 'sampler-section-body'); root.append(body);
    const cards = entries.map(([value, path, label]) => this.card(value, path, label)); body.append(...cards);
    this.sections.push({root, cards}); $('sampler-sections').append(root);
    return root;
  }
  render() {
    $('sampler-sections').replaceChildren(); this.sections = []; const m = this.matrix;
    const asEntries = (obj, path) => Object.entries(obj).map(([k, v]) => [v, `${path}.${k}`, k]);
    this.section('1 · What should the agent practice?', `${m.competencies.length} competency families · open a family to see its behaviors`, m.competencies.map((v, i) => [v, `competencies.${i}`, v.id]));
    this.section('2 · Set the scene & people', `${m.domains.length} domains · project phases, complexity and user characteristics`, [
      ...m.domains.map((v, i) => [v, `domains.${i}`, v.id]),
      ...asEntries(Object.fromEntries(Object.entries(m.dimensions).filter(([k]) => /^(user_|lifecycle|complexity|entry_state|recurrence|project_timescale)/.test(k))), 'dimensions')]);
    this.section('3 · Tools, resources & coordination', 'Tool access, agent availability, execution and observation choices', asEntries(Object.fromEntries(Object.entries(m.dimensions).filter(([k]) => /tool|resource|agent|team|execution|dependency|window|observation/.test(k))), 'dimensions'));
    const assigned = /^(user_|lifecycle|complexity|entry_state|recurrence|project_timescale)|tool|resource|agent|team|execution|dependency|window|observation/;
    this.section('4 · Conditions & constraints', `${Object.keys(m.event_tags).length} event tags · eligibility and remaining scenario dimensions`, [
      ...asEntries(Object.fromEntries(Object.entries(m.dimensions).filter(([k]) => !assigned.test(k))), 'dimensions'), ...asEntries(m.event_tags, 'event_tags')]);
    this.section('5 · Advanced weighting & decisions', `${Object.keys(m.decision_axes).length} decision axes · conditional multipliers and hard implications`, [
      ...asEntries(m.decision_axes, 'decision_axes'), [m.conditional_weights, 'conditional_weights', 'Conditional weights'], [m.hard_implications, 'hard_implications', 'Hard implications'],
      ...Object.entries(m).filter(([k]) => !['competencies', 'dimensions', 'domains', 'decision_axes', 'event_tags', 'conditional_weights', 'hard_implications', 'metrics_schema'].includes(k)).map(([k, v]) => [typeof v === 'object' && v !== null ? v : {[k]: v}, `matrix.${k}`, k])]);
    // Scalar top-level edits must write through to the original matrix, too.
    const top = this.sections.at(-1).cards;
    Object.entries(m).filter(([k, v]) => v === null || typeof v !== 'object').forEach(([k]) => {
      const card = top.find(c => c.dataset.search.startsWith(k.replaceAll('_', ' ') + ' '));
      if (card) { card.replaceChildren(el('h4', human(k)), this.field(m, k, k)); }
    });
    this.metricsSection(); this.filter();
  }
  metricsSection() {
    const defs = this.record.configuration.definitions;
    const root = this.section('6 · Linked metrics & tolerances', `${Object.keys(defs).length} shared definitions · targets and observations stay separate`, []);
    const body = root.querySelector('.sampler-section-body');
    body.append(el('p', 'Matrix choices define the category registry automatically when saved. These per-metric display and tolerance options apply to future definitions. Source counts can be calculated explicitly in Dataset Metrics; export measures need a compiler/profile. Semantic actuals remain unavailable until annotated.'));
    Object.entries(defs).forEach(([key, def]) => {
      const card = el('article', undefined, 'sampler-card'); card.dataset.search = `${key} ${JSON.stringify(def)}`.toLowerCase().replaceAll('_', ' ');
      card.append(el('h4', human(key)), el('p', `${human(def.scope)} · ${def.unit || 'no unit specified'} · ${def.choices ? def.choices.length + ' linked choices' : 'observed values may be unavailable'}`));
      const details = el('details'); details.append(el('summary', 'Customize label, unit, target & tolerance'));
      let built = false;
      details.addEventListener('toggle', () => {
        if (!details.open || built) return; built = true;
        const edit = this.metricOptions[key] || {};
        for (const field of ['label', 'unit', 'target']) {
          const wrap = el('label', undefined, 'sampler-field'); wrap.append(el('span', human(field)));
          const input = el('input'); input.value = edit[field] ?? ''; input.placeholder = def[field] || 'Not set'; input.dataset.path = `metrics.${key}.${field}`;
          input.oninput = () => { this.metricOptions[key] ||= {}; if (input.value) this.metricOptions[key][field] = input.value; else delete this.metricOptions[key][field]; this.changed(); };
          wrap.append(input); if (help[field]) wrap.append(el('small', help[field])); details.append(wrap);
        }
        let absolute, relative;
        const update = () => {
          this.metricOptions[key] ||= {};
          const empty = !absolute.value && !relative.value;
          const valid = empty || [absolute, relative].every(i => i.value !== '' && Number.isFinite(i.valueAsNumber) && i.valueAsNumber >= 0);
          for (const i of [absolute, relative]) i.setCustomValidity(valid ? '' : 'Set both nonnegative tolerance bands, or clear both.');
          if (valid) {
            if (empty) delete this.metricOptions[key].tolerance;
            else this.metricOptions[key].tolerance = {absolute: absolute.valueAsNumber, relative: relative.valueAsNumber};
          }
          this.changed();
        };
        [absolute, relative] = ['absolute', 'relative'].map(field => {
          const wrap = el('label', undefined, 'sampler-field'); wrap.append(el('span', field === 'absolute' ? `Absolute tolerance (${edit.unit || def.unit || 'metric units'})` : 'Relative tolerance (fraction)'));
          const input = el('input'); input.type = 'number'; input.min = '0'; input.step = 'any'; input.value = edit.tolerance?.[field] ?? ''; input.placeholder = 'Not set'; input.dataset.path = `metrics.${key}.${field}`; input.oninput = update;
          wrap.append(input, el('small', help[field])); details.append(wrap); return input;
        });
      });
      card.append(details); body.append(card); this.sections.at(-1).cards.push(card);
    });
  }
  filter() {
    const query = $('sampler-search').value.toLowerCase().trim().replaceAll('_', ' '); let count = 0;
    for (const {root, cards} of this.sections) {
      let matches = 0;
      for (const card of cards) { card.hidden = !!query && !card.dataset.search.includes(query); if (!card.hidden) matches++; }
      root.hidden = !matches; if (query && matches) root.open = true; count += matches;
    }
    $('sampler-matches').textContent = query ? `${count} matching groups` : 'All settings';
  }
  renderPins() {
    $('sampler-pins').replaceChildren();
    for (const [key, choices] of Object.entries(this.record.overrides)) {
      const label = el('label', undefined, 'sampler-field'); label.append(el('span', human(key)));
      const select = el('select'); select.dataset.pin = key;
      for (const choice of ['', ...choices]) { const option = el('option', choice ? human(choice) : 'Any · let the sampler choose'); option.value = choice; select.append(option); }
      select.value = this.pins[key] || ''; select.onchange = () => { if (select.value) this.pins[key] = select.value; else delete this.pins[key]; this.changed(false); };
      label.append(select); $('sampler-pins').append(label);
    }
  }
  validate() {
    const invalid = [...$('sampler-matrix-panel').querySelectorAll('input,textarea')].find(n => !n.validity.valid);
    if (!invalid) return true;
    for (let p = invalid.parentElement; p; p = p.parentElement) { if (p.tagName === 'DETAILS') p.open = true; if (p.classList.contains('sampler-card')) p.hidden = false; }
    invalid.focus(); invalid.reportValidity(); return false;
  }
  async save() {
    if (!this.validate()) throw new Error('Check the highlighted setting before saving.');
    // Snapshot the request. If editing continues during the PUT, retain the newer draft.
    const version = this.version;
    const record = await request('/sampler', 'PUT', {matrix: clone(this.matrix), metric_options: clone(this.metricOptions), expected_revision: this.record.settings_revision});
    this.record = record;
    if (version === this.version) this.dirty = false;
    this.changed(false);
    $('sampler-state').textContent = this.dirty ? 'Saved earlier edits; newer changes still unsaved.' : `Saved for future runs · revision ${record.settings_revision}`;
  }
  async preview() {
    const seed = $('sampler-seed').valueAsNumber;
    if (!Number.isSafeInteger(seed)) throw new Error('Use a whole-number seed within the safe integer range.');
    if (this.dirty) throw new Error('Save matrix edits before previewing.');
    const version = this.version, pins = clone(this.pins);
    const snapshot = await request('/sampler/proposals', 'POST', {seed, overrides: pins});
    if (version !== this.version) { $('sampler-preview-state').textContent = 'Settings changed during preview. Preview again.'; return; }
    if (snapshot.frozen.configuration.matrix_digest !== this.record.configuration.matrix_digest || snapshot.frozen.configuration.registry_digest !== this.record.configuration.registry_digest) {
      throw new Error('The saved project recipe changed elsewhere. Reload to inspect it before previewing. Your draft is retained.');
    }
    const p = snapshot.proposal, focus = p.learning_objective, scenario = p.scenario;
    const result = $('sampler-result'); result.replaceChildren(el('h3', human(focus.primary_behavior)), el('p', focus.description));
    const dl = el('dl');
    for (const [label, value] of [
      ['Practice', human(focus.primary_family)], ['Decision to demonstrate', focus.correct_coordination_behavior], ['Why this decision fits', focus.condition_making_decision_valid],
      ['Setting', `${human(scenario.domain.id)} · ${scenario.domain.suggested_project}`],
      ['Project', `${human(scenario.project_state.lifecycle)} · ${human(scenario.project_state.complexity)} · ${human(scenario.project_state.entry_state)}`],
      ['Resources', `${human(scenario.resources.tool_access)} tools · ${scenario.execution.available_agent_count} available agents`],
      ['Requested targets', Object.entries(p.metrics.numeric_targets).map(([k, v]) => `${human(k)}: ${v}`).join('; ')],
    ]) { dl.append(el('dt', label), el('dd', value)); }
    result.append(dl, el('p', 'These are sampled targets, not measured trajectory metrics. Unknown actuals remain unavailable.'));
    if (this.last) {
      const before = {...this.last.proposal.metrics.sampling, ...this.last.proposal.metrics.numeric_targets};
      const changes = Object.entries({...p.metrics.sampling, ...p.metrics.numeric_targets}).filter(([k, v]) => before[k] !== v).map(([k, v]) => `${human(k)}: ${human(before[k])} → ${human(v)}`);
      result.append(el('p', changes.length ? 'Changed since previous preview: ' + changes.join('; ') : 'Same sampled choices as the previous preview.'));
    }
    const details = el('details'); details.append(el('summary', 'Proposal provenance & full targets'), el('pre', JSON.stringify({id: snapshot.id, provenance: p.sampling_provenance, targets: p.metrics}, null, 2))); result.append(details);
    this.last = snapshot; $('sampler-preview-state').textContent = `Seed ${seed} · saved revision ${snapshot.frozen.settings_revision}. One proposal snapshot saved; no trajectory created.`;
  }
}
if (typeof document !== 'undefined' && $('sampler-dialog')) { globalThis.guidedSampler = new GuidedSampler(); globalThis.guidedSampler.bind(); }
