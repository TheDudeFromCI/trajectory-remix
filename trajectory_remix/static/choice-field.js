/* A dropdown with a "Custom…" choice. Picking Custom shows a text box below the
   dropdown for any other value; the full list of choices always stays available. */

const CUSTOM = '\u0000custom';

export class ChoiceField {
  /**
   * @param {HTMLSelectElement} select the dropdown
   * @param {HTMLInputElement} input the custom text box, shown only while Custom is picked
   * @param {{empty?: string, customLabel?: string, onChange?: (value: string) => void}} options
   *   `empty` labels a blank choice (for example "Model default"); omit it for no blank choice.
   */
  constructor(select, input, {empty, customLabel = 'Custom…', onChange} = {}) {
    this.select = select; this.input = input; this.empty = empty; this.customLabel = customLabel; this.onChange = onChange;
    this.choices = [];
    input.hidden = true;
    select.addEventListener('change', () => {
      const custom = select.value === CUSTOM;
      input.hidden = !custom;
      if (custom) input.focus?.();
      this.onChange?.(this.value);
    });
    input.addEventListener('input', () => this.onChange?.(this.value));
    this.setChoices([]);
  }
  /** Replace the listed choices, keeping the current value (custom if it is no longer listed). */
  setChoices(values) {
    const current = this.select.options.length ? this.value : '';
    this.choices = [...new Set(values.map(String))];
    const option = (value, label) => { const n = document.createElement('option'); n.value = value; n.textContent = label; return n; };
    this.select.replaceChildren(...(this.empty === undefined ? [] : [option('', this.empty)]),
      ...this.choices.map(v => option(v, v)), option(CUSTOM, this.customLabel));
    this.value = current;
  }
  get custom() { return this.select.value === CUSTOM; }
  get value() { return this.custom ? this.input.value.trim() : this.select.value; }
  set value(value) {
    const text = value == null ? '' : String(value);
    const listed = this.choices.includes(text) || (text === '' && this.empty !== undefined);
    if (listed) { this.select.value = text; this.input.value = ''; }
    else if (text === '' && !this.choices.length) { this.select.value = CUSTOM; this.input.value = ''; }
    else if (text === '') { this.select.value = this.choices[0]; this.input.value = ''; }
    else { this.select.value = CUSTOM; this.input.value = text; }
    this.input.hidden = !this.custom;
  }
  set disabled(disabled) { this.select.disabled = disabled; this.input.disabled = disabled; }
}
