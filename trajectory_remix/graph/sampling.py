"""Exact supplied sampler, linked definitions and durable proposal snapshots.

No compiler, tokenizer or model imports. Original algorithm is in sampler_originals.
"""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from .models import canonical, digest
from .sampler_originals import kidra_sampler as original

ROOT = Path(original.__file__).parent
OVERRIDES = (
    "focus.family",
    "lifecycle",
    "complexity",
    "domain",
    "user_competence",
    "user_patience",
    "user_involvement",
    "user_tone",
)
CALCULATORS = {
    "raw_event_fragments": lambda s: len(s.events),
    "logical_tool_calls": lambda s: len(
        {e.call_id for e in s.events.values() if e.kind == "tool_call"}
    ),
    "authored_windows": lambda s: len(s.windows),
}


def default_matrix():
    return original.load_matrix(ROOT / "kidra_sampling_matrix.v1.json")


def options(matrix):
    return {
        "focus.family": matrix["competencies"],
        "focus.behavior": [b for f in matrix["competencies"] for b in f["behaviors"]],
        "decision.axis": [{"id": k} for k in matrix["decision_axes"]],
        "decision.choice": [v for a in matrix["decision_axes"].values() for v in a["variants"]],
        "domain": matrix["domains"],
        **matrix["dimensions"],
    }


def validate_editor_matrix(matrix):
    """Additional editor integrity checks without changing the supplied sampler.

    Deletions retain references in drafts, but cannot publish dangling rules or
    silently duplicate globally addressed behaviors. Custom fields still survive.
    """
    grouped = options(matrix)
    for key in ("focus.family", "focus.behavior", "domain", *matrix["dimensions"]):
        items = grouped[key]
        ids = [x["id"] for x in items]
        if (
            not ids
            or any(not isinstance(i, str) or not i for i in ids)
            or len(ids) != len(set(ids))
        ):
            raise ValueError(f"Empty or duplicate IDs in {key}")
        if not any(x["weight"] > 0 for x in items):
            raise ValueError(f"At least one positive weight is required in {key}")
    for family in matrix["competencies"]:
        if not any(x["weight"] > 0 for x in family["behaviors"]):
            raise ValueError(f"At least one positive behavior is required in {family['id']}")
    for axis, data in matrix["decision_axes"].items():
        for choice in data["variants"]:
            original._check_weight(choice["weight"], f"decision axis {axis}")
        ids = [x["id"] for x in data["variants"]]
        if (
            not ids
            or len(ids) != len(set(ids))
            or not any(x["weight"] > 0 for x in data["variants"])
        ):
            raise ValueError(f"Empty, duplicate or disabled choices in decision axis {axis}")
    known = {key: {x["id"] for x in items} for key, items in grouped.items()}
    known["focus.id"] = known["focus.behavior"]
    # The exact supplied matrix mentions this unmatched choice.
    # Preserve its original rule rather than modifying the reference artifacts.
    known["decision.choice"].add("request_approval")

    def condition(values):
        if not isinstance(values, dict):
            raise ValueError("Rule conditions must be a field-to-values object")
        for key, allowed in values.items():
            if not isinstance(allowed, list) or not allowed:
                raise ValueError(f"Rule condition {key} requires a nonempty list")
            if key in known and any(v not in known[key] for v in allowed):
                raise ValueError(f"Unknown rule condition choice in {key}")

    for rule in [*matrix["conditional_weights"], *matrix.get("hard_implications", [])]:
        condition(rule["when"])
        for key, value in rule.get("force", {}).items():
            if key not in matrix["dimensions"] or value not in known[key]:
                raise ValueError(f"Unknown forced dimension choice: {key}.{value}")
    for family in matrix["competencies"]:
        for behavior in family["behaviors"]:
            if set(behavior.get("required_event_tags", [])) - set(matrix["event_tags"]):
                raise ValueError(f"Behavior {behavior['id']} references a removed event tag")
            if set(behavior.get("allowed_lifecycle", [])) - known["lifecycle"]:
                raise ValueError(f"Behavior {behavior['id']} references a removed lifecycle")
    for items in grouped.values():
        for item in items:
            for key in ("requires", "forbids"):
                if item.get(key):
                    condition(item[key])
            for key, value in item.get("forced_dimensions", {}).items():
                if key not in matrix["dimensions"] or value not in known[key]:
                    raise ValueError(f"Unknown forced dimension choice: {key}.{value}")


