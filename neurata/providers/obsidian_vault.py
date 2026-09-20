"""neurata/providers/obsidian_vault.py — harvest de vault Obsidian (v1.13).

Roadmap v1.13: "convenções de vault (aliases, pastas, `[[links]]`)",
opt-in explícito — `NEURATA_OBSIDIAN_VAULT` aponta o vault e é a
permissão. Neurata nunca escreve um byte na origem (fronteira do
roadmap: ler, jamais curar).

O que este provider acrescenta sobre colher o vault com o genérico:

* exclui `.obsidian/` (config do app — inclusive `.md` de plugins
  instalados) e `.trash/` (notas que o usuário DELETOU);
* colhe as DUAS portas de tag do Obsidian: frontmatter (adapter
  markdown, v1.8) e inline `#tag` no corpo, com as regras do próprio
  Obsidian — não-heading, fora de code fence, nested `#pai/filho`,
  nunca só-numérica;
* pasta imediata do arquivo vira tag lower — o único eixo de taxonomia
  que o índice aceita sem tocar schema (entry_tags, peso 2.0 no BM25);
  o caminho inteiro segue no `source_key`, que é a identidade;
* `fmt="markdown"` FIXO: vault é prosa — `class: semantic` uniforme, e
  um `SKILL.md` dentro de um vault é documento do vault, não skill.

Wikilinks não são tratados aqui: `_link_targets` (reindex, v1.9) já
resolve `[[alvo]]`, `[[alvo|display]]` e `[[alvo#heading]]` contra
título/alias/slug — e embeds `![[x]]` deixaram de contar como link
nesta mesma versão.
"""
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from neurata.providers import generic
from neurata.providers.claude_code import Skipped

__all__ = ["VAULT_EXCLUDE_DIRS", "VaultItem", "default_dir", "namespace",
           "scan"]

_ENV_VAULT = "NEURATA_OBSIDIAN_VAULT"

#: Exclusões de convenção de vault, SOMADAS às `generic.EXCLUDE_DIRS`.
#: Poda por nome em qualquer nível (contrato do `WalkConfig`): um
#: `.obsidian` aninhado é o mesmo lixo de config que o da raiz.
VAULT_EXCLUDE_DIRS = (".obsidian", ".trash")

#: Tag inline: `#` não grudado em palavra (senão `foo#bar` vira tag) nem
#: precedendo outro `#` (heading); seguido DIRETO de `[\w/-]` — `# título`
#: não é tag, `#Título` é (mesma leitura do Obsidian). `\w` é unicode:
#: acentos ok; `/` é a tag nested do Obsidian.
_TAG = re.compile(r"(?<![\w#])#([\w/-]+)")


@dataclass(frozen=True)
class VaultItem:
    """Item colhido de um vault. Contrato do harvest
    (`name/description/body/source_path/fmt/tags/aliases`) + `key`:
    caminho relativo POSIX — a identidade (mesmo desenho do `project`:
    renomear título não troca identidade; mover entre pastas troca, e
    a re-colheita emite item novo + tombstone)."""

    name: str
    description: str
    body: str
    source_path: str
    fmt: str
    tags: "list[str]" = field(default_factory=list)
    aliases: "list[str]" = field(default_factory=list)
    key: str = ""


def default_dir() -> "Path | None":
    """O vault a colher: env explícito e nada mais. Sem env → `None` —
    o scan reporta o motivo em `Skipped` (best-effort como o `project`
    sem raiz git, mas sem silêncio puro). Descobrir vault por heurística
    seria o oposto do opt-in que o roadmap exige."""
    raw = os.environ.get(_ENV_VAULT, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def namespace(skills_dir: "Path | None") -> str:
    """`obsidian-vault@<hash12 da raiz resolvida>` — o que este provider
    "possui" (mesmo desenho do genérico e do `project`): recolher o
    mesmo vault re-sincroniza; vaults distintos não colidem."""
    if skills_dir is None:
        return "obsidian-vault@sem-vault"
    real = Path(skills_dir).resolve().as_posix()
    digest = hashlib.sha256(real.encode("utf-8")).hexdigest()[:12]
    return f"obsidian-vault@{digest}"


def _tem_letra(tag: str) -> bool:
    """Tag só-numérica não é tag — regra do próprio Obsidian (`#1984`
    cai, `#y1984` passa). `_` conta como letra para a regra."""
    return any(c.isalpha() or c == "_" for c in tag)


def _inline_tags(body: str) -> "list[str]":
    """Tags `#tag` das linhas FORA de code fence (``` e ~~~).

    Fence aberto até o fim do arquivo mantém tudo depois como código —
    crash-safety da fonte torta, não é erro do colher. Linha de fence
    com conteúdo depois do ``` não vira fence (markdown comum), e fence
    indentado é fence para o Obsidian: `lstrip()` antes do teste.
    """
    out: list[str] = []
    in_fence = False
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        out.extend(m.group(1) for m in _TAG.finditer(line))
    return [t for t in out if _tem_letra(t)]


def scan(skills_dir: "Path | None",
         exclude_roots: "tuple" = ()
         ) -> "tuple[list[VaultItem], list[Skipped]]":
    """Contrato de provider: `(itens, skipped)` do vault apontado pelo env.

    `exclude_roots` é a subárvore a podar (o harvest injeta o
    `NEURATA_HOME`): colher `~/` com o home dentro não pode colher a
    própria Library — o guard do harvest cobre o sentido raiz-dentro-do-
    home, este cobre o inverso.
    """
    if skills_dir is None:
        return [], [Skipped("<sem vault>",
                             f"defina {_ENV_VAULT} com o caminho do vault")]
    root = Path(skills_dir).expanduser().resolve()
    # Guard do genérico (harvest.py: "raiz ausente é erro do chamador, não
    # uma colheita vazia legítima"): vault sumido (desmontado, renomeado)
    # devolveria scan vazio — e scan vazio + entries conhecidas =
    # tombstone em TODO o vault indexado. Perda de memória em massa
    # silenciosa; aqui é erro do chamador, como no genérico.
    if not root.is_dir():
        raise ValueError(f"vault inexistente: {root}")
    cfg = generic.WalkConfig(
        root=root,
        fmt="markdown",
        exclude_dirs=generic.EXCLUDE_DIRS + VAULT_EXCLUDE_DIRS,
        exclude_roots=tuple(exclude_roots),
    )
    scanned, skipped = generic.walk(cfg)
    itens: list[VaultItem] = []
    for item in scanned:
        rel = PurePosixPath(
            Path(item.source_path).relative_to(root).as_posix())
        # tags: frontmatter (adapter) + inline + pasta imediata, lower e
        # dedup — o destino normaliza lower de qualquer forma (v1.8);
        # dedupar aqui evita `#Financas` + `tags: [financas]` dobrar.
        tags = {t.lower() for t in item.tags}
        tags.update(t.lower() for t in _inline_tags(item.body))
        if len(rel.parts) > 1:
            tags.add(rel.parts[-2].lower())
        itens.append(VaultItem(
            name=item.name,
            description=item.description,
            body=item.body,
            source_path=item.source_path,
            fmt="markdown",
            tags=sorted(tags),
            aliases=list(item.aliases),
            key=str(rel)))
    return itens, skipped
