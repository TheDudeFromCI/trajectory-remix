"""Draft and Assembly: a prose draft of the whole episode, then one assembled graph.

Draft writes working notes in one JSON reply: messages, reasoning, tool calls with example
outputs. Assembly builds the graph from them in a native tool session (authoring_session).
The prompts come from the project settings (settings.prompts) and freeze with the run.
"""

from copy import deepcopy

from . import endpoint_prompts, prompt_defaults
from .models import GraphSource, digest

# Runs and manual reviews record the pipeline they were made for. Jobs made for an
# earlier pipeline pause with a message instead of resuming into phases that are gone.
DRIVER = "draft-assembly/3"
DRAFT, ASSEMBLE = "draft", "assembly"
DRAFT_SCHEMA = dict(
    type="object",
    properties=dict(draft=dict(type="string", minLength=200)),
    required=["draft"],
    additionalProperties=False,
)


def prompts(settings):
    """The frozen prompts for a run, with a version that changes when they are edited."""
    chosen = settings.prompts.model_dump(mode="json")
    base = prompt_defaults.packaged()["version"]
    custom = chosen != type(settings.prompts)().model_dump(mode="json")
    return dict(
        version=f"{base}+{digest(chosen)[:12]}" if custom else base,
        draft_instruction=chosen["draft"],
        assemble_instruction=chosen["assembly"],
        actor_instruction=chosen["actor"],
        authoring_instruction=chosen["authoring"],
    )


def role(settings, stage):
    """The endpoint role for a stage: its own role, else Draft's."""
    roles = settings.endpoint_roles
    if stage == ASSEMBLE and "assembly" in roles:
        return "assembly"
    return "draft"


def plan(proposal, settings):
    """The frozen per-trajectory plan: proposal, tools and the project's prompts."""
    frozen = prompts(settings)
    return dict(
        method="draft_assembly",
        windows={},
        proposal=deepcopy(proposal),
        tools={key: value.model_dump(mode="json") for key, value in settings.tools.items()},
        actor_instruction=frozen["actor_instruction"],
        prompts=frozen,
    )


def authoring_instruction(plan):
    return plan["prompts"]["authoring_instruction"]


def _draft_messages(plan, correction):
    user = endpoint_prompts.draft_user(
        plan["prompts"]["draft_instruction"], plan["proposal"], plan["tools"], correction
    )
    return [
        dict(role="system", content=endpoint_prompts.system(plan["actor_instruction"])),
        dict(role="user", content=user),
    ]


def payload(cp, plan):
    """The Draft request, with a correction after an unusable reply."""
    correction = cp.get("flow_error", "") if cp["errors"] else ""
    return dict(
        operation=DRAFT,
        prompt_version=plan["prompts"]["version"],
        schema=DRAFT_SCHEMA,
        messages=_draft_messages(plan, correction),
    )


def with_draft(source, draft):
    data = source.to_dict()
    data.setdefault("metadata", {})["draft"] = draft
    return GraphSource.model_validate(data)
