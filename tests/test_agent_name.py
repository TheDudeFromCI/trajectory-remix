"""The project's agent name setting: default, validation and round trip."""

import pytest
from pydantic import ValidationError

from trajectory_remix.graph.models import ProjectSettings


def test_agent_name_defaults_to_agent():
    assert ProjectSettings().agent_name == "agent"


def test_agent_name_round_trips():
    settings = ProjectSettings(agent_name="Kidra")
    assert ProjectSettings.model_validate(settings.model_dump(mode="json")).agent_name == "Kidra"


@pytest.mark.parametrize("name", ["", " padded ", "x" * 65])
def test_agent_name_rejects_blank_padded_or_long(name):
    with pytest.raises(ValidationError):
        ProjectSettings(agent_name=name)
