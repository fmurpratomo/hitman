import pytest

from hitman.cli import HOST, build_parser


def test_defaults():
    args = build_parser().parse_args([])
    assert args.port == 8765
    assert args.db is None


def test_port_and_db_are_configurable():
    args = build_parser().parse_args(["--port", "9000", "--db", "/tmp/x.db"])
    assert args.port == 9000
    assert args.db == "/tmp/x.db"


def test_bind_address_is_loopback_only():
    assert HOST == "127.0.0.1"


def test_there_is_deliberately_no_host_flag():
    """Exposing this app on a network turns it into an open proxy."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--host", "0.0.0.0"])


def test_port_is_free_reports_true_for_an_unused_port(closed_port):
    from hitman.cli import port_is_free

    assert port_is_free(closed_port) is True


def test_port_is_free_reports_false_when_something_is_listening():
    """A stale instance on the port must be detected, not silently talked to."""
    import socket

    from hitman.cli import port_is_free

    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    try:
        assert port_is_free(holder.getsockname()[1]) is False
    finally:
        holder.close()
