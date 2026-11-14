"""Snapshot export and leased, expiring /tmp artifacts. No worker/REST facade.

A durable job service may call build(), supply cancellation, persist the returned
manifest identity, and serve lease() downloads. No jobs are faked here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import tempfile
import time
import uuid
import zipfile

from .compiler import Cancelled, GraphCompiler, CONTRACT, VERSION
from .models import GraphSource, ProjectSettings
from . import ktremix as k

SPLITS = ("train", "development", "test")


@dataclass(frozen=True)
class ExportOptions:
    compiled: bool = True
    trajectories_per_file: int = 256
    minimum_stage: int | None = 3
    include_stale: bool = True
    exclude_missing_split: bool = False
    metadata: str = "all_metrics"
    seed: int = 0

    def __post_init__(self):
        if type(self.trajectories_per_file) is not int or self.trajectories_per_file < 1:
            raise ValueError("trajectories_per_file must be positive")
        if self.minimum_stage not in (None, 0, 1, 2, 3):
            raise ValueError("minimum_stage must be Pending through Ready, or None")
        if self.metadata not in ("full", "remove_all", "measured_metrics", "all_metrics"):
            raise ValueError("Invalid metadata projection")


def resolve_splits(sources, settings, *, seed=0, exclude_missing=False):
    """Balance indivisible known families against explicit population counts."""
    groups, warnings = {}, []
    for source in sources:
        meta = source.metadata
        split = meta.get("split")
        if split is None:
            warnings.append(f"{source.id}: missing split")
            if exclude_missing:
                continue
        elif split not in ("Auto", "auto", "Train", "Development", "Test", *SPLITS):
            raise ValueError(f"{source.id}: invalid split {split!r}")
        family = meta.get("family_id")
        if family is not None and (not isinstance(family, str) or not family):
            raise ValueError("family_id must be a nonempty string")
        key = ("family", family) if family else ("source", source.id)
        groups.setdefault(key, []).append(source)
    explicit, automatic, counts = {}, [], dict.fromkeys(SPLITS, 0)
    for key, members in groups.items():
        choices = {s.metadata["split"].lower() for s in members
                   if s.metadata.get("split") not in (None, "Auto", "auto")}
        if len(choices) > 1:
            raise ValueError(f"Conflicting explicit family splits: {key[1]}")
        if choices:
            split = choices.pop()
            for s in members:
                explicit[s.id] = split
            counts[split] += len(members)
        else:
            automatic.append((key, members))
    automatic.sort(key=lambda item: item[0])
    rng = random.Random(seed)
    rng.shuffle(automatic)
    # Largest indivisible groups first; seed deterministically breaks size ties.
    automatic.sort(key=lambda item: -len(item[1]))
    total = sum(len(v) for v in groups.values())
    desired = {s: getattr(settings.split_ratios, s) * total for s in SPLITS}
    for _, members in automatic:
        split = max(SPLITS, key=lambda s: desired[s] - counts[s])
        counts[split] += len(members)
        for s in members:
            explicit[s.id] = split
    return explicit, warnings


def project(source, policy):
    result = source.to_dict()
    if policy == "remove_all":
        result.pop("metadata", None)
    elif policy != "full":
        keys = ("metrics",) if policy == "measured_metrics" else ("metrics", "targets")
        if "metadata" in result:
            result["metadata"] = {key: value for key, value in source.metadata.items() if key in keys}
    return result


def _sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _publish_json(path, value):
    partial = path.with_suffix(path.suffix + ".partial")
    with partial.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(partial, path)
    partial.unlink()


class ArtifactService:
    def __init__(self, root="/tmp/trajectory-remix-exports", *, clock=time.time):
        self.root = Path(root).resolve()
        if not self.root.is_relative_to(Path("/tmp").resolve()) or self.root == Path("/tmp"):
            raise ValueError("Export root must be a dedicated directory under /tmp")
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.expire()

    def build(self, records, settings: ProjectSettings, *, options=ExportOptions(),
              compiler: GraphCompiler | None = None, cancelled=lambda: False):
        # Freeze the caller's sources/revisions before selection or projection.
        snapshot, provenance, identities = [], {}, set()
        for record in records:
            source = record.source if hasattr(record, "source") else record
            source = GraphSource.model_validate(source.to_dict() if isinstance(source, GraphSource) else source)
            if source.id in identities:
                raise ValueError(f"Duplicate source: {source.id}")
            identities.add(source.id)
            stage = source.metadata.get("stage", 0)
            if options.minimum_stage is not None and (type(stage) is not int or stage < options.minimum_stage):
                continue
            if not options.include_stale and source.metadata.get("stale", False):
                continue
            snapshot.append(source)
            provenance[source.id] = {"intrinsic_hash": source.intrinsic_hash,
                                     **{key: getattr(record, key) for key in
                                        ("revision", "commit_id", "settings_revision") if hasattr(record, key)}}
        settings = ProjectSettings.model_validate(settings.model_dump())
        assignments, warnings = resolve_splits(snapshot, settings, seed=options.seed,
                                               exclude_missing=options.exclude_missing_split)
        if options.compiled and compiler is None:
            raise ValueError("Compiled export requires an explicit compiler/profile")
        if options.compiled:
            compiler = GraphCompiler(compiler.profile, active_limit=compiler.active_limit,
                                     seed=compiler.seed, settings=settings, max_rows=compiler.max_rows)
        dataset = str(uuid.uuid4())
        with (self.root / ".registry.lock").open("a") as registry:
            fcntl.flock(registry, fcntl.LOCK_EX)
            directory = Path(tempfile.mkdtemp(prefix="export-", dir=self.root))
            lease = (directory / ".lease").open("w")
            fcntl.flock(lease, fcntl.LOCK_EX)
        try:
            def check():
                if cancelled():
                    raise Cancelled("Export cancelled")
            files, assets = [], []
            if options.compiled:
                _publish_json(directory / "profile.json", compiler.profile.model_dump())
                # Exact serialization whose hash is embedded in every shard.
                (directory / "tokenizer.json").write_text(compiler.tokenizer.to_str(), encoding="utf-8")
                assets = ["profile.json", "tokenizer.json"]
            for split in SPLITS:
                members = sorted((s for s in snapshot if assignments.get(s.id) == split), key=lambda s: s.id)
                for shard, start in enumerate(range(0, len(members), options.trajectories_per_file)):
                    check()
                    chunk = members[start:start + options.trajectories_per_file]
                    suffix = "ktremix" if options.compiled else "jsonl"
                    path = directory / f"{split}-{shard:04}.{suffix}"
                    if options.compiled:
                        manifest = dict(dataset_id=dataset, shard_id=str(uuid.uuid4()), split=split,
                                        shard_index=shard, execution_contract=CONTRACT, compiler=VERSION,
                                        seed=compiler.seed, active_window_limit=compiler.active_limit,
                                        profile_sha256=compiler.profile.fingerprint,
                                        export={"split": split, **asdict(options)},
                                        tokenizer={"sha256": hashlib.sha256(
                                            compiler.tokenizer.to_str().encode()).hexdigest(),
                                            "special_token_ids": compiler.controls})
                        def samples():
                            for source in chunk:
                                check()
                                columns, _ = compiler.compile(source, cancelled=cancelled)
                                yield source.id, columns, project(source, options.metadata).get("metadata")
                        k.write_shard(path, samples(), manifest, sample_count=len(chunk))
                    else:
                        partial = path.with_suffix(".partial")
                        with partial.open("x", encoding="utf-8") as stream:
                            for source in chunk:
                                check()
                                stream.write(json.dumps(project(source, options.metadata), ensure_ascii=False,
                                                        allow_nan=False) + "\n")
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.link(partial, path)
                        partial.unlink()
                    files.append({"name": path.name, "split": split, "trajectories": len(chunk),
                                  "bytes": path.stat().st_size,
                                  "sha256": _sha256(path)})
            check()
            completed = self.clock()
            manifest = dict(dataset_id=dataset, completed_at=completed, expires_at=completed + 24 * 60 * 60,
                            files=files, assets=assets, assignments=assignments, warnings=warnings,
                            settings_hash=hashlib.sha256(
                                k.canonical(settings.model_dump())).hexdigest(),
                            split_seed=options.seed,
                            sources={ident: provenance[ident] for ident in assignments},
                            metadata=options.metadata, compiled=options.compiled, selection=asdict(options))
            _publish_json(directory / "manifest.json", manifest)
            with zipfile.ZipFile(directory / "download.zip.partial", "x", compression=zipfile.ZIP_STORED) as archive:
                for name in [f["name"] for f in files] + assets + ["manifest.json"]:
                    check()
                    archive.write(directory / name, name)
            os.link(directory / "download.zip.partial", directory / "download.zip")
            (directory / "download.zip.partial").unlink()
            check()
            _publish_json(directory / ".complete.json", {"expires_at": manifest["expires_at"]})
            return directory.name, manifest
        except BaseException:
            shutil.rmtree(directory)
            raise
        finally:
            lease.close()

    @contextmanager
    def lease(self, artifact, name):
        directory = self.root / artifact
        if Path(artifact).name != artifact or not artifact.startswith("export-") or Path(name).name != name:
            raise ValueError("Invalid artifact path")
        with (directory / ".lease").open("r") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            complete = json.loads((directory / ".complete.json").read_text())
            if complete["expires_at"] <= self.clock():
                raise FileNotFoundError("Export expired")
            manifest = json.loads((directory / "manifest.json").read_text())
            allowed = {f["name"] for f in manifest["files"]} | set(manifest["assets"]) | {"manifest.json", "download.zip"}
            if name not in allowed:
                raise FileNotFoundError(name)
            with (directory / name).open("rb") as stream:
                yield stream

    def expire(self):
        with (self.root / ".registry.lock").open("a") as registry:
            fcntl.flock(registry, fcntl.LOCK_EX)
            return self._expire()

    def _expire(self):
        removed = []
        for directory in self.root.glob("export-*"):
            if directory.is_symlink() or not directory.is_dir():
                continue
            try:
                with (directory / ".lease").open("a") as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    complete = directory / ".complete.json"
                    # An unlocked incomplete directory is abandoned (e.g. crash).
                    if not complete.exists() or json.loads(complete.read_text())["expires_at"] <= self.clock():
                        shutil.rmtree(directory)
                        removed.append(directory.name)
            except FileNotFoundError:
                continue
        return removed
