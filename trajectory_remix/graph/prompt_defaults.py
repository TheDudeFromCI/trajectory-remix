"""The packaged generation prompts (graph/draft_assembly.yaml), read once.

Projects keep the agent (actor), authoring, Draft and Assembly prompts in
settings.prompts. endpoint_prompts lays them out for each endpoint.
"""

from functools import cache
from importlib.resources import files

import yaml

FILE = "draft_assembly.yaml"


@cache
def packaged():
    return yaml.safe_load(files(__package__).joinpath(FILE).read_text())


def actor():
    return packaged()["actor_instruction"]


def authoring():
    return packaged()["authoring_instruction"]


def draft():
    return packaged()["draft_instruction"]


def assembly():
    return packaged()["assemble_instruction"]


def assistant():
    return packaged()["assistant_instruction"]


def retired_actors():
    """Earlier shipped agent prompts, newest first."""
    return tuple(packaged()["retired"]["actor"])
