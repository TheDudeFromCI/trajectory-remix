from pathlib import Path
import pytest
from tokenizers import Tokenizer
from trajectory_remix.compiler import Compiler

@pytest.fixture
def compiler():
    return Compiler(Tokenizer.from_file(str(Path(__file__).parents[1] / "examples/demo-tokenizer.json")))

@pytest.fixture
def sample():
    return {"schema-version": "1.0", "id": "test", "tick-policy": "external-first", "replay": {"0": [{"op": "open", "window": "a"}, {"op": "open", "window": "b"}]}}
