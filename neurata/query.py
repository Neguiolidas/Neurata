"""neurata/query.py — orquestrador do pipeline de busca.

parse facets → prefiltro (subquery rowid) → fan-out ≤6 MATCH → RRF
→ união com vizinhos 1-hop → PPR aditivo → boost skill → viés de
contexto → cards.

Invariante de SQL (justifica os `# nosec B608` espalhados aqui): todo
valor vindo do usuário viaja em parâmetro `?`. O que é interpolado em
f-string se limita a três coisas geradas por nós — `marks`, que é
literalmente `",".join("?" * n)`; `pre_sql`, montado em `_prefilter`
só com cláusulas literais; e constantes de módulo (`_SNIP_*`, int).
Ao mexer nestas queries, manter a invariante ou remover o `nosec`.
"""
import math
import sqlite3

from neurata import (
    config,
    indexdb,
    linkgraph,
    reindex,
    router,
    rrf,
    shelf,
    usage,
)
from neurata.context import QueryContext, capture_query_context, days_since
from neurata.home import NeurataHome
from neurata.indexdb import connect

_TOPN = 50    # candidatos por variante
_LANE_TOPN = 20  # candidatos por variante na pista curada (corpus pequeno)
_SEEDS = 10   # seeds do PPR (top do RRF)
_SNIP_RAW = 3   # índice da coluna body no FTS
_SNIP_NORM = 7  # índice da coluna body_norm


class QueryError(ValueError):
    pass


def query(home: NeurataHome, qstr: str, limit: int = 10,
          context: "QueryContext | None" = None,
          include_stale: bool = False) -> dict:
    cfg = config.load(home)
    parsed = router.parse(qstr)
    # antes do check de vazia: `missing:xpto` sozinho tem has_facets e
    # cairia em "query vazia", escondendo o erro que o usuário precisa ver.
    _validate_missing(parsed)
    _validate_class(parsed)
    _validate_status(parsed)
    if not parsed.has_text and not parsed.has_facets:
        raise QueryError(
            "query vazia — passe texto e/ou facets (type:/tag:/env:/"
            "project:/regime:/class:/agent:/session:/origin:/missing:)")
    _ensure_searchable(home)
    con = connect(home)
    try:
        _migrate(con, home)
        _check_schema(con)
        ctx = context
        if ctx is None:
            ctx = _capture_if_worth(con, cfg)
        pre_sql, pre_params = _prefilter(parsed)
        if not parsed.has_text:
            assert pre_sql is not None  # has_facets garante clauses não-vazias
            results = _facet_listing(con, pre_sql, pre_params, limit,
                                     include_stale)
        else:
            results = _search(con, cfg, parsed, pre_sql, pre_params,
                              limit, home, ctx, include_stale)
    finally:
        con.close()
    for rank, card in enumerate(results, start=1):
        usage.log_event(home, "query", card["id"], query=qstr, rank=rank)
    return {"results": results,
            "context": {"project": ctx.project, "session": ctx.session,
                        "source": ctx.project_source}}


def _capture_if_worth(con: sqlite3.Connection,
                      cfg: dict) -> QueryContext:
    """Captura de contexto LAZY, em duas portas (v1.6):

    1. viés zerado na config → nada a aplicar, `source: "disabled"` —
       quem desligou a pista não paga subprocess nenhum;
    2. acervo SEM nenhum grão com `project`/`session` indexados → o
       boost não teria com o que casar: capturar (subprocess de git)
       seria custo puro. `source: "none"` — não há contexto USÁVEL.

    A consulta de existência é barata e roda dentro da conexão já
    aberta; o subprocess de git só acontece quando há sinal no acervo
    E pistas ligadas na config."""
    pesos = cfg["context"]
    if not (pesos["project_boost"] or pesos["session_boost"]
            or pesos["recency_weight"]):
        return QueryContext(project=None, session=None,
                            project_source="disabled")
    tem_contexto = con.execute(
        "SELECT EXISTS(SELECT 1 FROM entries WHERE project IS NOT NULL)"
        " OR EXISTS(SELECT 1 FROM entries WHERE session IS NOT NULL)"
    ).fetchone()[0]
    if not tem_contexto:
        return QueryContext(project=None, session=None,
                            project_source="none")
    return capture_query_context()


