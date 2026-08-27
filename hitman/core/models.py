"""Core data model.

Nothing in this module may import from ``hitman.web``. These types are the
contract every other module speaks in.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_TIMEOUT = 30.0
MAX_DISPLAY_BODY = 5 * 1024 * 1024

BODY_TYPES = ("none", "json", "raw", "form")

# Which Content-Type is implied when the user did not set one explicitly.
# 'raw' deliberately has no default: raw means "exactly what I typed".
DEFAULT_CONTENT_TYPE: dict[str, str | None] = {
    "none": None,
    "json": "application/json",
    "form": "application/x-www-form-urlencoded",
    "raw": None,
}


def ensure_scheme(url: str) -> str:
    """Prepend ``http://`` when the user typed a bare host.

    Required, not defensive: ``urlsplit("localhost:3000/api")`` parses
    ``localhost`` as the URL *scheme* and ``8931/api`` as the path, so host
    and port extraction silently produce nonsense without this.
    """
    url = url.strip()
    if not url or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", url):
        return url
    return "http://" + url


@dataclass
class KeyValue:
    """One row of the params, headers or form-fields table."""

    key: str
    value: str
    enabled: bool = True


@dataclass
class Request:
    method: str = "GET"
    url: str = ""
    params: list[KeyValue] = field(default_factory=list)
    headers: list[KeyValue] = field(default_factory=list)
    body_type: str = "none"
    body: str = ""
    form_fields: list[KeyValue] = field(default_factory=list)
    follow_redirects: bool = True
    verify_tls: bool = True
    timeout: float = DEFAULT_TIMEOUT

    def find_header(self, name: str) -> str | None:
        """Case-insensitive lookup across enabled headers."""
        wanted = name.lower()
        for kv in self.headers:
            if kv.enabled and kv.key.lower() == wanted:
                return kv.value
        return None

    def effective_headers(self) -> list[tuple[str, str]]:
        """Enabled headers, plus the implied Content-Type if the user set none."""
        headers = [(kv.key, kv.value) for kv in self.headers if kv.enabled and kv.key]
        implied = DEFAULT_CONTENT_TYPE[self.body_type]
        if implied and self.find_header("content-type") is None:
            headers.append(("Content-Type", implied))
        return headers

    def body_bytes(self) -> bytes | None:
        if self.body_type == "none":
            return None
        if self.body_type == "form":
            pairs = [(kv.key, kv.value) for kv in self.form_fields if kv.enabled and kv.key]
            return urlencode(pairs).encode("utf-8")
        return self.body.encode("utf-8")

    def full_url(self) -> str:
        """The URL actually sent: base URL with enabled params appended."""
        parts = urlsplit(ensure_scheme(self.url))
        query = parse_qsl(parts.query, keep_blank_values=True)
        query += [(kv.key, kv.value) for kv in self.params if kv.enabled and kv.key]
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Request:
        def rows(items: list[dict] | None) -> list[KeyValue]:
            return [KeyValue(**item) for item in (items or [])]

        return cls(
            method=data.get("method", "GET"),
            url=data.get("url", ""),
            params=rows(data.get("params")),
            headers=rows(data.get("headers")),
            body_type=data.get("body_type", "none"),
            body=data.get("body", ""),
            form_fields=rows(data.get("form_fields")),
            follow_redirects=data.get("follow_redirects", True),
            verify_tls=data.get("verify_tls", True),
            timeout=data.get("timeout", DEFAULT_TIMEOUT),
        )


@dataclass
class Response:
    engine: str
    status: int | None = None
    reason: str = ""
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: str = ""
    body_truncated: bool = False
    size_bytes: int = 0
    elapsed_ms: float = 0.0
    content_type: str = ""
    error: str | None = None
    curl_exit_code: int | None = None


def normalize(request: Request) -> Request:
    """Canonical form used for storage, comparison and the round-trip test.

    Three transformations, each one required to make two semantically
    identical requests compare equal:

    1. A query string embedded in ``url`` moves into ``params`` — the user can
       type ``?page=2`` in the URL bar or add a param row, and those must not
       be different requests.
    2. Disabled rows are dropped, since they are never sent.
    3. The implied Content-Type becomes an explicit header, so a request that
       relies on the default matches one that spells it out.
    """
    parts = urlsplit(ensure_scheme(request.url))
    params = [KeyValue(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    params += [KeyValue(kv.key, kv.value) for kv in request.params if kv.enabled and kv.key]

    return Request(
        method=request.method.upper(),
        url=urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment)),
        params=params,
        headers=[KeyValue(k, v) for k, v in request.effective_headers()],
        body_type=request.body_type,
        body=request.body,
        form_fields=[
            KeyValue(kv.key, kv.value) for kv in request.form_fields if kv.enabled and kv.key
        ],
        follow_redirects=request.follow_redirects,
        verify_tls=request.verify_tls,
        timeout=request.timeout,
    )
