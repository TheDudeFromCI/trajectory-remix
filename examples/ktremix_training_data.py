"""Letter-addition graph fixtures compiled by the production graph compiler."""

from __future__ import annotations

import hashlib
import json
import random
import string
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tokenizers import Regex, Tokenizer, models, pre_tokenizers

from trajectory_remix.graph import ktremix as k
from trajectory_remix.graph.compiler import CONTRACT, GraphCompiler
from trajectory_remix.graph.models import REQUIRED_CONTROLS, GraphSource, TokenizerProfile

SYMBOLS = list(string.ascii_lowercase + string.digits)
CONTROL_NAMES = sorted(REQUIRED_CONTROLS)
# Structural punctuation gets dedicated framing vocabulary entries. The ordinary
# task alphabet remains the requested 36 letters/digits; controls are never typed
# into source content. No unknown token can silently discard required headers.
FRAMING = list(string.punctuation + " \n") + [
    '"slot"',
    '"purpose"',
    '"reasoning_effort"',
    '"document"',
    '"reasoning"',
    "null",
]
VOCAB = {
    text: i for i, text in enumerate(SYMBOLS + [f"<|{name}|>" for name in CONTROL_NAMES] + FRAMING)
}
CONTROLS = {name: VOCAB[f"<|{name}|>"] for name in CONTROL_NAMES}
BOS, EOS, NOOP = (CONTROLS[n] for n in ("bos", "eos", "noop"))


def tokenizer():
    result = Tokenizer(models.WordLevel(VOCAB))
    # Ordinary task symbols stay one token each. Fixed protocol text is
    # encoded like BPE wordpieces rather than dozens of character tokens.
    macros = "|".join(__import__("re").escape(value) for value in FRAMING if len(value) > 1)
    result.pre_tokenizer = pre_tokenizers.Split(Regex(macros + "|[\\s\\S]"), behavior="isolated")
    result.add_special_tokens([f"<|{name}|>" for name in CONTROL_NAMES] + FRAMING)
    return result


def profile():
    return TokenizerProfile(
        name="addition", definition=json.loads(tokenizer().to_str()), special_tokens=CONTROLS
    )


def compiler(seed=17):
    return GraphCompiler(profile(), active_limit=3, seed=seed)


def make_source(seed, index=0, split="train", max_gap=5):
    """Vary input ordering with real dependencies, never authored tick delays.

    Each stream advances independently of agent outputs. max_gap is retained as
    a CLI knob controlling how often one input additionally follows its peer;
    it no longer means a fabricated wall-clock duration.
    """
    if max_gap < 1:
        raise ValueError("max_gap must be positive")
    rng = random.Random(f"{seed}:{split}:{index}")
    letters = [rng.choice(string.ascii_lowercase) for _ in range(10)]
    digits = [rng.randrange(10) for _ in range(10)]
    events = {
        "header": dict(
            kind="agent_text", window="result", content="", after=[], supervision={"weight": 0.1}
        )
    }
    previous = "header"
    for i, (letter, digit) in enumerate(zip(letters, digits)):
        left, right, output = f"letter-{i}", f"digit-{i}", f"sum-{i}"
        events[left] = dict(
            kind="external_text",
            window="letters",
            content=letter,
            after=[f"letter-{i - 1}"] if i else [],
            delivery={"chain_immediately": False},
        )
        events[right] = dict(
            kind="external_text",
            window="digits",
            content=str(digit),
            after=[f"digit-{i - 1}"] if i else [],
            delivery={"chain_immediately": False},
        )
        if rng.randrange(max_gap) != 0:
            late, early = (left, right) if rng.randrange(2) else (right, left)
            events[late]["after"].append(early)
        events[output] = dict(
            kind="agent_text",
            window="result",
            content=chr((ord(letter) - 97 + digit) % 26 + 97),
            after=[left, right, previous],
            close_window=i == 9,
            supervision={"weight": 5.0},
        )
        previous = output
    ident = str(uuid.uuid5(uuid.NAMESPACE_URL, f"graph-addition/3/{seed}/{split}/{index}"))
    return GraphSource.model_validate(
        dict(
            format="kidra-trajectory-graph/1",
            id=ident,
            defaults={"noop_loss_weight": 0.1},
            windows={
                "letters": dict(purpose="document", agent_writable=False),
                "digits": dict(purpose="document", agent_writable=False),
                "result": dict(purpose="reasoning", agent_writable=True),
            },
            events=events,
            metadata={
                "split": split,
                "demo": "graph-addition/3",
                "timing_policy": "earliest-legal-after-streamed-header-and-prior-output",
            },
        )
    ).to_dict()


