/* Review Settings → export/import a shareable pack: the three prompts plus every checklist item. */
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const FORMAT = 'trajectory-remix-review-pack', VERSION = 1;
const PROMPT_KEYS = ['actor', 'draft', 'assembly'], GROUPS = ['core', 'critical'];
const validId = id => /^[a-z][a-z0-9_-]*$/.test(id);
const isObject = v => v && typeof v === 'object' && !Array.isArray(v);

export function buildPack(settings) {
  const pack = {format: FORMAT, version: VERSION, prompts: {}, review_checklists: {core: {}, critical: {}}};
  for (const key of PROMPT_KEYS) pack.prompts[key] = settings.prompts?.[key] ?? '';
  for (const group of GROUPS) {
    for (const [id, item] of Object.entries(settings.review_checklists?.[group] || {})) {
      pack.review_checklists[group][id] = {text: item.text, allow_not_applicable: !!item.allow_not_applicable};
    }
  }
  return pack;
}

/** Returns a clean {prompts, review_checklists} or throws an Error describing the first problem. */
export function parsePack(text) {
  let data;
  try { data = JSON.parse(text); } catch { throw new Error('The file is not valid JSON.'); }
  if (!isObject(data) || data.format !== FORMAT) throw new Error('This is not a Trajectory Remix review pack.');
  if (data.version !== VERSION) throw new Error(`Unsupported pack version ${JSON.stringify(data.version)}.`);
  if (!isObject(data.prompts)) throw new Error('The pack has no prompts.');
  const prompts = {};
  for (const key of PROMPT_KEYS) {
    if (typeof data.prompts[key] !== 'string') throw new Error(`Prompt "${key}" must be text.`);
    prompts[key] = data.prompts[key];
  }
  if (!isObject(data.review_checklists)) throw new Error('The pack has no checklists.');
  const lists = {core: {}, critical: {}}, seen = new Set();
  for (const group of GROUPS) {
    const items = data.review_checklists[group] ?? {};
    if (!isObject(items)) throw new Error(`Checklist "${group}" must be an object.`);
    for (const [id, item] of Object.entries(items)) {
      if (!validId(id)) throw new Error(`Criterion ID "${id}" must use lowercase letters, numbers, hyphens or underscores.`);
      if (seen.has(id)) throw new Error(`Criterion ID "${id}" appears in both Core and Critical.`);
      if (!isObject(item) || typeof item.text !== 'string' || !item.text.trim()) throw new Error(`Criterion "${id}" needs text.`);
      if (item.allow_not_applicable !== undefined && typeof item.allow_not_applicable !== 'boolean') throw new Error(`Criterion "${id}" has an invalid N/A setting.`);
      seen.add(id); lists[group][id] = {text: item.text, allow_not_applicable: !!item.allow_not_applicable};
    }
  }
  return {prompts, review_checklists: lists};
}

function download(pack) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(pack, null, 2) + '\n'], {type: 'application/json'}));
  const a = node('a'); a.href = url; a.download = 'review-pack.json'; document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function renderReviewPack(target, {current, write, error, render}) {
  const box = node('div', undefined, 'settings-limits review-pack');
  const exportButton = node('button', 'Export pack'); exportButton.type = 'button'; exportButton.onclick = () => download(buildPack(current()));
  const file = node('input'); file.type = 'file'; file.accept = 'application/json,.json'; file.hidden = true; file.setAttribute('aria-label', 'Review pack file');
  file.onchange = async () => {
    const chosen = file.files?.[0]; if (!chosen) return;
    let pack;
    try { pack = parsePack(await chosen.text()); } catch (cause) { file.value = ''; return error('Could not import the pack: ' + cause.message); }
    file.value = '';
    if (!confirm('Replace the agent prompt, Draft and Assembly instructions, and all Core and Critical criteria with this pack? Your edits in this draft will be lost. Nothing is saved until you choose Save settings.')) return;
    write(d => { d.prompts = {...(d.prompts || {}), ...pack.prompts}; d.review_checklists = structuredClone(pack.review_checklists); });
    render('Review pack imported. Choose Save settings to keep it.');
  };
  const importButton = node('button', 'Import pack'); importButton.type = 'button'; importButton.onclick = () => file.click();
  const row = node('div', undefined, 'settings-add-row'); row.append(exportButton, importButton, file);
  box.append(row, node('p', 'A pack is a JSON file with the agent prompt, the Draft and Assembly instructions and every checklist item. Share it and import it into another project.', 'settings-help'));
  target.append(box);
}
