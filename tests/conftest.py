import copy
from pathlib import Path

import httpx
import pytest
from tokenizers import Tokenizer

from trajectory_remix.compiler import Compiler
from trajectory_remix.server import create_app


@pytest.fixture
def compiler():
    return Compiler(
        Tokenizer.from_file(
            str(Path(__file__).parents[1] / "examples/demo-tokenizer.json")
        )
    )


@pytest.fixture
def sample():
    return {
        "schema-version": "1.0",
        "id": "test",
        "tick-policy": "external-first",
        "replay": {"0": [{"op": "open", "window": "a"}, {"op": "open", "window": "b"}]},
    }


@pytest.fixture
async def studio(compiler, tmp_path, sample):
    app = create_app(compiler, tmp_path / "project.kidra-dataset")
    store = app.state.dataset.store
    store.replace([copy.deepcopy(sample)], 0)
    with store.connect() as db:
        db.execute("UPDATE project SET revision=0,sample_revision=0")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
        headers={"X-Remix-Token": app.state.csrf},
    ) as client:
        yield app, client
