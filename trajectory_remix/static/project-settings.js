/* Form editors over the existing project-settings DTO. No network or credentials. */
import {renderPromptSettings} from './prompt-settings.js';
import {renderChecklistSettings} from './checklist-settings.js';
import {renderReviewPack} from './review-pack.js';
import {persistCollapse} from './collapse-state.js';
const copy = value => structuredClone(value);
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const button = (text, run) => { const n = node('button', text); n.type = 'button'; n.onclick = run; return n; };
const field = (text, control) => { const n = node('label', text); n.append(control); return n; };
const heading = (box, title, help) => { box.append(node('h3', title)); if (help) box.append(node('p', help, 'settings-help')); };
const tipHeading = (box, title, tip) => { const n = node('h3', title, 'has-tip'); n.tabIndex = 0; n.dataset.tip = tip; box.append(n); };
const controls = ['bos', 'eos', 'noop', 'ctx_begin', 'ctx_end', 'tool_begin', 'tool_end', 'response_begin', 'response_end', 'msg_begin', 'msg_end', 'window_ref'];
const validId = id => /^[a-z][a-z0-9_-]*$/.test(id);
/** Mirrors the server's Endpoint rules; returns an error message or ''. */
export function tokenBudgetProblem(context, output) {
  const off = v => v === -1;
  if (![context, output].every(Number.isInteger)) return 'Context limit and max output tokens must be whole numbers (-1 for no limit).';
  if (!off(context) && context < 1024) return 'Context limit must be -1 (no limit) or at least 1024.';
  if (!off(output) && output < 1) return 'Max output tokens must be -1 (no limit) or at least 1.';
  if (!off(context) && !off(output) && output + 512 >= context) return 'Max output tokens + 512 must be less than the context limit.';
  return '';
}
/** Hover help for the endpoint token limits; -1 leaves a limit to the provider. */
export const TOKEN_LIMIT_TIPS = {
  context_window_tokens: 'The model\'s context window. Before each request Remix estimates the prompt size (bytes ÷ bytes per token) and stops instead of sending a request that would not fit beside the max output tokens and a 512-token margin, so a server that silently cuts long prompts cannot drop part of a trajectory. -1 (the default) skips this check and leaves it to the provider.',
  max_output_tokens: 'Sent as max_tokens. Caps each single response, including reasoning and tool calls, not the whole pass. With a context limit set, this many tokens are also held back from it on every request. A response that hits the cap is cut off and retried (Incomplete response retries). -1 (the default) sends no max_tokens and holds nothing back, so the provider\'s own limit applies.',
};
/** Request fields the transport sets itself; extra_body may not replace them. */
export const RESERVED_BODY_FIELDS = ['model', 'messages', 'stream', 'max_tokens', 'max_completion_tokens', 'response_format', 'session_id', 'temperature', 'top_p', 'presence_penalty', 'reasoning_effort'];
/** Mirrors the server's extra_body rules; returns [value, ''] or [undefined, error]. */
export function parseExtraBody(text) {
  let value;
  try { value = text.trim() ? JSON.parse(text) : {}; } catch { return [undefined, 'Extra request fields must be a JSON object.']; }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return [undefined, 'Extra request fields must be a JSON object.'];
  const reserved = Object.keys(value).filter(key => RESERVED_BODY_FIELDS.includes(key));
  if (reserved.length) return [undefined, `These are set by Remix and cannot be overridden: ${reserved.join(', ')}.`];
  return [value, ''];
}
/** Options for hosted routers such as OpenRouter: token estimate, sticky sessions, extra fields. */
function providerOptions(id, endpoint, edit, input) {
  const box = node('details', undefined, 'settings-subsection'); box.append(node('summary', 'Provider options'));
  box.open = (endpoint.bytes_per_token ?? 3) !== 3 || !!endpoint.sticky_sessions || Object.keys(endpoint.extra_body || {}).length > 0 || (endpoint.output_mode ?? 'auto') !== 'auto';
  const grid = node('div', undefined, 'settings-grid');
  const ratio = input(`Endpoint ${id} bytes_per_token`, endpoint.bytes_per_token ?? 3, v => edit('bytes_per_token', v), 'number');
  ratio.min = 1; ratio.max = 6; ratio.step = 0.1;
  const ratioField = field('Bytes per token (context estimate)', ratio);
  ratioField.append(node('span', 'Used only when a context limit is set. English and JSON average about 3.5; the default 3 keeps a margin. 1 never undercounts but rejects prompts that would fit.', 'settings-help'));
  const sticky = node('input'); sticky.type = 'checkbox'; sticky.checked = !!endpoint.sticky_sessions; sticky.setAttribute('aria-label', `Endpoint ${id} sticky sessions`);
  sticky.onchange = () => edit('sticky_sessions', sticky.checked);
  const stickyField = node('label', undefined, 'checkbox'); stickyField.append(sticky, ' Sticky sessions (send the trajectory ID as session_id)');
  const mode = node('select'); mode.setAttribute('aria-label', `Endpoint ${id} structured output`);
  for (const [value, text] of [['auto', 'Auto: native JSON schema where possible'], ['json_object', 'JSON object: schema in the prompt'], ['prompt', 'Prompt only: no response_format']]) {
    const option = node('option', text); option.value = value; mode.append(option);
  }
  mode.value = endpoint.output_mode || 'auto'; mode.onchange = () => edit('output_mode', mode.value);
  const modeField = field('Structured output', mode);
  modeField.append(node('span', 'Use JSON object or Prompt only if the server ignores or rejects JSON-schema output. Prompt only suits servers with no JSON mode at all.', 'settings-help'));
  grid.append(ratioField, modeField, stickyField);
  const extra = node('textarea'); extra.spellcheck = false; extra.setAttribute('aria-label', `Endpoint ${id} extra request fields`);
  extra.value = JSON.stringify(endpoint.extra_body || {}, null, 2);
  const problem = node('p', '', 'settings-help'); problem.setAttribute('role', 'alert');
  extra.oninput = () => {
    const [value, message] = parseExtraBody(extra.value);
    problem.textContent = message; problem.classList.toggle('error', !!message); extra.setAttribute('aria-invalid', message ? 'true' : 'false');
    if (value !== undefined) edit('extra_body', value);
  };
  const extraField = field('Extra request fields (JSON)', extra);
  box.append(node('p', 'For hosted routers such as OpenRouter. Sticky sessions keep each trajectory on one provider so its prompt stays cached. Extra fields are added to every request, for example {"reasoning": {"effort": "none"}, "provider": {"require_parameters": true}}.', 'settings-help'), grid, extraField, problem);
  return box;
}
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
    const card = node('details', undefined, 'settings-card'), grid = node('div', undefined, 'settings-grid');
    card.append(node('summary', id)); persistCollapse(card, `endpoint:${id}`);
    const edit = (key, value) => write(d => { d.endpoints[id][key] = value; });
    grid.append(field('Base URL', input(`Endpoint ${id} URL`, endpoint.url, v => edit('url', v), 'url')),
      field('Model identifier', input(`Endpoint ${id} model`, endpoint.model, v => edit('model', v))));
    const budget = node('p', '', 'settings-help'); budget.setAttribute('role', 'alert');
    const checkBudget = () => {
      const {context_window_tokens: ctx = -1, max_output_tokens: out = -1} = current().endpoints[id], problem = tokenBudgetProblem(ctx, out);
      budget.textContent = problem; budget.classList.toggle('error', Boolean(problem));
      for (const n of budget.fields || []) n.setAttribute('aria-invalid', problem ? 'true' : 'false');
    };
    const tokenFields = [];
    for (const [key, name, min] of [['concurrency', 'Concurrent requests', 1], ['retries', 'Transport retries', 0],
      ['context_window_tokens', 'Context limit (tokens)', -1], ['max_output_tokens', 'Max output tokens', -1]]) {
      const n = input(`Endpoint ${id} ${key}`, endpoint[key] ?? (key.endsWith('_tokens') ? -1 : undefined), v => { edit(key, v); checkBudget(); }, 'number'); n.min = min; n.step = 1;
      const label = field(name, n); grid.append(label);
      if (key.endsWith('_tokens')) { tokenFields.push(n); label.dataset.tip = TOKEN_LIMIT_TIPS[key]; }
    }
    budget.fields = tokenFields; checkBudget();
    const timeout = input(`Endpoint ${id} timeout_seconds`, endpoint.timeout_seconds ?? 120, v => { if (v > 0) edit('timeout_seconds', v); }, 'number'); timeout.min = 0; timeout.step = 1;
    const reconnect = input(`Endpoint ${id} reconnect_seconds`, endpoint.reconnect_seconds, v => edit('reconnect_seconds', v > 0 ? v : null), 'number'); reconnect.min = 0; reconnect.step = 1;
    const reconnectField = field('Auto-reconnect after (seconds)', reconnect);
    reconnectField.append(node('span', 'Blank keeps the endpoint paused after its retries fail until you press Resume. Set seconds to retry automatically.', 'settings-help'));
    const reasoning = input(`Endpoint ${id} reasoning`, endpoint.reasoning || '', v => edit('reasoning', v.trim() || null));
    reasoning.setAttribute('list', `reasoning-levels-${id}`); reasoning.placeholder = 'Model default';
    const suggestions = node('datalist'); suggestions.id = `reasoning-levels-${id}`;
    for (const level of ['none', 'minimal', 'low', 'medium', 'high', 'xhigh']) { const o = node('option'); o.value = level; suggestions.append(o); }
    const reasoningField = field('Reasoning level', reasoning); reasoningField.append(suggestions, node('span', 'Sent as reasoning_effort. Type any value your model accepts; "none" disables reasoning. Blank uses the model default.', 'settings-help'));
    grid.append(field('Request timeout (seconds)', timeout), reconnectField, reasoningField);
    grid.append(budget);
    card.append(grid);
    card.append(providerOptions(id, endpoint, edit, input));
    const env = 'REMIX_API_KEY_' + id.toUpperCase().replaceAll('-', '_');
    card.append(node('p', `Authentication, if required: set ${env} in the server environment before launch. Keys are never stored in the project.`, 'settings-help'), button(`Remove endpoint ${id}`, () => {
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
    write(d => { d.endpoints ||= {}; d.endpoints[id] = {url:'http://127.0.0.1:8000/v1', model:'', concurrency:1, retries:2, context_window_tokens:-1, max_output_tokens:-1}; }); render();
  })); container.append(addition);
  heading(container, 'Endpoint roles', 'Draft writes the working notes and Assembly builds the trajectory from them; Assembly falls back to Draft. Fast Review and Critical Review also perform their corresponding repairs. Roles may share an endpoint.');
  const roles = node('div', undefined, 'settings-grid');
  for (const [role, name] of [['draft', 'Draft'], ['assembly', 'Assembly'], ['fast', 'Fast Review'], ['critical', 'Critical Review']]) {
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
  const chance = input('Random extra tools percent', Math.round((current().tool_schema_extra_chance ?? 0.5) * 100), value => write(d => { d.tool_schema_extra_chance = Math.min(100, Math.max(0, value ?? 0)) / 100; }), 'number'); chance.min = 0; chance.max = 100; chance.step = 1;
  grid.append(field('Random extra tools in tool schema events (% chance each; 0 = none)', chance));
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
  const {current, write, input, status, error, report} = editor(container, initial, options);
  const render = notice => renderGenerationSettings(container, current(), {...(options.getSettings ? options : {...options, getSettings: current}), notice});
  const column = name => node('div', undefined, `review-column review-column-${name}`);
  const settingsColumn = column('settings'), promptsColumn = column('prompts'), checklistColumn = column('checklist');
  container.classList.add('review-columns'); container.append(settingsColumn, promptsColumn, checklistColumn);
  tipHeading(settingsColumn, 'Automatic review and repair', 'These options apply to future generation runs. A review runs only when its endpoint role is configured. Manual review remains available for detached trajectories.');
  const toggle = (key, title, fallback = true) => {
    const n = node('input'); n.type = 'checkbox'; n.checked = current().generation?.[key] ?? fallback; n.setAttribute('aria-label', title);
    n.onchange = () => write(d => { d.generation ||= {}; d.generation[key] = n.checked; }); return field(title, n);
  };
  settingsColumn.append(toggle('fast','Enable Fast Review'), toggle('critical','Enable Critical Review'), toggle('repair','Attempt repair after review failure'));
  settingsColumn.append(toggle('review_include_draft','Include Draft in review and repair', false));
  const limits = node('div', undefined, 'settings-limits'); limits.append(node('h4', 'Authoring limits'));
  const retries = input('Format correction retries', current().generation?.format_retries ?? 2, value => write(d => { d.generation ||= {}; d.generation.format_retries = value; }), 'number'); retries.min = 0; retries.max = 5; retries.step = 1;
  const exchanges = input('Maximum exchanges per pass', current().generation?.max_authoring_exchanges ?? 10, value => write(d => { d.generation ||= {}; d.generation.max_authoring_exchanges = value; }), 'number'); exchanges.min = 1; exchanges.step = 1;
  limits.append(field('Format correction retries', retries), field('Maximum exchanges per pass', exchanges), node('p', 'Assembly, review, and repair each use one conversation. The exchange count and the configured endpoint context limit bound each pass.', 'settings-help')); settingsColumn.append(limits);
  tipHeading(promptsColumn, 'Generation prompts', 'Runs freeze the prompts when they start; edits apply to new runs.');
  renderPromptSettings(promptsColumn, {current, write, error, render, loadDefault: options.loadDefaultPrompts});
  renderChecklistSettings(checklistColumn, {current, write, input, error, render, loadDefault: options.loadDefaultChecklists, tipHeading});
  tipHeading(settingsColumn, 'Prompt and checklist pack', 'Export the prompts and checklist as a JSON file, or import one shared from another project.');
  renderReviewPack(settingsColumn, {current, write, error, render});
  settingsColumn.append(status);
  if (options.notice) report(options.notice);
  if (options.footer) settingsColumn.append(options.footer);
}
