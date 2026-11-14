"""Tiny tokenizer-free contract and transactional edit correctness checks."""
import copy
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import ValidationError

from trajectory_remix.graph import GraphCommands, GraphSource, GraphStore, ProjectSettings, RevisionConflict, diagnose
from trajectory_remix.graph.models import REQUIRED_CONTROLS, TokenizerProfile


def source():
    return {"format": "kidra-trajectory-graph/1", "id": str(uuid4()),
            "windows": {"thought": {"purpose": "reasoning", "agent_writable": True},
                        "chat": {"purpose": "chatroom", "agent_writable": False},
                        "result": {"purpose": "tool", "agent_writable": False}},
            "events": {"input": {"kind": "chat_message", "window": "chat", "content": "Hello", "chat": {"speaker": "Alice"}},
                       "think": {"kind": "agent_text", "window": "thought", "after": ["input"],
                                 "content": "Think", "close_window": True}}}


def codes(raw, settings=None):
    return {d.code for d in diagnose(GraphSource.model_validate(raw), settings) if d.severity == "error"}


def tool_source():
    raw = source()
    raw["events"] = {
        "start": {"kind": "tool_call", "window": "thought", "call_id": "call", "tool": "read",
                  "content": '{"path":', "insert_tool_end": False},
        "submit": {"kind": "tool_call", "window": "thought", "call_id": "call", "tool": "read",
                   "after": ["start"], "content": '"a"}', "insert_tool_begin": False, "close_window": True},
        "reply": {"kind": "tool_response", "window": "result", "call_id": "call", "after": ["submit"],
                  "content": "free-form response", "close_window": True}}
    return raw


