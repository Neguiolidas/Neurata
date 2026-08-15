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
