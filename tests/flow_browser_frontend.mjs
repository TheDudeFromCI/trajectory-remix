/* Flow tab in a real browser with a fake Generator: walkthrough to the end, then edit and save. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {chromium} = createRequire(import.meta.url)('playwright');
const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox']});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 950}}), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('dialog', dialog => dialog.accept());
  const get = path => page.evaluate(async url => (await fetch(url, {headers: {'X-Remix-Token': document.querySelector('meta[name=remix-token]').content}})).json(), '/api/v1' + path);
  await page.goto(process.argv[2]);
  await page.locator('#flow-tab').click();
  await page.locator('[data-node="set_scene"]').waitFor();
  assert.ok(await page.locator('.flow-node').count() >= 15, 'the default flow has distinct phases and writers');
  // The chart opens zoomed to fit the canvas.
  assert.ok(await page.evaluate(() => flowView.canvas.scale) < 1);
  // Cards sit inside the canvas and do not overlap.
  const boxes = await page.locator('.flow-node').evaluateAll(nodes => nodes.map(n => n.getBoundingClientRect().toJSON()));
  for (const [i, a] of boxes.entries()) for (const b of boxes.slice(i + 1)) assert.ok(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top, 'cards overlap');

  // A layout saved before choose_tools existed: write_opening remembered where choose_tools
  // now goes. Reopening must not stack the new card on the old one.
  await page.evaluate(() => {
    const stale = structuredClone(flowView.positions);
    stale.write_opening = stale.choose_tools; delete stale.choose_tools;
    localStorage.setItem('flow-layout', JSON.stringify(stale));
  });
  await page.reload(); await page.locator('#flow-tab').click(); await page.locator('[data-node="choose_tools"]').waitFor();
  const reboxes = await page.locator('.flow-node').evaluateAll(nodes => nodes.map(n => n.getBoundingClientRect().toJSON()));
  for (const [i, a] of reboxes.entries()) for (const b of reboxes.slice(i + 1)) assert.ok(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top, 'cards overlap after a stale layout');
  assert.match(await page.locator('#flow-status').textContent(), /arranged again/);

  // The wheel zooms around the pointer: the card under it stays put.
  const canvas = page.locator('#flow-canvas'), card = page.locator('[data-node="work"]');
  await card.scrollIntoViewIfNeeded();
  const cardBefore = await card.boundingBox(), scaleBefore = await page.evaluate(() => flowView.canvas.scale);
  const at = {x: cardBefore.x + cardBefore.width / 2, y: cardBefore.y + cardBefore.height / 2};
  await page.mouse.move(at.x, at.y);
  await page.mouse.wheel(0, -400);
  await page.waitForFunction(old => flowView.canvas.scale > old, scaleBefore);
  const cardAfter = await card.boundingBox();
  assert.ok(cardAfter.width > cardBefore.width * 1.3, 'zoomed in');
  assert.ok(at.x > cardAfter.x && at.x < cardAfter.x + cardAfter.width && at.y > cardAfter.y && at.y < cardAfter.y + cardAfter.height, 'the pointed-at card stays under the pointer');
  assert.match(await page.locator('#flow-zoom-reset').textContent(), /^\d+%$/);
  assert.notEqual(await page.locator('#flow-zoom-reset').textContent(), `${Math.round(scaleBefore * 100)}%`);
  // Dragging empty canvas pans; dragging a card still moves the card.
  const box = await canvas.boundingBox();
  const scroll = () => page.evaluate(() => [flowView.canvas.root.scrollLeft, flowView.canvas.root.scrollTop]);
  const empty = await page.evaluate(() => {
    const root = flowView.canvas.root.getBoundingClientRect();
    for (let y = root.top + 10; y < root.bottom - 10; y += 15) for (let x = root.left + 10; x < root.right - 10; x += 15) {
      const hit = document.elementFromPoint(x, y);
      if (hit && !hit.closest('.flow-node') && flowView.canvas.root.contains(hit)) return {x, y};
    }
    return null;
  });
  assert.ok(empty, 'found empty canvas');
  const [left0, top0] = await scroll();
  await page.mouse.move(empty.x, empty.y); await page.mouse.down();
  await page.mouse.move(empty.x - 120, empty.y - 90, {steps: 6}); await page.mouse.up();
  const [left1, top1] = await scroll();
  assert.ok(left1 > left0 || top1 > top0, 'background drag pans the view');
  assert.ok(box.width > 0);
  await page.locator('#flow-zoom-fit').click();

  // Walk the default flow to the end with the fake Generator.
  await page.locator('[data-mode="walk"]').click();
  await page.locator('#walk-seed').fill('11');
  await page.locator('#walk-start').click();
  await page.locator('#walk-step:not([disabled])').waitFor();
  assert.match(await page.locator('#walk-detail').textContent(), /private authoring controller/);
  await page.locator('#walk-run').click();
  await page.waitForFunction(() => flowView.walk.latest?.state.done, null, {timeout: 20000});
  assert.equal(await page.evaluate(() => flowView.walk.latest.state.outcome), 'finished');
  assert.ok(await page.locator('.flow-node.is-current[data-node="done"]').count());
  assert.ok(await page.locator('.flow-step-chip').count() > 5);
  await page.locator('.flow-step-chip').nth(1).click();
  await page.locator('[data-tab="reply"]').click();
  assert.match(await page.locator('#walk-detail').textContent(), /accepted/);
  assert.equal(await page.locator('.flow-edge.is-active').count(), 1, 'the viewed step lights its link');
  const before = (await get('/trajectories')).total;
  await page.locator('#walk-save').click();
  await page.waitForFunction(() => /Saved trajectory/.test(document.querySelector('#flow-status').textContent));
  const after = (await get('/trajectories')).total;
  assert.equal(after, before + 1, 'Save trajectory stores exactly one source');

  // Edit: change the event instruction, save, and confirm the server has it.
  await page.locator('[data-mode="edit"]').click();
  await page.locator('[data-node="write_thought"]').click();
  await page.locator('#flow-tab-body textarea').fill('Write it plainly.');
  await page.locator('#flow-save:not([disabled])').click();
  await page.waitForFunction(() => document.querySelector('#flow-dirty').textContent === 'Saved');
  const saved = await get('/generation/flow');
  assert.equal(saved.origin, 'project'); assert.equal(saved.flow.nodes.write_thought.instruction, 'Write it plainly.');
  // Settings → Tools: import a catalogue file; same IDs are kept unless replacement is ticked.
  const catalogue = new URL('../examples/kidra_tools_core.json', import.meta.url).pathname;
  const toolsBefore = Object.keys((await get('/settings')).settings.tools);
  await page.locator('#settings-button').click();
  await page.locator('#settings-tab-tools').click();
  await page.locator('#tool-schema-editor input[type=file]').setInputFiles(catalogue);
  await page.locator('#tool-schema-editor .tool-import + p').filter({hasText: 'Save settings to apply'}).waitFor();
  const note = await page.locator('#tool-schema-editor .tool-import + p').textContent();
  assert.match(note, /Added 1[56]: subagent/);
  if (toolsBefore.includes('read')) assert.ok(!/Kept your existing.*subagent/.test(note));
  await page.locator('#settings-save').click();
  await page.waitForFunction(async () => (await (await fetch('/api/v1/settings', {headers: {'X-Remix-Token': document.querySelector('meta[name=remix-token]').content}})).json()).settings.tools.subagent);
  const savedTools = (await get('/settings')).settings.tools;
  assert.ok(savedTools.subagent.input_schema.properties.agent && savedTools.chat, 'imported tools saved; existing ones kept');
  await page.waitForFunction(() => !document.getElementById('settings-dialog').open);

  // The panel and canvas fit the compact layout without horizontal page overflow.
  await page.setViewportSize({width: 860, height: 900});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  if (process.env.UPDATE_SCREENSHOTS) await page.screenshot({path: process.env.UPDATE_SCREENSHOTS + '/flow-compact.png'});
  assert.deepEqual(errors, []);
  console.log('Flow tab: walkthrough to finish, step history, save trajectory and project flow edit passed.');
} finally { await browser.close(); }
