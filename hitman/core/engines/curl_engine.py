"""Send engine that shells out to the real curl binary.

Always invoked as an argv list with ``shell=False``. No part of the request
is ever interpolated into a shell string.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from functools import lru_cache
from urllib.parse import urlsplit

from hitman.core.curl_export import to_argv
from hitman.core.engines.base import decode_body
from hitman.core.models import Request, Response, ensure_scheme

# curl's documented exit codes, phrased the way a person debugging would want.
_FRIENDLY = {
    3: "Malformed URL.",
    5: "Could not resolve proxy.",
    6: "Could not resolve host {host}.",
    7: "Connection refused — is anything listening on {host}?",
    28: "Timed out after {timeout}s.",
    35: "TLS handshake failed with {host}.",
    47: "Too many redirects.",
    52: "Empty reply from {host}.",
    56: "Connection reset by {host}.",
    60: "TLS certificate could not be verified. Turn off 'Verify TLS' in Options to bypass.",
}


@lru_cache(maxsize=1)
def curl_available() -> bool:
    return shutil.which("curl") is not None


def _format_timeout(timeout: float) -> str:
    return str(int(timeout)) if float(timeout).is_integer() else str(timeout)


def _parse_header_dump(raw: str) -> tuple[int | None, str, list[tuple[str, str]]]:
    """Read the last header block; earlier blocks are redirect hops."""
    blocks = [block for block in re.split(r"\r?\n\r?\n", raw) if block.strip()]
    if not blocks:
        return None, "", []
    lines = blocks[-1].strip().splitlines()
    status, reason = None, ""
    match = re.match(r"HTTP/[\d.]+\s+(\d{3})\s*(.*)", lines[0])
    if match:
        status = int(match.group(1))
        reason = match.group(2).strip()
    headers = []
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers.append((key.strip(), value.strip()))
    return status, reason, headers


class CurlEngine:
    name = "curl"

    def send(self, request: Request) -> Response:
        host = urlsplit(ensure_scheme(request.url)).netloc or request.url
        body_fd, body_path = tempfile.mkstemp(prefix="hitman-body-")
        head_fd, head_path = tempfile.mkstemp(prefix="hitman-head-")
        os.close(body_fd)
        os.close(head_fd)

        argv = to_argv(request, for_execution=True)
        # -w %{json} puts a machine-readable summary on stdout while the body
        # and headers go to files, so nothing needs to be scraped from text.
        argv[1:1] = ["-s", "-S", "-o", body_path, "-D", head_path, "-w", "%{json}"]

        started = time.perf_counter()
        try:
            try:
                completed = subprocess.run(
                    argv, capture_output=True, timeout=request.timeout + 5, check=False
                )
            except FileNotFoundError:
                return Response(
                    engine=self.name,
                    error="The curl binary was not found on this system.",
                )
            except subprocess.TimeoutExpired:
                return Response(
                    engine=self.name,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                    error=f"Timed out after {_format_timeout(request.timeout)}s.",
                    curl_exit_code=28,
                )

            try:
                stats = json.loads(completed.stdout or b"{}")
            except (ValueError, UnicodeDecodeError):
                stats = {}

            elapsed_ms = float(stats.get("time_total") or 0) * 1000 or (
                time.perf_counter() - started
            ) * 1000

            if completed.returncode != 0:
                template = _FRIENDLY.get(completed.returncode)
                if template:
                    error = template.format(host=host, timeout=_format_timeout(request.timeout))
                else:
                    detail = (
                        stats.get("errormsg")
                        or completed.stderr.decode("utf-8", "replace").strip()
                        or "no further detail"
                    )
                    error = f"curl failed (exit {completed.returncode}): {detail}"
                return Response(
                    engine=self.name,
                    elapsed_ms=elapsed_ms,
                    error=error,
                    curl_exit_code=completed.returncode,
                )

            with open(head_path, "rb") as handle:
                status, reason, headers = _parse_header_dump(
                    handle.read().decode("utf-8", "replace")
                )
            with open(body_path, "rb") as handle:
                raw_body = handle.read()

            content_type = stats.get("content_type") or ""
            for key, value in headers:
                if key.lower() == "content-type":
                    content_type = value
                    break

            body, truncated, size = decode_body(raw_body, content_type)
            return Response(
                engine=self.name,
                status=status if status is not None else stats.get("response_code") or None,
                reason=reason,
                headers=headers,
                body=body,
                body_truncated=truncated,
                size_bytes=size,
                elapsed_ms=elapsed_ms,
                content_type=content_type,
                curl_exit_code=0,
            )
        finally:
            for path in (body_path, head_path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