def settings():
    return ProjectSettings.model_validate({"tools": {"read": {"name": "Read", "response_window": "result",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}})


@pytest.mark.parametrize("metadata", [None, {}, {"unknown": {"anything": [1, None, True, "x"]}}])
def test_roundtrip_metadata_is_optional(metadata):
    raw = source()
    if metadata is not None:
        raw["metadata"] = metadata
    graph = GraphSource.model_validate(raw)
    encoded = graph.to_dict()
    assert ("metadata" in encoded) == (metadata is not None)
    if metadata is not None:
        assert encoded["metadata"] == metadata
    assert GraphSource.model_validate_json(json.dumps(encoded)).to_dict() == encoded
    assert graph.events["input"].supervision.masked
    assert not graph.events["think"].supervision.masked
    assert not codes(raw)


def test_intrinsic_hash_ignores_only_metadata_and_presentation_order():
    raw = tool_source()
    original = GraphSource.model_validate(raw).intrinsic_hash
    raw["metadata"] = {"stage": 3, "split": "Auto", "metrics": {}, "annotations": [1]}
    assert GraphSource.model_validate(raw).intrinsic_hash == original
    raw["events"] = dict(reversed(list(raw["events"].items())))
    assert GraphSource.model_validate(raw).intrinsic_hash == original
    for field, value in [("content", "different"), ("insert_tool_begin", True), ("close_window", False)]:
        changed = copy.deepcopy(raw)
        changed["events"]["submit"][field] = value
        assert GraphSource.model_validate(changed).intrinsic_hash != original
    raw["events"]["reply"]["delivery"] = {"mode": "streamed", "chain_immediately": False}
    assert GraphSource.model_validate(raw).intrinsic_hash != original


@pytest.mark.parametrize("change", [
    {"format": "kidra-trajectory-graph/99"}, {"id": "not-a-uuid"}, {"replay": {}},
    {"metadata": None}, {"windows": {"": {"purpose": "x", "agent_writable": True}}},
    {"events": {"x": {"kind": "noop", "window": "thought", "content": ""}}},
    {"events": {"x": {"kind": "agent_text", "window": "thought", "content": 1}}},
    {"events": {"x": {"kind": "agent_text", "window": "thought", "content": "", "after": "input"}}},
    {"events": {"x": {"kind": "agent_text", "window": "thought", "content": "", "tick": 4}}},
    {"events": {"x": {"kind": "agent_text", "window": "thought", "content": "", "close_window": 1}}},
    {"defaults": {"noop_loss_weight": float("nan")}},
])
def test_malformed_dtos_rejected(change):
    raw = source()
    raw.update(change)
    with pytest.raises(ValidationError):
        GraphSource.model_validate(raw)


def test_literal_controls_and_typed_window_references():
    raw = source()
    raw["events"]["think"]["content"] = [{"kind": "text", "text": "<|eos|>{N_TICK:3} literal"},
                                         {"kind": "window_ref", "window": "chat"}]
    assert not codes(raw)
    raw["events"]["think"]["content"][1]["window"] = "missing"
    diagnostic = next(d for d in diagnose(GraphSource.model_validate(raw)) if d.code == "missing_window_reference")
    assert diagnostic.path == ["events", "think", "content", 1, "window"]
    assert diagnostic.event_id == "think"


def test_roots_transitive_dependencies_and_unordered_writers():
    raw = source()
    raw["events"]["first"] = {"kind": "agent_text", "window": "thought", "content": "first"}
    raw["events"]["input"]["after"] = ["first"]
    assert not codes(raw)
    raw["events"]["input"]["after"] = []
    assert codes(raw) == {"unordered_window_writers"}


def test_cycles_missing_refs_and_stable_diagnostics():
    raw = source()
    raw["events"]["input"]["after"] = ["think", "missing"]
    result = diagnose(GraphSource.model_validate(raw))
    assert {d.code for d in result} == {"missing_dependency", "dependency_cycle"}
    assert [d.event_id for d in result if d.code == "dependency_cycle"] == ["input", "think"]
    assert next(d for d in result if d.code == "missing_dependency").path == ["events", "input", "after", 1]
    raw["events"] = dict(reversed(list(raw["events"].items())))
    assert diagnose(GraphSource.model_validate(raw)) == result
    raw["events"]["child"] = {"kind": "external_text", "window": "result", "content": "", "after": ["think"]}
    assert "child" not in [d.event_id for d in diagnose(GraphSource.model_validate(raw)) if d.code == "dependency_cycle"]


def test_fragmented_request_submission_response_and_combined_eos():
    raw = tool_source()
    assert not diagnose(GraphSource.model_validate(raw), settings())
    raw["events"]["reply"]["content"] = 'invalid json output is fine'
    assert not diagnose(GraphSource.model_validate(raw), settings())
    raw["events"]["reply"]["insert_response_end"] = False
    raw["events"]["reply"]["close_window"] = False
    raw["events"]["reply-end"] = {**raw["events"]["reply"], "after": ["reply"], "content": "more",
        "insert_response_begin": False, "insert_response_end": True, "close_window": True}
    assert not diagnose(GraphSource.model_validate(raw), settings())


def test_abandoned_prefix_is_valid_but_cannot_receive_a_response():
    raw = tool_source()
    del raw["events"]["submit"]
    del raw["events"]["reply"]
    raw["events"]["start"]["close_window"] = True
    assert not diagnose(GraphSource.model_validate(raw), settings())
    raw["events"]["reply"] = {"kind": "tool_response", "window": "result", "call_id": "call",
                                "after": ["start"], "content": "future", "close_window": True}
    assert codes(raw, settings()) == {"unsubmitted_call"}


def test_tool_schema_warning_only_after_submission_and_no_contamination():
    raw = tool_source()
    raw["events"]["submit"]["content"] = '1}'
    diagnostics = diagnose(GraphSource.model_validate(raw), settings())
    assert [(d.severity, d.code, d.event_id) for d in diagnostics] == [("warning", "tool_input_schema", "submit")]
    raw["events"]["submit"]["insert_tool_end"] = False
    del raw["events"]["reply"]
    assert not diagnose(GraphSource.model_validate(raw), settings())


@pytest.mark.parametrize("event,field,value,expected", [
    ("start", "insert_tool_begin", False, "invalid_tool_begin"),
    ("submit", "insert_tool_begin", True, "invalid_tool_begin"),
    ("submit", "tool", "other", "inconsistent_call"),
    ("submit", "after", [], "unordered_window_writers"),
    ("reply", "after", ["start"], "unsubmitted_call"),
    ("reply", "call_id", "unknown", "unsubmitted_call"),
    ("reply", "insert_response_begin", False, "invalid_response_begin"),
    ("reply", "window", "chat", "response_window"),
])
def test_invalid_framing_and_submission(event, field, value, expected):
    raw = tool_source()
    raw["events"][event][field] = value
    assert expected in codes(raw, settings())


def test_output_after_closure_and_resubmission():
    raw = tool_source()
    raw["events"]["again"] = {**raw["events"]["submit"], "after": ["submit"]}
    assert {"output_after_close", "call_already_finished"} <= codes(raw)


def test_external_delivery_replacement_and_chat_echo():
    raw = tool_source()
    raw["events"]["reply"].update(replace_body=True, content="", delivery={"mode": "streamed"})
    assert "empty_replacement" in codes(raw)
    raw["events"]["reply"]["content"] = [{"kind": "text", "text": ""}]
    assert "empty_replacement" in codes(raw)
    raw["events"]["reply"]["content"] = "new"
    raw["events"]["echo"] = {"kind": "chat_message", "window": "chat", "after": ["submit"],
        "content": "echo", "call_id": "call", "chat": {"speaker": "Agent"}}
    assert not codes(raw)
    raw["events"]["submit"]["echo_chat"] = False
    assert codes(raw) == {"chat_echo_disabled"}


def test_settings_profiles_references_and_defaults():
    project = ProjectSettings()
    assert project.tokenizer_profiles == {}
    assert project.log_cache_mib == 1024
    profile = {"name": "test", "definition": {"model": {}},
               "special_tokens": {name: i for i, name in enumerate(sorted(REQUIRED_CONTROLS))}}
    profile["definition"]["added_tokens"] = [
        {"id": i, "content": f"<{name}>", "special": True}
        for name, i in profile["special_tokens"].items()]
    definition = copy.deepcopy(profile["definition"])
    first = TokenizerProfile.model_validate(profile)
    assert len(first.fingerprint) == 64
    assert profile["definition"] == definition
    profile["special_tokens"].pop("eos")
    with pytest.raises(ValidationError, match="Missing required controls"):
        TokenizerProfile.model_validate(profile)
    for change in [{"endpoint_roles": {"fast": "missing"}}, {"log_cache_mib": -1},
                   {"reasoning_levels": ["High", "High"]},
                   {"split_ratios": {"train": 1, "test": 1, "development": 1}},
                   {"tools": {"x": {"name": "x", "input_schema": {"type": "bogus"}}}},
                   {"endpoints": {"x": {"url": "file:///tmp/x", "model": "x"}}},
                   {"checklists": {"core": {"id": "x", "version": "1", "digest": "bad", "uri": "x"}}}]:
        with pytest.raises(ValidationError):
            ProjectSettings.model_validate(change)


def test_structural_project_references():
    raw = source()
    raw["windows"]["thought"]["reasoning_effort"] = "Impossible"
    raw["events"]["think"]["window"] = "chat"
    assert codes(raw, settings()) == {"unknown_reasoning_level", "window_not_writable"}
    raw["events"]["input"]["window"] = "missing"
    assert "missing_window" in codes(raw)


def test_edit_delete_keeps_broken_reference_until_explicit_removal(tmp_path):
    commands = GraphCommands(GraphStore.initialize(tmp_path / "graph.sqlite"))
    raw = source()
    created = commands.create(raw)
    deleted = commands.delete_event(raw["id"], "input", created.record.revision)
    assert deleted.has_errors
    assert deleted.record.source.events["think"].after == ["input"]
    assert deleted.record.source.events["think"].content == "Think"
    fixed = commands.remove_dependency(raw["id"], "think", "input", deleted.record.revision)
    assert not fixed.has_errors
    assert fixed.record.source.events["think"].after == []
    assert commands.read(raw["id"], 1).record.source.events["input"].content == "Hello"


def test_cas_and_immutable_hash_revisions_and_settings(tmp_path):
    store = GraphStore.initialize(tmp_path / "graph.sqlite")
    commands = GraphCommands(store)
    raw = source()
    one = commands.create(raw).record
    two = commands.patch(raw["id"], {"metadata": {"stage": 3}}, 1).record
    assert two.intrinsic_hash == one.intrinsic_hash
    assert two.intrinsic_revision == one.intrinsic_revision
    assert two.metadata_revision == one.metadata_revision + 1
    assert two.commit_id != one.commit_id
    with pytest.raises(RevisionConflict) as conflict:
        commands.patch_event(raw["id"], "think", {"content": "stale"}, 1)
    assert conflict.value.current_revision == 2
    assert conflict.value.commit_id == two.commit_id
    assert commands.read(raw["id"]).record == two
    settings_two = store.update_settings({"log_cache_mib": None}, 1)
    assert store.settings(1).settings.log_cache_mib == 1024
    assert settings_two.settings.log_cache_mib is None
    with pytest.raises(RevisionConflict):
        store.update_settings({}, 1)
    three = commands.patch_event(raw["id"], "think", {"content": "new"}, 2).record
    assert three.intrinsic_revision == two.intrinsic_revision + 1
    assert three.settings_revision == 2
    assert commands.read(raw["id"], 1).record == one
    assert GraphStore(store.path).read(raw["id"]) == three
    assert store.settings(1).commit_id != store.settings(2).commit_id
    for sql in ["UPDATE graph_trajectory_revisions SET source='{}'", "DELETE FROM graph_trajectory_revisions",
                "UPDATE graph_settings_revisions SET settings='{}'", "DELETE FROM graph_settings_revisions",
                "UPDATE graph_trajectories SET id='changed'", "UPDATE graph_project SET format='changed'"]:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with store.connect() as db:
                db.execute(sql)


def test_independent_sources_and_racing_writers(tmp_path):
    store = GraphStore.initialize(tmp_path / "graph.sqlite")
    commands = GraphCommands(store)
    a, b = source(), source()
    commands.create(a)
    commands.create(b)
    commands.patch_event(b["id"], "think", {"content": "B"}, 1)
    assert commands.patch_event(a["id"], "think", {"content": "A"}, 1).record.revision == 2

    def write(value):
        try:
            return GraphCommands(GraphStore(store.path)).patch_event(a["id"], "think", {"content": value}, 2)
        except RevisionConflict as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["one", "two"]))
    assert sum(isinstance(r, RevisionConflict) for r in results) == 1
    assert store.read(a["id"]).revision == 3


