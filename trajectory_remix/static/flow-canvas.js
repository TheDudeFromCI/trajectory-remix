/* Flowchart canvas: node cards over an SVG edge layer. Drag cards to move them, drag a
   port onto a card to connect it, click a card to select it. No network access. */
import {node, tag} from './ui-kit.js';
import {KINDS, NODE_H, NODE_W, edges, subtitle} from './flow-model.js';
import {lanesFor, route} from './flow-routes.js';

const SVG = 'http://www.w3.org/2000/svg';
export const MIN_ZOOM = 0.2, MAX_ZOOM = 3;
const svg = (name, attrs = {}) => {
  const el = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  return el;
};

function portPoint(pos, kind, port) {
  const ports = KINDS[kind]?.ports || [], i = Math.max(0, ports.indexOf(port));
  return {x: pos.x + NODE_W * (i + 1) / (ports.length + 1), y: pos.y + NODE_H};
}

export class FlowCanvas {
  constructor(root, {onSelect = () => {}, onConnect = () => {}, onMove = () => {}, onZoom = () => {}} = {}) {
    this.root = root; this.handlers = {onSelect, onConnect, onMove, onZoom}; this.scale = 1;
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
    this.bindNavigation();
  }

  /** Wheel or pinch zooms around the pointer; dragging empty space (or the middle
      button anywhere) pans. Cards and ports keep their own drag behaviour. */
  bindNavigation() {
    this.root.addEventListener('wheel', event => {
      event.preventDefault();
      // Trackpad pinches arrive as ctrl+wheel with small deltas; mouse wheels as ~100.
      const factor = Math.exp(-event.deltaY * (event.ctrlKey ? 0.01 : 0.0015));
      this.zoomAt(this.scale * factor, event.clientX, event.clientY);
    }, {passive: false});
    this.root.addEventListener('pointerdown', event => {
      const onCard = event.target.closest?.('.flow-node');
      if (!(event.button === 1 || (event.button === 0 && !onCard))) return;
      event.preventDefault();
      const start = {x: event.clientX, y: event.clientY, left: this.root.scrollLeft, top: this.root.scrollTop};
      this.root.classList.add('is-panning');
      const move = e => { this.root.scrollLeft = start.left - (e.clientX - start.x); this.root.scrollTop = start.top - (e.clientY - start.y); };
      const up = () => { removeEventListener('pointermove', move); removeEventListener('pointerup', up); this.root.classList.remove('is-panning'); };
      addEventListener('pointermove', move); addEventListener('pointerup', up);
    });
  }

  /** Zoom keeping the chart point under (clientX, clientY) where it is on screen. */
  zoomAt(scale, clientX, clientY) {
    const box = this.root.getBoundingClientRect();
    const at = {x: clientX - box.left, y: clientY - box.top};
    const point = {x: (this.root.scrollLeft + at.x) / this.scale, y: (this.root.scrollTop + at.y) / this.scale};
    const applied = this.setZoom(scale);
    const want = {x: point.x * applied - at.x, y: point.y * applied - at.y};
    // A chart narrower or shorter than the view would stop scrolling at its edge and slide
    // away from the pointer, so the scroll area gets just enough room past it.
    const w = (parseFloat(this.plane.style.width) || 0) * applied, h = (parseFloat(this.plane.style.height) || 0) * applied;
    this.slack = {x: Math.max(0, want.x + this.root.clientWidth - w), y: Math.max(0, want.y + this.root.clientHeight - h)};
    this.size();
    this.root.scrollLeft = want.x;
    this.root.scrollTop = want.y;
    return applied;
  }

  /** Zoom so the whole chart fits the visible area (never above 100%). */
  fit() {
    const width = parseFloat(this.plane.style.width) || 0, height = parseFloat(this.plane.style.height) || 0;
    const room = {w: this.root.clientWidth - 16, h: this.root.clientHeight - 16};
    if (!width || !height || room.w <= 0 || room.h <= 0) return this.scale;
    this.slack = null;
    return this.setZoom(Math.min(1, room.w / width, room.h / height));
  }

  setZoom(scale) {
    this.scale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, scale));
    this.plane.style.transform = `scale(${this.scale})`;
    this.size();
    this.handlers.onZoom(this.scale);
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
    const slack = this.slack || {x: 0, y: 0};
    this.root.style.setProperty('--plane-w', w * this.scale + slack.x + 'px'); this.root.style.setProperty('--plane-h', h * this.scale + slack.y + 'px');
  }

  drawEdges() {
    const group = this.edgeGroup; group.replaceChildren();
    const lanes = lanesFor(this.positions), active = this.trail?.edge;
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
      const path = route(start, target, lanes, edge.to);
      const cls = `flow-edge port-${edge.port}` + (isActive ? ' is-active' : '');
      const line = svg('path', {d: path.d, class: cls, 'marker-end': `url(#flow-${isActive ? 'arrow-active' : 'arrow'})`});
      line.dataset.from = edge.from; line.dataset.port = edge.port;
      group.append(line, this.label(edge, start, path, isActive));
    }
  }

  /** Labels sit beside the port (then left, others right), except on short straight drops. */
  label(edge, start, path, active) {
    const besidePort = edge.port === 'then' || edge.port === 'otherwise' || path.kind !== 'down';
    const at = besidePort ? {x: start.x + (edge.port === 'then' ? -8 : 8), y: start.y + 18} : {x: path.x + 6, y: path.y};
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
