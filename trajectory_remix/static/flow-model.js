/* Pure Flow helpers for the visual editor: node kinds, edges, edits and layout. No DOM. */

export const KINDS = {
  select: {
    label: 'Select', tone: 'sky', ports: ['next'],
    help: 'Asks the Generator to choose the next event, or to finish. The shared selector instruction (Flow settings) is the system prompt; this step instruction is sent after the data, nearest the answer. Without a shared instruction, this one is the system prompt. Output: a Selection (finished, reason, windows, task).',
    instruction: 'Step instruction'
  },
  event: {
    label: 'Write event', tone: 'mint', ports: ['next'],
    help: 'Asks the Generator to write the content of the task chosen by the last Select. The instruction is appended to that task. Output: one event added to the trajectory.',
    instruction: 'Extra instruction appended to the selected task'
  },
  branch: {
    label: 'Branch', tone: 'lilac', ports: ['then', 'otherwise'],
    help: 'Reads one field of the last Selection and routes without calling a model. Then is taken when the value equals the expected value exactly (same JSON type).'
  },
  tools: {
    label: 'Show tools', tone: 'butter', ports: ['next'],
    help: 'Asks the Generator once which tools the episode needs, from tool names and descriptions. Remix then lists those tools, plus each other tool at the chance below, as a masked event in a tools window placed before the next event. Afterwards selectors see only the listed tools, and calls to unlisted tools are refused. Place it between the first Select and the opening event.',
    instruction: 'Instruction for choosing tools (its system prompt)'
  },
  end: {
    label: 'End', tone: 'peach', ports: [],
    help: 'Finishes the trajectory and hands it to the configured reviews. The last Selection must have finished: true.'
  }
};

/** Fields each kind uses, in YAML order. Matches flow_document.FIELDS on the server. */
export const FIELDS = {
  select: ['kind', 'instruction', 'next'], event: ['kind', 'instruction', 'next'],
  branch: ['kind', 'path', 'equals', 'then', 'otherwise'], end: ['kind'],
  tools: ['kind', 'instruction', 'next', 'extra_tools']
};

/** Branch paths worth suggesting: the Selection fields a selector returns. */
export const PATHS = ['finished', 'reason', 'task.id', 'task.envelope.kind', 'task.envelope.window', 'task.envelope.tool', 'task.arguments'];

const DEFAULTS = {instruction: '', next: null, path: 'finished', equals: true, then: null, otherwise: null, extra_tools: 0.5};

/** Only the fields a node's kind uses; keeps edits and YAML free of stale defaults. */
export function compactNode(node) {
  const kind = KINDS[node.kind] ? node.kind : 'select';
  const result = {};
  for (const key of FIELDS[kind]) result[key] = key in node ? node[key] : DEFAULTS[key];
  if (kind === 'end' && node.instruction) result.instruction = node.instruction;
  result.kind = kind;
  return result;
}

export function compactFlow(flow) {
  const {nodes = {}, ...rest} = flow;
  return {...rest, nodes: Object.fromEntries(Object.entries(nodes).map(([id, n]) => [id, compactNode(n)]))};
}

export function portLabel(node, port) {
  if (port === 'then') return `= ${JSON.stringify(node.equals)}`;
  if (port === 'otherwise') return 'otherwise';
  return node.kind === 'select' ? 'Selection' : 'event written';
}

/** Every transition, including dangling ones (to: null or an unknown ID). */
export function edges(flow) {
  const list = [];
  for (const [from, node] of Object.entries(flow.nodes || {})) {
    for (const port of KINDS[node.kind]?.ports || []) list.push({from, port, to: node[port] ?? null, label: portLabel(node, port)});
  }
  return list;
}

export function uniqueId(flow, base) {
  const clean = String(base || 'node').trim().replace(/\s+/g, '_') || 'node';
  if (!(clean in (flow.nodes || {}))) return clean;
  let n = 2; while (`${clean}_${n}` in flow.nodes) n++;
  return `${clean}_${n}`;
}

export function addNode(flow, kind, id) {
  const ident = uniqueId(flow, id || kind);
  const node = compactNode({kind});
  if (kind === 'event') node.instruction = 'Author the selected event using its causal context.';
  flow.nodes = {...flow.nodes, [ident]: node};
  if (!flow.start || !(flow.start in flow.nodes)) flow.start = ident;
  return ident;
}

/** Rename keeping key order, and repoint start and every transition. */
export function renameNode(flow, from, to) {
  to = String(to).trim();
  if (!to) throw new Error('Node IDs cannot be empty');
  if (to === from) return from;
  if (to in flow.nodes) throw new Error(`A node named ${to} already exists`);
  flow.nodes = Object.fromEntries(Object.entries(flow.nodes).map(([id, n]) => [id === from ? to : id, n]));
  for (const node of Object.values(flow.nodes)) for (const port of ['next', 'then', 'otherwise']) if (node[port] === from) node[port] = to;
  if (flow.start === from) flow.start = to;
  return to;
}

/** Delete a node; transitions into it become unset so the problem is visible, not hidden. */
export function removeNode(flow, id) {
  const {[id]: _removed, ...rest} = flow.nodes; flow.nodes = rest;
  for (const node of Object.values(flow.nodes)) for (const port of ['next', 'then', 'otherwise']) if (node[port] === id) node[port] = null;
  if (flow.start === id) flow.start = Object.keys(flow.nodes)[0] || '';
}

export function setKind(flow, id, kind) {
  const old = flow.nodes[id];
  const next = compactNode({...old, kind});
  // Carry a single outgoing target across kinds so the chart stays connected.
  const target = old.next ?? old.then ?? null;
  for (const port of KINDS[kind].ports) next[port] = next[port] ?? target;
  flow.nodes[id] = next;
}

export const NODE_W = 210, NODE_H = 78, GAP_X = 60, GAP_Y = 84;

/**
 * Card subtitle: a branch's condition, or the start of the instruction's last paragraph
 * (YAML folds a blank line between paragraphs into one newline).
 * Selectors often share a long preamble, so the last paragraph is what tells them apart.
 * A leading capitalised label such as "THIS STEP:" is dropped.
 */
export function subtitle(node) {
  if (node.kind === 'branch') return `if ${node.path} = ${JSON.stringify(node.equals)}`;
  if (node.kind === 'end') return 'Finish, then review';
  if (node.kind === 'tools') return `Choose tools; add others at ${Math.round((node.extra_tools ?? 0.5) * 100)}%`;
  const paragraphs = String(node.instruction || '').split(/\n+/).map(p => p.trim()).filter(Boolean);
  const rest = (paragraphs.at(-1) || '').replace(/^[A-Z][A-Z ]*:\s*/, '');
  const text = rest.charAt(0).toUpperCase() + rest.slice(1);
  return text ? (text.length > 70 ? text.slice(0, 69) + '…' : text) : 'No instruction';
}
