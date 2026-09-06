"""neurata/providers/project.py — instruções do projeto onde estou (v1.7).

Roadmap v1.7 ("Cursor rules, AGENTS.md, Copilot instructions — adapters
mecânicos no padrão do provider Claude Code"), entregue como UM provider
com o conjunto canônico: os arquivos de instrução que um repo pode ter,
em local conhecido, cada um virando grão `procedural` com identity por
caminho relativo.

`neurata harvest project` (sem diretório) colhe a raiz git do cwd — a
MESMA âncora de "onde estou" que a v1.6 deu à busca (`context.git_root`,
uma implementação do conceito). Override explícito:
`NEURATA_PROJECT_ROOT`. Sem raiz → lista vazia (best-effort, mesmo
pacto do claude-code com dir ausente): colher instruções não pode
exigir cerimônia, nem mentir que colheu.

Namespace `project@<hash da raiz>` (mesmo desenho do `_namespace` do
genérico): colher o mesmo repo re-sincroniza; mover o repo → namespace
novo → re-colheita + tombstones. Chave por caminho relativo POSIX —
`AGENTS.md` de repositórios DIFERENTES não colidem, e renomear título
não troca a identidade.
"""
import hashlib
from dataclasses import dataclass
from pathlib import Path

from neurata import context
from neurata.providers import generic
from neurata.providers.claude_code import Skipped

#: Instruções canônicas na RAIZ do repo: nome → adapter. Os três
#: legados do Cursor/Windsurf/Cline são do formato `rules`; o trio .md
#: é prosa de instrução (`markdown`); os .mdc do Cursor moderno são
#: `mdc`. Nada fora da lista — não é walk, é lista com semântica.
_ROOT_FILES = {
    "AGENTS.md": "markdown",
    "CLAUDE.md": "markdown",
    ".github/copilot-instructions.md": "markdown",
    ".cursorrules": "rules",
    ".windsurfrules": "rules",
    ".clinerules": "rules",
}

#: Regras modernas do Cursor: um .mdc por regra.
_CURSOR_RULES_GLOB = ".cursor/rules/*.mdc"

_ENV_PROJECT_ROOT = "NEURATA_PROJECT_ROOT"


@dataclass(frozen=True)
class ProjectSkill:
    """Item colhido do projeto. Além do contrato que o harvest consome
    (`name/description/body/source_path/fmt`), carrega `key`: caminho
    relativo POSIX ao scan root — a identidade do espelho."""

    name: str
    description: str
    body: str
    source_path: str
    fmt: str
    key: str


def default_dir() -> "Path | None":
    """Raiz do projeto a colher: env explícita > raiz git do cwd > None."""
    import os
    raw = os.environ.get(_ENV_PROJECT_ROOT, "").strip()
    if raw:
        return Path(raw)
    return context.git_root()


def namespace(skills_dir: "Path | None") -> str:
    """`project@<hash>` — o que este provider "possui" (mesmo desenho
    do `_namespace` do genérico: resolver a raiz antes de hashear faz
    dois caminhos equivalentes baterem no mesmo namespace)."""
    real = (Path(skills_dir).resolve().as_posix() if skills_dir
            else "sem-raiz")
    digest = hashlib.sha256(real.encode("utf-8")).hexdigest()[:12]
    return f"project@{digest}"


def item_key(item) -> str:
    """Chave do item dentro do namespace: o caminho relativo POSIX."""
    return item.key


def scan(skills_dir: "Path | None") -> "tuple[list[ProjectSkill], list[Skipped]]":
    """Colhe o conjunto canônico de `skills_dir` (raiz do repo).

    Arquivo ausente é silêncio — é o estado normal de 5 dos 7; só
    presente-e-ilegível (grande, binário, vazio) vira `Skipped`.
    Ordem determinística: a lista canônica em ordem fixa, o glob dos
    .mdc ordenado."""
    skills: list[ProjectSkill] = []
    skipped: list[Skipped] = []
    if skills_dir is None or not Path(skills_dir).is_dir():
        return skills, skipped
    root = Path(skills_dir)

    candidatos: list[tuple[Path, str]] = [
        (root / rel, fmt) for rel, fmt in _ROOT_FILES.items()]
    candidatos += [(p, "mdc") for p in sorted(root.glob(_CURSOR_RULES_GLOB))]

    for path, fmt in candidatos:
        if not path.is_file():
            continue
        text, reason = generic._read_text(path, max_size=1_048_576)
        if text is None:
            skipped.append(Skipped(str(path), reason))
            continue
        # adapters vivem em módulos homônimos; o genérico já resolve
        # o import lazy (o pacote não carrega os submódulos por conta)
        mod = generic._adapter(fmt)
        try:
            item = mod.parse(path, text)
        except Exception as e:  # noqa: BLE001 — torto não derruba o batch
            skipped.append(Skipped(str(path), f"{fmt}: {e}"))
            continue
        if item is None:
            skipped.append(Skipped(str(path), f"{fmt}: não reconhecido"))
            continue
        key = path.relative_to(root).as_posix()
        skills.append(ProjectSkill(
            name=item.name, description=item.description, body=item.body,
            source_path=item.source_path, fmt=fmt, key=key))
    return skills, skipped
