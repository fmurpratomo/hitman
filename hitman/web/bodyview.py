"""Turn a response body into display lines, clipping over-long ones.

A base64 image, a data: URI or a minified bundle arrives as one enormous line.
Rendered in full it pushes everything else off the screen; dropped entirely it
loses data you may have wanted to read. So an over-long line is clipped for
display and its full text is carried alongside, letting the UI offer a toggle
per line.

Both the clipped and the full text are handed to the template as plain strings
and escaped by Jinja. No HTML is constructed here — a body is attacker
controlled, and building markup around it in Python is how escaping bugs start.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# Lines longer than CLIP_OVER are clipped down to CLIP_TO characters. The gap
# between the two stops a line that is barely over the limit from being clipped
# to something dramatically shorter for no benefit.
CLIP_OVER = 220
CLIP_TO = 160


@dataclass
class BodyLine:
    text: str
    short: str | None  # None when the line is short enough to show whole
    length: int

    @property
    def clipped(self) -> bool:
        return self.short is not None


def pretty_text(body: str) -> str:
    """Indent JSON when it is JSON; otherwise return the body unchanged."""
    if not body.strip():
        return body
    try:
        return json.dumps(json.loads(body), indent=2, ensure_ascii=False)
    except (ValueError, TypeError):
        return body


def pretty_lines(
    body: str, *, clip_over: int = CLIP_OVER, clip_to: int = CLIP_TO
) -> list[BodyLine]:
    """Pretty-print the body and mark which lines need a show/hide toggle."""
    lines = []
    for text in pretty_text(body).split("\n"):
        length = len(text)
        short = text[:clip_to] + "…" if length > clip_over else None
        lines.append(BodyLine(text=text, short=short, length=length))
    return lines
