#!/usr/bin/env python3
"""Compute observed metrics and deterministic audits; no sample rewriting or approval."""
import json
import math
import statistics
from datetime import datetime
from pathlib import Path

from tokenizers import Tokenizer
from trajectory_remix.compiler import Compiler
from trajectory_remix.training import query_batch

WORK = Path(__file__).resolve().parent


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def category(path):
    if path.startswith('validate'):
        return 'repair' if '.repair' in path else 'review_or_review_retry'
    if path == 'idea': return 'world_generation'
    if path == 'novelty': return 'novelty'
    if path == 'solution': return 'reference_solution'
    if path.endswith('.user'): return 'user_content'
    if path.endswith(('.tools', '.decisions')): return 'model_tool_decisions'
    if path.endswith('.assess'): return 'conversation_assessment'
    return 'reasoning_or_reply'


def main():
    results = json.loads((WORK / 'results.json').read_text())
    compiler = Compiler(Tokenizer.from_file(str(WORK / 'training-tokenizer.json')))
    audited = []
    for result in results:
        folder = WORK / 'runs' / result['run_id']
        sample = json.loads((folder / 'trajectory-1.json').read_text())
        plan = compiler.compile(sample)
        job = json.loads((folder / 'generation-job.json').read_text())
        records = [json.loads(p.read_text()) for p in sorted((WORK / 'mailboxes' / result['route']).glob('*.record.json'))]
        metrics = job['request_metrics']
        assert len(records) == len(metrics) == result['model_requests']
        counts = {}
        for entry in metrics:
            key = category(entry['path']); counts[key] = counts.get(key, 0) + 1
        draft_times = [datetime.fromisoformat(log['time']).timestamp() for log in job['logs']
                       if (log.get('step') or '').endswith('.draft') and log['message'].endswith(': complete')]
        review_records = [record for record, metric in zip(records, metrics) if category(metric['path']) == 'review_or_review_retry']
        targets = [r for r in plan['rows'] if r['loss_weight'] > 0]
        selected = targets[::max(1, len(targets)//100)]
        batch = query_batch(plan, selected)
        memory = [r for r in plan['rows'] if r['durable']]
        future = sum(1 for q, mask in zip(selected, batch['attention_allowed']) for k, allowed in zip(memory, mask)
                     if allowed and (k['tick'] > q['tick'] or k['tick'] == q['tick'] and k['phase'] == 'D'))
        visible_calls = [c for c in plan['tool_calls'] if not c.get('invisible')]
        req_masks = []
        for call in visible_calls:
            rows = [r for r in plan['rows'] if r['event'] == call['path'] + '.request']
            req_masks.append(bool(rows) and all(r['loss_weight'] > 0 for r in rows))
        phases_b = [r for r in plan['rows'] if r['phase'] == 'B']
        checks = {'compiles': True, 'all_windows_closed': all(w['close'] is not None for w in plan['windows']),
                  'observations_masked': all(r['loss_weight'] == 0 for r in phases_b),
                  'visible_tool_requests_supervised': all(req_masks), 'sampled_visibility_queries': len(selected),
                  'sampled_future_or_same_tick_generated_keys': future, 'compiler_diagnostics': plan['diagnostics'],
                  'bos_present': any(r['token_id'] == compiler.ids['bos'] for r in plan['rows']),
                  'eos_present': any(r['token_id'] == compiler.ids['eos'] for r in plan['rows']),
                  'noop_present': any(r['token_id'] == compiler.ids['noop'] for r in plan['rows'])}
        all_deterministic = all(checks[k] for k in ('compiles', 'all_windows_closed', 'observations_masked', 'visible_tool_requests_supervised', 'bos_present', 'eos_present')) and future == 0
        summary = {**result, 'request_counts_by_category': counts,
                   'first_draft_seconds': min(draft_times) - result['started_at'],
                   'final_draft_seconds': max(draft_times) - result['started_at'],
                   'pre_review_seconds': review_records[0]['received_at'] - result['started_at'] if review_records else result['elapsed_seconds'],
                   'proxy_turnaround_seconds': sum(r['turnaround_seconds'] for r in records),
                   'proxy_queue_wait_seconds': sum(max(0, (r.get('worker_claim') or {}).get('claimed_at', r['received_at']) - r['received_at']) for r in records),
                   'prompt_tokens_surrogate': sum(r['usage']['prompt_tokens'] for r in records),
                   'completion_tokens_surrogate': sum(r['usage']['completion_tokens'] for r in records),
                   'reasoning_tokens_actual': None, 'windows': len(plan['windows']), 'training_rows': len(plan['rows']),
                   'visible_tool_calls': len(visible_calls), 'tool_names': [c['tool'] for c in visible_calls],
                   'user_turns': sum(1 for es in sample['replay'].values() for e in es if e['op'] == 'append' and e.get('source') == 'user'),
                   'deterministic_checks_passed': all_deterministic, 'checks': checks}
        audited.append(summary)
        save(folder / 'audit.json', checks)
    groups = {}
    for mode in ('old','new'):
        group = [r for r in audited if r['run_id'].endswith(mode)]
        groups[mode] = {'cases': len(group), 'retained': sum(r['sample_count'] for r in group),
                        'generation_drafts_compiled': sum(r['deterministic_checks_passed'] for r in group),
                        'workflow_complete': sum(r['phase'] == 'complete' for r in group),
                        'evaluation_failed': sum(r['phase'] == 'evaluation_failed' for r in group),
                        'requests': sum(r['model_requests'] for r in group),
                        'mean_final_draft_seconds': statistics.mean(r['final_draft_seconds'] for r in group),
                        'mean_end_to_end_seconds': statistics.mean(r['elapsed_seconds'] for r in group),
                        'sum_end_to_end_seconds': sum(r['elapsed_seconds'] for r in group),
                        'mean_pre_review_seconds': statistics.mean(r['pre_review_seconds'] for r in group),
                        'prompt_tokens_surrogate': sum(r['prompt_tokens_surrogate'] for r in group),
                        'completion_tokens_surrogate': sum(r['completion_tokens_surrogate'] for r in group)}
    savings = {'request_fraction': 1-groups['new']['requests']/groups['old']['requests'],
               'mean_final_draft_time_fraction': 1-groups['new']['mean_final_draft_seconds']/groups['old']['mean_final_draft_seconds'],
               'mean_end_to_end_time_fraction': 1-groups['new']['mean_end_to_end_seconds']/groups['old']['mean_end_to_end_seconds'],
               'surrogate_total_tokens_fraction': 1-(groups['new']['prompt_tokens_surrogate']+groups['new']['completion_tokens_surrogate'])/(groups['old']['prompt_tokens_surrogate']+groups['old']['completion_tokens_surrogate'])}
    arithmetic = {'pottery_balance': 40-15, 'campaign_rates': {'A_signups':48/400, 'B_signups':66/400, 'A_complaints':2/400, 'B_complaints':8/400},
                  'campaign_optimum': max((a*.12+b*.165,a,b,a*.005+b*.02) for b in range(0,1201,100) for a in [1200-b] if a*.005+b*.02<=12),
                  'survey_margin_144':100/math.sqrt(144), 'survey_margin_400':100/math.sqrt(400), 'survey_random_target_cost':400*4,
                  'new_physics': {'bottom_energy':.5*20*3**2+20*9.81*.6-8*5,
                                  'bottom_speed':math.sqrt(2*(.5*20*3**2+20*9.81*.6-8*5)/20),
                                  'small_stop':167.72/128,'big_stop':167.72/158},
                  'old_physics': [{'ramp':L,'force':784*.6/L+.04*784*math.sqrt(1-(.6/L)**2),
                                    'work':(784*.6/L+.04*784*math.sqrt(1-(.6/L)**2))*L} for L in (3,4)]}
    save(WORK / 'metrics.json', {'runs':audited, 'groups':groups, 'savings':savings,
                                 'usage_source':'declared surrogate BPE; not actual agent billing/reasoning usage', 'independent_arithmetic':arithmetic})
    print(json.dumps({'groups':groups,'savings':savings,'cases':[{k:r[k] for k in ('run_id','model_requests','first_draft_seconds','final_draft_seconds','elapsed_seconds','request_counts_by_category')} for r in audited]},indent=2))


if __name__ == '__main__':
    main()
