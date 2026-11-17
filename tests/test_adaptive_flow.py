"""Behavioral acceptance of the adaptive backend, without a live provider."""

import copy
import json
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from flow_fixtures import MINIMAL_FLOW
from test_graph_generation import finish, service

from trajectory_remix.graph.adaptive_flow import accept_selection, branch, load_flow
from trajectory_remix.graph.flow import prepare_plan
from trajectory_remix.graph.generation import GenerationService
from trajectory_remix.graph.models import GraphSource
from trajectory_remix.graph.studio_api import create_studio_app
from trajectory_remix.graph.validation import diagnose


def selection(ident, kind, window, after=(), windows=None, **fields):
    return dict(
        finished=False,
        reason="Continue grounded coordination",
        windows=windows or {},
        task=dict(
            id=ident,
            envelope=dict(kind=kind, window=window, after=list(after), **fields),
            instruction="Write this external observation",
            world={},
        ),
    )


class Episode:
    def __init__(self, choices, contents):
        self.choices, self.contents, self.calls = list(choices), contents, []

    async def __call__(self, ticket):
        p = ticket.payload
        self.calls.append(copy.deepcopy(p))
        if p["operation"] == "flow_tools":  # Show tools: choose every tool offered.
            ids = {key: True for key in p["schema"]["properties"]["tools"]["required"]}
            return {
                "output": {"tools": ids, "reason": "All offered tools"},
                "model": "scripted-offline",
            }
        if p["operation"] == "flow_select":
            # The final finish is repeated when a verification step asks again.
            body = self.choices.pop(0) if len(self.choices) > 1 else self.choices[0]
        elif "arguments" in p["schema"]["properties"]:  # A complete call: JSON arguments.
            body = {"arguments": json.loads(self.contents[p["task"]["id"]])}
        else:
            body = {"content": self.contents[p["task"]["id"]]}
        return {"output": body, "model": "scripted-offline"}


def episode():
    windows = {
        "room": dict(purpose="chatroom", agent_writable=False),
        "reason": dict(purpose="reasoning", agent_writable=True),
        "a": dict(purpose="tool", agent_writable=True),
        "b": dict(purpose="tool", agent_writable=True),
        "chat": dict(purpose="tool", agent_writable=True),
    }
    choices = [
        selection("opening", "chat_message", "room", windows=windows, chat={"speaker": "user"}),
        # The default flow's immediate reply, before any reasoning.
        selection(
            "ack",
            "tool_call",
            "w_reply",
            ["opening"],
            windows={"w_reply": dict(purpose="reasoning", agent_writable=True)},
            tool="chat",
            call_id="ack",
            close_window=True,
        ),
        selection("think", "agent_text", "reason", ["opening"]),
        selection("assign-a", "tool_call", "a", ["think"], tool="read", call_id="a"),
        selection("assign-b", "tool_call", "b", ["think"], tool="read", call_id="b"),
        selection("result-b", "tool_response", "b", ["assign-b"], call_id="b"),
        selection("result-a", "tool_response", "a", ["assign-a"], call_id="a"),
        selection("integrate", "agent_text", "reason", ["result-a", "result-b"]),
        selection(
            "deliver",
            "tool_call",
            "chat",
            ["integrate"],
            tool="chat",
            call_id="delivery",
            close_window=True,
        ),
        dict(
            finished=True,
            reason="Results reconciled in integrate and delivered",
            windows={},
            task=None,
        ),
    ]
    contents = dict(
        opening="Compare the two supplied sources.",
        ack=json.dumps({"content": "Sure, let me compare them."}),
        think="Inspect both before deciding.",
        **{
            "assign-a": "{}",
            "assign-b": "{}",
            "result-a": "A: 42",
            "result-b": "B: 42",
            "integrate": "Both sources agree: 42.",
            "deliver": json.dumps({"content": "Verified agreement: 42."}),
        },
    )
    return Episode(choices, contents)


@pytest.mark.asyncio
async def test_default_adapts_with_parallel_results_join_and_causal_actor_context(tmp_path):
    fake = episode()
    gen, _ = service(tmp_path, fake, fast=False, critical=False)
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    assert source.metadata["stage"] == 0
    assert not [d for d in diagnose(source, gen.settings_for(job)) if d.severity == "error"]
    assert "deliver:echo" in source.events
    assert source.events["assign-a"].after == source.events["assign-b"].after == ["think"]
    deliver = next(p for p in fake.calls if p.get("task", {}).get("id") == "deliver")
    visible = json.loads(deliver["messages"][1]["content"])["visible"]
    assert "integrate" in str(visible)  # The decision remains visible through delivery.
    for p in fake.calls:
        if p["operation"] == "event" and p["task"]["envelope"]["kind"] in {
            "agent_text",
            "tool_call",
        }:
            prompt = json.loads(p["messages"][1]["content"])
            assert "proposal" not in prompt and "world" not in prompt
    # Nine events, one proposed finish and one confirmed finish after verification.
    selects = [p for p in fake.calls if p["operation"] == "flow_select"]
    assert len(selects) == 11
    steps = [json.loads(p["messages"][1]["content"])["current_step"] for p in selects]
    assert [s["node"] for s in steps[-2:]] == ["work", "verify"]
    assert "Audit the committed events" in steps[-1]["instruction"]


