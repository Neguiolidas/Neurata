"""tests/test_doctor.py"""
import time

from armarium.doctor import exit_code, run_checks
from armarium.home import ArmariumHome
from armarium.reindex import reindex


def _home(tmp_path):
    home = ArmariumHome(tmp_path)
    home.init()
    return home


def _by_name(checks):
    return {c.name: c for c in checks}


def test_healthy_home_all_ok(tmp_path):
    home = _home(tmp_path)
    reindex(home)
    checks = _by_name(run_checks(home))
    for name in ("python-version", "home-layout", "config", "fts5", "index"):
        assert checks[name].status == "ok", name
    assert exit_code(run_checks(home)) == 0


def test_missing_index_warns_with_remedy(tmp_path):
    home = _home(tmp_path)
    checks = _by_name(run_checks(home))
    assert checks["index"].status == "warn"
    assert "reindex" in checks["index"].remedy
    assert exit_code(run_checks(home)) == 1


def test_uninitialized_home_fails(tmp_path):
    home = ArmariumHome(tmp_path / "nao-existe")
    checks = _by_name(run_checks(home))
    assert checks["home-layout"].status == "fail"
    assert exit_code(run_checks(home)) == 2


def test_stale_index_warns(tmp_path):
    home = _home(tmp_path)
    reindex(home)
    time.sleep(1.1)  # last_reindex tem resolução de segundos
    (home.library / "novo.md").write_text("---\nid: 01X\ntitle: N\n---\nc\n")
    checks = _by_name(run_checks(home))
    assert checks["index-freshness"].status == "warn"


def test_skipped_files_warn(tmp_path):
    home = _home(tmp_path)
    (home.library / "quebrado.md").write_text("---\nsem fim")
    reindex(home)
    checks = _by_name(run_checks(home))
    assert checks["skipped-files"].status == "warn"
    assert "quebrado.md" in checks["skipped-files"].detail


def test_stale_lock_warns(tmp_path):
    home = _home(tmp_path)
    reindex(home)
    (home.root / "index.lock").write_text("999999999")
    checks = _by_name(run_checks(home))
    assert checks["lock"].status == "warn"
