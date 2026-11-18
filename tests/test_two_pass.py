"""Two-pass generation: a prose draft, one assembled graph, then review and repair."""

import pytest
from test_graph_generation import finish, service

from trajectory_remix.graph import compact_spec, two_pass

DRAFT = "Working notes. " * 20


def spec(**changes):
    body = dict(
        tools=["chat", "read"],
        windows=[
            dict(id="room", purpose="chatroom", writable=False),
            dict(id="notes", purpose="notes", writable=True),
            dict(id="w1", purpose="reasoning", writable=True),
            dict(id="w2", purpose="reasoning", writable=True),
        ],
        events=[
            dict(id="ask", kind="user", window="room", after=[], content="Hi", speaker="Ana"),
            dict(
                id="ack", kind="call", window="w1", after=["ask"], tool="chat",
                arguments={"content": "Sure, checking now."},
            ),
            dict(id="plan", kind="think", window="w1", after=["ack"], content="Read it."),
            dict(
                id="look", kind="call", window="w1", after=["plan"], tool="read",
                arguments={"path": "a.md"}, close=True,
            ),
            dict(id="got", kind="result", window="w2", after=["look"], content="A says yes."),
            dict(id="note", kind="think", window="notes", after=["got"], content="A: yes."),
            dict(
                id="tell", kind="call", window="w2", after=["note"], tool="chat",
                arguments={"content": "A says yes."}, close=True,
            ),
        ],
    )  # fmt: skip
    body.update(changes)
    return body


def two_pass_service(tmp_path, **opts):
    return service(tmp_path, method="two_pass", **opts)


@pytest.mark.asyncio
async def test_draft_then_assemble_then_both_reviews(tmp_path):
    gen, fake = two_pass_service(tmp_path)
    fake.responses = [{"draft": DRAFT}, spec()]
    job = gen.start([{"seed": 11}], {})
    await finish(gen, job)
    attempt = gen.jobs.attempts(job)[0]
    source = gen.store.read(attempt["trajectory_id"]).source
    operations = [c.payload["operation"] for c in fake.calls]
    assert operations[:2] == [two_pass.DRAFT, two_pass.ASSEMBLE]
    assert source.metadata["stage"] == 2 and attempt["outcome"] == "pass"
    assert source.metadata["two_pass"]["draft"] == DRAFT
    assert "ack:echo" in source.events and source.events["got"].call_id == "look"
    assert DRAFT in fake.calls[1].payload["messages"][1]["content"]


@pytest.mark.asyncio
async def test_unusable_spec_gets_a_correction(tmp_path):
    gen, fake = two_pass_service(tmp_path)
    broken = spec()
    broken["events"][2] = dict(broken["events"][2], after=["missing"])
    fake.responses = [{"draft": DRAFT}, broken, spec()]
    job = gen.start([{"seed": 12}], {})
    await finish(gen, job)
    retry = fake.calls[2].payload
    assert retry["operation"] == two_pass.ASSEMBLE and "missing" in retry["messages"][1]["content"]
    assert gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source.metadata["stage"] == 2


@pytest.mark.asyncio
async def test_structural_errors_go_straight_to_fast_repair(tmp_path):
    gen, fake = two_pass_service(tmp_path)
    tangled = spec()
    # Two writers in one window with no order between them is a structural error.
    tangled["events"][2] = dict(tangled["events"][2], after=["ask"])
    reason = "The fixture cannot be repaired; the episode needs rewriting."
    fake.responses = [{"draft": DRAFT}, tangled, {"action": "give_up", "reason": reason}]
    job = gen.start([{"seed": 13}], {})
    await finish(gen, job)
    assert fake.calls[2].payload["operation"] == "repair"
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    assert source.metadata["generation_failure"]["kind"] == "assembled_with_structural_errors"
    assert source.metadata["repair_outcomes"][-1]["outcome"] == "unfeasible"


def test_builder_fills_in_listing_echo_and_call_ids(tmp_path):
    gen, _ = service(tmp_path)
    tools = {k: v.model_dump(mode="json") for k, v in gen.store.settings().settings.tools.items()}
    base = dict(format="kidra-trajectory-graph/1", id="7d3f8a2e-1b4c-4e5f-9a6b-0c1d2e3f4a5b")
    data = compact_spec.build(spec(), base, tools)
    assert data["events"]["tools"]["kind"] == "external_text"
    assert data["events"]["ask"]["chat"] == {"speaker": "Ana"}
    assert data["events"]["look"]["content"] == '{"path":"a.md"}'
    assert data["events"]["look"]["close_window"] is True
    assert data["events"]["ack:echo"]["window"] == "room"


@pytest.mark.parametrize(
    "change, message",
    [
        (dict(id="ask"), "used twice"),
        (dict(window="nowhere"), "undefined window"),
        (dict(tool=None), "needs a tool"),
    ],
)
def test_builder_explains_bad_specs(tmp_path, change, message):
    gen, _ = service(tmp_path)
    tools = {k: v.model_dump(mode="json") for k, v in gen.store.settings().settings.tools.items()}
    body = spec()
    body["events"][3] = {k: v for k, v in dict(body["events"][3], **change).items() if v}
    base = dict(format="kidra-trajectory-graph/1", id="7d3f8a2e-1b4c-4e5f-9a6b-0c1d2e3f4a5b")
    with pytest.raises(ValueError, match=message):
        compact_spec.build(body, base, tools)
