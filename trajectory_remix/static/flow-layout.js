/* Automatic flowchart layout. Pure: positions only, no DOM.
   Rows follow the longest forward path from start, so loops become back links instead
   of pushing nodes down. A chain of branches linked by "otherwise" shares one row, like
   a switch. Within a row, nodes sit under their parents to keep links short. */
import {GAP_X, GAP_Y, KINDS, NODE_H, NODE_W} from './flow-model.js';

/** Room on the left for links routed down the side. */
export const MARGIN_X = 120;

const links = (nodes, id) => (KINDS[nodes[id].kind]?.ports || [])
  .map(port => ({port, to: nodes[id][port]}))
  .filter(link => link.to in nodes);

/** A branch's "otherwise" into another branch stays in the same row. */
export const sameRow = (nodes, id, port, to) =>
  nodes[id].kind === 'branch' && port === 'otherwise' && nodes[to]?.kind === 'branch';

/** Depth-first order from start, and the links that point back to an open ancestor. */
function explore(flow) {
  const nodes = flow.nodes, state = {}, order = [], back = new Set();
  const visit = id => {
    state[id] = 'open'; order.push(id);
    for (const {port, to} of links(nodes, id)) {
      if (state[to] === 'open') back.add(`${id}\n${port}`);
      else if (!state[to]) visit(to);
    }
    state[id] = 'done';
  };
  if (flow.start in nodes) visit(flow.start);
  const reached = new Set(order);
  for (const id of Object.keys(nodes)) if (!state[id]) visit(id);
  return {order, back, reached};
}

function layers(flow, {order, back, reached}) {
  const nodes = flow.nodes, layer = Object.fromEntries(order.map(id => [id, 0]));
  // Longest path over forward links; the graph without back links has no cycles.
  for (let changed = true, rounds = 0; changed && rounds <= order.length; rounds++) {
    changed = false;
    for (const id of order) for (const {port, to} of links(nodes, id)) {
      if (back.has(`${id}\n${port}`) || reached.has(id) !== reached.has(to)) continue;
      const want = layer[id] + (sameRow(nodes, id, port, to) ? 0 : 1);
      if (want > layer[to]) { layer[to] = want; changed = true; }
    }
  }
  // Nodes the start never reaches go below everything else.
  const bottom = Math.max(-1, ...order.filter(id => reached.has(id)).map(id => layer[id])) + 1;
  for (const id of order) if (!reached.has(id)) layer[id] += bottom;
  return layer;
}

/**
 * Returns {id: {x, y}} in canvas pixels for every node.
 */
export function layout(flow) {
  const nodes = flow.nodes || {};
  if (!Object.keys(nodes).length) return {};
  const found = explore({...flow, nodes});
  const layer = layers({...flow, nodes}, found), rank = Object.fromEntries(found.order.map((id, i) => [id, i]));
  const parents = {}, chained = {};
  for (const id of found.order) for (const {port, to} of links(nodes, id)) {
    if (found.back.has(`${id}\n${port}`)) continue;
    if (layer[to] === layer[id]) chained[id] = [...(chained[id] || []), to];
    else (parents[to] ||= []).push(id);
  }
  const rows = [];
  for (const id of found.order) (rows[layer[id]] ||= []).push(id);
  const centre = {}, positions = {};
  rows.forEach((row, level) => {
    if (!row) return;
    const bary = id => {
      const xs = (parents[id] || []).filter(p => p in centre).map(p => centre[p]);
      return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
    };
    // Chains follow their head; heads are ordered by where their parents are.
    const inChain = new Set(row.flatMap(id => chained[id] || []));
    const heads = row.filter(id => !inChain.has(id))
      .sort((a, b) => (bary(a) ?? Infinity) - (bary(b) ?? Infinity) || rank[a] - rank[b]);
    const placed = [], seen = new Set();
    const follow = id => { if (seen.has(id)) return; seen.add(id); placed.push(id); for (const next of chained[id] || []) follow(next); };
    heads.forEach(follow); row.forEach(follow);
    const known = placed.map(bary).filter(v => v !== null);
    const width = placed.length * NODE_W + (placed.length - 1) * GAP_X;
    const middle = known.length ? known.reduce((a, b) => a + b, 0) / known.length : width / 2;
    placed.forEach((id, i) => {
      const x = middle - width / 2 + i * (NODE_W + GAP_X);
      positions[id] = {x, y: 40 + level * (NODE_H + GAP_Y)};
      centre[id] = x + NODE_W / 2;
    });
  });
  const shift = MARGIN_X - Math.min(...Object.values(positions).map(p => p.x));
  for (const p of Object.values(positions)) p.x = Math.round(p.x + shift);
  return positions;
}

const overlap = (a, b) => Math.abs(a.x - b.x) < NODE_W && Math.abs(a.y - b.y) < NODE_H;

/**
 * Restore remembered card positions for a flow. Remembered positions are kept only when
 * they cover every node and no two cards overlap. When the flow gained nodes from outside
 * the editor (a new default, applied YAML), old positions belong to an older layout and a
 * new card would land on top of one, so the whole chart is laid out again.
 * Returns {positions, relaid}.
 */
export function restoreLayout(flow, remembered = {}) {
  const ids = Object.keys(flow.nodes || {});
  const known = ids.filter(id => remembered[id]);
  if (!known.length) return {positions: layout(flow), relaid: false};
  const kept = Object.fromEntries(known.map(id => [id, remembered[id]]));
  const clash = known.some((a, i) => known.slice(i + 1).some(b => overlap(kept[a], kept[b])));
  if (known.length < ids.length || clash) return {positions: layout(flow), relaid: true};
  return {positions: kept, relaid: false};
}

