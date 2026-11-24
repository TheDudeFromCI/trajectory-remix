/* Editor layout: draggable splitters, the collapsible trajectory header, and the
   timeline controls that move to the status bar while the timeline panel is hidden.
   Sizes and the collapsed state are remembered in this browser. */

const $ = id => document.getElementById(id);
const SIZES_KEY = 'studio-sizes';
const STEP = 16;

/** Each splitter resizes one panel: `sign` is +1 when dragging right/down grows it. `reserve` is
    the room kept for the centre; `beside` is the panel on the other side, whose width is kept too. */
const SPLITS = {
  dataset: {host: 'workspace', target: '#dataset-panel', prop: '--dataset-w', axis: 'x', sign: 1, min: 160, room: 'width', reserve: 360, beside: '#event-editor-panel'},
  event: {host: 'workspace', target: '#event-editor-panel', prop: '--event-w', axis: 'x', sign: -1, min: 280, room: 'width', reserve: 360, beside: '#dataset-panel'},
  timeline: {host: 'workspace', target: '.timeline-widget', prop: '--timeline-h', axis: 'y', sign: -1, min: 100, room: 'height', reserve: 140},
  jobs: {host: 'jobs-view', target: '.jobs-list-widget', prop: '--jobs-w', axis: 'x', sign: 1, min: 200, room: 'width', reserve: 360}
};

const read = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } };
const write = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ } };

let sizes = read(SIZES_KEY, {});
if (!sizes || typeof sizes !== 'object' || Array.isArray(sizes)) sizes = {};

function limits(spec) {
  const host = $(spec.host); if (!host) return {min: spec.min, max: spec.min};
  const space = spec.room === 'width' ? host.clientWidth : host.clientHeight;
  const beside = spec.beside ? document.querySelector(spec.beside)?.getBoundingClientRect().width || 0 : 0;
  return {min: spec.min, max: Math.max(spec.min, space - spec.reserve - beside)};
}
const measure = spec => { const box = document.querySelector(`#${spec.host} ${spec.target}`)?.getBoundingClientRect(); return box ? Math.round(spec.room === 'width' ? box.width : box.height) : spec.min; };

function describe(name, handle) {
  const spec = SPLITS[name], {min, max} = limits(spec), value = Math.min(max, Math.max(min, sizes[name] ?? measure(spec)));
  handle.setAttribute('aria-valuemin', String(min)); handle.setAttribute('aria-valuemax', String(max)); handle.setAttribute('aria-valuenow', String(value));
}

function setSize(name, value, handle) {
  const spec = SPLITS[name], {min, max} = limits(spec);
  sizes[name] = Math.round(Math.min(max, Math.max(min, value)));
  $(spec.host)?.style.setProperty(spec.prop, sizes[name] + 'px');
  describe(name, handle);
}

function resetSize(name, handle) {
  delete sizes[name]; $(SPLITS[name].host)?.style.removeProperty(SPLITS[name].prop);
  write(SIZES_KEY, sizes); requestAnimationFrame(() => describe(name, handle));
}

function bindSplitter(handle) {
  const name = handle.dataset.resize, spec = SPLITS[name]; if (!spec) return;
  if (sizes[name] != null) $(spec.host)?.style.setProperty(spec.prop, sizes[name] + 'px');
  describe(name, handle);
  handle.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    const origin = spec.axis === 'x' ? event.clientX : event.clientY, initial = measure(spec);
    handle.setPointerCapture(event.pointerId); handle.classList.add('dragging');
    document.body.classList.add('resizing'); document.body.style.cursor = spec.axis === 'x' ? 'col-resize' : 'row-resize';
    const move = e => setSize(name, initial + spec.sign * ((spec.axis === 'x' ? e.clientX : e.clientY) - origin), handle);
    const done = () => {
      handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', done); handle.removeEventListener('pointercancel', done);
      handle.classList.remove('dragging'); document.body.classList.remove('resizing'); document.body.style.cursor = '';
      write(SIZES_KEY, sizes);
    };
    handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', done); handle.addEventListener('pointercancel', done);
    event.preventDefault();
  });
  handle.addEventListener('dblclick', () => resetSize(name, handle));
  handle.addEventListener('keydown', event => {
    if (event.key === 'Home') { resetSize(name, handle); event.preventDefault(); return; }
    const direction = {x: {ArrowLeft: -1, ArrowRight: 1}, y: {ArrowUp: -1, ArrowDown: 1}}[spec.axis][event.key];
    if (!direction) return;
    setSize(name, measure(spec) + spec.sign * direction * STEP * (event.shiftKey ? 4 : 1), handle);
    write(SIZES_KEY, sizes); event.preventDefault();
  });
}
for (const handle of document.querySelectorAll('.splitter[data-resize]')) bindSplitter(handle);
addEventListener('resize', () => { for (const handle of document.querySelectorAll('.splitter[data-resize]')) describe(handle.dataset.resize, handle); });

// Timeline controls: with the timeline panel hidden they live in the status bar, and only while the Editor is showing.
const workspace = $('workspace'), slot = $('status-transport'), transport = document.querySelector('.timeline-controls .transport');
const home = transport?.parentElement, anchor = $('preview-status');
function syncTransport() {
  if (!workspace || !slot || !transport || !home) return;
  const inStatusBar = workspace.classList.contains('timeline-panel-hidden');
  if (inStatusBar && transport.parentElement !== slot) slot.append(transport);
  if (!inStatusBar && transport.parentElement !== home) home.insertBefore(transport, anchor);
  slot.hidden = !(inStatusBar && !workspace.hidden);
}
if (workspace) new MutationObserver(syncTransport).observe(workspace, {attributes: true, attributeFilter: ['class', 'hidden']});
syncTransport();
