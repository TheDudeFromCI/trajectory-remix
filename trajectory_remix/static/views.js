/* Top-level view switching shared by the shell, the controller and the Sampler. */
const VIEWS = {workspace: 'trajectory-tab', 'jobs-view': 'jobs-button', 'metrics-view': 'metrics-button', 'sampler-workbench': 'sampler-tab', 'review-view': 'review-tab'};

export function currentView() {
  return Object.keys(VIEWS).find(id => document.getElementById(id) && !document.getElementById(id).hidden) || 'workspace';
}

export function showView(id) {
  if (!VIEWS[id]) return;
  for (const [view, tab] of Object.entries(VIEWS)) {
    const panel = document.getElementById(view), button = document.getElementById(tab);
    if (panel) panel.hidden = view !== id;
    button?.setAttribute('aria-selected', String(view === id));
  }
  document.dispatchEvent(new CustomEvent('studio-view', {detail: id}));
}

let bound = false;
/** Idempotent: both the shell and the controller call this. */
export function bindViewTabs() {
  if (bound) return; bound = true;
  for (const [view, tab] of Object.entries(VIEWS)) document.getElementById(tab)?.addEventListener('click', () => showView(view));
}
