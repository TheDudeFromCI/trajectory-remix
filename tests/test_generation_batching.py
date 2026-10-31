"""Deferred scheduling preserves the exact eager timeline and tool observations."""
import copy
import sqlite3
from dataclasses import replace

import pytest

from trajectory_remix.compiler import Compiler, CompileError
from trajectory_remix.llm import SampleBuilder, default_config


def builder_for(compiler, sample):
    builder = SampleBuilder(compiler, default_config())
    builder.sample = copy.deepcopy(sample)
    plan = compiler.compile(builder.sample)
    builder.remember_plan(plan)
    builder.next_tick = plan['max_tick'] + 1
    return builder


def test_batched_streams_match_eager_ticks_and_rows(compiler, sample, monkeypatch):
    def run(enabled):
        local = Compiler(compiler.tokenizer, slot_policy=compiler.slot_policy)
        builder = builder_for(local, sample)
        calls = []
        original = local.compile
        def compile(source, **kwargs):
            calls.append(1)
            return original(source, **kwargs)
        monkeypatch.setattr(local, 'compile', compile)
        with builder.defer_compilation(enabled=enabled):
            builder.add_stream('a', 'first')
            builder.add_stream('b', 'second', close=True)
            builder.add_stream('a', local.special['eos'])
        count = len(calls)
        return builder.sample, builder.next_tick, original(builder.sample, incremental=False), count
    eager, batched = run(False), run(True)
    assert eager[:3] == batched[:3]
    assert eager[3] == 3
    assert batched[3] == 1


def test_tool_observations_force_a_real_compile(compiler, sample, monkeypatch):
    builder = builder_for(compiler, sample)
    calls = []
    original = compiler.compile
    def compile(source, **kwargs):
        calls.append(1)
        return original(source, **kwargs)
    monkeypatch.setattr(compiler, 'compile', compile)
    with builder.defer_compilation():
        builder.add_stream('a', 'check arithmetic')
        assert not calls
        builder.sample['replay'][str(builder.next_tick)] = [
            {'op': 'tool-call', 'window': 'calc', 'tool': 'calculator',
             'arguments': {'expression': '2 + 2'}}]
        plan = builder.compile_summary(force=True)
        assert len(calls) == 1
        assert plan['tool_calls'][0]['outcome']['result']['value'] == 4
        builder.next_tick = plan['max_tick'] + 1
        builder.add_stream('a', 'verified', close=True)
        assert len(calls) == 1
    assert len(calls) == 2
    assert builder._pending_summary is None


def test_slot_dependent_text_falls_back(compiler, sample, monkeypatch):
    sample['replay']['1'] = [{'op': 'generate', 'window': 'a', 'content': '{WINDOW:a}'}]
    builder = builder_for(compiler, sample)
    calls = []
    original = compiler.compile
    def compile(source, **kwargs):
        calls.append(1)
        return original(source, **kwargs)
    monkeypatch.setattr(compiler, 'compile', compile)
    with builder.defer_compilation():
        builder.add_stream('b', 'new')
        builder.add_stream('b', 'more')
    assert len(calls) == 2
    assert original(builder.sample) == original(builder.sample, incremental=False)


def test_failed_batch_clears_provisional_state(compiler, sample):
    builder = builder_for(compiler, sample)
    before = copy.deepcopy(builder.sample)
    with pytest.raises(CompileError):
        with builder.defer_compilation():
            builder.add_stream('a', 'closed', close=True)
            builder.add_stream('a', 'illegal write')
    assert builder._pending_summary is None
    # The scenario runner owns proposal rollback; summaries cannot survive it.
    builder.sample = before
    assert builder.compile_summary()['windows'][0]['close'] is None


def test_plugins_use_eager_path_without_extra_compiles(compiler, sample, monkeypatch):
    compiler.tools = copy.copy(compiler.tools)
    compiler.tools.tools = compiler.tools.tools.copy()
    compiler.tools.tools['calculator'] = replace(compiler.tools.get('calculator'), external=True)
    builder = builder_for(compiler, sample)
    calls = []
    original = compiler.compile
    def compile(source, **kwargs):
        calls.append(1)
        return original(source, **kwargs)
    monkeypatch.setattr(compiler, 'compile', compile)
    with builder.defer_compilation():
        assert builder._defer_depth == 0
        assert 'rows' in builder.add_stream('a', 'first')
        assert 'rows' in builder.add_stream('a', 'second')
    assert len(calls) == 2


@pytest.mark.parametrize('scenario', ['user_scenario', 'idle_scenario'])
async def test_resumed_batch_matches_eager_sandbox_and_timeline(compiler, tmp_path, monkeypatch, scenario):
    import httpx
    from test_idle_scenario import IdleClient
    from test_scenario_variations import NovalClient
    from trajectory_remix.server import create_app
    from trajectory_remix.storage import DatasetStore

    def client():
        return IdleClient() if scenario == 'idle_scenario' else NovalClient(turns=2, tools=True)
    path = tmp_path / 'source.sqlite'
    DatasetStore.initialize(path, compiler)
    interrupted = client()
    complete = interrupted.complete
    async def fail_action(endpoint, messages, tools=None):
        if messages[0]['content'].startswith(('Plan Kidra', "Choose Kidra's")):
            raise httpx.ConnectError('interrupted before action')
        return await complete(endpoint, messages, tools)
    interrupted.complete = fail_action
    app = create_app(path, client=interrupted)
    config = copy.deepcopy(app.state.dataset['config'])
    config['single_generation'] = {'max_attempts': 1, 'api_retries': 0}
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    owner = app.state.scenario_runner
    owner.start(scenario)
    await owner.job
    assert owner.status['phase'] == 'api_paused'
    ident = owner.status['draft_id']
    results = []
    defer = SampleBuilder.defer_compilation
    for enabled in (False, True):
        target = tmp_path / f'resume-{enabled}.sqlite'
        with app.state.dataset.store.connect() as source:
            with sqlite3.connect(target) as destination:
                source.backup(destination)
        resumed = create_app(target, client=client())
        runner = resumed.state.scenario_runner
        with monkeypatch.context() as patch:
            if not enabled:
                patch.setattr(SampleBuilder, 'defer_compilation',
                              lambda self, **kwargs: defer(self, enabled=False))
            runner.resume_saved(ident)
            await runner.job
        assert runner.status['phase'] == 'complete', list(runner.logs)
        sample = resumed.state.dataset['samples'][0]
        results.append((sample['replay'], runner.compiler.compile(sample, incremental=False)))
    assert results[0] == results[1]
