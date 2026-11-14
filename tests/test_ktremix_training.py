"""Small graph/demo integration checks; one optimizer update, no training run."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
sys.path.insert(0, str(EXAMPLES))
AVAILABLE = all(importlib.util.find_spec(n) for n in ("torch", "numpy", "tokenizers", "lz4"))
if AVAILABLE:
    import numpy as np
    import torch
    import ktremix_dataset as k
    import ktremix_training_data as d
    import train_ktremix as t
    from trajectory_remix.graph.models import GraphSource
    from trajectory_remix.graph.compiler import GraphCompiler


@unittest.skipUnless(AVAILABLE, "install project and optional training demo dependencies")
class TrainingDemoTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)
        torch.manual_seed(4)
        self.compiler = d.compiler()

    def fixture(self, seed=5):
        source = GraphSource.model_validate(d.make_source(seed))
        columns, metadata = self.compiler.compile(source)
        raw, entry = k.encode_sample(source.id, columns, metadata)
        return source, columns, metadata, k.Sample(raw, entry).validate()

    def test_vocabulary_headers_and_earliest_eligible_modulo_outputs(self):
        self.assertEqual(len(d.SYMBOLS), 36)
        self.assertEqual(d.tokenizer().encode("bz29").ids, [1, 25, 28, 35])
        source, a, meta, _ = self.fixture()
        frames = list(self.compiler.frames(source))
        finish = {e.event: frame.tick for frame in frames for e in (*frame.inputs, *frame.outputs) if e.event}
        starts = {e.event: frame.tick for frame in reversed(frames) for e in (*frame.inputs, *frame.outputs) if e.event}
        self.assertEqual(starts["header"], 0)
        self.assertGreater(finish["header"], 1)  # Real streamed CTX, no disabled-header adapter.
        for i in range(10):
            letter = source.events[f"letter-{i}"].content
            digit = int(source.events[f"digit-{i}"].content)
            output = source.events[f"sum-{i}"].content
            self.assertEqual(output, chr((ord(letter) - 97 + digit) % 26 + 97))
            self.assertEqual(starts[f"sum-{i}"], max(finish[f"letter-{i}"], finish[f"digit-{i}"]))
        result = next(e for f in reversed(frames) for e in f.outputs if e.event == "sum-9")
        self.assertEqual(result.tokens[0].control, "eos")
        self.assertEqual(meta["peak_active_windows"], 3)
        self.assertEqual(len(a["window_slots"]), 4)
        for row, wid in enumerate(a["window_ids"]):
            if meta["logical_windows"][wid] in ("letters", "digits"):
                self.assertEqual(a["loss_weights"][row], 0)
                self.assertFalse(a["row_flags"][row] & k.MODEL)

    def test_future_tokens_and_cross_sample_invariance(self):
        _, columns, _, sample = self.fixture()
        a = sample.numpy()
        query_index = next(i for i, row in enumerate(a["target_rows"]) if a["token_ids"][row] < 26)
        query_row = int(a["target_rows"][query_index])
        frontier = a["query_frontiers"][query_row]
        altered = copy.deepcopy(columns)
        changed = 0
        for row, visible in enumerate(a["visible_from"]):
            if visible > frontier and a["token_ids"][row] < 36:
                altered["token_ids"][row] = (altered["token_ids"][row] + 11) % 36
                changed += 1
        self.assertGreater(changed, 0)
        model = t.TinyKidra().eval()
        self.assertTrue(45000 <= sum(p.numel() for p in model.parameters()) <= 55000)
        with torch.no_grad():
            before = model(t.collate([sample]))[0, query_index]
            after = model(t.collate([altered]))[0, query_index]
            batched = model(t.collate([sample, altered]))[0, query_index]
        torch.testing.assert_close(before, after, rtol=0, atol=1e-6)
        torch.testing.assert_close(before, batched, rtol=0, atol=1e-6)

    def test_closed_and_replaced_memory_cannot_survive_through_peer_hidden_state(self):
        # A surviving window was constructed while OLD was visible. A later query
        # must not recover OLD through that window's higher-layer representation.
        from test_graph_compiler import source, event, profile
        for replacement in (False, True):
            events = {"old": event("external_text", "a", "OLD"),
                      "peer": event("external_text", "b", "v", ["old"])}
            if replacement:
                events["erase"] = event("external_text", "a", "NEW", ["peer"], replace_body=True)
            else:
                events["erase"] = event("agent_text", "a", "", ["peer"], close_window=True)
            events["query"] = event("agent_text", "b", "x", ["erase"], close_window=True)
            c = GraphCompiler(profile(), active_limit=2)
            columns, _ = c.compile(source(events))
            # Map the byte fixture tokens into the demo vocabulary, retaining
            # identity and control flags. We test visibility, not a second model.
            for i, token in enumerate(columns["token_ids"]):
                columns["token_ids"][i] = token % len(d.VOCAB)
            altered = copy.deepcopy(columns)
            targets = columns["target_rows"]
            query_index = next(i for i, row in enumerate(targets)
                               if columns["window_ids"][row] == 2 and
                               columns["row_flags"][row] & k.MODEL and not columns["row_flags"][row] & k.NOOP)
            q = columns["query_frontiers"][targets[query_index]]
            erased = [i for i, end in enumerate(columns["visible_until"])
                      if end <= q and columns["row_flags"][i] & k.DURABLE]
            self.assertTrue(erased)
            for row in erased:
                altered["token_ids"][row] = (altered["token_ids"][row] + 1) % len(d.VOCAB)
            model = t.TinyKidra().eval()
            with torch.no_grad():
                before = model(t.collate([columns]))[0, query_index]
                after = model(t.collate([altered]))[0, query_index]
            torch.testing.assert_close(before, after, rtol=0, atol=1e-6)

    def test_free_running_runtime_matches_compiler_with_mock_predictions(self):
        # No model or provider inference: prescribed predictions verify that
        # rollout and export agree about headers, inputs, allocation and EOS.
        source = GraphSource.model_validate(d.make_source(8))
        schedule = list(d.compiler().frames(source))
        class Oracle:
            def __init__(self):
                self.step = 0
            def eval(self):
                return self
            def __call__(self, batch):
                outputs = schedule[self.step].outputs
                self.step += 1
                logits = torch.full((1, len(outputs), len(d.VOCAB)), -10.)
                # Live writable logical windows precede the reserve, matching
                # the scheduler's stable output order for this one-agent demo.
                for index, emission in enumerate(outputs):
                    logits[0, index, emission.tokens[0].id] = 10.
                return logits
        report = t.rollout(Oracle(), [source.to_dict()], "cpu")
        self.assertEqual(report["exact_timing_and_closure_accuracy"], 1.)
        self.assertEqual(report["letters_at_correct_tick_accuracy"], 1.)

    def test_raw_graph_shards_and_one_optimizer_step(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "data"
            report = d.build_dataset(directory, train_samples=2, validation_samples=1, shard_size=1)
            self.assertEqual(report["trajectories"], 3)
            self.assertEqual(len(list(directory.glob("train-*.ktremix"))), 2)
            self.assertTrue(all(json.loads(line)["format"] == "kidra-trajectory-graph/1"
                                for line in (directory / "train.jsonl").read_text().splitlines()))
            with k.Dataset(sorted(directory.glob("train-*.ktremix")), verify_index=True) as dataset:
                for i in range(len(dataset)):
                    dataset[i].validate()
                model = t.TinyKidra()
                optimizer = torch.optim.AdamW(model.parameters(), lr=.001)
                batch = t.collate([dataset[0]])
                before = model.tokens.weight.detach().clone()
                loss = t.objective(model(batch), batch)
                self.assertTrue(torch.isfinite(loss))
                loss.backward()
                optimizer.step()
                self.assertFalse(torch.equal(before, model.tokens.weight))


if __name__ == "__main__":
    unittest.main()
