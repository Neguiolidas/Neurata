"""tests/test_doctor.py"""
import json
import os
import sqlite3
import time
from datetime import datetime

from neurata import archive, snapshot
from neurata.deposit import deposit
from neurata.doctor import _meta, exit_code, run_checks
from neurata.frontmatter import serialize
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
    assert len(checks) == 17  # todos os checks presentes (inclui derived-integrity, last-tick, gate)
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


def test_regime_ok(tmp_path):
    home = _home(tmp_path)
    reindex(home)
    check = _by_name(run_checks(home))["regime"]
    assert check.status == "ok"


def test_regime_detecta_espelho_refinado(tmp_path):
    home = _home(tmp_path)
    meta = {"id": "e1", "title": "espelho", "source_key": "skill:a",
            "source_path": "a/SKILL.md", "grain_quality": "refined",
            "created": "2026-08-10T00:00:00+00:00",
            "updated": "2026-08-10T00:00:00+00:00"}
    (home.library / "e1.md").write_text(serialize(meta, "corpo"),
                                        encoding="utf-8")
    reindex(home)
    check = _by_name(run_checks(home))["regime"]
    assert check.status == "fail"
    assert "e1" in check.detail
    assert check.remedy


def test_regime_detecta_pista_dessincronizada(tmp_path):
    home = _home(tmp_path)
    meta = {"id": "c1", "title": "curado",
            "created": "2026-08-10T00:00:00+00:00",
            "updated": "2026-08-10T00:00:00+00:00"}
    (home.library / "c1.md").write_text(serialize(meta, "corpo"),
                                        encoding="utf-8")
    reindex(home)
    con = sqlite3.connect(home.index_path)
    con.execute("DELETE FROM curated_fts")   # simula o writer que esqueceu
    con.commit()
    con.close()
    check = _by_name(run_checks(home))["regime"]
    assert check.status == "fail"
    assert "reindex" in check.remedy


# ── derived-integrity: espelho compactado com fonte/blob íntegro ──────────────
def test_derived_integrity_ok_when_nothing_compacted(tmp_path):
    home = _home(tmp_path)
    (home.library / "n.md").write_text("---\nid: 01Z\ntitle: T\n---\ncorpo\n")
    reindex(home)
    checks = _by_name(run_checks(home))
    assert checks["derived-integrity"].status == "ok"
    assert "nenhuma" in checks["derived-integrity"].detail


def test_derived_integrity_ok_when_blob_and_source_present(tmp_path):
    """Espelho compactado com blob no archive e fonte no disco: ok."""
    home = _home(tmp_path)
    # Cria arquivo espelhado com blob no archive e source_path válido
    sha = archive.put(home, b"full original")
    source = home.library / "fonte.md"
    source.write_text("---\nid: espelho\ntitle: T\n---\noriginal\n")
    entry = home.library / "c.md"
    entry.write_text(
        f"---\nid: 01A\ntitle: T\nderived_from: {sha}\n"
        f"source_path: library/fonte.md\n---\nsummary\n")
    reindex(home)
    checks = _by_name(run_checks(home))
    assert checks["derived-integrity"].status == "ok"


def test_derived_integrity_ok_with_null_source_path(tmp_path):
    """Espelho compactado com source_path NULL: não deve falhar.

    Consequência declarada (spec §5.3): source_path fica NULL até o
    primeiro reindex full. É normal e não é erro."""
    home = _home(tmp_path)
    sha = archive.put(home, b"full original")
    entry = home.library / "c.md"
    entry.write_text(
        f"---\nid: 01A\ntitle: T\nderived_from: {sha}\n---\nsummary\n")
    reindex(home)
    checks = _by_name(run_checks(home))
    assert checks["derived-integrity"].status == "ok"


def test_derived_integrity_quiet_on_pre_v11_schema(tmp_path):
    """Acervo ainda não migrado (schema v10, sem a coluna `derived_from`):
    o check sai calado em vez de gritar "índice ilegível".

    Regressão do smoke de upgrade real 1.3.0 → 1.4.0: o primeiro comando que
    o usuário roda depois de atualizar é `doctor`, e ele acusava corrupção
    (com o conselho caro de reindexar) sobre um índice perfeitamente íntegro,
    só velho. Quem é dono do assunto é o check `index-schema`, e ele continua
    avisando — um fato, um aviso."""
    home = _home(tmp_path)
    (home.library / "n.md").write_text("---\nid: 01Z\ntitle: T\n---\ncorpo\n")
    reindex(home)
    con = sqlite3.connect(home.index_path)
    con.execute("DROP INDEX IF EXISTS idx_entries_derived_from")
    con.execute("ALTER TABLE entries DROP COLUMN derived_from")
    con.execute("UPDATE meta SET value='10' WHERE key='index_schema_version'")
    con.commit()
    con.close()
    checks = _by_name(run_checks(home))
    assert checks["derived-integrity"].status == "ok"
    assert "ilegível" not in checks["derived-integrity"].detail
    assert not checks["derived-integrity"].remedy
    # o aviso legítimo continua de pé, uma vez só
    assert checks["index-schema"].status == "warn"


def test_derived_integrity_fails_when_blob_missing(tmp_path):
    """Espelho compactado com blob ausente no archive: fail."""
    home = _home(tmp_path)
    missing_sha = "a" * 64
    entry = home.library / "c.md"
    entry.write_text(
        f"---\nid: 01B\ntitle: T\nderived_from: {missing_sha}\n---\nsum\n")
    reindex(home)
    checks = _by_name(run_checks(home))
    assert checks["derived-integrity"].status == "fail"
    assert missing_sha[:8] in checks["derived-integrity"].detail
    assert checks["derived-integrity"].remedy


def test_derived_integrity_fails_when_source_missing(tmp_path):
    """Espelho compactado com fonte sumida no disco: fail."""
    home = _home(tmp_path)
    sha = archive.put(home, b"full original")
    entry = home.library / "c.md"
    entry.write_text(
        f"---\nid: 01C\ntitle: T\nderived_from: {sha}\n"
        f"source_path: library/sumiu.md\n---\nsummary\n")
    reindex(home)
    checks = _by_name(run_checks(home))
    assert checks["derived-integrity"].status == "fail"
    assert "sumiu.md" in checks["derived-integrity"].detail
    assert checks["derived-integrity"].remedy


def test_derived_integrity_fails_when_both_missing(tmp_path):
    """Espelho compactado com blob e fonte ausentes: fail mostra ambos."""
    home = _home(tmp_path)
    missing_sha = "b" * 64
    entry = home.library / "c.md"
    entry.write_text(
        f"---\nid: 01D\ntitle: T\nderived_from: {missing_sha}\n"
        f"source_path: library/sumiu.md\n---\nsummary\n")
    reindex(home)
    checks = _by_name(run_checks(home))
    assert checks["derived-integrity"].status == "fail"
    detail = checks["derived-integrity"].detail
    assert missing_sha[:8] in detail
    assert "sumiu.md" in detail
