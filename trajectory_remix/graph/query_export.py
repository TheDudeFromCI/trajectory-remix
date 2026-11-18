"""Stream every trajectory matching a studio search as JSON Lines."""

from __future__ import annotations

import json

from .bulk import BulkService

BATCH = 200


def iter_jsonl(store, query):
    """Yield one compact JSON line per matching trajectory, ordered by id.

    Pages by id (not offset) so edits made while streaming cannot skip or repeat rows.
    """
    where, args = BulkService._where(query or {})
    after = ""
    while True:
        with store.connect() as db:
            rows = db.execute(
                f"""SELECT t.id, r.source FROM graph_trajectories t
                JOIN graph_trajectory_revisions r
                ON r.trajectory_id=t.id AND r.revision=t.revision
                WHERE ({where}) AND t.id > ? ORDER BY t.id LIMIT ?""",
                [*args, after, BATCH],
            ).fetchall()
        for _ident, source in rows:
            yield json.dumps(json.loads(source), separators=(",", ":")) + "\n"
        if len(rows) < BATCH:
            return
        after = rows[-1][0]
