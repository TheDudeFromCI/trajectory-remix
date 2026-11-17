/* Import tool definitions from a JSON file into the settings draft. No network. */
import {node} from './ui-kit.js';

const TOOL_ID = /^[a-z][a-z0-9_-]*$/;

/** Accepts {"tools": {...}} (a settings file or catalogue) or a bare {id: tool} map. */
export function readCatalogue(text) {
  let data;
  try { data = JSON.parse(text); } catch { throw new Error('The file is not valid JSON.'); }
  const tools = data && typeof data === 'object' && !Array.isArray(data) ? (data.tools ?? data) : null;
  if (!tools || typeof tools !== 'object' || Array.isArray(tools)) throw new Error('Expected {"tools": {id: definition}} or {id: definition}.');
  for (const [id, tool] of Object.entries(tools)) {
    if (!TOOL_ID.test(id)) throw new Error(`Tool ID ${id} must start with a lowercase letter and use letters, digits, underscores or hyphens.`);
    if (!tool || typeof tool !== 'object' || typeof tool.name !== 'string' || !tool.name) throw new Error(`Tool ${id} needs a name.`);
    if (tool.input_schema !== undefined && typeof tool.input_schema !== 'object' && typeof tool.input_schema !== 'boolean') throw new Error(`Tool ${id} has an invalid input_schema.`);
  }
  return tools;
}

/** Merge into current (mutated). Same IDs are kept unless replace is true. */
export function mergeTools(current, incoming, {replace = false} = {}) {
  const result = {added: [], replaced: [], kept: []};
  for (const [id, tool] of Object.entries(incoming)) {
    if (!Object.hasOwn(current, id)) { current[id] = structuredClone(tool); result.added.push(id); }
    else if (replace) { current[id] = structuredClone(tool); result.replaced.push(id); }
    else result.kept.push(id);
  }
  return result;
}

export function describeMerge({added, replaced, kept}) {
  const parts = [];
  if (added.length) parts.push(`Added ${added.length}: ${added.join(', ')}.`);
  if (replaced.length) parts.push(`Replaced ${replaced.length}: ${replaced.join(', ')}.`);
  if (kept.length) parts.push(`Kept your existing ${kept.join(', ')}.`);
  return (parts.join(' ') || 'No tools in the file.') + ' Save settings to apply.';
}

/** A file picker and "replace existing" switch; onMerge(tools, replace) applies them. */
export function toolImportControls(onMerge) {
  const row = node('div', undefined, 'inline-form tool-import');
  const file = node('input'); file.type = 'file'; file.accept = '.json,application/json'; file.hidden = true;
  file.setAttribute('aria-label', 'Tool definitions JSON file');
  const replace = node('input'); replace.type = 'checkbox'; replace.setAttribute('aria-label', 'Replace tools with the same ID');
  const replaceLabel = node('label', undefined, 'checkbox'); replaceLabel.append(replace, ' Replace tools with the same ID');
  const pick = node('button', 'Import tools from JSON…', 'sm'); pick.type = 'button'; pick.onclick = () => file.click();
  const status = node('p', '', 'settings-help'); status.setAttribute('role', 'status');
  file.onchange = async () => {
    const chosen = file.files?.[0]; if (!chosen) return;
    try { status.textContent = onMerge(readCatalogue(await chosen.text()), replace.checked); status.classList.remove('error'); }
    catch (error) { status.textContent = error.message; status.classList.add('error'); }
    file.value = '';
  };
  row.append(pick, replaceLabel, file);
  return [row, status];
}