def _has_indexable_content(home: NeurataHome) -> bool:
    """Há `.md` nos mesmos diretórios que o `reindex` varre?

    Deliberadamente espelha o loop de `reindex._reindex_locked`. As duas
    varreduras precisam concordar: se esta disser "nada" e a do reindex
    achar algo, `query` devolve vazio tendo conteúdo — a mentira que o
    guard existia pra impedir.
    """
    return any(next(base.rglob("*.md"), None) is not None
               for base in (home.library, home.inbox))


def _ensure_searchable(home: NeurataHome) -> None:
    """Cura o índice nunca reindexado, em vez de mandar o usuário rodar
    `neurata reindex`.

    O guard antigo recusava buscar sem carimbo de versão porque índice
    vazio seria "indistinguível" de haver arquivos nunca indexados. É
    distinguível: basta olhar o disco. Disco vazio → zero resultados é a
    verdade, não um erro. Disco com conteúdo → reindexa aqui e responde.

    Só o estado `unstamped` é curado. `mismatch` (schema realmente
    antigo) continua sendo erro explícito de `_check_schema`: migrar
    schema é decisão do usuário, não efeito colateral de uma busca.
    """
    con = connect(home)
    try:
        if indexdb.schema_state(con) != "unstamped":
            return
    finally:
        con.close()
    if not _has_indexable_content(home):
        return
    try:
        reindex.reindex(home)
    except indexdb.LockHeldError as exc:
        raise QueryError(
            "há conteúdo não indexado e o índice está travado por outro "
            "processo — repita depois ou rode `neurata reindex`") from exc


def _migrate(con: sqlite3.Connection, home: NeurataHome) -> None:
    """Migra um índice de versão anterior antes de checar o schema —
    buscar é o caminho quente e ninguém deve ser mandado rodar `reindex`
    (que relê a library inteira) por uma migração de segundos.

    Lock ocupado é o mesmo caso de `_ensure_searchable`: outro processo
    está escrevendo, o pedido não é atendível agora, e mandar reindexar
    seria pior conselho ainda.
    """
    try:
        indexdb.migrate_if_needed(con, home)
    except indexdb.LockHeldError as exc:
        raise QueryError(
            "o índice precisa migrar de versão e está travado por outro "
            "processo — repita depois") from exc
    except indexdb.IndexSchemaError as exc:
        raise QueryError(str(exc)) from exc


def _check_schema(con: sqlite3.Connection) -> None:
    """Delegação pro `indexdb.check_schema` público (Task 2) — mesma
    checagem, API do query intacta (QueryError, não IndexSchemaError).

    `require_reindexed=False` porque `_ensure_searchable` já rodou: um
    índice sem carimbo aqui é um NEURATA_HOME comprovadamente sem `.md`
    no disco, e buscar nele devolve [] legitimamente.
    """
    try:
        indexdb.check_schema(con, require_reindexed=False)
    except indexdb.IndexSchemaError as exc:
        raise QueryError(str(exc)) from exc


# Fragmento pronto por coluna: nada que o usuário digitou chega ao SQL,
# nem por engano futuro. `regime = 'curated'` explícito porque o espelho é
# NULL nas três por construção e afogaria toda lacuna de verdade.
_MISSING_CLAUSE = {
    col: f"(e.{col} IS NULL AND e.regime = 'curated')"
    for col in indexdb.PROVENANCE_COLS
}
# `class` foge da regra do curado de propósito: classe nula é lacuna real
# nos dois regimes — espelho cujo adapter não declarou `class:` e grão com
# valor fora do domínio caem os dois em NULL. Restringir ao curado aqui
# esconderia justamente o caso que o usuário precisa achar.
_MISSING_CLAUSE["class"] = "e.class IS NULL"
_MISSING_KEYS = (*indexdb.PROVENANCE_COLS, "class")


def _validate_missing(parsed: router.ParsedQuery) -> None:
    """`missing:` só cobre as lacunas que o índice sabe rastrear.

    Chave desconhecida é erro de uso, não lista vazia. `regime:mirror` é
    erro só para as colunas de procedência: espelho é NULL nas três por
    construção, e devolver [] ali diria "não há lacunas" quando são todas
    lacuna. `missing:class` sobrevive no espelho porque lá classe nula é
    lacuna de verdade — adapter que não declarou.
    """
    if not parsed.missing:
        return
    validas = ", ".join(_MISSING_KEYS)
    for key in parsed.missing:
        if key not in _MISSING_CLAUSE:
            raise QueryError(
                f"missing:{key} não é uma lacuna rastreada — "
                f"chaves válidas: {validas}")
    if parsed.facets.get("regime") == "mirror":
        proc = [k for k in parsed.missing if k in indexdb.PROVENANCE_COLS]
        if proc:
            raise QueryError(
                f"missing:{proc[0]} descreve lacuna de procedência do "
                "regime curado; grão espelhado não tem procedência por "
                "construção")


