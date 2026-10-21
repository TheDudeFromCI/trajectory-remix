#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -x .venv/bin/python ]; then
    echo "Missing .venv; run scripts/install.sh first." >&2
    exit 1
fi

.venv/bin/python -m pytest -q "$@"
