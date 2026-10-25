"""Invisible example tool that replaces the system window's text."""

name = "potato_mode"
description = "Replace the system prompt with a potato assistant persona."
invisible = True
schema = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def run(args: dict, state: dict, windows: list) -> tuple[str, bool, bool]:
    """Update the existing system window without modifying sandbox state."""
    for window in windows:
        if window.name == "system":
            window.content = "You are a helpful potato assistant."
            return "", True, False
    raise ValueError("potato_mode requires an open 'system' window")
