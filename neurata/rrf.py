"""neurata/rrf.py — Reciprocal Rank Fusion ponderada.

score(d) = Σ_lista w / (k + rank). Determinístico. Seam para v0.7:
lista nova de sinal (vetores) entra na fusão sem mudar nada aqui.
"""


def fuse(ranked_lists: "list[tuple[float, list[int]]]",
         k: int = 60) -> dict[int, float]:
    scores: dict[int, float] = {}
    for weight, rows in ranked_lists:
        for rank, rowid in enumerate(rows, start=1):
            scores[rowid] = scores.get(rowid, 0.0) + weight / (k + rank)
    return scores
