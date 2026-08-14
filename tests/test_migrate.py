"""tests/test_migrate.py — cadeia de migração v7 → v8 → v9 → v10.

v8 = procedência curada (§5.2/§10); v9 = coluna `project` (§5.3);
v10 = eixo de memória, com `edges` rechaveada de `rowid` pra `id`.

Cada teste parte de um índice **forjado pela DDL histórica** (conftest),
nunca de um índice novo com o `meta` adulterado: só a tabela de verdade
prova que a migração fez o trabalho em vez de mentir sobre ele. Quem
começa em v7 atravessa a cadeia inteira e por isso afirma
`INDEX_SCHEMA_VERSION`, não um literal que apodrece no próximo passo.
"""
import sqlite3

import pytest
from conftest import (
    forge_v7_entries,
    forge_v9_edges,
    insert_entry,
    set_index_version,
)

from neurata import frontmatter, indexdb
from neurata.deposit import deposit
from neurata.harvest import harvest
from neurata.home import NeurataHome
from neurata.indexdb import (
    INDEX_SCHEMA_VERSION,
    IndexLock,
    IndexSchemaError,
    LockHeldError,
    connect,
    entries_columns,
    migrate_if_needed,
    schema_state,
)
from neurata.tick import curate_tick

_PROVENANCE = ("agent", "session", "origin")


def _home(tmp_path) -> NeurataHome:
    home = NeurataHome(tmp_path)
    home.init()
    return home


def _write_grain(home: NeurataHome, name: str, meta_block: str,
                 body: str = "corpo") -> str:
    """Escreve `library/<name>` e devolve o `path` relativo do índice."""
    (home.library / name).write_text(f"---\n{meta_block}---\n{body}\n",
                                     encoding="utf-8")
    return f"library/{name}"


def _curated(home: NeurataHome, con: sqlite3.Connection, entry_id: str,
             agent: str, session: str, origin: str) -> None:
    path = _write_grain(home, f"{entry_id}.md",
                        "title: Grão\n"
                        "source:\n"
                        f"  agent: {agent}\n"
                        f"  session: {session}\n"
                        f"  origin: {origin}\n")
    insert_entry(con, entry_id, entry_id, path, "Grão", regime="curated")


def _mirror(home: NeurataHome, con: sqlite3.Connection, entry_id: str) -> None:
    path = _write_grain(home, f"{entry_id}.md",
                        "title: Espelho\n"
                        "source:\n"
                        "  agent: alheio\n")
    insert_entry(con, entry_id, entry_id, path, "Espelho", regime="mirror",
                 source_key="obsidian:vault/x")


def _v7(tmp_path) -> "tuple[NeurataHome, sqlite3.Connection]":
    home = _home(tmp_path)
    con = connect(home)
    forge_v7_entries(con)
    set_index_version(con, 7)
    return home, con


def _prov(con: sqlite3.Connection, entry_id: str) -> tuple:
    return con.execute("SELECT agent, session, origin FROM entries"
                       " WHERE id=?", (entry_id,)).fetchone()


# ── 1-3: o caminho feliz ──────────────────────────────────────────────

def test_migration_preserves_rows_and_fts(tmp_path):
    """Migração é ALTER + UPDATE: nenhuma linha nasce ou morre, e a FTS
    (que não é tocada) segue com a mesma contagem."""
    home, con = _v7(tmp_path)
    _curated(home, con, "c1", "hermes", "s-1", "manual")
    _mirror(home, con, "m1")
    con.execute("INSERT INTO entries_fts(rowid, title, body)"
                " SELECT rowid, title, '' FROM entries")
    con.commit()
    before = (con.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
              con.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0])
    con.close()

    con = connect(home)
    try:
        migrate_if_needed(con, home)
        after = (con.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
                 con.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0])
        assert after == before == (2, 2)
    finally:
        con.close()


def test_migration_backfills_curated_and_never_mirror(tmp_path):
    """O backfill lê o `.md` e grava a procedência dos curados. Espelho é
    NULL por construção (`provenance` já recusa `source_key`) — se algum
    dia vazar, é procedência inventada e o teste cai."""
    home, con = _v7(tmp_path)
    _curated(home, con, "c1", "hermes", "s-1", "manual")
    _mirror(home, con, "m1")
    con.close()

    con = connect(home)
    try:
        migrate_if_needed(con, home)
        assert _prov(con, "c1") == ("hermes", "s-1", "manual")
        assert _prov(con, "m1") == (None, None, None)
    finally:
        con.close()


