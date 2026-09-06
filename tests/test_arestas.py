"""tests/test_arestas.py — v1.9 Arestas vivas, ponta a ponta.

O aceite do roadmap: arestas escritas pelo TICK (não só pelo reindex),
resolução por slug/título/alias com a mesma semântica de ambiguidade do
reindex, e o PPR — peso 0.3 da fusão — mudando a ordem de uma consulta.
"""

from neurata.home import NeurataHome
from neurata.indexdb import connect
from neurata.query import query
from neurata.reindex import reindex
from neurata.tick import curate_tick


def _home(tmp_path) -> NeurataHome:
    home = NeurataHome(tmp_path)
    home.init()
    return home


def _grao(home, slug, titulo, corpo, aliases=None):
    extra = f"aliases: [{', '.join(aliases)}]\n" if aliases else ""
    (home.inbox / f"{slug}.md").write_text(
        f"---\nid: {slug.upper()}\ntitle: {titulo}\n{extra}---\n{corpo}\n",
        encoding="utf-8")


def _edges(home):
    con = connect(home)
    try:
        return {(a, b) for a, b in con.execute(
            "SELECT src_id, dst_id FROM edges")}
    finally:
        con.close()


def test_tick_escreve_aresta_por_slug(tmp_path):
    home = _home(tmp_path)
    _grao(home, "alvo", "Alvo", "Conteudo do alvo.")
    curate_tick(home)
    _grao(home, "origem", "Origem", "Veja [[alvo]] antes de agir.")
    rep = curate_tick(home)
    assert rep.edges >= 1
    assert ("ORIGEM", "ALVO") in _edges(home)


def test_tick_escreve_aresta_por_titulo(tmp_path):
    home = _home(tmp_path)
    _grao(home, "alvo", "Guia de Deploy", "Conteudo.")
    curate_tick(home)
    _grao(home, "origem", "Origem", "Leia [[Guia de Deploy]] primeiro.")
    curate_tick(home)
    assert ("ORIGEM", "ALVO") in _edges(home)


def test_tick_escreve_aresta_por_alias(tmp_path):
    home = _home(tmp_path)
    _grao(home, "alvo", "Alvo", "Conteudo.", aliases=["apelido-alvo"])
    curate_tick(home)
    _grao(home, "origem", "Origem", "Veja [[apelido-alvo]] depois.")
    curate_tick(home)
    assert ("ORIGEM", "ALVO") in _edges(home)


def test_ambiguidade_de_titulo_nao_cria_aresta(tmp_path):
    """Mesma régua do `_AMBIG` do reindex: 2+ candidatos = sem escolha
    arbitrária, nem aqui nem lá."""
    home = _home(tmp_path)
    _grao(home, "a1", "Deploy", "Versao um.")
    _grao(home, "a2", "Deploy", "Versao dois.")
    curate_tick(home)
    _grao(home, "origem", "Origem", "Leia [[Deploy]] antes.")
    curate_tick(home)
    assert _edges(home) == set()


def test_ambiguidade_de_alias_nao_cria_aresta(tmp_path):
    home = _home(tmp_path)
    _grao(home, "a1", "Um", "x.", aliases=["gemeo"])
    _grao(home, "a2", "Dois", "y.", aliases=["gemeo"])
    curate_tick(home)
    _grao(home, "origem", "Origem", "Veja [[gemeo]].")
    curate_tick(home)
    assert _edges(home) == set()


def test_autolink_nao_cria_aresta(tmp_path):
    home = _home(tmp_path)
    _grao(home, "origem", "Origem", "Eu mesmo: [[origem]] e [[Origem]].")
    curate_tick(home)
    assert _edges(home) == set()


def test_display_e_secao_sao_cortados(tmp_path):
    home = _home(tmp_path)
    _grao(home, "alvo", "Alvo", "Conteudo.")
    curate_tick(home)
    _grao(home, "origem", "Origem",
          "Veja [[alvo|o guia]] e [[alvo#secao]].")
    curate_tick(home)
    arestas = _edges(home)
    assert ("ORIGEM", "ALVO") in arestas
    assert len(arestas) == 1  # duplicado do mesmo alvo vira UMA aresta


