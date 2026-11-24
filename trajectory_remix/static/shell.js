/* Application shell: view tabs, disclosure menus, tab panels and tooltips. */
import {bindViewTabs} from './views.js';

bindViewTabs();

// Menus: one open at a time, close on outside click, item click or Escape.
const menus = () => [...document.querySelectorAll('details.menu')];
document.addEventListener('toggle', event => {
  const menu = event.target;
  if (menu.matches?.('details.menu') && menu.open) for (const other of menus()) if (other !== menu) other.open = false;
}, true);
document.addEventListener('click', event => {
  const inside = event.target.closest('details.menu');
  for (const menu of menus()) if (menu !== inside || event.target.closest('.menu-list button')) menu.open = false;
});
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  const open = menus().find(menu => menu.open);
  if (open) { open.open = false; open.querySelector('summary').focus(); }
});

// Tabs: any [role=tablist] whose tabs name their panel with aria-controls.
function selectTab(tab) {
  const list = tab.closest('[role=tablist]');
  for (const other of list.querySelectorAll('[role=tab]')) {
    const selected = other === tab;
    other.setAttribute('aria-selected', String(selected));
    other.tabIndex = selected ? 0 : -1;
    const panel = document.getElementById(other.getAttribute('aria-controls'));
    if (panel) panel.hidden = !selected;
  }
}
for (const list of document.querySelectorAll('.tab-list[role=tablist]')) {
  const tabs = [...list.querySelectorAll('[role=tab]')];
  for (const tab of tabs) {
    tab.tabIndex = tab.getAttribute('aria-selected') === 'true' ? 0 : -1;
    tab.addEventListener('click', () => selectTab(tab));
    tab.addEventListener('keydown', event => {
      const step = {ArrowRight: 1, ArrowLeft: -1}[event.key];
      if (!step) return;
      const next = tabs[(tabs.indexOf(tab) + step + tabs.length) % tabs.length];
      selectTab(next); next.focus(); event.preventDefault();
    });
  }
}

// View menu: show or hide editor panels; the layout is remembered in this browser.
const PANELS = {dataset: 'dataset panel', timeline: 'timeline panel', editor: 'event panel'};
const workspace = document.getElementById('workspace');
let hidden = {};
try { hidden = JSON.parse(localStorage.getItem('studio-layout') || '{}'); } catch { hidden = {}; }
function applyLayout() {
  for (const [panel, name] of Object.entries(PANELS)) {
    workspace?.classList.toggle(`${panel}-panel-hidden`, !!hidden[panel]);
    const item = document.querySelector(`[data-panel="${panel}"] span`);
    if (item) item.textContent = `${hidden[panel] ? 'Show' : 'Hide'} ${name}`;
  }
  try { localStorage.setItem('studio-layout', JSON.stringify(hidden)); } catch { /* storage unavailable */ }
}
for (const button of document.querySelectorAll('[data-panel]')) {
  button.addEventListener('click', () => { hidden[button.dataset.panel] = !hidden[button.dataset.panel]; applyLayout(); });
}
document.getElementById('show-all-panels')?.addEventListener('click', () => { hidden = {}; applyLayout(); });
applyLayout();

// Expanded window: step controls drive the main timeline; Escape shrinks it.
for (const step of document.querySelectorAll('#window-popout [data-step]')) {
  step.addEventListener('click', () => document.getElementById(step.dataset.step)?.click());
}
document.addEventListener('keydown', event => {
  const popout = document.getElementById('window-popout');
  if (event.key === 'Escape' && popout && !popout.hidden && !document.querySelector('dialog[open], details.menu[open]')) popout.querySelector('.window-shrink')?.click();
});

// Tooltips: [data-tip] shows one floating bubble after a short delay, on hover or focus.
const bubble = document.getElementById('tooltip');
let timer, owner;
function place(target) {
  const box = target.getBoundingClientRect(), tip = bubble.getBoundingClientRect();
  let top = box.bottom + 6;
  if (top + tip.height > innerHeight - 4) top = box.top - tip.height - 6;
  const left = Math.min(Math.max(4, box.left + box.width / 2 - tip.width / 2), innerWidth - tip.width - 4);
  bubble.style.top = top + 'px'; bubble.style.left = left + 'px';
}
function show(target) {
  clearTimeout(timer); owner = target;
  timer = setTimeout(() => {
    if (owner !== target || !target.isConnected) return;
    // A modal dialog draws above the page, so the bubble must live inside it to be seen.
    const host = target.closest('dialog[open]') || document.body;
    if (bubble.parentElement !== host) host.append(bubble);
    bubble.textContent = target.dataset.tip; bubble.classList.add('show'); place(target);
    target.setAttribute('aria-describedby', 'tooltip');
  }, 250);
}
function hide() {
  clearTimeout(timer); bubble?.classList.remove('show');
  owner?.removeAttribute('aria-describedby'); owner = null;
}
if (bubble) {
  document.addEventListener('pointerover', event => { const t = event.target.closest('[data-tip]'); if (t && t !== owner) show(t); else if (!t && owner) hide(); });
  document.addEventListener('focusin', event => { const t = event.target.closest('[data-tip]'); if (t) show(t); });
  document.addEventListener('focusout', hide);
  document.addEventListener('pointerdown', hide);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') hide(); });
}
