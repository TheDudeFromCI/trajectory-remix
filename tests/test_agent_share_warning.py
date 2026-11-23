from trajectory_remix.graph.models import GraphSource
from trajectory_remix.graph.validation import diagnose


def source(agent_text, context_text):
    return GraphSource.model_validate(
        dict(
            format="kidra-trajectory-graph/1",
            id="7d3f8a2e-1b4c-4e5f-9a6b-0c1d2e3f4a5b",
            windows={
                "main": dict(
                    purpose="reasoning", agent_writable=True, intentionally_left_open=True
                ),
                "ctx": dict(purpose="context", agent_writable=False, intentionally_left_open=True),
            },
            events={
                "context": dict(kind="external_text", window="ctx", content=context_text, after=[]),
                "think": dict(
                    kind="agent_text", window="main", content=agent_text, after=["context"]
                ),
            },
        )
    )


def codes(src):
    return [d.code for d in diagnose(src) if d.code == "low_agent_share"]


def test_warns_below_one_sixteenth():
    src = source("a" * 5, "c" * 95)
    result = [d for d in diagnose(src) if d.code == "low_agent_share"]
    assert len(result) == 1 and result[0].severity == "warning"
    assert "5 of 100" in result[0].message


def test_no_warning_at_or_above_one_sixteenth():
    assert codes(source("a" * 16, "c" * 240)) == []  # exactly 1/16
    assert codes(source("a" * 50, "c" * 50)) == []


def test_no_warning_for_empty_source():
    assert codes(source("", "")) == []
