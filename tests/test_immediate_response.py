"""The immediate-response flag, flow-setting branches, Load default and token defaults."""

from fastapi.testclient import TestClient
from test_graph_generation import service

from trajectory_remix.graph import flow_document
from trajectory_remix.graph.adaptive_flow import branch, load_flow, lookup
from trajectory_remix.graph.models import Endpoint
from trajectory_remix.graph.studio_api import create_studio_app

REPLY = dict(kind="branch", path="flow.immediate_response", equals=True, then="a", otherwise="b")


def test_branches_can_read_flow_settings():
    assert lookup(REPLY, {"finished": True}, {"immediate_response": True}) == (True, True)
    assert branch(REPLY, None, {"immediate_response": True}) == "a"
    assert branch(REPLY, None, {"immediate_response": False}) == "b"
    assert lookup(REPLY, None, None) == (None, False)  # No flow given: not found.
    # Selection paths are unchanged.
    finished = dict(REPLY, path="finished")
    assert branch(finished, {"finished": True}, {"immediate_response": False}) == "a"


def test_default_flow_replies_to_a_chat_opening_before_reasoning():
    flow = load_flow()["flow"]
    nodes = flow["nodes"]
    assert flow["immediate_response"] is True
    assert nodes["write_opening"]["next"] == "reply_first"
    reply, chat = nodes["reply_first"], nodes["opening_is_chat"]
    assert branch(reply, None, flow) == "opening_is_chat"
    assert branch(reply, None, dict(flow, immediate_response=False)) == "first_move"
    opening = {"task": {"envelope": {"kind": "chat_message"}}}
    assert branch(chat, opening, flow) == "acknowledge"
    ticket = {"task": {"envelope": {"kind": "external_text"}}}
    assert branch(chat, ticket, flow) == "first_move"
    assert nodes["acknowledge"]["next"] == "write_reply"
    assert nodes["write_reply"]["next"] == "first_move"
    assert "never addressed to anyone" in flow["actor_instruction"]


def test_flow_paths_and_the_flag_are_checked():
    flow = load_flow()["flow"]
    clean = flow_document.check(flow=flow)
    assert clean["valid"] and not [d for d in clean["diagnostics"] if d["code"] == "path_root"]
    bad = flow_document.check(flow=dict(flow, immediate_response="yes"))
    assert not bad["valid"]
    assert any(d["code"] == "immediate_response" for d in bad["diagnostics"])


def test_load_default_reads_the_packaged_flow_without_saving(tmp_path):
    gen, _ = service(tmp_path)
    app = create_studio_app(gen.store.path, artifact_root=tmp_path / "exports")
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        current = client.get("/api/v1/generation/flow").json()
        edited = dict(current["flow"], max_events=7)
        body = dict(flow=edited, expected_revision=current["revision"])
        saved = client.put("/api/v1/generation/flow", json=body).json()
        default = client.get("/api/v1/generation/flow/default").json()
        assert default["origin"] == "packaged" and default["valid"]
        assert default["text"] == flow_document.packaged_text()
        assert default["flow"]["max_events"] == 128
        after = client.get("/api/v1/generation/flow").json()
        assert after["revision"] == saved["revision"] and after["flow"]["max_events"] == 7


def test_endpoints_default_to_a_65k_context_and_8k_output():
    endpoint = Endpoint.model_validate(dict(url="http://127.0.0.1:8000/v1", model="m"))
    assert endpoint.context_window_tokens == 65536 and endpoint.max_output_tokens == 8192
