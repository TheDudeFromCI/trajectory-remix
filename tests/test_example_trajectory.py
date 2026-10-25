"""Keep the checked-in demonstration available to MCP-only installations."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bundled_example_matches_repository_copy():
    source = json.loads((ROOT / "examples/build-graph-analysis.json").read_text())
    bundled = json.loads(
        (Path(__file__).resolve().parents[1] / "trajectory_remix/build-graph-analysis.json").read_text()
    )
    assert source == bundled
    assert source["schema-version"] == "1.0"
    assert source["replay"]["2"][0]["op"] == "tool-call"
    assert source["replay"]["2"][1]["op"] == "tool-call"
    assert source["replay"]["2"][0]["window"] != source["replay"]["2"][1]["window"]
    assert [e["op"] for e in source["replay"]["0"]].count("invisible-tool-call") == 7
    assert "2246" in source["replay"]
    assert source["replay"]["2246"][-1]["content"] == "<|eos|>"
    # A character-level tokenizer cannot reproduce the event durations of this
    # model-generated sample. Compilation must use a compatible tokenizer.
