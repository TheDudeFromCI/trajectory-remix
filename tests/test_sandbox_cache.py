"""Tool checkpoints preserve full replay under changes to their causal inputs."""
import copy
from dataclasses import replace

import pytest

from trajectory_remix.compiler import CompileError


@pytest.fixture
def source(sample):
    sample['replay']['0'] += [
        {'op': 'open', 'window': 'chat', 'window-type': 'chat'},
        {'op': 'invisible-tool-call', 'tool': 'file_write',
         'arguments': {'path': '/x', 'content': 'original'}},
    ]
    sample['replay']['20'] = [{'op': 'tool-call', 'window': 'read',
        'tool': 'file_read', 'arguments': {'path': '/x'}}]
    sample['replay']['100'] = [{'op': 'tool-call', 'window': 'reply',
        'tool': 'chat', 'arguments': {'content': 'hello'}}]
    return sample


def check(compiler, source, minimum_hits=0):
    actual = compiler.compile(source)
    assert compiler.last_compile_reused_tool_ticks >= minimum_hits
    # Full reference runs use a separate compiler so they cannot evict the cache
    # under test. Both return complete plans, including sandbox snapshots.
    from trajectory_remix.compiler import Compiler
    reference = Compiler(compiler.tokenizer, tools=compiler.tools,
                         slot_policy=compiler.slot_policy)
    assert actual == reference.compile(source, incremental=False)
    return actual


def test_unchanged_and_future_content_do_not_run_tools_again(compiler, source):
    check(compiler, source)
    first = check(compiler, source, 3)
    first['sandbox']['snapshots'].clear()
    first['tool_calls'][0]['outcome'].clear()
    source['replay']['1000'] = [{'op': 'generate', 'window': 'a', 'content': 'later'}]
    check(compiler, source, 3)


def test_only_changed_suffix_executes(compiler, source):
    check(compiler, source)
    source['replay']['100'][0]['arguments']['content'] = 'different reply'
    check(compiler, source, 2)
    assert compiler.last_compile_reused_tool_ticks == 2
    source['replay']['0'][-1]['arguments']['content'] = 'changed file'
    check(compiler, source)
    assert compiler.last_compile_reused_tool_ticks == 0


def test_window_reads_and_metadata_invalidate(compiler, source):
    check(compiler, source)
    source['replay']['1'] = [{'op': 'append', 'window': 'a', 'source': 'user', 'content': 'visible'}]
    check(compiler, source, 2)
    assert compiler.last_compile_reused_tool_ticks == 2
    source['metadata'] = {'agent_name': 'Other'}
    check(compiler, source)
    assert compiler.last_compile_reused_tool_ticks == 0


def test_external_tools_always_execute(compiler, sample):
    # Copy the registry so its shared built-in definitions are not modified.
    compiler.tools = copy.copy(compiler.tools)
    compiler.tools.tools = compiler.tools.tools.copy()
    compiler.tools.tools['calculator'] = replace(compiler.tools.get('calculator'), external=True)
    sample['replay']['1'] = [{'op': 'tool-call', 'window': 'calc', 'tool': 'calculator',
                             'arguments': {'expression': '2 + 2'}}]
    check(compiler, sample)
    check(compiler, sample)
    assert compiler.last_compile_reused_tool_ticks == 0


def test_timing_and_new_implicit_windows_invalidate(compiler, source):
    check(compiler, source)
    source['replay']['101'] = source['replay'].pop('100')
    check(compiler, source, 2)
    source['replay']['1000'] = [{'op': 'tool-call', 'window': 'future', 'tool': 'calculator',
                               'arguments': {'expression': '3 + 3'}}]
    check(compiler, source, 2)
    # Chat can inspect every implicit tool window, including future requests.
    assert compiler.last_compile_reused_tool_ticks == 2


def test_cancellation_replays_cleanup(compiler, source):
    source['replay']['20'][0]['duration-ticks'] = 10
    plan = check(compiler, source)
    read = next(c for c in plan['tool_calls'] if c['tool'] == 'file_read')
    source['replay'][str(read['start'] + 1)] = [
        {'op': 'generate', 'window': 'read', 'content': '<|eos|>'}]
    check(compiler, source, 1)
    check(compiler, source, 1)


def test_failed_compile_does_not_publish_checkpoints(compiler, source):
    check(compiler, source)
    bad = copy.deepcopy(source)
    bad['replay']['0'][-1]['arguments']['content'] = 'not committed'
    bad['replay']['1000'] = [{'op': 'generate', 'window': 'missing', 'content': 'invalid'}]
    with pytest.raises(CompileError):
        compiler.compile(bad)
    check(compiler, source, 3)


@pytest.mark.parametrize('limit', ['MAX_ENTRIES', 'MAX_BYTES'])
def test_cache_budget_falls_back(compiler, source, monkeypatch, limit):
    from trajectory_remix.sandbox_cache import SandboxReplayCache
    monkeypatch.setattr(SandboxReplayCache, limit, 1)
    check(compiler, source)
    check(compiler, source)
    assert compiler.last_compile_reused_tool_ticks <= 1


def test_custom_callable_without_module_uses_full_execution(compiler, source):
    compiler.tools = copy.copy(compiler.tools)
    compiler.tools.tools = compiler.tools.tools.copy()
    def custom(args, state, windows):
        return '{"ok":true,"result":"custom"}', True, False
    custom.__module__ = None
    compiler.tools.tools['file_read'] = replace(compiler.tools.get('file_read'), run=custom)
    check(compiler, source)
    check(compiler, source, 1)
    assert compiler.last_compile_reused_tool_ticks == 2  # Only the two bundled tools.
