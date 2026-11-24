"""Review checklists, signoff records, stage transitions and staleness. No provider calls.

A review is one native tool session per tier: the reviewer checks the trajectory against the
checklist, repairs what fails and signs off. Passing moves the stage up; an unfeasible signoff
records a failed review.
"""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field

from . import signoffs
from .models import DTO, GraphSource, JsonValue, digest


class ReviewInput(DTO):
    query: dict[str, JsonValue] = Field(default_factory=dict)
    mode: Literal["standard", "force_fast", "force_critical"] = "standard"
    trajectory: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)


def checklist(settings, tier):
    groups = ["core"] if tier == "fast" else ["core", "critical"]
    return {
        f"{group}.{key}": item.model_dump(mode="json")
        for group in groups
        for key, item in settings.review_checklists.get(group, {}).items()
    }


def identities(settings):
    return {
        g: digest(
            {k: v.model_dump(mode="json") for k, v in settings.review_checklists.get(g, {}).items()}
        )
        for g in ("core", "critical")
    }


def require_checklist(settings, tier):
    if not checklist(settings, tier):
        raise ValueError("Review requires at least one checklist criterion")


def signoff(
    source,
    record,
    settings,
    tier,
    body,
    *,
    result,
    endpoint,
    model,
    requested_model,
    provenance,
    prompt_version,
):
    """Record a review's signoff and move the stage: up on a pass, to the tier's failure."""
    old = source.metadata.get("stage", -3)
    target = (1 if tier == "fast" else 2) if result == "pass" else (-1 if tier == "fast" else -2)
    stage = max(old, target) if result == "pass" else target
    data = source.to_dict()
    metadata = data.setdefault("metadata", {})
    metadata["stage"] = stage
    metadata.setdefault("evaluations", []).append(
        dict(
            id=str(uuid4()),
            timestamp=datetime.now(UTC).isoformat(),
            tier=tier,
            operation="review",
            endpoint=endpoint,
            model=model,
            requested_model=requested_model,
            prompt_version=prompt_version,
            source_hash=source.intrinsic_hash,
            source_revision=record.revision + (source.intrinsic_hash != record.intrinsic_hash),
            intrinsic_revision=record.intrinsic_revision
            + (source.intrinsic_hash != record.intrinsic_hash),
            checklists=identities(settings),
            checklist_hash=digest(checklist(settings, tier)),
            checklist=checklist(settings, tier),
            verdict=result,
            **deepcopy(body),
            **provenance,
            transition={"before": old, "after": stage},
        )
    )
    return GraphSource.model_validate(data), result


def stale(source, settings):
    stage = source.metadata.get("stage", -3)
    tier = "critical" if type(stage) is int and stage in {-2, 2, 3} else "fast"
    entries = source.metadata.get("evaluations")
    reviews = (
        [r for r in entries if isinstance(r, dict) and r.get("tier") == tier]
        if isinstance(entries, list)
        else []
    )
    if not reviews and isinstance(entries, list):
        reviews = [
            r for r in entries if isinstance(r, dict) and r.get("tier") in {"fast", "critical"}
        ]
    if not reviews:
        entries = source.metadata.get("authoring_signoffs")
        if isinstance(entries, list):
            signoff = next((entry for entry in reversed(entries) if isinstance(entry, dict)), None)
            if signoff:
                reasons = signoffs.stale_reasons(source, settings, signoff)
                return dict(stale=bool(reasons), reasons=reasons, reviewed=False, signed_off=True)
        return dict(stale=False, reasons=[], reviewed=False)
    review = reviews[-1]
    tier = review["tier"]
    reasons = signoffs.stale_reasons(source, settings, review)
    hashes = identities(settings)
    for group in ("core", "critical") if tier == "critical" else ("core",):
        if review.get("checklists", {}).get(group) != hashes[group]:
            reasons.append(group + "_checklist_changed")
    return dict(stale=bool(reasons), reasons=reasons, reviewed=True)


def automatic_tiers(options, roles):
    return tuple(tier for tier in ("fast", "critical") if getattr(options, tier) and tier in roles)


def manual_operation(stage, mode, settings):
    """(tier, "review") for a manual review, or None to skip the trajectory.

    Standard mode: Pending gets the first assigned tier, Candidate gets Critical, and a
    failed tier is reviewed (and repaired) again at that tier.
    """
    if mode != "standard":
        return mode.removeprefix("force_"), "review"
    if stage == 0:
        tier = next(
            (tier for tier in ("fast", "critical") if tier in settings.endpoint_roles), None
        )
        return (tier, "review") if tier else None
    if stage in (1, -2):
        return "critical", "review"
    if stage == -1:
        return "fast", "review"
    return None
