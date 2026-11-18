"""The packaged generation prompts (graph/draft_assembly.yaml), read once.

Projects keep their own copy in settings.prompts; these are the defaults and what
Load default restores.
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


def draft():
    return packaged()["draft_instruction"]


def assembly():
    return packaged()["assemble_instruction"]
