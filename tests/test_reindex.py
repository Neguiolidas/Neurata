"""tests/test_reindex.py"""
import os

import pytest

from neurata.deposit import deposit
from neurata.home import NeurataHome
from neurata.indexdb import connect
from neurata.reindex import reindex


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    return home


def test_reindex_inbox_and_library(tmp_path):
    home = _home(tmp_path)
    deposit(home, "Compactação recuperável via archive.", title="Compactação")
    (home.library / "motor-hibrido.md").write_text(
        "---\nid: 01LIB\ntitle: Motor Híbrido\ntags: [rag]\n---\n"
        "BM25 e vetores fundidos por RRF.\n")
    result = reindex(home)
    assert result["indexed"] == 2
    assert result["skipped"] == []
    con = connect(home)
    rows = con.execute(
        "SELECT slug, location FROM entries ORDER BY location").fetchall()
    assert ("motor-hibrido", "library") in rows
    assert any(loc == "inbox" for _, loc in rows)


def test_fts_normalized_search(tmp_path):
    home = _home(tmp_path)
    (home.library / "nota.md").write_text(
        "---\nid: 01N\ntitle: Decisão de compactação\n---\ncurate_tick roda.\n")
    reindex(home)
    con = connect(home)
    hits = con.execute(
        "SELECT rowid FROM entries_fts WHERE entries_fts MATCH ?",
        ("title_norm:decisao AND body_norm:curate",)).fetchall()
    assert len(hits) == 1


def test_skips_invalid_and_reports(tmp_path):
    home = _home(tmp_path)
    (home.library / "sem-id.md").write_text("---\ntitle: X\n---\ncorpo\n")
    (home.library / "quebrado.md").write_text("---\nsem fim")
    (home.library / "ok.md").write_text("---\nid: 01OK\ntitle: Ok\n---\nc\n")
    result = reindex(home)
    assert result["indexed"] == 1
    reasons = {s["path"]: s["reason"] for s in result["skipped"]}
    assert reasons["library/sem-id.md"] == "missing-id"
    assert reasons["library/quebrado.md"] == "unparseable"


def test_slug_collision_skipped(tmp_path):
    home = _home(tmp_path)
    (home.library / "dup.md").write_text("---\nid: 01A\ntitle: A\n---\nx\n")
    sub = home.library / "sub"
    sub.mkdir()
    (sub / "dup.md").write_text("---\nid: 01B\ntitle: B\n---\ny\n")
    result = reindex(home)
    assert result["indexed"] == 1
    assert result["skipped"][0]["reason"] == "slug-collision"


@pytest.mark.skipif(os.geteuid() == 0, reason="root lê arquivos chmod 000")
def test_unreadable_file_skipped_not_fatal(tmp_path):
    home = _home(tmp_path)
    (home.library / "ok.md").write_text("---\nid: 01OK\ntitle: Ok\n---\nc\n")
    bad = home.library / "sem-leitura.md"
    bad.write_text("---\nid: 01BAD\ntitle: Bad\n---\nx\n")
    bad.chmod(0o000)
    try:
        result = reindex(home)
    finally:
        bad.chmod(0o644)
    assert result["indexed"] == 1
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["reason"] == "unreadable"
    assert result["skipped"][0]["path"] == "library/sem-leitura.md"
    con = connect(home)
    row = con.execute(
        "SELECT value FROM meta WHERE key = 'last_reindex'").fetchone()
    assert row is not None and row[0]


def test_rebuild_is_idempotent(tmp_path):
    home = _home(tmp_path)
    (home.library / "a.md").write_text("---\nid: 01A\ntitle: A\n---\nx\n")
    reindex(home)
    result = reindex(home)
    assert result["indexed"] == 1
    con = connect(home)
    assert con.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1


def test_edges_and_link_counters(tmp_path):
    home = _home(tmp_path)
    (home.library / "a.md").write_text(
        "---\nid: 01LA\ntitle: Alpha\naliases: [primeira]\n---\n"
        "liga [[b]] e [[Beta#sec|texto]] e [[nada]] e [[dupe]].\n")
    (home.library / "b.md").write_text(
        "---\nid: 01LB\ntitle: Beta\n---\nvolta [[primeira]] e [[a]].\n")
    (home.library / "c.md").write_text("---\nid: 01LC\ntitle: Dupe\n---\nx\n")
    (home.library / "d.md").write_text("---\nid: 01LD\ntitle: Dupe\n---\ny\n")
    result = reindex(home)
    assert result["edges"] == 2  # a→b e b→a, deduplicados por par
    assert result["unresolved_links"] == 1  # [[nada]]
    assert result["ambiguous_links"] == 1   # [[dupe]] (2 títulos "Dupe")


def test_id_collision_label(tmp_path):
    home = _home(tmp_path)
    (home.library / "um.md").write_text("---\nid: 01X\ntitle: Um\n---\nx\n")
    (home.library / "dois.md").write_text("---\nid: 01X\ntitle: Dois\n---\ny\n")
    result = reindex(home)
    assert result["indexed"] == 1
    assert result["skipped"][0]["reason"] == "id-collision"


def test_entry_tags_lowercased(tmp_path):
    home = _home(tmp_path)
    (home.library / "t.md").write_text(
        "---\nid: 01T\ntitle: T\ntags: [RAG, Busca]\n---\nx\n")
    reindex(home)
    con = connect(home)
    tags = {r[0] for r in con.execute("SELECT tag FROM entry_tags")}
    assert tags == {"rag", "busca"}


def test_alias_indexed_and_searchable(tmp_path):
    home = _home(tmp_path)
    (home.library / "m.md").write_text(
        "---\nid: 01M\ntitle: Motor\naliases: [hybrid-motor]\n---\nx\n")
    reindex(home)
    con = connect(home)
    hits = con.execute("SELECT rowid FROM entries_fts WHERE entries_fts"
                       " MATCH 'aliases:hybrid'").fetchall()
    assert len(hits) == 1
