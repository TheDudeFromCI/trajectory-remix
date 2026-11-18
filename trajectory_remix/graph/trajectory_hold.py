"""Holding one trajectory for attention instead of pausing its whole job.

A trajectory that needs manual intervention (output that stays invalid, a context limit,
a local error) is held: the other trajectories keep running. A hold belongs to the job
epoch it was made in, so Resume (which starts a new epoch) retries every held trajectory.
The job itself pauses only when nothing but held trajectories is left.
"""

from .models import GraphSource


def held(cp, epoch):
    """True while the trajectory is held in the current epoch."""
    return bool(cp and cp.get("held") and cp["held"]["epoch"] == epoch)


def mark(cp, epoch, reason):
    cp.update(errors=0, held=dict(reason=reason, epoch=epoch))


def flag(source, reason):
    """Show the hold on the trajectory itself, so lists and the editor can show it."""
    data = source.to_dict()
    data.setdefault("metadata", {})["needs_attention"] = reason
    return GraphSource.model_validate(data)


def unflag(source):
    if "needs_attention" not in source.metadata:
        return source
    data = source.to_dict()
    del data["metadata"]["needs_attention"]
    return GraphSource.model_validate(data)
