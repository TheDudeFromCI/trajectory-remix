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


def targets(args, state=None):
    values = []
    for field in ("path", "source", "destination", "url"):
        value = args.get(field)
        if isinstance(value, str):
            values.append(
                value.split("#", 1)[0] if field == "url" else "/" + str(PurePosixPath(value)).lstrip("/")
            )
    # Web references retain their original URL even after corpus replacement.
    # Resolve before both permission and fault matching to prevent ref bypasses.
    if not values and isinstance(args.get("ref"), str):
        page = (state or {}).get("web", {}).get("snapshots", {}).get(args["ref"])
        if page is not None:
            values.append(page["url"])
    return values


def matching_rule(config, name, args, state=None):
    for rule in config.get("rules", []):
        if rule.get("remaining", 1) == 0 or not fnmatchcase(
            name, rule.get("tool", "*")
        ):
            continue
        pattern = rule.get("path")
        if pattern is not None and not any(
            fnmatchcase(value, pattern) for value in targets(args, state)
        ):
            continue
        return rule
    return None


def consume(rule):
    if "remaining" not in rule:
        return False
    rule["remaining"] -= 1
    return True


def access_failure(name, args, message, modified):
    # Permission errors occur before tool execution. Apply the same web output
    # limit here so long author-supplied fault/rejection messages also fit.
    budget = (args.get("max_output_bytes", 6000)
              if name in ("web_search", "web_open", "web_find") else None)
    return failure(message, modified, max_bytes=budget)


def permission(state, name, args, *, consume_rule=True):
    config = state.get("tool_permissions", {})
    rules, denied_rule, denied = [], None, False
    # A transfer needs permission for both endpoints. An allow rule on the
    # source must never grant access to a separately rejected destination.
    for target in targets(args, state) or [None]:
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
        return access_failure(name, args, "Permission denied: " + message, modified), modified
    return None, modified


def io_failure(state, name, args):
    rule = matching_rule(state.get("io_failures", {}), name, args, state)
    if rule is None:
        return None
    modified = consume(rule)
    return access_failure(
        name, args,
        f"{rule.get('code', 'EIO')}: {rule.get('message', 'Simulated I/O failure')}",
        modified,
    )


def readable_path(state, name, path):
    denied, _ = permission(state, name, {"path": path}, consume_rule=False)
    return denied is None
