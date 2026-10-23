import json
from pathlib import Path

import pytest
from tokenizers import Tokenizer

from trajectory_remix.__main__ import main
from trajectory_remix.compiler import Compiler
from trajectory_remix.server import create_app
from trajectory_remix.storage import DatasetStore


def test_init_embeds_tokenizer_and_special_tokens_for_future_launches(tmp_path, monkeypatch):
    tokenizer_path = tmp_path / "tokenizer.json"
    tokenizer_path.write_bytes((Path(__file__).parents[1] / "examples/demo-tokenizer.json").read_bytes())
    expected = Compiler(Tokenizer.from_file(str(tokenizer_path)), bos="<|eos|>", eos="<|bos|>")
    project = tmp_path / "project.sqlite"
    main(["init", "--dataset", str(project), "--tokenizer", str(tokenizer_path), "--bos", "<|eos|>", "--eos", "<|bos|>"])
    store = DatasetStore(project)
    with store.connect() as db:
        embedded, special = db.execute("SELECT tokenizer,special_tokens FROM project").fetchone()
        assert json.loads(special) == expected.special
        assert Tokenizer.from_str(embedded).to_str() == expected.tokenizer.to_str()
    tokenizer_path.unlink()

    launches = []
    monkeypatch.setattr("uvicorn.run", lambda app, **options: launches.append(app))
    main(["--dataset", str(project)])
    main(["--dataset", str(project)])
    for app in launches:
        compiler = app.state.commands.compiler
        assert compiler.fingerprint == expected.fingerprint
        assert compiler.special == expected.special
    assert len(launches) == 2


@pytest.mark.parametrize("export", [False, True])
def test_normal_commands_do_not_create_projects(tmp_path, export):
    project = tmp_path / "missing-directory" / "project.sqlite"
    args = ["--dataset", str(project)]
    if export:
        args.extend(["--export", str(tmp_path / "export.jsonl")])
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 1
    assert not project.parent.exists()
    assert not (tmp_path / "export.jsonl").exists()
    with pytest.raises(ValueError, match="run .*init"):
        create_app(project)


def test_reinitializing_never_replaces_project_or_tokenizer(tmp_path, compiler):
    project = tmp_path / "project.sqlite"
    store = DatasetStore.initialize(project, compiler)
    store.samples.append({"id": "keep-me"})
    initial = project.read_bytes()
    with pytest.raises(SystemExit) as error:
        main(["init", "--dataset", str(project), "--tokenizer", "examples/demo-tokenizer.json", "--bos", "<|eos|>", "--eos", "<|bos|>"])
    assert error.value.code == 1
    assert project.read_bytes() == initial
    assert store.samples[0] == {"id": "keep-me"}
    assert store.load_compiler().special == compiler.special


@pytest.mark.parametrize("tokenizer,bos", [
    ("missing.json", "<|bos|>"),
    ("examples/demo-tokenizer.json", "missing-bos-token"),
])
def test_invalid_init_does_not_leave_a_database(tmp_path, tokenizer, bos):
    project = tmp_path / "project.sqlite"
    with pytest.raises(SystemExit) as error:
        main(["init", "--dataset", str(project), "--tokenizer", tokenizer, "--bos", bos])
    assert error.value.code == 1
    assert not project.exists()


@pytest.mark.parametrize("option,value", [
    ("--tokenizer", "examples/demo-tokenizer.json"),
    ("--bos", "<|eos|>"),
    ("--eos", "<|bos|>"),
    ("--noop", "different-noop"),
])
def test_existing_projects_cannot_override_tokenizer_at_launch(tmp_path, compiler, option, value):
    project = tmp_path / "project.sqlite"
    DatasetStore.initialize(project, compiler)
    with pytest.raises(SystemExit) as error:
        main(["--dataset", str(project), option, value])
    assert error.value.code == 2
    assert DatasetStore(project).load_compiler().fingerprint == compiler.fingerprint
