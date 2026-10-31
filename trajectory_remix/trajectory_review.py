"""Portable scenario checklists for external MCP review; no model calls."""

import copy
import hashlib
from pathlib import Path
from typing import Literal
import yaml

from pydantic import Field

from .review import checklist_schema, compact
from .scenarios import Definition, SemanticRule, Step


class ChecklistCheck(Definition):
    prompt: str
    rules: dict[str, SemanticRule] = Field(default_factory=dict)
    view: Literal['layout', 'tools', 'reasoning', 'correctness'] | None = None
    repair_prompt: str = ''
    repair_guides: list[str] = Field(default_factory=list)


class ScenarioChecklist(Definition):
    version: Literal[1] = 1
    scenario: str = Field(min_length=1)
    tag: str = Field(min_length=1)
    checks: dict[str, ChecklistCheck] = Field(default_factory=dict)


def build_checklist(scenario):
    checks = {}

    def collect(steps, prefix=''):
        for name, step in steps.items():
            if step.when is False:
                continue
            path = prefix + name
            if step.uses == 'remix.validate':
                definitions = step.checks or {'review': step.prompt}
                for check, definition in definitions.items():
                    checks[f'{path}.{check}'] = ChecklistCheck(
                        prompt=scenario.prompts[definition if isinstance(definition, str) else definition.prompt],
                        rules={} if isinstance(definition, str) else definition.rules,
                        view=None if isinstance(definition, str) else definition.view,
                        repair_prompt=scenario.prompts.get(step.repair_prompt, ''),
                        repair_guides=[scenario.prompts[p] for p in step.repair_guides],
                    )
            collect(step.steps, path + '.')

    collect(scenario.flow.steps)
    if not checks:
        return standard_checklist(scenario.name, scenario.flow.tag or scenario.name)
    return ScenarioChecklist(
        scenario=scenario.name, tag=scenario.flow.tag or scenario.name, checks=checks
    )


def standard_checklist(scenario='manual', tag=None):
    """One shared review contract, entirely outside the creation graph."""
    root = Path(__file__).with_name('review_templates')
    review = Step.model_validate(yaml.safe_load((root / 'review.yaml').read_text()))
    return ScenarioChecklist(scenario=scenario, tag=tag or scenario, checks={
        'validate.' + name: ChecklistCheck(
            prompt=(root / definition.prompt).read_text(), rules=definition.rules,
            view=definition.view,
            repair_prompt=(root / review.repair_prompt).read_text() if review.repair_prompt else '',
            repair_guides=[(root / guide).read_text() for guide in review.repair_guides],
        ) for name, definition in review.checks.items()
    })


def fingerprint(checklist):
    return hashlib.sha256(compact(checklist.model_dump()).encode()).hexdigest()


def result_schema(checklist):
    definitions = {}
    for index, check in enumerate(checklist.checks.values()):
        schema = checklist_schema(check.rules) if check.rules else {
            'type': 'object',
            'properties': {
                'valid': {'type': 'boolean'},
                'feedback': {'type': 'array', 'items': {'type': 'string'}},
            },
            'required': ['valid', 'feedback'], 'additionalProperties': False,
        }
        # Each category schema has local $defs. Rebase refs when nesting them
        # into the complete review schema, so callers can validate it directly.
        def rebase(value):
            if isinstance(value, dict):
                return {k: f'#/$defs/c{index}/' + v[2:] if k == '$ref' and v.startswith('#/') else rebase(v) for k, v in value.items()}
            if isinstance(value, list):
                return [rebase(v) for v in value]
            return value
        definitions[f'c{index}'] = rebase(schema)
    return {
        'type': 'object', '$defs': definitions,
        'properties': {name: {'$ref': f'#/$defs/c{i}'} for i, name in enumerate(checklist.checks)},
        'required': list(checklist.checks), 'additionalProperties': False,
    }


def public_checklist(checklist, source):
    result = copy.deepcopy(checklist.model_dump())
    result.update(checklist_hash=fingerprint(checklist), source=source, results_schema=result_schema(checklist))
    for name, check in checklist.checks.items():
        result['checks'][name]['instructions'] = (
            "Assess every rule across the complete trajectory, including all event groups. "
            "Write evidence before status; failures cite event IDs and actionable feedback. "
            "Other feedback is empty. Use not_applicable only where explicitly permitted. "
            "Resolve missing evidence with MCP event, window and sandbox inspection tools "
            "before recording; do not approve unseen evidence. Follow results_schema and "
            "return no overall verdict. The saved prompt describes the generation review; "
            "its bounded context-request protocol is not an MCP tool."
            if check.rules else
            "Assess the complete trajectory against the saved prompt; return its legacy "
            "valid/feedback result as specified by results_schema."
        )
    return result
