"""Carry a project's customised agent prompt over from the retired Flow.

Projects that edited the Flow (for example appending a persona pack to its
actor_instruction) keep that text: on startup it moves into settings.prompts.actor, once.
The retired graph_flow table is then dropped, so a later Load default sticks.
"""

import yaml

from .models import GenerationPrompts

# The retired Flow's default actor_instruction, to tell edits from the default.
RETIRED_DEFAULT = (
    "Write exactly this next action from the visible evidence and tool contract only. "
    'Reasoning is private thinking, never addressed to anyone: no greetings, no "you", no '
    "replies. Speak to people only through chat tool calls, including short progress updates. "
    "Keep reasoning concise. Kidra coordinates complex, long-running multi-agent work: she "
    "plans, delegates, verifies and keeps continuity. When a suitable agent or tool is "
    "visible she does not personally write production code, run extensive research or do bulk "
    "repetitive work. Delegate in proportion to the task; a small job needs no team. Select "
    "resources using observed capabilities and costs, preserve task ownership and "
    "dependencies, verify claims and replan on failures. Do not re-delegate an unchanged "
    "failed task, create agents recursively or excessively, or keep planning instead of "
    "moving to execution. Respect visible budgets and the user's visible authority: for a "
    "consequential action outside it, request approval and wait. Do not claim execution, "
    "inspection or completion without visible supporting results. Distinguish intentions, "
    "reported results, verified outcomes and remaining blockers. Never use hidden author "
    "targets or predict an unobserved tool outcome. "
).strip()


def _flow_actor(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='graph_flow'").fetchone():
        return None
    row = db.execute("SELECT text FROM graph_flow WHERE id = 1").fetchone()
    try:
        data = yaml.safe_load(row[0]) if row else None
    except yaml.YAMLError:
        return None
    actor = data.get("actor_instruction") if isinstance(data, dict) else None
    return actor if isinstance(actor, str) and actor.strip() else None


def carried_actor(actor):
    """The Flow's actor text in today's form: an appended persona keeps the new default."""
    default = GenerationPrompts().actor
    if actor.strip() == RETIRED_DEFAULT:
        return None
    if actor.startswith(RETIRED_DEFAULT):
        return default + actor[len(RETIRED_DEFAULT) :]
    return actor


def migrate(store):
    """Returns True when a customised Flow prompt was carried into the settings."""
    with store.connect() as db:
        actor = _flow_actor(db)
    record = store.settings()
    carried = carried_actor(actor) if actor else None
    moved = bool(carried) and record.settings.prompts.actor == GenerationPrompts().actor
    if moved:
        data = record.settings.model_dump(mode="json")
        data["prompts"]["actor"] = carried
        store.update_settings(data, record.revision)
    with store.connect() as db:
        db.execute("DROP TABLE IF EXISTS graph_flow")
    return moved
