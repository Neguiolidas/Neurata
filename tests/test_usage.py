"""tests/test_usage.py"""
import json

from neurata.home import NeurataHome
from neurata.usage import log_event, read_usage


def test_log_event_e_read(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    assert log_event(home, "query", "e1", query="foo", rank=0) is True
    assert log_event(home, "query", "e1", query="foo", rank=1) is True
    assert log_event(home, "expand", "e1") is True
    assert log_event(home, "query", "e2", query="bar", rank=0) is True

    agg = read_usage(home)
    assert agg["corrupt_lines"] == 0
    assert agg["entries"]["e1"]["impressions"] == 2
    assert agg["entries"]["e1"]["expands"] == 1
    assert agg["entries"]["e2"]["impressions"] == 1
    assert agg["entries"]["e2"]["expands"] == 0
    assert agg["entries"]["e1"]["last_used"] is not None


def test_read_usage_tolera_utf8_invalido(tmp_path):
    # Regressão: write torn/parcial deixa sequência UTF-8 cortada no fim do
    # arquivo. read_usage promete pular linha corrompida, não explodir.
    home = NeurataHome(tmp_path)
    home.init()
    log_event(home, "query", "e1", query="foo", rank=0)
    path = home.logs / "usage.jsonl"
    with path.open("ab") as fh:
        fh.write(b'\xe2\x9c')  # 3-byte char (✓) cortado ao meio

    agg = read_usage(home)  # não pode levantar UnicodeDecodeError
    assert agg["corrupt_lines"] == 1
    assert agg["entries"]["e1"]["impressions"] == 1


def test_read_usage_sem_arquivo(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    agg = read_usage(home)
    assert agg == {"entries": {}, "corrupt_lines": 0}


def test_read_usage_tolera_linha_corrompida(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    log_event(home, "query", "e1", query="foo", rank=0)
    path = home.logs / "usage.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("nao e json\n")
        fh.write(json.dumps({"event": "query"}) + "\n")  # falta entry_id
        fh.write(json.dumps({"entry_id": "e9", "event": "unknown",
                             "ts": "x"}) + "\n")
    log_event(home, "query", "e2", query="bar", rank=0)

    agg = read_usage(home)
    assert agg["corrupt_lines"] == 3
    assert agg["entries"]["e1"]["impressions"] == 1
    assert agg["entries"]["e2"]["impressions"] == 1
    assert "e9" not in agg["entries"]


def test_log_event_best_effort_nao_lanca(tmp_path, monkeypatch):
    home = NeurataHome(tmp_path)
    home.init()

    def boom(*a, **k):
        raise OSError("disco cheio")

    monkeypatch.setattr("os.open", boom)
    assert log_event(home, "query", "e1") is False
