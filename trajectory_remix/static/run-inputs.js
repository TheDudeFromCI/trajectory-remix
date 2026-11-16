/* Attempts/seed inputs shared by the Generation dialog and the Sampler workbench, kept in localStorage. */
const KEY = 'generation-run-inputs';
const DEFAULTS = {attempts: 100, seed: 0};

export function loadRunInputs() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { saved = {}; }
  const pick = (v, fallback) => (Number.isSafeInteger(v) ? v : fallback);
  return {attempts: pick(saved.attempts, DEFAULTS.attempts), seed: pick(saved.seed, DEFAULTS.seed)};
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