def timing_report(source, frames):
    """Separate the legal compiled schedule from the original demo requirement.

    No model or training is involved. Header latency cannot be hidden by
    delaying external inputs or by calling the compiled schedule user acceptance.
    """
    source = GraphSource.model_validate(source)
    finishes, starts, letters = {}, {}, {}
    for frame in frames:
        for emission in (*frame.inputs, *frame.outputs):
            if emission.event:
                starts.setdefault(emission.event, frame.tick)
                finishes[emission.event] = frame.tick
                if emission.event.startswith("sum-") and any(
                    not t.header and t.control is None for t in emission.tokens
                ):
                    letters[emission.event] = frame.tick
    rows = []
    for i in range(10):
        event = f"sum-{i}"
        ready = max(finishes[f"letter-{i}"], finishes[f"digit-{i}"])
        legal = max(ready, finishes["header" if i == 0 else f"sum-{i - 1}"] + 1)
        rows.append(
            dict(
                event=event,
                input_ready_tick=ready,
                earliest_legal_tick=legal,
                compiled_letter_tick=letters[event],
            )
        )
    independent = all(
        source.events[dep].kind == "external_text"
        for event in source.events.values()
        if event.kind == "external_text"
        for dep in event.after
    )
    return dict(
        requirement="earliest-legal-after-streamed-header-and-prior-output",
        external_inputs_independent=independent,
        input_ready_timing_satisfied=all(
            r["input_ready_tick"] == r["compiled_letter_tick"] for r in rows
        ),
        header_start_tick=starts["header"],
        header_finish_tick=finishes["header"],
        sums=rows,
    )


def check_training_report(report):
    """Apply the user-machine gate independently of model execution."""
    if not report["rollout"]["external_inputs_independent"]:
        raise ValueError(
            "External inputs depend on agent outputs: regenerate this legacy demo dataset."
        )
    if (
        report["history"][-1]["validation"]["letters_accuracy"] < 0.99
        or report["rollout"]["exact_timing_and_closure_accuracy"] < 0.95
    ):
        raise ValueError(
            "Training check failed; inspect training.json for accuracy and timing errors."
        )


def build_dataset(
    directory, *, train_samples=2048, validation_samples=256, shard_size=256, seed=17, max_gap=5
):
    if shard_size < 1:
        raise ValueError("shard_size must be positive")
    directory = Path(directory).resolve()
    if not directory.is_relative_to(Path("/tmp").resolve()):
        raise ValueError("Demo datasets must be generated under /tmp")
    directory.mkdir(parents=True, exist_ok=False)
    c = compiler(seed)
    (directory / "tokenizer.json").write_text(c.tokenizer.to_str())
    report = dict(
        trajectories=train_samples + validation_samples,
        rows=0,
        targets=0,
        compiler="kidra.graph-compiler/1",
    )
    dataset_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{CONTRACT}/addition/3/{seed}/{max_gap}")
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

                k.write_shard(
                    directory / f"{split}-{shard:04}.ktremix",
                    records(),
                    dict(
                        dataset_id=str(dataset_id),
                        shard_id=str(uuid.uuid5(dataset_id, f"{split}/{shard}")),
                        split=split,
                        execution_contract=CONTRACT,
                        compiler=report["compiler"],
                        active_window_limit=3,
                        seed=seed,
                        tokenizer={
                            "sha256": hashlib.sha256(c.tokenizer.to_str().encode()).hexdigest(),
                            "special_token_ids": CONTROLS,
                        },
                    ),
                    sample_count=min(shard_size, count - start),
                )
    report["compressed_bytes"] = sum(p.stat().st_size for p in directory.glob("*.ktremix"))
    report["raw_source_bytes"] = sum(p.stat().st_size for p in directory.glob("*.jsonl"))
    (directory / "build.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
