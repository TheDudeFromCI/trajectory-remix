"""Shared graph helpers: run request models, causal views and chat deliveries."""

from pydantic import Field

from .models import DTO, JsonValue


class ProposalInput(DTO):
    seed: int | None = None
    world_group_id: str | None = None
    overrides: dict[str, str] = Field(default_factory=dict)


class SamplerRun(DTO):
    requests: list[ProposalInput] = Field(min_length=1, max_length=1000)
    instructions: dict[str, JsonValue] = Field(default_factory=dict)


def causal_view(source, after, compact=False):
    """Fold only ancestors. Closed/replaced bodies never survive in a summary.

    compact drops fields left at their defaults, for prompts.
    """
    dump = (
        (lambda model: model.model_dump(mode="json", exclude_defaults=True))
        if compact
        else (lambda model: model.model_dump(mode="json"))
    )
    wanted, todo = set(), list(after)
    while todo:
        ident = todo.pop()
        if ident in wanted:
            continue
        if ident not in source.events:
            raise ValueError("Unknown causal frontier")
        wanted.add(ident)
        todo.extend(source.events[ident].after)
    ordered, pending = [], set(wanted)
    while pending:
        ready = sorted(i for i in pending if not set(source.events[i].after) & pending)
        if not ready:
            raise ValueError("Cyclic causal frontier")
        ordered.extend(ready)
        pending.difference_update(ready)
    windows = {}
    for ident in ordered:
        event = source.events[ident]
        if getattr(event, "replace_body", False):
            windows.pop(event.window, None)
        if event.close_window:
            windows.pop(event.window, None)
            continue
        window = windows.setdefault(
            event.window,
            dict(window=dump(source.windows[event.window]), events=[]),
        )
        window["events"].append(dict(id=ident, **dump(event)))
    return windows


def chat_echo(source, event_id):
    """Only the explicit synthetic chat tool. No callbacks/sandbox."""
    event = source.events[event_id]
    if event.kind != "tool_call" or event.tool != "chat" or not event.insert_tool_end:
        return None
    # Storage maps are canonically sorted, never execution order. Fold call
    # fragments by their causal chain even when their IDs sort differently.
    calls = [
        e for e in source.events.values() if e.kind == "tool_call" and e.call_id == event.call_id
    ]

    def depth(e):
        pending, seen = list(e.after), set()
        while pending:
            ident = pending.pop()
            if ident not in seen:
                seen.add(ident)
                pending.extend(source.events[ident].after)
        return len(seen)

    calls.sort(key=depth)
    if not all(e.echo_chat for e in calls):
        return None
    try:
        args = __import__("json").loads("".join(e.content for e in calls))
    except (ValueError, TypeError):
        return None  # Advisory malformed input; never manufacture a delivery.
    if not isinstance(args, dict) or not isinstance(args.get("content"), str):
        return None
    room = next(
        (w for w, d in source.windows.items() if d.purpose == "chatroom" and not d.agent_writable),
        None,
    )
    if room is None:
        return None
    after = [event_id]
    after.extend(sorted(i for i, e in source.events.items() if e.window == room))
    return dict(
        id=event_id + ":echo",
        envelope=dict(
            kind="chat_message",
            window=room,
            after=list(dict.fromkeys(after)),
            chat={"speaker": "agent"},
            call_id=event.call_id,
        ),
        instruction="",
        world={},
        arguments="text",
        when={},
        content=args["content"],
    )
