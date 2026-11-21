/* Remembers which collapsible settings cards the user closed, in localStorage. */
const KEY = 'settings-collapsed';
const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { return {}; } };

/** Make `details` remember its open/closed state under `id`. Open unless the user collapsed it. */
export function persistCollapse(details, id) {
  details.open = !load()[id];
  details.addEventListener('toggle', () => {
    const state = load();
    if (details.open) delete state[id]; else state[id] = true;
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* storage unavailable */ }
  });
}
