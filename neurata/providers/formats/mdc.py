"""Adapter `mdc` — regras do Cursor (`.cursor/rules/*.mdc`), v1.7.

`.mdc` é markdown com frontmatter Cursor (`description`, `globs`,
`alwaysApply`). Usa o MESMO parser do tick (`frontmatter.parse`,
CRLF-tolerante) — um SKILL.md colhido pelo provider genérico produz o
mesmo content_hash que qualquer outro caminho produziria pro mesmo
texto.

Fallback (mesma régua do skill-md): frontmatter ausente/quebrado não
descarta o arquivo — o nome do arquivo (stem) vira título e o texto
inteiro vira corpo. Corpo vazio → None (só frontmatter não é regra).

`globs`/`alwaysApply` são metadado de APLICAÇÃO: viram texto na
descrição, onde ficam buscáveis — perder a condição de aplicação seria
espelhar a regra pela metade.
"""
from pathlib import Path

from neurata.frontmatter import parse as parse_frontmatter
from neurata.providers.generic import Scanned, oneline


def _fallback_name(path: Path) -> str:
    """Stem do arquivo; sem stem (raiz), o nome do diretório pai."""
    if path.stem:
        return path.stem
    return path.parent.name or path.name


def parse(path: Path, text: str) -> "Scanned | None":
    try:
        meta, body = parse_frontmatter(text)
    except Exception:  # noqa: BLE001 — frontmatter torto não derruba o batch
        meta, body = {}, text
    description = meta.get("description", "")
    if not isinstance(description, str):
        description = ""
    globs = meta.get("globs")
    if isinstance(globs, list):
        globs = ", ".join(str(g) for g in globs if str(g).strip())
    if isinstance(globs, str) and globs.strip():
        description = f"{description} — globs: {globs.strip()}" \
            if description else f"globs: {globs.strip()}"
    if not body.strip():
        return None
    return Scanned(
        name=oneline(_fallback_name(path), 120) or _fallback_name(path),
        description=oneline(description),
        body=body,
        source_path=str(path),
        fmt="mdc",
    )
