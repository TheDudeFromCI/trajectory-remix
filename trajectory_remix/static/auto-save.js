/* Auto-save: saves the selected trajectory shortly after the last edit, when the preference is on. */
export const AUTO_SAVE_DELAY = 1500;

export class AutoSave {
  /** `enabled()` reads the preference, `pending()` says whether unsaved edits remain, `save()` saves them. */
  constructor({enabled, pending, save, report, delay = AUTO_SAVE_DELAY}) {
    Object.assign(this, {enabled, pending, saveNow: save, report, delay, timer: null, running: false});
  }
  /** Call after every edit; restarts the countdown. Does nothing while the preference is off. */
  schedule() {
    clearTimeout(this.timer); this.timer = null;
    if (this.enabled() && this.pending()) this.timer = setTimeout(() => this.run(), this.delay);
  }
  cancel() { clearTimeout(this.timer); this.timer = null; }
  async run() {
    this.timer = null;
    if (this.running) return this.schedule();
    if (!this.enabled() || !this.pending()) return;
    this.running = true;
    try { await this.saveNow(); } catch (error) { this.report(error); } finally { this.running = false; }
  }
}
