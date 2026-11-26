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
    for name in ("scripts/dev.sh", "scripts/install.sh", "scripts/run.sh"):
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
        [repo / "scripts/dev.sh", *args], cwd=repo.parent, capture_output=True, text=True
    )

    assert result.returncode == (pull_code or install_code), result.stderr
    expected = ["pull", "--ff-only"]
    if not pull_code:
        expected += PIP_ARGS + [LOCAL_BINDINGS] + EXTRAS
        if not install_code:
            expected += ["-m", "trajectory_remix", *args]
    assert result.stdout.splitlines() == expected


@pytest.mark.parametrize("with_env", [False, True])
def test_dev_loads_optional_root_env_for_child_processes(checkout, monkeypatch, with_env):
    repo, tools = checkout
    monkeypatch.delenv("DEV_TEST_VALUE", raising=False)
    if with_env:
        (repo / ".env").write_text('# Local settings\nDEV_TEST_VALUE="value with spaces"\n')
    # An unrelated working directory's .env must not be loaded.
    (repo.parent / ".env").write_text('DEV_TEST_VALUE="wrong directory"\n')
    report = 'printf "%s\\n" "$PWD" "${DEV_TEST_VALUE-unset}"'
    executable(tools / "git", report)
    executable(repo / "scripts/install.sh", report)
    executable(repo / "scripts/run.sh", report)

    result = subprocess.run(
        [repo / "scripts/dev.sh"], cwd=repo.parent, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    expected_value = "value with spaces" if with_env else "unset"
    assert result.stdout.splitlines() == [str(repo), expected_value] * 3
