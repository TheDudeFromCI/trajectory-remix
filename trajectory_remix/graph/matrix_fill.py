"""Sample a proposal from the matrix around a trajectory's assigned metrics.

The trajectory's category and label metrics are the settings to keep. Assigned values the
supplied sampler can pin (sampling.OVERRIDES) are pinned; among a few candidate samples the
one agreeing with the most other assigned values is chosen; any assigned value still
differing is written into the proposal, with a requirement telling the Draft to honor it.
Unset metrics take the sampled values, recorded with origin ``matrix_sample``.
"""

import secrets
from copy import deepcopy

from .models import digest
from .sampling import OVERRIDES, SamplerService

ORIGIN = "matrix_sample"
CANDIDATES = 32

# Where a sampled category also appears in the proposal's scenario and objective.
SCENARIO_PATHS = {
    "focus.family": ("learning_objective", "primary_family"),
    "focus.behavior": ("learning_objective", "primary_behavior"),
    "decision.axis": ("learning_objective", "decision_axis"),
    "decision.choice": ("learning_objective", "target_decision"),
    "domain": ("scenario", "domain", "id"),
    "resource_posture": ("scenario", "resources", "posture"),
    "tool_access": ("scenario", "resources", "tool_access"),
    "agent_reliability": ("scenario", "resources", "agent_reliability"),
    "user_competence": ("scenario", "user", "competence"),
    "user_patience": ("scenario", "user", "patience"),
    "user_involvement": ("scenario", "user", "involvement"),
    "user_tone": ("scenario", "user", "tone"),
    "execution_pattern": ("scenario", "execution", "pattern"),
    "observation_source": ("scenario", "execution", "observation_source"),
    "window_role_profile": ("scenario", "execution", "window_role_profile"),
    "episode_horizon": ("scenario", "execution", "episode_horizon"),
}


def _sampled(proposal, key, spec):
    group = "sampling" if spec["kind"] == "category" else "multi_labels"
    return proposal["metrics"][group].get(key)


def matrix_keys(defs):
    """Category and label metrics the sampler produces a value for."""
    return {k: s for k, s in defs.items() if s["scope"] == "annotation" and s.get("choices")}


def assigned(source, defs):
    """The trajectory's recorded category and label values that are still valid choices."""
    saved = source.metadata.get("metrics", {})
    result = {}
    for key, spec in matrix_keys(defs).items():
        value = saved.get(key, {}).get("value") if isinstance(saved.get(key), dict) else None
        if spec["kind"] == "category" and value in spec["choices"]:
            result[key] = value
        elif (
            spec["kind"] == "labels"
            and isinstance(value, list)
            and all(x in spec["choices"] for x in value)
        ):
            result[key] = list(value)
    return result


def _agreement(proposal, values, defs):
    return sum(_sampled(proposal, k, defs[k]) == v for k, v in values.items())


def _set_path(proposal, path, value):
    node = proposal
    for part in path[:-1]:
        node = node.get(part)
        if not isinstance(node, dict):
            return
    node[path[-1]] = value


def honor(proposal, values, defs):
    """Write assigned values the sample differs on into the proposal. Returns the keys."""
    changed = []
    for key, value in values.items():
        if _sampled(proposal, key, defs[key]) == value:
            continue
        group = "sampling" if defs[key]["kind"] == "category" else "multi_labels"
        proposal["metrics"][group][key] = deepcopy(value)
        if key in SCENARIO_PATHS:
            _set_path(proposal, SCENARIO_PATHS[key], value)
        elif key in proposal.get("scenario", {}).get("project_state", {}):
            proposal["scenario"]["project_state"][key] = value
        changed.append(key)
    if changed:
        shown = "; ".join(f"{k} = {values[k]}" for k in changed)
        proposal["generation_directive"]["requirements"].append(
            "These settings were assigned on the trajectory and replace the sampled choices;"
            f" follow them over any conflicting detail: {shown}."
        )
        proposal["sampling_provenance"]["assigned_overrides"] = {k: values[k] for k in changed}
    return changed


def sample(sampler, source, defs, seed=None):
    """A proposal snapshot (not yet stored) that keeps every assigned value."""
    values = assigned(source, defs)
    pins = {k: v for k, v in values.items() if k in OVERRIDES}
    rest = {k: v for k, v in values.items() if k not in pins}
    if seed is not None and type(seed) is not int:
        raise ValueError("seed must be an integer")
    seeds = [
        seed + i if seed is not None else secrets.randbits(62)
        for i in range(CANDIDATES if rest else 1)
    ]
    best = None
    for candidate in seeds:
        snapshot = sampler.propose(seed=candidate, overrides=pins or None, persist=False)
        score = _agreement(snapshot["proposal"], rest, defs)
        if best is None or score > best[0]:
            best = (score, snapshot)
        if score == len(rest):
            break
    snapshot = best[1]
    honor(snapshot["proposal"], values, defs)
    snapshot["payload_digest"] = digest(snapshot["proposal"])
    return snapshot


def fill(data, proposal, defs, intrinsic_revision, intrinsic_hash):
    """Record the proposal's values for every unset matrix metric. Returns the keys filled."""
    saved = data.setdefault("metadata", {}).setdefault("metrics", {})
    current = assigned(_Meta(data["metadata"]), defs)
    filled = []
    for key, spec in matrix_keys(defs).items():
        if key in current:
            continue
        value = _sampled(proposal, key, spec)
        # An empty label list is a sampled value too ("none of these"), so it is recorded.
        if value is None or (spec["kind"] == "category" and value not in spec["choices"]):
            continue
        saved[key] = dict(
            value=deepcopy(value),
            origin=ORIGIN,
            source_revision=intrinsic_revision,
            source_hash=intrinsic_hash,
            definition_revision=spec["revision"],
            profile_fingerprint=None,
            compiler_version=None,
        )
        filled.append(key)
    return filled


def attach(data, snapshot):
    """Make the snapshot the trajectory's proposal and targets, as a generation run does."""
    data.setdefault("metadata", {}).update(SamplerService.metadata(snapshot))


class _Meta:
    """The part of a source `assigned` reads, for a source still held as a dict."""

    def __init__(self, metadata):
        self.metadata = metadata
