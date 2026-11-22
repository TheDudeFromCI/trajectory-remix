"""REST routes for reviewer comments; the dashboard writes them as the user."""

from fastapi import Query

from .comments import CommentEdit, CommentService, NewComment, Status


def add_comment_routes(router, comments: CommentService):
    @router.get("/trajectories/{ident}/comments")
    def trajectory_comments(ident: str, status: Status | None = None):
        return dict(items=comments.for_trajectory(ident, status))

    @router.post("/trajectories/{ident}/comments", status_code=201)
    def add_comment(ident: str, body: NewComment):
        return comments.add(ident, body, author="user")

    @router.get("/comments")
    def comment_listing(
        status: Status | None = "open",
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        return comments.listing(status, limit=limit, offset=offset)

    @router.get("/comments/open-counts")
    def open_counts():
        return dict(counts=comments.open_counts())

    @router.patch("/comments/{ident}")
    def edit_comment(ident: str, body: CommentEdit):
        return comments.edit(ident, body)

    @router.delete("/comments/{ident}")
    def delete_comment(ident: str):
        return comments.delete(ident)
