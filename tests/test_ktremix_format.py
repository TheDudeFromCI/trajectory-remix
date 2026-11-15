"""Public binary-format reference tests; runnable without the Remix server.

python tests/test_ktremix_format.py
Dependencies: numpy; lz4 tests are skipped when lz4 is not installed.
"""
import copy
import importlib.util
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest
import uuid
import zlib

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "ktremix_dataset.py"
from trajectory_remix.graph import ktremix as k


@unittest.skipUnless(importlib.util.find_spec("numpy"), "reference writer tests require numpy")
class KTREMIXTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name="sample", count=1, *, codec="none", manifest=None, columns=None, offset=0):
        rows = [(uuid.uuid5(uuid.NAMESPACE_URL, f"test/{offset + i}"),
                 copy.deepcopy(columns or k.demo_columns(i + offset)), {"name": f"sample {i}"})
                for i in range(count)]
        return k.write_shard(self.root / f"{name}.ktremix", rows,
                             manifest or k.demo_manifest(), codec=codec)

    def test_wire_sizes_and_independent_header_offsets(self):
        path = self.write(count=2)
        raw = path.read_bytes()
        self.assertEqual(raw[:8], b"KTREMIX\0")
        self.assertEqual(struct.unpack_from("<HHI", raw, 8), (1, 0, 128))
        self.assertEqual(struct.unpack_from("<Q", raw, 24)[0], len(raw))
        manifest_offset, manifest_size, index_offset, count = struct.unpack_from("<4Q", raw, 32)
        self.assertEqual(manifest_offset, 128)
        self.assertEqual(count, 2)
        self.assertEqual(index_offset, k.align(128 + manifest_size))
        expected_crc = struct.unpack_from("<I", raw, 112)[0]
        self.assertEqual(k.crc(raw[:112] + b"\0" * 4 + raw[116:128]), expected_crc)

    def test_all_codecs_round_trip_all_columns(self):
        for codec in k.CODECS:
            if codec == "lz4" and importlib.util.find_spec("lz4") is None:
                continue
            with self.subTest(codec=codec):
                path = self.write(name=codec, codec=codec)
                shard = k.Shard(path, verify_index=True)
                self.addCleanup(shard.close)
                sample = shard.read(0).validate()
                for name, values in k.demo_columns().items():
                    observed = list(sample.column(name))
                    if name == "loss_weights":
                        for actual, expected in zip(observed, values):
                            self.assertAlmostEqual(actual, expected, places=6)
                    else:
                        self.assertEqual(observed, values, name)
                self.assertEqual(sample.metadata, {"name": "sample 0"})

    def test_numpy_views_are_read_only_and_keep_buffer_alive_after_close(self):
        for codec in ("none", "zlib"):
            path = self.write(name=codec, codec=codec)
            shard = k.Shard(path)
            views = shard.read(0).numpy()
            shard.close()
            self.assertEqual(views["token_ids"][3], 100)
            self.assertFalse(views["token_ids"].flags.writeable)
            self.assertIsNotNone(views["token_ids"].base)
            with self.assertRaises(ValueError):
                views["token_ids"][0] = 99

    def test_training_read_does_not_parse_record_metadata(self):
        from unittest.mock import patch
        path = self.write()
        shard = k.Shard(path)
        self.addCleanup(shard.close)
        with patch.object(k, "_json", side_effect=AssertionError("metadata parsed on hot path")):
            sample = shard.read(0)
            self.assertEqual(len(sample.numpy()["token_ids"]), 10)

    def test_unequal_shards_sample_uniformly_over_trajectories(self):
        a = self.write("a", count=1)
        b = self.write("b", count=9, offset=1)
        with k.Dataset([a, b], max_open_shards=1) as data:
            self.assertEqual(len(data), 10)
            small_id = data[0].uuid
            rng = random.Random(41)
            hits = sum(data.sample(rng).uuid == small_id for _ in range(5000))
            self.assertTrue(420 <= hits <= 580, hits)
            self.assertLessEqual(len(data._lru), 1)

    def test_distributed_epoch_is_disjoint_complete_and_reproducible(self):
        path = self.write(count=17)
        with k.Dataset([path]) as data:
            groups = [list(data.epoch_indices(19, 2, rank=r, world_size=3,
                                               worker_id=w, num_workers=2))
                      for r in range(3) for w in range(2)]
            flat = [x for group in groups for x in group]
            self.assertEqual(sorted(flat), list(range(17)))
            self.assertEqual(len(flat), len(set(flat)))
            self.assertEqual(list(data.epoch_indices(19, 2)), list(data.epoch_indices(19, 2)))
            self.assertNotEqual(list(data.epoch_indices(19, 2)), list(data.epoch_indices(19, 3)))
            with self.assertRaises(ValueError):
                list(data.epoch_indices(rank=3, world_size=3))

    def test_empty_shard_can_be_present_but_empty_dataset_is_rejected(self):
        empty = self.write("empty", count=0)
        with self.assertRaises(k.FormatError):
            k.Dataset([empty])
        full = self.write("full")
        with k.Dataset([empty, full]) as data:
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0].rows, 10)

    def test_duplicate_shards_and_incompatible_tokenizers_are_rejected(self):
        a = self.write("a")
        with self.assertRaises(k.FormatError):
            k.Dataset([a, a])
        manifest = k.demo_manifest()
        manifest["tokenizer"]["sha256"] = "0" * 64
        b = self.write("b", manifest=manifest)
        with self.assertRaises(k.FormatError):
            k.Dataset([a, b])
        manifest = k.demo_manifest()
        manifest["execution_contract"] = "another-model/2"
        c = self.write("c", manifest=manifest)
        with self.assertRaises(k.FormatError):
            k.Dataset([a, c])

    def test_different_dataset_ids_are_allowed_with_matching_training_contract(self):
        a = self.write("a")
        b = self.write("b", offset=1)
        with k.Dataset([a, b]) as data:
            self.assertEqual(len(data), 2)

    def test_header_manifest_index_and_record_corruption(self):
        path = self.write()
        original = path.read_bytes()
        with self.subTest(region="header"):
            raw = bytearray(original)
            raw[24] ^= 1
            path.write_bytes(raw)
            with self.assertRaises(k.FormatError):
                k.Shard(path)
        with self.subTest(region="manifest"):
            raw = bytearray(original)
            raw[130] ^= 1
            path.write_bytes(raw)
            with self.assertRaises(k.FormatError):
                k.Shard(path)
        path.write_bytes(original)
        shard = k.Shard(path)
        index_offset, offset = shard._index_offset, shard.entry(0)[1]
        shard.close()
        for region, position in (("index", index_offset), ("record", offset + 66)):
            with self.subTest(region=region):
                raw = bytearray(original)
                raw[position] ^= 1
                path.write_bytes(raw)
                with self.assertRaises(k.FormatError):
                    shard = k.Shard(path, verify_index=True)
                    try:
                        shard.read(0)
                    finally:
                        shard.close()

    def test_truncation_trailing_bytes_and_record_resource_limit(self):
        path = self.write()
        original = path.read_bytes()
        for raw in (original[:60], original[:-1], original + b"x"):
            path.write_bytes(raw)
            with self.assertRaises(k.FormatError):
                k.Shard(path)
        path.write_bytes(original)
        shard = k.Shard(path, max_record_bytes=128)
        self.addCleanup(shard.close)
        with self.assertRaises(k.FormatError):
            shard.read(0)

    def test_overlapping_and_wrong_shape_columns_are_rejected(self):
        payload, entry = k.encode_sample(uuid.uuid4(), k.demo_columns())
        raw = bytearray(payload)
        first_offset = k.COLUMN.unpack_from(raw, 64)[3]
        struct.pack_into("<Q", raw, 64 + 32 + 4, first_offset)
        with self.assertRaises(k.FormatError):
            k.Sample(raw, entry)
        bad = copy.deepcopy(k.demo_columns())
        bad["window_ids"] = [0]
        with self.assertRaises(ValueError):
            k.encode_sample(uuid.uuid4(), bad)

    def test_unknown_optional_column_is_skipped_unknown_required_rejected(self):
        payload, entry = k.encode_sample(uuid.uuid4(), k.demo_columns())
        # Existing directory/metadata padding has room for one empty extension.
        # Shift arrays to reserve a 17th entry while keeping 64-byte alignment.
        raw = bytearray(payload[:576] + b"\0" * 64 + payload[576:])
        struct.pack_into("<I", raw, 32, 17)
        for i in range(16):
            location = 64 + i * 32 + 4
            old = struct.unpack_from("<Q", raw, location)[0]
            struct.pack_into("<Q", raw, location, old + 64)
        k.COLUMN.pack_into(raw, 576, 900, 1, 0, 640, 0, 0, 0)
        new_entry = (*entry[:2], len(raw), len(raw), *entry[4:])
        k.Sample(raw, new_entry).validate()
        raw[576 + 3] = 1
        with self.assertRaises(k.FormatError):
            k.Sample(raw, new_entry)

    def test_visibility_replacement_and_parallel_queries(self):
        cols = k.demo_columns()
        payload, entry = k.encode_sample(uuid.uuid4(), cols)
        a = k.Sample(payload, entry).numpy()
        # Same-frontier NOOP targets cannot see their own labels; NOOPs never keys.
        for row in (5, 6):
            frontier = a["query_frontiers"][row]
            allowed = ((a["row_flags"] & k.DURABLE) != 0) & (a["visible_from"] <= frontier) & (frontier < a["visible_until"])
            self.assertFalse(allowed[row])
            self.assertTrue(allowed[a["previous_rows"][row]])
        # Simulate replacing a document: old text vanishes at the boundary at
        # which the first new token becomes visible. BOS is retained.
        cols["visible_until"][1] = 4
        payload, entry = k.encode_sample(uuid.uuid4(), cols)
        a = k.Sample(payload, entry).numpy()
        before, after = 3, 4
        self.assertTrue(a["visible_from"][1] <= before < a["visible_until"][1])
        self.assertFalse(a["visible_from"][1] <= after < a["visible_until"][1])
        self.assertTrue(a["visible_from"][7] <= after < a["visible_until"][7])
        self.assertTrue(a["visible_from"][0] <= after < a["visible_until"][0])

    def test_slot_reuse_and_simultaneous_durable_targets_preserve_causality(self):
        # Storage-contract fixture: window 2 reuses closed window 0's slot;
        # windows 1 and 2 predict simultaneously, then the next frame sees both.
        cols = {
            "token_ids": [1, 1, 10, 100, 1, 101, 102, 2],
            "window_ids": [0, 1, 0, 1, 2, 1, 2, 2],
            "position_ids": [0, 0, 1, 1, 0, 2, 1, 2],
            "query_frontiers": [0, 0, 1, 2, 3, 4, 4, 5],
            "visible_from": [1, 1, 2, 3, 4, 5, 5, 0],
            "visible_until": [3, k.FOREVER, 3, k.FOREVER, 6, k.FOREVER, 6, 0],
            "previous_rows": [k.NONE, k.NONE, 0, 1, k.NONE, 3, 4, 6],
            "loss_weights": [0, 0, 0, 1, 0, 1, 1, 1],
            "row_flags": [k.DURABLE | k.BOS, k.DURABLE | k.BOS, k.DURABLE,
                          k.DURABLE | k.MODEL, k.DURABLE | k.BOS,
                          k.DURABLE | k.MODEL, k.DURABLE | k.MODEL, k.MODEL | k.EOS],
            "frame_offsets": [0, 4, 5, 7, 8],
            "window_slots": [0, 1, 0],
            "window_open_frontiers": [0, 0, 3],
            "window_close_frontiers": [3, k.FOREVER, 6],
            "window_activation_frontiers": [2, 3, 5],
            "window_flags": [k.READ_ONLY, k.INITIALLY_AVAILABLE, k.INITIALLY_AVAILABLE],
            "target_rows": [3, 5, 6, 7],
        }
        path = self.write(columns=cols)
        with k.Dataset([path]) as data:
            a = data[0].validate().numpy()
            def visible_at(q):
                return ((a["row_flags"] & k.DURABLE) != 0) & (a["visible_from"] <= q) & (q < a["visible_until"])
            for row in (5, 6):
                visible = visible_at(a["query_frontiers"][row])
                self.assertFalse(visible[5])
                self.assertFalse(visible[6])
                self.assertFalse(visible[0])
                self.assertFalse(visible[2])
                self.assertTrue(visible[a["previous_rows"][row]])
            self.assertTrue(visible_at(a["query_frontiers"][7])[5:7].all())

    def test_semantic_errors_do_not_silently_create_trainable_arrays(self):
        for field, index, value in (("loss_weights", 3, float("nan")),
                                    ("window_ids", 3, 100),
                                    ("previous_rows", 3, 3),
                                    ("row_flags", 5, k.NOOP | k.DURABLE | k.MODEL),
                                    ("target_rows", 0, 0),
                                    ("visible_from", 3, 2),
                                    ("window_slots", 2, 0)):
            with self.subTest(field=field):
                cols = k.demo_columns()
                cols[field][index] = value
                with self.assertRaises(k.FormatError):
                    k.encode_sample(uuid.uuid4(), cols)

    def test_writer_is_atomic_non_overwriting_and_checks_stream_count(self):
        path = self.write()
        original = path.read_bytes()
        with self.assertRaises(FileExistsError):
            self.write()
        self.assertEqual(path.read_bytes(), original)
        for count in (0, 2):
            target = self.root / f"bad-{count}.ktremix"
            items = iter([(uuid.uuid4(), k.demo_columns(), None)])
            with self.assertRaises(ValueError):
                k.write_shard(target, items, k.demo_manifest(), sample_count=count, codec="none")
            self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob(".ktremix-*")))

    def test_cli_demo_verify_sample(self):
        folder = self.root / "cli"
        subprocess.run([sys.executable, str(SCRIPT), "demo", str(folder), "--codec", "zlib"],
                       check=True, capture_output=True)
        result = subprocess.run([sys.executable, str(SCRIPT), "verify", str(folder / "*.ktremix")],
                                check=True, capture_output=True, text=True)
        self.assertIn("Verified 10 trajectories", result.stdout)
        result = subprocess.run([sys.executable, str(SCRIPT), "sample", str(folder / "*.ktremix"),
                                 "--count", "3", "--epoch", "2"], check=True, capture_output=True, text=True)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(records), 3)
        self.assertEqual(len({r["uuid"] for r in records}), 3)


if __name__ == "__main__":
    unittest.main()
