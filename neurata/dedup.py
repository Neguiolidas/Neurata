"""neurata/dedup.py — near-dup detection: shingles + Jaccard.

Isola hashing/similaridade; reusa a normalização PT-aware de
`textnorm` sem duplicar lógica. Zero ML, zero config — v0.4 fixa as
constantes abaixo (spec §4: risco de falso positivo é aceito, marca é
reversível).
"""
import hashlib

from neurata.textnorm import normalize

NEAR_DUP_JACCARD = 0.85
SHINGLE_N = 5
MIN_WORDS = 5


def shingle_hashes(body: str, n: int = SHINGLE_N) -> list[str]:
    """Shingles de `n` palavras do corpo normalizado, hash truncado.

    Retorna lista ORDENADA de `sha256(shingle)[:16]` hex (set → sorted,
    dedup automático). Corpo com menos de MIN_WORDS palavras (após
    normalizar) é degenerado demais pro shingle — retorna `[]` (nunca
    acusa near-dup, spec §4 item 4).
    """
    words = normalize(body).split()
    if len(words) < MIN_WORDS:
        return []
    hashes = {
        hashlib.sha256(" ".join(words[i:i + n]).encode("utf-8")).hexdigest()[:16]
        for i in range(len(words) - n + 1)
    }
    return sorted(hashes)


def jaccard(a: frozenset, b: frozenset) -> float:
    """Similaridade de Jaccard entre dois sets de shingle-hashes.

    União vazia (ambos vazios) → 0.0, nunca divide por zero — corpo
    curto (shingles == []) nunca é near-dup de nada, inclusive de si
    mesmo em teoria degenerada.
    """
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)
