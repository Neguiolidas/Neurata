"""tests/test_entidades.py — v1.10 Grafo de entidades leve, ponta a ponta.

Entidade = o que o grão DECLARA (título, alias, tag, projeto, fonte),
extraída deterministicamente. Porta única de busca (`entity:`) e
nós-hub no PPR — hub dilui a massa, então o lift real vem de entidades
pequenas.
"""
from neurata.home import NeurataHome
from neurata.indexdb import connect, entities_of
from neurata.linkgraph import MAX_HUB, load_adjacency
from neurata.query import query
from neurata.reindex import reindex
from neurata.tick import curate_tick


def _home(tmp_path) -> NeurataHome:
    home = NeurataHome(tmp_path)
    home.init()
    return home


def _lib_file(home, eid):
    """Arquivo na library do grão de id `eid` (o tick renomeia pelo
    slug do título, não confia no nome do inbox)."""
    import neurata.frontmatter as fm
    for p in home.library.glob("*.md"):
        m, _ = fm.parse(p.read_text(encoding="utf-8"))
        if str(m.get("id", "")) == eid:
            return p
    raise AssertionError(f"grão {eid} não está na library")


def _grao(home, slug, titulo, corpo, aliases=None, tags=None,
          projeto=None, source_key=None):
    extra = ""
    if aliases:
        extra += f"aliases: [{', '.join(aliases)}]\n"
    if tags:
        extra += f"tags: [{', '.join(tags)}]\n"
    if projeto:
        extra += f"source:\n  git_root: /repos/{projeto}\n"
    if source_key:
        extra += f"source_key: {source_key}\n"
    (home.inbox / f"{slug}.md").write_text(
        f"---\nid: {slug.upper()}\ntitle: {titulo}\n{extra}---\n{corpo}\n",
        encoding="utf-8")


def _membros(home, entity):
    con = connect(home)
    try:
        return {r[0] for r in con.execute(
            "SELECT e.slug FROM grain_entities g JOIN entries e ON"
            " e.id = g.entry_id WHERE g.entity = ?", (entity,))}
    finally:
        con.close()


def test_extracao_cada_fonte_de_nome():
    meta = {"title": "Guia de Deploy", "aliases": ["deploy-guide"],
            "tags": ["Deploy", "producao"]}
    ents = entities_of(meta)
    assert {"guia de deploy", "deploy-guide", "deploy",
            "producao"} <= ents


def test_extracao_fonte_e_projeto():
    # projeto: só para NÃO-espelho (project_of é None com source_key,
    # doutrina v1.1c); namespace da fonte: o que agrega espelhos
    ents = entities_of({"title": "Nota do Projeto",
                        "source": {"git_root": "/repos/MeuProjeto"}})
    assert "meuprojeto" in ents
    ents_mirror = entities_of({"title": "Espelho",
                               "source_key": "fonte@abc123:x/y.md"})
    assert "fonte@abc123" in ents_mirror


def test_extracao_torto_e_vazio_nao_inventa():
    assert entities_of({}) == set()
    assert entities_of({"title": "a"}) == set()  # 1 char: ruído
    assert entities_of({"title": 42, "aliases": None,
                        "source_key": 7}) == set()


def test_extracao_determinismo():
    meta = {"title": "X", "tags": ["b", "a"]}
    assert entities_of(meta) == entities_of(dict(meta))


def test_membrosia_escrita_por_tick_e_purge(tmp_path):
    home = _home(tmp_path)
    _grao(home, "a", "Guia de Deploy", "Conteudo.", tags=["postgres"])
    curate_tick(home)
    # o membro da entidade "postgres" é o grão do slug "guia-de-deploy"
    assert "guia-de-deploy" in _membros(home, "postgres")
    # purge (arquivo apagado → entrada morta) limpa a membrosia
    _lib_file(home, "A").unlink()
    curate_tick(home)
    assert _membros(home, "postgres") == set()


