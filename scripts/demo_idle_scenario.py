#!/usr/bin/env python3
"""Offline idle-flow demonstration with scripted responses, never a model benchmark.

Runs the real scenario runner, context budgeting, tools, compiler and SQLite store.
All resulting candidates remain Pending; no semantic approval is performed.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path

from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers

from trajectory_remix.compiler import Compiler
from trajectory_remix.content_first import inference_tokenizer
from trajectory_remix.server import create_app
from trajectory_remix.storage import DatasetStore
from trajectory_remix.tools import default_registry

ROOT = Path(__file__).resolve().parents[1]
WORLD = {
    'identifier': 'idle-inventory-alarm',
    'situation': 'A scheduled project inventory check wakes Kidra without a user. The report is stale.',
    'success_criteria': 'Compare the inventory and report, correct the report to 3 available units, read back the correction, then complete inventory-check TODO. Do not send a chat message.',
    'idle_context': 'Scheduler: inventory-check alarm fired. Standing policy: compare /project/inventory.txt with /project/report.txt, correct stale counts, read back changes, and only then mark the TODO done. Work is confined to these virtual project files; no user is present.',
    'files': [{'path': '/project/inventory.txt', 'content': 'Current inventory: 3 available units.'},
              {'path': '/project/report.txt', 'content': 'Last report: 4 available units.'}],
    'operations': [{'tool': 'note_save', 'arguments': {'name': 'inventory-check',
        'description': 'On alarm, reconcile inventory report and verify changes before completion.',
        'status': 'not started', 'priority': 'high'}}],
}


class ScriptedIdleProvider:
    """Fixture responses; actions are emitted only after their evidence is visible."""
    async def complete(self, endpoint, messages, tools=None):
        prompt = messages[0]['content']
        if prompt.startswith('Create a fictional idle workspace'):
            output = WORLD
        elif prompt.startswith("Choose Kidra's next idle-work action"):
            data = json.loads(messages[1]['content'])
            visible = data['visible_windows']
            text = json.dumps(visible)
            cycle = data['cycle']
            release = [w['window'] for w in visible if w['type'] in ('reasoning', 'tool')]
            calls, finished = [], False
            if cycle == 0:
                assert 'Current inventory: 3' not in text
                notes = ['Inspect the inventory and report independently before deciding whether a correction is needed.',
                         'The alarm assigns a bounded inventory check. Keep its TODO open until a correction is verified by readback.']
                calls = [{'tool': 'file_read', 'arguments': {'path': path}} for path in
                         ('/project/inventory.txt', '/project/report.txt')]
            elif cycle == 1:
                assert 'Current inventory: 3' in text and 'Last report: 4' in text
                notes = ['Actual reads show inventory 3 units versus stale report 4. Correct /project/report.txt to 3; the TODO remains open pending write and readback.']
                calls = [{'tool': 'file_write', 'arguments': {'path': '/project/report.txt', 'content': 'Verified report: 3 available units.'}}]
            elif cycle == 2:
                assert 'Verified report: 3' in text and 'file_write' in text
                notes = ['The write succeeded for /project/report.txt with 3 available units, matching inventory. Standing policy requires readback before the TODO can be completed.']
                calls = [{'tool': 'file_read', 'arguments': {'path': '/project/report.txt'}}]
            elif cycle == 3:
                assert 'Verified report: 3' in text and 'file_read' in text
                notes = ['Readback confirms /project/report.txt now states 3 available units, matching inventory. The alarm follow-up is verified; complete inventory-check.']
                calls = [{'tool': 'note_save', 'arguments': {'name': 'inventory-check', 'status': 'done'}}]
            else:
                assert cycle == 4 and 'done' in text
                notes = ['Inventory report correction and readback succeeded; inventory-check TODO is done. No other obligation is visible. End quietly without chat.']
                finished = True
            output = {'streams': [{'content': note} for note in notes], 'calls': calls,
                      'release_windows': release, 'finished': finished}
        else:
            raise AssertionError('Offline demo must not generate users or invoke semantic validation')
        return {'content': json.dumps(output)}


async def demo(output):
    from transformers import PreTrainedTokenizerFast
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    project = output / 'idle.sqlite'
    if project.exists():
        raise ValueError(f'Refusing to overwrite {project}; choose a fresh output directory')
    backend = Tokenizer(models.BPE(unk_token='[UNK]'))
    backend.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    backend.decoder = decoders.ByteLevel()
    corpus = [p.read_text() for p in (ROOT / 'examples/scenarios/idle_scenario_noval').iterdir()]
    corpus += [json.dumps(default_registry().schemas()), json.dumps(WORLD), Path(__file__).read_text()]
    backend.train_from_iterator(corpus, trainers.BpeTrainer(vocab_size=8192, special_tokens=['[UNK]']))
    fixture = PreTrainedTokenizerFast(tokenizer_object=backend)
    fixture.chat_template = "{% for m in messages %}{{ m['role'] }}: {{ m['content'] }}\n{% endfor %}{% if add_generation_prompt %}assistant: {% endif %}"
    model_dir = output / 'fixture-tokenizer'
    fixture.save_pretrained(model_dir)
    training = Tokenizer.from_str(backend.to_str())
    training.add_special_tokens(['<|bos|>', '<|eos|>', '<|noop|>'])
    training.save(str(output / 'training-tokenizer.json'))
    compiler = Compiler(training)
    DatasetStore.initialize(project, compiler)
    app = create_app(project, scenarios_dir=ROOT / 'examples/scenarios', client=ScriptedIdleProvider())
    config = copy.deepcopy(app.state.dataset['config'])
    config['tasks'] = []
    config['endpoints'] = [config['endpoints'][0]]
    config['endpoints'][0].update(inference_tokenizer=str(model_dir), context_size=8192, max_tokens=1024)
    config['single_generation'] = {'max_attempts': 1, 'api_retries': 0}
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    # These fixture conditions describe the fixed world, rather than random tags.
    tags = {'scenario': {'domain': 'project maintenance', 'trigger': 'alarm fired',
                         'complication': 'contradictory files'}, 'kidra': {}}
    runner = app.state.scenario_runner
    runner.start('idle_scenario_noval', tags=tags)
    await runner.job
    sample = app.state.dataset['samples'][0]
    plan = compiler.compile(sample)
    metrics = sample['metadata']['generation_record']['metrics']
    report = {'measurement': 'offline-scripted-demonstration-not-a-model-benchmark',
              'phase': runner.status['phase'], 'last_error': runner.status['last_error'],
              'validation_level': sample['metadata']['validation_level'],
              'metrics': metrics, 'windows': len(plan['windows']),
              'visible_tool_calls': [c['tool'] for c in plan['tool_calls'] if not c.get('invisible')],
              'compiler_diagnostics': plan['diagnostics']}
    (output / 'trajectory.json').write_text(json.dumps(sample, indent=2) + '\n')
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if runner.status['phase'] != 'complete':
        raise RuntimeError('Demo incomplete; retained draft and diagnostics are in the output directory')
    assert metrics['request_count'] == 6
    assert not any(w['id'] == 'chat' for w in plan['windows'])
    assert not plan['diagnostics']
    # Keep tokenizer loading exercised through the actual local Hugging Face path.
    assert inference_tokenizer(str(model_dir)).chat_template
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'work/idle-demo')
    asyncio.run(demo(parser.parse_args().output_dir))
