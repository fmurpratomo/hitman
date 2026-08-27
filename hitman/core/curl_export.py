"""Render a :class:`Request` as a curl command.

Two consumers with different needs share one builder:

* the UI's "Copy as curl", which should look like something a person typed;
* the curl engine, which must send exactly what the httpx engine sends.
"""

from __future__ import annotations

import shlex

from hitman.core.models import DEFAULT_CONTENT_TYPE, DEFAULT_TIMEOUT, Request

# Flags whose value should stay on the same line when wrapping for display.
_VALUE_FLAGS = {"-X", "-H", "--data-raw", "--max-time"}


def _format_timeout(timeout: float) -> str:
    return str(int(timeout)) if float(timeout).is_integer() else str(timeout)


def to_argv(request: Request, *, for_execution: bool = False) -> list[str]:
    method = request.method.upper()
    argv = ["curl"]

    if method == "HEAD":
        # `curl -X HEAD` waits for a response body that a HEAD reply never
        # sends, and hangs until the timeout. `-I` is the correct spelling.
        argv.append("-I")
    elif method != "GET":
        argv += ["-X", method]

    if request.follow_redirects:
        argv.append("-L")
    if not request.verify_tls:
        argv.append("-k")
    if for_execution or request.timeout != DEFAULT_TIMEOUT:
        argv += ["--max-time", _format_timeout(request.timeout)]

    for key, value in request.effective_headers():
        argv += ["-H", f"{key}: {value}"]

    body = request.body_bytes()

    if (
        for_execution
        and body is not None
        and DEFAULT_CONTENT_TYPE[request.body_type] is None
        and request.find_header("content-type") is None
    ):
        # curl adds `Content-Type: application/x-www-form-urlencoded` to any
        # --data request. httpx does not. An empty-valued -H removes curl's
        # header, so both engines put identical bytes on the wire.
        argv += ["-H", "Content-Type:"]

    if body is not None:
        argv += ["--data-raw", body.decode("utf-8")]

    argv.append(request.full_url())
    return argv


def to_command(request: Request, *, width: int = 80) -> str:
    """Shell-quoted command for display and clipboard."""
    argv = to_argv(request)
    parts: list[str] = []
    index = 0
    while index < len(argv):
        if argv[index] in _VALUE_FLAGS and index + 1 < len(argv):
            parts.append(f"{argv[index]} {shlex.quote(argv[index + 1])}")
            index += 2
        else:
            parts.append(shlex.quote(argv[index]))
            index += 1

    single_line = " ".join(parts)
    if len(single_line) <= width:
        return single_line
    return " \\\n  ".join(parts)
