"""tests/test_query_context.py — viés determinístico por contexto (v1.6).

Cada pista (projeto, sessão, recência) tem que mudar o ranking no
sentido declarado, a faceta explícita tem que desligar o viés
correspondente, e config zerada tem que devolver o ranking idêntico ao
de hoje. Determinístico: relógio real só entra via datas com anos de
distância nos fixtures — asserções são de ORDEM, não de valor exato.

Os testes de mecanismo usam config injetada (boost alto) com o grão
favorecido lexicalmente PIOR: prova que o viés muda a cabeça sem
apostar na magnitude do default. Os corpos NUNCA são iguais — dup exato
vira quarantine no tick e mataria o acervo de teste.
"""
import pytest

from neurata.context import QueryContext
from neurata.home import CONTRACT_VERSION, NeurataHome
from neurata.query import query
from neurata.reindex import reindex
from neurata.router import parse as parse_q
from neurata.tick import curate_tick


def _home(tmp_path) -> NeurataHome:
    home = NeurataHome(tmp_path)
    home.init()
    return home


def _grao(home, projeto, titulo, corpo, session=None, updated=None):
    """Grão curado com `project` derivável do frontmatter — o caminho
    real do depósito (git root) forjado no arquivo, que é a verdade."""
    extra = f"source:\n  git_root: /repos/{projeto}\n"
    if session:
        extra += f"  session: {session}\n"
    if updated:
        extra += f"updated: {updated}\n"
    (home.inbox / f"{titulo}.md").write_text(
        f"---\nid: {titulo.upper()}\ntitle: {titulo}\n{extra}---\n{corpo}\n",
        encoding="utf-8")


def _config_boost(home, **vals):
    import json
    base = {"project_boost": 1.25, "session_boost": 1.5,
            "recency_weight": 0.1, "recency_tau_dias": 30.0}
    base.update(vals)
    home.config_path.write_text(
        json.dumps({"context": base}), encoding="utf-8")


def _order(home, qstr, context=None, limit=10):
    out = query(home, qstr, limit=limit, context=context)
    return [c["id"] for c in out["results"]], out


CTX_A = QueryContext(project="ProjA", session=None, project_source="env")
CTX_B = QueryContext(project="ProjB", session=None, project_source="env")
SEM_NADA = QueryContext(project=None, session=None, project_source="none")


# ── unit: a matemática do _apply_context ─────────────────────────────

def _row(rowid, projeto=None, session=None,
         updated="2020-01-01T00:00:00+00:00"):
    """Mesma forma de `_fetch_entries` (11 colunas)."""
    return (rowid, f"ID{rowid}", f"slug{rowid}", "t", "", "note",
            f"library/s{rowid}.md", None, projeto, session, updated)


CFG = {"project_boost": 1.25, "session_boost": 1.5,
       "recency_weight": 0.1, "recency_tau_dias": 30.0}


def test_unit_boost_projeto_multiplica():
    from neurata.query import _apply_context
    final = {1: 0.016, 2: 0.016}
    rows = {1: _row(1, projeto="ProjA"), 2: _row(2, projeto="ProjB")}
    _apply_context(final, rows, CFG, CTX_A, parse_q("termo"))
    assert final[1] == pytest.approx(0.016 * 1.25)
    assert final[2] == pytest.approx(0.016)


def test_unit_boost_sessao_multiplica():
    from neurata.query import _apply_context
    final = {1: 0.016}
    rows = {1: _row(1, session="s-1")}
    ctx = QueryContext(project=None, session="s-1", project_source="none")
    _apply_context(final, rows, CFG, ctx, parse_q("termo"))
    assert final[1] == pytest.approx(0.016 * 1.5)


def test_unit_recencia_soma_por_ultimo_e_decresce():
    from neurata.query import _apply_context
    final = {1: 0.016, 2: 0.016}
    rows = {1: _row(1, updated="2099-01-01T00:00:00+00:00"),
            2: _row(2)}
    ctx = QueryContext(project=None, session=None, project_source="none")
    _apply_context(final, rows, CFG, ctx, parse_q("termo"))
    # 2099 é futuro: Δ clampado a 0 → +w*exp(0) = +0.1; seis anos:
    # +w*exp(-Δ/30) ≈ 0
    assert final[1] == pytest.approx(0.116, abs=1e-3)
    assert final[2] < 0.017


def test_unit_soberania_da_faceta():
    from neurata.query import _apply_context
    final = {1: 0.016}
    rows = {1: _row(1, projeto="ProjA")}
    _apply_context(final, rows, CFG, CTX_A, parse_q("termo project:ProjA"))
    assert final[1] == pytest.approx(0.016)  # faceta soberana: sem boost


