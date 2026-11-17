"""Step-by-step Flow walkthrough: same prompts and acceptance as runs, nothing stored."""

import json

import pytest
from fastapi.testclient import TestClient
from test_adaptive_flow import episode, selection
from test_graph_generation import finish, service

from trajectory_remix.graph.studio_api import create_studio_app


def client_for(tmp_path, provider, run_provider=None, **options):
    options = {"fast": False, "critical": False} | options
    gen, _ = service(tmp_path, run_provider or provider, **options)
    app = create_studio_app(gen.store.path, artifact_root=tmp_path / "exports", provider=provider)
    client = TestClient(app)
    client.headers["X-Remix-Token"] = app.state.csrf
    return gen, client


def walk(client, state, **body):
    response = client.post("/api/v1/walkthrough/step", json=dict(state=state, **body))
    assert response.status_code == 200, response.text
    return response.json()


def run_to_end(client, state, limit=80):
    traces = []
    for _ in range(limit):
        result = walk(client, state)
        traces.append(result["trace"])
        state = result["state"]
        if state["done"]:
            return state, traces
    raise AssertionError("walkthrough did not finish")


def proposal_count(gen):
    with gen.store.connect() as db:
        return db.execute("SELECT count(*) FROM graph_proposals").fetchone()[0]


@pytest.mark.asyncio
async def test_walkthrough_matches_a_real_run_and_stores_nothing(tmp_path):
    run_provider, walk_provider = episode(), episode()
    gen, client = client_for(tmp_path, walk_provider, run_provider)
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    real = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    with client:
        jobs, proposals = gen.jobs.listing(), proposal_count(gen)
        started = client.post("/api/v1/walkthrough/start", json={"seed": 137}).json()
        first = started["next"]
        assert first["action"] == "model" and first["node"] == "set_scene"
        assert first["request"]["operation"] == "flow_select"
        assert first["request"]["wire"]["model"] == "g"  # Generator endpoint, no credentials.
        assert "Authorization" not in json.dumps(first)
        state, traces = run_to_end(client, started["state"])
        assert state["outcome"] == "finished"
        assert gen.jobs.listing() == jobs and proposal_count(gen) == proposals
    # Same prompts in the same order as the durable run; only the trajectory UUID differs.
    sent = [t["request"]["messages"] for t in traces if t["action"] == "model"]
    assert sent == [p["messages"] for p in walk_provider.calls]
    ours = json.dumps(sent, sort_keys=True).replace(state["source"]["id"], "ID")
    theirs = json.dumps([p["messages"] for p in run_provider.calls], sort_keys=True)
    assert theirs.replace(real.id, "ID") == ours
    assert set(state["source"]["events"]) == set(real.events)
    assert {t["action"] for t in traces} == {"model", "branch", "chat_echo", "end"}
    assert state["source"]["metadata"]["flow_outcome"]["finished"] is True


def test_branch_steps_report_the_value_they_read(tmp_path):
    _, client = client_for(tmp_path, episode())
    with client:
        state = client.post("/api/v1/walkthrough/start", json={"seed": 1}).json()["state"]
        branches = []
        for _ in range(20):
            result = walk(client, state)
            state = result["state"]
            if result["trace"]["action"] == "branch":
                branches.append(result["trace"])
                if result["trace"]["node"] == "plan_is_thought":
                    break
    # The flow setting is read first: the agent replies at once to a chat opening.
    first = branches[0]
    assert first["node"] == "reply_first" and first["path"] == "flow.immediate_response"
    assert first["value"] is True and first["found"] and first["to"] == "opening_is_chat"
    assert branches[1]["value"] == "chat_message" and branches[1]["to"] == "acknowledge"
    trace = result["trace"]  # first_move chose agent text, so the plan writer runs.
    assert trace["node"] == "plan_is_thought" and trace["path"] == "task.envelope.kind"
    assert trace["value"] == "agent_text" and trace["matched"] is True
    assert trace["to"] == "write_plan"
    assert result["next"]["request"]["operation"] == "event"
    assert result["next"]["knows"]["pending_task"]["id"] == "think"


def test_manual_reply_rejection_keeps_state_and_sends_correction(tmp_path):
    _, client = client_for(tmp_path, episode(), format_retries=1)
    with client:
        start = client.post("/api/v1/walkthrough/start", json={"seed": 5}).json()
        bad = walk(client, start["state"], mode="manual", response={"finished": "nope"})
        assert bad["trace"]["accepted"] is False and bad["trace"]["error"]
        assert bad["state"]["checkpoint"]["flow"] == start["state"]["checkpoint"]["flow"]
        assert bad["state"]["source"] == start["state"]["source"]
        prompt = json.loads(bad["next"]["request"]["messages"][1]["content"])
        assert prompt["correction"] == bad["trace"]["error"]
        # A second failure exceeds format_retries=1, as a run marks it Unsuccessful.
        again = walk(client, bad["state"], mode="manual", response={})
        assert again["state"]["done"] and again["state"]["outcome"] == "invalid_provider_output"
        assert again["state"]["source"]["metadata"]["stage"] == -1
        finished = client.post("/api/v1/walkthrough/step", json=dict(state=again["state"]))
        assert finished.status_code == 422


def test_manual_selection_is_checked_like_a_run(tmp_path):
    _, client = client_for(tmp_path, episode())
    with client:
        state = client.post("/api/v1/walkthrough/start", json={"seed": 5}).json()["state"]
        early = walk(
            client,
            state,
            mode="manual",
            response=dict(finished=True, reason="done", windows={}, task=None),
        )
        assert "Finish requires" in early["trace"]["error"]
        windows = {"room": dict(purpose="chatroom", agent_writable=False)}
        good = selection("hello", "chat_message", "room", windows=windows, chat={"speaker": "user"})
        chosen = walk(client, state, mode="manual", response=good)
    assert chosen["trace"]["accepted"] and chosen["trace"]["task"]["id"] == "hello"
    assert "room" in chosen["state"]["source"]["windows"]


def test_transport_failure_is_reported_without_changing_state(tmp_path):
    async def broken(ticket):
        raise RuntimeError("connection refused")

    _, client = client_for(tmp_path, broken)
    with client:
        start = client.post("/api/v1/walkthrough/start", json={"seed": 2}).json()
        result = walk(client, start["state"])
    assert "RuntimeError" in result["trace"]["error"]
    assert result["state"]["checkpoint"] == start["state"]["checkpoint"]


def test_walkthrough_can_use_an_unsaved_flow_draft(tmp_path):
    _, client = client_for(tmp_path, episode())
    with client:
        flow = client.get("/api/v1/generation/flow").json()["flow"]
        flow["nodes"]["set_scene"]["instruction"] = "DRAFT SELECTOR PROMPT"
        started = client.post("/api/v1/walkthrough/start", json={"seed": 3, "flow": flow}).json()
        body = json.loads(started["next"]["request"]["messages"][1]["content"])
        assert body["current_step"]["instruction"] == "DRAFT SELECTOR PROMPT"
        flow["nodes"]["set_scene"]["next"] = "missing"
        rejected = client.post("/api/v1/walkthrough/start", json={"seed": 3, "flow": flow})
    assert rejected.status_code == 422 and "invalid" in rejected.json()["message"]
