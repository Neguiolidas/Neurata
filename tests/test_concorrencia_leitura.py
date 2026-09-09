"""tests/test_concorrencia_leitura.py — leitura do índice sob escrita.

A v1.12 põe um processo LONGO servindo leitura ao lado do cron do tick, que
escreve. A auditoria apontou dois pontos frágeis nesse cenário; MEDINDO antes
de consertar, um deles não existia:

- `busy_timeout` ausente do código NÃO significa espera ausente: o módulo
  `sqlite3` do Python já define 5000 ms por padrão. Medido.
- a falta do pragma de WAL em `_meta_value` NÃO o deixa fora do WAL: o modo é
  propriedade persistente do arquivo, e todo índice existente foi criado pelo
  `connect()`, que o carimba. Medido.

O que sobrou de real é pequeno e vale: `_meta_value` só lê, então abre em
`mode=ro`. Deixa de ser possível escrever por acidente naquele caminho.
"""
import sqlite3

import pytest

from neurata import shelf
from neurata.home import NeurataHome
from neurata.indexdb import connect
from neurata.shelf import _meta_value


@pytest.fixture()
def home(tmp_path):
    h = NeurataHome(tmp_path)
    h.root.mkdir(parents=True, exist_ok=True)
    con = connect(h)
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                ("schema_version", "16"))
    con.commit()
    con.close()
    return h


def test_meta_value_abre_somente_leitura(home, monkeypatch):
    """A propriedade que o conserto compra. Sem isso o teste de baixo passaria
    igual, porque ler não escreve por acaso — o ponto é não PODER escrever."""
    vistos = {}
    real = sqlite3.connect

    def espiao(alvo, *a, **k):
        vistos["alvo"] = alvo
        vistos["uri"] = k.get("uri", False)
        return real(alvo, *a, **k)

    monkeypatch.setattr(shelf.__dict__.setdefault("sqlite3", sqlite3),
                        "connect", espiao, raising=False)
    monkeypatch.setattr(sqlite3, "connect", espiao)
    _meta_value(home, "schema_version")
    assert vistos["uri"] is True
    assert "mode=ro" in vistos["alvo"]


def test_conexao_somente_leitura_recusa_escrita(home):
    """Prova de que `mode=ro` faz o que promete, no mesmo arquivo do teste
    acima. Sem isto, `mode=ro` seria só uma string na chamada."""
    con = sqlite3.connect(f"{home.index_path.as_uri()}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO meta(key, value) VALUES('x', 'y')")
    finally:
        con.close()


def test_meta_value_le_com_escritor_segurando_o_banco(home):
    """O cenário real: o tick escreve enquanto uma busca lê."""
    escritor = sqlite3.connect(home.index_path)
    escritor.execute("BEGIN IMMEDIATE")
    escritor.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                     ("marca", "x"))
    try:
        assert _meta_value(home, "schema_version") == "16"
    finally:
        escritor.rollback()
        escritor.close()


def test_espera_em_disputa_ja_vinha_do_python(home):
    """Documenta a medição que derrubou metade do achado: a ausência de um
    PRAGMA explícito nunca significou falha instantânea."""
    con = connect(home)
    try:
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
    finally:
        con.close()


def test_wal_e_propriedade_do_arquivo_nao_da_conexao(home):
    """A outra metade: conexão nova, sem pragma nenhum, já nasce em WAL."""
    con = sqlite3.connect(home.index_path)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        con.close()


def test_meta_value_devolve_none_sem_indice(tmp_path):
    assert _meta_value(NeurataHome(tmp_path), "schema_version") is None


def test_meta_value_devolve_none_em_arquivo_corrompido(tmp_path):
    h = NeurataHome(tmp_path)
    h.root.mkdir(parents=True, exist_ok=True)
    h.index_path.write_bytes(b"isto nao e um banco sqlite")
    assert _meta_value(h, "schema_version") is None
