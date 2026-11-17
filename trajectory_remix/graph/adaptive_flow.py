"""Frozen YAML control flow and adaptive, author-only event selection."""

from copy import deepcopy
from importlib.resources import files

import yaml
from pydantic import Field, model_validator

from .flow import EventTask
from .models import DTO, GraphSource, JsonValue, Window, canonical, digest
from .prompt_order import ordered, source_json
from .validation import diagnose


class FlowLoader(yaml.SafeLoader):
    """Reject ambiguous duplicate fields rather than silently changing a policy."""


def _mapping(loader, node):
    loader.flatten_mapping(node)
    pairs = loader.construct_pairs(node)
    result = {}
    for key, value in pairs:
        try:
            if key in result:
                raise ValueError(f"Duplicate Flow YAML field: {key}")
            result[key] = value
        except TypeError as error:
            raise ValueError("Flow YAML mapping keys must be scalar") from error
    return result


FlowLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


class Node(DTO):
    kind: str
    instruction: str = ""
    next: str | None = None
    path: str = "finished"
    equals: JsonValue = True
    then: str | None = None
    otherwise: str | None = None


class Flow(DTO):
    version: str = "adaptive-flow/1"
    start: str
    max_steps: int = Field(default=600, ge=1, le=10000)
    max_events: int = Field(default=128, ge=1, le=2000)
    actor_instruction: str = Field(min_length=1)
    # Shared system prompt for every select node. When set, each node's instruction is
    # sent after the data as current_step, so all selectors share one cached prefix.
    selector_instruction: str = ""
    nodes: dict[str, Node]

    @model_validator(mode="after")
    def valid_graph(self):
        if self.version != "adaptive-flow/1" or self.start not in self.nodes:
            raise ValueError("Unsupported Flow version or missing start node")
        for node in self.nodes.values():
            if node.kind not in {"select", "event", "branch", "end"}:
                raise ValueError("Unknown Flow node kind")
            targets = []
            if node.kind in {"select", "event"}:
                targets = [node.next]
            elif node.kind == "branch":
                targets = [node.then, node.otherwise]
            if any(target not in self.nodes for target in targets):
                raise ValueError("Flow transition references a missing node")
        return self


class Selection(DTO):
    finished: bool
    reason: str = Field(min_length=1)
    windows: dict[str, Window] = Field(default_factory=dict)
    task: EventTask | None = None


def load_flow(path=None, text=None):
    if text is None:
        text = (
            path.read_text()
            if path and path.is_file()
            else files(__package__).joinpath("flow.yaml").read_text()
        )
    if not isinstance(text, str):
        raise ValueError("flow_yaml must be YAML text")
    if len(text.encode()) > 262144:
        raise ValueError("Flow YAML exceeds 256 KiB")
    try:
        raw = yaml.load(text, Loader=FlowLoader)
        flow = Flow.model_validate(raw)
    except (yaml.YAMLError, RecursionError) as error:
        raise ValueError("Invalid Flow YAML") from error
    result = flow.model_dump(mode="json")
    return dict(flow=result, flow_hash=digest(result))


def state(plan):
    return dict(node=plan["flow"]["start"], steps=0, output=None, task=None)


def branch(node, output):
    value = output
    for part in node["path"].split("."):
        if not isinstance(value, dict) or part not in value:
            return node["otherwise"]
        value = value[part]
    return (
        node["then"]
        if type(value) is type(node["equals"]) and value == node["equals"]
        else node["otherwise"]
    )


def previous_notes(output):
    """The last selection as the selector's own notes. Its task envelope is already in the
    source once written, so only the decision, reasoning and task ID are repeated."""
    if not isinstance(output, dict):
        return output
    task = output.get("task")
    return dict(
        finished=output.get("finished"),
        reason=output.get("reason"),
        task_id=task.get("id") if isinstance(task, dict) else None,
    )


def selection_payload(source, plan, current, correction=""):
    flow, node_id = plan["flow"], current["node"]
    node = flow["nodes"][node_id]
    remaining = flow["max_events"] - len(source.events)
    shared = flow.get("selector_instruction", "")
    # Most stable first, so requests share a long cacheable prefix. With a shared
    # selector instruction the step instruction comes last, nearest the answer.
    pairs = [
        ("tools", canonical(plan["tools"])),
        ("proposal", canonical(plan["proposal"])),
        ("source", source_json(source)),
    ]
    if shared:
        step = dict(node=node_id, instruction=node["instruction"])
        pairs.append(("current_step", canonical(step)))
    pairs += [
        ("remaining_events", canonical(remaining)),
        ("previous", canonical(previous_notes(current.get("output")))),
        ("correction", canonical(correction)),
    ]
    return dict(
        operation="flow_select",
        prompt_version="adaptive-flow/2",
        schema=Selection.model_json_schema(),
        messages=[
            dict(role="system", content=shared or node["instruction"]),
            dict(role="user", content=ordered(pairs)),
        ],
    )


def accept_selection(source, body, plan, settings):
    selection = Selection.model_validate(body)
    if selection.finished:
        if selection.task is not None or selection.windows or not source.events:
            raise ValueError("Finish requires existing events and no pending task/windows")
        if not any(e.kind in {"agent_text", "tool_call"} for e in source.events.values()):
            raise ValueError("Finish requires at least one authored actor event")
        if any(d.severity == "error" for d in diagnose(source, settings)):
            raise ValueError("Cannot finish a structurally broken episode")
        return source, None, selection.model_dump(mode="json")
    task = selection.task
    if task is None or task.id in source.events or task.id.endswith(":echo"):
        raise ValueError("Select a new event with a unique non-echo ID")
    if task.when:
        raise ValueError("Adaptive conditions belong in Flow branch nodes")
    if "content" in task.envelope:
        raise ValueError("Selection cannot pre-author event content")
    data = source.to_dict()
    for key, window in selection.windows.items():
        if key in source.windows:
            raise ValueError("Selection cannot redefine an existing window")
        data["windows"][key] = window.model_dump(mode="json")
    actor = task.envelope.get("kind") in {"agent_text", "tool_call"}
    if actor:
        if task.world:
            raise ValueError("Actor task cannot receive private world data")
        # A selector may plan with private targets. Its free-form instructions
        # must never smuggle a solution or hidden fact into an actor prompt.
        task.instruction = plan["flow"]["actor_instruction"]
    task.envelope.setdefault("after", [])
    if set(task.envelope["after"]) - source.events.keys():
        raise ValueError("Selected event must depend only on committed events")
    data["events"][task.id] = dict(task.envelope, content="placeholder")
    candidate = GraphSource.model_validate(data)
    errors = [d for d in diagnose(candidate, settings) if d.severity == "error"]
    if errors:
        raise ValueError(
            "Selected event invalid: " + "; ".join(f"{d.code}: {d.message}" for d in errors[:8])
        )
    event = candidate.events[task.id]
    if event.supervision.masked == actor:
        raise ValueError("Actor events must be supervised; external events must be masked")
    data["events"].pop(task.id)
    return (
        GraphSource.model_validate(data),
        task.model_dump(mode="json"),
        selection.model_dump(mode="json"),
    )


def frozen_plan(proposal, settings, snapshot):
    return dict(
        **deepcopy(snapshot),
        windows={},
        proposal=deepcopy(proposal),
        tools={key: value.model_dump(mode="json") for key, value in settings.tools.items()},
    )
