"""neurata/query.py — orquestrador do pipeline de busca.

parse facets → prefiltro (subquery rowid) → fan-out ≤6 MATCH → RRF
→ união com vizinhos 1-hop → PPR aditivo → boost skill → cards.
"""
import sqlite3

from neurata import config, linkgraph, router, rrf, shelf, usage
from neurata.home import NeurataHome
from neurata.indexdb import INDEX_SCHEMA_VERSION, connect

_TOPN = 50    # candidatos por variante
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
            "(type:/tag:/env:/project:)")
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


def _check_schema(con: sqlite3.Connection) -> None:
    row = con.execute(
        "SELECT value FROM meta WHERE key='index_schema_version'").fetchone()
    if row is None or str(row[0]) != str(INDEX_SCHEMA_VERSION):
        raise QueryError(
            "índice ausente ou em schema antigo — rode `neurata reindex`")


def _prefilter(parsed: router.ParsedQuery) -> "tuple[str | None, list]":
    clauses: list[str] = []
    params: list = []
    for key in ("type", "env", "project"):
        if key in parsed.facets:
            clauses.append(f"e.{key} = ?")
            params.append(parsed.facets[key])
    for tag in parsed.tags:
        clauses.append("EXISTS(SELECT 1 FROM entry_tags t"
                       " WHERE t.entry_rowid = e.rowid AND t.tag = ?)")
        params.append(tag)
    if not clauses:
        return None, []
    return ("SELECT e.rowid FROM entries e WHERE " + " AND ".join(clauses),
            params)


def _facet_listing(con: sqlite3.Connection, pre_sql: str, pre_params: list,
                   limit: int) -> list[dict]:
    rows = con.execute(
        "SELECT rowid, id, slug, title, description, type, path"
        f" FROM entries WHERE rowid IN ({pre_sql})"
        " ORDER BY updated DESC, rowid LIMIT ?",
        [*pre_params, limit]).fetchall()
    return [_card(r, score=None, snippet=None, via="facet") for r in rows]


def _search(con: sqlite3.Connection, cfg: dict, parsed: router.ParsedQuery,
            pre_sql: "str | None", pre_params: list,
            limit: int, home: NeurataHome) -> list[dict]:
    w = cfg["bm25_weights"]
    bm25_args = [w["title"], w["aliases"], w["tags"], w["body"]] * 2
    ranked: list[tuple[float, list[int]]] = []
    snippets: dict[int, str] = {}
    for var in router.variants(parsed):
        snip_col = _SNIP_RAW if var.name == "raw" else _SNIP_NORM
        sql = (f"SELECT rowid, snippet(entries_fts, {snip_col},"
               " '[', ']', '…', 12)"
               " FROM entries_fts WHERE entries_fts MATCH ?")
        params: list = [var.match]
        if pre_sql:
            sql += f" AND rowid IN ({pre_sql})"
            params.extend(pre_params)
        sql += " ORDER BY bm25(entries_fts, ?,?,?,?,?,?,?,?) LIMIT ?"
        params.extend([*bm25_args, _TOPN])
        rows = con.execute(sql, params).fetchall()
        ranked.append((cfg["variant_weights"][var.name],
                       [r[0] for r in rows]))
        for rowid, snip in rows:
            snippets.setdefault(rowid, snip)  # raw roda primeiro: preferência
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
    _apply_shelf(con, home, cfg["shelf"], top, rowid_of)
    top.sort(key=lambda c: (-c["score"], c["slug"]))
    return top


def _apply_shelf(con: sqlite3.Connection, home: NeurataHome,
                 cfg_shelf: dict, cards: list[dict],
                 rowid_of: dict[str, int]) -> None:
    if not cards:
        return
    rowids = [rowid_of[c["id"]] for c in cards]
    marks = ",".join("?" * len(rowids))
    rows = con.execute(
        f"SELECT rowid, updated, grain_quality FROM entries"
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
        f"SELECT rowid FROM ({pre_sql}) WHERE rowid IN ({marks})",
        [*pre_params, *sorted(rowids)]).fetchall()
    return {r[0] for r in rows}


def _fetch_entries(con: sqlite3.Connection, rowids: list[int]) -> list:
    if not rowids:
        return []
    marks = ",".join("?" * len(rowids))
    return con.execute(
        "SELECT rowid, id, slug, title, description, type, path"
        f" FROM entries WHERE rowid IN ({marks})",
        sorted(rowids)).fetchall()


def _card(row, score, snippet, via) -> dict:
    _, eid, slug, title, description, etype, path = row
    return {"id": eid, "slug": slug, "title": title,
            "description": description, "type": etype, "path": path,
            "score": score, "snippet": snippet, "via": via}
