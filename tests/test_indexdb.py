"""tests/test_indexdb.py"""
import json

import pytest

from neurata.home import NeurataHome
from neurata.indexdb import (
    INDEX_SCHEMA_VERSION, IndexLock, IndexSchemaError, LockHeldError,
    check_schema, connect, create_schema, drop_schema, ensure_fts5,
    fts5_available, load_shingle_sets,
)


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    return home


def test_connect_wal_and_schema(tmp_path):
    con = connect(_home(tmp_path))
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','virtual')"
        " OR type='table'")}
    assert {"meta", "entries"} <= tables


def test_fts5_available_and_ensure(tmp_path):
    con = connect(_home(tmp_path))
    assert fts5_available(con) is True   # ambiente de dev TEM fts5
    ensure_fts5(con)                     # não levanta


def test_entries_constraints(tmp_path):
    con = connect(_home(tmp_path))
    con.execute(
        "INSERT INTO entries(id, slug, path, location, type, env, title,"
        " description, content_hash, created, updated, shingles)"
        " VALUES ('u1','s1','library/s1.md','library','note','generic',"
        " 't','', 'h1','2026-01-01','2026-01-01','[]')")
    with pytest.raises(Exception):
        con.execute(
            "INSERT INTO entries(id, slug, path, location, type, env, title,"
            " description, content_hash, created, updated, shingles)"
            " VALUES ('u2','s2','x','BADLOC','note','generic','t','','h',"
            " '2026-01-01','2026-01-01','[]')")


def test_drop_and_recreate(tmp_path):
    home = _home(tmp_path)
    con = connect(home)
    drop_schema(con)
    create_schema(con)
    assert con.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0


def test_lock_excludes_and_releases(tmp_path):
    home = _home(tmp_path)
    with IndexLock(home):
        with pytest.raises(LockHeldError):
            IndexLock(home).__enter__()
    with IndexLock(home):
        pass


def test_stale_lock_is_taken(tmp_path):
    home = _home(tmp_path)
    (home.root / "index.lock").write_text("999999999")  # pid impossível
    with IndexLock(home):
        pass


def test_check_schema_raises_on_missing_version(tmp_path):
    con = connect(_home(tmp_path))
    with pytest.raises(IndexSchemaError):
        check_schema(con)


def test_check_schema_raises_on_stale_version(tmp_path):
    con = connect(_home(tmp_path))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('index_schema_version',"
                " ?)", (str(INDEX_SCHEMA_VERSION - 1),))
    con.commit()
    with pytest.raises(IndexSchemaError):
        check_schema(con)


def test_check_schema_passes_and_no_side_effect(tmp_path):
    con = connect(_home(tmp_path))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('index_schema_version',"
                " ?)", (str(INDEX_SCHEMA_VERSION),))
    con.commit()
    before = con.execute("SELECT * FROM meta ORDER BY key").fetchall()
    check_schema(con)  # não levanta
    after = con.execute("SELECT * FROM meta ORDER BY key").fetchall()
    assert before == after


def test_entries_shingles_column_not_null(tmp_path):
    con = connect(_home(tmp_path))
    with pytest.raises(Exception):
        con.execute(
            "INSERT INTO entries(id, slug, path, location, type, env,"
            " title, description, content_hash, created, updated)"
            " VALUES ('u1','s1','library/s1.md','library','note',"
            " 'generic','t','','h1','2026-01-01','2026-01-01')")


def test_load_shingle_sets_roundtrip(tmp_path):
    con = connect(_home(tmp_path))
    con.execute(
        "INSERT INTO entries(id, slug, path, location, type, env, title,"
        " description, content_hash, created, updated, shingles)"
        " VALUES ('u1','s1','library/s1.md','library','note','generic',"
        " 't','', 'h1','2026-01-01','2026-01-01', ?)",
        (json.dumps(["aa", "bb"]),))
    con.execute(
        "INSERT INTO entries(id, slug, path, location, type, env, title,"
        " description, content_hash, created, updated, shingles)"
        " VALUES ('u2','s2','library/s2.md','library','note','generic',"
        " 't2','', 'h2','2026-01-01','2026-01-01', ?)",
        (json.dumps([]),))
    con.commit()
    sets = load_shingle_sets(con)
    assert sets["u1"] == frozenset({"aa", "bb"})
    assert sets["u2"] == frozenset()
    assert isinstance(sets["u1"], frozenset)
