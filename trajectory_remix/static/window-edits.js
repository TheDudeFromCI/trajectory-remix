/* Pure draft edits for windows: create, rename and delete (with the events that use them). */

const refersTo = (content, id) => Array.isArray(content) && content.some(p => p?.kind === 'window_ref' && p.window === id);

/** Events that delete along with window `id`: those written to it, and those whose content references it. */
export function affectedEvents(draft, id) {
  return Object.entries(draft?.events || {}).filter(([, e]) => e.window === id || refersTo(e.content, id))
    .map(([eventId, e]) => ({id: eventId, kind: e.kind, reason: e.window === id ? 'writes to this window' : 'references this window'}));
}

/** Count of events written to each window. */
export function eventCounts(draft) {
  const counts = new Map(Object.keys(draft?.windows || {}).map(id => [id, 0]));
  for (const e of Object.values(draft?.events || {})) counts.set(e.window, (counts.get(e.window) || 0) + 1);
  return counts;
}

/** Explain why `id` cannot name a new window in `draft`, or return an empty string. */
export function windowIdProblem(draft, id, current) {
  if (!id) return 'Enter a window ID.';
  if (id !== id.trim()) return 'Window IDs cannot start or end with spaces.';
  if (id !== current && Object.hasOwn(draft.windows, id)) return `A window named “${id}” already exists.`;
  return '';
}

export function createWindow(draft, id, props) {
  const problem = windowIdProblem(draft, id);
  if (problem) throw new Error(problem);
  draft.windows[id] = props;
}

/** Rename a window, keeping its position in the map and updating every event that uses it. */
export function renameWindow(draft, from, to) {
  if (from === to) return;
  if (!Object.hasOwn(draft.windows, from)) throw new Error(`Window “${from}” does not exist.`);
  const problem = windowIdProblem(draft, to, from);
  if (problem) throw new Error(problem);
  draft.windows = Object.fromEntries(Object.entries(draft.windows).map(([id, w]) => [id === from ? to : id, w]));
  for (const e of Object.values(draft.events)) {
    if (e.window === from) e.window = to;
    if (Array.isArray(e.content)) for (const p of e.content) if (p?.kind === 'window_ref' && p.window === from) p.window = to;
  }
}

/** Delete a window and its affected events; other events stop depending on them. Returns the deleted event IDs. */
export function deleteWindow(draft, id) {
  const removed = affectedEvents(draft, id).map(e => e.id), gone = new Set(removed);
  delete draft.windows[id];
  for (const eventId of removed) delete draft.events[eventId];
  for (const e of Object.values(draft.events)) if (e.after?.some(dep => gone.has(dep))) e.after = e.after.filter(dep => !gone.has(dep));
  return removed;
}
