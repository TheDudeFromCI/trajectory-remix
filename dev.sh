#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v cargo >/dev/null 2>&1; then
    echo "Cargo is required to build the native compiler. Install Rust 1.89 or newer from https://rustup.rs/." >&2
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi

# The local bindings satisfy the studio's ktremix-native dependency.
.venv/bin/python -m pip install ./native/ktremix-python -e '.[test,mcp,export]'
