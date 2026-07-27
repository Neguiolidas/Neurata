"""Adapter `skill-md` — SKILL.md com frontmatter (Claude Code e afins).

Usa o MESMO parser do tick (`neurata.frontmatter.parse`), então um
`SKILL.md` colhido pelo provider genérico produz o mesmo `content_hash`
que o provider `claude-code` produziria pro mesmo arquivo.

Fallback (R3): frontmatter ausente/quebrado não descarta o arquivo — o
nome do diretório pai vira título e o texto inteiro vira corpo.
"""
from pathlib import Path

from neurata.frontmatter import FrontmatterError
from neurata.frontmatter import parse as parse_frontmatter
from neurata.providers.generic import Scanned, oneline


def _fallback_name(path: Path) -> str:
    """Nome do diretório pai; se o arquivo está na raiz, o próprio stem."""
    parent = path.parent.name
    return parent or path.stem


def parse(path: Path, text: str) -> "Scanned | None":
    try:
        meta, body = parse_frontmatter(text)
    except FrontmatterError:
        meta, body = {}, text
    name = meta.get("name") or meta.get("title") or _fallback_name(path)
    description = meta.get("description", "")
    if not isinstance(name, str):
        name = _fallback_name(path)
    if not isinstance(description, str):
        description = ""
    if not body.strip():
        # Só frontmatter, sem corpo: não há nada pra indexar.
        return None
    return Scanned(
        name=oneline(name, 120) or _fallback_name(path),
        description=oneline(description),
        body=body,
        source_path=str(path),
        fmt="skill-md",
    )
