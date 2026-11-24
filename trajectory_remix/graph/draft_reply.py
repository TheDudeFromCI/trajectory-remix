"""Read the Draft from a provider reply.

Draft replies are plain text: the model writes its working notes and nothing else, so
endpoints need no JSON mode or grammar support. The notes are wrapped into the
trajectory's metadata here, in code.
"""

import json

MIN_LENGTH = 200


def _unwrapped(text):
    """The notes from a reply that still wraps them as {"draft": "..."}, else the text."""
    stripped = text.strip()
    if not stripped.startswith("{"):
        return stripped
    try:
        value = json.loads(stripped)
    except ValueError:
        return stripped
    if isinstance(value, dict) and set(value) == {"draft"} and isinstance(value["draft"], str):
        return value["draft"].strip()
    return stripped


def draft_text(result):
    """The Draft notes from a transport result; ValueError explains an unusable reply.

    Results saved before plain-text Drafts hold {"output": {"draft": ...}} instead.
    """
    if "text" in result:
        text = result["text"]
    else:
        text = (result.get("output") or {}).get("draft")
    if not isinstance(text, str):
        raise ValueError("The previous reply had no text. Reply with the Draft notes.")
    text = _unwrapped(text)
    if len(text) < MIN_LENGTH:
        raise ValueError(
            f"The previous reply was {len(text)} characters; the Draft needs at least "
            f"{MIN_LENGTH}. Reply with the complete working notes as plain text."
        )
    return text