def _validate_class(parsed: router.ParsedQuery) -> None:
    """Classe é domínio fechado: valor fora dele é erro de uso.

    Mesma regra do `missing:`, e pela mesma razão. `class:procedual` só
    pode devolver lista vazia, e lista vazia aqui mente: o usuário lê
    "não tenho memória procedural" quando o que não existe é a classe
    que ele digitou. Facet de domínio aberto (`type:`, `tag:`) fica de
    fora — lá o vazio é resposta legítima sobre o acervo.
    """
    valor = parsed.facets.get("class")
    if valor is None or valor in indexdb.CLASSES:
        return
    raise QueryError(
        f"class:{valor} não é uma classe de memória — "
        f"válidas: {', '.join(indexdb.CLASSES)}")


_STATUS_VALUES = ("superseded",)


def _validate_status(parsed: router.ParsedQuery) -> None:
    """`status:` é domínio fechado, como `class:` — mesma razão.

    Hoje o único estado rastreado é `superseded` (marca curatorial do
    arquivo, derivada para o índice). "Em contradição aberta" existe como
    anotação no card, não como facet: é derivada da tabela `contradictions`
    e de geração em geração pode mudar de critério — expor como facet
    criaria contracto de busca sobre cache."""
    valor = parsed.facets.get("status")
    if valor is None or valor in _STATUS_VALUES:
        return
    raise QueryError(
        f"status:{valor} não é um estado rastreado — "
        f"válidos: {', '.join(_STATUS_VALUES)}")


def _prefilter(parsed: router.ParsedQuery) -> "tuple[str | None, list]":
    clauses: list[str] = []
    params: list = []
    for key in ("type", "env", "project", "regime", "class",
                *indexdb.PROVENANCE_COLS):
        if key in parsed.facets:
            clauses.append(f"e.{key} = ?")
            params.append(parsed.facets[key])
    if "status" in parsed.facets:
        # valor já validado por _validate_status (domínio fechado)
        clauses.append("e.superseded_by IS NOT NULL")
    for key in parsed.missing:
        clauses.append(_MISSING_CLAUSE[key])
    for tag in parsed.tags:
        clauses.append("EXISTS(SELECT 1 FROM entry_tags t"
                       " WHERE t.entry_rowid = e.rowid AND t.tag = ?)")
        params.append(tag)
    if "entity" in parsed.facets:
        # membrosia do grafo de entidades (v1.10): grão que É a
        # entidade (título/alias) ou que a TOCA (tag/projeto/fonte) —
        # valor lower-cased porque a extração canoniza
        clauses.append("EXISTS(SELECT 1 FROM grain_entities g"
                       " WHERE g.entry_id = e.id AND g.entity = ?)")
        params.append(parsed.facets["entity"].lower())
    if not clauses:
        return None, []
    return ("SELECT e.rowid FROM entries e WHERE "  # nosec B608
            + " AND ".join(clauses), params)


def _facet_listing(con: sqlite3.Connection, pre_sql: str, pre_params: list,
                   limit: int, include_stale: bool = False) -> list[dict]:
    filtro_stale = "" if include_stale         else " AND COALESCE(stale,'') <> 'true'"
    rows = con.execute(
        "SELECT rowid, id, slug, title, description, type, path,"
        " superseded_by"  # nosec B608
        f" FROM entries WHERE rowid IN ({pre_sql}){filtro_stale}"
        " ORDER BY updated DESC, rowid LIMIT ?",
        [*pre_params, limit]).fetchall()
    cards = [_card(r, score=None, snippet=None, via="facet") for r in rows]
    # Demotion estável: superseded afunda sem quebrar a ordem SQL
    # (updated DESC) dentro de cada grupo.
    cards.sort(key=lambda c: c["superseded_by"] is not None)
    _annotate(con, cards)
    return cards


def _contradiction_map(con: sqlite3.Connection) -> "dict[str, list[dict]]":
    """{entry_id: [{id, target, polarity}, ...]} dos pares em aberto.

    Só par aberto anota: grão substituído já perdeu a discussão — o card
    dele carrega `superseded_by`, não a lista de oponentes."""
    m: dict[str, list[dict]] = {}
    for p in indexdb.open_contradictions(con):
        m.setdefault(p["a_id"], []).append(
            {"id": p["b_id"], "target": p["target"],
             "polarity": p["a_pol"]})
        m.setdefault(p["b_id"], []).append(
            {"id": p["a_id"], "target": p["target"],
             "polarity": p["b_pol"]})
    return m


