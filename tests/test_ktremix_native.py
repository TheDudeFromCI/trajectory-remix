"""Cross-language compiler and wire compatibility checks for the native package."""

import json
from uuid import UUID

import numpy as np
import pytest
from ktremix_native import Compiler, Dataset, dry_run, dry_runs
from test_graph_compiler import event, profile, source

from trajectory_remix.graph import ktremix as reference
from trajectory_remix.graph.validation import diagnose


def native(**options):
    return Compiler(profile().model_dump(mode="json"), **options)


def trajectory(index, **events):
    value = source(events).to_dict()
    value["id"] = str(UUID(int=index))
    return value


def test_dry_run_uses_same_warnings_and_blocks_only_errors():
    value = source(
        {
            "read": event("external_text", "w", "context"),
            "write": event("agent_text", "w", "result", ["read", "read"]),
        }
    )
    raw = value.to_dict()
    structural = dry_run(raw)
    assert structural["metrics"] is None
    assert structural["diagnostics"] == [item.model_dump() for item in diagnose(value)]
    assert [(d["code"], d["severity"]) for d in structural["diagnostics"]] == [
        ("duplicate_dependency", "warning"),
    ]
    compiler = native()
    compiled = compiler.compile(raw)
    assert compiled["report"] == compiler.dry_run(raw)
    assert compiled["source_hash"] == value.intrinsic_hash
    minimal = {
        **raw,
        "events": {
            "read": {"kind": "external_text", "window": "w", "content": "context"},
            "write": {
                "kind": "agent_text",
                "window": "w",
                "content": "result",
                "after": ["read", "read"],
            },
        },
    }
    assert compiler.compile(minimal)["source_hash"] == compiled["source_hash"]
    assert (
        list(dry_runs(iter([raw, "{"]), workers=2))[1]["diagnostics"][0]["code"] == "invalid_source"
    )
    assert list(compiler.dry_runs(iter([raw, raw]), workers=2)) == [compiled["report"]] * 2
    assert compiled["report"]["metrics"]["total_tokens"] == len(compiled["columns"]["token_ids"])
    broken = trajectory(2, write=event("agent_text", "w", "x", ["missing"]))
    assert compiler.dry_run(broken)["diagnostics"][0]["code"] == "missing_dependency"
    with pytest.raises(ValueError, match="missing_dependency"):
        compiler.compile(broken)


def test_unknown_tool_warns_without_blocking_compilation():
    value = trajectory(
        1,
        call=event("tool_call", "w", "{}", call_id="request", tool="lookup", close_window=True),
    )
    settings = {"tools": {}}
    diagnostics = dry_run(value, settings=settings)["diagnostics"]
    assert [(d["severity"], d["code"], d["event_id"]) for d in diagnostics] == [
        ("warning", "unknown_tool", "call")
    ]
    assert native(settings=settings).compile(value)["report"]["diagnostics"] == diagnostics
    assert dry_run(value, settings={"tools": {"lookup": {}}})["diagnostics"] == []
    assert dry_run(value)["diagnostics"] == []


@pytest.mark.parametrize("codec", ["none", "lz4", "zlib"])
def test_native_and_reference_shards_cross_read(tmp_path, codec):
    compiler = native()
    ids = [UUID(int=1), UUID(int=2)]
    files = []
    for index, ident in enumerate(ids):
        value = trajectory(ident.int, write=event("agent_text", "w", str(index), close_window=True))
        value["metadata"] = {"corpus": index}
        path = tmp_path / f"{index}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        files.append(path)
    output = tmp_path / "native.ktremix"
    compiler.write_files(output, iter(files), sample_count=2, workers=2, queue_size=2, codec=codec)
    with reference.Dataset([output], verify_index=True) as reader:
        samples = [
            (reader[i].uuid, reader[i].validate().numpy(), reader[i].metadata)
            for i in range(len(reader))
        ]
        manifest = reader.shards[0].manifest.copy()
    assert [item[0] for item in samples] == list(map(str, ids))
    manifest.pop("shard_id")
    legacy = tmp_path / "reference.ktremix"
    reference.write_shard(legacy, samples, manifest, codec=codec)

    # Weighting can exclude a shard. The same UUID appears in both, and a
    # non-repeating epoch may still only yield it once.
    dataset = Dataset([legacy, output], without_replacement=True, batch_size=3, decode_workers=2)
    batch = dataset.next_batch()
    assert {sample.uuid for sample in batch} == set(map(str, ids))
    assert dataset.next_batch() == []
    order = [sample.uuid for sample in batch]
    dataset.reset_epoch(0)
    assert [sample.uuid for sample in dataset.next_batch()] == order
    for sample in batch:
        arrays = sample.numpy()
        expected = samples[ids.index(UUID(sample.uuid))][1]
        assert sample.metadata == samples[ids.index(UUID(sample.uuid))][2]
        for key in arrays:
            np.testing.assert_array_equal(arrays[key], expected[key])
            assert not arrays[key].flags.writeable
    weighted = Dataset([legacy, output], shard_weights=[0, 1], batch_size=8)
    assert len(weighted.next_batch()) == 8


