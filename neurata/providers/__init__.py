"""neurata/providers/ — scanners de fontes externas p/ harvest (v0.5).

Contrato: `scan(source_dir: Path) -> tuple[list[Skill], list[Skipped]]`,
stdlib puro, sem tocar índice/library. Ver
docs/superpowers/specs/2026-07-18-neurata-v0.5-harvest.md.
"""
from neurata.providers.claude_code import Skill, Skipped, scan

__all__ = ["Skill", "Skipped", "scan"]