def test_update_in_place_acompanha_o_corpo(tmp_path):
    """Mudança legítima de corpo de grão catalogado = edição na library
    que o tick absorve (v1.2): a aresta acompanha o corpo — sai com o
    link, volta com o link."""
    home = _home(tmp_path)
    _grao(home, "alvo", "Alvo", "Conteudo.")
    _grao(home, "origem", "Origem", "Veja [[alvo]] hoje.")
    curate_tick(home)
    assert ("ORIGEM", "ALVO") in _edges(home)

    import neurata.frontmatter as fm
    lib = home.library / "origem.md"
    m, _b = fm.parse(lib.read_text(encoding="utf-8"))
    lib.write_text(fm.serialize(m, "Sem links agora."), encoding="utf-8")
    curate_tick(home)
    assert ("ORIGEM", "ALVO") not in _edges(home)

    m, _b = fm.parse(lib.read_text(encoding="utf-8"))
    lib.write_text(fm.serialize(m, "[[alvo]] de volta."), encoding="utf-8")
    curate_tick(home)
    assert ("ORIGEM", "ALVO") in _edges(home)


def test_purge_limpa_as_duas_direcoes(tmp_path):
    home = _home(tmp_path)
    _grao(home, "alvo", "Alvo", "Conteudo.")
    _grao(home, "origem", "Origem", "Veja [[alvo]].")
    curate_tick(home)
    assert ("ORIGEM", "ALVO") in _edges(home)
    # apaga o alvo na mão: entrada morta é purgada, aresta vai junto
    (home.library / "alvo.md").unlink()
    curate_tick(home)
    assert _edges(home) == set()


def test_consistencia_tick_reindex_mesmas_arestas(tmp_path):
    home = _home(tmp_path)
    _grao(home, "alvo", "Alvo", "Conteudo.", aliases=["apelido"])
    _grao(home, "b", "B", "Veja [[alvo]] e [[apelido]] e [[Alvo]].")
    curate_tick(home)
    via_tick = _edges(home)
    reindex(home)
    via_reindex = _edges(home)
    assert via_tick == via_reindex == {("B", "ALVO")}
    # reindex backfila entry_aliases (a forja do tick já tinha posto)


def test_reindex_backfila_entry_aliases(tmp_path):
    """A migração v15 cria a tabela VAZIA (não lê disco); o reindex
    backfila do frontmatter — o mesmo pacto do `assertions` da v13."""
    home = _home(tmp_path)
    _grao(home, "alvo", "Alvo", "Conteudo.", aliases=["apelido", "outro"])
    curate_tick(home)  # writer do tick já preenche
    con = connect(home)
    con.execute("DELETE FROM entry_aliases")  # forja índice v15 recém-migrado
    con.commit()
    con.close()
    reindex(home)
    con = connect(home)
    try:
        aliases = {a for (a,) in con.execute(
            "SELECT alias FROM entry_aliases WHERE entry_id='ALVO'")}
        (n_edges,) = con.execute("SELECT COUNT(*) FROM edges").fetchone()
    finally:
        con.close()
    assert aliases == {"apelido", "outro"}
    assert n_edges == 0  # sem wikilink, sem aresta


def test_ppr_muda_a_ordem_da_consulta(tmp_path):
    """O aceite do roadmap: com arestas, a perna PPR (peso 0.3) muda o
    vencedor de uma consulta real. B vence no léxico; A é vizinho de
    grafo do seed C e vence COM o grafo."""
    home = _home(tmp_path)
    _grao(home, "a", "Postgres Alfa", "postgres")
    _grao(home, "b", "Postgres Beta", "postgres postgres configuracao.")
    _grao(home, "c", "Posters Gama", "postgres [[Postgres Alfa]] [[b]]")
    curate_tick(home)
    com_grafo = query(home, "postgres")["results"]
    ordem_com = [c["id"] for c in com_grafo]
    assert {"A", "B", "C"} <= set(ordem_com)
    # remove as arestas: o ranking volta ao léxico puro
    con = connect(home)
    con.execute("DELETE FROM edges")
    con.commit()
    con.close()
    sem_grafo = query(home, "postgres")["results"]
    ordem_sem = [c["id"] for c in sem_grafo]
    assert ordem_com[0] != ordem_sem[0] or ordem_com != ordem_sem
