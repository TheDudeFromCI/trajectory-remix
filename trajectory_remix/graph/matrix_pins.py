"""Pin any category or label metric when sampling from the matrix.

The supplied sampler takes pins for only a few dimensions (sampling.OVERRIDES). Every other pin
is applied by sampling from a copy of the matrix restricted to the pinned options, so the
sampler itself draws the unpinned fields, under its own rules, given the pins. The supplied
sampler is unchanged, and with no pins this is one ordinary sample.

A pinned value can still rule out others through the matrix's rules. Direct contradictions (a
behavior outside the pinned family, a hard implication or requires/forbids between two pinned
values) are reported at once. Otherwise samples that fail are retried with new seeds, which
also steers the unpinned upstream fields toward ones that fit; if none succeeds within the
budget, the pins are reported as incompatible.
"""

import hashlib
import random
import re
import secrets
from collections import Counter
from copy import deepcopy

from . import task_axes
from .sampling import OVERRIDES, original

ATTEMPTS = 300
# Balancing tolerances to try, strict first, when pins leave the joint weights unbalanceable.
TOLERANCES = (None, 0.01, 0.05, 0.2, 1.0)
JOINT_AXES = ("focus.family", "lifecycle", "task_length")
FOCUS = ("focus.family", "focus.behavior", "decision.axis", "decision.choice")
DERIVED = ("complexity", "task_length")  # Pinned through task_axes.sample.


class IncompatiblePins(ValueError):
    """The pinned values cannot all hold under the matrix's rules."""


def _behaviors(m):
    return {b["id"]: (f, b) for f in m["competencies"] for b in f["behaviors"]}


def _axes_of(m, choice):
    return [
        a for a, d in m["decision_axes"].items() if any(v["id"] == choice for v in d["variants"])
    ]


def _variant(m, axis, choice):
    return next((v for v in m["decision_axes"][axis]["variants"] if v["id"] == choice), None)


def _option(m, key, value):
    """The matrix entry a pinned category value selects (None for an axis or unknown value)."""
    if key == "focus.family":
        items = m["competencies"]
    elif key == "focus.behavior":
        return _behaviors(m).get(value, (None, None))[1]
    elif key == "domain":
        items = m["domains"]
    elif key in m["dimensions"]:
        items = m["dimensions"][key]
    else:
        return None
    return next((x for x in items if x["id"] == value), None)


def _level(m, pins):
    length = pins.get("task_length")
    option = _option(m, "task_length", length) if length else None
    return option and (option["level"], option["max_agents"])


# Direct contradictions between pinned values -------------------------------------------------


def _focus_conflicts(m, pins):
    found = []
    family, behavior = pins.get("focus.family"), pins.get("focus.behavior")
    axis, choice = pins.get("decision.axis"), pins.get("decision.choice")
    spec = None
    if behavior:
        owner, spec = _behaviors(m)[behavior]
        if family and owner["id"] != family:
            found.append(f"focus.behavior {behavior} belongs to {owner['id']}, not {family}")
        if axis and spec["decision_axis"] != axis:
            found.append(f"focus.behavior {behavior} decides {spec['decision_axis']}, not {axis}")
    if choice:
        axes = _axes_of(m, choice)
        if axis and axis not in axes:
            found.append(f"decision.choice {choice} is not a choice of {axis}")
        if spec and (
            spec["decision_axis"] not in axes
            or choice not in spec.get("allowed_decision_choices", [choice])
        ):
            found.append(f"focus.behavior {behavior} cannot make decision.choice {choice}")
    return found