def _annotate(con: sqlite3.Connection, cards: "list[dict]") -> None:
    if not cards:
        return
    cmap = _contradiction_map(con)
    for c in cards:
        c["contradicts"] = cmap.get(c["id"], [])


def _fanout(con: sqlite3.Connection, cfg: dict, parsed: router.ParsedQuery,
            table: str, pre_sql: "str | None", pre_params: list, topn: int,
            snippets: dict) -> list:
    """Fan-out FTS por variante -> lista (peso, rowids) pro RRF.

    `table` é `entries_fts` ou `curated_fts` — mesma ordem de colunas nas
    duas, então os índices de snippet valem para ambas. `snippets` é
    preenchido in-place: a variante `raw` roda primeiro, então o `setdefault`
    preserva o trecho mais fiel ao que o usuário digitou."""
    w = cfg["bm25_weights"]
    bm25_args = [w["title"], w["aliases"], w["tags"], w["body"]] * 2
    ranked: list[tuple[float, list[int]]] = []
    for var in router.variants(parsed):
        snip_col = _SNIP_RAW if var.name == "raw" else _SNIP_NORM
        sql = (f"SELECT rowid, snippet({table}, {snip_col},"  # nosec B608
               " '[', ']', '…', 12)"
               f" FROM {table} WHERE {table} MATCH ?")
        params: list = [var.match]
        if pre_sql:
            sql += f" AND rowid IN ({pre_sql})"  # nosec B608
            params.extend(pre_params)
        sql += f" ORDER BY bm25({table}, ?,?,?,?,?,?,?,?) LIMIT ?"
        params.extend([*bm25_args, topn])
        rows = con.execute(sql, params).fetchall()
        ranked.append((cfg["variant_weights"][var.name], [r[0] for r in rows]))
        for rowid, snip in rows:
            snippets.setdefault(rowid, snip)
    return ranked


def _search(con: sqlite3.Connection, cfg: dict, parsed: router.ParsedQuery,
            pre_sql: "str | None", pre_params: list,
            limit: int, home: NeurataHome,
            context: "QueryContext | None",
            include_stale: bool = False) -> list[dict]:
    snippets: dict[int, str] = {}
    ranked = _fanout(con, cfg, parsed, "entries_fts", pre_sql, pre_params,
                     _TOPN, snippets)
    scores = rrf.fuse(ranked, cfg["rrf_k"])
    final = dict(scores)
    via = {r: "lexical" for r in scores}
    seeds = sorted(scores, key=lambda r: (-scores[r], r))[:_SEEDS]
    if seeds:
        adj = linkgraph.load_adjacency(con)
        if adj:
            nbrs = linkgraph.neighbors(adj, seeds) - set(scores)
            # V1.10: vizinhos-hub (ids negativos = entidades) expandem
            # para os MEMBROS da entidade — é o que leva o lift do grafo
            # a um grão que não cita nem é citado, mas compartilha um
            # nome com quem a query já elegeu. O hub em si não é
            # candidato (não tem entry/rowid de card).
            hubs = {n for n in nbrs if n < 0}
            for h in sorted(hubs):
                nbrs |= adj.get(h, set())
            nbrs -= set(scores) | hubs
            nbrs = _filter_rowids(con, nbrs, pre_sql, pre_params)
            pr = linkgraph.ppr(adj, seeds)
            cand = set(scores) | nbrs
            mx = max((pr.get(r, 0.0) for r in cand), default=0.0)
            if mx > 0:
                for r in sorted(cand):
                    final[r] = (final.get(r, 0.0)
                                + cfg["w_ppr"] * pr.get(r, 0.0) / mx)
                    via.setdefault(r, "graph")
    # Exclusão de stale (v1.8): antes do corte — um grão morto que
    # entra no top-K é resultado que o usuário não pediu. Com
    # include_stale, nenhuma exclusão acontece (a flag existe para isso).
    stale_rowids: set = set()
    if not include_stale:
        stale_rowids = {r[0] for r in con.execute(
            "SELECT rowid FROM entries WHERE stale='true'")}
        if stale_rowids:
            for r in stale_rowids:
                final.pop(r, None)
                via.pop(r, None)
                snippets.pop(r, None)
            seeds = [s for s in seeds if s not in stale_rowids]
    boost = cfg["skill_boost"] if parsed.skill_hint else None
    rows = {r[0]: r for r in _fetch_entries(con, list(final))}
    for rowid, row in rows.items():
        if boost and row[5] == "skill":
            final[rowid] = final.get(rowid, 0.0) * boost
    # Viés determinístico por contexto (v1.6): age NO POOL, antes do
    # corte — é o ponto em que a shelf não consegue agir (ela só
    # reordena dentro do top-K). Multiplicadores de projeto/sessão,
    # recência somada por último (design v1.6 §3).
    _apply_context(final, rows, cfg["context"], context, parsed)
    cards = []
    rowid_of: dict[str, int] = {}
    for rowid, row in rows.items():
        card = _card(row, score=round(final[rowid], 6),
                    snippet=snippets.get(rowid), via=via[rowid])
        rowid_of[card["id"]] = rowid
        cards.append(card)
    cards.sort(key=lambda c: (-c["score"], c["slug"]))
    top = cards[:limit]
    need = _quota(parsed, cfg, limit) - len(
        _curados(con, [rowid_of[c["id"]] for c in top]))
    extra: list[dict] = []
    if need > 0:
        extra = _curated_lane(con, cfg, parsed, pre_sql, pre_params, need,
                              {c["id"] for c in top}, rowid_of,
                              stale_rowids)
        top = top[:limit - len(extra)]
    _apply_shelf(con, home, cfg["shelf"], top + extra, rowid_of)
    _annotate(con, top + extra)
    # Os dois segmentos ordenam separado: a cota é rodapé por decisão de
    # política, não por score. Um sort único jogaria o curado pro topo e
    # regrediria a cabeça do ranking (ver "As quatro medições" no plano).
    # Dentro de cada segmento, superseded afunda (demotion): o grão que
    # já perdeu uma supersessão não lidera a busca — o vencedor sim.
    top.sort(key=lambda c: (c["superseded_by"] is not None,
                            -c["score"], c["slug"]))
    extra.sort(key=lambda c: (c["superseded_by"] is not None,
                              -c["score"], c["slug"]))
    return top + extra


