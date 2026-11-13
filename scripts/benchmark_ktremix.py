#!/usr/bin/env python3
"""Reproducible synthetic, warm-page-cache KTREMIX loader comparison.

Not an end-to-end trainer, cold-storage, remote-storage or GPU benchmark.
python -m pip install numpy lz4
python scripts/benchmark_ktremix.py --directory /tmp --output result.json
"""
import argparse
import importlib.util
import json
import mmap
from pathlib import Path
import platform
import random
import statistics
import tempfile
import time
import uuid
import zlib

import numpy as np

reference_path = Path(__file__).resolve().parents[1] / "examples/ktremix_dataset.py"
spec = importlib.util.spec_from_file_location("ktremix_reference", reference_path)
k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k)


def columns(rows, seed):
    rng = np.random.default_rng(seed)
    i = np.arange(rows, dtype="<u4")
    noop = (i % 7 == 0) & (i != 0)
    external = (i % 11 == 0) & ~noop
    external[0] = True
    durable = ~noop
    tokens = rng.integers(4, 50000, rows, dtype="<u4")
    tokens[0], tokens[noop] = 1, 3
    flags = np.where(noop, k.MODEL | k.NOOP,
                     np.where(external, k.DURABLE, k.MODEL | k.DURABLE)).astype("u1")
    flags[0] = k.DURABLE | k.BOS
    weight = np.where(noop, .1, np.where(external, 0., 1.)).astype("<f4")
    cumulative = np.cumsum(durable, dtype="<u4")
    previous = np.empty(rows, dtype="<u4")
    previous[0] = k.NONE
    previous[1:] = np.maximum.accumulate(np.where(durable, i, 0))[:-1]
    return {"token_ids": tokens, "window_ids": np.zeros(rows, dtype="<u4"),
            "position_ids": np.where(durable, cumulative - 1, cumulative).astype("<u4"),
            "query_frontiers": i.astype("<u8"),
            "visible_from": np.where(durable, i.astype("<u8") + 1, 0).astype("<u8"),
            "visible_until": np.where(durable, k.FOREVER, 0).astype("<u8"),
            "previous_rows": previous, "loss_weights": weight, "row_flags": flags,
            "frame_offsets": np.arange(rows + 1, dtype="<u8"),
            "window_slots": [0], "window_open_frontiers": [0],
            "window_close_frontiers": [k.FOREVER], "window_activation_frontiers": [2],
            "window_flags": [k.INITIALLY_AVAILABLE],
            "target_rows": np.flatnonzero(weight > 0).astype("<u4")}


def measure(load, accesses):
    times, checksum = [], 0
    start = time.perf_counter_ns()
    for index in accesses:
        before = time.perf_counter_ns()
        # Keep a checksum so each random access touches decoded data.
        checksum += load(index)
        times.append((time.perf_counter_ns() - before) / 1e6)
    elapsed = (time.perf_counter_ns() - start) / 1e9
    times.sort()
    return {"samples_per_second": round(len(times) / elapsed, 1),
            "median_ms": round(statistics.median(times), 4),
            "p95_ms": round(times[int(.95 * (len(times) - 1))], 4), "checksum": checksum}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rows", type=int, default=4096)
    p.add_argument("--trajectories", type=int, default=256)
    p.add_argument("--draws", type=int, default=1000)
    p.add_argument("--directory", type=Path, default=Path(tempfile.gettempdir()))
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    if args.rows < 2 or args.trajectories < 2 or args.draws < 1:
        p.error("rows/trajectories >= 2 and draws >= 1 required")
    args.directory.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    accesses = [rng.randrange(args.trajectories) for _ in range(args.draws)]
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "numpy": np.__version__,
        "rows_per_trajectory": args.rows,
        "trajectories": args.trajectories,
        "random_draws": args.draws,
        "cache": "warm local OS page cache; files created immediately before measurement",
        "work": "random indexed read + CRC + decompression + all array views; first token consumed",
        "json_baseline": "same row fields, indexed per-record zlib-1 JSON; parsing only, no tensor assembly",
        "layouts": {},
    }
    with tempfile.TemporaryDirectory(prefix="ktremix-benchmark-", dir=args.directory) as folder:
        folder = Path(folder)
        for codec in ("none", "lz4", "zlib"):
            paths, offset, dataset_id = [], 0, uuid.uuid4()
            for shard_index, count in enumerate((args.trajectories // 4,
                                                  args.trajectories - args.trajectories // 4)):
                path = folder / f"{codec}-{shard_index}.ktremix"
                examples = (
                    (uuid.uuid5(dataset_id, str(j)), columns(args.rows, j), None)
                    for j in range(offset, offset + count)
                )
                manifest = k.demo_manifest(dataset_id, shard_index)
                manifest["execution_contract"] = "ktremix.synthetic-benchmark/1"
                k.write_shard(path, examples, manifest, sample_count=count, codec=codec)
                paths.append(path)
                offset += count
            with k.Dataset(paths) as data:
                # Warm every mmap/page before collecting random-access timings.
                for i in range(len(data)):
                    data[i].numpy()

                def load(i):
                    return int(data[i].numpy()["token_ids"][0])

                result["layouts"][codec] = {
                    "bytes": sum(path.stat().st_size for path in paths),
                    **measure(load, accesses),
                }
        path, entries = folder / "row-json.zlib-records", []
        names = [value[0] for value in k.COLUMNS.values() if value[2] == "R"]
        with path.open("wb") as stream:
            for i in range(args.trajectories):
                a = columns(args.rows, i)
                rows = [{name: a[name][j].item() for name in names} for j in range(args.rows)]
                value = zlib.compress(k.canonical({"rows": rows}), level=1)
                entries.append((stream.tell(), len(value), k.crc(value)))
                stream.write(value)
        with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            def load_json(i):
                offset, size, checksum = entries[i]
                value = data[offset:offset + size]
                assert k.crc(value) == checksum
                return json.loads(zlib.decompress(value))["rows"][0]["token_ids"]
            for i in range(args.trajectories):
                load_json(i)
            result["layouts"]["indexed_row_json_zlib1"] = {
                "bytes": path.stat().st_size,
                **measure(load_json, accesses),
            }
    text = json.dumps(result, indent=2) + "\n"
    print(text, end="")
    if args.output:
        args.output.write_text(text)


if __name__ == "__main__":
    main()
