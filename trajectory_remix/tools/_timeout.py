"""Bound individual tool invocations without leaving runaway mutation threads."""

import copyreg
from contextvars import ContextVar
import multiprocessing
import signal
import threading
import time
from contextlib import contextmanager

import cloudpickle

from ._protocol import ToolError

RUN_TIMEOUT_SECONDS = 10.0


def _execution_context_variable():
    from .runtime import execution_context

    return execution_context


def _reduce_context_variable(value):
    if value is not _execution_context_variable():
        raise TypeError(
            "Tool execution can only transfer the Remix execution context variable"
        )
    return _execution_context_variable, ()


copyreg.pickle(ContextVar, _reduce_context_variable)


class _DeadlineExpired(BaseException):
    # Tool implementations commonly catch Exception; the deadline must escape them.
    pass


@contextmanager
def _deadline(seconds):
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()

    def expired(signum, frame):
        raise _DeadlineExpired()

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        remaining, interval = previous_timer
        if remaining:
            remaining = max(0.000001, remaining - (time.monotonic() - started))
        signal.setitimer(signal.ITIMER_REAL, remaining, interval)


def _worker(connection, payload):
    from .runtime import WindowProperties, execution_context

    try:
        run, args, state, properties, context = cloudpickle.loads(payload)
        windows = [WindowProperties(**item) for item in properties]
        token = execution_context.set(context)
        try:
            result = run(args, state, windows)
            # Validate before accepting mutations from the worker.
            if (
                not isinstance(result, tuple)
                or len(result) != 3
                or not isinstance(result[0], str)
                or type(result[1]) is not bool
                or type(result[2]) is not bool
            ):
                raise ToolError("run must return (str, bool, bool)")
            connection.send_bytes(
                cloudpickle.dumps(
                    (
                        True,
                        result,
                        state if result[2] else None,
                        [w.content for w in windows],
                    )
                )
            )
        finally:
            execution_context.reset(token)
    except BaseException as error:
        connection.send_bytes(cloudpickle.dumps((False, str(error), None, None)))
    finally:
        connection.close()


def _isolated(run, args, state, windows, context, seconds):
    # Spawn is portable and avoids inheriting a server's threads and open database handles.
    process_context = multiprocessing.get_context("spawn")
    receiver, sender = process_context.Pipe(duplex=False)
    properties = [dict(window) for window in windows]
    payload = cloudpickle.dumps((run, args, state, properties, context))
    process = process_context.Process(
        target=_worker, args=(sender, payload), daemon=True
    )
    try:
        process.start()
        sender.close()
        if not receiver.poll(seconds):
            raise _DeadlineExpired()
        try:
            ok, result, changed, contents = cloudpickle.loads(receiver.recv_bytes())
        except EOFError as error:
            raise ToolError("Tool worker exited without returning a result") from error
        if not ok:
            raise ToolError(result)
        if result[2]:
            state.clear()
            state.update(changed)
        for window, content in zip(windows, contents):
            if window.content != content:
                window.content = content
        return result
    finally:
        receiver.close()
        sender.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(0.5)
            if process.is_alive():
                process.kill()
                process.join()
            process.close()


def invoke(tool, args, state, windows, context):
    seconds = RUN_TIMEOUT_SECONDS
    use_alarm = (
        not tool.external
        and hasattr(signal, "setitimer")
        and threading.current_thread() is threading.main_thread()
    )
    try:
        if use_alarm:
            with _deadline(seconds):
                return tool.run(args, state, windows)
        return _isolated(tool.run, args, state, windows, context, seconds)
    except _DeadlineExpired as error:
        raise ToolError(
            f"Tool {tool.name} exceeded {seconds:g} seconds at tick {context.tick}"
        ) from error