def test_atomic_dto_rejection_identity_and_tool_index(tmp_path):
    store = GraphStore.initialize(tmp_path / "graph.sqlite", settings())
    commands = GraphCommands(store)
    raw = tool_source()
    created = commands.create(raw).record
    assert store.using_tool("read") == [{"trajectory_id": raw["id"], "source_revision": 1}]
    with pytest.raises(ValidationError):
        commands.patch_event(raw["id"], "submit", {"close_window": "yes"}, 1)
    with pytest.raises(ValueError, match="immutable"):
        commands.patch(raw["id"], {"id": str(uuid4())}, 1)
    with pytest.raises(ValueError, match="already exists"):
        commands.create(raw)
    assert store.read(raw["id"]) == created
    changed = commands.patch(raw["id"], {"metadata": {"x": 1}}, 1)
    assert store.using_tool("read")[0]["source_revision"] == 2
    commands.patch(raw["id"], {"events": {"start": None, "submit": None}}, changed.record.revision)
    assert not store.using_tool("read")
    with store.connect() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_event_and_dependency_commands(tmp_path):
    commands = GraphCommands(GraphStore.initialize(tmp_path / "graph.sqlite"))
    raw = source()
    commands.create(raw)
    commands.add_event(raw["id"], "new", {"kind": "external_text", "window": "result", "content": "x"}, 1)
    result = commands.set_dependencies(raw["id"], "new", ["think"], 2)
    assert not result.has_errors
    assert result.record.source.events["new"].after == ["think"]
    with pytest.raises(ValueError):
        commands.add_event(raw["id"], "new", {}, 3)
    assert commands.read(raw["id"]).record.revision == 3


