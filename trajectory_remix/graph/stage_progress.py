"""Completed stage counts for frozen generation runs."""

import json

from .draft_assembly import DRIVER
from .models import GenerationOptions
from .reviews import automatic_tiers

# Resolve the immutable source from the most recent accepted checkpoint. Later
# edits or reviews on a detached trajectory do not belong to this run.
COUNTS = """
SELECT c.job_id,
       SUM(json_type(c.checkpoint, '$.draft') = 'text') AS draft,
       SUM(COALESCE(json_extract(c.checkpoint, '$.assembled'), 0)) AS assembly,
       SUM(EXISTS (SELECT 1 FROM json_each(r.source, '$.metadata.evaluations')
                   WHERE type = 'object' AND json_extract(value, '$.tier') = 'fast')) AS fast,
       SUM(EXISTS (SELECT 1 FROM json_each(r.source, '$.metadata.evaluations')
                   WHERE type = 'object' AND json_extract(value, '$.tier') = 'critical')) AS critical
FROM graph_checkpoints c
LEFT JOIN graph_steps s ON s.job_id = c.job_id AND s.step IN (
    c.trajectory_id || ':' || (json_extract(c.checkpoint, '$.serial') - 1),
    c.trajectory_id || ':' || (json_extract(c.checkpoint, '$.serial') - 1) || ':local'
)
LEFT JOIN graph_trajectory_revisions r ON r.commit_id = s.commit_id
WHERE c.job_id IN (SELECT value FROM json_each(?))
GROUP BY c.job_id
"""


def attach(db, jobs):
    """Add stage counts while preserving trajectory totals for API consumers."""
    runs = {job["id"]: job for job in jobs if job["kind"] == "run"}
    if not runs:
        return
    plans = db.execute(
        """SELECT id, json_extract(config, '$.settings.generation') AS options,
                      json_extract(config, '$.settings.endpoint_roles') AS roles
           FROM graph_jobs WHERE id IN (SELECT value FROM json_each(?))
           AND json_extract(config, '$.config.driver') = ?""",
        (json.dumps(list(runs)), DRIVER),
    ).fetchall()
    if not plans:
        return
    counts = {
        row["job_id"]: row
        for row in db.execute(COUNTS, (json.dumps([row["id"] for row in plans]),))
    }
    for plan in plans:
        job = runs[plan["id"]]
        tiers = automatic_tiers(
            GenerationOptions.model_validate(json.loads(plan["options"] or "{}")),
            json.loads(plan["roles"] or "{}"),
        )
        stages = ("draft", "assembly", *tiers)
        completed = counts.get(job["id"])
        job["stage_progress"] = dict(
            completed=sum(completed[stage] or 0 for stage in stages) if completed else 0,
            total=len(stages) * job["total"],
        )