def _fit_conflicts(m, pins, name, item):
    """A behavior or decision choice against pinned lifecycle, size and forced dimensions."""
    found = []
    lifecycle, level = pins.get("lifecycle"), _level(m, pins)
    if lifecycle and "allowed_lifecycle" in item and lifecycle not in item["allowed_lifecycle"]:
        found.append(f"{name} is not allowed in lifecycle {lifecycle}")
    if level and not item.get("min_complexity", 0) <= level[0] <= item.get("max_complexity", 99):
        found.append(f"{name} does not fit task_length {pins['task_length']}")
    if level and item.get("min_agents", 0) > level[1]:
        found.append(f"{name} needs more agents than task_length {pins['task_length']} allows")
    for key, value in item.get("forced_dimensions", {}).items():
        if key in pins and pins[key] != value:
            found.append(f"{name} requires {key} {value}, not {pins[key]}")
    tags = pins.get("event_tags")
    if tags is not None:
        missing = set(item.get("required_event_tags", [])) - set(tags)
        if missing:
            found.append(f"{name} requires event tags {sorted(missing)}")
        clash = set(item.get("forbidden_event_tags", [])) & set(tags)
        if clash:
            found.append(f"{name} forbids event tags {sorted(clash)}")
    return found


def _rule_conflicts(m, pins):
    found = []
    for rule in m.get("hard_implications", []):
        if all(pins.get(k) in v for k, v in rule["when"].items()):
            for key, value in rule["force"].items():
                if key in pins and pins[key] != value:
                    when = ", ".join(f"{k} {pins[k]}" for k in rule["when"])
                    found.append(f"{when} forces {key} {value}, not {pins[key]}")
    level = _level(m, pins)
    for key, value in pins.items():
        option = _option(m, key, value) if key not in FOCUS[:2] else None
        if not option:
            continue
        for field, allowed in (option.get("requires") or {}).items():
            if field in pins and pins[field] not in allowed:
                found.append(f"{key} {value} requires {field} in {allowed}")
        for field, banned in (option.get("forbids") or {}).items():
            if field in pins and pins[field] in banned:
                found.append(f"{key} {value} rules out {field} {pins[field]}")
        if level and not option.get("min_complexity", -1) <= level[0] <= option.get(
            "max_complexity", 99
        ):
            found.append(f"{key} {value} does not fit task_length {pins['task_length']}")
    return found


def _secondary_pairs(m, pins):
    """Pinned secondary competencies and behaviors as {family: behavior or None}."""
    families, behaviors = pins.get("secondary_competencies"), pins.get("secondary_behaviors")
    if families is None and behaviors is None:
        return None
    index = _behaviors(m)
    pairs = {}
    for behavior in behaviors or []:
        family = index[behavior][0]["id"]
        if family in pairs:
            raise IncompatiblePins(f"secondary_behaviors has two behaviors from {family}")
        pairs[family] = behavior
    if families is not None:
        if behaviors is not None and set(families) != set(pairs):
            raise IncompatiblePins(
                "secondary_behaviors must be one behavior from each secondary competency"
            )
        pairs = {f: pairs.get(f) for f in families}
    return pairs


def check(m, pins):
    """Raise IncompatiblePins for pinned values the matrix's rules directly contradict."""
    found = _focus_conflicts(m, pins)
    if pins.get("focus.behavior"):
        found += _fit_conflicts(
            m,
            pins,
            f"focus.behavior {pins['focus.behavior']}",
            _behaviors(m)[pins["focus.behavior"]][1],
        )
    if pins.get("decision.choice"):
        for axis in _axes_of(m, pins["decision.choice"]):
            found += _fit_conflicts(
                m,
                pins,
                f"decision.choice {pins['decision.choice']}",
                _variant(m, axis, pins["decision.choice"]),
            )
    found += _rule_conflicts(m, pins)
    pairs = _secondary_pairs(m, pins)
    primary = pins.get("focus.family") or (
        pins.get("focus.behavior") and _behaviors(m)[pins["focus.behavior"]][0]["id"]
    )
    if pairs and primary in pairs:
        found.append(f"focus.family {primary} cannot also be a secondary competency")
    if found:
        raise IncompatiblePins("; ".join(dict.fromkeys(found)))


# The restricted matrix ------------------------------------------------------------------------


def _keep(option):
    """A pinned option is drawn even if its weight is zero in the matrix."""
    if option is not None and not option.get("weight", 1) > 0:
        option["weight"] = 1.0


