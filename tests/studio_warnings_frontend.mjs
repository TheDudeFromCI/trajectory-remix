/* Studio warnings: missing split, stale review or signoff, stale metrics; nothing when all is well. */
import assert from 'node:assert/strict';
import {studioWarnings} from '../trajectory_remix/static/studio-warnings.js';

const record = (review_status) => ({source: {id: 't1'}, review_status});
const metrics = (values) => ({id: 't1', values});
const codes = args => studioWarnings(args).map(w => w.code);

assert.deepEqual(studioWarnings({draft: null, record: null}), []);
assert.deepEqual(codes({draft: {metadata: {split: 'Train'}}, record: record({stale: false})}), []);

const split = studioWarnings({draft: {metadata: {}}, record: record()});
assert.deepEqual(split.map(w => [w.code, w.severity, w.source, w.action]), [['missing_split', 'warning', 'studio', 'split']]);
assert.deepEqual(codes({draft: {metadata: {split: 'Auto'}}, record: record()}), [], 'Auto opts in without a warning');

const review = studioWarnings({draft: {metadata: {split: 'Test'}}, record: record({stale: true, reviewed: true, reasons: ['source_changed']})});
assert.deepEqual(review.map(w => w.code), ['review_stale']); assert.match(review[0].message, /source changed/);
assert.deepEqual(codes({draft: {metadata: {split: 'Test'}}, record: record({stale: true, reviewed: false, reasons: ['draft_changed']})}), ['signoff_stale']);

const values = {lifecycle: {status: 'stale', definition: {}}, domain: {status: 'stale', definition: {label: 'Domain'}}, stage: {status: 'available', definition: {}}};
const stale = studioWarnings({draft: {metadata: {split: 'Test'}}, record: record(), metrics: metrics(values)});
assert.deepEqual(stale.map(w => [w.code, w.action]), [['stale_metrics', 'metrics']]);
assert.match(stale[0].message, /^2 metric values were recorded .*lifecycle, Domain/);
assert.deepEqual(codes({draft: {metadata: {split: 'Test'}}, record: record(), metrics: {id: 'other', values}}), [], 'metrics of another trajectory are ignored');
console.log('Studio warnings: missing split, Auto opt-in, stale review/signoff, stale metrics and quiet when clean passed.');
