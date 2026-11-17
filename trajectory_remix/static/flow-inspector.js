/* Side-panel forms for the Flow editor. Each edit calls change(mutator); typing does not
   rebuild the form, so focus and cursor position are kept. */
import {node, tag} from './ui-kit.js';
import {KINDS, PATHS} from './flow-model.js';

const field = (text, control, hint) => {
  const label = node('label', text); label.append(control);
  if (hint) label.append(node('span', hint, 'small faint'));
  return label;
};
const select = (entries, value, onChange, name) => {
  const el = node('select'); el.setAttribute('aria-label', name);
  for (const [v, text] of entries) { const o = node('option', text); o.value = v; el.append(o); }
  el.value = value ?? ''; el.onchange = () => onChange(el.value);
  return el;
};
const targets = (flow, value) => {
  const list = [['', '— not connected —'], ...Object.keys(flow.nodes).map(id => [id, id])];
  if (value && !(value in flow.nodes)) list.push([value, `${value} (missing)`]);
  return list;
};

function problemList(diagnostics) {
  const list = node('ul', undefined, 'flow-problem-list');
  for (const d of diagnostics) {
    const item = node('li', undefined, `tone-${d.severity === 'error' ? 'danger' : 'warning'}`);
    item.append(tag(d.severity, d.severity === 'error' ? 'danger' : 'warning'), node('span', d.message));
    list.append(item);
  }
  return list;
}

/** Fields of one node. Hooks: change(mutator, rerender), rename(id), remove(), start(). */
export function renderNodeForm(box, flow, id, hooks, diagnostics = []) {
  const n = flow.nodes[id];
  if (!n) { box.replaceChildren(node('p', 'Select a node on the chart, or add one from the toolbar.', 'empty')); return; }
  const info = KINDS[n.kind];
  const ident = node('input'); ident.value = id; ident.spellcheck = false; ident.className = 'mono';
  ident.onchange = () => hooks.rename(ident.value);
  const kind = select(Object.entries(KINDS).map(([k, v]) => [k, v.label]), n.kind, value => hooks.kind(value), 'Node kind');
  const head = node('div', undefined, 'form-grid two');
  head.append(field('Node ID', ident), field('Kind', kind));
  const start = node('input'); start.type = 'checkbox'; start.checked = flow.start === id; start.disabled = start.checked;
  start.onchange = () => hooks.start();
  const startLabel = node('label', undefined, 'checkbox'); startLabel.append(start, ' Start the flow here');
  const parts = [head, node('p', info.help, 'note'), startLabel];
  const mine = diagnostics.filter(d => d.node === id);
  if (mine.length) parts.push(problemList(mine));
  if (n.kind === 'select' || n.kind === 'event' || n.kind === 'tools') {
    const text = node('textarea'); text.value = n.instruction || ''; text.spellcheck = true; text.className = 'flow-instruction';
    text.oninput = () => hooks.change(f => { f.nodes[id].instruction = text.value; }, false);
    parts.push(field(info.instruction, text));
    parts.push(field('Then go to', select(targets(flow, n.next), n.next, v => hooks.change(f => { f.nodes[id].next = v || null; }), 'Next node')));
  }
  if (n.kind === 'tools') {
    const chance = node('input'); chance.type = 'number'; chance.min = '0'; chance.max = '1'; chance.step = '0.05'; chance.value = n.extra_tools ?? 0.5;
    chance.setAttribute('aria-label', 'Chance to add each unchosen tool');
    chance.oninput = () => { const value = Number(chance.value); if (chance.value !== '' && value >= 0 && value <= 1) hooks.change(f => { f.nodes[id].extra_tools = value; }, false); };
    parts.push(field('Chance to add each unchosen tool (0–1)', chance, 'Tools the Generator did not choose are still listed this often, chosen at random but the same for a given trajectory, so the agent learns to ignore irrelevant tools. 0 lists only chosen tools.'));
  }
  if (n.kind === 'branch') parts.push(...branchFields(flow, id, n, hooks));
  const remove = node('button', 'Delete node', 'sm danger'); remove.type = 'button'; remove.onclick = () => hooks.remove();
  const actions = node('div', undefined, 'panel-actions'); actions.append(remove);
  box.replaceChildren(...parts, actions);
}

