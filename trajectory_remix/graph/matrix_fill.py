"""Sample a proposal from the matrix around a trajectory's assigned metrics.

The trajectory's category and label metrics are the settings to keep: each assigned value is a
pin (matrix_pins), and pins the matrix's rules rule out raise IncompatiblePins. Unset metrics
take the sampled values, recorded with origin ``matrix_sample``.
"""

from copy import deepcopy

from . import matrix_pins
from .models import digest
from .sampling import SamplerService

ORIGIN = "matrix_sample"


GROUPS = {"category": "sampling", "labels": "multi_labels", "number": "numeric_targets"}


def _sampled(proposal, key, spec):
    return proposal["metrics"][GROUPS[spec["kind"]]].get(key)


def matrix_keys(defs):
    """Category, label and numeric-target metrics the sampler produces a value for."""
    return {
        k: s
        for k, s in defs.items()
        if s["scope"] == "annotation" and (s.get("choices") or s.get("sampled"))
    }


def assigned(source, defs):
    """The trajectory's recorded values that still fit their metric: these are the pins."""
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
        elif spec["kind"] == "number" and type(value) is int and value >= 0:
            result[key] = value
    return result


def sample(sampler, source, defs, seed=None):
    """A proposal snapshot (not yet stored) that keeps every assigned value."""
    snapshot = matrix_pins.sample(sampler, assigned(source, defs), seed)
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
