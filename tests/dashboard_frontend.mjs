/* Fresh-project setup, jobs and real polling; one tiny fake-provider trajectory. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import fs from 'node:fs/promises';
const {chromium}=createRequire(import.meta.url)('playwright');
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
try {
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const api=(path,method='GET',body)=>page.evaluate(async({path,method,body})=>{
  const r=await fetch('/api/v1'+path,{method,headers:{'Content-Type':'application/json','X-Remix-Token':document.querySelector('meta[name=remix-token]').content},...(body?{body:JSON.stringify(body)}:{})});
  const value=await r.json(); if(!r.ok)throw new Error(JSON.stringify(value));return value;
 },{path,method,body});
 const close=()=>page.evaluate(()=>document.querySelectorAll('dialog[open]').forEach(d=>d.close()));
 const screenshot=async name=>{if(process.env.UPDATE_SCREENSHOTS==='1'){await fs.mkdir(new URL('../docs/images/dashboard-restored/',import.meta.url),{recursive:true});await page.screenshot({path:new URL(`../docs/images/dashboard-restored/${name}.png`,import.meta.url).pathname,fullPage:true});}};
 await page.goto(process.argv[2]);await page.waitForFunction(()=>globalThis.graphStudio?.settingsRecord);
 assert.equal(await page.locator('#jobs-button').isVisible(),true);
 await page.locator('#project-menu summary').click();
 await page.locator('#settings-button').click();
 await page.getByLabel('New endpoint ID',{exact:true}).fill('fixture');await page.getByRole('button',{name:'Add endpoint',exact:true}).click();
 await page.getByLabel('Endpoint fixture URL',{exact:true}).fill('http://127.0.0.1:1/v1');
 await page.getByLabel('Endpoint fixture model',{exact:true}).fill('fake-browser');
 // Tooltips inside the modal settings dialog must render above it, not behind it.
 await page.getByLabel('Endpoint fixture max_output_tokens',{exact:true}).hover();await page.waitForFunction(()=>{const t=document.getElementById('tooltip');return t.classList.contains('show')&&t.closest('dialog[open]')&&/max_tokens/.test(t.textContent);});
 await page.getByLabel('Draft endpoint',{exact:true}).selectOption('fixture');
 // Editing a tool after endpoint forms must not overwrite the shared settings draft.
 await page.getByRole('tab',{name:'Tools',exact:true}).click();
 await page.getByLabel('New tool ID',{exact:true}).fill('notes');await page.getByRole('button',{name:'Add tool to settings draft',exact:true}).click();
 assert.equal(await page.evaluate(()=>JSON.parse(document.getElementById('settings-json').value).endpoints.fixture.model),'fake-browser');
 await page.getByRole('tab',{name:'Validation',exact:true}).click();
 const warning=page.getByRole('checkbox',{name:'Open window',exact:true});
 assert.equal(await warning.isChecked(),true);
 await warning.uncheck();
 if(process.env.VALIDATION_SCREENSHOT) {
  await page.locator('#settings-panel-validation').evaluate(n=>n.scrollTop=0);
  await page.screenshot({path:process.env.VALIDATION_SCREENSHOT});
 }
 await page.locator('#settings-dialog').evaluate(n=>n.scrollTop=0);await screenshot('settings');await page.locator('#settings-save').click();await page.waitForFunction(()=>!document.getElementById('settings-dialog').open);
 const saved=await api('/settings');assert.equal(saved.settings.endpoint_roles.draft,'fixture');assert.ok(saved.settings.tools.notes);assert.deepEqual(saved.settings.disabled_warnings,['open_window']);
 await page.locator('#generation-settings-button').click();
 // The Generate dialog says what a run freezes; there is no Flow panel any more.
 await page.locator('#generation-attempts').waitFor();assert.equal(await page.locator('.flow-section').count(),0);
 await page.locator('#generation-attempts').fill('1');
 await page.locator('#generation-start').click();await page.waitForFunction(()=>graphStudio.dialogs.size===1);
 const jobs=await api('/job-summaries'),job=jobs[0].id;
 await page.waitForFunction(()=>!document.getElementById('jobs-view').hidden);
 await page.getByRole('button',{name:'Pause',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#job-detail .job-panel')?.dataset.state==='run · paused');
 assert.match(await page.locator('#job-detail .progress-pct').first().textContent(), /^[0-2]\/2 stages$/);
 if(process.env.GENERATION_PROGRESS_SCREENSHOT) await page.screenshot({path:process.env.GENERATION_PROGRESS_SCREENSHOT});
 await screenshot('jobs');await close();
 const attempt=(await api(`/jobs/${job}/attempts`))[0];
 await page.evaluate(async id=>await graphStudio.select(id,true),attempt.trajectory_id);
 assert.equal(await page.locator('#event-content').isDisabled(),true);
 await page.locator('#jobs-button').click();await page.locator('.job-row').first().click();
 await page.getByRole('button',{name:'Resume',exact:true}).click();await page.locator('#trajectory-tab').click();
 // No manual reload: the original blank attached snapshot must grow on server changes.
 await page.waitForFunction(()=>Object.keys(graphStudio.draft.events).length>0,{},{timeout:10000});
 await page.waitForFunction(()=>graphStudio.record.owner===null,{},{timeout:10000});
 assert.equal(await page.evaluate(()=>graphStudio.position),Number(await page.locator('#scrubber').getAttribute('max')));
 assert.ok(await page.locator('#window-grid').textContent());assert.equal(await page.locator('#event-content').isDisabled(),false);
 assert.equal((await api('/jobs/'+job)).state,'completed');
 assert.equal(await page.evaluate(()=>graphStudio.isDirty()),false);
 await page.locator('#scrubber').evaluate(n=>{n.value='2';n.dispatchEvent(new Event('input'));});await screenshot('desktop');
 // View menu hides and restores editor panels.
 await page.locator('#view-menu summary').click();await page.locator('#toggle-timeline-panel').click();
 assert.equal(await page.locator('.timeline-widget').isVisible(),false);
 await page.locator('#view-menu summary').click();assert.equal(await page.locator('#toggle-timeline-panel').textContent(),'Show timeline panel');
 await page.locator('#show-all-panels').click();assert.equal(await page.locator('.timeline-widget').isVisible(),true);
 // Clean changes refresh, while genuinely typed (not yet applied) editor text survives.
 const id=attempt.trajectory_id;
 // Follow appended server events when already at the live edge; preserve an
 // explicitly scrubbed historical position when another event arrives.
 await page.locator('#last-tick').click();
 let live=await api('/trajectories/'+id);
 const last=await page.evaluate(()=>Object.keys(graphStudio.draft.events).find(id=>id.endsWith(':echo')) || 'decision');
 await api('/trajectories/'+id,'PATCH',{expected_revision:live.revision,patch:{windows:{live_doc:{purpose:'document',agent_writable:false}},events:{live_one:{kind:'external_text',window:'live_doc',after:[last],content:'Live appended observation'}}}});
 await page.waitForFunction(()=>Boolean(graphStudio.draft.events.live_one));
 assert.equal(await page.evaluate(()=>graphStudio.position),Number(await page.locator('#scrubber').getAttribute('max')));
 assert.ok((await page.locator('#window-grid').textContent()).includes('Live appended observation'));
 await page.locator('#first-tick').click();
 live=await api('/trajectories/'+id);
 await api('/trajectories/'+id,'PATCH',{expected_revision:live.revision,patch:{events:{live_two:{kind:'external_text',window:'live_doc',after:['live_one'],content:'Later observation'}}}});
 await page.waitForFunction(()=>Boolean(graphStudio.draft.events.live_two));
 assert.equal(await page.evaluate(()=>graphStudio.position),0);
 let record=await api('/trajectories/'+id),event=Object.keys(record.source.events)[0];
 await page.evaluate(event=>graphStudio.selectEvent(event),event);
 await api('/trajectories/'+id,'PATCH',{expected_revision:record.revision,patch:{metadata:{browser_update:1}}});
 await page.waitForFunction(()=>graphStudio.draft.metadata.browser_update===1);
 await page.locator('#event-content').fill('unsaved local text');
 record=await api('/trajectories/'+id);
 await api('/trajectories/'+id,'PATCH',{expected_revision:record.revision,patch:{metadata:{browser_update:2}}});
 await page.waitForFunction(()=>document.getElementById('status').textContent.includes('edits are retained'));
 assert.equal(await page.locator('#event-content').inputValue(),'unsaved local text');
 assert.equal(await page.evaluate(()=>graphStudio.draft.metadata.browser_update),1);
 // Re-selecting a clean source always gets latest saved revision rather than a stale cache.
 await page.evaluate(async id=>await graphStudio.select(id,true),id);
 assert.equal(await page.evaluate(()=>graphStudio.draft.metadata.browser_update),2);
 assert.deepEqual(errors,[]);
 console.log('Dashboard workflow passed: form setup, shared tool draft, generation, Jobs pause/resume, live source refresh/unlock and dirty-buffer preservation.');
}finally{await browser.close();}
