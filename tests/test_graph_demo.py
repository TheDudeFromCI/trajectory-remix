"""Tiny demo scheduling regressions without torch, training or measurements."""

import importlib.util
from pathlib import Path

import pytest

from trajectory_remix.graph.models import GraphSource

spec = importlib.util.spec_from_file_location(
    "graph_demo_data", Path(__file__).parents[1] / "examples" / "ktremix_training_data.py"
)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@pytest.mark.parametrize("seed,max_gap", [(5, 1), (8, 5), (17, 5)])
def test_inputs_are_externally_driven_and_header_conflict_is_explicit(seed, max_gap):
    source = GraphSource.model_validate(demo.make_source(seed, max_gap=max_gap))
    c = demo.compiler()
    frames = list(c.frames(source))
    # Every input ancestor is external; delaying/removing outputs cannot gate it.
    for event in source.events.values():
        if event.kind == "external_text":
            assert all(source.events[dep].kind == "external_text" for dep in event.after)
    assert frames[0].inputs
    first_input = frames[0].inputs[0]
    assert first_input.tokens[0].control == "ctx_begin"
    assert frames[0].outputs[0].tokens[0].control == "ctx_begin"
    assert all(len(e.tokens) == 1 for f in frames for e in f.outputs)
    stripped = source.to_dict()
    stripped["events"] = {
        i: e for i, e in stripped["events"].items() if e["kind"] == "external_text"
    }
    external_only = list(c.frames(GraphSource.model_validate(stripped)))
    assert [(f.tick, f.inputs) for f in frames if f.inputs] == [
        (f.tick, f.inputs) for f in external_only if f.inputs
    ]
    report = demo.timing_report(source, frames)
    assert report["external_inputs_independent"] is True
    assert report["header_start_tick"] == 0
    assert report["header_finish_tick"] > report["sums"][0]["input_ready_tick"]
    assert report["input_ready_timing_satisfied"] is False
    for i, row in enumerate(report["sums"]):
        assert row["compiled_letter_tick"] == row["earliest_legal_tick"]
        letter = source.events[f"letter-{i}"].content
        digit = int(source.events[f"digit-{i}"].content)
        assert source.events[f"sum-{i}"].content == chr((ord(letter) - 97 + digit) % 26 + 97)
    assert sum(t.control == "eos" for f in frames for e in f.outputs for t in e.tokens) == 1
    assert all(e.weight == 0 and not e.model for f in frames for e in f.inputs)


def test_result_gated_fixture_cannot_claim_external_input_acceptance():
    source = demo.make_source(5, max_gap=1)
    for i in range(10):
        for stream in ("letter", "digit"):
            source["events"][f"{stream}-{i}"]["after"] = ["header" if i == 0 else f"sum-{i - 1}"]
    graph = GraphSource.model_validate(source)
    report = demo.timing_report(graph, demo.compiler().frames(graph))
    assert report["input_ready_timing_satisfied"] is True  # Timing alone hides the flaw.
    assert report["external_inputs_independent"] is False
    # Even perfect nominal predictions cannot accept a result-gated fixture.
    perfect = dict(
        history=[{"validation": {"letters_accuracy": 1.0}}],
        rollout={"external_inputs_independent": False, "exact_timing_and_closure_accuracy": 1.0},
    )
    with pytest.raises(ValueError, match="depend on agent outputs"):
        demo.check_training_report(perfect)
    perfect["rollout"]["external_inputs_independent"] = True
    # Approved earliest-legal scoring need not satisfy literal input-ready timing.
    perfect["rollout"]["input_ready_timing_satisfied"] = False
    demo.check_training_report(perfect)
