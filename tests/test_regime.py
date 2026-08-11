"""tests/test_regime.py — o regime do índice espelha o dialeto do arquivo.

Invariante, não contagem (spec §7.1): o teste não sabe quantos grãos de cada
regime existem, só que cada um está classificado pelo seu frontmatter.
"""
import sqlite3

from neurata.frontmatter import parse, serialize
from neurata.home import NeurataHome
from neurata.reindex import reindex


def _grao(home, slug, meta_extra, body="corpo do grao de teste"):
    meta = {"id": slug, "title": slug.replace("-", " "),
            "created": "2026-08-10T00:00:00+00:00",
            "updated": "2026-08-10T00:00:00+00:00"}
    meta.update(meta_extra)
    (home.library / f"{slug}.md").write_text(
        serialize(meta, body), encoding="utf-8")


def test_regime_espelha_dialeto_do_frontmatter(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    _grao(home, "espelhado", {"source_key": "skill:alfa",
                              "source_path": "alfa/SKILL.md"})
    _grao(home, "curado-envelope",
          {"source": {"origin": "deposit", "ts": "2026-08-10T00:00:00+00:00"}})
    _grao(home, "curado-nu", {})
    reindex(home)

    con = sqlite3.connect(home.index_path)
    try:
        indexado = dict(con.execute("SELECT slug, regime FROM entries"))
    finally:
        con.close()

    esperado = {}
    for path in sorted(home.library.glob("*.md")):
        meta, _ = parse(path.read_text(encoding="utf-8"))
        esperado[path.stem] = "mirror" if meta.get("source_key") else "curated"

    assert indexado == esperado
    assert set(esperado.values()) == {"mirror", "curated"}  # sanidade


def _pista(home):
    con = sqlite3.connect(home.index_path)
    try:
        return {r[0] for r in con.execute("SELECT rowid FROM curated_fts")}
    finally:
        con.close()


def _curados(home):
    con = sqlite3.connect(home.index_path)
    try:
        return {r[0] for r in con.execute(
            "SELECT rowid FROM entries WHERE regime='curated'")}
    finally:
        con.close()


def test_pista_curada_tem_exatamente_os_curados(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    _grao(home, "espelhado", {"source_key": "skill:alfa",
                              "source_path": "alfa/SKILL.md"})
    _grao(home, "curado-a", {})
    _grao(home, "curado-b", {})
    reindex(home)
    assert _pista(home) == _curados(home)
    assert len(_pista(home)) == 2


def test_pista_nao_deixa_orfao_quando_o_grao_some(tmp_path):
    """Grão apagado da biblioteca não pode sobreviver na pista."""
    home = NeurataHome(tmp_path)
    home.init()
    _grao(home, "curado-a", {})
    _grao(home, "curado-b", {})
    reindex(home)
    (home.library / "curado-b.md").unlink()
    reindex(home)
    assert _pista(home) == _curados(home)
    assert len(_pista(home)) == 1
