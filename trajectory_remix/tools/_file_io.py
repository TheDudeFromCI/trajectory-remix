"""File tools own latency configuration and pending execution state."""

from .runtime import execution_context
from ._support import run_builtin
from ._access import io_failure
from ._cancellation import remove_pending


def run_file_builtin(args, state, windows, operation, *, modifies=False):
    context = execution_context.get()
    if context.cancelled:
        return remove_pending(state, "file_io_pending", context.call_id)
    if context.elapsed == 0:
        failed = io_failure(state, operation.__module__.rsplit(".", 1)[-1], args)
        if failed is not None:
            return failed
    pending = state.get("file_io_pending", {})
    call = pending.get(context.call_id)
    modified = False
    if call is None:
        # Never resample an already-started call when load changes mid-flight.
        if context.elapsed != 0:
            return run_builtin(args, state, windows, operation, modifies=modifies)
        config = state.get("hardware_load", {})
        tool_name = operation.__module__.rsplit(".", 1)[-1]
        delay = config.get("operation_ticks", {}).get(
            tool_name, config.get("file_io_ticks", 0)
        )
        if not delay:
            return run_builtin(args, state, windows, operation, modifies=modifies)
        call = {"finish_tick": context.start + context.duration + delay}
        state.setdefault("file_io_pending", {})[context.call_id] = call
        modified = True
    if context.tick < call["finish_tick"]:
        return "", False, modified
    del pending[context.call_id]
    if not pending:
        state.pop("file_io_pending", None)
    output, done, _ = run_builtin(args, state, windows, operation, modifies=modifies)
    return output, done, True
