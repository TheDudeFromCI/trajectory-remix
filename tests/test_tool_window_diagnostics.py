"""Tool windows keep independent interactions and reasoning separate."""

from test_graph_compiler import event, profile, source

from trajectory_remix.graph.compiler import GraphCompiler
from trajectory_remix.graph.models import ProjectSettings, ToolDefinition

WINDOWS = {
    "tools": dict(purpose="tools", agent_writable=False),
    "work": dict(purpose="reasoning", agent_writable=True),
}
LISTING = '{"name":"read","parameters":{}}'


def test_fragmented_call_response_and_empty_eos_share_one_tool_window():
    graph = source(
        {
            "tools": event("tool_schema", "tools", LISTING),
            "start": event(
                "tool_call",
                "work",
                '{"path":',
                ["tools"],
                tool="read",
                call_id="r",
                insert_tool_end=False,
            ),
            "submit": event(
                "tool_call",
                "work",
                '"notes.txt"}',
                ["start"],
                tool="read",
                call_id="r",
                insert_tool_begin=False,
            ),
            "result": event("tool_response", "work", "Found it.", ["submit"], call_id="r"),
            "close": event("agent_text", "work", "", ["result"], close_window=True),
        },
        WINDOWS | {"work": dict(purpose="tool", agent_writable=True)},
    )
    assert GraphCompiler.dry_run(graph) == []


def test_distinct_calls_and_reasoning_warn_even_in_a_window_labelled_reasoning():
    graph = source(
        {
            "tools": event("tool_schema", "tools", LISTING),
            "first": event("tool_call", "work", "{}", ["tools"], tool="read", call_id="one"),
            "think": event("agent_text", "work", "Decide how to follow up.", ["first"]),
            "next": event(
                "tool_call", "work", "{}", ["think"], tool="read", call_id="two", close_window=True
            ),
        },
        WINDOWS,
    )
    settings = ProjectSettings(tools={"read": ToolDefinition(name="Read")})
    ds = GraphCompiler.dry_run(graph, settings)
    assert {(d.code, d.severity) for d in ds} == {
        ("multiple_tool_calls_in_window", "warning"),
        ("reasoning_in_tool_window", "warning"),
    }
    assert next(d for d in ds if d.code == "multiple_tool_calls_in_window").related_events == [
        "first",
        "next",
    ]
    assert next(d for d in ds if d.code == "reasoning_in_tool_window").path == [
        "events",
        "think",
        "window",
    ]
    GraphCompiler(profile(), settings=settings).compile(graph)
    settings.disabled_warnings = ["multiple_tool_calls_in_window", "reasoning_in_tool_window"]
    assert GraphCompiler.dry_run(graph, settings) == []


def test_reasoning_in_a_response_only_window_is_flagged():
    graph = source(
        {
            "tools": event("tool_schema", "tools", LISTING),
            "call": event(
                "tool_call",
                "request",
                "{}",
                ["tools"],
                tool="read",
                call_id="one",
                close_window=True,
            ),
            "result": event("tool_response", "work", "Found it.", ["call"], call_id="one"),
            "think": event(
                "agent_text", "work", "Evaluate the result.", ["result"], close_window=True
            ),
        },
        WINDOWS | {"request": dict(purpose="tool", agent_writable=True)},
    )
    assert [(d.code, d.event_id) for d in GraphCompiler.dry_run(graph)] == [
        ("reasoning_in_tool_window", "think")
    ]


def test_tools_window_opener_must_have_no_dependencies():
    events = {
        "prepare": event("agent_text", "work", "Check the available tools.", close_window=True),
        "listing": event("tool_schema", "tools", LISTING, ["prepare"]),
        "refresh": event("tool_schema", "tools", LISTING, ["listing"]),
    }
    diagnostics = GraphCompiler.dry_run(source(events, WINDOWS))
    assert [(d.code, d.severity, d.event_id, d.related_events) for d in diagnostics] == [
        ("tools_window_has_dependencies", "warning", "listing", ["prepare"])
    ]
    settings = ProjectSettings(disabled_warnings=["tools_window_has_dependencies"])
    assert GraphCompiler.dry_run(source(events, WINDOWS), settings) == []

    events["listing"]["after"] = []
    events["refresh"]["after"].append("prepare")
    assert GraphCompiler.dry_run(source(events, WINDOWS)) == []
