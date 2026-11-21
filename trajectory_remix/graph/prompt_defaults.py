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
    prompts = packaged()
    return prompts["trajectory_guide"] + "\n\n" + prompts["draft_instruction"]


def assembly():
    prompts = packaged()
    return prompts["trajectory_guide"] + "\n\n" + prompts["assemble_instruction"]