def _behavior_fits(behavior, pins, axes):
    tags = pins.get("event_tags")
    choice = pins.get("decision.choice")
    return (
        pins.get("focus.behavior") in (None, behavior["id"])
        and (axes is None or behavior["decision_axis"] in axes)
        and (choice is None or choice in behavior.get("allowed_decision_choices", [choice]))
        and (tags is None or set(behavior.get("required_event_tags", [])) <= set(tags))
        and (tags is None or not set(behavior.get("forbidden_event_tags", [])) & set(tags))
    )


def _restrict_focus(m, pins):
    axes = None
    if pins.get("decision.axis"):
        axes = {pins["decision.axis"]}
    if pins.get("decision.choice"):
        found = set(_axes_of(m, pins["decision.choice"]))
        axes = found if axes is None else axes & found
        for axis in found:
            data = m["decision_axes"][axis]
            data["variants"] = [v for v in data["variants"] if v["id"] == pins["decision.choice"]]
            _keep(data["variants"][0])
    tags = pins.get("event_tags")
    if tags is not None:
        for data in m["decision_axes"].values():
            data["variants"] = [
                v
                for v in data["variants"]
                if set(v.get("required_event_tags", [])) <= set(tags)
                and not set(v.get("forbidden_event_tags", [])) & set(tags)
            ]
    for family in m["competencies"]:
        family["behaviors"] = [b for b in family["behaviors"] if _behavior_fits(b, pins, axes)]
        for behavior in family["behaviors"]:
            _keep(behavior)
    m["competencies"] = [f for f in m["competencies"] if f["behaviors"]]
    if not m["competencies"]:
        raise IncompatiblePins("No behavior in the matrix fits the pinned focus, decision and tags")


def restrict(matrix, pins):
    """(restricted matrix, native overrides) for sampling with these pins."""
    m = deepcopy(matrix)
    native = {k: v for k, v in pins.items() if k in OVERRIDES}
    if pins.get("focus.behavior"):
        native["focus.family"] = _behaviors(m)[pins["focus.behavior"]][0]["id"]
    _restrict_focus(m, pins)
    for key, value in pins.items():
        if key in m["dimensions"] and key not in native and key not in DERIVED:
            m["dimensions"][key] = [x for x in m["dimensions"][key] if x["id"] == value]
        _keep(_option(m, key, value) if isinstance(value, str) else None)
    tags = pins.get("event_tags")
    if tags is not None:
        for tag, descriptor in m["event_tags"].items():
            descriptor["p"] = 1.0 if tag in tags else 0.0
        m["sampler"]["max_extra_event_tags"] = len(tags)
    _balance(m, pins)
    return m, native


def _joint_options(m, axis):
    if axis == 0:
        return m["competencies"]
    return m["dimensions"][JOINT_AXES[axis]]


def _joint(m):
    original._joint_cells(original.canonical_json(task_axes.to_supplied(m)))


def _balance(m, pins):
    """Make the sampler's joint family/lifecycle/size balancing work on the restricted matrix.

    Options left with no eligible behavior get weight 0, which the sampler accepts; a pinned
    one is an incompatibility. If the remaining weights still can't be balanced exactly, the
    balancing tolerance is loosened step by step: as close to the matrix as the pins allow."""
    for tolerance in TOLERANCES:
        if tolerance is not None:
            m["sampler"].setdefault("joint_balancing", {})["tolerance"] = tolerance
        for _ in range(sum(len(_joint_options(m, a)) for a in range(3)) + 1):
            try:
                return _joint(m)
            except ValueError as error:
                found = re.search(r"axis (\d) option (\d+) cannot be drawn", str(error))
                if not found:
                    if "cannot meet configured marginals" in str(error):
                        break
                    raise IncompatiblePins(
                        f"The pinned metrics leave nothing to sample: {error}"
                    ) from None
                axis, index = int(found[1]), int(found[2])
                option = _joint_options(m, axis)[index]
                if pins.get(JOINT_AXES[axis]) == option["id"]:
                    raise IncompatiblePins(
                        f"{JOINT_AXES[axis]} {option['id']} has no behavior that fits the other"
                        " pinned metrics"
                    ) from None
                option["weight"] = 0.0
    raise IncompatiblePins("The pinned metrics leave no balanced family, lifecycle and size")


