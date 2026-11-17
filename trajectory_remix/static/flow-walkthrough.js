/* Walkthrough: sample one proposal, then step the Flow and inspect each transition.
   The browser keeps every state; the server stores nothing. */
import {node, tag} from './ui-kit.js';

const pretty = value => JSON.stringify(value, null, 2);
const pre = (text, cls = '') => node('pre', text, 'flow-pre ' + cls);
const disclosure = (title, body, open = false) => {
  const d = node('details', undefined, 'collapsible'); d.open = open;
  const inner = node('div', undefined, 'collapsible-body'); inner.append(body);
  d.append(node('summary', title), inner); return d;
};
const empty = text => node('p', text, 'empty');

/** Copy text to the clipboard; falls back to a hidden textarea where the API is missing. */
export async function copyText(text) {
  if (globalThis.navigator?.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; }
  const area = document.createElement('textarea'); area.value = text; area.setAttribute('readonly', '');
  area.style.position = 'fixed'; area.style.opacity = '0'; document.body.append(area); area.select();
  try { document.execCommand?.('copy'); } finally { area.remove(); }
}

/** A small button that copies getText() and confirms with "Copied". */
export function copyButton(label, getText, title = 'Copy to the clipboard') {
  const button = node('button', label, 'ghost sm flow-copy'); button.type = 'button'; button.dataset.tip = title;
  button.onclick = async () => {
    try { await copyText(getText()); button.textContent = 'Copied'; }
    catch { button.textContent = 'Copy failed'; }
    setTimeout(() => { button.textContent = label; }, 1500);
  };
  return button;
}

/** Messages as plain text, each headed by its role: easy to paste into notes or an issue. */
export function messagesText(messages) {
  return (messages || []).map(m => `### ${m.role}\n${m.content}`).join('\n\n');
}
const ACTIONS = {model: 'Model call', branch: 'Branch', chat_echo: 'Chat delivery', end: 'End', limit: 'Limit', done: 'Done', blocked: 'Blocked'};

/** Skeleton reply for typing one by hand, shaped like the request's output schema. */
export function replyTemplate(request) {
  if (!request) return '';
  if (request.operation === 'flow_select') {
    return pretty({finished: false, reason: 'Why this event comes next', windows: {}, task: {id: 'event-id', envelope: {kind: 'agent_text', window: 'window-id', after: []}, instruction: '', world: {}}});
  }
  return pretty(request.schema?.required?.includes('arguments') ? {arguments: {}} : {content: ''});
}

/** Message content is often canonical JSON; show it formatted unless exact text is wanted. */
export function messageBody(content, exact) {
  if (!exact) {
    try { return pre(pretty(JSON.parse(content))); } catch { /* not pure JSON */ }
    // JSON mode puts the schema sentence before the data, in the same message.
    const at = content.indexOf('\n\n{');
    if (at >= 0) {
      try { return pre(content.slice(0, at) + '\n\n' + pretty(JSON.parse(content.slice(at + 2)))); } catch { /* plain text */ }
    }
  }
  return pre(content);
}

export function renderRequest(request, exact) {
  if (!request) return empty('This step does not call a model.');
  const box = node('div', undefined, 'stack');
  const meta = node('div', undefined, 'row wrap');
  meta.append(tag(request.operation, 'sky'));
  if (request.endpoint) meta.append(tag('endpoint ' + request.endpoint, 'accent', 'mono'));
  else meta.append(tag('No Generator endpoint configured', 'danger'));
  if (request.wire) meta.append(tag(request.wire.model, 'muted', 'mono'));
  if (request.budget) {
    const b = request.budget, estimate = b.input_tokens_estimate ?? b.input_bytes;
    const budget = node('span', `~${estimate} / ${b.input_ceiling} input tokens`, 'small muted num' + (estimate > b.input_ceiling ? ' error' : ''));
    budget.dataset.tip = `${b.input_bytes} bytes at ${b.bytes_per_token ?? 1} bytes per token (the endpoint's estimate). A run pauses before sending a request over the allowance.`;
    meta.append(budget);
  }
  box.append(meta);
  const messages = request.wire?.messages || request.messages;
  messages.forEach((m, i) => {
    const block = node('section', undefined, `flow-message role-${m.role}`);
    const appended = request.wire && i >= request.messages.length;
    const head = node('div', undefined, 'row flow-message-head');
    head.append(node('span', m.role + (appended ? ' · added for JSON mode' : ''), 'flow-message-role small grow'), copyButton('Copy', () => m.content, 'Copy this message exactly as sent'));
    block.append(head, messageBody(m.content, exact));
    box.append(block);
  });
  box.append(disclosure('Output schema', pre(pretty(request.schema))));
  if (request.task) box.append(disclosure('Selected task (from the last Select)', pre(pretty(request.task))));
  if (request.wire) box.append(disclosure('HTTP request body (no credentials)', pre(pretty(request.wire))));
  return box;
}