function branchFields(flow, id, n, hooks) {
  const path = node('input'); path.value = n.path; path.className = 'mono'; path.setAttribute('list', 'flow-path-options');
  const options = node('datalist'); options.id = 'flow-path-options';
  for (const p of PATHS) { const o = node('option'); o.value = p; options.append(o); }
  path.oninput = () => hooks.change(f => { f.nodes[id].path = path.value; }, false);
  const equals = node('input'); equals.value = JSON.stringify(n.equals); equals.className = 'mono';
  const status = node('span', 'JSON: true, false, a number, "text" or null', 'small faint');
  equals.oninput = () => {
    try { const value = JSON.parse(equals.value); equals.removeAttribute('aria-invalid'); status.textContent = `Matches ${typeof value === 'string' ? 'the text' : 'the value'} ${JSON.stringify(value)} exactly`; hooks.change(f => { f.nodes[id].equals = value; }, false); }
    catch { equals.setAttribute('aria-invalid', 'true'); status.textContent = 'Not valid JSON yet; the previous value is kept.'; }
  };
  const equalsField = field('Equals', equals); equalsField.append(status);
  return [
    options,
    field('Read this Selection field', path, 'Dotted path into the last Select output'),
    equalsField,
    field('When it matches, go to', select(targets(flow, n.then), n.then, v => hooks.change(f => { f.nodes[id].then = v || null; }), 'Then node')),
    field('Otherwise, go to', select(targets(flow, n.otherwise), n.otherwise, v => hooks.change(f => { f.nodes[id].otherwise = v || null; }), 'Otherwise node'))
  ];
}

/** Flow-wide limits and the actor instruction. */
export function renderFlowForm(box, flow, hooks, diagnostics = []) {
  const start = select(Object.keys(flow.nodes).map(id => [id, id]), flow.start, v => hooks.change(f => { f.start = v; }), 'Start node');
  const number = (key, max) => {
    const input = node('input'); input.type = 'number'; input.min = '1'; input.max = String(max); input.step = '1'; input.value = flow[key];
    input.oninput = () => { const value = Number(input.value); if (Number.isInteger(value)) hooks.change(f => { f[key] = value; }, false); };
    return input;
  };
  const grid = node('div', undefined, 'form-grid two');
  grid.append(field('Start node', start), node('span'),
    field('Max steps', number('max_steps', 10000), 'Every node visit counts, including branches'),
    field('Max events', number('max_events', 2000), 'Runs stop as Unfinished when reached'));
  const reply = node('input'); reply.type = 'checkbox'; reply.checked = flow.immediate_response !== false;
  reply.setAttribute('aria-label', 'Immediate response');
  reply.onchange = () => hooks.change(f => { f.immediate_response = reply.checked; }, false);
  const replyLabel = node('label', undefined, 'checkbox'); replyLabel.append(reply, ' Immediate response');
  grid.append(field('', replyLabel, 'The agent answers a person at once ("Sure, let me look into this.") before it reasons. Branch nodes read it as flow.immediate_response.'));
  const selector = node('textarea'); selector.value = flow.selector_instruction || ''; selector.className = 'flow-instruction';
  selector.oninput = () => hooks.change(f => { f.selector_instruction = selector.value; }, false);
  const actor = node('textarea'); actor.value = flow.actor_instruction || ''; actor.className = 'flow-instruction';
  actor.oninput = () => hooks.change(f => { f.actor_instruction = actor.value; }, false);
  const mine = diagnostics.filter(d => !d.node && d.field);
  box.replaceChildren(grid,
    field('Selector instruction', selector, 'The system prompt shared by every Select node. Keep the rules here and each node\'s step instruction short: requests then share one cached prefix. Leave empty to use each node\'s instruction as its system prompt.'),
    field('Actor instruction', actor, 'Replaces the selector\'s instruction for agent text and tool calls, so private plans never reach the actor.'),
    ...(mine.length ? [problemList(mine)] : []));
}

/** All problems; clicking one with a node selects it. */
export function renderProblems(box, diagnostics, pick) {
  if (!diagnostics.length) { box.replaceChildren(node('p', 'No problems found.', 'empty')); return; }
  const list = node('ul', undefined, 'flow-problem-list');
  for (const d of diagnostics) {
    const item = node('li'), text = node('button', undefined, 'ghost sm flow-problem');
    text.type = 'button'; text.append(tag(d.severity, d.severity === 'error' ? 'danger' : 'warning'), node('span', (d.node ? d.node + ': ' : '') + d.message));
    text.onclick = () => pick(d); item.append(text); list.append(item);
  }
  box.replaceChildren(list);
}
