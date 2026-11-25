/* One temporary project, a handful of deterministic requests. No performance checks. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import fs from 'node:fs/promises';
const require = createRequire(import.meta.url);
const {chromium} = require('playwright'); // Optional external browser tooling; no runtime dependency.
const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless:true, args:['--no-sandbox']});
try {
  const page = await browser.newPage({viewport:{width:1440,height:1100}}), errors=[], paths=[];
  page.on('pageerror', e=>errors.push(e.message));
  page.on('request', r=>{if(r.url().includes('/api/'))paths.push(r.url());});
  await page.goto(process.argv[2]); await page.locator('#sampler-tab').click();
  await page.locator('#sampler-detail h2').waitFor();
  const state = () => page.evaluate(()=>structuredClone(samplerWorkbench.config));
  const api = (path, body, method='POST') => page.evaluate(async ({path,body,method})=>{
    const r=await fetch('/api/v1'+path,{method,headers:{'Content-Type':'application/json','X-Remix-Token':document.querySelector('meta[name=remix-token]').content},body:JSON.stringify(body)});return {status:r.status,body:await r.json()};
  },{path,body,method});
  // A frozen run has no driver attached: it creates snapshots, never model traffic.
  const run = await api('/sampler/runs',{requests:[{seed:137}],instructions:{note:'frozen browser fixture'}});
  assert.equal(run.status,202);
  const before = (await api('/jobs/'+run.body.job_id,undefined,'GET')).body;
  await page.getByLabel('Find a dimension or entry').fill('user tone');
  assert.equal(await page.locator('.sw-nav-row').count(),2); // matrix group and its shared metric definition
  await page.locator('.sw-nav-row').first().click();
  assert.equal(await page.locator('#sampler-detail h2').textContent(),'user tone');
  await page.getByLabel('Find a dimension or entry').fill('');
  await page.getByLabel('Filter choices in group',{exact:true}).fill('friendly');
  assert.equal(await page.locator('.sw-table tbody tr').count(),1);
  await page.getByLabel('Filter choices in group',{exact:true}).fill('');
  const weight=page.locator('.sw-table input[type=number]').first();
  await weight.focus(); await weight.press('ControlOrMeta+a'); await weight.press('2');
  assert.equal(await weight.evaluate(n=>document.activeElement===n),true);
  await weight.press('Tab'); assert.equal(await page.locator('#sampler-dirty').textContent(),'Unsaved project draft');
  assert.equal(await page.locator('#sampler-preview').isDisabled(),true);
  await page.locator('.sw-table input[type=checkbox]').nth(1).check();
  await page.locator('.sw-table input[type=checkbox]').nth(2).check();
  await page.getByLabel('Repeated edit value').fill('3.5'); await page.getByRole('button',{name:'Set selected',exact:true}).click();
  const draft=await state(); assert.equal(draft.matrix.dimensions.user_tone[0].weight,3.5); assert.equal(draft.matrix.dimensions.user_tone[1].weight,3.5);
  await page.getByText('Advanced JSON · rules, constraints & custom fields',{exact:true}).click();
  const entries=draft.matrix.dimensions.user_tone; entries[0].custom_fixture={keep:['unknown','nested']};
  await page.getByLabel('Group JSON').fill(JSON.stringify(entries)); await page.getByRole('button',{name:'Apply JSON to project draft',exact:true}).click();
  await page.getByText('Linked metric · user_tone · annotation',{exact:true}).click();
  await page.getByLabel('user_tone absolute tolerance',{exact:true}).fill('0.25');
  await page.getByLabel('user_tone relative tolerance',{exact:true}).fill('0.05');
  await page.locator('#sampler-save').click(); await page.waitForFunction(()=>!samplerWorkbench.dirty()&&!samplerWorkbench.saving);
  assert.equal((await state()).matrix.dimensions.user_tone[0].custom_fixture.keep[1],'nested');
  assert.deepEqual((await state()).metric_options.user_tone.tolerance,{absolute:.25,relative:.05});
  assert.equal(await page.locator('.sw-table input[type=checkbox]:checked').count(),2);
  const after=(await api('/jobs/'+run.body.job_id,undefined,'GET')).body;
  assert.ok(before.config.config.sampler.configuration.matrix_digest); assert.deepEqual(after.config,before.config);
  assert.notEqual((await state()).matrix_digest,before.config.config.sampler.configuration.matrix_digest);
  // Friendly structural controls persist new entries and rules via shared CAS.
  await page.getByText('Add or remove entries',{exact:true}).click();
  await page.getByLabel('New entry ID',{exact:true}).fill('workbench_fixture');
  await page.getByRole('button',{name:'Copy entry to draft',exact:true}).click();
  assert.ok((await state()).matrix.dimensions.user_tone.some(x=>x.id==='workbench_fixture'));
  await page.locator('#sampler-save').click(); await page.waitForFunction(()=>!samplerWorkbench.dirty()&&!samplerWorkbench.saving);
  assert.ok((await state()).definitions.user_tone.choices.includes('workbench_fixture'));
  await page.getByText('Add or remove entries',{exact:true}).click();
  await page.getByLabel('Entry to copy or remove',{exact:true}).selectOption('workbench_fixture');
  page.once('dialog',d=>d.accept()); await page.getByRole('button',{name:'Remove entry from draft',exact:true}).click();
  await page.locator('#sampler-save').click(); await page.waitForFunction(()=>!samplerWorkbench.dirty()&&!samplerWorkbench.saving);
  await page.locator('.sw-nav-row').filter({hasText:'Conditional multipliers'}).click();
  const ruleCount=(await state()).matrix.conditional_weights.length;
  await page.getByText('Add or remove rules',{exact:true}).click();
  await page.getByRole('button',{name:'Add rule to draft',exact:true}).click();
  await page.locator('#sampler-save').click(); await page.waitForFunction(()=>!samplerWorkbench.dirty()&&!samplerWorkbench.saving);
  assert.equal((await state()).matrix.conditional_weights.length,ruleCount+1);
  await page.getByText('Add or remove rules',{exact:true}).click();
  await page.getByLabel('Entry to copy or remove',{exact:true}).selectOption('Rule '+(ruleCount+1));
  page.once('dialog',d=>d.accept()); await page.getByRole('button',{name:'Remove rule from draft',exact:true}).click();
  await page.locator('#sampler-save').click(); await page.waitForFunction(()=>!samplerWorkbench.dirty()&&!samplerWorkbench.saving);
  assert.equal((await state()).matrix.conditional_weights.length,ruleCount);
  await page.locator('.sw-nav-row').filter({hasText:'user tone'}).first().click();
  // Invalid group constraints are rejected and the entire project draft survives.
  await page.getByText('Advanced JSON · rules, constraints & custom fields',{exact:true}).click();
  const invalid=(await state()).matrix.dimensions.user_tone; invalid[1].id=invalid[0].id;
  await page.getByLabel('Group JSON').fill(JSON.stringify(invalid)); await page.getByRole('button',{name:'Apply JSON to project draft',exact:true}).click();
  await page.locator('#sampler-save').click(); await page.waitForFunction(()=>document.getElementById('sampler-message').textContent.includes('Save rejected'));
  assert.equal((await state()).matrix.dimensions.user_tone[1].id,invalid[0].id);
  page.once('dialog',d=>d.accept()); await page.locator('#sampler-reload').click(); await page.waitForFunction(()=>!samplerWorkbench.dirty());
  assert.equal((await state()).matrix.dimensions.user_tone[0].weight,3.5);
  // Invalid numeric text survives group navigation and blocks saving.
  await page.locator('.sw-table input[type=number]').first().fill('-2');
  await page.locator('.sw-nav-row').filter({hasText:'Competency families'}).click();
  await page.locator('#sampler-save').click();
  assert.ok((await page.locator('#sampler-message').textContent()).includes('Resolve invalid numeric'));
  await page.locator('.sw-nav-row').filter({hasText:'user tone'}).first().click();
  assert.equal(await page.locator('.sw-table input[type=number]').first().inputValue(),'-2');
  await page.locator('.sw-table input[type=number]').first().fill('3.5');
  // Same seed/pins -> same payload; pins don't alter weights.
  await page.locator('.sw-preview > summary').click(); await page.getByLabel('Pin domain',{exact:true}).selectOption('software_engineering');
  await page.locator('#sampler-seed').fill('137'); // seed 0 is random, so pin one for the determinism check
  await page.locator('#sampler-preview').click(); await page.waitForFunction(()=>document.getElementById('sampler-proposal').textContent.length>10);
  const first=JSON.parse(await page.locator('#sampler-proposal').textContent());
  await page.locator('#sampler-preview').click(); await page.waitForFunction(()=>!document.getElementById('sampler-preview').disabled);
  const second=JSON.parse(await page.locator('#sampler-proposal').textContent());
  assert.equal(first.payload_digest,second.payload_digest); assert.equal(second.proposal.metrics.sampling.domain,'software_engineering');
  assert.equal((await state()).matrix.dimensions.user_tone[0].weight,3.5);
  await page.getByLabel('Pin focus.family',{exact:true}).selectOption('strategic_planning');
  await page.getByLabel('Pin lifecycle',{exact:true}).selectOption('closure'); await page.getByLabel('Pin task_length',{exact:true}).selectOption('XXS');
  await page.locator('#sampler-preview').click(); await page.waitForFunction(()=>document.getElementById('sampler-message').textContent.includes('Preview rejected'));
  // Revision conflicts retain the browser draft.
  const current=await page.evaluate(async()=>await (await fetch('/api/v1/sampler',{headers:{'X-Remix-Token':document.querySelector('meta[name=remix-token]').content}})).json());
  await api('/sampler',{matrix:current.configuration.matrix,metric_options:current.configuration.metric_options,expected_revision:current.settings_revision},'PUT');
  await page.locator('.sw-table input[type=number]').first().fill('4'); await page.locator('#sampler-save').click();
  await page.waitForFunction(()=>document.getElementById('sampler-message').textContent.includes('Save rejected'));
  assert.equal((await state()).matrix.dimensions.user_tone[0].weight,4);
  page.once('dialog',d=>d.accept()); await page.locator('#sampler-reload').click(); await page.waitForFunction(()=>!samplerWorkbench.dirty());
  // A page reload proves persistence, including custom fields.
  await page.reload(); await page.locator('#sampler-tab').click(); await page.locator('#sampler-detail h2').waitFor();
  assert.equal((await state()).matrix.dimensions.user_tone[0].weight,3.5);
  assert.deepEqual((await state()).matrix.dimensions.user_tone[0].custom_fixture,{keep:['unknown','nested']});
  await page.getByLabel('Find a dimension or entry').fill('complexity');
  await page.locator('.sw-nav-row').filter({hasText:'complexity'}).first().click();
  await page.getByLabel('Find a dimension or entry').fill('');
  // Inline pins force one choice per field without editing weights or calling APIs.
  const savedBeforePins = await state(), requestsBeforePins = paths.filter(p=>p.includes('/sampler')).length;
  const pin = value => page.getByRole('button',{name:`Pin ${value} for complexity`,exact:true});
  assert.equal(await page.locator('.sw-pin[aria-pressed=true]').count(),0);
  await pin('medium').click(); assert.equal(await pin('medium').getAttribute('aria-pressed'),'true');
  assert.equal(await page.getByLabel('Pin complexity',{exact:true}).inputValue(),'medium');
  await pin('easy').click(); assert.equal(await pin('medium').getAttribute('aria-pressed'),'false');
  assert.equal(await page.locator('.sw-pin[aria-pressed=true]').count(),1);
  await pin('easy').click(); assert.equal(await page.locator('.sw-pin[aria-pressed=true]').count(),0);
  await pin('medium').focus(); await pin('medium').press('Space');
  await page.getByLabel('Filter choices in group',{exact:true}).fill('easy');
  await page.getByLabel('Filter choices in group',{exact:true}).fill('');
  assert.equal(await pin('medium').getAttribute('aria-pressed'),'true');
  await page.locator('.sw-nav-row').filter({hasText:'Competency families'}).click();
  await page.getByLabel('Find a dimension or entry').fill('complexity');
  await page.locator('.sw-nav-row').filter({hasText:'complexity'}).first().click();
  await page.getByLabel('Find a dimension or entry').fill('');
  assert.equal(await pin('medium').getAttribute('aria-pressed'),'true');
  assert.equal(paths.filter(p=>p.includes('/sampler')).length,requestsBeforePins);
  assert.deepEqual(await state(),savedBeforePins); assert.equal(await page.locator('#sampler-save').isDisabled(),true);
  await page.locator('.sw-preview > summary').click();
  await page.getByLabel('Pin complexity',{exact:true}).selectOption('expert');
  assert.equal(await pin('expert').getAttribute('aria-pressed'),'true');
  assert.equal(await pin('medium').getAttribute('aria-pressed'),'false');
  const previewRequest = page.waitForRequest(r=>r.url().endsWith('/sampler/proposals')&&r.method()==='POST');
  await page.locator('#sampler-preview').click();
  assert.deepEqual((await previewRequest).postDataJSON().overrides,{complexity:'expert'});
  await page.waitForFunction(()=>document.getElementById('sampler-proposal').textContent.length>10);
  assert.equal(JSON.parse(await page.locator('#sampler-proposal').textContent()).proposal.metrics.sampling.complexity,'expert');
  await pin('medium').click(); await page.locator('.sw-preview > summary').click();
  if (process.env.UPDATE_SCREENSHOTS) await fs.mkdir(new URL('../docs/images/sampler-workbench/',import.meta.url),{recursive:true});
  await page.locator('#sampler-workbench').evaluate(n=>n.scrollTop=0); await page.locator('#sampler-nav').evaluate(n=>n.scrollTop=0);
  if (process.env.UPDATE_SCREENSHOTS) await page.screenshot({path:new URL('../docs/images/sampler-workbench/desktop.png',import.meta.url).pathname,fullPage:true});
  await page.locator('#sampler-filter').selectOption('Rules'); await page.locator('.sw-nav-row').filter({hasText:'Hard implications'}).click();
  await page.getByText('Advanced JSON · rules, constraints & custom fields',{exact:true}).click();
  await page.locator('#sampler-workbench').evaluate(n=>n.scrollTop=0); await page.locator('#sampler-nav').evaluate(n=>n.scrollTop=0);
  if (process.env.UPDATE_SCREENSHOTS) await page.screenshot({path:new URL('../docs/images/sampler-workbench/rules.png',import.meta.url).pathname,fullPage:true});
  await page.setViewportSize({width:600,height:900});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]); assert.equal(paths.some(p=>/compile|tokeniz|preview$/.test(p)),false);
  console.log('Sampler browser checks passed: search/filter, keyboard focus, repeated edits, persistence, unknown fields, tolerance, invalid constraints, revision conflict, fixed seed, pins, run isolation and narrow layout.');
} finally { await browser.close(); }
