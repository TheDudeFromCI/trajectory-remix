"""Reference export reader and exact attention-mask adapter.

No model is bundled. A Kidra model must consume target queries independently of
committed memory tokens; feeding the target token as its own query leaks labels.
"""

from __future__ import annotations

import json
from pathlib import Path

from .compiler import visible_to


def read_plans(path: str | Path):
    """Yield reassembled plans, rejecting missing chunks and mixed tokenizers."""
    current, expected, rows, first = None, 0, [], None
    finished = set()
    with open(path, encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            chunk = json.loads(line)
            if chunk.get("format") != "kidra-token-chunk/1":
                raise ValueError(f"Line {line_no}: unsupported export format")
            if chunk["sample_id"] != current:
                if current is not None:
                    if (
                        expected != first["chunk_count"]
                        or len(rows) != first["sample_row_count"]
                    ):
                        raise ValueError("Incomplete sample: missing suffix chunk")
                    finished.add(current)
                    yield {**first, "rows": rows}
                current, expected, rows, first = chunk["sample_id"], 0, [], chunk
                if current in finished:
                    raise ValueError("Sample chunks must be contiguous")
            if (
                chunk["chunk_index"] != expected
                or chunk["requires_prefix_through_row"] != len(rows) - 1
            ):
                raise ValueError(f"Line {line_no}: missing/reordered prefix chunk")
            if (
                chunk["tokenizer_sha256"] != first["tokenizer_sha256"]
                or chunk["windows"] != first["windows"]
            ):
                raise ValueError("Tokenizer/lifecycle metadata changed within a sample")
            if any(r["index"] != len(rows) + i for i, r in enumerate(chunk["rows"])):
                raise ValueError("Missing or reordered token rows")
            rows.extend(chunk["rows"])
            expected += 1
        if current is not None:
            if (
                expected != first["chunk_count"]
                or len(rows) != first["sample_row_count"]
            ):
                raise ValueError("Incomplete sample: missing suffix chunk")
            yield {**first, "rows": rows}


def query_batch(plan, target_rows):
    """Pure-Python reference; True means a memory key is visible.

    For short tests/debugging only: dense masks cost O(targets * durable_tokens).
    Production implementations should build a block-sparse mask from tick, phase,
    window lifetime and input_rank. Loss masks never affect key visibility.
    """
    memory = [r for r in plan["rows"] if r["durable"]]
    windows = {w["id"]: i for i, w in enumerate(plan["windows"])}
    targets = [r for r in target_rows if r["loss_weight"] > 0]
    allowed = [[visible_to(q, k, plan["windows"]) for k in memory] for q in targets]
    previous = []
    for query, mask in zip(targets, allowed):
        own = [
            k for k, yes in zip(memory, mask) if yes and k["window"] == query["window"]
        ]
        if not own:
            raise ValueError("A supervised query must have an opened BOS prefix")
        previous.append(own[-1]["token_id"])
    return {
        "memory_token_ids": [r["token_id"] for r in memory],
        "memory_window_ids": [windows[r["window"]] for r in memory],
        "memory_positions": [r["position"] for r in memory],
        "memory_ticks": [r["tick"] for r in memory],
        "query_previous_token_ids": previous,
        "query_window_ids": [windows[r["window"]] for r in targets],
        "query_ticks": [r["tick"] for r in targets],
        "query_positions": [r["position"] for r in targets],
        "labels": [r["token_id"] for r in targets],
        "loss_weights": [r["loss_weight"] for r in targets],
        "attention_allowed": allowed,
    }


def to_torch(batch, device="cpu"):
    """Optional PyTorch conversion; torch is intentionally not a UI dependency."""
    import torch

    return {
        name: torch.tensor(
            value,
            dtype=torch.bool
            if name == "attention_allowed"
            else torch.float32
            if name == "loss_weights"
            else torch.long,
            device=device,
        )
        for name, value in batch.items()
    }
