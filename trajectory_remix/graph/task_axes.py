"""Complexity and task length as two separate sampler axes.

The supplied sampler has one ``complexity`` dimension that mixes task scale (task count,
dependency depth, agents, windows, tool calls) with difficulty. The editor matrix splits it:

* ``complexity``: how hard the task is, Trivial to Expert. Sampled here, independently.
* ``task_length``: how big the task is, XXS to XXL (the size-label names). It carries the
  structural ranges and a 0-3 ``level`` and is handed to the supplied sampler in place of its
  own ``complexity``, so behavior eligibility and structure keep working unchanged.

Matrices saved before the split (no ``task_length``) are passed through untouched.
"""

import random
from copy import deepcopy

COMPLEXITY = [
    ("trivial", 0.10, "Routine; the right move is obvious and little can go wrong"),
    ("easy", 0.24, "Straightforward with a few things to keep track of"),
    ("medium", 0.30, "Several considerations to balance; some judgment needed"),
    ("hard", 0.24, "Competing constraints, ambiguity or traps that need careful reasoning"),
    ("expert", 0.12, "Subtle, high-stakes coordination that needs deep expertise"),
]

# Sizes grow geometrically (each step doubles), so weights follow a bell over the log scale
# rather than a flat or linear spread: most tasks are S-L, few are XXS or XXL.
# (id, weight, level, tasks, dependency depth, max agents, windows, tool calls, ~total tokens)
TASK_LENGTH = [
    ("XXS", 0.05, 0, (1, 2), (0, 0), 1, (1, 2), (0, 1), "8k"),
    ("XS", 0.12, 0, (1, 3), (0, 1), 2, (1, 3), (1, 3), "16k"),
    ("S", 0.22, 1, (2, 5), (0, 2), 3, (1, 4), (1, 5), "32k"),
    ("M", 0.26, 2, (4, 9), (1, 3), 4, (2, 5), (3, 9), "64k"),
    ("L", 0.19, 2, (7, 14), (1, 4), 5, (2, 7), (5, 14), "128k"),
    ("XL", 0.11, 3, (12, 22), (2, 6), 7, (3, 9), (6, 20), "256k"),
    ("XXL", 0.05, 3, (18, 32), (3, 8), 8, (4, 12), (10, 30), "512k"),
]
# Rules in the supplied matrix that refer to its scale choices.
LEGACY_SCALE = {
    "trivial": ["XXS"],
    "small": ["XS", "S"],
    "medium": ["M", "L"],
    "large": ["XL", "XXL"],
}


def is_split(matrix):
    return "task_length" in matrix.get("dimensions", {})


def complexity_options():
    return [dict(id=i, weight=w, level=n, description=d) for n, (i, w, d) in enumerate(COMPLEXITY)]


def task_length_options():
    return [
        dict(
            id=i,
            weight=w,
            level=level,
            description=f"About {tokens} tokens in total; {t[0]}-{t[1]} tasks",
            task_count=list(t),
            dependency_depth=list(d),
            max_agents=agents,
            window_budget=list(win),
            expected_tool_calls=list(tools),
        )
        for i, w, level, t, d, agents, win, tools, tokens in TASK_LENGTH
    ]


def _rename_conditions(conditions, old, new, values=None):
    if old not in conditions:
        return
    chosen = conditions.pop(old)
    if values:
        chosen = [x for v in chosen for x in values.get(v, [v])]
    conditions[new] = chosen


def _rename_rules(matrix, old, new, values=None):
    for rule in matrix.get("conditional_weights", []):
        _rename_conditions(rule["when"], old, new, values)
    for rule in matrix.get("hard_implications", []):
        _rename_conditions(rule["when"], old, new, values)
    schema = matrix["metrics_schema"]
    schema["cross_tabs"] = [[new if x == old else x for x in pair] for pair in schema["cross_tabs"]]