export function renderReply(trace) {
  if (!trace) return empty('Not sent yet. Run the next step to see the reply.');
  const box = node('div', undefined, 'stack');
  const meta = node('div', undefined, 'row wrap');
  meta.append(tag(trace.accepted ? 'accepted' : 'rejected', trace.accepted ? 'success' : 'danger'));
  const r = trace.response || {};
  if (r.model) meta.append(tag(r.model, 'muted', 'mono'));
  if (r.usage) meta.append(node('span', Object.entries(r.usage).filter(([, v]) => typeof v === 'number').map(([k, v]) => `${k.replace('_tokens', '')} ${v}`).join(' · '), 'small muted num'));
  box.append(meta);
  if (trace.error) box.append(node('p', trace.error, 'flow-error'));
  if (r.context_limit) box.append(node('p', 'The request was over the context budget, so nothing was sent.', 'note'));
  if (r.invalid_output) {
    const cut = r.finish_reason === 'length';
    box.append(node('p', cut ? 'The reply was cut off at Max output tokens. If the text below is the model thinking, turn thinking off in the endpoint\'s extra request fields; otherwise raise Max output tokens.' : 'The reply was not a single JSON object.', 'note'));
    if (r.raw_reasoning) box.append(disclosure('Reasoning text (start)', pre(r.raw_reasoning), true));
    if (r.raw_content) box.append(disclosure('Reply text (start)', pre(r.raw_content), true));
    if (cut && !r.raw_reasoning && !r.raw_content) box.append(node('p', 'The server returned no text, so the whole limit was spent before any answer.', 'small muted'));
  }
  if ('output' in r) box.append(node('h4', 'Reply'), pre(pretty(r.output)));
  if (trace.selection) box.append(disclosure('Accepted Selection', pre(pretty(trace.selection)), true));
  if (trace.event) box.append(disclosure('Event added', pre(pretty(trace.event)), true));
  if (!trace.response && !trace.error) box.append(node('p', trace.summary || 'No model call.', 'muted'));
  return box;
}

export function renderKnows(knows) {
  if (!knows) return empty('Start a walkthrough first.');
  const box = node('div', undefined, 'stack');
  const grid = node('dl', undefined, 'flow-facts');
  const fact = (k, v) => { grid.append(node('dt', k), node('dd', v)); };
  fact('Position', knows.node); fact('Steps', `${knows.steps} of ${knows.max_steps}`); fact('Events', `${knows.events} of ${knows.max_events}`);
  fact('Format errors', `${knows.format_errors} (a run allows ${knows.format_retries} corrections)`);
  fact('Windows', knows.windows.join(', ') || 'none yet');
  if (knows.chat_echo_pending) fact('Chat to deliver', knows.chat_echo_pending);
  box.append(grid);
  if (knows.correction) box.append(node('h4', 'Correction sent with the next request'), node('p', knows.correction, 'flow-error'));
  box.append(disclosure('Last Selection (what branches read)', pre(knows.last_selection ? pretty(knows.last_selection) : 'none yet'), true));
  box.append(disclosure('Task to write next', pre(knows.pending_task ? pretty(knows.pending_task) : 'none'), !!knows.pending_task));
  if (knows.diagnostics.length) box.append(disclosure(`Trajectory problems (${knows.diagnostics.length})`, pre(knows.diagnostics.map(d => `${d.severity} ${d.code}: ${d.message}`).join('\n'))));
  return box;
}