def _apply_context(final: "dict[int, float]",
                   rows: "dict[int, tuple]",
                   cfg_ctx: dict, context: "QueryContext | None",
                   parsed: router.ParsedQuery) -> None:
    """Viés determinístico por contexto (v1.6) — in-place sobre `final`.

    Chamada NO POOL, antes do corte do top-K: é onde a shelf não chega
    (ela reordena dentro do corte). Ordem declarada no design (§3):
    multiplicadores de projeto/sessão primeiro, recência somada por
    último — a recência não é amplificada pelos multiplicadores.

    Soberania da faceta: `project:`/`session:` explícitos desligam o
    viés correspondente (todos os resultados já casam; boost viraria
    distorção grátis) — mesma régua da cota curada com `regime:`.
    Recência não tem faceta: sempre ativa (config 0 desliga).
    """
    if context is None:
        return
    w_proj = cfg_ctx["project_boost"]
    w_sess = cfg_ctx["session_boost"]
    w_rec = cfg_ctx["recency_weight"]
    tau = cfg_ctx["recency_tau_dias"]
    if not (w_proj or w_sess or w_rec):
        return
    boost_proj = (w_proj and context.project
                  and "project" not in parsed.facets)
    boost_sess = (w_sess and context.session
                  and "session" not in parsed.facets)
    for rowid, row in rows.items():
        score = final.get(rowid)
        if score is None:
            continue
        projeto, sessao, updated = row[8], row[9], row[10]
        if boost_proj and projeto == context.project:
            score *= w_proj
        if boost_sess and sessao == context.session:
            score *= w_sess
        if w_rec:
            delta = days_since(updated)
            if delta != float("inf"):
                # tau<=0 zera a pista (mesma régua da shelf), não estoura
                score += w_rec * (math.exp(-delta / tau) if tau > 0 else 0.0)
        final[rowid] = score


