"""Verify an installed trajectory_remix wheel ships its package data.

Run with the interpreter of a clean environment, from outside the source checkout:
    python scripts/check_package.py
"""

import importlib.resources as resources
import subprocess
import sys

REQUIRED = [
    "static/graph.html",
    "static/graph.js",
    "static/graph.css",
    "static/shell.js",
    "static/views.js",
    "static/ui-kit.js",
    "static/jobs-view.js",
    "static/metrics-view.js",
    "static/style.css",
    "static/ibm-plex-sans-latin-400-normal.woff2",
    "static/fonts-LICENSE.txt",
    "Skill.md",
]


def main() -> int:
    root = resources.files("trajectory_remix")
    missing = [name for name in REQUIRED if not root.joinpath(name).is_file()]
    for pattern_dir in ("guides", "graph/sampler_originals"):
        if not root.joinpath(pattern_dir).is_dir() or not any(root.joinpath(pattern_dir).iterdir()):
            missing.append(pattern_dir + "/")
    if missing:
        print("Missing package data:", ", ".join(missing), file=sys.stderr)
        return 1
    subprocess.run([sys.executable, "-m", "trajectory_remix", "--help"], check=True)
    print("Package data OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
