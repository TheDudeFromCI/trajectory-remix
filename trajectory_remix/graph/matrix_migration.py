"""Bring a project's saved sampler matrix up to date with the editor's axes, once.

Saved matrices made before XXXS and XXXL existed get both task lengths (task_axes.extend_lengths)
on startup, as one settings save. Projects without a saved matrix use the default and need none.
"""

from copy import deepcopy

from . import task_axes
from .sampling import configuration


def migrate(store):
    """Returns True when the saved matrix was upgraded."""
    record = store.settings()
    saved = record.settings.sampler_configuration
    if not saved:
        return False
    matrix = deepcopy(saved["matrix"])
    if not task_axes.extend_lengths(matrix):
        return False
    data = record.settings.model_dump(mode="json")
    data["sampler_configuration"] = configuration(matrix, saved.get("metric_options"))
    store.update_settings(data, record.revision)
    return True
