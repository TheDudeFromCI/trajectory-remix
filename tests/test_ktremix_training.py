"""End-to-end fixture/attention checks; optional PyTorch demo dependencies."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
sys.path.insert(0, str(EXAMPLES))
AVAILABLE = all(importlib.util.find_spec(n) for n in ("torch", "numpy", "tokenizers", "lz4", "cloudpickle", "simpleeval"))
if AVAILABLE:
    import numpy as np
    import torch
    import ktremix_dataset as k
    import ktremix_training_data as d
    import train_ktremix as t
    from trajectory_remix.compiler import Compiler, visible_to


@unittest.skipUnless(AVAILABLE, "install project and optional training demo dependencies")
class TrainingDemoTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)
        torch.manual_seed(4)
        self.compiler = Compiler(d.tokenizer(), slot_policy="declaration-v1")

    def fixture(self, seed=5):
        source = d.make_source(seed)
        plan = self.compiler.compile(source, incremental=False)
        cols, metadata = d.lower_plan(plan)
        raw, entry = k.encode_sample(source["uuid"], cols, metadata)
        return source, plan, cols, metadata, k.Sample(raw, entry)

    def test_vocabulary_wraparound_and_earliest_output_ticks(self):
        self.assertEqual(len(d.VOCAB), 39)
        self.assertEqual(d.tokenizer().encode("bz29").ids, [1, 25, 28, 35])
        # Across random fixtures, verify against independently reconstructed input
        # values/timestamps rather than trusting generated target metadata.
        wrap_seen = False
        for seed in range(12):
            source, _, a, meta, sample = self.fixture(seed)
            sample.validate()
            inputs = {"letters": [], "digits": []}
            outputs = []
            for tick, events in source["replay"].items():
                for event in events:
                    if event["op"] == "append":
                        inputs[event["window"]].append((int(tick), event["content"]))
                    elif event["op"] == "generate":
                        outputs.append((int(tick), event["content"]))
            for (ta, letter), (tb, digit), (tick, output) in zip(inputs["letters"], inputs["digits"], outputs):
                self.assertEqual(tick, max(ta, tb))
                self.assertEqual(output, chr((ord(letter) - 97 + int(digit)) % 26 + 97))
                wrap_seen |= ord(letter) - 97 + int(digit) >= 26
            self.assertEqual(outputs[-1], (outputs[-2][0] + 1, "<|eos|>"))
            self.assertEqual(len(a["window_slots"]), 4)  # three Active plus reserve
            self.assertEqual(meta["peak_active_windows"], 3)
            for row, wid in enumerate(a["window_ids"]):
                if wid < 2:
                    self.assertEqual(a["loss_weights"][row], 0)
                    self.assertFalse(a["row_flags"][row] & k.MODEL)
        self.assertTrue(wrap_seen)

    def test_visible_memory_matches_existing_compiler_on_shared_rows(self):
        _, plan, a, meta, _ = self.fixture()
        # Compare every retained result query to every non-opening durable key.
        # Available BOS insertion is intentionally new to this fixture profile.
        candidates = [r for r in plan["rows"] if r["durable"] and r["op"] != "open"
                      and r["token_id"] != d.EOS]
        for query in [r for r in plan["rows"] if r["phase"] == "D" and r["window"] == "result"]:
            if query["tick"] not in meta["model_ticks"]:
                continue  # idle wall-clock ticks collapsed by the sleep contract
            qr = next(i for i, (tick, wid) in enumerate(zip(meta["row_ticks"], a["window_ids"]))
                      if tick == query["tick"] and wid == 2 and a["row_flags"][i] & k.MODEL)
            frontier = a["query_frontiers"][qr]
            for key in candidates:
                wid = {"letters": 0, "digits": 1, "result": 2}[key["window"]]
                kr = next(i for i, (tick, window) in enumerate(zip(meta["row_ticks"], a["window_ids"]))
                          if tick == key["tick"] and window == wid and a["token_ids"][i] == key["token_id"]
                          and a["row_flags"][i] & k.DURABLE)
                allowed = a["visible_from"][kr] <= frontier < a["visible_until"][kr]
                self.assertEqual(allowed, visible_to(query, key, plan["windows"]))

    def test_sleep_keeps_one_noop_decision_and_resumes_on_external_input(self):
        source = d.make_source(7, max_gap=15)
        plan = self.compiler.compile(source, incremental=False)
        a, meta = d.lower_plan(plan)
        self.assertLess(len(meta["model_ticks"]), meta["wall_ticks"])
        self.assertTrue(all(b > a for a, b in zip(meta["model_ticks"], meta["model_ticks"][1:])))
        for i, q in enumerate(a["query_frontiers"]):
            if not a["row_flags"][i] & k.MODEL:
                continue
            available = sum(bool(opened <= q < closed and (active == k.FOREVER or q < active)
                            and flags & k.INITIALLY_AVAILABLE)
                            for opened, closed, active, flags in zip(a["window_open_frontiers"],
                                a["window_close_frontiers"], a["window_activation_frontiers"], a["window_flags"]))
            self.assertEqual(available, 1)

    def test_future_tokens_cannot_leak_through_either_model_layer(self):
        _, _, columns, _, sample = self.fixture()
        a = sample.numpy()
        query_index = next(i for i, row in enumerate(a["target_rows"]) if a["token_ids"][row] < 26)
        query_row = a["target_rows"][query_index]
        frontier = a["query_frontiers"][query_row]
        altered = copy.deepcopy(columns)
        changed = 0
        for row, visible in enumerate(a["visible_from"]):
            if visible > frontier and a["token_ids"][row] < 36:
                altered["token_ids"][row] = (altered["token_ids"][row] + 11) % 36
                changed += 1
        self.assertGreater(changed, 0)
        model = t.TinyKidra().eval()
        self.assertEqual(sum(p.numel() for p in model.parameters()), 49968)
        with torch.no_grad():
            before = model(t.collate([sample]))[0, query_index]
            after = model(t.collate([altered]))[0, query_index]
        torch.testing.assert_close(before, after, rtol=0, atol=1e-6)
        # No cross-sample attention through padded batching either.
        with torch.no_grad():
            batched = model(t.collate([sample, altered]))[0, query_index]
        torch.testing.assert_close(before, batched, rtol=0, atol=1e-6)

    def test_raw_compile_shard_sample_and_optimizer_step(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "data"
            report = d.build_dataset(directory, train_samples=5, validation_samples=2, shard_size=2)
            self.assertEqual(report["trajectories"], 7)
            self.assertEqual(len(list(directory.glob("train-*.ktremix"))), 3)
            self.assertEqual(len((directory / "train.jsonl").read_text().splitlines()), 5)
            train_ids = {json.loads(line)["uuid"] for line in (directory / "train.jsonl").read_text().splitlines()}
            validation_ids = {json.loads(line)["uuid"] for line in (directory / "validation.jsonl").read_text().splitlines()}
            self.assertFalse(train_ids & validation_ids)
            with k.Dataset(sorted(directory.glob("train-*.ktremix")), verify_index=True) as dataset:
                for i in range(len(dataset)):
                    dataset[i].validate()
                model = t.TinyKidra()
                optimizer = torch.optim.AdamW(model.parameters(), lr=.001)
                batch = t.collate([dataset[0], dataset[4]])
                before = model.tokens.weight.detach().clone()
                loss = t.objective(model(batch), batch)
                self.assertTrue(torch.isfinite(loss))
                loss.backward()
                optimizer.step()
                self.assertFalse(torch.equal(before, model.tokens.weight))


if __name__ == "__main__":
    unittest.main()