def test_unit_config_zerada_e_identidade():
    from neurata.query import _apply_context
    zerada = {"project_boost": 0, "session_boost": 0,
              "recency_weight": 0, "recency_tau_dias": 30.0}
    final = {1: 0.016}
    rows = {1: _row(1, projeto="ProjA", session="s-1",
                    updated="2026-09-06T00:00:00+00:00")}
    antes = dict(final)
    _apply_context(final, rows, zerada, CTX_A, parse_q("termo"))
    assert final == antes


def test_unit_updated_ilegivel_nao_ganha_recencia():
    from neurata.query import _apply_context
    final = {1: 0.016}
    rows = {1: _row(1, updated="data-torta")}
    ctx = QueryContext(project=None, session=None, project_source="none")
    _apply_context(final, rows, CFG, ctx, parse_q("termo"))
    assert final[1] == pytest.approx(0.016)


def test_unit_recencia_age_sem_contexto_de_projeto():
    """Recência não depende de projeto/sessão: contexto com campos None
    e recência ligada AINDA aplica a pista."""
    from neurata.query import _apply_context
    final = {1: 0.016}
    rows = {1: _row(1, updated="2099-01-01T00:00:00+00:00")}
    ctx = QueryContext(project=None, session=None, project_source="none")
    _apply_context(final, rows, CFG, ctx, parse_q("termo"))
    assert final[1] > 0.016


def test_unit_contexto_none_e_noop():
    from neurata.query import _apply_context
    final = {1: 0.016}
    rows = {1: _row(1, projeto="ProjA")}
    _apply_context(final, rows, CFG, None, parse_q("termo"))
    assert final[1] == pytest.approx(0.016)


# ── integração: o ranking muda no sentido declarado ──────────────────

def _acervo_dois_projetos(home):
    """GRAOA lexicalmente PIOR (corpo longo perde BM25 pro curto): o
    boost é o que precisa virar a cabeça, não um empate."""
    _grao(home, "ProjA", "graoa",
          "Deploy com postgres precisa de WAL ligado no servidor de "
          "producao com replicacao.")
    _grao(home, "ProjB", "graob", "Deploy com postgres.")
    reindex(home)


def test_projeto_corrente_muda_a_cabeca(tmp_path):
    home = _home(tmp_path)
    _acervo_dois_projetos(home)
    _config_boost(home, project_boost=3.0)
    ordem_a, _ = _order(home, "deploy postgres", context=CTX_A)
    ordem_b, _ = _order(home, "deploy postgres", context=CTX_B)
    assert ordem_a[0] == "GRAOA"  # contexto A promove o pior lexical
    assert ordem_b[0] == "GRAOB"  # contexto B confirma o melhor lexical


def test_sem_contexto_ordem_lexica_estavel(tmp_path):
    home = _home(tmp_path)
    _acervo_dois_projetos(home)
    _config_boost(home, project_boost=3.0)  # boost alto e SEM contexto
    o1, _ = _order(home, "deploy postgres", context=SEM_NADA)
    o2, _ = _order(home, "deploy postgres", context=SEM_NADA)
    assert o1 == o2 == ["GRAOB", "GRAOA"]  # lexical puro: melhor primeiro


def test_afinidade_de_sessao(tmp_path):
    home = _home(tmp_path)
    _grao(home, "ProjA", "sessatual",
          "Cache com sqlite local funciona melhor no geral do setup.",
          session="s-atual")
    _grao(home, "ProjA", "sessvelha", "Cache com sqlite.")
    reindex(home)
    _config_boost(home, session_boost=3.0)
    ctx = QueryContext(project=None, session="s-atual",
                       project_source="none")
    ordem, _ = _order(home, "cache sqlite", context=ctx)
    assert ordem[0] == "SESSATUAL"


def test_recencia_traz_recente_para_o_topo(tmp_path):
    home = _home(tmp_path)
    _grao(home, "ProjA", "recente",
          "Migracao do banco exige dump consistente verificado pelo time.")
    _grao(home, "ProjA", "antigo",
          "Migracao do banco exige dump consistente.",
          updated="2020-01-01T00:00:00+00:00")
    reindex(home)
    _config_boost(home, recency_weight=1.0)
    ctx = QueryContext(project=None, session=None, project_source="none")
    ordem, _ = _order(home, "migracao banco dump", context=ctx, limit=2)
    assert ordem[0] == "RECENTE"


def test_faceta_soberana_na_integracao(tmp_path):
    home = _home(tmp_path)
    _acervo_dois_projetos(home)
    _config_boost(home, project_boost=3.0)
    com_faceta, _ = _order(home, "deploy postgres project:ProjB",
                           context=CTX_A)
    # filtro explícito em ProjB: contexto A não fura a faceta
    assert com_faceta == ["GRAOB"]


