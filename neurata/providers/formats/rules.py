"""Adapter `rules` — `.cursorrules`, `.windsurfrules`, `.clinerules`.

Formato livre, sem estrutura: são dotfiles de instrução em prosa. Não há
título dentro do arquivo, então o nome vem do diretório que o contém —
`~/proj/.cursorrules` vira "proj (.cursorrules)", que é o que distingue
um do outro numa lista com 50 repos.
"""
from pathlib import Path

from neurata.providers.generic import Scanned, oneline


def parse(path: Path, text: str) -> "Scanned | None":
    parent = path.parent.name
    title = f"{parent} ({path.name})" if parent else path.name
    return Scanned(
        name=oneline(title, 120),
        description=oneline(text),
        body=text,
        source_path=str(path),
        fmt="rules",
    )