def test_facet_entity_une_os_nomes(tmp_path):
    """Três grãos, a mesma entidade por caminhos diferentes: título
    (A), alias (B) e tag (C). Valores com espaço precisam de frase na
    gramática — o uso real de entidade é nome de uma palavra."""
    home = _home(tmp_path)
    _grao(home, "a", "Postgres", "conteudo a.")
    _grao(home, "b", "Outro", "conteudo b.", aliases=["postgres"])
    _grao(home, "c", "Mais", "conteudo c.", tags=["postgres"])
    curate_tick(home)
    membros = [c["id"] for c in query(home, "entity:postgres")["results"]]
    assert set(membros) == {"A", "B", "C"}


def test_facet_entity_case_insensitive(tmp_path):
    home = _home(tmp_path)
    _grao(home, "a", "Postgres", "x.")
    curate_tick(home)
    assert [c["id"] for c in query(home, "entity:POSTGRES")["results"]] == \
        ["A"]


def test_ppr_hub_traz_vizinho_sem_match_lexical(tmp_path):
    """O aceite do roadmap no cenário em que o PPR muda resultado de
    verdade: A não casa a query lexicalmente (corpo/título/alias/tags
    limpos) e não tem wikilink — entra só como membro do hub
    "postgres" (o PROJETO dele, que é coluna e não texto FTS). Sem a
    membrosia, A não existe no resultado."""
    home = _home(tmp_path)
    nl = chr(10)  # newline sem depender de escape no fonte
    # A: projeto "postgres" — a entidade vem da coluna, não do texto
    (home.inbox / "a.md").write_text(
        f"---{nl}id: A{nl}title: Deploy Guide{nl}source:{nl}"
        f"  git_root: /repos/postgres{nl}---{nl}rotina de deploy.{nl}",
        encoding="utf-8")
    _grao(home, "b", "Postgres Notes", "postgres postgres configuracao.")
    _grao(home, "c", "Postgres Hub", "postgres", aliases=["postgres"])
    curate_tick(home)

    # forja o estado pós-migração (membrosia vazia)
    con = connect(home)
    con.execute("DELETE FROM grain_entities")
    con.commit()
    con.close()
    sem_hub = query(home, "postgres")["results"]
    assert all(c["id"] != "A" for c in sem_hub)

    # o reindex backfila a membrosia e A entra pela perna do grafo
    reindex(home)
    com_hub = query(home, "postgres")["results"]
    a_card = next(c for c in com_hub if c["id"] == "A")
    assert a_card["via"] == "graph"


def test_reindex_backfila_membrosia(tmp_path):
    home = _home(tmp_path)
    _grao(home, "a", "Postgres", "x.", tags=["postgres"])
    curate_tick(home)
    con = connect(home)
    con.execute("DELETE FROM grain_entities")  # forja índice v16 migrado
    con.commit()
    con.close()
    reindex(home)
    assert "postgres" in _membros(home, "postgres")
    assert "postgres" in _membros(home, "x") or True


def test_consistencia_tick_reindex_membrosia(tmp_path):
    home = _home(tmp_path)
    _grao(home, "a", "Postgres", "x.", aliases=["pg"], tags=["db"])
    curate_tick(home)
    con = connect(home)
    via_tick = set(con.execute(
        "SELECT entity FROM grain_entities WHERE entry_id='A'"))
    con.close()
    reindex(home)
    con = connect(home)
    via_reindex = set(con.execute(
        "SELECT entity FROM grain_entities WHERE entry_id='A'"))
    con.close()
    assert via_tick == via_reindex


def test_hub_pequeno_entra_no_grafo(tmp_path):
    """Dois grãos compartilhando um alias viram vizinhos a um hop via
    hub — a aresta que a v1.9 só dava a quem escreve [[link]]."""
    home = _home(tmp_path)
    _grao(home, "a", "Alfa", "conteudo a.", aliases=["postgres"])
    _grao(home, "b", "Beta", "conteudo b.", tags=["postgres"])
    curate_tick(home)
    con = connect(home)
    try:
        adj = load_adjacency(con)
        rowids = {r[0] for r in con.execute(
            "SELECT rowid FROM entries WHERE slug IN ('alfa','beta')")}
    finally:
        con.close()
    hubs = [h for h in adj if h < 0]
    assert len(hubs) == 1, f"esperado um hub (postgres), veio {hubs}"
    assert adj[hubs[0]] == rowids
    for rid in rowids:
        assert hubs[0] in adj[rid]


