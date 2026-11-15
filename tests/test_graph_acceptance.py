"""Safety/entry checks only; never execute the user-owned workload or models."""

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.user_validation import require_user_machine


def test_manual_guard_requires_opt_in_and_refuses_ci(monkeypatch):
    for key in ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "TF_BUILD"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="--user-machine"):
        require_user_machine(False)
    require_user_machine(True)
    for key in ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "TF_BUILD"):
        monkeypatch.setenv(key, "true")
        with pytest.raises(ValueError, match="CI"):
            require_user_machine(True)
        monkeypatch.delenv(key)


def test_harness_imports_have_no_io_or_provider_calls(monkeypatch):
    monkeypatch.setattr(
        GraphStore,
        "initialize",
        lambda *a, **k: pytest.fail("Imported workload initialized a project"),
    )
    for name in ("graph_workload", "graph_local_llm_smoke"):
        path = Path(__file__).parents[1] / "scripts" / (name + ".py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert callable(module.main)


def test_cli_graph_init_no_tokenizer_and_old_project_fails(tmp_path):
    path = tmp_path / "graph.sqlite"
    done = subprocess.run(
        [sys.executable, "-m", "trajectory_remix", "graph-init", "--dataset", str(path)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    assert not GraphStore(path).settings().settings.tokenizer_profiles
    old = tmp_path / "old.sqlite"
    with sqlite3.connect(old) as db:
        db.execute("CREATE TABLE project(id TEXT)")
    from trajectory_remix.server import create_app

    with pytest.raises(ValueError, match="no legacy reader"):
        create_app(old)
    invalid = subprocess.run(
        [sys.executable, "-m", "trajectory_remix", "init", "--dataset", str(path)],
        capture_output=True,
        text=True,
    )
    assert invalid.returncode != 0 and "invalid choice" in invalid.stderr
