"""Two-pass generation: a prose draft, one assembled graph, then review and repair."""

import json

import pytest
from jsonschema import Draft202012Validator
from test_graph_generation import DRAFT, finish, service, spec  # noqa: F401

from trajectory_remix.graph import compact_spec, draft_assembly, prompt_defaults
from trajectory_remix.graph.flow import causal_view
from trajectory_remix.graph.models import GraphSource, ToolDefinition
from trajectory_remix.graph.validation import diagnose


def draft_assembly_service(tmp_path, **opts):
    return service(tmp_path, method="draft_assembly", **opts)


@pytest.mark.asyncio
async def test_draft_then_assemble_then_both_reviews(tmp_path):
    gen, fake = draft_assembly_service(tmp_path)
    fake.responses = [{"draft": DRAFT}, spec()]
    job = gen.start([{"seed": 11}], {})
    await finish(gen, job)
    attempt = gen.jobs.attempts(job)[0]
    source = gen.store.read(attempt["trajectory_id"]).source
    operations = [c.payload["operation"] for c in fake.calls]
    assert operations[:2] == [draft_assembly.DRAFT, draft_assembly.ASSEMBLE]
    assert source.metadata["stage"] == 2 and attempt["outcome"] == "pass"
    assert source.metadata["draft"] == DRAFT
    assert "ack:echo" in source.events and source.events["got"].call_id == "look"
    assert DRAFT in fake.calls[1].payload["messages"][1]["content"]
    system_message = fake.calls[1].payload["messages"][0]["content"]
    assert prompt_defaults.assembly() in system_message


@pytest.mark.asyncio
async def test_unusable_spec_gets_a_correction(tmp_path):
    gen, fake = draft_assembly_service(tmp_path)
    broken = spec()
    broken["events"][2] = dict(broken["events"][2], after=["missing"])
    fake.responses = [{"draft": DRAFT}, broken, spec()]
    job = gen.start([{"seed": 12}], {})
    await finish(gen, job)
    retry = fake.calls[2].payload
    assert (
        retry["operation"] == draft_assembly.ASSEMBLE
        and "missing" in retry["messages"][1]["content"]
    )
    assert gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source.metadata["stage"] == 2


@pytest.mark.asyncio
async def test_errors_left_after_the_assembly_fix_go_to_fast_repair(tmp_path):
    gen, fake = draft_assembly_service(tmp_path)
    tangled = spec()
    # Two writers in one window with no order between them is a structural error.
    tangled["events"][2] = dict(tangled["events"][2], after=["ask"])
    reason = "The fixture cannot be repaired; the episode needs rewriting."
    give_up = {"action": "give_up", "reason": reason}
    fake.responses = [{"draft": DRAFT}, tangled, give_up, give_up]  # Assembly fix, then repair.
    job = gen.start([{"seed": 13}], {})
    await finish(gen, job)
    assert [c.payload["operation"] for c in fake.calls[2:4]] == ["assembly_fix", "repair"]
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


def test_assemble_prompt_example_builds_cleanly(tmp_path):
    gen, _ = service(tmp_path)
    settings = gen.store.settings().settings
    settings.tools["read"] = ToolDefinition(
        name="Read",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        response_window="inbox",
    )
    tools = {key: tool.model_dump(mode="json") for key, tool in settings.tools.items()}
    example = prompt_defaults.assembly().split("```json\n", 1)[1].split("```", 1)[0]
    body = json.loads(example)
    Draft202012Validator(compact_spec.schema(tools)).validate(body)
    base = dict(format="kidra-trajectory-graph/1", id="7d3f8a2e-1b4c-4e5f-9a6b-0c1d2e3f4a5b")
    source = GraphSource.model_validate(compact_spec.build(body, base, tools))

    assert diagnose(source, settings) == []
    assert source.events["read-n"].after == source.events["read-s"].after == ["ack:echo"]
    assert source.events["got-n"].call_id == "read-n"
    assert source.events["got-s"].call_id == "read-s"
    visible = causal_view(source, ["report"])
    assert "notes" in visible
    assert "inbox" not in visible


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


@pytest.mark.asyncio
async def test_a_failed_draft_is_not_sent_to_repair(tmp_path):
    gen, fake = draft_assembly_service(tmp_path, format_retries=0)
    fake.responses = [{"draft": "too short"}]
    job = gen.start([{"seed": 14}], {})
    await finish(gen, job)
    attempt = gen.jobs.attempts(job)[0]
    assert [c.payload["operation"] for c in fake.calls] == [draft_assembly.DRAFT]
    assert attempt["outcome"] == "fail"


@pytest.mark.asyncio
async def test_requests_carry_the_prompt_file_version(tmp_path):
    gen, fake = draft_assembly_service(tmp_path)
    fake.responses = [{"draft": DRAFT}, spec()]
    await finish(gen, gen.start([{"seed": 15}], {}))
    assert fake.calls[0].payload["prompt_version"] == prompt_defaults.packaged()["version"]


def roles_service(tmp_path, roles):
    from test_graph_generation import Fake, settings

    from trajectory_remix.graph.bulk import BulkService
    from trajectory_remix.graph.generation import GenerationService
    from trajectory_remix.graph.jobs import JobService
    from trajectory_remix.graph.sampling import SamplerService
    from trajectory_remix.graph.storage import GraphStore

    config = settings(method="draft_assembly")
    config["endpoints"].update({k: dict(config["endpoints"]["g"], model=k) for k in "da"})
    config["endpoint_roles"] = dict(roles, fast="f", critical="c")
    jobs = JobService(GraphStore.initialize(tmp_path / "p.sqlite", config))
    fake = Fake()
    return GenerationService(jobs, SamplerService(jobs), BulkService(jobs), fake), fake


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "roles, expected",
    [
        (dict(draft="d"), ["d", "d", "d"]),
        (dict(draft="d", assembly="a"), ["d", "a", "a"]),
    ],
)
async def test_draft_assembly_and_fix_use_their_own_roles(tmp_path, roles, expected):
    gen, fake = roles_service(tmp_path, roles)
    tangled = spec()
    tangled["events"][2] = dict(tangled["events"][2], after=["ask"])
    fix = {
        "action": "patch",
        "event_id": "plan",
        "expected_source_hash": None,
        "patch": {"after": ["ack"]},
    }

    def patch(t):
        return dict(fix, expected_source_hash=t.payload["source_hash"])

    fake.responses = [{"draft": DRAFT}, tangled, patch, {"action": "done"}]
    await finish(gen, gen.start([{"seed": 16}], {}))
    assert [c.endpoint for c in fake.calls[:3]] == expected


def test_draft_assembly_needs_a_draft_role(tmp_path):
    gen, _ = roles_service(tmp_path, dict(assembly="a"))
    with pytest.raises(ValueError, match="Draft"):
        gen.start([{"seed": 17}], {})
