/* Form editors over the existing project-settings DTO. No network or credentials. */
const copy = value => structuredClone(value);
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const button = (text, run) => { const n = node('button', text); n.type = 'button'; n.onclick = run; return n; };
const field = (text, control) => { const n = node('label', text); n.append(control); return n; };
const heading = (box, title, help) => { box.append(node('h3', title)); if (help) box.append(node('p', help, 'settings-help')); };
const controls = ['bos', 'eos', 'noop', 'ctx_begin', 'ctx_end', 'tool_begin', 'tool_end', 'response_begin', 'response_end', 'msg_begin', 'msg_end', 'window_ref'];
const validId = id => /^[a-z][a-z0-9_-]*$/.test(id);
function editor(container, initial, options) {
  // Read the shared draft on EVERY edit: tools, advanced JSON and both dialogs
  // all edit the same record without overwriting each other's unsaved changes.
  let local = copy(initial);
  const current = () => copy(options.getSettings ? options.getSettings() : local);
  const write = change => { const next = current(); change(next); local = next; options.onChange?.(copy(next)); return next; };
  const status = node('p', '', 'settings-feedback'); status.setAttribute('role', 'status');
  const error = text => { status.textContent = text; status.classList.add('error'); };
  const report = text => { status.textContent = text; status.classList.remove('error'); };
  const input = (label, value, change, type = 'text') => {
    const n = node('input'); n.type = type; n.value = value ?? ''; n.setAttribute('aria-label', label);
    n.oninput = () => change(type === 'number' ? (n.value === '' ? null : Number(n.value)) : n.value);
    return n;
  };
  const select = (label, value, choices, change) => {
    const n = node('select'); n.setAttribute('aria-label', label);
    for (const [id, text] of choices) { const o = node('option', text); o.value = id; n.append(o); }
    n.value = value || ''; n.onchange = () => change(n.value); return n;
  };
  container.replaceChildren(); container.classList.add('settings-section');
  return {current, write, status, error, report, input, select};
}

