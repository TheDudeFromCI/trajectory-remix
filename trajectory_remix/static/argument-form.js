/* Tool argument form: one typed field per argument, driven by the tool's JSON input schema.
   Strings get a text box, numbers a number box, booleans and enums a dropdown, and objects,
   arrays or untyped values a small JSON box. Arguments not in the schema are listed too. */
import {node} from './ui-kit.js';

const UNSET = '';

/** The single JSON type a property uses, ignoring "null" in a union such as ["string", "null"]. */
export function fieldType(schema = {}) {
  if (Array.isArray(schema.enum) && schema.enum.length) return 'enum';
  const types = [schema.type].flat().filter(t => t && t !== 'null');
  return types.length === 1 && ['string', 'integer', 'number', 'boolean'].includes(types[0]) ? types[0] : 'json';
}

/** Parse event content as an argument object; blank content is an empty object. Returns null otherwise. */
export function parseArguments(text) {
  if (!text.trim()) return {};
  try { const value = JSON.parse(text); return value && typeof value === 'object' && !Array.isArray(value) ? value : null; }
  catch { return null; }
}

function dropdown(choices, current, optional) {
  const select = node('select');
  const listed = current === undefined || choices.some(v => JSON.stringify(v) === JSON.stringify(current));
  // A saved value outside the schema's choices stays selectable, so opening the form never changes it.
  const values = listed ? choices : [...choices, current];
  const entries = [...(optional ? [[UNSET, 'Not set']] : []), ...values.map(v => [JSON.stringify(v), String(v)])];
  for (const [value, label] of entries) { const o = node('option', label); o.value = value; select.append(o); }
  select.value = current === undefined ? UNSET : JSON.stringify(current);
  return select;
}

/** Build the control for one argument and a reader that turns its value back into JSON (undefined = unset). */
function control(type, schema, value, optional) {
  if (type === 'enum' || type === 'boolean') {
    const select = dropdown(type === 'enum' ? schema.enum : [true, false], value, optional || value === undefined);
    return [select, () => select.value === UNSET ? undefined : JSON.parse(select.value), 'change'];
  }
  if (type === 'integer' || type === 'number') {
    const input = node('input'); input.type = 'number'; input.step = type === 'integer' ? '1' : 'any';
    if (schema.minimum !== undefined) input.min = schema.minimum;
    if (schema.maximum !== undefined) input.max = schema.maximum;
    input.value = value === undefined ? '' : String(value);
    return [input, () => input.value === '' ? undefined : Number(input.value), 'input'];
  }
  if (type === 'string') {
    const box = node('textarea'); box.rows = 1; box.spellcheck = false; box.value = value === undefined ? '' : String(value);
    if (schema.default !== undefined) box.placeholder = String(schema.default);
    return [box, () => box.value === '' && optional ? undefined : box.value, 'input'];
  }
  const box = node('textarea', undefined, 'mono'); box.rows = 2; box.spellcheck = false;
  box.value = value === undefined ? '' : JSON.stringify(value, null, 2);
  box.placeholder = 'JSON value';
  return [box, () => box.value.trim() === '' ? undefined : JSON.parse(box.value), 'change'];
}

function fieldLabel(key, schema, type, required, known) {
  const head = node('span', undefined, 'argument-head');
  head.append(node('span', key, 'argument-name'));
  if (required) head.append(node('span', 'required', 'argument-required'));
  head.append(node('span', known ? (type === 'json' ? [schema.type].flat().filter(Boolean).join(' | ') || 'any' : type) : 'not in schema', 'argument-type muted'));
  return head;
}

/**
 * Render the form into `holder`.
 * @param {HTMLElement} holder
 * @param {{args: object, schema?: object, disabled?: boolean, onChange: (args: object) => void, report: (msg: string) => void}} options
 *   `onChange` receives the updated argument object after each edit; `report` shows a problem.
 */
export function renderArgumentForm(holder, {args, schema, disabled = false, onChange, report}) {
  const properties = schema?.properties || {}, required = new Set(schema?.required || []);
  const keys = [...new Set([...Object.keys(properties), ...Object.keys(args)])];
  const fields = keys.map(key => {
    const known = key in properties, spec = properties[key] || {}, type = known ? fieldType(spec) : 'json';
    const [input, read, event] = control(type, spec, args[key], !required.has(key));
    input.disabled = disabled; input.setAttribute('aria-label', 'Argument ' + key); input.dataset.argument = key;
    input.addEventListener(event, () => {
      let value;
      try { value = read(); } catch { report(`Argument "${key}" needs valid JSON; the previous value is kept.`); return; }
      if (value === undefined) delete args[key]; else args[key] = value;
      onChange(args);
    });
    const label = node('label', undefined, 'argument-field');
    label.append(fieldLabel(key, spec, type, required.has(key), known), input);
    if (spec.description) label.append(node('span', spec.description, 'argument-description muted'));
    return label;
  });
  if (!fields.length) fields.push(node('p', 'This tool takes no arguments. Use Raw JSON to add any.', 'empty'));
  holder.replaceChildren(...fields);
}
