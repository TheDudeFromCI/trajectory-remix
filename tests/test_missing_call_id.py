"""An empty call ID or tool no longer blocks saving: the source saves and the compiler reports it."""

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trajectory_remix.graph.compiler import CompileError, GraphCompiler
from trajectory_remix.graph.models import GraphSource
from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.studio_api import create_studio_app
from trajectory_remix.graph.validation import diagnose

sys.path.insert(0, str(Path(__file__).parent))
from test_graph_compiler import profile  # noqa: E402  (shared tokenizer fixture)


def _source(call_id="", response_call_id="c1"):
    return dict(
        format="kidra-trajectory-graph/1",
        id=str(uuid4()),
        windows={"t": {"purpose": "tool", "agent_writable": True}},
        events={
            "call": {
                "kind": "tool_call",
                "window": "t",
                "tool": "chat",
                "call_id": call_id,
                "content": "{}",
            },
            "reply": {
                "kind": "tool_response",
                "window": "t",
                "call_id": response_call_id,
                "content": "ok",
                "after": ["call"],
            },
        },
    )


@pytest.mark.parametrize(
    ("call", "reply", "flagged"),
    [("", "c1", ["call"]), ("c1", "", ["reply"]), ("", "", ["call", "reply"])],
)
def test_missing_call_id_is_a_compiler_error_at_the_field(call, reply, flagged):
    found = [d for d in diagnose(GraphSource.model_validate(_source(call, reply)))]
    missing = [d for d in found if d.code == "missing_call_id"]
    assert [d.event_id for d in missing] == flagged
    assert all(d.severity == "error" and d.path[-1] == "call_id" for d in missing)
    assert not any(d.code == "invalid_source" for d in found)


def test_omitted_call_id_reads_as_empty():
    data = _source()
    del data["events"]["call"]["call_id"]
    assert GraphSource.model_validate(data).events["call"].call_id == ""


def test_compiling_reports_the_missing_call_id():
    with pytest.raises(CompileError, match="missing_call_id"):
        GraphCompiler(profile()).compile(GraphSource.model_validate(_source()))


def test_editor_save_with_empty_call_id_succeeds(tmp_path):
    GraphStore.initialize(tmp_path / "g.sqlite")
    app = create_studio_app(tmp_path / "g.sqlite", artifact_root=tmp_path / "exports")
    with TestClient(app) as c:
        c.headers["X-Remix-Token"] = app.state.csrf
        source = _source(call_id="c1")
        created = c.post("/api/v1/trajectories", json=source)
        assert created.status_code == 201, created.text
        source["events"]["call"]["call_id"] = ""  # The Call ID field was cleared.
        saved = c.put(
            f"/api/v1/trajectories/{source['id']}",
            json={"expected_revision": 1, "source": source},
        )
        assert saved.status_code == 200, saved.text
        codes = [(d["code"], d["event_id"]) for d in saved.json()["diagnostics"]]
        assert ("missing_call_id", "call") in codes


def test_missing_tool_is_a_compiler_error_at_the_field():
    data = _source(call_id="c1")
    data["events"]["call"]["tool"] = ""
    found = diagnose(GraphSource.model_validate(data))
    assert [
        (d.code, d.event_id, d.path[-1], d.severity) for d in found if d.code == "missing_tool"
    ] == [("missing_tool", "call", "tool", "error")]
    assert not any(d.code == "invalid_source" for d in found)
