/* Mark a form control as broken (red outline via the [aria-invalid] style) or clear the mark. */

/** Set or clear the invalid state on a control; returns `broken` for convenient chaining. */
export function markInvalid(control, broken) {
  if (broken) control.setAttribute('aria-invalid', 'true'); else control.removeAttribute('aria-invalid');
  return !!broken;
}

/** True when a required argument is omitted: unset, or an empty string for a text field. */
export function isMissing(value) { return value === undefined || value === ''; }
