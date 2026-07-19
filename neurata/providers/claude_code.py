"""neurata/providers/claude_code.py — scanner de skills do Claude Code.

`scan(skills_dir)` itera `<skills_dir>/<nome>/SKILL.md`, parseia
frontmatter (subset via `neurata.frontmatter.parse` — mesmo parser do
tick, p/ hash de `content_hash` bater) e monta `Skill`. Qualquer falha
por-arquivo vira `Skipped`, nunca aborta o scan inteiro. Não toca
índice/library; não calcula `content_hash` (responsabilidade do harvest).
"""
from dataclasses import dataclass
from pathlib import Path

from neurata.frontmatter import FrontmatterError, parse


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    source_path: str


@dataclass(frozen=True)
class Skipped:
    path: str
    reason: str


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
        skills.append(Skill(name=name, description=description, body=body,
                             source_path=str(skill_md)))
    return skills, skipped
