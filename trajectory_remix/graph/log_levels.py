"""Status levels for job log lines, so the dashboard can colour them."""

LEVELS = ("info", "success", "warning", "error")


def check(level):
    if level not in LEVELS:
        raise ValueError(f"Log level must be one of {', '.join(LEVELS)}")
    return level


def migrate(db):
    """Add the level column to log tables created before levels existed."""
    columns = {row[1] for row in db.execute("PRAGMA table_info(graph_job_logs)")}
    if "level" not in columns:
        db.execute("ALTER TABLE graph_job_logs ADD COLUMN level TEXT NOT NULL DEFAULT 'info'")
