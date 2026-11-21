"""Prompts are ordered for prefix caching: stable fields first, new turns appended."""

import pytest
from test_graph_generation import DRAFT, finish, frozen_legacy_run, service, tangled_spec

from trajectory_remix.graph.provider import response_contract


def wire(payload):
    return "".join(m["role"] + m["content"] for m in response_contract(payload)[1])


@pytest.mark.asyncio
async def test_each_assembly_fix_request_extends_the_previous_one(tmp_path):
    gen, fake = service(tmp_path, fast=False)
    tangled = tangled_spec()

    def patch(t):
        return {
            "action": "patch",
            "event_id": "got",
            "expected_source_hash": t.payload["source_hash"],
            "patch": {"after": ["look"]},
        }

    fake.responses = [{"draft": DRAFT}, tangled, patch, {"action": "done"}]
    await finish(gen, frozen_legacy_run(gen, [{"seed": 137}], {}))
    first, second = [c.payload for c in fake.calls if c.payload["operation"] == "assembly_fix"]
    assert wire(second).startswith(wire(first))


@pytest.mark.asyncio
async def test_draft_and_assembly_share_the_run_wide_context(tmp_path):
    gen, fake = service(tmp_path, fast=False)
    await finish(gen, gen.start([{"seed": 137}, {"seed": 138}], {}))
    drafts = [c.payload for c in fake.calls if c.payload["operation"] == "draft"]
    assert drafts[0]["messages"][0] == drafts[1]["messages"][0]  # Same system prompt.


def test_json_mode_schema_follows_the_system_prompt():
    payload = dict(
        operation="assembly",
        schema={"type": "object", "properties": {"x": {}}},
        messages=[dict(role="system", content="rules"), dict(role="user", content="data")],
    )
    mode, messages = response_contract(payload)
    assert mode == {"type": "json_object"}
    assert [m["role"] for m in messages] == ["system", "user"]  # Roles alternate.
    assert messages[1]["content"].startswith("Return one JSON object")
    assert messages[1]["content"].endswith(
        "Schema: " + '{"type": "object", "properties": {"x": {}}}' + "\n\ndata"
    )
    assert payload["messages"][1]["content"] == "data"  # The payload itself is unchanged.