def test_migration_stamps_current_version(tmp_path):
    home, con = _v7(tmp_path)
    _curated(home, con, "c1", "hermes", "s-1", "manual")
    con.close()

    con = connect(home)
    try:
        assert migrate_if_needed(con, home) == INDEX_SCHEMA_VERSION
        assert schema_state(con) == "current"
    finally:
        con.close()


# ── 4, 10: idempotência ───────────────────────────────────────────────

def test_second_migration_is_a_noop(tmp_path):
    """Rodar de novo não pode reescrever nada: a 2ª chamada devolve None
    (nada migrado) e a procedência editada à mão sobrevive."""
    home, con = _v7(tmp_path)
    _curated(home, con, "c1", "hermes", "s-1", "manual")
    con.close()

    con = connect(home)
    try:
        migrate_if_needed(con, home)
        con.execute("UPDATE entries SET agent='editado' WHERE id='c1'")
        con.commit()

        assert migrate_if_needed(con, home) is None
        assert _prov(con, "c1") == ("editado", "s-1", "manual")
    finally:
        con.close()


def test_migration_on_current_index_is_noop(tmp_path):
    home = _home(tmp_path)
    con = connect(home)
    try:
        set_index_version(con, INDEX_SCHEMA_VERSION)
        assert migrate_if_needed(con, home) is None
    finally:
        con.close()


# ── 5-7: falha no meio ────────────────────────────────────────────────

def test_crash_midway_rolls_back_ddl_and_stamp(tmp_path, monkeypatch):
    """Crash na 2ª linha: nem carimbo, nem colunas. Um v7 com as colunas
    penduradas e sem carimbo é o estado que faria o próximo `INSERT` do
    tick pensar que tem schema novo — a transação existe pra isso."""
    home, con = _v7(tmp_path)
    _curated(home, con, "c1", "hermes", "s-1", "manual")
    _curated(home, con, "c2", "atena", "s-2", "manual")
    con.close()

    calls = {"n": 0}
    real_parse = frontmatter.parse

    def _explode(text: str):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disco pegando fogo")
        return real_parse(text)

    monkeypatch.setattr("neurata.indexdb.frontmatter.parse", _explode)

    con = connect(home)
    try:
        with pytest.raises(RuntimeError, match="disco pegando fogo"):
            migrate_if_needed(con, home)

        assert schema_state(con) == "mismatch"
        assert not set(_PROVENANCE) & entries_columns(con)
    finally:
        con.close()


def test_migration_resumes_over_dangling_columns(tmp_path):
    """v7 com as colunas já penduradas — herança de uma versão sem
    transação. O guard é a coluna, não o carimbo: `ALTER` é pulado e a
    migração completa em vez de morrer em `duplicate column name`."""
    home, con = _v7(tmp_path)
    _curated(home, con, "c1", "hermes", "s-1", "manual")
    for col in _PROVENANCE:
        con.execute(f"ALTER TABLE entries ADD COLUMN {col} TEXT")
    con.commit()
    con.close()

    con = connect(home)
    try:
        assert migrate_if_needed(con, home) == INDEX_SCHEMA_VERSION
        assert _prov(con, "c1") == ("hermes", "s-1", "manual")
    finally:
        con.close()


def test_unreadable_grain_becomes_null_and_migration_completes(tmp_path):
    """Arquivo sumido ou frontmatter quebrado é uma linha NULL, não uma
    migração abortada: o índice inteiro não pode ficar refém de um `.md`
    que o usuário apagou na mão."""
    home, con = _v7(tmp_path)
    _curated(home, con, "sumido", "hermes", "s-1", "manual")
    _curated(home, con, "quebrado", "atena", "s-2", "manual")
    _curated(home, con, "bom", "iris", "s-3", "manual")
    (home.library / "sumido.md").unlink()
    (home.library / "quebrado.md").write_text("---\nsem fim\n",
                                              encoding="utf-8")
    con.close()

    con = connect(home)
    try:
        assert migrate_if_needed(con, home) == INDEX_SCHEMA_VERSION
        assert _prov(con, "sumido") == (None, None, None)
        assert _prov(con, "quebrado") == (None, None, None)
        assert _prov(con, "bom") == ("iris", "s-3", "manual")
    finally:
        con.close()


