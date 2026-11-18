"""Two-pass generation: a prose draft of the whole episode, then one assembled graph.

Pass 1 (draft) writes working notes: messages, reasoning, tool calls with example outputs.
Pass 2 (assemble) turns them into a compact spec, which compact_spec builds into a graph.
Review and repair then run as for any generated trajectory.
"""

from copy import deepcopy
from importlib.resources import files

import yaml

from . import compact_spec, reviews
from .models import GraphSource, canonical
from .validation import diagnose

DRAFT, ASSEMBLE = "two_pass_draft", "two_pass_assemble"
OPERATIONS = {DRAFT, ASSEMBLE}
VERSION = "two-pass/1"
DRAFT_SCHEMA = dict(
    type="object",
    properties=dict(draft=dict(type="string", minLength=200)),
    required=["draft"],
    additionalProperties=False,
)


def prompts():
    return yaml.safe_load(files(__package__).joinpath("two_pass.yaml").read_text())


def plan(proposal, settings, snapshot):
    """The frozen per-trajectory plan. The actor guide comes from the project flow."""
    return dict(
        method="two_pass",
        windows={},
        proposal=deepcopy(proposal),
        tools={key: value.model_dump(mode="json") for key, value in settings.tools.items()},
        actor_instruction=snapshot["flow"]["actor_instruction"],
        prompts=prompts(),
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
    if "draft" not in cp:
        return dict(
            operation=DRAFT,
            prompt_version=VERSION,
            schema=DRAFT_SCHEMA,
            messages=_messages(
                plan, settings, plan["prompts"]["draft_instruction"], dict(correction=correction)
            ),
        )
    return dict(
        operation=ASSEMBLE,
        prompt_version=VERSION,
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
    built = GraphSource.model_validate(compact_spec.build(body, base, plan["tools"]))
    if not any(e.kind in {"agent_text", "tool_call"} for e in built.events.values()):
        raise ValueError("The episode needs at least one think or call event by the agent")
    return built, [d for d in diagnose(built, settings) if d.severity == "error"]


def with_draft(source, draft):
    data = source.to_dict()
    data.setdefault("metadata", {})["two_pass"] = dict(draft=draft)
    return GraphSource.model_validate(data)
