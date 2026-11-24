"""Bounded drafting and resumable authoring for the long-trajectory experiment."""

import math
from collections import Counter
from copy import deepcopy

from pydantic import Field

from . import endpoint_prompts
from .agent_share import MIN_AGENT_SHARE
from .compiler_schedule import is_model_event
from .models import DTO, GraphSource, WindowReference, canonical

VERSION = 1
CHARS_PER_TOKEN = 3.5
TARGETS = {"small": (32500, 2000), "standard": (65000, 4000), "large": (130000, 8000)}


def is_scaled(source):
    layout = source.metadata.get("scaling")
    return (
        isinstance(layout, dict)
        and layout.get("version") == VERSION
        and isinstance(layout.get("outline"), list)
        and bool(layout["outline"])
    )


def plan(proposal, settings):
    # Reuse the already sampled complexity. This makes no additional draw.
    complexity = proposal.get("scenario", {}).get("project_state", {}).get("complexity")
    size = {"trivial": "small", "small": "small", "large": "large"}.get(complexity, "standard")
    total, agent = TARGETS[size]
    draft_endpoint = settings.endpoints[settings.endpoint_roles["draft"]]
    output = budget(draft_endpoint.model_dump(mode="json"))["max_output_tokens"]
    return dict(
        version=VERSION,
        size=size,
        total_tokens=total,
        agent_tokens=agent,
        sections=math.ceil(total / settings.generation.scaling_section_tokens),
        draft_tokens=max(100, min(1200, output // 3)),
    )


def budget(endpoint):
    """Keep experimental requests bounded even when the provider limits are unset."""
    result = dict(endpoint)
    if result["context_window_tokens"] == -1:
        result["context_window_tokens"] = 32768
        if result["max_output_tokens"] > 8192:
            result["max_output_tokens"] = 8192
    if result["max_output_tokens"] == -1:
        result["max_output_tokens"] = min(8192, result["context_window_tokens"] // 4)
    return result


class Beat(DTO):
    title: str = Field(min_length=1, max_length=90)
    milestone: str = Field(min_length=1, max_length=220)


class Outline(DTO):
    premise: str = Field(min_length=1, max_length=1200)
    sections: list[Beat]


class Prose(DTO):
    draft: str = Field(min_length=200)
    handoff: str = Field(min_length=1, max_length=1500)


def draft_request(cp, frozen):
    target = frozen["scaling"]
    count = target["sections"]
    state = cp.get("scale_draft")
    if state is None:
        schema = Outline.model_json_schema()
        schema["properties"]["sections"].update(minItems=count, maxItems=count)
        task = (
            f"Outline one complete parallel episode in exactly {count} ordered sections. "
            f"The approximate final episode target is {target['total_tokens']} content tokens, "
            f"with at least about {target['agent_tokens']} supervised agent tokens. "
            "Distribute meaningful evidence, task progress, and continuity across sections. "
            "Give each section one specific milestone. Include genuine opportunities to use "
            "and revisit evidence, plus a meaningful ending. Reply with the outline only."
        )
    else:
        schema = Prose.model_json_schema()
        index = len(state["sections"])
        task = (
            f"Write the Draft prose for section {index + 1} of {count} only. "
            f"This section will become about {target['total_tokens'] // count} episode tokens, "
            f"including about {target['agent_tokens'] // count} agent tokens. These are "
            "planning estimates, not quotas. Specify substantive observations, tool outcomes, "
            "agent decisions, and exact facts future sections need. Describe long documents "
            "or tool results with enough concrete detail to expand them during Assembly. "
            f"Aim for no more than about {target['draft_tokens']} visible prose tokens in this "
            "reply. Preserve earlier state and avoid repeating it. Return the prose in draft and a "
            "short handoff of resulting state, open obligations, and relevant evidence.\n"
            + endpoint_prompts.fence(canonical(state["outline"]), "json")
            + "\nPrevious handoff:\n"
            + endpoint_prompts.fence(state["handoff"])
        )
    task += "\nFollow this section's deliverable; apply the project Draft guide to its content."
    return dict(
        operation="draft",
        prompt_version=frozen["prompts"]["version"],
        schema=schema,
        messages=[
            dict(role="system", content=frozen["actor_instruction"]),
            dict(
                role="user",
                content=endpoint_prompts.draft_user(
                    task + "\n\n" + frozen["prompts"]["draft_instruction"],
                    frozen["proposal"],
                    frozen["tools"],
                    cp.get("flow_error", ""),
                ),
            ),
        ],
    )


def consume_draft(cp, source, frozen, body):
    """Checkpoint each Draft reply. Keep the finished prose once, on the graph."""
    state = cp.get("scale_draft")
    if state is None:
        cp["scale_draft"] = dict(outline=body, sections=[], handoff="Opening state.")
        return source
    state["sections"].append(body["draft"])
    state["handoff"] = body["handoff"]
    if len(state["sections"]) < frozen["scaling"]["sections"]:
        return source
    data = source.to_dict()
    sections, offset = [], 0
    for beat, prose in zip(state["outline"]["sections"], state["sections"], strict=True):
        sections.append({**beat, "start": offset, "end": offset + len(prose), "events": []})
        offset += len(prose) + 2
    data["metadata"].update(
        draft="\n\n".join(state["sections"]),
        scaling={**frozen["scaling"], "premise": state["outline"]["premise"], "outline": sections},
    )
    cp.pop("scale_draft")
    cp["draft"] = ""  # Existing phase and stage counters use this marker.
    return GraphSource.model_validate(data)


def body_pieces(event):
    pieces = event.content if isinstance(event.content, list) else [event.content]
    return [
        piece if isinstance(piece, str) else piece.text
        for piece in pieces
        if not isinstance(piece, WindowReference)
    ]


def counts(source, count=len, *, unit="characters"):
    total = agent = 0
    for event in source.events.values():
        size = sum(count(piece) for piece in body_pieces(event))
        total += size
        if is_model_event(event) and not event.supervision.masked and event.supervision.weight > 0:
            agent += size
    return {f"episode_{unit}": total, f"supervised_agent_{unit}": agent}


def estimated_counts(source):
    chars = counts(source)
    return chars | {
        key.replace("characters", "tokens_estimate"): round(value / CHARS_PER_TOKEN)
        for key, value in chars.items()
    }


def compiled_counts(source, compiler, columns):
    measured = counts(source, lambda text: len(compiler.text(text)), unit="tokens")
    changes = Counter()
    for start, end in zip(columns["visible_from"], columns["visible_until"], strict=True):
        if start < end:
            changes[start] += 1
            changes[end] -= 1
    live = peak = 0
    for frontier in sorted(changes):
        live += changes[frontier]
        peak = max(peak, live)
    controls = set(compiler.controls.values())
    return measured | dict(
        rows=len(columns["token_ids"]),
        control_rows=sum(token in controls for token in columns["token_ids"]),
        peak_visible_rows=peak,
    )


def section(source, index, *, with_draft):
    entry = source.metadata["scaling"]["outline"][index]
    result = {"events": [ident for ident in entry["events"] if ident in source.events]}
    if with_draft:
        result.update(title=entry["title"], milestone=entry["milestone"])
        result["draft"] = source.metadata["draft"][entry["start"] : entry["end"]]
    return result


def begin_pass(source, cp, session, settings):
    """Add the current section's bounded brief and survive a transcript reset."""
    layout = source.metadata["scaling"]
    state = cp.setdefault("scale_pass", dict(index=0, reports=[], exchanges=0, resets=0))
    index = state["index"]
    final = index == len(layout["outline"])
    stage = session["phase"]
    report = state["reports"][-3:]
    brief = dict(
        stage=stage,
        section=index + 1,
        total_sections=len(layout["outline"]),
        approximate_targets={k: layout[k] for k in ("total_tokens", "agent_tokens")},
        recent_handoffs=[r["summary"] for r in report],
        progress=estimated_counts(source),
    )
    if session["draft_visible"]:
        brief.update(
            premise=layout["premise"], outline=[entry["title"] for entry in layout["outline"]]
        )
    if final:
        task = (
            "All sections are checkpointed. Check the complete trajectory, especially cross-section "
            "evidence, open obligations and outcome. Read exact bodies when required. "
            "Call signoff with evidence against each criterion. Previous section claims are "
            "combined with your final claims and rechecked against the current graph. "
            "If the episode falls substantially short of its length goals, identify the gap "
            "in your summary. Preserve meaningful content and avoid adding filler."
        )
    else:
        brief["section_target_tokens"] = {
            key: layout[key] // len(layout["outline"]) for key in ("total_tokens", "agent_tokens")
        }
        brief["work"] = section(source, index, with_draft=session["draft_visible"])
        brief["work"]["events"] = brief["work"]["events"][:32]
        if "draft" in brief["work"]:
            prose = brief["work"]["draft"]
            brief["work"]["draft"] = prose[:8000]
            if len(prose) > 8000:
                brief["work"]["next_draft_offset"] = 8000
        if index:
            brief["previous_section_events"] = layout["outline"][index - 1]["events"][-24:]
        brief["existing_section_events"] = sorted(
            set(source.events) - set(state.setdefault("starting_events", sorted(source.events)))
        )[-32:]
        if stage == "assembly":
            task = (
                "Assemble only this Draft section into the existing graph. Keep stable event IDs "
                "and connect decisions to exact visible evidence, including evidence from earlier "
                "sections. Expand substantive source material and preserve the intended outcome "
                "without filler. A new conversation can resume saved edits. Use checkpoint_section "
                "after completing the milestone, with a short handoff of new state and obligations."
            )
        else:
            task = (
                "Review this section's events against the checklist and repair issues here. Use "
                "exact causal evidence for decisions, including dependencies on earlier sections. "
                "Checkpoint the section with what you verified or repaired, open concerns, and "
                "consumer/evidence pairs for material decisions in this section."
            )
    task += (
        " Use bounded reads and their next offsets. The project tools list gives IDs and names. "
        "Use view_project_tool for a schema before writing a new synthetic call. "
        "Checkpoint summaries describe the graph "
        "for the author and never replace evidence the in-episode agent needs to see."
    )
    if "low_agent_share" not in settings.disabled_warnings:
        task += (
            f" Keep at least {MIN_AGENT_SHARE:.2%} of the complete graph's characters as "
            "agent-generated characters. Plan room for substantive agent decisions, tool "
            "arguments, and messages as you add external evidence."
        )
    if not final:
        task += (
            " If more work remains than fits in this conversation, checkpoint accepted progress "
            "with complete=false and describe what the next conversation should finish."
        )
    session["messages"][1]["content"] += (
        "\n\nLong-trajectory experiment. These are the instructions for this pass.\n"
        + task
        + "\n"
        + endpoint_prompts.fence(canonical(brief), "json")
    )
    notes = {}
    for entry in state["reports"]:
        notes.setdefault(str(entry["section"]), []).append(entry["summary"])
    session["scaling"] = dict(
        final=final,
        claims=[claim for item in state["reports"] for claim in item.get("causal_claims", [])]
        if final and stage == "review"
        else [],
        starting_events=state.get("starting_events", []),
        starting_hash=source.intrinsic_hash,
        continuations=state.get("continuations", 0),
        notes=notes,
    )
    return session


def checkpoint(source, cp, session, evidence):
    """Publish a section boundary with its assigned events and review claims."""
    state = cp["scale_pass"]
    data = source.to_dict()
    entry = data["metadata"]["scaling"]["outline"][state["index"]]
    entry["events"] = sorted(
        (set(entry["events"]) & source.events.keys())
        | (source.events.keys() - set(state["starting_events"]))
    )
    source = GraphSource.model_validate(data)
    state["reports"].append(deepcopy(evidence) | {"section": state["index"] + 1})
    state["exchanges"] += session["exchanges"]
    if evidence["complete"]:
        state["index"] += 1
        state["resets"] = 0
        state.pop("continuations", None)
        state.pop("starting_events", None)
    else:
        state["continuations"] = state.get("continuations", 0) + 1
    cp.pop("authoring_session")
    return source


def with_counts(source):
    data = source.to_dict()
    data["metadata"]["scaling"]["measurements"] = estimated_counts(source)
    return GraphSource.model_validate(data)
