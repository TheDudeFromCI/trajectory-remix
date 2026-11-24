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
for (const key of ['document', 'localStorage', 'HTMLElement', 'Event', 'MouseEvent', 'CustomEvent']) globalThis[key] = window[key];
const nativeFetch = globalThis.fetch;
const paths = [];
globalThis.fetch = (path, init) => { paths.push(String(path)); return nativeFetch(new URL(path, base), init); };
// graph.js is loaded from a data: URL, so resolve its relative module imports to files.
const code = (await fs.readFile(new URL('../trajectory_remix/static/graph.js', import.meta.url), 'utf8')).replace(/'\.\/([\w-]+\.js)'/g, (_, name) => JSON.stringify(new URL('../trajectory_remix/static/' + name, import.meta.url).href));
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
// NOOP runs fold into one chip; the readable view, Raw toggle and pop-out show the same window.
for (const chip of $('window-grid').querySelectorAll('.noop')) assert.equal(chip.nextElementSibling?.classList.contains('noop') ?? false,false);
const firstWindow=[...ui.windowModels.keys()][0];
ui.openPopout(firstWindow); assert.equal($('window-popout').hidden,false);
assert.equal($('window-popout-body').querySelector('h2').textContent,firstWindow);
$('window-popout-body').querySelector('.window-shrink').click(); assert.equal($('window-popout').hidden,true);
$('window-grid').querySelector('.window-raw').click();
assert.equal($('window-grid').querySelector('.window-raw').getAttribute('aria-pressed'),'true');
assert.ok($('window-grid').querySelector('.window-tokens'));
$('window-grid').querySelector('.window-raw').click();
const tickNode=$('timeline').querySelector('button'); assert.ok(tickNode.title.includes('Depends on:'));
$('first-tick').click(); assert.equal(ui.position,0);
// Zoom slider rescales the timeline horizontally and persists.
const zoomWidth=()=>parseFloat($('timeline').querySelector('.timeline-lanes').style.width);
const baseWidth=zoomWidth(); $('timeline-zoom').value='2'; $('timeline-zoom').dispatchEvent(new Event('input'));
assert.equal(ui.prefs.zoom,2); assert.ok(zoomWidth()>baseWidth);
$('timeline-zoom').value='1'; $('timeline-zoom').dispatchEvent(new Event('input')); assert.equal(zoomWidth(),baseWidth);
assert.equal($('export-profile').value,'');
// Multiple independent dialogs and polling never steal selected draft/event.
const selected = ui.id, selectedEvent=ui.event; $('event-content').value='unsaved'; $('event-content').dispatchEvent(new Event('input'));
const j1=await module.api('/exports','POST',{options:{compiled:false,minimum_stage:null}});
const j2=await module.api('/exports','POST',{options:{compiled:false,minimum_stage:null}});
await ui.inspectJob(j1.job_id); await ui.inspectJob(j2.job_id); await ui.pollJobs();
assert.equal(ui.dialogs.size,2); assert.equal(ui.id,selected); assert.equal(ui.event,selectedEvent); assert.equal($('event-content').value,'unsaved');
// Reuse the existing metrics panel and frozen-search calculation job.
$('metrics-button').click(); await until(()=>!$('metrics-view').hidden && $('metrics-values').textContent);
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
// Per-window acknowledgment persists, suppresses only its advisory, and is undoable.
await until(()=>[...ui.ownedBulk].every(id=>ui.recorded.has(id)));
const ack = await module.api('/trajectories', 'POST', {format:'kidra-trajectory-graph/1',id:crypto.randomUUID(),
 windows:{a:{purpose:'reasoning',agent_writable:true},b:{purpose:'tool',agent_writable:true}},
 events:{a:{kind:'agent_text',window:'a',content:'open'},b:{kind:'agent_text',window:'b',content:'open'}}});
await ui.select(ack.source.id);
// Switching to a source with other windows must not leave a stale dirty badge.
assert.equal(ui.isDirty(),false); assert.match($('dirty-indicator').textContent,/^Saved/);
assert.equal(ui.diagnostics.filter(d=>d.code==='open_window').length,2);
$('window-id').value='a'; $('window-id').dispatchEvent(new Event('change'));
assert.equal($('window-left-open').checked,false);
$('window-left-open').checked=true; $('window-save').click(); await ui.save();
assert.equal(ui.record.source.windows.a.intentionally_left_open,true);
assert.deepEqual(ui.diagnostics.filter(d=>d.code==='open_window').map(d=>d.event_id),['b']);
assert.equal(ui.record.source.events.a.close_window,false);
await ui.select(ack.source.id,true); assert.equal($('window-left-open').checked,true);
$('window-id').value='b'; $('window-id').dispatchEvent(new Event('change'));
assert.equal($('window-left-open').checked,false);
await ui.restore(true); assert.equal(ui.record.source.windows.a.intentionally_left_open ?? false,false);
await ui.restore(false); assert.equal(ui.record.source.windows.a.intentionally_left_open,true);
// Clicking a window header jumps the event panel to the latest event that edited it (buttons excluded).
const headModel={tokens:[{control:'ctx_begin'},{text:'x',event:'a'},{text:'y',event:'b'}],closed:false};
let head=ui.windowCard('a',headModel).querySelector('.window-card-head'); head.click(); assert.equal(ui.event,'b');
ui.selectEvent('a'); head.querySelector('button').click(); assert.equal(ui.event,'a');
head=ui.windowCard('a',{tokens:[{text:'x',event:'a'},{text:'y',event:'a:echo'},{text:'z'}],closed:false}).querySelector('.window-card-head'); head.click(); assert.equal(ui.event,'a');
// Deleting the selected trajectory must clear the event panel immediately.
await ui.select(ack.source.id); ui.selectEvent('a'); assert.notEqual($('event-content').value,'');
globalThis.confirm=()=>true; window.confirm=()=>true;
$('delete-sample').click(); await until(()=>ui.id===null);
assert.equal($('event-content').value,''); assert.equal($('event-picker').value,'');
assert.ok($('event-editor-panel').classList.contains('no-event'));
// Playback does not loop: it pauses on the last tick, and Play at the end restarts from 0.
$('scrubber').max=1; ui.position=0; $('play').click();
await until(()=>!ui.playing); assert.equal(ui.position,1); assert.equal($('play').textContent,'Play');
$('play').click(); assert.equal(ui.position,0); assert.equal($('play').textContent,'Pause'); $('play').click();
ui.stopped=true;clearTimeout(ui.pollTimer);clearTimeout(ui.validationTimer);ui.preview.invalidate();if(ui.playing)clearInterval(ui.playing);
assert.ok(paths.every(path => path.startsWith('/api/v1/')));
console.log('Graph DOM/API: dependency editing, graph/tick scrub, debounce fencing/cache, partial tool content, chat, JSON recovery, split separation, independent jobs and CAS drafts passed.');
window.happyDOM.abort();