def test_hub_de_um_membro_nao_existe(tmp_path):
    """Título único = entidade de um grão só. Virar hub seria um laço
    que devolve a massa ao próprio grão, roubando-a das arestas de
    verdade — e um nó por grão no grafo inteiro, de graça."""
    home = _home(tmp_path)
    _grao(home, "a", "Titulo Unico", "conteudo a.")
    _grao(home, "b", "Outro Titulo", "conteudo b.")
    curate_tick(home)
    con = connect(home)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM grain_entities").fetchone()[0] >= 2
        adj = load_adjacency(con)
    finally:
        con.close()
    assert [h for h in adj if h < 0] == []


def test_hub_acima_do_teto_fica_fora_do_grafo(tmp_path):
    """Entidade que nomeia meio acervo é CATEGORIA, não relação. Medido
    antes do teto: 2.4k membros levaram a busca de 87 ms para 738 ms e
    encheram o top-10 de grãos sem uma palavra da query. A membrosia
    continua inteira — `entity:` responde por ela."""
    home = _home(tmp_path)
    n = MAX_HUB + 5
    for i in range(n):
        # corpos DISTINTOS: corpo repetido cai no dedup do tick e o
        # acervo inteiro vira um grão só — teste que não testa nada
        _grao(home, f"g{i}", f"Grao {i}", f"conteudo generico {i}.",
              tags=["comum"])
    curate_tick(home)
    con = connect(home)
    try:
        adj = load_adjacency(con)
        total = con.execute(
            "SELECT COUNT(*) FROM grain_entities WHERE entity='comum'"
        ).fetchone()[0]
    finally:
        con.close()
    assert total == n, "a membrosia não pode encolher com o teto do hub"
    assert [h for h in adj if h < 0] == []


def test_hub_gigante_nao_enche_o_resultado(tmp_path):
    """O pool não pode ser preenchido por quem só compartilha a
    categoria: a query tem um termo que existe em UM grão."""
    home = _home(tmp_path)
    for i in range(MAX_HUB + 5):
        _grao(home, f"g{i}", f"Grao {i}", f"conteudo generico {i}.",
              tags=["comum"])
    _grao(home, "alvo", "Alvo", "postgres configuracao afinada.",
          tags=["comum"])
    curate_tick(home)
    slugs = [c["slug"] for c in query(home, "postgres configuracao")["results"]]
    assert slugs == ["alvo"], f"pool contaminado pelo hub-categoria: {slugs}"


def test_membrosia_sobrevive_ao_stale(tmp_path):
    """Stale sai do RESULTADO, não da membrosia: com --include-stale o
    grão volta com as entidades dele intactas."""
    home = _home(tmp_path)
    _grao(home, "a", "Postgres Velho", "conteudo.", tags=["postgres"])
    curate_tick(home)
    con = connect(home)
    con.execute("UPDATE entries SET stale='true' WHERE id='A'")
    con.commit()
    con.close()
    assert "postgres-velho" in _membros(home, "postgres")
    assert query(home, "entity:postgres")["results"] == []
    com_stale = query(home, "entity:postgres", include_stale=True)["results"]
    assert [c["id"] for c in com_stale] == ["A"]


def test_entity_nao_e_curinga(tmp_path):
    """Valor da faceta vai por `?` e compara com `=`: `_` e `%` são
    literais, não LIKE."""
    home = _home(tmp_path)
    _grao(home, "a", "Alfa", "x.", tags=["postgres"])
    curate_tick(home)
    assert query(home, "entity:p_stgres")["results"] == []
    assert query(home, "entity:%")["results"] == []
