"""neurata/query.py — orquestrador do pipeline de busca.

parse facets → prefiltro (subquery rowid) → fan-out ≤6 MATCH → RRF
→ união com vizinhos 1-hop → PPR aditivo → boost skill → cards.

Invariante de SQL (justifica os `# nosec B608` espalhados aqui): todo
valor vindo do usuário viaja em parâmetro `?`. O que é interpolado em
f-string se limita a três coisas geradas por nós — `marks`, que é
literalmente `",".join("?" * n)`; `pre_sql`, montado em `_prefilter`
só com cláusulas literais; e constantes de módulo (`_SNIP_*`, int).
Ao mexer nestas queries, manter a invariante ou remover o `nosec`.
"""
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
from neurata.home import NeurataHome
from neurata.indexdb import connect

_TOPN = 50    # candidatos por variante
_LANE_TOPN = 20  # candidatos por variante na pista curada (corpus pequeno)
_SEEDS = 10   # seeds do PPR (top do RRF)
_SNIP_RAW = 3   # índice da coluna body no FTS
_SNIP_NORM = 7  # índice da coluna body_norm


class QueryError(ValueError):
    pass


def query(home: NeurataHome, qstr: str, limit: int = 10) -> dict:
    cfg = config.load(home)
    parsed = router.parse(qstr)
    if not parsed.has_text and not parsed.has_facets:
        raise QueryError(
            "query vazia — passe texto e/ou facets "
            "(type:/tag:/env:/project:/regime:)")
    _ensure_searchable(home)
    con = connect(home)
    try:
        _check_schema(con)
        pre_sql, pre_params = _prefilter(parsed)
        if not parsed.has_text:
            assert pre_sql is not None  # has_facets garante clauses não-vazias
            results = _facet_listing(con, pre_sql, pre_params, limit)
        else:
            results = _search(con, cfg, parsed, pre_sql, pre_params,
                              limit, home)
    finally:
        con.close()
    for rank, card in enumerate(results, start=1):
        usage.log_event(home, "query", card["id"], query=qstr, rank=rank)
    return {"results": results}


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


def _prefilter(parsed: router.ParsedQuery) -> "tuple[str | None, list]":
    clauses: list[str] = []
    params: list = []
    for key in ("type", "env", "project", "regime"):
        if key in parsed.facets:
            clauses.append(f"e.{key} = ?")
            params.append(parsed.facets[key])
    for tag in parsed.tags:
        clauses.append("EXISTS(SELECT 1 FROM entry_tags t"
                       " WHERE t.entry_rowid = e.rowid AND t.tag = ?)")
        params.append(tag)
    if not clauses:
        return None, []
    return ("SELECT e.rowid FROM entries e WHERE "  # nosec B608
            + " AND ".join(clauses), params)


def _facet_listing(con: sqlite3.Connection, pre_sql: str, pre_params: list,
                   limit: int) -> list[dict]:
    rows = con.execute(
        "SELECT rowid, id, slug, title, description, type, path"  # nosec B608
        f" FROM entries WHERE rowid IN ({pre_sql})"
        " ORDER BY updated DESC, rowid LIMIT ?",
        [*pre_params, limit]).fetchall()
    return [_card(r, score=None, snippet=None, via="facet") for r in rows]


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
            limit: int, home: NeurataHome) -> list[dict]:
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
            nbrs = _filter_rowids(con, nbrs, pre_sql, pre_params)
            pr = linkgraph.ppr(adj, seeds)
            cand = set(scores) | nbrs
            mx = max((pr.get(r, 0.0) for r in cand), default=0.0)
            if mx > 0:
                for r in sorted(cand):
                    final[r] = (final.get(r, 0.0)
                                + cfg["w_ppr"] * pr.get(r, 0.0) / mx)
                    via.setdefault(r, "graph")
    boost = cfg["skill_boost"] if parsed.skill_hint else None
    cards = []
    rowid_of: dict[str, int] = {}
    for row in _fetch_entries(con, list(final)):
        rowid = row[0]
        score = final[rowid]
        if boost and row[5] == "skill":
            score *= boost
        card = _card(row, score=round(score, 6),
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
                              {c["id"] for c in top}, rowid_of)
        top = top[:limit - len(extra)]
    _apply_shelf(con, home, cfg["shelf"], top + extra, rowid_of)
    # Os dois segmentos ordenam separado: a cota é rodapé por decisão de
    # política, não por score. Um sort único jogaria o curado pro topo e
    # regrediria a cabeça do ranking (ver "As quatro medições" no plano).
    top.sort(key=lambda c: (-c["score"], c["slug"]))
    extra.sort(key=lambda c: (-c["score"], c["slug"]))
    return top + extra


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
                  rowid_of: dict) -> list[dict]:
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
        "SELECT rowid, id, slug, title, description, type, path"  # nosec B608
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
    _, eid, slug, title, description, etype, path = row
    return {"id": eid, "slug": slug, "title": title,
            "description": description, "type": etype, "path": path,
            "score": score, "snippet": snippet, "via": via}
