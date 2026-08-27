"""Command line entry point: ``uv run hitman``."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser
from pathlib import Path

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


def port_is_free(port: int, host: str = HOST) -> bool:
    """Can we actually bind this port?

    SO_REUSEADDR matches how uvicorn binds, so a socket merely lingering in
    TIME_WAIT is not misreported as occupied.
    """
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def main() -> None:
    args = build_parser().parse_args(sys.argv[1:])
    url = f"http://{HOST}:{args.port}"

    # Checked before create_app so a doomed start does not create a database
    # directory, and before the banner so we never claim to be running when
    # the port belongs to somebody else.
    if not port_is_free(args.port):
        raise SystemExit(
            f"Port {args.port} is already in use — another Hitman may still be running.\n"
            f"Stop it with:  pkill -f 'hitman --port {args.port}'\n"
            f"or use another port:  hitman --port {args.port + 1}"
        )

    app = create_app(args.db)
    print(f"Hitman is running at {url} (loopback only — press Ctrl+C to stop)", flush=True)
    print(f"Data:  {Path(app.state.store.path).resolve()}", flush=True)
    if not args.no_browser:
        # Give uvicorn a moment to bind before the browser asks for the page.
        threading.Timer(1.0, webbrowser.open, [url]).start()

    uvicorn.run(app, host=HOST, port=args.port, log_level="warning")
