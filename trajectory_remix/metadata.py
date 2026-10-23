"""Typed, parameterized metadata queries over the SQLite projection."""

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr


class MetadataFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: Annotated[str, Field(min_length=1, max_length=256)]
    op: Literal["eq", "ne", "lt", "lte", "gt", "gte", "exists"] = "eq"
    value: StrictStr | StrictInt | StrictFloat | StrictBool | None = None


COUNTS = {"counts.events": "m.event_count", "counts.windows": "m.window_count", "counts.tags": "m.tag_count"}
OPERATORS = {"eq": "=", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}


def where(filters=None, tags=None, tag_mode="all"):
    clauses, parameters = [], []
    for raw in filters or []:
        item = raw if isinstance(raw, MetadataFilter) else MetadataFilter.model_validate(raw)
        key, op, value = item.key, item.op, item.value
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Metadata filters require finite numbers")
        if type(value) is int and not -(1 << 63) <= value < (1 << 63):
            raise ValueError("Metadata integer filters must fit a signed 64-bit integer")
        if key in COUNTS:
            if op == "exists":
                continue
            if type(value) not in (int, float):
                raise ValueError("Count filters require numeric values")
            clauses.append(f"{COUNTS[key]} {OPERATORS[op]} ?")
            parameters.append(value)
            continue
        if op == "exists":
            clauses.append("EXISTS(SELECT 1 FROM json_each(m.properties) f WHERE f.key=?)")
            parameters.append(key)
            continue
        if value is None:
            expression, args = ("f.type='null'", [])
            if op == "ne":
                expression = "f.type!='null'"
            elif op != "eq":
                raise ValueError("Null metadata supports only eq, ne, and exists")
        else:
            if type(value) in (int, float):
                typed = "f.type IN ('integer','real')"
            elif type(value) is bool:
                typed = "f.type IN ('true','false')"
            else:
                typed = "f.type='text'"
            expression = f"{typed} AND f.atom {OPERATORS[op]} ?"
            args = [value]
        clauses.append(f"EXISTS(SELECT 1 FROM json_each(m.properties) f WHERE f.key=? AND ({expression}))")
        parameters.extend([key, *args])
    tags = list(dict.fromkeys(tags or []))
    if any(not isinstance(tag, str) for tag in tags):
        raise ValueError("Tags must be strings")
    if tag_mode not in ("all", "any"):
        raise ValueError("tag_mode must be all or any")
    if tags:
        placeholders = ",".join("?" for _ in tags)
        if tag_mode == "all":
            clauses.append(f"(SELECT count(*) FROM metadata_tags t WHERE t.trajectory_uuid=m.trajectory_uuid AND t.tag IN ({placeholders}))=?")
            parameters.extend([*tags, len(tags)])
        else:
            clauses.append(f"m.trajectory_uuid IN (SELECT trajectory_uuid FROM metadata_tags WHERE tag IN ({placeholders}))")
            parameters.extend(tags)
    return " AND ".join(clauses) or "1", parameters


def query(store, filters=None, tags=None, tag_mode="all", offset=0, limit=100):
    condition, parameters = where(filters, tags, tag_mode)
    with store.connect() as db:
        db.execute("BEGIN")
        revision = db.execute("SELECT revision FROM project").fetchone()[0]
        total = db.execute(f"SELECT count(*) FROM trajectory_metadata m WHERE {condition}", parameters).fetchone()[0]
        rows = db.execute(f"""
            SELECT s.uuid,s.sample_id,m.properties,m.tags,m.event_count,m.window_count,m.tag_count,s.content_hash
            FROM trajectory_metadata m JOIN samples s ON s.uuid=m.trajectory_uuid
            WHERE {condition} ORDER BY s.position LIMIT ? OFFSET ?
        """, [*parameters, limit, offset])
        import json
        trajectories = [{
            "uuid": row[0], "id": row[1], "metadata": json.loads(row[2]) if row[2] is not None else {},
            "tags": json.loads(row[3]), "counts": {"events": row[4], "windows": row[5], "tags": row[6]},
            "content_hash": row[7],
        } for row in rows]
        return {"revision": revision, "total": total, "trajectories": trajectories}


def counts(store, key="tags", filters=None, tags=None, tag_mode="all", limit=100):
    condition, parameters = where(filters, tags, tag_mode)
    with store.connect() as db:
        db.execute("BEGIN")
        revision = db.execute("SELECT revision FROM project").fetchone()[0]
        total = db.execute(f"SELECT count(*) FROM trajectory_metadata m WHERE {condition}", parameters).fetchone()[0]
        if key == "tags":
            rows = db.execute(f"SELECT 'text',t.tag,count(*) AS n FROM trajectory_metadata m JOIN metadata_tags t ON t.trajectory_uuid=m.trajectory_uuid WHERE {condition} GROUP BY t.tag ORDER BY n DESC,t.tag LIMIT ?", [*parameters, limit])
        elif key in COUNTS:
            column = COUNTS[key]
            rows = db.execute(f"SELECT 'integer',{column},count(*) AS n FROM trajectory_metadata m WHERE {condition} GROUP BY {column} ORDER BY n DESC,{column} LIMIT ?", [*parameters, limit])
        else:
            rows = db.execute(f"""
                SELECT f.type,f.atom,count(*) AS n FROM trajectory_metadata m JOIN json_each(m.properties) f
                WHERE ({condition}) AND f.key=? AND f.type IN ('text','integer','real','true','false','null')
                GROUP BY f.type,f.atom ORDER BY n DESC,f.type,f.atom LIMIT ?
            """, [*parameters, key, limit])
        groups = [{"value": bool(value) if kind in ("true", "false") else value, "type": kind, "count": count} for kind, value, count in rows]
        return {"revision": revision, "total": total, "key": key, "groups": groups, "limit": limit}
