"""The selector sees closed windows as outlines, so long episodes stop growing its prompt."""

import json

from trajectory_remix.graph import selector_view
from trajectory_remix.graph.adaptive_driver import CLOSING, request_payload
from trajectory_remix.graph.adaptive_flow import load_flow, selection_payload
from trajectory_remix.graph.models import GraphSource, ProjectSettings

LONG = "Detailed working notes. " * 40


def episode(steps):
    """An opening, a notes window, and `steps` reasoning windows that each close."""
    windows = {
        "chat": {"purpose": "chatroom", "agent_writable": False},
        "notes": {"purpose": "notes", "agent_writable": True},
    }
    events = {
        "opening": dict(kind="chat_message", window="chat", content="Do it.", chat={"speaker": "A"})
    }
    last = "opening"
    for n in range(steps):
        window = f"w_{n}"
        windows[window] = {"purpose": "reasoning", "agent_writable": True}
        events[f"think_{n}"] = dict(kind="agent_text", window=window, after=[last], content=LONG)
        events[f"note_{n}"] = dict(
            kind="agent_text", window="notes", after=[f"think_{n}"], content=f"Fact {n}."
        )
        events[f"close_{n}"] = dict(
            kind="agent_text",
            window=window,
            after=[f"note_{n}"],
            content="Done.",
            close_window=True,
        )
        last = f"close_{n}"
    return GraphSource.model_validate(
        {
            "format": "kidra-trajectory-graph/1",
            "id": "6ca0a6d8-c7aa-46b8-b931-bfcdf29e1d3b",
            "windows": windows,
            "events": events,
        }
    )


def test_closed_windows_keep_their_shape_but_not_their_content():
    source = episode(2)
    view = json.loads(selector_view.source_view(source))
    assert view["windows"]["w_0"]["closed"] and "closed" not in view["windows"]["notes"]
    think = view["events"]["think_0"]
    assert think["content"] == selector_view.WRITTEN
    assert think["after"] == ["opening"] and think["window"] == "w_0"
    assert view["events"]["note_0"]["content"] == "Fact 0."  # Notes stay open.
    assert list(view["events"])[-1] == "close_1"  # Still causal order.


def test_open_windows_reports_only_what_is_still_open():
    source = episode(1)
    data = source.to_dict()
    data["windows"]["w_live"] = {"purpose": "reasoning", "agent_writable": True}
    data["events"]["live"] = dict(
        kind="agent_text", window="w_live", after=["close_0"], content="abc"
    )
    report = selector_view.open_windows(GraphSource.model_validate(data))
    assert set(report) == {"chat", "notes", "w_live"}
    assert report["w_live"] == dict(purpose="reasoning", events=1, chars=3)


def test_selector_prompt_does_not_grow_with_closed_work():
    plan = dict(flow=load_flow()["flow"], tools={}, proposal={})
    plan["flow"]["max_events"] = 500

    def size(steps):
        current = dict(node="work", steps=0, output=None, task=None)
        return len(selection_payload(episode(steps), plan, current)["messages"][1]["content"])

    # Each closed step adds its outline and a note, not its long reasoning.
    per_step = (size(12) - size(2)) / 10
    assert per_step < len(LONG) / 2, per_step
    body = json.loads(
        selection_payload(episode(1), plan, dict(node="work", steps=0))["messages"][1]["content"]
    )
    assert set(body["open_windows"]) == {"chat", "notes"}


def test_a_closing_agent_event_is_told_its_text_will_not_be_seen_again():
    source = episode(1)
    settings = ProjectSettings()
    plan = dict(flow=load_flow()["flow"], tools={}, proposal={})
    data = source.to_dict()
    data["windows"]["w_live"] = {"purpose": "reasoning", "agent_writable": True}
    source = GraphSource.model_validate(data)

    def instruction(close):
        envelope = dict(kind="agent_text", window="w_live", after=["close_0"], close_window=close)
        task = dict(id="t", envelope=envelope, instruction="Act.", world={}, arguments="text")
        cp = dict(flow=dict(node="write_thought", steps=0, task=task), errors=0)
        return request_payload(source, cp, plan, settings)["task"]["instruction"]

    assert CLOSING in instruction(True) and CLOSING not in instruction(False)


def test_default_flow_teaches_short_lived_windows():
    flow = load_flow()["flow"]
    work = flow["nodes"]["work"]["instruction"]
    assert "NEW reasoning window" in work and "close_window" in work and "notes" in work
    assert "notes window" in flow["nodes"]["set_scene"]["instruction"]
    assert "Keep decision and evidence windows open" not in json.dumps(flow)
