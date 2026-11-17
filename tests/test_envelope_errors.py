"""Selected events: a missing call ID is filled in, and other errors read as short fixes."""

import pytest
from test_adaptive_flow import selection

from trajectory_remix.graph.adaptive_flow import accept_selection, load_flow
from trajectory_remix.graph.models import GraphSource, ProjectSettings

CHAT = {
    "name": "Chat",
    "input_schema": {"type": "object", "properties": {"content": {"type": "string"}}},
}


def opened():
    return GraphSource.model_validate(
        {
            "format": "kidra-trajectory-graph/1",
            "id": "6ca0a6d8-c7aa-46b8-b931-bfcdf29e1d3b",
            "windows": {"room": {"purpose": "chatroom", "agent_writable": False}},
            "events": {
                "opening": dict(
                    kind="chat_message", window="room", content="Hi", chat={"speaker": "A"}
                )
            },
        }
    )


def accept(body):
    settings = ProjectSettings.model_validate(dict(tools={"chat": CHAT}))
    plan = dict(flow=load_flow()["flow"], tools={"chat": CHAT}, proposal={})
    return accept_selection(opened(), body, plan, settings)


REPLY = dict(windows={"w_reply": dict(purpose="reasoning", agent_writable=True)}, tool="chat")


def test_a_tool_call_without_a_call_id_uses_its_event_id():
    _, task, chosen = accept(selection("ack", "tool_call", "w_reply", ["opening"], **REPLY))
    assert task["envelope"]["call_id"] == "ack"
    assert chosen["task"]["envelope"]["call_id"] == "ack"


def test_envelope_errors_name_the_field_the_selector_writes():
    no_tool = dict(REPLY, tool=None)
    bad = selection("ack", "tool_call", "w_reply", ["opening"], **no_tool)
    with pytest.raises(ValueError) as caught:
        accept(bad)
    text = str(caught.value)
    assert text.startswith("Selected event invalid: task.envelope.tool:")
    assert "errors.pydantic.dev" not in text and "placeholder" not in text


def test_complete_catalogue_calls_are_written_as_json_arguments():
    _, task, _ = accept(selection("ack", "tool_call", "w_reply", ["opening"], **REPLY))
    assert task["arguments"] == "json"  # The selector's default "text" is overridden.
    from trajectory_remix.graph.selection_fixes import argument_mode

    part = dict(kind="tool_call", tool="chat", insert_tool_end=False)
    assert argument_mode(part, {"chat": CHAT}, "text") == "text"  # Fragments stay text.
    assert argument_mode(dict(kind="tool_call", tool="nope"), {"chat": CHAT}, "text") == "text"


def test_window_reasoning_levels_are_matched_or_dropped():
    from trajectory_remix.graph.selection_fixes import reasoning_level

    levels = ["Low", "Medium", "High"]
    assert reasoning_level("medium", levels) == "Medium"
    assert reasoning_level("light", levels) is None
    assert reasoning_level(None, levels) is None
    windows = {"w_reply": dict(purpose="reasoning", agent_writable=True, reasoning_effort="light")}
    body = selection("ack", "tool_call", "w_reply", ["opening"], **dict(REPLY, windows=windows))
    source, _, _ = accept(body)
    assert source.windows["w_reply"].reasoning_effort is None


def closed_source():
    data = opened().to_dict()
    data["windows"]["w_read"] = dict(purpose="reasoning", agent_writable=True)
    data["events"]["read"] = dict(
        kind="tool_call",
        window="w_read",
        after=["opening"],
        tool="chat",
        call_id="read",
        content='{"content": "x"}',
        close_window=True,
    )
    return GraphSource.model_validate(data)


def test_closed_and_repeated_windows_get_corrections_that_say_what_to_do():
    from trajectory_remix.graph.selection_fixes import new_windows

    source = closed_source()
    reply = dict(kind="tool_response", window="w_read", call_id="read")
    with pytest.raises(ValueError, match="w_read is closed .* for example w_read_2"):
        new_windows(source, {}, reply, {"w_read"})
    room = dict(purpose="chatroom", agent_writable=False)
    assert new_windows(source, {"room": room}, dict(window="w_x"), {"w_read"}) == {}
    with pytest.raises(ValueError, match="leave it out of windows"):
        new_windows(source, {"room": dict(room, agent_writable=True)}, {}, set())


def test_events_follow_their_window_and_results_find_their_call():
    from trajectory_remix.graph.selection_fixes import follow_window, response_call

    data = opened().to_dict()
    data["windows"]["w"] = dict(purpose="reasoning", agent_writable=True)
    data["events"]["a"] = dict(kind="agent_text", window="w", after=["opening"], content="a")
    data["events"]["b"] = dict(kind="agent_text", window="w", after=["a"], content="b")
    data["events"]["call"] = dict(
        kind="tool_call", window="w", after=["b"], tool="chat", call_id="c1", content="{}"
    )
    source = GraphSource.model_validate(data)
    envelope = dict(kind="agent_text", window="w", after=["opening"])
    follow_window(source, envelope)
    assert envelope["after"] == ["opening", "call"]
    already = dict(kind="agent_text", window="w", after=["call"])
    follow_window(source, already)
    assert already["after"] == ["call"]
    result = dict(kind="tool_response", window="r", after=["call"])
    response_call(source, result)
    assert result["call_id"] == "c1"


def test_hand_written_chat_deliveries_are_refused():
    body = selection("ack:echo_2", "chat_message", "room", ["opening"], chat={"speaker": "Kidra"})
    with pytest.raises(ValueError, match="written automatically"):
        accept(body)
