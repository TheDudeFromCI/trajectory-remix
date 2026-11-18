"""Assembly fix: the Assembly model fixes its own graph from validator diagnostics."""

import pytest
from test_draft_assembly import DRAFT, spec
from test_graph_generation import finish, service

from trajectory_remix.graph import assembly_fix


def tangled():
    body = spec()
    # Two writers in one window with no order between them: a structural error.
    body["events"][2] = dict(body["events"][2], after=["ask"])
    return body


def left_open():
    body = spec()
    body["events"][-1] = {k: v for k, v in body["events"][-1].items() if k != "close"}
    return body  # A reasoning window left open: a warning only.


def fix(t):
    return {
        "action": "patch",
        "event_id": "plan",
        "expected_source_hash": t.payload["source_hash"],
        "patch": {"after": ["ack"]},
    }


async def run(tmp_path, responses, **opts):
    gen, fake = service(tmp_path, method="draft_assembly", **opts)
    fake.responses = [{"draft": DRAFT}, *responses]
    job = gen.start([{"seed": 21}], {})
    await finish(gen, job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    return gen, job, source, [c.payload["operation"] for c in fake.calls], fake


@pytest.mark.asyncio
async def test_generator_fixes_errors_then_reviews_run(tmp_path):
    gen, job, source, ops, fake = await run(tmp_path, [tangled(), fix, {"action": "done"}])
    assert ops[2:4] == [assembly_fix.OPERATION] * 2 and "repair" not in ops
    assert source.metadata["stage"] == 2
    assert source.metadata["assembly_fix"]["reason"] == "done"
    assert source.metadata["assembly_fix"]["errors"] == 0
    first = fake.calls[2].payload["messages"]
    assert "unordered_window_writers" in first[1]["content"] and DRAFT in first[1]["content"]


@pytest.mark.asyncio
async def test_done_is_refused_while_errors_remain(tmp_path):
    responses = [tangled(), {"action": "done"}, fix, {"action": "done"}]
    gen, job, source, ops, fake = await run(tmp_path, responses)
    refused = fake.calls[3].payload["messages"][-1]["content"]
    assert '"accepted":false' in refused and "unordered_window_writers" in refused
    assert source.metadata["stage"] == 2


@pytest.mark.asyncio
async def test_warnings_alone_start_self_repair_and_give_up_hands_on(tmp_path):
    reason = "The open window is deliberate in this fixture episode."
    gen, job, source, ops, fake = await run(
        tmp_path, [left_open(), {"action": "give_up", "reason": reason}]
    )
    assert ops[2] == assembly_fix.OPERATION and source.metadata["stage"] == 2
    assert source.metadata["assembly_fix"]["reason"] == "gave_up"
    assert source.metadata["assembly_fix"]["warnings"] >= 1


@pytest.mark.asyncio
async def test_invalid_self_repair_output_hands_on_without_pausing(tmp_path):
    gen, job, source, ops, fake = await run(
        tmp_path, [left_open(), {"action": "bogus"}], format_retries=0
    )
    assert gen.jobs.inspect(job)["state"] == "completed"
    assert source.metadata["assembly_fix"]["reason"] == "invalid_output"


@pytest.mark.asyncio
async def test_clean_graph_skips_self_repair(tmp_path):
    gen, job, source, ops, fake = await run(tmp_path, [spec()])
    assert assembly_fix.OPERATION not in ops and "assembly_fix" not in source.metadata