# Sampling -------------------------------------------------------------------------------------


def _secondary(m, proposal, pairs, rng):
    """Set pinned secondary competencies and behaviors; ValueError when they don't fit."""
    if pairs is None:
        return
    sampling = proposal["metrics"]["sampling"]
    if sampling["focus.family"] in pairs:
        raise ValueError("the sampled primary competency is a pinned secondary one")
    level = _option(m, "task_length", sampling["task_length"])["level"]
    families = {f["id"]: f for f in m["competencies"]}
    chosen = []
    for family, behavior in pairs.items():
        eligible = [
            b
            for b in families[family]["behaviors"]
            if sampling["lifecycle"] in b["allowed_lifecycle"]
            and b["min_complexity"] <= level <= b["max_complexity"]
        ]
        if behavior is not None:
            eligible = [b for b in eligible if b["id"] == behavior]
        if not eligible:
            raise ValueError(f"no secondary behavior of {family} fits the sampled scenario")
        pick = rng.choices(eligible, weights=[max(b["weight"], 1e-9) for b in eligible])[0]
        chosen.append(dict(family=family, behavior=pick["id"], objective=pick["objective"]))
    proposal["learning_objective"]["secondary"] = chosen
    labels = proposal["metrics"]["multi_labels"]
    labels["secondary_competencies"] = [x["family"] for x in chosen]
    labels["secondary_behaviors"] = [x["behavior"] for x in chosen]


def _holds(proposal, pins):
    sampled = proposal["metrics"]["sampling"]
    tags = pins.get("event_tags")
    if tags is not None and set(proposal["metrics"]["multi_labels"]["event_tags"]) != set(tags):
        raise ValueError("the pinned event tags are not all eligible in the sampled scenario")
    for key, value in pins.items():
        if isinstance(value, str) and sampled.get(key) != value:
            raise ValueError(f"{key} was not kept")


def _reason(error):
    text = str(error).split("; context=")[0].split(". Check compatibility")[0]
    return text.removeprefix("Invalid sampler configuration or pins: ")


def _provenance(proposal, matrix, pins):
    """Record the pins, and name the project's matrix rather than the restricted copy."""
    provenance = proposal["sampling_provenance"]
    provenance["pinned"] = deepcopy(pins)
    supplied = original.canonical_json(task_axes.to_supplied(matrix))
    actual = hashlib.sha256(supplied.encode()).hexdigest()
    if provenance.get("matrix_sha256") != actual:
        provenance["restricted_matrix_sha256"] = provenance["matrix_sha256"]
        provenance["matrix_sha256"] = actual


def sample(sampler, pins, seed=None):
    """A proposal snapshot (not stored) keeping every pinned value; IncompatiblePins if none can."""
    if seed is not None and type(seed) is not int:
        raise ValueError("seed must be an integer")
    matrix = sampler.editor()["configuration"]["matrix"]
    if not pins:
        return sampler.propose(seed=seed, persist=False)
    check(matrix, pins)
    restricted, native = restrict(matrix, pins)
    pairs = _secondary_pairs(matrix, pins)
    failures = Counter()
    for attempt in range(ATTEMPTS):
        current = seed + attempt if seed is not None else secrets.randbits(62)
        try:
            snapshot = sampler.propose(
                seed=current, overrides=native or None, persist=False, matrix=restricted
            )
            proposal = snapshot["proposal"]
            _secondary(matrix, proposal, pairs, random.Random(f"secondary:{current}"))
            _holds(proposal, pins)
        except ValueError as error:
            failures[_reason(error)] += 1
            continue
        _provenance(proposal, matrix, pins)
        return snapshot
    reason = failures.most_common(1)[0][0]
    raise IncompatiblePins(
        f"The pinned metrics can't all hold together: no sample fit in {ATTEMPTS} tries"
        f" (most often: {reason}). Change or clear one of them."
    )
