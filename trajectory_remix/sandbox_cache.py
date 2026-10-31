"""Bounded replay checkpoints for deterministic bundled tool invocations.

The scheduler still resolves every call. A hit restores its opaque post-call
state and window writes; anything that could change the call's inputs misses.
External tools always execute, preserving their observable side effects.
"""
import copy
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     separators=(',', ':')).encode()).digest()


def window_inputs(items, calls, writes, tick):
    # This intentionally over-approximates window_view's read set. Future tool
    # windows exist implicitly at tick zero, even before their request starts.
    visible = []
    for item in items:
        event, start = item['event'], item['tick']
        if start is not None and start <= tick:
            visible.append((start, item['order'], event, item.get('instant'),
                            item.get('rate'), item['encoded'].ids if 'encoded' in item else None))
        elif event['op'] == 'tool-call':
            visible.append(('implicit', event['window']))
    results = [(call['window'], call['order'], [
        (r['tick'], r['instant'], r['rate'], r['encoded'].ids)
        for r in call['results'] if r['tick'] <= tick])
        for call in calls if not call.get('invisible')]
    return digest((visible, results, [w for w in writes if w['tick'] <= tick]))


class SandboxReplayCache:
    MAX_BYTES = 16_000_000
    MAX_ENTRIES = 1024

    def __init__(self, compiler):
        self.compiler = compiler
        self.previous = getattr(compiler, '_sandbox_invocations', ())
        self.entries = []
        self.index = 0
        self.bytes = 0

    def run(self, name, args, state, windows, context, writes, window_key, *, cache_allowed=True):
        compiler = self.compiler
        tool = compiler.tools.get(name)
        index, self.index = self.index, self.index + 1
        module = getattr(tool.run, '__module__', None)
        eligible = (index < self.MAX_ENTRIES and self.bytes < self.MAX_BYTES
                    and cache_allowed and compiler._incremental
                    and not context.cancelled and not tool.external
                    and isinstance(module, str) and module.startswith('trajectory_remix.tools.'))
        key = None
        if eligible:
            try:
                key = digest((id(tool.run), tool.file_hash, compiler.fingerprint,
                              compiler.special, compiler.ids, context.__dict__, args, state))
            except (TypeError, ValueError):
                pass  # Opaque/custom state requires normal tool execution.
        previous = self.previous[index] if index < len(self.previous) else None
        if key is not None and previous is not None and previous['key'] == key:
            if previous['windows'] is None or previous['windows'] == window_key():
                state.clear()
                state.update(copy.deepcopy(previous['state']))
                writes.extend(copy.deepcopy(previous['writes']))
                self._append(previous)
                compiler.last_compile_reused_tool_ticks += 1
                return previous['result']
        before = len(writes)
        # Capture the read signature lazily, before a tool can write windows.
        signature = []
        original = windows._factory
        def load():
            if key is not None:
                signature.append(window_key())
            return original()
        if original is not None:
            windows._factory = load
        result = compiler.tools.run_tick(name, args, state, windows, context)
        entry = None
        if key is not None:
            candidate = {'key': key, 'windows': signature[0] if signature else None,
                         'state': state, 'writes': writes[before:], 'result': result}
            try:
                candidate['bytes'] = len(json.dumps((state, writes[before:], result), allow_nan=False).encode())
                if self.bytes + candidate['bytes'] <= self.MAX_BYTES:
                    entry = copy.deepcopy(candidate)
            except (TypeError, ValueError):
                pass
        self._append(entry)
        return result

    def _append(self, entry):
        if len(self.entries) < self.MAX_ENTRIES:
            size = entry['bytes'] if entry is not None else 0
            if self.bytes + size > self.MAX_BYTES:
                entry, size = None, 0
            self.bytes += size
            self.entries.append(entry)
