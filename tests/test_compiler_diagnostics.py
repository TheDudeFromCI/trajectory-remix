"""Verify structural diagnostics through source edits and token compilation."""

import pytest
from test_graph_compiler import event, profile, source

from trajectory_remix.graph import GraphCommands, GraphStore, diagnose
from trajectory_remix.graph.compiler import CompileError, GraphCompiler


def test_duplicate_dependency_warns_without_changing_the_schedule(tmp_path):
    graph = source(
        {
            "input": event("external_text", "in"),
            "output": event("agent_text", "out", after=["input", "input"], close_window=True),
        }
    )
    commands = GraphCommands(GraphStore.initialize(tmp_path / "project.sqlite"))
    saved = commands.create(graph.to_dict())
    diagnostics = GraphCompiler.dry_run(graph)
    assert diagnostics == diagnose(graph) == saved.diagnostics
    assert [(d.severity, d.code, d.path) for d in diagnostics] == [
        ("warning", "duplicate_dependency", ["events", "output", "after"])
    ]
    assert not saved.has_errors

    deduplicated = commands.set_dependencies(graph.id, "output", ["input"], saved.record.revision)
    assert not deduplicated.diagnostics
    compiler = GraphCompiler(profile())
    assert list(compiler.frames(graph)) == list(compiler.frames(deduplicated.record.source))
    assert compiler.compile(graph)[0] == compiler.compile(deduplicated.record.source)[0]


def test_cycle_reports_every_member_excluding_blocked_descendants():
    graph = source(
        {
            "a": event("agent_text", "a", after=["b", "c"]),
            "b": event("external_text", "b", after=["a"]),
            "c": event("external_text", "c", after=["b"]),
            "descendant": event("external_text", "descendant", after=["c"]),
            "self": event("external_text", "self", after=["self"]),
        }
    )

    assert [(d.code, d.event_id) for d in GraphCompiler.dry_run(graph)] == [
        ("dependency_cycle", ident) for ident in ("a", "b", "c", "self")
    ]
    with pytest.raises(CompileError, match="dependency_cycle"):
        GraphCompiler(profile()).compile(graph)


@pytest.mark.parametrize("kind", ["tool_call", "tool_response"])
@pytest.mark.parametrize("same_window", [True, False])
def test_parallel_fragments_use_window_order_and_consistency_diagnostics(kind, same_window):
    if kind == "tool_call":
        events = {
            "first": event(
                kind, "w", '{"query":', call_id="request", tool="example", insert_tool_end=False
            ),
            "second": event(
                kind,
                "w" if same_window else "other",
                '"example"}',
                call_id="request",
                tool="example",
                insert_tool_begin=False,
            ),
        }
        inconsistent = "inconsistent_call"
    else:
        events = {
            "request": event(
                "tool_call", "tool", '{"query":"example"}', call_id="request", tool="example"
            ),
            "first": event(
                kind, "w", after=["request"], call_id="request", insert_response_end=False
            ),
            "second": event(
                kind,
                "w" if same_window else "other",
                after=["request"],
                call_id="request",
                insert_response_begin=False,
            ),
        }
        inconsistent = "inconsistent_response"

    windows = {
        definition["window"]: dict(purpose="document", agent_writable=True)
        for definition in events.values()
    }
    windows["tools"] = dict(purpose="tools", agent_writable=False)
    events["tools"] = event("tool_schema", "tools", '{"name":"example","parameters":{}}')
    for definition in events.values():
        if definition["kind"] == "tool_call":
            definition["after"].append("tools")
    graph = source(events, windows)
    diagnostics = GraphCompiler.dry_run(graph)
    compiler = GraphCompiler(profile())
    if same_window:
        assert [(d.severity, d.code) for d in diagnostics] == [
            ("error", "unordered_window_writers")
        ]
        assert diagnostics[0].related_events == ["first"]
        with pytest.raises(CompileError, match="unordered_window_writers"):
            compiler.compile(graph)
    else:
        assert [(d.severity, d.code) for d in diagnostics] == [("warning", inconsistent)]
        compiler.compile(graph)
