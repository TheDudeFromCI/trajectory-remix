"""Send to agent: the Assistant reviews a trajectory's metrics and records annotations."""

import pytest
from test_assistant import edit, flagged, reply, results, run, service, turn

from trajectory_remix.graph import assistant_metrics
from trajectory_remix.graph.metrics import MetricService
from trajectory_remix.graph.sampling import SamplerService


def metric(key, value):
    return ("set_metric", {"key": key, "value": value})


def stored(gen, record):
    return gen.store.read(record.source.id)


def panel(gen, record):
    """The Metrics panel's view of the stored trajectory."""
    service = MetricService(SamplerService(gen.jobs))
    defs = service.sampler.editor()["configuration"]["definitions"]
    return service.view(stored(gen, record), defs)["values"]


@pytest.mark.asyncio
async def test_assistant_sees_metric_tools_and_reads_current_values(tmp_path):
    gen, fake = service(tmp_path)
    record, (only,) = flagged(gen, "Check the metrics.")
    fake.responses = [turn(("view_metrics", {})), turn(reply(only, False, "Looks right."))]
    await run(gen, record)
    names = [tool["function"]["name"] for tool in fake.calls[0].payload["tools"]]
    assert {"view_metrics", "set_metric", "reply", "update_event"} <= set(names)
    (viewed,) = results(fake.calls[1].payload)
    assert viewed["accepted"] is True
    complexity = viewed["metrics"]["complexity"]
    assert complexity["status"] == "unavailable" and complexity["writable"] is True
    assert "medium" in complexity["choices"] and "value" not in complexity
    assert viewed["metrics"]["total_characters"]["writable"] is False


@pytest.mark.asyncio
async def test_assistant_sets_metrics_after_edits_and_they_commit(tmp_path):
    gen, fake = service(tmp_path)
    record, (only,) = flagged(gen, "Complexity should be medium; tag ambiguity.")
    fake.responses = [
        turn(
            edit(),
            metric("complexity", "medium"),
            metric("event_tags", ["ambiguous_requirements"]),
            reply(only, True, "Set both."),
        )
    ]
    await run(gen, record)
    assert len(fake.calls) == 1, "one exchange answers everything"
    source = stored(gen, record).source
    assert source.events["x"].content == "after"
    saved = source.metadata["metrics"]["complexity"]
    assert saved["value"] == "medium" and saved["origin"] == assistant_metrics.ORIGIN
    values = panel(gen, record)
    assert values["complexity"]["status"] == "available"
    assert values["event_tags"]["observation"]["value"] == ["ambiguous_requirements"]


@pytest.mark.asyncio
async def test_a_later_edit_makes_an_assistant_annotation_stale(tmp_path):
    gen, fake = service(tmp_path)
    record, (only,) = flagged(gen, "Fix it.")
    fake.responses = [
        turn(metric("complexity", "easy"), edit("changed"), reply(only, True, "Done."))
    ]
    await run(gen, record)
    assert panel(gen, record)["complexity"]["status"] == "stale"


@pytest.mark.asyncio
async def test_invalid_and_read_only_metrics_are_rejected_without_changes(tmp_path):
    gen, fake = service(tmp_path)
    record, (only,) = flagged(gen, "Metrics.")
    fake.responses = [
        turn(
            metric("complexity", "impossible"),
            metric("event_tags", "ambiguous_requirements"),
            metric("total_characters", 5),
            metric("observed_context_tokens", 5),
            metric("no_such_metric", 1),
            ("set_metric", {"key": "complexity"}),
        ),
        turn(reply(only, False, "Could not.")),
    ]
    await run(gen, record)
    replies = results(fake.calls[1].payload)
    assert [r["accepted"] for r in replies] == [False] * 6
    assert "Unknown category" in replies[0]["error"]
    assert "list of labels" in replies[1]["error"]
    assert "read-only" in replies[2]["error"] and "read-only" in replies[3]["error"]
    assert "Unknown metric" in replies[4]["error"]
    assert "value" in replies[5]["error"]
    assert "metrics" not in stored(gen, record).source.metadata


@pytest.mark.asyncio
async def test_null_clears_an_existing_observation(tmp_path):
    gen, fake = service(tmp_path)
    record, (only,) = flagged(gen, "Complexity is unknown.")
    service_ = MetricService(SamplerService(gen.jobs))
    record = gen.store.read(record.source.id)
    service_.annotate(record.source.id, record.revision, "complexity", "hard", "manual_annotation")
    fake.responses = [turn(metric("complexity", None), reply(only, True, "Cleared."))]
    await run(gen, stored(gen, record))
    assert "complexity" not in stored(gen, record).source.metadata.get("metrics", {})
    assert panel(gen, record)["complexity"]["status"] == "unavailable"
