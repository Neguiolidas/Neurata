"""tests/test_query_selfheal.py — `query` num índice sem carimbo de versão.

O guard antigo recusava buscar sempre que faltava
`meta.index_schema_version`, sob o argumento de que índice vazio seria
indistinguível de haver arquivos nunca indexados. É distinguível: basta
olhar o disco, nos mesmos diretórios que o `reindex` varre.

O invariante que o guard protege — nunca devolver zero em silêncio
havendo conteúdo não indexado — continua valendo aqui, e o teste
`test_lock_held_raises_instead_of_returning_empty` é quem o trava.
"""
import pytest

from neurata.deposit import deposit
from neurata.home import NeurataHome
from neurata.indexdb import INDEX_SCHEMA_VERSION, IndexLock, connect
from neurata.query import QueryError, query


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    return home


def _write(home, name, meta_lines, body):
    (home.library / f"{name}.md").write_text(
        "---\n" + "\n".join(meta_lines) + "\n---\n" + body)


def _stamped_version(home):
    con = connect(home)
    try:
        row = con.execute(
            "SELECT value FROM meta WHERE key='index_schema_version'"
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def test_empty_home_returns_no_results(tmp_path):
    """Disco vazio: zero resultados é a verdade, não um erro."""
    home = _home(tmp_path)

    assert query(home, "qualquer coisa")["results"] == []


def test_selfheals_content_never_indexed_in_library(tmp_path):
    home = _home(tmp_path)
    _write(home, "motor-hibrido",
           ["id: 01A", "title: Motor Híbrido"],
           "BM25 e vetores fundidos por RRF.\n")

    hits = query(home, "vetores")["results"]

    assert [h["id"] for h in hits] == ["01A"]


def test_selfheals_after_deposit_without_manual_reindex(tmp_path):
    """O caso reportado: `deposit` escreve no inbox e `query` acha."""
    home = _home(tmp_path)
    created = deposit(home, content="Título Xenolítico\n\nConteúdo sobre "
                      "criptofauna abissal.")

    hits = query(home, "criptofauna")["results"]

    assert any(h["id"] == created["id"] for h in hits)


def test_selfheal_stamps_version_once(tmp_path):
    """Curado uma vez, fica curado — a segunda query não reindexa."""
    home = _home(tmp_path)
    _write(home, "nota", ["id: 01A", "title: Nota"], "conteúdo qualquer\n")
    assert _stamped_version(home) is None

    query(home, "conteúdo")

    assert _stamped_version(home) == str(INDEX_SCHEMA_VERSION)


def test_lock_held_raises_instead_of_returning_empty(tmp_path):
    """Invariante: com conteúdo não indexado e cura impossível, o erro
    tem de ser alto. Devolver [] aqui seria mentir por omissão."""
    home = _home(tmp_path)
    _write(home, "nota", ["id: 01A", "title: Nota"], "conteúdo qualquer\n")

    with IndexLock(home), pytest.raises(QueryError):
        query(home, "conteúdo")


def test_stale_schema_still_raises(tmp_path):
    """Versão presente mas divergente continua sendo erro explícito —
    self-heal aqui é decisão separada, não vem de carona."""
    home = _home(tmp_path)
    _write(home, "nota", ["id: 01A", "title: Nota"], "conteúdo qualquer\n")
    con = connect(home)
    con.execute("INSERT OR REPLACE INTO meta VALUES "
                "('index_schema_version', ?)",
                (str(INDEX_SCHEMA_VERSION - 1),))
    con.commit()
    con.close()

    with pytest.raises(QueryError):
        query(home, "conteúdo")
