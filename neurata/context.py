"""neurata/context.py — contexto de quem consulta (v1.6).

A v1.2 ensinou o DEPÓSITO a saber onde está (cwd, raiz git, envs de
agente/sessão); esta é a metade da busca: captura o contexto do
chamador para o viés determinístico da query. Best-effort por
construção — contexto ausente significa "viés desligado", nunca erro.

Precedência de projeto: `NEURATA_PROJECT` (contrato de agente, custo
zero — a ponte/v2.0 seta) > raiz git do cwd (subprocess com timeout
curto; a busca é caminho quente e não pode esperar 2 s como o
depósito) > nada. Sessão: a MESMA precedência do depósito
(`NEURATA_SESSION` > `CLAUDE_CODE_SESSION_ID`) — quem deposita e quem
consulta compartilham o contrato.

O basename do git root usa a MESMA normalização de `project_of`
(unificar separador nativo para `/` antes do PurePosixPath): os dois
lados têm que
concordar sobre como um repo se chama, senão o boost de projeto nunca
casa com a coluna gravada pelo depósito.
"""
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

#: Busca é caminho quente: git que não responde em meio segundo não
#: participa. O depósito pode esperar 2 s (escreve raramente); a query,
#: nunca.
GIT_TIMEOUT_S = 0.5


@dataclass(frozen=True)
class QueryContext:
    """Contexto do chamador. `project_source` diz DE ONDE o projeto
    veio ("env" | "git" | "none") — viés que não declara a origem não
    é depurável."""

    project: "str | None"
    session: "str | None"
    project_source: str


def _env(d: "dict | None", name: str) -> "str | None":
    """Valor da env sem espaço em volta; ausente/em branco = None.

    Mesmo pacto de `envelope._env_str` — `NEURATA_PROJECT='  '` não é
    um projeto. Helper local: o envelope é do depósito; acoplar os dois
    por um import privado economizaria 3 linhas e criaria um dono a
    menos da precedência da busca."""
    raw = (os.environ if d is None else d).get(name, "")
    return raw.strip() or None


def git_root(cwd: "Path | str | None" = None) -> "Path | None":
    """Raiz git do diretório — best-effort, nunca levanta.

    A âncora compartilhada de "onde estou": a busca (v1.6) deriva o
    projeto dela; o harvest de instruções do projeto (v1.7) colhe a
    partir dela. Uma implementação do conceito, não duas."""
    if cwd is not None:
        workdir = Path(cwd)
    else:
        try:
            workdir = Path.cwd()
        except OSError:
            return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
            check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    root = proc.stdout.strip().splitlines()[0] if proc.stdout.strip()         else ""
    if not root:
        return None
    return Path(root)


def _project_from_git(cwd: Path) -> "str | None":
    root = git_root(cwd)
    if root is None:
        return None
    # Mesma normalização de `project_of` (indexdb): separadores são
    # unificados ANTES do PurePosixPath — o mock/teste e um git em
    # Windows podem devolver backslashes, e `as_posix()` de um Path
    # Linux não os separa (a barra invertida é caractere comum lá).
    # A normalização tem que ser agnóstica de plataforma.
    return PurePosixPath(str(root).replace(chr(92), "/")).name or None



def capture_query_context(cwd: "Path | str | None" = None,
                          env: "dict | None" = None) -> QueryContext:
    """Contexto do chamador da query. Nunca levanta.

    `env=None` usa `os.environ`; dict injetado é usado como veio
    (testes, ponte, v2.0). `cwd=None` usa `Path.cwd()` — se o diretório
    atual foi deletado sob o processo, o git falha e o contexto sai
    vazio, que é o comportamento declarado."""
    project = _env(env, "NEURATA_PROJECT")
    source = "env" if project else "none"
    if project is None:
        if cwd is not None:
            workdir = Path(cwd)
        else:
            try:
                workdir = Path.cwd()
            except OSError:
                workdir = None
        if workdir is not None:
            project = _project_from_git(workdir)
            if project:
                source = "git"
    session = _env(env, "NEURATA_SESSION") or _env(
        env, "CLAUDE_CODE_SESSION_ID")
    return QueryContext(project=project, session=session,
                        project_source=source)


def days_since(updated: "str | None",
               now: "datetime | None" = None) -> float:
    """Δ em dias desde `updated` (ISO); ilegível/ausente = infinito.

    Infinito, não zero: frontmatter quebrado não pode GANHAR recência —
    só perder. Mesma tolerância da shelf (`_parse_iso`), com o mesmo
    default: quem não sabe dizer quando mudou é tratado como velho."""
    if not updated:
        return float("inf")
    try:
        dt = datetime.fromisoformat(str(updated))
    except ValueError:
        return float("inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400.0)
