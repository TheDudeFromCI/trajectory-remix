#!/usr/bin/env python3
"""Six isolated projects: three paired conditions, old/new pipelines, one worker each."""
import asyncio
import copy
import hashlib
import json
import random
import shutil
import subprocess
import time
from pathlib import Path

from tokenizers import Tokenizer, models, pre_tokenizers, decoders, trainers
from transformers import PreTrainedTokenizerFast
from trajectory_remix.compiler import Compiler
from trajectory_remix.scenarios import ScenarioRegistry
from trajectory_remix.server import create_app
from trajectory_remix.storage import DatasetStore
from trajectory_remix.tools import default_registry

ROOT = Path('/workspace/trajectory-remix')
WORK = ROOT / 'work/six-agent-experiment'
SCENARIOS = ROOT / 'examples/scenarios'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def prepare():
    if (WORK / 'plan.json').exists():
        raise ValueError('Existing experiment is immutable; use a fresh directory')
    backend = Tokenizer(models.BPE(unk_token='[UNK]'))
    backend.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    backend.decoder = decoders.ByteLevel()
    corpus = [p.read_text() for p in sorted(SCENARIOS.rglob('*')) if p.suffix in ('.md', '.yaml')]
    corpus += [json.dumps(default_registry().schemas()), 'reasoning response resolved next_exchange observations history request']
    backend.train_from_iterator(corpus, trainers.BpeTrainer(vocab_size=8192, special_tokens=['[UNK]']))
    inference = PreTrainedTokenizerFast(tokenizer_object=backend)
    inference.chat_template = "{% for m in messages %}{{ m['role'] }}: {{ m['content'] }}\n{% endfor %}{% if add_generation_prompt %}assistant: {% endif %}"
    inference.save_pretrained(WORK / 'inference-tokenizer')
    training = Tokenizer.from_str(backend.to_str())
    training.add_special_tokens(['<|bos|>', '<|eos|>', '<|noop|>'])
    training.save(str(WORK / 'training-tokenizer.json'))
    source = ScenarioRegistry(SCENARIOS).scenarios['user_problem'].flow
    pools = {'scenario': source.tags, **{role: profile.tags for role, profile in source.roles.items()}}
    runs = []
    routes = ['kestrel', 'marble', 'cobalt', 'willow', 'amber', 'cedar']
    rng = random.Random(87193)
    rng.shuffle(routes)
    for pair, seed in enumerate([1471, 2353, 3149], 1):
        chosen_rng = random.Random(seed)
        tags = {scope: {dimension: chosen_rng.choices(options, weights=[v.weight for v in options])[0].name
                        for dimension, options in dimensions.items()} for scope, dimensions in pools.items()}
        for scenario in ['user_problem', 'user_scenario_noval']:
            route = routes.pop()
            mailbox = WORK / 'mailboxes' / route
            mailbox.mkdir(parents=True)
            shutil.copy2(WORK / 'inference-tokenizer/tokenizer.json', mailbox / 'tokenizer.json')
            runs.append({'run_id': f'pair{pair}-' + ('old' if scenario == 'user_problem' else 'new'),
                         'scenario': scenario, 'route': route, 'seed': seed, 'tags': tags})
    save(WORK / 'plan.json', {'created_at': time.time(), 'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), 'runs': runs,
                             'context_budget': 8192, 'completion_cap': 1024, 'sampling': 'paired random tag selections; independently authored worlds',
                             'usage_source': 'surrogate BPE; real worker reasoning/billing unavailable'})
    save(WORK / 'review-rubric.json', {'frozen_before_execution': True, 'dimensions': {
        'task_correctness': '0-4: satisfies original request and constraints; verify calculations independently',
        'grounding': '0-4: claims match actual tool evidence and supplied world, no invented results',
        'training_semantics': '0-4: causal visibility, generated tool requests, external masks and lifecycle',
        'coherence': '0-4: user traits, dialogue continuity, private reasoning vs reply, relevant actions'},
        'critical_failure': 'Wrong consequential answer, fabricated action/evidence, future leakage, or structurally unusable trajectory',
        'approval': 'No sample is granted Ready by this experiment'})
    print(json.dumps({'mailboxes': [run['route'] for run in runs], 'pairs': [runs[i]['tags'] for i in (0, 2, 4)]}, indent=2))


async def run_one(spec):
    folder = WORK / 'runs' / spec['run_id']
    folder.mkdir(parents=True, exist_ok=True)
    compiler = Compiler(Tokenizer.from_file(str(WORK / 'training-tokenizer.json')))
    project = folder / 'dataset.sqlite'
    DatasetStore.initialize(project, compiler)
    app = create_app(project, scenarios_dir=SCENARIOS)
    runner = app.state.scenario_runner
    config = copy.deepcopy(app.state.dataset['config'])
    for endpoint in config['endpoints']:
        endpoint.update(base_url='http://127.0.0.1:8791/v1', model=spec['route'], inference_tokenizer=str(WORK / 'inference-tokenizer'),
                        context_size=8192, max_tokens=1024, temperature=.8, timeout=600, api_key_env='REMIX_EMULATOR_UNUSED')
    config['single_generation'] = {'max_attempts': 1, 'max_parallel': 1, 'api_retries': 0}
    config['content_first'] = {'context_budget': 8192, 'reserve_tokens': 256}
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    started = time.time()
    begin = time.monotonic()
    runner.start(spec['scenario'], tags=spec['tags'], max_parallel=1, batch_size=1)
    try:
        await asyncio.wait_for(runner.job, 2400)
    except Exception as error:
        save(folder / 'harness-error.json', {'type': type(error).__name__, 'error': str(error)})
    elapsed = time.monotonic() - begin
    samples = list(app.state.dataset['samples'])
    for index, sample in enumerate(samples):
        save(folder / f'trajectory-{index+1}.json', sample)
        try:
            plan = compiler.compile(sample)
            save(folder / f'compiled-summary-{index+1}.json', {key: plan[key] for key in ('windows', 'events', 'tool_calls', 'diagnostics', 'max_tick')})
            save(folder / f'final-view-{index+1}.json', compiler.frame(plan, plan['max_tick']))
        except Exception as error:
            save(folder / f'compile-error-{index+1}.json', {'error': str(error)})
    save(folder / 'generation-job.json', runner.snapshot)
    result = {**spec, 'started_at': started, 'finished_at': time.time(), 'elapsed_seconds': elapsed,
              'phase': runner.status['phase'], 'sample_count': len(samples), 'model_requests': runner.status.get('model_requests'),
              'last_error': runner.status.get('last_error'), 'validation_levels': [s.get('metadata', {}).get('validation_level') for s in samples]}
    save(folder / 'result.json', result)
    save(WORK / 'mailboxes' / spec['route'] / 'terminal.json', {'done': True})
    print(json.dumps({'finished': spec['run_id'], 'phase': result['phase'], 'seconds': elapsed, 'requests': result['model_requests']}), flush=True)
    return result


async def execute():
    plan = json.loads((WORK / 'plan.json').read_text())
    deadline = time.monotonic() + 600
    while not all((WORK / 'mailboxes' / run['route'] / 'ready.json').exists() for run in plan['runs']):
        if time.monotonic() > deadline:
            raise TimeoutError('Workers did not become ready')
        await asyncio.sleep(.25)
    save(WORK / 'start.json', {'started_at': time.time()})
    results = await asyncio.gather(*(run_one(spec) for spec in plan['runs']))
    save(WORK / 'results.json', results)


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'prepare':
        prepare()
    else:
        asyncio.run(execute())
