#!/usr/bin/env python3
"""scripts/bench_query.py — T9: benchmark manual de p50/p95 de query.

Roda sob demanda (NÃO entra no CI — ver tests/test_bench.py pro gate
frouxo de CI). Gera um corpus representativo (~500 notas, títulos/tags/
corpo variados, links cruzados) sob um NEURATA_HOME temporário, reindexa,
e mede latência de >=100 queries lexicais representativas (termo único,
dois termos, facet, frase). Reporta p50/p95 em ms + specs da máquina.

Uso:
    .venv/bin/python scripts/bench_query.py
"""
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neurata.home import NeurataHome
from neurata.query import query
from neurata.reindex import reindex

N_NOTES = 500
N_QUERIES = 120

_WORDS = [
    "compactação", "vetores", "grafo", "skill", "deploy", "índice",
    "busca", "memória", "agente", "contexto", "curadoria", "sqlite",
    "python", "commit", "revisão", "spec", "plano", "tick", "harvest",
    "envelope", "dedup", "reindex", "snapshot", "quarentena", "journal",
]

_TYPES = ["note", "decision", "skill"]


def _build_corpus(home: NeurataHome) -> None:
    for i in range(N_NOTES):
        w = [_WORDS[(i + j) % len(_WORDS)] for j in range(5)]
        link_target = (i * 7) % N_NOTES
        dtype = _TYPES[i % len(_TYPES)]
        (home.library / f"nota-{i:04d}.md").write_text(
            f"---\nid: 01B{i:05d}\ntitle: Nota {i} sobre {w[0]} e {w[1]}\n"
            f"type: {dtype}\ntags: [{w[2]}, {w[3]}]\n---\n"
            f"Discussão detalhada sobre {w[2]} aplicado a {w[3]}, "
            f"com impacto em {w[4]}. Referência relacionada em "
            f"[[nota-{link_target:04d}]] e considerações de {w[0]}.\n")


def _representative_queries() -> "list[str]":
    qs = []
    for i in range(N_QUERIES):
        kind = i % 4
        a = _WORDS[i % len(_WORDS)]
        b = _WORDS[(i + 5) % len(_WORDS)]
        if kind == 0:
            qs.append(a)
        elif kind == 1:
            qs.append(f"{a} {b}")
        elif kind == 2:
            qs.append(f"type:{_TYPES[i % len(_TYPES)]} {a}")
        else:
            qs.append(f"sobre {a} aplicado")
    return qs


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="neurata-bench-") as tmp:
        home = NeurataHome(Path(tmp))
        home.init()
        _build_corpus(home)
        reindex(home)

        queries = _representative_queries()
        for q in queries[:5]:
            query(home, q)  # warm-up fora da medição

        times_ms = []
        for q in queries:
            t0 = time.perf_counter()
            query(home, q)
            times_ms.append((time.perf_counter() - t0) * 1000)

        p50 = statistics.median(times_ms)
        p95 = statistics.quantiles(times_ms, n=20)[18]

        print(f"corpus: {N_NOTES} notas, {len(queries)} queries")
        print(f"p50: {p50:.2f} ms")
        print(f"p95: {p95:.2f} ms")
        print(f"máquina: {platform.platform()}, "
              f"python {platform.python_version()}, "
              f"cpu_count={__import__('os').cpu_count()}")


if __name__ == "__main__":
    main()
