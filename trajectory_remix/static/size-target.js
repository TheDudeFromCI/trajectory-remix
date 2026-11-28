/* Target size: the trajectory's `task_length` metric (XXXS…XXXL, the size-label names), or the
   sampled proposal's task length when the metric is unset. Compared with the saved size label
   to warn when a trajectory is larger or smaller than intended. */

export const TARGET_KEY = 'task_length';

/** The target size name and where it came from ('metric' or 'proposal'), or null. */
export function targetSize(metrics) {
  const value = metrics?.values?.[TARGET_KEY];
  if (value && value.status !== 'unavailable' && typeof value.observation?.value === 'string') return {name: value.observation.value, from: 'metric'};
  const sampled = metrics?.targets?.sampling?.[TARGET_KEY];
  return typeof sampled === 'string' ? {name: sampled, from: 'proposal'} : null;
}

const tokens = n => n >= 1024 ? `${Math.round(n / 1024)}k` : String(n);

/** {direction: 'above' | 'below', current, target, ...} when the saved size misses the target, else null.
   `labels` are the project's size labels ({name, total_tokens}); an empty trajectory is never off target. */
export function sizeOffTarget({record, metrics, labels}) {
  const size = record?.size, target = targetSize(metrics);
  if (!size?.label || !target || !Object.keys(record.source?.events || {}).length) return null;
  const sorted = [...(labels || [])].sort((a, b) => a.total_tokens - b.total_tokens);
  const at = name => sorted.findIndex(l => l.name === name);
  const here = at(size.label), want = at(target.name);
  if (here < 0 || want < 0 || here === want) return null;
  return {direction: here > want ? 'above' : 'below', current: size.label, target: target.name, from: target.from,
    current_tokens: size.total_tokens_estimate, target_tokens: sorted[want].total_tokens};
}

/** The studio warning for a missed target size, or null. */
export function sizeWarning(args) {
  const off = sizeOffTarget(args);
  if (!off) return null;
  const source = off.from === 'proposal' ? ' (from its sampled proposal)' : '';
  return {code: `size_${off.direction}_target`, action: 'metrics',
    message: `This trajectory is about ${tokens(off.current_tokens)} tokens (${off.current}), ${off.direction} its target size ${off.target}${source}, about ${tokens(off.target_tokens)} tokens.`};
}
