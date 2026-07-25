"""tests/test_doctor_gate.py — check gate da v1.0 no doctor.

Critério: >= 10 dias distintos com >= 1 comando real (não-tick) em
janela corrida de 14 dias. Sempre ok — pendente mostra progresso,
passado mostra PASSED. Nunca warn, nunca fail.
"""
import json
from datetime import date, timedelta

from neurata.doctor import _gate, run_checks
from neurata.home import NeurataHome

START = date(2026, 7, 1)


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    return home


def _write_log(home, days, cmd="query"):
    lines = [json.dumps({"ts": f"{d}T12:00:00+00:00", "cmd": cmd,
                         "duration_ms": 3, "ok": True}) for d in days]
    (home.root / "usage.log").write_text("\n".join(lines) + "\n",
                                         encoding="utf-8")


def _days(offsets):
    return [START + timedelta(days=o) for o in offsets]


def test_missing_log_is_ok_zero(tmp_path):
    c = _gate(_home(tmp_path))
    assert c.status == "ok"
    assert c.detail.startswith("0/10")


def test_partial_progress_counts_distinct_days(tmp_path):
    home = _home(tmp_path)
    # 3 dias distintos, um deles duplicado — duplicata não conta
    _write_log(home, _days([0, 0, 1, 2]))
    c = _gate(home)
    assert c.status == "ok"
    assert c.detail.startswith("3/10")
    assert "PASSED" not in c.detail


def test_exactly_10_distinct_days_in_14_passes(tmp_path):
    home = _home(tmp_path)
    _write_log(home, _days([0, 1, 2, 3, 4, 9, 10, 11, 12, 13]))
    c = _gate(home)
    assert c.status == "ok"
    assert c.detail.startswith("PASSED")


def test_10_days_spread_beyond_14_does_not_pass(tmp_path):
    home = _home(tmp_path)
    # 10 dias em span de 19 — melhor janela de 14d tem só 7
    _write_log(home, _days([0, 2, 4, 6, 8, 10, 12, 14, 16, 18]))
    c = _gate(home)
    assert c.status == "ok"
    assert "PASSED" not in c.detail
    assert c.detail.startswith("7/10")


def test_only_ticks_count_zero(tmp_path):
    home = _home(tmp_path)
    _write_log(home, _days([0, 1, 2]), cmd="tick")
    c = _gate(home)
    assert c.detail.startswith("0/10")


def test_invalid_ts_is_ignored(tmp_path):
    home = _home(tmp_path)
    line = json.dumps({"ts": "not-a-date", "cmd": "query",
                       "duration_ms": 1, "ok": True})
    (home.root / "usage.log").write_text(line + "\n", encoding="utf-8")
    c = _gate(home)
    assert c.status == "ok"
    assert c.detail.startswith("0/10")


def test_gate_registered_in_run_checks(tmp_path):
    names = [c.name for c in run_checks(_home(tmp_path))]
    assert "gate" in names


def test_never_warn_never_fail(tmp_path):
    home = _home(tmp_path)
    _write_log(home, _days(list(range(20))))  # passou de sobra
    c = _gate(home)
    assert c.status == "ok"
