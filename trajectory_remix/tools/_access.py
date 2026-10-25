"""Deterministic author-configured permissions and I/O faults."""

from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from ._simulation import failure

MATCH_PROPERTIES = {
    "tool": {"type": "string", "minLength": 1, "maxLength": 128},
    "path": {"type": "string", "minLength": 1, "maxLength": 2048},
    "remaining": {"type": "integer", "minimum": 1, "maximum": 100000},
    "message": {"type": "string", "maxLength": 1024},
}


def targets(args):
    values = []
    for field in ("path", "source", "destination", "url"):
        value = args.get(field)
        if isinstance(value, str):
            values.append(
                value if field == "url" else "/" + str(PurePosixPath(value)).lstrip("/")
            )
    return values


def matching_rule(config, name, args):
    for rule in config.get("rules", []):
        if rule.get("remaining", 1) == 0 or not fnmatchcase(
            name, rule.get("tool", "*")
        ):
            continue
        pattern = rule.get("path")
        if pattern is not None and not any(
            fnmatchcase(value, pattern) for value in targets(args)
        ):
            continue
        return rule
    return None


def consume(rule):
    if "remaining" not in rule:
        return False
    rule["remaining"] -= 1
    return True


def permission(state, name, args, *, consume_rule=True):
    config = state.get("tool_permissions", {})
    rules, denied_rule, denied = [], None, False
    # A transfer needs permission for both endpoints. An allow rule on the
    # source must never grant access to a separately rejected destination.
    for target in targets(args) or [None]:
        request = {"url": target} if target is not None else {}
        rule = matching_rule(config, name, request)
        if rule is not None and not any(rule is previous for previous in rules):
            rules.append(rule)
        if (rule["decision"] if rule else config.get("default", "allow")) == "deny":
            denied, denied_rule = True, rule
    modified = False
    if consume_rule:
        for rule in rules:
            modified |= consume(rule)
    if denied:
        message = (
            denied_rule.get("message", "User rejected this tool operation")
            if denied_rule
            else "User rejected this tool operation"
        )
        return failure("Permission denied: " + message, modified), modified
    return None, modified


def io_failure(state, name, args):
    rule = matching_rule(state.get("io_failures", {}), name, args)
    if rule is None:
        return None
    modified = consume(rule)
    return failure(
        f"{rule.get('code', 'EIO')}: {rule.get('message', 'Simulated I/O failure')}",
        modified,
    )


def readable_path(state, name, path):
    denied, _ = permission(state, name, {"path": path}, consume_rule=False)
    return denied is None
