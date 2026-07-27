"""neurata/providers/ — scanners de fontes externas p/ harvest (v0.5).

Contrato: `scan(source_dir: Path) -> tuple[list[Skill], list[Skipped]]`,
stdlib puro, sem tocar índice/library. Ver
docs/superpowers/specs/2026-07-18-neurata-v0.5-harvest.md.

`scan`/`Skill` reexportados na raiz são os do `claude_code` — o provider
default, mantido por compat com quem importa `neurata.providers.scan`.
Providers novos entram pelo `resolve()`.
"""
from neurata.providers import claude_code
from neurata.providers.claude_code import Skill, Skipped, scan

#: Providers nomeados: `harvest <nome>` sem diretório de origem.
REGISTRY = {"claude-code": claude_code}

#: Provider usado quando o harvest recebe um diretório qualquer.
GENERIC = "generic"

__all__ = ["GENERIC", "REGISTRY", "Skill", "Skipped", "resolve", "scan"]


def resolve(target: str):
    """Devolve o módulo provider de `target`, ou levanta `KeyError`.

    `generic` é importado sob demanda: ele arrasta os adapters de formato
    (`neurata.providers.formats.*`), que nenhum harvest de `claude-code`
    precisa carregar.
    """
    if target == GENERIC:
        from neurata.providers import generic
        return generic
    return REGISTRY[target]
