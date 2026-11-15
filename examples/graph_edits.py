"""Small graph edit/CAS example; creates only the supplied new project path."""
import argparse
import json
from pathlib import Path

from trajectory_remix.graph import GraphCommands, GraphStore, RevisionConflict


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    commands = GraphCommands(GraphStore.initialize(args.project))
    raw = json.loads(Path(__file__).with_name("graph_minimal.json").read_text())
    created = commands.create(raw)
    ident = created.record.source.id
    annotated = commands.patch(ident, {"metadata": {"stage": -3, "split": "Auto"}}, 1)
    assert annotated.record.intrinsic_hash == created.record.intrinsic_hash
    try:
        commands.patch_event(ident, "think", {"content": "stale edit"}, 1)
    except RevisionConflict as conflict:
        assert conflict.current_revision == 2
    else:
        raise AssertionError("Stale edit should conflict")
    deleted = commands.delete_event(ident, "input", 2)
    assert deleted.has_errors
    assert deleted.record.source.events["think"].after == ["input"]
    repaired = commands.remove_dependency(ident, "think", "input", 3)
    assert not repaired.has_errors
    assert commands.read(ident, 1).record == created.record
    print(json.dumps({"trajectory_id": ident, "revision": repaired.record.revision,
                      "commit_id": repaired.record.commit_id, "errors": repaired.has_errors}))


if __name__ == "__main__":
    main()
