"""Build a character-level HF tokenizer and tokenizer-independent demo fixtures."""

import json
from pathlib import Path

from tokenizers import AddedToken, Tokenizer, decoders, models, pre_tokenizers

root = Path(__file__).parent
special = ["<|unk|>", "<|bos|>", "<|eos|>", "<|noop|>"]
chars = [chr(i) for i in range(32, 127)] + ["\n", "\t"]
vocab = {s: i for i, s in enumerate(special + chars)}
tokenizer = Tokenizer(models.BPE(vocab=vocab, merges=[], unk_token=special[0]))
tokenizer.pre_tokenizer = pre_tokenizers.Split("", behavior="isolated")
tokenizer.decoder = decoders.Fuse()
tokenizer.add_special_tokens(
    [AddedToken(s, special=True, normalized=False) for s in special]
)
tokenizer.save(str(root / "demo-tokenizer.json"))
sample = {
    "schema-version": "1.0",
    "id": "react-to-stream-warning",
    "tick-policy": "external-first",
    "defaults": {"implicit-noop-loss-weight": 0.1},
    "metadata": {"tags": ["software", "interruption", "tool-use"]},
    "replay": {
        "0": [
            {"op": "open", "window": "system", "window-type": "system"},
            {
                "op": "append",
                "window": "system",
                "source": "system",
                "content": "You are Kidra. Coordinate tasks and react to failures.",
            },
            {"op": "open", "window": "user", "window-type": "chat"},
            {
                "op": "append",
                "window": "user",
                "source": "user",
                "content": "Scan the archive. Tell me if the tool encounters a problem.",
            },
            {"op": "open", "window": "reasoning", "window-type": "reasoning"},
            {"op": "open", "window": "chat", "window-type": "chat"},
            {"op": "open", "window": "scan", "window-type": "tool"},
        ],
        "1": [
            {
                "op": "append",
                "window": "scan",
                "source": "tool",
                "delivery": "stream",
                "content": "Scanning archive... WARNING: retrying chunk 7.{N_TICK:4} Backing off while the connection recovers. Resuming scan... Verified 192 files. Done.{N_TICK:5}",
                "loss": {"mask": "exclude"},
            }
        ],
        "{N_TICK:4}+1": [
            {
                "op": "generate",
                "window": "reasoning",
                "content": "A warning arrived. The scan is still running; notify the user.<|eos|>",
            }
        ],
        "{N_TICK:4}+2": [
            {
                "op": "generate",
                "window": "chat",
                "content": "The scan hit a temporary issue and is retrying. It is still running.",
            }
        ],
        "{N_TICK:5}+1": [{"op": "generate", "window": "scan", "content": "<|noop|>"}],
        "{N_TICK:6}+1": [{"op": "generate", "window": "scan", "content": "<|eos|>"}],
        "{N_TICK:5}+3": [
            {
                "op": "generate",
                "window": "chat",
                "content": "\nThe scan completed and verified 192 files.<|eos|>{N_TICK:6}",
            }
        ],
    },
}
(root / "reactive-sample.json").write_text(json.dumps(sample, indent=2))
(root / "samples.jsonl").write_text(json.dumps(sample) + "\n")
if __name__ == "__main__":
    print("Created demo-tokenizer.json and reactive sample fixtures.")
