"""tests/test_reindex.py"""
from armarium.deposit import deposit
from armarium.home import ArmariumHome
from armarium.indexdb import connect
from armarium.reindex import reindex


def _home(tmp_path):
    home = ArmariumHome(tmp_path)
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


def test_rebuild_is_idempotent(tmp_path):
    home = _home(tmp_path)
    (home.library / "a.md").write_text("---\nid: 01A\ntitle: A\n---\nx\n")
    reindex(home)
    result = reindex(home)
    assert result["indexed"] == 1
    con = connect(home)
    assert con.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1
