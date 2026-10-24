"""Rename a virtual file, using the same atomic semantics as file_move."""

from ._file_io import run_file_builtin
from ._file_transfer import SCHEMA, transfer

name = "file_rename"
description = "Rename a virtual file using source/destination paths. Same behavior as file_move; overwrite is false by default."
schema = SCHEMA


def _run(runtime, args, windows):
    return transfer(runtime, args, move=True)


def run(args, state, windows):
    return run_file_builtin(args, state, windows, _run, modifies=True)