export function renderProjectSettings(container, initial, options = {}) {
  if (!options.getSettings) {
    let draft = copy(initial); const notify = options.onChange;
    return renderProjectSettings(container, draft, {...options, getSettings: () => draft, onChange: value => { draft = copy(value); notify?.(copy(value)); }});
  }
  const e = editor(container, initial, options), {current, write, input, select, error, report, status} = e;
  const render = notice => renderProjectSettings(container, current(), {...options, notice});
  heading(container, 'Model endpoints', 'Add an OpenAI-compatible base URL and model, then assign its role. Settings affect future jobs; existing jobs retain their frozen configuration.');
  const endpoints = node('div', undefined, 'settings-cards'); container.append(endpoints);
  for (const [id, endpoint] of Object.entries(current().endpoints || {})) {
    const card = node('section', undefined, 'settings-card'), grid = node('div', undefined, 'settings-grid');
    card.append(node('h4', id));
    const edit = (key, value) => write(d => { d.endpoints[id][key] = value; });
    grid.append(field('Base URL', input(`Endpoint ${id} URL`, endpoint.url, v => edit('url', v), 'url')),
      field('Model identifier', input(`Endpoint ${id} model`, endpoint.model, v => edit('model', v))));
    for (const [key, name, min] of [['concurrency', 'Concurrent requests', 1], ['retries', 'Transport retries', 0]]) {
      const n = input(`Endpoint ${id} ${key}`, endpoint[key], v => edit(key, v), 'number'); n.min = min; n.step = 1; grid.append(field(name, n));
    }
    const env = 'REMIX_API_KEY_' + id.toUpperCase().replaceAll('-', '_');
    card.append(grid, node('p', `Authentication, if required: set ${env} in the server environment before launch. Keys are never stored in the project.`, 'settings-help'), button(`Remove endpoint ${id}`, () => {
      const cleared = Object.entries(current().endpoint_roles || {}).filter(([, assigned]) => assigned === id).map(([role]) => role);
      write(d => { delete d.endpoints[id]; for (const role of cleared) delete d.endpoint_roles[role]; });
      render(`Endpoint ${id} removed from the draft.${cleared.length ? ' Cleared endpoint roles: ' + cleared.join(', ') + '.' : ''} Existing jobs keep their frozen configuration.`);
    })); endpoints.append(card);
  }
  if (!Object.keys(current().endpoints || {}).length) endpoints.append(node('p', 'No endpoints configured. Add one below; local models may serve all three roles.'));
  const addition = node('div', undefined, 'settings-add-row');
  const ident = input('New endpoint ID', '', () => {}); ident.placeholder = 'local-model';
  addition.append(field('New endpoint ID', ident), button('Add endpoint', () => {
    const id = ident.value.trim();
    if (!validId(id) || Object.hasOwn(current().endpoints || {}, id)) return error('Use a unique endpoint ID starting with a lowercase letter, followed by letters, numbers, hyphens or underscores.');
    write(d => { d.endpoints ||= {}; d.endpoints[id] = {url:'http://127.0.0.1:8000/v1', model:'', concurrency:1, retries:2}; }); render();
  })); container.append(addition);
  heading(container, 'Endpoint roles', 'Generator writes event content. Fast Review and Critical Review also perform their corresponding repairs. Roles may share an endpoint.');
  const roles = node('div', undefined, 'settings-grid');
  for (const [role, name] of [['generator', 'Generator'], ['fast', 'Fast Review'], ['critical', 'Critical Review']]) {
    const choices = [['', 'Not configured'], ...Object.keys(current().endpoints || {}).map(id => [id, id])];
    roles.append(field(name, select(`${name} endpoint`, current().endpoint_roles?.[role], choices, value => {
      write(d => { d.endpoint_roles ||= {}; if (value) d.endpoint_roles[role] = value; else delete d.endpoint_roles[role]; });
    })));
  }
  container.append(roles);
  const profiles = node('details', undefined, 'settings-subsection'); profiles.append(node('summary', 'Tokenizer profiles'));
  profiles.append(node('p', 'Import a tokenizer JSON file, then map every required control to a dedicated special token already in that tokenizer. No vocabulary entries are added. Graph authoring does not need a tokenizer.', 'settings-help'));
  for (const [id, profile] of Object.entries(current().tokenizer_profiles || {})) {
    const card = node('section', undefined, 'settings-card'), grid = node('div', undefined, 'settings-grid');
    card.append(node('h4', id), field('Display name', input(`Tokenizer ${id} name`, profile.name, value => write(d => { d.tokenizer_profiles[id].name = value; }))));
    const dedicated = (profile.definition?.added_tokens || []).filter(t => t.special === true && Number.isInteger(t.id));
    for (const control of controls) {
      const choices = [['', 'Select dedicated token'], ...dedicated.map(t => [String(t.id), `${t.content} · ${t.id}`])];
      grid.append(field(control.toUpperCase().replaceAll('_', ' '), select(`Tokenizer ${id} ${control}`, String(profile.special_tokens?.[control] ?? ''), choices, value => write(d => {
        const map = d.tokenizer_profiles[id].special_tokens;
        if (value === '') delete map[control]; else map[control] = Number(value);
      }))));
    }
    card.append(grid, button(`Remove tokenizer ${id}`, () => { write(d => { delete d.tokenizer_profiles[id]; }); render(); })); profiles.append(card);
  }
  const profileId = input('New tokenizer profile ID', '', () => {}), upload = node('input'); upload.type = 'file'; upload.accept = '.json,application/json'; upload.setAttribute('aria-label', 'Tokenizer JSON file');
  upload.onchange = async () => {
    const id = profileId.value.trim(), file = upload.files?.[0];
    if (!file) return;
    if (!validId(id) || Object.hasOwn(current().tokenizer_profiles || {}, id)) return error('Supply a unique tokenizer profile ID before choosing its JSON file.');
    try {
      const definition = JSON.parse(await file.text());
      if (!definition || typeof definition !== 'object' || Array.isArray(definition) || !definition.model || !Array.isArray(definition.added_tokens)) throw new Error('Expected a serialized tokenizer with a model and added_tokens array.');
      if (definition.added_tokens.filter(t => t.special === true && Number.isInteger(t.id)).length < controls.length) throw new Error('Tokenizer needs at least twelve dedicated special tokens for the required control map. Import rejected; its vocabulary will not be extended.');
      const special_tokens = {};
      for (const control of controls) {
        const match = definition.added_tokens.find(t => t.special === true && [`<|${control}|>`, `<${control}>`, `[${control.toUpperCase()}]`].includes(t.content));
        if (match) special_tokens[control] = match.id;
      }
      write(d => { d.tokenizer_profiles ||= {}; d.tokenizer_profiles[id] = {name:id, definition, special_tokens}; }); render();
    } catch (cause) { error('Tokenizer import failed: ' + cause.message); }
  };
  profiles.append(field('New profile ID', profileId), field('Tokenizer JSON file', upload)); container.append(profiles);
  const other = node('details', undefined, 'settings-subsection'); other.append(node('summary', 'Project defaults'));
  const grid = node('div', undefined, 'settings-grid');
  for (const split of ['train', 'development', 'test']) {
    const n = input(`${split} split ratio`, current().split_ratios?.[split], value => write(d => { d.split_ratios ||= {}; d.split_ratios[split] = value; }), 'number'); n.min = 0; n.max = 1; n.step = 'any'; grid.append(field(`${split} fraction`, n));
  }
  const cap = input('Log cache limit MiB', current().log_cache_mib, value => write(d => { d.log_cache_mib = value; }), 'number'); cap.min = 0; cap.step = 1; cap.placeholder = 'Unlimited';
  grid.append(field('Log cache limit (MiB; blank = unlimited)', cap));
  const levels = input('Reasoning effort levels', (current().reasoning_levels || []).join(', '), value => write(d => { d.reasoning_levels = value.split(',').map(v => v.trim()).filter(Boolean); }));
  grid.append(field('Reasoning effort levels, comma separated', levels));
  other.append(grid, node('p', 'Split fractions must total 1. Changing settings never rewrites existing trajectories.', 'settings-help')); container.append(other, status);
  report(options.notice || 'Changes remain in this settings draft until Save settings.');
}

