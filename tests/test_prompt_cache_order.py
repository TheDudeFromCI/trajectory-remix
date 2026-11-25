"""Prompts are ordered for prefix caching: stable fields first, new turns appended."""

import pytest
from test_graph_generation import finish, service


@pytest.mark.asyncio
async def test_draft_and_assembly_share_the_run_wide_context(tmp_path):
    gen, fake = service(tmp_path, fast=False)
    await finish(gen, gen.start([{"seed": 137}, {"seed": 138}], {}))
    drafts = [c.payload for c in fake.calls if c.payload["operation"] == "draft"]
    assert drafts[0]["messages"][0] == drafts[1]["messages"][0]  # Same system prompt.
