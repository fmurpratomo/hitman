"""Parse a pasted ``curl`` command into a :class:`Request`.

``shlex.split`` *tokenizes*; it does not evaluate. No shell, no subprocess
and no ``eval`` is involved anywhere in this module, so a hostile paste
cannot execute anything — the worst it can do is fail to parse.
"""

from __future__ import annotations

import re
import shlex
from base64 import b64encode
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

from hitman.core.models import KeyValue, Request, normalize


class CurlParseError(ValueError):
    def __init__(self, message: str, token: str | None = None, index: int | None = None) -> None:
        self.token = token
        self.index = index
        super().__init__(message)


@dataclass
class ParsedCurl:
    request: Request
    warnings: list[str] = field(default_factory=list)


# Flags that only control curl's own console output. They have no meaning in
# a GUI, so they are dropped silently rather than reported as warnings.
_IGNORED = {
    "-s", "--silent", "-S", "--show-error", "-v", "--verbose", "-i", "--include",
    "--no-progress-meter", "-#", "--progress-bar", "-f", "--fail",
}
_IGNORED_WITH_VALUE = {"-o", "--output", "-w", "--write-out", "--retry"}
_BOOLEAN_SHORT = set("sSviLkIGf#")


class _Tokens:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.index = 0

    def __bool__(self) -> bool:
        return self.index < len(self.tokens)

    def next(self) -> str:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def value_for(self, name: str, inline: str | None) -> str:
        if inline is not None:
            return inline
        if self.index >= len(self.tokens):
            raise CurlParseError(
                f"{name} needs a value but the command ends here.", name, self.index
            )
        return self.next()


