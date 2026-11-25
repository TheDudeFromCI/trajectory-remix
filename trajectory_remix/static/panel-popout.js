/* Pop-out panels: move a live editor panel into a second browser window (for a second monitor).
   The panel's elements move as they are, so their listeners and the studio state keep working;
   closing the popup puts the panel back. Lookups by id go through `byId`, which also searches
   the popup. */

const PANELS = {
  timeline: {selector: '.timeline-widget', title: 'Timeline', className: 'timeline-popped-out'}
};
const GEOMETRY_KEY = 'studio-popout-geometry';
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
  const g = readGeometry(name);
  const box = g ? g : {width: Math.min(1400, screen.availWidth), height: 480};
  return 'popup,' + Object.entries(box).filter(([, v]) => Number.isFinite(v)).map(([k, v]) => `${k}=${Math.round(v)}`).join(',');
}

/** Give the empty popup the studio's stylesheets, icons and theme. */
function prepare(doc, spec) {
  doc.head.replaceChildren(); doc.title = `${spec.title} · Trajectory Remix`;
  for (const link of document.querySelectorAll('link[rel=stylesheet]')) {
    const copy = doc.createElement('link'); copy.rel = 'stylesheet'; copy.href = link.href; doc.head.append(copy);
  }
  const sprite = document.querySelector('body > svg');
  doc.body.className = 'panel-popout-window';
  doc.body.replaceChildren(...(sprite ? [doc.importNode(sprite, true)] : []));
}

function dock(name) {
  const entry = open.get(name); if (!entry) return;
  open.delete(name);
  entry.placeholder.replaceWith(entry.section);
  document.getElementById('workspace')?.classList.remove(PANELS[name].className);
  sync(name);
  if (!entry.win.closed) { saveGeometry(name, entry.win); entry.win.close(); }
}

function undock(name) {
  const spec = PANELS[name], section = document.querySelector(spec.selector);
  if (!section) return;
  const win = window.open('', `trajectory-remix-${name}`, features(name));
  if (!win) { alert('The browser blocked the popup window. Allow popups for this page and try again.'); return; }
  prepare(win.document, spec);
  const placeholder = document.createComment(`${name} panel is in a popup window`);
  section.replaceWith(placeholder); win.document.body.append(section);
  open.set(name, {win, section, placeholder});
  document.getElementById('workspace')?.classList.add(spec.className);
  win.addEventListener('pagehide', () => dock(name));
  win.addEventListener('beforeunload', () => saveGeometry(name, win));
  sync(name); win.focus();
}

export function togglePopout(name) { if (open.has(name)) dock(name); else undock(name); }

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

for (const button of document.querySelectorAll('[data-popout]')) {
  button.addEventListener('click', () => togglePopout(button.dataset.popout));
}
for (const name of Object.keys(PANELS)) sync(name);
window.addEventListener('pagehide', () => { for (const name of [...open.keys()]) dock(name); });
