"""Graph-only public CLI. Old tick/replay projects are intentionally unsupported."""
import argparse
import sqlite3
import sys
from pathlib import Path


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description='Trajectory Remix graph studio')
    parser.add_argument('command', nargs='?', choices=['graph-init', 'graph-studio'], default='graph-studio')
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--mcp-allow-origin', action='append', default=[])
    args = parser.parse_args(argv)
    try:
        if args.command == 'graph-init':
            from .graph.storage import GraphStore
            GraphStore.initialize(args.dataset)
            print(f'Initialized graph project {args.dataset}')
        else:
            import uvicorn
            from .mcp_server import browser_origin
            from .graph.studio_api import create_studio_app
            origins = [browser_origin(origin) for origin in args.mcp_allow_origin]
            uvicorn.run(create_studio_app(args.dataset, mcp_allowed_origins=origins), host='127.0.0.1', port=args.port)
    except (ValueError, OSError, sqlite3.Error, argparse.ArgumentTypeError) as error:
        parser.exit(1, f'Trajectory Remix: {error}\n')


if __name__ == '__main__':
    main()
