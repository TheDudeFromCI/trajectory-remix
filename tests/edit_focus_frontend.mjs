/* Real-browser checks for event edits during auto-save, polling and delayed listing requests. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {chromium} = createRequire(import.meta.url)('playwright');
const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox']});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 900}}), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(process.argv[2]); await page.waitForFunction(() => globalThis.graphStudio?.settingsRecord);

  // A tool with a string argument, a tools window and an event that calls it.
  const id = await page.evaluate(async () => {
    const call = async (path, method, body) => {
      const response = await fetch('/api/v1' + path, {method, body: JSON.stringify(body),
        headers: {'X-Remix-Token': document.querySelector('meta[name=remix-token]').content, 'Content-Type': 'application/json'}});
      if (!response.ok) throw new Error(path + ': ' + await response.text());
      return response.json();
    };
    const current = await call('/settings', 'GET');
    current.settings.tools.focus_search = {name: 'Search', input_schema: {type: 'object', properties: {query: {type: 'string'}}, required: ['query']}};
    await call('/settings', 'PUT', {expected_revision: current.revision, settings: current.settings});
    const made = await call('/trajectories', 'POST', {format: 'kidra-trajectory-graph/1', id: crypto.randomUUID(),
      windows: {tools: {purpose: 'tools', agent_writable: false}, doc: {purpose: 'document', agent_writable: true}},
      events: {
        tools: {kind: 'tool_schema', window: 'tools', content: ''},
        call: {kind: 'tool_call', window: 'doc', tool: 'focus_search', call_id: 'one', content: '{"query": ""}', after: ['tools']},
      }});
    return made.source.id;
  });
  await page.evaluate(async id => { const ui = globalThis.graphStudio; await ui.loadSettings(); await ui.listing(); await ui.select(id); ui.selectEvent('call'); }, id);
  await page.selectOption('#content-mode', 'form');
  const field = page.locator('[data-argument="query"]');
  await field.click();
  await page.evaluate(() => {
    globalThis.focusLost = 0;
    document.addEventListener('focusout', e => { if (e.target.dataset?.argument) globalThis.focusLost++; }, true);
  });

  // Type in bursts with pauses long enough for an auto-save and then a poll (every 1.5 s) to land.
  let typed = '';
  for (const word of ['alpha ', 'beta ', 'gamma']) {
    await page.keyboard.type(word, {delay: 30}); typed += word;
    await page.waitForFunction(text => !globalThis.graphStudio.autoSave.running && globalThis.graphStudio.record.source.events.call.content.includes(text), typed.trim());
    await page.waitForTimeout(1800);
  }
  assert.equal(await page.evaluate(() => globalThis.focusLost), 0, 'the argument field kept focus through saves and polls');
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.argument), 'query');
  assert.equal(await field.inputValue(), typed, 'every keystroke landed');

  const before = await page.evaluate(() => graphStudio.draft.events.call.content);
  await page.evaluate(() => graphStudio.selectEvent('tools'));
  const checkbox = page.getByRole('checkbox', {name: 'List tool focus_search', exact: true});
  const listingPath = '**/api/v1/tools/listing';
  let unblock;
  const delay = new Promise(resolve => { unblock = resolve; });
  await page.route(listingPath, async route => { await delay; await route.continue(); });
  const requested = page.waitForRequest(listingPath);
  await checkbox.check();
  assert.equal((await requested).postDataJSON().event_id, 'tools');
  await page.evaluate(() => graphStudio.selectEvent('call'));
  const responded = page.waitForResponse(listingPath);
  unblock();
  assert.equal(await (await responded).finished(), null);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.deepEqual(await page.evaluate(() => ({
    call: graphStudio.draft.events.call.content,
    tools: graphStudio.draft.events.tools.content,
    unsaved: graphStudio.hasUnsavedChanges(),
  })), {call: before, tools: '', unsaved: false});
  await page.unroute(listingPath);

  await page.evaluate(() => graphStudio.selectEvent('tools'));
  await checkbox.check();
  await page.waitForFunction(() => graphStudio.record.source.events.tools.content.includes('"name":"focus_search"'));
  if (process.env.TOOL_PICKER_SCREENSHOT) {
    await page.locator('#event-editor-panel').screenshot({path: process.env.TOOL_PICKER_SCREENSHOT});
  }
  assert.deepEqual(errors, []);
  console.log('Event editing: typing survives auto-save and polling, and late tool listings cannot edit another event.');
} finally {
  await browser.close();
}