# ── 8-9: fora de alcance ──────────────────────────────────────────────

def test_v6_index_demands_reindex(tmp_path):
    """Só existe registry de 7→8. Versão mais velha não tem caminho e
    precisa de `reindex` — dizer isso é melhor que migrar pela metade."""
    home, con = _v7(tmp_path)
    set_index_version(con, 6)
    con.close()

    con = connect(home)
    try:
        with pytest.raises(IndexSchemaError, match="reindex"):
            migrate_if_needed(con, home)
        assert schema_state(con) == "mismatch"
    finally:
        con.close()


def test_migration_refuses_to_run_under_foreign_lock(tmp_path):
    """A entrada pública pega o lock: se outro processo estiver escrevendo,
    é `LockHeldError` e não uma migração concorrente."""
    home, con = _v7(tmp_path)
    _curated(home, con, "c1", "hermes", "s-1", "manual")
    con.close()

    con = connect(home)
    try:
        with IndexLock(home), pytest.raises(LockHeldError):
            migrate_if_needed(con, home)
        assert schema_state(con) == "mismatch"
    finally:
        con.close()


# ── as portas: quem chama a migração de verdade (§8) ──────────────────
# A função migrar certo não basta — o valor da fase é nenhum dos três
# caminhos de entrada recusar um v7 legítimo. E o `tick` é o que mais
# pode dar errado: ele já detém o `IndexLock`, então tem de chamar a
# variante que *não* o pede de novo, sob pena de deadlock em LockHeldError.

def test_tick_migrates_stamped_v7_instead_of_refusing(tmp_path):
    """`curate_tick` num v7 carimbado migra e cura no mesmo passo.

    A biblioteca fica vazia de propósito: um grão v7 forjado é órfão do
    journal e o `tick` o quarentena por uma regra ortogonal — misturar as
    duas faria o teste falhar por um motivo que não é o desta fase.
    """
    home, con = _v7(tmp_path)
    con.close()
    deposit(home, content="Título Novo\n\nCorpo novo qualquer.")

    report = curate_tick(home)

    assert report.processed == 1
    con = connect(home)
    try:
        assert schema_state(con) == "current"
    finally:
        con.close()


def test_harvest_migrates_stamped_v7_instead_of_refusing(tmp_path):
    """Mesma porta pelo lado do `harvest`, que não detém o lock."""
    home, con = _v7(tmp_path)
    _curated(home, con, "c1", "hermes", "s-1", "manual")
    con.close()
    source = tmp_path.parent / "fonte-v7"
    source.mkdir()
    (source / "nota.md").write_text("# Nota\n\nCorpo colhido.\n",
                                    encoding="utf-8")

    report = harvest(home, "fonte", source_dir=source)

    assert report.harvested == 1
    con = connect(home)
    try:
        assert schema_state(con) == "current"
        # o `harvest` não reconcilia a biblioteca: aqui o backfill do grão
        # v7 tem de estar de pé depois de atravessar a porta.
        assert _prov(con, "c1") == ("hermes", "s-1", "manual")
    finally:
        con.close()


def test_migration_step_that_does_not_stamp_raises(tmp_path, monkeypatch):
    """Passo que esquece de carimbar viraria laço infinito sob IndexLock —
    e laço infinito sob lock trava o acervo inteiro. Vira erro."""
    home, con = _v7(tmp_path)
    con.close()
    monkeypatch.setitem(indexdb._MIGRATIONS, 7, lambda con, home: None)
    con = connect(home)
    try:
        with pytest.raises(IndexSchemaError, match="não avançou"):
            migrate_if_needed(con, home)
    finally:
        con.close()


