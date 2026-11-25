/* Pop-out panels: move a live editor panel into a second browser window (for a second monitor).
   The panel's elements move as they are, so their listeners and the studio state keep working.
   The popup (panel-popout.html) asks for the panel through `panelPopout.attach`, so it
   reconnects after either window reloads. Closing the popup puts the panel back. Lookups by id
   go through `byId`, which also searches the popup. */

const PANELS = {
  timeline: {selector: '.timeline-widget', title: 'Timeline', className: 'timeline-popped-out'}
};
const GEOMETRY_KEY = 'studio-popout-geometry';
// Each editor gets its own window names, so two editors never take over each other's popups.
const INSTANCE = Math.random().toString(36).slice(2);
const open = new Map(); // panel name -> {win, section, placeholder}

/** Find an element by id in the main page or in any popped-out panel. */
export function byId(id) {
  const found = document.getElementById(id);
  if (found) return found;
  for (const {win} of open.values()) { const node = win.document.getElementById(id); if (node) return node; }
  return null;
}

export const isPoppedOut = name => open.has(name);

function readGeometry(name) {
  try { return JSON.parse(localStorage.getItem(GEOMETRY_KEY) || '{}')[name] || null; } catch { return null; }
}

function saveGeometry(name, win) {
  try {
    const all = JSON.parse(localStorage.getItem(GEOMETRY_KEY) || '{}');
    all[name] = {left: win.screenX, top: win.screenY, width: win.outerWidth, height: win.outerHeight};
    localStorage.setItem(GEOMETRY_KEY, JSON.stringify(all));
  } catch { /* storage unavailable or window already gone */ }
}

function features(name) {
  const box = readGeometry(name) || {width: Math.min(1400, screen.availWidth), height: 480};
  return 'popup,' + Object.entries(box).filter(([, v]) => Number.isFinite(v)).map(([k, v]) => `${k}=${Math.round(v)}`).join(',');
}

/** Give the popup the studio's stylesheets and icons (once). */
function prepare(doc, spec) {
  doc.title = `${spec.title} · Trajectory Remix`;
  const have = new Set([...doc.querySelectorAll('link[rel=stylesheet]')].map(link => link.href));
  for (const link of document.querySelectorAll('link[rel=stylesheet]')) {
    if (have.has(link.href)) continue;
    const copy = doc.createElement('link'); copy.rel = 'stylesheet'; copy.href = link.href; doc.head.append(copy);
  }
  const sprite = document.querySelector('body > svg');
  if (sprite && !doc.querySelector('body > svg')) doc.body.prepend(doc.importNode(sprite, true));
  doc.getElementById('panel-popout-waiting')?.setAttribute('hidden', '');
}

/** Put the panel back in the editor. The popup stays open unless `closeWindow` is set. */
function dock(name, closeWindow) {
  const entry = open.get(name); if (!entry) return;
  open.delete(name);
  entry.placeholder.replaceWith(entry.section);
  document.getElementById('workspace')?.classList.remove(PANELS[name].className);
  sync(name);
  if (entry.win.closed) return;
  saveGeometry(name, entry.win);
  if (closeWindow) entry.win.close();
  else entry.win.document.getElementById('panel-popout-waiting')?.removeAttribute('hidden');
}

/** Called by the popup: move the panel into `win` if it is ours and the panel is free. */
function attach(name, win) {
  const spec = PANELS[name]; if (!spec || win.opener !== window) return 'busy';
  if (open.has(name)) return open.get(name).win === win ? 'attached' : 'busy';
  const section = document.querySelector(spec.selector);
  if (!section) return 'wait';
  prepare(win.document, spec);
  const placeholder = document.createComment(`${name} panel is in a popup window`);
  section.replaceWith(placeholder); win.document.body.append(section);
  open.set(name, {win, section, placeholder});
  document.getElementById('workspace')?.classList.add(spec.className);
  // Fires when the popup closes or reloads; after a reload it asks for the panel again.
  win.addEventListener('pagehide', () => dock(name, false));
  sync(name);
  return 'attached';
}

function undock(name) {
  const win = window.open(`/static/panel-popout.html?panel=${name}`, `trajectory-remix-${name}-${INSTANCE}`, features(name));
  if (!win) { alert('The browser blocked the popup window. Allow popups for this page and try again.'); return; }
  win.focus();
}

export function togglePopout(name) { if (open.has(name)) dock(name, true); else undock(name); }

function sync(name) {
  const out = open.has(name), title = PANELS[name].title.toLowerCase();
  const label = out ? `Return ${title} to the main window` : `Pop out ${title} into a new window`;
  const docs = [document, ...[...open.values()].map(entry => entry.win.document)];
  for (const button of docs.flatMap(doc => [...doc.querySelectorAll(`[data-popout="${name}"]`)])) {
    const text = button.querySelector('span');
    if (text) text.textContent = out ? `Return ${title} panel` : `Pop out ${title} panel`;
    button.setAttribute('aria-label', label); if (button.dataset.tip !== undefined) button.dataset.tip = label;
    button.setAttribute('aria-pressed', String(out));
  }
}

window.panelPopout = {attach};
for (const button of document.querySelectorAll('[data-popout]')) {
  button.addEventListener('click', () => togglePopout(button.dataset.popout));
}
for (const name of Object.keys(PANELS)) sync(name);
// The editor is reloading or closing: take the panel back so the popup is left empty. After a
// reload the popup asks the new page for it; if the editor closed, the popup closes itself.
window.addEventListener('pagehide', () => { for (const name of [...open.keys()]) dock(name, false); });
