/* Adaptive generation Flow in the Generate dialog: outline, hash and per-run YAML.
   Reads GET /generation/flow; sends YAML only as instructions.flow_yaml for one run. */
import {node, tag} from './ui-kit.js';

const KIND_TONES = {select: 'sky', event: 'mint', branch: 'lilac', end: 'peach'};

/** Minimal YAML writer for the normalized Flow (plain objects, arrays, scalars). */
export function toYaml(value, indent = 0) {
  const pad = '  '.repeat(indent);
  if (Array.isArray(value)) return value.length ? value.map(v => `${pad}- ${scalarOrBlock(v, indent + 1).trimStart()}`).join('\n') : `${pad}[]`;
  return Object.entries(value).map(([key, v]) => {
    const name = /^[A-Za-z_][\w-]*$/.test(key) ? key : JSON.stringify(key);
    if (v && typeof v === 'object') {
      const empty = Array.isArray(v) ? !v.length : !Object.keys(v).length;
      return empty ? `${pad}${name}: ${Array.isArray(v) ? '[]' : '{}'}` : `${pad}${name}:\n${toYaml(v, indent + 1)}`;
    }
    return `${pad}${name}: ${scalar(v, indent + 1)}`;
  }).join('\n');
}
function scalarOrBlock(v, indent) { return v && typeof v === 'object' ? '\n' + toYaml(v, indent) : scalar(v, indent); }

/** Long prose becomes a folded block when that is lossless; everything else is JSON-quoted. */
function scalar(v, indent) {
  if (typeof v !== 'string') return JSON.stringify(v);
  const foldable = v.length > 72 && !/[\n\t]|^\s|\s$/.test(v);
  if (!foldable) return JSON.stringify(v);
  const lines = [], width = 84;
  let rest = v;
  while (rest.length > width) {
    // Break only at a single space so folding (join with one space) restores the text exactly.
    let at = -1;
    for (let i = width; i > 20; i--) if (rest[i] === ' ' && rest[i - 1] !== ' ' && rest[i + 1] !== ' ') { at = i; break; }
    if (at < 0) break;
    lines.push(rest.slice(0, at)); rest = rest.slice(at + 1);
  }
  lines.push(rest);
  const pad = '  '.repeat(indent);
  return '>-\n' + lines.map(line => pad + line).join('\n');
}

function transitions(node_) {
  if (node_.kind === 'branch') return `if ${node_.path} = ${JSON.stringify(node_.equals)} → ${node_.then}, else → ${node_.otherwise}`;
  if (node_.kind === 'end') return 'Finish, then run configured reviews';
  return node_.next ? `→ ${node_.next}` : '';
}

/** Render the Flow section. Returns {instructions()} for the run request. */
export async function renderFlowPanel(container, api) {
  container.replaceChildren(node('h3', 'Flow'));
  let loaded;
  try { loaded = await api('/generation/flow'); } catch {
    container.append(node('p', 'This server builds trajectories with its built-in recipe. Adaptive flows need a newer backend.', 'small muted'));
    return {instructions: () => ({})};
  }
  const {flow, flow_hash: hash} = loaded;
  const summary = node('div', undefined, 'flow-summary');
  const hashTag = tag(String(hash).slice(0, 12), 'accent', 'mono'); hashTag.dataset.tip = 'Flow hash ' + hash + '. A run freezes its flow at start.'; hashTag.tabIndex = 0;
  summary.append(tag(flow.version || 'flow', 'muted'), hashTag,
    node('span', `${Object.keys(flow.nodes || {}).length} nodes · ≤ ${flow.max_events} events · ≤ ${flow.max_steps} steps`, 'small muted'));
  const info = node('span', undefined, 'info'); info.tabIndex = 0;
  info.dataset.tip = 'Loaded from flow.yaml beside the project database, or the packaged default. Edit that file to change future runs, or use a custom flow for one run below.';
  summary.append(info);

  const outline = node('details', undefined, 'collapsible flow-outline');
  outline.append(node('summary', 'Flow outline'));
  const outlineBody = node('div', undefined, 'collapsible-body'), list = node('ol', undefined, 'flow-nodes');
  for (const [id, n] of Object.entries(flow.nodes || {})) {
    const item = node('li'), head = node('div', undefined, 'row');
    head.append(node('code', id), tag(n.kind, KIND_TONES[n.kind] || 'muted'));
    if (id === flow.start) head.append(tag('start', 'accent'));
    item.append(head, node('span', transitions(n), 'small muted'));
    if (n.instruction) { item.dataset.tip = n.instruction.length > 400 ? n.instruction.slice(0, 400) + '…' : n.instruction; item.tabIndex = 0; }
    list.append(item);
  }
  outlineBody.append(list); outline.append(outlineBody);

  const custom = node('details', undefined, 'collapsible flow-custom');
  custom.append(node('summary', 'Custom flow for this run'));
  const customBody = node('div', undefined, 'collapsible-body');
  const use = node('input'); use.type = 'checkbox'; use.id = 'generation-flow-custom';
  const useLabel = node('label'); useLabel.append(use, ' Use this YAML for the next run only');
  const yaml = node('textarea'); yaml.id = 'generation-flow-yaml'; yaml.spellcheck = false; yaml.setAttribute('aria-label', 'Flow YAML for this run');
  yaml.value = toYaml(flow) + '\n';
  yaml.oninput = () => { use.checked = true; };
  customBody.append(useLabel, yaml, node('p', 'Sent as instructions.flow_yaml and validated before any attempt is reserved. flow.yaml on disk is not changed.', 'small muted'));
  custom.append(customBody);

  container.append(summary, outline, custom);
  return {instructions: () => (use.checked ? {flow_yaml: yaml.value} : {})};
}
