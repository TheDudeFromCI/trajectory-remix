"""Two-pass generation: a prose draft, one natively assembled graph, then the reviews."""

import pytest
from test_graph_generation import DRAFT, finish, native_report, service, tool_message

from trajectory_remix.graph import draft_assembly, prompt_defaults


@pytest.mark.asyncio
async def test_draft_then_assemble_then_both_reviews(tmp_path):
    gen, fake = service(tmp_path)
    job = gen.start([{"seed": 11}], {})
    await finish(gen, job)
    attempt = gen.jobs.attempts(job)[0]
    source = gen.store.read(attempt["trajectory_id"]).source
    operations = [c.payload["operation"] for c in fake.calls]
    assert operations == [draft_assembly.DRAFT, draft_assembly.ASSEMBLE, "review", "review"]
    assert source.metadata["stage"] == 2 and attempt["outcome"] == "pass"
    assert source.metadata["draft"] == DRAFT
    assert "ack:echo" in source.events and source.events["got"].call_id == "look"
    system, task = fake.calls[1].payload["messages"]
    assert DRAFT in task["content"] and prompt_defaults.authoring() in system["content"]


def test_packaged_authoring_prompt_describes_the_native_workflow():
    guidance = prompt_defaults.authoring()
    for tool in ("post_tool_schema", "post_chat_message", "view_event", "signoff", "reply"):
        assert tool in guidance
    assert "signoff" in prompt_defaults.assembly()


@pytest.mark.asyncio
async def test_a_failed_draft_is_not_reviewed(tmp_path):
    gen, fake = service(tmp_path, format_retries=0)
    fake.responses = [{"draft": "too short"}]
    job = gen.start([{"seed": 14}], {})
    await finish(gen, job)
    attempt = gen.jobs.attempts(job)[0]
    assert [c.payload["operation"] for c in fake.calls] == [draft_assembly.DRAFT]
    assert attempt["outcome"] == "fail"


@pytest.mark.asyncio
async def test_assembly_fixes_a_structural_error_with_only_a_draft_endpoint(tmp_path):
    gen, fake = service(tmp_path, format_retries=0)
    settings = gen.store.settings()
    settings.settings.endpoint_roles = {"draft": "g"}
    gen.store.update_settings(settings.settings, settings.revision)
    fake.responses = [
        DRAFT,
        lambda t: tool_message(
            ("create_event", dict(event_id="a", kind="agent_text", window="n", content="One")),
            ("create_event", dict(event_id="b", kind="agent_text", window="n", content="Two")),
            native_report(t.payload, ["a", "b"]),
        ),
        lambda t: tool_message(
            ("update_event", {"event_id": "b", "after": ["a"], "close_window": True}),
            native_report(t.payload, ["a", "b"]),
        ),
    ]
    job = gen.start([{"seed": 18}], {})
    await finish(gen, job)
    attempt = gen.jobs.attempts(job)[0]
    source = gen.store.read(attempt["trajectory_id"]).source
    assert [(t.endpoint, t.payload["operation"]) for t in fake.calls] == [
        ("g", "draft"),
        ("g", "assembly"),
        ("g", "assembly"),
    ]
    assert "unordered_window_writers" in str(fake.calls[-1].payload["messages"])
    assert source.events["b"].after == ["a"]
    assert source.metadata["stage"] == 0 and attempt["outcome"] == "pass"


@pytest.mark.asyncio
async def test_requests_carry_the_prompt_file_version(tmp_path):
    gen, fake = service(tmp_path)
    await finish(gen, gen.start([{"seed": 15}], {}))
    assert fake.calls[0].payload["prompt_version"] == prompt_defaults.packaged()["version"]


def roles_service(tmp_path, roles):
    from test_graph_generation import Fake, settings

    from trajectory_remix.graph.bulk import BulkService
    from trajectory_remix.graph.generation import GenerationService
    from trajectory_remix.graph.jobs import JobService
    from trajectory_remix.graph.sampling import SamplerService
    from trajectory_remix.graph.storage import GraphStore

    config = settings()
    config["endpoints"].update({k: dict(config["endpoints"]["g"], model=k) for k in "da"})
    config["endpoint_roles"] = dict(roles, fast="f", critical="c")
    store = GraphStore.initialize(tmp_path / "p.sqlite", config)
    jobs = JobService(store)
    fake = Fake()
    fake.store = store
    return GenerationService(jobs, SamplerService(jobs), BulkService(jobs), fake), fake


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "roles, expected",
    [
        (dict(draft="d"), ["d", "d", "f", "c"]),
        (dict(draft="d", assembly="a"), ["d", "a", "f", "c"]),
    ],
)
async def test_draft_and_assembly_use_their_own_roles(tmp_path, roles, expected):
    gen, fake = roles_service(tmp_path, roles)
    await finish(gen, gen.start([{"seed": 16}], {}))
    assert [c.endpoint for c in fake.calls] == expected


def test_draft_assembly_needs_a_draft_role(tmp_path):
    gen, _ = roles_service(tmp_path, dict(assembly="a"))
    with pytest.raises(ValueError, match="Draft"):
        gen.start([{"seed": 17}], {})
