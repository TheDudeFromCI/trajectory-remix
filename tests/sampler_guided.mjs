/* A handful of real-browser interactions; no load, distribution, or model tests. */
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';
const base = process.argv[2];
const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium', args:['--no-sandbox']});
const page = await browser.newPage({viewport:{width:1440,height:1100}});
const errors = [], requests = [];
page.on('pageerror',e=>errors.push(e.message));
page.on('request', r => {if(r.url().includes('/api/')) requests.push(r.url());});
const screenshotDir = path.resolve('work/sampler-guided'); await fs.mkdir(screenshotDir,{recursive:true});
try {
  await page.goto(base); await page.waitForFunction(()=>globalThis.graphStudio?.settingsRecord);
  const api = (route, method='GET', body) => page.evaluate(async ({route,method,body})=>{
    const r=await fetch('/api/v1'+route,{method,headers:{'Content-Type':'application/json','X-Remix-Token':document.querySelector('meta[name=remix-token]').content}, ...(body===undefined?{}:{body:JSON.stringify(body)})});
    if(!r.ok)throw new Error(await r.text()); return r.json();
  },{route,method,body});
  // Seed the shared backend with an extension that the UI must not drop.
  const initial = await api('/sampler');
  initial.configuration.matrix.custom_extension = {owner:'keep me',nested:{flag:true,values:[1,'two']}};
  initial.configuration.matrix.custom_scalar = 'Original custom value';
  initial.configuration.matrix.dimensions.user_tone[0].custom_note = 'preserve this';
  await api('/sampler','PUT',{matrix:initial.configuration.matrix, metric_options:initial.configuration.metric_options,expected_revision:initial.settings_revision});
  await page.getByRole('button',{name:'Generation Settings',exact:true}).click();
  await page.waitForFunction(()=>globalThis.guidedSampler?.record);
  const saved = await api('/sampler');
  const snapshot = await api('/sampler/proposals','POST',{seed:137});
  assert.equal(await page.locator('#sampler-sections > details[open]').count(),0);
  await page.locator('#sampler-search').fill('user patience');
  assert.ok(await page.locator('#sampler-sections > details:visible').count() > 0);
  const group=page.locator('.sampler-card:visible').filter({has:page.locator('h4',{hasText:'User patience'})}).first();
  await group.locator(':scope > details > summary').first().click();
  await page.locator('#sampler-search').fill('');
  await page.locator('#sampler-collapse').click();
  // Plain-language competency explanations precede the numeric editor.
  await page.locator('#sampler-sections > details').first().locator(':scope > summary').click();
  const family = page.locator('#sampler-sections > details').first().locator('article.sampler-card').first();
  await family.locator(':scope > details > summary').first().click();
  await page.waitForFunction(()=>document.querySelector('[data-search^="scope and acceptance"]'));
  assert.ok(await page.getByText('Clarify goal boundaries, deliverables, and measurable acceptance criteria',{exact:true}).isVisible());
  await page.screenshot({path:path.join(screenshotDir,'guided-desktop.png')});
  // Edit a description through a real typed control and round-trip unknowns.
  await page.locator('#sampler-search').fill('user tone');
  const tone = page.locator('#sampler-sections > details:visible .sampler-section-body > .sampler-card:visible').first();
  await tone.locator(':scope > details > summary').first().click();
  const firstTone = tone.locator('article').first();
  await firstTone.locator(':scope > details > summary').last().click();
  const desc=page.locator('[data-path="dimensions.user_tone.0.description"]');
  await desc.fill('A calm, direct person who appreciates clear explanations.');
  assert.equal(await page.locator('#sampler-preview').isDisabled(),true);
  await page.locator('#sampler-save').click();
  await page.waitForFunction(()=>!globalThis.guidedSampler.dirty && !globalThis.guidedSampler.busy);
  const updated=await api('/sampler');
  assert.deepEqual(updated.configuration.matrix.custom_extension, saved.configuration.matrix.custom_extension);
  assert.equal(updated.configuration.matrix.dimensions.user_tone[0].custom_note,'preserve this');
  assert.equal(updated.configuration.matrix.dimensions.user_tone[0].description,'A calm, direct person who appreciates clear explanations.');
  assert.deepEqual((await api('/sampler/proposals/'+snapshot.id)).frozen,snapshot.frozen);
  // Custom scalar keys with underscores must write through, not edit a presentation copy.
  await page.locator('#sampler-search').fill('custom scalar');
  await page.locator('[data-path="custom_scalar"]').fill('Edited custom value');
  await page.locator('#sampler-save').click();
  await page.waitForFunction(()=>!globalThis.guidedSampler.dirty && !globalThis.guidedSampler.busy);
  assert.equal((await api('/sampler')).configuration.matrix.custom_scalar,'Edited custom value');
  // Per-metric tolerance is opt-in, two bands, and supports clearing after errors.
  await page.locator('#sampler-search').fill('authored windows');
  const metric = page.locator('#sampler-sections > details:visible .sampler-card:visible').first();
  await metric.locator('summary').click();
  await page.locator('[data-path="metrics.authored_windows.absolute"]').fill('2');
  await page.locator('#sampler-save').click();
  await page.waitForFunction(()=>!globalThis.guidedSampler.busy);
  assert.match(await page.locator('#sampler-error').textContent(),/highlighted/);
  await page.locator('[data-path="metrics.authored_windows.relative"]').fill('0.05');
  await page.locator('#sampler-save').click();
  await page.waitForFunction(()=>!globalThis.guidedSampler.dirty && !globalThis.guidedSampler.busy);
  assert.deepEqual((await api('/sampler')).configuration.definitions.authored_windows.tolerance,{absolute:2,relative:0.05});
  await page.locator('#sampler-search').fill(''); await page.locator('#sampler-collapse').click();
  // Tabs work with keyboard, Any is default, pinning never touches project weights.
  await page.locator('#sampler-matrix-tab').focus(); await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#sampler-pins-tab').getAttribute('aria-selected'),'true');
  assert.equal(await page.locator('#sampler-pins-tab').evaluate(e=>e===document.activeElement),true);
  assert.equal(await page.locator('[data-pin="domain"]').inputValue(),'');
  await page.locator('[data-pin="domain"]').selectOption('software_engineering');
  const digest=(await api('/sampler')).configuration.matrix_digest;
  await page.locator('#sampler-preview').click();
  await page.waitForFunction(()=>globalThis.guidedSampler.last && !globalThis.guidedSampler.busy);
  assert.equal(await page.evaluate(()=>globalThis.guidedSampler.last.proposal.scenario.domain.id),'software_engineering');
  assert.equal((await api('/sampler')).configuration.matrix_digest,digest);
  const first=await page.evaluate(()=>globalThis.guidedSampler.last.proposal);
  await page.locator('#sampler-preview').click(); await page.waitForFunction(()=>!globalThis.guidedSampler.busy);
  assert.deepEqual(await page.evaluate(()=>globalThis.guidedSampler.last.proposal),first);
  assert.match(await page.locator('#sampler-result').textContent(),/Same sampled choices/);
  await page.locator('#sampler-dialog').evaluate(e=>e.scrollTop=0);
  await page.screenshot({path:path.join(screenshotDir,'guided-pins-preview.png')});
  // Incompatible overrides fail explicitly, keeping pins and last result visible.
  await page.locator('[data-pin="focus.family"]').selectOption('strategic_planning');
  await page.locator('[data-pin="lifecycle"]').selectOption('closure');
  await page.locator('[data-pin="complexity"]').selectOption('trivial');
  await page.locator('#sampler-preview').click(); await page.waitForFunction(()=>!globalThis.guidedSampler.busy);
  assert.match(await page.locator('#sampler-error').textContent(),/compatible|configuration|pins/i);
  assert.equal(await page.locator('[data-pin="complexity"]').inputValue(),'trivial');
  await page.locator('#sampler-clear-pins').click();
  // Closing by Escape retains unsaved work; reopening restores focus in the dialog.
  await page.locator('#sampler-pins-tab').focus(); await page.keyboard.press('ArrowLeft');
  await page.locator('#sampler-search').fill('no such setting anywhere');
  assert.match(await page.locator('#sampler-matches').textContent(),/^0 /);
  await page.locator('#sampler-collapse').focus(); await page.keyboard.press('Escape');
  await page.locator('#sampler-dialog').waitFor({state:'hidden'});
  await page.getByRole('button',{name:'Generation Settings',exact:true}).click();
  assert.equal(await page.locator('#sampler-search').inputValue(),'no such setting anywhere');
  await page.locator('#sampler-search').fill(''); await page.locator('#sampler-collapse').click();
  await page.setViewportSize({width:700,height:1100});
  await page.locator('#sampler-dialog').evaluate(e=>e.scrollTop=0);
  assert.equal(await page.locator('#sampler-dialog').evaluate(e=>e.scrollWidth<=e.clientWidth+1),true);
  await page.screenshot({path:path.join(screenshotDir,'guided-narrow.png')});
  // CAS conflict preserves the local draft instead of overwriting external changes.
  await page.setViewportSize({width:1440,height:1100});
  await page.locator('#sampler-search').fill('user tone');
  await desc.fill('Local unsaved description');
  const external=await api('/sampler');
  await api('/sampler','PUT',{matrix:external.configuration.matrix,metric_options:external.configuration.metric_options,expected_revision:external.settings_revision});
  await page.locator('#sampler-save').click(); await page.waitForFunction(()=>!globalThis.guidedSampler.busy);
  assert.match(await page.locator('#sampler-error').textContent(),/draft is retained/);
  assert.equal(await desc.inputValue(),'Local unsaved description');
  assert.equal(await page.evaluate(()=>globalThis.guidedSampler.dirty),true);
  assert.ok(!requests.some(p=>p.includes('/preview') || p.includes('/sampler/runs')));
  assert.deepEqual(errors,[]);
  console.log('Guided sampler Chromium checks passed; screenshots in work/sampler-guided.');
} finally { await browser.close(); }
