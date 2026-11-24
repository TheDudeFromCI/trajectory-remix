/* "Working..." indicator shown while the Assistant endpoint's job owns the selected trajectory. */
import {node} from './ui-kit.js';

const ID = 'assistant-working';

function build() {
  const el = node('span', undefined, 'assistant-working');
  el.id = ID; el.setAttribute('role', 'status');
  const dots = node('span', undefined, 'working-dots');
  dots.setAttribute('aria-hidden', 'true');
  dots.append(...[1, 2, 3].map(() => node('span', '.')));
  el.append(node('span', undefined, 'spinner'), node('span', 'Working'), dots);
  return el;
}

/** Show or hide the indicator inside `slot`; call whenever the owner or the job list changes. */
export function renderAssistantIndicator(slot, working) {
  let el = slot.querySelector(`#${ID}`);
  if (working && !el) { el = build(); slot.prepend(el); }
  if (el) el.hidden = !working;
}

/** True when the trajectory is owned by an assistant job (summaries carry `action`). */
export const isAssistantOwner = (owner, jobs) => !!owner && jobs.get(owner)?.action === 'assistant';
