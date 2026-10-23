import argparse
import json
import sqlite3
import sys
from pathlib import Path

from tokenizers import Tokenizer

from .compiler import Compiler, chunks
from .storage import DatasetStore


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description="Trajectory Remix — local Kidra dataset studio"
    )
    parser.epilog = "Initialize once: trajectory-remix init --dataset project.sqlite --tokenizer tokenizer.json"
    initializing = bool(argv) and argv[0] == "init"
    if initializing:
        parser.prog += " init"
        parser.add_argument("--tokenizer", type=Path, required=True, help="Hugging Face tokenizer.json to embed in the new project")
        parser.add_argument("--bos", default="<|bos|>")
        parser.add_argument("--eos", default="<|eos|>")
        parser.add_argument("--noop", default="<|noop|>")
        parser.add_argument("--max-window-slots", type=int, help="Maximum distinct windows per trajectory; unlimited if omitted")
    parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="New SQLite project file" if initializing else "Existing initialized SQLite project file",
    )
    if not initializing:
        from .mcp_server import browser_origin

        parser.add_argument("--port", type=int, default=8765)
        parser.add_argument(
            "--mcp-allow-origin",
            action="append",
            type=browser_origin,
            default=[],
            help="Allow an exact browser origin to use /mcp; repeat for multiple UIs",
        )
        parser.add_argument(
            "--export",
            type=Path,
            help="Compile --dataset to chunked JSONL without starting the dashboard",
        )
        parser.add_argument("--chunk-ticks", type=int, default=64)
    args = parser.parse_args(argv[1:] if initializing else argv)
    try:
        if initializing:
            try:
                tokenizer = Tokenizer.from_file(str(args.tokenizer))
            except Exception as error:
                raise ValueError(f"Could not load tokenizer: {error}") from error
            compiler = Compiler(tokenizer, args.bos, args.eos, args.noop, max_window_slots=args.max_window_slots)
            DatasetStore.initialize(args.dataset, compiler)
            print(f"Initialized {args.dataset} with tokenizer {compiler.fingerprint}")
            return
        if args.chunk_ticks < 1:
            parser.error("--chunk-ticks must be positive")
        store = DatasetStore(args.dataset)
        if args.export:
            compiler = store.load_compiler()

            import os
            from tempfile import NamedTemporaryFile

            args.export.parent.mkdir(parents=True, exist_ok=True)
            temp = None
            try:
                with NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=args.export.parent, delete=False
                ) as stream:
                    temp = stream.name
                    count = 0
                    with store.read_samples() as samples:
                        for sample in samples:
                            for chunk in chunks(compiler.compile(sample), args.chunk_ticks):
                                stream.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                            count += 1
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, args.export)
            finally:
                if temp and os.path.exists(temp):
                    os.unlink(temp)
            print(f"Exported {count} samples to {args.export}")
            return

        import uvicorn

        from .server import create_app

        print(f"Trajectory Remix: http://127.0.0.1:{args.port}")
        uvicorn.run(
            create_app(args.dataset, mcp_allowed_origins=args.mcp_allow_origin),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
        )
    except (ValueError, OSError, sqlite3.Error) as e:
        parser.exit(1, f"Trajectory Remix: {e}\n")


if __name__ == "__main__":
    main()
