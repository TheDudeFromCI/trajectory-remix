"""Generation prompts live in project settings; the retired Flow leaves nothing behind."""

import pytest
import yaml
from test_graph_generation import finish, service

from trajectory_remix.graph import prompt_defaults, prompt_migration
from trajectory_remix.graph.models import GenerationPrompts, ProjectSettings


def edit_prompts(gen, **prompts):
    record = gen.store.settings()
    data = record.settings.model_dump(mode="json")
    data["prompts"].update(prompts)
    gen.store.update_settings(data, record.revision)


@pytest.mark.asyncio
async def test_runs_use_and_freeze_the_project_prompts(tmp_path):
    gen, fake = service(tmp_path, fast=False)
    edit_prompts(gen, actor="Be Kidra, the persona.", draft="Write the notes.")
    job = gen.start([{"seed": 137}], {})
    edit_prompts(gen, actor="Edited after the run started.")
    await finish(gen, job)
    system = fake.calls[0].payload["messages"][0]["content"]
    assert system.startswith("Write the notes.") and "Be Kidra, the persona." in system
    version = fake.calls[0].payload["prompt_version"]
    assert version.startswith(prompt_defaults.packaged()["version"] + "+")


def test_defaults_come_from_the_packaged_file():
    prompts = GenerationPrompts()
    assert prompts.actor == prompt_defaults.actor()
    assert prompts.assembly == prompt_defaults.assembly()


@pytest.mark.asyncio
async def test_draft_and_assembly_receive_the_parallel_trajectory_guide(tmp_path):
    gen, fake = service(tmp_path, fast=False)
    await finish(gen, gen.start([{"seed": 137}], {}))
    assert [ticket.payload["operation"] for ticket in fake.calls] == ["draft", "assembly"]
    guide = prompt_defaults.packaged()["trajectory_guide"]
    assert "A final comparison depends on" in guide
    for ticket in fake.calls:
        assert ticket.payload["messages"][0]["content"].count(guide) == 1


def test_settings_saved_with_the_retired_method_still_load():
    settings = ProjectSettings.model_validate({"generation": {"method": "flow", "fast": False}})
    assert settings.generation.fast is False and "method" not in settings.generation.model_dump()


def test_runs_refuse_retired_flow_instructions(tmp_path):
    gen, _ = service(tmp_path)
    for retired in ({"plan": {}}, {"flow_yaml": "x"}):
        with pytest.raises(ValueError, match="Draft and Assembly"):
            gen.start([{"seed": 137}], retired)


def test_default_prompts_route(tmp_path):
    from fastapi.testclient import TestClient

    from trajectory_remix.graph.studio_api import create_studio_app

    gen, _ = service(tmp_path)
    app = create_studio_app(gen.store.path, artifact_root=tmp_path / "exports")
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        served = client.get("/api/v1/generation/prompts/default").json()
    assert served == GenerationPrompts().model_dump(mode="json")


def saved_flow(gen, actor):
    with gen.store.connect() as db:
        db.execute(
            "CREATE TABLE graph_flow (id INTEGER PRIMARY KEY, text TEXT, origin TEXT, updated TEXT)"
        )
        text = yaml.safe_dump({"version": "adaptive-flow/1", "actor_instruction": actor})
        db.execute("INSERT INTO graph_flow VALUES(1, ?, 'saved', 'now')", (text,))


@pytest.mark.parametrize(
    "actor, expected",
    [
        # The retired default plus an appended persona keeps the persona on today's default.
        (
            prompt_migration.RETIRED_DEFAULT + "\nPersona: Kidra is warm.",
            GenerationPrompts().actor + "\nPersona: Kidra is warm.",
        ),
        ("A completely custom agent prompt.", "A completely custom agent prompt."),
        (prompt_migration.RETIRED_DEFAULT, GenerationPrompts().actor),  # Unedited: nothing.
    ],
)
def test_a_customised_flow_prompt_moves_into_settings_once(tmp_path, actor, expected):
    gen, _ = service(tmp_path)
    saved_flow(gen, actor)
    prompt_migration.migrate(gen.store)
    assert gen.store.settings().settings.prompts.actor == expected
    with gen.store.connect() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='graph_flow'").fetchone()
    edit_prompts(gen, actor=GenerationPrompts().actor)  # Load default, then a restart:
    prompt_migration.migrate(gen.store)
    assert gen.store.settings().settings.prompts.actor == GenerationPrompts().actor


def test_retired_default_matches_the_old_flow_text():
    old = (
        "Write exactly this next action from the visible evidence and tool contract only."
        " Reasoning is private thinking"
    )
    assert prompt_migration.RETIRED_DEFAULT.startswith(old)
    assert prompt_migration.RETIRED_DEFAULT.endswith("predict an unobserved tool outcome.")
    assert "  " not in prompt_migration.RETIRED_DEFAULT


def test_retired_instructions_reserve_no_attempts(tmp_path):
    gen, _ = service(tmp_path)
    before = len(gen.jobs.listing())
    with pytest.raises(ValueError):
        gen.start([{"seed": 137}], {"flow_yaml": "x"})
    assert len(gen.jobs.listing()) == before


def test_new_matrix_dimensions_reach_draft_and_assembly(tmp_path):
    import json

    from trajectory_remix.graph import draft_assembly

    gen, _ = service(tmp_path)
    settings = gen.store.settings().settings
    proposal = gen.sampler.propose(seed=137)["proposal"]
    proposal["future_dimension"] = {"quantum_potatoes": {"required": True}}
    plan = draft_assembly.plan(proposal, settings)
    assert plan["proposal"] == proposal
    payload = draft_assembly.payload(dict(errors=0), plan, settings)
    assert "quantum_potatoes" in json.dumps(payload["messages"])


def test_endpoints_default_to_provider_token_limits():
    from trajectory_remix.graph.models import Endpoint

    endpoint = Endpoint.model_validate(dict(url="http://127.0.0.1:8000/v1", model="m"))
    assert endpoint.context_window_tokens == -1 and endpoint.max_output_tokens == -1
    assert endpoint.bytes_per_token == 3.0
