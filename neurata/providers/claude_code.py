"""neurata/providers/claude_code.py — scanner de skills do Claude Code.

`scan(skills_dir)` itera `<skills_dir>/<nome>/SKILL.md`, parseia
frontmatter (subset via `neurata.frontmatter.parse` — mesmo parser do
tick, p/ hash de `content_hash` bater) e monta `Skill`. Qualquer falha
por-arquivo vira `Skipped`, nunca aborta o scan inteiro. Não toca
índice/library; não calcula `content_hash` (responsabilidade do harvest).
"""
from dataclasses import dataclass, field
from pathlib import Path

from neurata.frontmatter import FrontmatterError, parse


@dataclass(frozen=True)
class Skill:
    """Item colhido do Claude Code. `fmt` é o mesmo campo de
    `generic.Scanned`: um SKILL.md é a forma que o adapter `skill-md` lê,
    e é ele que declara a classe do espelho. Fixo por contrato, não por
    default de conveniência — um provider que passe a ler outra forma
    muda este valor junto, e o harvest não precisa adivinhar."""

    name: str
    description: str
    body: str
    source_path: str
    fmt: str = "skill-md"
    tags: "list[str]" = field(default_factory=list)
    aliases: "list[str]" = field(default_factory=list)


@dataclass(frozen=True)
class Skipped:
    path: str
    reason: str


def as_str_list(value) -> "list[str]":
    """Coerção única de tags/aliases declarados pela fonte.

    str → split por vírgula (com `[a, b]` despelotado); lista/tupla →
    str por item; vazio/torto → []. Um único ponto de coerção: dois
    adapters coerindo diferente é como o mesmo YAML colhido por caminhos
    diferentes virava dois grãos. Mora aqui (base do pacote de
    providers) porque generic já importa Skipped daqui — e o caminho
    reverso seria ciclo."""
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        return [p.strip() for p in s.split(",") if p.strip()]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            s = str(item).strip()
            if s:
                out.append(s)
        return out
    return []


def scan(skills_dir: Path) -> "tuple[list[Skill], list[Skipped]]":
    skills: list[Skill] = []
    skipped: list[Skipped] = []
    if not skills_dir.is_dir():
        return skills, skipped
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            skipped.append(Skipped(str(skill_md), "no SKILL.md"))
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError as e:
            skipped.append(Skipped(str(skill_md), f"unreadable: {e}"))
            continue
        try:
            meta, body = parse(text)
        except FrontmatterError as e:
            skipped.append(Skipped(str(skill_md), f"frontmatter inválido: {e}"))
            continue
        name = str(meta.get("name") or entry.name)
        description = str(meta.get("description", ""))
        skills.append(Skill(
            name=name, description=description, body=body,
            source_path=str(skill_md),
            tags=as_str_list(meta.get("tags")),
            aliases=as_str_list(meta.get("aliases"))))
    return skills, skipped
