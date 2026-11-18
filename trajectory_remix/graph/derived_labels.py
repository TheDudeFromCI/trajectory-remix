"""Labels derived from a proposal's frozen targets, never stored as observations.

The sampler matrix is byte-pinned, so a concept that crosses its families lives here
instead. A derived label describes what a proposal *intended*; it says nothing about what
a finished trajectory did, and it is never written to a source's `metrics`.
"""

from collections import Counter

# A proposal weighs an automation or infrastructure investment when its decision choice
# is one of these, or when an event tag marks recurring work as an automation candidate.
INVESTMENT_CHOICES = frozenset({"invest_in_automation", "perform_manually", "defer_investment"})
INVESTMENT_TAGS = frozenset({"automation_candidate"})


def strategic_investment(targets):
    """True when the targets (a proposal's `metrics` block) carry an investment decision."""
    targets = targets or {}
    choice = (targets.get("sampling") or {}).get("decision.choice")
    tags = (targets.get("multi_labels") or {}).get("event_tags") or ()
    return choice in INVESTMENT_CHOICES or bool(INVESTMENT_TAGS.intersection(tags))


DERIVED = {"strategic_investment": strategic_investment}


class DerivedLabelCounts:
    """Counts derived labels over proposals, regardless of each one's primary family."""

    def __init__(self):
        self.proposals, self.counts = 0, Counter()

    def add(self, proposal):
        self.proposals += 1
        targets = (proposal or {}).get("metrics")
        for key, label in DERIVED.items():
            self.counts[key] += bool(label(targets))

    def report(self):
        return {
            key: dict(proposals=self.counts[key], of=self.proposals, basis="proposal_targets")
            for key in DERIVED
        }
