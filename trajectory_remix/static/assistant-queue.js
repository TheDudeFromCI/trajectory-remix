/* Auto-send comments that arrive while a job owns the trajectory wait here, then go to the
   Assistant as one extra job once the trajectory is free. One entry per trajectory, because the
   job sends every comment that is open at that moment. */

export class AssistantQueue {
  constructor() { this.waiting = new Set(); }
  hold(id) { this.waiting.add(id); }
  has(id) { return this.waiting.has(id); }
  clear(id) { this.waiting.delete(id); }
  /** Whether the held trajectory should be sent now. Forgets it when there is nothing left to send. */
  ready(id, {owner, dirty, comments, auto}) {
    if (!this.has(id)) return false;
    if (!auto || !comments.some(c => c.status === 'open')) { this.clear(id); return false; }
    return !owner && !dirty;
  }
}
