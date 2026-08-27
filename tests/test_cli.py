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


def test_startup_reports_the_resolved_database_path(tmp_path, capsys, monkeypatch, closed_port):
    """A relative db path resolves against the launch directory; say which one."""
    from pathlib import Path

    from hitman import cli

    db = tmp_path / "sub" / "hitman.db"
    monkeypatch.setattr(
        cli.sys, "argv",
        ["hitman", "--db", str(db), "--no-browser", "--port", str(closed_port)],
    )
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: None)
    cli.main()
    printed = capsys.readouterr().out
    assert str(Path(db).resolve()) in printed


def test_the_package_is_runnable_with_python_dash_m():
    """`python3 -m hitman` is the no-install path; keep the entry point alive."""
    import importlib.util

    spec = importlib.util.find_spec("hitman.__main__")
    assert spec is not None


def test_requirements_txt_matches_pyproject():
    """Two dependency lists drift. Fail loudly rather than silently."""
    import re
    from pathlib import Path

    def names(text):
        found = re.findall(r"^\s*[\"']?([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?[><=]", text, re.M)
        return {n.lower() for n in found}

    pyproject = Path("pyproject.toml").read_text()
    # Close on "\n]", not "]": an extra like uvicorn[standard] contains one.
    block = pyproject.split("dependencies = [")[1].split("\n]")[0]
    requirements = Path("requirements.txt").read_text()
    assert names(block) == names(requirements)
