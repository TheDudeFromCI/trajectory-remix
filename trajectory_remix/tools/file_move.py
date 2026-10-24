"""Move one virtual file atomically."""

from ._file_io import run_file_builtin
from ._file_transfer import SCHEMA, transfer

name = "file_move"
description = "Move a virtual file atomically. Destination must be absent unless overwrite=true. expected_version checks the source. Historical versions remain readable."
schema = SCHEMA


def _run(runtime, args, windows):
    return transfer(runtime, args, move=True)


def run(args, state, windows):
    return run_file_builtin(args, state, windows, _run, modifies=True)
