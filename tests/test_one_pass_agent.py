"""One-pass agent: a prose draft, then one model builds, reviews and repairs the graph."""

import pytest
from test_graph_generation import finish, service
from test_two_pass import DRAFT, spec

from trajectory_remix.graph import build_agent


def verdict(status="pass", ids=("ask",)):
    def make(t):
        review = t.payload["schema"]["oneOf"][5]["properties"]["review"]
        keys = review["properties"]["items"]["properties"]
        items = {k: dict(status=status, evidence="Seen.", event_ids=list(ids)) for k in keys}
        return {"action": "finish", "review": dict(items=items, summary="agent fixture")}

    return make


def add(body=None):
    body = body or spec()
    return {"action": "add", "windows": body["windows"], "events": body["events"]}


TOOLS = {"action": "set_tools", "tools": ["chat", "read"]}


async def run(tmp_path, responses, transport=None, **opts):
    gen, fake = service(tmp_path, method="one_pass_agent", **opts)
    fake.responses = [{"draft": DRAFT}, *responses]
    if transport:
        gen.transport = transport(fake)
    job = gen.start([{"seed": 31}], {})
    await finish(gen, job)
    attempt = gen.jobs.attempts(job)[0]
    source = gen.store.read(attempt["trajectory_id"]).source
    return attempt, source, fake


@pytest.mark.asyncio
async def test_agent_builds_reviews_and_signs_off(tmp_path):
    attempt, source, fake = await run(tmp_path, [TOOLS, add(), {"action": "view"}, verdict()])
    ops = [c.payload["operation"] for c in fake.calls]
    assert ops == ["two_pass_draft"] + [build_agent.OPERATION] * 4
    assert attempt["outcome"] == "pass" and source.metadata["stage"] == 2
    evaluation = source.metadata["evaluations"][-1]
    assert evaluation["operation"] == build_agent.OPERATION and evaluation["endpoint"] == "c"
    assert "ack:echo" in source.events
    assert all(c.endpoint == "c" for c in fake.calls[1:])
    assert DRAFT in fake.calls[1].payload["messages"][1]["content"]


@pytest.mark.asyncio
async def test_unbuildable_change_is_refused_and_spec_kept(tmp_path):
    bad = spec()
    bad["events"][2] = dict(bad["events"][2], after=["missing"])
    attempt, source, fake = await run(tmp_path, [TOOLS, add(bad), add(), verdict()])
    refused = fake.calls[3].payload["messages"][-1]["content"]
    assert '"accepted":false' in refused and "missing" in refused
    assert attempt["outcome"] == "pass" and len(source.events) > 5


@pytest.mark.asyncio
async def test_failing_verdict_and_structural_errors_keep_the_session_open(tmp_path):
    tangled = spec()
    tangled["events"][2] = dict(tangled["events"][2], after=["ask"])
    fixed = dict(tangled["events"][2], after=["ack"])
    responses = [
        TOOLS,
        add(tangled),
        verdict(),  # Refused: structural errors remain.
        {"action": "replace_event", "event": fixed},
        verdict("fail"),  # Refused: the agent's own verdict fails.
        verdict(),
    ]
    attempt, source, fake = await run(tmp_path, responses)
    assert "unordered_window_writers" in fake.calls[4].payload["messages"][-1]["content"]
    assert "still_failing" in fake.calls[6].payload["messages"][-1]["content"]
    assert attempt["outcome"] == "pass"


@pytest.mark.asyncio
async def test_give_up_fails_with_its_reason(tmp_path):
    reason = "The notes contradict themselves about who owns the task."
    attempt, source, fake = await run(tmp_path, [{"action": "give_up", "reason": reason}])
    assert attempt["outcome"] == "fail" and source.metadata["stage"] == -3
    assert source.metadata["agent_outcome"] == {"reason": "gave_up", "detail": reason}


def limited(at):
    def wrap(fake):
        async def call(t):
            if len(fake.calls) in at:
                fake.calls.append(t)
                return {"context_limit": True}
            return await fake(t)

        return call

    return wrap


@pytest.mark.asyncio
async def test_context_limit_starts_a_round_on_the_current_spec(tmp_path):
    attempt, source, fake = await run(tmp_path, [TOOLS, add(), verdict()], limited({3}))
    fresh = fake.calls[4].payload["messages"]
    assert len(fresh) == 2 and '"ack:echo"' not in fresh[1]["content"]
    assert '"id":"look"' in fresh[1]["content"] and attempt["outcome"] == "pass"


@pytest.mark.asyncio
async def test_round_cap_fails_the_build(tmp_path):
    attempt, source, fake = await run(tmp_path, [TOOLS], limited({2}), repair_rounds=1)
    assert attempt["outcome"] == "fail"
    assert source.metadata["agent_outcome"]["reason"] == "round_limit"


def test_agent_role_must_be_configured(tmp_path):
    from test_graph_generation import Fake, settings

    from trajectory_remix.graph.bulk import BulkService
    from trajectory_remix.graph.generation import GenerationService
    from trajectory_remix.graph.jobs import JobService
    from trajectory_remix.graph.sampling import SamplerService
    from trajectory_remix.graph.storage import GraphStore

    config = settings(method="one_pass_agent", agent_role="critical")
    del config["endpoint_roles"]["critical"]
    jobs = JobService(GraphStore.initialize(tmp_path / "p.sqlite", config))
    gen = GenerationService(jobs, SamplerService(jobs), BulkService(jobs), Fake())
    with pytest.raises(ValueError, match="one-pass agent"):
        gen.start([{"seed": 32}], {})