def _quota(parsed: router.ParsedQuery, cfg: dict, limit: int) -> int:
    """Slots reservados ao regime curado neste top-k.

    Zero quando o usuário pediu regime explícito — a faceta é soberana sobre
    a política default. Nunca mais que metade dos slots: com `limit=1` a cota
    tomaria o único resultado da consulta."""
    if "regime" in parsed.facets:
        return 0
    return min(int(cfg["regime"]["curated_quota"]), limit // 2)


def _curated_lane(con: sqlite3.Connection, cfg: dict,
                  parsed: router.ParsedQuery, pre_sql: "str | None",
                  pre_params: list, need: int, ja_no_top: set,
                  rowid_of: dict, stale_rowids: "set | None" = None
                  ) -> list[dict]:
    """Os `need` melhores grãos curados que o pool principal não trouxe.

    Busca de novo em `curated_fts` em vez de re-ordenar o pool porque o pool
    não os contém: com o espelho saturando o `LIMIT _TOPN` por variante, o
    grão curado nem chega a ser candidato (medido: 11 de 21 termos em
    disputa, zero curados no pool). O prefiltro de facets continua valendo —
    `type:`/`tag:`/`env:`/`project:` filtram a pista igual filtram o pool."""
    snippets: dict[int, str] = {}
    ranked = _fanout(con, cfg, parsed, "curated_fts", pre_sql, pre_params,
                     _LANE_TOPN, snippets)
    scores = rrf.fuse(ranked, cfg["rrf_k"])
    if stale_rowids:
        scores = {r: v for r, v in scores.items() if r not in stale_rowids}
    ordem = sorted(scores, key=lambda r: (-scores[r], r))
    linhas = {row[0]: row for row in _fetch_entries(con, ordem)}
    extra: list[dict] = []
    for rowid in ordem:
        if len(extra) == need:
            break
        row = linhas.get(rowid)
        if row is None or row[1] in ja_no_top:
            continue
        card = _card(row, score=round(scores[rowid], 6),
                     snippet=snippets.get(rowid), via="curated")
        rowid_of[card["id"]] = rowid
        extra.append(card)
    return extra


def _apply_shelf(con: sqlite3.Connection, home: NeurataHome,
                 cfg_shelf: dict, cards: list[dict],
                 rowid_of: dict[str, int]) -> None:
    if not cards:
        return
    rowids = [rowid_of[c["id"]] for c in cards]
    marks = ",".join("?" * len(rowids))
    rows = con.execute(
        f"SELECT rowid, updated, grain_quality FROM entries"  # nosec B608
        f" WHERE rowid IN ({marks})", rowids).fetchall()
    meta = {r[0]: (r[1], r[2]) for r in rows}
    agg = usage.read_usage(home)["entries"]
    for card in cards:
        rowid = rowid_of[card["id"]]
        updated, grain_quality = meta.get(rowid, ("", "mechanical"))
        u = agg.get(card["id"], {"impressions": 0, "expands": 0})
        card["shelf_score"] = shelf.compute_score(
            cfg_shelf, u["impressions"], u["expands"], updated,
            grain_quality)
    shelf.apply_boost(cards, cfg_shelf["beta"])
    for card in cards:
        card["score"] = round(card["score"], 6)
        del card["shelf_score"]


def _filter_rowids(con: sqlite3.Connection, rowids: set[int],
                   pre_sql: "str | None", pre_params: list) -> set[int]:
    if pre_sql is None or not rowids:
        return rowids
    marks = ",".join("?" * len(rowids))
    rows = con.execute(
        f"SELECT rowid FROM ({pre_sql}) WHERE rowid IN ({marks})",  # nosec B608
        [*pre_params, *sorted(rowids)]).fetchall()
    return {r[0] for r in rows}


def _fetch_entries(con: sqlite3.Connection, rowids: list[int]) -> list:
    if not rowids:
        return []
    marks = ",".join("?" * len(rowids))
    return con.execute(
        "SELECT rowid, id, slug, title, description, type, path,"
        " superseded_by, project, session, updated"  # nosec B608
        f" FROM entries WHERE rowid IN ({marks})",
        sorted(rowids)).fetchall()


def _curados(con: sqlite3.Connection, rowids: list) -> set:
    """Subconjunto curado de `rowids`, numa query só (não uma por card)."""
    if not rowids:
        return set()
    marks = ",".join("?" * len(rowids))
    sql = (f"SELECT rowid FROM entries WHERE rowid IN ({marks})"  # nosec B608
           " AND regime = 'curated'")
    return {r[0] for r in con.execute(sql, rowids)}


def _card(row, score, snippet, via) -> dict:
    _, eid, slug, title, description, etype, path, superseded = row[:8]
    return {"id": eid, "slug": slug, "title": title,
            "description": description, "type": etype, "path": path,
            "superseded_by": superseded,
            "score": score, "snippet": snippet, "via": via}
