import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PIP_ARGS = ["-m", "pip", "install"]
EXTRAS = ["-e", ".[test,mcp,export]"]
LOCAL_BINDINGS = "./native/ktremix-python"


def executable(path, body):
    path.write_text(f"#!/bin/bash\n{body}\n")
    path.chmod(0o755)


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    for name in ("dev.sh", "scripts/install.sh", "scripts/run.sh"):
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)

    tools = tmp_path / "bin"
    tools.mkdir()
    for name in ("bash", "dirname"):
        (tools / name).symlink_to(shutil.which(name))
    monkeypatch.setenv("PATH", str(tools))

    python = checkout / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    executable(python, 'printf "%s\\n" "$@"')
    return checkout, tools


@pytest.mark.parametrize("compile_rust", [True, False])
def test_install_chooses_native_source_or_pip(checkout, compile_rust):
    repo, tools = checkout
    args = []
    expected = PIP_ARGS + EXTRAS
    if compile_rust:
        executable(tools / "cargo", "exit 0")
        expected = PIP_ARGS + [LOCAL_BINDINGS] + EXTRAS
    else:
        args.append("--no-rust-compile")

    result = subprocess.run(
        [repo / "scripts/install.sh", *args], cwd=repo.parent, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == expected


@pytest.mark.parametrize("pull_code, install_code", [(0, 0), (3, 0), (0, 4)])
def test_dev_pulls_installs_and_passes_arguments(checkout, pull_code, install_code):
    repo, tools = checkout
    executable(tools / "git", f'printf "%s\\n" "$@"\nexit {pull_code}')
    executable(tools / "cargo", "exit 0")
    python = repo / ".venv/bin/python"
    executable(python, f'printf "%s\\n" "$@"\nif [[ "$2" == "pip" ]]; then exit {install_code}; fi')
    args = ["--dataset", "project with spaces.sqlite", "--port", "8766"]

    result = subprocess.run(
        [repo / "dev.sh", *args], cwd=repo.parent, capture_output=True, text=True
    )

    assert result.returncode == (pull_code or install_code), result.stderr
    expected = ["pull", "--ff-only"]
    if not pull_code:
        expected += PIP_ARGS + [LOCAL_BINDINGS] + EXTRAS
        if not install_code:
            expected += ["-m", "trajectory_remix", *args]
    assert result.stdout.splitlines() == expected
