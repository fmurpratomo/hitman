from hitman.web.bodyview import CLIP_OVER, pretty_lines, pretty_text


def test_pretty_text_indents_json():
    assert pretty_text('{"a":1}') == '{\n  "a": 1\n}'


def test_pretty_text_leaves_non_json_alone():
    assert pretty_text("<html></html>") == "<html></html>"


def test_pretty_text_leaves_an_empty_body_alone():
    assert pretty_text("") == ""


def test_short_lines_are_not_clipped():
    lines = pretty_lines('{"a": 1}')
    assert [line.clipped for line in lines] == [False, False, False]
    assert all(line.short is None for line in lines)


def test_a_long_value_is_clipped_and_keeps_its_full_text():
    blob = "A" * 5000
    lines = pretty_lines(f'{{"avatar": "{blob}"}}')
    clipped = [line for line in lines if line.clipped]
    assert len(clipped) == 1
    line = clipped[0]
    assert blob in line.text          # full text is preserved for the toggle
    assert len(line.short) < len(line.text)
    assert line.short.endswith("…")
    assert line.length == len(line.text)


def test_the_key_stays_visible_in_the_clipped_form():
    """You must still be able to tell which field was truncated."""
    lines = pretty_lines('{"avatar": "%s"}' % ("A" * 5000))
    clipped = next(line for line in lines if line.clipped)
    assert '"avatar"' in clipped.short


def test_a_line_just_over_the_limit_is_clipped():
    lines = pretty_lines("x" * (CLIP_OVER + 1))
    assert lines[0].clipped is True


def test_a_line_exactly_at_the_limit_is_not_clipped():
    lines = pretty_lines("x" * CLIP_OVER)
    assert lines[0].clipped is False


def test_only_the_long_lines_are_clipped():
    body = '{"small": "ok", "big": "%s"}' % ("A" * 5000)
    lines = pretty_lines(body)
    assert sum(line.clipped for line in lines) == 1


def test_non_json_long_lines_are_clipped_too():
    lines = pretty_lines("data:image/png;base64," + "A" * 9000)
    assert lines[0].clipped is True
    assert lines[0].short.startswith("data:image/png;base64,")


def test_stylesheet_keeps_the_hidden_attribute_working():
    """Regression guard for a bug that is invisible to every other test.

    The `hidden` attribute is `display: none` in the UA stylesheet, so ANY
    author rule that sets `display` on the same element silently overrides it.
    `.clip-full` sets `display: block`, which made "show less" leave the
    expanded text on screen. Server-side tests cannot catch this — the markup
    was correct and only the rendering was wrong.
    """
    from pathlib import Path

    css = Path("hitman/web/static/app.css").read_text()
    assert "[hidden] { display: none !important; }" in css
