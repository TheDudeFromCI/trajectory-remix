"""Assistant sessions: a model works through a trajectory's open reviewer comments.

"Send open comments to agent" freezes the open comments into a one-trajectory job on the
Assistant endpoint role. The session is a native tool conversation: the authoring tools
edit the graph, and `reply` answers each listed comment exactly once. It finishes when
every comment has a reply. Then fixed comments become addressed, with the note in the
thread, for a person to accept or send back; the rest get the note as a reply and stay
open. The model never resolves a comment. No stage, review or checklist changes; edits
simply make an earlier review stale.

A finished conversation is kept (assistant_memory), and the next send appends the new
comments to it, so the model keeps what it learned.
"""

from __future__ import annotations

import json

from . import endpoint_prompts
from .authoring_session import apply_tool, tool_reply
from .authoring_tools import TOOLS, editing_tools
from .models import canonical
from .schema_errors import explain

PHASE = "assist"
ROLE = "assistant"
ACTION = "assistant"
VERSION = "graph-assistant/3"
NOTE_LIMIT = 10_000


def frozen(comments):
    """The open comments a job works on, frozen at admission, with their threads."""
    return [
        dict(
            id=c["id"],
            target=dict(kind=c["target_kind"], id=c["target_id"]),
            body=c["body"],
            author=c["author"],
            replies=[dict(author=r["author"], body=r["body"]) for r in c.get("replies", [])],
        )
        for c in comments
    ]


def reply_parameters(comment_ids):
    return dict(
        type="object",
        properties=dict(
            comment_id=dict(type="string", enum=list(comment_ids)),
            done=dict(
                type="boolean",
                description="True when you fixed what the comment asks; false to explain or ask.",
            ),
            note=dict(type="string", minLength=1, maxLength=NOTE_LIMIT),
        ),
        required=["comment_id", "done", "note"],
        additionalProperties=False,
    )


def reply_tool(comment_ids):
    return dict(
        type="function",
        function=dict(
            name="reply",
            description=(
                "Answer one listed comment, once, after making your edits. done true marks it"
                " addressed with your note for a person to review; done false adds the note as"
                " a reply and leaves the comment open."
            ),
            parameters=reply_parameters(comment_ids),
        ),
    )


def begin(source, settings, comments, prior=None):
    """A new session, or the prior conversation with the new comments appended."""
    user = endpoint_prompts.assistant_user(comments, source.id, follow_up=bool(prior))
    if prior:
        messages = [*prior, dict(role="user", content=user)]
    else:
        system = endpoint_prompts.system(settings.prompts.actor, settings.prompts.authoring)
        messages = [dict(role="system", content=system), dict(role="user", content=user)]
    return dict(
        phase=PHASE,
        tier=ROLE,
        prompt_version=VERSION,
        exchanges=0,
        comment_ids=[c["id"] for c in comments],
        replies={},
        messages=messages,
    )


def payload(session):
    return dict(
        operation=PHASE,
        tier=ROLE,
        prompt_version=session["prompt_version"],
        messages=session["messages"],
        tools=[*editing_tools(), reply_tool(session["comment_ids"])],
    )


def remaining(session):
    return [ident for ident in session["comment_ids"] if ident not in session["replies"]]


def _record(session, args):
    problem = explain(reply_parameters(session["comment_ids"]), args)
    if problem:
        raise ValueError(problem)
    if args["comment_id"] in session["replies"]:
        raise ValueError("You already replied to this comment")
    session["replies"][args["comment_id"]] = dict(done=args["done"], note=args["note"])
    return dict(remaining=remaining(session))


def _call(source, settings, session, call):
    name = call["function"]["name"]
    try:
        args = json.loads(call["function"]["arguments"])
        if name == "reply":
            reply = _record(session, args)
        elif name in TOOLS and name != "signoff":
            source, reply = apply_tool(source, settings, name, args)
        else:
            raise ValueError("This tool is unavailable in this session")
        reply.update(accepted=True, source_hash=source.intrinsic_hash)
    except (ValueError, KeyError, TypeError, RecursionError) as error:
        reply = dict(accepted=False, error=str(error)[:700], source_hash=source.intrinsic_hash)
    session["messages"].append(tool_reply(call["id"], reply))
    return source


def step(source, settings, session, message):
    """Apply one provider reply. Returns (source, finished)."""
    session["exchanges"] += 1
    session["messages"].append(message)
    calls = message.get("tool_calls", [])
    for call in calls:
        source = _call(source, settings, session, call)
    if remaining(session) and not calls:
        nudge = dict(next="Reply to every listed comment with the reply tool.")
        nudge["remaining"] = remaining(session)
        session["messages"].append(dict(role="user", content=canonical(nudge)))
    return source, not remaining(session)


def apply(comments, replies):
    """Mark fixed comments addressed and reply to the rest; ones deleted meanwhile are
    skipped. Returns how many were addressed."""
    from .comments import NewReply

    addressed = 0
    for ident, answer in replies.items():
        try:
            if answer["done"]:
                comments.address(ident, answer["note"])
                addressed += 1
            else:
                comments.reply(ident, NewReply(body=answer["note"]), "agent")
        except KeyError:
            continue
    return addressed
