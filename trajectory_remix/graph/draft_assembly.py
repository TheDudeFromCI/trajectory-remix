"""Draft and Assembly: a prose draft of the whole episode, then one assembled graph.

Draft writes working notes: messages, reasoning, tool calls with example outputs.
Assembly turns them into a compact spec, which compact_spec builds into a graph; the
assembly fix then corrects it. Review and repair run as for any generated trajectory.
The prompts come from the project settings (settings.prompts).
"""

from copy import deepcopy

from . import compact_spec, prompt_defaults, reviews
from .models import GraphSource, canonical, digest
from .validation import diagnose

DRAFT, ASSEMBLE = "draft", "assembly"
OPERATIONS = {DRAFT, ASSEMBLE}
DRAFT_SCHEMA = dict(
    type="object",
    properties=dict(draft=dict(type="string", minLength=200)),
    required=["draft"],
    additionalProperties=False,
)


def prompts(settings):
    """The frozen prompts for a run, with a version that changes when they are edited."""
    # The Assistant prompt is not part of generation, so editing it keeps run versions.
    exclude = {"assistant"}
    chosen = settings.prompts.model_dump(mode="json", exclude=exclude)
    base = prompt_defaults.packaged()["version"]
    custom = chosen != type(settings.prompts)().model_dump(mode="json", exclude=exclude)
    return dict(
        version=f"{base}+{digest(chosen)[:12]}" if custom else base,
        draft_instruction=chosen["draft"],
        assemble_instruction=chosen["assembly"],
        actor_instruction=chosen["actor"],
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


def _messages(plan, settings, instruction, extra):
    system = f"{instruction}\n\nActor guide:\n{plan['actor_instruction']}"
    user = dict(
        proposal=plan["proposal"],
        tools=plan["tools"],
        checklist=reviews.checklist(settings, "critical"),
        **extra,
    )
    return [dict(role="system", content=system), dict(role="user", content=canonical(user))]


def payload(cp, plan, settings):
    """The next request, or None when the assembled graph is ready for review."""
    correction = cp.get("flow_error", "") if cp["errors"] else ""
    version = plan["prompts"]["version"]
    if "draft" not in cp:
        return dict(
            operation=DRAFT,
            prompt_version=version,
            schema=DRAFT_SCHEMA,
            messages=_messages(
                plan, settings, plan["prompts"]["draft_instruction"], dict(correction=correction)
            ),
        )
    return dict(
        operation=ASSEMBLE,
        prompt_version=version,
        schema=compact_spec.schema(list(plan["tools"])),
        messages=_messages(
            plan,
            settings,
            plan["prompts"]["assemble_instruction"],
            dict(notes=cp["draft"], correction=correction),
        ),
    )


def assemble(source, body, plan, settings):
    """Build the graph. Returns (source, structural errors); raises on an unusable spec."""
    base = source.to_dict()
    base.pop("windows", None)
    base.pop("events", None)
    data = compact_spec.build(body, base, plan["tools"], settings.tool_schema_extra_chance)
    built = GraphSource.model_validate(data)
    if not any(e.kind in {"agent_text", "tool_call"} for e in built.events.values()):
        raise ValueError("The episode needs at least one think or call event by the agent")
    return built, [d for d in diagnose(built, settings) if d.severity == "error"]


def with_draft(source, draft):
    data = source.to_dict()
    data.setdefault("metadata", {})["draft"] = draft
    return GraphSource.model_validate(data)
