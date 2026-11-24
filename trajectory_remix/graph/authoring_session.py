"""One native tool conversation per Assembly or review pass.

Every pass can edit the trajectory. It ends with signoff: a passing verdict, or unfeasible
with a reason. A signoff with failing criteria is refused, so the model keeps fixing.
"""

import json

from . import authoring, draft_assembly, endpoint_prompts, prompt_defaults, reviews, signoffs
from .authoring_tools import READ_ONLY, TOOLS, provider_tools
from .models import canonical
from .schema_errors import explain


def _review_context(source, settings, draft):
    proposal = source.metadata.get("proposal")
    objective = None
    if isinstance(proposal, dict) and isinstance(proposal.get("proposal"), dict):
        objective = proposal["proposal"].get("learning_objective")
    return dict(
        objective=objective,
        targets=source.metadata.get("targets"),
        draft=draft,
        tools={k: v.model_dump(mode="json") for k, v in settings.tools.items()},
    )


def _opening(source, settings, phase, checks, plan, draft):
    """(prompt version, system prompt, first user message) for a native pass."""
    if phase == "assembly":
        system = endpoint_prompts.system(
            plan["actor_instruction"], draft_assembly.authoring_instruction(plan)
        )
        user = endpoint_prompts.assembly_user(
            plan["prompts"]["assemble_instruction"], source.id, draft, plan["tools"]
        )
        return plan["prompts"]["version"], system, user
    system = endpoint_prompts.system(settings.prompts.actor, settings.prompts.authoring)
    user = endpoint_prompts.review_user(checks, source.id, _review_context(source, settings, draft))
    return prompt_defaults.packaged()["review_version"], system, user


def begin(source, settings, phase, tier, *, plan=None):
    draft = source.metadata.get("draft")
    draft_visible = (
        isinstance(draft, str)
        and bool(draft)
        and (phase == "assembly" or settings.generation.review_include_draft)
    )
    checks = signoffs.criteria(reviews.checklist(settings, tier) if tier else {}, draft_visible)
    prompt_version, system, user = _opening(
        source, settings, phase, checks, plan, draft if draft_visible else None
    )
    return dict(
        phase=phase,
        tier=tier,
        prompt_version=prompt_version,
        draft_visible=draft_visible,
        checks=checks,
        require_causal_claims=True,
        exchanges=0,
        messages=[dict(role="system", content=system), dict(role="user", content=user)],
    )


def payload(session):
    return dict(
        operation=session["phase"],
        tier=session["tier"],
        prompt_version=session.get("prompt_version", signoffs.VERSION),
        messages=session["messages"],
        tools=provider_tools(
            signoff_parameters=signoffs.parameters(
                session["checks"], require_causal_claims=session.get("require_causal_claims", False)
            ),
        ),
    )


def apply_tool(source, settings, name, args):
    """Run one edit or read tool. Returns (source, reply); raises ValueError on misuse."""
    problem = explain(TOOLS[name][0].model_json_schema(), args)
    if problem:
        raise ValueError(problem)
    source, reply = authoring.apply(source, settings, name, args)
    if name not in READ_ONLY:
        reply["diagnostics"] = authoring.findings(source, settings)
    return source, reply


def tool_reply(call_id, data):
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
    terminal = None
    correction = None
    rejected = False
    for call in calls:
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"]["arguments"])
            if name not in TOOLS:
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
                if result == "fail":
                    raise ValueError("Continue fixing failing criteria or sign off as unfeasible")
                terminal = (result, evidence)
                reply = {"outcome": result}
            else:
                source, reply = apply_tool(source, settings, name, args)
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
        messages.append(tool_reply(call["id"], reply))
    if correction and terminal is None:
        messages.append(dict(role="user", content=canonical(correction)))
    return source, terminal
