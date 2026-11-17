"""The example Kidra tool catalogues are valid contracts with neutral descriptions."""

import json
import re
from pathlib import Path

import pytest

from trajectory_remix.graph.models import ProjectSettings

EXAMPLES = Path(__file__).parents[1] / "examples"


def catalogue(name):
    return json.loads((EXAMPLES / name).read_text())["tools"]


@pytest.mark.parametrize("name", ["kidra_tools.json", "kidra_tools_core.json"])
def test_catalogues_load_as_project_tools(name):
    tools = catalogue(name)
    settings = ProjectSettings.model_validate({"tools": tools})
    assert set(settings.tools) == set(tools)
    for ident, tool in tools.items():
        schema = tool["input_schema"]
        assert schema["description"], ident  # The selector and writers read this.
        # Contracts only: nothing about how an old sandbox simulated the tool.
        assert not re.search(r"virtual|simulat|synthetic|sandbox", schema["description"], re.I)
        assert "invisible" not in json.dumps(schema)


def test_core_is_a_subset_and_delegation_names_the_specialist():
    full, core = catalogue("kidra_tools.json"), catalogue("kidra_tools_core.json")
    assert set(core) < set(full) and all(core[k] == full[k] for k in core)
    subagent = full["subagent"]["input_schema"]
    assert "agent" in subagent["properties"] and subagent["required"] == ["task"]
    assert "response" not in subagent["properties"]  # Old simulator hook removed.
    assert {"subagent", "subagent_get", "subagent_list", "subagent_close"} <= set(core)
    # Every selector request carries the catalogue, so the core set stays small.
    assert len(json.dumps(core, separators=(",", ":"))) < 6500
