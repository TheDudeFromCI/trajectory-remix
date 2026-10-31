"""Shared generation ownership rules for discovery and dataset-wide resume."""
from __future__ import annotations


GENERATION_PHASES = {'unfinished': 'planning', 'complete': 'complete'}

# The aliases are shared with storage listing and the resume queue. Missing or
# corrupt checkpoints remain discoverable for repair; a known foreign owner
# excludes manual copies that inherited their source's generation metadata.
AUTOMATED_GENERATION_SQL = """(
    json_extract(m.properties,'$.origin') IS 'automation'
    AND json_type(m.properties,'$.generation_job') IS 'text'
    AND json_extract(m.properties,'$.generation_job') IS NOT ''
    AND NOT EXISTS (
        SELECT 1 FROM generation_jobs j
        WHERE j.id=json_extract(m.properties,'$.generation_job')
          AND CASE WHEN json_valid(j.snapshot)
              THEN coalesce(json_extract(j.snapshot,'$.checkpoint.sample.uuid')=s.uuid,1)
              ELSE 1 END = 0
    )
)"""


def generation_matches(sample, phase, store=None):
    metadata = sample.get('metadata')
    if not isinstance(metadata, dict):
        return False
    job = metadata.get('generation_job')
    if (metadata.get('origin') != 'automation' or metadata.get('generation_phase') != phase
            or not isinstance(job, str) or not job):
        return False
    if store is not None:
        with store.connect() as db:
            row = db.execute('''SELECT CASE WHEN json_valid(snapshot)
                THEN json_extract(snapshot,'$.checkpoint.sample.uuid') ELSE NULL END
                FROM generation_jobs WHERE id=?''', (job,)).fetchone()
        if row and row[0] is not None:
            return row[0] == sample.get('uuid')
    # An isolated authoring draft has no checkpoint registry to consult.
    return True
