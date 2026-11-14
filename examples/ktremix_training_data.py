"""Letter-addition fixtures: raw Remix sources -> compiler -> KTREMIX arrays.

This deliberately narrow export adapter exercises the existing replay compiler.
It adds the agreed Available-window/read-only/sleep behavior for this fixture;
it is not the future general dependency-graph compiler.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import string
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tokenizers import Tokenizer, models, pre_tokenizers
from trajectory_remix.compiler import Compiler
import ktremix_dataset as k

SYMBOLS = list(string.ascii_lowercase + string.digits)
BOS, EOS, NOOP = 36, 37, 38
VOCAB = {text: i for i, text in enumerate(SYMBOLS + ["<|bos|>", "<|eos|>", "<|noop|>"])}
CONTRACT = "ktremix.letter-addition-progressive/1"


def tokenizer():
    result = Tokenizer(models.WordLevel(VOCAB))
    result.pre_tokenizer = pre_tokenizers.Split("", behavior="isolated")
    result.add_special_tokens(["<|bos|>", "<|eos|>", "<|noop|>"])
    return result


def make_source(seed, index=0, split="train", max_gap=5):
    if max_gap < 1:
        raise ValueError("max_gap must be positive")
    rng = random.Random(f"{seed}:{split}:{index}")
    letters = [rng.choice(string.ascii_lowercase) for _ in range(10)]
    digits = [rng.randrange(10) for _ in range(10)]

    # Give each input stream its own arrival schedule so the result becomes
    # available progressively rather than as one preassembled example.
    arrivals = []
    for _ in range(2):
        tick, times = 0, []
        for _ in range(10):
            tick += rng.randint(1, max_gap)
            times.append(tick)
        arrivals.append(times)

    replay = {}

    def add(tick, event):
        replay.setdefault(str(tick), []).append(event)

    streams = ("letters", "digits")
    values_by_stream = (letters, list(map(str, digits)))
    for wid, values, times in zip(streams, values_by_stream, arrivals):
        add(times[0], {"op": "open", "window": wid, "window-type": "document"})
        for value, tick in zip(values, times):
            add(
                tick,
                {
                    "op": "append",
                    "window": wid,
                    "source": "user",
                    "content": value,
                    "loss": {"mask": "exclude"},
                },
            )

    # A result can be predicted as soon as both corresponding input values arrive.
    output_ticks = [max(a, b) for a, b in zip(*arrivals)]
    add(
        output_ticks[0],
        {"op": "open", "window": "result", "window-type": "reasoning"},
    )
    for letter, digit, tick in zip(letters, digits, output_ticks):
        value = chr((ord(letter) - ord('a') + digit) % 26 + ord('a'))
        add(tick, {"op": "generate", "window": "result", "content": value})
    add(output_ticks[-1] + 1, {"op": "generate", "window": "result", "content": "<|eos|>"})
    ident = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ktremix-letter-addition/{seed}/{split}/{index}"))
    return {"schema-version": "1.0", "id": ident, "uuid": ident,
            "tick-policy": "external-first",
            "defaults": {"window-headers": False, "implicit-noop-loss-weight": 1.0},
            "metadata": {"split": split, "demo": CONTRACT},
            "replay": dict(sorted(replay.items(), key=lambda pair: int(pair[0])))}


def new_columns():
    return {name: [] for name in k.BY_NAME}


class Execution:
    """Fixture runtime shared by export and free-running evaluation.

    Model queries see only externally committed text and earlier model commits.
    The output begins as the Available BOS. First text activates it and creates
    another reserve. NOOPs are decisions, never memory. Read-only inputs persist.
    """
    def __init__(self):
        self.a = new_columns()
        self.frontier = 0
        self.latest = {}
        self.activated, self.closed, self.sleeping = False, False, False
        self.ticks, self.row_ticks = [], []
        self.opened_inputs = set()
        # Windows 0/1 acquire actual open times when their first external text arrives.
        for name, values in {
            "window_slots": [0, 1, 2],
            "window_open_frontiers": [0, 0, 0],
            "window_close_frontiers": [k.FOREVER] * 3,
            "window_activation_frontiers": [k.FOREVER] * 3,
            "window_flags": [k.READ_ONLY, k.READ_ONLY, k.INITIALLY_AVAILABLE],
        }.items():
            self.a[name] = values
        self.a["frame_offsets"] = [0]
        self.insert(BOS, 2, k.DURABLE | k.BOS, 0, 0)

    def insert(self, token, window, flags, loss, tick, *, commit=None):
        a, q = self.a, self.frontier
        index = len(a["token_ids"])
        previous = self.latest.get(window, k.NONE)
        position = 0 if previous == k.NONE else a["position_ids"][previous] + 1
        durable = bool(flags & k.DURABLE)

        # A durable row becomes visible only after its construction frontier.
        # Model rows in one frame share a commit frontier and cannot see each other.
        visible_from = 0
        if durable:
            visible_from = q + 1 if commit is None else commit

        for name, value in {
            "token_ids": token,
            "window_ids": window,
            "position_ids": position,
            "query_frontiers": q,
            "visible_from": visible_from,
            "visible_until": k.FOREVER if durable else 0,
            "previous_rows": previous,
            "loss_weights": loss,
            "row_flags": flags,
        }.items():
            a[name].append(value)
        if loss > 0:
            a["target_rows"].append(index)
        self.row_ticks.append(tick)
        if durable:
            self.latest[window] = index
            if commit is None:
                self.frontier += 1
        return index

    def external(self, token, window, tick):
        if window not in self.opened_inputs:
            self.a["window_open_frontiers"][window] = self.frontier
            self.insert(BOS, window, k.DURABLE | k.BOS, 0, tick)
            self.opened_inputs.add(window)
        self.insert(token, window, k.DURABLE, 0, tick)
        if self.a["window_activation_frontiers"][window] == k.FOREVER:
            self.a["window_activation_frontiers"][window] = self.frontier

    def query_windows(self):
        windows = []
        if not self.closed:
            windows.append(2)
        if self.activated:
            windows.append(3)
        return windows

    def commit(self, predictions, tick):
        a, boundary = self.a, self.frontier + 1
        activate = False

        # All predictions for this tick are recorded at one shared boundary.
        # NOOP rows carry loss but never become durable context for later queries.
        for window, token in predictions.items():
            if token == NOOP:
                flags = k.MODEL | k.NOOP
            elif token == EOS:
                flags = k.MODEL | k.EOS
            else:
                flags = k.MODEL | k.DURABLE
            self.insert(token, window, flags, 1., tick, commit=boundary)
            if window == 2 and token not in (NOOP, EOS, BOS) and not self.activated:
                activate = True
            if window == 2 and token == EOS:
                self.closed = True
                a["window_close_frontiers"][2] = boundary
                for row, wid in enumerate(a["window_ids"]):
                    if wid == 2 and a["row_flags"][row] & k.DURABLE:
                        a["visible_until"][row] = boundary
        self.frontier = boundary
        if activate:
            self.activated = True
            a["window_activation_frontiers"][2] = boundary
            new_window = {
                "window_slots": 3,
                "window_open_frontiers": boundary,
                "window_close_frontiers": k.FOREVER,
                "window_activation_frontiers": k.FOREVER,
                "window_flags": k.INITIALLY_AVAILABLE,
            }
            for name, value in new_window.items():
                a[name].append(value)
            self.insert(BOS, 3, k.DURABLE | k.BOS, 0, tick)
        self.sleeping = all(token == NOOP for token in predictions.values())
        self.ticks.append(tick)
        a["frame_offsets"].append(len(a["token_ids"]))


def lower_plan(plan):
    """Compile this fixture's plan into the explicit frontier-array contract."""
    runtime = Execution()
    external, expected = {}, {}
    for row in plan["rows"]:
        if row["op"] == "append":
            window = {"letters": 0, "digits": 1}[row["window"]]
            external.setdefault(row["tick"], []).append((window, row["token_id"]))
        elif row["phase"] == "D" and row["window"] == "result":
            expected[row["tick"]] = row["token_id"]
    final_tick = max(expected)
    for tick in range(final_tick + 1):
        updates = external.get(tick, [])
        if runtime.sleeping and not updates:
            continue
        for window, token in updates:
            runtime.external(token, window, tick)
        predictions = {wid: (expected.get(tick, NOOP) if wid == 2 else NOOP)
                       for wid in runtime.query_windows()}
        runtime.commit(predictions, tick)
    return runtime.a, {"model_ticks": runtime.ticks, "row_ticks": runtime.row_ticks,
                       "wall_ticks": final_tick + 1, "peak_active_windows": 3}


