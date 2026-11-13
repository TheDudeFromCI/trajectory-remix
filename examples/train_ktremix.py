#!/usr/bin/env python3
"""Train a 49,968-parameter, two-layer global shared-KV model on KTREMIX.

See docs/KTREMIX-TRAINING-DEMO.md. Run from a source checkout:
  python examples/train_ktremix.py run /tmp/letter-addition
No provider endpoints, downloads of model weights, or teacher model are used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import platform
import time

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

import ktremix_dataset as k
from ktremix_training_data import (BOS, EOS, NOOP, VOCAB, CONTRACT, Execution,
                                    build_dataset)


class GlobalSharedKV(nn.Module):
    """Four query heads share one K/V head and one global bank across windows.

    Memory construction and prediction queries use the same projections. No
    target token is used to initialize its prediction query. Both paths obey
    per-row construction frontiers at EVERY layer, preventing indirect leakage.
    """
    def __init__(self):
        super().__init__()
        self.norm = nn.LayerNorm(48)
        self.q = nn.Linear(48, 48, bias=False)
        self.k = nn.Linear(48, 12, bias=False)
        self.v = nn.Linear(48, 12, bias=False)
        self.out = nn.Linear(48, 48, bias=False)
        self.ff_norm = nn.LayerNorm(48)
        self.ff = nn.Sequential(nn.Linear(48, 184, bias=False), nn.GELU(), nn.Linear(184, 48, bias=False))

    def attend(self, values, key, value, allowed):
        b, n, _ = values.shape
        query = self.q(self.norm(values)).view(b, n, 4, 12).transpose(1, 2)
        attended = F.scaled_dot_product_attention(query, key.expand(-1, 4, -1, -1),
                                                value.expand(-1, 4, -1, -1),
                                                attn_mask=allowed[:, None], dropout_p=0.)
        values = values + self.out(attended.transpose(1, 2).reshape(b, n, 48))
        return values + self.ff(self.ff_norm(values))

    def forward(self, memory, queries, memory_allowed, query_allowed):
        normed = self.norm(memory)
        key = self.k(normed)[:, None]
        value = self.v(normed)[:, None]
        # Both updates read the PRE-layer bank. Updating memory first and then
        # reading it here would silently change the architecture's depth.
        return (self.attend(memory, key, value, memory_allowed),
                self.attend(queries, key, value, query_allowed))


class TinyKidra(nn.Module):
    def __init__(self):
        super().__init__()
        self.tokens = nn.Embedding(39, 48)
        self.windows = nn.Embedding(4, 48)
        self.positions = nn.Embedding(12, 48)
        self.layers = nn.ModuleList([GlobalSharedKV(), GlobalSharedKV()])
        self.norm = nn.LayerNorm(48)
        self.apply(self.initialize)

    @staticmethod
    def initialize(module):
        if isinstance(module, (nn.Linear, nn.Embedding)):
            nn.init.normal_(module.weight, std=.02)

    def embed(self, tokens, windows, positions):
        return self.tokens(tokens) + self.windows(windows) + self.positions(positions)

    def forward(self, batch):
        memory = self.embed(batch["memory_tokens"], batch["memory_windows"], batch["memory_positions"])
        queries = self.embed(batch["query_previous"], batch["query_windows"], batch["query_positions"])
        for layer in self.layers:
            memory, queries = layer(memory, queries, batch["memory_allowed"], batch["query_allowed"])
        return F.linear(self.norm(queries), self.tokens.weight)  # tied output head


def collate(samples, device="cpu", *, inference_queries=None):
    """Read sampled arrays and form a small dense reference attention batch.

    Full trajectories travel through the model; no detached prefix cache. Dense
    masks are appropriate for this tiny demo, not large production trajectories.
    inference_queries optionally overrides target selection with live queries.
    """
    items = []
    for sample_index, sample in enumerate(samples):
        a = sample.numpy() if hasattr(sample, "numpy") else {n: np.asarray(v) for n, v in sample.items()}
        memory = np.flatnonzero((a["row_flags"] & k.DURABLE) != 0)
        targets = a["target_rows"].astype(np.int64)
        if inference_queries is None:
            previous = a["previous_rows"][targets].astype(np.int64)
            query_previous = a["token_ids"][previous]
            query_windows = a["window_slots"][a["window_ids"][targets].astype(np.int64)]
            query_positions = a["position_ids"][targets]
            frontiers = a["query_frontiers"][targets]
            labels, weights = a["token_ids"][targets], a["loss_weights"][targets]
        else:
            live = inference_queries[sample_index]
            query_previous, query_windows, query_positions, frontiers = map(np.asarray, zip(*live))
            labels, weights = np.zeros(len(live), dtype=np.int64), np.ones(len(live))
        start, end = a["visible_from"][memory], a["visible_until"][memory]
        construction = a["query_frontiers"][memory]
        items.append({"memory_tokens": a["token_ids"][memory],
                      "memory_windows": a["window_slots"][a["window_ids"][memory].astype(np.int64)],
                      "memory_positions": a["position_ids"][memory],
                      "query_previous": query_previous, "query_windows": query_windows,
                      "query_positions": query_positions, "labels": labels, "weights": weights,
                      "memory_allowed": (start[None] <= construction[:, None]) & (construction[:, None] < end[None]),
                      "query_allowed": (start[None] <= frontiers[:, None]) & (frontiers[:, None] < end[None])})
    m = max(len(x["memory_tokens"]) for x in items)
    q = max(len(x["labels"]) for x in items)
    batch = {}
    for name in items[0]:
        if name == "memory_allowed":
            shape, dtype = (len(items), m, m), np.bool_
        elif name == "query_allowed":
            shape, dtype = (len(items), q, m), np.bool_
        else:
            shape = (len(items), m if name.startswith("memory_") else q)
            dtype = np.float32 if name == "weights" else np.int64
        out = np.zeros(shape, dtype=dtype)
        for i, item in enumerate(items):
            value = item[name]
            if value.ndim == 2:
                out[i, :value.shape[0], :value.shape[1]] = value
            else:
                out[i, :len(value)] = value
        batch[name] = torch.from_numpy(out).to(device)
    return batch


def objective(logits, batch):
    losses = F.cross_entropy(logits.reshape(-1, 39), batch["labels"].reshape(-1), reduction="none")
    weights = batch["weights"].reshape(-1)
    return (losses * weights).sum() / weights.sum()


@torch.no_grad()
def evaluate(model, dataset, batch_size, device):
    model.eval()
    total_loss, total, correct, exact = 0., 0, 0, 0
    groups = {"letters": [0, 0], "noop": [0, 0], "eos": [0, 0]}
    for offset in range(0, len(dataset), batch_size):
        b = collate([dataset[i] for i in range(offset, min(len(dataset), offset + batch_size))], device)
        logits = model(b)
        valid = b["weights"] > 0
        matches = logits.argmax(-1) == b["labels"]
        n = int(valid.sum())
        total_loss += float(objective(logits, b)) * n
        total += n
        correct += int((matches & valid).sum())
        exact += int((matches | ~valid).all(-1).sum())
        for name, mask in (("letters", b["labels"] < 26), ("noop", b["labels"] == NOOP),
                           ("eos", b["labels"] == EOS)):
            selected = mask & valid
            groups[name][0] += int((matches & selected).sum())
            groups[name][1] += int(selected.sum())
    return {"loss": total_loss / total, "token_accuracy": correct / total,
            "exact_trajectory_accuracy": exact / len(dataset),
            **{name + "_accuracy": a / b if b else 0. for name, (a, b) in groups.items()}}


@torch.no_grad()
def rollout(model, sources, device):
    """Teacher-free execution: only external stimuli, model outputs and BOS.

    Ground-truth outputs are used solely for scoring afterwards. An error cannot
    be corrected by injecting a later authored output into the model's context.
    """
    model.eval()
    exact, letters_correct, premature, failures, examples = 0, 0, 0, 0, []
    for source in sources:
        external, expected = {}, {}
        for tick, events in source["replay"].items():
            for event in events:
                if event["op"] == "append":
                    external.setdefault(int(tick), []).append(({"letters": 0, "digits": 1}[event["window"]], VOCAB[event["content"]]))
                elif event["op"] == "generate":
                    expected[int(tick)] = VOCAB[event["content"]]
        runtime = Execution()
        observed, malformed = {}, False
        # Bounded evaluation horizon also catches late/missing EOS and no wake.
        for tick in range(max(expected) + 12):
            updates = external.get(tick, [])
            if runtime.sleeping and not updates:
                continue
            for window, token in updates:
                runtime.external(token, window, tick)
            live_windows = runtime.query_windows()
            queries = []
            for window in live_windows:
                previous = runtime.latest[window]
                queries.append((runtime.a["token_ids"][previous], window,
                                runtime.a["position_ids"][previous] + 1, runtime.frontier))
            if any(q[2] >= 12 for q in queries):
                malformed = True
                break
            logits = model(collate([runtime.a], device, inference_queries=[queries]))
            predictions = dict(zip(live_windows, logits[0, :len(queries)].argmax(-1).tolist()))
            token = predictions.get(2, NOOP)
            # The reserve is a genuine trained prediction, not forced to NOOP.
            if predictions.get(3, NOOP) != NOOP or token == BOS or 26 <= token < 36:
                malformed = True
                break
            if token != NOOP:
                observed[tick] = token
                if token < 26 and expected.get(tick, NOOP) == NOOP:
                    premature += 1
            runtime.commit(predictions, tick)
            if runtime.closed:
                break
        matches = sum(observed.get(t) == value for t, value in expected.items() if value < 26)
        letters_correct += matches
        passed = not malformed and observed == expected
        exact += int(passed)
        failures += int(malformed)
        if len(examples) < 3:
            examples.append({"id": source["id"], "expected": expected, "observed": observed,
                             "exact": passed, "malformed": malformed})
    return {"trajectories": len(sources), "exact_timing_and_closure_accuracy": exact / len(sources),
            "letters_at_correct_tick_accuracy": letters_correct / (10 * len(sources)),
            "premature_letters": premature, "malformed_trajectories": failures, "examples": examples}


def synchronize(device):
    if torch.device(device).type == "cuda":
        torch.cuda.synchronize(device)


def train(directory, *, steps=3000, batch_size=16, seed=17, device="cpu", eval_every=500,
          learning_rate=.002, rollout_count=64):
    directory = Path(directory)
    torch.manual_seed(seed)
    rng = random.Random(seed)
    model = TinyKidra().to(device)
    count = sum(p.numel() for p in model.parameters())
    assert count == 49968, count
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=.01)
    timing = {"sample_seconds": 0., "collate_and_transfer_seconds": 0.,
              "forward_backward_optimizer_seconds": 0., "evaluation_seconds": 0.}
    target_count, row_count, history = 0, 0, []
    with k.Dataset(sorted(directory.glob("train-*.ktremix")), verify_index=True) as data, \
         k.Dataset(sorted(directory.glob("validation-*.ktremix")), verify_index=True) as validation:
        for shard in data.shards + validation.shards:
            if shard.manifest["execution_contract"] != CONTRACT:
                raise ValueError("Wrong training execution contract")
        synchronize(device)
        before = time.perf_counter()
        initial = evaluate(model, validation, batch_size, device)
        synchronize(device)
        timing["evaluation_seconds"] += time.perf_counter() - before
        print(json.dumps({"parameters": count, "initial_validation": initial}), flush=True)
        start = time.perf_counter()
        for step in range(1, steps + 1):
            model.train()
            before = time.perf_counter()
            samples = [data.sample(rng) for _ in range(batch_size)]
            timing["sample_seconds"] += time.perf_counter() - before
            before = time.perf_counter()
            batch = collate(samples, device)
            synchronize(device)
            timing["collate_and_transfer_seconds"] += time.perf_counter() - before
            target_count += sum(sample.targets for sample in samples)
            row_count += sum(sample.rows for sample in samples)
            before = time.perf_counter()
            optimizer.zero_grad(set_to_none=True)
            loss = objective(model(batch), batch)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.)
            optimizer.step()
            synchronize(device)
            timing["forward_backward_optimizer_seconds"] += time.perf_counter() - before
            if step % eval_every == 0 or step == steps:
                before = time.perf_counter()
                metrics = evaluate(model, validation, batch_size, device)
                synchronize(device)
                timing["evaluation_seconds"] += time.perf_counter() - before
                entry = {"step": step, "train_loss": float(loss.detach()), "validation": metrics}
                history.append(entry)
                print(json.dumps(entry), flush=True)
        train_elapsed = time.perf_counter() - start
        source_lines = (directory / "validation.jsonl").read_text().splitlines()[:rollout_count]
        before = time.perf_counter()
        free_running = rollout(model, [json.loads(line) for line in source_lines], device)
        synchronize(device)
        timing["evaluation_seconds"] += time.perf_counter() - before
    active_seconds = sum(timing[n] for n in ("sample_seconds", "collate_and_transfer_seconds", "forward_backward_optimizer_seconds"))
    # Saving a state_dict is for the demo's locally produced checkpoint only.
    torch.save(model.state_dict(), directory / "model.pt")
    report = {"parameters": count, "layers": 2, "vocabulary": 39, "non_special_tokens": 36,
              "device": str(device), "torch": torch.__version__, "threads": torch.get_num_threads(),
              "python": platform.python_version(), "platform": platform.platform(),
              "steps": steps, "batch_size": batch_size, "seed": seed, "learning_rate": learning_rate,
              "initial_validation": initial, "history": history, "rollout": free_running,
              "timing": timing, "train_wall_seconds_including_periodic_validation": train_elapsed,
              "training_trajectories_per_second": steps * batch_size / active_seconds,
              "training_rows_per_second": row_count / active_seconds,
              "supervised_targets_per_second": target_count / active_seconds}
    (directory / "training.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"training_trajectories_per_second": report["training_trajectories_per_second"],
                      "rollout": free_running}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "generate", "train"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--train-samples", type=int, default=2048)
    parser.add_argument("--validation-samples", type=int, default=256)
    parser.add_argument("--shard-size", type=int, default=256)
    parser.add_argument("--max-gap", type=int, default=5)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--steps", type=int, default=3000)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--eval-every", type=int, default=500)
    parser.add_argument("--rollout-count", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=.002)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--check", action="store_true", help="fail unless held-out letters >=99%% and exact free rollouts >=95%%")
    args = parser.parse_args()
    for name in ("train_samples", "validation_samples", "shard_size", "max_gap", "steps", "batch_size",
                 "eval_every", "rollout_count", "threads"):
        if getattr(args, name) < 1:
            parser.error(f"{name} must be positive")
    if args.learning_rate <= 0:
        parser.error("learning-rate must be positive")
    if args.check and args.command == "generate":
        parser.error("--check requires run or train")
    torch.set_num_threads(args.threads)
    started = time.perf_counter()
    if args.command in ("run", "generate"):
        report = build_dataset(args.directory, train_samples=args.train_samples,
                               validation_samples=args.validation_samples, shard_size=args.shard_size,
                               seed=args.seed, max_gap=args.max_gap)
        print(json.dumps({"build": report}), flush=True)
    if args.command in ("run", "train"):
        report = train(args.directory, steps=args.steps, batch_size=args.batch_size, seed=args.seed,
              device=args.device, eval_every=args.eval_every, learning_rate=args.learning_rate,
              rollout_count=args.rollout_count)
    (args.directory / "last-command.json").write_text(json.dumps({"command": args.command,
        "wall_seconds": time.perf_counter() - started, "options": vars(args)}, default=str, indent=2) + "\n")
    if args.check:
        if (report["history"][-1]["validation"]["letters_accuracy"] < .99 or
                report["rollout"]["exact_timing_and_closure_accuracy"] < .95):
            parser.exit(1, "Training check failed; inspect training.json for accuracy and timing errors.\n")


if __name__ == "__main__":
    main()
