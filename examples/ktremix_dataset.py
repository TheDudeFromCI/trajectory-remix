"""Public command-line entry point for the packaged KTREMIX reference codec."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trajectory_remix.graph.ktremix import main  # noqa: E402

if __name__ == "__main__":
    main()
