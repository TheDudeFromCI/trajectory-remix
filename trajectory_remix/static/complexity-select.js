/* Complexity and Target size pickers on the Metrics tab. Each shows and sets one category metric
   (`complexity`, `task_length`), the same value as the metric list, and offers the sampler's
   target as a hint when none is set. */
import {node} from './ui-kit.js';

const title = id => id.charAt(0).toUpperCase() + id.slice(1);

/** Fill the select from a metrics view (or clear it when there is none). */
export function renderComplexity(select, view, locked, key = 'complexity') {
  const value = view?.values?.[key], choices = value?.definition?.choices || [];
  const target = view?.targets?.sampling?.[key];
  const unset = node('option', target && choices.includes(target) ? `Not set (target ${title(target)})` : 'Not set');
  unset.value = '';
  const wanted = [unset, ...choices.map(id => { const o = node('option', title(id)); o.value = id; return o; })];
  if ([...select.options].map(o => o.value + o.textContent).join() !== wanted.map(o => o.value + o.textContent).join()) select.replaceChildren(...wanted);
  const current = value?.status === 'unavailable' ? '' : value?.observation?.value ?? '';
  select.value = choices.includes(current) ? current : '';
  select.disabled = !value || !!locked;
}