def test_superseded_nao_ressuscita_com_contexto(tmp_path):
    home = _home(tmp_path)
    _grao(home, "ProjA", "vencedor", "Use fila com idempotencia.")
    _grao(home, "ProjA", "perdedor", "Use fila sem garantias de retorno.")
    curate_tick(home)
    from neurata.supersede import supersede
    supersede(home, "PERDEDOR", "VENCEDOR")
    _config_boost(home, project_boost=3.0)
    ctx = QueryContext(project="ProjA", session=None, project_source="env")
    out = query(home, "fila", context=ctx)
    ids = [c["id"] for c in out["results"]]
    assert ids.index("VENCEDOR") < ids.index("PERDEDOR")
    sup = next(c for c in out["results"] if c["id"] == "PERDEDOR")
    assert sup["superseded_by"] == "VENCEDOR"


def test_envelope_contexto_e_card_intacto(tmp_path):
    home = _home(tmp_path)
    _acervo_dois_projetos(home)
    _, out = _order(home, "deploy postgres", context=CTX_A)
    assert out["context"] == {"project": "ProjA", "session": None,
                              "source": "env"}
    card = out["results"][0]
    assert set(card.keys()) == {"id", "slug", "title", "description",
                                "type", "path", "superseded_by", "score",
                                "snippet", "via", "contradicts"}
    assert CONTRACT_VERSION == 6


def test_contexto_capturado_do_environ(tmp_path, monkeypatch):
    home = _home(tmp_path)
    _acervo_dois_projetos(home)
    monkeypatch.setenv("NEURATA_PROJECT", "ProjB")
    out = query(home, "deploy postgres")
    assert out["context"]["project"] == "ProjB"
    assert out["context"]["source"] == "env"
    assert out["results"][0]["id"] == "GRAOB"  # melhor lexical E boostado


def test_facet_listing_nao_muda_com_contexto(tmp_path):
    home = _home(tmp_path)
    _acervo_dois_projetos(home)
    _config_boost(home, project_boost=3.0)
    sem_ctx = query(home, "project:ProjA")["results"]
    com_ctx = query(home, "project:ProjA", context=CTX_A)["results"]
    assert [c["id"] for c in sem_ctx] == [c["id"] for c in com_ctx] == [
        "GRAOA"]


def test_config_zerada_nao_captura_contexto(tmp_path, monkeypatch):
    """Captura é lazy: com o viés desligado na config, a query não paga
    o subprocess de git — e o envelope declara `disabled`, que é
    diferente de `none` (olhado e vazio)."""
    home = _home(tmp_path)
    _grao(home, "ProjA", "graoa", "Deploy com postgres.")
    reindex(home)
    home.config_path.write_text(
        '{"context": {"project_boost": 0, "session_boost": 0,'
        ' "recency_weight": 0, "recency_tau_dias": 30.0}}',
        encoding="utf-8")

    def explode():
        raise AssertionError("captura não deveria rodar com viés zerado")

    monkeypatch.setattr("neurata.query.capture_query_context", explode)
    out = query(home, "deploy")
    assert out["context"] == {"project": None, "session": None,
                              "source": "disabled"}
    assert out["results"][0]["id"] == "GRAOA"


def test_acervo_sem_dados_de_contexto_nao_captura(tmp_path, monkeypatch):
    """Sem nenhum grão com project/session indexados, o boost não teria
    com o que casar: capturar (subprocess de git) é custo puro. A query
    segue com recência (que não depende de captura) e o envelope declara
    `none` — olhado, e não há contexto USÁVEL."""
    home = _home(tmp_path)
    nl = chr(10)
    (home.inbox / "a.md").write_text(
        f"---{nl}id: A1{nl}title: a{nl}---{nl}Deploy com postgres.{nl}",
        encoding="utf-8")
    reindex(home)

    def explode():
        raise AssertionError("captura não deveria rodar sem dados de contexto")

    monkeypatch.setattr("neurata.query.capture_query_context", explode)
    out = query(home, "deploy")
    assert out["context"] == {"project": None, "session": None,
                              "source": "none"}
    assert out["results"][0]["id"] == "A1"


def test_recencia_viva_em_acervo_sem_projetos(tmp_path):
    home = _home(tmp_path)
    nl = chr(10)
    (home.inbox / "a.md").write_text(
        f"---{nl}id: ANTIGO{nl}title: a{nl}"
        f"updated: 2020-01-01T00:00:00+00:00{nl}---{nl}"
        f"Migracao exige dump.{nl}",
        encoding="utf-8")
    (home.inbox / "b.md").write_text(
        f"---{nl}id: NOVO{nl}title: b{nl}---{nl}Migracao exige dump fiel.{nl}",
        encoding="utf-8")
    reindex(home)
    _config_boost(home, recency_weight=1.0)
    ctx = QueryContext(project=None, session=None, project_source="none")
    ordem, _ = _order(home, "migracao dump", context=ctx, limit=2)
    assert ordem[0] == "NOVO"