@pytest.mark.asyncio
async def test_default_flow_routes_each_event_kind_to_its_own_writer(tmp_path):
    fake = episode()
    gen, _ = service(tmp_path, fake, fast=False)
    await finish(gen, gen.start([{"seed": 137}], {}))
    flow = load_flow()["flow"]["nodes"]
    writers = {
        "opening": "write_opening",
        "ack": "write_reply",
        "think": "write_plan",
        "assign-a": "write_tool_call",
        "result-a": "write_tool_result",
        "integrate": "write_thought",
    }
    events = {p["task"]["id"]: p for p in fake.calls if p["operation"] == "event"}
    for ident, node in writers.items():
        assert events[ident]["task"]["instruction"].endswith(flow[node]["instruction"])
    # Each phase has its own selector prompt.
    # Every phase shares one system prompt; the step instruction comes after the data.
    selects = [p for p in fake.calls if p["operation"] == "flow_select"]
    shared = load_flow()["flow"]["selector_instruction"]
    assert shared and {p["messages"][0]["content"] for p in selects} == {shared}
    steps = [json.loads(p["messages"][1]["content"])["current_step"] for p in selects]
    for step, node in zip(steps, ["set_scene", "acknowledge", "first_move", "work"]):
        assert step == {"node": node, "instruction": flow[node]["instruction"]}


@pytest.mark.asyncio
async def test_yaml_is_frozen_and_resume_does_not_repeat_committed_events(tmp_path):
    fake = episode()
    gen, _ = service(tmp_path, fake, fast=False)
    path = tmp_path / "flow.yaml"
    original = load_flow()["flow"]
    path.write_text(yaml.safe_dump(original))
    job = gen.start([{"seed": 137}], {})
    gen._admit(job)
    ident = gen.jobs.attempts(job)[0]["trajectory_id"]
    gen._prepare(job, ident)
    await gen._call("g")  # Commit selected envelope and windows.
    checkpoint = gen.jobs.checkpoint(job, ident)
    assert checkpoint["flow"]["task"]["id"] == "opening"
    gen.jobs.control(job, "pause")
    path.write_text("malformed: [")
    gen.jobs.control(job, "resume")
    resumed = GenerationService(gen.jobs, gen.sampler, gen.bulk, fake)
    await finish(resumed, job)
    assert gen.store.read(ident).source.metadata["stage"] == 0
    assert sum(p.get("task", {}).get("id") == "opening" for p in fake.calls) == 1


