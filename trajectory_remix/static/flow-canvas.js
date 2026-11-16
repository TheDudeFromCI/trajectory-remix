/* Flowchart canvas: node cards over an SVG edge layer. Drag cards to move them, drag a
   port onto a card to connect it, click a card to select it. No network access. */
import {node, tag} from './ui-kit.js';
import {KINDS, NODE_H, NODE_W, edges, subtitle} from './flow-model.js';

const SVG = 'http://www.w3.org/2000/svg';
const svg = (name, attrs = {}) => {
  const el = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  return el;
};

function portPoint(pos, kind, port) {
  const ports = KINDS[kind]?.ports || [], i = Math.max(0, ports.indexOf(port));
  return {x: pos.x + NODE_W * (i + 1) / (ports.length + 1), y: pos.y + NODE_H};
}

/** Path and label point for one transition. Upward or sideways links route round the right. */
export function edgePath(from, to, lane = 0, right = 0) {
  const {x: sx, y: sy} = from, tx = to.x + NODE_W / 2, ty = to.y;
  if (ty > sy + 20) {
    return {d: `M ${sx} ${sy} C ${sx} ${sy + 40}, ${tx} ${ty - 40}, ${tx} ${ty}`, x: (sx + tx) / 2, y: (sy + ty) / 2};
  }
  // Loops run up a lane right of every card, so they never cross one.
  const rx = Math.max(sx, to.x + NODE_W, right) + 40 + lane * 26;
  const d = `M ${sx} ${sy} C ${sx} ${sy + 56}, ${rx} ${sy + 56}, ${rx} ${(sy + ty) / 2} S ${tx} ${ty - 56}, ${tx} ${ty}`;
  return {d, x: rx, y: (sy + ty) / 2};
}

