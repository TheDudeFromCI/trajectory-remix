"""Per-request token usage and cost, so a run's real cost can be read off its job.

One row per completed model response: prompt, completion, cached and reasoning tokens,
and the provider-reported cost when there is one (OpenRouter reports it on every
response). Only counts are kept: never prompts, responses or credentials. Missing
values stay missing, never zero.
"""

from datetime import UTC, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_usage (
    job_id TEXT NOT NULL,
    trajectory_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    operation TEXT NOT NULL,
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cached_tokens INTEGER,
    reasoning_tokens INTEGER,
    cost REAL,
    seconds REAL NOT NULL,
    created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS graph_usage_job ON graph_usage(job_id);
"""
COUNTS = ("prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens")


def _int(value):
    return value if type(value) is int and value >= 0 else None


def counts(usage):
    """Normalized counts from an OpenAI-compatible usage object; unknown stays None."""
    usage = usage if isinstance(usage, dict) else {}
    prompt = usage.get("prompt_tokens_details") or {}
    completion = usage.get("completion_tokens_details") or {}
    cost = usage.get("cost")
    return dict(
        prompt_tokens=_int(usage.get("prompt_tokens")),
        completion_tokens=_int(usage.get("completion_tokens")),
        cached_tokens=_int(prompt.get("cached_tokens")) if isinstance(prompt, dict) else None,
        reasoning_tokens=(
            _int(completion.get("reasoning_tokens")) if isinstance(completion, dict) else None
        ),
        cost=float(cost) if type(cost) in (int, float) and cost >= 0 else None,
    )


def describe(values):
    """A short log suffix such as 'prompt_tokens=10 cached_tokens=4 cost=0.0001'."""
    parts = [f"{key}={values[key]}" for key in COUNTS if values[key] is not None]
    if values["cost"] is not None:
        parts.append(f"cost={values['cost']:.6f}")
    return " ".join(parts)


def record(store, ticket, result, seconds):
    if not isinstance(result, dict):
        return dict.fromkeys((*COUNTS, "cost"))
    values = counts(result.get("usage"))
    with store.connect() as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO graph_usage VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ticket.job_id,
                ticket.trajectory_id,
                ticket.endpoint,
                str(ticket.payload.get("operation", "")),
                result.get("model") if isinstance(result.get("model"), str) else None,
                *(values[key] for key in COUNTS),
                values["cost"],
                round(seconds, 3),
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
    return values


def summary(store, job):
    """Totals for a job, overall and per operation, plus per-trajectory averages."""
    columns = ", ".join(f"SUM({c}), COUNT({c})" for c in (*COUNTS, "cost"))
    with store.connect() as db:
        db.executescript(SCHEMA)
        rows = db.execute(
            f"SELECT operation, COUNT(*), SUM(seconds), {columns} FROM graph_usage "
            "WHERE job_id=? GROUP BY operation ORDER BY operation",
            (job,),
        ).fetchall()
        trajectories, first, last = db.execute(
            "SELECT COUNT(DISTINCT trajectory_id), MIN(created), MAX(created) FROM graph_usage "
            "WHERE job_id=?",
            (job,),
        ).fetchone()
        models = [
            r[0]
            for r in db.execute(
                "SELECT DISTINCT model FROM graph_usage WHERE job_id=? AND model IS NOT NULL",
                (job,),
            )
        ]

    def totals(row):
        result = dict(requests=row[1], seconds=round(row[2] or 0, 1))
        for i, key in enumerate((*COUNTS, "cost")):
            total, known = row[3 + 2 * i], row[4 + 2 * i]
            # A total is only reported when every response reported the value.
            result[key] = total if known == row[1] and known else None
            result[key + "_reported"] = known
        return result

    operations = {row[0]: totals(row) for row in rows}
    overall = [None, 0, 0] + [0] * (2 * (len(COUNTS) + 1))
    for row in rows:
        for i in range(1, len(row)):
            overall[i] += row[i] or 0
    total = totals(overall) if rows else None
    average = None
    if total and trajectories:
        average = {
            key: (total[key] / trajectories if total[key] is not None else None)
            for key in (*COUNTS, "cost", "requests", "seconds")
        }
    return dict(
        job_id=job,
        trajectories=trajectories,
        # From the first recorded response to the last, to the second. Requests run
        # concurrently, so this is usually far less than the summed model time.
        wall_seconds=(
            (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds()
            if first and last
            else None
        ),
        models=models,
        total=total,
        per_trajectory=average,
        operations=operations,
    )


def router(store):
    from fastapi import APIRouter

    routes = APIRouter(prefix="/api/v1")

    @routes.get("/jobs/{job}/usage")
    def job_usage(job: str):
        return summary(store, job)

    return routes
