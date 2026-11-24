/* Working indicator: shown only while an assistant job owns the trajectory, and never duplicated. */
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window = new Window();
for (const key of ['document', 'Event', 'MutationObserver']) globalThis[key] = window[key];
const {isAssistantOwner, renderAssistantIndicator} = await import('../trajectory_remix/static/assistant-indicator.js');

const jobs = new Map([['a', {action: 'assistant'}], ['r', {action: 'review_repair'}]]);
assert.equal(isAssistantOwner('a', jobs), true);
assert.equal(isAssistantOwner('r', jobs), false);
assert.equal(isAssistantOwner(null, jobs), false);
assert.equal(isAssistantOwner('missing', jobs), false);

const slot = document.createElement('span'); document.body.append(slot);
renderAssistantIndicator(slot, false);
assert.equal(slot.querySelector('#assistant-working'), null);
renderAssistantIndicator(slot, true); renderAssistantIndicator(slot, true);
const el = slot.querySelectorAll('#assistant-working');
assert.equal(el.length, 1);
assert.equal(el[0].hidden, false);
assert.match(el[0].textContent, /Working\.\.\./);
assert.equal(el[0].querySelectorAll('.working-dots span').length, 3);
renderAssistantIndicator(slot, false);
assert.equal(slot.querySelector('#assistant-working').hidden, true);
console.log('assistant indicator ok');
