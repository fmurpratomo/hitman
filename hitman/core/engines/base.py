"""Response-body decoding shared by both send engines."""

from __future__ import annotations

from hitman.core.models import MAX_DISPLAY_BODY

_TEXTUAL_HINTS = (
    "json", "text", "xml", "javascript", "html", "csv", "yaml",
    "x-www-form-urlencoded", "graphql",
)


def is_textual(content_type: str) -> bool:
    lowered = (content_type or "").lower()
    if not lowered:
        return True  # no Content-Type: assume text and let decoding cope
    return any(hint in lowered for hint in _TEXTUAL_HINTS)


def decode_body(raw: bytes, content_type: str) -> tuple[str, bool, int]:
    """Return ``(display_text, truncated, size_in_bytes)``.

    Binary payloads are summarised rather than decoded — dumping megabytes of
    PNG into the DOM helps nobody.
    """
    size = len(raw)
    if not is_textual(content_type):
        label = content_type or "binary data"
        return f"[{size} bytes of {label} — not shown]", False, size

    truncated = size > MAX_DISPLAY_BODY
    data = raw[:MAX_DISPLAY_BODY] if truncated else raw

    charset = "utf-8"
    if "charset=" in (content_type or "").lower():
        charset = content_type.lower().split("charset=")[-1].split(";")[0].strip() or "utf-8"
    try:
        text = data.decode(charset, errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    return text, truncated, size
