"""Shared application service for future REST/MCP adapters; no compiler hooks."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from . import authoring, signoffs
from .authoring_tools import READ_ONLY
from .storage import GraphStore, SourceRecord
from .validation import Diagnostic, diagnose


def merge_patch(target, patch):
    """RFC 7396 object merge patch; arrays (including dependencies) replace whole."""
    if not isinstance(patch, dict):
        return deepcopy(patch)
    result = deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


@dataclass(frozen=True)
class GraphResult:
    record: SourceRecord
    diagnostics: list[Diagnostic]

    @property
    def has_errors(self):
        return any(d.severity == "error" for d in self.diagnostics)


class GraphCommands:
    def __init__(self, store: GraphStore):
        self.store = store

    def _result(self, record):
        # Pin diagnostics to the settings version saved with this source commit.
        return GraphResult(
            record, diagnose(record.source, self.store.settings(record.settings_revision).settings)
        )

    def read(self, trajectory_id: str, revision: int | None = None):
        return self._result(self.store.read(trajectory_id, revision))

    def author(self, trajectory_id, name, arguments, expected_revision=None):
        read_only = name in READ_ONLY
        with self.store.connect() as db:
            db.execute("BEGIN" if read_only else "BEGIN IMMEDIATE")
            record = self.store._read(db, trajectory_id)
            if not read_only:
                self.store._check_revision(trajectory_id, expected_revision, record)
                self.store._check_owner(db, trajectory_id)
            job_owned = read_only and self.store._owner(db, trajectory_id) is not None
            snapshot = self.store._settings(db, record.settings_revision if job_owned else None)
            source, settings = record.source, snapshot.settings
            accepted = True
            if name == "signoff":
                draft = source.metadata.get("draft")
                draft_visible = isinstance(draft, str) and bool(draft)
                try:
                    verdict, evidence = signoffs.validate(
                        source,
                        settings,
                        signoffs.criteria({}, draft_visible),
                        arguments,
                    )
                except ValueError as error:
                    accepted, reply = False, dict(error=str(error))
                else:
                    source = signoffs.record(
                        source,
                        settings,
                        evidence,
                        operation="mcp",
                        verdict=verdict,
                        draft_visible=draft_visible,
                        provenance=dict(
                            source_revision=record.revision,
                            settings_revision=snapshot.revision,
                        ),
                    )
                    reply = dict(outcome=verdict)
            else:
                source, reply = authoring.apply(source, settings, name, arguments)
            if not read_only and source.to_dict() != record.source.to_dict():
                record = self.store._commit(
                    db,
                    source,
                    record,
                    dict(operation=name, channel="mcp"),
                    settings_revision=snapshot.revision,
                )
            if "diagnostics" not in reply:
                reply["diagnostics"] = authoring.findings(source, settings)
            return reply | dict(
                accepted=accepted,
                source_hash=source.intrinsic_hash,
                revision=record.revision,
                settings_revision=snapshot.revision,
            )

    def create(self, source: dict, *, provenance: dict | None = None):
        return self._result(self.store.create(source, provenance))

    def replace(
        self,
        trajectory_id: str,
        source: dict,
        expected_revision: int,
        *,
        provenance: dict | None = None,
        rebase: bool = False,
        resolutions: dict | None = None,
    ):
        return self._result(
            self.store.replace(
                trajectory_id,
                source,
                expected_revision,
                provenance,
                rebase=rebase,
                resolutions=resolutions,
            )
        )

    def patch(self, trajectory_id: str, patch: dict, expected_revision: int):
        if not isinstance(patch, dict):
            raise ValueError("Source patch must be an object")
        draft = self.store.read(trajectory_id).source.to_dict()
        return self.replace(trajectory_id, merge_patch(draft, patch), expected_revision)

    def _event_edit(self, trajectory_id, expected_revision, edit):
        draft = self.store.read(trajectory_id).source.to_dict()
        edit(draft["events"])
        return self.replace(trajectory_id, draft, expected_revision)

    def add_event(self, trajectory_id: str, event_id: str, event: dict, expected_revision: int):
        def edit(events):
            if event_id in events:
                raise ValueError("Event already exists")
            events[event_id] = event

        return self._event_edit(trajectory_id, expected_revision, edit)

    def patch_event(self, trajectory_id: str, event_id: str, patch: dict, expected_revision: int):
        if not isinstance(patch, dict):
            raise ValueError("Event patch must be an object")

        def edit(events):
            events[event_id] = merge_patch(events[event_id], patch)

        return self._event_edit(trajectory_id, expected_revision, edit)

    def delete_event(self, trajectory_id: str, event_id: str, expected_revision: int):
        def edit(events):
            del events[event_id]

        return self._event_edit(trajectory_id, expected_revision, edit)

    def set_dependencies(
        self, trajectory_id: str, event_id: str, after: list[str], expected_revision: int
    ):
        return self.patch_event(trajectory_id, event_id, {"after": after}, expected_revision)

    def remove_dependency(
        self, trajectory_id: str, event_id: str, dependency_id: str, expected_revision: int
    ):
        def edit(events):
            events[event_id]["after"].remove(dependency_id)

        return self._event_edit(trajectory_id, expected_revision, edit)
