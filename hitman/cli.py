"""Command line entry point: ``uv run hitman``."""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from hitman.web.app import create_app

# Deliberately not configurable. Serving this app on any other interface would
# let anyone on the network reach the host's internal services and run the
# curl binary with arguments of their choosing.
HOST = "127.0.0.1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hitman", description="Local API testing client.")
    parser.add_argument("--port", type=int, default=8765, help="port to listen on")
    parser.add_argument("--db", default=None, help="SQLite file (default: data/hitman.db)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    app = create_app(args.db)
    url = f"http://{HOST}:{args.port}"

    print(f"Hitman is running at {url} (loopback only — press Ctrl+C to stop)")
    if not args.no_browser:
        # Give uvicorn a moment to bind before the browser asks for the page.
        threading.Timer(1.0, webbrowser.open, [url]).start()

    uvicorn.run(app, host=HOST, port=args.port, log_level="warning")