export function renderTrajectory(source, added = []) {
  const box = node('div', undefined, 'stack');
  const events = Object.entries(source.events);
  if (!events.length) box.append(empty('No events yet.'));
  const list = node('ol', undefined, 'flow-events');
  for (const [id, e] of events) {
    const item = node('li', undefined, added.includes(id) ? 'is-new' : '');
    const head = node('div', undefined, 'row wrap');
    head.append(node('code', id), tag(e.kind, 'muted'), node('span', 'in ' + e.window, 'small muted'));
    if (e.after?.length) head.append(node('span', 'after ' + e.after.join(', '), 'small faint'));
    const text = typeof e.content === 'string' ? e.content : JSON.stringify(e.content);
    item.append(head, node('p', text.length > 400 ? text.slice(0, 399) + '…' : text, 'small'));
    list.append(item);
  }
  box.append(list, disclosure('Source JSON', pre(pretty(source))));
  return box;
}

export class WalkthroughPanel {
  constructor(root, strip, {api, flow, pins = () => ({}), onTrail = () => {}, report = () => {}}) {
    Object.assign(this, {root, strip, api, flow, pins, onTrail, report});
    this.entries = []; this.view = 0; this.tab = 'prompt'; this.exact = false; this.busy = false; this.stopped = false;
    this.mount();
  }

  mount() {
    const $ = s => this.root.querySelector(s);
    this.root.innerHTML = `<section class="flow-walk-start"><div class="row wrap"><label class="inline-label">Seed <input id="walk-seed" type="number" step="1" value="0" title="0 picks a random seed"></label><button id="walk-start" class="primary sm" type="button">Sample &amp; start</button></div><p id="walk-pins" class="small muted"></p></section>
      <section class="flow-walk-controls" hidden><div class="row wrap"><select id="walk-mode" aria-label="How to get replies"><option value="call">Call the Generator</option><option value="manual">Type replies myself</option></select><button id="walk-step" class="primary sm" type="button">Run next step</button><button id="walk-run" class="sm" type="button">Run to end</button><button id="walk-stop" class="sm danger" type="button" hidden>Stop</button></div>
      <div class="row wrap"><label class="checkbox small" data-tip="Each step uses the latest saved project settings, so raised limits or a changed endpoint apply straight away. Off: the settings the walkthrough started with, as a real run would."><input id="walk-latest" type="checkbox" checked> Use current settings</label><button id="walk-apply" class="ghost sm" type="button" data-tip="Reload the saved settings into the next step's preview">Apply saved settings</button></div>
      <div class="row wrap"><button id="walk-prev" class="ghost icon sm" type="button" aria-label="Previous step">‹</button><span id="walk-position" class="num small"></span><button id="walk-next" class="ghost icon sm" type="button" aria-label="Next step">›</button><span class="grow"></span><button id="walk-rewind" class="ghost sm" type="button">Rewind to here</button><button id="walk-save" class="ghost sm" type="button">Save trajectory</button></div></section>
      <div id="walk-summary" class="flow-walk-summary" role="status" aria-live="polite"></div>
      <div class="tab-list flow-walk-tabs" role="tablist" aria-label="Step details"></div><div id="walk-detail" class="flow-walk-detail"></div>`;
    this.el = {seed: $('#walk-seed'), start: $('#walk-start'), pinsNote: $('#walk-pins'), controls: $('.flow-walk-controls'), mode: $('#walk-mode'), step: $('#walk-step'), run: $('#walk-run'), stop: $('#walk-stop'), latest: $('#walk-latest'), apply: $('#walk-apply'), prev: $('#walk-prev'), next: $('#walk-next'), position: $('#walk-position'), rewind: $('#walk-rewind'), save: $('#walk-save'), summary: $('#walk-summary'), tabs: $('.flow-walk-tabs'), detail: $('#walk-detail')};
    const guard = fn => async () => { try { await fn(); } catch (error) { this.report(error); } };
    this.el.start.onclick = guard(() => this.start());
    this.el.step.onclick = guard(() => this.advance());
    this.el.run.onclick = guard(() => this.runToEnd());
    this.el.stop.onclick = guard(() => this.stop());
    this.el.apply.onclick = guard(() => this.applySettings());
    this.el.prev.onclick = () => this.show(this.view - 1);
    this.el.next.onclick = () => this.show(this.view + 1);
    this.el.rewind.onclick = () => { this.entries = this.entries.slice(0, this.view + 1); this.show(this.view); };
    this.el.save.onclick = guard(() => this.saveTrajectory());
    this.el.mode.onchange = () => this.render();
    for (const [id, text] of [['prompt', 'Prompt'], ['reply', 'Reply'], ['knows', 'Flow knows'], ['trajectory', 'Trajectory'], ['proposal', 'Proposal']]) {
      const tab = node('button', text); tab.type = 'button'; tab.setAttribute('role', 'tab'); tab.dataset.tab = id;
      tab.onclick = () => { this.tab = id; this.render(); };
      this.el.tabs.append(tab);
    }
    this.renderPins(); this.render();
  }

