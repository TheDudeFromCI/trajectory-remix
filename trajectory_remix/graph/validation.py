"""Studio DTOs for the compiler's tokenizer-free dry-run diagnostics."""

from typing import Literal

from ktremix_native import dry_run
from pydantic import Field

from .models import DTO, GraphSource, ProjectSettings


class Diagnostic(DTO):
    severity: Literal["error", "warning"]
    code: str
    path: list[str | int]
    event_id: str | None = None
    message: str
    related_events: list[str] = Field(default_factory=list)


def diagnose(source: GraphSource, settings: ProjectSettings | None = None) -> list[Diagnostic]:
    snapshot = (
        None
        if settings is None
        else settings.model_dump(mode="json", include={"tools", "reasoning_levels"})
    )
    report = dry_run(source.model_dump_json(exclude={"metadata"}), settings=snapshot)
    return [Diagnostic.model_validate(item) for item in report["diagnostics"]]
