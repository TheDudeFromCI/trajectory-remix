/* Rename an event in a draft source, updating every `after` reference to it. */

/** Why `next` cannot replace `current` as an event ID, or '' when it can. */
export function renameProblem(events, current, next) {
  if (!next || !next.trim()) return 'Event name cannot be empty.';
  if (next === current) return '';
  if (Object.hasOwn(events, next)) return `An event named "${next}" already exists.`;
  return '';
}

/** Rename `current` to `next` in place, keeping event order. Returns false when nothing changed. */
export function renameEvent(draft, current, next) {
  if (next === current || !Object.hasOwn(draft.events, current)) return false;
  const events = {};
  for (const [id, event] of Object.entries(draft.events)) {
    if (Array.isArray(event.after)) event.after = event.after.map(dep => (dep === current ? next : dep));
    events[id === current ? next : id] = event;
  }
  draft.events = events;
  return true;
}
