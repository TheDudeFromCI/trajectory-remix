/* Tiny DOM/API integration against the graph app; deterministic preview timers. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {Window} from 'happy-dom';
const base = process.argv[2];
const response = await fetch(base), html = await response.text();
const window = new Window({url: base});
globalThis.window = window;
window.document.write(html.replace(/<script[^>]*>.*?<\/script>/gs, ''));
for (const key of ['document', 'localStorage', 'HTMLElement', 'Event', 'MouseEvent']) globalThis[key] = window[key];
const nativeFetch = globalThis.fetch;
const paths = [];
globalThis.fetch = (path, init) => { paths.push(String(path)); return nativeFetch(new URL(path, base), init); };
const code = await fs.readFile(new URL('../trajectory_remix/static/graph.js', import.meta.url), 'utf8');
const module = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const ui = globalThis.graphStudio, $ = id => document.getElementById(id);
const until = async predicate => { for(let i=0;i<300;i++){ if(await predicate()) return; await new Promise(r=>setTimeout(r,10)); } throw new Error('DOM state did not settle: ' + $('status').textContent); };
await until(() => ui.settingsRecord && ui.lastItems?.length === 0);
// Fake timers prove trailing debounce resets and superseded responses cannot publish.
let timer, clears=0, calls=0, publishes=[]; const pending=[];
const controller = new module.PreviewController(p => {calls++;return new Promise(resolve=>pending.push(resolve));}, v=>publishes.push(v), e=>{throw e;}, {
 setTimeout(fn, delay){assert.equal(delay,500);timer=fn;return 1;}, clearTimeout(){clears++;}
});
const payload = {sourceHash:'a',profile:'bytes'};
controller.schedule('a',payload,500); controller.schedule('a',payload,500); assert.equal(calls,0); assert.equal(clears,2);
const old = timer(); assert.equal(calls,1);
controller.schedule('b',{sourceHash:'b',profile:'bytes'},500); const latest=timer();
pending[0]({client_key:'a',profile:'bytes'}); await old; assert.equal(publishes.length,0);
pending[1]({client_key:'b',profile:'bytes'}); await latest; assert.equal(publishes.length,1);
controller.schedule('b',{sourceHash:'b',profile:'bytes'},500); await timer(); assert.equal(calls,2); // cached
controller.schedule('c',{sourceHash:'c',profile:'other'},500); const wrong=timer(); pending[2]({client_key:'c',profile:'bytes'}); await wrong; assert.equal(publishes.length,2);

$('new-sample').click(); await until(()=>ui.draft);
assert.equal(ui.prefs.mode,'graph'); assert.equal(ui.frames,null);
$('new-event-id').value='first'; $('new-event').click(); await until(()=>ui.event==='first');
$('event-content').value='hello'; $('event-content').dispatchEvent(new Event('input'));
$('close-window').checked=true; $('close-window').dispatchEvent(new Event('change'));
$('save-source').click(); await until(()=>ui.record.revision===2);
assert.equal(ui.record.source.events.first.content,'hello');
assert.ok($('window-grid').textContent.includes('EOS'));
$('new-event-id').value='second'; $('new-event').click(); await until(()=>ui.event==='second');
$('dependency-picker').value='first'; $('dependency-add').click(); await until(()=>ui.draft.events.second.after.length===1);
assert.equal($('dependencies').textContent,'first ×');
ui.draft.events.second.after=[]; ui.renderTimeline();
const firstNode=[...$('timeline').querySelectorAll('button')].find(n=>n.textContent.startsWith('first →'));
firstNode.dispatchEvent(new MouseEvent('click',{shiftKey:true})); assert.deepEqual(ui.draft.events.second.after,['first']);
$('delete-event').click(); await until(()=>!ui.draft.events.second);
// Chat uses a structured handler; no authored MSG string.
$('window-id').value='chat'; $('window-purpose').value='chatroom'; $('window-writable').checked=false; $('window-save').click();
$('new-event-id').value='chat-event'; $('new-event').click(); await until(()=>ui.event==='chat-event');
$('event-kind').value='chat_message'; $('event-kind').dispatchEvent(new Event('change'));
$('event-window').value='chat'; $('event-window').dispatchEvent(new Event('change'));
$('event-content').value='Hi'; $('event-content').dispatchEvent(new Event('input'));
$('event-apply').click(); assert.equal(ui.draft.events['chat-event'].chat.speaker,'User');
assert.ok($('timeline').querySelector('[title="Depends on: root"]'));
// Tool partial JSON survives raw/form round trips.
ui.settings.tools.echo={name:'Echo',input_schema:{type:'object',properties:{text:{type:'string'}}}};
const toolOption=document.createElement('option');toolOption.value='echo';toolOption.textContent='Echo';$('tool-id').append(toolOption);
$('event-kind').value='tool_call'; $('event-kind').dispatchEvent(new Event('change'));
$('event-content').value='{"text":'; $('content-mode').value='json'; $('event-content').dispatchEvent(new Event('input'));
$('content-mode').value='form'; $('content-mode').dispatchEvent(new Event('change')); assert.equal($('event-content').value,'{"text":');
$('content-mode').value='json'; $('content-mode').dispatchEvent(new Event('change')); assert.equal($('event-content').value,'{"text":');
// Return to the saved graph and distinguish missing split from intentional Auto.
await ui.select(ui.id,true); $('split').value='Auto'; $('split').dispatchEvent(new Event('change'));
assert.equal(ui.draft.metadata.split,'Auto'); assert.equal($('split-warning').textContent,'');
$('split').value=''; $('split').dispatchEvent(new Event('change')); assert.ok($('split-warning').textContent.includes('Missing'));
const intrinsic = ui.record.intrinsic_hash;
$('split').value='Auto'; $('split').dispatchEvent(new Event('change')); await ui.save(); assert.equal(ui.record.intrinsic_hash,intrinsic);
// Consecutive local history actions track revisions created by our own restores.
await ui.restore(true); await ui.restore(true); assert.equal(Object.keys(ui.record.source.events).length,0);
await ui.restore(false); await ui.restore(false); assert.equal(ui.record.source.events.first.content,'hello');
assert.equal(ui.record.source.metadata.split,'Auto');
// Invalid advanced JSON preserves the working source.
const saved = JSON.stringify(ui.draft); $('source-json').value='{'; $('source-apply').click(); await until(()=>$('status').textContent.includes('JSON'));
assert.equal(JSON.stringify(ui.draft),saved);
// Preview is opt-in with a separate Preferences profile, scrub works in both modes.
ui.prefs.profile='bytes'; ui.prefs.debounce=0; $('view-mode').value='ticks'; $('view-mode').dispatchEvent(new Event('change'));
await until(()=>ui.frames?.length); $('last-tick').click(); assert.equal(ui.position,ui.frames.length-1);
assert.ok($('window-grid').textContent.includes('EOS'));
assert.equal($('window-grid').textContent.includes('@available'),false);
const tickNode=$('timeline').querySelector('button'); assert.ok(tickNode.title.includes('Depends on:'));
$('first-tick').click(); assert.equal(ui.position,0);
assert.equal($('export-profile').value,'');
// Multiple independent dialogs and polling never steal selected draft/event.
const selected = ui.id, selectedEvent=ui.event; $('event-content').value='unsaved'; $('event-content').dispatchEvent(new Event('input'));
const j1=await module.api('/exports','POST',{options:{compiled:false,minimum_stage:null}});
const j2=await module.api('/exports','POST',{options:{compiled:false,minimum_stage:null}});
await ui.inspectJob(j1.job_id); await ui.inspectJob(j2.job_id); await ui.pollJobs();
assert.equal(ui.dialogs.size,2); assert.equal(ui.id,selected); assert.equal(ui.event,selectedEvent); assert.equal($('event-content').value,'unsaved');
// Reuse the existing metrics panel and frozen-search calculation job.
$('metrics-button').click(); await until(()=>$('metrics-dialog').open);
assert.ok($('metrics-values').textContent.includes('proposal_coverage'));
assert.ok($('metrics-values').textContent.includes('unavailable'));
$('metrics-calculate').click(); await until(()=>ui.dialogs.size===3);
await until(async ()=>(await module.api('/job-summaries')).some(j=>j.kind==='bulk'&&j.state==='completed'));
assert.equal($('event-content').value,'unsaved'); assert.equal(ui.id,selected);
// Object tool schema forms update a settings draft, including required fields.
$('settings-button').click(); await until(()=>$('settings-dialog').open);
const toolBox=$('tool-schema-editor'), named=name=>toolBox.querySelector(`[aria-label="${name}"]`);
named('New tool ID').value='fixture_tool';
[...toolBox.querySelectorAll('button')].find(n=>n.textContent==='Add tool to settings draft').click();
named('New tool field ID').value='payload';
[...toolBox.querySelectorAll('button')].find(n=>n.textContent==='Add tool field').click();
named('Required tool field payload').checked=true; named('Required tool field payload').dispatchEvent(new Event('change'));
assert.deepEqual(JSON.parse($('settings-json').value).tools.fixture_tool.input_schema.required,['payload']);
$('settings-save').click(); await until(()=>!$('settings-dialog').open);
assert.equal(ui.settings.tools.fixture_tool.input_schema.properties.payload.type,'string');
// CAS conflict leaves the browser draft intact.
await module.api('/trajectories/'+ui.id,'PATCH',{expected_revision:(await module.api('/trajectories/'+ui.id)).revision,patch:{metadata:{test:true}}});
await assert.rejects(()=>ui.save(),/Revision conflict/); assert.equal(ui.draft.events.first.content,'unsaved');
ui.stopped=true;clearTimeout(ui.pollTimer);clearTimeout(ui.validationTimer);ui.preview.invalidate();if(ui.playing)clearInterval(ui.playing);
assert.ok(paths.every(path => path.startsWith('/api/v1/')));
console.log('Graph DOM/API: dependency editing, graph/tick scrub, debounce fencing/cache, partial tool content, chat, JSON recovery, split separation, independent jobs and CAS drafts passed.');
window.happyDOM.abort();
