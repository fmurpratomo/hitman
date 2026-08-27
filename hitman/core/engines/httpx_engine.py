"""Default send engine: structured, in-process, fast."""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import httpx

from hitman.core.engines.base import decode_body
from hitman.core.models import Request, Response, ensure_scheme


def _host_label(request: Request) -> str:
    return urlsplit(ensure_scheme(request.url)).netloc or request.url


def _format_timeout(timeout: float) -> str:
    return str(int(timeout)) if float(timeout).is_integer() else str(timeout)


def describe_error(exc: Exception, request: Request) -> str:
    """Turn an httpx exception into something a person can act on."""
    host = _host_label(request)
    if isinstance(exc, httpx.TooManyRedirects):
        return "Too many redirects."
    if isinstance(exc, httpx.TimeoutException):
        return f"Timed out after {_format_timeout(request.timeout)}s."
    if isinstance(exc, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        return f"Invalid URL: {exc}"
    if isinstance(exc, httpx.ConnectError):
        text = str(exc).lower()
        if "refused" in text:
            return f"Connection refused — is anything listening on {host}?"
        if any(
            hint in text
            for hint in ("name or service not known", "nodename nor servname",
                         "getaddrinfo", "name resolution", "no address associated")
        ):
            return f"Could not resolve host {host}."
        if "certificate" in text or "ssl" in text:
            return (
                f"TLS error talking to {host}: {exc}. "
                "Turn off 'Verify TLS' in Options to bypass."
            )
        return f"Could not connect to {host}: {exc}"
    return f"{type(exc).__name__}: {exc}"


class HttpxEngine:
    name = "httpx"

    def send(self, request: Request) -> Response:
        started = time.perf_counter()
        try:
            with httpx.Client(
                follow_redirects=request.follow_redirects,
                verify=request.verify_tls,
                timeout=request.timeout,
            ) as client:
                reply = client.request(
                    request.method.upper(),
                    request.full_url(),
                    headers=request.effective_headers(),
                    content=request.body_bytes(),
                )
        except Exception as exc:  # noqa: BLE001 - every failure becomes a Response
            return Response(
                engine=self.name,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                error=describe_error(exc, request),
            )

        elapsed_ms = (time.perf_counter() - started) * 1000
        content_type = reply.headers.get("content-type", "")
        body, truncated, size = decode_body(reply.content, content_type)
        return Response(
            engine=self.name,
            status=reply.status_code,
            reason=reply.reason_phrase,
            headers=list(reply.headers.items()),
            body=body,
            body_truncated=truncated,
            size_bytes=size,
            elapsed_ms=elapsed_ms,
            content_type=content_type,
        )
