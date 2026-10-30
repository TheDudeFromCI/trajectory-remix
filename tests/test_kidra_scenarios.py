"""Kidra lifecycle conditions, causal specialist work and portable separate review."""
import asyncio
import copy
import json
import re

import jsonschema
import pytest
import yaml

from trajectory_remix.dataset import training_eligible
from trajectory_remix.scenarios import ScenarioRegistry
from trajectory_remix.server import create_app
from trajectory_remix.trajectory_review import build_checklist
from test_scenarios import app_for


KIDRA = ['kidra_large', 'kidra_medium', 'kidra_small']


def selections(scenario, lifecycle='recovery'):
    selected = {key: values[0].name for key, values in scenario.flow.tags.items()}
    selected.update(lifecycle=lifecycle, primary_competency='quality assurance and replanning',
                    recurrence='one-off work', complication='failed acceptance')
    return {'scenario': selected, 'kidra': {}}


def test_kidra_choices_preserve_defaults_and_independent_sampling_axes():
    registry = ScenarioRegistry()
    assert [s['name'] for s in registry.listing()] == ['user_scenario', 'idle_scenario', *KIDRA]
    for name, limit in [('kidra_small', 10), ('kidra_medium', 16), ('kidra_large', 20)]:
        scenario = registry.scenarios[name]
        assert scenario.flow.steps['work'].max_iterations == limit
        assert scenario.flow.steps['work'].inputs['decision_limit'] == limit
        assert scenario.flow.steps['idea'].inputs['decision_limit'] == limit
        assert [t.name for t in scenario.flow.tags['project_size']] == [name.removeprefix('kidra_')]
        assert {t.name: t.weight for t in scenario.flow.tags['lifecycle']} == {
            'initiation': 25, 'active execution': 50, 'recovery': 9, 'revision': 8, 'completion': 8}
        assert [t.weight for t in scenario.flow.tags['primary_competency']] == [20, 20, 20, 15, 15, 10]
        assert len(scenario.flow.tags['recurrence']) == 4
        assert scenario.flow.generation_mode == 'content-first'
        assert not any(s.uses == 'remix.validate' for s in scenario.flow.steps.values())
        checks = build_checklist(scenario).checks
        assert len(checks) == 7
        assert {'validate.layout', 'validate.tools', 'validate.reasoning', 'validate.correctness'} <= checks.keys()
        assert checks['scenario.coordination'].rules['continuity_progress'].scope == 'sample'
        assert checks['scenario.investment'].rules['lifetime_value'].allow_not_applicable
        assert 'persona v0.1' in checks['scenario.persona'].prompt
    assert len(registry.scenarios['user_scenario'].flow.tags['domain']) >= 40
    assert len(build_checklist(registry.scenarios['idle_scenario']).checks) == 4


