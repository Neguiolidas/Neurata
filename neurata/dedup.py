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


SHINGLE_BYTES = 8


def pack_shingles(hashes: "list[str]") -> bytes:
    """Serializa shingle-hashes hex(16) num blob de 8 B por shingle.

    Cada hash é hex de 16 chars — 8 bytes de informação que o JSON antigo
    gastava 20 B pra guardar (aspas, vírgula e a expansão hex 2:1). A
    conversão é **sem perda**: `bytes.fromhex` é a inversa exata de
    `hexdigest()[:16]`, e `shingle_hashes` já devolve a lista ordenada,
    então a ordem sobrevive à ida e volta sem precisar reordenar.
    """
    return bytes.fromhex("".join(hashes))


def unpack_shingles(blob: "bytes | None") -> frozenset:
    """Inversa de `pack_shingles` — blob → frozenset de hex(16).

    `None`/vazio devolve set vazio (corpo curto nunca teve shingles, e
    `jaccard` já trata união vazia). Um blob com tamanho não múltiplo de
    `SHINGLE_BYTES` é corrupção silenciosa de dado, não um shingle
    degenerado: estoura em vez de devolver um set truncado que faria a
    dedup errar sem ninguém perceber.
    """
    if not blob:
        return frozenset()
    if len(blob) % SHINGLE_BYTES:
        raise ValueError(
            f"blob de shingles com {len(blob)} B não é múltiplo de "
            f"{SHINGLE_BYTES} — índice corrompido, rode `neurata reindex`"
        )
    return frozenset(
        blob[i:i + SHINGLE_BYTES].hex()
        for i in range(0, len(blob), SHINGLE_BYTES)
    )


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
