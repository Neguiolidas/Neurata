"""tests/test_cli_usage_hook.py — cli.main() loga cada invocação.

Cada chamada da CLI que chega a criar o home grava 1 linha em
NEURATA_HOME/usage.log (cmd, duration_ms, ok). Erros de parse (antes do
home) não geram linha. O log é best-effort: nunca altera o exit code.
"""
from neurata.cli import main
from neurata.home import NeurataHome
from neurata.usage import read_invocations


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    return NeurataHome(tmp_path)


def test_main_logs_invocation(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    rc = main(["doctor"])
    data = read_invocations(home)
    assert len(data["entries"]) == 1
    rec = data["entries"][0]
    assert rec["cmd"] == "doctor"
    assert rec["ok"] == (rc == 0)
    assert isinstance(rec["duration_ms"], int)
    assert rec["duration_ms"] >= 0


def test_main_logs_each_invocation(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    main(["doctor"])
    main(["reindex"])
    data = read_invocations(home)
    assert [e["cmd"] for e in data["entries"]] == ["doctor", "reindex"]


def test_parse_error_does_not_log(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    rc = main(["query"])  # falta arg posicional 'q' → UsageError, sem home
    assert rc == 2
    data = read_invocations(home)
    assert data["entries"] == []


def test_no_command_does_not_log(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    rc = main([])
    assert rc == 2
    assert read_invocations(home)["entries"] == []


def test_logging_failure_never_changes_rc(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    baseline = main(["doctor"])  # rc real, sem interferência do log

    def boom(*a, **k):
        raise OSError("disco cheio")

    monkeypatch.setattr("neurata.cli.log_invocation", boom)
    rc = main(["doctor"])  # log explode, mas a CLI não pode quebrar
    assert rc == baseline