  renderPins() {
    const pins = Object.entries(this.pins() || {});
    this.el.pinsNote.textContent = pins.length ? 'Sampler pins: ' + pins.map(([k, v]) => `${k} = ${v}`).join(', ') : 'No Sampler pins; the saved matrix weights decide. Uses the chart as drawn, including unsaved edits.';
  }

  get latest() { return this.entries[this.entries.length - 1]; }
  get current() { return this.entries[this.view]; }

  async start() {
    let seed = Number(this.el.seed.value);
    if (!Number.isSafeInteger(seed)) throw new Error('Seed must be a whole number.');
    if (seed === 0) seed = Math.floor(Math.random() * 2 ** 31) + 1;
    this.el.seed.value = seed; this.renderPins();
    this.busy = true; this.render();
    try {
      const started = await this.api('/walkthrough/start', 'POST', {seed, overrides: {...(this.pins() || {})}, flow: this.flow()});
      this.entries = [{state: started.state, next: started.next, trace: null}];
      this.view = 0; this.tab = 'prompt';
      this.report(`Sampled seed ${seed}. Nothing is saved unless you choose Save trajectory.`);
    } finally { this.busy = false; this.render(); }
  }

  async advance() {
    if (!this.latest || this.latest.state.done) return null;
    const manual = this.el.mode.value === 'manual' && this.latest.next.action === 'model';
    let response = null;
    if (manual) {
      const box = this.root.querySelector('#walk-reply');
      try { response = JSON.parse(box?.value ?? ''); } catch { throw new Error('The typed reply is not valid JSON.'); }
    }
    this.busy = true; this.render();
    try {
      this.callId = 'c' + Math.random().toString(36).slice(2, 12);
      const result = await this.api('/walkthrough/step', 'POST', {state: this.latest.state, mode: manual ? 'manual' : 'call', response, latest_settings: this.el.latest.checked, call_id: this.callId});
      this.entries.push({state: result.state, next: result.next, trace: result.trace});
      if (result.trace.accepted) this.draftReply = undefined;
      this.view = this.entries.length - 1;
      if (this.tab === 'prompt' && result.trace.action === 'model') this.tab = 'reply';
      return result.trace;
    } finally { this.busy = false; this.render(); }
  }

  /** Stop a run to the end, and cancel the request waiting on the model if there is one. */
  async stop() {
    this.stopped = true;
    if (this.busy && this.callId) await this.api('/walkthrough/cancel', 'POST', {call_id: this.callId});
  }

  /** Re-read the saved settings into the newest position, e.g. after raising a limit. */
  async applySettings() {
    const entry = this.latest; if (!entry || entry.state.done) return;
    const next = await this.api('/walkthrough/describe?latest_settings=true', 'POST', entry.state);
    entry.state = {...entry.state, settings_revision: next.settings_revision};
    entry.next = next; this.view = this.entries.length - 1;
    this.report('Saved settings applied to the next step.'); this.render();
  }

