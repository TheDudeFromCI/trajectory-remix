"""Declarative scenario loading. References select data; dependencies only schedule it."""

from __future__ import annotations

import copy
import math
import random
import re
from pathlib import Path
from typing import Any, Literal

import jsonschema
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

REFERENCE = re.compile(r"^\$\{([a-zA-Z0-9_.-]+)\}$")


class Definition(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Failure(Definition):
    action: Literal["reject", "fallback"] = "reject"
    value: Any = None


class SemanticRule(Definition):
    requirement: str = Field(min_length=1, pattern=r"\S")
    scope: Literal["group", "sample"] = "group"
    allow_not_applicable: bool = False


class ReviewCheck(Definition):
    prompt: str = Field(min_length=1)
    view: Literal["layout", "tools", "reasoning", "correctness"]
    inputs: list[str] = Field(default_factory=list)
    events_per_chunk: int = Field(default=10, ge=1, le=100)
    overlap: int = Field(default=2, ge=0, le=99)
    max_input_tokens: int = Field(default=6000, ge=512, le=100000)
    content_tokens: int = Field(default=800, ge=64, le=8000)
    context_rounds: int = Field(default=2, ge=0, le=5)
    rules: dict[str, SemanticRule] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_limits(self):
        for name in self.rules:
            if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", name):
                raise ValueError(f"Invalid semantic rule ID: {name}")
        if self.overlap >= self.events_per_chunk:
            raise ValueError("Review overlap must be smaller than events_per_chunk")
        if self.content_tokens >= self.max_input_tokens:
            raise ValueError("content_tokens must be smaller than max_input_tokens")
        return self


class Step(Definition):
    uses: Literal[
        "llm.generate",
        "llm.content",
        "remix.assemble",
        "flow.repeat",
        "remix.novelty",
        "remix.message",
        "remix.tools",
        "remix.setup",
        "remix.draft",
        "remix.validate",
        "remix.reject",
    ]
    needs: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    prompt: str | None = None
    profile: str | None = None
    endpoint: str = "generator"
    output_schema: str | None = None
    visible_windows: list[str] = Field(default_factory=list)
    max_input_tokens: int | None = Field(default=None, ge=512, le=100000)
    retries: int = Field(default=0, ge=0, le=5)
    on_failure: Failure = Field(default_factory=Failure)
    when: Any = True
    steps: dict[str, Step] = Field(default_factory=dict)
    max_iterations: int = Field(default=1, ge=1, le=20)
    until: str | None = None
    history: dict[str, Any] = Field(default_factory=dict)
    repair_prompt: str | None = None
    checks: dict[str, str | ReviewCheck] = Field(default_factory=dict)
    repair_mode: Literal["replay", "edits"] = "replay"
    repair_max_input_tokens: int = Field(default=8000, ge=512, le=100000)
    repair_guides: list[str] = Field(default_factory=list)
    max_repairs: int = Field(default=0, ge=0, le=5)
    review_retries: int = Field(default=2, ge=0, le=5)
    level: Literal[1, 2] = 1
    attempts: int = Field(default=3, ge=1, le=10)
    fallback: bool = True


def check_scenario_endpoints(name, flow, endpoints, *, content_first=False):
    available = {endpoint["id"] for endpoint in endpoints}

    def check(steps, prefix=""):
        for step_name, step in steps.items():
            if step.when is False:
                continue
            path = prefix + step_name
            if (
                step.uses in (("llm.generate", "llm.content") if content_first else ("llm.generate", "llm.content", "remix.validate", "remix.novelty"))
                and step.endpoint not in available
            ):
                raise ValueError(
                    f"Scenario '{name}' requires endpoint '{step.endpoint}' (step '{path}'). Add that endpoint in Generation Settings or update flow.yaml."
                )
            check(step.steps, path + ".")

    check(flow.steps)


class Tag(Definition):
    name: str = Field(min_length=1)
    description: str = ""
    weight: float = Field(default=1, ge=0, allow_inf_nan=False)


class Profile(Definition):
    instructions: str = ""
    tags: dict[str, list[Tag]] = Field(default_factory=dict)


class Flow(Definition):
    generation_mode: Literal["existing", "content-first"] = "existing"
    system_prompt: str = "You are Kidra, a coordinator."
    version: Literal[1] = 1
    description: str = ""
    tag: str | None = Field(default=None, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    schemas: dict[str, dict] = Field(default_factory=dict)
    tags: dict[str, list[Tag]] = Field(default_factory=dict)
    roles: dict[str, Profile] = Field(default_factory=dict)
    steps: dict[str, Step]

    @model_validator(mode="after")
    def validate_flow(self):
        if not self.steps:
            raise ValueError("A scenario must contain steps")
        for schema in self.schemas.values():
            jsonschema.Draft202012Validator.check_schema(schema)
            if schema.get("type") != "object":
                raise ValueError("Output schemas must describe objects")
        for pools in [self.tags, *(r.tags for r in self.roles.values())]:
            for options in pools.values():
                if (
                    not options
                    or not math.isfinite(sum(t.weight for t in options))
                    or not any(t.weight > 0 for t in options)
                ):
                    raise ValueError("Each tag dimension needs a positive total weight")
        self._check_steps(self.steps)
        return self

    def _check_steps(self, steps):
        pending, done = dict(steps), set()
        for name, step in steps.items():
            if set(step.needs) - steps.keys():
                raise ValueError(f"Unknown dependency in {name}")
        for name, step in steps.items():
            if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", name):
                raise ValueError(f"Invalid step name: {name}")
            if step.output_schema and step.output_schema not in self.schemas:
                raise ValueError(f"Unknown output schema: {step.output_schema}")
            if step.profile and step.profile not in self.roles:
                raise ValueError(f"Unknown role: {step.profile}")
            if (
                step.uses in ("llm.generate", "llm.content", "remix.novelty", "remix.validate")
                and not step.prompt
                and not (step.uses == "remix.validate" and step.checks)
            ):
                raise ValueError(f"{name} needs a prompt")
            if step.checks:
                if step.uses != "remix.validate" or step.prompt:
                    raise ValueError(
                        "checks requires remix.validate and replaces prompt"
                    )
                for check, prompt in step.checks.items():
                    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", check) or not prompt:
                        raise ValueError(f"Invalid validation check: {check}")
            if step.uses == "flow.repeat":
                if not step.steps or not step.until:
                    raise ValueError(f"{name} needs steps and an until reference")
                for ref in references(step.until):
                    if (
                        not ref.startswith("steps.")
                        or ref.split(".")[1] not in step.steps
                    ):
                        raise ValueError(
                            f"{name}: until must reference a nested step output"
                        )
                self._check_steps(step.steps)
            elif step.steps:
                raise ValueError("Only repeat steps can contain steps")
            if set(step.needs) - steps.keys():
                raise ValueError(f"Unknown dependency in {name}")
            # Local output references must be declared dependencies (or their ancestors).
            ancestors, frontier = set(), list(step.needs)
            while frontier:
                dep = frontier.pop()
                if dep not in ancestors:
                    ancestors.add(dep)
                    frontier.extend(steps[dep].needs)
            for ref in references([step.inputs, step.when, step.on_failure.value]):
                if ref.startswith("steps.") and ref.split(".")[1] not in ancestors:
                    raise ValueError(f"{name}: reference {ref} needs a dependency")
        while pending:
            ready = [n for n, s in pending.items() if set(s.needs) <= done]
            if not ready:
                raise ValueError("Scenario dependencies contain a cycle")
            for name in ready:
                pending.pop(name)
                done.add(name)


def references(value):
    if isinstance(value, str):
        match = REFERENCE.fullmatch(value)
        if match:
            yield match[1]
    elif isinstance(value, dict):
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def resolve(value, context):
    if isinstance(value, str) and (match := REFERENCE.fullmatch(value)):
        result = context
        for key in match[1].split("."):
            try:
                result = result[int(key)] if isinstance(result, list) else result[key]
            except (KeyError, IndexError, TypeError, ValueError) as error:
                raise ValueError(f"Unresolved scenario reference: {value}") from error
        return copy.deepcopy(result)
    if isinstance(value, dict):
        return {k: resolve(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, context) for v in value]
    return value


def sample_tags(pools):
    return {
        dimension: random.choices(options, weights=[o.weight for o in options], k=1)[
            0
        ].model_dump(exclude={"weight"})
        for dimension, options in pools.items()
    }


class UniqueLoader(yaml.SafeLoader):
    """Reject silently overwritten CI-style step definitions."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, (str, int, float, bool)) or key in result:
                raise ValueError(f"Duplicate or invalid YAML key: {key}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


class Scenario:
    def __init__(self, path):
        self.path = path.resolve()
        self.name = path.name
        raw = (path / "flow.yaml").read_text()
        if len(raw) > 1_000_000:
            raise ValueError("flow.yaml exceeds 1 MB")
        try:
            self.flow = Flow.model_validate(yaml.load(raw, Loader=UniqueLoader))
        except (yaml.YAMLError, ValueError) as error:
            raise ValueError(f"Invalid scenario {self.name}: {error}") from error
        self.prompts = {}

        def collect(steps):
            for step in steps.values():
                for filename in (
                    step.prompt,
                    step.repair_prompt,
                    *(
                        c if isinstance(c, str) else c.prompt
                        for c in step.checks.values()
                    ),
                    *step.repair_guides,
                ):
                    if filename:
                        target = (self.path / filename).resolve()
                        if (
                            not target.is_relative_to(self.path)
                            or target.suffix != ".md"
                        ):
                            raise ValueError(
                                "Prompts must be Markdown files inside their scenario"
                            )
                        text = target.read_text()
                        if len(text) > 200_000:
                            raise ValueError("Prompt exceeds 200 KB")
                        self.prompts[filename] = text
                collect(step.steps)

        collect(self.flow.steps)


class ScenarioRegistry:
    def __init__(self, root=None):
        self.root = Path(root).resolve() if root is not None else None
        self.scenarios = {}
        if self.root is not None:
            if not self.root.is_dir():
                raise ValueError(f"Scenario directory does not exist: {self.root}")
            for filename in sorted(self.root.rglob("flow.yaml")):
                if not filename.resolve().is_relative_to(self.root):
                    raise ValueError(
                        "Scenario files must stay inside the scenario directory"
                    )
                scenario = Scenario(filename.parent)
                if scenario.name in self.scenarios:
                    raise ValueError(f"Duplicate scenario type: {scenario.name}")
                tag = scenario.flow.tag or scenario.name
                if any((s.flow.tag or s.name).casefold() == tag.casefold() for s in self.scenarios.values()):
                    raise ValueError(f"Duplicate scenario tag: {tag}")
                self.scenarios[scenario.name] = scenario

    def listing(self):
        return [
            {
                "name": s.name,
                "tag": s.flow.tag or s.name,
                "description": s.flow.description,
                "tag_pools": {
                    "scenario": {
                        k: [t.model_dump() for t in v] for k, v in s.flow.tags.items()
                    },
                    **{
                        role: {
                            k: [t.model_dump() for t in v]
                            for k, v in profile.tags.items()
                        }
                        for role, profile in s.flow.roles.items()
                    },
                },
            }
            # Keep existing-mode scenarios first so adding opt-in examples does
            # not change the dashboard's default generation workflow.
            for s in sorted(self.scenarios.values(), key=lambda s: (s.flow.generation_mode == 'content-first', s.name))
        ]
