/* Dashboard-only view of a tools window: a list of tool names instead of raw JSON.
   The tools listing is one JSON object per line ({"name", "description", "parameters"}).
   Purely visual; the stored events and tokens are never changed, and Raw mode shows them as is. */
import {node} from './ui-kit.js';

/** Parse a tools listing into [{name, description}], or null if any line is not a named tool. */
export function parseToolListing(text) {
  const lines = text.split('\n').map(line => line.trim()).filter(Boolean);
  if (!lines.length) return null;
  const tools = [];
  for (const line of lines) {
    let entry;
    try { entry = JSON.parse(line); } catch { return null; }
    if (!entry || typeof entry !== 'object' || typeof entry.name !== 'string' || !entry.name) return null;
    tools.push({name: entry.name, description: typeof entry.description === 'string' ? entry.description : ''});
  }
  return tools;
}

/**
 * A formatted list element, or null when the text is not a tools listing.
 * Names in `extras` (tools added at random) are grayed out, or left out with hideExtras.
 */
export function toolListBlock(text, {extras = [], hideExtras = false} = {}) {
  const all = parseToolListing(text);
  if (!all) return null;
  const tools = hideExtras ? all.filter(tool => !extras.includes(tool.name)) : all;
  const list = node('ul', undefined, 'wt-tools');
  list.setAttribute('aria-label', `${tools.length} tool${tools.length === 1 ? '' : 's'}`);
  for (const tool of tools) {
    const item = node('li', undefined, 'wt-tool' + (extras.includes(tool.name) ? ' random' : ''));
    if (extras.includes(tool.name)) item.dataset.tip = 'Added at random; not one of the chosen tools.';
    item.append(node('code', tool.name, 'wt-tool-name'));
    if (tool.description && tool.description !== tool.name) item.append(node('span', tool.description, 'wt-tool-desc small muted'));
    list.append(item);
  }
  return list;
}
