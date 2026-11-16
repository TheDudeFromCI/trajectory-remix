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
  await page.locator('[data-node="choose"]').waitFor();
  assert.equal(await page.locator('.flow-node').count(), 4);
  // Cards sit inside the canvas and do not overlap.
  const boxes = await page.locator('.flow-node').evaluateAll(nodes => nodes.map(n => n.getBoundingClientRect().toJSON()));
  for (const [i, a] of boxes.entries()) for (const b of boxes.slice(i + 1)) assert.ok(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top, 'cards overlap');

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
  await page.locator('[data-node="write"]').click();
  await page.locator('#flow-tab-body textarea').fill('Write it plainly.');
  await page.locator('#flow-save:not([disabled])').click();
  await page.waitForFunction(() => document.querySelector('#flow-dirty').textContent === 'Saved');
  const saved = await get('/generation/flow');
  assert.equal(saved.origin, 'project'); assert.equal(saved.flow.nodes.write.instruction, 'Write it plainly.');
  // The panel and canvas fit the compact layout without horizontal page overflow.
  await page.setViewportSize({width: 860, height: 900});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  if (process.env.UPDATE_SCREENSHOTS) await page.screenshot({path: process.env.UPDATE_SCREENSHOTS + '/flow-compact.png'});
  assert.deepEqual(errors, []);
  console.log('Flow tab: walkthrough to finish, step history, save trajectory and flow.yaml edit passed.');
} finally { await browser.close(); }
