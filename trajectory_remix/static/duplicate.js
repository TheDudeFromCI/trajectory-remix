/* Duplicate a trajectory: a deep copy of the saved source under a fresh identity. */

/** Copy `source` with a new `id`; windows, events and metadata are kept unchanged. */
export function duplicateSource(source, id = crypto.randomUUID()) {
  if (!source || typeof source !== 'object') throw new Error('Select a trajectory to duplicate.');
  return {...structuredClone(source), id};
}

/** Create the copy through the studio API and return the new trajectory record. */
export async function duplicateTrajectory(api, source) {
  return api('/trajectories', 'POST', duplicateSource(source));
}
