/* Endpoint provider options (OpenRouter-style): no server, endpoint or model. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window(); globalThis.document = window.document;
const {renderProjectSettings, parseExtraBody} = await import('../trajectory_remix/static/project-settings.js');
const root = document.createElement('div'); document.body.append(root);
let draft = {endpoints: {openrouter: {url: 'https://openrouter.ai/api/v1', model: 'vendor/model', concurrency: 1, retries: 2, context_window_tokens: 32768, max_output_tokens: 2048}}, endpoint_roles: {generator: 'openrouter'}, tools: {}};
renderProjectSettings(root, draft, {getSettings: () => draft, onChange: value => { draft = value; }});
const byLabel = name => [...root.querySelectorAll('[aria-label]')].find(n => n.getAttribute('aria-label') === name);
const enter = (n, value) => { n.value = value; n.dispatchEvent(new window.Event('input')); };
const check = (n, value) => { n.checked = value; n.dispatchEvent(new window.Event('change')); };

// Untouched options add nothing to the endpoint; the section starts closed.
assert.equal(root.querySelector('details.settings-subsection').open, false);
assert.ok(!('bytes_per_token' in draft.endpoints.openrouter));
assert.equal(byLabel('Endpoint openrouter use Responses API').checked, false);

enter(byLabel('Endpoint openrouter bytes_per_token'), '3');
assert.equal(draft.endpoints.openrouter.bytes_per_token, 3);
const sticky = byLabel('Endpoint openrouter sticky sessions'); sticky.checked = true; sticky.dispatchEvent(new window.Event('change'));
assert.equal(draft.endpoints.openrouter.sticky_sessions, true);

// Drafts are plain text, so there is no structured-output (JSON mode) choice.
assert.equal(byLabel('Endpoint openrouter structured output'), undefined);
const extra = byLabel('Endpoint openrouter extra request fields');
enter(extra, '{"reasoning": {"effort": "none"}, "provider": {"require_parameters": true}}');
assert.deepEqual(draft.endpoints.openrouter.extra_body, {reasoning: {effort: 'none'}, provider: {require_parameters: true}});
// Invalid or reserved input is reported and never written over the last good value.
enter(extra, '{"model": "other"}');
assert.equal(extra.getAttribute('aria-invalid'), 'true');
assert.match(root.querySelector('details.settings-subsection [role=alert]').textContent, /cannot be overridden: model/);
enter(extra, '{oops');
assert.match(root.querySelector('details.settings-subsection [role=alert]').textContent, /JSON object/);
assert.deepEqual(draft.endpoints.openrouter.extra_body.reasoning, {effort: 'none'});
assert.deepEqual(parseExtraBody('   '), [{}, '']);
assert.equal(parseExtraBody('[1]')[1], 'Extra request fields must be a JSON object.');

// Re-rendering a configured endpoint opens the section so the options are visible.
renderProjectSettings(root, draft, {getSettings: () => draft, onChange: value => { draft = value; }});
assert.equal(root.querySelector('details.settings-subsection').open, true);
const responses = byLabel('Endpoint openrouter use Responses API');
const configuredSticky = byLabel('Endpoint openrouter sticky sessions');
const configuredExtra = byLabel('Endpoint openrouter extra request fields');
check(responses, true);
assert.equal(draft.endpoints.openrouter.use_responses_api, true);
assert.equal(configuredSticky.disabled, true);
assert.equal(configuredExtra.getAttribute('aria-invalid'), 'true');
enter(configuredExtra, '{"prompt_cache_key": "project"}');
assert.deepEqual(draft.endpoints.openrouter.extra_body, {prompt_cache_key: 'project'});
check(responses, false);
assert.equal(draft.endpoints.openrouter.use_responses_api, false);
assert.equal(configuredSticky.disabled, false);
assert.equal(configuredSticky.checked, true);
console.log('Provider options: API toggle, bytes per token, sticky sessions and validated extra request fields passed.');
window.happyDOM.abort();
