"""neurata/router.py — parse de query, facets e fan-out determinístico.

Sanitização por construção: input do usuário NUNCA vai cru pro MATCH.
Tokens são quotados; operadores FTS5 não são expostos. Propriedade
garantida: nenhuma query de usuário produz OperationalError de sintaxe.
"""
import re
from dataclasses import dataclass

from neurata.textnorm import normalize

_FACET = re.compile(r"(?:^|(?<=\s))(type|tag|env|project):(\S+)")
_PHRASE = re.compile(r'"([^"]+)"')
_SKILL = re.compile(r"\bcomo fa[çc]o\b|\bhow do i\b", re.IGNORECASE)
_WORD = re.compile(r"\w")
_RAW_COLS = "{title aliases tags body}"
_NORM_COLS = "{title_norm aliases_norm tags_norm body_norm}"


@dataclass
class Variant:
    name: str    # raw | norm | singular | plural | prefix (= chave de peso)
    match: str   # string MATCH FTS5 pronta (sanitizada)


@dataclass
class ParsedQuery:
    text: str
    tokens: list[str]
    phrases: list[str]
    facets: dict[str, str]  # type/env/project
    tags: list[str]
    skill_hint: bool

    @property
    def has_text(self) -> bool:
        return bool(self.tokens or self.phrases)

    @property
    def has_facets(self) -> bool:
        return bool(self.facets or self.tags)


def parse(q: str) -> ParsedQuery:
    skill = bool(_SKILL.search(q))
    # frases primeiro: facet quotado ("type:x") é literal, não facet
    phrases = [p.strip() for p in _PHRASE.findall(q) if _WORD.search(p)]
    rest = _PHRASE.sub(" ", q).replace('"', " ")  # aspa desbalanceada=literal
    facets: dict[str, str] = {}
    tags: list[str] = []

    def _take(m: "re.Match[str]") -> str:
        key, val = m.group(1), m.group(2)
        if key == "tag":
            tags.append(val.lower())
        else:
            facets[key] = val
        return " "

    rest = _FACET.sub(_take, rest)
    # token sem \w (só símbolo) nunca vira MATCH — some do fan-out
    tokens = [t for t in rest.split() if _WORD.search(t)]
    return ParsedQuery(q, tokens, phrases, facets, tags, skill)


def variants(parsed: ParsedQuery) -> list[Variant]:
    """≤5 variantes, deduplicadas. raw primeiro (preferência de snippet)."""
    if not parsed.has_text:
        return []
    out = [Variant("raw", _match(_RAW_COLS, parsed.tokens, parsed.phrases))]
    norm_t = [t for tok in parsed.tokens for t in normalize(tok).split()]
    norm_p = [np for p in parsed.phrases if (np := normalize(p))]
    if not (norm_t or norm_p):
        return out
    base = _match(_NORM_COLS, norm_t, norm_p)
    out.append(Variant("norm", base))
    seen = {base}
    sing = [t[:-1] if t.endswith("s") and len(t) > 3 else t for t in norm_t]
    plur = [t if t.endswith("s") else t + "s" for t in norm_t]
    for name, toks in (("singular", sing), ("plural", plur)):
        m = _match(_NORM_COLS, toks, norm_p)
        if m not in seen:
            out.append(Variant(name, m))
            seen.add(m)
    if norm_t:
        m = _match(_NORM_COLS, norm_t, norm_p, prefix_last=True)
        if m not in seen:
            out.append(Variant("prefix", m))
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