def test_kidra_action_schema_rejects_false_stops_and_non_specialist_outcomes():
    schema = ScenarioRegistry().scenarios['kidra_small'].flow.schemas['Action']
    action = {'streams': [{'content': 'Need evidence.'}], 'calls': [], 'release_windows': [], 'finished': True}
    jsonschema.validate(action, schema)
    action['calls'] = [{'tool': 'file_read', 'arguments': {'path': '/draft'}}]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(action, schema)
    action['finished'] = False
    action['calls'][0]['outcome'] = {'ok': True, 'result': {'content': 'invented'}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(action, schema)


async def test_explicit_review_receives_saved_kidra_conditions_and_all_checks(studio):
    from test_standalone_validation import Reviewer, attach

    app, _ = studio
    scenario = app.state.scenario_runner.registry.scenarios['kidra_large']
    conditions = {key: {'name': value} for key, value in selections(scenario)['scenario'].items()}
    await app.state.commands.call('update_trajectory_metadata', {
        'sample_id': 'test', 'patch': {'metadata': {
            'scenario': scenario.name, 'tags': [scenario.name], 'context_tags': conditions,
            'review_checklist': build_checklist(scenario).model_dump(),
        }},
    })
    original = copy.deepcopy(app.state.dataset['samples'][0])
    reviewer = Reviewer()
    validator = attach(app, reviewer)
    result = await validator.validate('test', record=False)
    assert len(result['checks']) == 7
    assert {'scenario.coordination', 'scenario.investment', 'scenario.persona'} <= result['checks'].keys()
    assert reviewer.messages
    for messages in reviewer.messages:
        payload = json.loads(messages[1]['content'])
        assert payload['inputs']['sampled_conditions'] == conditions
    assert app.state.dataset['samples'][0] == original


@pytest.mark.parametrize('prompt', ['../outside.md', '/outside.md', 'review.txt'])
def test_separate_review_prompts_obey_scenario_path_boundary(tmp_path, prompt):
    folder = tmp_path / 'custom'
    folder.mkdir()
    (folder / 'action.md').write_text('Generate content.')
    (folder / 'flow.yaml').write_text(yaml.safe_dump({
        'review_checks': {'coordination': {'prompt': prompt, 'view': 'correctness'}},
        'steps': {'act': {'uses': 'llm.content', 'prompt': 'action.md'}},
    }))
    with pytest.raises(ValueError, match='inside their scenario'):
        ScenarioRegistry(tmp_path)


class KidraClient:
    """One-off document repair, with independent parallel assessments and recheck."""
    def __init__(self, *, hold_cycle=None, invalid_cycle=None, blocked=False, never_stop=False):
        self.messages = []
        self.hold_cycle, self.invalid_cycle = hold_cycle, invalid_cycle
        self.blocked, self.never_stop = blocked, never_stop
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def complete(self, endpoint, messages, tools=None):
        assert endpoint['id'] == 'generator', 'Creation must not invoke a reviewer'
        self.messages.append(copy.deepcopy(messages))
        prompt, data = messages[0]['content'], json.loads(messages[1]['content'])
        if prompt.startswith('Create a fictional Kidra project episode'):
            phase = data['conditions']['lifecycle']['name']
            output = {
                'identifier': 'kidra-document-recovery',
                'problem': f'Project phase: {phase}. Fix the handoff document in /draft to say "Owner: Ada". '
                           'Coordinate an editor and independent reviewer, then verify and deliver it. '
                           'Use /project for criteria, roster and budget. Virtual work only; no publishing.',
                'situation': 'AUTHOR_ONLY_STATE: the previous draft failed its owner criterion.',
                'success_criteria': 'AUTHOR_ONLY_EXPECTATION: observe the repaired artifact, review it independently and deliver; missing inputs may block.',
                'files': [{'path': '/project', 'content': 'Accept only Owner: Ada. Roster: fast editor costs 1 unit, independent reviewer costs 2 units. '
                            'Budget: 8 units. One document; automation costs 20 units. No external publication. '
                            'Editor owns repair; reviewer owns acceptance. Prior draft is unverified; next milestone is delivery.'},
                          *([] if self.blocked else [{'path': '/draft', 'content': 'Owner: unknown'}])],
                'operations': [{'tool': 'note_save', 'arguments': {'name': 'repair', 'description': 'Editor repairs owner; independent reviewer checks exact candidate.',
                                  'status': 'not started', 'priority': 'high'}}],
            }
        elif prompt.startswith("Choose Kidra's next project-coordination action"):
            cycle = data['cycle']
            if cycle == self.hold_cycle:
                self.entered.set()
                await self.release.wait()
            assert 'AUTHOR_ONLY_' not in messages[1]['content']
            assert 'success_criteria' not in data
            visible = data['visible_windows']
            text = json.dumps(visible)
            old = [w['window'] for w in visible if w['type'] in ('reasoning', 'tool')]
            calls, finished = [], False
            notes = 'Need current evidence before deciding.'
            if self.never_stop:
                notes = 'Still considering; not claiming completion.'
            elif cycle == 0:
                assert 'automation costs 20 units' not in text
                calls = [{'tool': 'file_read', 'arguments': {'path': path}} for path in ['/project', '/draft']]
            elif self.blocked:
                if cycle == 1:
                    assert 'false' in text
                    notes = 'Draft read failed; record missing input as a blocker, not a completed repair.'
                    calls = [{'tool': 'note_save', 'arguments': {'name': 'repair', 'status': 'blocked'}}]
                elif cycle == 2:
                    assert 'blocked' in text
                    calls = [{'tool': 'chat', 'arguments': {'content': 'The draft is missing; repair remains blocked. Please supply /draft.'}}]
                else:
                    assert 'draft is missing' in text
                    finished = True
                    notes = 'Delivered blocker; repair remains open, with no authorized replacement input.'
            elif cycle == 1:
                assert 'automation costs 20 units' in text and 'Owner: unknown' in text
                notes = 'One document does not justify 20-unit automation. Request a 1-unit editor repair and 2-unit independent baseline assessment; budget 8.'
                calls = [
                    {'tool': 'subagent', 'arguments': {'task': 'Owner editor, fast tier, 1 unit. Repair this supplied draft: Owner: unknown. Criterion: exactly Owner: Ada. Return candidate, no publishing.'},
                     'duration-ticks': 2, 'outcome': {'ok': True, 'result': {'response': 'Candidate: Owner: Ada'}}},
                    {'tool': 'subagent', 'arguments': {'task': 'Owner reviewer, independent tier, 2 units. Assess this supplied baseline: Owner: unknown. Criterion: exactly Owner: Ada. Do not assume a repair occurred.'},
                     'duration-ticks': 5, 'outcome': {'ok': True, 'result': {'response': 'Baseline fails: owner unknown. Recheck the repaired version before delivery.'}}},
                ]
            elif cycle == 2:
                assert 'Candidate: Owner: Ada' in text and 'Baseline fails' in text
                notes = 'Editor supplied Owner: Ada; reviewer rejected the old baseline. Publish candidate, then arrange a separate check.'
                calls = [{'tool': 'file_write', 'arguments': {'path': '/draft', 'content': 'Owner: Ada'}}]
            elif cycle == 3:
                # Only current visible write evidence supplies this artifact version.
                version = re.search(r'sha256:[a-f0-9]{64}', text).group()
                assert 'saved' in text
                notes = f'Published /draft {version}; baseline review does not verify it. Recheck exact version, 2 units; 5 total, 3 remaining.'
                calls = [{'tool': 'subagent', 'arguments': {'task': 'Independently check shared candidate against criterion exactly Owner: Ada. Reviewer tier, 2 units.',
                          'resume': 'conversation-2', 'artifacts': [{'path': '/draft', 'version': version}]},
                          'outcome': {'ok': True, 'result': {'response': 'Checked shared candidate: exactly Owner: Ada; criterion passes.'}}}]
            elif cycle == 4:
                assert 'criterion passes' in text
                notes = 'Independent review of published candidate passed. Record verified repair and exact remaining budget.'
                calls = [{'tool': 'note_save', 'arguments': {'name': 'repair', 'status': 'done'}},
                         {'tool': 'file_write', 'arguments': {'path': '/checkpoint', 'content': 'Goal: owner handoff. Editor repair published in /draft; independent reviewer checked exact version. repair done. Cost 5/8, 3 remaining. No deployment authorized; next: user delivery.'}}]
            elif cycle == 5:
                assert 'done' in text and '/checkpoint' in text
                notes = 'Repair and checkpoint saves succeeded. Deliver the verified outcome with specialist attribution.'
                calls = [{'tool': 'chat', 'arguments': {'content': 'The editor repaired /draft to Owner: Ada; an independent reviewer checked the published candidate. Checkpoint: /checkpoint. Cost 5 of 8 units. No external publication performed.'}}]
            else:
                assert cycle == 6 and 'The editor repaired' in text
                finished = True
                notes = 'Delivery observed; verified repair complete. Retain /draft, /checkpoint, 3 units remaining and publication restriction.'
            output = {'streams': [{'content': notes}], 'calls': calls, 'release_windows': old, 'finished': finished}
            if cycle == self.invalid_cycle:
                output['calls'] = [{'tool': 'file_write', 'arguments': {'path': '/unjustified', 'content': 'bad'}}]
                output['finished'] = True
        else:
            pytest.fail('Unexpected request: ' + prompt[:100])
        return {'content': json.dumps(output)}


def setup(tmp_path, compiler, client):
    return app_for(tmp_path, compiler, client, scenarios=None, legacy_reviews=False)


@pytest.mark.parametrize('name', KIDRA)
async def test_kidra_causal_repair_parallel_specialists_versions_and_review(compiler, tmp_path, name):
    fake = KidraClient()
    app = setup(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.start(name, tags=selections(runner.registry.scenarios[name]))
    await asyncio.wait_for(runner.job, 30)
    assert runner.status['phase'] == 'complete', list(runner.logs)
    sample = app.state.dataset['samples'][0]
    assert sample['metadata']['scenario'] == name
    assert sample['metadata']['context_tags']['lifecycle']['name'] == 'recovery'
    assert sample['metadata']['validation_level'] == 0 and not training_eligible(sample)
    assert len(fake.messages) == 8  # one workspace plus seven observation-dependent decisions
    plan = compiler.compile(sample)
    assert not plan['diagnostics']
    specialists = [c for c in plan['tool_calls'] if c['tool'] == 'subagent']
    assert len(specialists) == 3
    assert specialists[0]['request_start'] == specialists[1]['request_start']
    assert specialists[0]['end'] < specialists[1]['end']
    write = next(c for c in plan['tool_calls'] if c['tool'] == 'file_write' and not c.get('invisible'))
    review = specialists[-1]
    assert specialists[1]['end'] < write['request_start'] < write['end'] < review['request_start']
    assert review['arguments']['artifacts'] == [{'path': '/draft', 'version': write['outcome']['result']['version']}]
    done = next(c for c in plan['tool_calls'] if c['tool'] == 'note_save' and c['arguments'].get('status') == 'done')
    assert review['end'] < done['request_start']
    for call in specialists:
        rows = [r for r in plan['rows'] if r['event'] == call['path'] + '.request']
        assert rows and all(r['loss_weight'] > 0 for r in rows)
    assert all(r['loss_weight'] == 0 for r in plan['rows'] if r['phase'] == 'B')
    assert all(w['close'] is not None for w in plan['windows'] if w['type'] != 'notes')
    record = sample['metadata']['generation_record']
    assert record['requirements']['prompts']['review-investment.md']
    assert record['requirements']['flow']['review_checks']['coordination']
    reopened = create_app(app.state.dataset.store.path)
    reopened.state.scenario_runner.registry.scenarios.pop(name)
    checklist = await reopened.state.commands.call('get_review_checklist', {'sample_id': sample['id']})
    assert checklist['checklist']['source'] == 'generation_snapshot'
    assert len(checklist['checklist']['checks']) == 7


async def test_kidra_blocked_handoff_never_marks_missing_work_complete(compiler, tmp_path):
    fake = KidraClient(blocked=True)
    app = setup(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.start('kidra_small')
    await runner.job
    assert runner.status['phase'] == 'complete', list(runner.logs)
    calls = compiler.compile(app.state.dataset['samples'][0])['tool_calls']
    assert not any(c['arguments'].get('status') == 'done' for c in calls)
    assert not any(c['tool'] == 'subagent' for c in calls)
    assert any(c['tool'] == 'chat' and 'blocked' in c['arguments']['content'] for c in calls)


async def test_kidra_cancel_resume_keeps_original_conditions_and_completed_requests(compiler, tmp_path):
    fake = KidraClient(hold_cycle=2)
    app = setup(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    chosen = selections(runner.registry.scenarios['kidra_medium'], lifecycle='revision')
    runner.start('kidra_medium', tags=chosen)
    await asyncio.wait_for(fake.entered.wait(), 20)
    await runner.stop()
    sample = app.state.dataset['samples'][0]
    assert sample['metadata']['validation_level'] == 0
    fake.hold_cycle = None
    reopened = create_app(app.state.dataset.store.path, client=fake)
    resumed = reopened.state.scenario_runner
    resumed.resume_saved(sample['id'])
    await asyncio.wait_for(resumed.job, 30)
    assert resumed.status['phase'] == 'complete', list(resumed.logs)
    assert sum(m[0]['content'].startswith('Create a fictional Kidra') for m in fake.messages) == 1
    final = reopened.state.dataset['samples'][0]
    assert final['metadata']['context_tags']['lifecycle']['name'] == 'revision'
    assert len([c for c in compiler.compile(final)['tool_calls'] if c['tool'] == 'subagent']) == 3


async def test_kidra_invalid_stop_retains_proposal_without_tool_side_effect(compiler, tmp_path):
    fake = KidraClient(invalid_cycle=0)
    app = setup(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.start('kidra_small')
    await runner.job
    assert runner.status['phase'] != 'complete'
    sample = app.state.dataset['samples'][0]
    assert '/unjustified' in sample['metadata']['generation_record']['requests'][-1]['raw_output']
    assert not any(c['arguments'].get('path') == '/unjustified' for c in compiler.compile(sample)['tool_calls'])
    assert runner.can_resume(sample['id']) and sample['metadata']['validation_level'] == 0


async def test_kidra_horizon_exhaustion_retains_unfinished_sample(compiler, tmp_path):
    app = setup(tmp_path, compiler, KidraClient(never_stop=True))
    runner = app.state.scenario_runner
    runner.start('kidra_small')
    await runner.job
    assert runner.status['phase'] != 'complete'
    sample = app.state.dataset['samples'][0]
    record = sample['metadata']['generation_record']
    assert len(record['requests']) == 11
    assert 'iteration limit' in json.dumps(record['failures'])
    assert sample['metadata']['validation_level'] == 0 and runner.can_resume(sample['id'])
