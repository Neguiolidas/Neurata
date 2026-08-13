"""tests/test_indexdb.py"""
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from conftest import forge_v7_entries, insert_entry, set_index_version

from neurata.deposit import deposit
from neurata.frontmatter import parse
from neurata.home import NeurataHome
from neurata.indexdb import (
    INDEX_SCHEMA_VERSION,
    IndexLock,
    IndexSchemaError,
    LockHeldError,
    check_schema,
    connect,
    create_schema,
    drop_schema,
    ensure_fts5,
    fts5_available,
    load_shingle_sets,
    project_of,
    provenance,
    schema_state,
    stamp_if_unversioned,
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
    # IntegrityError e não Exception: `raises(Exception)` passaria também
    # se o INSERT quebrasse por erro de sintaxe, sem provar nada sobre o
    # CHECK de `location`.
    with pytest.raises(sqlite3.IntegrityError):
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
    with IndexLock(home), pytest.raises(LockHeldError):
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
    with pytest.raises(sqlite3.IntegrityError):  # NOT NULL de `shingles`
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


# Phase 1 — Schema v6: source_key column tests

def test_create_schema_has_source_key_column(tmp_path):
    """Teste RED: create_schema deve criar entries com coluna source_key."""
    con = connect(_home(tmp_path))
    columns = con.execute("PRAGMA table_info(entries)").fetchall()
    column_names = [col[1] for col in columns]
    assert "source_key" in column_names


def test_index_schema_version_is_9():
    """v9 = coluna `project`, derivada do frontmatter. Literal de
    propósito: o número é contrato com índices no disco, então subir a
    constante tem que quebrar um teste e forçar um passo de migração."""
    assert INDEX_SCHEMA_VERSION == 9


def test_create_schema_has_project_column(tmp_path):
    """Índice novo já nasce com a coluna da v9 — senão só quem migrou
    de um índice antigo teria `project`, e a query divergiria por origem
    do banco."""
    con = connect(_home(tmp_path))
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(entries)")}
        assert "project" in cols
    finally:
        con.close()


def test_create_schema_has_provenance_columns(tmp_path):
    """Índice novo já nasce com as colunas de procedência da v8."""
    con = connect(_home(tmp_path))
    columns = {col[1] for col in con.execute("PRAGMA table_info(entries)")}
    assert {"agent", "session", "origin"} <= columns


def test_provenance_extracts_the_three_fields():
    meta = {"source": {"agent": "hermes", "session": "s1",
                       "origin": "cli", "git_branch": "main"}}
    assert provenance(meta) == ("hermes", "s1", "cli")


def test_provenance_without_source_is_all_none():
    assert provenance({"id": "01A"}) == (None, None, None)


@pytest.mark.parametrize("bad", ["hermes", ["hermes"], 7, None])
def test_provenance_source_not_a_dict_is_all_none(bad):
    """Frontmatter é entrada não confiável: `source:` escalar não explode."""
    assert provenance({"source": bad}) == (None, None, None)


@pytest.mark.parametrize("bad", [7, {"a": 1}, ["x"], None])
def test_provenance_non_string_field_is_none(bad):
    """Campo não-string vira NULL, não `str(...)`: indexar "['x']" como
    nome de agente seria inventar um agente que não existe."""
    meta = {"source": {"agent": bad, "session": "s1", "origin": "cli"}}
    assert provenance(meta) == (None, "s1", "cli")


def test_provenance_empty_string_is_none():
    """"" e ausente são a mesma coisa — senão `missing:agent` mentiria."""
    meta = {"source": {"agent": "  ", "session": "", "origin": "cli"}}
    assert provenance(meta) == (None, None, "cli")


def test_check_schema_raises_on_v6_index(tmp_path):
    """check_schema com índice v6 (pré-regime) deve levantar."""
    con = connect(_home(tmp_path))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('index_schema_version',"
                " ?)", (str(6),))
    con.commit()
    with pytest.raises(IndexSchemaError):
        check_schema(con)


def test_check_schema_not_requiring_reindexed_with_never_reindexed(tmp_path):
    """Teste RED: check_schema(require_reindexed=False) com índice nunca-reindexado não deve levantar."""
    con = connect(_home(tmp_path))
    # Não gravar versão na meta — simula índice nunca reindexado
    check_schema(con, require_reindexed=False)  # não deve levantar


def test_check_schema_requiring_reindexed_with_never_reindexed(tmp_path):
    """Teste RED: check_schema(require_reindexed=True) com índice nunca-reindexado deve levantar."""
    con = connect(_home(tmp_path))
    # Não gravar versão na meta — simula índice nunca reindexado
    with pytest.raises(IndexSchemaError):
        check_schema(con, require_reindexed=True)


def test_check_schema_raises_on_v5_index_even_when_not_requiring_reindexed(tmp_path):
    """Mismatch de versão (índice v5, schema atual v6) deve levantar
    IndexSchemaError independente de require_reindexed=False — a checagem
    de versão é anterior/independente do parâmetro `require_reindexed`,
    que só afeta o caso de índice nunca-reindexado (sem linha em meta)."""
    con = connect(_home(tmp_path))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('index_schema_version',"
                " ?)", (str(6),))
    con.commit()
    with pytest.raises(IndexSchemaError):
        check_schema(con, require_reindexed=False)


def test_source_key_accepts_null(tmp_path):
    con = connect(_home(tmp_path))
    con.execute(
        "INSERT INTO entries(id, slug, path, location, type, env, title,"
        " description, content_hash, created, updated, shingles,"
        " source_key)"
        " VALUES ('u1','s1','library/s1.md','library','note','generic',"
        " 't','', 'h1','2026-01-01','2026-01-01','[]', NULL)")
    con.commit()
    row = con.execute(
        "SELECT source_key FROM entries WHERE id='u1'").fetchone()
    assert row[0] is None


def test_source_key_accepts_value(tmp_path):
    con = connect(_home(tmp_path))
    con.execute(
        "INSERT INTO entries(id, slug, path, location, type, env, title,"
        " description, content_hash, created, updated, shingles,"
        " source_key)"
        " VALUES ('u1','s1','library/s1.md','library','note','generic',"
        " 't','', 'h1','2026-01-01','2026-01-01','[]', ?)",
        ("some-source-key",))
    con.commit()
    row = con.execute(
        "SELECT source_key FROM entries WHERE id='u1'").fetchone()
    assert row[0] == "some-source-key"


# ── stamp_if_unversioned: verificação direta de colunas (v1.1, F3) ────

def test_stamp_refuses_index_whose_columns_are_old(tmp_path):
    """Índice v7 sem carimbo e com entries não-vazia NÃO pode ser
    carimbado: a prova indutiva ("um INSERT só passa no schema novo")
    não vale num run que não inseriu nada. Carimbar aqui promoveria o
    schema velho a `current` e desligaria a cura a jusante."""
    con = connect(_home(tmp_path))
    forge_v7_entries(con)
    insert_entry(con, "v7a", "motor-v7", "library/motor-v7.md", "Motor V7")
    set_index_version(con, None)

    stamp_if_unversioned(con)

    assert schema_state(con) == "unstamped"


def test_stamp_marks_index_whose_columns_are_current(tmp_path):
    """Contraparte: colunas de procedência presentes + entries não-vazia
    → carimba, que é o caso que destrava `query` sem reindex redundante."""
    con = connect(_home(tmp_path))
    insert_entry(con, "v8a", "motor-v8", "library/motor-v8.md", "Motor V8")
    set_index_version(con, None)

    stamp_if_unversioned(con)

    row = con.execute("SELECT value FROM meta WHERE"
                      " key='index_schema_version'").fetchone()
    assert row[0] == str(INDEX_SCHEMA_VERSION)


def test_stamp_is_noop_on_empty_index(tmp_path):
    """Índice vazio segue não-carimbado — indistinguível de library com
    arquivos nunca indexados; só `reindex` promove esse caso."""
    con = connect(_home(tmp_path))
    set_index_version(con, None)

    stamp_if_unversioned(con)

    assert schema_state(con) == "unstamped"


# ── check_schema: sem carimbo ≠ índice novo (v1.1, F3) ────────────────

def test_check_schema_rejects_unstamped_index_with_old_columns(tmp_path):
    """A tolerância de `require_reindexed=False` existe pro home recém-
    inicializado. Um v7 nunca carimbado tem a mesma cara no meta e cara
    diferente na tabela: quem escrever nele morre em `no column named
    agent` no meio do trabalho."""
    con = connect(_home(tmp_path))
    forge_v7_entries(con)
    insert_entry(con, "v7a", "motor-v7", "library/motor-v7.md", "Motor V7")
    set_index_version(con, None)

    with pytest.raises(IndexSchemaError, match="reindex"):
        check_schema(con, require_reindexed=False)


def test_check_schema_accepts_unstamped_index_with_current_columns(tmp_path):
    """Contraparte: o home recém-inicializado que a tolerância protege —
    sem carimbo, sem dados, colunas correntes. O tick roda."""
    con = connect(_home(tmp_path))
    set_index_version(con, None)

    check_schema(con, require_reindexed=False)


def test_project_of_reads_what_the_depositor_actually_writes(tmp_path,
                                                             monkeypatch):
    """Âncora contra ficção: a forma vem do writer real, não de um dict
    escrito à mão. `project_of` já leu `source.git.root` aninhado — que
    nenhum writer produz, porque o frontmatter é um subset de um nível e
    `deposit._flatten` achata para `git_root` — e o bug passou por todo o
    unitário justamente por os testes fabricarem a forma que testavam.

    Repo próprio em vez de herdar o cwd da suíte: o nome do projeto que
    se afirma tem que ser escolhido pelo teste, não pelo diretório de
    onde o pytest foi chamado.
    """
    repo = tmp_path / "MeuProjeto"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    # Precisa de commit: sem HEAD resolvível o `rev-parse` sai com rc != 0
    # e o envelope fica sem git nenhum.
    subprocess.run(["git", "-C", str(repo),
                    "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "c0"], check=True)
    monkeypatch.chdir(repo)

    home = _home(tmp_path / "home")
    rec = deposit(home, "corpo em repo", title="Grao")
    meta, _ = parse((home.root / rec["path"]).read_text(encoding="utf-8"))

    assert meta["source"]["git_root"] == str(Path(repo).resolve())
    assert project_of(meta) == "MeuProjeto"


def test_project_of_prefers_explicit_over_git_root():
    meta = {"project": "Explicito",
            "source": {"git_root": "/repos/Neurata"}}
    assert project_of(meta) == "Explicito"


def test_project_of_derives_basename_from_git_root():
    meta = {"source": {"git_root": "/repos/Neurata"}}
    assert project_of(meta) == "Neurata"


def test_project_of_ignores_trailing_slash_in_git_root():
    meta = {"source": {"git_root": "/repos/Neurata/"}}
    assert project_of(meta) == "Neurata"


def test_project_of_is_none_for_mirror():
    """Espelho é cache de sistema externo, não trabalho num repo meu —
    `None` por construção, como em `provenance()`."""
    meta = {"source_key": "obsidian:vault/x", "project": "Qualquer",
            "source": {"git_root": "/repos/Alheio"}}
    assert project_of(meta) is None


@pytest.mark.parametrize("meta", [
    {},
    {"project": "   "},
    {"project": ["a", "b"]},
    {"project": {"nome": "x"}},
    {"project": 42},
    {"source": "escalar"},
    {"source": {"git_root": ""}},
    {"source": {"git_root": 42}},
    {"source": {"git_root": "/"}},
    {"source": {"git": {"root": "/repos/Neurata"}}},
])
def test_project_of_collapses_garbage_to_none(meta):
    """Frontmatter é entrada não confiável e nada aqui coage tipo:
    `str(["a","b"])` indexaria `"['a', 'b']"` como nome de projeto."""
    assert project_of(meta) is None