def split(matrix):
    """Editor layout from a supplied (unsplit) matrix."""
    if is_split(matrix):
        return deepcopy(matrix)
    m = deepcopy(matrix)
    m["dimensions"]["complexity"] = complexity_options()
    m["dimensions"]["task_length"] = task_length_options()
    _rename_rules(m, "complexity", "task_length", LEGACY_SCALE)
    tracked = m["metrics_schema"]["tracked_one_of"]
    tracked.insert(tracked.index("complexity") + 1, "task_length")
    m["metrics_schema"]["cross_tabs"].append(["complexity", "focus.family"])
    return m


def to_supplied(matrix):
    """The matrix the supplied sampler expects: task length stands in for its complexity."""
    if not is_split(matrix):
        return deepcopy(matrix)
    m = deepcopy(matrix)
    m["dimensions"].pop("complexity")
    m["dimensions"]["complexity"] = m["dimensions"].pop("task_length")
    for pair in m["metrics_schema"]["cross_tabs"]:
        if "complexity" in pair:
            pair[:] = []
    m["metrics_schema"]["cross_tabs"] = [p for p in m["metrics_schema"]["cross_tabs"] if p]
    _rename_rules(m, "task_length", "complexity")
    tracked = m["metrics_schema"]["tracked_one_of"]
    m["metrics_schema"]["tracked_one_of"] = [x for x in tracked if x != "task_length"]
    return m


def _pick_complexity(matrix, seed, pin):
    choices = matrix["dimensions"]["complexity"]
    if pin is not None:
        chosen = next((x for x in choices if x["id"] == pin), None)
        if chosen is None:
            raise ValueError(f"Unknown complexity: {pin}")
        return chosen
    eligible = [x for x in choices if x["weight"] > 0]
    rng = random.Random(f"complexity:{seed}")
    return rng.choices(eligible, weights=[x["weight"] for x in eligible], k=1)[0]


def _directive(proposal, complexity, length):
    proposal["generation_directive"]["requirements"] += [
        f"Pitch the task at {complexity['id']} complexity: {complexity.get('description', '')}.",
        f"Size the trajectory for task length {length['id']}: {length.get('description', '')}.",
    ]


def sample(sample_one, matrix, seed, world_group_id, overrides):
    """Run the supplied sampler on a split matrix and report both axes."""
    if not is_split(matrix):
        return sample_one(matrix, seed, world_group_id, overrides)
    pins = dict(overrides or {})
    complexity_pin, length_pin = pins.pop("complexity", None), pins.pop("task_length", None)
    if length_pin is not None:
        pins["complexity"] = length_pin
    proposal = sample_one(to_supplied(matrix), seed, world_group_id, pins or None)
    used_seed = proposal["sampling_provenance"]["seed"]
    complexity = _pick_complexity(matrix, used_seed, complexity_pin)
    proposal["sampling_provenance"]["requested_overrides"] = overrides
    state = proposal["scenario"]["project_state"]
    state["task_length"] = state.pop("complexity")
    state["complexity"] = complexity["id"]
    sampled = proposal["metrics"]["sampling"]
    sampled["task_length"] = sampled.pop("complexity")
    sampled["complexity"] = complexity["id"]
    length = next(x for x in matrix["dimensions"]["task_length"] if x["id"] == state["task_length"])
    _directive(proposal, complexity, length)
    return proposal


def add_length_comparison(report, matrix):
    """Coverage for task length, in the same shape the supplied report uses for its axes."""
    if not is_split(matrix):
        return report
    choices = matrix["dimensions"]["task_length"]
    total, used = sum(x["weight"] for x in choices), report["included_records"]
    counts = report["marginals"].get("task_length", {})
    rows = {}
    for item in choices:
        desired = item["weight"] / total if total else 0.0
        observed = counts.get(item["id"], {}).get("count", 0) / used if used else 0.0
        rows[item["id"]] = dict(
            configured_target_share=round(desired, 6),
            observed_share=round(observed, 6),
            delta_percentage_points=round(100 * (observed - desired), 3),
            configured_target_count_at_current_n=round(desired * used, 2),
        )
    report["configured_target_comparison"]["task_length"] = rows
    return report