  async runToEnd() {
    if (this.el.mode.value === 'manual') throw new Error('Run to end calls the Generator; switch from typed replies first.');
    this.stopped = false; this.running = true; this.render();
    try {
      for (let i = 0; i < 1000 && !this.stopped && this.latest && !this.latest.state.done; i++) {
        const trace = await this.advance();
        // A failed request (no reply) would fail the same way again; stop and show it.
        if (!trace || (trace.error && !trace.response)) break;
      }
    } finally { this.running = false; this.render(); }
  }

  async saveTrajectory() {
    const source = this.current?.state.source;
    if (!source) return;
    await this.api('/trajectories', 'POST', source);
    this.report(`Saved trajectory ${source.id.slice(0, 8)} to the dataset at its current stage.`);
    document.dispatchEvent(new CustomEvent('graph-walkthrough-saved', {detail: source.id}));
  }

  show(index) { this.view = Math.max(0, Math.min(this.entries.length - 1, index)); this.render(); }

  /** Canvas highlight for the viewed entry: position, visit counts and last transition. */
  trail() {
    if (!this.current) return null;
    const visits = {};
    for (const entry of this.entries.slice(1, this.view + 1)) if (entry.trace.action !== 'chat_echo') visits[entry.trace.node] = (visits[entry.trace.node] || 0) + 1;
    const t = this.current.trace;
    let edge = null;
    if (t && t.accepted && (t.action === 'model' || t.action === 'branch')) {
      edge = {from: t.node, port: t.action === 'branch' ? (t.matched ? 'then' : 'otherwise') : 'next'};
    }
    return {current: this.current.next.node, visits, edge};
  }

  render() {
    const has = this.entries.length > 0, entry = this.current, latest = entry === this.latest;
    this.el.controls.hidden = !has;
    this.el.start.disabled = this.busy;
    const done = !has || this.latest.state.done;
    this.el.step.disabled = this.busy || done || !latest;
    this.el.run.disabled = this.busy || done || !latest || this.running;
    this.el.run.hidden = !!this.running; this.el.stop.hidden = !(this.running || this.busy);
    this.el.apply.disabled = this.busy || done || !latest || !this.el.latest.checked;
    this.el.prev.disabled = this.view <= 0; this.el.next.disabled = !has || latest;
    this.el.rewind.disabled = !has || latest || this.busy;
    this.el.save.disabled = !has;
    this.el.position.textContent = has ? `Step ${this.view} of ${this.entries.length - 1}` : '';
    for (const tab of this.el.tabs.children) { const on = tab.dataset.tab === this.tab; tab.setAttribute('aria-selected', String(on)); tab.tabIndex = on ? 0 : -1; }
    this.renderSummary(entry, latest);
    this.renderDetail(entry, latest);
    this.renderStrip();
    this.onTrail(this.trail());
  }

  renderSummary(entry, latest) {
    const box = this.el.summary;
    if (!entry) { box.replaceChildren(node('p', 'Sample a proposal to walk through the flow one step at a time. Each step shows the exact prompt, the reply and what the flow knows afterwards.', 'muted')); return; }
    const parts = [];
    if (entry.trace) {
      const t = entry.trace, line = node('div', undefined, 'row wrap');
      line.append(tag(ACTIONS[t.action] || t.action, t.accepted ? 'mint' : 'danger'), node('code', t.node), node('span', '→ ' + t.to, 'muted'), node('span', t.summary || t.error || '', t.summary || !t.error ? 'small' : 'small error'));
      if (t.settings_revision) { const rev = tag('settings r' + t.settings_revision, 'faint', 'mono'); rev.dataset.tip = 'The saved settings revision this step used.'; line.append(rev); }
      parts.push(line);
    }
    const n = entry.next, upcoming = node('div', undefined, 'row wrap flow-walk-next');
    upcoming.append(node('span', latest ? 'Up next' : 'Then', 'small faint'), tag(ACTIONS[n.action] || n.action, n.action === 'done' ? 'success' : 'sky'), node('code', n.node), node('span', n.summary, 'small'));
    parts.push(upcoming);
    if (this.busy) parts.push(node('p', this.el.mode.value === 'call' ? 'Waiting for the Generator…' : 'Working…', 'small muted'));
    box.replaceChildren(...parts);
  }

