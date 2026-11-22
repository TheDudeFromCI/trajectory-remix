/* Small fake-provider browser checks. No live model traffic. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {chromium} = createRequire(import.meta.url)('playwright');
const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium',headless:true,args:['--no-sandbox']});
try {
  const page = await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.argv[2]); await page.locator('#sampler-tab').click();
  await page.locator('#sampler-detail h2').waitFor();
  await page.locator('.sw-preview summary').first().click();
  const saved = await page.evaluate(()=>JSON.stringify(samplerWorkbench.config));
  await page.getByLabel('Pin domain',{exact:true}).selectOption('software_engineering');
  await page.locator('#sampler-generate').click();
  await page.waitForFunction(()=>document.getElementById('sampler-run-status').textContent.includes('Started 1 attempts'));
  const job = await page.evaluate(()=>document.getElementById('sampler-run-status').textContent.match(/job ([0-9a-f-]+)/)[1]);
  for (let i=0;i<100;i++) {
    const status=await page.evaluate(async job=>await (await fetch(`/api/v1/jobs/${job}`,{headers:{'X-Remix-Token':document.querySelector('meta[name=remix-token]').content}})).json(),job);
    if (status.state==='completed') break;
    if (status.state==='paused') throw new Error('Fixture paused');
    await page.waitForTimeout(50);
  }
  await page.evaluate(()=>[...document.querySelectorAll('dialog[open]')].forEach(d=>d.close()));
  const get = path => page.evaluate(async path=>await (await fetch('/api/v1'+path,{headers:{'X-Remix-Token':document.querySelector('meta[name=remix-token]').content}})).json(),path);
  const attempts = await get(`/jobs/${job}/attempts`), record = await get(`/trajectories/${attempts[0].trajectory_id}`);
  assert.equal(attempts.length,1); assert.equal(record.source.metadata.stage,0,JSON.stringify({stage:record.source.metadata.stage,events:Object.keys(record.source.events),state:(await get('/jobs/'+job)).state}));
  assert.equal(attempts[0].proposal.proposal.sampling_provenance.requested_overrides.domain,'software_engineering');
  assert.equal(await page.evaluate(()=>JSON.stringify(samplerWorkbench.config)),saved);
  await page.getByLabel('Pin domain',{exact:true}).selectOption('');
  assert.equal((await get(`/jobs/${job}/attempts`))[0].proposal.proposal.sampling_provenance.requested_overrides.domain,'software_engineering');
  // Unsaved weights block generation; pins alone do not dirty the matrix.
  await page.locator('.sw-table input[type=number]').first().fill('2.5');
  assert.equal(await page.locator('#sampler-generate').isDisabled(),true);
  page.once('dialog',d=>d.accept()); await page.locator('#sampler-reset').click();
  await page.locator('#trajectory-tab').click();
  await page.evaluate(async id=>await graphStudio.select(id,true),record.source.id);
  await page.locator('#trajectory-menu summary').click(); await page.locator('#review-source').click();
  await page.waitForFunction(()=>[...graphStudio.jobs.values()].filter(j=>j.kind==='bulk'&&j.state==='completed').length>=1);
  const reviewed = await get(`/trajectories/${record.source.id}`);
  assert.equal(reviewed.source.metadata.stage,1);
  assert.equal(reviewed.source.metadata.evaluations.length,1); // Standard Pending is Fast only.
  await page.evaluate(async id=>await graphStudio.select(id,true),record.source.id);
  await page.locator('#trajectory-menu summary').click(); await page.locator('#duplicate-sample').click();
  await page.waitForFunction(id=>graphStudio.record && graphStudio.record.source.id !== id,record.source.id);
  const duplicateId = await page.evaluate(()=>graphStudio.record.source.id);
  assert.deepEqual((await get(`/trajectories/${duplicateId}`)).source, {...reviewed.source,id:duplicateId});
  await page.locator('#trajectory-menu summary').click(); await page.locator('#review-repair-source').click();
  await page.waitForFunction(()=>[...graphStudio.jobs.values()].filter(j=>j.kind==='bulk'&&j.state==='completed').length>=2);
  const duplicateReview = (await get(`/trajectories/${duplicateId}`)).source.metadata;
  assert.equal(duplicateReview.stage,2);
  assert.equal(duplicateReview.evaluations.at(-1).prompt_version,'native-review/2');
  assert.deepEqual((await get(`/trajectories/${record.source.id}`)).source,reviewed.source);
  // More → Export downloads the open trajectory's source as JSON.
  await page.evaluate(async id=>await graphStudio.select(id,true),record.source.id);
  await page.locator('#trajectory-menu summary').click();
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#export-source').click()]);
  assert.equal(download.suggestedFilename(),`${record.source.id}.json`);
  // Bulk Export downloads every trajectory matching the current search.
  await page.locator('.bulk-edit summary').click(); await page.selectOption('#bulk-action','export');
  const [bulk]=await Promise.all([page.waitForEvent('download'),page.locator('#bulk-start').click()]);
  assert.equal(bulk.suggestedFilename(),'trajectories.jsonl');
  // Two distinct review jobs, independent logs and settled history records.
  const summaries = await get('/job-summaries');
  assert.deepEqual(summaries.filter(j=>j.kind==='bulk').map(j=>j.action), ['review_repair','review_repair']);
  assert.deepEqual(summaries.find(j=>j.id===job).stage_progress, {completed:2,total:2});
  await page.locator('#trajectory-tab').click();
  await page.evaluate(async id=>await graphStudio.select(id,true),record.source.id);
  // The acknowledgment is usable at desktop and compact sizes, persists, and
  // affects only the open-window advisory rather than inserting closure.
  const openWindow = Object.keys(reviewed.source.windows).find(id=>reviewed.source.windows[id].purpose==='reasoning');
  assert.ok(openWindow);
  for (const viewport of [{width:1440,height:1000},{width:1024,height:768}]) {
    await page.setViewportSize(viewport);
    await page.evaluate(()=>[...document.querySelectorAll('dialog[open]')].forEach(d=>d.close()));
    await page.getByRole('tab',{name:'Windows',exact:true}).click();
    await page.locator('#window-id').fill(openWindow); await page.locator('#window-id').dispatchEvent('change');
    const acknowledgment=page.getByLabel('Intentionally left open (acknowledge warning)',{exact:true});
    await acknowledgment.scrollIntoViewIfNeeded();
    assert.equal(await acknowledgment.isVisible(),true);
    assert.equal(await acknowledgment.evaluate(n=>getComputedStyle(n).visibility),'visible');
    await acknowledgment.check(); await page.locator('#window-save').click();
    await page.locator('#save-source').click();
    await page.waitForFunction(id=>graphStudio.record.source.windows[id]?.intentionally_left_open===true && !graphStudio.isDirty(),openWindow);
    assert.deepEqual((await get(`/trajectories/${record.source.id}`)).source.events,reviewed.source.events);
    assert.equal(await page.evaluate(id=>graphStudio.diagnostics.some(d=>d.code==='open_window'&&graphStudio.draft.events[d.event_id].window===id),openWindow),false);
  }
  assert.deepEqual(errors,[]);
  console.log('Generation pins, saved weights, frozen run and Review & Repair browser checks passed');
} finally { await browser.close(); }
