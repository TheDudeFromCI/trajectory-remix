/* A few deterministic proposals; no runs, generation, tokenizers or benchmarks. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', args:['--no-sandbox']});
const page = await browser.newPage({viewport:{width:1600,height:1500}});
const errors = [], requests = [];
page.on('pageerror', e => errors.push(e.message));
page.on('request', r => { if(r.url().includes('/api/')) requests.push(r.url()); });
const base = process.argv[2];
const screenshot = async name => {
  if (!process.env.SAMPLER_SCREENSHOTS) return;
  await fs.mkdir(process.env.SAMPLER_SCREENSHOTS, {recursive:true});
  await page.screenshot({path: `${process.env.SAMPLER_SCREENSHOTS}/${name}.png`});
};
const ready = () => page.waitForFunction(() => document.querySelector('#sampler-workspace').getAttribute('aria-busy') === 'false');
const snapshot = () => page.locator('#sampler-raw').textContent().then(JSON.parse);
const api = (path, method='GET', body) => page.evaluate(async ({path,method,body}) => {
  const response = await fetch('/api/v1'+path, {method,headers:{'X-Remix-Token':document.querySelector('meta[name=remix-token]').content,'Content-Type':'application/json'}, ...(body===undefined?{}:{body:JSON.stringify(body)})});
  const data = await response.json(); if(!response.ok) throw new Error(JSON.stringify(data)); return data;
}, {path,method,body});
try {
  await page.goto(base);
  await page.getByRole('button',{name:'Sampler Matrix',exact:true}).click();
  await page.locator('.sampler-hero').waitFor(); await ready();
  const initial = await snapshot();
  assert.equal(initial.proposal.sampling_provenance.seed,137);
  assert.match(await page.locator('#sampler-brief').innerText(), /WHEN THIS IS THE RIGHT DECISION/);
  await screenshot('scenario-brief');

  await page.locator('.sampler-learning').getByRole('button',{name:initial.proposal.learning_objective.primary_behavior.replaceAll('_',' '),exact:true}).click();
  assert.equal(JSON.parse(await page.locator('#sampler-field').inputValue()).id,initial.proposal.learning_objective.primary_behavior);
  assert.match(await page.locator('#sampler-inspector').innerText(),/Eligible lifecycle/);
  await screenshot('competency-constraints');

  // The attribute opens its actual controlling matrix object, with sibling weights.
  await page.locator('.sampler-hero').getByRole('button',{name:initial.proposal.scenario.domain.id.replaceAll('_',' '),exact:true}).click();
  const domain = initial.proposal.scenario.domain.id;
  const originalOption = JSON.parse(await page.locator('#sampler-field').inputValue());
  assert.equal(originalOption.id,domain);
  await page.getByLabel('Relative project weight',{exact:true}).fill('0');
  assert.equal(JSON.parse(await page.locator('#sampler-field').inputValue()).weight,0);
  const edited = {...originalOption, weight:0, custom_review_note:'preserved custom option data'};
  await page.locator('#sampler-field').fill(JSON.stringify(edited,null,2));
  await page.getByRole('button',{name:'Apply field edit',exact:true}).click();
  assert.equal(await page.locator('#sampler-preview').isDisabled(),true);
  await screenshot('attribute-settings');
  await page.locator('#sampler-save').click(); await ready();
  await page.locator('#sampler-comparison h3').waitFor();
  const changed = await snapshot();
  assert.notEqual(changed.proposal.scenario.domain.id,domain);
  assert.equal(changed.proposal.sampling_provenance.seed,initial.proposal.sampling_provenance.seed);
  assert.deepEqual(changed.proposal.sampling_provenance.requested_overrides,initial.proposal.sampling_provenance.requested_overrides);
  assert.match(await page.locator('#sampler-comparison').innerText(), /sampling \/ domain/);
  assert.deepEqual(await api('/sampler/proposals/'+initial.id),initial); // historical snapshot unchanged
  assert.equal((await api('/sampler')).configuration.matrix.domains.find(x=>x.id===domain).custom_review_note,edited.custom_review_note);
  await page.locator('#sampler-workspace').evaluate(n=>n.scrollTop=n.scrollHeight);
  await screenshot('fixed-seed-comparison');

  // Index reaches custom/rare fields outside the current scenario, with a lossless round trip.
  await page.locator('#sampler-search').fill('custom_review_note');
  await page.locator('.sampler-index-row').click();
  assert.equal(JSON.parse(await page.locator('#sampler-field').inputValue()),edited.custom_review_note);
  await page.locator('#sampler-search').fill('hard_implications');
  assert.ok(await page.locator('.sampler-index-row').count()>0);
  await page.locator('#sampler-search').fill('metric_options');
  await page.locator('.sampler-index-row').click();
  await page.locator('#sampler-field').fill(JSON.stringify({custom_scenario_note:{label:'Custom observation',unit:'rating',tolerance:{absolute:0,relative:.05}}}));
  await page.getByRole('button',{name:'Apply field edit',exact:true}).click();
  await page.locator('#sampler-save').click(); await ready();
  await page.locator('#sampler-reload').click();
  await page.waitForFunction(()=>document.querySelector('#sampler-dirty').textContent.includes('revision 3'));
  const saved = await api('/sampler');
  assert.equal(saved.configuration.metric_options.custom_scenario_note.unit,'rating');
  assert.equal(saved.configuration.matrix.domains.find(x=>x.id===domain).custom_review_note,edited.custom_review_note);

  // Same seed reproduces the payload; a supported pin is separate from project configuration.
  const digest = saved.configuration.matrix_digest;
  await page.locator('#sampler-preview').click(); await ready();
  const a = await snapshot();
  await page.locator('#sampler-preview').click(); await ready();
  assert.deepEqual((await snapshot()).proposal,a.proposal);
  await page.locator('.sampler-pins summary').click();
  await page.getByLabel('Preview pin domain',{exact:true}).selectOption('software_engineering');
  assert.match(await page.locator('#sampler-preview-label').innerText(),/Previous outcome/);
  await page.locator('#sampler-preview').click(); await ready();
  assert.equal((await snapshot()).proposal.scenario.domain.id,'software_engineering');
  assert.equal((await api('/sampler')).configuration.matrix_digest,digest);
  await page.locator('#sampler-vary').click(); await ready();
  assert.equal((await snapshot()).proposal.sampling_provenance.seed,138);

  // Incompatible pins surface the backend error and retain the last successful outcome.
  for (const [key,value] of Object.entries({'focus.family':'strategic_planning',lifecycle:'closure',complexity:'trivial'})) await page.getByLabel('Preview pin '+key,{exact:true}).selectOption(value);
  const last = await snapshot();
  await page.locator('#sampler-preview').click(); await ready();
  await page.waitForFunction(()=>/infeasible|incompatible|No |conflict/i.test(document.querySelector('#sampler-message').textContent));
  assert.deepEqual(await snapshot(),last);

  // A revision conflict preserves the local draft and exposes the error.
  await api('/sampler','PUT',{expected_revision:saved.settings_revision,matrix:saved.configuration.matrix,metric_options:saved.configuration.metric_options});
  await page.locator('#sampler-search').fill('custom_review_note'); await page.locator('.sampler-index-row').click();
  await page.locator('#sampler-field').fill('"retained conflict draft"'); await page.getByRole('button',{name:'Apply field edit',exact:true}).click();
  await page.locator('#sampler-save').click(); await ready();
  await page.waitForFunction(()=>/revision|conflict/i.test(document.querySelector('#sampler-message').textContent));
  assert.equal(JSON.parse(await page.locator('#sampler-field').inputValue()),'retained conflict draft');
  assert.match(await page.locator('#sampler-dirty').innerText(),/Unsaved/);

  // Small viewport remains navigable, and the existing studio/metrics surfaces still work.
  await page.setViewportSize({width:720,height:1000});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
  await page.getByRole('button',{name:'Trajectory Studio',exact:true}).click();
  assert.equal(await page.locator('#workspace').isVisible(),true);
  await page.getByRole('button',{name:'Dataset Metrics',exact:true}).click();
  await page.locator('#metrics-dialog[open]').waitFor();
  assert.deepEqual(errors,[]);
  assert.equal(requests.some(p=>/\/preview$|\/sampler\/runs|\/agent\/|\/compile/.test(p)),false);
  console.log('Scenario browser checks passed: links, edits, fixed seed, pins, variation, custom settings, conflicts, isolation, responsive layout, metrics.');
} finally { await browser.close(); }
