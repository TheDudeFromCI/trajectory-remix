/* Tool checkboxes for the event editor: for the event `tools` in the window `tools`, the listing
   is edited by ticking tools instead of typing. Each tool is one JSON line in the event's text; ticking
   adds the server-rendered line (POST /tools/listing), unticking removes it. */
import {node} from './ui-kit.js';

const nameOf = line => { try { const v = JSON.parse(line); return v && typeof v.name === 'string' ? v.name : null; } catch { return null; } };
const linesOf = text => text.split('\n').filter(line => line.trim());

/** Tool names found in a listing (lines that are not tool entries are ignored). */
export const listedNames = text => linesOf(text).map(nameOf).filter(Boolean);

/** The listing with one tool added (using its rendered line) or removed. Other lines are kept as they are. */
export function withTool(text, name, on, line) {
  const kept = linesOf(text).filter(l => nameOf(l) !== name);
  if (on && line) kept.push(line.trim());
  return kept.join('\n');
}

/**
 * Fill `container` with a checkbox per project tool (plus listed names the project no longer has).
 * lineFor(name) → Promise<string> renders a tool's line; onChange(newText) receives the edited listing.
 */
export function renderToolPicker(container, {text, tools, lineFor, onChange, disabled = false, report = () => {}}) {
  const listed = new Set(listedNames(text)), ids = [...Object.keys(tools || {}), ...[...listed].filter(n => !(n in (tools || {})))];
  const fieldset = node('fieldset', undefined, 'tool-picker'); fieldset.append(node('legend', 'Tools in this window'));
  if (!ids.length) fieldset.append(node('p', 'No tools in the project. Add or import some in Settings → Tools.', 'small muted'));
  for (const id of ids) {
    const box = node('input'); box.type = 'checkbox'; box.value = id; box.checked = listed.has(id); box.disabled = disabled;
    box.setAttribute('aria-label', 'List tool ' + id);
    box.onchange = async () => {
      box.disabled = true;
      try { onChange(withTool(text, id, box.checked, box.checked ? await lineFor(id) : '')); }
      catch (error) { box.checked = !box.checked; box.disabled = disabled; report(error.message); }
    };
    const unknown = !(id in (tools || {})), label = node('label', undefined, 'checkbox');
    label.append(box, ' ', node('code', id), unknown ? node('span', ' (not in project tools)', 'small muted') : '');
    fieldset.append(label);
  }
  container.replaceChildren(fieldset);
}
