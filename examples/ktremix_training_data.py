"""Letter-addition graph fixtures compiled by the production graph compiler."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import string
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tokenizers import Tokenizer, models, pre_tokenizers
from trajectory_remix.graph.models import GraphSource, TokenizerProfile, REQUIRED_CONTROLS
from trajectory_remix.graph.compiler import GraphCompiler, CONTRACT, Runtime, Frame, Emission, Token
from trajectory_remix.graph import ktremix as k

SYMBOLS = list(string.ascii_lowercase + string.digits)
CONTROL_NAMES = sorted(REQUIRED_CONTROLS)
# Structural punctuation gets dedicated framing vocabulary entries. The ordinary
# task alphabet remains the requested 36 letters/digits; controls are never typed
# into source content. No unknown token can silently discard required headers.
FRAMING = list(string.punctuation + " \n")
VOCAB = {text: i for i, text in enumerate(
    SYMBOLS + [f"<|{name}|>" for name in CONTROL_NAMES] + FRAMING)}
CONTROLS = {name: VOCAB[f"<|{name}|>"] for name in CONTROL_NAMES}
BOS, EOS, NOOP = (CONTROLS[n] for n in ("bos", "eos", "noop"))


def tokenizer():
    result = Tokenizer(models.WordLevel(VOCAB))
    result.pre_tokenizer = pre_tokenizers.Split("", behavior="isolated")
    result.add_special_tokens([f"<|{name}|>" for name in CONTROL_NAMES] + FRAMING)
    return result


def profile():
    return TokenizerProfile(name="addition", definition=json.loads(tokenizer().to_str()), special_tokens=CONTROLS)


def compiler(seed=17):
    return GraphCompiler(profile(), active_limit=3, seed=seed)


def make_source(seed, index=0, split="train", max_gap=5):
    """Vary input ordering with real dependencies, never authored tick delays.

    max_gap is retained as a CLI knob controlling the frequency of next-phase
    input dependencies. It no longer means a fabricated wall-clock duration.
    """
    if max_gap < 1:
        raise ValueError("max_gap must be positive")
    rng = random.Random(f"{seed}:{split}:{index}")
    letters = [rng.choice(string.ascii_lowercase) for _ in range(10)]
    digits = [rng.randrange(10) for _ in range(10)]
    events = {"header": dict(kind="agent_text", window="result", content="", after=[])}
    previous = "header"
    for i, (letter, digit) in enumerate(zip(letters, digits)):
        left, right, output = f"letter-{i}", f"digit-{i}", f"sum-{i}"
        events[left] = dict(kind="external_text", window="letters", content=letter,
                            after=[previous], delivery={"chain_immediately": False})
        events[right] = dict(kind="external_text", window="digits", content=str(digit),
                             after=[previous], delivery={"chain_immediately": False})
        if rng.randrange(max_gap) != 0:
            late, early = (left, right) if rng.randrange(2) else (right, left)
            events[late]["after"] = [early]
        events[output] = dict(kind="agent_text", window="result",
                              content=chr((ord(letter) - 97 + digit) % 26 + 97),
                              after=[left, right, previous], close_window=i == 9)
        previous = output
    ident = str(uuid.uuid5(uuid.NAMESPACE_URL, f"graph-addition/{seed}/{split}/{index}"))
    return GraphSource.model_validate(dict(
        format="kidra-trajectory-graph/1", id=ident,
        defaults={"noop_loss_weight": 1.0},
        windows={"letters": dict(purpose="document", agent_writable=False),
                 "digits": dict(purpose="document", agent_writable=False),
                 "result": dict(purpose="reasoning", agent_writable=True)},
        events=events, metadata={"split": split, "demo": "graph-addition/1"})).to_dict()


def build_dataset(directory, *, train_samples=2048, validation_samples=256,
                  shard_size=256, seed=17, max_gap=5):
    if shard_size < 1:
        raise ValueError("shard_size must be positive")
    directory = Path(directory).resolve()
    if not directory.is_relative_to(Path("/tmp").resolve()):
        raise ValueError("Demo datasets must be generated under /tmp")
    directory.mkdir(parents=True, exist_ok=False)
    c = compiler(seed)
    (directory / "tokenizer.json").write_text(c.tokenizer.to_str())
    report = dict(trajectories=train_samples + validation_samples, rows=0, targets=0,
                  compiler="kidra.graph-compiler/1")
    dataset_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{CONTRACT}/addition/{seed}/{max_gap}")
    for split, count in (("train", train_samples), ("validation", validation_samples)):
        with (directory / f"{split}.jsonl").open("x") as raw:
            for shard, start in enumerate(range(0, count, shard_size)):
                def records():
                    for index in range(start, min(count, start + shard_size)):
                        source = make_source(seed, index, split, max_gap)
                        raw.write(json.dumps(source) + "\n")
                        arrays, metadata = c.compile(source)
                        report["rows"] += len(arrays["token_ids"])
                        report["targets"] += len(arrays["target_rows"])
                        yield source["id"], arrays, metadata
                k.write_shard(directory / f"{split}-{shard:04}.ktremix", records(),
                              dict(dataset_id=str(dataset_id), shard_id=str(uuid.uuid5(dataset_id, f"{split}/{shard}")),
                                   split=split, execution_contract=CONTRACT,
                                   compiler=report["compiler"], active_window_limit=3, seed=seed,
                                   tokenizer={"sha256": hashlib.sha256(
                                       c.tokenizer.to_str().encode()).hexdigest(), "special_token_ids": CONTROLS}),
                              sample_count=min(shard_size, count - start))
    report["compressed_bytes"] = sum(p.stat().st_size for p in directory.glob("*.ktremix"))
    report["raw_source_bytes"] = sum(p.stat().st_size for p in directory.glob("*.jsonl"))
    (directory / "build.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