  /** What the Copy button copies on each tab. */
  copyable(entry) {
    const t = entry.trace, n = entry.next;
    if (this.tab === 'prompt') { const request = t ? t.request : n.request; return request ? messagesText(request.wire?.messages || request.messages) : ''; }
    if (this.tab === 'reply') return t ? pretty({accepted: t.accepted, error: t.error, response: t.response, selection: t.selection, event: t.event}) : '';
    if (this.tab === 'knows') return pretty(t ? t.knows : n.knows);
    if (this.tab === 'trajectory') return pretty(entry.state.source);
    return pretty(entry.state.plan.proposal);
  }

  renderDetail(entry, latest) {
    const box = this.el.detail;
    if (!entry) { box.replaceChildren(); return; }
    const t = entry.trace, n = entry.next;
    const pendingModel = latest && n.action === 'model';
    const tools = node('div', undefined, 'row flow-copy-row');
    tools.append(node('span', '', 'grow'), copyButton('Copy', () => this.copyable(entry), 'Copy this tab'));
    if (this.tab === 'prompt') {
      const request = t ? t.request : n.request;
      if (request?.wire) tools.append(copyButton('Copy JSON', () => pretty(request.wire), 'Copy the exact HTTP request body (no credentials)'));
    }
    if (this.tab === 'prompt') {
      // A step shows what it sent; the newest position shows what will be sent next.
      const request = t ? t.request : n.request;
      const exact = node('label', undefined, 'checkbox small'); const box2 = node('input'); box2.type = 'checkbox'; box2.checked = this.exact;
      box2.onchange = () => { this.exact = box2.checked; this.render(); }; exact.append(box2, ' Show exact text sent');
      const head = node('p', t ? 'What this step sent.' : 'What the next step will send.', 'small muted');
      box.replaceChildren(tools, head, exact, renderRequest(request, this.exact));
      if (t && pendingModel) box.append(disclosure('Next request', renderRequest(n.request, this.exact)));
    } else if (this.tab === 'reply') {
      box.replaceChildren(tools, renderReply(t));
      if (pendingModel && this.el.mode.value === 'manual') {
        const area = node('textarea', undefined, 'flow-reply mono'); area.id = 'walk-reply'; area.spellcheck = false;
        area.value = this.draftReply ?? replyTemplate(n.request); area.setAttribute('aria-label', 'Reply to use for the next step');
        area.oninput = () => { this.draftReply = area.value; };
        const reset = node('button', 'Reset to template', 'ghost sm'); reset.type = 'button'; reset.onclick = () => { this.draftReply = undefined; this.render(); };
        box.append(node('h4', 'Type the next reply'), node('p', 'Checked exactly like a model reply, then Run next step.', 'small muted'), area, reset);
      }
    } else if (this.tab === 'knows') {
      box.replaceChildren(tools, node('p', t ? 'After this step:' : 'Before the first step:', 'small muted'), renderKnows(t ? t.knows : n.knows));
    } else if (this.tab === 'trajectory') {
      box.replaceChildren(tools, renderTrajectory(entry.state.source, t?.added_events || []));
    } else {
      const proposal = entry.state.plan.proposal;
      box.replaceChildren(tools, node('p', 'The sampled proposal the selector reads. Actors never see it.', 'small muted'), pre(pretty(proposal)));
    }
  }

  renderStrip() {
    const chips = this.entries.map((entry, i) => {
      const chip = node('button', undefined, 'flow-step-chip' + (i === this.view ? ' is-viewed' : '') + (entry.trace && !entry.trace.accepted ? ' is-rejected' : ''));
      chip.type = 'button';
      const name = !i ? 'start' : entry.trace.action === 'chat_echo' ? 'chat delivery' : entry.trace.node;
      chip.append(node('span', String(i), 'num'), node('span', name));
      chip.setAttribute('aria-label', i ? `Step ${i}: ${name}${entry.trace.accepted ? '' : ', rejected'}` : 'Start');
      chip.onclick = () => this.show(i);
      return chip;
    });
    this.strip.replaceChildren(...chips);
    this.strip.hidden = !chips.length;
    chips[this.view]?.scrollIntoView?.({block: 'nearest', inline: 'nearest'});
  }
}
