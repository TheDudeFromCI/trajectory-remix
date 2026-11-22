"""Endpoint message layouts and upgrades of the shipped default prompts."""

import pytest
from test_graph_generation import finish, service

from trajectory_remix.graph import authoring_session, endpoint_prompts, prompt_defaults
from trajectory_remix.graph.models import GenerationPrompts, GraphSource


def test_fences_outgrow_backticks_inside_the_text():
    assert endpoint_prompts.fence("plain") == "```\nplain\n```"
    assert endpoint_prompts.fence("has ```` four", "json").startswith("`````json\n")


def test_comment_table_escapes_cells_and_lists_threads():
    table = endpoint_prompts.comment_table(
        [
            dict(
                id="c1",
                target=dict(kind="window", id="w"),
                body="a | b\nc",
                author="user",
                replies=[dict(author="agent", body="Done."), dict(author="user", body="No.")],
            ),
            dict(id="c2", target=dict(kind="trajectory", id=None), body="x", replies=[]),
        ]
    )
    rows = table.splitlines()
    assert rows[0] == "| ID | Target | Comment | Thread |"
    assert rows[2] == "| `c1` | window `w` | a \\| b<br>c | agent: Done.<br>user: No. |"
    assert rows[3] == "| `c2` | trajectory | x | - |"


def test_review_and_repair_tasks_name_the_checklist_then_the_trajectory():
    criteria = {
        "completeness": dict(text="Whole episode.", allow_not_applicable=False),
        "critical.x": dict(text="Maybe.", allow_not_applicable=True),
    }
    review = endpoint_prompts.review_user(criteria, "t-1")
    assert review.splitlines()[:4] == [
        endpoint_prompts.REVIEW,
        "",
        "Review Checklist:",
        "- `completeness`: Whole episode.",
    ]
    assert "- `critical.x`: Maybe. (may be not applicable)" in review
    assert review.endswith("Trajectory Id: `t-1`")
    repair = endpoint_prompts.review_user(
        criteria, "t-1", repair=True, context=dict(failed_review={"summary": "Gap."})
    )
    assert repair.startswith(endpoint_prompts.REPAIR) and '"summary": "Gap."' in repair


def test_repair_opens_with_failed_review_and_project_tools(tmp_path):
    gen, _ = service(tmp_path)
    settings = gen.store.settings().settings
    source = GraphSource.model_validate(
        dict(format="kidra-trajectory-graph/1", id="7d3f8a2e-1b4c-4e5f-9a6b-0c1d2e3f4a5b")
    )
    session = authoring_session.begin(source, settings, "repair", "fast", prior={"summary": "Gap"})
    system, task = session["messages"]
    assert system["content"] == settings.prompts.actor + "\n\n" + settings.prompts.authoring
    assert task["content"].startswith(endpoint_prompts.REPAIR)
    assert "Failed review:" in task["content"] and "Project tools:" in task["content"]


@pytest.mark.asyncio
async def test_assembly_gets_its_prompt_the_trajectory_id_and_the_draft(tmp_path):
    gen, fake = service(tmp_path, fast=False)
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    ident = gen.jobs.attempts(job)[0]["trajectory_id"]
    prompts = gen.store.settings().settings.prompts
    draft = fake.calls[0].payload
    assembly = next(c.payload for c in fake.calls if c.payload["operation"] == "assembly")
    assert draft["messages"][0]["content"] == prompts.actor
    task = assembly["messages"][1]["content"]
    assert task.startswith(prompts.assembly + f"\n\nTrajectory Id: `{ident}`")
    assert "\n\nDraft:\n```\nWorking notes." in task


def test_shipped_defaults_upgrade_and_custom_prompts_stay():
    retired = prompt_defaults.retired_actors()[0]
    upgraded = GenerationPrompts(actor=retired + "\nPersona: calm.")
    assert upgraded.actor == prompt_defaults.actor() + "\nPersona: calm."
    assert upgraded.authoring == prompt_defaults.authoring()
    custom = GenerationPrompts(actor="Mine.", draft="My draft.", assembly="My assembly.")
    assert (custom.actor, custom.draft, custom.assembly) == ("Mine.", "My draft.", "My assembly.")
