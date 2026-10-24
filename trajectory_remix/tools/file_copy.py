"""Copy one virtual file, retaining the source and its history."""

from ._file_io import run_file_builtin
from ._file_transfer import SCHEMA, transfer

name = "file_copy"
description = "Copy a virtual file. Destination must be absent unless overwrite=true. expected_version checks the source."
schema = SCHEMA


def _run(runtime, args, windows):
    return transfer(runtime, args, move=False)


def run(args, state, windows):
    return run_file_builtin(args, state, windows, _run, modifies=True)
