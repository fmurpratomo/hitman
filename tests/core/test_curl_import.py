import pytest

from hitman.core.curl_import import CurlParseError, parse_curl


def parse(text):
    return parse_curl(text).request


def headers_of(request):
    return [(kv.key, kv.value) for kv in request.headers]


def test_simple_get():
    request = parse("curl https://api.example.com/users")
    assert request.method == "GET"
    assert request.url == "https://api.example.com/users"


def test_url_query_is_split_into_params():
    request = parse("curl 'http://localhost:3000/api?page=2&limit=10'")
    assert request.url == "http://localhost:3000/api"
    assert [(kv.key, kv.value) for kv in request.params] == [("page", "2"), ("limit", "10")]


def test_multiline_chrome_style_command():
    text = """curl 'https://api.example.com/v1/items' \\
      -H 'Accept: application/json' \\
      -H 'Authorization: Bearer abc123' \\
      --compressed"""
    request = parse(text)
    assert request.url == "https://api.example.com/v1/items"
    assert ("Accept", "application/json") in headers_of(request)
    assert ("Authorization", "Bearer abc123") in headers_of(request)
    assert ("Accept-Encoding", "gzip, deflate") in headers_of(request)


def test_leading_shell_prompt_is_stripped():
    assert parse("$ curl https://example.com/").url == "https://example.com/"


def test_explicit_json_body():
    request = parse(
        "curl -X POST https://api.example.com/users "
        "-H 'Content-Type: application/json' -d '{\"name\": \"ada\"}'"
    )
    assert request.method == "POST"
    assert request.body_type == "json"
    assert request.body == '{"name": "ada"}'


def test_data_implies_post():
    assert parse("curl https://x.test/ -d 'a=1'").method == "POST"


def test_urlencoded_data_becomes_form_fields():
    request = parse("curl https://x.test/ -d 'a=1&b=2'")
    assert request.body_type == "form"
    assert [(kv.key, kv.value) for kv in request.form_fields] == [("a", "1"), ("b", "2")]
    assert ("Content-Type", "application/x-www-form-urlencoded") in headers_of(request)


def test_json_body_without_content_type_stays_raw_and_warns():
    parsed = parse_curl("curl https://x.test/ -d '{\"a\": 1}'")
    assert parsed.request.body_type == "raw"
    assert parsed.request.body == '{"a": 1}'
    assert any("x-www-form-urlencoded" in w for w in parsed.warnings)


def test_repeated_data_flags_are_joined_with_ampersand():
    request = parse("curl https://x.test/ -d 'a=1' -d 'b=2'")
    assert [(kv.key, kv.value) for kv in request.form_fields] == [("a", "1"), ("b", "2")]


def test_basic_auth_becomes_authorization_header():
    request = parse("curl -u user:pass https://x.test/")
    assert ("Authorization", "Basic dXNlcjpwYXNz") in headers_of(request)


def test_head_flag_sets_head_method():
    assert parse("curl -I https://x.test/").method == "HEAD"


def test_get_flag_moves_data_into_query_params():
    request = parse("curl -G https://x.test/search -d 'q=cats' -d 'page=2'")
    assert request.method == "GET"
    assert [(kv.key, kv.value) for kv in request.params] == [("q", "cats"), ("page", "2")]
    assert request.body_type == "none"


def test_insecure_flag_disables_tls_verification():
    assert parse("curl -k https://x.test/").verify_tls is False


def test_max_time_sets_timeout():
    assert parse("curl -m 5 https://x.test/").timeout == 5.0


def test_combined_short_flags_are_expanded():
    request = parse("curl -sSL https://x.test/")
    assert request.follow_redirects is True


def test_long_flag_with_inline_value():
    request = parse("curl --header='X-Key: v' https://x.test/")
    assert ("X-Key", "v") in headers_of(request)


def test_form_flag_converts_to_urlencoded_with_warning():
    parsed = parse_curl("curl -F 'name=ada' https://x.test/")
    assert parsed.request.body_type == "form"
    assert [(kv.key, kv.value) for kv in parsed.request.form_fields] == [("name", "ada")]
    assert any("multipart" in w for w in parsed.warnings)


def test_form_file_upload_is_dropped_with_warning():
    parsed = parse_curl("curl -F 'avatar=@photo.png' -F 'name=ada' https://x.test/")
    assert [kv.key for kv in parsed.request.form_fields] == ["name"]
    assert any("avatar" in w for w in parsed.warnings)


def test_unknown_flag_warns_but_still_parses():
    parsed = parse_curl("curl --http2 https://x.test/")
    assert parsed.request.url == "https://x.test/"
    assert any("--http2" in w for w in parsed.warnings)


def test_output_flags_are_dropped_silently():
    parsed = parse_curl("curl -s -o /dev/null https://x.test/")
    assert parsed.warnings == []
    assert parsed.request.url == "https://x.test/"


def test_missing_url_raises():
    with pytest.raises(CurlParseError, match="No URL"):
        parse_curl("curl -X POST")


def test_header_without_colon_raises():
    with pytest.raises(CurlParseError, match="colon"):
        parse_curl("curl -H 'BadHeader' https://x.test/")


def test_flag_missing_its_value_raises():
    with pytest.raises(CurlParseError, match="needs a value"):
        parse_curl("curl https://x.test/ -H")


def test_empty_input_raises():
    with pytest.raises(CurlParseError):
        parse_curl("   ")
