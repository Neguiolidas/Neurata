"""neurata/providers/ — scanners de fontes externas p/ harvest (v0.5).

Contrato: `scan(source_dir: Path) -> tuple[list[Skill], list[Skipped]]`,
stdlib puro, sem tocar índice/library. Ver
docs/superpowers/specs/2026-07-18-neurata-v0.5-harvest.md.

`scan`/`Skill` reexportados na raiz são os do `claude_code` — o provider
default, mantido por compat com quem importa `neurata.providers.scan`.
Providers novos entram pelo `resolve()`.

Providers com `namespace()` (root-scoped) aceitam `exclude_roots` no
`scan` — o harvest injeta o `NEURATA_HOME` para que colher `~/` com o
home DENTRO da raiz não auto-colha a Library (v1.13).
"""
from types import ModuleType

from neurata.providers import claude_code, obsidian_vault, project
from neurata.providers.claude_code import Skill, Skipped, scan

#: Providers nomeados: `harvest <nome>` sem diretório de origem.
REGISTRY = {"claude-code": claude_code,
            "obsidian-vault": obsidian_vault,
            "project": project}

#: Provider usado quando o harvest recebe um diretório qualquer.
GENERIC = "generic"

__all__ = ["GENERIC", "REGISTRY", "Skill", "Skipped", "resolve", "scan"]


def resolve(target: str) -> "ModuleType":
    """Devolve o módulo provider de `target`, ou levanta `KeyError`.

    `generic` é importado sob demanda: os adapters de formato
    (`neurata.providers.formats.*`), que é onde o peso está, continuam
    lazy nele — nenhum harvest de `claude-code` os carrega. Os
    providers nomeados que varrem árvore importam `generic` de qualquer
    forma; `generic` em si é leve.
    """
    if target == GENERIC:
        from neurata.providers import generic
        return generic
    return REGISTRY[target]
