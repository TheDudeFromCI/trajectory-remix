"""The selector's schema describes the envelope, so small models need not guess field names."""

import json
from pathlib import Path

import jsonschema
import pytest
from test_graph_generation import service

from trajectory_remix.graph.adaptive_flow import accept_selection, frozen_plan, load_flow
from trajectory_remix.graph.models import GraphSource
from trajectory_remix.graph.selection_schema import prompt_schema

# A real first reply from a small local model: invented envelope fields and a "when".
INVENTED = json.loads(
    (Path(__file__).parent / "fixtures" / "selector_invented_envelope.json").read_text()
)


def empty_source():
    return GraphSource.model_validate(
        dict(format="kidra-trajectory-graph/1", id="00000000-0000-4000-8000-000000000000")
    )


def check(body):
    jsonschema.Draft202012Validator(prompt_schema()).validate(body)


def test_schema_describes_every_envelope_field_without_titles():
    text = json.dumps(prompt_schema())
    envelope = prompt_schema()["$defs"]["Envelope"]
    assert envelope["required"] == ["kind", "window"]
    assert set(envelope["properties"]["kind"]["enum"]) == {
        "agent_text",
        "external_text",
        "tool_call",
        "tool_response",
        "chat_message",
    }
    assert all("description" in field for field in envelope["properties"].values())
    assert '"title"' not in text and "when" not in envelope["properties"]
    assert "when" not in prompt_schema()["$defs"]["SelectedTask"]["properties"]
    assert len(text) < 4500  # Every selector request carries it.


def test_window_purpose_is_described_with_its_special_values():
    window = prompt_schema()["$defs"]["NewWindow"]["properties"]
    assert "chatroom" in window["purpose"]["description"]
    assert "agent_writable" in prompt_schema()["$defs"]["NewWindow"]["required"]


def test_a_chat_window_without_the_chatroom_purpose_explains_the_fix(tmp_path):
    gen, _ = service(tmp_path)
    settings = gen.store.settings().settings
    plan = frozen_plan({}, settings, load_flow())
    reply = dict(
        finished=False,
        reason="Open with the user",
        windows={"room": dict(purpose="User channel", agent_writable=False)},
        task=dict(
            id="opening", envelope=dict(kind="chat_message", window="room", chat={"speaker": "u"})
        ),
    )
    with pytest.raises(ValueError, match='purpose is "chatroom"'):
        accept_selection(empty_source(), reply, plan, settings)


def test_invented_envelope_is_rejected_with_a_correction_naming_the_problem():
    with pytest.raises(jsonschema.ValidationError) as caught:
        check(INVENTED)
    message = caught.value.message
    assert "kind" in message or "type" in message or "when" in message


def test_the_corrected_reply_is_accepted(tmp_path):
    gen, _ = service(tmp_path)
    settings = gen.store.settings().settings
    plan = frozen_plan({}, settings, load_flow())
    fixed = json.loads(json.dumps(INVENTED))
    task = fixed["task"]
    task.pop("when")
    task["id"] = "opening"
    task["envelope"] = dict(kind="chat_message", window="chatroom", chat={"speaker": "user"})
    fixed["windows"]["chatroom"]["purpose"] = "chatroom"  # The second thing the model guessed.
    check(fixed)
    source, accepted, selection = accept_selection(empty_source(), fixed, plan, settings)
    assert accepted["envelope"] == {
        "kind": "chat_message",
        "window": "chatroom",
        "chat": {"speaker": "user"},
        "after": [],
    }
    assert accepted["world"]["user"]["name"] == "Dr. Aris Thorne"  # World facts survive.
    assert selection["task"]["envelope"]["kind"] == "chat_message"  # Branches read this.
    assert set(source.windows) == {"chatroom", "reasoning", "task_board", "agent_reports"}
