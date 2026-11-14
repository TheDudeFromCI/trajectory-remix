#!/usr/bin/env python3
"""Kidra practical-SFT scenario-definition sampler.

Pure Python 3.10+, standard library only. One call to sample_one() returns
exactly one proposed scenario definition. This does not generate the trajectory.

Usage:
    python kidra_sampler.py --matrix kidra_sampling_matrix.v1.json --seed 42
    python kidra_sampler.py --seed 137 --overrides '{"complexity":"medium","domain":"software_engineering","user_competence":"expert","user_patience":"impatient"}'
    python kidra_sampler.py --matrix kidra_sampling_matrix.v1.json --report accepted.jsonl
    python kidra_sampler.py --matrix kidra_sampling_matrix.v1.json --report proposals.jsonl --population all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import secrets
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

SAMPLER_VERSION = "1.0.0"


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_matrix(path: str | Path) -> dict[str, Any]:
    matrix = json.loads(Path(path).read_text(encoding="utf-8"))
    if matrix.get("schema_version") != "1.0.0":
        raise ValueError("Expected matrix schema_version 1.0.0")
    validate_matrix(matrix)
    return matrix


def validate_matrix(matrix: dict[str, Any]) -> None:
    """Validate schema basics and all known behavior/variant compatibility.

    This is NOT a proof of arbitrary future constraint systems. Smoke-test
    changed matrices with many seeds and inspect the resulting distributions.
    """
    required = ("competencies", "decision_axes", "dimensions", "domains", "event_tags",
                "sampler", "conditional_weights", "requirements", "metrics_schema")
    for key in required:
        if key not in matrix:
            raise ValueError(f"Matrix missing {key}")
    for family in matrix["competencies"]:
        _check_weight(family["weight"], f"competency {family['id']}")
        if not family.get("behaviors"):
            raise ValueError(f"Empty competency: {family['id']}")
        for behavior in family["behaviors"]:
            _check_weight(behavior["weight"], f"behavior {behavior['id']}")
            axis = behavior["decision_axis"]
            if axis not in matrix["decision_axes"]:
                raise ValueError(f"Behavior {behavior['id']} references missing decision axis {axis}")
            valid_variants = {v['id'] for v in matrix["decision_axes"][axis]["variants"]}
            unknown = set(behavior.get("allowed_decision_choices", valid_variants)) - valid_variants
            if unknown:
                raise ValueError(f"Behavior {behavior['id']} references invalid choices {sorted(unknown)}")
            for v in matrix["decision_axes"][axis]["variants"]:
                _check_weight(v["weight"], f"decision variant {v['id']}")
    for dim, choices in matrix["dimensions"].items():
        if not choices:
            raise ValueError(f"Dimension {dim} empty")
        for v in choices:
            _check_weight(v["weight"], f"{dim}.{v['id']}")
        ids = [v["id"] for v in choices]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate IDs in dimension {dim}")
    for tag, val in matrix["event_tags"].items():
        p = float(val['p'])
        if not math.isfinite(p) or not (0 <= p <= 1):
            raise ValueError(f"Invalid probability: {tag}")
    for r in matrix["conditional_weights"]:
        _check_weight(r["factor"], f"conditional factor for {r['target']}")
        if r["factor"] == 0:
            # Zero multipliers are supported but often create unintentionally
            # unreachable cells. Explicitly prefer eligibility constraints.
            pass
        dim, _, option = r["target"].partition(".")
        if not option or dim not in matrix["dimensions"]:
            raise ValueError(f"Unknown conditional target: {r['target']}")
        if option not in {v['id'] for v in matrix["dimensions"][dim]}:
            raise ValueError(f"Unknown conditional option: {r['target']}")
    for domain in matrix["domains"]:
        _check_weight(domain["weight"], f"domain {domain['id']}")
        if not domain.get("project_archetypes") or not domain.get("agent_roles"):
            raise ValueError(f"Missing archetypes or roles for domain {domain['id']}")


def _check_weight(value: Any, description: str) -> None:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid nonnegative finite weight in {description}: {value!r}")


def _choose(rng: random.Random, options: list[dict[str, Any]],
            description: str, context: dict[str, Any] | None = None,
            rules: list[dict[str, Any]] | None = None,
            dimension: str | None = None) -> dict[str, Any]:
    """Weighted draw, without retry/rejection. Rules alter only eligible weights."""
    eligible: list[dict[str, Any]] = []
    weights: list[float] = []
    for opt in options:
        if context is not None and not _eligible(opt, context):
            continue
        w = float(opt.get("weight", 1.0))
        if rules and dimension:
            for rule in rules:
                if rule['target'] == f"{dimension}.{opt['id']}" and _matches(rule['when'], context or {}):
                    w *= float(rule['factor'])
        if w > 0:
            eligible.append(opt)
            weights.append(w)
    if not eligible:
        raise ValueError(f"No eligible positive-weight candidates for {description}. "
                         f"Check compatibility and disabled weights; context={context}")
    # Deliberately normalize via random.choices; options' weights need not sum to 1.
    return rng.choices(eligible, weights=weights, k=1)[0]


def _matches(conditions: dict[str, list[str]], context: dict[str, Any]) -> bool:
    """All-of match on already sampled scalar values. Unknown keys do not match."""
    return all(key in context and context[key] in values
               for key, values in conditions.items())


def _eligible(option: dict[str, Any], context: dict[str, Any]) -> bool:
    if option.get('requires') and not _matches(option['requires'], context):
        return False
    if option.get('forbids') and any(
        key in context and context[key] in values
        for key, values in option['forbids'].items()
    ):
        return False
    level = context.get('complexity.level')
    if level is not None:
        if level < option.get('min_complexity', -math.inf):
            return False
        if level > option.get('max_complexity', math.inf):
            return False
    if context.get('agents.available', 0) < option.get('min_agents', 0):
        return False
    return True


def _merge_forced(*parts: dict[str, Any]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for part in parts:
        for key, val in part.get('forced_dimensions', {}).items():
            if key in merged and merged[key] != val:
                raise ValueError(f"Conflicting forced dimensions for {key}: "
                                 f"{merged[key]} versus {val}")
            merged[key] = val
    return merged


def _consistent_with_implications(matrix: dict[str, Any], context: dict[str, Any],
                                  forced: dict[str, str], key: str, value: str) -> bool:
    """Reject proposed choices that would require contradictory future facts."""
    hypothetical = {**forced, **context}
    hypothetical[key] = value
    implied = dict(forced)
    for rule in matrix.get('hard_implications', []):
        if _matches(rule['when'], hypothetical):
            for dim, required in rule['force'].items():
                if dim in implied and implied[dim] != required:
                    return False
                implied[dim] = required
    return all(dim not in hypothetical or hypothetical[dim] == val
               for dim, val in implied.items())


def _update_implications(matrix: dict[str, Any], context: dict[str, Any],
                         forced: dict[str, str]) -> None:
    for rule in matrix.get('hard_implications', []):
        if _matches(rule['when'], context):
            for dim, target in rule['force'].items():
                if dim in forced and forced[dim] != target:
                    raise ValueError(f"Conflicting hard implication: {dim}")
                if dim in context and context[dim] != target:
                    raise ValueError(f"Hard implication violates selected {dim}")
                forced[dim] = target


def _sample_dimension(rng: random.Random, matrix: dict[str, Any],
                      dim: str, context: dict[str, Any], forced: dict[str, str],
                      extras: dict[str, Any] | None = None) -> dict[str, Any]:
    options = matrix['dimensions'][dim]
    if dim in forced:
        options = [x for x in options if x['id'] == forced[dim]]
        if not options:
            raise ValueError(f"Forced unknown value {dim}={forced[dim]}")
    options = [opt for opt in options if all(
        opt.get(key) in vals if isinstance(vals, list) else opt.get(key) == vals
        for key, vals in (extras or {}).items()
    )]
    options = [opt for opt in options if _consistent_with_implications(
        matrix, context, forced, dim, opt['id'])]
    result = _choose(rng, options, dim, context, matrix['conditional_weights'], dim)
    context[dim] = result['id']
    if 'level' in result:
        context[f'{dim}.level'] = result['level']
    return result


def _viable_variants(matrix: dict[str, Any], behavior: dict[str, Any],
                     lifecycle: str, complexity: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = behavior.get('allowed_decision_choices')
    level = complexity['level']
    axis = matrix['decision_axes'][behavior['decision_axis']]
    valid = []
    for v in axis['variants']:
        if allowed is not None and v['id'] not in allowed:
            continue
        if lifecycle not in behavior['allowed_lifecycle']:
            continue
        if lifecycle not in v.get('allowed_lifecycle', behavior['allowed_lifecycle']):
            continue
        if not (behavior.get('min_complexity', 0) <= level <= behavior.get('max_complexity', 99)):
            continue
        if not (v.get('min_complexity', 0) <= level <= v.get('max_complexity', 99)):
            continue
        required_agents = max(behavior.get('min_agents', 0), v.get('min_agents', 0))
        try:
            forced = _merge_forced(behavior, v)
        except ValueError:
            continue
        if 'incident' in forced:
            incident_spec = next((x for x in matrix['dimensions']['incident']
                                  if x['id'] == forced['incident']), None)
            if incident_spec is None:
                continue
            required_agents = max(required_agents, incident_spec.get('min_agents', 0))
        if required_agents > complexity['max_agents']:
            continue
        # Entry-state force must be satisfiable for the chosen lifecycle.
        if 'entry_state' in forced and not any(
            x['id'] == forced['entry_state'] and
            _eligible(x, {'lifecycle': lifecycle, 'complexity.level': level})
            for x in matrix['dimensions']['entry_state']
        ):
            continue
        if 'dependency_pattern' in forced and not any(
            x['id'] == forced['dependency_pattern'] and
            _eligible(x, {'complexity.level': level})
            for x in matrix['dimensions']['dependency_pattern']
        ):
            continue
        valid.append(v)
    return valid


@lru_cache(maxsize=8)
def _joint_cells(canonical_matrix: str) -> tuple[tuple[int,int,int,float], ...]:
    """3-axis iterative proportional fitting with structural zero cells.

    Preserves configured marginal weights on *proposals* if feasible. The
    pair/triple interactions retain a meaningful soft prior from eligible
    behavior combinations. Accepted trajectories must still be measured.
    """
    m = json.loads(canonical_matrix)
    families = m['competencies']
    lifecycles = m['dimensions']['lifecycle']
    complexities = m['dimensions']['complexity']
    axes = (families, lifecycles, complexities)
    targets = []
    for choices in axes:
        total = sum(x['weight'] for x in choices)
        if total <= 0:
            raise ValueError('A balanced sampling axis has all zero weights')
        targets.append([x['weight'] / total for x in choices])
    cells = []
    for i,fam in enumerate(families):
        for j,life in enumerate(lifecycles):
            for k,comp in enumerate(complexities):
                b_total = sum(x['weight'] for x in fam['behaviors'])
                feasible = 0.0
                for beh in fam['behaviors']:
                    if b_total <= 0:
                        continue
                    variants = _viable_variants(m, beh, life['id'], comp)
                    all_variants = m['decision_axes'][beh['decision_axis']]['variants']
                    denom = sum(x['weight'] for x in all_variants)
                    if variants and denom > 0:
                        feasible += (beh['weight']/b_total) * (
                            sum(x['weight'] for x in variants)/denom)
                if feasible <= 0:
                    continue
                # Optional lifecycle preference factors are soft interactions.
                ctx = {'focus.family': fam['id'], 'lifecycle': life['id'],
                       'complexity': comp['id']}
                multiplier = 1.0
                for rule in m['conditional_weights']:
                    if rule['target'] == f"lifecycle.{life['id']}" and _matches(rule['when'], ctx):
                        multiplier *= rule['factor']
                prior = targets[0][i] * targets[1][j] * targets[2][k] * feasible * multiplier
                if prior > 0:
                    cells.append([i,j,k,prior])
    if not cells:
        raise ValueError('No supported joint competency/lifecycle/complexity cells')
    settings = m['sampler'].get('joint_balancing', {})
    tol = settings.get('tolerance', 1e-7)
    max_iter = settings.get('max_iterations', 1500)
    for _ in range(max_iter):
        for axis in range(3):
            observed = [0.0] * len(axes[axis])
            for cell in cells:
                observed[cell[axis]] += cell[3]
            factor = []
            for idx, target in enumerate(targets[axis]):
                if observed[idx] == 0:
                    if target > 0:
                        raise ValueError(f'Joint matrix impossible: axis {axis} option {idx} cannot be drawn')
                    factor.append(0)
                else:
                    factor.append(target/observed[idx])
            for cell in cells:
                cell[3] *= factor[cell[axis]]
        max_err = max(
            abs(sum(c[3] for c in cells if c[axis] == i) - target)
            for axis in range(3)
            for i, target in enumerate(targets[axis])
        )
        if max_err < tol:
            break
    else:
        raise ValueError(f'Joint sampler cannot meet configured marginals; best error={max_err:.5g}')
    total = sum(c[3] for c in cells)
    return tuple((i,j,k,w/total) for i,j,k,w in cells)


def _select_joint(rng: random.Random, matrix: dict[str, Any],
                  serialized_matrix: str, overrides: dict[str, str]) -> tuple[
                      dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    cells = _joint_cells(serialized_matrix)
    cells = [cell for cell in cells
             if ('focus.family' not in overrides or matrix['competencies'][cell[0]]['id'] == overrides['focus.family'])
             and ('lifecycle' not in overrides or matrix['dimensions']['lifecycle'][cell[1]]['id'] == overrides['lifecycle'])
             and ('complexity' not in overrides or matrix['dimensions']['complexity'][cell[2]]['id'] == overrides['complexity'])]
    if not cells:
        raise ValueError('Requested overrides have no compatible competency/lifecycle/complexity cell')
    i,j,k,_ = rng.choices(cells, weights=[cell[3] for cell in cells], k=1)[0]
    family = matrix['competencies'][i]
    lifecycle = matrix['dimensions']['lifecycle'][j]
    complexity = matrix['dimensions']['complexity'][k]
    weighted_behaviors = []
    for beh in family['behaviors']:
        variants = _viable_variants(matrix, beh, lifecycle['id'], complexity)
        denominator = sum(x['weight'] for x in matrix['decision_axes'][beh['decision_axis']]['variants'])
        if variants and denominator:
            weighted_behaviors.append({**beh, 'weight': beh['weight'] * (
                sum(x['weight'] for x in variants) / denominator)})
    behavior = _choose(rng, weighted_behaviors, 'behavior conditional on joint cell')
    variant = _choose(rng, _viable_variants(matrix, behavior, lifecycle['id'], complexity),
                      'decision conditional on joint cell')
    return family, behavior, variant, lifecycle, complexity


def _secondary_competencies(rng: random.Random, matrix: dict[str, Any],
                            primary_family: str, context: dict[str, Any]) -> list[dict[str, str]]:
    s = matrix['sampler']
    selected = []
    remaining = [f for f in matrix['competencies'] if f['id'] != primary_family]
    for _ in range(s['max_secondary_competencies']):
        if rng.random() >= s['secondary_competency_probability']:
            break
        candidates = []
        for f in remaining:
            choices = [b for b in f['behaviors']
                       if context['lifecycle'] in b['allowed_lifecycle'] and
                       b['min_complexity'] <= context['complexity.level'] <= b['max_complexity']]
            if choices:
                candidates.append((f, choices))
        if not candidates:
            break
        family = _choose(rng, [f for f, _ in candidates], 'secondary competency')
        behaviors = next(c for f, c in candidates if f['id'] == family['id'])
        behavior = _choose(rng, behaviors, f"secondary behavior/{family['id']}")
        selected.append({'family': family['id'], 'behavior': behavior['id'],
                         'objective': behavior['objective']})
        remaining = [f for f in remaining if f['id'] != family['id']]
    return selected


def sample_one(matrix: dict[str, Any], seed: int | None = None,
               world_group_id: str | None = None,
               overrides: dict[str, str] | None = None) -> dict[str, Any]:
    """Return EXACTLY ONE proposed scenario definition, deterministic for a seed.

    Arguments:
        matrix: loaded editable JSON matrix (read-only; never modified)
        seed: unique integer for deterministic sampling/reproduction; random if None
        world_group_id: externally assigned family/group key for counterfactuals;
                        share one key across variants to protect evaluation splits
        overrides: optional selected values for domain, lifecycle, complexity,
                   primary family, or user attributes. Overrides deliberately
                   change the sampling distribution; they are logged.

    The returned 'metrics.sampling' records INTENDED coverage only. Downstream
    generation/validation must attach realized observations and set status='accepted'.
    """
    overrides = dict(overrides or {})
    allowed_overrides = {
        'focus.family','lifecycle','complexity','domain',
        'user_competence','user_patience','user_involvement','user_tone'
    }
    unknown_overrides = set(overrides) - allowed_overrides
    if unknown_overrides:
        raise ValueError(f'Unsupported override keys: {sorted(unknown_overrides)}')
    for key,value in overrides.items():
        if not isinstance(value,str):
            raise TypeError(f'Override {key} must be a string ID')
        if key == 'focus.family':
            options = matrix['competencies']
        elif key == 'domain':
            options = matrix['domains']
        else:
            options = matrix['dimensions'][key]
        if value not in {opt['id'] for opt in options}:
            raise ValueError(f'Unknown override {key}={value}')
    if seed is None:
        seed = secrets.randbits(matrix['sampler'].get('default_seed_bits', 64))
    if not isinstance(seed, int):
        raise TypeError('seed must be an integer')
    rng = random.Random(seed)
    serialized_matrix = canonical_json(matrix)
    matrix_hash = hashlib.sha256(serialized_matrix.encode('utf-8')).hexdigest()
    family, behavior, variant, lifecycle_chosen, complexity_chosen = _select_joint(
        rng, matrix, serialized_matrix, overrides)
    forced = _merge_forced(behavior, variant)
    for k,v in overrides.items():
        if k in ('focus.family','lifecycle','complexity','domain'):
            continue
        if k in forced and forced[k] != v:
            raise ValueError(f'Override {k} conflicts with behavior constraint')
        forced[k] = v
    min_level = max(behavior.get('min_complexity', 0), variant.get('min_complexity', 0))
    max_level = min(behavior.get('max_complexity', 99), variant.get('max_complexity', 99))
    min_agents = max(behavior.get('min_agents', 0), variant.get('min_agents', 0))
    if 'incident' in forced:
        incident_spec = next((x for x in matrix['dimensions']['incident']
                              if x['id'] == forced['incident']), None)
        if incident_spec is None:
            raise ValueError(f"Unknown forced incident {forced['incident']}")
        min_agents = max(min_agents, incident_spec.get('min_agents', 0))
    lives = set(behavior['allowed_lifecycle']) & set(variant.get('allowed_lifecycle',
                                                                  behavior['allowed_lifecycle']))
    context: dict[str, Any] = {
        'focus.family': family['id'], 'focus.behavior': behavior['id'],
        'decision.axis': behavior['decision_axis'], 'decision.choice': variant['id']
    }
    order = matrix['sampler']['selection_order']
    selections: dict[str, dict[str, Any]] = {
        'lifecycle': lifecycle_chosen, 'complexity': complexity_chosen
    }
    context['lifecycle'] = lifecycle_chosen['id']
    context['complexity'] = complexity_chosen['id']
    context['complexity.level'] = complexity_chosen['level']
    context['complexity.max_agents'] = complexity_chosen['max_agents']
    _update_implications(matrix, context, forced)
    domain = None
    for key in order:
        if key in ('focus', 'decision', 'lifecycle', 'complexity', 'event_tags',
                   'secondary_competencies', 'numeric_targets'):
            continue
        if key == 'domain':
            domain_options = matrix['domains']
            if 'domain' in overrides:
                domain_options = [x for x in domain_options if x['id'] == overrides['domain']]
            domain = _choose(rng, domain_options, 'domain')
            context['domain'] = domain['id']
            continue
        if key == 'team_posture':
            opts = [x for x in matrix['dimensions'][key]
                    if x['agent_range'][1] >= min_agents and x['agent_range'][0] <= context['complexity.max_agents']
                    and _consistent_with_implications(matrix, context, forced, key, x['id'])]
            if key in forced:
                opts = [x for x in opts if x['id'] == forced[key]]
            selections[key] = _choose(rng, opts, key, context, matrix['conditional_weights'], key)
            context[key] = selections[key]['id']
            low, high = selections[key]['agent_range']
            context['agents.available'] = rng.randint(max(min_agents, low),
                                                    min(high, context['complexity.max_agents']))
        elif key in matrix['dimensions']:
            exclusions = set(variant.get('forbidden_entry_states', [])) if key == 'entry_state' else set()
            if exclusions:
                selected_options = [o for o in matrix['dimensions'][key] if o['id'] not in exclusions]
                # Restrict only this dimension, not the caller's matrix.
                local_matrix = {**matrix, 'dimensions': {**matrix['dimensions'], key: selected_options}}
            else:
                local_matrix = matrix
            selections[key] = _sample_dimension(rng, local_matrix, key, context, forced)
        else:
            raise ValueError(f"Unknown sampling dimension in selection_order: {key}")
        _update_implications(matrix, context, forced)
    assert domain is not None
    unknown_forced = set(forced) - set(selections)
    if unknown_forced:
        raise ValueError(f"Forced dimensions not selected: {sorted(unknown_forced)}")
    required_tags = list(dict.fromkeys(behavior.get('required_event_tags', []) +
                                      variant.get('required_event_tags', [])))
    forbidden_tags = set(behavior.get('forbidden_event_tags', []) +
                         variant.get('forbidden_event_tags', []))
    if forbidden_tags & set(required_tags):
        raise ValueError('Required and forbidden event tags overlap')
    event_tags = list(required_tags)
    for tag in required_tags:
        if tag not in matrix['event_tags'] or not _eligible(matrix['event_tags'][tag], context):
            raise ValueError(f"Required event tag is incompatible: {tag}, context={context}")
    extra_candidates = []
    for tag, descriptor in matrix['event_tags'].items():
        if tag in required_tags or tag in forbidden_tags or not _eligible(descriptor, context):
            continue
        if rng.random() < descriptor['p']:
            extra_candidates.append(tag)
    if len(extra_candidates) > matrix['sampler']['max_extra_event_tags']:
        extra_candidates = rng.sample(extra_candidates,
                                      matrix['sampler']['max_extra_event_tags'])
    event_tags += sorted(extra_candidates)
    secondary = _secondary_competencies(rng, matrix, family['id'], context)
    comp = selections['complexity']
    task_count = rng.randint(*comp['task_count'])
    pattern = context['dependency_pattern']
    depth_lo, depth_hi = comp['dependency_depth']
    if pattern == 'independent':
        depth = 0
    else:
        depth_lo = max(depth_lo, 2 if pattern in ('branch_merge','critical_path_with_sidework') else 1)
        depth = rng.randint(depth_lo, min(depth_hi, task_count - 1))
    window_lo, window_hi = comp['window_budget']
    window_lo = max(window_lo, selections['execution_pattern'].get('min_windows', 1))
    if context['agents.available'] >= 2 and pattern != 'chain':
        window_lo = max(window_lo, 2)
    if window_lo > window_hi:
        raise ValueError('Window capacity inconsistent with execution pattern')
    target_windows = rng.randint(window_lo, window_hi)
    tool_min, tool_max = comp['expected_tool_calls']
    if context['tool_access'] == 'offline':
        expected_tool_calls = 0
    else:
        expected_tool_calls = rng.randint(tool_min, tool_max)
        if context['observation_source'] in ('tool_calls', 'mixed_sources') or 'tool_call_failure' in event_tags:
            expected_tool_calls = max(1, expected_tool_calls)
    archetype = rng.choice(domain['project_archetypes'])
    agents = []
    roles = list(domain['agent_roles'])
    rng.shuffle(roles)
    for i in range(context['agents.available']):
        agents.append({'slot': f"agent_{i+1}", 'specialty': roles[i % len(roles)],
                       'capability_expectation': context['agent_reliability']})
    numerical = {
        'suggested_coordination_events': rng.randint(*selections['episode_horizon']['coordination_events']),
        'suggested_task_count': task_count,
        'suggested_dependency_depth': depth,
        'available_agent_count': context['agents.available'],
        'target_active_window_capacity': target_windows,
        'suggested_tool_observations_or_calls': expected_tool_calls,
    }
    sampling_metrics = {
        'focus.family': family['id'], 'focus.behavior': behavior['id'],
        'decision.axis': behavior['decision_axis'], 'decision.choice': variant['id'],
        'domain': domain['id'], **{name: opt['id'] for name, opt in selections.items()}
    }
    group = world_group_id if world_group_id is not None else f"provisional-world-{seed}"
    if not isinstance(group, str) or not group.strip():
        raise ValueError('world_group_id must be a nonempty string')
    definition_id = 'kd-' + hashlib.sha256(
        f"{matrix_hash}:{seed}:{group}".encode('utf-8')).hexdigest()[:20]
    scenario = {
        'definition_id': definition_id,
        'schema_version': '1.0.0',
        'status': 'proposed',
        'sampling_provenance': {'matrix_id': matrix['matrix_id'], 'matrix_sha256': matrix_hash,
                                'sampler_version': SAMPLER_VERSION, 'seed': seed,
                                'world_group_id': group, 'dataset_split': None,
                                'requested_overrides': overrides},
        'learning_objective': {'primary_family': family['id'], 'primary_behavior': behavior['id'],
                               'description': behavior['objective'], 'secondary': secondary,
                               'decision_axis': behavior['decision_axis'],
                               'target_decision': variant['id'],
                               'condition_making_decision_valid': variant['scenario_condition'],
                               'correct_coordination_behavior': variant['target_behavior']},
        'scenario': {
            'domain': {'id': domain['id'], 'description': domain['description'],
                       'suggested_project': archetype},
            'project_state': {'lifecycle': context['lifecycle'],
                              'entry_state': context['entry_state'],
                              'complexity': context['complexity'],
                              'recurrence': context['recurrence'],
                              'dependency_pattern': context['dependency_pattern'],
                              'information_state': context['information_state'],
                              'incident': context['incident'],
                              'episode_goal': context['episode_goal'],
                              'decision_difficulty': context['decision_difficulty'],
                              'stakes': context['stakes'],
                              'project_timescale': context['project_timescale'],
                              'phase_transition': context['phase_transition']},
            'resources': {'posture': context['resource_posture'],
                          'tool_access': context['tool_access'],
                          'agent_reliability': context['agent_reliability'],
                          'agents': agents},
            'user': {k.removeprefix('user_'): context[k]
                     for k in ('user_competence','user_patience','user_involvement','user_tone')},
            'execution': {'pattern': context['execution_pattern'],
                          'observation_source': context['observation_source'],
                          'window_role_profile': context['window_role_profile'],
                          'roles_to_exercise': selections['window_role_profile']['roles'],
                          'episode_horizon': context['episode_horizon'],
                          **numerical},
            'extension_dimensions': {name: {'id': opt['id'], 'description': opt.get('description', '')}
                                     for name, opt in selections.items()
                                     if name not in {'lifecycle','complexity','entry_state','recurrence',
                                         'resource_posture','dependency_pattern','information_state',
                                         'tool_access','agent_reliability','user_competence','user_patience',
                                         'user_involvement','user_tone','team_posture','incident',
                                         'episode_goal','execution_pattern','decision_difficulty','stakes',
                                         'project_timescale','observation_source','window_role_profile',
                                         'phase_transition','episode_horizon'}},
            'additional_event_tags': [{'id': tag, 'description': matrix['event_tags'][tag]['description']}
                                      for tag in event_tags],
        },
        'generation_directive': {
            'task': 'Create a concrete project world and exactly one training trajectory for the specified decision.',
            'requirements': [
                'Use the selected scenario attributes and add realistic, consistent details (goal, constraints, budgets, tools, acceptance criteria).',
                'Make the condition making the target decision correct actually true in the scenario; avoid contradictory extra facts.',
                'Demonstrate the specified coordination behavior using evidence that Kidra could observe at each decision.',
                'Kidra coordinates specialists rather than doing substantial specialist work itself; avoid overdelegating trivial decisions.',
                'Show real tool-call/result and agent-call/result events for any execution; do not narrate imagined external actions as completed.',
                'Preserve task IDs, ownership, dependencies, window lifetimes, asynchronous ordering and truthful completion status.',
                'The episode goal need not mean total project completion; do not claim it without observed acceptance evidence.',
                'Prefer a coherent purposeful trajectory over forcing every secondary label into the episode.',
                'Provide observable validation evidence and explicit uncertainty; never use hidden future outcomes as model knowledge.'
            ]},
        'metrics': {'sampling': sampling_metrics,
                    'multi_labels': {'event_tags': event_tags,
                                     'secondary_competencies': [x['family'] for x in secondary],
                                     'secondary_behaviors': [x['behavior'] for x in secondary]},
                    'numeric_targets': numerical,
                    'actual': {}},
    }
    return scenario


def metrics_report(records: Iterable[dict[str, Any]], matrix: dict[str, Any],
                   population: str = 'accepted') -> dict[str, Any]:
    """Summarize definitions from saved JSONL; default counts ONLY explicit accepted.

    These are proposed/sampled attributes, not realized trajectory attributes.
    Keep post-generation actuals separately. Percentage denominators are records,
    and multi-label frequencies need not sum to 100%.
    """
    if population not in ('accepted', 'all'):
        raise ValueError('population must be accepted or all')
    marginals: dict[str, Counter[str]] = defaultdict(Counter)
    cross_tabs: dict[str, Counter[str]] = defaultdict(Counter)
    multi: dict[str, Counter[str]] = defaultdict(Counter)
    statuses: Counter[str] = Counter()
    actual_sum: Counter[str] = Counter()
    actual_observed: Counter[str] = Counter()
    numeric_target_values: dict[str, list[float]] = defaultdict(list)
    matrix_versions: Counter[str] = Counter()
    used = 0
    for rec in records:
        statuses[rec.get('status', 'unknown')] += 1
        if population == 'accepted' and rec.get('status') != 'accepted':
            continue
        used += 1
        matrix_versions[rec.get('sampling_provenance',{}).get('matrix_sha256','unknown')] += 1
        for field, value in rec['metrics'].get('numeric_targets',{}).items():
            if isinstance(value,(int,float)) and not isinstance(value,bool):
                numeric_target_values[field].append(float(value))
        target = rec['metrics']['sampling']
        # Unknown/new dimensions are automatically visible in the coverage report.
        for key, val in target.items():
            marginals[key][str(val)] += 1
        for dim in matrix['metrics_schema']['tracked_multi_label']:
            for label in set(rec['metrics']['multi_labels'].get(dim, [])):
                multi[dim][label] += 1
        for left, right in matrix['metrics_schema']['cross_tabs']:
            if left in target and right in target:
                cross_tabs[f'{left} × {right}'][f"{target[left]} | {target[right]}"] += 1
        for field, value in rec['metrics'].get('actual', {}).items():
            if isinstance(value, (int,float)) and not isinstance(value, bool):
                actual_sum[field] += value
                actual_observed[field] += 1
    def pack_count(counter: Counter[str]) -> dict[str, dict[str, float | int]]:
        return {key: {'count': value, 'share_of_records': round(value / used, 6) if used else 0.0}
                for key, value in counter.most_common()}
    target_lists = {
        'focus.family': matrix['competencies'],
        'lifecycle': matrix['dimensions']['lifecycle'],
        'complexity': matrix['dimensions']['complexity'],
        'domain': matrix['domains'],
    }
    comparisons = {}
    for dim, opts in target_lists.items():
        denominator = sum(x['weight'] for x in opts)
        if not denominator:
            continue
        comparison = {}
        for item in opts:
            desired = item['weight']/denominator
            observed = marginals[dim][item['id']]/used if used else 0.0
            comparison[item['id']] = {
                'configured_target_share': round(desired, 6),
                'observed_share': round(observed, 6),
                'delta_percentage_points': round(100*(observed-desired), 3),
                'configured_target_count_at_current_n': round(desired * used, 2),
            }
        comparisons[dim] = comparison
    numeric_summary = {}
    for field, values in numeric_target_values.items():
        values.sort()
        n = len(values)
        numeric_summary[field] = {
            'count': n, 'min': values[0], 'max': values[-1],
            'mean': round(sum(values)/n, 3),
            'median': values[(n-1)//2], 'p90': values[int(0.9*(n-1))],
        }
    known_behaviors = {b['id'] for family in matrix['competencies'] for b in family['behaviors']}
    covered_behaviors = set(marginals['focus.behavior'])
    return {
        'population': population, 'included_records': used,
        'matrix_fingerprints_in_population': dict(matrix_versions),
        'mixed_matrix_versions_warning': len(matrix_versions)>1,
        'configured_target_comparison': comparisons,
        'behavior_coverage': {'covered': len(covered_behaviors & known_behaviors),
                              'defined': len(known_behaviors),
                              'uncovered': sorted(known_behaviors - covered_behaviors)},
        'numeric_target_summary': numeric_summary,
        'status_counts_over_all_input': dict(statuses),
        'marginals': {key: pack_count(c) for key,c in sorted(marginals.items())},
        'multi_labels': {key: pack_count(c) for key,c in sorted(multi.items())},
        'cross_tabs': {key: pack_count(c) for key,c in sorted(cross_tabs.items())},
        'numeric_actual_observations': {
            field: {'sum': value, 'reported_records': actual_observed[field],
                    'mean_of_reported': value / actual_observed[field]}
            for field, value in actual_sum.items()},
        'note': 'Counts refer to sampled intentions; actual execution/tokens must be measured by generator and validator.'
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--matrix', default=str(Path(__file__).with_name('kidra_sampling_matrix.v1.json')))
    p.add_argument('--seed', type=int, help='RNG seed, unique per proposed definition')
    p.add_argument('--world-group-id', help='Shared ID for counterfactual siblings and split grouping')
    p.add_argument('--overrides', help='JSON object of compatible targeted selectors, e.g. {\"complexity\":\"medium\"}')
    p.add_argument('--report', help='Read previously saved proposal JSONL and report aggregate coverage')
    p.add_argument('--population', choices=('accepted', 'all'), default='accepted')
    p.add_argument('--output', help='Write JSON result to this file instead of stdout')
    args = p.parse_args()
    matrix = load_matrix(args.matrix)
    if args.report:
        with open(args.report, 'r', encoding='utf-8') as f:
            records = (json.loads(line) for line in f if line.strip())
            obj = metrics_report(records, matrix, population=args.population)
    else:
        overrides = json.loads(args.overrides) if args.overrides else None
        obj = sample_one(matrix, seed=args.seed, world_group_id=args.world_group_id,
                         overrides=overrides)
    payload = json.dumps(obj, indent=2, ensure_ascii=False) + '\n'
    if args.output:
        Path(args.output).write_text(payload, encoding='utf-8')
    else:
        print(payload, end='')


if __name__ == '__main__':
    main()
