"""Assembly replies: unambiguous slips about chat deliveries and shapes are settled."""

import pytest
from test_draft_assembly import spec
from test_graph_generation import service

from trajectory_remix.graph import compact_spec, spec_normalize

BASE = dict(format="kidra-trajectory-graph/1", id="7d3f8a2e-1b4c-4e5f-9a6b-0c1d2e3f4a5b")


def tools(tmp_path):
    gen, _ = service(tmp_path)
    return {k: v.model_dump(mode="json") for k, v in gen.store.settings().settings.tools.items()}


def test_echo_of_an_event_without_a_delivery_means_the_event(tmp_path):
    body = spec()
    body["events"][1]["after"] = ["ask:echo"]  # ask is a person's message: no delivery.
    body["events"][4]["after"] = ["look:echo"]  # look is a read call: no delivery.
    data = compact_spec.build(body, BASE, tools(tmp_path))
    assert data["events"]["ack"]["after"] == ["ask"]
    assert data["events"]["got"]["after"] == ["look"]


def test_real_chat_deliveries_are_kept(tmp_path):
    body = spec()
    body["events"][2]["after"] = ["ack:echo"]
    data = compact_spec.build(body, BASE, tools(tmp_path))
    assert data["events"]["plan"]["after"] == ["ack:echo"]


def test_written_echo_events_tools_dicts_and_wrappers_are_settled():
    body = spec()
    body["events"].insert(2, dict(id="ack:echo", kind="document", window="room", after=["ack"]))
    body["tools"] = {"chat": {"name": "Chat"}, "read": {"name": "Read"}}
    fixed = spec_normalize.normalize({"spec": body})
    assert fixed["tools"] == ["chat", "read"]
    assert all(not e["id"].endswith(":echo") for e in fixed["events"])


def test_ambiguous_replies_are_left_alone():
    assert spec_normalize.normalize({"a": 1, "b": 2}) == {"a": 1, "b": 2}
    assert spec_normalize.normalize(["not", "a", "dict"]) == ["not", "a", "dict"]


@pytest.mark.parametrize(
    "after, message",
    [(["tell"], "listed after it"), (["nowhere"], "Only chat calls have a delivery")],
)
def test_builder_says_how_to_fix_a_bad_reference(tmp_path, after, message):
    body = spec()
    body["events"][2] = dict(body["events"][2], after=after)
    with pytest.raises(ValueError, match=message):
        compact_spec.build(body, BASE, tools(tmp_path))
