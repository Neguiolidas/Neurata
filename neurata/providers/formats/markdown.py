"""Adapter `markdown` — `.md`/`.markdown` genérico, sem frontmatter.

Título = primeiro H1; sem H1, o nome do arquivo. Descrição = começo do
texto DEPOIS do H1 (repetir o título na descrição não informa nada).
Corpo = texto integral, inclusive o H1: o corpo é o que vai pro índice e
cortar o cabeçalho perderia sinal de busca.
"""
import re
from pathlib import Path

from neurata.frontmatter import FrontmatterError
from neurata.frontmatter import parse as parse_frontmatter
from neurata.providers.claude_code import as_str_list
from neurata.providers.generic import Scanned, oneline

#: H1 ATX na primeira linha não-vazia. `^#{1}\s` evita casar `## sub` e
#: `#hashtag`; MULTILINE porque arquivos reais começam com badge/HTML.
_H1 = re.compile(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)


def parse(path: Path, text: str) -> "Scanned | None":
    meta, body, via_frontmatter = {}, text, False
    if text.startswith("---" + chr(10)) or text.startswith("---" + chr(13)
            + chr(10)):
        try:
            meta, body = parse_frontmatter(text)
            via_frontmatter = bool(meta)
        except FrontmatterError:
            meta, body, via_frontmatter = {}, text, False

    if via_frontmatter:
        match = _H1.search(body)
        raw_title = meta.get("title")
        if isinstance(raw_title, str) and raw_title.strip():
            titulo = raw_title.strip()
        elif match:
            titulo = match.group(1)
        else:
            titulo = path.stem
        raw_description = meta.get("description")
        if isinstance(raw_description, str) and raw_description.strip():
            descricao = raw_description
        elif match:
            descricao = text[match.end():]
        else:
            descricao = body
        return Scanned(
            name=oneline(titulo, 120) or path.stem,
            description=oneline(descricao),
            body=body,
            source_path=str(path),
            fmt="markdown",
            tags=as_str_list(meta.get("tags")),
            aliases=as_str_list(meta.get("aliases")),
        )

    match = _H1.search(text)
    title_h1: str | None = match.group(1) if match else None
    title = title_h1 or path.stem
    rest = text[match.end():] if match else text
    return Scanned(
        name=oneline(title, 120) or path.stem,
        description=oneline(rest),
        body=text,
        source_path=str(path),
        fmt="markdown",
    )
