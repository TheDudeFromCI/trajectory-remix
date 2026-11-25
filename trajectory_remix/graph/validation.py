"""Studio DTOs for the compiler's tokenizer-free dry-run diagnostics."""

from typing import Literal

from ktremix_native import dry_run
from pydantic import Field

from .diagnostic_policy import disabled
from .models import DTO, GraphSource, ProjectSettings


class Diagnostic(DTO):
    """One finding from the dry run.

    ``severity`` ``"error"`` blocks compilation; ``"warning"`` does not. ``code`` is
    a stable identifier, ``path`` locates the offending value in the source JSON,
    and ``event_id`` / ``related_events`` name the events involved.
    """

    severity: Literal["error", "warning"]
    code: str
    path: list[str | int]
    event_id: str | None = None
    message: str
    related_events: list[str] = Field(default_factory=list)


def diagnose(source: GraphSource, settings: ProjectSettings | None = None) -> list[Diagnostic]:
    """Check a graph's structure without a tokenizer.

    ``settings`` adds project tool-contract and reasoning-level checks. The
    checks themselves run in the native compiler.
    """
    # The native checker only needs the tool and reasoning-level parts of settings.
    snapshot = (
        None
        if settings is None
        else settings.model_dump(mode="json", include={"tools", "reasoning_levels"})
    )
    # Metadata does not affect compilation, so leave it out of what the checker parses.
    report = dry_run(source.model_dump_json(exclude={"metadata"}), settings=snapshot)
    suppressed = disabled(settings)
    return [
        Diagnostic.model_validate(item)
        for item in report["diagnostics"]
        if item["severity"] == "error" or item["code"] not in suppressed
    ]
