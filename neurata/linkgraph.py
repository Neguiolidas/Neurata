"""neurata/linkgraph.py — grafo de [[links]] + Personalized PageRank.

Adjacência não-direcionada (link = sinal de relação nos dois sentidos;
backlink vale). PPR com lazy random walk (p ← ½p + ½step): mata a
oscilação em grafos bipartidos (cadeias) que o power iteration puro tem
com poucas iterações. Iteração em ordem sorted — determinístico bit a bit.
"""
import sqlite3

ALPHA = 0.85
ITERS = 10


def load_adjacency(con: sqlite3.Connection) -> dict[int, set[int]]:
    """Carrega a adjacência em espaço de `rowid`, traduzindo do `id` com
    que `edges` é persistida.

    A tabela é chaveada por `id` porque `rowid` é reciclado: o
    update-in-place do `tick` apaga e reinsere, e uma aresta guardada por
    `rowid` passaria a apontar para o grão que herdou o número. O ranking,
    porém, é todo em `rowid` (é a chave do FTS) — então a tradução mora
    aqui, na fronteira, e o PPR nunca fica sabendo.

    O JOIN é a defesa que fecha o ciclo: aresta cuja ponta não existe mais
    simplesmente não entra, em vez de ressuscitar via `rowid` reciclado.
    """
    adj: dict[int, set[int]] = {}
    rows = con.execute(
        "SELECT s.rowid, d.rowid FROM edges e"
        " JOIN entries s ON s.id = e.src_id"
        " JOIN entries d ON d.id = e.dst_id")
    for src, dst in rows:
        adj.setdefault(src, set()).add(dst)
        adj.setdefault(dst, set()).add(src)
    return adj


def neighbors(adj: dict[int, set[int]], seeds: list[int]) -> set[int]:
    out: set[int] = set()
    for s in seeds:
        out |= adj.get(s, set())
    return out


def ppr(adj: dict[int, set[int]], seeds: list[int],
        alpha: float = ALPHA, iters: int = ITERS) -> dict[int, float]:
    if not seeds or not adj:
        return {}
    teleport = 1.0 / len(seeds)
    p: dict[int, float] = {s: teleport for s in sorted(set(seeds))}
    seed_set = sorted(set(seeds))
    for _ in range(iters):
        nxt: dict[int, float] = {s: (1 - alpha) * teleport for s in seed_set}
        for node in sorted(p):
            mass = p[node]
            nbrs = adj.get(node)
            if not nbrs:
                # dangling: devolve massa aos seeds (mantém soma ~1)
                for s in seed_set:
                    nxt[s] = nxt.get(s, 0.0) + alpha * mass * teleport
                continue
            share = alpha * mass / len(nbrs)
            for nb in sorted(nbrs):
                nxt[nb] = nxt.get(nb, 0.0) + share
        # lazy walk: metade da massa fica parada
        keys = sorted(set(p) | set(nxt))
        p = {k: 0.5 * p.get(k, 0.0) + 0.5 * nxt.get(k, 0.0) for k in keys}
    return p