def build_dataset(directory, *, train_samples=2048, validation_samples=256,
                  shard_size=256, seed=17, max_gap=5):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    tok = tokenizer()
    tok.save(str(directory / "tokenizer.json"))
    compiler = Compiler(tok, slot_policy="declaration-v1")
    report = {"generation_seconds": 0., "compilation_seconds": 0., "write_seconds": 0.,
              "trajectories": train_samples + validation_samples, "rows": 0, "targets": 0}
    dataset_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{CONTRACT}/{seed}/{max_gap}")
    splits = (("train", train_samples), ("validation", validation_samples))
    for split, count in splits:
        raw_path = directory / f"{split}.jsonl"
        start = time.perf_counter()
        with raw_path.open("w") as stream:
            for i in range(count):
                stream.write(json.dumps(make_source(seed, i, split, max_gap)) + "\n")
        report["generation_seconds"] += time.perf_counter() - start
        with raw_path.open() as stream:
            for shard_index, offset in enumerate(range(0, count, shard_size)):
                def records():
                    for _ in range(min(shard_size, count - offset)):
                        source = json.loads(next(stream))
                        before = time.perf_counter()
                        plan = compiler.compile(source, incremental=False)
                        columns, metadata = lower_plan(plan)
                        report["compilation_seconds"] += time.perf_counter() - before
                        report["rows"] += len(columns["token_ids"])
                        report["targets"] += len(columns["target_rows"])
                        source_digest = hashlib.sha256(k.canonical(source)).hexdigest()
                        metadata.update(split=split, source_sha256=source_digest)
                        yield source["uuid"], columns, metadata
                manifest = {
                    "dataset_id": str(dataset_id),
                    "shard_id": str(uuid.uuid5(dataset_id, f"{split}/{shard_index}")),
                    "execution_contract": CONTRACT,
                    "split": split,
                    "shard_index": shard_index,
                    "active_window_limit": 3,
                    "tokenizer": {
                        "sha256": compiler.fingerprint,
                        "special_token_ids": {"bos": BOS, "eos": EOS, "noop": NOOP},
                    },
                    "compiler": "Remix replay + fixture-only progressive/sleep adapter",
                }
                before = time.perf_counter()
                compiled_before = report["compilation_seconds"]
                shard_path = directory / f"{split}-{shard_index:04}.ktremix"
                k.write_shard(
                    shard_path,
                    records(),
                    manifest,
                    sample_count=min(shard_size, count - offset),
                    codec="lz4",
                )
                write_elapsed = time.perf_counter() - before
                compile_elapsed = report["compilation_seconds"] - compiled_before
                report["write_seconds"] += write_elapsed - compile_elapsed
    report["compressed_bytes"] = sum(p.stat().st_size for p in directory.glob("*.ktremix"))
    report["raw_source_bytes"] = sum(p.stat().st_size for p in directory.glob("*.jsonl"))
    (directory / "build.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
