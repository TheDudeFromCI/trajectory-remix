"""Incremental results must retain the full compiler's causal semantics."""
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from trajectory_remix.compiler import CompileError


def assert_full(compiler, sample):
    actual = compiler.compile(sample)
    reused = compiler.last_compile_reused_rows
    assert actual == compiler.compile(sample, incremental=False)
    # Re-prime after the independent reference compilation.
    compiler.compile(sample)
    return actual, reused


@pytest.mark.parametrize('slot_policy', ['declaration-v1', 'uuid-hash-v1'])
def test_append_edit_truncate_and_config_changes(compiler, sample, slot_policy):
    compiler.slot_policy = slot_policy
    assert_full(compiler, sample)
    sample['replay']['1'] = [{'op': 'generate', 'window': 'a', 'content': 'first'}]
    assert assert_full(compiler, sample)[1] > 0
    sample['replay']['20'] = [{'op': 'generate', 'window': 'b', 'content': 'second<|eos|>'}]
    assert assert_full(compiler, sample)[1] > 0
    sample['replay']['1'][0]['content'] = 'edited'
    assert_full(compiler, sample)
    del sample['replay']['20']
    assert_full(compiler, sample)
    sample['defaults'] = {'implicit-noop-loss-weight': .7, 'window-headers': False}
    assert_full(compiler, sample)


def test_tools_anchors_metadata_and_cancellation(compiler, sample):
    sample['replay']['1'] = [{'op': 'tool-call', 'window': 'tool', 'tool': 'chat',
                            'arguments': {'content': 'hello'}, 'result-anchor': 8,
                            'duration-ticks': 10}]
    sample['replay']['0'].append({'op': 'open', 'window': 'chat', 'window-type': 'chat'})
    sample['replay']['{N_TICK:8}+1'] = [{'op': 'generate', 'window': 'a', 'content': 'done'}]
    assert_full(compiler, sample)
    sample['metadata'] = {'agent_name': 'Changed'}
    assert_full(compiler, sample)
    plan, _ = assert_full(compiler, sample)
    sample['replay'][str(plan['tool_calls'][0]['start'] + 1)] = [
        {'op': 'generate', 'window': 'tool', 'content': '<|eos|>'}]
    assert_full(compiler, sample)


def test_replacement_and_caller_mutation(compiler, sample):
    sample['replay']['1'] = [{'op': 'append', 'window': 'a', 'source': 'user', 'content': 'old'}]
    plan, _ = assert_full(compiler, sample)
    plan['rows'][0]['token_id'] = -1
    plan['windows'][0]['close'] = -1
    assert_full(compiler, sample)
    sample['replay']['3'] = [{'op': 'append', 'window': 'a', 'source': 'user',
                            'content': 'new', 'replace-content': True}]
    assert assert_full(compiler, sample)[1] == 0


def test_invalid_suffix_does_not_poison_cache(compiler, sample):
    assert_full(compiler, sample)
    bad = copy.deepcopy(sample)
    bad['replay']['5'] = [{'op': 'generate', 'window': 'missing', 'content': 'invalid'}]
    with pytest.raises(CompileError):
        compiler.compile(bad)
    assert_full(compiler, sample)


def test_large_timeline_and_cross_trajectory_isolation(compiler, large_sample, sample):
    assert_full(compiler, large_sample)
    large_sample['replay']['20016'] = [{'op': 'generate', 'window': 'late', 'content': 'tail'}]
    assert_full(compiler, large_sample)
    assert assert_full(compiler, sample)[1] == 0


def test_shared_compiler_workers(compiler, sample):
    sources = []
    for i in range(12):
        source = copy.deepcopy(sample)
        source['id'] = f'worker-{i}'
        source['replay']['1'] = [{'op': 'generate', 'window': 'a', 'content': str(i)}]
        sources.append(source)
    expected = [compiler.compile(s, incremental=False) for s in sources]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(compiler.compile, sources)) == expected


def test_encoding_cache_preserves_segment_array_validation(compiler):
    event = {'op': 'generate', 'segments': [{'text': 'valid'}]}
    compiler.encode(event, 'event', .1)
    event['segments'] = tuple(event['segments'])
    with pytest.raises(CompileError, match='segments must be a nonempty array'):
        compiler.encode(event, 'event', .1)
