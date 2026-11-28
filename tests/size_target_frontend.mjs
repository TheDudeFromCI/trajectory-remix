/* Target size: read from the task_length metric (else the sampled proposal) and warn when the
   saved size label is above or below it. */
import assert from 'node:assert/strict';
import {sizeOffTarget, sizeWarning, targetSize} from '../trajectory_remix/static/size-target.js';
import {studioWarnings} from '../trajectory_remix/static/studio-warnings.js';

const labels = [['XS', 16], ['S', 32], ['M', 64], ['L', 128], ['XL', 256]].map(([name, k]) => ({name, total_tokens: k * 1024}));
const record = (label, events = {a: {}}) => ({source: {id: 't1', events}, size: {label, total_tokens_estimate: 30000}});
const metrics = (value, sampled) => ({id: 't1', targets: sampled ? {sampling: {task_length: sampled}} : {},
  values: {task_length: value ? {status: 'available', observation: {value}} : {status: 'unavailable'}}});

assert.deepEqual(targetSize(metrics('XL', 'M')), {name: 'XL', from: 'metric'}, 'the metric wins over the proposal');
assert.deepEqual(targetSize(metrics(null, 'M')), {name: 'M', from: 'proposal'});
assert.equal(targetSize(metrics(null)), null);
assert.equal(targetSize({values: {task_length: {status: 'stale', observation: {value: 'L'}}}}).name, 'L', 'a stale target still counts');

assert.equal(sizeOffTarget({record: record('S'), metrics: metrics('XL'), labels}).direction, 'below');
assert.equal(sizeOffTarget({record: record('XL'), metrics: metrics('S'), labels}).direction, 'above');
assert.equal(sizeOffTarget({record: record('L'), metrics: metrics('L'), labels}), null, 'on target');
assert.equal(sizeOffTarget({record: record('S', {}), metrics: metrics('XL'), labels}), null, 'an empty trajectory is not off target');
assert.equal(sizeOffTarget({record: record('S'), metrics: metrics('XXL'), labels}), null, 'a target with no size label is not compared');
assert.equal(sizeOffTarget({record: record('S'), metrics: metrics(null), labels}), null, 'no target, no warning');

const below = sizeWarning({record: record('S'), metrics: metrics('XL'), labels});
assert.equal(below.code, 'size_below_target'); assert.equal(below.action, 'metrics');
assert.match(below.message, /about 29k tokens \(S\), below its target size XL, about 256k tokens/);
assert.match(sizeWarning({record: record('XL'), metrics: metrics(null, 'S'), labels}).message, /above its target size S \(from its sampled proposal\)/);

const draft = {metadata: {split: 'Test'}};
assert.deepEqual(studioWarnings({draft, record: record('S'), metrics: metrics('XL'), sizeLabels: labels}).map(w => [w.code, w.source]), [['size_below_target', 'studio']]);
assert.deepEqual(studioWarnings({draft, record: record('S'), metrics: {...metrics('XL'), id: 'other'}, sizeLabels: labels}), [], 'another trajectory\'s metrics are ignored');
console.log('Target size: metric or proposal target, above/below/on target, empty and unknown sizes, studio warning passed.');
