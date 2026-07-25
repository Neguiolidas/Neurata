"""tests/test_doctor.py"""
import json
import os
import sqlite3
import time
from datetime import datetime

from neurata import archive, snapshot
from neurata.deposit import deposit
from neurata.doctor import _meta, exit_code, run_checks
from neurata.home import NeurataHome
from neurata.reindex import reindex
from neurata.tick import curate_tick
from neurata.usage import log_invocation


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    return home


def _by_name(checks):
    return {c.name: c for c in checks}


def test_healthy_home_all_ok(tmp_path):
    home = _home(tmp_path)
    reindex(home)
    snapshot.commit_manual(home)  # repo + commit limpo -> snapshot check ok
    log_invocation(home, "tick", 5, True)  # tick recente -> last-tick ok
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
    home = NeurataHome(tmp_path / "nao-existe")
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


def test_corrupt_index_fails_cleanly(tmp_path):
    home = _home(tmp_path)
    home.index_path.write_bytes(b"\x00lixo que nao e sqlite\xff\xfe")
    checks = run_checks(home)  # nao pode explodir
    assert len(checks) == 15  # todos os checks presentes (inclui last-tick, gate)
    by = _by_name(checks)
    assert by["index"].status == "fail"
    assert by["index"].remedy
    assert exit_code(checks) == 2


def test_freshness_same_second_not_stale(tmp_path):
    home = _home(tmp_path)
    path = home.library / "mesmo-segundo.md"
    path.write_text("---\nid: 01Y\ntitle: T\n---\nb\n")
    reindex(home)
    con = sqlite3.connect(home.index_path)
    last = con.execute(
        "SELECT value FROM meta WHERE key='last_reindex'").fetchone()[0]
    con.close()
    last_ts = datetime.fromisoformat(last).timestamp()
    # mtime fracionalmente depois, mas no MESMO segundo do last_reindex
    os.utime(path, (last_ts + 0.9, last_ts + 0.9))
    checks = _by_name(run_checks(home))
    assert checks["index-freshness"].status == "ok"


def test_freshness_ok_after_tick_rewrites_indexed_file(tmp_path):
    """O tick move inbox->library reescrevendo o arquivo e atualizando o
    índice, mas quem carimba `last_reindex` é só o `reindex`. Comparar por
    mtime dava warn (e rc=1) sempre que o tick cruzava a virada do segundo
    — flaky de 1-em-60. O corpo indexado é o mesmo: tem que ser ok."""
    home = _home(tmp_path)
    deposit(home, content="Titulo\n\ncorpo qualquer da nota\n")
    reindex(home)
    time.sleep(1.1)  # garante que o tick escreve num segundo POSTERIOR
    curate_tick(home)
    lib = list(home.library.glob("*.md"))
    assert lib, "tick precisa ter catalogado o item"
    assert int(lib[0].stat().st_mtime) > int(datetime.fromisoformat(
        _meta(home, "last_reindex")).timestamp())
    checks = _by_name(run_checks(home))
    assert checks["index-freshness"].status == "ok"


def test_freshness_warns_when_indexed_file_edited_out_of_band(tmp_path):
    """A razão de existir do check: alguém editou o arquivo por fora e o
    índice ficou com o corpo velho. Hash diferente -> warn."""
    home = _home(tmp_path)
    path = home.library / "editada.md"
    path.write_text("---\nid: 01W\ntitle: T\n---\ncorpo original\n")
    reindex(home)
    time.sleep(1.1)
    path.write_text("---\nid: 01W\ntitle: T\n---\ncorpo TROCADO por fora\n")
    checks = _by_name(run_checks(home))
    assert checks["index-freshness"].status == "warn"
    assert "reindex" in checks["index-freshness"].remedy


def test_archive_ok_when_nothing_compacted(tmp_path):
    home = _home(tmp_path)
    (home.library / "n.md").write_text("---\nid: 01Z\ntitle: T\n---\ncorpo\n")
    reindex(home)
    checks = _by_name(run_checks(home))
    assert checks["archive"].status == "ok"


def test_archive_ok_when_blob_present(tmp_path):
    home = _home(tmp_path)
    sha = archive.put(home, b"full original")
    (home.library / "c.md").write_text(
        f"---\nid: 01A\ntitle: T\nderived_from: {sha}\n---\nsummary\n")
    reindex(home)
    checks = _by_name(run_checks(home))
    assert checks["archive"].status == "ok"


def test_archive_fails_when_blob_missing(tmp_path):
    home = _home(tmp_path)
    missing_sha = "a" * 64
    (home.library / "c.md").write_text(
        f"---\nid: 01B\ntitle: T\nderived_from: {missing_sha}\n---\nsum\n")
    reindex(home)
    checks = _by_name(run_checks(home))
    assert checks["archive"].status == "fail"
    assert missing_sha in checks["archive"].detail
    assert checks["archive"].remedy


def test_usage_ok_when_no_log(tmp_path):
    home = _home(tmp_path)
    reindex(home)
    checks = _by_name(run_checks(home))
    assert checks["usage"].status == "ok"


def test_usage_warns_on_corrupt_lines(tmp_path):
    home = _home(tmp_path)
    reindex(home)
    (home.logs / "usage.jsonl").write_text("{not json\n")
    checks = _by_name(run_checks(home))
    assert checks["usage"].status == "warn"
    assert "1" in checks["usage"].detail


# ── snapshot (§8): nunca fail — degradação graciosa ──────────────────
def test_snapshot_check_in_run_checks(tmp_path):
    home = _home(tmp_path)
    assert "snapshot" in [c.name for c in run_checks(home)]


def test_snapshot_no_git_warns_never_fails(tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setattr(snapshot, "git_available", lambda: False)
    check = _by_name(run_checks(home))["snapshot"]
    assert check.status == "warn"
    assert "git" in check.detail


def test_snapshot_repo_not_init_warns(tmp_path):
    home = _home(tmp_path)  # git disponível, mas nenhum tick rodou → sem .git
    check = _by_name(run_checks(home))["snapshot"]
    assert check.status == "warn"
    assert "não inicializado" in check.detail


def test_snapshot_repo_ok(tmp_path):
    home = _home(tmp_path)
    (home.library / "n.md").write_text("---\nid: n\n---\ncorpo\n",
                                       encoding="utf-8")
    snapshot.commit_manual(home)  # cria repo + 1 commit, tree limpo
    check = _by_name(run_checks(home))["snapshot"]
    assert check.status == "ok"
    assert "1 snapshot(s)" in check.detail


def test_snapshot_auto_push_without_remote_warns(tmp_path):
    home = _home(tmp_path)
    cfg = home.load_config()
    cfg["snapshot"] = {"auto_push": True, "remote": None}
    home.config_path.write_text(json.dumps(cfg))
    check = _by_name(run_checks(home))["snapshot"]
    assert check.status == "warn"
    assert "auto_push" in check.detail
