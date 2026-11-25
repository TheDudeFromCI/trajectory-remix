/* Real-browser check: the timeline pops out into a second window, keeps driving the editor, and docks back. */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const {chromium} = createRequire(import.meta.url)('playwright');
const example = JSON.parse(readFileSync(new URL('../examples/graph_minimal.json', import.meta.url)));
const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox']});
try {
  const context = await browser.newContext({viewport: {width: 1440, height: 900}});
  const page = await context.newPage(), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(process.argv[2]);
  await page.waitForFunction(() => globalThis.graphStudio?.settingsRecord);
  await page.evaluate(async source => {
    const token = document.querySelector('meta[name=remix-token]').content;
    const response = await fetch('/api/v1/trajectories', {method: 'POST', headers: {'X-Remix-Token': token, 'Content-Type': 'application/json'}, body: JSON.stringify(source)});
    if (!response.ok) throw new Error(await response.text());
    await globalThis.graphStudio.select(source.id);
  }, example);
  await page.locator('.event-node').first().waitFor();

  const popOut = async from => {
    const [win] = await Promise.all([context.waitForEvent('page'), from.locator('#popout-timeline').click()]);
    await win.locator('#timeline .event-node').first().waitFor(); return win;
  };

  // Popping out moves the live timeline into a new window and gives its room to the Windows panel.
  const windows = await page.locator('.windows-widget').boundingBox();
  const [popup] = await Promise.all([context.waitForEvent('page'), page.locator('#popout-timeline').click()]);
  await popup.locator('#timeline .event-node').first().waitFor();
  assert.equal(await page.locator('.timeline-widget').count(), 0, 'the timeline left the main window');
  assert.equal(await page.locator('#split-timeline').isVisible(), false);
  assert.ok((await page.locator('.windows-widget').boundingBox()).height > windows.height + 100, 'the Windows panel grows');
  assert.equal(await page.locator('#status-transport').isHidden(), true, 'controls stay with the popped-out timeline');
  assert.equal(await popup.locator('#popout-timeline').getAttribute('aria-pressed'), 'true');
  await page.locator('#view-menu summary').click();
  assert.equal(await page.locator('#popout-timeline-menu span').textContent(), 'Return timeline panel');
  await page.locator('#view-menu summary').click();
  const box = await popup.locator('.timeline-widget').boundingBox(), viewport = popup.viewportSize();
  assert.ok(box.width > viewport.width - 40 && box.height > viewport.height - 40, 'the panel fills the popup');
  assert.notEqual(await popup.locator('#popout-timeline svg use').evaluate(u => u.getBoundingClientRect().width), 0, 'icons render');

  // The popup still drives the editor: selecting an event and stepping the position.
  await popup.locator('.event-node', {hasText: 'think'}).click();
  assert.equal(await page.evaluate(() => graphStudio.event), 'think');
  await popup.locator('#first-tick').click();
  await popup.locator('#next-tick').click();
  assert.equal(await page.evaluate(() => graphStudio.position), 1);
  assert.equal(await popup.locator('#tick-number').inputValue(), '1');
  assert.ok(await popup.locator('.event-node.selected').count() === 1, 'the timeline re-renders in the popup');

  // Closing the popup docks the timeline back where it was.
  await popup.close();
  await page.locator('.timeline-widget #timeline .event-node').first().waitFor();
  assert.equal(await page.locator('#split-timeline').isVisible(), true);
  assert.equal(await page.evaluate(() => document.querySelector('.editor-center').lastElementChild.matches('.timeline-widget')), true);
  assert.equal(await page.locator('#popout-timeline').getAttribute('aria-pressed'), 'false');

  // The View menu pops it out and returns it too.
  await page.locator('#view-menu summary').click();
  const [again] = await Promise.all([context.waitForEvent('page'), page.locator('#popout-timeline-menu').click()]);
  await again.locator('#timeline').waitFor();
  await page.locator('#view-menu summary').click(); await page.locator('#popout-timeline-menu').click();
  await page.locator('.timeline-widget #timeline').waitFor();
  assert.equal(again.isClosed(), true);

  // Reloading the popup keeps the timeline in it, and it still drives the editor.
  let popout = await popOut(page);
  await popout.reload();
  await popout.locator('#timeline .event-node').first().waitFor();
  assert.equal(await page.locator('.timeline-widget').count(), 0, 'the reloaded popup reconnects');
  await popout.locator('.event-node', {hasText: 'input'}).click();
  assert.equal(await page.evaluate(() => graphStudio.event), 'input');

  // Reloading the editor: the popup waits, then reconnects to the new page.
  await page.reload(); await page.waitForFunction(() => globalThis.graphStudio?.settingsRecord);
  await page.evaluate(id => graphStudio.select(id), example.id);
  await popout.locator('#timeline .event-node').first().waitFor();
  assert.equal(popout.isClosed(), false);
  assert.equal(await page.locator('.timeline-widget').count(), 0, 'the popup reconnects to the reloaded editor');
  await popout.locator('.event-node', {hasText: 'think'}).click();
  assert.equal(await page.evaluate(() => graphStudio.event), 'think');

  // Two editors, the second opened from the first: each keeps its own popup.
  const [other] = await Promise.all([context.waitForEvent('page'), page.evaluate(url => window.open(url), page.url())]);
  await other.waitForFunction(() => globalThis.graphStudio?.settingsRecord);
  await other.evaluate(id => graphStudio.select(id), example.id); await other.locator('.event-node').first().waitFor();
  const otherPopout = await popOut(other);
  assert.notEqual(otherPopout, popout);
  assert.equal(await popout.locator('#timeline .event-node').count(), 2, "the first editor's popup is untouched");
  await otherPopout.locator('.event-node', {hasText: 'input'}).click();
  assert.equal(await other.evaluate(() => graphStudio.event), 'input');
  assert.equal(await page.evaluate(() => graphStudio.event), 'think', 'clicks reach only their own editor');

  // Closing an editor closes its popup and leaves the other pair alone.
  await other.close();
  await otherPopout.waitForEvent('close', {timeout: 5000});
  assert.equal(popout.isClosed(), false);
  await page.close();
  await popout.waitForEvent('close', {timeout: 5000});

  assert.deepEqual(errors, []);
  console.log('Pop-out panels: pop out, drive the editor, dock back, reload either window, two editors and closing passed.');
} finally {
  await browser.close();
}
