"""Bounded inference and deterministic stream assembly for content-first flows.

Inference tokenization deliberately has no dependency on the training compiler.
"""
from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache


@lru_cache(maxsize=16)
def inference_tokenizer(path):
    try:
        from transformers import AutoTokenizer
    except ImportError as error:
        raise ValueError('Content-first inference needs pip install trajectory-remix[inference]') from error
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    if not tokenizer.chat_template:
        raise ValueError('Inference tokenizer must include the endpoint model chat_template')
    return tokenizer


class RequestBudget:
    """Count the actual chat template, including its generation prefix.

    max_tokens is a TOTAL completion cap (visible output plus hidden reasoning).
    Endpoints must implement that OpenAI-compatible contract. No truncation or
    heuristic training-tokenizer fallback is allowed.
    """
    def __init__(self, endpoint, settings):
        self.context = min(settings.get('context_budget', 8192), endpoint.get('context_size') or settings.get('context_budget', 8192))
        self.reserve = settings.get('reserve_tokens', 256)
        self.completion = int(endpoint.get('max_tokens', 1024))
        self.parameter = endpoint.get('completion_limit_parameter', 'max_tokens')
        self.tokenizer = inference_tokenizer(endpoint.get('inference_tokenizer', ''))
        self.identity = endpoint['inference_tokenizer']
        if not hasattr(self.tokenizer, '_remix_fingerprint'):
            backend = getattr(self.tokenizer, 'backend_tokenizer', None)
            serialized = backend.to_str() if backend is not None else self.identity
            self.tokenizer._remix_fingerprint = hashlib.sha256(
                (serialized + json.dumps(self.tokenizer.chat_template, sort_keys=True)).encode()
            ).hexdigest()
        self.fingerprint = self.tokenizer._remix_fingerprint

    def check(self, messages, max_input_tokens=None, response_format=None):
        tokens = self.tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_dict=False)
        prompt = len(tokens)
        # Some servers inject response_format into their prompt; others use it
        # only as a decoding grammar. Reserve its full serialized size as an
        # additional allowance, without claiming these are measured prompt tokens.
        schema_tokens = self.output_tokens(json.dumps(response_format, ensure_ascii=False, separators=(',', ':'))) if response_format else 0
        request_input = prompt + schema_tokens
        if request_input + self.completion + self.reserve > self.context or (
            max_input_tokens is not None and request_input > max_input_tokens
        ):
            raise ValueError(
                f'Request context budget exceeded: {prompt} prompt + {schema_tokens} schema allowance + {self.completion} '
                f'completion (including reasoning) + {self.reserve} reserve > {self.context}, '
                f'or input cap {max_input_tokens}; required context was retained'
            )
        return {
            'budget_enforced': True, 'prompt_tokens_preflight': prompt, 'completion_cap': self.completion,
            'structured_output_tokens_reserved': schema_tokens, 'request_input_tokens_preflight': request_input,
            'reserved_tokens': self.reserve, 'context_budget': self.context,
            'context_utilization_reserved': (request_input + self.completion + self.reserve) / self.context,
            'inference_tokenizer': self.identity, 'inference_tokenizer_sha256': self.fingerprint,
            'prompt_token_ids_sha256': hashlib.sha256(json.dumps(tokens).encode()).hexdigest(),
            'completion_limit_parameter': self.parameter,
        }

    def output_tokens(self, text):
        return len(self.tokenizer.encode(text, add_special_tokens=False))


def validate_content(compiler, text):
    if any(s in text for s in compiler.special.values()) or '{N_TICK:' in text or '{WINDOW:' in text:
        raise ValueError('Content contains trajectory control syntax')


