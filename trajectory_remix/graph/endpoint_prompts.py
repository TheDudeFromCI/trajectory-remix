"""Message layouts for the five endpoints: Draft, Assembly, Fast/Critical Review, Assistant.

Every system prompt starts with the project's agent prompt. Sessions that use the
authoring tools (Assembly, review, repair, Assistant) add the authoring prompt after it,
so the system prompt is the same for every request of a run and caches well. The task
and its data go in the first user message.
"""

import json
import re

from .models import canonical

REVIEW = (
    "Please review the trajectory to ensure it meets the qualifications in this checklist,"
    " and call `signoff` when you're done."
)
REPAIR = (
    "An earlier review found problems with this trajectory. Please repair any issues so it"
    " meets the qualifications in this checklist, and call `signoff` when you're done."
)
ASSIST = "Please address the following issues:"
ASSIST_AFTER = (
    "After addressing the listed issues, reply to the comments using the `reply` tool."
    " Further user comments will be appended to this conversation."
)
FOLLOW_UP = (
    "The user sent more comments. The trajectory may have changed since your last turn,"
    " so view it again before editing."
)


def fence(text, lang=""):
    """A code fence longer than any backtick run inside the text."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}\n{text}\n{ticks}"


def _json(value):
    return fence(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False), "json")


def system(actor, authoring=None):
    """The agent prompt first; the authoring prompt after it for tool sessions."""
    return actor if authoring is None else f"{actor}\n\n{authoring}"


def _identity(trajectory_id):
    return f"Trajectory Id: `{trajectory_id}`"


def draft_user(instruction, proposal, tools, correction=""):
    parts = [
        instruction,
        "Sampled proposal:\n" + fence(canonical(proposal), "json"),
        "Project tools:\n" + fence(canonical(tools), "json"),
    ]
    if correction:
        parts.append("Correction for your previous reply:\n" + correction)
    return "\n\n".join(parts)


def assembly_user(instruction, trajectory_id, draft, tools):
    return "\n\n".join(
        [
            instruction,
            _identity(trajectory_id),
            "Project tools:\n" + fence(canonical(tools), "json"),
            "Draft:\n" + fence(draft),
        ]
    )


def checklist(criteria):
    """One line per criterion: its ID, text and whether it may be not applicable."""
    return "\n".join(
        f"- `{key}`: {item['text']}"
        + (" (may be not applicable)" if item.get("allow_not_applicable") else "")
        for key, item in criteria.items()
    )


def review_user(criteria, trajectory_id, *, repair=False, context=None):
    """Review or repair. context holds optional objective, targets, Draft, failed review
    and, for repair, the project tools."""
    context = context or {}
    parts = [REPAIR if repair else REVIEW, "Review Checklist:\n" + checklist(criteria)]
    if context.get("objective"):
        parts.append(f"Objective: {context['objective']}")
    if context.get("targets"):
        parts.append("Sampled targets (advisory):\n" + _json(context["targets"]))
    if context.get("draft"):
        parts.append("Draft:\n" + fence(context["draft"]))
    if context.get("failed_review"):
        parts.append("Failed review:\n" + _json(context["failed_review"]))
    if context.get("tools"):
        parts.append("Project tools:\n" + fence(canonical(context["tools"]), "json"))
    parts.append(_identity(trajectory_id))
    return "\n\n".join(parts)


def _cell(text):
    return text.replace("|", "\\|").replace("\r", "").replace("\n", "<br>").strip() or " "


def _target(target):
    if target["kind"] == "trajectory":
        return "trajectory"
    return f"{target['kind']} `{target['id']}`"


def comment_table(comments):
    """A Markdown table of the comments, with their threads."""
    rows = ["| ID | Target | Comment | Thread |", "| --- | --- | --- | --- |"]
    for comment in comments:
        thread = "<br>".join(
            f"{reply['author']}: {_cell(reply['body'])}" for reply in comment["replies"]
        )
        rows.append(
            f"| `{comment['id']}` | {_target(comment['target'])} | {_cell(comment['body'])}"
            f" | {thread or '-'} |"
        )
    return "\n".join(rows)


def assistant_user(comments, trajectory_id, *, follow_up=False):
    parts = [FOLLOW_UP] if follow_up else []
    parts += [ASSIST, comment_table(comments), _identity(trajectory_id), ASSIST_AFTER]
    return "\n\n".join(parts)
