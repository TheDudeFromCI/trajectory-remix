/* Status shown at the foot of the Comments panel while the Assistant's job owns the selected
   trajectory: "Working... (1m 13s)", or "Running `tool`... (43s)" for two seconds after each
   tool call. The counter is the job's total running time. */
import {node} from './ui-kit.js';

export const TOOL_HOLD_MS = 2000;
const TOOL_LINE = /Assistant tool: (\w+)/;

/** True when the trajectory is owned by an assistant job (summaries carry `action`). */
export const isAssistantOwner = (owner, jobs) => !!owner && jobs.get(owner)?.action === 'assistant';

/** The last tool name announced in a page of log items, or null. */
export const lastToolCall = items => {
  for (let i = items.length - 1; i >= 0; i--) { const m = TOOL_LINE.exec(items[i].text); if (m) return m[1]; }
  return null;
};

/** 43 → "43s", 73 → "1m 13s", 3725 → "1h 2m 5s". */
export function formatElapsed(seconds) {
  const s = Math.max(0, Math.floor(seconds)), h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
  return [h && `${h}h`, (h || m) && `${m}m`, `${s % 60}s`].filter(Boolean).join(' ');
}

export class AssistantStatus {
  constructor({host, api, now = () => performance.now(), setTimer = (fn, ms) => setTimeout(fn, ms),
    clearTimer = id => clearTimeout(id), setTick = (fn, ms) => setInterval(fn, ms), clearTick = id => clearInterval(id)}) {
    Object.assign(this, {host, api, now, setTimer, clearTimer, setTick, clearTick});
    this.job = null; this.cursor = 0; this.tool = null; this.timer = null; this.ticker = null; this.startedAt = 0;
    host.setAttribute('role', 'status');
    this.label = node('span', undefined, 'working-label');
    host.append(node('span', undefined, 'spinner'), this.label);
    this.render();
  }
  /** Call on every poll with the owning assistant job id (or null) and how long it has run, in seconds. */
  async update(job, age = 0) {
    if (job !== this.job) { this.reset(); this.job = job; if (job) await this.begin(job, age); }
    if (job) await this.readTools(job);
    this.host.hidden = !job; this.render();
  }
  reset() {
    this.clearTimer(this.timer); this.clearTick(this.ticker);
    this.timer = this.ticker = null; this.tool = null; this.cursor = 0; this.job = null;
  }
  /** Start the clock, and skip tool calls that happened before we started watching. */
  async begin(job, age) {
    this.startedAt = this.now() - age * 1000;
    this.ticker = this.setTick(() => this.render(), 1000);
    this.cursor = (await this.api(`/jobs/${job}/logs?after=0&limit=1`)).last;
  }
  async readTools(job) {
    for (;;) {
      const page = await this.api(`/jobs/${job}/logs?after=${this.cursor}&limit=100`);
      if (job !== this.job) return;
      this.cursor = page.items.at(-1)?.sequence ?? this.cursor;
      const tool = lastToolCall(page.items);
      if (tool) this.showTool(tool);
      if (page.items.length < 100) return;
    }
  }
  /** A newer call overwrites the text and restarts the two-second hold. */
  showTool(name) {
    this.tool = name; this.clearTimer(this.timer);
    this.timer = this.setTimer(() => { this.tool = null; this.render(); }, TOOL_HOLD_MS);
  }
  render() {
    const dots = node('span', undefined, 'working-dots');
    dots.setAttribute('aria-hidden', 'true');
    dots.append(...[1, 2, 3].map(() => node('span', '.')));
    const time = node('span', this.job ? `(${formatElapsed((this.now() - this.startedAt) / 1000)})` : '', 'working-time');
    this.label.replaceChildren(...(this.tool ? ['Running ', node('code', this.tool)] : ['Working']), dots, ' ', time);
  }
}
