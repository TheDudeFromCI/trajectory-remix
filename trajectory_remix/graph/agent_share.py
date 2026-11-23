"""Warn when a trajectory contains very little agent-generated text.

The default size labels pair agent and total tokens at 1:16, so a source whose agent share
falls below that ratio is lopsided: it is mostly context the agent never wrote.
"""

from .size_labels import agent_characters, total_characters

MIN_AGENT_SHARE = 1 / 16


def low_agent_share_message(source):
    """Return a warning message when agent characters are under the minimum share, else None."""
    total = total_characters(source)
    if total == 0:
        return None
    agent = agent_characters(source)
    if agent / total >= MIN_AGENT_SHARE:
        return None
    return (
        f"Only {agent:,} of {total:,} characters ({agent / total:.1%}) are agent-generated, "
        f"below the {MIN_AGENT_SHARE:.2%} minimum (1/16)"
    )
