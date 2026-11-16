"""Explicit source measurements and provenance-aware population queries."""

from copy import deepcopy

from .bulk import BulkService
from .derived_labels import DerivedLabelCounts
from .sampling import CALCULATORS, original


class MetricService:
    def __init__(self, sampler):
        self.sampler, self.store = sampler, sampler.store

    def calculate(self, record, frozen):
        defs = frozen["definitions"]
        return {
            key: dict(
                value=calc(record.source),
                origin="source_calculation",
                source_revision=record.intrinsic_revision,
                source_hash=record.intrinsic_hash,
                definition_revision=defs[key]["revision"],
            )
            for key, calc in CALCULATORS.items()
            if key in defs
        }

    def view(self, record, defs):
        metrics = record.source.metadata.get("metrics", {})
        values = {}
        for key, spec in defs.items():
            saved = metrics.get(key)
            valid = isinstance(saved, dict) and saved.get("value") is not None
            stale = valid and (
                saved.get("source_hash") != record.intrinsic_hash
                or saved.get("definition_revision") != spec["revision"]
            )
            if valid and spec["scope"] == "export":
                valid = all(saved.get(k) for k in ("profile_fingerprint", "compiler_version"))
            target = (
                record.source.metadata.get("targets", {})
                .get("numeric_targets", {})
                .get(spec.get("target", key))
            )
            drift = None
            if (
                valid
                and not stale
                and "tolerance" in spec
                and type(target) in (int, float)
                and type(saved["value"]) in (int, float)
            ):
                t = spec["tolerance"]
                drift = abs(saved["value"] - target) > max(
                    t["absolute"], abs(target) * t["relative"]
                )
            values[key] = dict(
                drift=drift,
                status="unavailable" if not valid else "stale" if stale else "available",
                observation=saved,
                definition=spec,
            )
        return dict(
            id=record.source.id,
            revision=record.revision,
            values=values,
            targets=record.source.metadata.get("targets", {}),
        )

    def annotate(
        self,
        ident,
        expected_revision,
        key,
        value,
        origin,
        profile_fingerprint=None,
        compiler_version=None,
    ):
        record = self.store.read(ident)
        defs = self.sampler.editor()["configuration"]["definitions"]
        if key not in defs:
            raise ValueError("Unknown metric definition")
        spec = defs[key]
        if origin not in ("manual_annotation", "export"):
            raise ValueError("Annotation origin must be manual_annotation or export")
        if spec["scope"] == "export" and (
            origin != "export" or not profile_fingerprint or not compiler_version
        ):
            raise ValueError("Export measures require profile fingerprint and compiler version")
        if value is None:
            raise ValueError("Unknown observations must remain unavailable")
        if spec["kind"] == "category" and value not in spec["choices"]:
            raise ValueError("Unknown category")
        if spec["kind"] == "labels" and (
            not isinstance(value, list) or any(x not in spec["choices"] for x in value)
        ):
            raise ValueError("Unknown labels")
        if spec["kind"] == "number" and (
            type(value) not in (int, float) or not -float("inf") < value < float("inf")
        ):
            raise ValueError("Expected finite numeric value")
        source = record.source.to_dict()
        source.setdefault("metadata", {}).setdefault("metrics", {})[key] = dict(
            value=value,
            origin=origin,
            source_revision=record.intrinsic_revision,
            source_hash=record.intrinsic_hash,
            definition_revision=spec["revision"],
            profile_fingerprint=profile_fingerprint,
            compiler_version=compiler_version,
        )
        self.store.replace(ident, source, expected_revision)
        return self.view(self.store.read(ident), defs)

    def population(self, query=None, population="all"):
        if population not in ("all", "accepted"):
            raise ValueError("population must be all or accepted")
        c = self.sampler.editor()["configuration"]
        where, args = BulkService._where(query or {})
        unavailable = dict.fromkeys(c["definitions"], 0)
        stale = dict.fromkeys(c["definitions"], 0)
        total, derived = 0, DerivedLabelCounts()
        with self.store.connect() as db:
            db.execute("BEGIN")

            def records():
                nonlocal total
                rows = db.execute(
                    f"""SELECT r.* FROM graph_trajectories t JOIN graph_trajectory_revisions r
                    ON r.trajectory_id=t.id AND r.revision=t.revision WHERE {where}""",
                    args,
                )
                for row in rows:
                    r = self.store._record(row)
                    stage = r.source.metadata.get("stage", -3)
                    accepted = type(stage) is int and stage >= 1
                    if population == "accepted" and not accepted:
                        continue
                    total += 1
                    view = self.view(r, c["definitions"])
                    actual = {}
                    for key, value in view["values"].items():
                        if value["status"] == "unavailable":
                            unavailable[key] += 1
                        elif value["status"] == "stale":
                            stale[key] += 1
                        else:
                            actual[key] = value["observation"]["value"]
                    proposal = r.source.metadata.get("proposal", {}).get("proposal")
                    if not proposal:
                        continue
                    derived.add(proposal)
                    proposal = deepcopy(proposal)
                    proposal["status"] = "accepted" if accepted else "proposed"
                    proposal["metrics"]["actual"] = actual
                    yield proposal

            report = original.metrics_report(records(), c["matrix"], population=population)
        return dict(
            total=total,
            proposal_coverage=report,
            derived_labels=derived.report(),
            unavailable=unavailable,
            stale=stale,
            definitions=c["definitions"],
            acceptance="stage >= Candidate (1)",
            note="Coverage describes frozen proposal targets, not observed behaviors.",
        )