def test_migration_step_that_moves_backwards_raises(tmp_path, monkeypatch):
    """Progresso é estrito: carimbar pra trás também é laço."""
    home, con = _v7(tmp_path)
    con.close()

    def _regride(con, home):
        con.execute("INSERT OR REPLACE INTO meta VALUES"
                    " ('index_schema_version', '6')")
        con.commit()

    monkeypatch.setitem(indexdb._MIGRATIONS, 7, _regride)
    con = connect(home)
    try:
        with pytest.raises(IndexSchemaError, match="não avançou"):
            migrate_if_needed(con, home)
    finally:
        con.close()


def test_migration_chains_v7_to_current(tmp_path):
    """Um v7 chega ao schema corrente numa chamada só: o loop segue o
    dicionário de passos, não um número escrito na mão."""
    home, con = _v7(tmp_path)
    _curated(home, con, "c1", "hermes", "s-1", "manual")
    con.close()
    con = connect(home)
    try:
        assert migrate_if_needed(con, home) == INDEX_SCHEMA_VERSION
    finally:
        con.close()


# ── v8 → v9: `project` dos curados ────────────────────────────────────

def _v8(tmp_path) -> "tuple[NeurataHome, sqlite3.Connection]":
    """Índice com a DDL corrente carimbado como v8 — a coluna `project`
    existe desde a v7, então aqui o carimbo basta: não há DDL a forjar."""
    home = _home(tmp_path)
    con = connect(home)
    set_index_version(con, 8)
    return home, con


def _com_git_root(home, con, entry_id: str, root: str, **extra) -> None:
    path = _write_grain(home, f"{entry_id}.md",
                        "title: Grão\n"
                        "source:\n"
                        f"  git_root: {root}\n")
    insert_entry(con, entry_id, entry_id, path, "Grão", **extra)


def _project(con, entry_id: str) -> "str | None":
    return con.execute("SELECT project FROM entries WHERE id=?",
                       (entry_id,)).fetchone()[0]


def test_v9_fills_project_from_git_root(tmp_path):
    home, con = _v8(tmp_path)
    _com_git_root(home, con, "c1", "/repos/Neurata", regime="curated")
    con.close()
    con = connect(home)
    try:
        assert migrate_if_needed(con, home) == INDEX_SCHEMA_VERSION
        assert _project(con, "c1") == "Neurata"
    finally:
        con.close()


def test_v9_never_touches_mirror(tmp_path):
    """Critério 3 é estrutural: mirror fica fora do WHERE, não depende de
    o `project_of` recusar depois."""
    home, con = _v8(tmp_path)
    _com_git_root(home, con, "m1", "/repos/Alheio", regime="mirror",
                  source_key="obsidian:vault/x")
    con.close()
    con = connect(home)
    try:
        migrate_if_needed(con, home)
        assert _project(con, "m1") is None
    finally:
        con.close()


def test_v9_invents_no_provenance(tmp_path):
    """v9 preenche `project` e só. `agent`/`session` que não existiam
    continuam não existindo (invariante 2)."""
    home, con = _v8(tmp_path)
    _com_git_root(home, con, "c1", "/repos/Neurata", regime="curated")
    con.close()
    con = connect(home)
    try:
        migrate_if_needed(con, home)
        assert _prov(con, "c1") == (None, None, None)
    finally:
        con.close()


def test_v9_skips_row_whose_file_vanished(tmp_path):
    """Divergência deliberada do precedente: `_v7_to_v8` grava NULL quando
    o arquivo sumiu; v9 pula a linha. v9 é enriquecimento, não
    reconstrução — zerar dado existente por erro de leitura é perda."""
    home, con = _v8(tmp_path)
    _com_git_root(home, con, "c1", "/repos/Neurata", regime="curated",
                  project="Anterior")
    (home.library / "c1.md").unlink()
    con.close()
    con = connect(home)
    try:
        migrate_if_needed(con, home)
        assert _project(con, "c1") == "Anterior"
    finally:
        con.close()


def test_v9_skips_row_with_broken_frontmatter(tmp_path):
    home, con = _v8(tmp_path)
    _com_git_root(home, con, "c1", "/repos/Neurata", regime="curated",
                  project="Anterior")
    (home.library / "c1.md").write_text("---\ntitle: [\n---\ncorpo\n",
                                        encoding="utf-8")
    con.close()
    con = connect(home)
    try:
        migrate_if_needed(con, home)
        assert _project(con, "c1") == "Anterior"
    finally:
        con.close()


