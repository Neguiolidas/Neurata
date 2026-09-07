"""neurata/linkgraph.py — grafo de [[links]] + entidades + PPR.

Adjacência não-direcionada (link = sinal de relação nos dois sentidos;
backlink vale). PPR com lazy random walk (p ← ½p + ½step): mata a
oscilação em grafos bipartidos (cadeias) que o power iteration puro tem
com poucas iterações. Iteração em ordem sorted — determinístico bit a bit.

v1.10: além das arestas de wikilink, ENTIDADES (título/alias/tag/
projeto/fonte que os grãos declaram) viram nós-hub com id sintético
negativo — grãos que compartilham um nome ficam a um hop um do outro
sem [[link]] explícito. Só entidade na FAIXA de relação vira hub: com
um membro só o hub é um laço que devolve a massa ao próprio grão, e
acima do teto ele dilui até virar ruído cobrando O(membros) em toda
busca (medido: 87 ms → 738 ms com 2.4k membros). A membrosia não
encolhe por isso — `entity:` continua respondendo pela entidade grande.
"""
import sqlite3

ALPHA = 0.85
ITERS = 10
# Faixa em que uma entidade é RELAÇÃO e não categoria (ver o topo do
# módulo). Fora dela o hub não é construído.
MIN_HUB = 2
MAX_HUB = 64


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

    # Entidades como nós-hub (v1.10): grão ↔ hub ↔ grão. O hub é nó de
    # primeira classe com id sintético NEGATIVO (nunca colide com
    # rowid): a massa que recebe DILUI entre os membros — hub de 2-3 dá
    # lift real, e o que passa do teto nem é construído (ver MAX_HUB).
    # `sorted(membros)` mantém o determinismo bit a bit do PPR.
    rowid_of: dict[str, int] = {
        eid: rid for rid, eid in con.execute("SELECT rowid, id FROM entries")
    }
    membros: dict[str, list[int]] = {}
    for entry_id, entity in con.execute(
            "SELECT entry_id, entity FROM grain_entities"):
        rid = rowid_of.get(entry_id)
        if rid is not None:
            membros.setdefault(entity, []).append(rid)
    hub = -1
    for entity in sorted(membros):
        members = sorted(set(membros[entity]))
        if not MIN_HUB <= len(members) <= MAX_HUB:
            continue
        adj.setdefault(hub, set()).update(members)
        for rid in members:
            adj.setdefault(rid, set()).add(hub)
        hub -= 1
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
