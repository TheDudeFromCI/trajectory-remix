/* Real-browser layout: header placement, resizable panels, collapsible header, status-bar transport. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {chromium} = createRequire(import.meta.url)('playwright');
const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox']});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 900}}), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const open = async () => { await page.goto(process.argv[2]); await page.waitForFunction(() => globalThis.graphStudio?.settingsRecord); };
  const box = async selector => page.locator(selector).first().boundingBox();
  const drag = async (selector, dx, dy) => {
    const b = await box(selector), x = b.x + b.width / 2, y = b.y + b.height / 2;
    await page.mouse.move(x, y); await page.mouse.down(); await page.mouse.move(x + dx, y + dy, {steps: 6}); await page.mouse.up();
  };
  await open();

  // The trajectory header is the top of the event panel, and panel margins are tight.
  assert.equal(await page.evaluate(() => document.getElementById('event-editor-panel').firstElementChild.id), 'metadata-panel');
  const dataset = await box('#dataset-panel'), center = await box('.editor-center'), event = await box('#event-editor-panel');
  assert.ok(center.x - (dataset.x + dataset.width) <= 8.5 && event.x - (center.x + center.width) <= 8.5, 'panels are 8px apart');
  assert.ok(dataset.x <= 8.5, 'the view padding is 8px');

  // Dragging a splitter resizes a panel, and the size survives a reload; double-click resets.
  await drag('#split-dataset', 60, 0);
  assert.ok(Math.abs((await box('#dataset-panel')).width - (dataset.width + 60)) < 3);
  await drag('#split-event', -80, 0);
  assert.ok(Math.abs((await box('#event-editor-panel')).width - (event.width + 80)) < 3);
  const timeline = await box('.timeline-widget');
  await drag('#split-timeline', 0, -90);
  assert.ok(Math.abs((await box('.timeline-widget')).height - (timeline.height + 90)) < 3);
  await drag('#split-dataset', -2000, 0);
  assert.ok((await box('#dataset-panel')).width >= 159, 'a panel cannot shrink below its minimum');
  await drag('#split-dataset', 4000, 0);
  assert.ok((await box('.editor-center')).width >= 320, 'a panel cannot crowd out the centre');
  await page.locator('#split-dataset').dblclick(); await page.waitForTimeout(50);
  assert.ok(Math.abs((await box('#dataset-panel')).width - dataset.width) < 2, 'double-click restores the default width');
  await drag('#split-dataset', 40, 0);
  await open();
  assert.ok(Math.abs((await box('#dataset-panel')).width - (dataset.width + 40)) < 3, 'sizes are remembered');
  await page.locator('#split-dataset').focus(); await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#split-dataset').getAttribute('aria-valuenow'), String(Math.round(dataset.width + 56)));
  await page.keyboard.press('Home'); await page.waitForTimeout(50);
  assert.ok(Math.abs((await box('#dataset-panel')).width - dataset.width) < 2, 'Home resets');

  // Hidden panels take their splitters with them.
  await page.locator('#view-menu summary').click(); await page.locator('#toggle-dataset-panel').click();
  assert.equal(await page.locator('#split-dataset').isVisible(), false);
  await page.locator('#view-menu summary').click(); await page.locator('#show-all-panels').click();
  assert.equal(await page.locator('#split-dataset').isVisible(), true);

  // The trajectory header collapses to its title row and remembers that.
  assert.equal(await page.locator('#trajectory-meta').isVisible(), true);
  await page.locator('#header-toggle').click();
  assert.equal(await page.locator('#trajectory-meta').isVisible(), false);
  assert.equal(await page.locator('#header-toggle').getAttribute('aria-expanded'), 'false');
  await open();
  assert.equal(await page.locator('#trajectory-meta').isVisible(), false);
  await page.locator('#header-toggle').click();
  assert.equal(await page.locator('#trajectory-meta').isVisible(), true);

  // With the timeline hidden its media controls move to the status bar, only while the Editor shows.
  const inStatus = () => page.evaluate(() => document.getElementById('status-transport').contains(document.getElementById('play')));
  assert.equal(await inStatus(), false);
  await page.locator('#view-menu summary').click(); await page.locator('#toggle-timeline-panel').click();
  assert.equal(await inStatus(), true);
  const status = await box('.status-bar'), play = await box('#play');
  assert.ok(play && play.y >= status.y && play.y + play.height <= status.y + status.height + 1, 'controls sit inside the status bar');
  await page.locator('#jobs-button').click();
  assert.equal(await page.locator('#play').isVisible(), false);
  await page.locator('#trajectory-tab').click();
  assert.equal(await page.locator('#play').isVisible(), true);
  await page.locator('#view-menu summary').click(); await page.locator('#show-all-panels').click();
  assert.equal(await inStatus(), false);
  assert.equal(await page.locator('.timeline-controls #play').isVisible(), true);

  // Jobs list is resizable too.
  await page.locator('#jobs-button').click();
  const jobs = await box('.jobs-list-widget'); await drag('#split-jobs', 50, 0);
  assert.ok(Math.abs((await box('.jobs-list-widget')).width - (jobs.width + 50)) < 3);

  assert.deepEqual(errors, []);
  console.log('Layout: header in the event panel, splitters, collapsible header and status-bar transport passed.');
} finally { await browser.close(); }
