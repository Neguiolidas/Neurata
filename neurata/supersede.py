"""neurata/supersede.py — resolução de contradição por supersessão (v1.5).

Supersessão é ato curatorial: vive no ARQUIVO (`superseded_by` no
frontmatter do perdedor, aditivo, reversível removendo a marca) e o
índice deriva — mesmo pacto de `regime_of`/`derived_from`. Journal verb
`supersede`. Espelho não participa: a verdade dele é a fonte upstream,
e o re-sync apagaria a marca na volta seguinte.

A regra determinística de vencedor (`--resolve`) existe para o lote;
para um par específico o dono escolhe com `supersede --by`. Empate
procedência (decisão do roadmap: "procedência decidindo empate" é o
motivo de `agent`/`session` existirem no índice desde a v1.1c):

  1. episódico perde para semântico/procedural (fato sobrevive a evento)
  2. `updated` mais recente vence
  3. procedência completa (`agent` E `session`) vence
  4. `created` mais recente vence
  5. menor id (ULID) — último desempate, sempre total
"""
from datetime import datetime, timezone

from neurata.entryref import resolve
from neurata.frontmatter import serialize
from neurata.home import NeurataHome, atomic_write_text, relposix
from neurata.indexdb import (LockHeldError, check_schema, connect,
                             migrate_if_needed, open_contradictions)
from neurata.reindex import reindex
from neurata.ulid import new_ulid


class SupersedeError(ValueError):
    pass


def supersede(home: NeurataHome, loser_ref: str, winner_ref: str) -> dict:
    """Marca `loser_ref` como substituído por `winner_ref`."""
    loser = resolve(home, loser_ref)
    winner = resolve(home, winner_ref)
    loser_id = str(loser.meta.get("id", ""))
    winner_id = str(winner.meta.get("id", ""))
    if not loser_id or not winner_id:
        raise SupersedeError("entrada sem id no frontmatter — rode "
                             "`neurata reindex`")
    if loser_id == winner_id:
        raise SupersedeError(
            "um grão não pode ser substituído por si mesmo")
    if loser.meta.get("source_key"):
        raise SupersedeError(
            "grão espelhado não é substituível — a verdade dele é a "
            "fonte upstream; o re-sync apagaria a marca")
    already = str(loser.meta.get("superseded_by", "") or "")
    rel = relposix(loser.path, home.root)
    if already == winner_id:
        return {"action": "noop", "loser": loser_id, "winner": winner_id,
                "path": rel,
                "reason": "já marcado com este vencedor"}

    meta = dict(loser.meta)
    meta["superseded_by"] = winner_id
    atomic_write_text(loser.path, serialize(meta, loser.body))
    _journal(home, loser_id, rel, winner_id)
    return {"action": "superseded", "loser": loser_id, "winner": winner_id,
            "path": rel, "index": _sync_index(home)}


def contradictions_report(home: NeurataHome) -> dict:
    """Pares em aberto com títulos, e quantos já estão resolvidos."""
    con = connect(home)
    try:
        # Mesma cortesia de query/harvest: índice em schema antigo migra
        # em linha — reportar contradição não pode exigir reindex manual
        # (e bater em `no such table: contradictions` cru é diagnóstico
        # errado: o índice não está corrompido, está desatualizado).
        migrate_if_needed(con, home)
        check_schema(con, require_reindexed=False)
        abertos = open_contradictions(con)
        titles = _titles(con, {p["a_id"] for p in abertos}
                         | {p["b_id"] for p in abertos})
        total = con.execute(
            "SELECT COUNT(*) FROM contradictions").fetchone()[0]
        n_assertions = con.execute(
            "SELECT COUNT(*) FROM assertions").fetchone()[0]
    finally:
        con.close()
    pares = [{"a_id": p["a_id"], "b_id": p["b_id"], "target": p["target"],
              "a_pol": p["a_pol"], "b_pol": p["b_pol"],
              "a_title": titles.get(p["a_id"], p["a_id"]),
              "b_title": titles.get(p["b_id"], p["b_id"])}
             for p in abertos]
    return {"open": pares, "open_count": len(pares),
            "resolved_count": total - len(pares), "total": total,
            "assertions": n_assertions}


