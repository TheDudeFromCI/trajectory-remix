#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

compile_rust=true
if [[ "${1:-}" == "--no-rust-compile" ]]; then
    compile_rust=false
    shift
fi
if (( $# > 0 )); then
    echo "Usage: scripts/install.sh [--no-rust-compile]" >&2
    exit 2
fi

install_args=(-e '.[test,mcp,export]')
if "$compile_rust"; then
    if ! command -v cargo >/dev/null 2>&1; then
        echo "Cargo is required to build the native compiler. Install Rust 1.89 or newer from https://rustup.rs/." >&2
        exit 1
    fi
    # The local bindings satisfy the studio's ktremix-native dependency.
    install_args=(./native/ktremix-python "${install_args[@]}")
fi

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi

exec .venv/bin/python -m pip install "${install_args[@]}"
