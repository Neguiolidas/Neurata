"""neurata/entryref.py — resolve entrada (id ou slug) direto dos .md.

Não depende do índice: compact/expand precisam funcionar com índice
destruído (só arquivo + archive). Varre library + inbox, mesma ordem do
reindex; entradas ilegíveis são silenciosamente puladas (reindex já as
reporta em `skipped`).
"""
from dataclasses import dataclass
from pathlib import Path

from neurata.frontmatter import FrontmatterError, parse
from neurata.home import NeurataHome


class EntryNotFoundError(LookupError):
    pass


class EntryAmbiguousError(LookupError):
    pass


@dataclass
class EntryRef:
    path: Path
    meta: dict
    body: str
    slug: str


def load(home: NeurataHome, path: Path, ref: str) -> "EntryRef | None":
    """Carrega a entrada de um caminho JÁ conhecido, confirmando que ela
    ainda é `ref`. Atalho de `resolve` para quem leu o path no índice.

    `resolve` varre library+inbox inteiros e faz parse de cada `.md` —
    O(acervo) por chamada, o que num acervo de ~15 mil arquivos custa
    ~0,2 s cada e domina qualquer laço. Quem já sabe o caminho (o tick
    lê `path` na mesma linha do SELECT) paga um único open.

    Devolve `None` — e não uma entrada errada — quando o arquivo sumiu,
    ficou ilegível, caiu fora de library/inbox ou deixou de ser `ref`
    (renomeado, id reescrito entre a leitura do índice e agora). O
    chamador cai no `resolve` completo, que é a fonte de verdade: o
    índice pode estar velho, o disco não.

    O domínio é o mesmo de `resolve` de propósito: um índice velho (ou
    adulterado) apontando pra fora do home não pode virar entrada por
    este caminho e escapar da varredura.
    """
    if not path.is_relative_to(home.library) and \
            not path.is_relative_to(home.inbox):
        return None
    try:
        meta, body = parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, FrontmatterError):
        return None
    slug = path.stem
    if str(meta.get("id", "")) != ref and slug != ref:
        return None
    return EntryRef(path=path, meta=meta, body=body, slug=slug)


def resolve(home: NeurataHome, ref: str) -> EntryRef:
    matches: list[EntryRef] = []
    for base in (home.library, home.inbox):
        for path in sorted(base.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
                meta, body = parse(text)
            except (OSError, UnicodeDecodeError, FrontmatterError):
                continue
            slug = path.stem
            if str(meta.get("id", "")) == ref or slug == ref:
                matches.append(EntryRef(path=path, meta=meta, body=body,
                                        slug=slug))
    if not matches:
        raise EntryNotFoundError(
            f"entrada não encontrada: {ref!r} — verifique o id (ULID) ou "
            "o slug (nome do arquivo sem .md)")
    if len(matches) > 1:
        paths = ", ".join(str(m.path.relative_to(home.root))
                          for m in matches)
        raise EntryAmbiguousError(
            f"{ref!r} casa {len(matches)} entradas ({paths}) — use o id "
            "(ULID) completo para desambiguar")
    return matches[0]
