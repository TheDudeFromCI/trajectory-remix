/* Remembers the Content mode (Text, Tool argument form, Raw JSON) chosen for each event, so
   re-rendering an event after a save or switching back to it doesn't reset the dropdown. */

export class ContentModes {
  constructor() { this.modes = new Map(); }
  static key(trajectory, event) { return JSON.stringify([trajectory, event]); }
  remember(trajectory, event, mode) { if (event) this.modes.set(ContentModes.key(trajectory, event), mode); }
  rename(trajectory, from, to) {
    const key = ContentModes.key(trajectory, from);
    if (this.modes.has(key)) { this.modes.set(ContentModes.key(trajectory, to), this.modes.get(key)); this.modes.delete(key); }
  }
  /** Structured content only works as Raw JSON; text content keeps the mode last chosen for it. */
  pick(trajectory, event, content) {
    if (typeof content !== 'string') return 'json';
    return this.modes.get(ContentModes.key(trajectory, event)) || 'text';
  }
}
