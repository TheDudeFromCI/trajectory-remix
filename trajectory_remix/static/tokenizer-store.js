/* Keeps imported tokenizer definitions out of the editable settings draft.
 *
 * A Hugging Face tokenizer JSON is often several megabytes (Qwen-style files
 * carry ~150k merges). The settings dialog round-trips its draft through the
 * Advanced JSON textarea on every edit, so a full definition there makes each
 * change parse, clone and pretty-print tens of megabytes and freezes the page.
 * The draft instead holds a small stub naming a definition kept in memory here,
 * and the real definition is put back just before saving. */
export const STUB_KEY = '$stored_tokenizer';
const stored = new Map();
const keys = new WeakMap();

const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
export const isStub = value => isObject(value) && typeof value[STUB_KEY] === 'string';

/** Content key: equal definitions get the same stub, so dirty checks stay exact. */
function keyOf(definition) {
  if (keys.has(definition)) return keys.get(definition);
  const text = JSON.stringify(definition);
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 0x01000193);
  const key = `${text.length} chars · ${(hash >>> 0).toString(16).padStart(8, '0')}`;
  keys.set(definition, key);
  return key;
}

/** Store a definition and return its stub; stubs and non-objects pass through. */
export function parkDefinition(definition) {
  if (!isObject(definition) || isStub(definition)) return definition;
  const key = keyOf(definition);
  stored.set(key, definition);
  return {[STUB_KEY]: key};
}

/** The stored definition for a stub, or the value itself when it is not a stub. */
export function resolveDefinition(definition) {
  return isStub(definition) ? stored.get(definition[STUB_KEY]) : definition;
}

function mapProfiles(settings, change) {
  if (!isObject(settings) || !isObject(settings.tokenizer_profiles)) return settings;
  const profiles = Object.fromEntries(Object.entries(settings.tokenizer_profiles).map(([id, profile]) =>
    [id, isObject(profile) ? {...profile, definition: change(profile.definition, id)} : profile]));
  return {...settings, tokenizer_profiles: profiles};
}

/** A shallow copy of settings with every tokenizer definition replaced by its stub. */
export const parkSettings = settings => mapProfiles(settings, parkDefinition);

/** A shallow copy of settings with every stub replaced by its stored definition. */
export const restoreSettings = settings => mapProfiles(settings, (definition, id) => {
  const value = resolveDefinition(definition);
  if (value === undefined) throw new Error(`Tokenizer profile ${id} refers to a tokenizer that is no longer loaded. Re-import its JSON file.`);
  return value;
});
