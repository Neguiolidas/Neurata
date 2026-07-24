"""tests/test_doctor_last_tick.py — check last-tick do doctor.

Lê o maior ts de cmd:"tick" no usage.log. ok ≤ 2h; warn > 2h; warn se
ausente. Nunca fail (cron parado não corrompe nada).
"""
from datetime import datetime, timedelta, timezone

from neurata.doctor import _last_tick
from neurata.home import NeurataHome
from neurata.usage import log_invocation


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    return home


def _write_tick(home, when):
    # escreve uma linha de tick com ts arbitrário direto no usage.log
    line = ('{"ts":"%s","cmd":"tick","duration_ms":5,"ok":true}\n'
            % when.isoformat(timespec="seconds"))
    (home.root / "usage.log").write_text(line, encoding="utf-8")


def test_no_tick_is_warn(tmp_path):
    home = _home(tmp_path)
    log_invocation(home, "query", 3, True)  # uso, mas nenhum tick
    c = _last_tick(home)
    assert c.status == "warn"
    assert c.name == "last-tick"


def test_missing_usage_log_is_warn(tmp_path):
    home = _home(tmp_path)
    c = _last_tick(home)
    assert c.status == "warn"


def test_recent_tick_is_ok(tmp_path):
    home = _home(tmp_path)
    _write_tick(home, datetime.now(timezone.utc) - timedelta(minutes=30))
    c = _last_tick(home)
    assert c.status == "ok"


def test_stale_tick_is_warn(tmp_path):
    home = _home(tmp_path)
    _write_tick(home, datetime.now(timezone.utc) - timedelta(hours=3))
    c = _last_tick(home)
    assert c.status == "warn"


def test_last_tick_never_fails(tmp_path):
    home = _home(tmp_path)
    _write_tick(home, datetime.now(timezone.utc) - timedelta(days=10))
    c = _last_tick(home)
    assert c.status != "fail"
