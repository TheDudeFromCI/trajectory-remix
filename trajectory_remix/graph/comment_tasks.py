"""MCP tasks for reviewer comments, so an agent can find and answer what a person flagged."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .comments import MAX_BODY, NewComment, NewReply
from .models import DTO


class CommentList(DTO):
    trajectory: str | None = None
    status: Literal["open", "addressed", "resolved", "unresolved", "all"] = "open"
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class CommentAdd(NewComment):
    trajectory: str


class CommentReply(DTO):
    id: str
    body: str = Field(min_length=1, max_length=MAX_BODY)


class CommentAddress(DTO):
    id: str
    note: str = Field(min_length=1, max_length=MAX_BODY)


TASKS = {
    "list_graph_comments": (
        CommentList,
        "List reviewer comments on trajectories, events or windows; open ones are work to do.",
    ),
    "add_graph_comment": (
        CommentAdd,
        "Leave a comment on a trajectory, event or window, for example a question for the user.",
    ),
    "reply_graph_comment": (
        CommentReply,
        "Reply in a comment's thread, for example to explain a fix or ask the user a question.",
    ),
    "address_graph_comment": (
        CommentAddress,
        "After saving a fix, mark a comment addressed with a note of what changed. The user"
        " reviews it and resolves it or requests changes; agents never resolve comments.",
    ),
}


def call(comments, name, args):
    if name == "list_graph_comments":
        status = None if args.status == "all" else args.status
        if args.trajectory is None:
            return comments.listing(status, limit=args.limit, offset=args.offset)
        items = comments.for_trajectory(args.trajectory, status)
        page = items[args.offset : args.offset + args.limit]
        more = args.offset + len(page) < len(items)
        return dict(
            items=page,
            total=len(items),
            next_offset=args.offset + len(page) if more else None,
        )
    if name == "add_graph_comment":
        return comments.add(
            args.trajectory, NewComment(target=args.target, body=args.body), "agent"
        )
    if name == "reply_graph_comment":
        return comments.reply(args.id, NewReply(body=args.body), "agent")
    return comments.address(args.id, args.note)
