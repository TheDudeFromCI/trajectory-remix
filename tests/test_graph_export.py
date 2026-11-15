"""Few-record split/artifact correctness, with fake time and cancellation."""
import json
from pathlib import Path
import uuid
import zipfile

import pytest

from trajectory_remix.graph.export import ArtifactService, ExportOptions, resolve_splits
from trajectory_remix.graph.models import ProjectSettings
from trajectory_remix.graph.compiler import GraphCompiler, Cancelled
from trajectory_remix.graph import ktremix as k
from test_graph_compiler import source, event, profile


def records():
    result = []
    for i, (split, family) in enumerate([(None, "a"), ("Auto", "a"), ("Test", "b"), ("Auto", "b")]):
        s = source({"e": event("external_text", "w", str(i))},
                   metadata={"stage": 3, "family_id": family, "metrics": {"x": 1}, "targets": {"x": 2}})
        s.id = str(uuid.UUID(int=i + 1))
        if split is not None:
            s.metadata["split"] = split
        result.append(s)
    return result


def test_family_splits_explicit_counts_and_missing():
    sources = records()
    resolved, warnings = resolve_splits(sources, ProjectSettings())
    assert resolved[sources[0].id] == resolved[sources[1].id] == "train"
    assert resolved[sources[2].id] == resolved[sources[3].id] == "test"
    assert len(warnings) == 1
    assert sources[0].metadata.get("split") is None
    excluded, _ = resolve_splits(sources, ProjectSettings(), exclude_missing=True)
    assert sources[0].id not in excluded
    assert sources[1].id in excluded  # Auto is intentional, never excluded.
    sources[3].metadata["split"] = "Train"
    with pytest.raises(ValueError, match="Conflicting"):
        resolve_splits(sources, ProjectSettings())


@pytest.mark.parametrize("compiled", [True, False])
def test_split_shards_projection_zip_and_expiry(tmp_path, compiled):
    now = [100.]
    service = ArtifactService(tmp_path / "exports", clock=lambda: now[0])
    sources = records()
    key, manifest = service.build(sources, ProjectSettings(),
                                 compiler=GraphCompiler(profile(), active_limit=2),
                                 options=ExportOptions(compiled=compiled, trajectories_per_file=1,
                                                       metadata="remove_all"))
    directory = service.root / key
    assert len(manifest["files"]) == 4
    assert not list(directory.glob("*.partial"))
    with service.lease(key, "download.zip") as stream:
        with zipfile.ZipFile(stream) as archive:
            assert set(archive.namelist()) == {f["name"] for f in manifest["files"]} | set(manifest["assets"]) | {"manifest.json"}
    for f in manifest["files"]:
        with service.lease(key, f["name"]) as stream:
            assert len(stream.read()) == f["bytes"]
        if compiled:
            with k.Dataset([directory / f["name"]], verify_index=True) as dataset:
                sample = dataset[0].validate()
                assert sample.metadata == {}
                assert dataset.shards[0].manifest["split"] == f["split"]
        else:
            raw = json.loads((directory / f["name"]).read_text())
            assert "metadata" not in raw
            assert raw["events"] and raw["windows"]
    now[0] += 86401
    # Active download is protected even when the completed bundle expires.
    now[0] -= 2
    with service.lease(key, "manifest.json"):
        now[0] += 2
        assert service.expire() == []
    with pytest.raises(FileNotFoundError, match="expired"):
        with service.lease(key, "manifest.json"):
            pass
    assert service.expire() == [key]
    assert not directory.exists()


def test_cancellation_failure_partial_cleanup_and_startup(tmp_path):
    service = ArtifactService(tmp_path / "exports")
    calls = [0]
    def cancel():
        calls[0] += 1
        return calls[0] > 3
    with pytest.raises(Cancelled):
        service.build(records(), ProjectSettings(), compiler=GraphCompiler(profile()), cancelled=cancel)
    assert list(service.root.glob("export-*")) == []
    with pytest.raises(ValueError, match="Duplicate"):
        service.build(records()[:1] * 2, ProjectSettings(), options=ExportOptions(compiled=False))
    abandoned = service.root / "export-abandoned"
    abandoned.mkdir()
    (abandoned / ".lease").touch()
    (abandoned / "broken.partial").touch()
    ArtifactService(service.root)
    assert not abandoned.exists()


def test_default_stage_stale_and_projection(tmp_path):
    service = ArtifactService(tmp_path / "exports")
    sources = records()
    sources[0].metadata["stage"] = 2
    sources[1].metadata["stale"] = True
    key, manifest = service.build(sources, ProjectSettings(), options=ExportOptions(
        compiled=False, include_stale=False))
    assert len(manifest["assignments"]) == 2
    values = [json.loads(line) for p in (service.root / key).glob("*.jsonl") for line in p.read_text().splitlines()]
    assert all(set(v["metadata"]) == {"metrics", "targets"} for v in values)
