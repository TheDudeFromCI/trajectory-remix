"""Short corrections for a selected event that the source model rejects.

Pydantic's message names the internal path (events.<id>.tool_call.call_id), repeats the
whole input and ends with a documentation link. Small models given that text repeat the
mistake. Here each problem is named by the field the selector actually writes.
"""

from pydantic import ValidationError

MOST = 4


def explain(error: ValidationError, task_id):
    """A one-line correction naming each task.envelope field at fault."""
    problems = []
    for item in error.errors(include_url=False, include_input=False)[:MOST]:
        loc = [str(part) for part in item["loc"]]
        if loc[:2] == ["events", task_id]:
            # events.<id>.<kind>.<field>: the kind is the union tag, not a field.
            field = ".".join(loc[3:] if len(loc) > 3 else loc[2:])
            where = "task.envelope." + field if field else "task.envelope"
        elif loc[:1] == ["windows"]:
            where = "windows." + ".".join(loc[1:])
        else:
            where = ".".join(loc) or "selection"
        problems.append(f"{where}: {item['msg']}")
    return "Selected event invalid: " + "; ".join(problems)
