#!/usr/bin/env python3
"""Reproducible offline architecture comparison or a real local-endpoint run.

Offline counters use a fixture tokenizer and simulated inference delays, NEVER
hardware performance claims. Neither mode approves samples for training.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import random
from pathlib import Path

from tokenizers import Tokenizer

from trajectory_remix.compiler import Compiler
from trajectory_remix.content_first import dataset_generation_metrics, inference_tokenizer
from trajectory_remix.server import create_app
from trajectory_remix.storage import DatasetStore

ROOT = Path(__file__).resolve().parents[1]


class FixtureProvider:
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    async def complete(self, endpoint, messages, tools=None):
        prompt = messages[0]['content']
        if prompt.startswith('Create a fictional'):
            output = {'problem': 'An invoice claims 2 + 2 is 5. Check invoice.txt and the sum.',
                      'situation': 'The invoice may be missing.', 'success_criteria': 'Calculate 4 and report any missing evidence.', 'files': []}
        elif prompt.startswith('Analyze'):
            output = 'The arithmetic needs verification.'
        elif prompt.startswith('Independently'):
            output = 'The invoice may be missing; do not assume its contents.'
        elif prompt.startswith('Choose useful'):
            output = {'calls': [{'tool': 'calculator', 'arguments': {'expression': '2 + 2'}},
                                {'tool': 'file_read', 'arguments': {'path': 'invoice.txt'}}]}
        elif prompt.startswith('Integrate actual'):
            assert 'false' in messages[1]['content']
            output = {'reasoning': 'Calculator confirms 4. The file could not be read.',
                      'response': '2 + 2 = 4. I could not read invoice.txt, so its contents remain unverified.'}
        elif prompt.startswith('Validate'):
            output = {'valid': True, 'feedback': []}
        else:
            raise ValueError('Unknown fixture prompt')
        text = output if isinstance(output, str) else json.dumps(output)
        prompt_count = len(self.tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_dict=False))
        output_count = len(self.tokenizer.encode(text, add_special_tokens=False))
        # Explicit simulation: 10ms request latency, 100k prompt tokens/s and
        # 5k generated tokens/s. This is NOT a measurement of a real model.
        await asyncio.sleep(.01 + prompt_count / 100_000 + output_count / 5_000)
        return {'content': text, '_usage': {'prompt_tokens': prompt_count, 'completion_tokens': output_count,
                                           'total_tokens': prompt_count + output_count}}


async def benchmark(args):
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    tokenizer_path = args.inference_tokenizer
    if not args.live:
        from transformers import PreTrainedTokenizerFast
        fixture = PreTrainedTokenizerFast(tokenizer_object=Tokenizer.from_file(str(ROOT / 'examples/demo-tokenizer.json')))
        fixture.chat_template = "{% for m in messages %}{{ m['role'] }}: {{ m['content'] }}\n{% endfor %}{% if add_generation_prompt %}assistant: {% endif %}"
        tokenizer_path = str(output / 'fixture-tokenizer')
        fixture.save_pretrained(tokenizer_path)
    elif not tokenizer_path or not args.base_url or not args.model:
        raise ValueError('--live requires --base-url, --model, and --inference-tokenizer')
    counter = inference_tokenizer(tokenizer_path)
    report = {'measurement': 'live-endpoint' if args.live else 'offline-fixture-simulated-inference',
              'seed': args.seed, 'samples_per_mode': args.samples,
              'scenario': 'content_first', 'context_budget': args.context_budget, 'modes': {}}
    for mode in ('existing', 'content-first'):
        random.seed(args.seed)
        project = output / (mode + '.sqlite')
        if project.exists():
            raise ValueError(f'Refusing to overwrite {project}; select a new --output-dir')
        compiler = Compiler(Tokenizer.from_file(str(ROOT / 'examples/demo-tokenizer.json')))
        DatasetStore.initialize(project, compiler)
        app = create_app(project, scenarios_dir=ROOT / 'examples/scenarios',
                         client=None if args.live else FixtureProvider(counter))
        runner = app.state.scenario_runner
        runner.registry.scenarios['content_first'].flow.generation_mode = mode
        config = copy.deepcopy(app.state.dataset['config'])
        for endpoint in config['endpoints']:
            endpoint.update(model=args.model or 'offline-fixture-model', inference_tokenizer=tokenizer_path,
                            max_tokens=args.completion_cap, context_size=args.context_budget, temperature=0)
            if args.base_url:
                endpoint['base_url'] = args.base_url
            endpoint['completion_limit_parameter'] = args.completion_limit_parameter
        config['content_first'] = {'context_budget': args.context_budget, 'reserve_tokens': 256}
        config['single_generation'] = {'max_attempts': 1, 'api_retries': 0}
        app.state.dataset.set_config(config, app.state.dataset['revision'])
        runner.start('content_first', max_parallel=args.parallel, batch_size=args.samples)
        await runner.job
        samples = list(app.state.dataset['samples'])
        metrics = dataset_generation_metrics(samples)[mode]
        metrics['total_tokens'] = metrics['prompt_tokens'] + metrics['completion_tokens']
        metrics['generation_phase'] = runner.status['phase']
        metrics['candidate_retention'] = len(samples) / max(runner.status['batch']['attempts'], 1)
        report['modes'][mode] = metrics
        (output / (mode + '.samples.json')).write_text(json.dumps(samples, indent=2) + '\n')
    old, new = report['modes']['existing'], report['modes']['content-first']
    comparable = old['constructed_drafts'] == new['constructed_drafts'] == args.samples
    report['comparable_completed_candidates'] = comparable
    report['reductions_fraction'] = {key: 1 - new[key] / old[key] if old[key] and comparable else None
                                     for key in ('request_count', 'total_tokens', 'inference_seconds', 'elapsed_seconds')}
    report['limitations'] = [
        'Offline results use scripted text and simulated delays, not real LLM quality or GPU timing.',
        'Equivalent scenario prompts and tag seed; live model outputs may differ even at temperature zero.',
        'All candidates remain unapproved. Usable training throughput requires independent review and Ready approval.',
        'The existing YAML runner already generates content; this comparison isolates bounded construction and deferred validation.',
    ]
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'work/content-first-demo')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--base-url')
    parser.add_argument('--model')
    parser.add_argument('--inference-tokenizer')
    parser.add_argument('--context-budget', type=int, default=8192)
    parser.add_argument('--completion-cap', type=int, default=1024)
    parser.add_argument('--completion-limit-parameter', choices=['max_tokens', 'max_completion_tokens'], default='max_tokens')
    parser.add_argument('--samples', type=int, default=1)
    parser.add_argument('--parallel', type=int, default=3)
    parser.add_argument('--seed', type=int, default=42)
    asyncio.run(benchmark(parser.parse_args()))


if __name__ == '__main__':
    main()
