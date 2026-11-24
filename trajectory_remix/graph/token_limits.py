"""Optional endpoint token limits.

Each limit is a positive token count or DISABLED (-1). A disabled limit is left to the
provider: no context limit means no local size check before sending, and no output
limit means no max_tokens field and nothing reserved for the reply.
"""

DISABLED = -1
# Chat-template framing kept free beyond the messages and the reply.
FRAMING_TOKENS = 512
MIN_CONTEXT_TOKENS = 1024


def problem(context, output):
    """Why this pair of limits is invalid, or '' when it is valid."""
    for name, value, least in (
        ("Context limit", context, MIN_CONTEXT_TOKENS),
        ("Max output tokens", output, 1),
    ):
        if value != DISABLED and value < least:
            return f"{name} must be -1 (no limit) or at least {least}."
    if DISABLED not in (context, output) and output + FRAMING_TOKENS >= context:
        return "Max output tokens + 512 must be less than the context limit."
    return ""


def input_ceiling(context, output):
    """Tokens a request may use before it is refused locally; None skips the check."""
    if context == DISABLED:
        return None
    return context - (0 if output == DISABLED else output) - FRAMING_TOKENS


def exceeds(budget):
    """Whether a measured request is over its local ceiling."""
    ceiling = budget["input_ceiling"]
    return ceiling is not None and budget["input_tokens_estimate"] > ceiling