def assemble_streams(builder, inputs):
    """Append a causally safe batch. All starts are relative to the committed frontier.

    Content supplies language and decisions; this function supplies only protocol
    and scheduling. The compiler owns BOS, EOS, NOOP, tools and loss semantics.
    Mutations are transactional at the scenario-step boundary.
    """
    streams = inputs.get('streams', [])
    if not isinstance(streams, list) or not 1 <= len(streams) <= 128:
        raise ValueError('streams must contain 1 to 128 content streams')
    frontier = builder.next_tick
    plan = builder.compile_summary()
    windows = {w['id']: w for w in plan['windows']}
    occupied = set()
    staged = []
    for stream in streams:
        if not isinstance(stream, dict) or set(stream) - {'window', 'type', 'content', 'op', 'source', 'offset', 'close', 'loss'}:
            raise ValueError('Unknown content stream field')
        window, text = stream.get('window'), stream.get('content')
        if 'window' not in stream:
            while f'reasoning-{builder.serial}' in windows or f'reasoning-{builder.serial}' in occupied:
                builder.serial += 1
            window = f'reasoning-{builder.serial}'
            builder.serial += 1
        if not isinstance(window, str) or not window or window in occupied:
            raise ValueError('Each concurrent stream needs a distinct window')
        occupied.add(window)
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Content stream must contain nonempty text')
        # Syntax belongs to the assembler. Literal special tokens and tick anchors
        # in generated prose must be repaired, never interpreted as control flow.
        validate_content(builder.compiler, text)
        offset = stream.get('offset', 0)
        if type(offset) is not int or offset < 0:
            raise ValueError('Stream offset must be a nonnegative integer')
        op = stream.get('op', 'generate')
        if op not in ('generate', 'append'):
            raise ValueError('Stream op must be generate or append')
        if type(stream.get('close', False)) is not bool:
            raise ValueError('Stream close must be boolean')
        if op == 'append' and stream.get('close'):
            raise ValueError('Close external windows in a later generated lifecycle step')
        if op == 'generate' and window == 'chat':
            raise ValueError('Deliver assistant dialogue through a generated chat tool request')
        if op == 'append' and stream.get('source', 'user') == 'assistant':
            raise ValueError('Assistant content must be generated, not appended')
        tick = str(frontier + offset)
        events = builder.sample['replay'].setdefault(tick, [])
        first_event = len(events)
        if window not in windows:
            events.append({'op': 'open', 'window': window, 'window-type': stream.get('type', 'reasoning' if op == 'generate' else 'document')})
        elif windows[window]['close'] is not None:
            raise ValueError(f'Cannot write closed window {window}')
        if stream.get('close'):
            text += builder.compiler.special['eos']
        event = {'op': op, 'window': window, 'content': text}
        if 'loss' in stream:
            event['loss'] = copy.deepcopy(stream['loss'])
        if op == 'append':
            event.update(source=stream.get('source', 'user'), delivery='instant')
        events.append(event)
        staged.extend((tick, event) for event in events[first_event:])
    deferred = builder.defer_events(plan, staged)
    if deferred is not None:
        plan = deferred
    else:
        plan = builder.compiler.compile(builder.sample)
        builder.remember_plan(plan)
    builder.next_tick = plan['max_tick'] + 1
    return {'windows': sorted(occupied), 'start_tick': frontier, 'end_tick': plan['max_tick']}


def summarize_requests(requests, elapsed, outcome):
    inference = sum(r.get('metrics', {}).get('generation_seconds', 0) for r in requests)
    intervals = sorted((r['started_at'], r.get('finished_at', r['started_at'] + r.get('metrics', {}).get('generation_seconds', 0))) for r in requests if 'started_at' in r)
    wall, end = 0, 0
    for start, stop in intervals:
        wall += max(0, stop - max(start, end))
        end = max(end, stop)
    result = {
        'mode': 'content-first',
        'inference_wall_seconds': wall, 'overhead_seconds': max(0, elapsed - wall), 'request_count': len(requests),
        'raw_proposals': sum(bool(r.get('raw_output')) for r in requests),
        'failed_requests': sum(r.get('state') != 'complete' for r in requests),
        'elapsed_seconds': elapsed, 'inference_seconds': inference,
        # Concurrent requests overlap: summed request time can exceed wall time.
        'constructed_drafts': int(outcome == 'complete'),
        'unsuccessful_drafts': int(outcome in ('rejected', 'error', 'cancelled', 'api_paused', 'interrupted')),
        'pending_drafts': int(outcome not in ('complete', 'rejected', 'error', 'cancelled', 'api_paused', 'interrupted')),
        'outcome': outcome,
    }
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'reasoning_tokens'):
        values = [r['metrics'][key] for r in requests if key in r.get('metrics', {})]
        if values:
            result[key] = sum(values)
    result['completion_tokens_per_inference_second'] = result.get('completion_tokens', 0) / max(inference, .001)
    result['candidates_per_second'] = 1 / max(elapsed, .001)
    return result


