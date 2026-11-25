/* Complexity picker in the trajectory bar. It shows and sets the `complexity` metric, the same
   value as the Metrics tab, and offers the sampler's target as a hint when none is set. */
import {node} from './ui-kit.js';

const title = id => id.charAt(0).toUpperCase() + id.slice(1);

/** Fill the select from a metrics view (or clear it when there is none). */
export function renderComplexity(select, view, locked) {
  const value = view?.values?.complexity, choices = value?.definition?.choices || [];
  const target = view?.targets?.sampling?.complexity;
  const unset = node('option', target && choices.includes(target) ? `Not set (target ${title(target)})` : 'Not set');
  unset.value = '';
  const wanted = [unset, ...choices.map(id => { const o = node('option', title(id)); o.value = id; return o; })];
  if ([...select.options].map(o => o.value + o.textContent).join() !== wanted.map(o => o.value + o.textContent).join()) select.replaceChildren(...wanted);
  const current = value?.status === 'unavailable' ? '' : value?.observation?.value ?? '';
  select.value = choices.includes(current) ? current : '';
  select.disabled = !value || !!locked;
}
