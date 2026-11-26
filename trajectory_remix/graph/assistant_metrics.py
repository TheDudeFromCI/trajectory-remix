"""Metric tools for Assistant sessions: review a trajectory's metrics and annotate them.

`view_metrics` lists every metric definition with its current observation and status, the
same view the Metrics panel shows. `set_metric` records or clears an annotation on the
session's working source, so it commits with the session's edits and Undo covers it.
Observations carry `origin="assistant_annotation"` and the source hash at the time of the
call; a later graph edit makes them stale, like any other annotation. Calculated (`source`)
and compiled (`export`) metrics are read-only here: they come from Calculate missing and
from export, never from a model's estimate. Unknown values are cleared, never zeroed.
"""

from __future__ import annotations

from copy import deepcopy

from .models import GraphSource
from .sampling import configuration
from .schema_errors import explain

ORIGIN = "assistant_annotation"
WRITABLE_SCOPE = "annotation"

VIEW_PARAMETERS = dict(type="object", properties={}, additionalProperties=False)
SET_PARAMETERS = dict(
    type="object",
    properties=dict(
        key=dict(type="string", minLength=1),
        value=dict(
            description=(
                "A choice ID for category metrics, a list of choice IDs for labels, a number,"
                " or any JSON value for free annotations. null clears the observation."
            )
        ),
    ),
    required=["key", "value"],
    additionalProperties=False,
)

TOOLS = {
    "view_metrics": (
        VIEW_PARAMETERS,
        "Read this trajectory's metrics: each definition (kind, scope, choices, target) with"
        " its recorded value, origin and status (available, stale or unavailable).",
    ),
    "set_metric": (
        SET_PARAMETERS,
        "Record an annotation metric for this trajectory, or clear it with null. Only"
        " annotation-scope metrics are writable; calculated and export metrics are read-only."
        " Set metrics after your graph edits: a later edit makes them stale.",
    ),
}


def provider_tools():
    return [
        dict(type="function", function=dict(name=name, description=text, parameters=params))
        for name, (params, text) in TOOLS.items()
    ]


def definitions(settings):
    """The metric definitions frozen with the job's settings."""
    return (settings.sampler_configuration or configuration())["definitions"]


def _status(saved, spec, source):
    if not isinstance(saved, dict) or saved.get("value") is None:
        return "unavailable"
    if spec["scope"] == "export" and not all(
        saved.get(k) for k in ("profile_fingerprint", "compiler_version")
    ):
        return "unavailable"
    stale = (
        saved.get("source_hash") != source.intrinsic_hash
        or saved.get("definition_revision") != spec["revision"]
    )
    return "stale" if stale else "available"


def view(source, settings):
    saved = source.metadata.get("metrics", {})
    targets = source.metadata.get("targets", {}).get("numeric_targets", {})
    metrics = {}
    for key, spec in definitions(settings).items():
        entry = {k: spec[k] for k in ("kind", "scope", "choices", "label", "unit") if k in spec}
        status = _status(saved.get(key), spec, source)
        entry.update(status=status, writable=spec["scope"] == WRITABLE_SCOPE)
        if status != "unavailable":
            entry.update(value=saved[key]["value"], origin=saved[key].get("origin"))
        target = targets.get(spec.get("target", key))
        if target is not None:
            entry["target"] = target
        metrics[key] = entry
    return dict(metrics=metrics)


def check_value(spec, value):
    """Raise ValueError unless value fits the definition (same rules as manual annotation)."""
    kind = spec["kind"]
    if kind == "category" and value not in spec["choices"]:
        raise ValueError(f"Unknown category; choose one of {spec['choices']}")
    if kind == "labels" and (
        not isinstance(value, list) or any(x not in spec["choices"] for x in value)
    ):
        raise ValueError(f"Expected a list of labels from {spec['choices']}")
    if kind == "number" and (
        type(value) not in (int, float) or not -float("inf") < value < float("inf")
    ):
        raise ValueError("Expected a finite number")


def set_metric(source, settings, key, value):
    defs = definitions(settings)
    if key not in defs:
        raise ValueError(f"Unknown metric; known metrics are {sorted(defs)}")
    spec = defs[key]
    if spec["scope"] != WRITABLE_SCOPE:
        raise ValueError(f"Metric {key} is {spec['scope']}-scoped and read-only")
    data = deepcopy(source.to_dict())
    metrics = data.setdefault("metadata", {}).setdefault("metrics", {})
    if value is None:
        metrics.pop(key, None)
    else:
        check_value(spec, value)
        metrics[key] = dict(
            value=value,
            origin=ORIGIN,
            source_revision=None,
            source_hash=source.intrinsic_hash,
            definition_revision=spec["revision"],
            profile_fingerprint=None,
            compiler_version=None,
        )
    return GraphSource.model_validate(data)


def apply(source, settings, name, args):
    """Run one metric tool. Returns (source, reply); raises ValueError on misuse."""
    problem = explain(TOOLS[name][0], args)
    if problem:
        raise ValueError(problem)
    if name == "set_metric":
        source = set_metric(source, settings, args["key"], args["value"])
        return source, dict(metric=view(source, settings)["metrics"][args["key"]])
    return source, view(source, settings)