def dataset_generation_metrics(samples):
    """Current dataset retention/review stages, grouped for pipeline comparison."""
    modes = {}
    for sample in samples:
        metadata = sample.get('metadata', {})
        if not (metadata.get('generation_record') or metadata.get('generation_metrics') or metadata.get('origin') == 'automation'):
            continue
        mode = metadata.get('generation_mode', 'existing')
        bucket = modes.setdefault(mode, {
            'retained_candidates': 0, 'constructed_drafts': 0, 'unsuccessful_drafts': 0, 'pending_drafts': 0,
            'repaired_samples': 0, 'approved_samples': 0, 'request_count': 0,
            'raw_proposals': 0, 'prompt_tokens': 0, 'completion_tokens': 0,
            'reasoning_tokens': 0, 'inference_seconds': 0, 'elapsed_seconds': 0,
        })
        bucket['retained_candidates'] += 1
        bucket['repaired_samples'] += int(bool(metadata.get('external_repairs')))
        bucket['approved_samples'] += int(metadata.get('validation_level') == 3)
        record = metadata.get('generation_record', {})
        if record:
            metrics = record['metrics']
            for key in bucket.keys() - {'retained_candidates', 'repaired_samples', 'approved_samples'}:
                bucket[key] += metrics.get(key, 0)
        else:
            bucket['elapsed_seconds'] += metadata.get('generation_elapsed_seconds', 0)
            metrics = metadata.get('generation_metrics', [])
            bucket['request_count'] += len(metrics)
            bucket['raw_proposals'] += sum(bool(r.get('raw_proposal')) for r in metrics)
            bucket['constructed_drafts'] += int(metadata.get('generation_phase') == 'complete')
            bucket['unsuccessful_drafts'] += int(metadata.get('generation_phase') in ('rejected', 'error', 'cancelled'))
            bucket['pending_drafts'] += int(metadata.get('generation_phase') == 'planning')
            for request in metrics:
                for key in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens'):
                    bucket[key] += request['metrics'].get(key, 0)
                bucket['inference_seconds'] += request['metrics'].get('generation_seconds', 0)
    for bucket in modes.values():
        elapsed = bucket['elapsed_seconds']
        bucket['candidate_throughput_per_second'] = bucket['retained_candidates'] / elapsed if elapsed else None
        bucket['usable_throughput_per_second'] = bucket['approved_samples'] / elapsed if elapsed else None
    return modes


def public_tool_contracts(schemas):
    """Keep complete argument constraints and tool purposes, without wire wrappers.

    Documentation on individual fields is redundant with their schema contract;
    executable schema keywords, defaults and every tool remain present.
    """
    def strip(value):
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if key in ('description', 'title', '$schema', '$comment', 'examples'):
                    continue
                if key in ('properties', 'patternProperties', '$defs', 'definitions', 'dependentSchemas', 'dependentRequired') and isinstance(child, dict):
                    result[key] = {name: strip(schema) for name, schema in child.items()}
                elif key in ('const', 'enum', 'default'):
                    result[key] = copy.deepcopy(child)
                else:
                    result[key] = strip(child)
            return result
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    return {entry['function']['name']: {
        'purpose': entry['function'].get('description', ''),
        'arguments': strip(entry['function']['parameters']),
    } for entry in schemas}
