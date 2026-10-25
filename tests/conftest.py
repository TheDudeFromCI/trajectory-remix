import copy
from pathlib import Path

import httpx
import pytest
from tokenizers import Tokenizer

from trajectory_remix.compiler import Compiler
from trajectory_remix.server import create_app
from trajectory_remix.storage import DatasetStore


@pytest.fixture
def compiler():
    # Historical replay fixtures retain the original policy; UUID allocation and
    # project limits are exercised independently in test_slot_limits.py.
    return Compiler(
        Tokenizer.from_file(
            str(Path(__file__).parents[1] / "examples/demo-tokenizer.json")
        ),
        slot_policy="declaration-v1",
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
def large_sample():
    """Little text, many legitimate idle records, and late replay transitions."""
    return {
        "schema-version": "1.0", "id": "large", "tick-policy": "external-first",
        "replay": {
            "0": [{"op": "open", "window": w} for w in ("a", "b", "c")],
            "1": [
                {"op": "append", "source": "user", "window": "a", "content": "earlier"},
                {"op": "append", "source": "user", "window": "b", "content": "old"},
                {"op": "append", "source": "user", "window": "c", "content": "context"},
            ],
            "20000": [{"op": "generate", "window": "a", "segments": [
                {"text": "x", "loss": {"weight": 0.25}},
                {"text": "y", "loss": {"mask": "exclude"}},
                {"text": "z{N_TICK:7}<|eos|>"},
            ]}],
            "20001": [{"op": "invisible-tool-call", "tool": "file_write",
                       "arguments": {"path": "x", "content": "new"}, "duration-ticks": 3}],
            "20002": [{"op": "append", "source": "user", "window": "b", "content": "replacement", "replace-content": True}],
            "20005": [{"op": "tool-call", "tool": "file_read", "window": "tool",
                       "arguments": {"path": "missing"}, "content": "x", "duration-ticks": 2}],
            "20010": [{"op": "generate", "window": "c", "content": "<|eos|>"}],
            "20011": [{"op": "open", "window": "late"},
                       {"op": "append", "source": "user", "window": "late", "content": "late"}],
            "20015": [{"op": "append", "source": "user", "window": "b", "content": "tail"}],
        },
    }


@pytest.fixture
async def studio(compiler, tmp_path, sample):
    DatasetStore.initialize(tmp_path / "project.kidra-dataset", compiler)
    app = create_app(tmp_path / "project.kidra-dataset")
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
