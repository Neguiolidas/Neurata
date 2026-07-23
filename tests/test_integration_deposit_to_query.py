"""tests/test_integration_deposit_to_query.py — T8: integração
depósito→consultável em ≤ 1 tick.

`deposit` cria item novo no inbox; um único `curate_tick` com budget
padrão (sem limite) cataloga tudo que está pendente; `query` lexical
encontra o termo depositado. Determinístico — sem sleep, sem retry.
"""
from neurata.deposit import deposit
from neurata.home import NeurataHome
from neurata.query import query
from neurata.reindex import reindex
from neurata.tick import curate_tick


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    reindex(home)  # bootstrap: marca index_schema_version, exigido por query()
    return home


def test_deposit_searchable_after_single_tick(tmp_path):
    home = _home(tmp_path)

    result = deposit(
        home, content="Título Xenolítico\n\nConteúdo sobre criptofauna "
        "abissal e tokenização espectral.")
    assert result["action"] == "created"

    report = curate_tick(home)
    assert report.errors == []
    assert report.processed == 1

    hits = query(home, "criptofauna")["results"]
    assert any(h["id"] == result["id"] for h in hits)
