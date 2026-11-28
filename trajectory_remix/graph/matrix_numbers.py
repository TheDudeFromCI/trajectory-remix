"""Pinned numeric targets (task count, agents, windows...) for matrix_pins.

The supplied sampler draws each numeric target from a range on one matrix option, such as the
task length's ``task_count``. A pinned number keeps only the options whose range contains it,
narrowed to exactly that number, plus the few sampler rules that would otherwise move it (a
non-independent dependency pattern needs depth >= 1, offline tools mean zero calls, ...).
"""

# Numeric target -> (dimension, the option field holding its range).
RANGES = {
    "suggested_task_count": ("task_length", "task_count"),
    "suggested_dependency_depth": ("task_length", "dependency_depth"),
    "target_active_window_capacity": ("task_length", "window_budget"),
    "suggested_tool_observations_or_calls": ("task_length", "expected_tool_calls"),
    "available_agent_count": ("team_posture", "agent_range"),
    "suggested_coordination_events": ("episode_horizon", "coordination_events"),
}
NAMES = tuple(RANGES)
# Patterns that raise the minimum dependency depth (the supplied sampler's rule).
DEEP_PATTERNS = ("branch_merge", "critical_path_with_sidework")
# Observation sources that force at least one tool call.
TOOL_SOURCES = ("tool_calls", "mixed_sources")


def _skips_range(key, value):
    """Zero depth comes from an independent pattern and zero calls may come from offline tools,
    whatever the size's range says."""
    return value == 0 and key in (
        "suggested_dependency_depth",
        "suggested_tool_observations_or_calls",
    )


def _span(option, field):
    low, high = option[field]
    return f"{low}-{high}" if low != high else str(low)


def conflicts(m, pins):
    """Messages for pinned numbers that a pinned option's range or rule rules out."""
    found = []
    for key, (dim, field) in RANGES.items():
        value = pins.get(key)
        if value is None or _skips_range(key, value) or dim not in pins:
            continue
        option = next(x for x in m["dimensions"][dim] if x["id"] == pins[dim])
        low, high = option[field]
        if not low <= value <= high:
            found.append(f"{key} {value} is outside {dim} {pins[dim]} ({_span(option, field)})")
    depth, pattern = pins.get("suggested_dependency_depth"), pins.get("dependency_pattern")
    if depth is not None and pattern:
        if (depth == 0) != (pattern == "independent"):
            found.append(
                f"suggested_dependency_depth {depth} does not fit dependency_pattern {pattern}"
            )
        elif depth == 1 and pattern in DEEP_PATTERNS:
            found.append(f"dependency_pattern {pattern} needs suggested_dependency_depth 2 or more")
    count = pins.get("suggested_task_count")
    if depth and count is not None and depth >= count:
        found.append(f"suggested_dependency_depth {depth} needs more than {count} tasks")
    tools = pins.get("suggested_tool_observations_or_calls")
    if tools and pins.get("tool_access") == "offline":
        found.append(f"tool_access offline means no tool calls, not {tools}")
    if tools == 0 and pins.get("observation_source") in TOOL_SOURCES:
        found.append(f"observation_source {pins['observation_source']} needs a tool call")
    agents, length = pins.get("available_agent_count"), pins.get("task_length")
    if agents and length:
        option = next(x for x in m["dimensions"]["task_length"] if x["id"] == length)
        if agents > option["max_agents"]:
            found.append(f"task_length {length} allows at most {option['max_agents']} agents")
    return found


def _narrow(m, dim, field, value):
    kept = []
    for option in m["dimensions"][dim]:
        low, high = option[field]
        if low <= value <= high:
            option[field] = [value, value]
            kept.append(option)
    m["dimensions"][dim] = kept


def _exclude(m, dim, ids):
    m["dimensions"][dim] = [x for x in m["dimensions"][dim] if x["id"] not in ids]


def _keep_only(m, dim, ids):
    m["dimensions"][dim] = [x for x in m["dimensions"][dim] if x["id"] in ids]


def _depth(m, value):
    if value == 0:
        _keep_only(m, "dependency_pattern", {"independent"})
        return
    _exclude(m, "dependency_pattern", {"independent", *(DEEP_PATTERNS if value == 1 else ())})
    _narrow(m, "task_length", "dependency_depth", value)
    for option in m["dimensions"]["task_length"]:  # Depth needs more tasks than its value.
        option["task_count"][0] = max(option["task_count"][0], value + 1)
    m["dimensions"]["task_length"] = [
        x for x in m["dimensions"]["task_length"] if x["task_count"][0] <= x["task_count"][1]
    ]


def _tools(m, value):
    if value == 0:
        for option in m["dimensions"]["task_length"]:
            option["expected_tool_calls"] = [0, 0]
        _exclude(m, "observation_source", set(TOOL_SOURCES))
        return
    _exclude(m, "tool_access", {"offline"})
    _narrow(m, "task_length", "expected_tool_calls", value)


def restrict(m, pins):
    """Narrow a matrix copy in place to the pinned numbers. Returns the emptied dimension, if any."""
    for key in NAMES:
        value = pins.get(key)
        if value is None:
            continue
        if key == "suggested_dependency_depth":
            _depth(m, value)
        elif key == "suggested_tool_observations_or_calls":
            _tools(m, value)
        else:
            _narrow(m, *RANGES[key], value)
        if key == "available_agent_count":
            m["dimensions"]["task_length"] = [
                x for x in m["dimensions"]["task_length"] if x["max_agents"] >= value
            ]
        if key == "target_active_window_capacity":
            m["dimensions"]["execution_pattern"] = [
                x for x in m["dimensions"]["execution_pattern"] if x.get("min_windows", 1) <= value
            ]
        for dim in m["dimensions"]:
            if not m["dimensions"][dim]:
                return f"No {dim} allows {key} {value} with the other pinned metrics"
    return None


def holds(proposal, pins):
    numbers = proposal["metrics"]["numeric_targets"]
    for key in NAMES:
        if key in pins and numbers.get(key) != pins[key]:
            raise ValueError(f"{key} {pins[key]} did not fit the sampled scenario")