def definitions(matrix, custom=None):
    """Derive definitions from the matrix; customizations cannot fork categories."""
    original.validate_matrix(matrix)
    if matrix.get("schema_version") != "1.0.0":
        raise ValueError("Expected matrix schema_version 1.0.0")
    validate_editor_matrix(matrix)
    opts = options(matrix)
    result = {}
    for key in matrix["metrics_schema"]["tracked_one_of"]:
        result[key] = dict(
            kind="category",
            scope="annotation",
            choices=list(dict.fromkeys(x["id"] for x in opts.get(key, []))),
        )
    for key in matrix["metrics_schema"]["tracked_multi_label"]:
        choices = (
            list(matrix["event_tags"])
            if key == "event_tags"
            else [
                x["id"]
                for x in opts[
                    "focus.family" if key == "secondary_competencies" else "focus.behavior"
                ]
            ]
        )
        result[key] = dict(kind="labels", scope="annotation", choices=choices)
    for key in matrix["metrics_schema"]["post_generation_actual_fields"]:
        result.setdefault(key, dict(kind="annotation", scope="annotation"))
    for key in CALCULATORS:
        result[key] = dict(kind="number", scope="source", unit="count")
    # These require an explicit compiled export/profile; source fragments are no proxy.
    for key in (
        "peak_active_windows",
        "observed_context_tokens",
        "observed_supervised_tokens",
        "observed_idle_tokens",
    ):
        result[key] = dict(kind="number", scope="export", unit="count")
    for key, edit in (custom or {}).items():
        if not isinstance(edit, dict) or set(edit) - {"label", "unit", "tolerance", "target"}:
            raise ValueError("Metric options support label, unit, target and tolerance only")
        if key not in result:
            result[key] = dict(kind="annotation", scope="annotation")
        if "tolerance" in edit:
            t = edit["tolerance"]
            if not isinstance(t, dict) or set(t) != {"absolute", "relative"}:
                raise ValueError("Tolerance requires absolute and relative bands")
            for v in t.values():
                if type(v) not in (int, float) or not 0 <= v < float("inf"):
                    raise ValueError("Tolerance bands must be finite and nonnegative")
        if any(not isinstance(edit[k], str) for k in ("label", "unit", "target") if k in edit):
            raise ValueError("Metric labels/units must be strings")
        result[key].update(edit)
    for key, spec in result.items():
        spec["revision"] = digest([key, spec])
    return result


def configuration(matrix=None, metric_options=None):
    matrix = deepcopy(default_matrix() if matrix is None else matrix)
    try:
        defs = definitions(matrix, metric_options)
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError(f"Invalid matrix structure: {error}") from error
    return dict(
        matrix=matrix,
        matrix_digest=digest(matrix),
        sampler_version=original.SAMPLER_VERSION,
        sampler_sha256=hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest(),
        metric_options=deepcopy(metric_options or {}),
        definitions=defs,
        registry_digest=digest(defs),
    )


class SamplerService:
    def __init__(self, jobs):
        self.jobs, self.store = jobs, jobs.store
        with self.store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS graph_proposals (
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL, snapshot TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS immutable_graph_proposal_update BEFORE UPDATE ON graph_proposals
                    BEGIN SELECT RAISE(ABORT,'Proposal snapshots are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_graph_proposal_delete BEFORE DELETE ON graph_proposals
                    BEGIN SELECT RAISE(ABORT,'Proposal snapshots are immutable'); END;
            """)

    def editor(self):
        r = self.store.settings()
        c = r.settings.sampler_configuration or configuration()
        return dict(
            configuration=deepcopy(c),
            settings_revision=r.revision,
            settings_commit=r.commit_id,
            overrides={k: [x["id"] for x in options(c["matrix"])[k]] for k in OVERRIDES},
        )

    def update(self, matrix, metric_options, expected_revision):
        r = self.store.settings()
        self.store._check_revision("settings", expected_revision, r)
        data = r.settings.model_dump(mode="json")
        data["sampler_configuration"] = configuration(matrix, metric_options)
        self.store.update_settings(data, expected_revision)
        return self.editor()

    def propose(self, *, seed=None, world_group_id=None, overrides=None, frozen=None):
        frozen = deepcopy(self.editor() if frozen is None else frozen)
        c = frozen["configuration"]
        if seed is not None and type(seed) is not int:
            raise ValueError("seed must be an integer")
        # Default provisional seed groups are provenance, never dataset families.
        try:
            proposal = original.sample_one(c["matrix"], seed, world_group_id, overrides)
        except (TypeError, KeyError, AssertionError) as e:
            raise ValueError(f"Invalid sampler configuration or pins: {e}") from e
        ident = str(uuid4())
        snapshot = dict(
            id=ident,
            proposal=proposal,
            payload_digest=digest(proposal),
            family_id=world_group_id,
            frozen=frozen,
        )
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO graph_proposals VALUES(?,?,?)",
                (ident, digest(snapshot), canonical(snapshot)),
            )
        return snapshot

    def read(self, ident):
        with self.store.connect() as db:
            r = db.execute("SELECT snapshot FROM graph_proposals WHERE id=?", (ident,)).fetchone()
            if r is None:
                raise KeyError("Proposal not found")
            return json.loads(r[0])

    def run(self, requests, instructions):
        frozen = self.editor()
        proposals = [self.propose(**request, frozen=frozen) for request in requests]
        return self.jobs.create_run(dict(sampler=frozen, instructions=instructions), proposals)

    @staticmethod
    def metadata(snapshot):
        # Callers explicitly preserve this metadata for variants and repairs.
        data = dict(proposal=deepcopy(snapshot), targets=deepcopy(snapshot["proposal"]["metrics"]))
        if snapshot["family_id"]:
            data["family_id"] = snapshot["family_id"]
        return data
