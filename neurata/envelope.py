"""neurata/envelope.py — proveniência best-effort do depósito."""
import os
import re
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_AGENT_VERSION = re.compile(r"^v?\d+([.\-]\d+)*$")


def _normalize_agent(raw: str) -> str:
    """Nome de agente sem a versão que o host pendura.

    `AI_AGENT` chega como `claude-code_2-1-229_agent`; sem normalizar,
    cada upgrade do host criaria um "agente" novo e a facet `agent:`
    fragmentaria o acervo. Regra deliberadamente burra: separa por `_`,
    descarta o segmento final `agent` e todo segmento que seja versão,
    rejunta. Se sobrar vazio, devolve o cru — normalizar nunca pode
    apagar o dado.
    """
    stripped = raw.strip()
    segs = stripped.split("_")
    if segs and segs[-1] == "agent":
        segs = segs[:-1]
    segs = [s for s in segs if s and not _AGENT_VERSION.match(s)]
    return "_".join(segs) or stripped


def _env_str(name: str) -> "str | None":
    """Valor da env sem espaço em volta; `None` se ausente ou em branco —
    `AI_AGENT='  '` não é um agente."""
    return os.environ.get(name, "").strip() or None


def capture(origin: str = "manual", agent: "str | None" = None,
            session: "str | None" = None, cwd: "Path | None" = None) -> dict:
    if cwd is not None:
        resolved_cwd = Path(cwd)
    else:
        try:
            resolved_cwd = Path.cwd()
        except OSError:
            # cwd deletado sob o processo (rmdir concorrente) — não deixa a
            # proveniência best-effort derrubar o depósito.
            resolved_cwd = Path(".")
    try:
        host = socket.gethostname()
    except OSError:
        host = "unknown"
    env: dict = {
        "host": host,
        "cwd": str(resolved_cwd),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origin": origin,
    }
    git = _git_context(resolved_cwd)
    if git:
        env["git"] = git
    # Precedência (D1): arg explícito > NEURATA_* > var do host > None.
    # Argumento em branco conta como ausência: procedência vazia é `None`,
    # nunca string vazia, senão `missing:agent` passa a significar duas
    # coisas.
    agent = (agent or "").strip() or None
    if agent is None:
        agent = _env_str("NEURATA_AGENT")
        if agent is None:
            raw_agent = _env_str("AI_AGENT")
            agent = _normalize_agent(raw_agent) if raw_agent else None

    session = (session or "").strip() or None
    if session is None:
        session = (_env_str("NEURATA_SESSION")
                   or _env_str("CLAUDE_CODE_SESSION_ID"))

    if agent is not None:
        env["agent"] = agent
    if session is not None:
        env["session"] = session
    return env


def _git_context(cwd: Path) -> dict:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse",
             "--show-toplevel", "HEAD", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    lines = proc.stdout.strip().splitlines()
    if proc.returncode == 0 and len(lines) >= 3:
        return {"root": str(Path(lines[0]).resolve()),
                "commit": lines[1][:12], "branch": lines[2]}
    # Repo sem commit ainda é repo: `--show-toplevel` imprime a raiz e o
    # `HEAD` do mesmo comando falha (rc=128). Abortar no rc apagaria a
    # proveniência — e o projeto, que vem da raiz — justo no início de um
    # projeto novo. Só se aproveita a primeira linha se for caminho
    # absoluto: com rc≠0 ela pode ser resto do erro do git ("HEAD"), e
    # `Path("HEAD").resolve()` inventaria uma raiz sob o cwd.
    if lines and Path(lines[0]).is_absolute():
        return {"root": str(Path(lines[0]).resolve())}
    return {}