def test_no_overwrite_no_migration_or_silent_version_changes(tmp_path):
    path = tmp_path / "graph.sqlite"
    with pytest.raises(ValueError, match="does not exist"):
        GraphStore(path)
    assert not path.exists()
    store = GraphStore.initialize(path)
    with pytest.raises(FileExistsError):
        GraphStore.initialize(path)
    with store.connect() as db:
        # Bypass the guard to simulate a future version, never upgrade on open.
        db.execute("DROP TRIGGER immutable_graph_project")
        db.execute("UPDATE graph_project SET format='kidra-graph-project/2'")
    with pytest.raises(ValueError, match="Unsupported"):
        GraphStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT format FROM graph_project").fetchone()[0] == "kidra-graph-project/2"
    other = tmp_path / "old.sqlite"
    with sqlite3.connect(other) as db:
        db.execute("CREATE TABLE project(version INTEGER)")
    with pytest.raises(ValueError, match="no legacy reader"):
        GraphStore(other)


def test_new_path_never_imports_tokenizer_compiler_or_runtime(tmp_path):
    # A fresh interpreter catches import-time dependencies as well as method calls.
    script = '''
import builtins, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in ('tokenizers', 'transformers') or name in (
        'compiler', 'trajectory_remix.compiler', 'trajectory_remix.tools', 'trajectory_remix.llm'):
        raise AssertionError('Forbidden graph dependency: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from trajectory_remix.graph import GraphCommands, GraphStore
import json
commands = GraphCommands(GraphStore.initialize(sys.argv[1]))
raw = json.loads(sys.argv[2])
commands.create(raw)
commands.read(raw['id'])
commands.patch_event(raw['id'], 'think', {'content': 'edited'}, 1)
commands.store.update_settings({'log_cache_mib': None}, 1)
commands.store.using_tool('read')
'''
    subprocess.run([sys.executable, "-c", script, str(tmp_path / "graph.sqlite"), json.dumps(source())], check=True)


