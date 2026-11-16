/* Attempts/seed inputs shared by the Generation dialog and the Sampler workbench, kept in localStorage. */
const KEY = 'generation-run-inputs';
const DEFAULTS = {attempts: 100, seed: 0};

/** Saved inputs; `defaults` overrides the fallback for fields that were never saved. */
export function loadRunInputs(defaults = {}) {
  const fallback = {...DEFAULTS, ...defaults};
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { saved = {}; }
  const pick = (v, fallback) => (Number.isSafeInteger(v) ? v : fallback);
  return {attempts: pick(saved.attempts, fallback.attempts), seed: pick(saved.seed, fallback.seed)};
}

/** Merge a partial update into the stored inputs. */
export function saveRunInputs(patch) {
  const next = {...loadRunInputs(), ...patch};
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* storage unavailable */ }
}

/** Persist an input's integer value on every edit (blank or invalid values are not stored). */
export function persistInput(input, name) {
  input.addEventListener('input', () => {
    const v = Number(input.value);
    if (input.value !== '' && Number.isSafeInteger(v)) saveRunInputs({[name]: v});
  });
}

/** Seed 0 means "pick a random starting seed". */
export const resolveSeed = seed => (seed === 0 ? 1 + Math.floor(Math.random() * 2 ** 31) : seed);
