"""Generation reuses summaries without losing validation or dependency checks."""
import copy
from dataclasses import replace

import pytest

from trajectory_remix.compiler import CompileError
from trajectory_remix.llm import SampleBuilder, default_config


def test_summary_reuse_and_invalidation(compiler, sample, monkeypatch):
    builder = SampleBuilder(compiler, default_config())
    builder.sample = sample
    calls = []
    original = compiler.compile
    def compile(source, **kwargs):
        calls.append(source['id'])
        return original(source, **kwargs)
    monkeypatch.setattr(compiler, 'compile', compile)
    first = builder.compile_summary()
    first['windows'][0]['close'] = -1
    assert builder.compile_summary()['windows'][0]['close'] is None
    assert len(calls) == 1
    builder.sample['replay']['1'] = [{'op': 'generate', 'window': 'a', 'content': 'new'}]
    assert builder.compile_summary()['max_tick'] > 0
    assert len(calls) == 2
    builder.sample['metadata'] = {'agent_name': 'changed'}
    builder.compile_summary()
    assert len(calls) == 3
    # JSON would encode a tuple exactly like a list, but replay requires lists.
    builder.sample['replay']['1'] = tuple(builder.sample['replay']['1'])
    with pytest.raises(CompileError):
        builder.compile_summary()
    builder.sample['replay']['1'] = list(builder.sample['replay']['1'])
    compiler.max_window_slots = 1
    with pytest.raises(CompileError):
        builder.compile_summary()


def test_add_stream_primes_summary_without_exposing_cached_data(compiler, sample, monkeypatch):
    builder = SampleBuilder(compiler, default_config())
    builder.sample = sample
    plan = builder.add_stream('a', 'text')
    plan['windows'][0]['close'] = -1
    def unexpected(*args, **kwargs):
        pytest.fail('Unchanged source should reuse the scheduling summary')
    monkeypatch.setattr(compiler, 'compile', unexpected)
    assert builder.compile_summary()['windows'][0]['close'] is None


def test_external_tools_disable_summary_reuse(compiler, sample, monkeypatch):
    compiler.tools = copy.copy(compiler.tools)
    compiler.tools.tools = compiler.tools.tools.copy()
    compiler.tools.tools['calculator'] = replace(compiler.tools.get('calculator'), external=True)
    builder = SampleBuilder(compiler, default_config())
    builder.sample = sample
    calls = []
    original = compiler.compile
    def compile(source, **kwargs):
        calls.append(1)
        return original(source, **kwargs)
    monkeypatch.setattr(compiler, 'compile', compile)
    builder.compile_summary()
    builder.compile_summary()
    assert len(calls) == 2


async def test_publish_validates_final_draft_before_saving(tmp_path, compiler, monkeypatch):
    from test_idle_scenario import setup_idle, IdleClient
    app, runner = setup_idle(tmp_path, compiler, monkeypatch, IdleClient('quiet'))
    runner.start('idle_scenario', _schedule=False)
    draft = runner.closed_draft()
    draft['replay']['1000'] = [{'op': 'generate', 'window': 'missing', 'content': 'invalid'}]
    before = list(app.state.dataset['samples'])
    monkeypatch.setattr(runner, 'closed_draft', lambda **kwargs: draft)
    with pytest.raises(CompileError):
        runner.publish()
    assert list(app.state.dataset['samples']) == before
