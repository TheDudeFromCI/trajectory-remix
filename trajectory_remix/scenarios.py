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


class Step(Definition):
    uses: Literal[
        "llm.generate",
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
    retries: int = Field(default=0, ge=0, le=5)
    on_failure: Failure = Field(default_factory=Failure)
    when: Any = True
    steps: dict[str, Step] = Field(default_factory=dict)
    max_iterations: int = Field(default=1, ge=1, le=20)
    until: str | None = None
    history: dict[str, Any] = Field(default_factory=dict)
    repair_prompt: str | None = None
    max_repairs: int = Field(default=0, ge=0, le=5)
    level: Literal[1, 2] = 1
    attempts: int = Field(default=3, ge=1, le=10)
    fallback: bool = True


class Tag(Definition):
    name: str = Field(min_length=1)
    description: str = ""
    weight: float = Field(default=1, ge=0, allow_inf_nan=False)


class Profile(Definition):
    instructions: str = ""
    tags: dict[str, list[Tag]] = Field(default_factory=dict)


class Flow(Definition):
    system_prompt: str = "You are Kidra, a coordinator."
    version: Literal[1] = 1
    description: str = ""
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
                step.uses in ("llm.generate", "remix.novelty", "remix.validate")
                and not step.prompt
            ):
                raise ValueError(f"{name} needs a prompt")
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
                for filename in (step.prompt, step.repair_prompt):
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
                self.scenarios[scenario.name] = scenario

    def listing(self):
        return [
            {
                "name": s.name,
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
            for s in self.scenarios.values()
        ]
