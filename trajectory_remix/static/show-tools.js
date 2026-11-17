/* Show tools for manual authoring: list tool schemas for the agent as a masked event.
   The listing text comes from POST /tools/listing, so it matches what generation writes,
   including the repeatable random additions. */
import {node} from './ui-kit.js';

const unused = (taken, base) => {
  if (!(base in taken)) return base;
  let n = 2; while (`${base}_${n}` in taken) n++;
  return `${base}_${n}`;
};

/** Events that follow nothing: the start of the trajectory, normally the opening. */
export const rootEvents = (draft, except) => Object.entries(draft.events || {}).filter(([id, e]) => id !== except && !(e.after || []).length).map(([id]) => id);

/**
 * Add the listing to a draft (mutated): a masked external_text event in a window whose
 * purpose is "tools", created if needed. With beforeRoots, events that followed nothing
 * now follow the listing, so everything after it, the agent included, can see it.
 */
export function addListingEvent(draft, {eventId, window, content, beforeRoots}) {
  if (!draft.windows[window]) draft.windows[window] = {purpose: 'tools', agent_writable: false};
  const roots = beforeRoots ? rootEvents(draft, eventId) : [];
  draft.events[eventId] = {kind: 'external_text', window, after: [], content, close_window: false, replace_body: false, supervision: {masked: true, weight: 1}};
  for (const id of roots) draft.events[id].after = [eventId];
  return {eventId, followers: roots};
}

/** Open the Show tools dialog. onAdd({eventId, followers, listing}) runs after the draft changes. */
export function openShowTools({api, draft, tools, onAdd, report = () => {}}) {
  document.getElementById('show-tools-dialog')?.remove();
  const dialog = node('dialog'); dialog.id = 'show-tools-dialog'; dialog.setAttribute('aria-labelledby', 'show-tools-title');
  const head = node('div', undefined, 'dialog-head'), title = node('h2', 'Show tools'); title.id = 'show-tools-title';
  const close = node('button', 'Close', 'ghost sm'); close.type = 'button'; close.onclick = () => dialog.close();
  head.append(title, close);
  const body = node('div', undefined, 'dialog-body stack');
  body.append(node('p', 'Lists the chosen tools\' schemas for the agent as a masked event in a tools window, the same listing a generation run writes. The agent sees it in every event that follows it.', 'note'));

  const ids = Object.keys(tools || {});
  const picks = node('fieldset', undefined, 'show-tools-list'); picks.append(node('legend', 'Tools to list'));
  if (!ids.length) picks.append(node('p', 'No tools in the project. Add or import some in Settings → Tools.', 'small muted'));
  for (const id of ids) {
    const box = node('input'); box.type = 'checkbox'; box.value = id; box.setAttribute('aria-label', 'List tool ' + id);
    const schema = tools[id].input_schema || {}, label = node('label', undefined, 'checkbox');
    label.append(box, ' ', node('code', id), node('span', schema.description ? ' — ' + schema.description : '', 'small muted'));
    picks.append(label);
  }
  const random = node('input'); random.type = 'checkbox'; random.setAttribute('aria-label', 'Randomly add additional tools');
  const chance = node('input'); chance.type = 'number'; chance.min = '0'; chance.max = '1'; chance.step = '0.05'; chance.value = '0.5'; chance.setAttribute('aria-label', 'Chance for each additional tool');
  const randomRow = node('div', undefined, 'row wrap'), randomLabel = node('label', undefined, 'checkbox');
  randomLabel.append(random, ' Randomly add additional tools');
  const chanceLabel = node('label', 'Chance for each'); chanceLabel.append(chance);
  randomRow.append(randomLabel, chanceLabel);

  const grid = node('div', undefined, 'form-grid two');
  const ident = node('input'); ident.value = unused(draft.events || {}, 'tools'); ident.className = 'mono'; ident.setAttribute('aria-label', 'Event ID');
  const windowPick = node('select'); windowPick.setAttribute('aria-label', 'Window');
  const toolWindows = Object.entries(draft.windows || {}).filter(([, w]) => w.purpose === 'tools').map(([id]) => id);
  for (const id of toolWindows) { const o = node('option', id); o.value = id; windowPick.append(o); }
  const fresh = unused(draft.windows || {}, 'tools'), freshOption = node('option', `New window: ${fresh}`); freshOption.value = fresh; windowPick.append(freshOption);
  const idLabel = node('label', 'Event ID'); idLabel.append(ident);
  const windowLabel = node('label', 'Window'); windowLabel.append(windowPick);
  grid.append(idLabel, windowLabel);
  const roots = rootEvents(draft);
  const before = node('input'); before.type = 'checkbox'; before.checked = roots.length > 0; before.disabled = !roots.length; before.setAttribute('aria-label', 'Place before the first events');
  const beforeLabel = node('label', undefined, 'checkbox');
  beforeLabel.append(before, roots.length ? ` Place it before the first event${roots.length > 1 ? 's' : ''} (${roots.join(', ')}), so the agent sees it` : ' No events yet; later events can follow it');
  const status = node('p', '', 'status-line'); status.setAttribute('role', 'status');
  body.append(picks, randomRow, grid, beforeLabel, status);

  const foot = node('div', undefined, 'dialog-foot'), add = node('button', 'Add listing to draft', 'primary'); add.type = 'button';
  add.onclick = async () => {
    const chosen = [...picks.querySelectorAll('input:checked')].map(b => b.value);
    const eventId = ident.value.trim(), value = Number(chance.value);
    try {
      if (!chosen.length) throw new Error('Choose at least one tool to list.');
      if (!eventId || eventId in draft.events) throw new Error('Use a new, unique event ID.');
      if (random.checked && !(value >= 0 && value <= 1)) throw new Error('Use a chance from 0 to 1.');
      add.disabled = true;
      const listing = await api('/tools/listing', 'POST', {trajectory_id: draft.id, event_id: eventId, tools: chosen, extra_tools: random.checked ? value : 0});
      const added = addListingEvent(draft, {eventId, window: windowPick.value, content: listing.content, beforeRoots: before.checked && !before.disabled});
      dialog.close(); onAdd({...added, listing});
      report(`Listed ${listing.shown.length} tools in ${eventId}${listing.added.length ? ` (${listing.added.length} added at random)` : ''}. Save to keep it.`);
    } catch (error) { status.textContent = error.message; status.classList.add('error'); }
    finally { add.disabled = false; }
  };
  foot.append(node('span', '', 'grow'), add);
  dialog.append(head, body, foot);
  dialog.addEventListener('close', () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal?.();
  return dialog;
}
