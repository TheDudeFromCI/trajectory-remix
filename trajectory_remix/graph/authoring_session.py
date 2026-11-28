"""One native tool conversation per Assembly or review pass.

Every pass can edit the trajectory. It ends with signoff: a passing verdict, or unfeasible
with a reason. A signoff with failing criteria is refused, so the model keeps fixing.
"""

import json

from . import (
    authoring,
    draft_assembly,
    endpoint_prompts,
    prompt_defaults,
    reviews,
    scaling_reads,
    signoffs,
)
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


def _opening(source, settings, phase, checks, plan, draft, *, segmented):
    """(prompt version, system prompt, first user message) for a native pass."""
    if phase == "assembly":
        system = endpoint_prompts.system(
            plan["actor_instruction"], draft_assembly.authoring_instruction(plan)
        )
        tools = plan["tools"]
        if segmented:
            tools = {key: {"name": value["name"]} for key, value in tools.items()}
        user = endpoint_prompts.assembly_user(
            plan["prompts"]["assemble_instruction"], source.id, draft, tools
        )
        return plan["prompts"]["version"], system, user
    system = endpoint_prompts.system(settings.prompts.actor, settings.prompts.authoring)
    context = _review_context(source, settings, draft)
    if segmented:
        context["tools"] = {key: {"name": value["name"]} for key, value in context["tools"].items()}
    user = endpoint_prompts.review_user(checks, source.id, context)
    return prompt_defaults.packaged()["review_version"], system, user


def begin(source, settings, phase, tier, *, plan=None, segmented=False):
    draft = source.metadata.get("draft")
    draft_visible = (
        isinstance(draft, str)
        and bool(draft)
        and (phase == "assembly" or settings.generation.review_include_draft)
    )
    checks = signoffs.criteria(reviews.checklist(settings, tier) if tier else {}, draft_visible)
    prompt_version, system, user = _opening(
        source,
        settings,
        phase,
        checks,
        plan,
        draft if draft_visible and not segmented else "",
        segmented=segmented,
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
    request = dict(
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
    if segment := session.get("scaling"):
        request["tools"] = scaling_reads.tools(request["tools"], checkpoint=not segment["final"])
    return request


def _checkpoint(source, settings, session, args):
    report = scaling_reads.Checkpoint.model_validate(args)
    segment = session["scaling"]
    if not report.complete and segment["continuations"] >= 2:
        raise ValueError("Finish this section before requesting another partial checkpoint")
    errors = [d for d in authoring.findings(source, settings) if d["severity"] == "error"]
    if errors:
        raise ValueError(f"Fix structural errors before checkpointing: {errors[0]['message']}")
    if session["phase"] == "assembly" and not set(source.events).difference(
        segment["starting_events"]
    ):
        raise ValueError("Assemble the section's events before checkpointing")
    if (
        not report.complete
        and session["phase"] == "assembly"
        and source.intrinsic_hash == segment["starting_hash"]
    ):
        raise ValueError("Save progress before requesting a partial Assembly checkpoint")
    if report.causal_claims:
        signoffs._validate_causal_claims(source, report.causal_claims)
    return report.model_dump(mode="json")


def _merge_claims(source, session, args):
    items = args.get("items")
    if not isinstance(items, dict):
        return
    item = items.get(signoffs.CAUSAL_EVIDENCE)
    if not isinstance(item, dict) or item.get("status") != "pass":
        return
    prior = {
        claim["consumer_event_id"]: claim
        for claim in session["scaling"]["claims"]
        if claim["consumer_event_id"] in source.events
    }
    if prior:
        new = item.get("causal_claims", [])
        if not isinstance(new, list):
            return
        for claim in new:
            parsed = scaling_reads.CausalClaim.model_validate(claim)
            prior[parsed.consumer_event_id] = claim
        item["causal_claims"] = list(prior.values())


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
                        diagnostics=(
                            scaling_reads.page(authoring.findings(source, settings), 0)
                            if session.get("scaling")
                            else authoring.findings(source, settings)
                        ),
                    )
                ),
            )
        )
        return source, None
    terminal = None
    correction = None
    rejected = False
    segment = session.get("scaling")
    for call in calls:
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"]["arguments"])
            if segment and name in scaling_reads.READS:
                reply = scaling_reads.read(source, settings, session, name, args)
            elif segment and name == "checkpoint_section" and not segment["final"]:
                if call is not calls[-1] or rejected:
                    raise ValueError("Checkpoint after the other tools have succeeded")
                terminal = ("section", _checkpoint(source, settings, session, args))
                reply = {"outcome": "section"}
            elif name == "signoff":
                if segment and not segment["final"]:
                    raise ValueError("Checkpoint this section before the final signoff pass")
                if call is not calls[-1] or (
                    rejected and isinstance(args, dict) and args.get("status") != "unfeasible"
                ):
                    raise ValueError("Sign off after the other tools have succeeded")
                if segment and isinstance(args, dict):
                    _merge_claims(source, session, args)
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
            elif name in TOOLS:
                source, reply = apply_tool(source, settings, name, args)
                if segment and "diagnostics" in reply:
                    reply["diagnostics"] = scaling_reads.page(reply["diagnostics"], 0)
            else:
                raise ValueError("This tool is unavailable in the current pass")
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
                    next=(
                        "Complete the section and call checkpoint_section."
                        if segment and not segment["final"]
                        else "Correct the evidence or edit the source, then call signoff again."
                    ),
                    diagnostics=authoring.findings(source, settings),
                )
        messages.append(tool_reply(call["id"], reply))
    if correction and terminal is None:
        if segment:
            correction["diagnostics"] = scaling_reads.page(correction["diagnostics"], 0)
        messages.append(dict(role="user", content=canonical(correction)))
    return source, terminal
