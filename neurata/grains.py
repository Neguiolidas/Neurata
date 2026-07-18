"""neurata/grains.py — grãos mecânicos: card (uma linha) e summary (extrativo).

card = description do frontmatter; fallback = primeira frase do corpo
(cap 140 com '…'). summary = cada heading + primeiro parágrafo da seção;
preâmbulo contribui o primeiro parágrafo; corpo sem headings → 2 primeiros
parágrafos. Fence-aware: heading dentro de ``` não é heading.

grain_quality 'refined' é v2.0 — aqui tudo é mechanical. Regra
anti-summary-de-summary (corpo compactado ⇒ summary = corpo) fica no
reindex, que conhece o frontmatter.
"""
import re

_HEADING = re.compile(r"^#{1,6}\s+\S")
_SENTENCE = re.compile(r"(?<=[.!?])\s")
_CARD_CAP = 140


def make_card(meta: dict, body: str) -> str:
    desc = str(meta.get("description", "") or "").strip()
    if desc:
        return desc
    for _, text in _sections(body):
        para = _first_paragraph(text)
        if para:
            first = _SENTENCE.split(" ".join(para.split()))[0]
            if len(first) > _CARD_CAP:
                return first[:_CARD_CAP - 1] + "…"
            return first
    return ""


def make_summary(body: str) -> str:
    sections = _sections(body)
    has_heading = any(h for h, _ in sections)
    parts: list[str] = []
    if not has_heading:
        paras = _paragraphs(body)[:2]
        return "\n\n".join(paras)
    for heading, text in sections:
        para = _first_paragraph(text)
        if heading and para:
            parts.append(f"{heading}\n\n{para}")
        elif heading:
            parts.append(heading)
        elif para:
            parts.append(para)
    return "\n\n".join(parts)


def _sections(body: str) -> list[tuple[str, str]]:
    """[(heading|'', texto-da-seção)] — fence-aware, determinístico."""
    out: list[tuple[str, str]] = []
    cur_head = ""
    cur: list[str] = []
    fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            cur.append(line)
            continue
        if not fence and _HEADING.match(line):
            if cur_head or cur:
                out.append((cur_head, "\n".join(cur)))
            cur_head = line.rstrip()
            cur = []
        else:
            cur.append(line)
    if cur_head or cur:
        out.append((cur_head, "\n".join(cur)))
    return out


def _paragraphs(text: str) -> list[str]:
    """Blocos separados por linha em branco, fora de fence."""
    paras: list[str] = []
    buf: list[str] = []
    fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            buf.append(line)
            continue
        if not line.strip() and not fence:
            if buf:
                paras.append("\n".join(buf).strip())
                buf = []
        else:
            buf.append(line)
    if buf:
        paras.append("\n".join(buf).strip())
    return [p for p in paras if p]


def _first_paragraph(text: str) -> str:
    paras = _paragraphs(text)
    return paras[0] if paras else ""
