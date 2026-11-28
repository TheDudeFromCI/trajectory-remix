/* Studio warnings: editor-side checks shown in the Problems tab next to the compiler's diagnostics.
   They are worked out in the browser from the draft, the saved record and the metric values, so
   they never reach the compiler, export gating or the agents that read diagnostics. */
import {sizeWarning} from './size-target.js';

const words = value => String(value).replaceAll('_', ' ');
const list = names => names.length <= 3 ? names.join(', ') : `${names.slice(0, 3).join(', ')} and ${names.length - 3} more`;

/** Warnings for the selected trajectory. `metrics` is the Metrics tab's view, if loaded;
   `sizeLabels` are the project's size labels, for the target-size check.
   Each warning: {severity: 'warning', source: 'studio', code, message, action?: 'split' | 'metrics'}. */
export function studioWarnings({draft, record, metrics, sizeLabels = []}) {
  if (!draft || !record) return [];
  const found = [];
  if (!draft.metadata?.split) found.push({code: 'missing_split', action: 'split',
    message: 'No split chosen. Export assigns one automatically and warns. Choose Auto to opt in without a warning.'});
  const review = record.review_status;
  if (review?.stale) {
    const what = review.reviewed ? 'review' : 'signoff';
    found.push({code: `${what}_stale`, message: `The last ${what} no longer matches this trajectory (${list(review.reasons.map(words))}). Run Review from More to refresh it.`});
  }
  const stale = Object.entries(metrics?.id === record.source?.id ? metrics.values || {} : {})
    .filter(([, value]) => value.status === 'stale').map(([key, value]) => value.definition?.label || words(key));
  if (stale.length) found.push({code: 'stale_metrics', action: 'metrics',
    message: `${stale.length} metric value${stale.length === 1 ? ' was' : 's were'} recorded against older content: ${list(stale)}. Confirm or change ${stale.length === 1 ? 'it' : 'them'} in the Metrics tab.`});
  const size = metrics?.id === record.source?.id ? sizeWarning({record, metrics, labels: sizeLabels}) : null;
  if (size) found.push(size);
  return found.map(w => ({severity: 'warning', source: 'studio', ...w}));
}
