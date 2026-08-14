"""neurata/router.py — parse de query, facets e fan-out determinístico.

Sanitização por construção: input do usuário NUNCA vai cru pro MATCH.
Tokens são quotados; operadores FTS5 não são expostos. Propriedade
garantida: nenhuma query de usuário produz OperationalError de sintaxe.
"""
import re
from dataclasses import dataclass, field

from neurata.textnorm import normalize

_FACET = re.compile(
    r"(?:^|(?<=\s))"
    r"(type|tag|env|project|regime|class|agent|session|origin):(\S+)")
_MISSING = re.compile(r"(?:^|(?<=\s))missing:(\S+)")
_PHRASE = re.compile(r'"([^"]+)"')
_SKILL = re.compile(r"\bcomo fa[çc]o\b|\bhow do i\b", re.IGNORECASE)
_WORD = re.compile(r"\w")
_RAW_COLS = "{title aliases tags body}"
_NORM_COLS = "{title_norm aliases_norm tags_norm body_norm}"


@dataclass
class Variant:
    name: str    # raw | norm | prefix (= chave de peso)
    match: str   # string MATCH FTS5 pronta (sanitizada)


@dataclass
class ParsedQuery:
    text: str
    tokens: list[str]
    phrases: list[str]
    facets: dict[str, str]  # type/env/project/regime/agent/session/origin
    tags: list[str]
    skill_hint: bool
    # acrescentado no fim com default: a única construção é posicional
    missing: list[str] = field(default_factory=list)

    @property
    def has_text(self) -> bool:
        return bool(self.tokens or self.phrases)

    @property
    def has_facets(self) -> bool:
        return bool(self.facets or self.tags or self.missing)


def parse(q: str) -> ParsedQuery:
    skill = bool(_SKILL.search(q))
    # frases primeiro: facet quotado ("type:x") é literal, não facet
    phrases = [p.strip() for p in _PHRASE.findall(q) if _WORD.search(p)]
    rest = _PHRASE.sub(" ", q).replace('"', " ")  # aspa desbalanceada=literal
    facets: dict[str, str] = {}
    tags: list[str] = []
    missing: list[str] = []

    def _take(m: "re.Match[str]") -> str:
        key, val = m.group(1), m.group(2)
        if key == "tag":
            tags.append(val.lower())
        else:
            facets[key] = val
        return " "

    def _take_missing(m: "re.Match[str]") -> str:
        key = m.group(1).lower()
        if key not in missing:  # conjunção idempotente, ordem preservada
            missing.append(key)
        return " "

    # `missing:` antes de `_FACET`: nenhuma chave colide, mas a ordem
    # torna a precedência explícita em vez de acidental.
    rest = _MISSING.sub(_take_missing, rest)
    rest = _FACET.sub(_take, rest)
    # token sem \w (só símbolo) nunca vira MATCH — some do fan-out
    tokens = [t for t in rest.split() if _WORD.search(t)]
    return ParsedQuery(q, tokens, phrases, facets, tags, skill, missing)


def _formas(norm_t: list[str]) -> list[str]:
    """Cada token mais sua variação de número, na ordem, sem repetir.

    Token multi-palavra é identificador quebrado (`HTTPServer` → `http
    server`) e casa como frase: flexionar a frase inteira não teria sentido.
    """
    out: list[str] = []
    for n in norm_t:
        out.append(n)
        if " " not in n:
            out.append(n[:-1] if n.endswith("s") and len(n) > 3 else n + "s")
    return list(dict.fromkeys(out))


def variants(parsed: ParsedQuery) -> list[Variant]:
    """1 a 3 variantes; raw primeiro (preferência de snippet).

    Morfologia é expansão de TERMO, não leitura alternativa da query inteira:
    as formas entram na MESMA lista e o BM25 arbitra. Uma lista por leitura
    morfológica degenerava — a forma que não casa nada (`datapipes`) some do
    MATCH, a lista vira "quem casa o resto" e dá voto de rank 1 no RRF a um
    documento que o termo raro não elegeria.
    """
    if not parsed.has_text:
        return []
    out = [Variant("raw", _match(_RAW_COLS, parsed.tokens, parsed.phrases))]
    # normalize() pode devolver várias palavras, mas o token do usuário segue
    # sendo UM termo (frase FTS5) — nunca N termos OR'd, senão "0.10" vira
    # "0" OR "10" e passa a casar qualquer "3.0".
    norm_t = [n for tok in parsed.tokens if (n := normalize(tok))]
    norm_p = [np for p in parsed.phrases if (np := normalize(p))]
    if not (norm_t or norm_p):
        return out
    out.append(Variant("norm", _match(_NORM_COLS, _formas(norm_t), norm_p)))
    if norm_t:
        out.append(Variant(
            "prefix", _match(_NORM_COLS, norm_t, norm_p, prefix_last=True)))
    return out


def _quote(tok: str) -> str:
    return '"' + tok.replace('"', '""') + '"'


def _match(cols: str, tokens: list[str], phrases: list[str],
           prefix_last: bool = False) -> str:
    toks = [_quote(t) for t in tokens]
    if prefix_last and toks:
        toks[-1] += "*"
    parts = toks + [_quote(p) for p in phrases]
    # OR = bag-of-words: BM25 já premia interseção; AND estrito mataria
    # recall em query multi-termo ("como faço deploy" sem doc com os 3)
    return f"{cols}: ({' OR '.join(parts)})"