export function renderGenerationSettings(container, initial, options = {}) {
  if (!options.getSettings) {
    let draft = copy(initial); const notify = options.onChange;
    return renderGenerationSettings(container, draft, {...options, getSettings: () => draft, onChange: value => { draft = copy(value); notify?.(copy(value)); }});
  }
  const {current, write, input, status, error} = editor(container, initial, options);
  const render = () => renderGenerationSettings(container, current(), options.getSettings ? options : {...options, getSettings: current});
  const column = name => node('div', undefined, `review-column review-column-${name}`);
  const settingsColumn = column('settings'), coreColumn = column('core'), criticalColumn = column('critical');
  container.classList.add('review-columns'); container.append(settingsColumn, coreColumn, criticalColumn);
  heading(settingsColumn, 'Automatic review and repair', 'These options apply to future generation runs. A review runs only when its endpoint role is configured. Manual review remains available for detached trajectories.');
  const toggle = (key, title) => {
    const n = node('input'); n.type = 'checkbox'; n.checked = current().generation?.[key] ?? true; n.setAttribute('aria-label', title);
    n.onchange = () => write(d => { d.generation ||= {}; d.generation[key] = n.checked; }); return field(title, n);
  };
  settingsColumn.append(toggle('fast','Enable Fast Review'), toggle('critical','Enable Critical Review after Fast succeeds'), toggle('repair','Attempt repair after review failure'));
  const limits = node('div', undefined, 'settings-limits'); limits.append(node('h4', 'Response and repair limits'));
  const retries = input('Format correction retries', current().generation?.format_retries ?? 2, value => write(d => { d.generation ||= {}; d.generation.format_retries = value; }), 'number'); retries.min = 0; retries.max = 5; retries.step = 1;
  const bytes = input('Repair transcript byte limit', current().generation?.repair_context_bytes ?? 65536, value => write(d => { d.generation ||= {}; d.generation.repair_context_bytes = value; }), 'number'); bytes.min = 256; bytes.step = 1;
  limits.append(field('Format correction retries', retries), field('Repair transcript byte limit', bytes), node('p', 'Repair keeps one continuing session. This transcript bound and provider context exhaustion end that attempt; it is not a spending cap.', 'settings-help')); settingsColumn.append(limits);
  const checklistHelp = 'Each review returns evidence for every criterion. Checklist changes mark affected signoffs stale; they do not automatically re-review trajectories.';
  for (const [group, target] of [['core', coreColumn], ['critical', criticalColumn]]) {
    if (group === 'core') heading(target, 'Core checklist', `Applied in both Fast and Critical Review. ${checklistHelp}`); else heading(target, 'Critical checklist', 'Additional criteria applied only during Critical Review, on top of the core checklist.');
    const section = node('section', undefined, 'settings-card'); 
    for (const [id, criterion] of Object.entries(current().review_checklists?.[group] || {})) {
      const row = node('div', undefined, 'settings-criterion'), text = node('textarea'); text.value = criterion.text; text.setAttribute('aria-label', `${group} criterion ${id}`);
      text.oninput = () => write(d => { d.review_checklists[group][id].text = text.value; });
      const allowed = node('input'); allowed.type = 'checkbox'; allowed.checked = criterion.allow_not_applicable; allowed.setAttribute('aria-label', `${group} ${id} allow not applicable`);
      allowed.onchange = () => write(d => { d.review_checklists[group][id].allow_not_applicable = allowed.checked; });
      row.append(field(id, text), field('Allow not applicable', allowed), button(`Remove ${group} criterion ${id}`, () => { write(d => { delete d.review_checklists[group][id]; }); render(); })); section.append(row);
    }
    const id = input(`New ${group} criterion ID`, '', () => {});
    section.append(field('New criterion ID', id), button(`Add ${group} criterion`, () => {
      const key = id.value.trim(); if (!validId(key) || Object.hasOwn(current().review_checklists?.[group] || {}, key)) return error('Use a unique criterion ID with lowercase letters, numbers, hyphens or underscores.');
      write(d => { d.review_checklists ||= {}; d.review_checklists[group] ||= {}; d.review_checklists[group][key] = {text:'New criterion', allow_not_applicable:false}; }); render();
    })); target.append(section);
  }
  settingsColumn.append(status);
  if (options.footer) settingsColumn.append(options.footer);
}