@pytest.mark.asyncio
async def test_loop_limit_retains_unfinished_without_fabricated_success(tmp_path):
    fake = episode()
    gen, _ = service(tmp_path, fake, fast=False)
    config = load_flow()["flow"]
    config["max_steps"] = 2
    job = gen.start([{"seed": 137}], {"flow_yaml": yaml.safe_dump(config)})
    await finish(gen, job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    assert source.metadata["stage"] == -3
    assert source.metadata["generation_failure"]["kind"] == "flow_limit"


def test_yaml_api_and_invalid_configuration_no_job(tmp_path):
    gen, _ = service(tmp_path)
    app = create_studio_app(gen.store.path, provider=episode())
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        response = client.get("/api/v1/generation/flow")
        assert response.status_code == 200
        assert response.json()["flow"]["nodes"]["set_scene"]["kind"] == "select"
    with pytest.raises(ValueError):
        gen.start([{"seed": 137}], {"flow_yaml": "nodes: ["})
    assert gen.jobs.listing() == []


def test_new_matrix_dimensions_preserved_without_competency_switches(tmp_path):
    gen, _ = service(tmp_path)
    proposal = gen.sampler.propose(seed=137)["proposal"]
    proposal["future_dimension"] = {"quantum_potatoes": {"required": True}}
    prepared = prepare_plan(proposal, gen.store.settings().settings, {})
    assert prepared["proposal"] == proposal
    assert "quantum_potatoes" in json.dumps(prepared)


@pytest.mark.parametrize("value,expected", [(True, "yes"), (1, "no"), (False, "no"), (None, "no")])
def test_conditions_are_typed(value, expected):
    assert (
        branch(dict(path="finished", equals=True, then="yes", otherwise="no"), {"finished": value})
        == expected
    )


def test_actor_selection_cannot_supply_private_world_or_redefine_windows(tmp_path):
    gen, _ = service(tmp_path)
    plan = prepare_plan(
        gen.sampler.propose(seed=137)["proposal"], gen.store.settings().settings, {}
    )
    source = GraphSource(id=str(uuid4()), format="kidra-trajectory-graph/1", windows={}, events={})
    body = selection(
        "think", "agent_text", "r", windows={"r": dict(purpose="reasoning", agent_writable=True)}
    )
    body["task"]["world"] = {"answer": "secret"}
    with pytest.raises(ValueError, match="private world"):
        accept_selection(source, body, plan, gen.store.settings().settings)
    body["task"]["world"] = {}
    body["task"]["instruction"] = "The private solution is 99"
    source, task, _ = accept_selection(source, body, plan, gen.store.settings().settings)
    assert "99" not in task["instruction"]
    # Repeating the window as it is defined is ignored; changing it is not allowed.
    body["windows"]["r"]["agent_writable"] = False
    with pytest.raises(ValueError, match="already exists"):
        accept_selection(source, body, plan, gen.store.settings().settings)


@pytest.mark.parametrize(
    "text", ["!!python/object:os.system {}", "version: unknown\nstart: x\nnodes: {}", "[]"]
)
def test_yaml_rejects_unsafe_and_invalid_shapes(text):
    with pytest.raises(ValueError):
        load_flow(text=text)


@pytest.mark.asyncio
async def test_chat_echo_mutation_blocks_structural_acceptance(tmp_path):
    fake = episode()
    gen, _ = service(tmp_path, fake, fast=False)
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    data = source.to_dict()
    data["events"]["deliver:echo"]["content"] = "A different scientific assay"
    assert "chat_echo_mismatch" in {
        d.code for d in diagnose(GraphSource.model_validate(data), gen.settings_for(job))
    }


@pytest.mark.asyncio
async def test_cancel_fences_late_selector_result(tmp_path):
    from trajectory_remix.graph.jobs import Fenced, OwnershipConflict

    fake = episode()
    gen, _ = service(tmp_path, fake, fast=False)
    job = gen.start([{"seed": 137}], {})
    gen._admit(job)
    ident = gen.jobs.attempts(job)[0]["trajectory_id"]
    gen._prepare(job, ident)
    ticket = gen.jobs.claim("g")
    gen.jobs.request_result(ticket, await fake(ticket))
    gen.jobs.control(job, "cancel")
    with pytest.raises((Fenced, OwnershipConflict)):
        gen.consume(ticket)
    assert not gen.store.read(ident).source.events


@pytest.mark.parametrize("instructions", [{"flow_yaml": 1}, {"flow_yaml": "", "plan": {}}])
def test_invalid_flow_options_do_not_reserve_attempts(tmp_path, instructions):
    gen, _ = service(tmp_path)
    with pytest.raises(ValueError):
        gen.start([{"seed": 137}], instructions)
    assert not gen.jobs.listing()


@pytest.mark.asyncio
async def test_yaml_custom_branch_extends_event_authoring(tmp_path):
    fake = episode()
    gen, _ = service(tmp_path, fake, fast=False)
    config = copy.deepcopy(MINIMAL_FLOW)
    config["nodes"]["finished"]["otherwise"] = "is_call"
    config["nodes"]["is_call"] = dict(
        kind="branch", path="task.envelope.kind", equals="tool_call", then="call", otherwise="write"
    )
    config["nodes"]["call"] = dict(
        kind="event", instruction="Check this tool contract carefully.", next="choose"
    )
    job = gen.start([{"seed": 137}], {"flow_yaml": yaml.safe_dump(config)})
    await finish(gen, job)
    calls = [
        p for p in fake.calls if p.get("task", {}).get("envelope", {}).get("kind") == "tool_call"
    ]
    assert calls and all(
        "Check this tool contract carefully" in p["task"]["instruction"] for p in calls
    )


def test_duplicate_yaml_keys_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        load_flow(text="start: a\nstart: b")


@pytest.mark.asyncio
async def test_browser_fake_supports_default_adaptive_generation(tmp_path):
    # The browser launchers import this same provider. Rejecting flow_select used
    # to pause their jobs before any event and time out the live-update check.
    gen, fake = service(tmp_path, fast=False, critical=False)
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    assert gen.jobs.inspect(job)["state"] == "completed"
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    assert source.metadata["stage"] == 0
    assert "reply:echo" in source.events
    assert any(call.payload["operation"] == "flow_select" for call in fake.calls)