def test_parallel_shard_discards_output_when_a_later_batch_fails(tmp_path):
    target = tmp_path / "broken.ktremix"
    valid = trajectory(1, write=event("agent_text", "w", "ok", close_window=True))
    broken = trajectory(2, write=event("agent_text", "w", "bad", ["missing"]))
    with pytest.raises(ValueError, match="missing_dependency"):
        native().write_trajectories(target, [valid, broken], sample_count=2, queue_size=1)
    assert list(tmp_path.iterdir()) == []


def test_corpus_targets_preserve_order_and_prior_windows_remain_visible(tmp_path):
    document = tmp_path / "corpus.jsonl"
    texts = {"source": "AAAA\n\nBBBB\n\nCCCC", "distractor": "xxxxxxxxxxxxxxxxxx"}
    document.write_text(
        "".join(json.dumps({"id": name, "text": text}) + "\n" for name, text in texts.items()),
        encoding="utf-8",
    )
    compiler = native(active_limit=6)
    configuration = dict(
        single_window_fraction=0,
        min_section_tokens=4,
        max_section_tokens=6,
        max_tokens_per_document_part=24,
        retained_sections=2,
        max_tick_offset=0,
        samples_per_shard=1,
    )
    paths = compiler.convert_corpus([document], tmp_path / "first", workers=1, **configuration)
    parallel = compiler.convert_corpus(
        [document], tmp_path / "parallel", workers=2, **configuration
    )
    assert paths[0].read_bytes() == parallel[0].read_bytes()
    assert (
        compiler.convert_corpus([document], tmp_path / "first", resume=True, **configuration)
        == paths
    )

    shard = reference.Shard(paths[0], verify_index=True)
    sample = shard.read(0).validate()
    arrays = sample.numpy()
    expected_source = compiler.literal_ids(texts["source"])
    expected_distractor = compiler.literal_ids(texts["distractor"])
    targets = arrays["target_rows"]
    assert len(targets) == len(expected_source) + len(expected_distractor)
    ids = arrays["token_ids"]
    distractor_token = expected_distractor[0]
    main_rows = [r for r in targets if ids[r] != distractor_token]
    assert ids[main_rows].tolist() == expected_source
    windows = arrays["window_ids"]
    assert len(set(windows[main_rows])) >= 3
    first = main_rows[0]
    last = main_rows[-1]
    assert windows[first] != windows[last]
    assert arrays["visible_from"][first] <= arrays["query_frontiers"][last]
    assert arrays["query_frontiers"][last] < arrays["visible_until"][first]
    assert all(arrays["query_frontiers"][i] < arrays["visible_from"][i] for i in targets)
    distractors = targets[ids[targets] == distractor_token]
    assert any(
        arrays["visible_from"][d] <= arrays["query_frontiers"][r] < arrays["visible_until"][d]
        for d in distractors
        for r in main_rows
    )
    assert sample.metadata["corpus"]["text_tokens"] == len(targets)
    shard.close()


def test_single_window_corpus_keeps_all_targets(tmp_path):
    document = tmp_path / "document.txt"
    document.write_text("sample\n\nother", encoding="utf-8")
    compiler = native()
    paths = compiler.convert_corpus(
        [document], tmp_path / "single", input="text", single_window_fraction=1
    )
    sample = Dataset(paths, without_replacement=True).next_batch()[0]
    arrays = sample.numpy()
    assert len(set(arrays["window_ids"][arrays["target_rows"]])) == 1
    assert arrays["token_ids"][arrays["target_rows"]].tolist() == compiler.literal_ids(
        "sample\n\nother"
    )


def test_corpus_accepts_streamed_documents_and_native_buffer_survives_sample(tmp_path):
    compiler = native()
    paths = compiler.convert_documents(
        ({"id": "owned", "text": "portable"} for _ in range(1)),
        tmp_path,
        single_window_fraction=1,
    )
    arrays = Dataset(paths).next_batch()[0].numpy()
    assert arrays["token_ids"][arrays["target_rows"]].tolist() == compiler.literal_ids("portable")
    assert memoryview(arrays["token_ids"]).readonly
