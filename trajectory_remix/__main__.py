import argparse
import json
from pathlib import Path

from tokenizers import Tokenizer

from .compiler import Compiler, chunks
from .dataset import parse_import


def main():
    parser = argparse.ArgumentParser(
        description="Trajectory Remix — local Kidra dataset studio"
    )
    parser.add_argument(
        "--tokenizer", type=Path, required=True, help="Hugging Face tokenizer.json"
    )
    parser.add_argument("--bos", default="<|bos|>")
    parser.add_argument("--eos", default="<|eos|>")
    parser.add_argument("--noop", default="<|noop|>")
    parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="Project .kidra-dataset file; created if missing and saved to on every save",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--export",
        type=Path,
        help="Compile --dataset to chunked JSONL without starting the dashboard",
    )
    parser.add_argument("--chunk-ticks", type=int, default=64)
    args = parser.parse_args()
    try:
        compiler = Compiler(
            Tokenizer.from_file(str(args.tokenizer)), args.bos, args.eos, args.noop
        )
        if args.export:
            if not args.dataset.exists():
                parser.error(f"--dataset not found: {args.dataset}")

            samples, _ = parse_import(
                args.dataset.name, args.dataset.read_text("utf-8")
            )
            plans = [compiler.compile(s) for s in samples]

            import os
            from tempfile import NamedTemporaryFile

            args.export.parent.mkdir(parents=True, exist_ok=True)
            temp = None
            try:
                with NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=args.export.parent, delete=False
                ) as stream:
                    temp = stream.name
                    for plan in plans:
                        for chunk in chunks(plan, args.chunk_ticks):
                            stream.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, args.export)
            finally:
                if temp and os.path.exists(temp):
                    os.unlink(temp)
            print(f"Exported {len(plans)} samples to {args.export}")
            return

        import uvicorn

        from .server import create_app

        print(f"Trajectory Remix: http://127.0.0.1:{args.port}")
        uvicorn.run(
            create_app(compiler, args.dataset),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
        )
    except (ValueError, OSError) as e:
        parser.exit(1, f"Trajectory Remix: {e}\n")


if __name__ == "__main__":
    main()
