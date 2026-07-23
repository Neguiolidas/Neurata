"""tests/test_usage_invocations.py — usage log de invocação (gate v1.0).

Distinto do log da shelf (logs/usage.jsonl); este é NEURATA_HOME/usage.log,
uma linha por invocação de CLI. Best-effort, torn-line-tolerante.
"""
import json

from neurata.home import NeurataHome
from neurata.usage import log_invocation, read_invocations


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    return home


def test_log_invocation_writes_valid_line(tmp_path):
    home = _home(tmp_path)
    assert log_invocation(home, "query", 12, True) is True
    path = home.root / "usage.log"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["cmd"] == "query"
    assert rec["duration_ms"] == 12
    assert rec["ok"] is True
    assert rec["ts"].endswith("+00:00")


def test_log_invocation_appends(tmp_path):
    home = _home(tmp_path)
    log_invocation(home, "deposit", 3, True)
    log_invocation(home, "tick", 40, True)
    data = read_invocations(home)
    assert len(data["entries"]) == 2
    assert [e["cmd"] for e in data["entries"]] == ["deposit", "tick"]
    assert data["corrupt_lines"] == 0


def test_read_invocations_missing_file(tmp_path):
    home = _home(tmp_path)
    data = read_invocations(home)
    assert data == {"entries": [], "corrupt_lines": 0}


def test_read_invocations_tolerates_corrupt(tmp_path):
    home = _home(tmp_path)
    path = home.root / "usage.log"
    path.write_text(
        '{"ts":"2026-07-23T10:00:00+00:00","cmd":"query","duration_ms":5,"ok":true}\n'
        'not json at all\n'
        '{"cmd":"missing_ts","duration_ms":1,"ok":true}\n'
        '{"ts":"2026-07-23T11:00:00+00:00","cmd":"tick","duration_ms":9,"ok":true}\n',
        encoding="utf-8")
    data = read_invocations(home)
    assert len(data["entries"]) == 2
    assert data["corrupt_lines"] == 2
    assert [e["cmd"] for e in data["entries"]] == ["query", "tick"]


def test_log_invocation_best_effort_never_raises(tmp_path):
    # root aponta pra caminho onde o arquivo não pode ser criado: um
    # arquivo comum no lugar do diretório root faz o open falhar.
    blocker = tmp_path / "blocked"
    blocker.write_text("x")
    home = NeurataHome(blocker / "sub")  # sub/ não existe e não dá pra criar
    assert log_invocation(home, "query", 1, True) is False
