"""Adapter `markdown` — `.md`/`.markdown` genérico, sem frontmatter.

Título = primeiro H1; sem H1, o nome do arquivo. Descrição = começo do
texto DEPOIS do H1 (repetir o título na descrição não informa nada).
Corpo = texto integral, inclusive o H1: o corpo é o que vai pro índice e
cortar o cabeçalho perderia sinal de busca.
"""
import re
from pathlib import Path

from neurata.providers.generic import Scanned, oneline

#: H1 ATX na primeira linha não-vazia. `^#{1}\s` evita casar `## sub` e
#: `#hashtag`; MULTILINE porque arquivos reais começam com badge/HTML.
_H1 = re.compile(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)


def parse(path: Path, text: str) -> "Scanned | None":
    match = _H1.search(text)
    if match:
        title = match.group(1)
        rest = text[match.end():]
    else:
        title = path.stem
        rest = text
    return Scanned(
        name=oneline(title, 120) or path.stem,
        description=oneline(rest),
        body=text,
        source_path=str(path),
        fmt="markdown",
    )