def _clean(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\\\r?\n", " ", text)   # shell line continuation
    text = re.sub(r"\^\r?\n", " ", text)   # windows cmd line continuation
    text = re.sub(r"`\r?\n", " ", text)    # powershell line continuation
    return text.lstrip("$").strip()


def _expand_combined(tokens: list[str]) -> list[str]:
    """Turn ``-sSL`` into ``-s -S -L``."""
    expanded: list[str] = []
    for token in tokens:
        if re.fullmatch(r"-[a-zA-Z#]{2,}", token) and all(c in _BOOLEAN_SHORT for c in token[1:]):
            expanded.extend(f"-{c}" for c in token[1:])
        else:
            expanded.append(token)
    return expanded


def _split_inline(token: str) -> tuple[str, str | None]:
    """``--header=X: 1`` -> ``("--header", "X: 1")``."""
    if token.startswith("--") and "=" in token:
        name, _, value = token.partition("=")
        return name, value
    return token, None


def _looks_like_url(token: str) -> bool:
    return "://" in token or bool(re.match(r"^[\w.-]+(:\d+)?(/|$)", token))


def _try_pairs(data: str) -> list[tuple[str, str]] | None:
    """Key/value pairs when the body is cleanly URL-encoded form data, else None."""
    if not data or "=" not in data:
        return None
    for segment in data.split("&"):
        key, _, _ = segment.partition("=")
        if "=" not in segment or not key:
            return None
        if any(ch in segment for ch in "{}[]\"' \n\t"):
            return None
    return parse_qsl(data, keep_blank_values=True)


def parse_curl(text: str) -> ParsedCurl:
    stream = _Tokens(_expand_combined(shlex.split(_clean(text))))
    if not stream:
        raise CurlParseError("Nothing to import — paste a curl command.")
    if stream.tokens[0] == "curl":
        stream.index = 1

    # curl does not follow redirects unless -L is given, so an imported
    # command must start from False even though a request built in the UI
    # defaults to True. Without this, follow_redirects=False does not
    # survive an export/import round trip.
    request = Request(follow_redirects=False)
    warnings: list[str] = []
    bare_tokens: list[str] = []
    data_parts: list[str] = []
    form_parts: list[str] = []
    explicit_method: str | None = None
    as_get = False
    as_head = False

    while stream:
        position = stream.index
        token = stream.next()
        name, inline = _split_inline(token)

        if name in _IGNORED:
            continue
        if name in _IGNORED_WITH_VALUE:
            stream.value_for(name, inline)
            continue

        if name in ("-X", "--request"):
            explicit_method = stream.value_for(name, inline).upper()
        elif name in ("-H", "--header"):
            raw = stream.value_for(name, inline)
            key, sep, value = raw.partition(":")
            if not sep:
                raise CurlParseError(
                    f"Header {raw!r} is missing a colon.", token, position
                )
            request.headers.append(KeyValue(key.strip(), value.strip()))
        elif name in ("-d", "--data", "--data-raw", "--data-ascii", "--data-binary",
                      "--data-urlencode"):
            data_parts.append(stream.value_for(name, inline))
        elif name in ("-F", "--form"):
            form_parts.append(stream.value_for(name, inline))
        elif name in ("-u", "--user"):
            encoded = b64encode(stream.value_for(name, inline).encode()).decode()
            request.headers.append(KeyValue("Authorization", f"Basic {encoded}"))
        elif name in ("-A", "--user-agent"):
            request.headers.append(KeyValue("User-Agent", stream.value_for(name, inline)))
        elif name in ("-b", "--cookie"):
            request.headers.append(KeyValue("Cookie", stream.value_for(name, inline)))
        elif name in ("-e", "--referer"):
            request.headers.append(KeyValue("Referer", stream.value_for(name, inline)))
        elif name in ("-m", "--max-time"):
            raw = stream.value_for(name, inline)
            try:
                request.timeout = float(raw)
            except ValueError:
                raise CurlParseError(f"{name} needs a number, got {raw!r}.", token, position) from None
        elif name in ("-L", "--location"):
            request.follow_redirects = True
        elif name in ("-k", "--insecure"):
            request.verify_tls = False
        elif name == "--compressed":
            request.headers.append(KeyValue("Accept-Encoding", "gzip, deflate"))
        elif name in ("-G", "--get"):
            as_get = True
        elif name in ("-I", "--head"):
            as_head = True
        elif name == "--url":
            bare_tokens.append(stream.value_for(name, inline))
        elif name.startswith("-"):
            warnings.append(f"Ignored unsupported flag {name}.")
        else:
            bare_tokens.append(token)

    urls = [t for t in bare_tokens if _looks_like_url(t)] or bare_tokens
    if not urls:
        raise CurlParseError("No URL found in the command.")
    if len(urls) > 1:
        warnings.append(f"Ignored extra URLs: {', '.join(urls[1:])}. Only the first is used.")
    request.url = urls[0]

    data = "&".join(data_parts)

    if as_head:
        request.method = "HEAD"
    elif explicit_method:
        request.method = explicit_method
    elif (data and not as_get) or form_parts:
        request.method = "POST"
    else:
        request.method = "GET"

    if as_get and data:
        request.params.extend(
            KeyValue(k, v) for k, v in parse_qsl(data, keep_blank_values=True)
        )
        data = ""

    explicit_type = request.find_header("content-type")

    if form_parts:
        request.body_type = "form"
        for part in form_parts:
            key, _, value = part.partition("=")
            if value.startswith(("@", "<")):
                warnings.append(
                    f"Dropped file upload field {key!r} — file upload is not supported yet."
                )
                continue
            request.form_fields.append(KeyValue(key, value))
        warnings.append(
            "Converted -F from multipart to URL-encoded form; v1 does not send multipart."
        )
    elif data:
        pairs = _try_pairs(data)
        if explicit_type and "json" in explicit_type.lower():
            request.body_type = "json"
            request.body = data
        elif pairs is not None and (
            explicit_type is None or "x-www-form-urlencoded" in explicit_type.lower()
        ):
            request.body_type = "form"
            request.form_fields = [KeyValue(k, v) for k, v in pairs]
        else:
            request.body_type = "raw"
            request.body = data
            if explicit_type is None:
                warnings.append(
                    "curl would send Content-Type: application/x-www-form-urlencoded for "
                    "this body; Hitman sends no Content-Type. Add one in Headers if the "
                    "server needs it."
                )

    return ParsedCurl(request=normalize(request), warnings=warnings)
