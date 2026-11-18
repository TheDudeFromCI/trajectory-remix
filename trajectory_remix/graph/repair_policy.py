"""When a repair session continues, starts a fresh round, or ends.

A repair only fails when the repairer gives up (says repair is not feasible), or when the
round cap, a spending guard, is reached. A failing verdict from the repairer keeps the
session going; a session that outgrows its context starts a fresh round on the current
graph.
"""

from .flow import causal_view
from .models import canonical
from .validation import diagnose

NOTE = (
    " Repair actions: view returns the current source with diagnostics; inspect shows one"
    " event and what it can see; patch merges a patch into an event (a new event ID adds"
    " one); patch_window edits a window; remove_event deletes one. What an event can see"
    " (causal_views) is computed from its after list, so to give an event missing evidence,"
    " patch its after to include the events that show it. A verdict that still"
    " fails keeps the session open: keep repairing. Use give_up, with the reason, only"
    " when repair is not feasible."
)


def actions():
    """Extra repair actions, beside inspect, patch, patch_window, remove_event and verdict."""
    return [
        dict(
            type="object",
            properties={"action": {"const": "view"}},
            required=["action"],
            additionalProperties=False,
        ),
        dict(
            type="object",
            properties={
                "action": {"const": "give_up"},
                "reason": {"type": "string", "minLength": 20},
            },
            required=["action", "reason"],
            additionalProperties=False,
        ),
    ]


def view(source, settings):
    return dict(
        source=source.model_dump(mode="json", exclude={"metadata"}),
        source_hash=source.intrinsic_hash,
        diagnostics=[d.model_dump(mode="json") for d in diagnose(source, settings)],
    )


def inspect(source, ident):
    if ident not in source.events:
        raise ValueError("Unknown event ID")
    try:
        visible = causal_view(source, source.events[ident].after)
    except (ValueError, KeyError):
        visible = "Structurally invalid causal frontier; see diagnostics from view."
    return dict(
        event=source.events[ident].model_dump(mode="json"),
        visible=visible,
        source_hash=source.intrinsic_hash,
    )


def still_failing(review):
    """The message that keeps a session open after a failing verdict."""
    failing = sorted(k for k, item in review["items"].items() if item["status"] == "fail")
    return dict(
        role="user",
        content=canonical(
            dict(
                still_failing=failing,
                next="Keep repairing these items with view, inspect and patch, then return a"
                " new verdict. Use give_up only if repair is not feasible.",
            )
        ),
    )


def new_round(cp, settings, base):
    """Start a fresh round on the current graph, or return False at the round cap.

    base is the repair payload messages for the current source.
    """
    rounds = cp.get("round", 1)
    if rounds >= settings.generation.repair_rounds:
        return False
    messages = list(base)
    if cp.get("failed_review"):
        messages.append(
            dict(
                role="user",
                content=canonical(
                    dict(
                        failed_review=cp["failed_review"],
                        round=rounds + 1,
                        note="Earlier rounds patched the source shown above; continue from it.",
                    )
                ),
            )
        )
    cp.update(round=rounds + 1, history=[], repair_base=messages, errors=0)
    return True
