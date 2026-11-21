"""Evidence contracts and freshness for native authoring signoffs."""

from datetime import UTC, datetime

from .authoring_tools import Signoff
from .diagnostic_policy import identity
from .models import GraphSource, digest
from .schema_errors import explain
from .validation import diagnose

VERSION = "native-authoring/1"


def criteria(checklist, draft_visible):
    required = {
        "completeness": dict(
            text="The trajectory shows the whole episode, including its final state.",
            allow_not_applicable=False,
        ),
    }
    if draft_visible:
        required["draft_fidelity"] = dict(
            text="The authored episode preserves the given Draft's material facts and outcomes.",
            allow_not_applicable=False,
        )
    return required | checklist


def parameters(checks):
    evidence = dict(
        evidence=dict(type="string", minLength=1),
        event_ids=dict(type="array", items=dict(type="string", minLength=1), uniqueItems=True),
    )
    items = {
        key: dict(
            type="object",
            properties=dict(
                status=dict(
                    type="string",
                    enum=["pass", "fail"]
                    + (["not_applicable"] if item["allow_not_applicable"] else []),
                ),
                **evidence,
            ),
            required=["status", "evidence", "event_ids"],
            additionalProperties=False,
        )
        for key, item in checks.items()
    }
    return dict(
        type="object",
        properties=dict(
            status=dict(type="string", enum=["complete", "unfeasible"]),
            summary=dict(type="string", minLength=1),
            items=dict(type="object", properties=items, additionalProperties=False),
        ),
        required=["status", "summary"],
        additionalProperties=False,
    )


def validate(source, settings, checks, arguments):
    problem = explain(parameters(checks), arguments)
    if problem:
        raise ValueError(problem)
    signoff = Signoff.model_validate(arguments)
    if not signoff.summary.strip():
        raise ValueError("Provide a nonempty signoff summary")
    if signoff.status == "unfeasible":
        return "unfeasible", signoff.model_dump(mode="json", exclude={"status"})
    if signoff.items.keys() != checks.keys():
        raise ValueError(
            "Provide evidence for every criterion: "
            + ", ".join(sorted(checks.keys() - signoff.items.keys()))
        )
    for key, item in signoff.items.items():
        if not item.evidence.strip() or set(item.event_ids) - source.events.keys():
            raise ValueError(f"{key} needs nonempty evidence and valid event IDs")
        if item.status == "pass" and not item.event_ids:
            raise ValueError(f"{key} needs event IDs supporting its passing evidence")
    verdict = "fail" if any(item.status == "fail" for item in signoff.items.values()) else "pass"
    if verdict == "pass" and diagnose(source, settings):
        raise ValueError("Resolve every reported error and enabled warning before signing off")
    return verdict, signoff.model_dump(mode="json", exclude={"status"})


def stamp(source, settings, *, draft_visible, binds_review_option=False):
    return dict(
        diagnostic_policy=identity(settings),
        draft_hash=digest(source.metadata.get("draft")) if draft_visible else None,
        review_include_draft=settings.generation.review_include_draft
        if binds_review_option
        else None,
    )


def stale_reasons(source, settings, entry):
    reasons = []
    if entry.get("source_hash") != source.intrinsic_hash:
        reasons.append("source_changed")
    if entry.get("diagnostic_policy") is not None and entry["diagnostic_policy"] != identity(
        settings
    ):
        reasons.append("validation_settings_changed")
    if entry.get("draft_hash") is not None and entry["draft_hash"] != digest(
        source.metadata.get("draft")
    ):
        reasons.append("draft_changed")
    if (
        entry.get("review_include_draft") is not None
        and entry["review_include_draft"] != settings.generation.review_include_draft
    ):
        reasons.append("draft_visibility_changed")
    return reasons


def record(source, settings, body, *, operation, verdict, draft_visible, provenance=None):
    data = source.to_dict()
    metadata = data.setdefault("metadata", {})
    entry = dict(
        version=VERSION,
        timestamp=datetime.now(UTC).isoformat(),
        operation=operation,
        verdict=verdict,
        source_hash=source.intrinsic_hash,
        **stamp(source, settings, draft_visible=draft_visible),
        **body,
        **(provenance or {}),
    )
    log = metadata.get("authoring_signoffs")
    metadata["authoring_signoffs"] = (log if isinstance(log, list) else []) + [entry]
    return GraphSource.model_validate(data)
