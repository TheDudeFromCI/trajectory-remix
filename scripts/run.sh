#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -x .venv/bin/python ]; then
    echo "Missing .venv; run scripts/install.sh first." >&2
    exit 1
fi

# Defaults to the demo tokenizer; pass your own args to override.
if [ "$#" -eq 0 ]; then
    set -- --tokenizer examples/demo-tokenizer.json --dataset demo.kidra-dataset
fi

exec .venv/bin/python -m trajectory_remix "$@"