def resolve_all(home: NeurataHome) -> dict:
    """Aplica a regra determinística a todos os pares em aberto.

    Marcando um perdedor, outros pares envolvendo ele fecham sozinhos —
    cada iteração reconfere o estado no índice e pula o par que já
    fechou. Journal por par; um `reindex` no fim sincroniza o cache."""
    con = connect(home)
    try:
        migrate_if_needed(con, home)
        check_schema(con, require_reindexed=False)
        pendentes = open_contradictions(con)
        resolvidos: list[dict] = []
        for p in pendentes:
            a_id, b_id = p["a_id"], p["b_id"]
            estado = con.execute(
                "SELECT id, superseded_by FROM entries"
                " WHERE id IN (?,?)", (a_id, b_id)).fetchall()
            if len(estado) < 2 or any(
                    (sb or "") != "" for _eid, sb in estado):
                continue  # fechou com a marca de outro par do lote
            winner_id, loser_id = _decide(con, a_id, b_id)
            loser = resolve(home, loser_id)
            meta = dict(loser.meta)
            meta["superseded_by"] = winner_id
            rel = relposix(loser.path, home.root)
            atomic_write_text(loser.path, serialize(meta, loser.body))
            _journal(home, loser_id, rel, winner_id)
            resolvidos.append({"loser": loser_id, "winner": winner_id,
                               "target": p["target"]})
    finally:
        con.close()
    return {"resolved": resolvidos, "resolved_count": len(resolvidos),
            "index": _sync_index(home) if resolvidos else "unchanged"}


def _decide(con, a_id: str, b_id: str) -> "tuple[str, str]":
    """(winner_id, loser_id) pela regra determinística — docstring do
    módulo tem a ordem exata e o porquê de cada desempate."""
    rows = con.execute(
        "SELECT id, class, updated, created, agent, session"
        " FROM entries WHERE id IN (?,?)", (a_id, b_id)).fetchall()
    info = {r[0]: r for r in rows}
    if len(info) < 2:
        raise SupersedeError(f"par {a_id}/{b_id} com lado ausente do "
                             "índice — rode `neurata reindex`")
    a, b = info[a_id], info[b_id]

    def _episodic(row) -> bool:
        # grão sem classe derivável (NULL) trata como episódico-ausente:
        # não há base para o desempate 1, cai para o próximo
        return row[1] == "episodic"

    if _episodic(a) != _episodic(b):
        loser, winner = (a_id, b_id) if _episodic(a) else (b_id, a_id)
        return winner, loser
    if a[2] != b[2]:
        return (a_id, b_id) if a[2] > b[2] else (b_id, a_id)
    pa = bool(a[4] and a[5])
    pb = bool(b[4] and b[5])
    if pa != pb:
        return (a_id, b_id) if pa else (b_id, a_id)
    if a[3] != b[3]:
        return (a_id, b_id) if a[3] > b[3] else (b_id, a_id)
    return (a_id, b_id) if a_id < b_id else (b_id, a_id)


def _sync_index(home: NeurataHome) -> str:
    """Reindex best-effort pós-marcas (mesma cortesia do `compact`):
    lock ocupado não desfaz marca alguma já gravada no arquivo."""
    try:
        reindex(home)
        return "reindexed"
    except LockHeldError:
        return "pending-index"


def _journal(home: NeurataHome, loser_id: str, loser_rel: str,
             winner_id: str) -> None:
    home.append_log("journal", {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tick": new_ulid(), "verb": "supersede", "item": loser_id,
        "src": None, "dst": loser_rel, "winner": winner_id,
    })


def _titles(con, ids: "set[str]") -> "dict[str, str]":
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT id, title FROM entries WHERE id IN ({marks})",  # nosec B608
        sorted(ids)).fetchall()
    return {eid: title for eid, title in rows}
