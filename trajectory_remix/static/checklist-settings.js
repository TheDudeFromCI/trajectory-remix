/* Review Settings → one bullet list of review criteria (core and critical) with per-item actions. */
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const validId = id => /^[a-z][a-z0-9_-]*$/.test(id);
const GROUPS = ['core', 'critical'];
const HELP = 'Core criteria apply in both Fast and Critical Review. Unchecking Core limits a criterion to the more detailed Critical Review. Each review returns evidence for every criterion. Checklist changes mark affected signoffs stale; they do not automatically re-review trajectories.';

const marker = (label, aria, checked, change) => {
  const box = node('input'); box.type = 'checkbox'; box.checked = checked; box.setAttribute('aria-label', aria); box.onchange = () => change(box.checked);
  const n = node('label', undefined, 'criterion-marker'); n.append(box, node('span', label)); return n;
};
const iconButton = (label, aria, run, cls) => {
  const n = node('button', label, cls); n.type = 'button'; n.setAttribute('aria-label', aria); n.onclick = run; return n;
};

function criterionItem(id, group, criterion, {write, render}) {
  const li = node('li', undefined, 'checklist-item'), body = node('div', undefined, 'checklist-body');
  const text = node('span', criterion.text, 'checklist-text'), tag = node('code', id, 'checklist-id');
  body.append(text, tag);
  const actions = node('div', undefined, 'checklist-actions');
  const allowed = marker('N/A allowed', `criterion ${id} allow not applicable`, !!criterion.allow_not_applicable,
    value => write(d => { d.review_checklists[group][id].allow_not_applicable = value; }));
  const core = marker('Core', `criterion ${id} is core`, group === 'core', value => {
    write(d => {
      const lists = d.review_checklists, to = value ? 'core' : 'critical', from = value ? 'critical' : 'core';
      lists[to] ||= {}; lists[to][id] = lists[from][id]; delete lists[from][id];
    });
    render();
  });
  let editor = null;
  const edit = iconButton('Edit', `Edit criterion ${id}`, () => {
    if (editor) { editor.remove(); editor = null; text.hidden = false; edit.textContent = 'Edit'; return; }
    editor = node('textarea'); editor.value = criterion.text; editor.setAttribute('aria-label', `criterion ${id}`);
    editor.oninput = () => { write(d => { d.review_checklists[group][id].text = editor.value; }); text.textContent = editor.value; };
    text.hidden = true; text.after(editor); edit.textContent = 'Done'; editor.focus();
  });
  const del = iconButton('Delete', `Delete criterion ${id}`, () => { write(d => { delete d.review_checklists[group][id]; }); render(); }, 'danger');
  actions.append(allowed, core, edit, del); li.append(body, actions); return li;
}

export function renderChecklistSettings(target, {current, write, input, error, render, loadDefault, tipHeading}) {
  tipHeading(target, 'Review checklist', HELP);
  const lists = current().review_checklists || {}, list = node('ul', undefined, 'checklist-list');
  for (const group of GROUPS) for (const [id, criterion] of Object.entries(lists[group] || {})) list.append(criterionItem(id, group, criterion, {write, render}));
  if (!list.children.length) target.append(node('p', 'No criteria yet. Add one below.', 'settings-help')); else target.append(list);
  const id = input('New criterion ID', '', () => {}), add = node('div', undefined, 'settings-add-row'), label = node('label', 'New criterion ID');
  label.append(id); add.append(label, iconButton('Add criterion', 'Add criterion', () => {
    const key = id.value.trim(), taken = GROUPS.some(group => Object.hasOwn(current().review_checklists?.[group] || {}, key));
    if (!validId(key) || taken) return error('Use a unique criterion ID with lowercase letters, numbers, hyphens or underscores.');
    write(d => { d.review_checklists ||= {}; d.review_checklists.core ||= {}; d.review_checklists.core[key] = {text: 'New criterion', allow_not_applicable: false}; }); render();
  })); target.append(add);
  if (!loadDefault) return;
  const reset = node('div', undefined, 'settings-limits');
  reset.append(iconButton('Reset review checklists to defaults', 'Reset review checklists to defaults', async () => {
    let defaults;
    try { defaults = await loadDefault(); } catch (cause) { return error('Could not load the default checklists: ' + cause.message); }
    if (!confirm('Replace the Core and Critical review checklists with the defaults? Your added, edited and removed criteria in this draft will be lost. Nothing is saved until you choose Save settings.')) return;
    write(d => { d.review_checklists = structuredClone(defaults); }); render('Review checklists reset to the defaults. Choose Save settings to keep them.');
  }), node('p', 'Replaces the whole list with the packaged defaults. Saving changed checklists marks affected sign-offs stale.', 'settings-help'));
  target.append(reset);
}