export class FlowCanvas {
  constructor(root, {onSelect = () => {}, onConnect = () => {}, onMove = () => {}} = {}) {
    this.root = root; this.handlers = {onSelect, onConnect, onMove}; this.scale = 1;
    this.plane = node('div', undefined, 'flow-plane');
    this.edgeLayer = svg('svg', {class: 'flow-edges', 'aria-hidden': 'true'});
    const defs = svg('defs');
    for (const name of ['arrow', 'arrow-active', 'arrow-bad']) {
      const marker = svg('marker', {id: 'flow-' + name, viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse'});
      marker.append(svg('path', {d: 'M 0 0 L 10 5 L 0 10 z', class: 'flow-arrow ' + name})); defs.append(marker);
    }
    this.edgeLayer.append(defs); this.edgeGroup = svg('g'); this.edgeLayer.append(this.edgeGroup);
    this.plane.append(this.edgeLayer); root.replaceChildren(this.plane);
    this.cards = new Map();
  }

  setZoom(scale) {
    this.scale = Math.min(2, Math.max(0.4, scale));
    this.plane.style.transform = `scale(${this.scale})`;
    this.size();
    return this.scale;
  }

  render(flow, {positions, selected, problems = new Map(), trail = null, editable = true}) {
    this.flow = flow; this.positions = positions; this.trail = trail; this.editable = editable;
    const ids = Object.keys(flow.nodes || {});
    for (const [id, card] of this.cards) if (!ids.includes(id)) { card.remove(); this.cards.delete(id); }
    for (const id of ids) {
      let card = this.cards.get(id);
      if (!card) { card = this.card(id); this.cards.set(id, card); this.plane.append(card); }
      this.fill(card, id, flow.nodes[id], {selected: id === selected, problem: problems.get(id), start: id === flow.start});
      this.place(card, positions[id]);
    }
    this.drawEdges();
    this.size();
  }

  card(id) {
    const card = node('div', undefined, 'flow-node');
    card.tabIndex = 0; card.setAttribute('role', 'button'); card.dataset.node = id;
    card.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.handlers.onSelect(card.dataset.node); } });
    card.addEventListener('pointerdown', event => this.dragCard(event, card));
    return card;
  }

  fill(card, id, n, {selected, problem, start}) {
    const info = KINDS[n.kind] || KINDS.select;
    card.dataset.node = id;
    card.className = `flow-node kind-${n.kind}` + (selected ? ' is-selected' : '') + (problem ? ` has-${problem}` : '');
    const visits = this.trail?.visits?.[id] || 0;
    if (this.trail) {
      card.classList.toggle('is-current', this.trail.current === id);
      card.classList.toggle('is-visited', visits > 0);
    }
    card.setAttribute('aria-label', `${info.label} node ${id}${start ? ', start' : ''}${problem ? ', has ' + problem + 's' : ''}`);
    card.setAttribute('aria-pressed', String(selected));
    const head = node('div', undefined, 'flow-node-head');
    head.append(tag(info.label, info.tone));
    if (start) head.append(tag('start', 'accent'));
    if (visits) head.append(node('span', '×' + visits, 'flow-visits num'));
    if (problem) head.append(node('span', '', `flow-problem-dot tone-${problem === 'error' ? 'danger' : 'warning'}`));
    const ports = node('div', undefined, 'flow-ports');
    for (const port of info.ports) {
      const handle = node('span', undefined, `flow-port port-${port}`);
      handle.dataset.port = port; handle.dataset.tip = `${port}: drag onto a node to connect`;
      handle.addEventListener('pointerdown', event => this.dragPort(event, card.dataset.node, port));
      ports.append(handle);
    }
    card.replaceChildren(head, node('div', id, 'flow-node-id mono truncate'), node('div', subtitle(n), 'flow-node-sub small muted truncate'), ports);
  }

  place(card, pos = {x: 0, y: 0}) { card.style.left = pos.x + 'px'; card.style.top = pos.y + 'px'; }

  size() {
    let w = 400, h = 300;
    for (const pos of Object.values(this.positions || {})) { w = Math.max(w, pos.x + NODE_W + 160); h = Math.max(h, pos.y + NODE_H + 120); }
    this.edgeLayer.setAttribute('width', w); this.edgeLayer.setAttribute('height', h);
    this.plane.style.width = w + 'px'; this.plane.style.height = h + 'px';
    // The scroll area follows the scaled size.
    this.root.style.setProperty('--plane-w', w * this.scale + 'px'); this.root.style.setProperty('--plane-h', h * this.scale + 'px');
  }

  drawEdges() {
    const group = this.edgeGroup; group.replaceChildren();
    const lanes = new Map(), active = this.trail?.edge;
    const right = Math.max(0, ...Object.values(this.positions).map(p => p.x + NODE_W));
    for (const edge of edges(this.flow)) {
      const fromPos = this.positions[edge.from]; if (!fromPos) continue;
      const start = portPoint(fromPos, this.flow.nodes[edge.from].kind, edge.port);
      const target = this.positions[edge.to];
      const isActive = active && active.from === edge.from && active.port === edge.port;
      if (!target) {
        group.append(svg('path', {d: `M ${start.x} ${start.y} l 0 34`, class: 'flow-edge is-dangling', 'marker-end': 'url(#flow-arrow-bad)'}));
        const label = svg('text', {x: start.x + 6, y: start.y + 30, class: 'flow-edge-label is-dangling'}); label.textContent = edge.to ? `missing: ${edge.to}` : 'not connected';
        group.append(label); continue;
      }
      const back = target.y <= start.y + 20;
      const lane = back ? (lanes.set('all', (lanes.get('all') ?? -1) + 1), lanes.get('all')) : 0;
      const path = edgePath(start, target, lane, right);
      const cls = `flow-edge port-${edge.port}` + (isActive ? ' is-active' : '');
      const line = svg('path', {d: path.d, class: cls, 'marker-end': `url(#flow-${isActive ? 'arrow-active' : 'arrow'})`});
      line.dataset.from = edge.from; line.dataset.port = edge.port;
      group.append(line, this.label(edge, start, path, isActive));
    }
  }

  /** Branch labels sit beside their port (then left, otherwise right); others mid-edge. */
  label(edge, start, path, active) {
    const branch = edge.port === 'then' || edge.port === 'otherwise';
    const at = branch ? {x: start.x + (edge.port === 'then' ? -8 : 8), y: start.y + 18} : {x: path.x + 6, y: path.y};
    const text = svg('text', {x: at.x, y: at.y, class: 'flow-edge-label' + (active ? ' is-active' : '')});
    if (edge.port === 'then') text.setAttribute('text-anchor', 'end');
    text.textContent = edge.label.length > 30 ? edge.label.slice(0, 29) + '…' : edge.label;
    const title = svg('title'); title.textContent = edge.label; text.append(title);
    return text;
  }

  point(event) {
    const box = this.plane.getBoundingClientRect();
    return {x: (event.clientX - box.left) / this.scale, y: (event.clientY - box.top) / this.scale};
  }

  dragCard(event, card) {
    if (event.button !== 0 || event.target.closest('.flow-port')) return;
    const id = card.dataset.node, origin = {...this.positions[id]}, from = this.point(event);
    let moved = false;
    const move = e => {
      const p = this.point(e), dx = p.x - from.x, dy = p.y - from.y;
      if (!moved && Math.hypot(dx, dy) < 4) return;
      moved = true; card.classList.add('is-dragging');
      this.positions[id] = {x: Math.max(0, Math.round(origin.x + dx)), y: Math.max(0, Math.round(origin.y + dy))};
      this.place(card, this.positions[id]); this.drawEdges();
    };
    const up = () => {
      removeEventListener('pointermove', move); removeEventListener('pointerup', up);
      card.classList.remove('is-dragging');
      if (moved) { this.size(); this.handlers.onMove(id, this.positions[id]); } else this.handlers.onSelect(id);
    };
    addEventListener('pointermove', move); addEventListener('pointerup', up);
  }

  dragPort(event, from, port) {
    if (event.button !== 0 || !this.editable) return;
    event.preventDefault(); event.stopPropagation();
    const start = portPoint(this.positions[from], this.flow.nodes[from].kind, port);
    const line = svg('path', {class: 'flow-edge is-drawing'}); this.edgeGroup.append(line);
    const move = e => { const p = this.point(e); line.setAttribute('d', `M ${start.x} ${start.y} L ${p.x} ${p.y}`); };
    const up = e => {
      removeEventListener('pointermove', move); removeEventListener('pointerup', up); line.remove();
      const target = document.elementFromPoint?.(e.clientX, e.clientY)?.closest?.('.flow-node');
      if (target && this.plane.contains(target)) this.handlers.onConnect(from, port, target.dataset.node);
    };
    addEventListener('pointermove', move); addEventListener('pointerup', up);
  }
}
