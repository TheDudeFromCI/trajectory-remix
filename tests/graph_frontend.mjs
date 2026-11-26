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
// Every change saves itself; no Save button.
assert.equal($('save-source'),null); await until(()=>ui.record.revision>=2 && !ui.hasUnsavedChanges());
assert.equal(ui.record.source.events.first.content,'hello');
assert.ok($('window-grid').textContent.includes('EOS'));
$('new-event-id').value='second'; $('new-event').click(); await until(()=>ui.event==='second');
$('dependency-picker').value='first'; $('dependency-add').click(); await until(()=>ui.draft.events.second.after.length===1);
assert.equal($('dependencies').textContent,'first ×');
ui.draft.events.second.after=[]; ui.renderTimeline();
const firstNode=[...$('timeline').querySelectorAll('button')].find(n=>n.textContent.startsWith('first →'));
firstNode.dispatchEvent(new MouseEvent('click',{shiftKey:true})); assert.deepEqual(ui.draft.events.second.after,['first']);
// Renaming an event updates the dependencies that point at it.
$('dependency-picker').value='first'; $('dependency-add').click(); await until(()=>ui.draft.events.second.after.length===1);
$('event-name').value='renamed'; $('event-name').dispatchEvent(new Event('change'));
assert.equal(ui.event,'renamed'); assert.ok(!ui.draft.events.second); assert.deepEqual(ui.draft.events.renamed.after,['first']);
$('event-name').value='first'; $('event-name').dispatchEvent(new Event('change'));
assert.equal(ui.event,'renamed'); assert.equal($('event-name').value,'renamed');
$('delete-event').click(); await until(()=>!ui.draft.events.renamed);
// Chat uses a structured handler; no authored MSG string.
$('window-new').click(); $('window-id').value='chat'; $('window-purpose').value='chatroom'; $('window-writable').checked=false; $('window-form').dispatchEvent(new Event('submit',{cancelable:true}));
assert.deepEqual(ui.draft.windows.chat,{purpose:'chatroom',agent_writable:false,reasoning_effort:null,intentionally_left_open:false});
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
$('content-mode').value='form'; $('content-mode').dispatchEvent(new Event('change')); assert.equal($('event-content').value,'{"text":'); assert.equal($('event-content').hidden,false);
$('content-mode').value='json'; $('content-mode').dispatchEvent(new Event('change')); assert.equal($('event-content').value,'{"text":');
// Complete arguments swap the text box for typed fields; editing one rewrites the content.
$('tool-id').value='echo'; $('event-content').value='{"text":"hi"}'; $('event-content').dispatchEvent(new Event('input'));
$('content-mode').value='form'; $('content-mode').dispatchEvent(new Event('change'));
assert.equal($('event-content').hidden,true); const textField=$('argument-form').querySelector('[data-argument="text"]');
assert.equal(textField.value,'hi'); textField.value='bye'; textField.dispatchEvent(new Event('input'));
assert.deepEqual(JSON.parse(ui.draft.events['chat-event'].content),{text:'bye'});
// The chosen mode survives a save that re-renders the event and a trip to another event.
await ui.autoSave.flush(); ui.renderEvent(); assert.equal($('content-mode').value,'form'); assert.equal($('event-content').hidden,true);
ui.selectEvent('first'); assert.equal($('content-mode').value,'text'); ui.selectEvent('chat-event'); assert.equal($('content-mode').value,'form');
$('content-mode').value='json'; $('content-mode').dispatchEvent(new Event('change')); assert.equal($('event-content').hidden,false);
// Return to the saved graph and distinguish missing split from intentional Auto.
await ui.select(ui.id,true); $('split').value='Auto'; $('split').dispatchEvent(new Event('change'));
// Missing split is a studio warning in the Problems tab, gone as soon as a split (even Auto) is chosen.
const studioCodes=()=>[...$('diagnostics').querySelectorAll('.diagnostic')].filter(n=>n.textContent.includes('Studio')).map(n=>n.querySelector('code').textContent);
assert.equal(ui.draft.metadata.split,'Auto'); assert.deepEqual(studioCodes(),[]);
$('split').value=''; $('split').dispatchEvent(new Event('change')); assert.deepEqual(studioCodes(),['missing_split']);
assert.ok([...$('diagnostics').querySelectorAll('button')].some(b=>b.textContent==='Set split'));
const intrinsic = ui.record.intrinsic_hash;
$('split').value='Auto'; $('split').dispatchEvent(new Event('change')); await ui.autoSave.flush(); assert.equal(ui.record.intrinsic_hash,intrinsic);
// Consecutive local history actions track revisions created by our own restores.
// Every change saved itself, so Undo walks back through each one to the empty trajectory.
await ui.autoSave.flush(); const steps=ui.undo.length; assert.ok(steps>=2);
while (ui.undo.length) await ui.restore(true); assert.equal(Object.keys(ui.record.source.events).length,0);
while (ui.redo.length) await ui.restore(false); assert.equal(ui.undo.length,steps); assert.equal(ui.record.source.events.first.content,'hello');
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
// Graph mode steps one dependency level at a time and the scrub line is draggable.
{
 ui.prefs.mode='graph'; ui.position=0; ui.renderTimeline();
 const events=ui.draft.events, level={};
 for (const id of Object.keys(events)) { const walk=i=>Math.max(0,...events[i].after.map(d=>walk(d)+1)); level[id]=walk(id); }
 const top=Math.max(...Object.values(level)); assert.equal(ui.maxPosition,top);
 const shown=()=>$('timeline').querySelectorAll('.event-node:not(.future)').length;
 assert.equal(shown(),Object.values(level).filter(l=>l===0).length);
 $('next-tick').click(); assert.equal(ui.position,Math.min(1,top));
 assert.equal(shown(),Object.values(level).filter(l=>l<=ui.position).length);
 // Windows written by the step into the current position glow faintly.
 assert.ok(ui.changedWindows.size>0);
 assert.equal($('window-grid').querySelectorAll('.window-card.changed').length,[...ui.windowModels.keys()].filter(w=>ui.changedWindows.has(w)).length);
 assert.deepEqual(new Set(Object.keys(events).filter(i=>level[i]===ui.position).map(i=>events[i].window)),ui.changedWindows);
 assert.equal($('scrubber'),null); assert.ok($('timeline').querySelector('.timeline-cursor'));
 assert.equal(ui.positionAt(ui.cursorX(top)),top); assert.equal(ui.positionAt(-500),0);
 $('first-tick').click();
}
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
await ui.autoSave.flush(); ui.draft.events.first.content='unsaved';
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
assert.equal(ui.windowsPanel.selected,'a'); $('window-edit').click();
assert.equal($('window-left-open').checked,false);
$('window-left-open').checked=true; $('window-form').dispatchEvent(new Event('submit',{cancelable:true})); await ui.autoSave.flush();
assert.equal(ui.record.source.windows.a.intentionally_left_open,true);
assert.deepEqual(ui.diagnostics.filter(d=>d.code==='open_window').map(d=>d.event_id),['b']);
assert.equal(ui.record.source.events.a.close_window,false);
await ui.select(ack.source.id,true); $('window-edit').click(); assert.equal($('window-left-open').checked,true);
ui.windowsPanel.select('b'); assert.equal($('window-form').hidden,true,'choosing another window closes the form');
$('window-edit').click(); assert.equal($('window-left-open').checked,false); $('window-cancel').click();
// Deleting a window deletes its events (after a warning listing them) and saves.
$('window-delete').click(); assert.deepEqual([...$('window-delete-events').querySelectorAll('code')].map(n=>n.textContent),['b']);
$('window-delete-confirm').click(); assert.deepEqual(Object.keys(ui.draft.events),['a']); assert.ok(!ui.draft.windows.b);
await ui.autoSave.flush(); assert.ok(!ui.record.source.windows.b && !ui.record.source.events.b);
await ui.restore(true); assert.ok(ui.record.source.windows.b && ui.record.source.events.b);
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
ui.maxPosition=1; ui.position=0; $('play').click();
await until(()=>!ui.playing); assert.equal(ui.position,1); assert.equal($('play').textContent,'Play');
$('play').click(); assert.equal(ui.position,0); assert.equal($('play').textContent,'Pause'); $('play').click();
ui.stopped=true;clearTimeout(ui.pollTimer);clearTimeout(ui.validationTimer);ui.preview.invalidate();if(ui.playing)clearInterval(ui.playing);
assert.ok(paths.every(path => path.startsWith('/api/v1/')));
console.log('Graph DOM/API: dependency editing, graph/tick scrub, debounce fencing/cache, partial tool content, chat, JSON recovery, split separation, independent jobs and CAS drafts passed.');
window.happyDOM.abort();
