/* Link paths for the flowchart. Pure geometry, no DOM.
   - Down to the next row: a short S-curve into the top of the target.
   - Across one row (a switch chain): out of the port and into the target's side.
   - Back up (a loop) or down past other rows: along a side lane, so it never crosses
     a card. Lanes go on the side nearer the link, one lane per link. */
import {GAP_Y, NODE_H, NODE_W} from './flow-model.js';

const LANE_GAP = 16, LANE_START = 28;

export function routeKind(from, target) {
  if (Math.abs(target.y - (from.y - NODE_H)) < NODE_H / 2) return 'across';
  const drop = target.y - from.y;
  return drop > 10 && drop <= GAP_Y + 10 ? 'down' : 'lane';
}

/**
 * from: the port point (bottom of the source card); target: the target card position.
 * key names the target node: links into the same node share a lane, like a bus.
 */
export function route(from, target, lanes, key = '') {
  const {x: sx, y: sy} = from, tx = target.x + NODE_W / 2, ty = target.y;
  const kind = routeKind(from, target);
  if (kind === 'down') {
    return {kind, d: `M ${sx} ${sy} C ${sx} ${sy + 40}, ${tx} ${ty - 40}, ${tx} ${ty}`, x: (sx + tx) / 2, y: (sy + ty) / 2};
  }
  if (kind === 'across') {
    const left = target.x > sx, ex = left ? target.x : target.x + NODE_W, ey = target.y + NODE_H / 2;
    const reach = left ? -36 : 36;
    return {kind, d: `M ${sx} ${sy} C ${sx} ${sy + 30}, ${ex + reach} ${ey}, ${ex} ${ey}`, x: (sx + ex) / 2, y: sy + 18};
  }
  const side = (sx + tx) / 2 < lanes.middle ? 'left' : 'right';
  const lane = `${side}\n${key}`;
  if (!lanes.assigned.has(lane)) lanes.assigned.set(lane, lanes[side]++);
  const index = lanes.assigned.get(lane);
  const lx = side === 'left' ? Math.max(6, lanes.min - LANE_START - index * LANE_GAP) : lanes.max + LANE_START + index * LANE_GAP;
  const d = `M ${sx} ${sy} C ${sx} ${sy + 36}, ${lx} ${sy + 20}, ${lx} ${sy + 56} L ${lx} ${ty - 56} C ${lx} ${ty - 20}, ${tx} ${ty - 36}, ${tx} ${ty}`;
  return {kind, side, d, x: lx, y: (sy + ty) / 2};
}

/** Lane bookkeeping for one drawing pass over the given card positions. */
export function lanesFor(positions) {
  const xs = Object.values(positions);
  const min = Math.min(...xs.map(p => p.x)), max = Math.max(...xs.map(p => p.x + NODE_W));
  return {min, max, middle: (min + max) / 2, left: 0, right: 0, assigned: new Map()};
}
