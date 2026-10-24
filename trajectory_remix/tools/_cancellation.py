"""Helpers for built-in tools' cooperative terminal cleanup."""


def remove_pending(state, key, call_id):
    pending = state.get(key, {})
    modified = call_id in pending
    pending.pop(call_id, None)
    if not pending:
        state.pop(key, None)
    return "", True, modified
