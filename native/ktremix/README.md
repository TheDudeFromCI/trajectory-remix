# KTREMIX Rust crate

`ktremix` compiles Trajectory Remix graph JSON and raw corpus text into KTREMIX
1.0 training shards. Rust consumers can depend on this crate by path or Git
revision.

- `Compiler` compiles graph sources and runs diagnostics with or without a
  tokenizer profile. Use `shard::CompileWriter` to write batches to disk.
- `Compiler::convert_corpus` converts UTF-8 text, JSONL, or a document iterator
  into training shards.
- `shard::Shard` reads individual samples. `dataset::Dataset` adds weighted
  batching and optional sampling without replacement across shards.

## Kidra midtraining data flow

The corpus converter splits documents at meaningful boundaries, interleaves
sections across windows, and writes KTREMIX training shards.

![Kidra midtraining data flow showing how sections from three corpus documents are interleaved across training windows](assets/kidra_midtraining_data_flow.svg)