def test_atomic_chat_delivery_and_partial_controls():
    raw = source()
    raw["events"]["input"]["delivery"] = {"mode": "instant", "chain_immediately": False}
    assert not codes(raw)
    raw["events"]["input"]["delivery"]["mode"] = "streamed"
    with pytest.raises(ValidationError):
        GraphSource.model_validate(raw)


def test_interleaved_call_framing_is_structurally_invalid():
    raw = tool_source()
    raw["events"]["other-call"] = {"kind": "tool_call", "window": "thought", "content": "{}", "tool": "read",
        "call_id": "other", "after": ["start"]}
    raw["events"]["submit"]["after"] = ["other-call"]
    assert "interleaved_framing" in codes(raw)
    raw["events"]["other-call"] = {"kind": "agent_text", "window": "thought", "content": "unframed",
                                      "after": ["start"]}
    assert "interleaved_framing" in codes(raw)


def test_schema_resolution_is_offline_and_advisory(monkeypatch):
    import urllib.request
    def forbidden(*args, **kwargs):
        raise AssertionError("Graph validation cannot retrieve schemas over the network")
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    project = settings().model_dump(mode="json")
    project["tools"]["read"]["input_schema"] = {"$ref": "https://example.invalid/schema"}
    diagnostics = diagnose(GraphSource.model_validate(tool_source()), ProjectSettings.model_validate(project))
    assert [(d.severity, d.code) for d in diagnostics] == [("warning", "tool_input_schema")]
    project["tools"]["read"]["input_schema"] = {"$defs": {"arg": {"type": "object"}}, "$ref": "#/$defs/arg"}
    assert not diagnose(GraphSource.model_validate(tool_source()), ProjectSettings.model_validate(project))
    project["tools"]["read"]["input_schema"] = False
    assert not codes(tool_source(), ProjectSettings.model_validate(project))


def test_profile_rejects_missing_or_shared_dedicated_ids_without_modifying_definition():
    mapping = {name: index for index, name in enumerate(sorted(REQUIRED_CONTROLS))}
    profile = {"name": "fixture", "definition": {"added_tokens": []}, "special_tokens": mapping}
    before = copy.deepcopy(profile)
    with pytest.raises(ValidationError, match="absent from dedicated"):
        TokenizerProfile.model_validate(profile)
    assert profile == before
    profile["definition"]["added_tokens"] = [{"id": i, "content": str(i), "special": True} for i in mapping.values()]
    profile["special_tokens"]["bos"] = profile["special_tokens"]["eos"]
    with pytest.raises(ValidationError, match="distinct dedicated"):
        TokenizerProfile.model_validate(profile)


def test_instance_mutation_is_revalidated_before_persistence(tmp_path):
    graph = GraphSource.model_validate(source())
    graph.events["think"].close_window = "not a bool"
    store = GraphStore.initialize(tmp_path / "graph.sqlite")
    with pytest.raises(ValidationError):
        store.create(graph)


def test_public_examples_and_generated_schemas(tmp_path):
    from pathlib import Path
    import jsonschema
    root = Path(__file__).resolve().parents[1]
    for model in (GraphSource, ProjectSettings):
        jsonschema.Draft202012Validator.check_schema(model.model_json_schema())
    project = ProjectSettings.model_validate({"tools": {"read": {"name": "Read",
        "response_window": "observation", "input_schema": {"type": "object"}}}})
    for filename in ("graph_minimal.json", "graph_tools.json"):
        raw = json.loads((root / "examples" / filename).read_text())
        jsonschema.Draft202012Validator(GraphSource.model_json_schema()).validate(raw)
        assert not diagnose(GraphSource.model_validate(raw), project)
    result = subprocess.run([sys.executable, str(root / "examples/graph_edits.py"),
                             str(tmp_path / "example.sqlite")], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)["revision"] == 4


def test_chat_echo_opt_out_on_first_fragment_applies_to_whole_call():
    raw = tool_source()
    raw["events"]["start"]["echo_chat"] = False
    raw["events"]["echo"] = {"kind": "chat_message", "window": "chat", "after": ["submit"],
                             "chat": {"speaker": "Agent"}, "content": "echo", "call_id": "call"}
    assert codes(raw) == {"chat_echo_disabled"}
