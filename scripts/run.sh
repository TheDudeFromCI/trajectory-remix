#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -x .venv/bin/python ]; then
    echo "Missing .venv; run scripts/install.sh first." >&2
    exit 1
fi

# Opens the initialized demo project by default; pass init or other args explicitly.
if [ "$#" -eq 0 ]; then
    set -- --dataset demo.sqlite
fi

exec .venv/bin/python -m trajectory_remix "$@"
