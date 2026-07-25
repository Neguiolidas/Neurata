"""tests/test_bench.py — p50 < 100 ms (CI folgado; alvo real 50 ms)."""
import statistics
import time

from neurata.home import NeurataHome
from neurata.query import query
from neurata.reindex import reindex

_WORDS = ["compactação", "vetores", "grafo", "skill", "deploy", "índice",
          "busca", "memória", "agente", "contexto", "curadoria", "sqlite"]


def test_bench_p50_warm(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    n = 500
    termos: dict[str, set[str]] = {}
    for i in range(n):
        w = [_WORDS[(i + j) % len(_WORDS)] for j in range(4)]
        link = f"[[nota-{(i * 7) % n:03d}]]"
        slug = f"nota-{i:03d}"
        termos[slug] = set(w)
        (home.library / f"{slug}.md").write_text(
            f"---\nid: 01B{i:04d}\ntitle: Nota {i} {w[0]}\n"
            f"tags: [{w[1]}]\n---\n"
            f"Corpo sobre {w[2]} e {w[3]} ligando {link}.\n")
    reindex(home)
    queries = [f"{_WORDS[i % len(_WORDS)]} {_WORDS[(i + 5) % len(_WORDS)]}"
               for i in range(30)]
    for q in queries[:3]:
        query(home, q)  # warm-up fora da medição
    times = []
    for q in queries:
        t0 = time.perf_counter()
        res = query(home, q)
        times.append(time.perf_counter() - t0)
        # Fora da janela medida. `assert res["results"]` sozinho passava com
        # QUALQUER documento devolvido — inclusive nenhum acerto. Aqui cada
        # card tem que conter um termo da query (ou ser vizinho de 1 salto de
        # quem contém, que é como o grafo entra no resultado por desenho).
        assert res["results"], f"{q!r} não devolveu nada"
        alvo = set(q.split())
        for card in res["results"]:
            vizinhos = termos.get(card["slug"], set())
            assert alvo & vizinhos or card["via"] != "lexical", (
                f"{q!r}: {card['slug']} veio como lexical sem conter termo "
                f"({sorted(vizinhos)})")
    assert statistics.median(times) < 0.1