class _CommitExplode(sqlite3.Connection):
    """Conexão cujo `commit` falha enquanto armada.

    `sqlite3.Connection` é tipo imutável (3.14): a explosão entra por
    subclasse via `factory=`, não por monkeypatch do método.
    """

    armed = False

    def commit(self):
        if self.armed:
            raise sqlite3.OperationalError("disco cheio")
        return super().commit()


def test_v9_is_atomic(tmp_path):
    """Carimbo é a penúltima escrita: se o commit explodir, ninguém vê v9
    anunciado com dado pela metade (invariante 4)."""
    home, con = _v8(tmp_path)
    _com_git_root(home, con, "c1", "/repos/Neurata", regime="curated")
    con.close()

    con = sqlite3.connect(home.index_path, factory=_CommitExplode)
    try:
        con.armed = True
        with pytest.raises(sqlite3.OperationalError):
            migrate_if_needed(con, home)
        con.armed = False
        assert _project(con, "c1") is None
        assert con.execute(
            "SELECT value FROM meta WHERE key='index_schema_version'"
        ).fetchone()[0] == "8"
    finally:
        con.close()


# ---------------------------------------------------------------- v9 → v10


def _v9(tmp_path) -> "tuple[NeurataHome, sqlite3.Connection]":
    """Índice v9 de verdade: `edges` chaveada por `rowid` (DDL histórica)."""
    home = _home(tmp_path)
    con = connect(home)
    forge_v9_edges(con)
    set_index_version(con, 9)
    return home, con


def _rowid(con: sqlite3.Connection, entry_id: str) -> int:
    return con.execute("SELECT rowid FROM entries WHERE id=?",
                       (entry_id,)).fetchone()[0]


def test_v10_traduz_aresta_de_rowid_para_id(tmp_path):
    """A aresta sobrevive à migração falando `id` em vez de `rowid`."""
    home, con = _v9(tmp_path)
    insert_entry(con, "a", "a", _write_grain(home, "a.md", "title: A\n"), "A")
    insert_entry(con, "b", "b", _write_grain(home, "b.md", "title: B\n"), "B")
    con.execute("INSERT INTO edges VALUES (?, ?)",
                (_rowid(con, "a"), _rowid(con, "b")))
    con.commit()

    migrate_if_needed(con, home)

    assert con.execute("SELECT src_id, dst_id FROM edges").fetchall() \
        == [("a", "b")]


def test_v10_descarta_aresta_pendurada(tmp_path):
    """Ponta que não existe mais em `entries` não vira aresta órfã.

    O `JOIN` limpa de graça o que a v9 deixaria apontando pro vazio.
    """
    home, con = _v9(tmp_path)
    insert_entry(con, "a", "a", _write_grain(home, "a.md", "title: A\n"), "A")
    con.execute("INSERT INTO edges VALUES (?, ?)", (_rowid(con, "a"), 4242))
    con.commit()

    migrate_if_needed(con, home)

    assert con.execute("SELECT count(*) FROM edges").fetchone()[0] == 0


def test_v10_nao_inventa_dado_no_eixo_de_memoria(tmp_path):
    """As colunas novas nascem `NULL`: quem preenche é a re-derivação."""
    home, con = _v9(tmp_path)
    insert_entry(con, "a", "a", _write_grain(home, "a.md", "title: A\n"), "A")

    migrate_if_needed(con, home)

    assert con.execute("SELECT class, source_path, derived_hash FROM entries"
                       " WHERE id='a'").fetchone() == (None, None, None)


def test_v10_deixa_indice_indistinguivel_de_um_novo(tmp_path):
    """Migrado e recém-criado convergem: mesma versão, mesmo `edges`."""
    home, con = _v9(tmp_path)
    migrate_if_needed(con, home)
    novo = connect(_home(tmp_path / "outro"))

    def forma(c: sqlite3.Connection) -> list:
        return [(r[1], r[2]) for r in c.execute("PRAGMA table_info(edges)")]

    assert forma(con) == forma(novo)
    assert schema_state(con) == "current"
