"""The opt-in pipeline keeps one graph and can restart a bounded section."""

import json
import re
from copy import deepcopy

import pytest
from test_graph_compiler import event, profile, source
from test_graph_generation import finish, native_report, service, tool_message

from trajectory_remix.graph import ktremix, scaling, scaling_reads, token_limits
from trajectory_remix.graph.compiler import GraphCompiler
from trajectory_remix.graph.provider import request_body


class Provider:
    def __init__(self):
        self.calls = []
        self.restarted = False
        self.was_repaired = False

    async def __call__(self, ticket):
        payload = ticket.payload
        self.calls.append(deepcopy(payload))
        body, budget = request_body(payload, ticket.trajectory_id)
        assert not token_limits.exceeds(budget)
        assert body["max_tokens"] <= 8192
        cp = payload["checkpoint"]
        if payload["operation"] == "draft":
            assert "response_format" not in body
            if "scale_draft" not in cp:
                n = int(
                    re.search(r"exactly (\d+) ordered sections", body["messages"][1]["content"])[1]
                )
                text = json.dumps(
                    dict(
                        premise="A continuous episode",
                        sections=[
                            dict(title=f"Part {i}", milestone=f"Complete phase {i}")
                            for i in range(n)
                        ],
                    )
                )
            else:
                i = len(cp["scale_draft"]["sections"])
                text = (
                    f"Source for section {i}. " * 40
                ).strip() + f"\n\n## Handoff\nSaved phase {i}."
            return {"text": text}

        session = cp["authoring_session"]
        i = cp["scale_pass"]["index"]
        assert "Long-trajectory experiment" in payload["messages"][1]["content"]
        if payload["operation"] == "review" and not session["draft_visible"]:
            assert "A continuous episode" not in payload["messages"][1]["content"]
            assert "Complete phase" not in payload["messages"][1]["content"]
        if session["scaling"]["final"]:
            graph = self.store.read(ticket.trajectory_id).source
            args = native_report(payload, [f"think-{i - 1}"])
            message = tool_message(args)
            assert graph.events["think-0"].content.startswith("An agent")
        elif payload["operation"] == "assembly":
            message = self.assemble(i, session["exchanges"])
            if i == 0 and session["exchanges"] == 1 and not self.restarted:
                self.restarted = True
                return {"context_limit": True}
        else:
            message = self.review(ticket, i)
        return {"message": message, "model": "scaled-fixture"}

    def assemble(self, i, exchanges):
        if i == 0 and self.restarted:
            assert exchanges == 0  # The previous transcript was dropped, while its edit survived.
            return tool_message(
                ("checkpoint_section", {"summary": "The first document remains open."})
            )
        calls = [
            (
                "create_event",
                dict(
                    event_id=f"doc-{i}",
                    kind="external_text",
                    window=f"doc-{i}",
                    after=[f"think-{i - 1}"] if i else [],
                    content="Observed evidence in the authored episode. " * 420,
                ),
            ),
            (
                "create_event",
                dict(
                    event_id=f"think-{i}",
                    kind="agent_text",
                    window=f"reason-{i}",
                    after=[f"doc-{i}"],
                    content="An agent decision with a deliberately unverified detail. " * 28,
                    close_window=True,
                ),
            ),
        ]
        if i:
            calls.append(("checkpoint_section", {"summary": f"Completed section {i}."}))
        return tool_message(*calls)

    def review(self, ticket, i):
        calls = []
        first_visit = i == 1 and not self.was_repaired
        if first_visit:
            graph = self.store.read(ticket.trajectory_id).source
            assert "doc-0" in graph.events
            calls.append(
                (
                    "update_event",
                    {
                        "event_id": "think-1",
                        "content": "Compare the current observation with the earlier document. "
                        * 28,
                        "after": ["doc-0", "doc-1"],
                    },
                )
            )
            self.was_repaired = True
        calls.append(
            (
                "checkpoint_section",
                dict(
                    summary="Checked section.",
                    complete=not first_visit,
                    causal_claims=[
                        dict(
                            consumer_event_id=f"think-{i}",
                            evidence_event_ids=[f"doc-{i}"] if i != 1 else ["doc-0", "doc-1"],
                        )
                    ],
                ),
            )
        )
        return tool_message(*calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("complexity", "size", "section_tokens", "sections"),
    [("small", "small", 16000, 3), ("medium", "standard", 4000, 17)],
)
async def test_scaled_run_rolls_over_and_repairs_cross_section_evidence(
    tmp_path, complexity, size, section_tokens, sections
):
    provider = Provider()
    gen, _ = service(
        tmp_path,
        fake=provider,
        fast=True,
        critical=False,
        review_include_draft=size == "small",
        scaling_experiment=True,
        scaling_section_tokens=section_tokens,
    )
    job = gen.start([{"seed": 137, "overrides": {"complexity": complexity}}], {})
    await finish(gen, job)
    attempt = gen.jobs.attempts(job)[0]
    assert attempt["outcome"] == "pass"
    graph = gen.store.read(attempt["trajectory_id"]).source
    layout = graph.metadata["scaling"]
    assert "## Handoff" not in graph.metadata["draft"]
    assert layout["size"] == size
    assert (layout["total_tokens"], layout["agent_tokens"]) == scaling.TARGETS[size]
    assert len(layout["outline"]) == sections
    assert [x["events"] for x in layout["outline"]] == [
        [f"doc-{i}", f"think-{i}"] for i in range(sections)
    ]
    assert layout["measurements"]["episode_characters"] > 50000
    assert provider.restarted and provider.was_repaired
    report = graph.metadata["evaluations"][0]
    assert report["verdict"] == "pass"
    assert len(report["items"]["core.causal_evidence"]["causal_claims"]) == sections
    assert gen.jobs.inspect(job, include_config=False)["stage_progress"] == {
        "completed": 3,
        "total": 3,
    }


def test_paged_reads_and_training_token_measurement():
    graph = source(
        {
            "doc": event("external_text", "doc", "x" * 13000),
            "think": event("agent_text", "reason", "abc", after=["doc"], close_window=True),
            "masked": event(
                "agent_text",
                "masked",
                "skip",
                after=["think"],
                close_window=True,
                supervision={"masked": True},
            ),
        }
    )
    first = scaling_reads.read(graph, None, {}, "view_event", {"event_id": "doc"})["event"][
        "content"
    ]
    second = scaling_reads.read(
        graph,
        None,
        {},
        "view_event",
        {
            "event_id": "doc",
            "content_offset": first["next_offset"],
        },
    )["event"]["content"]
    assert first["total"] == second["total"] == 13000 and len(second["items"]) == scaling_reads.TEXT
    compiler = GraphCompiler(profile())
    columns, _ = compiler.compile(graph)
    measurement = scaling.compiled_counts(graph, compiler, columns)
    assert measurement["episode_tokens"] == 13007
    assert measurement["supervised_agent_tokens"] == 3
    assert measurement["rows"] > measurement["episode_tokens"]
    raw, index = ktremix.encode_sample(graph.id, columns)
    ktremix.Sample(raw, index).validate()
