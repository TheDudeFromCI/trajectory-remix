"""Causal evidence must still be visible at the agent event that consumes it."""

import pytest
from test_graph_compiler import event, source

from trajectory_remix.graph import signoffs
from trajectory_remix.graph.compiler import GraphCompiler
from trajectory_remix.graph.models import ProjectSettings, ToolDefinition

CHECKS = {signoffs.CAUSAL_EVIDENCE: {"allow_not_applicable": False}}


def report(evidence="result"):
    return {
        "status": "complete",
        "summary": "The decision follows the available evidence.",
        "items": {
            signoffs.CAUSAL_EVIDENCE: {
                "status": "pass",
                "evidence": "The decision uses the saved observation.",
                "event_ids": ["decide", evidence],
                "causal_claims": [
                    {"consumer_event_id": "decide", "evidence_event_ids": [evidence]}
                ],
            }
        },
    }


def test_closed_tool_result_warns_and_cannot_support_signoff_even_if_warning_is_disabled():
    events = {
        "tools": event("tool_schema", "tools", '{"name":"read","parameters":{}}'),
        "call": event(
            "tool_call", "request", "{}", ["tools"], tool="read", call_id="r", close_window=True
        ),
        "result": event(
            "tool_response", "result_window", "Verified.", ["call"], call_id="r", close_window=True
        ),
        "decide": event("agent_text", "reason", "The result verifies the decision.", ["result"]),
    }
    windows = {
        "tools": dict(purpose="tools", agent_writable=False),
        "result_window": dict(purpose="tool", agent_writable=False),
        "request": dict(purpose="tool", agent_writable=True),
        "reason": dict(purpose="reasoning", agent_writable=True, intentionally_left_open=True),
    }
    graph = source(events, windows)
    settings = ProjectSettings(tools={"read": ToolDefinition(name="Read")})
    [warning] = GraphCompiler.dry_run(graph, settings)
    assert (warning.code, warning.severity, warning.path, warning.related_events) == (
        "tool_result_unavailable",
        "warning",
        ["events", "decide", "after", 0],
        ["result"],
    )
    settings.disabled_warnings = ["tool_result_unavailable"]
    assert GraphCompiler.dry_run(graph, settings) == []
    with pytest.raises(ValueError, match="result.*decide.*closes its own window"):
        signoffs.validate(graph, settings, CHECKS, report())

    events["result"]["close_window"] = False
    events["close"] = event(
        "external_text", "result_window", "", ["result", "decide"], close_window=True
    )
    fixed = source(events, windows)
    assert GraphCompiler.dry_run(fixed, settings) == []
    assert signoffs.validate(fixed, settings, CHECKS, report())[0] == "pass"


@pytest.mark.parametrize("kind", ["external_text", "agent_text"])
def test_same_evidence_rule_applies_to_documents_and_reasoning(kind):
    events = {
        "result": event(kind, "evidence", "Relevant information.", close_window=True),
        "decide": event("agent_text", "reason", "Decide.", ["result"]),
    }
    assert not GraphCompiler.dry_run(source(events))
    with pytest.raises(ValueError, match="closes its own window"):
        signoffs.validate(source(events), ProjectSettings(), CHECKS, report())
    events["result"]["close_window"] = False
    assert signoffs.validate(source(events), ProjectSettings(), CHECKS, report())[0] == "pass"


def test_signoff_accepts_a_visible_copy_and_rejects_replaced_or_racing_evidence():
    events = {
        "result": event("external_text", "document", "The original finding."),
        "copy": event("agent_text", "retained", "Record the original finding.", ["result"]),
        "replacement": event(
            "external_text", "document", "New content.", ["copy"], replace_body=True
        ),
        "decide": event(
            "agent_text", "reason", "Use the retained finding.", ["copy", "replacement"]
        ),
    }
    settings = ProjectSettings()
    graph = source(events)
    with pytest.raises(ValueError, match="replacement replaces its body"):
        signoffs.validate(graph, settings, CHECKS, report())
    assert signoffs.validate(graph, settings, CHECKS, report("copy"))[0] == "pass"

    events["decide"]["after"] = ["copy"]
    with pytest.raises(ValueError, match="replacement can clear the window"):
        signoffs.validate(source(events), settings, CHECKS, report())

    events["replacement"]["after"].append("decide")
    assert signoffs.validate(source(events), settings, CHECKS, report())[0] == "pass"


def test_passing_causal_review_requires_explicit_claims_and_checks_ancestry():
    graph = source(
        {
            "result": event("external_text", "evidence"),
            "decide": event("agent_text", "reason"),
        }
    )
    settings = ProjectSettings()
    with pytest.raises(ValueError, match="not a causal predecessor"):
        signoffs.validate(graph, settings, CHECKS, report())
    body = report()
    del body["items"][signoffs.CAUSAL_EVIDENCE]["causal_claims"]
    with pytest.raises(ValueError, match="needs causal_claims"):
        signoffs.validate(graph, settings, CHECKS, body)
    assert (
        signoffs.validate(graph, settings, CHECKS, body, require_causal_claims=False)[0] == "pass"
    )
