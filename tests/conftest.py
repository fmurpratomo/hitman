import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep pytest output clean
        pass

    def _respond(self, status, payload: bytes, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Fixture", "hitman")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _echo(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        payload = json.dumps(
            {
                "method": self.command,
                "path": self.path,
                "content_type": self.headers.get("Content-Type"),
                "body": body,
                "headers": {k.lower(): v for k, v in self.headers.items()},
            }
        ).encode()
        self._respond(200, payload)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/json":
            self._respond(200, b'{"hello": "world"}')
        elif path == "/slow":
            time.sleep(2)
            self._respond(200, b'{"slow": true}')
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/json")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/binary":
            self._respond(200, bytes(range(256)) * 4, "image/png")
        elif path == "/html":
            self._respond(200, b"<script>alert(1)</script>", "text/html")
        elif path.startswith("/status/"):
            self._respond(int(path.rsplit("/", 1)[1]), b"{}")
        else:
            self._echo()

    do_HEAD = do_GET
    do_POST = do_PUT = do_PATCH = do_DELETE = _echo


@pytest.fixture(scope="session")
def fixture_server():
    """A real HTTP server on a random loopback port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="session")
def closed_port():
    """A port number with nothing listening on it."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port
