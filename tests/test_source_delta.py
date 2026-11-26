"""Entity-level diffs and three-way merges behind Assistant proposals."""

from copy import deepcopy

import pytest

from trajectory_remix.graph.source_delta import ProposalConflict, changes, conflicts, merge


def base():
    return dict(
        format="kidra-trajectory-graph/1",
        id="00000000-0000-4000-8000-000000000001",
        defaults={},
        windows={"doc": dict(purpose="document")},
        events={
            "a": dict(kind="external_text", window="doc", content="hello"),
            "b": dict(kind="agent_text", window="doc", content="world", after=["a"]),
        },
        metadata={"stage": 1},
    )


def edited(**edits):
    source = deepcopy(base())
    for path, value in edits.items():
        section, key = path.split("__")
        if value is None:
            source[section].pop(key)
        else:
            source[section][key] = value
    return source


def test_changes_list_added_removed_and_changed_entities_in_order():
    proposed = edited(
        events__b=dict(kind="agent_text", window="doc", content="there", after=["a"]),
        events__c=dict(kind="agent_text", window="doc", content="new"),
        events__a=None,
        metadata__complexity="easy",
    )
    found = [(c["kind"], c["id"], c["change"]) for c in changes(base(), proposed)]
    assert found == [
        ("event", "a", "removed"),
        ("event", "b", "changed"),
        ("event", "c", "added"),
        ("metadata", "complexity", "added"),
    ]
    removed, changed, added, _ = changes(base(), proposed)
    assert removed["after"] is None and added["before"] is None
    assert changed["before"]["content"] == "world" and changed["after"]["content"] == "there"
    assert changes(base(), base()) == []


def test_merge_replays_the_proposal_onto_unrelated_newer_edits():
    current = edited(metadata__stage=2, windows__notes=dict(purpose="notes"))
    proposed = edited(
        events__b=dict(kind="agent_text", window="doc", content="there", after=["a"]),
        events__a=None,
    )
    merged = merge(base(), current, proposed)
    assert merged["metadata"]["stage"] == 2 and "notes" in merged["windows"]
    assert merged["events"] == {"b": proposed["events"]["b"]}


def test_both_sides_making_the_same_change_is_not_a_conflict():
    same = edited(metadata__stage=3)
    assert conflicts(base(), same, same) == []
    assert merge(base(), same, same)["metadata"]["stage"] == 3


def test_both_sides_changing_one_entity_differently_conflicts():
    current = edited(events__b=dict(kind="agent_text", window="doc", content="mine"))
    proposed = edited(events__b=None, metadata__stage=4)
    assert conflicts(base(), current, proposed) == [("event", "b")]
    with pytest.raises(ProposalConflict, match="event b") as raised:
        merge(base(), current, proposed)
    assert raised.value.code == "proposal_conflict"
