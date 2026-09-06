"""neurata/assertion.py — extração determinística de afirmações normativas.

v1.5 "Contradição de verdade": o near-dup (Jaccard sobre shingles) marca
quase-duplicata — "Use X" e "nunca use X" jamais seriam marcados por
sobreposição de texto. Aqui a unidade é a AFIRMAÇÃO: (alvo, polaridade)
extraída de sentenças imperativas do corpo, para que dois grãos curados
que afirmam polaridades opostas SOBRE O MESMO ALVO fiquem emparelháveis
por igualdade exata de alvo normalizado.

Frontera deliberada (design 2026-09-05): só o IMPERATIVO normativo —
verbo de recomendação/proibição com objeto ("use X", "nunca habilite Y",
"evite Z"). Declarações nominais ("X foi deprecado"), comparação,
ironia e negação implícita exigem julgamento — v2.2. Zero LLM: mesmo
corpo → mesmas afirmações, em qualquer máquina.

Puro: nenhuma I/O, nenhum estado. Quem chama (tick incremental e reindex
full) decide persistência — como shingles, afirmação é cache derivável,
nunca fonte de verdade.
"""
import re
from dataclasses import dataclass

from neurata.textnorm import normalize

#: Polaridade fechada. `pos` = recomendação ("use X"); `neg` = proibição
#: ("nunca use X", "evite X").
POLARITIES = ("pos", "neg")

# Negação ANTES do verbo muda a polaridade, nunca o alvo: o objeto de
# "nunca use X" é o mesmo de "use X" — é isso que torna o par
# emparelhável por alvo. Formas já normalizadas (strip_accents + lower):
# "não" → "nao", "don't" → "don t".
_NEGATION = r"(?:nunca|jamais|nao|never|don t|do not)\s+"

# Verbos de recomendação (pos). Interseção PT/EN de propósito: "use" e
# "configure" valem nos dois; formas flexionadas próximas ao infinitivo
# cobrem o imperativo PT ("habilite", "prefira").
_POS_VERB = (r"(?:use|utilize|habilite|ative|instale|configure|importe|"
             r"prefira|escolha|adopte|aplique|prefer|enable|install|"
             r"adopt|choose|apply)")

# Verbos de proibição com polaridade própria (negação embutida no
# verbo). "nao evite" é dupla negação — fora do escopo, segue `neg`.
_NEG_VERB = r"(?:evite|evitar|avoid|omita|abstenha)"

_ASSERTION = re.compile(
    rf"\b(?:(?P<neg>{_NEGATION})?)?(?P<verb>{_POS_VERB}|{_NEG_VERB})"
    rf"(?:\s+(?P<rest>[a-z0-9][a-z0-9 ]*?))?(?=[.!?;:]|\n|$)")

# Conectivos e preposições encerram o alvo: o que vem depois é
# condição/loc/beneficiário, não o objeto ("use o cache SE a leitura
# dominar", "use sqlite PARA o cache" — alvos são "cache" e "sqlite").
# Cortar na preposição encurta o alvo ao núcleo do objeto, o que AUMENTA
# o emparelhamento entre grãos que descrevem o mesmo alvo com detalhe
# diferente.
_CONNECTOR = re.compile(
    r"\b(?:se|quando|para|porque|pois|caso|a menos|em vez|sem|com|em|no|"
    r"na|nos|nas|do|da|dos|das|de|ao|if|when|for|because|since|unless|"
    r"instead|without|using|with|on|in|at|from|of|to|by)\b")

# Deterministas/artigos à esquerda do alvo não são a identidade dele:
# "use O postgres" e "use postgres" são a mesma afirmação.
_LEFT_STRIP = ("o ", "a ", "os ", "as ", "um ", "uma ", "the ", "seu ",
               "sua ", "esse ", "essa ", "este ", "esta ", "this ",
               "that ")

_MAX_TARGET_TOKENS = 6
_MIN_TARGET_CHARS = 3


@dataclass(frozen=True)
class Assertion:
    """Afirmação normativa extraída. `quote` guarda a sentença (cap 120)
    para o journal — auditoria sem reler o corpo."""

    target: str
    polarity: str
    quote: str

    def __post_init__(self) -> None:
        if self.polarity not in POLARITIES:
            raise ValueError(f"polaridade fora do domínio: {self.polarity!r}")


def _target(rest: "str | None") -> "str | None":
    if not rest:
        return None
    cut = _CONNECTOR.search(rest)
    # Conector colado no início ("use in-memory cache" → "in" na posição
    # zero) não é fronteira — é o próprio objeto começando.
    if cut is not None and cut.start() > 0:
        rest = rest[:cut.start()]
    tokens = rest.split()[:_MAX_TARGET_TOKENS]
    target = " ".join(tokens)
    for prefix in _LEFT_STRIP:
        if target.startswith(prefix):
            target = target[len(prefix):]
            break
    target = target.strip()
    if len(target) < _MIN_TARGET_CHARS or not target:
        return None
    return target


def extract(body: str) -> "list[Assertion]":
    """Afirmações do corpo, dedup (alvo, polaridade), ordem determinística.

    Cada sentença pode render mais de uma afirmação ("use sqlite para o
    cache; evite redis") — cada predicado casa uma vez, na ordem do
    texto. Corpo sem predicado normativo → lista vazia (a maioria do
    acervo: prosa descritiva não é afirmação).
    """
    out: dict[tuple[str, str], Assertion] = {}
    for sentence in re.split(r"[.!?;\n]", body):
        norm = normalize(sentence)
        if not norm:
            continue
        for m in _ASSERTION.finditer(norm):
            verb = m.group("verb")
            polarity = "neg" if (m.group("neg") or verb in
                                 ("evite", "evitar", "avoid", "omita",
                                  "abstenha")) else "pos"
            target = _target(m.group("rest"))
            if target is None:
                continue
            quote = sentence.strip()[:120]
            out.setdefault((target, polarity),
                           Assertion(target, polarity, quote))
    return [out[k] for k in sorted(out)]


def find_pairs(new: "list[Assertion]",
               known: "dict[str, list[tuple[str, str]]]") -> "list[tuple[str, str, str]]":
    """Pares de contradição de `new` contra o acervo já indexado.

    `known` é {alvo: [(entry_id, polaridade), ...]}. Devolve
    (opponent_id, alvo, polaridade_da_nova) para cada oponente de
    polaridade oposta. Mesma polaridade sobre o mesmo alvo é CONCOR-
    DÂNCIA, não conflito. O chamador filtra auto-referência e canoniza
    a ordem do par.
    """
    pairs: list[tuple[str, str, str]] = []
    for a in new:
        for opponent_id, opponent_pol in known.get(a.target, []):
            if opponent_pol != a.polarity:
                pairs.append((opponent_id, a.target, a.polarity))
    return pairs
