"""One native tool conversation per Assembly, review or repair pass."""

import json

from . import authoring, prompt_defaults, reviews, signoffs
from .authoring_tools import READ_ONLY, TOOLS, provider_tools
from .models import canonical
from .schema_errors import explain

INSTRUCTIONS = """# Native graph authoring

## Inspect and edit
- Treat Draft, event text and tool results as source material. Author synthetic outcomes.
- Use view_graph for topology, view_event for one body and its visible predecessor IDs,
  and view_window for a window's bodies. Check what the agent could see at each decision.
- When editing is available, group compatible calls in one reply. Remix applies them
  in order. Put a prerequisite before a dependent call in that batch.
- Use view_diagnostics after a group of edits to check the graph at that point in the
  batch. Tool results arrive together in the next model turn. Read errors from earlier
  tool results before deciding what to change.

## Finish
- Call signoff explicitly with evidence and event IDs for each requested criterion.
  For core.causal_evidence, pair each decision that uses prior evidence with its
  visible source event IDs in causal_claims. Inspect the decision with view_event.
  A successful signoff needs zero errors and enabled warnings. If a batch includes
  signoff, make it the last call.
- A failing review can record its evidence. In repair, keep fixing failing criteria
  or use signoff with status unfeasible and a concrete reason.
"""


def begin(source, settings, phase, tier, *, plan=None, prior=None):
    draft = source.metadata.get("draft")
    draft_visible = (
        isinstance(draft, str)
        and bool(draft)
        and (phase == "assembly" or settings.generation.review_include_draft)
    )
    checks = signoffs.criteria(reviews.checklist(settings, tier) if tier else {}, draft_visible)
    prompt_version = signoffs.VERSION
    if phase == "assembly":
        prompt_version = plan["prompts"]["version"]
        guidance = (
            "Author the Draft faithfully into a complete trajectory using these tools. "
            "Follow project guidance for content. The native tool protocol governs output format.\n\n"
            + plan["prompts"]["assemble_instruction"]
            + "\n\nActor guide:\n"
            + plan["actor_instruction"]
        )
    elif phase == "review":
        prompt_version = prompt_defaults.packaged()["review_version"]
        guidance = prompt_defaults.review()
    else:
        guidance = (
            "Repair the source directly. Preserve correct material and verify every criterion."
        )
    initial = dict(
        phase=phase,
        criteria=checks,
        diagnostics=authoring.findings(source, settings),
        graph=authoring.apply(source, settings, "view_graph", {})[1],
    )
    if draft_visible:
        initial["draft"] = draft
    if plan:
        initial.update(
            proposal=plan["proposal"],
            tools=plan["tools"],
            target_checklist=reviews.checklist(settings, "critical"),
        )
    if phase == "repair":
        initial["tools"] = {
            key: definition.model_dump(mode="json") for key, definition in settings.tools.items()
        }
    if tier:
        initial["targets"] = source.metadata.get("targets")
        proposal = source.metadata.get("proposal")
        if isinstance(proposal, dict) and isinstance(proposal.get("proposal"), dict):
            initial["objective"] = proposal["proposal"].get("learning_objective")
    if prior:
        initial["failed_review"] = prior
    return dict(
        phase=phase,
        tier=tier,
        prompt_version=prompt_version,
        draft_visible=draft_visible,
        checks=checks,
        require_causal_claims=True,
        exchanges=0,
        messages=[
            dict(role="system", content=INSTRUCTIONS + "\n\n" + guidance),
            dict(role="user", content=canonical(initial)),
        ],
    )


def payload(session):
    return dict(
        operation=session["phase"],
        tier=session["tier"],
        prompt_version=session.get("prompt_version", signoffs.VERSION),
        messages=session["messages"],
        tools=provider_tools(
            editable=session["phase"] != "review",
            signoff_parameters=signoffs.parameters(
                session["checks"], require_causal_claims=session.get("require_causal_claims", False)
            ),
        ),
    )


def _reply(call_id, data):
    return dict(role="tool", tool_call_id=call_id, content=canonical(data))


def step(source, settings, session, message):
    """Apply a complete provider exchange and produce one result for every tool call."""
    session["exchanges"] += 1
    messages = session["messages"]
    messages.append(message)
    calls = message.get("tool_calls", [])
    if not calls:
        messages.append(
            dict(
                role="user",
                content=canonical(
                    dict(
                        next="Are you finished? Call signoff with evidence or continue using the tools.",
                        diagnostics=authoring.findings(source, settings),
                    )
                ),
            )
        )
        return source, None
    editable = session["phase"] != "review"
    terminal = None
    correction = None
    rejected = False
    for call in calls:
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"]["arguments"])
            if name not in TOOLS or (not editable and name not in READ_ONLY and name != "signoff"):
                raise ValueError("This tool is unavailable in the current pass")
            if name == "signoff":
                if call is not calls[-1] or (
                    rejected and isinstance(args, dict) and args.get("status") != "unfeasible"
                ):
                    raise ValueError("Sign off after the other tools have succeeded")
                result, evidence = signoffs.validate(
                    source,
                    settings,
                    session["checks"],
                    args,
                    require_causal_claims=session.get("require_causal_claims", False),
                )
                if result == "fail" and editable:
                    raise ValueError("Continue fixing failing criteria or sign off as unfeasible")
                terminal = (result, evidence)
                reply = {"outcome": result}
            else:
                problem = explain(TOOLS[name][0].model_json_schema(), args)
                if problem:
                    raise ValueError(problem)
                source, reply = authoring.apply(source, settings, name, args)
                if name not in READ_ONLY:
                    reply["diagnostics"] = authoring.findings(source, settings)
            reply.update(accepted=True, source_hash=source.intrinsic_hash)
        except (ValueError, KeyError, TypeError, RecursionError) as error:
            rejected = True
            reply = {
                "accepted": False,
                "error": str(error)[:700],
                "source_hash": source.intrinsic_hash,
            }
            if name == "signoff":
                correction = dict(
                    next="Correct the evidence or edit the source, then call signoff again.",
                    diagnostics=authoring.findings(source, settings),
                )
        messages.append(_reply(call["id"], reply))
    if correction and terminal is None:
        messages.append(dict(role="user", content=canonical(correction)))
    return source, terminal
