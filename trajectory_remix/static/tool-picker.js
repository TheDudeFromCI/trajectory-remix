/* Tool checkboxes for tool_schema events in the event editor. Ticking tools re-renders the
   listing on the server (POST /tools/listing). Only chosen tools are stored; random extras are
   added when the trajectory compiles and show grayed out in windows (see Preferences). */
import {node} from './ui-kit.js';

const nameOf = line => { try { const v = JSON.parse(line); return v && typeof v.name === 'string' ? v.name : null; } catch { return null; } };

/** Tool names found in a listing (lines that are not tool entries are ignored). */
export const listedNames = text => text.split('\n').map(nameOf).filter(Boolean);


/**
 * Fill `container` with a checkbox per project tool (plus chosen names the project no longer has).
 * relist(chosen) → Promise runs after each tick with the new chosen names.
 */
export function renderToolPicker(container, {text, tools, relist, disabled = false, report = () => {}}) {
  const chosen = new Set(listedNames(text)), known = tools || {};
  const ids = [...Object.keys(known), ...[...chosen].filter(name => !(name in known))];
  const fieldset = node('fieldset', undefined, 'tool-picker'); fieldset.append(node('legend', 'Tools shown to the agent'));
  if (!ids.length) fieldset.append(node('p', 'No tools in the project. Add or import some in Settings → Tools.', 'small muted'));
  for (const id of ids) {
    const box = node('input'); box.type = 'checkbox'; box.value = id; box.checked = chosen.has(id); box.disabled = disabled;
    box.setAttribute('aria-label', 'List tool ' + id);
    box.onchange = async () => {
      const next = [...ids.filter(other => other !== id && chosen.has(other)), ...(box.checked ? [id] : [])];
      box.disabled = true;
      try { await relist(next); }
      catch (error) { box.checked = !box.checked; box.disabled = disabled; report(error.message); }
    };
    const label = node('label', undefined, 'checkbox');
    label.append(box, ' ', node('code', id));
    if (!(id in known)) label.append(node('span', ' (not in project tools)', 'small muted'));
    fieldset.append(label);
  }
  container.replaceChildren(fieldset);
}
