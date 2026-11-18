"""Turn a schema violation into a short correction that names the exact fields.

jsonschema's message for a field that may be an object or null ("anyOf") is
"<the whole value> is not valid under any of the given schemas": it repeats the entire
reply and hides the real problems. Small models given that correction repeat the mistake.
Here the alternatives are searched for the specific failures (ignoring "should be null"
when a value was given), each is prefixed with its field path, and long values quoted in a
message are shortened so the explanation itself survives.
"""

from jsonschema import Draft202012Validator

MOST = 4  # Problems listed in one correction.
QUOTE = 80  # Longest value quoted inside a message.


def _is_null_branch(error):
    return error.validator == "type" and error.validator_value == "null"


def _leaves(error):
    if not error.context:
        return [error]
    branches = [c for c in error.context if not _is_null_branch(c)] or list(error.context)
    return [leaf for branch in branches for leaf in _leaves(branch)]


def _message(error):
    text = error.message
    quoted = repr(error.instance)
    if len(quoted) > QUOTE and quoted in text:
        text = text.replace(quoted, quoted[: QUOTE - 2] + "…" + quoted[-1])
    path = ".".join(str(part) for part in error.absolute_path) or "reply"
    return f"{path}: {text}"


def explain(schema, body):
    """None when body matches schema; otherwise a short correction naming each problem."""
    errors = sorted(Draft202012Validator(schema).iter_errors(body), key=lambda e: e.path)
    if not errors:
        return None
    seen = []
    for error in errors:
        for leaf in _leaves(error):
            message = _message(leaf)
            if message not in seen:
                seen.append(message)
    extra = len(seen) - MOST
    return "; ".join(seen[:MOST]) + (f"; and {extra} more" if extra > 0 else "")
