"""Evidence contracts and freshness for native authoring signoffs."""

from datetime import UTC, datetime

from .authoring_tools import Signoff
from .compiler_schedule import EventSchedule, is_model_event
from .diagnostic_policy import identity
from .flow import causal_view
from .models import GraphSource, digest
from .schema_errors import explain
from .validation import diagnose
from .window_evidence import evidence_blocker, invalidators

VERSION = "native-authoring/1"
CAUSAL_EVIDENCE = "core.causal_evidence"


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


def parameters(checks, *, require_causal_claims=True):
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
    if require_causal_claims and CAUSAL_EVIDENCE in items:
        items[CAUSAL_EVIDENCE]["properties"]["causal_claims"] = dict(
            type="array",
            description=(
                "Required for a pass. Cite the agent event that uses the evidence and the source "
                "events visible at that point. Include every material use of prior evidence. "
                "Use an empty list only if the episode has no such uses."
            ),
            items=dict(
                type="object",
                properties=dict(
                    consumer_event_id=dict(type="string", minLength=1),
                    evidence_event_ids=dict(
                        type="array",
                        items=dict(type="string", minLength=1),
                        minItems=1,
                        uniqueItems=True,
                    ),
                ),
                required=["consumer_event_id", "evidence_event_ids"],
                additionalProperties=False,
            ),
        )
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


def validate(source, settings, checks, arguments, *, require_causal_claims=True):
    problem = explain(parameters(checks, require_causal_claims=require_causal_claims), arguments)
    if problem:
        raise ValueError(problem)
    signoff = Signoff.model_validate(arguments)
    if not signoff.summary.strip():
        raise ValueError("Provide a nonempty signoff summary")
    if signoff.status == "unfeasible":
        return "unfeasible", signoff.model_dump(mode="json", exclude={"status"}, exclude_none=True)
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
    causal = signoff.items.get(CAUSAL_EVIDENCE)
    if causal and causal.status == "pass" and require_causal_claims:
        if causal.causal_claims is None:
            raise ValueError(f"{CAUSAL_EVIDENCE} needs causal_claims for a pass")
        _validate_causal_claims(source, causal.causal_claims)
    verdict = "fail" if any(item.status == "fail" for item in signoff.items.values()) else "pass"
    if verdict == "pass" and diagnose(source, settings):
        raise ValueError("Resolve every reported error and enabled warning before signing off")
    return verdict, signoff.model_dump(mode="json", exclude={"status"}, exclude_none=True)


def _validate_causal_claims(source, claims):
    if not claims:
        return
    ordered, ancestors, cyclic = EventSchedule(source).causal_order()
    if cyclic:
        raise ValueError("Resolve dependency cycles before passing causal evidence")
    cleared = invalidators(source, ordered)
    visible_by_consumer = {}
    for claim in claims:
        consumer_id = claim.consumer_event_id
        consumer = source.events.get(consumer_id)
        if consumer is None or not is_model_event(consumer):
            raise ValueError(f"Causal evidence consumer {consumer_id} must be an agent event")
        if consumer_id not in visible_by_consumer:
            try:
                view = causal_view(source, consumer.after)
            except (ValueError, KeyError) as error:
                raise ValueError(f"Cannot check the causal view for {consumer_id}") from error
            visible_by_consumer[consumer_id] = {
                item["id"] for window in view.values() for item in window["events"]
            }
        for evidence_id in claim.evidence_event_ids:
            if evidence_id not in source.events:
                raise ValueError(f"Unknown evidence event {evidence_id} for {consumer_id}")
            blocker = evidence_blocker(
                source,
                ancestors,
                cleared,
                evidence_id,
                consumer_id,
                check_race=True,
            )
            if blocker:
                ident, reason = blocker
                raise ValueError(
                    f"Evidence {evidence_id} is unavailable or not guaranteed for {consumer_id}: {ident} {reason}"
                )
            if evidence_id not in visible_by_consumer[consumer_id]:
                raise ValueError(f"Evidence {evidence_id} is not visible to {consumer_id}")


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
