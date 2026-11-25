/* Auto-save: every change to the selected trajectory is saved straight away. Typing waits for a
   short pause so a sentence becomes one save (and one Undo step) instead of one per key. */
export const TYPING_DELAY = 400;

export class AutoSave {
  /** `pending()` says whether unsaved changes remain, `save()` saves them,
     `typing()` says whether the change came from a text field still being typed in. */
  constructor({pending, save, report, typing = () => false, delay = TYPING_DELAY}) {
    Object.assign(this, {pending, saveNow: save, report, typing, delay, timer: null, running: null, error: null});
  }
  /** `error` holds the last failed save until a save succeeds; the draft is kept and retried on the next change. */
  /** Call after every change. Saves now, or after a typing pause. */
  schedule() {
    clearTimeout(this.timer); this.timer = null;
    if (!this.pending()) return;
    if (this.typing()) this.timer = setTimeout(() => this.run(), this.delay);
    else queueMicrotask(() => this.run());
  }
  cancel() { clearTimeout(this.timer); this.timer = null; }
  /** Save anything still waiting (before switching trajectory or acting on the stored copy). */
  async flush() {
    this.cancel();
    if (this.running) await this.running.catch(() => {});
    if (this.pending()) await this.run();
  }
  async run() {
    this.cancel();
    if (this.running) { await this.running.catch(() => {}); return this.schedule(); }
    if (!this.pending()) return;
    this.running = this.saveNow();
    try { await this.running; this.error = null; } catch (error) { this.error = error; this.report(error); } finally { this.running = null; }
  }
}
