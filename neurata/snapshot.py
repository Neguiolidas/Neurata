"""neurata/snapshot.py — git audit da library (plumbing best-effort).

Espelha o padrão de `envelope.py`: subprocess puro (sem `import git`/
dulwich), timeout, catch `(OSError, subprocess.TimeoutExpired)`. Repo git
vive dentro de `home.library` (repo root = library dir); `git -C
<library>` sempre, nunca `chdir`.

NUNCA importar `neurata.tick` aqui (ciclo: tick.py importa `commit_tick`
deste módulo). Funções que recebem um `TickReport` fazem duck-typing —
só acessam atributos (`report.processed` etc.), nunca `isinstance`.
"""
import subprocess

from neurata.home import CONTRACT_VERSION, SCHEMA_VERSION
from neurata.indexdb import INDEX_SCHEMA_VERSION

GIT_TIMEOUT = 15  # commit/checkout local; push tem timeout próprio maior

_AVAIL: "bool | None" = None

# Ordem fixa do subject (spec §3): sinal + label. `stale` não tem sinal —
# nunca aparece no subject, só no body.
_SUBJECT_CATEGORIES = (
    ("processed", "+", "catalogados"),
    ("updated", "~", "atualizado"),
    ("quarantined", "-", "quarentena"),
    ("conflicts", "⚠", "dup"),
    ("renamed", "→", "rename"),
)

# Ordem fixa do body (spec §3): sempre as 6 linhas, mesmo com valor 0.
_BODY_LABELS = (
    ("cataloga:", "processed"),
    ("atualiza:", "updated"),
    ("quarentena:", "quarantined"),
    ("near-dup:", "conflicts"),
    ("rename:", "renamed"),
    ("stale:", "stale"),
)

_BODY_LABEL_WIDTH = 13  # "quarentena:" (11) + 2 espaços = maior label + margem


def _body_extra(attr: str, report) -> "str | None":
    """Glosa entre parênteses de cada linha do body — só quando o
    contador correspondente é não-zero. `processed` é o único caso cujo
    detalhe é um contador de fato (report.literate); os demais são texto
    fixo descrevendo a categoria (não há subtipo no TickReport)."""
    if attr == "processed":
        literate = getattr(report, "literate", 0)
        return f"{literate} alfabetizados" if literate else None
    if attr == "updated":
        return "source-keyed in-place" if report.updated else None
    if attr == "quarantined":
        return "duplicata exata" if report.quarantined else None
    if attr == "conflicts":
        return "marcado conflito" if report.conflicts else None
    return None


def _tick_subject(report) -> str:
    """Subject (≤72 col) — spec §3. Só categorias não-zero, ordem fixa
    `+catalogados ~atualizado -quarentena ⚠dup →rename`. Se nenhuma for
    não-zero (ex.: edição aditiva de `conflicts_with` sem novo processed/
    updated/quarantined/conflicts/renamed), cai no fallback genérico."""
    parts = [f"{sign}{value} {label}"
            for attr, sign, label in _SUBJECT_CATEGORIES
            if (value := getattr(report, attr, 0))]
    if not parts:
        return "snapshot: metadados atualizados"
    return "snapshot: " + ", ".join(parts)


def _tick_body(report) -> str:
    """Body (spec §3) — detalhamento por tipo de op, sempre as 6
    categorias (mesmo zeradas), rodapé com tick id + versões de schema."""
    lines = []
    for label, attr in _BODY_LABELS:
        value = getattr(report, attr, 0)
        line = f"{label:<{_BODY_LABEL_WIDTH}}{value}"
        extra = _body_extra(attr, report)
        if extra:
            line += f"  ({extra})"
        lines.append(line)
    lines.append("")
    lines.append(f"tick: {report.tick}")
    lines.append(f"schema: config={SCHEMA_VERSION} index={INDEX_SCHEMA_VERSION}"
                f" contract={CONTRACT_VERSION}")
    return "\n".join(lines)


class SnapshotError(RuntimeError):
    pass


def _run(home, *args, timeout=GIT_TIMEOUT, check=False) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(home.library), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if check:
            raise SnapshotError(str(exc)) from exc
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(exc))
    if check and proc.returncode != 0:
        raise SnapshotError(proc.stderr)
    return proc


def git_available() -> bool:
    global _AVAIL
    if _AVAIL is None:
        try:
            proc = subprocess.run(["git", "--version"], capture_output=True,
                                   text=True, timeout=GIT_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            _AVAIL = False
        else:
            _AVAIL = proc.returncode == 0
    return _AVAIL


def ensure_repo(home) -> bool:
    if not git_available():
        return False
    if (home.library / ".git").exists():
        return True
    _run(home, "init", "-q", "-b", "main")
    _run(home, "config", "user.name", "neurata")
    _run(home, "config", "user.email", "neurata@localhost")
    _run(home, "config", "commit.gpgsign", "false")
    _run(home, "config", "core.autocrlf", "false")
    _run(home, "config", "core.hooksPath", "/dev/null")
    # Return estado REAL: se init falhou (lib ausente/permissão/disco) o
    # .git não existe — não mentir "pronto" (senão commit degrada a None
    # silencioso sem log). best-effort honesto.
    return (home.library / ".git").exists()


def has_changes(home) -> bool:
    if not git_available():
        return False
    proc = _run(home, "status", "--porcelain")
    return bool(proc.stdout.strip())


def commit(home, subject, body="") -> "str | None":
    if not git_available():
        return None
    _run(home, "add", "-A")
    if not has_changes(home):
        return None
    args = ["commit", "-m", subject]
    if body:
        args += ["-m", body]
    _run(home, *args, check=True)
    proc = _run(home, "rev-parse", "--short=12", "HEAD", check=True)
    return proc.stdout.strip()


def commit_tick(home, report) -> "str | None":
    """Commit best-effort do tick — spec §3+§4. `report` é um TickReport
    (duck-typed, ver módulo docstring). Chama `has_changes` primeiro:
    tree limpo → `None`, sem commit, mesmo que os contadores do report
    sejam >0 (nada a versionar). Tree sujo → sempre commita, mesmo com
    report todo-zero (ex.: edição aditiva de `conflicts_with`), usando o
    subject fallback de `_tick_subject`."""
    if not has_changes(home):
        return None
    return commit(home, _tick_subject(report), _tick_body(report))


def set_remote(home, url: str) -> None:
    proc = _run(home, "remote", "get-url", "neurata")
    if proc.returncode == 0:
        _run(home, "remote", "set-url", "neurata", url)
    else:
        _run(home, "remote", "add", "neurata", url)
